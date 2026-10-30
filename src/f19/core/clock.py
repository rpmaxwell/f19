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


class FrameClock:
    """Game seconds and the real game/wall ratio from the frame counter (ds:5520, u16, wraps), measured as MissileWatch does:
    one frame = 2 / fs game s (fs = ds:4EFC frames per 2 s tick). It only MEASURES (2026-10-29, scope decision): nothing
    flies on it yet, loops and behaviors keep wall timers and time_mult `ts`. feed() returns game s since the first feed.
    A step of >= JUMP frames (a save-state load, a pause) re-bases instead of counting. ratio: game s per wall s over the last
    WINDOW_S wall s, None until MIN_SPAN_S has passed or when the reading is outside 0.5..8 (a pause, a load)."""
    JUMP, WINDOW_S, MIN_SPAN_S = 2000, 6.0, 1.5

    def __init__(self):
        self.gt, self.last, self.fs, self.hist, self.ratio = 0.0, None, None, [], None

    def now(self): return self.gt

    def feed(self, frame, fs, wall):
        if fs and fs > 0: self.fs = fs
        if self.last is not None and self.fs:
            df = (frame - self.last) & 0xFFFF
            if df < self.JUMP: self.gt += df * 2.0 / self.fs
            else: self.hist.clear()
        self.last = frame
        self.hist.append((wall, self.gt)); self.hist = [h for h in self.hist if h[0] >= wall - self.WINDOW_S]
        w0, g0 = self.hist[0]
        r = (self.gt - g0) / (wall - w0) if wall - w0 >= self.MIN_SPAN_S else None
        self.ratio = r if r is not None and 0.5 < r < 8 else None
        return self.gt
