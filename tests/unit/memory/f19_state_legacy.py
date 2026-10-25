"""Frozen copy of scripts/f19_state.py before WP-4 (git 78752f4), for the equivalence tests. Do not edit."""
"""Live player state from F-19 memory via the DOSBox-X automation socket.

Addresses below are physical addresses from the session in which they were
found (2026-10-07/09, no LOADFIX). Where DOS loads the game depends on what is
resident below it (e.g. LOADFIX moved everything +0x10420), so the actual base
is located at startup by finding a fixed text string that sits just before the
waypoint table ("...o delivered\\0Safe Landing" at old phys 0x403B0).

  0x40250 u16  altitude, ft            (== HUD altitude, r=1.0000; copy at 0x451C6)
  0x440CA u16  airspeed, knots         (== HUD airspeed, r=1.0000)
  0x4024A u16  heading, 65536 = 360 deg
  0x451B8 u32  X position (east +)      2048 units per km
  0x451BE u32  Y position (south +)
  0x403CA 4 x (u16 X, u16 Y)  waypoint table (= position >> 5, 64 per km)
  0x4041C u8   selected waypoint index (0-3)
  0x417DA u8   keyboard sensitivity - 1 (0..2; Insert cycles it 1->2->3->1; found 2026-10-14
               from the HUD's "KYBD SENSITIVITYn" message during a memlog flight)
Game distance metric: max(|dx|,|dy|) + min(|dx|,|dy|)/2.
"""
import struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox

SIGNATURE, SIG_OLD = b"o delivered\x00Safe Landing", 0x403B0
ALT, SPD, HDG, POS, WPT, WPIDX, SENS = 0x40250, 0x440CA, 0x4024A, 0x451B8, 0x403CA, 0x4041C, 0x417DA
_offset = {}

def _peek(db, phys, n):
    return db.peek(f"{phys >> 4:X}", f"{phys & 15:X}", n)

def _peek_many(db, reqs):
    """Pipelined PEEKs: [(phys, n)] -> [bytes]. Sends all, then reads all replies, so a batch
    costs about one round trip instead of one per read (the --auto loop needs ~15 reads a cycle)."""
    for phys, n in reqs: db._send(f"PEEK {phys >> 4:X}:{phys & 15:X} {n}")
    return [bytes.fromhex(db._recv_line().split(" ", 1)[1]) for _ in reqs]

def find_offset(db, start=0x10000, end=0xA0000):
    """Scan conventional memory (batched PEEKs) for the signature; cache the shift."""
    if id(db) in _offset: return _offset[id(db)]
    buf = bytearray()
    for a in range(start, end, 64 * 64):
        for i in range(64): db._send(f"PEEK {(a + i * 64) >> 4:X}:0 64")
        for i in range(64): buf += bytes.fromhex(db._recv_line().split(" ", 1)[1])
    i = buf.find(SIGNATURE)
    if i < 0: raise RuntimeError("F-19 data segment not found - is EGAME running?")
    _offset[id(db)] = start + i - SIG_OLD
    return _offset[id(db)]

def read_state(db, tries=10):
    """Player state. Short/empty replies (e.g. while a save state is loading) are retried,
    re-locating the data segment in case the game moved."""
    for k in range(tries):
        o = find_offset(db)
        a, h, s, p = _peek_many(db, [(ALT + o, 2), (HDG + o, 2), (SPD + o, 2), (POS + o, 10)])
        if len(a) == 2 and len(h) == 2 and len(s) == 2 and len(p) == 10: break
        if k == tries - 1: raise RuntimeError("player state unreadable - is F-19 in flight (EGAME running)?")
        time.sleep(1.0); _offset.pop(id(db), None)
        try: find_offset(db)
        except RuntimeError: pass
    return dict(alt=struct.unpack("<H", a)[0], spd=struct.unpack("<H", s)[0],
                hdg=struct.unpack("<H", h)[0] * 360 / 65536,
                x=struct.unpack_from("<I", p, 0)[0], y=struct.unpack_from("<I", p, 6)[0])

def read_waypoints(db):
    o = find_offset(db)
    t = _peek(db, WPT + o, 16)
    return [struct.unpack_from("<HH", t, 4 * k) for k in range(4)], _peek(db, WPIDX + o, 1)[0]

