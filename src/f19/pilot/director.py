"""The FlightDirector (WP-7c): "what are we doing now". The USER's design (refactor plan 4.2, recorded 2026-10-26):

  The mission has ordered waypoints (takeoff, primary, secondary, landing); only TAKEOFF is required. The best-case
  pre-flight route (the reference) is flown first. After EVERY deviation (any reaction that took control, or a
  constraint failing mid-route) the plan is wrong and is rebuilt from the current state:

    1. mission chain, first feasible:  full remaining mission -> primary>landing -> secondary>landing -> landing
    2. no landing feasible:  score after bail-out <= 0  -> straight to the bail-out chain;
                             score after bail-out  > 0  -> rank the earning options (a reachable live target, a score
                             loiter) by  net = points - (penalty where the option leaves us - best bail-out penalty
                             reachable now); fly the best positive one, then the bail-out chain
    3. bail-out chain, first feasible: friendly -> neutral / ocean -> enemy. Per territory type: the nearest reachable
       cell of that type (a route into it that satisfies the constraints); enemy = eject in place, always feasible.
       The plane ejects the moment it is in a cell of the wanted type.
  Constraints are a pluggable list (only planner.FuelConstraint now; p(detection) / p(missile hit) later).

How a candidate is judged (WP-6 finding: the cheap fuel estimate is only a LOWER BOUND, optimistic by hundreds of lb and
up to ~4.6k lb straight to landing): the estimate may only rule a candidate OUT. A candidate that passes is confirmed
with the real router (planner.route, integrated fuel) and the constraints run on that result. BudgetExceeded / PlanError
make the candidate infeasible for this round; but if the whole mission chain is lost to such failures alone (nothing
was proven infeasible), the director does NOT drop into the bail-out chain at once: it keeps the current route and
retries (`retry_s`, `max_retries` times), because ejecting over a planner time-out would throw a healthy flight away.

Threading. planner.route takes 1-12 s (WP-6), the runner ticks every 0.15 s. `on_tick` therefore never plans: it
detects the triggers, snapshots (GameState, event list, a copy of the MissionPlan) and hands the work to ONE worker
thread (`ThreadExecutor`, daemon). The pilot keeps flying the active route while it runs (no safe holding route: the
current route is what the arbiter's reactions already fly around). A finished plan is adopted on the main thread by
`PlanStore.replace`, in the next `on_tick`; the runner picks the new Route up at its next tick. A plan whose inputs went
stale (a new trigger fired while it ran) is discarded and the work restarted from the new state. The planner is only
ever touched by the worker (its caches are not thread-safe); tests use `InlineExecutor` for determinism. The worker
shares the GIL: numpy releases it, the Python parts of the router do not, so a busy planner delays the caller's loop
(measured 2026-10-26 on the golden CE plan, test_director_golden.py: a rejoin replan took 2.25 s end to end, on_tick
returned in 8 ms, and the caller's 10 ms-sleep loop saw gaps of up to ~230 ms while the worker ran: about one extra
0.15 s tick period). A process pool would remove that but needs the planner context rebuilt per process (1.5-3 s).

Planning times measured on the real planner (golden CE, 4 states): full-mission rejoin 0.5-2.3 s, landing-only 0.6-1.7 s,
a whole bail-out chain with three cell routes 1-2.4 s; defaults: budget_s 4 per candidate (a time-out is retried once with 3x), total_budget_s 30 per chain.

The plan is built from the state at the trigger; by adoption the plane has flown a few seconds on. The new Route starts
at that old pose; FlyLeg joins the nearest leg (the plane is some hundred metres from it).

Not decided (open design questions, see the WP-7c report): the bail-out multipliers for friendly / neutral cells
(BAIL_FRACTION: only enemy x2/8 is confirmed), loiter earning options (a marked TODO with its interface in place), and
alternate airfields for the landing candidates.
"""
import dataclasses
import math
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field

import numpy as np

from f19.core.types import Action, Command, Route, WaypointKind, WaypointStatus
from f19.core.units import K
from f19.pilot.plan_state import TARGET_KINDS, MissionPlan, PlanStore
from f19.planner import api
from f19.planner import estimate as est_mod
from f19.planner.estimate import FuelConstraint
from f19.planner.router import BudgetExceeded, PlanError
from f19.rules import scoring

# --------------------------------------------------------------------------------------------------- constants
REACTIONS = ("fighter", "missile", "terrain_escape")      # Decision.behavior values that took control of the plane
FRIENDLY, NEUTRAL, ENEMY = "friendly", "neutral", "enemy"
BAIL_ORDER = (FRIENDLY, NEUTRAL, ENEMY)                    # the chain
# Final score after ejecting = score x fraction. Only ENEMY (2/8) is confirmed (scoring-awards.md: x [2,3,6,4][byte
# 0x978c] / 8, index 0 = enemy). Which of 3/8, 6/8, 4/8 belongs to friendly / neutral cells is not known: ranking uses
# the smallest of them (3/8) for both, i.e. the director never counts on more than that. Override with bail_fractions=.
BAIL_FRACTION = {ENEMY: 2 / 8, NEUTRAL: 3 / 8, FRIENDLY: 3 / 8}
CELL = 2048.0                                              # territory cell: 32 km in units
SIDE_CODE = {FRIENDLY: 3, NEUTRAL: 2, ENEMY: 0}
ATTACK_DONE = re.compile(r"^(primary|secondary) attack done")
ARRIVE_KM = {WaypointKind.LANDING: 3.0}                    # arrival circle per waypoint (targets: events only)


