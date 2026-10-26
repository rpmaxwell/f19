"""The cheap feasibility estimate (refactor plan 4.2): is a candidate worth running the router on?

  remaining planned fuel   route.json point fuel_lb interpolated at the current position (scripts/nav_tui.py "plan
                           here"), minus the plan's fuel at the end, plus the Extra Fuel transfers the plan still makes
  + extra distance x lb/km the profile's nav burn at nav's held speed: nav_lb_min / (nav_hold_kt x ground km/min per kt)
                           = 62.1 / (190 x 1.459376 / 60) = 13.4 lb/km (what fuelplan.integrate burns per km)

  est = estimate(points, pose, P, extra_km)          on / near the reference route
  est = estimate_rejoin(points, pose, P, index)      fly straight to reference point `index`, then the reference
  est = estimate_direct(pose, [xy, ...], P)          straight legs through xy (a goal off the reference)
  FuelConstraint(reserve_lb).ok(est) / .check(route_result, planner)

Fuel figures are TOTAL fuel (internal + untransferred Extra Fuel tanks), the way fuelplan.integrate's fuel_land counts.
"""
import math
from dataclasses import dataclass

from .fuelplan import TANK_LB
from .world import GROUND_KMH_PER_KT, K

TRANSFER_JUMP_LB = 1000.0       # a point-to-point rise in planned fuel_lb this large is an Extra Fuel transfer


def lb_per_km(P):
    """Planned burn per km: nav_lb_min at nav_hold_kt (x burn_factor), as fuelplan.integrate burns it."""
    if not P.get("nav_hold_kt"):
        raise ValueError("estimate: the profile has no nav_hold_kt (per-leg speeds): no single lb/km")
    return P["nav_lb_min"] / (P["nav_hold_kt"] * GROUND_KMH_PER_KT / 60) * P["burn_factor"]


def _xy(p): return float(p["x"]), float(p["y"])


def nearest_leg(points, xy):
    """(i, f, d_units): leg points[i-1] -> points[i] nearest xy, fraction f along it, distance."""
    best = None
    for i in range(1, len(points)):
        (ax, ay), (bx, by) = _xy(points[i - 1]), _xy(points[i])
        L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
        f = max(0.0, min(1.0, ((xy[0] - ax) * (bx - ax) + (xy[1] - ay) * (by - ay)) / L2))
        d = math.hypot(xy[0] - ax - f * (bx - ax), xy[1] - ay - f * (by - ay))
        if best is None or d < best[2]: best = (i, f, d)
    return best


def planned_fuel_here(points, xy, leg=None):
    """Planned internal fuel (lb) at xy: nav_tui's "plan here" (fuel_lb interpolated along leg `leg`, default the nearest
    leg). -> (lb, leg, fraction)."""
    if leg is None: leg, f, _ = nearest_leg(points, xy)
    else:
        (ax, ay), (bx, by) = _xy(points[leg - 1]), _xy(points[leg])
        L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
        f = max(0.0, min(1.0, ((xy[0] - ax) * (bx - ax) + (xy[1] - ay) * (by - ay)) / L2))
    a, b = points[leg - 1]["fuel_lb"], points[leg]["fuel_lb"]
    return a + (b - a) * f, leg, f


def transfers_after(points, leg):
    """Extra Fuel transfers the plan makes from leg `leg` on (rises of planned fuel_lb)."""
    fl = [p["fuel_lb"] for p in points[leg - 1:]]
    return sum(1 for a, b in zip(fl, fl[1:]) if b - a > TRANSFER_JUMP_LB)


def planned_burn_from(points, xy, leg=None):
    """Total fuel the reference still burns from xy to its end: internal drop + TANK_LB per transfer still to come."""
    here, leg, _ = planned_fuel_here(points, xy, leg)
    return here - points[-1]["fuel_lb"] + TANK_LB * transfers_after(points, leg), leg


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


def estimate(points, pose, P, extra_km=0.0, leg=None):
    """Fuel at the reference's end if the plane flies the rest of it from here, plus extra_km of detour."""
    burn, _ = planned_burn_from(points, pose.xy, leg)
    return Estimate(_total_now(pose, P), burn, float(extra_km), lb_per_km(P))


def estimate_rejoin(points, pose, P, index):
    """Straight from the pose to reference point `index`, then the reference to its end."""
    burn = points[index]["fuel_lb"] - points[-1]["fuel_lb"] + TANK_LB * transfers_after(points, index + 1)
    km = math.dist(pose.xy, _xy(points[index])) / K
    return Estimate(_total_now(pose, P), burn, km, lb_per_km(P))


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
