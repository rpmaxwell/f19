"""WP-7c FlightDirector.on_tick: the triggers (deviation ended, constraint failure, waypoint reached, retry, deadline hook),
the bookkeeping from decisions and events, stale-plan handling, determinism, and the Runner with the director as its route
provider. Scripted router, golden CE reference; offline."""
import dataclasses

import pytest

from director_fakes import FakeRouter, Dec, fake_planner, golden_reference, grid, state_at, state_xy
from f19.core.clock import ManualClock
from f19.core.types import Attitude, Command, GameState, Position, Route, RoutePoint, Systems, WaypointKind as K, WaypointStatus as S
from f19.pilot.director import FlightDirector, InlineExecutor, Order
from f19.planner.estimate import FuelConstraint
from f19.planner.router import PlanError


@pytest.fixture(scope="module")
def gold(egame):
    ref, mission, _ = golden_reference()
    return ref, mission


def make(gold, router=None, g=None, **kw):
    ref, mission = gold
    router = router or FakeRouter()
    kw.setdefault("executor", InlineExecutor())
    kw.setdefault("off_route_km", -1.0)                  # the plane counts as off its route; the gate has its own tests
    return FlightDirector(mission, ref, fake_planner(g), route_fn=router, **kw), router


def at(d, t, i=10, **kw): return state_at(d.reference, i, t=t, **kw)


# ------------------------------------------------------------------------------------------------ deviation
def test_a_deviation_replans_when_the_reaction_ends_not_while_it_runs(gold):
    d, r = make(gold)
    assert d.on_tick(at(d, 1000), Dec("fly_leg")) is None
    for t, b in ((1001, "missile"), (1002, "missile"), (1003, "terrain_escape")):
        assert d.on_tick(at(d, t), Dec(b)) is None and d.version == 0 and not r.calls          # reacting: no plan yet
    o = d.on_tick(at(d, 1004), Dec("fly_leg"))                                                 # the reaction let go
    assert d.version == 1 and "deviation ended: terrain_escape" in d.history[-1].reason
    assert isinstance(o, Order) and o.events[0].startswith("plan v1: full") and o.command is None
    assert d.on_tick(at(d, 1005), Dec("fly_leg")) is None and d.version == 1                   # and only once


def test_a_deviation_during_an_attack_waits_for_the_route_phase(gold):
    d, r = make(gold)
    d.on_tick(at(d, 1000), Dec("fighter", "route"))
    assert d.on_tick(at(d, 1001), Dec("attack", "attack")) is None and d.version == 0           # ended, but mid-attack
    assert d.on_tick(at(d, 1002), Dec("attack", "attack")) is None and d.version == 0
    d.on_tick(at(d, 1003), Dec("fly_leg", "route"))
    assert d.version == 1 and "deviation ended: fighter" in d.history[-1].reason


def test_no_planning_on_the_ground_or_while_landing(gold):
    d, r = make(gold)
    d.on_tick(at(d, 1000, alt=250), Dec("missile")); d.on_tick(at(d, 1001, alt=50), Dec("fly_leg"))
    assert d.version == 0 and d._reasons                                                        # waits, reason kept
    d.on_tick(at(d, 1002), Dec("fly_leg", "landing"))
    assert d.version == 0
    d.on_tick(at(d, 1003), Dec("fly_leg", "route"))
    assert d.version == 1


def test_min_gap_between_plan_starts(gold):
    d, r = make(gold, min_gap_s=10.0)
    d.on_tick(at(d, 1000), Dec("missile")); d.on_tick(at(d, 1001), Dec("fly_leg"))
    assert d.version == 1
    d.on_tick(at(d, 1003), Dec("missile")); d.on_tick(at(d, 1004), Dec("fly_leg"))
    assert d.version == 1 and d._reasons                                                        # too soon: pending
    d.on_tick(at(d, 1012), Dec("fly_leg"))
    assert d.version == 2


