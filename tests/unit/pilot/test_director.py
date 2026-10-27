"""WP-7c FlightDirector: every branch of the chain over synthetic states on the golden CE reference route (Kolobrzeg:
primary points 32-34, secondary 62-64, landing last), with a scripted router (the real planner is test_director_golden.py).
Offline; nothing touches a game."""
import pytest

from director_fakes import (KOL, FakeRouter, Dec, enemy_samples, fake_planner, golden_reference, grid, state_at,
                            state_xy)
from f19.core.types import Action, WaypointKind as K, WaypointStatus as S
from f19.pilot.director import FlightDirector, InlineExecutor, Territory
from f19.planner.estimate import FuelConstraint
from f19.planner.router import BudgetExceeded, PlanError

PRIMARY_XY, SECONDARY_XY, LANDING_XY = (16448, 11456), (16576, 21056), (3392, 21184)


@pytest.fixture(scope="module")
def gold(egame):
    ref, mission, rj = golden_reference()
    return ref, mission


def make(gold, router=None, g=None, **kw):
    ref, mission = gold
    router = router or FakeRouter()
    d = FlightDirector(mission, ref, fake_planner(g), route_fn=router, executor=InlineExecutor(), **kw)
    return d, router


def roles(call): return call[0]


# ------------------------------------------------------------------------------------------------ mission chain
def test_full_mission_is_flown_first_and_spliced_on_the_reference(gold):
    d, r = make(gold)
    st = state_at(d.reference, 10)
    route = d.replan(st, "test")
    assert d.choose_goal(st).name == "full"
    assert r.kinds[0] == ("rejoin",)                            # the reference splice, from the pose
    assert route.version == 1 and d.version == 1 and d.current() is route
    h = d.history[-1]
    assert (h.candidate, h.kind, h.reason) == ("full", "mission", "test") and h.skipped == ()
    assert d.plan.pending_kinds() == (K.PRIMARY, K.SECONDARY, K.LANDING)
    assert d.meta.follows_reference and d.meta.kind == "mission"


def test_fuel_too_low_for_the_secondary_drops_it(gold):
    low = lambda goals, pose, b: 100.0 if any(g.role == "secondary" or g.kind == "rejoin" for g in goals) else 2000.0
    d, r = make(gold, FakeRouter(low))
    route = d.replan(state_at(d.reference, 10), "fuel")
    assert d.history[-1].candidate == "primary>landing" and d.history[-1].skipped == (K.SECONDARY,)
    assert [roles(c) for c in r.calls] == [("rejoin",), ("primary", "secondary", "landing"), ("primary", "landing")]   # the splice, plain full, then the next
    assert [a[1] for a in d.history[-1].attempts] == ["infeasible", "ok"]
    assert d.plan.status(K.SECONDARY) is S.SKIPPED and d.plan.status(K.PRIMARY) is S.PENDING
    assert route.points[-1].kind == "landing"


def test_secondary_then_landing_when_the_primary_cannot_be_reached(gold):
    rule = lambda goals, pose, b: 100.0 if any(g.role == "primary" or g.kind == "rejoin" for g in goals) else 2000.0
    d, r = make(gold, FakeRouter(rule))
    d.replan(state_at(d.reference, 10), "x")
    assert d.history[-1].candidate == "secondary>landing" and d.history[-1].skipped == (K.PRIMARY,)
    assert d.plan.status(K.PRIMARY) is S.SKIPPED
    assert ("secondary", "landing") in [roles(c) for c in r.calls]


def test_landing_only(gold):
    rule = lambda goals, pose, b: 2000.0 if [g.kind for g in goals] == ["landing"] else 100.0
    d, r = make(gold, FakeRouter(rule))
    d.replan(state_at(d.reference, 10), "x")
    assert d.history[-1].candidate == "landing" and set(d.history[-1].skipped) == {K.PRIMARY, K.SECONDARY}
    assert [roles(c) for c in r.calls] == [("rejoin",), ("primary", "secondary", "landing"), ("primary", "landing"),
                                         ("secondary", "landing"), ("landing",)]
    assert d.plan.pending_kinds() == (K.LANDING,)


