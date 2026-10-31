"""FlyLeg (f19.pilot.phases): route following (load, loiter legs, zero-length legs, fly-over targets, in-air starts, rejoin
from far off the line), the attack / landing triggers and the takeoff hand-over. A CHARACTERISATION test: on every nav-CSV
row of the replay fixtures and on synthetic routes the leg, heading, turn-rate cap and leg altitude of each tick are
digested and compared with the stored digest (they once agreed exactly with nav.py's own route code, a copy of which
lived here); the replays are also checked against what nav logged (leg, want on rows no override touched; the CSV
positions are rounded to whole units, so the heading is compared within 0.5 deg). If a change of FlyLeg is meant, re-run
with PRINT_DIGESTS=1 (pytest -s), paste the new digests and say why in the commit."""
import hashlib
import json
import math
import os

import pytest

from pilot_worlds import flat_state  # first: puts tests/replay on sys.path
import harness as H  # noqa: E402
from f19.core.clock import ManualClock
from f19.core.types import FlightContext, Route
from f19.pilot.phases import FlyLeg

K = 64.0


# digests recorded 2026-10-30 from the current FlyLeg (replays: the 2026-10-27 takeoff_done turn included)
GOLD = {
'replay-1792609731': '9cb3ac63ec992db8',
    'replay-1792546698': '80372ab6bbeaf9d2',
    'replay-1792715432': '5d6f3fdddc1dbf95',
    'synthetic-runway': '5883b57646dcec5a',
    'synthetic-air': 'e3a08db3a4e3884b',
    'synthetic-far_off': 'a9b333d8261266b6',
    'triggers-1792609731': '7a7b2d9dc64a9df4',
    'triggers-1792546698': '25b158c6f2a4bc92',
    'triggers-1792715432': '05603d5a54c5068f',
}


def _check(name, got):
    if os.environ.get("PRINT_DIGESTS"): print(f"DIGEST {name!r}: {got!r},")
    else: assert got == GOLD[name], f"{name}: route following changed (digest {got}, stored {GOLD[name]})"


def _digest(out): return hashlib.sha256(repr(out).encode()).hexdigest()[:16]


def run(route_json, inputs):
    """inputs: [(t, s dict, p)] -> (ticks, [(leg, want, cap, leg alt) per tick])."""
    fl = FlyLeg(); ctx = FlightContext(ManualClock(0.0), route=Route.from_route_json(route_json))
    out = []
    for t, s, p in inputs:
        ctx.clock.set(t)
        c = fl.step(flat_state(s["alt"], s["spd"], s["hdg"], *p), ctx)
        out.append((ctx.leg, c.heading, c.rate_cap, c.alt, fl.mode))
    return len(out), out


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id", ["1792609731", "1792546698", "1792715432"])
def test_fly_leg_replay(run_id):
    R = H.Run.load(run_id)
    rows = [r for r in R.rows if r.get("alt") not in (None, "")]
    inputs = [(float(r["t"]), dict(alt=int(r["alt"]), spd=int(r["spd"]), hdg=float(r["hdg"])), (float(r["x"]), float(r["y"])))
              for r in rows]
    n, out = run(R.route, inputs)
    assert n > 1000
    _check(f"replay-{run_id}", _digest(out))
    out = [o[:4] for o in out]
    # vs the nav log (not exact: CSV positions are whole units, and the log's want is after every override)
    clean = [(r, o) for r, o in zip(rows, out) if r["phase"] in ("takeoff", "route") and not any(
        r.get(k) for k in ("attack_state", "escape", "fighter", "missile"))]
    leg_ok = sum(int(r["leg"]) == o[0] for r, o in clean)
    want_ok = sum(abs((float(r["want"]) - o[1] + 180) % 360 - 180) <= 0.5 for r, o in clean)
    print(f"fly_leg {run_id}: {n} rows; vs nav log on {len(clean)} un-overridden rows: "
          f"leg {leg_ok} ({leg_ok / len(clean):.1%}), want within 0.5 deg {want_ok} ({want_ok / len(clean):.1%})")
    assert leg_ok / len(clean) > 0.95 and want_ok / len(clean) > 0.9


def _synthetic_route():
    """Takeoff, Climb-out, a corner, a zero-length leg, a loiter (8 points round a 4 km circle), a target (fly-over),
    an approach and Final."""
    pts = [dict(x=10000, y=30000, label="Takeoff (Base)", kind="takeoff"), dict(x=10000, y=29000, label="Climb-out", kind="climb", alt_ft=450),
           dict(x=10000, y=26000, label="", alt_ft=300), dict(x=12000, y=24000, label=""), dict(x=12000.4, y=24000.2, label="dup", alt_ft=999),
           dict(x=14000, y=24000, label="LOITER 1", kind="loiter")]
    cx, cy = 14000, 24256
    pts += [dict(x=round(cx + 256 * math.sin(a * math.pi / 4)), y=round(cy - 256 * math.cos(a * math.pi / 4)), label="") for a in range(1, 9)]
    pts += [dict(x=18000, y=22000, label="PRIMARY run-in", kind="runin", role="primary"),
            dict(x=19000, y=21000, label="PRIMARY: photo point 1 km", kind="photo", role="primary"),
            dict(x=19500, y=20500, label="PRIMARY: Tank Farm", kind="overfly", role="primary"),
            dict(x=16000, y=18000, label=""), dict(x=10000, y=14000, label="Approach fix", kind="approach"),
            dict(x=10000, y=11000, label="Final", kind="final"), dict(x=10000, y=10000, label="Landing (Base)", kind="landing")]
    return dict(points=pts)