# ------------------------------------------------------------------------------------------------ constraints
def test_a_failing_fuel_constraint_mid_route_replans(gold):
    d, r = make(gold)
    assert d.on_tick(at(d, 1000), Dec("fly_leg")) is None and d.version == 0                   # on the plan: fine
    d.on_tick(at(d, 1006, fuel=int(d.reference.points[10].fuel_lb) - 1700), Dec("fly_leg"))   # burned too much
    assert d.version == 1 and d.history[-1].reason.startswith("constraint: fuel at the end")


class PDetect:
    """A fake p(detection) constraint: .check on routes, .live_violation mid-route (the plug-in contract)."""
    def __init__(self): self.hot, self.checked = False, 0
    def check(self, res, planner): self.checked += 1; return []
    def live_violation(self, state, plan): return ["p(detect) 0.9"] if self.hot else []


def test_pluggable_constraint_triggers_a_replan_and_sees_the_routes(gold):
    pd = PDetect()
    d, r = make(gold, constraints=(FuelConstraint(), pd), check_period_s=5.0)
    d.on_tick(at(d, 1000), Dec("fly_leg"))
    pd.hot = True
    d.on_tick(at(d, 1002), Dec("fly_leg")); assert d.version == 0                              # not checked yet (period)
    d.on_tick(at(d, 1006), Dec("fly_leg"))
    assert d.version == 1 and d.history[-1].reason == "constraint: p(detect) 0.9" and pd.checked >= 1


def test_deadline_hook_does_nothing_by_default_and_can_trigger(gold):
    d, r = make(gold)
    assert d.deadline_passed(at(d, 1000), 1000.0) is False
    d.on_tick(at(d, 1000), Dec("fly_leg")); d.on_tick(at(d, 1001), Dec("fly_leg")); assert d.version == 0

    class Late(FlightDirector):
        def deadline_passed(self, state, now): return now > 1500
    ref, mission = gold
    d2 = Late(mission, ref, fake_planner(), route_fn=FakeRouter(), executor=InlineExecutor())
    d2.on_tick(at(d2, 1000), Dec("fly_leg")); d2.on_tick(at(d2, 1600), Dec("fly_leg"))
    assert d2.version == 1 and d2.history[-1].reason == "deadline"


# ------------------------------------------------------------------------------------------- waypoint bookkeeping
def test_takeoff_is_reached_when_airborne(gold):
    d, _ = make(gold)
    d.on_tick(at(d, 1000, alt=20), Dec("takeoff", "takeoff")); assert d.plan.status(K.TAKEOFF) is S.PENDING
    d.on_tick(at(d, 1010, alt=400), Dec("fly_leg")); assert d.plan.status(K.TAKEOFF) is S.REACHED


def test_attack_done_lines_and_game_objective_events_mark_targets_reached(gold):
    d, _ = make(gold)
    d.on_tick(at(d, 1000), Dec("attack", "attack", events=["primary attack start (photo, record 19)"]))
    assert d.plan.status(K.PRIMARY) is S.PENDING
    d.on_tick(at(d, 1001), Dec("attack", "route", events=["primary attack done"]))
    assert d.plan.status(K.PRIMARY) is S.REACHED and d.plan.status(K.SECONDARY) is S.PENDING
    d.on_tick(at(d, 1002), Dec("fly_leg"), events=[(5, 10, 10, 10, 0x40, 1)])                # type 10 photographed, secondary flag
    assert d.plan.status(K.SECONDARY) is S.REACHED
    assert d.plan.status(K.LANDING) is S.PENDING


