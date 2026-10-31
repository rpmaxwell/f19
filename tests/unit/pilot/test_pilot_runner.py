"""WP-7a Runner and f19.cli.fly, offline only (no emulator is ever contacted):
  - the pilot-local copies match their originals: parse_missile_world == analysis/missile_game.parse on every fixture
    snapshot, terrain_ahead_probe == nav's terrain_ahead (tests/replay/harness copy)
  - `f19.cli.fly --replay <memlog> --dry-run` on all three fixtures: the whole stack runs, no key is left held, the CSV
    (CsvSink, nav's columns + route_want / behavior / plan_version) and events are written, and the decisions are the
    ones the arbiter tests check
  - the HUD draws the runner's frames (HudSink on a NullTerminal)
  - releases: an exception in the loop, not-in-flight for 10 s, a hand-over; MemLog snapshots
"""
import csv
import random

import pytest

from pilot_worlds import KeyLog, product
import harness as H  # noqa: E402
from f19.cli import fly
from f19.core.clock import ManualClock
from f19.core.types import Attitude, GameState, Position, Route, RoutePoint, Systems
from f19.memory import ReplayReader
from f19.memory import reader as MR
from f19.pilot.runner import MemLog, Reads
from f19.rules.missiles import parse_missile_world
from f19.rules.terrain import terrain_ahead_probe
from f19.telemetry import EXTENDED_COLUMNS

need_fix = pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
RUNS = {"1792609731": "ce_katowice_1021_1458", "1792546698": "ce_test_1019_2337", "1792715432": "ce_stupsk_1022_2025"}


def _m(x):
    if isinstance(x, dict): return {k: _m(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)): return [_m(v) for v in x]
    if hasattr(x, "__slots__") and hasattr(x, "spec"): return [getattr(x, k) for k in x.__slots__]
    return x


# run -> (digest of the old missile_game.parse over every snapshot, snapshots), recorded 2026-10-30 before that module was retired
GOLD_PARSE = {"1792546698": ("4d18dae4673de0c3", 313), "1792609731": ("7c75c400058eaac3", 404), "1792715432": ("7234b38343c2da21", 787)}


@need_fix
@pytest.mark.parametrize("run_id", list(RUNS))
def test_parse_missile_world_matches_the_old_parse(run_id, egame):
    import hashlib
    import json
    rr = ReplayReader(H.FIX / "logs" / "memlog" / run_id)
    h, n = hashlib.sha256(), 0
    for i in range(len(rr)):
        rr.seek_index(i)
        get = MR.missile_world_memory(rr.emu, full=bool(i % 2))
        h.update(json.dumps(_m(parse_missile_world(get)), sort_keys=True, default=repr).encode())
        n += 1
    assert (h.hexdigest()[:16], n) == GOLD_PARSE[run_id]
    assert n > 200


@need_fix
def test_terrain_probe_matches_nav(egame):
    import numpy as np
    R = H.Run.load("1792609731")
    t = np.load(R.plan_dir / "terrain.npz")
    new, old = terrain_ahead_probe(t["height_game"], int(t["height_step"])), H.make_terrain_ahead(R.plan_dir / "terrain.npz")
    rnd = random.Random(7)
    hits = 0
    for _ in range(3000):
        p = (rnd.uniform(0, 32768), rnd.uniform(0, 32768)); h = rnd.uniform(0, 360); a = rnd.choice([100, 300, 700, 1500])
        mk = rnd.choice([6.0, 8.0])
        r = new(p, h, a, max_km=mk); hits += r is not None
        assert r == old(p, h, a, max_km=mk)
    assert hits > 100


def _args(run_id, out, **kw):
    a = fly.parser().parse_args(["--replay", str(H.FIX / "logs" / "memlog" / run_id), "--dry-run", "--quiet", "--out", str(out),
                                 "--route", str(H.FIX / "plan" / RUNS[run_id] / "route.json"), "--auto"])
    for k, v in kw.items(): setattr(a, k, v)
    return fly.resolve(a)


