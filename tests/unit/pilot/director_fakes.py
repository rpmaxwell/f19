"""Test doubles for the FlightDirector tests (no planner, no game): a fake planner context, a scripted router that
returns real planner.api.RouteResult objects, synthetic GameStates on the golden CE reference route, and decisions."""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

TESTS = Path(__file__).resolve().parents[2]
for _p in (TESTS, TESTS / "unit" / "planner"):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))

from f19.core.types import Attitude, GameState, Mission, Position, Route, Systems  # noqa: E402
from f19.pilot.director import Territory  # noqa: E402
from f19.planner import api  # noqa: E402
from f19.planner.router import BudgetExceeded, PlanError  # noqa: E402

KOL = "ce_kolobrzeg_1022_2250"
CELL = 2048
LB_PER_KM = 62.1 / (190 * 1.459376 / 60)            # estimate.lb_per_km for this profile (13.4)


def profile(reserve=300.0):
    return dict(nav_lb_min=62.1, nav_hold_kt=190, burn_factor=1.0, landing_reserve_lb=reserve, fuel_loaded_lb=13800,
                extra_fuel_bays=2)


def grid(default=0, **cells):
    """16 x 16 territory grid: all `default` (0 enemy, 2 neutral, 3 friendly); cells={"x,y": code} overrides."""
    g = np.full((16, 16), default, np.uint8)
    for k, v in cells.items():
        x, y = map(int, k.split(",")); g[y, x] = v
    return g


def fake_planner(g=None, reserve=300.0, settings=None):
    g = grid() if g is None else g
    return SimpleNamespace(P=profile(reserve), W=SimpleNamespace(tgrid=g), th="CE",
                           spec={"settings": settings if settings is not None else
                                 {"roe": 0, "mission": "strike", "opponent": "elite", "landing": "realistic",
                                  "fuel_estimate": 13800}})


def golden_reference():
    """(Route, Mission, route dict) of the Kolobrzeg golden: primary at points 32-34, secondary 62-64, landing at the end."""
    from planner_golden import golden
    ref, mm = golden(KOL)
    return Route.from_route_json(ref), Mission.from_mission_json(mm), ref


def state_at(route, i, fuel=None, off_km=0.0, dh=0.0, t=None, alt=250.0, tanks=2, opp=3, events=None):
    """A GameState on the reference route at point i (like planner_golden.pose_near, with the route's own fuel)."""
    pts = route.points
    a, b = pts[i], pts[i + 1]
    u = np.subtract(b.xy, a.xy); u = u / np.hypot(*u)
    p = np.array(a.xy, float) + np.array([-u[1], u[0]]) * off_km * 64
    hdg = (np.degrees(np.arctan2(u[0], -u[1])) + dh) % 360
    return GameState(t=float(a.t_game_s if t is None else t), pos=Position(float(p[0]), float(p[1]), alt), att=Attitude(float(hdg)),
                     speed_kt=190.0, systems=Systems(fuel_lb=int(a.fuel_lb if fuel is None else fuel), loadout=((17, tanks),)),
                     opp=opp, events=events)


def state_xy(x, y, fuel=5000, t=1000.0, alt=250.0, hdg=0.0, tanks=0, opp=3):
    return GameState(t=t, pos=Position(float(x), float(y), alt), att=Attitude(hdg), speed_kt=190.0,
                     systems=Systems(fuel_lb=fuel, loadout=((17, tanks),)), opp=opp)


class Dec:
    """A Decision stand-in: .behavior, .phase, .events."""
    def __init__(self, behavior="fly_leg", phase="route", events=()):
        self.behavior, self.phase, self.events = behavior, phase, list(events)


def result(pose, goals, fuel_end, lands=True, errors=(), end_xy=None):
    """A real RouteResult for a scripted router: a pose point, one point per goal (the landing last), fuel_lb falling
    linearly to fuel_end (internal fuel; no tanks left)."""
    xy = end_xy or (pose.x + 640.0, pose.y - 640.0)
    pts = [((pose.x, pose.y), "Present position", {"kind": "pose"}), (xy, "Goal", {"kind": "landing"} if lands else {"kind": "overfly"})]
    f0 = pose.fuel_lb if pose.fuel_lb is not None else 13800
    points = [dict(x=pose.x, y=pose.y, label="Present position", kind="pose", fuel_lb=int(f0), t_game_s=int(pose.t)),
              dict(x=xy[0], y=xy[1], label="Landing (X)" if lands else "Goal", kind="landing" if lands else "overfly",
                   fuel_lb=int(fuel_end), t_game_s=int(pose.t) + 600)]
    tanks = pose.tanks or 0
    return api.RouteResult(pose, tuple(goals), pts, points, {"fuel_land": float(fuel_end) + 1900 * 0, "fuel0": f0 + 1900 * tanks, "steps": []},
                           list(errors), [], [], 100.0, 0.0, {})


class FakeRouter:
    """api.route stand-in. rule(goals, pose, budget_s) -> fuel_end (float), or an Exception instance / class to raise,
    or a RouteResult. Records every call as (tuple of goal kinds / roles, budget_s)."""
    def __init__(self, rule=None):
        self.rule = rule or (lambda goals, pose, budget: 5000.0)
        self.calls = []

    def __call__(self, state, goal, constraints=(), budget_s=10.0, *, planner, **kw):
        goals = (goal,) if isinstance(goal, api.Goal) else tuple(goal)
        pose = api.as_pose(state)
        self.calls.append((tuple(g.role or g.kind for g in goals), round(budget_s, 3)))
        r = self.rule(goals, pose, budget_s)
        if isinstance(r, BaseException) or (isinstance(r, type) and issubclass(r, BaseException)):
            raise r if isinstance(r, BaseException) else r("scripted")
        if isinstance(r, api.RouteResult): return r
        lands = goals[-1].kind in ("landing", "rejoin")
        last = goals[-1]
        end = (last.x, last.y) if last.kind == "point" else None
        return result(pose, goals, r, lands=lands, end_xy=end)

    @property
    def kinds(self): return [c[0] for c in self.calls]


class Clock:
    """A manual clock for first_feasible / the director's time accounting."""
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def enemy_samples(n, cell=(8, 8), t0=0):
    """n cold-war sample events (type 9) in territory cell `cell`: (clock, x >> 7, y >> 7, type, flags, extra)."""
    x, y = cell[0] * CELL >> 7, cell[1] * CELL >> 7
    return [(t0 + 32 * k, x, y, 9, 0, 0) for k in range(n)]


__all__ = ["BudgetExceeded", "PlanError", "Territory"]
