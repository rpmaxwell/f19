"""WP-7c FlightDirector on the REAL planner (the golden CE Kolobrzeg plan: planner.api.context + planner.route), from
synthetic airborne states on its reference route. ~25 s. Timings print with -s."""
import time

import numpy as np
import pytest

from director_fakes import Dec, golden_reference, state_at, enemy_samples
from f19.core.types import Action, WaypointKind as K, WaypointStatus as S
from f19.pilot.director import FlightDirector, InlineExecutor, ThreadExecutor
from f19.pilot.phases.fly_leg import nav_points
from planner_golden import context

TIMES = []


@pytest.fixture(scope="module")
def world(egame):
    ref, mission, rj = golden_reference()
    return ref, mission, context("ce_kolobrzeg_1022_2250")


def director(world, **kw):
    ref, mission, ctx = world
    kw.setdefault("executor", InlineExecutor())
    return FlightDirector(mission, ref, ctx, **kw)


def replan(d, st, reason="test", **kw):
    t0 = time.monotonic(); r = d.replan(st, reason, **kw); TIMES.append((d.history[-1].candidate if r is not None else "-", time.monotonic() - t0))
    return r


def test_full_mission_rejoins_the_reference(world):
    ref = world[0]
    d = director(world)
    st = state_at(ref, 52, off_km=8, dh=45)                       # after the primary, pushed 8 km off by an evasion
    d._progress(st, None, None); d.plan.mark(K.PRIMARY, S.REACHED)
    route = replan(d, st)
    h = d.history[-1]
    assert (h.candidate, h.kind, h.version) == ("full", "mission", 1) and route.version == 1
    assert route.points[0].label == "Present position" and route.points[-1].kind == "landing"
    tail = route.points[-40:]
    assert [p.xy for p in tail] == [p.xy for p in ref.points[-40:]]            # the reference's own end, spliced
    assert h.fuel_end_lb > 300 and d.meta.follows_reference and d.plan.pending_kinds() == (K.SECONDARY, K.LANDING)
    assert nav_points(route)[0][1] == "Present position"                      # FlyLeg takes the Route as it is


def test_less_fuel_drops_the_secondary_then_the_chain_goes_on(world):
    ref = world[0]
    d = director(world)
    st = state_at(ref, 40, fuel=ref.points[40].fuel_lb - 1000)       # two tanks in the loadout
    d._progress(st, None, None); d.plan.mark(K.PRIMARY, S.REACHED)
    route = replan(d, st, events=[])
    assert d.history[-1].candidate == "landing" and d.history[-1].skipped == (K.SECONDARY,) and d.plan.status(K.SECONDARY) is S.SKIPPED
    assert [a[:2] for a in d.history[-1].attempts] == [("full", "ruled_out"), ("landing", "ok")]
    assert route.points[-1].kind == "landing" and d.history[-1].fuel_end_lb >= 300


def test_bail_out_to_neutral_territory_and_eject_on_entering_it(world):
    ref = world[0]
    d = director(world)
    st = state_at(ref, 40, fuel=ref.points[40].fuel_lb - 4800, tanks=0)
    d._progress(st, None, None); d.plan.mark(K.PRIMARY, S.REACHED)
    route = replan(d, st, events=enemy_samples(30, (8, 5)))                    # a score to protect: the earning option is looked at
    h = d.history[-1]
    assert h.kind == "bail" and h.candidate.startswith("bail:neutral") and d.meta.side == "neutral"
    assert any(a[0].startswith("bail:friendly") and a[1] in ("infeasible", "ruled_out") for a in h.attempts)
    assert any(a[0] == "earn:secondary" for a in h.attempts)                    # considered (ruled out on fuel), not flown
    end = route.points[-1]
    assert d.territory.side(end.x, end.y) == "neutral" and h.fuel_end_lb >= 100
    far = state_at(ref, 40, t=3500, fuel=900, tanks=0)
    assert d.on_tick(far, Dec("fly_leg")) is None and not d.terminal
    import dataclasses
    inside = dataclasses.replace(far, pos=dataclasses.replace(far.pos, x=end.x, y=end.y), t=3600.0)
    o = d.on_tick(inside, Dec("fly_leg"))
    assert d.terminal and o.command.actions == (Action.EJECT,) and "neutral" in o.exit


def test_nothing_reachable_ejects_in_place_in_enemy_territory(world):
    ref = world[0]
    d = director(world)
    st = state_at(ref, 40, fuel=ref.points[40].fuel_lb - 5600, tanks=0)
    assert replan(d, st, events=[]) is None
    assert d.terminal and d.history[-1].kind == "eject" and d._order.exit.startswith("director: ejected in enemy")


def test_real_planner_timings_summary(world):
    print("\ndirector replans (real planner):", ", ".join(f"{c} {t:.2f}s" for c, t in TIMES))
    assert TIMES and max(t for _, t in TIMES) < 15


def test_threaded_planning_never_blocks_the_tick(world):
    """ThreadExecutor + the real planner: on_tick returns at once while the route is computed on the worker; the new
    plan is adopted by a later tick. Records the tick gap the GIL leaves the caller."""
    ref = world[0]
    d = director(world, executor=ThreadExecutor())
    d.plan.mark(K.TAKEOFF, S.REACHED)
    st = lambda t: state_at(ref, 10, off_km=8, dh=45, t=t)
    t = 1000.0
    d.on_tick(st(t), Dec("missile"))
    t0 = time.monotonic(); d.on_tick(st(t + 1), Dec("fly_leg")); first = time.monotonic() - t0
    assert d.busy and first < 0.1 and d.version == 0                           # started, not waited for
    gaps, last, adopted_after = [], time.monotonic(), None
    while time.monotonic() - t0 < 30:
        time.sleep(0.01)                                                       # a 0.15 s tick, here denser to see stalls
        now = time.monotonic(); gaps.append(now - last); last = now
        t += 0.15
        o = d.on_tick(st(t + 2), Dec("fly_leg"))
        if o is not None and d.version == 1: adopted_after = now - t0; break
    assert adopted_after is not None and d.current().version == 1 and not d.busy
    print(f"\nthreaded replan: started in {first * 1000:.1f} ms, adopted after {adopted_after:.2f} s; "
          f"loop gaps (10 ms sleep): max {max(gaps) * 1000:.0f} ms, p99 {np.percentile(gaps, 99) * 1000:.0f} ms over {len(gaps)} ticks")
    assert max(gaps) < 1.0
    d.close()
