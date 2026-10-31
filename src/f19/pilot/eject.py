"""The safe bail-out (2026-10-28): get the plane level, then press Shift+F10 on the next read.

Why (docs/game/landing-takeoff.md, "Ejection: when it kills you"): the death roll is made once, at the instant of the key,
as S = 5.69 x (|pitch| + |roll|) deg + kt  against  T = 500 + rand(300). Level and under ~450 kt, S < 500 and the
ejection cannot fail; a hard bank at speed fails most of the time. So the sequence is

  1. read the attitude (pitch ds:46DC, roll ds:46DE) and its rates from successive reads,
  2. drive both toward 0 with short key taps (roll: right key = roll angle up; pitch: the "down" arrow = nose up), and
     centre the stick (keypad 5: it stops the rates, the attitude persists) as soon as the coast would overshoot,
     (the pitch target is nose-up by 0.15 deg per ft/s of sink, 0..PITCH_UP_MAX: a banked plane sinks and the descent
     must stop before the key, the S cost of +4 deg is 23),
  3. once pitch and roll are inside LEVEL_DEG, the sink is under SINK_ARM_FPS with both rates quiet and the death-roll S under SAFE_S, ARM,
  4. on the NEXT read, if the plane is still inside the same limits, press Shift+F10 at once; otherwise disarm and go on.

Keyboard sensitivity is raised to SENSITIVITY for the sequence (faster stick; level 3 crashed a flight 2026-10-14, so 2
at most) and the key gains are re-learned from each tap's effect. It gives up levelling (ejects at the best attitude seen)
after MAX_S seconds, when the ground is under GROUND_S away at the current sink, or when the reads fail: a bail-out that never ejects is worse than a risky one. Nothing in it depends on altitude
(the roll does not), so it does not wait for height.

NOT TESTED LIVE: the roll and pitch key gains below are guesses from the pitch taps and the heading hold; the controller is
closed-loop (taps, re-read, centre) so a wrong gain costs time, not stability, but tune them on a save state first.
"""
from dataclasses import dataclass, field

from f19.core.types import Action
from f19.pilot.controls import CENTER, TAP, NOSE_DOWN, NOSE_UP, ROLL_LEFT, ROLL_RIGHT

DEG_TO_S = 65536 / 360 / 32                   # the death roll's (|angle16| >> 5) per degree = 5.689


def death_roll_s(pitch, roll, kt):
    """The game's S (EGAME 0xD6FF): P(death) = clamp(S - 500, 0, 300) / 300."""
    return DEG_TO_S * (abs(pitch) + abs(roll)) + kt


@dataclass
class EjectResult:
    ejected: bool                              # the key was sent
    why: str                                   # level | timeout | low | read error
    ticks: int = 0
    s_final: float | None = None
    log: list = field(default_factory=list)


