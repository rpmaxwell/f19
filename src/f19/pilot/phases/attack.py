"""Attack (scripts/phases.py Attack) on step(state, ctx) -> Command.

One target: run-in -> weapon + target selected -> launch / photo -> back to cruise.

Prep: weapon station (Space), trackcam air-to-ground (F2), trackcam on ("/"), B until the briefing record is locked.
mode "maverick": steer straight at the target, holding cruise altitude; inside 14 km and 10 deg of the nose open the
bay, fire (Enter until the Maverick count drops, 3 tries), close the bay. Level, no pop-up (2026-10-18). mode "photo":
straight at the target, bay open over the last ~3 km, one frame in the game's photo window, close the bay after.
mode "overfly" (bombs / supply): cue only, the autopilot doesn't release.

What used to be keys / reads:
  F2 / Space / "/" / B / Enter        Action.TRACKCAM_MODE / NEXT_WEAPON / TRACKCAM_VIEW / SELECT_TARGET / FIRE
  "8" bay, "9" flaps                  Toggle requests (the photo run's bay re-press: settle_s BAY_RETRY_S)
  read_events / read_trackcam         state.events / state.systems.trackcam
  read_projectiles / read_target      state.projectiles / state.target_records (the reader must include `target_reads`)
  .log / .key                         Command.events (the lines added this tick; .log keeps all) / .key (this tick)
The bay-press count of the photo run (banner, log) predicts Controls' debounce from ctx.toggled (tap_due).
"""
import math

from f19.core.types import BAY, FLAPS, Action

from f19.pilot.phases._common import CAMERA, MAVERICK, K, bearing, command_of, game_dist, tap_due, want_toggle, wrap

AIR_GROUND = 2                                   # trackcam mode byte (f19.memory.addresses.AIR_GROUND)


