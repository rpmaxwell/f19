"""The control loops (WP-7a), moved out of scripts/nav.py's main loop with the same gains, thresholds and timers:

  SensitivityFix   nav :651-658  keyboard sensitivity must be 1: let go of the roll key and press Ins every 1.5 s
  HeadingHold      nav :660-688  roll keys set a TURN RATE (a held key changes the steady rate ~0.28 deg/s per s held,
                                 the bank persists after release); rate caps, bank limits, stuck-key watchdog
  AltitudeHold     nav :689-734  vertical-speed hold with pitch taps: vs estimate, deadband, floor / sink guards,
                                 alt / alt_cap / alt_min / vs_max / vs_target / deadband / floor, the ALT file override
  FlapsRule        nav :735-742  slow flight: flaps down below 140 kt, up again above 180 kt (only if it lowered them)
  SpeedHold        phases.Throttle  speed hold with the throttle keys (max / idle / guard / step)

They read the tick's merged Command (the arbiter's: the route's heading / rate_cap / alt under every override) and
press keys only through Controls. Loops.step runs them in nav's order. Timers run on the injected clock: nav took
time.time() AFTER a blocking tap for last_press and the next Ins press (so the gap counted from the release); the
loops do the same with clock.now() (a WallClock reproduces it; tests advance a fake clock in the recording emulator).

Modes: nav's flags that change in flight (args.hdg, args.alt, args.auto, args.strafe); the arbiter and the runner
share one Modes (a hand-over sets hdg False and alt None).
"""
from dataclasses import dataclass, field
from pathlib import Path

from f19.core.geometry import wrap_deg as wrap
from f19.core.types import FLAPS, Action, Toggle
from f19.pilot.controls import EQUALS, MINUS, NOSE_DOWN, NOSE_UP, TAP

CLIMB_ALT = 300                       # nav: above this once, the plane counts as airborne at any height


@dataclass
class Modes:
    hdg: bool = False                 # nav --hdg: hold heading (roll keys)
    alt: float | None = None          # nav --alt: base hold altitude (None: no altitude hold); the ALT file changes it
    auto: bool = False                # nav --auto
    strafe: bool = False              # nav --strafe given


@dataclass
class LoopReport:
    """What one tick of the loops did (nav's CSV / display variables)."""
    turn: float = 0.0
    press: str = ""
    rate: float | None = None
    rate_cmd: float | None = None
    roll_evt: str = ""
    vs: float | None = None
    hold_alt: float | None = None
    sens: int | None = None
    sens_ok: bool = True
    sens_tap: bool = False            # Ins pressed this tick (nav logged its 11-column row)
    ts: float = 1.0
    airborne: bool = False
    events: list = field(default_factory=list)   # mission-log texts (without the time prefix)


