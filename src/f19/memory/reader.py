"""Reading the game: MemoryReader(emulator) -> GameState / Mission, plus the old dict-returning readers.

  r = MemoryReader(Emulator())
  r.snapshot()        -> f19.core.types.GameState  (state + flight + status + alerts + aircraft + sites + waypoints,
                                                    trackcam, sensitivity: two pipelined round trips)
  r.mission(world)    -> f19.core.types.Mission    (reads all of conventional memory; see memory.mission)
  r.time_mult(), r.frame(), r.events(), r.event_list(start)

The dict readers (read_state, read_flight, read_status, read_alerts, read_aircraft, read_site_records, ...) are the
functions scripts/f19_state.py had, with the same requests and the same results; f19_state re-exports them. They take
any connection `db`: an Emulator (peek_many), or an old-style fake with `_send` / `_recv_line` (tests' and nav_tui's
SnapDB). The data-segment offset is cached per connection in `_offset` (id(db) -> offset), which callers may seed or
clear as before.

ReplayReader serves the same API from a nav --memlog run (windows.json + snap_<t>.bin).
"""
import json
import struct
import time
from pathlib import Path

from f19.core.types import GameState, Mission, TrackcamView
from f19.core.units import CLOCK_TICK_S
from f19.memory import addresses as A
from f19.memory import mission as M

_offset = {}             # id(db) -> EGAME offset (old phys -> phys)
_events = {}             # id(db) -> phys of the event list found by its first records, or (None, time of the failed scan)
EVENT_RETRY_S = 5.0      # a failed scan is retried this often (the list only has its two marker records ~64 game s in)


# ------------------------------------------------------------------------------------------------ transport
def _peek_many(db, reqs):
    """Pipelined PEEKs: [(phys, n)] -> [bytes], through db.peek_many, or the old line API for fakes without it."""
    reqs = list(reqs)
    if not reqs: return []
    if hasattr(db, "peek_many"): return db.peek_many(reqs)
    for phys, n in reqs: db._send(f"PEEK {phys >> 4:X}:{phys & 15:X} {n}")
    return [bytes.fromhex(db._recv_line().split(" ", 1)[1]) for _ in reqs]


def _peek(db, phys, n):
    return _peek_many(db, [(phys, n)])[0]


def _u16(b): return struct.unpack("<H", b)[0]


# --------------------------------------------------------------------------------------------- locating EGAME
def find_offset(db, start=A.SCAN_LO, end=A.SCAN_HI):
    """Scan conventional memory (batched PEEKs) for the signature; cache the shift per connection."""
    if id(db) in _offset: return _offset[id(db)]
    buf = bytearray()
    for a in range(start, end, 64 * 64):
        buf += b"".join(_peek_many(db, [((a + i * 64) & ~0xF, 64) for i in range(64)]))
    i = buf.find(A.SIGNATURE)
    if i < 0: raise RuntimeError("F-19 data segment not found - is EGAME running?")
    _offset[id(db)] = start + i - A.SIG_OLD
    return _offset[id(db)]


def find_events(db, lo=A.EVENT_SCAN_LO, hi=A.EVENT_SCAN_HI):
    """Phys address of the flight-event list (6-byte records [status, target, u16 time, cell x, cell y];
    END.EXE scores these): found by its start, the takeoff record 08 00 20 00 then a 64 s sample 09 00 40 00."""
    hit = _events.get(id(db))
    if isinstance(hit, int): return hit
    if hit is not None and time.monotonic() - hit[1] < EVENT_RETRY_S: return None
    buf = b"".join(b"".join(_peek_many(db, [(a, 64) for a in range(x, min(x + 4096, hi), 64)])) for x in range(lo, hi, 4096))
    i = buf.find(A.EVENT_START)
    while i >= 0 and buf[i + 6:i + 10] != A.EVENT_NEXT: i = buf.find(A.EVENT_START, i + 1)
    # 2026-10-29: a miss used to be cached for good. The first read on the runway, before the list holds its takeoff and
    # first sample records, then made read_events return [] all flight: the type-10 photo record (in memory ~1 s after
    # the frame in both Oct 28-29 flights) was never seen and the photo run flew on into the target.
    _events[id(db)] = (None, time.monotonic()) if i < 0 else lo + i
    return None if i < 0 else lo + i