# --------------------------------------------------------------------------------------------------- territory
class Territory:
    """The 16 x 16 territory grid ([y, x] uint8, 32 km cells, low two bits: 3 friendly, 2 neutral, 0 enemy; 1 unknown)."""

    def __init__(self, grid): self.g = np.asarray(grid)

    @classmethod
    def from_planner(cls, planner): return cls(planner.W.tgrid)

    def side(self, x, y):
        gx, gy = int(x) >> 11, int(y) >> 11
        if not (0 <= gx < 16 and 0 <= gy < 16): return "off-map"
        return {3: FRIENDLY, 2: NEUTRAL, 0: ENEMY}.get(int(self.g[gy, gx]) & 3, "1?")

    def ranked_points(self, side, xy, inset=2.0 * K):
        """[(distance, (x, y))]: for every cell of `side` the point in it nearest to xy (kept `inset` units inside the
        cell edges), nearest first."""
        out = []
        for gy, gx in zip(*np.nonzero((self.g & 3) == SIDE_CODE[side])):
            lo_x, lo_y = gx * CELL + inset, gy * CELL + inset
            p = (min(max(xy[0], lo_x), (gx + 1) * CELL - inset), min(max(xy[1], lo_y), (gy + 1) * CELL - inset))
            out.append((math.dist(xy, p), p))
        return sorted(out)


# --------------------------------------------------------------------------------------------------- executors
class InlineExecutor:
    """Runs the job at submit (deterministic: tests, replays)."""
    def submit(self, fn):
        f = Future()
        try: f.set_result(fn())
        except BaseException as e: f.set_exception(e)       # noqa: BLE001
        return f

    def shutdown(self): pass


class ThreadExecutor:
    """One daemon thread per job (jobs never overlap: the director starts one at a time)."""
    def submit(self, fn):
        f = Future()

        def run():
            try: f.set_result(fn())
            except BaseException as e: f.set_exception(e)   # noqa: BLE001
        threading.Thread(target=run, name="f19-director-plan", daemon=True).start()
        return f

    def shutdown(self): pass


# --------------------------------------------------------------------------------------------------- candidates
@dataclass(frozen=True)
class Candidate:
    """One thing the director could do next. goal_sets: alternative planner Goal tuples, tried in order (a reference
    splice first, the from-scratch chain second)."""
    name: str
    kind: str                            # mission | earn | bail
    waypoints: tuple = ()                # WaypointKinds this candidate flies (mission / earn)
    goal_sets: tuple = ()
    must_land: bool = False
    chain_xy: tuple = ()                 # straight-line chain for the cheap estimate
    rejoin_index: int | None = None      # reference point a splice joins at (estimate_rejoin)
    side: str | None = None              # bail: the territory type wanted; earn: where the option leaves us
    eject_in_place: bool = False
    points: float = 0.0                  # earn: score gained
    net: float = 0.0                     # earn: ranking value
    follows_reference: bool = False      # goal_sets[0] is a reference splice


@dataclass
class Attempt:
    candidate: str
    verdict: str                         # ok | ruled_out | infeasible | budget | error | out_of_time
    seconds: float = 0.0
    detail: str = ""

    def astuple(self): return (self.candidate, self.verdict, round(self.seconds, 3))


@dataclass
class Selection:
    candidate: Candidate | None
    result: object
    attempts: list

    @property
    def inconclusive(self):
        """Some candidate was lost to a planner time-out / error rather than proven infeasible."""
        return any(a.verdict in ("budget", "error", "out_of_time") for a in self.attempts)


