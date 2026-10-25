"""Game-time clocks (refactor plan 4.3). now() returns game seconds. The real source will be the frame counter
(ds:5520, as MissileWatch measures it; WP-4); until then WallClock scales wall time by a ratio (the game ran
~2.45x real time in the memlogs, 10-90 % 2.0-2.9; MissileWatch docstring)."""
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> float: ...


class WallClock:
    """Game s = t0 + (wall s since start) x ratio. Changing `ratio` keeps now() continuous."""
    def __init__(self, ratio=1.0, t0=0.0, time_fn=time.monotonic):
        self._fn, self._ratio = time_fn, float(ratio)
        self._w0, self._g0 = time_fn(), float(t0)

    @property
    def ratio(self): return self._ratio

    @ratio.setter
    def ratio(self, r):
        self._g0, self._w0 = self.now(), self._fn(); self._ratio = float(r)

    def now(self): return self._g0 + (self._fn() - self._w0) * self._ratio


class ManualClock:
    """A clock tests move by hand."""
    def __init__(self, t=0.0): self.t = float(t)
    def now(self): return self.t
    def advance(self, dt): self.t += dt; return self.t
    def set(self, t): self.t = float(t)