def read_sensitivity(db):
    """Keyboard sensitivity level 1-3 (1 = gentlest; the autopilot's tuning assumes 1)."""
    return _peek(db, SENS + find_offset(db), 1)[0] + 1

# Flight state for the takeoff / attack / landing phases (scripts/phases.py). These come
# from the EGAME disassembly (FINDINGS.md "Weapon release", "Photo, lock and guidance",
# "Landing rules"): ds offsets, with ds:46E0 = old phys 0x40250, so old phys = 0x3BB70 + ds.
# NOT yet checked live; scripts/probe_controls.py verifies them.
DS = 0x3BB70
PITCH, ROLL = DS + 0x46DC, DS + 0x46DE          # i16, 65536 = 360 deg, pitch + = nose up
FLAGS = DS + 0x684C                              # u16: 0x01 gear UP, 0x02 flaps, 0x04 bay open, 0x08 brakes
LOCKED = DS + 0x5526                             # u16 locked ground target (target table index)
OPP = DS + 0x4EF0                                # u16 opponent level 0 Green .. 3 Elite
RWY_BOX = DS + 0x5534                            # runway-box test result (nonzero = inside)
PROJ = DS + 0x53FC                               # projectile slots, 0x18 each; player 8-11
TARGETS = DS + 0x80AA                            # 16-byte target records (.WLD order): +2 X, +4 Y, +8 bit 0x80 destroyed
GEAR_UP, FLAPS, BAY, BRAKES = 0x01, 0x02, 0x04, 0x08
# Measured live on the runway 2026-10-17 (probe_controls.py): toggle bits above all confirmed with
# 0.2 s taps; throttle percent (= +5/+10 steps, Shift+= 100, Shift+- 0; 0x44168 follows it with a lag);
# selected weapon station 0-3 (Space cycles); loadout 4 x (u16 weapon index, u16 count), weapon
# indices as analysis/ordnance.py (0 AIM-9M, 5 Maverick, 16 Camera, 17 Extra Fuel; the user's loadout
# Camera, Extra Fuel, 2 Maverick, 4 Sidewinder read back exactly); nose-gun rounds u16 (650 at start); briefing
# blocks (EGAME ds:8794: +0 type, +2 target record = TARGETS index, confirmed against the briefing).
THROTTLE, STATION, LOADOUT, GUN_ROUNDS, BRIEF = 0x40262, 0x40A5A, 0x40A4A, 0x40A5C, 0x44304
HUD_LOCK = DS + 0x6342                           # "Missile Lock" (disassembly; logged, not yet trusted)

def read_flight(db):
    """pitch/roll (deg), flags word, locked target, opponent level, runway-box result."""
    o = find_offset(db)
    pr, f, lk, op, bx, th, st, lo, hl, fu = _peek_many(db, [(PITCH + o, 4), (FLAGS + o, 2), (LOCKED + o, 2), (OPP + o, 2),
                                                    (RWY_BOX + o, 2), (THROTTLE + o, 1), (STATION + o, 2), (LOADOUT + o, 16),
                                                    (HUD_LOCK + o, 2), (FUEL + o, 2)])
    pitch, roll = struct.unpack("<hh", pr)
    return dict(pitch=pitch * 360 / 65536, roll=roll * 360 / 65536, flags=struct.unpack("<H", f)[0],
                locked=struct.unpack("<H", lk)[0], opp=struct.unpack("<H", op)[0], box=struct.unpack("<H", bx)[0],
                throttle=th[0], station=struct.unpack("<H", st)[0] & 3,
                loadout=[struct.unpack_from("<HH", lo, 4 * i) for i in range(4)], hud_lock=struct.unpack("<H", hl)[0],
                fuel=struct.unpack("<H", fu)[0])

TRACKCAM_MODE, AIR_GROUND = 0x455A6, 2              # F2 cycles 2 air-to-ground -> 0 air-to-air -> 1 nav (2026-10-18)

def read_trackcam(db):
    return _peek(db, TRACKCAM_MODE + find_offset(db), 1)[0]

