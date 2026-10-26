"""GunAttack (scripts/phases.py GunAttack) on step(state, ctx) -> Command.

Strafe one radar site with the gun: run-in -> dive -> pull -> check, then extend and come back for another pass until
it's dead, the gun is empty or MAX_PASSES are flown. Aiming is the flight-validated CCIP (f19.rules.weapons.ccip):
bullet velocities are whole units a frame, so the descent per frame k = round(r sin(-pitch)) picks the range and the
pitch only has to sit inside a band. The dive aims for the CENTRE of the band whose impact lands on (or just past) the
target (the Wittstock pass, 2026-10-19, sat 0.1 deg outside the band it needed and every bullet landed long). Fire only
while the predicted miss is inside half the kill radius. Cease fire and pull at CEASE_FT + 1.2 s of sink.
UNTESTED: pitch rate per held key at sensitivity 1 (PITCH_RATE) and whether a held Backspace auto-repeats through the
automation port (falls back to a fresh press every cycle).

What used to be keys / reads:
  Backspace held (key_down / key_up)  Command.gun_fire (True = held this tick); re-press -> Command.gun_repress when the
                                      rounds haven't moved for 0.8 s. Controls must ALSO re-press the trigger when the
                                      set of other held keys changed since the last tick it was held (db._held rule).
  pitch tap toward a band             Command.pitch_deg + pitch_tol (one tap this tick; this phase spaces them 0.15 s)
  nose-up held in the pull            Command.pitch_hold = "up" (held while set)
  abort() on done                     nothing: Controls releases the trigger / pitch hold when they are no longer set
  read_gun / read_target              state.systems.gun_rounds, frame_scale, gun_slots / state.target_records
"""
import math


from f19.rules.weapons import ccip, gun_kill_radius

from f19.pilot.phases._common import K, bearing, command_of, game_dist, wrap


