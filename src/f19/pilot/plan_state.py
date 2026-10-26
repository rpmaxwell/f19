"""Flight-plan bookkeeping for the FlightDirector (WP-7c): what the mission still asks for, and which route the pilot
flies. Pure state, no planning.

MissionPlan   the ordered Waypoints (TAKEOFF, PRIMARY, SECONDARY, LANDING; only TAKEOFF is required) with a status each:
              pending -> reached | skipped. A status only moves forward from pending (a reached / skipped waypoint is
              never reopened). Waypoint is frozen: a change swaps the entry.
PlanStore     the reference Route (the best-case pre-flight route, version = its own counter, kept for splicing) and the
              active Route (what the pilot flies). `replace()` is the one method that changes the active route: it
              bumps the plan version, stamps it on the Route and records a PlanRecord (the telemetry's plan_version
              values and why they exist). The runner reads `current()` / `version` every tick; replace() is safe to call
              from any thread (lock), the pilot only ever sees a whole Route.
"""
import threading
from dataclasses import dataclass, field

from f19.core.types import Route, Waypoint, WaypointKind, WaypointStatus

ORDER = (WaypointKind.TAKEOFF, WaypointKind.PRIMARY, WaypointKind.SECONDARY, WaypointKind.LANDING)
TARGET_KINDS = (WaypointKind.PRIMARY, WaypointKind.SECONDARY)


class MissionPlan:
    """The ordered waypoints of the mission and their statuses."""

    def __init__(self, waypoints):
        self._wps = {w.kind: w for w in sorted(waypoints, key=lambda w: ORDER.index(w.kind))}

    @classmethod
    def from_mission(cls, mission):
        """From a core Mission (its .waypoints(): the ones the mission has)."""
        return cls(mission.waypoints())

    def copy(self): return MissionPlan(self._wps.values())

    # --- queries
    def __iter__(self): return iter(self._wps.values())

    def __len__(self): return len(self._wps)

    def get(self, kind): return self._wps.get(kind)

    def status(self, kind):
        w = self._wps.get(kind)
        return None if w is None else w.status

    def has(self, kind): return kind in self._wps

    def pending(self):
        """Waypoints still to do, in mission order."""
        return tuple(w for w in self if w.status is WaypointStatus.PENDING)

    def pending_kinds(self): return tuple(w.kind for w in self.pending())

    def pending_targets(self): return tuple(w for w in self.pending() if w.kind in TARGET_KINDS)

    def required_pending(self): return tuple(w for w in self.pending() if w.required)

    @property
    def landing_pending(self): return self.status(WaypointKind.LANDING) is WaypointStatus.PENDING

    # --- changes
    def mark(self, kind, status):
        """pending -> status. True if the waypoint changed (a missing or already-settled waypoint: False)."""
        w = self._wps.get(kind)
        if w is None or w.status is not WaypointStatus.PENDING or status is WaypointStatus.PENDING: return False
        self._wps[kind] = w.with_status(status)
        return True

    def summary(self):
        return {w.kind.value: w.status.value for w in self}


@dataclass(frozen=True)
class PlanRecord:
    """One plan version, for telemetry and tests: why it exists and what it cost to find."""
    version: int
    t: float | None                      # game s (or the director's clock) when adopted
    reason: str                          # "deviation ended: missile", "fuel: ...", ...
    candidate: str                       # "full", "primary>landing", "bail:friendly", ...
    kind: str                            # mission | earn | bail | eject | initial
    points: int = 0                      # route points
    fuel_end_lb: float | None = None
    plan_s: float = 0.0                  # worker time spent finding it
    skipped: tuple = ()                  # waypoint kinds given up with this version
    attempts: tuple = ()                 # (candidate, verdict, seconds)


@dataclass
class PlanStore:
    """reference + active Route, both versioned (see the module docstring)."""
    reference: Route
    active: Route = None
    version: int = 0
    records: list = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def __post_init__(self):
        if self.active is None: self.active = self.reference.with_version(0)
        self.version = self.active.version
        self.records.append(PlanRecord(self.version, None, "pre-flight reference", "reference", "initial",
                                       len(self.active.points)))

    def current(self):
        with self._lock: return self.active

    def set_reference(self, route):
        """A new best-case route (the reference's own version counts up; the active route is untouched)."""
        with self._lock: self.reference = route.with_version(self.reference.version + 1)
        return self.reference

    def replace(self, route, reason, *, t=None, candidate="", kind="mission", fuel_end_lb=None, plan_s=0.0,
                skipped=(), attempts=()):
        """Make `route` the active route as the next plan version (the Route is stamped with it); records why."""
        with self._lock:
            self.version += 1
            self.active = route.with_version(self.version)
            self.records.append(PlanRecord(self.version, t, reason, candidate, kind, len(route.points), fuel_end_lb,
                                           plan_s, tuple(skipped), tuple(attempts)))
            return self.active
