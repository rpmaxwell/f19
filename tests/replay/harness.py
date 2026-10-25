"""Replay harness: feed recorded flights through the pure reactions / phase state machines and compare their decisions
with what nav logged.

Sources (fixtures/replay, built by make_fixtures.py):
  memlog snapshots   game memory, one every ~1-1.3 s of real time (MemLog spreads a 49 KB read over several nav cycles,
                     so a snapshot is not atomic and is named by the MIDPOINT of its read window)
  nav CSV            one row per nav cycle (~0.2-0.3 s with --memlog): inputs (x, y, alt, spd, hdg, tscale) and the
                     decisions (want, phase, attack_state, escape, fighter, ...) AFTER every override of that cycle
  events.txt         phase events with epoch times ("takeoff done", "terrain escape: ...", "... attack start")

Replays (each returns a metrics dict; tests/replay/baseline.json freezes them):
  terrain_escape     TerrainEscape on EVERY nav CSV row (all its inputs are logged), probe = nav's terrain_ahead (copied
                     below) on the route's terrain.npz. Dense and close to exact.
  fighter_watch      FighterWatch on every snapshot: aircraft from the snapshot (f19_state.read_aircraft through SnapDB),
                     player state from the snapshot, compared with the nav row nearest in time.
  takeoff / attack   phases.Takeoff / phases.Attack open-loop on snapshots: a fake connection answers the reads from the
                     snapshot and swallows the key presses; the recorded memory already shows what nav's real presses did.
  missile_watch      MissileWatch on snapshots in act mode. No fixture flight logged MissileWatch's decisions (the memlog
                     runs predate it; the flights with m_action columns have no memlog), so this one is a frozen
                     self-baseline (decision digest + first-prediction-vs-real), not a comparison with nav.

Known limits (why agreement is not 100 %, by design of the data, not a bug):
  - nav's route want before a reaction's override isn't logged: route_want = the logged want of the last row where no
    reaction was active (stale by the length of the reaction, seconds to minutes for FighterWatch).
  - snapshots are sparser than nav cycles: state machines with timers (CLEAR_S, next_ok, settle) step at ~1 Hz.
  - the player state in a snapshot is up to ~0.5 s off the nav row it is compared with.
"""
import bisect, csv, json, math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import product

S = product.settings()
FIX = S.data / "fixtures" / "replay"
K = 64.0


def wrap(d): return (d + 180) % 360 - 180


def have_fixtures() -> bool:
    return (FIX / "manifest.json").exists()


def manifest() -> dict:
    return json.loads((FIX / "manifest.json").read_text())


# ---------------------------------------------------------------------------------------------------- fake connection
class SnapDB:
    """A stand-in for the automation connection that answers PEEKs from one memlog snapshot (as nav_tui.SnapDB) and
    records the keys a phase presses instead of sending them."""
    def __init__(self, windows):
        self.windows, self.raw, self.q, self.keys, self.now = windows, b"", [], [], 0.0
    def load(self, raw, now=0.0): self.raw, self.now = raw, now
    def _read(self, phys, n):
        out, pos = bytearray(n), 0
        for _, lo, hi in self.windows:
            if lo <= phys < hi or lo < phys + n <= hi:
                a, b = max(lo, phys), min(hi, phys + n)
                out[a - phys:b - phys] = self.raw[pos + a - lo:pos + b - lo]
            pos += hi - lo
        return bytes(out)
    def _send(self, cmd):
        _, addr, n = cmd.split(); seg, off = addr.split(":")
        self.q.append("PEEK-REPLY " + self._read(int(seg, 16) * 16 + int(off, 16), int(n)).hex())
    def _recv_line(self): return self.q.pop(0)
    def peek(self, seg, off, n): return self._read(int(seg, 16) * 16 + int(off, 16), n)
    # keys: recorded with the replay time
    def tap(self, key, hold_secs=0.05): self.keys.append((self.now, key))
    def scan_tap(self, code, hold_secs=0.5, shift=False): self.keys.append((self.now, f"scan{code:#x}{'+shift' if shift else ''}"))
    def key_down(self, key): self.keys.append((self.now, key + "+"))
    def key_up(self, key): self.keys.append((self.now, key + "-"))


