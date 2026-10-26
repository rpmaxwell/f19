"""What the HUD draws from: per-frame data (HudFrame), per-flight static data (HudContext) and what it accumulates over
frames (History). The HUD never reads memory: an adapter (f19.cli.hud for replays, nav for live flights) fills these.

HudFrame carries, besides the GameState of a telemetry Frame, what Frame / GameState do not have yet (the adapter
supplies it; core / telemetry can absorb these fields later):
  view        nav's view of the cycle (nav.py ui.frame dict): phase, banner, leg, wps, want, turn, to_turn_km,
              xtrack_km, next_name, next_wp, territory, route_left_km, hold_alt, hdg_hold, ts, vs, rate, rate_cmd,
              rolling, sens_msg, attrs, aim, phase_log
  event_list  the game's flight-event list (ds:8E46): every record so far, (clock, x >> 7, y >> 7, type, flags, target)
  errors      {item: message} of failed reads (the old status line's "read errors")
"""
import math
from dataclasses import dataclass, field

from f19.core.types import GameState
from f19.core.units import K

VIEW_KEYS = ("phase", "banner", "leg", "wps", "want", "turn", "to_turn_km", "xtrack_km", "next_name", "next_wp",
             "territory", "route_left_km", "hold_alt", "hdg_hold", "ts", "vs", "rate", "rate_cmd", "rolling",
             "sens_msg", "attrs", "aim", "phase_log", "gw", "sel", "p", "alt", "kt", "hdg")


@dataclass(frozen=True)
class HudFrame:
    t: float                                   # wall epoch s (nav's `now`; a memlog snapshot's name)
    state: GameState | None = None
    view: dict = field(default_factory=dict)
    event_list: tuple | None = None            # None = not read this frame (keep what we have)
    errors: dict = field(default_factory=dict)

    @classmethod
    def from_telemetry(cls, tf, view=None, event_list=None, errors=None):
        """From a TelemetryFrame: the GameState of its core Frame; view = leg, want (Command.heading), banner, phase
        (the behavior) from the Frame, then the TelemetryFrame's nav values with a view name, then `view`."""
        f = tf.frame
        v = {}
        if f is not None:
            v.update(leg=f.leg, want=f.command.heading, phase=tf.behavior or f.behavior)
            if f.command.banner: v["banner"] = f.command.banner
        v.update({k: val for k, val in tf.nav.items() if k in VIEW_KEYS and val is not None})
        v.update(view or {})
        return cls(tf.t, f.state if f is not None else None, v, event_list, dict(errors or {}))


@dataclass
class HudContext:
    """Per flight. theater: "CE" / "NC" / "LB" / "PG" or None; grid: the 16 x 16 territory grid [y, x] (int);
    heights / height_step: decoded terrain heights (ft) on a grid of height_step units; places {(x, y): name} (the
    theater's named .WLD places); mission: mission_mem.json; route: route.json; settings: plan.json settings
    (route spec first); wps: route points [((x, y), label)]; radar_systems {radar type: (system, "P"/"D", range km)}."""
    theater: str | None = None
    grid: object = None
    heights: object = None
    height_step: int = 16
    places: dict = field(default_factory=dict)
    mission: dict = field(default_factory=dict)
    route: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    wps: list | None = None
    radar_systems: dict = field(default_factory=dict)

    @property
    def names(self):
        """{target record: dict(place, system, ...)} from the mission's targets and radars (nav_tui.UI.names)."""
        n = {t["record"]: dict(place=t["place"], system=t.get("object", "")[:6]) for t in self.mission.get("targets", [])
             if t.get("record") is not None}
        n.update({r["record"]: r for r in self.mission.get("radars", []) if "record" in r})
        return n

    @property
    def capacity(self): return float(self.route.get("profile", {}).get("fuel_loaded_lb") or 0)

    @property
    def reserve(self): return float(self.route.get("profile", {}).get("landing_reserve_lb") or 1000)


class History:
    """What the HUD keeps across frames (nav_tui's Telemetry + MapView.trail + UI.log): the game event list read so
    far, fuel and clock histories (burn measured, game time ratio), the flown track and the log lines.
    period: histories are sampled at most this often (frame time s; nav_tui polled once a second live, every frame
    in --replay)."""
    LOG_MAX = 200

    def __init__(self, period=1.0):
        self.period = period
        self.t = None
        self.have_status = False
        self.n_events, self.events, self.new_events = 0, [], []
        self.fuel_hist, self.clock_hist = [], []        # (clock, fuel), (frame t, clock)
        self.trail = []
        self.log, self._phase_n = [], 0
        self.errors = {}

    def poll(self, fr):
        """nav_tui.Telemetry.poll on one frame's data."""
        if self.t is not None and fr.t - self.t < self.period: return
        self.t = fr.t
        self.errors = dict(fr.errors)
        st = fr.state
        if st is not None: self.have_status = True
        if fr.event_list is not None:
            ev = list(fr.event_list); n = len(ev)
            if n < self.n_events: self.events, self.n_events = [], 0          # a new flight
            recs = ev[self.n_events:]
            self.events += recs; self.new_events += recs; self.n_events = n
        if st is not None and st.t is not None and st.systems.fuel_lb is not None:
            c, f = int(st.t) // 2, st.systems.fuel_lb
            if self.fuel_hist and c < self.fuel_hist[-1][0]: self.fuel_hist, self.clock_hist = [], []   # new flight
            self.fuel_hist.append((c, f)); self.fuel_hist = [h for h in self.fuel_hist if h[0] >= c - 90]   # 3 game min
            self.clock_hist.append((fr.t, c)); self.clock_hist = [h for h in self.clock_hist if h[0] >= fr.t - 30]

    def burn_measured(self):
        """lb per game minute over the last ~3 game min (None until 1 game min of data; tank transfers reset it)."""
        h = self.fuel_hist
        for i in range(1, len(h)):
            if h[i][1] > h[i - 1][1] + 50: h[:] = h[i:]; break   # Extra Fuel transfer: start again
        if len(h) < 2 or h[-1][0] - h[0][0] < 30: return None
        return (h[0][1] - h[-1][1]) / ((h[-1][0] - h[0][0]) * 2 / 60)

    def time_ratio(self):
        """game s per real s from the clock (1 tick = 2 game s)."""
        h = self.clock_hist
        if len(h) < 2 or h[-1][0] - h[0][0] < 3: return None
        return (h[-1][1] - h[0][1]) * 2 / (h[-1][0] - h[0][0])

    def add_trail(self, p):
        if not self.trail or math.dist(self.trail[-1], p) > 0.25 * K: self.trail.append(p); self.trail = self.trail[-4000:]