class Attack:
    name, priority = "attack", 20
    # Reach: at launch the missile gets ttl = R*32*fs/(vmax+1) + 6 frames (EGAME 0x7b88-0x7bc2) = time to fly R*32 units
    # (= the HUD in-range cue, red hexagon, 16 km for the Maverick, R 32) at top speed (12). It starts at kt/76 and lofts
    # ~7 deg above the target line, so it reaches ~14.5-15.5 km and expires short beyond that: 3/3 shots from 16.9-18.9 km
    # were "Ineffective" (2026-10-18). Fire at 14 km. Inside reach it connects (seeker +-22.5 deg); then the matrix roll.
    MAV_MIN_KM, MAV_MAX_KM, MAV_SPEC = 6.0, 14.0, 0x1B
    B_MAX = 40                                         # B presses before the lock is left to the pilot
    PHOTO_BAY_KM, BAY_RETRY_S = 3.0, 1.5               # open the bay this far before the photo point; re-press every
    # Elite halves the photo window (budget (8 - 2 opp) x 64 = 128). Its camera error is |look-down - pitch + 9.74|,
    # and clean at 190 kt the nose sits ~6.4 deg up: at 250 ft NO range fits the Elite window (Veteran: 0.4-2.8 km).
    # Flaps cut the level pitch to ~3.8 deg -> window 0.7-1.3 km (photo_score; untested in flight). So at Elite the
    # photo run lowers the flaps PHOTO_FLAPS_KM before the bay opens and raises them once the bay is shut.
    PHOTO_FLAPS_OPP, PHOTO_FLAPS_KM = 3, 2.0

    def __init__(self, role, mode, target, record, launch_km, photo_km):
        self.role, self.mode, self.T, self.record = role, mode, target, record
        self.launch_km, self.photo_km = launch_km, photo_km
        self.state, self.next_ok, self.b_presses, self.shots, self.log = "prep", 0.0, 0, 0, []
        self.before = None; self.space_presses = self.f2_presses = 0; self.events0 = None; self.cam_on = False
        self.seen4 = 0; self.slot = self.gone = None
        self.key = ""                                  # the key asked for this step (nav's attack_key column)
        self.bay_presses = 0
        self.flaps_set = False
        self.locks = []                                # distinct records the game locked while B was pressed
        self.weapon = MAVERICK if mode == "maverick" else CAMERA if mode == "photo" else None
        self.done = False

    @property
    def target_reads(self): return () if self.record is None else (self.record,)

    def wants_control(self, state, ctx): return not self.done

    def target_locked(self, sy):
        return self.record is not None and sy.locked == self.record

    def photo_score(self, state, p):
        """Game photo test (units): r + (|e - pitch + 9.74| + |brg - hdg|) * 65536/360/16 < (8 - 2 opp) * 64."""
        r = game_dist(p, self.T); e = math.degrees(math.atan2(-state.alt_ft / 32, max(r, 1)))
        err = abs(e - state.att.pitch + 9.74) + abs(wrap(bearing(p, self.T) - state.heading))
        return r + err * 65536 / 360 / 16, (8 - 2 * state.opp) * 64

    def step(self, state, ctx):
        n_log = len(self.log); acts = []; self.key = ""
        out = self._step(state, ctx, acts)
        if out.get("done"): self.done = True
        return command_of(out, self.log[n_log:], acts)

    def _step(self, state, ctx, acts):
        now, sy, p, ts = ctx.now(), state.systems, state.xy, state.ts
        alt, hdg, pitch, opp = state.alt_ft, state.heading, state.att.pitch, state.opp
        events = state.events or ()
        loaded = lambda w: [n for x, n in sy.loadout if x == w]
        d = game_dist(p, self.T) / K; brg = bearing(p, self.T); off = wrap(brg - hdg)
        out = dict(want=brg, banner=f"  AUTO ATTACK {self.role.upper()} ({self.mode}): {self.state}   {d:.1f} km, {off:+.0f} deg off the nose")
        if self.mode == "overfly":
            out["banner"] += "   - release by hand (autopilot doesn't drop bombs / supplies yet)"
            if d < 0.3 or self.state == "past": self.state = "past"
            if self.state == "past" and d > 1.0: out["done"] = True
            return out
        if now < self.next_ok: return out
        if self.state == "prep" and self.events0 is None: self.events0 = len(events)
        if self.state == "prep" and sy.locked not in self.locks: self.locks.append(sy.locked)
        # Give up a target that can't be locked once it is passed (behind, or the presses are spent and it is
        # close): 2026-10-19 the lock sat on the Cottbus SA-11 site (record 6) and nav, still in prep, steered
        # back at the target for 4 minutes at 250 ft beside the site.
        if self.state == "prep" and not self.target_locked(sy) and (
                (d < 3.0 and abs(off) > 90) or (self.b_presses >= self.B_MAX and d < 2.0)):
            self.log.append(f"no lock on record {self.record} after {self.b_presses} B presses (locked {self.locks}): "
                            f"attack abandoned {d:.1f} km from the target")
            out["done"] = True; out.pop("want"); return out
        if self.state == "prep":
            # Ground targets lock only with the trackcam in air-to-ground (F2 cycles it). There, B steps through
            # the target table nearest first (Mittenwalde 2026-10-18: airbase 32, then the AirPlane 2, ...);
            # N grabs the nearest on-screen object instead (often clutter, record 0), so B it is.
            # no speed change for an attack (user, 2026-10-18): it upsets nav and adds radar exposure for nothing
            if sy.trackcam != AIR_GROUND and self.f2_presses < 4:
                acts.append(Action.TRACKCAM_MODE); self.f2_presses += 1; self.next_ok = now + 0.8; self.key = "f2"; return out
            have = [i for i, (w, n) in enumerate(sy.loadout) if w == self.weapon and n > 0]
            if not have:
                out["banner"] += f"\n  NO {'CAMERA' if self.mode == 'photo' else 'MAVERICK'} LEFT IN THE LOADOUT {[tuple(x) for x in sy.loadout]} - attack skipped"
                self.log.append("weapon not loaded / empty: skipped"); out["done"] = True; out.pop("want"); return out
            if sy.station not in have and self.space_presses < 8:   # Space cycles the 4 stations
                acts.append(Action.NEXT_WEAPON); self.space_presses += 1; self.next_ok = now + 0.8; self.key = "space"; return out
            if not self.target_locked(sy) and not self.cam_on:         # B only works with the trackcam up ("/", scancode)
                acts.append(Action.TRACKCAM_VIEW); self.cam_on = True; self.next_ok = now + 1.0; self.key = "/"; return out
            if not self.target_locked(sy) and self.b_presses < self.B_MAX:   # B cycles the ground targets (wraps)
                acts.append(Action.SELECT_TARGET); self.b_presses += 1; self.next_ok = now + 0.8; self.key = f"b{self.b_presses}"; return out
            if not self.target_locked(sy):
                out["banner"] += f"\n  TARGET NOT SELECTED (locked {sy.locked}, want record {self.record}) - select it by hand (B)"
                if self.record is not None: return out
            self.state = "track"
        if self.state == "track":
            out["banner"] += f"   HUD lock {sy.hud_lock}"
            # docs: target within 22 deg of the nose, launch inside 20 km (16-20 planned; closer is fine)
            if self.mode == "maverick" and d < self.MAV_MIN_KM:
                self.log.append(f"too close for a Maverick ({d:.1f} km < {self.MAV_MIN_KM}): not fired"); self.state = "away"; return out
            if self.mode == "maverick" and d <= self.MAV_MAX_KM and abs(off) < 10:
                # LEVEL shot at cruise altitude (the user fires Mavericks level at 450 ft; the pop-up from the planner
                # notes climbed to 2300 ft and still didn't fire, 2026-10-18): alt and heading holds stay on.
                self.state = "fire"; self.before = sum(loaded(MAVERICK)); self.tries = 0
                want_toggle(acts, state, Action.BAY_TOGGLE, True); self.next_ok = now + 0.6
            elif self.mode == "photo" and opp >= self.PHOTO_FLAPS_OPP and d <= self.photo_km + self.PHOTO_BAY_KM + self.PHOTO_FLAPS_KM \
                    and not sy.flags & FLAPS and not self.flaps_set:
                want_toggle(acts, state, Action.FLAPS_TOGGLE, True); self.flaps_set = True; self.key = "9"
                self.log.append(f"flaps down for the Elite photo window ({d:.1f} km out)")
            if self.mode == "photo" and self.flaps_set and not sy.flags & FLAPS and self.state == "track":
                want_toggle(acts, state, Action.FLAPS_TOGGLE, True)       # re-press until the flag shows them down
            if self.mode == "photo" and d <= self.photo_km + self.PHOTO_BAY_KM:
                want_toggle(acts, state, Action.BAY_TOGGLE, True, settle=self.BAY_RETRY_S); self.key = "8"; self.state = "photo"
            return out
        if self.state == "fire":
            if not sy.flags & BAY:                                    # bay must be open before Enter
                want_toggle(acts, state, Action.BAY_TOGGLE, True); self.next_ok = now + 0.6; return out
            have = sum(loaded(MAVERICK))
            if have < self.before:
                self.log.append(f"Maverick fired at {d:.1f} km, off {off:+.0f}, alt {alt}, pitch {pitch:+.1f}, HUD lock {sy.hud_lock}")
                self.state = "close"; return out
            if self.tries >= 3 or d < self.MAV_MIN_KM:
                self.log.append(f"Maverick did NOT fire after {self.tries} presses ({d:.1f} km, locked {sy.locked}, HUD lock {sy.hud_lock}, station {sy.station})")
                self.state = "close"; return out
            acts.append(Action.FIRE); self.tries += 1; self.next_ok = now + 2.0   # let the count drop: a 2nd Enter = a 2nd Maverick
            return out
        if self.state == "photo":
            score, budget = self.photo_score(state, p)
            out["banner"] += f"   photo score {score:.0f} / {budget}"
            # ONE frame well inside the window, then the bay shuts at once: an open bay is a radar beacon,
            # and the first flight's 4 frames (3.1 -> 2.2 km, all inside the window) were 3 too many.
            # A 2nd frame only if the 1st was taken outside the window (shouldn't happen at 0.8).
            if self.events0 is None: self.events0 = len(events)
            # the bay must be OPEN before Enter (2026-10-19: the one "8" tap was lost, the camera fired with the
            # bay shut and no frame was taken): re-press until the flag shows it, never fire with it shut
            if not sy.flags & BAY:
                if tap_due(ctx, Action.BAY_TOGGLE, now, self.BAY_RETRY_S): self.key = "8"; self.bay_presses += 1
                want_toggle(acts, state, Action.BAY_TOGGLE, True, settle=self.BAY_RETRY_S)
                out["banner"] += f"   BAY NOT OPEN ({self.bay_presses} presses)"
                if d < 0.1 or (score >= budget and d < self.photo_km):
                    self.log.append(f"bay never opened ({self.bay_presses} presses of 8): no frame"); self.state = "close"
                return out
            if score < 0.8 * budget and self.shots == 0:
                acts.append(Action.FIRE); self.shots += 1; self.next_ok = now + 1.0; self.key = "enter"
                self.log.append(f"camera fired at {d:.2f} km, score {score:.0f}/{budget}, bay open"); self.state = "close"
            elif d < 0.1:                                              # overflown without a frame
                self.log.append("photo point passed without a frame"); self.state = "close"
            return out
        if self.state == "close":                                      # keep at it until the flag says shut
            if not want_toggle(acts, state, Action.BAY_TOGGLE, False) and self.flaps_set and sy.flags & FLAPS:
                want_toggle(acts, state, Action.FLAPS_TOGGLE, False); return out    # Elite photo: flaps back up first
            if not want_toggle(acts, state, Action.BAY_TOGGLE, False):
                if self.mode == "photo":
                    ok = [e for e in events[self.events0 or 0:] if e[0] == 10 and e[2] == self.record]
                    self.log.append("Target photographed (event log)" if ok else "no photographed event in the log yet")
                    self.state = "away"
                else:
                    self.state, self.t_fired = "result", now
                    # our missile = a player slot (8-11) holding the Maverick spec with time to live left
                    self.slot = next((i for i, (spec, ttl) in enumerate(state.projectiles) if spec == self.MAV_SPEC and ttl), None)
            out["banner"] += "   closing bay"
            return out
        if self.state == "result":                                     # wait for the hit: kill or "Ineffective" (event 4)
            self.next_ok = now + 2.0
            # Event type 4 is the LAUNCH (logged ~at once), not the result: the log has no "Ineffective"/miss record
            # (2026-10-18: the retry read type 4 as Ineffective and wasted the 2nd Maverick). So: a kill event or
            # the destroyed bit = done; still alive after the flight time = re-shoot if a Maverick and room are left.
            ev = [e for e in events[self.events0 or 0:] if e[2] == self.record and e[0] in (1, 2, 3, 12)]
            dead = self._record(state)[2] if self.record is not None else False
            left = sum(loaded(MAVERICK))
            r = state.projectiles[self.slot] if self.slot is not None else None
            flying = r is not None and r[0] == self.MAV_SPEC and r[1]
            out["banner"] += f"   missile {'in flight' if flying else 'gone'} ({now - self.t_fired:.0f} s)"
            if flying: self.gone = None
            else: self.gone = self.gone or now
            if dead or ev:
                self.log.append(f"target destroyed ({now - self.t_fired:.0f} s after the shot)"); self.state = "away"
            elif (self.gone and now - self.gone > 2.0) or now - self.t_fired > 120 / ts:
                self.log.append(f"missile gone after {now - self.t_fired:.0f} s, target alive ({d:.1f} km), {left} Maverick(s) left")
                if left and d >= self.MAV_MIN_KM:
                    self.state = "fire"; self.before = left; self.tries = 0; want_toggle(acts, state, Action.BAY_TOGGLE, True)
                else: self.state = "away"
            return out
        if self.state == "away" and sy.flags & BAY:                   # Maverick path too: never leave it open
            want_toggle(acts, state, Action.BAY_TOGGLE, False); return out
        if self.state == "away":
            dead = self._record(state)[2] if self.record is not None else None
            if dead is not None: self.log.append(f"target record {self.record} destroyed flag: {dead}")
            out["done"] = True; out.pop("want")
        return out

    def _record(self, state):
        r = state.target_record(self.record)
        if r is None: raise KeyError(f"target record {self.record} not in the GameState (read target_reads)")
        return r