def test_a_reached_primary_is_not_flown_again_and_there_is_no_splice(gold):
    d, r = make(gold)
    st = state_at(d.reference, 40)                              # after the primary (points 32-34), before the secondary
    d.plan.mark(K.PRIMARY, S.REACHED)
    d.replan(st, "x")
    assert r.kinds[0] == ("rejoin",)                            # reference ahead of the plane = [secondary]: splice is valid
    d2, r2 = make(gold)
    d2.plan.mark(K.PRIMARY, S.SKIPPED)
    d2.replan(state_at(d2.reference, 10), "x")                  # before the primary on the reference, but it was skipped
    assert r2.kinds[0] == ("secondary", "landing") and r2.kinds[0] != ("rejoin",)   # no splice: it would fly the primary


def test_splice_falls_back_to_the_chain_when_the_rejoin_has_no_path(gold):
    rule = lambda goals, pose, b: PlanError("rejoin: no path") if goals[0].kind == "rejoin" else 5000.0
    d, r = make(gold, FakeRouter(rule))
    d.replan(state_at(d.reference, 10), "x")
    assert d.history[-1].candidate == "full" and [roles(c) for c in r.calls] == [("rejoin",), ("primary", "secondary", "landing")]
    assert not d.meta.follows_reference                         # the chain from scratch is not a reference splice


def test_estimate_rules_candidates_out_before_the_router(gold):
    d, r = make(gold)
    st = state_at(d.reference, 10, fuel=3700, tanks=0)         # far too little for the mission, enough for the landing alone
    d.replan(st, "x")
    assert d.history[-1].candidate == "landing"
    assert [roles(c) for c in r.calls] == [("landing",)]       # the three target candidates never reached the router
    assert [a[1] for a in d.history[-1].attempts] == ["ruled_out", "ruled_out", "ruled_out", "ok"]


def test_estimate_passes_but_the_real_route_fails_falls_through(gold):
    rule = lambda goals, pose, b: 50.0 if any(g.role == "secondary" or g.kind == "rejoin" for g in goals) else 4000.0
    d, r = make(gold, FakeRouter(rule))
    d.replan(state_at(d.reference, 10), "x")
    a = d.history[-1].attempts
    assert a[0][:2] == ("full", "infeasible") and d.history[-1].candidate == "primary>landing"


def test_budget_exceeded_keeps_the_route_and_retries_then_gives_up(gold):
    d, r = make(gold, FakeRouter(lambda g, p, b: BudgetExceeded("slow")), max_retries=2)
    st = state_at(d.reference, 10)
    assert d.replan(st, "a") is None and d.version == 0 and d._retries == 1 and not d.terminal
    assert d.replan(st, "b") is None and d._retries == 2 and not d.terminal
    assert d.current() is d.store.active and d.plan.pending_kinds() == (K.PRIMARY, K.SECONDARY, K.LANDING)
    assert d.replan(st, "c") is None and d.terminal                        # still nothing proven: down the chain, eject in place
    assert d.history[-1].kind == "eject"


def test_planner_exception_does_not_escape(gold):
    d, r = make(gold, FakeRouter(lambda g, p, b: ZeroDivisionError("bug")))
    out = d._decide(d._snapshot(state_at(d.reference, 10), None, "x", 0.0))
    assert out.kind == "keep" and "ZeroDivisionError" in out.error          # a router bug: keep flying, report it
    d2, _ = make(gold)
    d2._planner = None
    d2._planner_factory = lambda: 1 / 0
    out = d2._decide(d2._snapshot(state_at(d2.reference, 10), None, "x", 0.0))
    assert out.kind == "keep" and "ZeroDivisionError" in out.error


def test_no_landing_waypoint_chain_is_targets_only(gold):
    import dataclasses
    ref, mission = gold
    d = FlightDirector(dataclasses.replace(mission, landing=None), ref, fake_planner(), route_fn=FakeRouter(), executor=InlineExecutor())
    assert [c.name for c in d.mission_candidates(d.plan, __import__("f19.planner.api", fromlist=["x"]).Pose(0, 0, 0))] == \
        ["full", "primary", "secondary"]


# ------------------------------------------------------------------------------------------------ no landing
def stranded(fuel, x=8 * 2048 + 700, y=8 * 2048 + 900, **kw):
    """Too little fuel for any landing candidate (the field is ~250 km away)."""
    return state_xy(x, y, fuel=fuel, tanks=0, **kw)