# ------------------------------------------------------------------- readers as (requests, decoder) pairs
# Each _r_* gives the PEEK requests at offset o and a decoder of their replies, so the dict readers below and
# MemoryReader.snapshot (which merges the requests into one batch) read exactly the same bytes.
def _r_state(o):
    return [(A.ALT + o, 2), (A.HDG + o, 2), (A.SPD + o, 2), (A.POS + o, 10)], _d_state


def _state_ok(r):
    a, h, s, p = r
    return len(a) == 2 and len(h) == 2 and len(s) == 2 and len(p) == 10


def _d_state(r):
    a, h, s, p = r
    return dict(alt=_u16(a), spd=_u16(s), hdg=_u16(h) * 360 / 65536,
                x=struct.unpack_from("<I", p, 0)[0], y=struct.unpack_from("<I", p, 6)[0])


def _r_flight(o):
    return [(A.PITCH + o, 4), (A.FLAGS + o, 2), (A.LOCKED + o, 2), (A.OPP + o, 2), (A.RWY_BOX + o, 2),
            (A.THROTTLE + o, 1), (A.STATION + o, 2), (A.LOADOUT + o, 16), (A.HUD_LOCK + o, 2), (A.FUEL + o, 2)], _d_flight


def _d_flight(r):
    pr, f, lk, op, bx, th, st, lo, hl, fu = r
    pitch, roll = struct.unpack("<hh", pr)
    return dict(pitch=pitch * 360 / 65536, roll=roll * 360 / 65536, flags=_u16(f), locked=_u16(lk), opp=_u16(op),
                box=_u16(bx), throttle=th[0], station=_u16(st) & 3,
                loadout=[struct.unpack_from("<HH", lo, 4 * i) for i in range(4)], hud_lock=_u16(hl), fuel=_u16(fu))


_STATUS_WORDS = [A.CLOCK, A.FUEL, A.DMG_BITS, A.DMG_COUNT, A.VIS_IDS, A.RADAR_IDS, A.ALERTED, A.EMV_W, A.THREAT_COLOR,
                 A.AP_ALT, A.SPOOL, A.N_EVENTS, A.GUN_ROUNDS, A.NET_ALERT]
_STATUS_NAMES = ["clock", "fuel", "dmg_bits", "dmg_count", "vis_ids", "radar_ids", "alerted", "emv", "threat",
                 "ap_alt", "spool", "n_events", "gun_rounds", "net_alert"]


def _r_status(o):
    return [(a + o, 2) for a in _STATUS_WORDS] + [(A.CM_STORES + o, 6), (A.HUD_MSG + o, 40)], _d_status


def _d_status(r):
    *vals, cm, hud = r
    out = dict(zip(_STATUS_NAMES, (_u16(v) for v in vals)))
    out["flares"], out["chaff"], out["decoys"] = struct.unpack("<3H", cm)
    out["hud"] = hud.split(b"\0", 1)[0].decode("latin1")
    return out


def _r_aircraft(o):
    n = A.AIRCRAFT_N * A.AIRCRAFT_SZ
    return [(A.AIRCRAFT + o + k, min(60, n - k)) for k in range(0, n, 60)], _d_aircraft


def _d_aircraft(r):
    raw = b"".join(r); out = []
    for i in range(A.AIRCRAFT_N):
        x, y, alt = struct.unpack_from("<HHH", raw, i * A.AIRCRAFT_SZ)
        typ, fl = struct.unpack_from("<HH", raw, i * A.AIRCRAFT_SZ + 0x14)
        al = struct.unpack_from("<H", raw, i * A.AIRCRAFT_SZ + 0x20)[0]
        if x == 0 and y == 0: continue
        out.append(dict(slot=i, x=x, y=y, alt=alt, type=typ, name=A.AIRCRAFT_TYPES.get(typ, f"type {typ}"), flags=fl, alert=al))
    return out