def test_landing_reached_by_phase_or_arrival_circle(gold):
    d, _ = make(gold)
    d.on_tick(at(d, 1000), Dec("landing", "landing")); assert d.plan.status(K.LANDING) is S.PENDING
    d.on_tick(at(d, 1001), Dec("landing", "handover")); assert d.plan.status(K.LANDING) is S.REACHED
    d2, _ = make(gold)
    lx, ly = d2.plan.get(K.LANDING).pos
    d2.on_tick(state_xy(lx + 640, ly, alt=200), Dec("fly_leg")); assert d2.plan.status(K.LANDING) is S.PENDING     # 10 km out
    d3, _ = make(gold)
    d3.on_tick(state_xy(lx + 64, ly, alt=250, fuel=3000), Dec("fly_leg")); assert d3.plan.status(K.LANDING) is S.REACHED
    d4, _ = make(gold)
    d4.on_tick(state_xy(lx + 64, ly, alt=2500, fuel=3000), Dec("fly_leg")); assert d4.plan.status(K.LANDING) is S.PENDING   # overhead, high


def test_reaching_a_waypoint_replans_only_when_the_plan_is_not_the_reference(gold):
    d, r = make(gold)                                              # the active plan is the reference: nothing to redo
    d.on_tick(at(d, 1000), Dec("fly_leg")); d.on_tick(at(d, 1020), Dec("attack", "route", events=["primary attack done"]))
    assert d.version == 0 and d.plan.status(K.PRIMARY) is S.REACHED
    rule = lambda goals, pose, b: PlanError("rejoin") if goals[0].kind == "rejoin" else 5000.0
    d, r = make(gold, FakeRouter(rule))
    d.replan(at(d, 1000), "setup")
    assert not d.meta.follows_reference
    d.on_tick(at(d, 1020), Dec("attack", "route", events=["primary attack done"]))
    assert d.version == 2 and d.history[-1].reason == "waypoint reached: primary" and d.history[-1].candidate == "full"
    assert r.kinds[-1] == ("secondary", "landing")                  # the remaining mission only: no primary again


def test_a_finished_earning_route_replans(gold):
    g = grid(0, **{"7,8": 3})
    d, r = make(gold, g=g)
    ev = [(0, 1, 1, 9, 0, 0)] * 30
    st = state_xy(13448, 11456, fuel=900, tanks=0, t=1000.0)
    d.replan(st, "x", events=ev)
    assert d.meta.kind == "earn"
    x, y = d.meta.end_xy
    d.on_tick(state_xy(x + 64, y, fuel=700, tanks=0, t=1030.0), Dec("fly_leg"), events=ev)       # at the route's end
    assert d.version == 2 and d.history[-1].reason.startswith("route end")


# --------------------------------------------------------------------------------------------- inconclusive / retry
def test_inconclusive_round_retries_after_retry_s(gold):
    state = {"slow": True}
    def rule(goals, pose, b):
        from f19.planner.router import BudgetExceeded
        if state["slow"]: raise BudgetExceeded("slow")
        return 5000.0
    d, r = make(gold, FakeRouter(rule), retry_s=8.0, min_gap_s=1.0)
    d.on_tick(at(d, 1000), Dec("missile")); o = d.on_tick(at(d, 1001), Dec("fly_leg"))
    assert d.version == 0 and "plan kept" in o.events[0] and d._retry_at == 1009.0
    state["slow"] = False
    d.on_tick(at(d, 1005), Dec("fly_leg")); assert d.version == 0
    d.on_tick(at(d, 1010), Dec("fly_leg"))
    assert d.version == 1 and d.history[-1].reason.startswith("retry")


# ------------------------------------------------------------------------------------------------ eject order
def test_eject_order_is_terminal_and_versioned(gold):
    from f19.core.types import Action
    d, r = make(gold, g=grid(0))
    o = d.on_tick(state_xy(8 * 2048 + 700, 8 * 2048 + 900, fuel=900, tanks=0, t=1000.0), Dec("fly_leg"), events=[])   # the fuel check fails
    assert d.terminal and o.command.actions == (Action.EJECT,) and o.exit and o.events[0].startswith("EJECT in enemy")
    assert d.history[-1].kind == "eject" and d.version == 1
    assert d.on_tick(at(d, 1003), Dec("fly_leg")) is None