class ReplayClock:
    """Stands in for the `time` module inside phases (set_toggle's settle timer reads time.time())."""
    def __init__(self): self.t = 0.0
    def time(self): return self.t
    def sleep(self, s): self.t += s


# ---------------------------------------------------------------------------------------------------- one flight
@dataclass
class Run:
    run: str
    nav: str
    plan: str
    windows_cover: list
    dir: Path = field(init=False)
    def __post_init__(self):
        self.dir = FIX / "logs" / "memlog" / self.run
        w = json.loads((self.dir / "windows.json").read_text())
        self.offset, self.windows = w["offset"], [tuple(x) for x in w["windows"]]
        self.snap_files = sorted(self.dir.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))
        self.rows = [r for r in csv.DictReader(open(FIX / "logs" / "nav" / f"{self.nav}.csv")) if r.get("x")]
        self.row_t = [float(r["t"]) for r in self.rows]
        self.events = []
        ev = FIX / "logs" / "nav" / f"{self.nav}.events.txt"
        for line in (ev.read_text().splitlines() if ev.exists() else []):
            t, _, text = line.partition(" ")
            try: self.events.append((float(t), text))
            except ValueError: pass
        self.plan_dir = FIX / "plan" / self.plan
        self.route = json.loads((self.plan_dir / "route.json").read_text())
        self.mission = json.loads((self.plan_dir / "mission_mem.json").read_text())

    @classmethod
    def load(cls, run):
        m = manifest()[run]
        return cls(run, m["nav"], m["plan"], m["windows"])

    def row_at(self, t):
        """The nav row nearest in time."""
        i = bisect.bisect_left(self.row_t, t)
        if i >= len(self.rows) or (i > 0 and t - self.row_t[i - 1] < self.row_t[i] - t): i -= 1
        return self.rows[max(0, i)]

    def db(self):
        db = SnapDB(self.windows)
        fs = product.f19_state()
        fs._offset[id(db)] = self.offset             # find_offset would scan 0x10000-0xA0000 through the fake
        return db

    def snaps(self, t0=None, t1=None):
        for f in self.snap_files:
            t = float(f.stem[5:])
            if (t0 is None or t >= t0) and (t1 is None or t <= t1): yield t, f.read_bytes()

    def event_times(self, prefix):
        return [t for t, e in self.events if e.startswith(prefix)]


def theater_grid(theater):
    """nav's _gbyte (territory grid byte at a game position) and FighterWatch's terrain_type."""
    g = product.territory().grid(theater)
    gbyte = lambda x, y: int(g[min(15, max(0, int(y) >> 11)), min(15, max(0, int(x) >> 11))])
    return gbyte, (lambda x, y: (gbyte(x, y) >> 2) & 3)