class GunAttack:
    name, priority = "gun_attack", 20
    ENTRY_FT, PUSH_PITCH, LEAD_U = 1500, 12.0, 25      # run-in height; push where a -12 deg shot would land LEAD_U past
    CEASE_FT, CEASE_SINK_S, HARD_FLOOR = 300, 1.2, 450 # cease/pull at CEASE_FT + sink * 1.2 s (never below HARD_FLOOR)
    PULL_TO, PULL_MAX_S = 5.0, 4.0                     # hold nose-up until this pitch (or this long)
    PITCH_RATE, TAP_MIN, TAP_MAX = 6.0, 0.08, 0.5      # deg/s while a pitch key is held (UNTESTED at sens 1)
    MAX_OFF, EXTEND_KM, MAX_PASSES = 3.0, 8.0, 3       # run-in heading error allowed at the push; extension before a re-attack
    TAP_GAP_S = 0.15                                   # after a pitch tap (was time.time() + 0.15 after the blocking tap)

    def __init__(self, record, target):
        self.record, self.T = record, target
        self.state, self.passes, self.log, self.next_ok = "runin", 0, [], 0.0
        self.k = None; self.firing = False; self.fire_t = None; self.rounds_at = None; self.pulse = False
        self.hist = []; self.last_t = None; self.cycle = 0.5; self.pull_t = None
        self.run_hdg = None; self.bottom = None; self.fired = 0; self.rounds0 = None
        self.done = False

    @property
    def target_reads(self): return (self.record,)

    def wants_control(self, state, ctx): return not self.done

    # -- helpers
    def _fire(self, on, now, rounds, out):
        if on and not self.firing:
            out["gun_fire"] = True; self.firing, self.fire_t, self.rounds_at = True, now, rounds
        elif not on and self.firing:
            self.firing = False                                    # gun_fire unset: released
        elif on:
            # a re-press when the rounds haven't moved in 0.8 s of trigger (no typematic repeat at all); Controls adds
            # the re-press when another key was pressed or released since the last cycle (that stops the repeat)
            if rounds != self.rounds_at: self.rounds_at, self.fire_t = rounds, now
            elif now - self.fire_t > 0.8: self.pulse = True
            out["gun_fire"] = True
            if self.pulse: out["gun_repress"] = True

    def _pitch_to(self, state, want, tol, now, ts, out):
        err = want - state.att.pitch
        if abs(err) <= tol or now < self.next_ok: return ""
        hold = max(self.TAP_MIN, min(self.TAP_MAX, abs(err) / self.PITCH_RATE / ts))
        out["pitch_deg"], out["pitch_tol"] = want, tol; self.next_ok = now + self.TAP_GAP_S
        return f"pitch {'up' if err > 0 else 'down'} {hold:.2f}s"

    def _bands(self, state, p, fs, nslots):
        """[(k, pitch, impact, along error)] for every descent band: along error > 0 = impact past the target."""
        r = 112 // max(fs, 1); out = []; hdg, alt = state.heading, state.alt_ft
        ux, uy = math.sin(math.radians(hdg)), -math.cos(math.radians(hdg))
        for k in range(1, int(r * 0.6) + 1):                       # up to ~-37 deg
            pit = -math.degrees(math.asin(k / r))
            c = ccip(p[0], p[1], alt, pit, hdg, fs, nslots)
            if c: out.append((k, pit, c, (c[0] - self.T[0]) * ux + (c[1] - self.T[1]) * uy))
        return out

    def step(self, state, ctx):
        n_log = len(self.log)
        out = self._step(state, ctx)
        if out.get("done"): self.done = True
        return command_of(out, self.log[n_log:])

    def _step(self, state, ctx):
        now, ts, p = ctx.now(), state.ts, state.xy
        alt, hdg, pitch, opp = state.alt_ft, state.heading, state.att.pitch, state.opp
        rounds, fs, nslots = state.systems.gun_rounds, state.frame_scale, state.gun_slots
        if self.rounds0 is None: self.rounds0 = rounds
        if self.last_t is not None: self.cycle = 0.7 * self.cycle + 0.3 * (now - self.last_t)
        self.last_t = now
        self.hist.append((now, alt)); self.hist = [h for h in self.hist if h[0] >= now - 1.0]
        sink = max(0.0, -(self.hist[-1][1] - self.hist[0][1]) / max(0.3, self.hist[-1][0] - self.hist[0][0]) / ts)
        d = game_dist(p, self.T); brg = bearing(p, self.T); off = wrap(brg - hdg)
        radius = gun_kill_radius(opp)                              # gun v 4 vs SAM radar: kill if dist < 24/(opp+2)
        fire_miss = max(1.5, radius / 2)
        c = ccip(p[0], p[1], alt, pitch, hdg, fs, nslots)
        miss = game_dist(c, self.T) if c else None
        out = dict(want=brg, banner=f"  AUTO GUN record {self.record}: {self.state}   pass {self.passes + 1}/{self.MAX_PASSES}"
                   f"   {d / K:.2f} km, {off:+.1f} deg off   rounds {rounds}   CCIP miss {'-' if miss is None else f'{miss:.1f}'} u (fire < {fire_miss:.1f})")
        if self.state == "runin":
            out.update(alt=self.ENTRY_FT, vs_max=20.0, rate_cap=0.8)
            if rounds < 40 // max(fs, 1):
                self.log.append("gun empty"); out["done"] = True; out.pop("want"); return out
            r = 112 // max(fs, 1); k0 = round(r * math.sin(math.radians(self.PUSH_PITCH)))
            c0 = ccip(p[0], p[1], alt, -math.degrees(math.asin(k0 / r)), hdg, fs, nslots)
            push_at = (game_dist(p, c0) if c0 else 0) + self.LEAD_U
            out["banner"] += f"   push at {push_at / K:.2f} km"
            if d <= push_at:
                if abs(off) > self.MAX_OFF or alt < self.ENTRY_FT - 300:
                    self.log.append(f"pass {self.passes + 1}: not set up at the push ({off:+.1f} deg off, {alt} ft) - extending")
                    self.state, self.run_hdg = "extend", hdg
                else:
                    self.state, self.k, self.bottom, self.fired = "dive", None, None, rounds
            return out
        if self.state == "dive":
            out.update(alt_hold=False, rate_cap=0.4)
            cease = max(self.HARD_FLOOR, self.CEASE_FT + self.CEASE_SINK_S * sink)
            bands = self._bands(state, p, fs, nslots)
            ok = [b for b in bands if b[3] >= -2]                      # long or on: the burst walks back through it
            low = alt - sink * (self.cycle + 0.2) <= cease
            if low or not ok:                                          # too low, or so close that every band lands short
                self._fire(False, now, rounds, out); out["pitch_hold"] = "up"; self.pull_t = now; self.state = "pull"
                self.log.append(f"pass {self.passes + 1}: pull ({'cease altitude' if low else 'too close'}) at {alt} ft,"
                                f" pitch {pitch:+.1f}, sink {sink:.0f} ft/s, {(self.fired - rounds) // max(1, 40 // max(fs, 1))} bullets")
                return out
            best = min(ok, key=lambda b: b[3])
            cur = next((b for b in bands if b[0] == self.k), None)
            if best and (cur is None or abs(cur[3]) > radius and abs(best[3]) < abs(cur[3]) - 3): self.k = best[0]; cur = best
            if cur:
                r = 112 // max(fs, 1); half = math.degrees(0.5 / r)       # half a band, deg
                out["banner"] += f"   band k={cur[0]} pitch {cur[1]:+.1f} (now {pitch:+.1f})  impact {cur[3]:+.0f} u along"
                # pitch moves in ~1.58 deg steps (hand-flown logs: -7.80 -9.38 -10.95 -12.53), so +-0.8 is the best
                # reachable; bands are +-1.0 (fs 4) .. +-2 deg (fs 8) wide, so that's still inside
                tol = max(0.8, 0.6 * half)
                if abs(cur[1] - pitch) > tol and now >= self.next_ok:
                    self._fire(False, now, rounds, out)                 # no bullets while the nose moves
                    out["banner"] += "   " + self._pitch_to(state, cur[1], tol, now, ts, out); return out
            out["roll_hold"] = not self.firing                         # roll keys would end the trigger's repeat
            self._fire(miss is not None and miss < fire_miss, now, rounds, out)
            if self.firing: out["banner"] += "   FIRING"
            return out
        if self.state == "pull":
            out.update(alt_hold=False, roll_hold=False)
            self.bottom = alt if self.bottom is None else min(self.bottom, alt)
            if pitch >= self.PULL_TO or now - self.pull_t > self.PULL_MAX_S / ts:
                self.state, self.next_ok = "check", now + 1.5            # pitch_hold unset: nose-up released
            else: out["pitch_hold"] = "up"
            return out
        if self.state == "check":
            out.update(alt=self.ENTRY_FT, vs_max=20.0)
            self.bottom = min(self.bottom, alt)
            if now < self.next_ok: return out                          # let the last bullets land
            self.passes += 1; dead = self._record(state)[2]
            self.log.append(f"pass {self.passes}: bottom {self.bottom} ft, {self.rounds0 - rounds} rounds used so far, "
                            f"target {'DESTROYED' if dead else 'alive'}")
            if dead or self.passes >= self.MAX_PASSES or rounds < 40 // max(fs, 1):
                out["done"] = True; out.pop("want"); return out
            self.state, self.run_hdg = "extend", hdg
            return out
        if self.state == "extend":                                     # straight on past it, then turn back
            out.update(want=self.run_hdg, alt=self.ENTRY_FT, vs_max=20.0)
            if d > self.EXTEND_KM * K and abs(wrap(brg - hdg)) > 90: self.state = "turn"
            return out
        if self.state == "turn":
            out.update(alt=self.ENTRY_FT, vs_max=20.0, rate_cap=1.6)
            if abs(off) < self.MAX_OFF: self.state = "runin"
            return out
        return out

    def _record(self, state):
        r = state.target_record(self.record)
        if r is None: raise KeyError(f"target record {self.record} not in the GameState (read target_reads)")
        return r