_events = {}
def find_events(db, lo=0x40000, hi=0x80000):
    """Phys address of the flight-event list (6-byte records [status, target, u16 time, cell x, cell y];
    END.EXE scores these): found by its start, the takeoff record 08 00 20 00 then a 64 s sample 09 00 40 00."""
    if id(db) in _events: return _events[id(db)]
    buf = b"".join(b"".join(_peek_many(db, [(a, 64) for a in range(x, min(x + 4096, hi), 64)])) for x in range(lo, hi, 4096))
    i = buf.find(bytes.fromhex("080020000000"[:8]))
    while i >= 0 and buf[i + 6:i + 10] != bytes.fromhex("09004000"): i = buf.find(bytes.fromhex("08002000"), i + 1)
    _events[id(db)] = None if i < 0 else lo + i
    return _events[id(db)]

AIRCRAFT, AIRCRAFT_N, AIRCRAFT_SZ = DS + 0x8854, 20, 0x24   # sim objects (FINDINGS "Visual ID"): X, Y, alt, type, flags, alert
AIRCRAFT_TYPES = {0: "MIG-23", 1: "MIG-25", 2: "MIG-29", 3: "MIG-31", 4: "Su-27", 5: "IL-76", 6: "F-4E", 7: "F-14",
                  8: "F-18", 9: "An-72", 10: "F-18", 11: "MIG-23"}       # EGAME ds:49B0 + 32 x type

def read_aircraft(db):
    """[dict(slot, x, y, alt, type, name, flags, alert)] of the 20 sim-object slots in use (x, y waypoint units, as
    the player's position >> 5). Memlog-confirmed 2026-10-21: the MiG-31 that shot the F-19 down had alert 80 -> 160
    -> 240, flags 0x8b -> 0x488b (0x4000 = alert > 0xC0 / network alert, 0x0800 = visual ID)."""
    o = find_offset(db); n = AIRCRAFT_N * AIRCRAFT_SZ
    raw = b"".join(_peek_many(db, [(AIRCRAFT + o + k, min(60, n - k)) for k in range(0, n, 60)]))
    out = []
    for i in range(AIRCRAFT_N):
        x, y, alt = struct.unpack_from("<HHH", raw, i * AIRCRAFT_SZ)
        typ, fl = struct.unpack_from("<HH", raw, i * AIRCRAFT_SZ + 0x14)
        al = struct.unpack_from("<H", raw, i * AIRCRAFT_SZ + 0x20)[0]
        if x == 0 and y == 0: continue
        out.append(dict(slot=i, x=x, y=y, alt=alt, type=typ, name=AIRCRAFT_TYPES.get(typ, f"type {typ}"), flags=fl, alert=al))
    return out

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
    b = _peek(db, BRIEF + find_offset(db), 0x24)
    return [struct.unpack_from("<HH", b, 0x12 * k) for k in range(2)]

def read_projectiles(db):
    """Player projectile slots 8-11: raw 0x18-byte records (compare before/after a launch)."""
    return [_peek(db, PROJ + 0x18 * i + find_offset(db), 0x18) for i in range(8, 12)]

def read_target(db, index):
    """(x, y, destroyed) of target-table record `index`."""
    r = _peek(db, TARGETS + 16 * index + find_offset(db), 16)
    return struct.unpack_from("<H", r, 2)[0], struct.unpack_from("<H", r, 4)[0], bool(r[8] & 0x80)

# Flight-log variables (FINDINGS.md "Flight-log variables", EGAME disassembly 2026-10-18). Clock and fuel checked
# against memlog snapshots; damage, IDs and alert levels are static-only until a flight exercises them.
# Clock: 1 tick = 2 game s. Fuel lb. Damage bits: 0x01 missile warning, 0x02 engine, 0x04 flight controls, 0x08 autopilot/INS,
# 0x10 bay jammed open, 0x20 fuel leak, 0x40 countermeasures; damage count starts at 2, +opp+1 per hit.
CLOCK, FUEL, DMG_BITS, DMG_COUNT = DS + 0x6630, DS + 0x4ED0, DS + 0x4ECE, DS + 0x95BE
CM_STORES = DS + 0x4ED4                          # u16 flares, chaff, decoys
VIS_IDS, RADAR_IDS, ALERTED = DS + 0x966E, DS + 0x9672, DS + 0x9674
EMV_W, THREAT_COLOR, AP_ALT, SPOOL = DS + 0x551C, DS + 0x552C, DS + 0x4EEE, DS + 0x85F8
EVENTS, N_EVENTS = DS + 0x8E46, DS + 0x632E      # 6-byte records [u16 clock, x>>7, y>>7, type|flags, target]
N_TARGETS, SIMOBJ = DS + 0x9C78, DS + 0x8854     # target records +0x0A = radar alert; 20 planes x 0x24, +0x20 alert
HUD_MSG = DS + 0x65C6                            # message build buffer; also number-formatting scratch
ECM, IRJ = 0x10, 0x20                            # FLAGS bits