@pytest.mark.parametrize("start", ["runway", "air", "far_off"])
def test_fly_leg_synthetic(start):
    """Fly the synthetic route with a crude turn model (2 deg a tick toward the heading FlyLeg asks for)."""
    route = _synthetic_route()
    x, y, alt, hdg = dict(runway=(10000, 30000, 0, 0.0), air=(12500, 24300, 900, 90.0), far_off=(4000, 26000, 600, 45.0))[start]
    fl = FlyLeg(); ctx = FlightContext(ManualClock(0.0), route=Route.from_route_json(route))
    out, spd, t = [], 0, 0.0
    for i in range(6000):
        t += 0.25; spd = min(200, spd + 5); alt = min(500, alt + (4 if spd > 120 else 0))
        ctx.clock.set(t)
        c = fl.step(flat_state(alt, spd, hdg, x, y), ctx)
        out.append((ctx.leg, c.heading, c.rate_cap, c.alt, fl.mode))
        d = (c.heading - hdg + 180) % 360 - 180; hdg = (hdg + max(-2, min(2, d))) % 360
        v = spd / 200 * 25
        x += v * math.sin(math.radians(hdg)); y -= v * math.cos(math.radians(hdg))
        if math.hypot(x - 10000, y - 10000) < 100: break
    legs = {o[0] for o in out}
    assert {o[2] for o in out} == {0.8, 2.5}, legs                                  # loiter cap seen
    if start == "runway": assert {None, 450, 300} <= {o[3] for o in out}, {o[3] for o in out}   # leg altitudes
    assert max(legs) >= 18, legs                      # reached the approach legs
    _check(f"synthetic-{start}", _digest(out))
    print(f"fly_leg synthetic {start}: {len(out)} ticks, legs {sorted(legs)}")


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id", ["1792609731", "1792546698", "1792715432"])
def test_fly_leg_triggers_replay(run_id):
    """FlyLeg.attacks vs nav's build_attacks (the harness copy); FlyLeg.attack_role / landing_due on every row (the digest);
    the first attack start per role vs nav's logged event."""
    R = H.Run.load(run_id)
    fl = FlyLeg(); ctx = FlightContext(ManualClock(0.0), route=Route.from_route_json(R.route))
    rows = [r for r in R.rows if r.get("alt") not in (None, "")]
    started, done, trace = {}, set(), []
    for r in rows:
        s, p = dict(alt=int(r["alt"]), spd=int(r["spd"]), hdg=float(r["hdg"])), (float(r["x"]), float(r["y"]))
        st = flat_state(s["alt"], s["spd"], s["hdg"], *p)
        fl.step(st, ctx)
        new_role = fl.attack_role(st, ctx, done)
        name = fl.wps[ctx.leg][1]
        due = fl.landing_due(ctx)
        assert due == (name == "Final" or name.startswith("Landing"))
        trace.append((ctx.leg, new_role, due))
        if new_role and new_role not in started: started[new_role] = float(r["t"]); done.add(new_role)
    assert fl.attacks(R.mission) == H.build_attacks(R.route, R.mission)
    for role, t in started.items():
        logged = R.event_times(f"{role} attack start")
        if logged: assert abs(t - logged[0]) < 1.0, (role, t, logged)
    _check(f"triggers-{run_id}", _digest(trace))
    print(f"fly_leg triggers {run_id}: attack starts {started}, nav events "
          f"{ {ro: R.event_times(f'{ro} attack start')[:1] for ro in started} }")


def test_takeoff_done_turns_onto_the_route_at_once():
    """2026-10-27: --auto leaves the runway heading as soon as the Takeoff behavior is done, not 6 km on."""
    from f19.core.clock import ManualClock
    from f19.core.types import Attitude, FlightContext, GameState, Position, Route, RoutePoint, Systems
    route = Route((RoutePoint(10000.0, 30000.0, "Takeoff (Base)"), RoutePoint(10000.0, 29000.0, "Climb-out"),
                   RoutePoint(14000.0, 20000.0, "WP"), RoutePoint(14000.0, 10000.0, "Landing")))
    ctx = FlightContext(ManualClock(0.0), route=route)
    mk = lambda y, alt: GameState(t=0.0, pos=Position(10000.0, y, alt), att=Attitude(0.0), speed_kt=250.0, systems=Systems(flags=0x01), sensitivity=1)
    for done in (False, True):
        f = FlyLeg(); ctx.leg = 1
        f.step(mk(30000.0, 20), ctx)                          # on the ground: the runway heading is taken
        f.takeoff_done = done
        f.step(mk(29900.0, 350), ctx)                         # 1.5 km on, 350 ft
        assert (f.mode == "route") is done and (ctx.leg == 2) is done
