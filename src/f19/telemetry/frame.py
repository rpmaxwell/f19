"""What flows through the telemetry stream.

TelemetryFrame wraps a core Frame (GameState, behavior, Command, plan_version, leg) with what the nav log carries
besides it: the wall-clock time nav stamps rows with, nav's loop internals (xtrack, press, rate, tscale, ... keyed by
nav CSV column), the route heading before any reaction overrode it, an optional memory snapshot and events. Any of
these may be absent: a replayed legacy CSV row has no GameState of its own (one is rebuilt from the row), a memlog
snapshot arrives as a frame of its own.

Times: TelemetryFrame.t, Snapshot.t and Event.t are wall-clock epoch seconds (what nav logs). Frame.t is game seconds.
"""
import json
from dataclasses import dataclass, field

from f19.core.types import Attitude, Command, Frame, GameState, Position, Systems

from f19.telemetry.navcsv import Event


@dataclass(frozen=True, slots=True)
class MemlogLayout:
    """windows.json: the memory offset nav found and the windows [(name, lo, hi)] (physical, hi exclusive) whose
    bytes, concatenated in order, make one snapshot. Carries the layout itself: no address table needed."""
    offset: int
    windows: tuple                       # ((name, lo, hi), ...)

    def __post_init__(self):
        object.__setattr__(self, "windows", tuple((str(n), int(lo), int(hi)) for n, lo, hi in self.windows))

    @property
    def size(self): return sum(hi - lo for _, lo, hi in self.windows)

    def to_json(self):
        """Exactly what nav.MemLog writes: json.dump({"offset", "windows"}, indent=1), no trailing newline."""
        return json.dumps({"offset": self.offset, "windows": [list(w) for w in self.windows]}, indent=1)

    @classmethod
    def from_json(cls, text):
        d = json.loads(text)
        return cls(d["offset"], tuple(tuple(w) for w in d["windows"]))

    def window(self, name):
        """(lo, hi, position in the snapshot) of a named window."""
        pos = 0
        for n, lo, hi in self.windows:
            if n == name: return lo, hi, pos
            pos += hi - lo
        raise KeyError(name)

    def read(self, data, phys, n):
        """n bytes at physical address phys from snapshot data (zeros outside the windows; as nav_tui.SnapDB)."""
        out, pos = bytearray(n), 0
        for _, lo, hi in self.windows:
            if lo <= phys < hi or lo < phys + n <= hi:
                a, b = max(lo, phys), min(hi, phys + n)
                out[a - phys:b - phys] = data[pos + a - lo:pos + b - lo]
            pos += hi - lo
        return bytes(out)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """One memlog snapshot: t = epoch s (nav names it by the MIDPOINT of its multi-cycle read), data = the windows'
    bytes concatenated."""
    t: float
    data: bytes

    @property
    def name(self): return f"snap_{self.t:.2f}.bin"


@dataclass(frozen=True, slots=True)
class TelemetryFrame:
    t: float                                  # wall epoch s (nav's `now`)
    frame: Frame | None = None
    nav: dict = field(default_factory=dict)   # nav CSV column -> typed value; overrides what is derived from `frame`
    route_heading: float | None = None        # the route's want before reactions override it (CSV route_want)
    behavior: str | None = None               # active behavior; default frame.behavior
    plan_version: int | None = None           # default frame.plan_version
    snapshot: Snapshot | None = None
    events: tuple = ()                        # Event
    row_width: int | None = None              # short CSV row (nav's sensitivity row: 11)

    @property
    def has_row(self): return self.frame is not None or bool(self.nav)

    @classmethod
    def sensitivity_row(cls, t, sens):
        """nav.py :658: the 11-column row logged while pressing Insert to get keyboard sensitivity 1."""
        return cls(t, nav={"t": t, "press": f"sens {sens}->ins"}, row_width=11)

    def values(self):
        """dict nav CSV column -> typed value: derived from the Frame, then the extra fields, then `nav` on top."""
        v = {"t": self.t}
        f = self.frame
        if f is not None:
            s, c = f.state, f.command
            v.update(x=s.pos.x, y=s.pos.y, alt=s.pos.alt_ft, spd=s.speed_kt, hdg=s.att.heading, leg=f.leg,
                     want=c.heading, escape=c.escape, fighter=c.fighter, behavior=f.behavior,
                     plan_version=f.plan_version, throttle=s.systems.throttle, locked=s.systems.locked,
                     fuel=s.systems.fuel_lb, flags=s.systems.flags)
        if self.route_heading is not None: v["route_want"] = self.route_heading
        if self.behavior is not None: v["behavior"] = self.behavior
        if self.plan_version is not None: v["plan_version"] = self.plan_version
        v.update(self.nav)
        return v


def _num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)


def frame_from_values(v):
    """A core Frame rebuilt from nav CSV values (the replay of a log without its own GameState). Frame.t (game s) is
    unknown in the CSV: None. None when the row has no position (the sensitivity row)."""
    if not all(_num(v.get(k)) for k in ("x", "y", "alt", "spd", "hdg")): return None
    g = lambda k: v.get(k) if _num(v.get(k)) else None
    st = GameState(t=None, pos=Position(v["x"], v["y"], v["alt"]), att=Attitude(v["hdg"]), speed_kt=v["spd"],
                   systems=Systems(flags=g("flags") or 0, throttle=g("throttle"), fuel_lb=g("fuel"), locked=g("locked")))
    s = lambda k: v.get(k) or None
    cmd = Command(heading=g("want"), escape=s("escape"), fighter=s("fighter"))   # CSV "missile" is a count, not Command.missile
    beh = v.get("behavior") or v.get("phase") or ""
    return Frame(None, st, beh if isinstance(beh, str) else str(beh), cmd, plan_version=g("plan_version"), leg=g("leg"))