def first_feasible(state, candidates, constraints, estimate, plan, *, profile=None, planner=None, budget_s=4.0,
                   total_budget_s=30.0, clock=time.monotonic):
    """The first candidate that survives, in order.

    estimate(state, cand) -> Estimate | None   the cheap lower bound; a constraint with .ok(est, profile) that fails
                                               on it rules the candidate OUT (never in)
    plan(state, cand, budget_s) -> RouteResult | None   the real router (None: nothing to route, an eject in place);
                                               BudgetExceeded / PlanError -> infeasible this round (verdicts "budget" /
                                               "error": Selection.inconclusive)
    The real result must have no validation errors, no constraint violations (each constraint's
    .check(result, planner), appended to result.violations) and, for must_land candidates, end on a runway.
    Time: budget_s per candidate, total_budget_s for the whole call. -> Selection(candidate | None, result, attempts)."""
    attempts, t_end = [], clock() + total_budget_s
    for cand in candidates:
        t0 = clock()
        if cand.eject_in_place:
            attempts.append(Attempt(cand.name, "ok")); return Selection(cand, None, attempts)
        if estimate is not None and profile is not None:
            try: est = estimate(state, cand)
            except Exception: est = None                     # noqa: BLE001 - no estimate: the router decides
            bad = [c for c in constraints if est is not None and hasattr(c, "ok") and not c.ok(est, profile)]
            if bad:
                attempts.append(Attempt(cand.name, "ruled_out", clock() - t0,
                                        f"estimate: fuel at the end {getattr(est, 'fuel_end_lb', float('nan')):.0f} lb"))
                continue
        left = t_end - clock()
        if left <= 0:
            attempts.append(Attempt(cand.name, "out_of_time")); continue
        try:
            try:
                res = plan(state, cand, min(budget_s, left))
            except BudgetExceeded:                           # a slow candidate is not an infeasible one: once more, 3x the time,
                left = t_end - clock()                       # before a lesser candidate takes its place (Liberec 2026-10-27)
                if left <= 0: raise
                res = plan(state, cand, min(3 * budget_s, left))
        except BudgetExceeded as e:
            attempts.append(Attempt(cand.name, "budget", clock() - t0, str(e))); continue
        except PlanError as e:
            attempts.append(Attempt(cand.name, "error", clock() - t0, str(e))); continue
        if res is not None:
            viol = [m for c in constraints for m in c.check(res, planner)]
            res.violations = list(getattr(res, "violations", [])) + viol
            why = (f"errors: {res.errors[:2]}" if res.errors else f"violations: {res.violations[:2]}" if res.violations
                   else "does not end on a runway" if cand.must_land and not res.lands else "")
            if why:
                attempts.append(Attempt(cand.name, "infeasible", clock() - t0, why)); continue
        attempts.append(Attempt(cand.name, "ok", clock() - t0))
        return Selection(cand, res, attempts)
    return Selection(None, None, attempts)


# --------------------------------------------------------------------------------------------------- results
@dataclass
class Order:
    """What on_tick hands the runner: a Command to merge into the tick's (the EJECT), an exit text (the flight is over),
    and mission-log lines."""
    command: Command | None = None
    exit: str | None = None
    events: tuple = ()


@dataclass
class Outcome:
    """What one planning round found (built by the worker, adopted on the main thread)."""
    kind: str                            # route | eject | keep
    candidate: Candidate | None = None
    result: object = None
    route: Route | None = None
    reason: str = ""
    attempts: list = field(default_factory=list)
    elapsed_s: float = 0.0
    skipped: tuple = ()                  # WaypointKinds given up
    live_ref: object = None              # estimate.Reference of the new route (the live constraint check)
    constraints: tuple = ()
    error: str = ""
    note: str = ""                       # why this branch (score, penalties, ...)


@dataclass
class ActivePlan:
    """What the director knows about the route being flown (live checks, the eject test)."""
    kind: str                            # reference | mission | earn | bail
    candidate: str = ""
    side: str | None = None
    follows_reference: bool = True
    ref: object = None                   # estimate.Reference of the active route
    constraints: tuple = ()
    end_xy: tuple | None = None


@dataclass
class EarningOption:
    """A way to earn points with the remaining fuel (`target` options now; score loiters are the TODO)."""
    candidate: Candidate
    points: float
    end_side: str