@need_fix
@pytest.mark.parametrize("run_id", list(RUNS))
def test_dry_run_fixture(run_id, tmp_path, egame):
    res = fly.dry_run(_args(run_id, tmp_path))
    assert res["ticks"] > 200 and res["in_flight"] == res["ticks"] and res["exit"] is None
    keys = res["keys"]
    assert keys and keys[-1][1:] == ("tap", "kp5", 0.2)                      # released + centred at the end
    held = set()
    for e in keys:
        if e[1] == "down": held.add(e[2])
        elif e[1] == "up": held.discard(e[2])
    assert not held
    rows = list(csv.reader(open(tmp_path / f"{run_id}.csv")))
    assert tuple(rows[0]) == EXTENDED_COLUMNS
    body = [dict(zip(rows[0], r)) for r in rows[1:] if len(r) == len(rows[0])]
    assert len(body) > 200 and all(r["route_want"] and r["behavior"] and r["plan_version"] == "0" for r in body)
    ev = (tmp_path / f"{run_id}.events.txt").read_text()
    want = {"1792609731": ["takeoff done", "primary attack done"], "1792546698": ["primary attack start (photo"],
            "1792715432": ["takeoff done", "MISSILE EVASION"]}[run_id]
    assert all(w in ev for w in want), ev[:2000]


@need_fix
def test_dry_run_without_route_flies_the_game_waypoints(tmp_path, egame):
    a = _args("1792715432", tmp_path, route=None, auto=False, hdg=True, alt=None)
    res = fly.dry_run(a)
    r = res["runner"]
    assert res["ticks"] > 200 and [n for _, n, _ in r.arb.fly_leg.wps][0] == "start"


@need_fix
def test_hud_draws_runner_frames(tmp_path, egame):
    from f19.cli.hud import load_context, route_points
    from f19.hud import Hud, HudFrame, HudSink
    from f19.hud import layout as L
    from f19.hud.terminal import NullTerminal
    route = H.FIX / "plan" / RUNS["1792609731"] / "route.json"
    term = NullTerminal((160, 50))
    sink = HudSink(Hud(load_context(None, route, route_points(route)), L.load(None), history_period=0.0), term)

    class Adapter:
        stats = sink.stats
        def write(self, tf):
            if tf.frame is not None: sink.write(HudFrame.from_telemetry(tf, event_list=tf.nav.get("event_list")), force=True)
        def flush(self, timeout=None): return True
        def close(self, timeout=10.0): return True
    a = _args("1792609731", tmp_path)
    rr = ReplayReader(a.replay); clock = ManualClock(0.0); emu = fly.RecordingEmulator(clock)
    runner = None
    for i, t in enumerate(rr.times()[:150]):
        rr.seek_index(i); clock.set(t)
        if runner is None:
            runner = fly.build(a, rr, emu, clock, rr.snapshot(), sleep=lambda s: None, wall=clock.now, say=lambda m: None,
                               sinks=(Adapter(),))
        runner.tick(now=t)
    assert sink.failed is None and term.frames > 100
    text = "\n".join(term.screen.rows())
    assert "TAKEOFF" in text or "takeoff" in text


# ------------------------------------------------------------------------------------------- synthetic runner
class FakeReader:
    """snapshot() from a callable; emu for Reads (not used: FakeReads)."""
    def __init__(self, fn): self.fn, self.emu, self.n = fn, None, 0
    def snapshot(self, threats=True): self.n += 1; return self.fn(self.n)
    def event_list(self, start=0): return 0, []
    def offset(self, refresh=False): return 0


class FakeReads(Reads):
    def time_mult(self): return 1.0
    def events(self): return []
    def damage(self): return 0, 2


def _state(alt=1000, spd=250, hdg=0.0, x=10000.0, y=20000.0):
    return GameState(t=0.0, pos=Position(x, y, alt), att=Attitude(hdg), speed_kt=spd, systems=Systems(flags=0x01, throttle=60),
                     sensitivity=1)


def _runner(fn, clock, **kw):
    a = fly.resolve(fly.parser().parse_args(["--hdg", "--alt", "1000"]))
    a.dry_run = True
    reader = FakeReader(fn)
    emu = KeyLog()
    route = Route((RoutePoint(10000.0, 20000.0, "start"), RoutePoint(10000.0, 10000.0, "WP"), RoutePoint(14000.0, 6000.0, "Landing")))
    r = fly.build(a, reader, emu, clock, fn(0), sleep=lambda s: clock.advance(s), wall=clock.now, say=lambda m: None, route=route)
    r.reads = r.arb.reads = FakeReads(reader)
    return r, emu