def _r_records(o, n):
    return [(A.TARGETS + o + 64 * k, 64) for k in range((16 * n + 63) // 64)]


def _r_plane_alerts(o):
    return [(A.SIMOBJ + o + 0x24 * k + 0x20, 2) for k in range(20)]


def _d_alerts(recs, planes, n):
    sites = {k: struct.unpack_from("<H", recs, 16 * k + 10)[0] for k in range(n) if struct.unpack_from("<H", recs, 16 * k + 6)[0]}
    return sites, {k: v for k in range(20) if (v := struct.unpack_from("<H", planes, 2 * k)[0])}


def _d_site_records(recs, n):
    out = []
    for k in range(n):
        x, y, t, fl, al = struct.unpack_from("<HHHHH", recs, 16 * k + 2)
        if t: out.append(dict(record=k, x=x, y=y, type=t, flags=fl, alert=al, dead=bool(fl & 0x80), on=bool(fl & 2)))
    return out


def _r_waypoints(o):
    return [(A.WPT + o, 16), (A.WPIDX + o, 1)], lambda r: ([struct.unpack_from("<HH", r[0], 4 * k) for k in range(4)], r[1][0])


# ------------------------------------------------------------------------------------- the dict readers
def read_state(db, tries=10):
    """Player state. Short/empty replies (e.g. while a save state is loading) are retried, re-locating the data
    segment in case the game moved."""
    for k in range(tries):
        o = find_offset(db)
        reqs, dec = _r_state(o)
        r = _peek_many(db, reqs)
        if _state_ok(r): break
        if k == tries - 1: raise RuntimeError("player state unreadable - is F-19 in flight (EGAME running)?")
        time.sleep(1.0); _offset.pop(id(db), None)
        try: find_offset(db)
        except RuntimeError: pass
    return dec(r)


def read_waypoints(db):
    o = find_offset(db)
    t = _peek(db, A.WPT + o, 16)
    return [struct.unpack_from("<HH", t, 4 * k) for k in range(4)], _peek(db, A.WPIDX + o, 1)[0]


def read_sensitivity(db):
    """Keyboard sensitivity level 1-3 (1 = gentlest; the autopilot's tuning assumes 1)."""
    return _peek(db, A.SENS + find_offset(db), 1)[0] + 1


def read_flight(db):
    """pitch/roll (deg), flags word, locked target, opponent level, runway-box result, throttle, station, loadout,
    HUD lock, fuel."""
    reqs, dec = _r_flight(find_offset(db))
    return dec(_peek_many(db, reqs))


def read_trackcam(db):
    return _peek(db, A.TRACKCAM_MODE + find_offset(db), 1)[0]


def read_trackcam_view(db):
    """TrackcamView: screen id, view offset, night, B-blocked flags, cooldown and every record the B search walks
    (ds:9500 of them, 16 B each: ~1.2 KB in one pipelined batch)."""
    o = find_offset(db)
    head = _peek_many(db, [(A.CAM_SCREEN + o, 2), (A.CAM_VIEW + o, 2), (A.NIGHT + o, 2), (A.CAM_FLAGS + o, 2),
                           (A.CAM_COOLDOWN + o, 2), (A.N_SEARCHED + o, 2)])
    screen, view, night, flags, cool, n = (_u16(h) for h in head)
    n = min(n, 255)
    raw = b"".join(_peek_many(db, _r_records(o, n)))
    recs = tuple((k, *struct.unpack_from("<HH", raw, 16 * k + 2), raw[16 * k + 8]) for k in range(n))
    return TrackcamView(screen, view, night, flags, cool, recs)


def read_aircraft(db):
    """[dict(slot, x, y, alt, type, name, flags, alert)] of the 20 sim-object slots in use (x, y waypoint units)."""
    reqs, dec = _r_aircraft(find_offset(db))
    return dec(_peek_many(db, reqs))


def read_events(db, n=200):
    """[(type, flags, target, time)] up to the first empty record. type 10 = photographed, 0x80 primary, 0x40 secondary."""
    base = find_events(db)
    if base is None: return []
    raw = b"".join(_peek_many(db, [(base + k, 60) for k in range(0, 6 * n, 60)])); out = []
    for k in range(0, len(raw) - 5, 6):
        if raw[k] == 0: break
        out.append((raw[k] & 0x3F, raw[k] & 0xC0, raw[k + 1], struct.unpack_from("<H", raw, k + 2)[0]))
    return out


def read_briefing(db):
    """[(type, target record)] for the primary and secondary blocks (1 photo, 2 destroy, 3 supply, 4 landing)."""
    b = _peek(db, A.BRIEF + find_offset(db), 0x24)
    return [struct.unpack_from("<HH", b, 0x12 * k) for k in range(2)]


def read_projectiles(db):
    """Player projectile slots 8-11: raw 0x18-byte records."""
    return [_peek(db, A.PROJ + 0x18 * i + find_offset(db), 0x18) for i in range(8, 12)]


def read_target(db, index):
    """(x, y, destroyed) of target-table record `index`."""
    r = _peek(db, A.TARGETS + 16 * index + find_offset(db), 16)
    return struct.unpack_from("<H", r, 2)[0], struct.unpack_from("<H", r, 4)[0], bool(r[8] & 0x80)


def read_status(db):
    """Clock, fuel, damage, stores, ID counts, EMV and threat colour, game autopilot, spool, event count, HUD buffer."""
    reqs, dec = _r_status(find_offset(db))
    return dec(_peek_many(db, reqs))


def read_alerts(db):
    """{target record: alert 0-255} for radar sites (type != 0) and {plane slot: alert} for sim objects."""
    o = find_offset(db)
    n = _u16(_peek(db, A.N_TARGETS + o, 2))
    recs = b"".join(_peek_many(db, _r_records(o, n)))
    planes = b"".join(_peek_many(db, _r_plane_alerts(o)))
    return _d_alerts(recs, planes, n)


def read_gun(db):
    """(rounds, frame scale, bullet slots)."""
    o = find_offset(db)
    return tuple(_u16(v) for v in _peek_many(db, [(A.GUN_ROUNDS + o, 2), (A.FRAME_SCALE + o, 2), (A.GUN_SLOTS + o, 2)]))


def read_site_records(db):
    """[dict(record, x, y, type, flags, alert, dead, on)] of target records with a radar type (+6 != 0), dead or alive."""
    o = find_offset(db)
    n = min(_u16(_peek(db, A.N_TARGETS + o, 2)), 255)
    recs = b"".join(_peek_many(db, _r_records(o, n)))
    return _d_site_records(recs, n)


def read_event_list(db, start=0):
    """(count, records start..count-1) of the flight-event list END.EXE scores (ds:8E46, count ds:632E): records
    (clock, x >> 7, y >> 7, type, flags 0x80 primary / 0x40 secondary, target). A count below `start` = a new flight."""
    o = find_offset(db)
    n = min(_u16(_peek(db, A.N_EVENTS + o, 2)), 255)
    if n <= start: return n, []
    a0, ln = A.EVENTS + o + 6 * start, 6 * (n - start)
    raw = b"".join(_peek_many(db, [(a0 + k, min(60, ln - k)) for k in range(0, ln, 60)]))
    return n, [(c, x, y, tf & 0x3F, tf & 0xC0, tg) for c, x, y, tf, tg in struct.iter_unpack("<HBBBB", raw)]


def read_sites(db):
    """{record: (x, y)} of live radar sites (target records with a radar type at +6, destroyed bit clear)."""
    o = find_offset(db)
    n = _u16(_peek(db, A.N_TARGETS + o, 2))
    recs = b"".join(_peek_many(db, _r_records(o, n)))
    return {k: struct.unpack_from("<HH", recs, 16 * k + 2) for k in range(n)
            if struct.unpack_from("<H", recs, 16 * k + 6)[0] and not recs[16 * k + 8] & 0x80}


def enemy_projectiles_raw(db):
    """Enemy projectile slots 0-7 as one 192-byte block (missile_game.parse decodes it)."""
    o = find_offset(db)
    return b"".join(_peek_many(db, [(A.PROJ + o + k, min(64, 0xC0 - k)) for k in range(0, 0xC0, 64)]))


def missile_world_memory(db, full=True):
    """Everything MissileWatch reads, in one pipelined batch (a second one with full=True for the site records, sim
    aircraft and territory grid). Returns get(ds_offset, n) -> bytes (zeros where not read), the accessor
    analysis/missile_game.parse takes."""
    o = find_offset(db)

    def fetch(ranges):
        reqs = [(A.DS + a + o + k, min(60, n - k)) for a, n in ranges for k in range(0, n, 60)]
        got = _peek_many(db, reqs); mem = {}
        for (ph, n), b in zip(reqs, got): mem[ph - A.DS - o] = b
        return mem
    mem = fetch(A.MISSILE_WORLD_DS)
    if full:
        F = A.MISSILE_WORLD_FULL_DS
        n = min(struct.unpack("<H", mem[0x9C78])[0], 120)
        mem.update(fetch([(F["targets"], 16 * n), (F["simobj"], 0x24 * 20), (F["grid"], 256)]))
    starts = sorted(mem)

    def get(a, n):
        out = bytearray()
        while len(out) < n:
            s = max((x for x in starts if x <= a + len(out)), default=None)
            if s is None or a + len(out) >= s + len(mem[s]): out += bytes(n - len(out)); break   # not read: zeros
            off = a + len(out) - s; out += mem[s][off: off + n - len(out)]
        return bytes(out)
    return get


def read_time_mult(db):
    """Game time acceleration (old 0x410AA: 1 normal, 2 accelerated); 1.0 when the byte is out of 1..8 (nav.time_mult)."""
    m = _peek(db, A.TIME_MULT + find_offset(db), 1)
    return float(m[0]) if m and 1 <= m[0] <= 8 else 1.0


def read_frame(db):
    """The u16 frame counter ds:5520 (wraps at 65536)."""
    return _u16(_peek(db, A.FRAME + find_offset(db), 2))


def read_frame_fs(db):
    """(frame counter ds:5520, frame scale ds:4EFC) in one round trip: what core.clock.FrameClock is fed."""
    o = find_offset(db)
    f, fs = _peek_many(db, [(A.FRAME + o, 2), (A.FRAME_SCALE + o, 2)])
    return _u16(f), _u16(fs)


def read_memory(db, lo=0, hi=0xA0000):
    """Guest memory [lo, hi) (all of conventional memory by default) in 64-byte PEEKs, 64 a round trip."""
    buf = bytearray()
    for a in range(lo, hi, 64 * 64):
        buf += b"".join(_peek_many(db, [(a + i * 64, 64) for i in range(min(64, (hi - a + 63) // 64))]))
    return bytes(buf[:hi - lo])


# ------------------------------------------------------------------------------------------- MemoryReader
class MemoryReader:
    """The pilot's one read path: snapshot() once per tick. `emulator` is an f19.emulator.Emulator (or anything with
    peek_many). `world` (memory.mission.World) is needed only by mission()."""

    def __init__(self, emulator, world=None):
        self.emu, self.world = emulator, world

    # --- EGAME
    def offset(self, refresh=False):
        if refresh: _offset.pop(id(self.emu), None)
        return find_offset(self.emu)

    def snapshot_dicts(self, threats=True):
        """The old dicts, read in two pipelined round trips: dict(state, flight, status, waypoints, trackcam,
        sensitivity[, aircraft, alerts, sites])."""
        o = self.offset()
        parts = [("state",) + _r_state(o), ("flight",) + _r_flight(o), ("status",) + _r_status(o),
                 ("waypoints",) + _r_waypoints(o),
                 ("trackcam", [(A.TRACKCAM_MODE + o, 1)], lambda r: r[0][0]),
                 ("sensitivity", [(A.SENS + o, 1)], lambda r: r[0][0] + 1)]
        if threats:
            parts += [("aircraft",) + _r_aircraft(o), ("plane_alerts", _r_plane_alerts(o), lambda r: b"".join(r)),
                      ("n_targets", [(A.N_TARGETS + o, 2)], lambda r: _u16(r[0]))]
        reqs = [q for _, rq, _ in parts for q in rq]
        got = _peek_many(self.emu, reqs)
        out, i = {}, 0
        for name, rq, dec in parts:
            r = got[i:i + len(rq)]; i += len(rq)
            if name == "state" and not _state_ok(r):
                raise RuntimeError("player state unreadable - is F-19 in flight (EGAME running)?")
            out[name] = dec(r)
        if threats:
            n = out.pop("n_targets"); planes = out.pop("plane_alerts")
            recs = b"".join(_peek_many(self.emu, _r_records(o, n)))
            out["alerts"] = _d_alerts(recs, planes, n)
            m = min(n, 255)
            out["sites"] = _d_site_records(recs[:16 * m], m)
        return out

    def snapshot(self, threats=True):
        """GameState (f19.core.types) from snapshot_dicts. Missiles are not in it: they need rules.missiles
        (missile_world() gives the raw accessor)."""
        d = self.snapshot_dicts(threats)
        return GameState.from_dicts(d["state"], d["flight"], d["status"], d.get("alerts"), d.get("aircraft"),
                                    d.get("sites"), d["waypoints"], d["trackcam"], d["sensitivity"])

    def time_mult(self): return read_time_mult(self.emu)

    def frame(self): return read_frame(self.emu)

    def game_time(self):
        """Game seconds from the clock ds:6630 (1 tick = 2 s)."""
        return _u16(_peek(self.emu, A.CLOCK + self.offset(), 2)) * CLOCK_TICK_S

    def events(self, n=200): return read_events(self.emu, n)

    def event_list(self, start=0): return read_event_list(self.emu, start)

    def missile_world(self, full=True): return missile_world_memory(self.emu, full)

    def target(self, index): return read_target(self.emu, index)

    # --- START / the mission
    def memory(self, lo=0, hi=0xA0000):
        return read_memory(self.emu, lo, hi)

    def start_ds(self, mem=None):
        """Phys of START's data segment, or None when START isn't running (needs a full memory read when mem is None)."""
        return M.start_ds(mem if mem is not None else self.memory())

    def mission_dict(self, theater=None, mem=None, world=None):
        """read_mission.py's dict (mission_mem.json), with "theater" set."""
        world = world or self.world
        if world is None: raise ValueError("mission() needs a world (memory.mission.World: .WLD bytes, names, radars)")
        mem = mem if mem is not None else self.memory()
        th = M.detect_theater(mem, world) if theater is None else theater
        d = M.parse(mem, th, world=world)
        d["theater"] = M.theater_code(th)
        return d

    def mission(self, theater=None, mem=None, world=None):
        return Mission.from_mission_json(self.mission_dict(theater, mem, world))


# ----------------------------------------------------------------------------------------------- replay
class SnapshotMemory:
    """Answers peek_many from one memlog snapshot: the windows' bytes at their physical addresses, zeros elsewhere."""

    def __init__(self, windows, raw=b""):
        self.windows, self.raw = [tuple(w) for w in windows], raw

    def load(self, raw): self.raw = raw

    def _read(self, phys, n):
        out, pos = bytearray(n), 0
        for _, lo, hi in self.windows:
            a, b = max(lo, phys), min(hi, phys + n)
            if a < b: out[a - phys:b - phys] = self.raw[pos + a - lo:pos + b - lo]
            pos += hi - lo
        return bytes(out)

    def peek_many(self, reqs):
        return [self._read(a, n) for a, n in reqs]

    def peek(self, addr, n):
        return self._read(addr, n)


class ReplayReader(MemoryReader):
    """MemoryReader over a nav --memlog run directory: snapshot() etc. answer from the current snapshot.

      rr = ReplayReader(run_dir)
      for t in rr.times(): rr.seek(t); state = rr.snapshot()
      for t, state in rr.states(): ...
    The EGAME offset comes from windows.json (no scan)."""

    def __init__(self, run_dir, world=None):
        self.dir = Path(run_dir)
        w = json.loads((self.dir / "windows.json").read_text())
        self.windows = [tuple(x) for x in w["windows"]]
        self.snap_files = sorted(self.dir.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))
        self._times = [float(f.stem[5:]) for f in self.snap_files]
        super().__init__(SnapshotMemory(self.windows), world)
        _offset[id(self.emu)] = self.fixed_offset = w["offset"]
        self.t = None
        if self.snap_files: self.seek_index(0)

    def times(self): return list(self._times)

    def __len__(self): return len(self.snap_files)

    def seek_index(self, i):
        self.emu.load(self.snap_files[i].read_bytes()); self.t = self._times[i]
        _events.pop(id(self.emu), None)
        return self

    def seek(self, t):
        """The snapshot nearest to time t (epoch s, the snapshot's name)."""
        import bisect
        i = bisect.bisect_left(self._times, t)
        if i >= len(self._times) or (i > 0 and t - self._times[i - 1] < self._times[i] - t): i -= 1
        return self.seek_index(max(0, i))

    def offset(self, refresh=False):
        return self.fixed_offset

    def states(self, threats=True):
        """(t, GameState) per snapshot; snapshots whose player state reads empty (x == 0) are skipped."""
        for i in range(len(self.snap_files)):
            self.seek_index(i)
            s = self.snapshot(threats)
            if s.pos.x: yield self.t, s

    def memory(self, lo=0, hi=0xA0000):
        return self.emu._read(lo, hi - lo)