class LevelOut:
    """LevelOut(controls, reader, sleep, wall, say). run() blocks until the key is sent. Everything goes through Controls."""
    LEVEL_DEG = 5.0                            # |pitch| and |roll| inside this (S adds < 57)
    RATE_QUIET = 1.5                           # deg/s, both axes
    SAFE_S = 470.0                             # death roll S must be under this (T >= 500)
    MAX_S = 25.0                               # then eject at the best attitude seen
    MIN_ALT_FT = 40.0                          # about to hit the ground: eject now
    GROUND_S = 2.0                             # ... or this close to it at the current sink
    SINK_ARM_FPS = 15.0                        # do not arm while sinking faster than this
    PITCH_UP_PER_FPS, PITCH_UP_MAX = 0.15, 4.0 # nose-up target (deg) per ft/s of sink, and its cap
    VS_WINDOW_S = 1.0
    BIAS_STEP, BIAS_MAX = 0.2, 2.5             # per steering pass; the pitch target's integral part (deg)
    FINE_DEG = 1.0                             # inside this of the target an axis is left alone
    SENSITIVITY = 2                            # keyboard sensitivity during the sequence (None: leave it)
    INS_TRIES = 4
    TICK_S = 0.1                               # pause between reads (the read itself takes ~0.1 s)
    ROLL_GAIN, PITCH_GAIN = 25.0, 15.0         # deg/s of rate per second a key is held at SENSITIVITY 2 (live 2026-10-29: learned
                                               # roll ~32, pitch ~17; at sensitivity 1 the guesses were 8 and 6)
    ROLL_MAX_RATE, PITCH_MAX_RATE = 12.0, 6.0  # deg/s: never ask for more
    P = 0.8                                    # desired rate = P x error
    RATE_DEAD = 0.8                            # deg/s: no tap inside this of the wanted rate
    LEAD_S = 0.6                               # centre the stick when |error| < |rate| x LEAD_S (overshoot coming)
    HOLD_MIN, HOLD_MAX = 0.06, 0.35
    SETTLE_S = 0.25                            # no new tap this soon after a tap or a centre (the rate needs a read to show)

    def __init__(self, controls, reader, sleep, wall, say=print):
        self.controls, self.reader, self.sleep, self.wall, self.say = controls, reader, sleep, wall, say
        self.gain = {"roll": self.ROLL_GAIN, "pitch": self.PITCH_GAIN}
        self.alts, self.last_tap, self.bias = [], [], 0.0
        self.sens_before = None

    def vs(self, now, alt):
        """ft/s over the last VS_WINDOW_S of reads (0.0 until two reads span 0.3 s)."""
        self.alts.append((now, alt)); self.alts[:] = [a for a in self.alts if a[0] >= now - self.VS_WINDOW_S]
        (t0, a0) = self.alts[0]
        return (alt - a0) / (now - t0) if now - t0 >= 0.3 else 0.0

    def pitch_target(self, vs):
        """Nose-up target: proportional to the sink, plus a bias that creeps up while the sink stays above 70 % of the arm
        limit (the proportional part alone settles just short of it) and back down once climbing."""
        if vs < -0.7 * self.SINK_ARM_FPS: self.bias = min(self.BIAS_MAX, self.bias + self.BIAS_STEP)
        elif vs > 0: self.bias = max(0.0, self.bias - self.BIAS_STEP)
        return max(0.0, min(self.PITCH_UP_MAX, -vs * self.PITCH_UP_PER_FPS + self.bias))

    def raise_sensitivity(self, st):
        """Ins (a 0.2 s tap: longer auto-repeats) until the reading is SENSITIVITY; -> the reading found first."""
        first = st.sensitivity
        if self.SENSITIVITY is None or first is None: return first
        for _ in range(self.INS_TRIES):
            if st.sensitivity == self.SENSITIVITY: break
            self.controls.tap("ins", TAP); self.sleep(0.3)
            st = self._read()
        return first

    def restore_sensitivity(self, first):
        """For a sequence that does not end in an ejection (tests, dry runs): back to the level found."""
        for _ in range(self.INS_TRIES):
            if first is None or self._read().sensitivity == first: break
            self.controls.tap("ins", TAP); self.sleep(0.3)

    def learn(self, rate):
        """The last taps' effect on their axis rates -> that axis' gain (EMA, clamped to 0.3x..4x the baseline)."""
        taps, self.last_tap = self.last_tap, []
        for axis, sign, hold, w0 in taps:
            if hold < 0.08: continue
            g = sign * (rate[0 if axis == "roll" else 1] - w0) / hold
            base = self.ROLL_GAIN if axis == "roll" else self.PITCH_GAIN
            if g > 0: self.gain[axis] = max(0.3 * base, min(4 * base, 0.5 * self.gain[axis] + 0.5 * g))

    def level(self, att, rate):
        return abs(att.pitch) <= self.LEVEL_DEG and abs(att.roll) <= self.LEVEL_DEG and abs(rate[0]) <= self.RATE_QUIET \
            and abs(rate[1]) <= self.RATE_QUIET

    def _read(self):
        return self.reader.snapshot(threats=False)

    def run(self):
        res, ticks, armed, prev, best, quiet_until = EjectResult(False, ""), 0, False, None, None, 0.0
        t0 = self.wall()
        self.controls.release_all()            # no key left down by a behavior; the stick is centred, attitude persists
        try:
            self.sens_before = self.raise_sensitivity(self._read())
            if self.sens_before != self.SENSITIVITY: res.log.append(f"sensitivity {self.sens_before} -> {self.SENSITIVITY}")
        except Exception: pass                 # noqa: BLE001  (the loop's own read handles a dead link)
        while True:
            now = self.wall()
            try: st = self._read()
            except Exception as e:             # noqa: BLE001
                return self._fire(res, ticks, "read error", f"read failed ({type(e).__name__}): ejecting as is")
            ticks += 1
            att = st.att
            rate = (0.0, 0.0)                  # (roll, pitch) deg/s from the last read
            if prev is not None and now > prev[0]:
                dt = now - prev[0]
                rate = ((att.roll - prev[1].roll) / dt, (att.pitch - prev[1].pitch) / dt)
            prev = (now, att)
            self.learn(rate)
            vs = self.vs(now, st.alt_ft)
            s = death_roll_s(att.pitch, att.roll, st.speed_kt)
            if best is None or s < best[0]: best = (s, now)
            res.s_final = s
            ok = self.level(att, rate) and s <= self.SAFE_S and vs >= -self.SINK_ARM_FPS
            if armed:                          # the tick after "confident": still level -> eject now
                if ok: return self._fire(res, ticks, "level", f"level (pitch {att.pitch:+.1f} roll {att.roll:+.1f}, S {s:.0f}): EJECT")
                armed = False; res.log.append(f"disarmed: pitch {att.pitch:+.1f} roll {att.roll:+.1f}")
            elif ok and rate_ok_next(att, rate, self.wall() - now + self.TICK_S, self.LEVEL_DEG):
                armed = True; res.log.append(f"armed: pitch {att.pitch:+.1f} roll {att.roll:+.1f}, vs {vs:+.0f} ft/s, S {s:.0f}")
                self.sleep(self.TICK_S); continue
            if st.alt_ft < self.MIN_ALT_FT or (vs < 0 and st.alt_ft < -vs * self.GROUND_S):
                return self._fire(res, ticks, "low", f"alt {st.alt_ft:.0f} ft at {vs:+.0f} ft/s: ejecting as is (S {s:.0f})")
            if now - t0 > self.MAX_S:
                return self._fire(res, ticks, "timeout", f"no level in {self.MAX_S:.0f} s (best S {best[0]:.0f}): ejecting as is (S {s:.0f})")
            if now >= quiet_until:
                quiet_until = now + self._steer(att, rate, self.pitch_target(vs), now)
            self.sleep(self.TICK_S)

    def _steer(self, att, rate, pitch_to, now):
        """One correction. -> seconds to leave the stick alone. Priority: (1) either axis coasting onto level too fast, or
        running away from it faster than it is needed: centre the stick (zeroes both rates); (2) each axis off its wanted rate
        (P x error, capped) gets a tap toward it, roll then pitch. Pitch is steered to pitch_to (nose-up while sinking), roll to 0."""
        axes = (("roll", att.roll, rate[0], self.gain["roll"], self.ROLL_MAX_RATE, ROLL_RIGHT, ROLL_LEFT),
                ("pitch", att.pitch - pitch_to, rate[1], self.gain["pitch"], self.PITCH_MAX_RATE, NOSE_UP, NOSE_DOWN))
        for _, ang, w, *_ in axes:
            if w * ang < 0 and abs(ang) < abs(w) * self.LEAD_S:        # coasting onto 0 (w opposes the angle): stop here
                self.controls.tap(CENTER); return self.SETTLE_S
        for _, ang, w, *_ in axes:
            if w * ang > 0 and abs(w) > self.RATE_QUIET:               # rate carries it further from level
                if abs(w) > self.ROLL_MAX_RATE: self.controls.tap(CENTER); return self.SETTLE_S
        tapped = False
        for name, ang, w, gain, wmax, up_key, down_key in axes:        # both axes in the same pass: a big bank must not starve pitch
            want = max(-wmax, min(wmax, -self.P * ang))
            d = want - w
            if abs(d) < self.RATE_DEAD: continue
            # roll: positive angle = right wing down (loops.py bank sign): a positive rate = the right key;
            # pitch: positive = nose up = the "down" arrow
            hold = max(self.HOLD_MIN, min(self.HOLD_MAX, abs(d) / gain))
            self.controls.tap(up_key if d > 0 else down_key, hold)
            self.last_tap.append((name, 1 if d > 0 else -1, hold, w)); tapped = True
        return self.SETTLE_S if tapped else 0.0

    def _fire(self, res, ticks, why, text):
        self.say(f"  EJECT: {text}")
        res.log.append(text)
        self.controls.action(Action.EJECT, None, self.wall(), {})
        res.ejected, res.why, res.ticks = True, why, ticks
        return res


def rate_ok_next(att, rate, dt, tol):
    """The attitude the next read should show at the current rates is still inside tol (the 'confident' test)."""
    return abs(att.roll + rate[0] * dt) <= tol and abs(att.pitch + rate[1] * dt) <= tol
