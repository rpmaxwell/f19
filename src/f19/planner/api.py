"""The planner's entry points, all in memory (no files: cli.py reads plan dirs and writes route.json / report / png).

  plan_mission(spec, mission) -> PlanResult       the whole pre-flight plan: what `python analysis/planner <dir>` does
                                                  (every option: target order, approach side, attack bearings, loiters,
                                                  probes; the fuel-limited re-plan), 3-8 min
  context(spec, mission) -> Planner               built once per flight (World, radar model, Router: ~10-20 s)
  route(state, goal, constraints, budget_s, planner=ctx) -> RouteResult
                                                  the fast in-flight path (refactor plan 4.2): from the current pose
                                                  (position, heading, fuel, clock, tanks) to one goal or a chain of
                                                  goals, ONE option (no order / side / bearing / loiter / probe product),
                                                  a bounded search box and a time budget; fuel re-integrated from the
                                                  current state. Goal.rejoin splices the reference route back in.

Goals: Goal.point(x, y), Goal.target(role, mode, bearing), Goal.landing(base, side), Goal.rejoin(points, index).
"""
import dataclasses
import math
import time
from dataclasses import dataclass, field

import numpy as np

from f19.core.types import GameState

from . import report as _report
from .estimate import FuelConstraint, nearest_leg
from .planner import FUEL_FALLBACK, Planner, fixed, game_takeoff_heading, mission_dict
from .router import BudgetExceeded, PlanError, from_hdg, hdg, unit
from .world import K

EXTRA_FUEL_WEAPON = 17          # loadout weapon index of an Extra Fuel tank (scripts/nav_tui.py)


# ------------------------------------------------------------------------------------------------ plan_mission
def resolve_takeoff_heading(spec, mission):
    """spec's takeoff_heading, or what the game will use (theater + carrier), as cli main() did; may be "both"."""
    th = spec.get("takeoff_heading")
    if th is not None: return th
    m = mission_dict(mission) or {}
    theater = spec.get("theater") or m.get("theater")
    carrier = isinstance(spec.get("takeoff"), dict) and spec["takeoff"].get("carrier", True)   # as World.place
    return round(game_takeoff_heading(theater, carrier), 1)


@dataclass
class PlanResult:
    spec: dict                   # the spec as planned (takeoff_heading resolved)
    planner: Planner             # the Planner whose plan was chosen (a fuel-limited re-plan has its own)
    best: dict                   # Planner.plan() result of the chosen option (+ "tag")
    results: list                # every option planned
    summary: dict
    sim: dict                    # alertsim.simulate
    samples: list                # cold-war sample points (xy, enemy) for the picture
    report: str                  # report.txt
    route: dict                  # route.json (schema 2)

    @property
    def valid(self): return not self.best["errors"]

    def image(self):
        """route.png (PIL image)."""
        return _report.draw(self.planner, self.best, self.samples)


def plan_mission(spec, mission, label="", progress=None):
    """Plan one takeoff direction of a mission. spec: plan.json dict (takeoff_heading None = the game's; not "both");
    mission: mission_mem.json dict or core Mission; label: report header (the plan dir); progress(msg): status lines."""
    say = progress or (lambda msg: None)
    th = resolve_takeoff_heading(spec, mission)
    if th == "both": raise ValueError('plan_mission plans one takeoff direction: pass takeoff_heading 0 or 180, not "both"')
    if spec.get("takeoff_heading") is None: spec = {**spec, "takeoff_heading": th}
    pl = Planner(spec, mission, label)
    best, results = pl.run()
    head = []
    if best["errors"] and all(e.startswith("fuel at landing") for e in best["errors"]):
        # FUEL-LIMITED (2026-10-21 Lukow at Elite: every stealth route ran dry): re-plan with cheaper radar prices and no
        # loiter until a route fits, and say what that trades away. Stops at the first that fits.
        s0, sim0 = _report.summarise(pl, best)[:2]
        P0 = pl.P; tried = []; options = [(s0["fuel_landing_lb"], pl, best, results)]
        for f in FUEL_FALLBACK:
            pc = round(P0["pulse_cost_samples"] * f, 2)
            spec2 = {**spec, "overrides": {**(spec.get("overrides") or {}), "pulse_cost_samples": pc, "loiter": "off"}}
            say(f"fuel-limited: re-planning with the radar price at {pc:g} samples per seen pulse, no loiter")
            pl2 = Planner(spec2, mission, label); b2, r2 = pl2.run(); s2 = _report.summarise(pl2, b2)[0]
            tried.append(f"  radar price {pc:g} (was {P0['pulse_cost_samples']:g}): {s2['km']:.0f} km, fuel at landing {s2['fuel_landing_lb']} lb, "
                         f"launch possible {s2['alert']['expected']['p_any_launch']:.0%}, radar IDs {s2['alert']['expected']['expected_ids']:.2f}"
                         + ("" if b2["errors"] else "  <- this plan"))
            if not b2["errors"]: pl, best, results = pl2, b2, r2; break
            options.append((s2["fuel_landing_lb"], pl2, b2, r2))
        else:                                       # nothing fits: show the one closest to fitting
            _, pl, best, results = max(options, key=lambda o: o[0])
        e0 = s0["alert"]["expected"]
        head = ["!! FUEL-LIMITED: the " + P0["name"] + f" route needs more fuel than carried ({s0['km']:.0f} km, {s0['fuel_landing_lb']} lb at landing "
                f"against a {P0['landing_reserve_lb']:.0f} lb reserve; launch possible {e0['p_any_launch']:.0%}, radar IDs {e0['expected_ids']:.2f})."]
        head += tried
        head.append("!! " + ("using the first cheaper-radar plan that fits: more radar risk for less distance."
                             if not best["errors"] else
                             "nothing fits. Options: more Extra Fuel, a lower landing_reserve_lb (your call; a crash isn't scored), "
                             "or a different mission."))
        head.append("")
    summ, sim, samples = _report.summarise(pl, best)
    rep = "\n".join(head) + _report.report(pl, best, results, summ, sim)
    return PlanResult(spec, pl, best, results, summ, sim, samples, rep, _report.route_json(pl, best, spec, summ))