def test_runner_releases_on_exception():
    clock = ManualClock(1000.0)
    def fn(n):
        if n > 30: raise ValueError("boom")
        return _state(hdg=(n * 4.0) % 360)                      # turning: the heading hold holds roll keys
    r, emu = _runner(fn, clock)
    with pytest.raises(ValueError):
        r.run(signals=False)
    k = emu.take()
    assert any(e[0] == "down" for e in k) and not emu._held and ("tap", "kp5", 0.2) in k


def test_runner_exits_after_10s_out_of_flight():
    clock = ManualClock(1000.0)
    def fn(n):
        if n > 5: raise RuntimeError("player state unreadable")
        return _state()
    r, emu = _runner(fn, clock)
    msg = r.run(signals=False)
    assert msg and "not in flight for 10 s" in msg and not emu._held


def test_runner_hands_over_at_the_route_end():
    """Without --auto nav handed over on the leg to "Landing": keys released, heading and altitude holds off."""
    clock = ManualClock(1000.0)
    def fn(n): return _state(x=10000.0 + 40 * n, y=max(6000.0, 20000.0 - 160 * n), hdg=20.0)
    r, emu = _runner(fn, clock)
    phases = []
    for _ in range(150):
        res = r.tick(); phases.append(res.decision.phase)
        if res.decision.phase == "handover": break
    assert phases[-1] == "handover" and not r.modes.hdg and r.modes.alt is None and not emu._held


@need_fix
def test_memlog_snapshots(egame):
    rr = ReplayReader(H.FIX / "logs" / "memlog" / "1792609731")
    rr.seek_index(10)
    clock = ManualClock(5.0)
    ml = MemLog(rr.emu, {}, clock=clock.now)
    assert ml.layout.windows[0][0] == "egame_data" and ml.layout.size == 0xC000
    snaps = []
    for _ in range(4):
        clock.advance(1.0); s = ml.step()
        if s: snaps.append(s)
    assert len(snaps) == 1 and len(snaps[0].data) == 0xC000 and snaps[0].t == round((5.0 + 8.0) / 2, 2)
    lo = ml.layout.windows[0][1]
    assert snaps[0].data[:256] == rr.emu.peek(lo, 256)


def _clock_runner(tm, rate):
    """A Runner with only what _measure_clock touches: frame counter advancing `rate` game s per real s (fs 8: 0.25 game s a frame)."""
    from f19.core.clock import FrameClock
    from f19.pilot.runner import Runner
    r = Runner.__new__(Runner)
    r.game_clock, r._gc_log, r._gc_t0, r.phase_log = FrameClock(), 0.0, None, []
    st = {"wall": 0.0, "frame": 1000}
    class FakeReads:
        def frame_fs(self_): return st["frame"], 8
        def time_mult(self_): return tm
    r.reads = FakeReads()
    out = []
    for i in range(30):                                         # 30 real seconds (the first 20 are warm-up: no line)
        st["wall"] = float(i); st["frame"] = 1000 + int(round(i * rate * 4))
        r._measure_clock(st["wall"], out)
    return out


def test_clock_line_reports_the_speed_relative_to_the_tuned_one():
    out = _clock_runner(1.0, 2.5)                               # 10 frames a real second (whole frames), 2.5 vs the tuned 2.46
    assert len(out) == 1 and "time byte 1 = 1.02 of the tuned speed" in out[0].text and "OUTSIDE" not in out[0].text
    out = _clock_runner(2.0, 5.0)                               # TIME x2: twice the rate, the byte says 2: still the tuned speed
    assert "time byte 2 = 1.02 of the tuned speed" in out[0].text and "OUTSIDE" not in out[0].text


def test_clock_line_waits_for_a_real_measurement():
    """The first reading of a run is noisy (0.5-2.6 seen): nothing is said, and nothing is flagged, in the first CLOCK_WARMUP_S."""
    from f19.pilot.runner import Runner
    assert all(e.t >= Runner.CLOCK_WARMUP_S for e in _clock_runner(1.0, 1.5)) and _clock_runner(1.0, 1.5)


def test_clock_line_flags_a_different_emulator_speed():
    out = _clock_runner(1.0, 1.5)                               # a slower machine: 0.61 of the speed the loops were tuned at
    assert "0.61 of the tuned speed" in out[0].text and "OUTSIDE 0.8-1.25" in out[0].text