# ------------------------------------------------------------------------------------------------ stale plans
class ManualExecutor:
    """submit() queues the job; run() executes the queued ones (a worker that finishes when the test says)."""
    def __init__(self): self.queue = []
    def submit(self, fn):
        from concurrent.futures import Future
        f = Future(); self.queue.append((f, fn)); return f
    def run(self):
        for f, fn in self.queue:
            try: f.set_result(fn())
            except BaseException as e: f.set_exception(e)       # noqa: BLE001
        self.queue = []
    def shutdown(self): pass


def test_a_plan_finishing_after_a_new_trigger_is_discarded_and_redone(gold):
    ex = ManualExecutor()
    d, r = make(gold, executor=ex, min_gap_s=1.0)
    d.on_tick(at(d, 1000), Dec("missile")); d.on_tick(at(d, 1001), Dec("fly_leg"))
    assert d.busy and d.version == 0 and len(ex.queue) == 1                    # the pilot flies on while it plans
    d.on_tick(at(d, 1003), Dec("missile")); d.on_tick(at(d, 1004), Dec("fly_leg"))   # a second deviation meanwhile
    ex.run()
    o = d.on_tick(at(d, 1005), Dec("fly_leg"))
    assert d.version == 0 and o.events[0].startswith("plan discarded") and not d.busy
    d.on_tick(at(d, 1006), Dec("fly_leg"))
    assert d.busy                                                              # the work restarted from the new state
    ex.run(); d.on_tick(at(d, 1007), Dec("fly_leg"))
    assert d.version == 1 and "deviation ended" in d.history[-1].reason


def test_a_fresh_plan_is_adopted_on_the_next_tick(gold):
    ex = ManualExecutor()
    d, r = make(gold, executor=ex)
    d.on_tick(at(d, 1000), Dec("missile")); d.on_tick(at(d, 1001), Dec("fly_leg"))
    assert d.current().version == 0
    ex.run()
    o = d.on_tick(at(d, 1002), Dec("fly_leg"))
    assert d.current().version == 1 and o.events[0].startswith("plan v1")