NET_ALERT = DS + 0x95CE                          # network alert level (>= 0x80 = recent network alert, radar_game.duty_active)

def read_status(db):
    """Clock, fuel, damage, stores, ID counts, EMV and threat colour, game autopilot, spool, event count, HUD buffer."""
    o = find_offset(db)
    w = [CLOCK, FUEL, DMG_BITS, DMG_COUNT, VIS_IDS, RADAR_IDS, ALERTED, EMV_W, THREAT_COLOR, AP_ALT, SPOOL, N_EVENTS, GUN_ROUNDS, NET_ALERT]
    *vals, cm, hud = _peek_many(db, [(a + o, 2) for a in w] + [(CM_STORES + o, 6), (HUD_MSG + o, 40)])
    r = dict(zip(["clock", "fuel", "dmg_bits", "dmg_count", "vis_ids", "radar_ids", "alerted", "emv", "threat",
                  "ap_alt", "spool", "n_events", "gun_rounds", "net_alert"], (struct.unpack("<H", v)[0] for v in vals)))
    r["flares"], r["chaff"], r["decoys"] = struct.unpack("<3H", cm)
    r["hud"] = hud.split(b"\0", 1)[0].decode("latin1")
    return r

def read_alerts(db):
    """{target record: alert 0-255} for radar sites (type != 0) and {plane slot: alert} for sim objects."""
    o = find_offset(db)
    n = struct.unpack("<H", _peek(db, N_TARGETS + o, 2))[0]
    recs = b"".join(_peek_many(db, [(TARGETS + o + 64 * k, 64) for k in range((16 * n + 63) // 64)]))
    planes = b"".join(_peek_many(db, [(SIMOBJ + o + 0x24 * k + 0x20, 2) for k in range(20)]))
    sites = {k: struct.unpack_from("<H", recs, 16 * k + 10)[0] for k in range(n) if struct.unpack_from("<H", recs, 16 * k + 6)[0]}
    return sites, {k: v for k in range(20) if (v := struct.unpack_from("<H", planes, 2 * k)[0])}

# Gun (FINDINGS.md "Guns", flight-tested 2026-10-18/19): frame scale sets bullet speed (112//fs units a frame) and the
# rounds per bullet (40//fs); slot count = clamp(2 fs, 3, 16) caps the reach at 2*slots frames.
FRAME_SCALE, GUN_SLOTS = DS + 0x4EFC, DS + 0x855E

def read_gun(db):
    """(rounds, frame scale, bullet slots)."""
    o = find_offset(db)
    return tuple(struct.unpack("<H", v)[0] for v in _peek_many(db, [(GUN_ROUNDS + o, 2), (FRAME_SCALE + o, 2), (GUN_SLOTS + o, 2)]))

def read_site_records(db):
    """[dict(record, x, y, type, flags, alert, dead, on)] of target records with a radar type (+6 != 0), dead or alive.
    on = switched on in this duty window (flags bit 0x02, radar_game.duty_active)."""
    o = find_offset(db)
    n = min(struct.unpack("<H", _peek(db, N_TARGETS + o, 2))[0], 255)
    recs = b"".join(_peek_many(db, [(TARGETS + o + 64 * k, 64) for k in range((16 * n + 63) // 64)]))
    out = []
    for k in range(n):
        x, y, t, fl, al = struct.unpack_from("<HHHHH", recs, 16 * k + 2)
        if t: out.append(dict(record=k, x=x, y=y, type=t, flags=fl, alert=al, dead=bool(fl & 0x80), on=bool(fl & 2)))
    return out

def read_event_list(db, start=0):
    """(count, records start..count-1) of the flight-event list END.EXE scores (ds:8E46, count ds:632E):
    records (clock, x >> 7, y >> 7, type, flags 0x80 primary / 0x40 secondary, target). A count below `start`
    means a new flight."""
    o = find_offset(db)
    n = min(struct.unpack("<H", _peek(db, N_EVENTS + o, 2))[0], 255)
    if n <= start: return n, []
    a0, ln = EVENTS + o + 6 * start, 6 * (n - start)
    raw = b"".join(_peek_many(db, [(a0 + k, min(60, ln - k)) for k in range(0, ln, 60)]))
    return n, [(c, x, y, tf & 0x3F, tf & 0xC0, tg) for c, x, y, tf, tg in struct.iter_unpack("<HBBBB", raw)]

def read_sites(db):
    """{record: (x, y)} of live radar sites (target records with a radar type at +6, destroyed bit clear)."""
    o = find_offset(db)
    n = struct.unpack("<H", _peek(db, N_TARGETS + o, 2))[0]
    recs = b"".join(_peek_many(db, [(TARGETS + o + 64 * k, 64) for k in range((16 * n + 63) // 64)]))
    return {k: struct.unpack_from("<HH", recs, 16 * k + 2) for k in range(n)
            if struct.unpack_from("<H", recs, 16 * k + 6)[0] and not recs[16 * k + 8] & 0x80}

def read_enemy_missiles(db):
    """Live enemy missiles (projectile slots 0-7, one 192-byte read): [analysis/missile_game.Missile]."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis")); import missile_game as G
    o = find_offset(db)
    raw = b"".join(_peek_many(db, [(PROJ + o + k, min(64, 0xC0 - k)) for k in range(0, 0xC0, 64)]))
    return G.parse(lambda a, n: raw[a - 0x53FC: a - 0x53FC + n] if 0x53FC <= a < 0x54BC else bytes(max(n, 2)))["missiles"]

def read_missile_world(db, full=True):
    """Everything MissileWatch needs (analysis/missile_game.parse): enemy slots 0-7, CM slots ds:520A + 12k, stores
    ds:4ED4, flags ds:684C, net timer ds:5522 / ds:6EAA, homing count ds:7F96, fs ds:4EFC, frame ds:5520, player, damage,
    and with full=True the site records (+8 bit 0x10 seen, +0x0C pulse countdown), sim aircraft (+0x16 bit 8) and the
    territory cell map ds:85FC. One pipelined batch (~50 PEEKs with full, ~12 without)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis")); import missile_game as G
    o = find_offset(db)
    rng = [(0x46D8, 0x10), (0x4EC0, 0x40), (0x5200, 0x40), (0x53FC, 0xC0), (0x5520, 8), (0x6630, 2), (0x684C, 2),
           (0x6EAA, 2), (0x7F94, 4), (0x94EE, 2), (0x94FE, 2), (0x95BE, 2), (0x9C78, 2)]
    def fetch(ranges):
        reqs = [(DS + a + o + k, min(60, n - k)) for a, n in ranges for k in range(0, n, 60)]
        got = _peek_many(db, reqs); mem = {}
        for (ph, n), b in zip(reqs, got): mem[ph - DS - o] = b
        return mem
    mem = fetch(rng)
    if full:
        n = min(struct.unpack("<H", mem[0x9C78])[0], 120)
        mem.update(fetch([(G.TARGETS, 16 * n), (G.SIMOBJ, 0x24 * 20), (G.GRID, 256)]))
    starts = sorted(mem)
    def get(a, n):
        out = bytearray()
        while len(out) < n:
            s = max((x for x in starts if x <= a + len(out)), default=None)
            if s is None or a + len(out) >= s + len(mem[s]): out += bytes(n - len(out)); break   # not read: zeros
            off = a + len(out) - s; out += mem[s][off: off + n - len(out)]
        return bytes(out)
    return G.parse(get)

if __name__ == "__main__":
    with Dosbox() as db:
        print(f"memory offset {find_offset(db):+#x}")
        print("waypoints:", read_waypoints(db))
        for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 3):
            st = read_state(db)
            print(f"alt {st['alt']:5d} ft  spd {st['spd']:3d} kt  hdg {st['hdg']:5.1f}  x {st['x']:9d}  y {st['y']:9d}")
            time.sleep(0.5)