# --------------------------------------------------------------------------------------------------- the director
class FlightDirector:
    """FlightDirector(mission, reference, planner=...): the runner's route provider (current() / version / on_tick() /
    replan()). See the module docstring.

    mission: core Mission. reference: the pre-flight Route (route.json). planner: planner.api.context() (or planner_factory,
    built lazily in the worker). constraints: pluggable list, default (FuelConstraint(),); each has .check(result,
    planner) -> [violation messages] and optionally .ok(estimate, P) (cheap rule-out), .live_violation(state, plan) ->
    [messages] (mid-route check) and .for_bail() (the variant used for bail-out routes).
    territory: Territory (default from the planner). route_fn: planner.api.route (tests inject a fake). executor:
    ThreadExecutor (default) or InlineExecutor. budget_s per candidate, total_budget_s per chain. min_gap_s: shortest
    time between two plan starts (director clock = game s). check_period_s: live constraint check period."""

    def __init__(self, mission, reference, planner=None, *, planner_factory=None, constraints=None, territory=None,
                 route_fn=None, executor=None, budget_s=4.0, total_budget_s=30.0, min_gap_s=10.0, check_period_s=5.0,
                 retry_s=8.0, max_retries=2, bail_reserve_lb=100.0, bail_cells=3, bail_fractions=None,
                 arrive_km=None, clock=time.monotonic):
        self.mission, self.plan = mission, MissionPlan.from_mission(mission)
        self.store = PlanStore(reference)
        self._planner, self._planner_factory, self._planner_lock = planner, planner_factory, threading.Lock()
        self.constraints = tuple(constraints) if constraints is not None else (FuelConstraint(),)
        self._territory = territory
        self.route_fn = route_fn or api.route
        self.executor = executor or ThreadExecutor()
        self.budget_s, self.total_budget_s, self.min_gap_s, self.check_period_s = budget_s, total_budget_s, min_gap_s, check_period_s
        self.retry_s, self.max_retries, self.bail_reserve_lb, self.bail_cells = retry_s, max_retries, bail_reserve_lb, bail_cells
        self.bail_fractions = {**BAIL_FRACTION, **(bail_fractions or {})}
        self.arrive_km = {**ARRIVE_KM, **(arrive_km or {})}
        self.clock = clock
        self.ref_points = [p.to_dict() for p in reference.points]
        self.meta = ActivePlan("reference", "reference", ref=self._reference_estimator(reference))
        self.earning_sources = [self._target_options, self._loiter_options]
        self.log, self.timings = [], []
        self.terminal = False
        self._job, self._job_t0, self._stale = None, 0.0, False
        self._reasons, self._reacting = [], None
        self._last_start = self._last_check = self._retry_at = None
        self._retries = 0

    # ------------------------------------------------------------------------------------- route provider API
    def current(self): return self.store.current()

    @property
    def version(self): return self.store.version

    @property
    def reference(self): return self.store.reference

    @property
    def history(self): return tuple(self.store.records)

    @property
    def planner(self):
        with self._planner_lock:
            if self._planner is None: self._planner = self._planner_factory()
            return self._planner

    @property
    def territory(self):
        if self._territory is None: self._territory = Territory.from_planner(self.planner)
        return self._territory

    @property
    def busy(self): return self._job is not None

    def close(self): self.executor.shutdown()

    def replan(self, state, reason="manual", events=None):
        """Plan now, on the calling thread, and adopt the result (the StaticRoute hook / tests). -> the new Route or
        None (nothing replaced: inconclusive, or an eject: see .terminal)."""
        self._progress(state, None, events)
        out = self._decide(self._snapshot(state, events, reason, self._now(state, None)))
        self._apply(out, state, self._now(state, None))
        return out.route

    def choose_goal(self, state, events=None):
        """The candidate the chain would fly from `state` (None: nothing decided). Runs the planner synchronously and
        changes nothing."""
        return self._decide(self._snapshot(state, events, "choose", self._now(state, None))).candidate

    def deadline_passed(self, state, now):
        """Deadline-style hook (a mission deadline, a fuel-time limit): does nothing yet. True triggers a replan."""
        return False

    # ----------------------------------------------------------------------------------------------- tick
    def on_tick(self, state, decision, ctx=None, events=None):
        """Called by the Runner after the arbiter chose (decision: .behavior, .phase, .events). Detects triggers, starts
        / adopts planning and returns an Order or None. Never blocks on the planner."""
        if self.terminal: return None
        now = self._now(state, ctx)
        if events is None and state.events and len(state.events[0]) == 6: events = state.events
        changed = self._progress(state, decision, events)
        if (o := self._check_eject(state, decision, now)) is not None: return o
        self._triggers(state, decision, now, changed)
        out = []
        if self._job is None and self._reasons and self._can_plan(state, decision, now): self._start(state, events, now)
        if self._job is not None and self._job.done(): out = self._finish(state, now)
        if self.terminal: return self._order
        return Order(events=tuple(out)) if out else None

    # --------------------------------------------------------------------------------------- bookkeeping
    @staticmethod
    def _now(state, ctx):
        return state.t if state.t is not None else ctx.now() if ctx is not None else time.monotonic()

    def _progress(self, state, decision, events):
        """Reached detection: the takeoff (airborne), a target (its "X attack done" line, or the game's objective event
        flag 0x80 / 0x40), the landing (the landed / handover phase, or the arrival circle on the ground).
        -> [kinds that changed]."""
        P, ch = self.plan, []

        def mark(kind):
            if P.mark(kind, WaypointStatus.REACHED): ch.append(kind)
        if state.alt_ft > 100: mark(WaypointKind.TAKEOFF)
        for text in getattr(decision, "events", ()) or ():
            if m := ATTACK_DONE.match(text): mark(WaypointKind(m.group(1)))
        for e in events or ():
            if e[3] in scoring.EV_OBJECTIVE_TYPES:
                if e[4] & scoring.PRIMARY_FLAG: mark(WaypointKind.PRIMARY)
                if e[4] & scoring.SECONDARY_FLAG: mark(WaypointKind.SECONDARY)
        w = P.get(WaypointKind.LANDING)
        if w is not None and w.status is WaypointStatus.PENDING:
            near = math.dist(state.xy, w.pos) / K < self.arrive_km.get(WaypointKind.LANDING, 0.0)
            if getattr(decision, "phase", "") in ("landed", "handover") or (near and state.alt_ft < 300): mark(WaypointKind.LANDING)
        for k in ch: self.log.append(f"waypoint {k.value}: reached")
        return ch

    def _triggers(self, state, decision, now, changed):
        r = self._reasons
        beh = getattr(decision, "behavior", "")
        if beh in REACTIONS: self._reacting = beh
        elif self._reacting:
            r.append(f"deviation ended: {self._reacting}"); self._reacting = None
        if any(k in TARGET_KINDS or k is WaypointKind.LANDING for k in changed) and not self.meta.follows_reference:
            r.append("waypoint reached: " + ", ".join(k.value for k in changed))
        if self.deadline_passed(state, now): r.append("deadline")
        if self._retry_at is not None and now >= self._retry_at:
            r.append("retry: the last round was inconclusive"); self._retry_at = None
        if self.meta.kind in ("earn",) and self._route_end(state): r.append("route end: earning option flown")
        if self._last_check is None or now - self._last_check >= self.check_period_s:
            self._last_check = now
            if bad := self._live_violations(state):
                r.append("constraint: " + "; ".join(bad[:2]))
        if self._job is not None and r: self._stale = True            # inputs of the running plan are out of date

    def _route_end(self, state):
        e = self.meta.end_xy
        return e is not None and math.dist(state.xy, e) < 3.0 * K

    def _can_plan(self, state, decision, now):
        if state.alt_ft <= 100 or getattr(decision, "phase", "route") != "route": return False
        if not (self.plan.pending_targets() or self.plan.landing_pending): return False
        return self._last_start is None or now - self._last_start >= self.min_gap_s

    def _live_violations(self, state):
        """The active route's constraints against the current state, cheaply (no routing)."""
        m, out = self.meta, []
        if m.ref is None: return out
        P = self._planner.P if self._planner is not None else None     # not built yet (lazy): no live fuel check
        for c in m.constraints or self.constraints:
            if isinstance(c, FuelConstraint) and P is not None:
                e = est_mod.estimate(m.ref, api.Pose.from_state(state), P)
                if not c.ok(e, P): out.append(f"fuel at the end {e.fuel_end_lb:.0f} lb below the {c.reserve(P):.0f} lb reserve")
            elif hasattr(c, "live_violation"): out += list(c.live_violation(state, m))
        return out

    # ------------------------------------------------------------------------------------- start / finish
    def _snapshot(self, state, events, reason, now):
        return dict(state=state, events=None if events is None else tuple(events), plan=self.plan.copy(), reason=reason,
                    now=now, retries=self._retries)

    def _start(self, state, events, now):
        reason = "; ".join(dict.fromkeys(self._reasons)); self._reasons = []
        snap = self._snapshot(state, events, reason, now)
        self._last_start, self._stale, self._job_t0 = now, False, self.clock()
        self.log.append(f"{now:.0f} plan started: {reason}")
        self._job = self.executor.submit(lambda: self._decide(snap))
        self._job.reason, self._job.snap_t = reason, now

    def _finish(self, state, now):
        job, self._job = self._job, None
        try: out = job.result()
        except Exception as e:                           # noqa: BLE001 - a planner bug must not stop the flight
            out = Outcome("keep", reason=job.reason, error=f"{type(e).__name__}: {e}")
        self.timings.append(out.elapsed_s)
        if self._stale:
            self.log.append(f"{now:.0f} plan discarded: new trigger while planning ({'; '.join(self._reasons)})")
            return [f"plan discarded (stale): {out.candidate.name if out.candidate else out.kind}"]
        return self._apply(out, state, now)

    def _apply(self, out, state, now):
        """Adopt an Outcome on the main thread. -> mission-log lines (and for an eject: sets .terminal and
        self.order)."""
        lines = []
        if out.kind == "keep":
            self._retries += 1; self._retry_at = now + self.retry_s
            lines.append(f"plan kept: {out.reason or out.error or 'inconclusive'}"
                         + (f" ({out.error})" if out.error else "") + f"; retry {self._retries} in {self.retry_s:.0f} s")
            self.log += [f"{now:.0f} {l}" for l in lines]
            return lines
        self._retries, self._retry_at = 0, None
        for k in out.skipped:
            if self.plan.mark(k, WaypointStatus.SKIPPED): self.log.append(f"waypoint {k.value}: skipped")
        cand = out.candidate
        att = tuple(a.astuple() for a in out.attempts)
        if out.kind == "eject":
            self.store.replace(self.store.active, out.reason, t=now, candidate=cand.name, kind="eject", plan_s=out.elapsed_s,
                               skipped=out.skipped, attempts=att)
            self.terminal = True
            lines.append(f"EJECT ({cand.side}): {out.note or out.reason}")
        else:
            end_xy = out.route.points[-1].xy
            route = self.store.replace(out.route, out.reason, t=now, candidate=cand.name, kind=cand.kind,
                                       fuel_end_lb=out.result.fuel_end_lb, plan_s=out.elapsed_s, skipped=out.skipped,
                                       attempts=att)
            out.route = route
            self.meta = ActivePlan(cand.kind, cand.name, cand.side, cand.follows_reference, out.live_ref, out.constraints, end_xy)
            lines.append(f"plan v{route.version}: {cand.name} ({out.reason}); {len(route.points)} points, fuel at the end "
                         f"{out.result.fuel_end_lb:.0f} lb, {out.elapsed_s:.1f} s" + (f"; {out.note}" if out.note else "")
                         + (f"; gave up {', '.join(k.value for k in out.skipped)}" if out.skipped else ""))
        self.log += [f"{now:.0f} {l}" for l in lines]
        if out.kind == "eject": self._order = self._eject_order(out.note or out.reason, cand.side)
        return lines

    # ---------------------------------------------------------------------------------------------- eject
    @staticmethod
    def _eject_order(why, side):
        return Order(Command(actions=(Action.EJECT,), banner=f"  DIRECTOR: BAILING OUT ({side} territory): {why}", done=True),
                     exit=f"director: ejected in {side} territory ({why})", events=(f"EJECT in {side} territory: {why}",))

    def _check_eject(self, state, decision, now):
        """A bail-out route ejects as soon as the plane is in a cell of the wanted type (an eject decided in the worker
        is delivered by on_tick through self._order)."""
        if self.meta.kind == "bail" and self.meta.side and (self._territory is not None or self._planner is not None) \
                and self.territory.side(*state.xy) == self.meta.side:
            self.terminal = True
            why = f"reached {self.meta.side} territory on the bail-out route"
            self.store.replace(self.store.active, why, t=now, candidate=self.meta.candidate, kind="eject")
            self.log.append(f"{now:.0f} EJECT ({self.meta.side}): {why}")
            return self._eject_order(why, self.meta.side)
        return None

    # ===================================================================================== the decision
    def _decide(self, snap):
        """The whole chain on a snapshot (worker thread). Never raises: a bug is an Outcome("keep", error=...)."""
        t0 = self.clock()
        try: out = self._chain(snap)
        except Exception as e:                           # noqa: BLE001
            out = Outcome("keep", reason=snap["reason"], error=f"{type(e).__name__}: {e}")
        out.elapsed_s = self.clock() - t0
        return out

    def _profile(self): return self.planner.P

    def _chain(self, snap):
        state, plan, reason = snap["state"], snap["plan"], snap["reason"]
        pose = api.Pose.from_state(state)
        # 1. the mission chain
        cands = self.mission_candidates(plan, pose)
        att = []
        if cands:
            sel = first_feasible(state, cands, self.constraints, self._estimate(pose), self._plan_fn(pose), profile=self._profile(),
                                 planner=self.planner, budget_s=self.budget_s, total_budget_s=self.total_budget_s, clock=self.clock)
            att += sel.attempts
            if sel.candidate is not None:
                flown = set(sel.candidate.waypoints)
                skipped = tuple(k for k in plan.pending_kinds() if k not in flown and k is not WaypointKind.TAKEOFF)
                return self._route_outcome(sel, reason, att, pose, skipped, self.constraints)
            if sel.inconclusive and snap["retries"] < self.max_retries:
                return Outcome("keep", reason="mission chain inconclusive (planner time-out / no path); keeping the route",
                               attempts=att)
        # 2. no landing feasible
        return self._no_landing(snap, pose, att)

    # --- mission candidates
    def mission_candidates(self, plan, pose):
        """full remaining mission -> primary>landing -> secondary>landing -> landing (each only where it differs)."""
        L = WaypointKind.LANDING
        targets = tuple(k for k in plan.pending_kinds() if k in TARGET_KINDS)
        land = (L,) if plan.landing_pending else ()
        seqs = [targets + land]
        for k in targets: seqs.append((k,) + land)
        if land: seqs.append(land)
        out, seen = [], set()
        for i, seq in enumerate(seqs):
            if not seq or seq in seen: continue
            seen.add(seq)
            out.append(self._mission_candidate(seq, pose, "full" if i == 0 else None))
        return out

    def _ref_roles_ahead(self, xy):
        """Target roles still ahead on the reference from the plane's projection on it, in order."""
        i, _, _ = est_mod.nearest_leg(self.ref_points, xy)
        roles = []
        for p in self.ref_points[i:]:
            if p.get("role") and p["role"] not in roles: roles.append(p["role"])
        return roles

    def _mission_candidate(self, seq, pose, name=None):
        w = self.plan.get  # waypoint positions (the plan's, not the snapshot's: positions never change)
        L = WaypointKind.LANDING
        tgt = [k for k in seq if k in TARGET_KINDS]
        lands = L in seq
        goals = tuple(api.Goal.target(k.value) for k in tgt) + ((api.Goal.landing(),) if lands else ())
        ref_ends = self.ref_points[-1].get("kind") == "landing"
        splice = lands and ref_ends and [k.value for k in tgt] == self._ref_roles_ahead(pose.xy)
        sets, idx = [goals], None
        if splice:
            sets.insert(0, (api.Goal.rejoin(self.ref_points),))
            idx = api.rejoin_candidates(self.ref_points, pose.xy)[0]
        xy = tuple(w(k).pos for k in seq if w(k) is not None)
        return Candidate(name or ">".join(k.value for k in seq), "mission", tuple(seq), tuple(sets), must_land=lands,
                         chain_xy=xy, rejoin_index=idx, follows_reference=splice)

    # --- estimates and routing
    def _estimate(self, pose):
        ref = est_mod.Reference({"points": self.ref_points, "profile": self.store.reference.profile,
                                 "summary": self.store.reference.summary})

        def estimate(state, cand):
            P = self._profile()
            # rule-out only on the straight chain: the rejoin estimate prices the pre-flight reference (slow, low, radar-
            # avoiding), which the plain goal set beats by thousands of lb (2026-10-27, 12.7k vs 9.4k lb burned)
            return est_mod.estimate_direct(pose, cand.chain_xy, P) if cand.chain_xy else None
        return estimate

    def _plan_fn(self, pose, constraints=None):
        """plan(state, cand, budget_s) for first_feasible: the goal sets in order on the real router. A PlanError on one
        goal set tries the next; BudgetExceeded ends the candidate."""
        def plan(state, cand, budget_s):
            t_end, err = self.clock() + budget_s, None
            for goals in cand.goal_sets:
                left = t_end - self.clock()
                if left <= 0: raise BudgetExceeded("per-candidate budget spent")
                try:
                    res = self.route_fn(pose, goals if len(goals) > 1 else goals[0], (), left, planner=self.planner)
                    res.spliced = goals[0].kind == "rejoin"          # which goal set worked (the active plan's meta)
                    if res.spliced and goals is not cand.goal_sets[-1] and any(c.check(res, self.planner) for c in self.constraints):
                        continue                                     # the reference splice breaks a constraint: try the plain goals
                    return res
                except BudgetExceeded: raise
                except PlanError as e: err = e
            raise err or PlanError("no goal set")
        return plan

    def _route_outcome(self, sel, reason, att, pose, skipped, constraints, note=""):
        res, cand = sel.result, sel.candidate
        cand = dataclasses.replace(cand, follows_reference=bool(getattr(res, "spliced", False)))
        pts = res.points
        route = Route.from_route_json({"points": pts, "profile": dict(self.store.reference.profile),
                                       "summary": {"fuel_landing_lb": res.fuel_end_lb}})
        tanks = pose.tanks if pose.tanks is not None else self.planner.P.get("extra_fuel_bays", 0)
        try:
            ref = est_mod.Reference({"points": pts, "profile": {"extra_fuel_bays": tanks},
                                     "summary": {"fuel_landing_lb": res.fuel_end_lb}})
        except Exception: ref = None                     # noqa: BLE001 - no live check for this route
        return Outcome("route", cand, res, route, reason, att, skipped=skipped, live_ref=ref, constraints=tuple(constraints),
                       note=note)

    # --- no landing: score, earning options, bail-out
    def bail_constraints(self):
        out = []
        for c in self.constraints:
            if hasattr(c, "for_bail"): out.append(c.for_bail())
            elif isinstance(c, FuelConstraint): out.append(FuelConstraint(self.bail_reserve_lb))
            else: out.append(c)
        return tuple(out)

    def bail_candidates(self, side, pose):
        """The cells of `side` nearest first as route candidates (at most bail_cells); in a cell of that type already, or
        for ENEMY (always feasible), an eject in place."""
        here = self.territory.side(*pose.xy)
        if here == side or side == ENEMY:
            return [Candidate(f"bail:{side}", "bail", side=here if side == ENEMY else side, eject_in_place=True)]
        out = []
        for d, p in self.territory.ranked_points(side, pose.xy)[:self.bail_cells]:
            out.append(Candidate(f"bail:{side}@{int(p[0]) >> 11},{int(p[1]) >> 11}", "bail", goal_sets=((api.Goal.point(*p, f"Bail-out ({side})"),),),
                                 chain_xy=(p,), side=side))
        return out

    def _bail_search(self, side, state, pose, bc, att):
        sel = first_feasible(state, self.bail_candidates(side, pose), bc, self._estimate(pose), self._plan_fn(pose),
                             profile=self._profile(), planner=self.planner, budget_s=self.budget_s,
                             total_budget_s=self.total_budget_s, clock=self.clock)
        att += sel.attempts
        return sel

    def score_now(self, state, events):
        """The score so far (mean of the estimate's range; 0 when the event list is unavailable) and the pieces."""
        if events is None: return 0.0, None
        pl = self.planner
        cs = scoring.current_score(list(events), pl.W.tgrid, pl.th, pl.spec.get("settings") or {}, state.opp)
        if "score" not in cs: return 0.0, cs
        return sum(cs["score"]) / 2, cs

    def _score_if(self, state, cs, primary=None, secondary=None, extra_samples=0):
        s = dict(self.planner.spec.get("settings") or {})
        if state.opp is not None: s["opponent"] = scoring.OPPONENT_NAMES[state.opp]
        (_, _), (s0, s1), _ = scoring.estimate([cs["enemy"] + extra_samples], self.planner.th, s,
                                               cs["primary"] if primary is None else primary,
                                               cs["secondary"] if secondary is None else secondary,
                                               id_penalty=scoring.id_penalty(cs["radar_ids"], cs["visual_ids"]))
        return (s0 + s1) / 2

    def _no_landing(self, snap, pose, att):
        state, plan, reason = snap["state"], snap["plan"], snap["reason"]
        if snap["events"] is None and snap["retries"] < self.max_retries:
            # the event list has not been read yet (the Runner reads it every second): the score is unknown, and a score
            # above zero changes what is worth doing: wait a round rather than eject on a guess
            return Outcome("keep", reason="no landing feasible, but the score is unknown (event list not read yet)", attempts=att)
        bc = self.bail_constraints()
        score, cs = self.score_now(state, snap["events"])
        found = {}
        # first the chain's first feasible; with a score to protect, every type (the best penalty reachable now)
        for side in BAIL_ORDER:
            sel = self._bail_search(side, state, pose, bc, att)
            if sel.candidate is not None:
                found[side] = sel
                if score <= 0: break
        if not found: raise PlanError("no bail-out possible")      # unreachable: ENEMY is an eject in place
        first = found[next(s for s in BAIL_ORDER if s in found)]
        skipped_all = tuple(k for k in plan.pending_kinds() if k is not WaypointKind.TAKEOFF)
        low = min(self.bail_fractions.values())
        best_frac = max(self.bail_fractions.get(f.candidate.side, low) for f in found.values())
        note = f"score {score:.0f}"
        if score * best_frac > 0 and cs is not None:
            opts = [o for src in self.earning_sources for o in src(state, plan, pose, cs, score, best_frac)]
            opts = sorted((o for o in opts if o.candidate.net > 0), key=lambda o: -o.candidate.net)
            if opts:
                sel = first_feasible(state, [o.candidate for o in opts], bc, self._estimate(pose), self._plan_fn(pose),
                                     profile=self._profile(), planner=self.planner, budget_s=self.budget_s,
                                     total_budget_s=self.total_budget_s, clock=self.clock)
                att += sel.attempts
                if sel.candidate is not None:
                    return self._route_outcome(sel, reason, att, pose, (), bc,
                                               f"{note}, earning +{sel.candidate.points:.0f} (net {sel.candidate.net:.0f})")
            note += ", no earning option reachable" if opts else ", no earning option with a positive net"
        else:
            note += ", nothing to protect: bail out" if score <= 0 else ""
        sel = first
        cand = sel.candidate
        if cand.eject_in_place:
            return Outcome("eject", cand, None, None, reason, att, skipped=skipped_all, note=note)
        return self._route_outcome(sel, reason, att, pose, skipped_all, bc, note + f", bail out to {cand.side} territory")

    # --- earning options
    def _target_options(self, state, plan, pose, cs, score, best_frac):
        """A pending PRIMARY / SECONDARY target as a stand-alone goal (no landing)."""
        out = []
        for w in plan.pending_targets():
            role = w.kind.value
            s1 = self._score_if(state, cs, **{role: True})
            pts = s1 - score
            end_side = self.territory.side(*w.pos)
            frac = self.bail_fractions.get(end_side, min(self.bail_fractions.values()))
            net = pts - (s1 * (1 - frac) - score * (1 - best_frac))
            c = Candidate(f"earn:{role}", "earn", (w.kind,), ((api.Goal.target(role),),), chain_xy=(w.pos,), side=end_side,
                          points=pts, net=net)
            out.append(EarningOption(c, pts, end_side))
        return out

    def _loiter_options(self, state, plan, pose, cs, score, best_frac):
        """TODO: score loiters over radar-quiet enemy ground (planner.loiter.candidates: a racetrack per quiet enemy cell;
        points = per_sample x samples flown, end side = enemy). Not wired: planner.api.route has no loiter goal and
        adding one means changing the planner. Interface: return EarningOption(Candidate(kind="earn", goal_sets=...,
        points=..., net=points - (penalty at the end - best penalty now)), points, end_side)."""
        return []

    # --- the reference as the first active route
    def _reference_estimator(self, route):
        try:
            return est_mod.Reference({"points": [p.to_dict() for p in route.points], "profile": route.profile,
                                      "summary": route.summary})
        except Exception: return None                    # noqa: BLE001 - a reference without fuel figures: no live check
