"""WP-7c: MissionPlan / PlanStore bookkeeping and first_feasible, on synthetic data (no planner, no game data)."""
import pytest

from director_fakes import Clock
from f19.core.types import Route, RoutePoint, Waypoint, WaypointKind as K, WaypointStatus as S
from f19.pilot.director import Attempt, Candidate, first_feasible
from f19.pilot.plan_state import MissionPlan, PlanStore
from f19.planner.router import BudgetExceeded, PlanError


def _plan():
    return MissionPlan([Waypoint(K.LANDING, (3, 3), "home"), Waypoint(K.TAKEOFF, (0, 0), "base", required=True),
                        Waypoint(K.SECONDARY, (2, 2)), Waypoint(K.PRIMARY, (1, 1))])


def test_mission_plan_order_and_statuses():
    p = _plan()
    assert [w.kind for w in p] == [K.TAKEOFF, K.PRIMARY, K.SECONDARY, K.LANDING]          # sorted into mission order
    assert [w.kind for w in p.required_pending()] == [K.TAKEOFF]                          # only takeoff is required
    assert p.mark(K.TAKEOFF, S.REACHED) and p.mark(K.PRIMARY, S.REACHED) and p.mark(K.SECONDARY, S.SKIPPED)
    assert p.pending_kinds() == (K.LANDING,) and p.landing_pending and p.pending_targets() == ()
    assert p.required_pending() == () and p.summary() == {"takeoff": "reached", "primary": "reached",
                                                           "secondary": "skipped", "landing": "pending"}


def test_status_only_moves_forward_from_pending():
    p = _plan()
    assert p.mark(K.PRIMARY, S.REACHED)
    assert not p.mark(K.PRIMARY, S.SKIPPED) and not p.mark(K.PRIMARY, S.PENDING) and p.status(K.PRIMARY) is S.REACHED
    assert not p.mark(K.PRIMARY, S.REACHED)                       # no change: False
    assert MissionPlan([Waypoint(K.TAKEOFF, (0, 0), required=True)]).mark(K.LANDING, S.REACHED) is False   # not in the plan


def test_copy_is_independent_and_waypoints_frozen():
    p = _plan(); q = p.copy()
    q.mark(K.PRIMARY, S.SKIPPED)
    assert p.status(K.PRIMARY) is S.PENDING and q.status(K.PRIMARY) is S.SKIPPED
    with pytest.raises(Exception):
        p.get(K.PRIMARY).status = S.REACHED


def test_plan_from_mission_has_only_what_the_mission_has():
    from f19.core.types import Base, Mission, Target
    m = Mission(None, targets=(Target("primary", 5, 5, "A", "Radar"),), takeoff=Base("T", 0, 0))
    p = MissionPlan.from_mission(m)
    assert [w.kind for w in p] == [K.TAKEOFF, K.PRIMARY] and not p.landing_pending and p.get(K.TAKEOFF).required


def _route(n=3, v=0): return Route(tuple(RoutePoint(float(i), 0.0, f"p{i}") for i in range(n)), v)


def test_plan_store_versions_and_records():
    st = PlanStore(_route())
    assert st.version == 0 and st.current().version == 0 and len(st.records) == 1 and st.records[0].kind == "initial"
    r = st.replace(_route(5), "deviation ended: missile", t=100.0, candidate="full", kind="mission", fuel_end_lb=1500.0,
                   plan_s=2.5, skipped=(K.SECONDARY,), attempts=(("full", "ok", 2.5),))
    assert r.version == 1 and st.current() is r and st.version == 1 and st.reference.version == 0
    r2 = st.replace(_route(2), "constraint: fuel", t=200.0, candidate="landing", kind="mission")
    assert r2.version == 2 and [x.version for x in st.records] == [0, 1, 2]
    rec = st.records[1]
    assert (rec.reason, rec.candidate, rec.points, rec.skipped, rec.t, rec.plan_s) == ("deviation ended: missile", "full", 5,
                                                                                       (K.SECONDARY,), 100.0, 2.5)


def test_reference_has_its_own_version():
    st = PlanStore(_route())
    st.replace(_route(4), "x")
    ref = st.set_reference(_route(6))
    assert ref.version == 1 and st.reference is ref and st.version == 1 and len(st.current().points) == 4


# ------------------------------------------------------------------------------------------------ first_feasible
class Fuel:
    """A constraint with the cheap test (.ok) and the real check (.check), as planner.FuelConstraint."""
    def __init__(self, reserve=300): self.reserve = reserve
    def ok(self, est, P): return est >= self.reserve
    def check(self, res, planner): return [] if res.fuel >= self.reserve else [f"fuel {res.fuel}"]


class Res:
    def __init__(self, fuel, lands=True, errors=()): self.fuel, self.lands, self.errors, self.violations = fuel, lands, list(errors), []