# ------------------------------------------------------------------------------------------------ in flight
def context(spec, mission, label=""):
    """The planning context for in-flight routing: a Planner without the loiter search (World, radar model, Router
    with its cost grids), built once and passed to route() as planner=."""
    return Planner(spec, mission, label, loiters=False)


@dataclass(frozen=True)
class Pose:
    """Where the plane is now: position (units), heading (deg), fuel (internal lb; None = full), game clock (s) and
    Extra Fuel tanks not yet transferred (None = the profile's extra_fuel_bays)."""
    x: float
    y: float
    heading: float
    alt_ft: float = 0.0
    fuel_lb: float | None = None
    t: float = 0.0
    tanks: int | None = None

    @property
    def xy(self): return self.x, self.y

    @classmethod
    def from_state(cls, state, tanks=None):
        """From a core GameState; tanks default: Extra Fuel tanks in the loadout (weapon 17, as the HUD counts them)."""
        if tanks is None and state.systems.loadout:
            tanks = sum(n for w, n in state.systems.loadout if w == EXTRA_FUEL_WEAPON)
        return cls(state.pos.x, state.pos.y, state.att.heading, state.pos.alt_ft, state.systems.fuel_lb, state.t or 0.0,
                   tanks)


def as_pose(state):
    if isinstance(state, Pose): return state
    if isinstance(state, GameState): return Pose.from_state(state)
    if isinstance(state, dict): return Pose(**state)
    raise TypeError(f"route(): state must be a Pose, GameState or dict, not {type(state).__name__}")


# Computed per point by route_points (route.json); a rejoined reference point keeps only what the planner gave it.
_COMPUTED = ("throttle", "flaps", "speed_kt", "fuel_lb", "t_game_s", "alt_ft")


@dataclass(frozen=True)
class Goal:
    kind: str                    # point | target | landing | rejoin
    x: float = 0.0
    y: float = 0.0
    label: str = ""
    role: str = ""               # target
    mode: str | None = None      # target: overfly / photo / maverick (None: the briefing task's, Planner.attack_mode)
    bearing: float | None = None # target attack bearing (deg); None: straight in from where the route comes from
    base: object = None          # landing: airbase name / {"x","y"} (None: the spec's landing)
    side: str | None = None      # landing: "N" / "S" (None: the side whose intercept is nearer)
    points: tuple = ()           # rejoin: the reference route.json points
    index: int | None = None     # rejoin: first reference point flown again (None: the first >= join_km ahead)
    join_km: float = 25.0
    window: tuple | None = None  # rejoin: (lo, hi) reference legs the plane may be projected on (None: all)

    @classmethod
    def point(cls, x, y, label=""): return cls("point", float(x), float(y), label)

    @classmethod
    def target(cls, role, mode=None, bearing=None): return cls("target", role=role, mode=mode, bearing=bearing)

    @classmethod
    def landing(cls, base=None, side=None): return cls("landing", base=base, side=side)

    @classmethod
    def rejoin(cls, points, index=None, join_km=25.0, window=None):
        return cls("rejoin", points=tuple(points), index=index, join_km=join_km, window=window)