# ------------------------------------------------------------------------------------------------- speed hold
class SpeedHold:
    """phases.Throttle (speed hold with the throttle keys: nudges -/= every few seconds from airspeed and its trend,
    one tap = 10 %, 5 % below 10 %). guard(): full power at once when the throttle setting reads below GUARD_THR or
    the speed below GUARD_KT, then straight down to RECOVER_THR at RECOVER_KT and back to the kept target; with
    engine damage (full power doesn't raise the reading) the guard stops pressing (capped). See phases.Throttle for
    the flights behind each number."""
    GAP, BAND = 3.0, 10.0
    GUARD_THR, GUARD_KT, GUARD_RETRY = 30, 138, 2.0
    RECOVER_KT, RECOVER_THR = 155, 60

    def __init__(self):
        self.target, self.next_ok, self.hist = None, 0.0, []
        self.guarding, self.saved, self.guard_next = False, None, 0.0
        self.capped, self.pressed_at = None, None

    def max(self, controls): controls.scan(EQUALS, True, TAP); self.target = None

    def idle(self, controls): controls.scan(MINUS, True, TAP); self.target = None

    def guard(self, controls, state, now, ts):
        """-> event text ("" when nothing done). Every tick while airborne in cruise / attack."""
        thr, spd = state.systems.throttle, state.speed_kt
        low = thr < self.GUARD_THR or spd < self.GUARD_KT
        if self.pressed_at is not None and now >= self.pressed_at + self.GUARD_RETRY / ts:
            if thr < 95 and self.capped is None:
                self.capped = thr; self.pressed_at = None
                return f"throttle capped at {thr}% (engine damage: 100 - 4 x damage count) - guard stops pressing"
            self.pressed_at = None
        if low and now >= self.guard_next and self.capped is None:
            if not self.guarding: self.saved = self.target
            controls.scan(EQUALS, True, TAP)
            self.guarding, self.target, self.guard_next, self.pressed_at = True, None, now + self.GUARD_RETRY / ts, now
            return f"THR GUARD ({thr}% {spd} kt)"
        if self.guarding and not low and spd >= self.RECOVER_KT:
            taps = max(0, (thr - self.RECOVER_THR) // 10)
            for _ in range(int(taps)): controls.scan(MINUS, False, TAP)
            self.guarding, self.target, self.next_ok = False, self.saved, now + self.GAP / ts
            return f"thr guard off ({spd} kt, throttle {thr} -> {thr - 10 * taps}%)"
        if self.capped is not None and thr > self.capped: self.capped = None
        return ""

    def step(self, controls, state, now, ts, allow_up=True):
        spd = state.speed_kt
        self.hist.append((now, spd)); self.hist = [h for h in self.hist if h[0] >= now - 3.0]
        if self.guarding or self.target is None or now < self.next_ok or len(self.hist) < 3: return ""
        trend = (self.hist[-1][1] - self.hist[0][1]) / max(0.5, self.hist[-1][0] - self.hist[0][0]) / ts
        err = self.target - spd
        if err > self.BAND and trend < 1.0:
            if not allow_up: return ""
            controls.scan(EQUALS, False, TAP); evt = "thr+"
        elif err < -self.BAND and trend > -1.0: controls.scan(MINUS, False, TAP); evt = "thr-"
        else: return ""
        self.next_ok = now + self.GAP / ts; return evt


# ------------------------------------------------------------------------------------------------ heading hold
class HeadingHold:
    """nav's heading hold (sensitivity level 1, measured 2026-10-09: a held roll key changes the steady turn rate by
    ~0.28 deg/s per second held). Turn-rate cap 0.8 deg/s (EMV ~10 vs ~18 at 1.5; the planner's TURN_RATE), 2.5 on
    loiter legs (FlyLeg's rate_cap); bank limits 20 deg below 500 ft, 30 above (2026-10-23 fighter-evasion crash), a
    missile evasion's bank_max only above 500 ft."""
    H_GAIN, H_RATE_MAX, H_PER_S = 0.1, 0.8, 0.28
    H_DEAD, H_MIN, H_MAX, H_SETTLE = 0.25, 0.6, 2.5, 2.5
    BANK_LIMIT_LOW, BANK_LIMIT, BANK_LOW_ALT = 20.0, 30.0, 500
    UNSTICK_RATE = 2.5

    def __init__(self):
        self.roll = dict(key=None, release=0.0, next_ok=0.0)
        self.hist = []

    @property
    def key(self): return self.roll["key"]

    def let_go(self, controls):
        """Release the roll key now (sensitivity fix, a hand-over)."""
        if self.roll["key"]: controls.up(self.roll["key"]); self.roll["key"] = None

    def roll_rate(self, now):
        pts = [(t, h) for t, h in self.hist if t >= now - 2.0]
        if len(pts) < 4 or pts[-1][0] - pts[0][0] < 1.0: return None
        t0, h0 = pts[0]; dh = sum(wrap(b[1] - a[1]) for a, b in zip(pts, pts[1:]))
        return dh / (pts[-1][0] - t0)

    def step(self, controls, now, state, cmd, turn, ts, bank, airborne, sens_ok):
        """-> (rate, rate_cmd, roll_evt). bank: the roll angle (nav: only with --auto, else None)."""
        roll, alt, roll_evt = self.roll, state.alt_ft, ""
        self.hist.append((now, state.heading)); self.hist[:] = [h for h in self.hist if h[0] >= now - 3.0]
        if roll["key"] and now >= roll["release"]:
            controls.up(roll["key"]); roll["key"] = None; roll["next_ok"] = now + max(1.2, self.H_SETTLE / ts)
        rate = self.roll_rate(now); rate = None if rate is None else rate / ts
        cap = cmd.rate_cap if cmd.rate_cap is not None else self.H_RATE_MAX
        rate_cmd = max(-cap, min(cap, self.H_GAIN * turn))
        # stuck-key watchdog: a turn this fast with no roll key held by us means the game still sees one held (a lost
        # break code; 2026-10-17 spiral): re-send both releases
        if rate is not None and abs(rate) > self.UNSTICK_RATE and roll["key"] is None and airborne:
            controls.up("left"); controls.up("right"); roll_evt = "UNSTICK"
        roll_hold = cmd.roll_hold is not False
        if not roll_hold and roll["key"]: controls.up(roll["key"]); roll["key"] = None
        blim = self.BANK_LIMIT_LOW if alt < self.BANK_LOW_ALT else self.BANK_LIMIT
        if cmd.bank_max is not None and alt >= self.BANK_LOW_ALT: blim = max(blim, cmd.bank_max)
        deeper = None if bank is None else ("left" if bank < 0 else "right")
        if bank is not None and airborne and roll_hold and abs(bank) > blim:
            if roll["key"] == deeper: controls.up(roll["key"]); roll["key"] = None; roll_evt = f"BANK LIMIT {bank:+.0f}"
            if roll["key"] is None and abs(bank) > blim + 5 and now >= roll.get("bank_next", 0.0):
                k = "right" if bank < 0 else "left"
                controls.down(k); roll.update(key=k, release=now + 0.8 / ts, bank_next=now + 1.5 / ts)
                roll_evt = f"BANK {bank:+.0f} -> {k}"
        if sens_ok and airborne and roll_hold and rate is not None and roll["key"] is None and now >= roll["next_ok"]:
            err = rate_cmd - rate
            if abs(err) > self.H_DEAD:
                k = "right" if err > 0 else "left"; hold = max(0.5, max(self.H_MIN, min(self.H_MAX, abs(err) / self.H_PER_S)) / ts)
                if bank is not None and k == deeper and abs(bank) > 0.8 * blim:
                    roll_evt = f"bank {bank:+.0f}: no deeper"
                else:
                    controls.down(k); roll.update(key=k, release=now + hold); roll_evt = f"{k} {hold:.1f}s"
        return rate, rate_cmd, roll_evt


# ----------------------------------------------------------------------------------------------- altitude hold
class AltitudeHold:
    """nav's altitude hold (alt_hold_mem.py tuning): a vertical-speed target KP x (hold - alt) within +-VS_MAX, pitch
    taps of PRESS s at least MIN_GAP (x speed / 200) apart, outside a deadband. Guards: below the floor or a predicted
    sink under it -> 0.6 s nose-up ("UP!"); a fast sink -> "SINK!"; near the stall high up and not sinking, no pull."""
    KP, VS_MAX, DEADBAND, PRESS, MIN_GAP, FLOOR = 0.25, 15.0, 6.0, 0.5, 1.2, 250
    STALL_KT = 140
    LOW_ALT, SINK_LOOK_S, SINK_LOW_VS, NO_PUSH_BANK = 500, 8.0, -15.0, 15.0
    SLOW_HOLD_ALT = 400

    def __init__(self, alt_file=None):
        self.alt_file = None if alt_file is None else Path(alt_file)
        self.hist, self.last_press = [], 0.0

    def read_alt_file(self, base):
        """nav: a number (ft) written to logs/nav/ALT changes the hold mid-flight (only while a hold is on)."""
        if base is not None and self.alt_file is not None and self.alt_file.exists():
            try: return float(self.alt_file.read_text().split()[0])
            except (ValueError, IndexError, OSError): pass
        return base

    @staticmethod
    def hold_target(cmd, base):
        """nav's hold_alt: the leg's alt_ft (FlyLeg's Command.alt) or the base, then the overrides alt / alt_cap /
        alt_min. In the merged Command `alt` is the top override's alt or the leg's; None base = no hold at all."""
        if base is None: return None
        h = cmd.alt if cmd.alt is not None else base
        if cmd.alt_cap is not None: h = min(h, cmd.alt_cap)
        if cmd.alt_min is not None: h = max(h, cmd.alt_min)
        return h

    def step(self, controls, clock, now, state, cmd, hold_alt, phase, ts, banked_roll, airborne, sens_ok):
        """-> (press, vs). banked_roll: the roll angle when known (nav: with --auto) else None."""
        alt, spd = state.alt_ft, state.speed_kt
        vs_max = cmd.vs_max if cmd.vs_max is not None else self.VS_MAX
        press, vs = "", None
        if hold_alt is None or cmd.alt_hold is False: return press, vs
        # vertical speed over the last ~1.5 s; no estimate -> no press (2026-10-17: a stale 0 flew into the ground)
        self.hist.append((now, alt)); self.hist = [h for h in self.hist if h[0] >= now - 1.5]
        span = self.hist[-1][0] - self.hist[0][0]
        vs = (self.hist[-1][1] - self.hist[0][1]) / span / ts if len(self.hist) >= 2 and span >= 0.4 else None
        # descents near the ground at most 8 ft/s (a 15 ft/s descent at 340 kt overshot to -40 ft/s, 2026-10-18)
        dn = min(vs_max, 8.0) if alt < 700 and cmd.vs_max is None else vs_max
        tvs = max(-dn, min(vs_max, self.KP * (hold_alt - alt)))
        if cmd.vs_target is not None: tvs = cmd.vs_target
        dband = cmd.deadband if cmd.deadband is not None else self.DEADBAND
        gap = self.MIN_GAP * max(1.0, spd / 200.0)
        slow = phase in ("route", "attack", "strafe") and spd < self.STALL_KT and alt > self.SLOW_HOLD_ALT and (vs is None or vs > -5)
        if airborne and sens_ok and vs is not None and slow:
            press = "slow: no pull"
        elif airborne and sens_ok and vs is not None:
            floor = cmd.floor if cmd.floor is not None else max(80, min(self.FLOOR, hold_alt - 100))
            look = self.SINK_LOOK_S if alt < self.LOW_ALT else 5.0
            predict = cmd.floor is None and alt + look * vs < floor
            banked = banked_roll is not None and abs(banked_roll) > self.NO_PUSH_BANK
            if (alt < floor or predict) and vs < 5 and now - self.last_press >= 0.8:
                controls.tap(NOSE_UP, 0.6); press = "UP!"
            elif vs < -40 and alt < 1000 + 15 * -vs and now - self.last_press >= 0.8:
                controls.tap(NOSE_UP, 0.6); press = "SINK!"
            elif cmd.floor is None and alt < self.LOW_ALT and vs < self.SINK_LOW_VS and now - self.last_press >= 0.8:
                controls.tap(NOSE_UP, 0.6); press = "SINK!"
            elif now - self.last_press >= gap:
                if vs < tvs - dband * ts: controls.tap(NOSE_UP, self.PRESS); press = "up"
                elif vs > tvs + dband * ts and not (phase == "takeoff" and alt < CLIMB_ALT) and not banked:
                    controls.tap(NOSE_DOWN, self.PRESS); press = "down"
        if press and press != "slow: no pull": self.last_press = clock.now()
        return press, vs


# --------------------------------------------------------------------------------------------------- flaps rule
class FlapsRule:
    """Slow flight (engine damage, a sag): flaps down cut the stall from ~120 to ~90 kt; up again once fast - only the
    flaps this rule lowered."""
    FLAPS_SLOW_KT, FLAPS_UP_KT = 140, 180

    def __init__(self): self.slow_flaps = False

    def step(self, controls, state, now, toggled):
        spd, flags = state.speed_kt, state.systems.flags
        if spd < self.FLAPS_SLOW_KT and not flags & FLAPS:
            if controls.toggle(Toggle(Action.FLAPS_TOGGLE, True), state, now, toggled) and not self.slow_flaps:
                self.slow_flaps = True; return f"slow ({spd} kt): flaps down"
        elif self.slow_flaps and spd > self.FLAPS_UP_KT and flags & FLAPS:
            if controls.toggle(Toggle(Action.FLAPS_TOGGLE, False), state, now, toggled):
                self.slow_flaps = False; return f"flaps up ({spd} kt)"
        return ""


# --------------------------------------------------------------------------------------------- sensitivity fix
class SensitivityFix:
    """Keyboard sensitivity must be level 1 (all tuning assumes it; level 3 crashed the plane 2026-10-14). If it
    isn't: let go of the roll key and press Ins (0.2 s: longer auto-repeats two steps) every 1.5 s until it is."""
    GAP_S = 1.5

    def __init__(self): self.next = 0.0

    def step(self, controls, clock, now, sens):
        """-> True when Ins was pressed."""
        if now >= self.next:
            controls.tap("ins", TAP); self.next = clock.now() + self.GAP_S
            return True
        return False


# ---------------------------------------------------------------------------------------------------- all loops
class Loops:
    """The loops in nav's order: sensitivity fix, heading hold, altitude hold, flaps rule + throttle guard, speed
    hold. Loops(clock, modes, alt_file=None, speed=None); speed: the SpeedHold Controls shares."""

    def __init__(self, clock, modes, alt_file=None, speed=None):
        self.clock, self.modes = clock, modes
        self.sens = SensitivityFix()
        self.heading = HeadingHold()
        self.altitude = AltitudeHold(alt_file)
        self.flaps = FlapsRule()
        self.speed = speed if speed is not None else SpeedHold()
        self.climbed = False

    def airborne(self, state):
        """nav: never act on the ground (takeoff roll included), but once above CLIMB_ALT stay in control at any
        height (the old alt > 150 test let go below 150 ft and the 2026-10-16 flight sank into the ground)."""
        self.climbed = self.climbed or state.alt_ft > CLIMB_ALT
        return self.climbed or state.alt_ft > 150

    def let_go(self, controls): self.heading.let_go(controls)

    def step(self, controls, state, cmd, ctx, phase, takeoff_active=False, now=None):
        """One tick. cmd: the merged Command (heading = the final want). phase: nav's phase string (takeoff, route,
        attack, strafe, landing, landed, handover). takeoff_active: a Takeoff phase is running (nav: tko not finished)."""
        m = self.modes
        now = ctx.now() if now is None else now
        rep = LoopReport()
        want = cmd.heading if cmd.heading is not None else state.heading
        rep.turn = turn = wrap(want - state.heading)
        rep.airborne = airborne = self.airborne(state)
        rep.sens = sens = state.sensitivity
        rep.sens_ok = sens_ok = sens == 1
        if not sens_ok and (m.hdg or m.alt is not None):
            self.heading.let_go(controls)
            rep.sens_tap = self.sens.step(controls, self.clock, now, sens)
        rep.ts = ts = state.ts
        bank = state.att.roll if m.auto else None
        if m.hdg:
            rep.rate, rep.rate_cmd, rep.roll_evt = self.heading.step(controls, now, state, cmd, turn, ts, bank, airborne, sens_ok)
        m.alt = self.altitude.read_alt_file(m.alt)
        rep.hold_alt = hold_alt = AltitudeHold.hold_target(cmd, m.alt)
        rep.press, rep.vs = self.altitude.step(controls, self.clock, now, state, cmd, hold_alt, phase, ts, bank, airborne, sens_ok)
        if m.auto and airborne and phase in ("route", "attack", "strafe") and not takeoff_active:
            ev = self.flaps.step(controls, state, now, ctx.toggled)
            if ev: rep.events.append(ev)
            g = self.speed.guard(controls, state, now, ts)
            if g: rep.press = (rep.press + " " + g).strip(); rep.events.append(g)
        # no throttle presses while an attack holds the nose (a press blocks ~0.6 s: the pop overshot to +23.5)
        if m.auto and airborne and phase not in ("landed", "handover") and cmd.alt_hold is not False:
            thr_evt = self.speed.step(controls, state, now, ts, allow_up=not cmd.no_thr_up)
            rep.press = (rep.press + " " + thr_evt).strip()
        return rep
