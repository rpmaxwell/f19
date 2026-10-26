"""Takeoff (scripts/phases.py Takeoff) on step(state, ctx) -> Command.

Runway start -> climbing through the cruise height on the runway heading. Brakes off, flaps down (stall 120 -> 90 kt),
max power; rotate at VR holding PITCH_LO..PITCH_HI; gear up above GEAR_ALT; flaps up above FLAPS_ALT and FLAPS_KT;
then speed hold to cruise and the altitude hold takes over pitch.

What used to be keys:
  brakes "0" / flaps "9" / gear "6"   Toggle requests in Command.actions (Controls debounces them, 3 s)
  Shift+= (Throttle.max)              Action.THROTTLE_MAX (also switches the speed hold off, as Throttle.max did)
  pitch taps 0.6 s up / 0.5 s down    Command.pitch_min / pitch_max: the band (the pitch loop taps, 1.0 / ts apart)
  thr.target = cruise_kt              Command.speed_kt (persists in the speed hold after the phase)
  nav's tko.finished                  Command.done and the `done` attribute
"""
from f19.core.types import GEAR_UP, FLAPS, Action

from f19.pilot.phases._common import SpeedIntent, command_of, want_toggle


class Takeoff:
    name, priority = "takeoff", 10
    VR, PITCH_LO, PITCH_HI, GEAR_ALT, FLAPS_ALT, FLAPS_KT = 110, 8.0, 13.0, 80, 300, 180
    LEVEL_ALT, LEVEL_PITCH = 220, (1.0, 4.0)      # nose down to a shallow climb before handing over (first flight
                                                  # handed over at +10 deg, full power: 1450 ft overshoot)

    def __init__(self, cruise_kt, cruise_ft=450):
        self.cruise_kt, self.state, self.t0 = cruise_kt, "setup", None
        # relative to the cruise height: with a 300 ft cruise the fixed 220/300 ft thresholds handed over at 570 ft and
        # ~355 kt, and the altitude hold's descent at that speed flew into the ground (2026-10-18)
        self.LEVEL_ALT = min(220, cruise_ft - 100); self.FLAPS_ALT = min(300, cruise_ft - 30)
        self.done = False

    def wants_control(self, state, ctx): return not self.done

    def step(self, state, ctx):
        now, f, alt, spd, pitch = ctx.now(), state.systems, state.alt_ft, state.speed_kt, state.att.pitch
        acts, thr = [], SpeedIntent(ctx)
        out = dict(alt_hold=False, roll_hold=alt > 50, banner=f"  AUTO TAKEOFF: {self.state}")
        if self.state == "setup":
            if want_toggle(acts, state, Action.BRAKES_TOGGLE, False) or want_toggle(acts, state, Action.FLAPS_TOGGLE, True):
                return command_of(out, actions=acts)
            thr.max(acts); self.state, self.t0 = "roll", now
        elif self.state == "roll":
            if f.throttle < 100 and now - self.t0 > 3:              # max power didn't take: press again
                thr.max(acts); self.t0 = now
            if spd >= self.VR: self.state = "rotate"
        if self.state in ("rotate", "climb"):
            # own the pitch until CLIMB_ALT: the altitude hold is off (alt_hold=False)
            out["pitch_min"], out["pitch_max"] = self.LEVEL_PITCH if alt > self.LEVEL_ALT else (self.PITCH_LO, self.PITCH_HI)
        if self.state == "rotate" and alt > 20: self.state = "climb"
        if self.state == "climb":
            if alt > self.GEAR_ALT: want_toggle(acts, state, Action.GEAR_TOGGLE, True)
            if f.flags & GEAR_UP and thr.target is None: thr.set(self.cruise_kt)   # cruise power once gear is up
            if alt > self.FLAPS_ALT and spd > self.FLAPS_KT and f.flags & FLAPS:
                want_toggle(acts, state, Action.FLAPS_TOGGLE, False); thr.set(self.cruise_kt)   # full power ran up to 305 kt
            if alt > self.FLAPS_ALT and not f.flags & FLAPS and f.flags & GEAR_UP and pitch <= self.LEVEL_PITCH[1] + 1:
                thr.set(self.cruise_kt); out["alt_hold"] = True; out["done"] = True; self.done = True
        out["banner"] = f"  AUTO TAKEOFF: {self.state}   pitch {pitch:+.1f}   gear {'up' if f.flags & GEAR_UP else 'DOWN'}" \
                        f"   flaps {'DOWN' if f.flags & FLAPS else 'up'}"
        return command_of(out, actions=acts, speed_kt=thr.speed_kt)