@dataclass
class RouteResult:
    pose: Pose
    goals: tuple
    pts: list                    # flown points [(xy, label, attrs)]
    points: list                 # route.json-style point dicts (fuel_lb / t_game_s from the current state)
    fuel: dict                   # fuelplan.integrate result
    errors: list                 # validation (turns, terrain, targets, Maverick geometry)
    violations: list             # constraint failures
    log: list
    cost: float
    elapsed_s: float
    plan: dict = field(repr=False, default_factory=dict)

    @property
    def feasible(self): return not self.errors and not self.violations

    @property
    def lands(self): return bool(self.pts) and (self.pts[-1][2] or {}).get("kind") == "landing"

    @property
    def fuel_end_lb(self):
        """Fuel at the end of the route, unused tanks included (= fuel at landing when it lands)."""
        return self.fuel["fuel_land"]


def rejoin_index(points, xy, join_km=25.0, window=None):
    """First reference point at least join_km (along the route) beyond the projection of xy on its nearest leg."""
    i, f, _ = nearest_leg(points, xy, window)
    a, b = points[i - 1], points[i]
    along = (1 - f) * math.dist((a["x"], a["y"]), (b["x"], b["y"])) / K
    k = i
    while k < len(points) - 2 and along < join_km:
        along += math.dist((points[k]["x"], points[k]["y"]), (points[k + 1]["x"], points[k + 1]["y"])) / K
        k += 1
    return k


def _rejoin_piece(goal, pose, k):
    pts = list(goal.points)
    if not 0 < k <= len(pts) - 2: raise PlanError(f"rejoin: point {k} leaves no reference leg to join")
    tail = []
    for p in pts[k:]:
        at = {a: v for a, v in p.items() if a not in ("x", "y", "label")}
        if at.get("kind") != "probe":                # a probe's own alt / speed are the planner's input: kept
            for c in _COMPUTED: at.pop(c, None)
        tail.append(((float(p["x"]), float(p["y"])), p.get("label", ""), at))
    piece = fixed(tail)
    piece.update(rejoin=k, overfly_labels=[l for _, l, a in tail if a.get("kind") == "overfly" and l])
    return piece


def _target_element(pl, goal, prev_xy):
    t = next((t for t in pl.M["targets"] if t["role"].rstrip("?") == goal.role), None)
    if t is None: raise PlanError(f"no {goal.role} target in the mission")
    el = pl.targets((goal.role.rstrip("?"),))[0]
    mode = goal.mode or pl.attack_mode(t)[0]
    if mode == "overfly": return el
    b0 = goal.bearing if goal.bearing is not None else hdg(unit(np.subtract(el["xy"], prev_xy)))
    for d in (0, 30, -30, 60, -60, 90, -90, 120, -120, 150, -150, 180):       # first bearing whose piece is clear
        piece = pl.attack_piece(el, t, mode, float((b0 + d) % 360))
        if piece is not None: return piece
    raise PlanError(f"{el['label']}: no clear {mode} run-in near bearing {b0:.0f}")


def _landing_piece(pl, goal, prev_xy):
    if goal.side: return pl.landing(goal.side, goal.base)
    pieces = [pl.landing(s, goal.base) for s in ("N", "S")]
    return min(pieces, key=lambda pc: math.dist(pc["pts"][0][0], prev_xy))


def _end_xy(el):
    return el["pts"][-1][0] if el["t"] == "fixed" else el["xy"]


REJOIN_FACTORS = (1, 2, 3, 4, 6)    # rejoin points tried at join_km x these along the reference
LOOP_DEG = 270.0                # a join that turns this much in total is a loop: a farther rejoin point is tried
ATTACK_KINDS = ("runin", "photo", "launch", "overfly")


def rejoin_candidates(points, xy, join_km=25.0, tries=5, window=None):
    """Reference point indices to rejoin at: join_km x REJOIN_FACTORS along the reference from xy's projection, never
    past its next attack point (a rejoin beyond it would skip the target: the candidates stop at that run-in)."""
    i, _, _ = nearest_leg(points, xy, window)
    stop = next((k for k in range(i, len(points) - 1) if points[k].get("kind") in ATTACK_KINDS), len(points) - 2)
    out = []
    for f in REJOIN_FACTORS[:tries]:
        k = min(rejoin_index(points, xy, join_km * f, window), stop)
        if k not in out: out.append(k)
        if k == stop: break
    return out