def test_score_zero_goes_straight_to_the_bail_out_chain_friendly_first(gold):
    g = grid(0, **{"7,8": 3, "9,8": 2})                         # a friendly and a neutral cell beside an enemy plane
    d, r = make(gold, g=g)
    route = d.replan(stranded(900), "x", events=[])
    assert d.history[-1].candidate.startswith("bail:friendly@7,8") and d.history[-1].kind == "bail"
    assert d.meta.side == "friendly" and route.points[-1].x < 8 * 2048          # the route ends inside cell (7, 8)
    assert d.plan.pending_kinds() == () and set(d.history[-1].skipped) == {K.PRIMARY, K.SECONDARY, K.LANDING}
    assert [roles(c) for c in r.calls] == [("point",)]                          # no landing routed: all ruled out by the estimate
    assert all(a[1] == "ruled_out" for a in d.history[-1].attempts[:4])        # the four landing candidates


def test_bail_out_neutral_when_no_friendly_cell_is_reachable(gold):
    g = grid(0, **{"9,8": 2})
    d, r = make(gold, g=g)
    route = d.replan(stranded(900), "x", events=[])
    assert d.history[-1].candidate.startswith("bail:neutral@9,8") and d.meta.side == "neutral" and route.points[-1].x > 9 * 2048


def test_friendly_cell_too_far_falls_to_neutral(gold):
    g = grid(0, **{"0,0": 3, "9,8": 2})                         # the friendly cell is 8 cells (260 km) away
    d, r = make(gold, g=g)
    d.replan(stranded(900), "x", events=[])
    assert d.history[-1].candidate.startswith("bail:neutral")
    atts = d.history[-1].attempts
    assert any(a[0].startswith("bail:friendly") and a[1] == "ruled_out" for a in atts)


def test_bail_out_enemy_ejects_in_place(gold):
    d, r = make(gold, g=grid(0))
    assert d.replan(stranded(900), "x", events=[]) is None
    assert d.terminal and d.history[-1].kind == "eject" and d.history[-1].candidate == "bail:enemy"
    o = d._order
    assert o.command.actions == (Action.EJECT,) and o.exit.startswith("director: ejected in enemy") and o.command.done


def test_already_in_a_friendly_cell_ejects_there(gold):
    d, r = make(gold, g=grid(3))
    d.replan(stranded(900), "x", events=[])
    assert d.terminal and d._order.exit.startswith("director: ejected in friendly")


def test_bail_route_ejects_on_entering_the_cell(gold):
    g = grid(0, **{"7,8": 3})
    d, r = make(gold, g=g)
    d.replan(stranded(900), "x", events=[])
    assert not d.terminal and d.meta.kind == "bail"
    o = d.on_tick(stranded(800, t=1100.0), Dec())               # still in the enemy cell: flies on
    assert not d.terminal and (o is None or o.command is None)
    o = d.on_tick(stranded(700, x=7 * 2048 + 1000, t=1110.0), Dec())
    assert d.terminal and o.command.actions == (Action.EJECT,) and "friendly" in o.exit
    assert d.history[-1].kind == "eject" and d.on_tick(stranded(700, x=7 * 2048 + 1000), Dec()) is None


def test_score_above_zero_picks_an_earning_option(gold):
    g = grid(0, **{"7,8": 3})
    d, r = make(gold, g=g)
    st = state_xy(PRIMARY_XY[0] - 3000, PRIMARY_XY[1], fuel=900, tanks=0)
    route = d.replan(st, "x", events=enemy_samples(40, (8, 5)))
    h = d.history[-1]
    assert h.kind == "earn" and h.candidate == "earn:primary" and ("primary",) in [roles(c) for c in r.calls]
    assert d.plan.pending_kinds() == (K.PRIMARY, K.SECONDARY, K.LANDING)         # nothing given up: replanned after the pass
    assert d.meta.kind == "earn" and route.version == 1


