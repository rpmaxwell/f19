"""The cheap feasibility estimate (refactor plan 4.2): is a candidate worth running the router on?

  remaining planned fuel   route.json point fuel_lb interpolated at the current position (scripts/nav_tui.py "plan
                           here"), as TOTAL fuel (internal + the Extra Fuel tanks the plan has not transferred there),
                           minus the plan's total fuel at landing (summary fuel_landing_lb)
  + extra distance x lb/km the profile's nav burn at nav's held speed: nav_lb_min / (nav_hold_kt x ground km/min per kt)
                           = 62.1 / (190 x 1.459376 / 60) = 13.4 lb/km (exactly what fuelplan.integrate burns per km)

  ref = Reference(route_json)
  estimate(ref, pose, P, extra_km)                 fly the rest of the reference from the pose's projection on it
  estimate_rejoin(ref, pose, P, index)             straight to reference point `index`, then the reference
  estimate_direct(pose, [xy, ...], P)              straight legs through xy (a goal off the reference)
  FuelConstraint(reserve_lb).ok(est, P) / .check(route_result, planner)

Fuel figures are TOTAL fuel (internal + untransferred tanks), the way fuelplan.integrate's fuel_land counts it. The
straight-line distances make estimate_rejoin / estimate_direct optimistic (no turns, approach pattern or detours): a
candidate that fails the estimate fails the router too; one that passes still goes to the router.
"""
import math
from dataclasses import dataclass

from .fuelplan import TANK_LB
from .world import GROUND_KMH_PER_KT, K


def lb_per_km(P):
    """Planned burn per km: nav_lb_min at nav_hold_kt (x burn_factor), as fuelplan.integrate burns it."""
    if not P.get("nav_hold_kt"):
        raise ValueError("estimate: the profile has no nav_hold_kt (per-leg speeds): no single lb/km")
    return P["nav_lb_min"] / (P["nav_hold_kt"] * GROUND_KMH_PER_KT / 60) * P["burn_factor"]


def _xy(p): return float(p["x"]), float(p["y"])


def nearest_leg(points, xy, window=None):
    """(i, f, d_units): leg points[i-1] -> points[i] nearest xy, fraction f along it, distance. window: (lo, hi) leg indices
    to look at (a reference that loops back past the plane would otherwise be projected on at the wrong place)."""
    best = None
    lo, hi = window if window else (1, len(points) - 1)
    for i in range(max(1, lo), min(len(points) - 1, hi) + 1):
        (ax, ay), (bx, by) = _xy(points[i - 1]), _xy(points[i])
        L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
        f = max(0.0, min(1.0, ((xy[0] - ax) * (bx - ax) + (xy[1] - ay) * (by - ay)) / L2))
        d = math.hypot(xy[0] - ax - f * (bx - ax), xy[1] - ay - f * (by - ay))
        if best is None or d < best[2]: best = (i, f, d)
    return best


def _fraction(points, leg, xy):
    (ax, ay), (bx, by) = _xy(points[leg - 1]), _xy(points[leg])
    L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
    return max(0.0, min(1.0, ((xy[0] - ax) * (bx - ax) + (xy[1] - ay) * (by - ay)) / L2))


def planned_fuel_here(points, xy, leg=None):
    """Planned internal fuel (lb) at xy: nav_tui's "plan here" (fuel_lb interpolated along leg `leg`, default the nearest
    leg). -> (lb, leg, fraction)."""
    if leg is None: leg, f, _ = nearest_leg(points, xy)
    else: f = _fraction(points, leg, xy)
    a, b = points[leg - 1]["fuel_lb"], points[leg]["fuel_lb"]
    return a + (b - a) * f, leg, f


class Reference:
    """A planned route (route.json dict) for the estimate: per point the planned TOTAL fuel. Planned internal fuel only
    rises at an Extra Fuel transfer (+TANK_LB), so each rise of fuel_lb is one tank used."""
    def __init__(self, route):
        self.points = list(route["points"])
        prof, spec, summ = route.get("profile") or {}, route.get("spec") or {}, route.get("summary") or {}
        self.bays = int(prof.get("extra_fuel_bays", spec.get("extra_fuel", 0)))
        fl = [p["fuel_lb"] for p in self.points]
        used = 0; self.total = []
        for i, f in enumerate(fl):
            if i and f > fl[i - 1]: used += 1
            self.total.append(f + TANK_LB * (self.bays - used))
        self.end_total = float(summ["fuel_landing_lb"]) if "fuel_landing_lb" in summ else self.total[-1]

    def total_at(self, xy, leg=None):
        """Planned total fuel at xy (interpolated along `leg`, default the nearest leg)."""
        if leg is None: leg, f, _ = nearest_leg(self.points, xy)
        else: f = _fraction(self.points, leg, xy)
        return self.total[leg - 1] + (self.total[leg] - self.total[leg - 1]) * f

    def burn_from(self, xy, leg=None):
        return self.total_at(xy, leg) - self.end_total

    def burn_from_point(self, i):
        return self.total[i] - self.end_total


@dataclass(frozen=True)
class Estimate:
    fuel_now_lb: float           # total (internal + tanks) now
    burn_lb: float               # planned burn still to come along the reference part
    extra_km: float
    lb_per_km: float

    @property
    def fuel_end_lb(self): return self.fuel_now_lb - self.burn_lb - self.extra_km * self.lb_per_km


def _total_now(pose, P):
    fuel = P["fuel_loaded_lb"] if pose.fuel_lb is None else pose.fuel_lb
    tanks = P["extra_fuel_bays"] if pose.tanks is None else pose.tanks
    return float(fuel) + TANK_LB * int(tanks)


def _ref(ref): return ref if isinstance(ref, Reference) else Reference(ref)


def estimate(ref, pose, P, extra_km=0.0, leg=None):
    """Fuel at the reference's end if the plane flies the rest of it from here, plus extra_km of detour."""
    ref = _ref(ref)
    return Estimate(_total_now(pose, P), ref.burn_from(pose.xy, leg), float(extra_km), lb_per_km(P))


def estimate_rejoin(ref, pose, P, index):
    """Straight from the pose to reference point `index`, then the reference to its end."""
    ref = _ref(ref)
    km = math.dist(pose.xy, _xy(ref.points[index])) / K
    return Estimate(_total_now(pose, P), ref.burn_from_point(index), km, lb_per_km(P))


def estimate_direct(pose, waypoints, P):
    """Straight legs from the pose through each (x, y) of `waypoints` (e.g. a target, then a landing field)."""
    km, p = 0.0, pose.xy
    for q in waypoints: km += math.dist(p, q) / K; p = q
    return Estimate(_total_now(pose, P), 0.0, km, lb_per_km(P))


@dataclass(frozen=True)
class FuelConstraint:
    """Fuel at the route's end (total, the way fuelplan counts fuel_land) >= reserve_lb (None: the profile's
    landing_reserve_lb)."""
    reserve_lb: float | None = None

    def reserve(self, P): return P["landing_reserve_lb"] if self.reserve_lb is None else self.reserve_lb

    def ok(self, est, P):
        """The cheap test on an Estimate."""
        return est.fuel_end_lb >= self.reserve(P)

    def check(self, result, planner):
        """On a planner.api.RouteResult: [] or [a violation message]."""
        r = self.reserve(planner.P); f = result.fuel_end_lb
        if f >= r: return []
        return [f"fuel at the end {f:.0f} lb, below the {r:.0f} lb reserve"]