def _join_turn(pts, tail):
    """Total |turn| (deg) of the flown points before the rejoined reference tail."""
    first = np.asarray(tail["pts"][0][0], float)
    n = next((i for i, p in enumerate(pts) if np.linalg.norm(np.asarray(p[0], float) - first) < 1.0), len(pts))
    xy = [np.asarray(p[0], float) for p in pts[:n + 2]]
    tot = 0.0
    for a, b, c in zip(xy, xy[1:], xy[2:]):
        if np.linalg.norm(b - a) > 1e-6 and np.linalg.norm(c - b) > 1e-6:
            tot += abs((hdg(c - b) - hdg(b - a) + 180) % 360 - 180)
    return tot


def _goal_xy(pl, g, prev):
    """Where a goal is (None: unknown), for the variants below."""
    if g.kind == "point": return (g.x, g.y)
    if g.kind == "target":
        t = next((t for t in pl.M["targets"] if t["role"].rstrip("?") == g.role), None)
        return None if t is None else tuple(pl.targets((g.role.rstrip("?"),))[0]["xy"])
    if g.kind == "landing":
        try: return tuple(_landing_piece(pl, g, prev)["pts"][0][0])
        except Exception: return None                      # noqa: BLE001
    return None


def _variants(pl, pose, goals):
    """(pose, goals) the router tries in order after the plain one fails with a PlanError (2026-10-27, Liberec: the first
    attack bearing straight in from where the plane is often needs a U-turn into a mountain on the way on, and a plane
    heading away from its goal cannot always turn inside the walls). 1. the attack bearing that is the half-turn between
    the way in and the way on (both senses), per target without a bearing; 2. the pose's heading turned toward the first
    goal (the autopilot turns onto the route anyway)."""
    prev, xy = pose.xy, []
    for g in goals:
        xy.append(_goal_xy(pl, g, prev))
        if xy[-1] is not None: prev = xy[-1]
    for i, g in enumerate(goals):
        if g.kind != "target" or g.bearing is not None or xy[i] is None: continue
        a = xy[i - 1] if i and xy[i - 1] is not None else pose.xy
        nxt = next((q for q in xy[i + 1:] if q is not None), None)
        if nxt is None: continue
        h_in, h_out = hdg(unit(np.subtract(xy[i], a))), hdg(unit(np.subtract(nxt, xy[i])))
        mid = (h_in + ((h_out - h_in + 180) % 360 - 180) / 2) % 360
        mids = [(mid, True), ((mid + 180) % 360, True)]
        for b, _ in mids:
            yield pose, tuple(dataclasses.replace(x, bearing=float(b)) if j == i else x for j, x in enumerate(goals))
        for b, _ in mids:                                  # and the plane already turned onto that bearing (as the autopilot will)
            if abs((b - pose.heading + 180) % 360 - 180) > 20:
                yield dataclasses.replace(pose, heading=float(b)), tuple(
                    dataclasses.replace(x, bearing=float(b)) if j == i else x for j, x in enumerate(goals))
    first = next((q for q in xy if q is not None), None)
    if first is not None:
        h0 = hdg(unit(np.subtract(first, pose.xy)))
        for off in (0.0, 40.0, -40.0):
            h = (h0 + off) % 360
            if abs((h - pose.heading + 180) % 360 - 180) > 20: yield dataclasses.replace(pose, heading=h), goals


def route(state, goal, constraints=(), budget_s=10.0, *, planner, margins_km=(40.0, 120.0), lead_km=2.0, land=False,
          rejoin_tries=5):
    """Route from the current pose to `goal` (a Goal or a sequence of Goals, flown in order).

    state: Pose, core GameState or a Pose dict. constraints: e.g. (FuelConstraint(),), each .check(result, planner) ->
    [violation]. budget_s: time budget for everything (BudgetExceeded, a PlanError, when spent). planner: context().
    margins_km: search boxes around each leg's ends, tried in order while the search finds no path (the mission planner
    uses 120 km; a small box fails fast when the goal needs a loop to arrive on its heading). lead_km: the start piece
    flies the current heading this far before the first turn. land: append Goal.landing() unless the chain already
    ends on a runway (a landing goal, or a rejoin whose reference ends on one). rejoin_tries: a rejoin without an index
    tries up to this many reference points (rejoin_candidates), the first whose join does not loop.

    Bounded: no option product (one target order, one approach side, one attack bearing, no loiter, no probe). The
    start piece is the current pose (no takeoff): its first leg is checked for walls; the last leg is exempt only when
    the route ends on a landing runway. Fuel and time integrate from the pose's fuel, clock and tanks."""
    t0 = time.monotonic(); deadline = t0 + budget_s
    pl = planner; pose = as_pose(state)
    goals = (goal,) if isinstance(goal, Goal) else tuple(goal)
    if not goals: raise ValueError("route(): no goal")
    if land and goals[-1].kind not in ("landing", "rejoin"): goals += (Goal.landing(),)
    if any(g.kind == "rejoin" for g in goals[:-1]): raise ValueError("route(): a rejoin can only be the last goal")
    try:
        return _route_once(pose, goals, constraints, deadline, t0, pl, margins_km, lead_km, rejoin_tries)
    except PlanError as first:
        if any(g.kind == "rejoin" for g in goals): raise
        for pose_v, goals_v in _variants(pl, pose, goals):
            if time.monotonic() > deadline: break
            try: return _route_once(pose_v, goals_v, constraints, deadline, t0, pl, margins_km, lead_km, rejoin_tries, goals)
            except PlanError: continue
        raise first


