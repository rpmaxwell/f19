"""pilot.eject.LevelOut: level the plane, then Shift+F10 on the next read. Offline: a toy plane whose roll / pitch RATE
is set by key taps (right key / "down" arrow add rate, keypad 5 zeroes it) with the gain deliberately different from
the controller's guess, on a manual clock."""
import pytest

from f19.core.clock import ManualClock
from f19.core.types import Attitude, GameState, Position, Systems
from f19.pilot.controls import CENTER, F10, NOSE_DOWN, NOSE_UP, ROLL_RIGHT, Controls
from f19.pilot.eject import LevelOut, death_roll_s


class Plane:
    """True gains differ from LevelOut's guesses (roll 8, pitch 6 deg/s per s held)."""
    def __init__(self, clock, roll=0.0, pitch=0.0, kt=250, alt=3000, roll_gain=5.0, pitch_gain=9.0):
        self.clock, self.roll, self.pitch, self.kt, self.alt = clock, roll, pitch, kt, alt
        self.wr = self.wp = 0.0
        self.gain = (roll_gain, pitch_gain)
        self.t, self.log, self.fail_reads = clock.now(), [], False
        self.sink, self.sens = 0.0, 1

    def advance(self):
        dt = self.clock.now() - self.t; self.t = self.clock.now()
        self.roll += self.wr * dt; self.pitch += self.wp * dt
        self.alt += (self.sink + 4.0 * self.pitch) * dt                # sink in a bank/descent; 4 ft/s per deg of nose-up

    # emulator side
    def tap(self, key, hold=0.2):
        self.advance(); self.log.append(("tap", key, round(hold, 2)))
        if key == ROLL_RIGHT: self.wr += self.gain[0] * hold
        elif key == "left": self.wr -= self.gain[0] * hold
        elif key == NOSE_UP: self.wp += self.gain[1] * hold              # the "down" arrow = nose up
        elif key == NOSE_DOWN: self.wp -= self.gain[1] * hold
        elif key == CENTER: self.wr = self.wp = 0.0
        elif key == "ins": self.sens = self.sens % 3 + 1
        self.clock.advance(hold)
    def scan_tap(self, code, hold=0.2, shift=False): self.advance(); self.log.append(("scan", code, shift)); self.clock.advance(hold)
    def key_down(self, k): pass
    def key_up(self, k): self.log.append(("up", k))

    # reader side
    def snapshot(self, threats=True):
        self.advance(); self.clock.advance(0.1)
        if self.fail_reads: raise RuntimeError("gone")
        self.kt += 0
        return GameState(t=0.0, pos=Position(1.0, 1.0, self.alt), att=Attitude(0.0, self.pitch, self.roll), speed_kt=self.kt,
                         systems=Systems(flags=1, throttle=60), sensitivity=self.sens)


def _run(plane, clock, **kw):
    lo = LevelOut(Controls(plane), plane, sleep=clock.advance, wall=clock.now, say=lambda m: None)
    for k, v in kw.items(): setattr(lo, k, v)
    return lo.run()


def _ejects(plane): return [e for e in plane.log if e[0] == "scan" and e[1] == F10]


def test_death_roll_formula():
    assert death_roll_s(0, 0, 190) == pytest.approx(190)
    assert death_roll_s(0, 60, 230) == pytest.approx(571, abs=1)          # landing-takeoff.md: 60 deg bank at 230 kt, S = 571


@pytest.mark.parametrize("roll,pitch,kt", [(0, 0, 250), (60, 0, 300), (-85, 12, 350), (25, -30, 230), (120, 20, 300)])
def test_levels_then_ejects_once(roll, pitch, kt):
    clock = ManualClock(0.0)
    p = Plane(clock, roll, pitch, kt)
    res = _run(p, clock)
    assert res.ejected and res.why == "level"
    assert len(_ejects(p)) == 1 and p.log[-1][:2] == ("scan", F10)             # the key is the last thing sent
    p.advance()
    assert abs(p.roll) < 7 and abs(p.pitch) < 7                               # still level when the key went
    assert res.s_final <= LevelOut.SAFE_S


def test_ejects_on_the_read_after_arming_not_the_same_one():
    clock = ManualClock(0.0)
    p = Plane(clock)
    res = _run(p, clock)
    assert res.why == "level" and res.ticks >= 2 and any(l.startswith("armed") for l in res.log)


def test_disarms_if_the_plane_leaves_level():
    clock = ManualClock(0.0)
    p = Plane(clock)
    real, n = p.snapshot, {"n": 0}
    def snap(threats=True):
        n["n"] += 1
        if n["n"] == 4: p.roll = 30.0                                          # a gust between the two reads
        return real(threats)
    p.snapshot = snap
    res = _run(p, clock)
    assert any(l.startswith("disarmed") for l in res.log) and res.ejected and abs(p.roll) < 7


def test_times_out_and_ejects_anyway():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=90, kt=300, roll_gain=0.0)                           # the keys do nothing
    res = _run(p, clock)
    assert res.ejected and res.why == "timeout" and len(_ejects(p)) == 1
    assert clock.now() >= LevelOut.MAX_S


def test_low_altitude_ejects_now():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=45, alt=20)
    res = _run(p, clock)
    assert res.why == "low" and len(_ejects(p)) == 1 and clock.now() < 2


def test_read_failure_ejects():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=45); p.fail_reads = True
    res = _run(p, clock)
    assert res.why == "read error" and len(_ejects(p)) == 1


def test_nose_low_sink_gets_pulled_up_before_the_key():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=40, pitch=-5, kt=300, alt=900)
    p.sink = -25.0                                                             # sinking even with the nose level
    res = _run(p, clock)
    assert res.ejected and res.why == "level"
    p.advance()
    assert p.pitch > 0.5                                                       # pulled up to stop the sink
    assert any(l.startswith("armed") and "vs" in l for l in res.log)


def test_ejects_when_the_ground_is_two_seconds_away():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=70, kt=300, alt=250, roll_gain=0.0)
    p.sink = -150.0
    res = _run(p, clock)
    assert res.why == "low" and len(_ejects(p)) == 1


def test_sensitivity_is_raised_to_two_and_never_to_three():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=30)
    res = _run(p, clock)
    assert p.sens == 2 and res.ejected
    assert sum(1 for e in p.log if e[:2] == ("tap", "ins")) == 1


def test_key_gain_is_learned():
    clock = ManualClock(0.0)
    p = Plane(clock, roll=80, roll_gain=60.0)                                  # 2.4x the baseline
    lo = LevelOut(Controls(p), p, sleep=clock.advance, wall=clock.now, say=lambda m: None)
    lo.run()
    assert lo.gain["roll"] > LevelOut.ROLL_GAIN * 1.3