def test_earning_option_with_a_negative_net_is_skipped_for_the_bail_out(gold):
    g = grid(0, **{"7,8": 3})
    d, r = make(gold, g=g)
    d.plan.mark(K.PRIMARY, S.REACHED)                                            # only the 35-point secondary is left to earn
    st = state_xy(SECONDARY_XY[0] - 3000, SECONDARY_XY[1] - 8000, fuel=1500, tanks=0)
    ev = enemy_samples(250, (8, 8)) + [(2000, 1, 1, 10, 0x80, 1)]               # a big score, primary photographed
    score, cs = d.score_now(st, ev)
    assert score > 1000
    opts = d._target_options(st, d.plan, None, cs, score, 3 / 8)
    assert opts and opts[0].candidate.name == "earn:secondary" and opts[0].candidate.net < 0
    d.replan(st, "x", events=ev)
    assert d.history[-1].kind in ("bail", "eject") and not any(a[0] == "earn:secondary" for a in d.history[-1].attempts)


def test_unknown_event_list_waits_and_then_means_nothing_to_protect(gold):
    d, r = make(gold, g=grid(0, **{"7,8": 3}), max_retries=1)
    assert d.replan(stranded(900), "x", events=None) is None and d.version == 0 and d._retries == 1     # wait for the score
    d.replan(stranded(900), "x", events=None)                                                        # retries used up
    assert d.history[-1].kind == "bail"


def test_loiter_options_are_a_marked_todo_with_the_interface_in_place(gold):
    d, _ = make(gold)
    assert d._loiter_options(None, d.plan, None, {}, 0.0, 0.0) == [] and d._loiter_options in d.earning_sources


def test_constraint_may_choose_a_different_bail_reserve(gold):
    d, _ = make(gold)
    assert d.bail_constraints() == (FuelConstraint(100.0),)
    d2, _ = make(gold, bail_reserve_lb=0.0)
    assert d2.bail_constraints() == (FuelConstraint(0.0),)


def test_territory_sides_and_nearest_points():
    t = Territory(grid(0, **{"3,3": 3, "4,3": 2}))
    assert t.side(3 * 2048 + 5, 3 * 2048 + 5) == "friendly" and t.side(4 * 2048, 3 * 2048) == "neutral" and t.side(0, 0) == "enemy"
    assert t.side(-1, 0) == "off-map" and t.side(16 * 2048, 0) == "off-map"
    (d, p), = t.ranked_points("friendly", (0.0, 3 * 2048 + 1000.0), inset=128.0)
    assert 3 * 2048 + 128 <= p[0] <= 4 * 2048 - 128 and t.side(*p) == "friendly" and p[0] == 3 * 2048 + 128 and p[1] == 3 * 2048 + 1000.0


def test_a_splice_that_breaks_the_reserve_falls_back_to_the_plain_goals(gold):
    """2026-10-27: the reference splice ends below the reserve (the pre-flight route is slow and low), the router's own
    full mission does not: the full mission is flown, not dropped."""
    rule = lambda goals, pose, b: 100.0 if any(g.kind == "rejoin" for g in goals) else 2000.0
    d, r = make(gold, FakeRouter(rule))
    d.replan(state_at(d.reference, 10), "x")
    h = d.history[-1]
    assert h.candidate == "full" and h.skipped == () and not d.meta.follows_reference
    assert [roles(c) for c in r.calls] == [("rejoin",), ("primary", "secondary", "landing")]


def test_a_better_candidate_lost_to_a_router_error_keeps_the_route_then_gives_in(gold):
    """2026-10-27: the full mission raised a router mountain error at the first evasion replan; primary>landing routed, the
    secondary was dropped, and 1,900 lb of fuel flew home unused. The route being flown was fine on fuel: keep it and retry."""
    broken = {"on": True}
    rule = lambda goals, pose, b: PlanError("passes within 2.0 km of a mountain") if broken["on"] and (len(goals) == 3 or goals[0].kind == "rejoin") else 5000.0
    d, r = make(gold, FakeRouter(rule), max_retries=2)
    st = state_at(d.reference, 10)
    assert d.replan(st, "a") is None and d.version == 0 and d._retries == 1 and d.plan.pending_kinds() == (K.PRIMARY, K.SECONDARY, K.LANDING)
    assert d.replan(st, "b") is None and d.version == 0 and d._retries == 2
    assert d.replan(st, "c") is not None and d.history[-1].candidate == "primary>landing" and d.history[-1].skipped == (K.SECONDARY,)
    d2, _ = make(gold, FakeRouter(rule), max_retries=2)                         # the pose changes and routes again: full is flown
    d2.replan(st, "a"); broken["on"] = False
    assert d2.replan(st, "b") is not None and d2.history[-1].candidate == "full" and d2.history[-1].skipped == ()