# ---------------------------------------------------------------------------------------------------- terrain probe
def make_terrain_ahead(npz_path):
    """scripts/nav.py terrain_ahead (the height_game branch), copied: nav is module-level code and can't be imported.
    When the probe moves into the package, call it from there instead (and the baseline must not change)."""
    t = np.load(npz_path); H, step = t["height_game"], int(t["height_step"])
    def terrain_ahead(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100):
        hr = math.radians(hdg); sx, cy = math.sin(hr), -math.cos(hr)
        for d in np.arange(0.25, max_km + 0.01, 0.25):
            tops = []
            for w in (-half_width_km, 0.0, half_width_km):
                gx, gy = p[0] + (sx * d - cy * w) * K, p[1] + (cy * d + sx * w) * K
                i, j = int(gx // step), int(gy // step)
                if 0 <= j < H.shape[0] and 0 <= i < H.shape[1]: tops.append(int(H[j, i]))
            if tops and max(tops) > 0 and max(tops) >= alt - margin_ft: return float(d), max(tops)
        return None
    return terrain_ahead


def _s(row):
    return dict(alt=int(row["alt"]), spd=int(row["spd"]), hdg=float(row["hdg"]))


def _p(row):
    return (float(row["x"]), float(row["y"]))


def _ratio(a, b): return round(a / b, 4) if b else None


# ---------------------------------------------------------------------------------------------------- replays
WANT_TOL_DEG = 1.0          # terrain escape: same inputs, same probe -> the same heading up to CSV rounding (0.1 deg)


def replay_terrain_escape(run: Run, margin_ft=None):
    """TerrainEscape on every nav row where nav would call it (phase route/attack/strafe, alt > 100). margin_ft: run with
    another TerrainEscape.MARGIN_FT (the 2026-10-21 flight flew with 150; the code has 100 since) to check the harness
    itself reproduces the flight when the parameters match."""
    if "escape" not in run.rows[0]: return None           # flight predates TerrainEscape: nothing logged to compare
    ph = product.phases()
    if margin_ft is not None:
        saved = ph.TerrainEscape.MARGIN_FT; ph.TerrainEscape.MARGIN_FT = margin_ft
        try: return replay_terrain_escape(run)
        finally: ph.TerrainEscape.MARGIN_FT = saved
    probe = make_terrain_ahead(run.plan_dir / "terrain.npz")
    esc = ph.TerrainEscape(lambda p_, h_, a_, mk_: probe(p_, h_, a_, max_km=mk_, margin_ft=ph.TerrainEscape.MARGIN_FT))
    n = agree = act_log = act_pred = both = want_ok = 0
    route_want, events = None, []
    for r in run.rows:
        if r["phase"] not in ("route", "attack", "strafe") or int(r["alt"]) <= 100: continue
        logged = r.get("escape", "")
        if not logged and not r.get("fighter") and not r.get("missile"): route_want = float(r["want"])
        rw = route_want if route_want is not None else float(r["want"])
        ov, ev = esc.step(_s(r), _p(r), float(r["t"]), rw, float(r["tscale"] or 1))
        if ev: events.append(ev.split(" - ")[0].split(" (")[0])
        pred = ov.get("escape", "")
        n += 1; agree += pred == logged; act_log += bool(logged); act_pred += bool(pred)
        if pred and logged:
            both += 1; want_ok += abs(wrap(ov["want"] - float(r["want"]))) <= WANT_TOL_DEG
    logged_events = [e.split(" - ")[0].split(" (")[0] for _, e in run.events if e.startswith("terrain escape")]
    return dict(rows=n, state_agree=agree, state_agree_rate=_ratio(agree, n), active_logged=act_log, active_replayed=act_pred,
                both_active=both, want_within_tol=want_ok, want_rate=_ratio(want_ok, both),
                events_replayed=len(events), events_logged=len(logged_events),
                event_kinds_match=[e.split(":")[0] for e in events] == [e.split(":")[0] for e in logged_events])


FIGHTER_WANT_TOL_DEG = 15.0     # FighterWatch picks from a 15 deg fan (FAN_STEP)


def replay_fighter_watch(run: Run):
    """FighterWatch on every snapshot taken while nav would call it; compared with the nearest nav row. None when the
    flight's nav didn't have FighterWatch yet (no fighter column)."""
    if "fighter" not in run.rows[0]: return None
    ph, fs = product.phases(), product.f19_state()
    gbyte, ttype = theater_grid(run.mission.get("theater", "CE"))
    fw = ph.FighterWatch(ttype, gbyte)
    db = run.db()
    n = agree = act_log = act_pred = both = want_ok = 0
    route_want, ri = None, 0
    for t, raw in run.snaps():
        db.load(raw, t)
        try: st = fs.read_state(db, tries=1)
        except RuntimeError: continue
        if not st["x"]: continue
        row = run.row_at(t)
        # the route want: logged want of the last row before t with no reaction active
        while ri < len(run.rows) and run.row_t[ri] <= t:
            r = run.rows[ri]; ri += 1
            if not r.get("fighter") and not r.get("escape") and not r.get("missile"): route_want = float(r["want"])
        if row["phase"] not in ("route", "attack", "strafe") or st["alt"] <= 100: continue
        s = dict(alt=st["alt"], spd=st["spd"], hdg=st["hdg"]); p = (st["x"] / 32, st["y"] / 32)
        opp = fs.read_flight(db)["opp"]
        ov, _ = fw.step(s, p, t, route_want if route_want is not None else float(row["want"]), fs.read_aircraft(db), opp)
        pred, logged = ov.get("fighter", ""), row.get("fighter", "")
        n += 1; agree += pred == logged; act_log += bool(logged); act_pred += bool(pred)
        if pred and logged:
            both += 1; want_ok += abs(wrap(ov["want"] - float(row["want"]))) <= FIGHTER_WANT_TOL_DEG
    return dict(snapshots=n, state_agree=agree, state_agree_rate=_ratio(agree, n), active_logged=act_log,
                active_replayed=act_pred, both_active=both, want_within_tol=want_ok, want_rate=_ratio(want_ok, both))


def _with_replay_clock(fn):
    """Run fn(clock) with phases' `time` module replaced by a replay clock (set_toggle's settle timer)."""
    ph = product.phases()
    clock, saved = ReplayClock(), ph.time
    ph.time = clock; ph._toggled.clear()
    try: return fn(clock)
    finally: ph.time = saved; ph._toggled.clear()


TAKEOFF_TOL_S = 5.0             # snapshots every ~1-1.7 s (2-3 intervals) and done needs several flags at once


def replay_takeoff(run: Run):
    """phases.Takeoff from the first snapshot on the runway until it reports done; its done time vs nav's event."""
    ph, fs = product.phases(), product.f19_state()
    prof = run.route.get("profile", {})
    logged = run.event_times("takeoff done")
    if not logged: return None
    def go(clock):
        db = run.db(); tko = None; states = []
        for t, raw in run.snaps():
            clock.t = t; db.load(raw, t)
            try: s = fs.read_state(db, tries=1)
            except RuntimeError: continue
            if tko is None:
                if s["alt"] >= 50: continue
                tko = ph.Takeoff(ph.Throttle(), float(prof.get("cruise_kt") or 190), float(prof.get("cruise_ft") or 450))
            f = fs.read_flight(db); p = (s["x"] / 32, s["y"] / 32)
            ov = tko.step(db, s, f, p, t, 1.0)
            if not states or states[-1][1] != tko.state: states.append((t, tko.state))
            if ov.get("done"):
                return dict(done_t=t, states=[x for _, x in states], keys=[k for _, k in db.keys])
        return dict(done_t=None, states=[x for _, x in states], keys=[k for _, k in db.keys])
    r = _with_replay_clock(go)
    if not r["states"]: return None                     # no snapshot on the runway in this fixture (trimmed away)
    dt = None if r["done_t"] is None else round(r["done_t"] - logged[0], 2)
    return dict(done_replayed=r["done_t"] is not None, done_dt_s=dt,
                within_tol=dt is not None and abs(dt) <= TAKEOFF_TOL_S, states=r["states"], keys=r["keys"])


def build_attacks(route, mission):
    """scripts/nav.py build_attacks, copied (module-level in nav): {role: dict(mode, T, record, launch_km, photo_km)}."""
    ph = product.phases()
    pts = route["points"]
    out = {}
    for t in mission["targets"]:
        role = t["role"].rstrip("?"); T = (float(t["x"]), float(t["y"]))
        kinds = {a.get("kind"): (a["x"], a["y"]) for a in pts if a.get("role") == role}
        mode = "maverick" if "launch" in kinds else "photo" if "photo" in kinds else "overfly"
        out[role] = dict(mode=mode, T=T, record=t.get("record"),
                         launch_km=ph.game_dist(kinds["launch"], T) / K if "launch" in kinds else None,
                         photo_km=ph.game_dist(kinds["photo"], T) / K if "photo" in kinds else None)
    return out


def replay_attack(run: Run, role):
    """phases.Attack on the snapshots from nav's '<role> attack start' event to the replay's done; the attack state at
    each snapshot vs the nearest nav row's attack_state, and the attack's log lines vs nav's events."""
    ph, fs = product.phases(), product.f19_state()
    starts = run.event_times(f"{role} attack start")
    ends = run.event_times(f"{role} attack done")
    if not starts: return None
    A = build_attacks(run.route, run.mission)[role]
    # nav fills a missing record from the in-flight briefing; the event text names the record nav used
    rec = next(int(e.rsplit("record ", 1)[1].rstrip(")")) for t, e in run.events if e.startswith(f"{role} attack start"))
    A["record"] = A["record"] if A["record"] is not None else rec
    def go(clock):
        db = run.db(); fs._events.pop(id(db), None)
        at = ph.Attack(role, A["mode"], A["T"], A["record"], A["launch_km"], A["photo_km"], ph.Throttle())
        n = agree = 0; done_t = None; seq = []
        for t, raw in run.snaps(starts[0] - 0.5, (ends[0] + 5) if ends else None):
            clock.t = t; db.load(raw, t)
            s = fs.read_state(db, tries=1); f = fs.read_flight(db); p = (s["x"] / 32, s["y"] / 32)
            ov = at.step(db, s, f, p, t, 1.0) if done_t is None else {}   # nav drops the attack once it is done
            row = run.row_at(t)
            logged = row.get("attack_state", "") if row["phase"] == "attack" else ""
            pred = "" if done_t else at.state
            n += 1; agree += pred == logged
            if not seq or seq[-1] != at.state: seq.append(at.state)
            if ov.get("done") and done_t is None: done_t = t
        return dict(n=n, agree=agree, done_t=done_t, seq=seq, log=list(at.log))
    r = _with_replay_clock(go)
    if not r["n"]: return None                          # attack not inside the fixture's snapshot windows
    nav_lines = [e.split(": ", 1)[1] for _, e in run.events if e.startswith(f"{role}: ")]
    norm = lambda x: x.split(" at ")[0].split(" (")[0]
    return dict(mode=A["mode"], snapshots=r["n"], state_agree=r["agree"], state_agree_rate=_ratio(r["agree"], r["n"]),
                done_dt_s=None if r["done_t"] is None or not ends else round(r["done_t"] - ends[0], 2),
                states=r["seq"], log=[norm(x) for x in r["log"]], log_matches_nav=[norm(x) for x in r["log"]] == [norm(x) for x in nav_lines])


def replay_missile_watch(run: Run, mode="act"):
    """MissileWatch on every snapshot with the nav row's want as the plan (as scripts/missile_replay.py does); a digest of
    its decisions plus first prediction vs real outcome per missile. Self-baseline only (nothing logged to compare)."""
    G, MW = product.missile_game(), product.missile_watch()
    saved = G.S
    G.S = type("FixtureSettings", (), {"logs": FIX / "logs"})()     # load_run reads settings.logs / memlog / <run>
    try: snaps = G.load_run(run.run)
    finally: G.S = saved
    gbyte, _ = theater_grid(run.mission.get("theater", "CE"))
    mw = MW.MissileWatch(mode, gbyte)
    digest, first, real, n_missile = [], {}, {}, 0
    for k, sn in enumerate(snaps):
        P = sn["player"]; row = run.row_at(sn["t"])
        ov, ev = mw.step(dict(alt=P["alt"], spd=P["kt"], hdg=P["hdg"] * 360 / 65536), (P["x"], P["y"]), sn["t"], float(row["want"]), sn)
        if sn["missiles"]:
            n_missile += 1
            digest.append([round(sn["t"] - snaps[0]["t"], 2), mw.state, mw.action, round(ov["want"], 1) if "want" in ov else None, ov.get("cm")])
        for m in sn["missiles"]:
            key = mw._key(m)
            if key not in first: first[key] = mw.known.get(key, {}).get("pred")
            if k + 1 < len(snaps) and snaps[k + 1]["dmg"] - sn["dmg"] == sn["world"]["opp"] + 1 and \
                    not any(x.slot == m.slot and x.spec == m.spec for x in snaps[k + 1]["missiles"] if x.ttl > 2 * sn["world"]["fs"]):
                real[key] = "hit"
    right = sum((pred == "HIT") == (real.get(key) == "hit") for key, pred in first.items())
    states = sorted({d[1] for d in digest})
    return dict(snapshots=len(snaps), snapshots_with_missiles=n_missile, missiles=len(first), first_predictions_right=right,
                states_seen=states, decisions=digest)


# ---------------------------------------------------------------------------------------------------- everything
REPLAYS = {
    "terrain_escape": replay_terrain_escape,
    "terrain_escape_margin150": lambda r: replay_terrain_escape(r, margin_ft=150),
    "fighter_watch": replay_fighter_watch,
    "takeoff": replay_takeoff,
    "attack_primary": lambda r: replay_attack(r, "primary"),
    "attack_secondary": lambda r: replay_attack(r, "secondary"),
    "missile_watch": replay_missile_watch,
}


def replay_run(run: str) -> dict:
    """{replay name: metrics} for one fixture run; replays with nothing to compare in that run are left out."""
    R = Run.load(run)
    out = {}
    for name, fn in REPLAYS.items():
        m = fn(R)
        if m is not None: out[name] = m
    return out