def C(name, **kw): return Candidate(name, "mission", **kw)


def _run(cands, est=None, plan=None, constraints=(Fuel(),), **kw):
    calls = []

    def planner_fn(state, c, b):
        calls.append((c.name, b))
        r = plan(c) if plan else Res(1000)
        if isinstance(r, BaseException): raise r
        return r
    sel = first_feasible(None, cands, constraints, (lambda s, c: est[c.name]) if est else None, planner_fn,
                         profile={}, clock=Clock(), **kw)
    return sel, calls


def test_first_feasible_returns_the_first_that_survives_in_order():
    sel, calls = _run([C("a"), C("b"), C("c")], plan=lambda c: Res(100 if c.name == "a" else 900))
    assert sel.candidate.name == "b" and [c[0] for c in calls] == ["a", "b"]           # c never routed
    assert [(a.candidate, a.verdict) for a in sel.attempts] == [("a", "infeasible"), ("b", "ok")]
    assert not sel.inconclusive


def test_estimate_rules_out_but_never_in():
    est = {"a": 100, "b": 5000, "c": 9999}                      # a fails the cheap test
    sel, calls = _run([C("a"), C("b"), C("c")], est=est, plan=lambda c: Res(100) if c.name == "b" else Res(900))
    assert [c[0] for c in calls] == ["b", "c"]                  # a ruled out without routing; b passes the estimate but fails for real
    assert [a.verdict for a in sel.attempts] == ["ruled_out", "infeasible", "ok"] and sel.candidate.name == "c"


def test_budget_exceeded_and_plan_error_fall_through_and_are_inconclusive():
    sel, _ = _run([C("a"), C("b"), C("c")],
                  plan=lambda c: {"a": BudgetExceeded("slow"), "b": PlanError("no path")}.get(c.name, Res(900)))
    assert sel.candidate.name == "c" and [a.verdict for a in sel.attempts] == ["budget", "error", "ok"] and sel.inconclusive
    sel, _ = _run([C("a")], plan=lambda c: PlanError("no path"))
    assert sel.candidate is None and sel.inconclusive


def test_proven_infeasible_is_not_inconclusive():
    sel, _ = _run([C("a"), C("b", must_land=True)], plan=lambda c: Res(50) if c.name == "a" else Res(900, lands=False))
    assert sel.candidate is None and not sel.inconclusive
    assert sel.attempts[1].verdict == "infeasible" and "runway" in sel.attempts[1].detail


def test_validation_errors_make_a_candidate_infeasible():
    sel, _ = _run([C("a"), C("b")], plan=lambda c: Res(900, errors=["terrain: leg 3"]) if c.name == "a" else Res(900))
    assert sel.candidate.name == "b" and "terrain" in sel.attempts[0].detail


def test_eject_in_place_needs_no_router():
    sel, calls = _run([C("a"), Candidate("eject", "bail", eject_in_place=True)], plan=lambda c: Res(1))
    assert sel.candidate.name == "eject" and sel.result is None and [c[0] for c in calls] == ["a"]


def test_budgets_per_candidate_and_total():
    clock = Clock()

    def plan(state, c, b):
        clock.t += 6.0                                          # every router call burns 6 s
        raise BudgetExceeded("spent")
    sel = first_feasible(None, [C("a"), C("b"), C("c"), C("d")], (), None, plan, budget_s=4.0, total_budget_s=15.0, clock=clock)
    # a: 6 s, retried once (6 s) = 12 s; b gets the 3 s left, burns 6 and has no time for a retry; c and d find the total spent
    assert [a.verdict for a in sel.attempts] == ["budget", "budget", "out_of_time", "out_of_time"]
    seen = []
    first_feasible(None, [C("a"), C("b")], (), None, lambda s, c, b: seen.append(b) or Res(1), budget_s=4.0, total_budget_s=2.5, clock=Clock())
    assert seen == [2.5]


class PDetect:
    """A fake p(detection) constraint: the plug-in contract is .check(result, planner) (+ optional .ok / .live_violation)."""
    def __init__(self): self.seen = []
    def check(self, res, planner): self.seen.append(res.fuel); return ["p(detect) 0.8"] if res.fuel < 500 else []


def test_constraint_list_is_pluggable():
    pd = PDetect()
    sel, _ = _run([C("a"), C("b")], constraints=(Fuel(), pd), plan=lambda c: Res(400) if c.name == "a" else Res(900))
    assert sel.candidate.name == "b" and pd.seen == [400, 900]
    assert sel.attempts[0].verdict == "infeasible"
    sel, _ = _run([C("a"), C("b")], constraints=(), plan=lambda c: Res(1))
    assert sel.candidate.name == "a"                            # no constraints: any real result passes


def test_attempt_tuple():
    assert Attempt("x", "ok", 1.23456).astuple() == ("x", "ok", 1.235)