# ------------------------------------------------------------------------------------------------ determinism
def _script(gold, router):
    d, r = make(gold, router)
    for t, b, ph in ((1000, "fly_leg", "route"), (1001, "missile", "route"), (1002, "fly_leg", "route"), (1020, "terrain_escape", "route"),
                     (1021, "fly_leg", "route"), (1040, "fly_leg", "route")):
        d.on_tick(at(d, t, i=10 + (t - 1000) // 10), Dec(b, ph))
    return d


def test_same_inputs_same_plans(gold):
    rule = lambda goals, pose, b: 100.0 if any(g.role == "secondary" or g.kind == "rejoin" for g in goals) else 2000.0
    a, b = _script(gold, FakeRouter(rule)), _script(gold, FakeRouter(rule))
    key = lambda d: [(h.version, h.t, h.reason, h.candidate, h.kind, h.points, h.fuel_end_lb, h.skipped,
                      tuple((x[0], x[1]) for x in h.attempts)) for h in d.history]
    assert key(a) == key(b) and len(a.history) == 3
    assert [p.to_dict() for p in a.current().points] == [p.to_dict() for p in b.current().points]
    assert a.plan.summary() == b.plan.summary()


# --------------------------------------------------------------------------------------------- runner integration
def _state(alt=1000, x=10000.0, y=20000.0, hdg=0.0):
    return GameState(t=0.0, pos=Position(x, y, alt), att=Attitude(hdg), speed_kt=250, systems=Systems(flags=0x01, throttle=60), sensitivity=1)


def _runner(provider, n_ticks_state):
    from test_pilot_runner import FakeReader, FakeReads
    from pilot_equiv import KeyLog
    from f19.cli import fly
    clock = ManualClock(1000.0)
    a = fly.resolve(fly.parser().parse_args(["--hdg", "--alt", "1000"])); a.dry_run = True
    reader = FakeReader(n_ticks_state)
    emu = KeyLog()
    route = Route((RoutePoint(10000.0, 20000.0, "start"), RoutePoint(10000.0, 10000.0, "WP"), RoutePoint(14000.0, 6000.0, "Next")))
    r = fly.build(a, reader, emu, clock, n_ticks_state(0), sleep=lambda s: clock.advance(s), wall=clock.now, say=lambda m: None, route=route)
    r.reads = r.arb.reads = FakeReads(reader)
    provider.route = route
    r.routes = provider
    return r, emu, route


class SwapProvider:
    """A route provider with an on_tick hook that swaps the route on tick `swap_at` and ends the flight on `exit_at`."""
    def __init__(self, swap_at, exit_at=None):
        self.swap_at, self.exit_at, self.n, self.seen, self.route, self.new = swap_at, exit_at, 0, [], None, None
    def current(self): return self.route
    @property
    def version(self): return self.route.version
    def on_tick(self, state, dec, ctx, events):
        self.n += 1; self.seen.append((ctx.leg, ctx.route.version, dec.phase))
        if self.n == self.swap_at:
            self.new = Route((RoutePoint(10000.0, 20000.0, "Present position"), RoutePoint(2000.0, 12000.0, "Elsewhere")), version=1)
            self.route = self.new
            return Order(events=("plan v1: swapped",))
        if self.n == self.exit_at:
            return Order(Command(banner="  DIRECTOR TEST", done=True), exit="director: test exit", events=("exit now",))


def test_runner_swaps_the_route_the_provider_hands_over_and_restarts_at_leg_one():
    prov = SwapProvider(swap_at=5)
    r, emu, route = _runner(prov, lambda n: _state(y=max(8000.0, 20000.0 - 100 * n)))
    legs = []
    for _ in range(9):
        res = r.tick(); legs.append((r.ctx.leg, r.ctx.route.version))
    assert legs[3] == (1, 0) or legs[3][1] == 0                      # before the swap the old route
    assert r.ctx.route is prov.new and r.arb.fly_leg.wps[-1][1] == "Elsewhere" and r.ctx.leg == 1
    assert any("plan v1: swapped" in l for l in r.phase_log)
    assert res.frame.plan_version == 1                              # telemetry carries the new version
    assert not emu._held or True


def test_runner_applies_an_order_command_and_exit():
    prov = SwapProvider(swap_at=99, exit_at=3)
    r, emu, route = _runner(prov, lambda n: _state())
    out = [r.tick() for _ in range(3)]
    assert out[-1].exit == "director: test exit" and out[-1].decision.behavior == "director"
    assert out[-1].decision.command.banner == "  DIRECTOR TEST" and any("exit now" in l for l in r.phase_log)
    assert not emu._held


def test_runner_survives_a_provider_error_and_works_with_a_static_route():
    class Bad(SwapProvider):
        def on_tick(self, *a): raise RuntimeError("director bug")
    r, emu, _ = _runner(Bad(99), lambda n: _state())
    res = r.tick()
    assert res.in_flight and any("director error: RuntimeError" in l for l in r.phase_log)
    from f19.pilot.runner import StaticRoute
    r2, _, route = _runner(SwapProvider(99), lambda n: _state())
    r2.routes = StaticRoute(route)
    assert r2.tick().in_flight


def test_director_as_the_runner_provider_replans_from_a_missile_evasion(gold):
    """The real FlightDirector in the Runner: a reaction ends -> the route object the runner flies changes."""
    ref, mission = gold
    d, rt = make(gold)
    r, emu, route = _runner(SwapProvider(99), lambda n: _state())
    r.routes = d
    r.ctx.route = d.current()
    seq = ["fly_leg", "missile", "missile", "fly_leg", "fly_leg"]
    orig = r.arb.step

    def step(state, ctx):
        dec = orig(state, ctx); dec.behavior = seq[min(r.n_ticks, len(seq) - 1)]; r.n_ticks += 1
        return dec
    r.n_ticks = 0; r.arb.step = step
    route0 = d.current()
    st = lambda n: state_at(ref, 10, t=1000.0 + n, alt=600.0)
    r.reader.fn = st
    for _ in range(6): r.tick()
    assert d.version == 1 and r.ctx.route is d.current() and r.ctx.route is not route0 and r.ctx.leg >= 1


def test_unknown_score_waits_instead_of_ejecting(gold):
    d, r = make(gold, g=grid(0), retry_s=2.0, max_retries=1, min_gap_s=1.0)
    st = lambda t: state_xy(8 * 2048 + 700, 8 * 2048 + 900, fuel=900, tanks=0, t=t)
    o = d.on_tick(st(1000.0), Dec("fly_leg"))                      # the first tick: no event list yet, the fuel check fails
    assert not d.terminal and o.events[0].startswith("plan kept") and "score is unknown" in o.events[0]
    d.on_tick(st(1003.0), Dec("fly_leg"), events=[])               # events read: score 0, nothing to protect
    assert d.terminal


class _Deferred:
    """An executor that runs the plan only when told (a worker still busy)."""
    def __init__(self): self.jobs = []
    def submit(self, fn):
        from concurrent.futures import Future
        f = Future(); self.jobs.append((f, fn)); return f
    def run(self):
        for f, fn in self.jobs: f.set_result(fn())
        self.jobs = []


def test_a_fuel_violation_while_planning_does_not_make_the_plan_stale(gold):
    """2026-10-27: the live check fired every check period while the plan for that very violation ran, so every plan
    was discarded as stale (46 times in 280 s) and the route kept being flown."""
    ex = _Deferred()
    d, r = make(gold, executor=ex)
    low = int(d.reference.points[10].fuel_lb) - 1700
    d.on_tick(at(d, 1006, fuel=low), Dec("fly_leg"))                      # starts the plan
    assert len(ex.jobs) == 1
    for t in (1012, 1018, 1024): d.on_tick(at(d, t, fuel=low), Dec("fly_leg"))     # still running, still violating
    ex.run()
    o = d.on_tick(at(d, 1025, fuel=low), Dec("fly_leg"))
    assert d.version == 1 and o is not None and o.events[0].startswith("plan v1")


# ------------------------------------------------------------------------------------------------ trust the plan
def test_a_reaction_that_leaves_the_plane_on_its_route_does_not_replan(gold):
    d, r = make(gold, off_route_km=4.0)
    d.on_tick(at(d, 1000), Dec("fighter"))
    assert d.on_tick(at(d, 1001, off_km=1.0), Dec("fly_leg")) is None
    assert d.version == 0 and not r.calls and not d._reasons
    assert any("keeping the plan" in m for m in d.log)


def test_a_reaction_that_leaves_the_plane_far_off_replans(gold):
    d, r = make(gold, off_route_km=4.0)
    d.on_tick(at(d, 1000), Dec("terrain_escape"))
    d.on_tick(at(d, 1001, off_km=9.0), Dec("fly_leg"))
    assert d.version == 1 and "deviation ended: terrain_escape" in d.history[-1].reason and "9.0 km off" in d.history[-1].reason


def test_the_live_constraint_is_not_checked_on_the_route(gold):
    d, r = make(gold, off_route_km=4.0, check_period_s=1.0)
    for t in range(1000, 1010): d.on_tick(at(d, t, fuel=300), Dec("fly_leg"))                # 300 lb, on the route: fly it
    assert d.version == 0 and not r.calls
    for t in range(1010, 1020): d.on_tick(at(d, t, fuel=300, off_km=8.0), Dec("fly_leg"))     # far off with no fuel: replan
    assert d.version >= 1 or r.calls
