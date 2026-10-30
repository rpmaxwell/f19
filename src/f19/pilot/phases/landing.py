"""Landing (scripts/phases.py Landing) on step(state, ctx) -> Command.

Approach fix -> Final -> touchdown -> stopped, on the planner's N-S axis. Runway box (docs/game/landing-takeoff.md):
within +-5 units (+-78 m) of the base record's X and +-64 units (+-1 km) along Y. Touchdown limits (Realistic):
kt*sin(glide) <= 19, bank <= 16.9 deg, gear down. Aims AIM_UNITS short of the base record, glides down ALT_PER_KM,
flares to FLARE_VS, then idle + brakes (150 kt stops in ~0.84 km). Goes around (max power, climb, hand back) if it
isn't down by the base record or is more than GA_XTRACK units off the centreline low down.

What used to be keys: gear "6" / flaps "9" / brakes "0" -> Toggle requests; thr.idle / thr.max -> THROTTLE_IDLE /
THROTTLE_MAX (speed hold off); thr.target = APPROACH_KT / FINAL_KT -> Command.speed_kt (every approach / final tick).
"""
import math

from f19.core.types import FLAPS, GEAR_UP, Action

from f19.pilot.phases._common import K, SpeedIntent, bearing, command_of, want_toggle, wrap


class Landing:
    name, priority = "landing", 30
    # First automatic landing (2026-10-18): touched down 26 units past the base record at 149 kt after floating at ~7 ft,
    # stopped 5 units short of the box end. So: aim 48 units short (box starts 64 short), 135 kt, shallower float.
    APPROACH_KT, FINAL_KT, ALT_PER_KM, AIM_UNITS = 170, 135, 60.0, 48
    # flaps + final speed at 11 km (was 7): lowering them on the glide ballooned the plane to 544 ft at 6 km and it reached
    # the aim point at 120 ft -> go-around (2026-10-18)
    GEAR_KM, FLAPS_KM, FLARE_ALT, FLARE_VS, GA_XTRACK = 12.0, 11.0, 25, 4.0, 8
    # Power to idle below IDLE_ALT (the float came from the speed hold adding power). 15 -> 30 ft (2026-10-30, Leck): at
    # 143 kt and 50 % power the plane floated level at 22-24 ft past the aim point and never reached 15 ft.
    # GA_PAST_UNITS: go around when more than this many units past the aim point and still above 20 ft (was AIM_UNITS = 48,
    # i.e. at the base record; the runway box runs another 64 units past it, 2026-10-30).
    IDLE_ALT, GA_PAST_UNITS = 30, 80
    # 2026-10-30, second Leck landing (348 kt, 765 ft, 19 km out): flaps down at 340 kt ballooned the plane ~120 ft, the gear would not
    # come down until ~260 kt (6 km out) and the speed hold took 100 s to bleed 348 kt, so the glide started ~190 ft high and ended
    # in a steep dive (21 ft at -19 ft/s, 140 ft over the glide at the aim point). So: BLEED_KT: idle the engine while more than
    # this above the target speed (resume the hold within RESUME_KT of it); HIGH_FT / HIGH_KM: go around when still above
    # HIGH_FT with HIGH_KM to the aim point left; IDLE_SINK_MAX: idle at IDLE_ALT only when sinking slower than this (ft/s),
    # else keep power down to LOW_IDLE_ALT. None switches each off.
    # BLEED_KT is OFF (None): the first live try (2026-10-30, 252 kt at 11.6 km) cut the throttle to 0 and the speed hold, which adds power in
    # small steps with pauses, only began adding it at 92 kt: stall, -90 ft/s, the plane hit the ground 3 km out. Do not switch it on
    # without a power floor / a direct throttle command on the way back (and a test in the air at altitude).
    BLEED_KT, RESUME_KT, HIGH_FT, HIGH_KM, IDLE_SINK_MAX, LOW_IDLE_ALT = None, 12, 160, 1.0, 10.0, 15
    LOG_GO_AROUND = True                           # one event line with the reason (the old phases logged none)
    # Short final (2026-10-20 go-around): below GLIDE_ALT the hold flies a SINK RATE aimed at the touchdown point
    # (height / time to the aim point, at least MIN_SINK) with a GLIDE_DEADBAND ft/s deadband. And no power added below
    # GLIDE_ALT unless slower than NO_THR_UP_KT.
    GLIDE_ALT, MIN_SINK, GLIDE_DEADBAND, NO_THR_UP_KT = 150, 1.5, 2.0, 112
    K_XT = 0.5
    # Lead on the drift: steer on where the plane will be LEAD_S game s from now (xt + LEAD_S x drift rate).
    LEAD_S = 15.0

    def __init__(self, base, approach_fix):
        self.base, self.state = base, "approach"
        self.rwy = bearing(approach_fix, base)                   # heading flown on final (0 or 180)
        self.s = 1 if abs(wrap(self.rwy)) < 90 else -1           # +1: landing northbound (approach from y larger)
        self.aim = (base[0], base[1] + self.s * self.AIM_UNITS)
        self.idled, self.bleeding, self.hist = False, False, []
        self.done = False

    def wants_control(self, state, ctx): return not self.done

    def step(self, state, ctx):
        f, p, alt, spd, hdg = state.systems, state.xy, state.alt_ft, state.speed_kt, state.heading
        acts, events, thr = [], [], SpeedIntent(ctx)
        to_aim = (p[1] - self.aim[1]) * self.s / K               # km before the aim point (negative: past it)
        xt = p[0] - self.base[0] if self.s > 0 else self.base[0] - p[0]   # + = right of the centreline, units
        # One centreline law from intercept to touchdown: heading offset = -K * xt (units), capped 35 deg far out and
        # 10 deg inside 4 km (K = 0.5 deg/unit keeps the lateral loop ~4x slower than the press-based heading hold).
        cap = 35.0 if to_aim > 4.0 else 10.0
        drift = spd * 0.5144 * K / 1000 * math.sin(math.radians(wrap(hdg - self.rwy)))   # units per s, + = rightward
        want = (self.rwy - max(-cap, min(cap, self.K_XT * (xt + self.LEAD_S * drift)))) % 360
        out = dict(want=want, rate_cap=1.5, floor=0)
        if self.state in ("approach", "final"):
            if to_aim < self.GEAR_KM: want_toggle(acts, state, Action.GEAR_TOGGLE, False)
            if to_aim < self.FLAPS_KM: want_toggle(acts, state, Action.FLAPS_TOGGLE, True)
            tgt = self.FINAL_KT if to_aim < self.FLAPS_KM else self.APPROACH_KT
            if self.BLEED_KT is not None and (self.bleeding or spd > tgt + self.BLEED_KT):
                if not self.bleeding: thr.idle(acts); self.bleeding = True; events.append(f"landing: {spd:.0f} kt, engine idle until {tgt + self.RESUME_KT} kt")
                elif spd <= tgt + self.RESUME_KT: self.bleeding = False; thr.set(tgt)
            else: thr.set(tgt)
            if to_aim < self.FLAPS_KM: self.state = "final"
            out["alt_cap"] = max(0.0, to_aim * self.ALT_PER_KM)   # glide path: never above it, never climb to it
            # sink limits by height: 12 ft/s above 100 ft, 6 ft/s down to the flare, 4 ft/s in the flare
            out["vs_max"] = self.FLARE_VS if alt < self.FLARE_ALT else 6.0 if alt < 100 else 12.0
            if self.state == "final" and alt < self.GLIDE_ALT:
                t_aim = max(2.0, to_aim * 1000 / max(30.0, spd * 0.5144))      # s to the aim point (2 s once past it)
                out["vs_target"] = max(-out["vs_max"], min(-self.MIN_SINK, -alt / t_aim))
                out["deadband"] = self.GLIDE_DEADBAND
                out["no_thr_up"] = spd >= self.NO_THR_UP_KT
            if alt < 60 and abs(state.att.roll) > 8 and abs(xt) <= 5: out["want"] = hdg   # aligned: wings level first
            now = ctx.now(); self.hist = [h for h in self.hist + [(now, alt)] if h[0] >= now - 1.5]
            vs = (alt - self.hist[0][1]) / (now - self.hist[0][0]) if now - self.hist[0][0] >= 0.4 else None
            sinking = self.IDLE_SINK_MAX is not None and vs is not None and vs < -self.IDLE_SINK_MAX
            if 0 < alt < (self.LOW_IDLE_ALT if sinking else self.IDLE_ALT) and not self.idled:
                thr.idle(acts); self.idled = True
            if alt == 0: thr.idle(acts); self.state = "rollout"
            elif ((to_aim < -self.GA_PAST_UNITS / K and alt > 20) or (alt < 80 and abs(xt) > self.GA_XTRACK)
                  or (self.HIGH_FT is not None and 0 < to_aim < self.HIGH_KM and alt > self.HIGH_FT)):
                thr.max(acts); self.state = "go-around"
                why = (f"{-to_aim:.2f} km past the aim point at {alt:.0f} ft" if to_aim < -self.GA_PAST_UNITS / K
                       else f"{xt:+.0f} units off the centreline at {alt:.0f} ft" if alt < 80 and abs(xt) > self.GA_XTRACK
                       else f"too high on short final: {alt:.0f} ft with {to_aim:.2f} km to the aim point")
                if self.LOG_GO_AROUND: events.append(f"landing go-around: {why}, {spd:.0f} kt")
        if self.state == "rollout":
            out.update(alt_hold=False, roll_hold=False)
            want_toggle(acts, state, Action.BRAKES_TOGGLE, True)
            if spd <= 1: self.state = "stopped"; out["done"] = True
        if self.state == "go-around":
            out.update(alt=500, vs_max=10.0); want_toggle(acts, state, Action.GEAR_TOGGLE, True)
            if alt > 400: out["done"] = True
        if out.get("done"): self.done = True
        out["banner"] = (f"  AUTO LANDING: {self.state}   aim {to_aim:5.2f} km   centreline {xt:+.0f} units (box +-5)"
                         f"   roll {state.att.roll:+.0f}   gear {'up' if f.flags & GEAR_UP else 'down'}"
                         f"   flaps {'down' if f.flags & FLAPS else 'up'}   box {f.rwy_box}")
        return command_of(out, events=events, actions=acts, speed_kt=thr.speed_kt)