def _route_once(pose, goals, constraints, deadline, t0, pl, margins_km, lead_km, rejoin_tries, asked=None):
    if any(g.kind == "rejoin" for g in goals[:-1]): raise ValueError("route(): a rejoin can only be the last goal")
    u = from_hdg(pose.heading)
    p0 = np.array(pose.xy, float); p1 = p0 + u * lead_km * K
    if not all(64 <= c <= pl.W.size - 64 for c in p1): raise PlanError("the pose's lead point is off the map")
    start = fixed([(p0, "Present position", {"kind": "pose"}), (p1, "", {"kind": "pose"})])
    seq, roles = [start], set()
    for g in goals:
        prev = _end_xy(seq[-1])
        if g.kind == "point": el = dict(t="target", xy=(g.x, g.y), role="", label=g.label or "Goal", attrs={"kind": "overfly"})
        elif g.kind == "target":
            el = _target_element(pl, g, prev)
            if el.get("attack") != "maverick": roles.add(g.role.rstrip("?"))
        elif g.kind == "landing": el = _landing_piece(pl, g, prev)
        elif g.kind == "rejoin": el = None                   # below: one per rejoin point tried
        else: raise ValueError(f"unknown goal kind {g.kind!r}")
        seq.append(el)
    last = goals[-1]
    if last.kind == "rejoin":
        ks = [int(last.index)] if last.index is not None else rejoin_candidates(last.points, pose.xy, last.join_km, rejoin_tries, last.window)
        tails = [_rejoin_piece(last, pose, k) for k in ks]
    else:
        tails = [seq[-1]]
    r = err = None; looped = []
    for tail in tails:
        s_ = seq[:-1] + [tail]
        rl = roles | {a["role"] for _, l, a in tail["pts"] if a.get("kind") == "overfly" and a.get("role")} \
            if "rejoin" in tail else roles
        lands = tail["t"] == "fixed" and (tail["pts"][-1][2] or {}).get("kind") == "landing"
        for m in margins_km:
            try:
                r = pl.plan(s_, start_fuel=pose.fuel_lb, start_t=pose.t, tanks_left=pose.tanks, ends=(False, lands),
                            margin_km=m, deadline=deadline, roles=rl)
                break
            except BudgetExceeded:
                if not looped: raise
                break                                # out of time: the best looped join found so far
            except PlanError as e:
                err = e
        if r is None and looped and time.monotonic() > deadline: break
        if r is not None and "rejoin" in tail and len(tails) > 1 and _join_turn(r["pts"], tail) > LOOP_DEG:
            looped.append(r); r = None                # joined with a loop: try a farther rejoin point
            continue
        if r is not None: break
    if r is None and looped: r = max(looped, key=lambda x: x["fuel"]["fuel_land"])
    if r is None: raise err
    # plan() checks the landing reserve on every plan; here the constraints decide (a route may end in the air)
    errors = [e for e in r["errors"] if not e.startswith("fuel at landing")]
    best = {**r, "tag": "in-flight"}
    res = RouteResult(pose, asked or goals, r["pts"], _report.route_points(pl, best), r["fuel"], errors, [], r["log"], r["cost"],
                      0.0, r)
    for c in constraints: res.violations += list(c.check(res, pl))
    res.elapsed_s = time.monotonic() - t0
    return res


__all__ = ["FuelConstraint", "Goal", "PlanError", "PlanResult", "Pose", "RouteResult", "as_pose", "context",
           "plan_mission", "rejoin_index", "resolve_takeoff_heading", "route"]
