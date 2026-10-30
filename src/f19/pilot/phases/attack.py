"""Attack (scripts/phases.py Attack) on step(state, ctx) -> Command.

One target: run-in -> weapon + target selected -> launch / photo -> back to cruise.

Prep: weapon station (Space), trackcam air-to-ground (F2), trackcam on ("/", verified on ds:9740), then a closed loop on
the decoded pick (rules.trackcam, TRACKCAM_LOOP): no B unless the briefing record is in the game's cone and range; "/" or B
by the shorter predicted way; the lock is read back after each key and compared with the prediction.
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
from f19.rules import trackcam as TC

from f19.pilot.phases._common import CAMERA, MAVERICK, K, bearing, command_of, game_dist, tap_due, want_toggle, wrap

AIR_GROUND = 2                                   # trackcam mode byte (f19.memory.addresses.AIR_GROUND)


class Attack:
    name, priority = "attack", 20
    # Reach: at launch the missile gets ttl = R*32*fs/(vmax+1) + 6 frames (EGAME 0x7b88-0x7bc2) = time to fly R*32 units
    # (= the HUD in-range cue, red hexagon, 16 km for the Maverick, R 32) at top speed (12). It starts at kt/76 and lofts
    # ~7 deg above the target line, so it reaches ~14.5-15.5 km and expires short beyond that: 3/3 shots from 16.9-18.9 km
    # were "Ineffective" (2026-10-18). Fire at 14 km. Inside reach it connects (seeker +-22.5 deg); then the matrix roll.
    MAV_MIN_KM, MAV_MAX_KM, MAV_SPEC = 6.0, 14.0, 0x1B
    B_MAX = 40                                         # B presses before the lock is left to the pilot (TRACKCAM_LOOP False)
    # Closed-loop target acquisition (docs/design/target_acquisition_task.md; EGAME 0xab84-0xacbc). False = the blind
    # "/" once + B x B_MAX of scripts/phases.py (the equivalence tests pin it).
    TRACKCAM_LOOP = True
    SLASH_MAX, SLASH_GAP_S, SLASH_PAUSE_S = 5, 1.0, 8.0   # "/" re-sends until ds:9740 shows the trackcam; pause between rounds
    KEY_GAP_S, SETTLE_S, RESOLVE_S = 0.5, 0.4, 1.6        # between keys; game s for the pick to run; for a -1 (searching) to resolve
    REPEAT_MAX, HOLD_S = 3, 10.0                          # the same key -> same lock this often without the record: hold HOLD_S
    FALLBACK_GAP_S = 2.0                                  # between keys once the model has been wrong MISMATCHES times
    MISMATCHES, ACQ_BUDGET_S = 2, 90.0                    # mismatches before alternating "/" and B; game s of prep before giving up
    PHOTO_BAY_KM, BAY_RETRY_S = 3.0, 1.5               # open the bay this far before the photo point; re-press every
    CONFIRM_PHOTO = True                               # keep taking frames until the event log shows the target photographed
    FRAME_RETRY_S = 1.0                                # photo: one more Enter this often until the event log shows the target photographed
    # Pull-up over the target (2026-10-29: Kolobrzeg and Katowice flew into the Tank Farm at 247-251 ft, 0.11-0.12 km out, END
    # "flying into that hill" = code 2, a solid hit while airborne; Liberec cleared one at 314 ft). Inside CLEAR_KM the photo run
    # ends whether or not the event log has shown the photo, and the plane climbs CLEAR_UP_FT over its run-in altitude until it is
    # CLEAR_PAST_KM past the target. CLEAR_PASS False = scripts/phases.py (the equivalence tests pin it).
    CLEAR_PASS = True
    CLEAR_KM, CLEAR_PAST_KM, CLEAR_UP_FT, CLEAR_VS = 1.0, 1.2, 150, 40.0
    # Elite halves the photo window (budget (8 - 2 opp) x 64 = 128). Its camera error is |look-down - pitch + 9.74|,
    # and clean at 190 kt the nose sits ~6.4 deg up: at 250 ft NO range fits the Elite window (Veteran: 0.4-2.8 km).
    # Flaps cut the level pitch to ~3.8 deg -> window 0.7-1.3 km (photo_score; untested in flight). So at Elite the
    # photo run lowers the flaps PHOTO_FLAPS_KM before the bay opens and raises them once the bay is shut.
    PHOTO_FLAPS_OPP, PHOTO_FLAPS_KM = 3, 2.0

    def __init__(self, role, mode, target, record, launch_km, photo_km):
        self.role, self.mode, self.T, self.record = role, mode, target, record
        self.launch_km, self.photo_km = launch_km, photo_km
        self.state, self.next_ok, self.b_presses, self.shots, self.log = "prep", 0.0, 0, 0, []
        self.before = None; self.space_presses = self.f2_presses = 0; self.events0 = None; self.cam_on = False; self.photographed = False
        self.away_logged, self.near, self.clear_alt = False, math.inf, None      # closest approach (km); the pull-up altitude, set when it starts
        self.seen4 = 0; self.slot = self.gone = None
        self.key = ""                                  # the key asked for this step (nav's attack_key column)
        self.bay_presses = 0
        self.flaps_set = False
        self.locks = []                                # distinct records the game locked while B was pressed
        self.slash_tries = 0; self.slash_t = -math.inf  # "/" sends of this round; when the last one went
        self.t_acq = None                              # game s prep (the closed loop) began
        self.pred = None                               # (record the last key should have locked, key, settled at)
        self.mismatches, self.fallback, self.last_key = 0, False, ""
        self.seen_b = []                               # locks the B presses produced since the last "/"
        self.trans, self.hold_until = {}, -math.inf    # (lock before, key, lock after) -> times seen; no keys before this time
        self.force_slash, self.wait_why, self.cycles = False, "", 0
        self.weapon = MAVERICK if mode == "maverick" else CAMERA if mode == "photo" else None
        self.done = False

    @property
    def target_reads(self): return () if self.record is None else (self.record,)

    @property
    def wants_cam(self):
        """Should the arbiter read the trackcam inputs (state.cam) this tick? Only while the closed loop is in prep."""
        return self.TRACKCAM_LOOP and self.state == "prep" and self.mode != "overfly" and self.record is not None

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
        if self.CLEAR_PASS and self.mode == "photo": self._clear(state, out)
        if out.get("done"): self.done = True
        return command_of(out, self.log[n_log:], acts)

    def _clear(self, state, out):
        """Photo run: hold the plane until it is CLEAR_PAST_KM past the target, and climb over it inside CLEAR_KM."""
        if self.state not in ("photo", "close", "away"): return
        d = game_dist(state.xy, self.T) / K
        self.near = min(self.near, d)
        if self.near < self.CLEAR_KM and d > self.CLEAR_PAST_KM and d > self.near: return    # past it: let go
        if out.pop("done", None): out["want"] = bearing(state.xy, self.T)                      # photographed early: stay on the run
        if self.near < self.CLEAR_KM:
            if self.clear_alt is None:
                self.clear_alt = state.alt_ft + self.CLEAR_UP_FT
                self.log.append(f"pull-up over the target at {d:.2f} km: {state.alt_ft:.0f} -> {self.clear_alt:.0f} ft")
            out.update(alt=self.clear_alt, vs_max=self.CLEAR_VS)

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
        closed = self.TRACKCAM_LOOP and state.cam is not None and self.record is not None
        spent = (self.t_acq is not None and now - self.t_acq > self.ACQ_BUDGET_S) if closed else self.b_presses >= self.B_MAX
        if self.state == "prep" and not self.target_locked(sy) and ((d < 3.0 and abs(off) > 90) or (spent and d < 2.0)):
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
            if closed and not self.target_locked(sy):
                r = self._acquire(state, now, out, acts, d, off)
                if r is not None: return r
            elif not self.target_locked(sy) and not self.cam_on:       # B only works with the trackcam up ("/", scancode)
                acts.append(Action.TRACKCAM_VIEW); self.cam_on = True; self.next_ok = now + 1.0; self.key = "/"; return out
            if not closed and not self.target_locked(sy) and self.b_presses < self.B_MAX:   # B cycles the ground targets (wraps)
                acts.append(Action.SELECT_TARGET); self.b_presses += 1; self.next_ok = now + 0.8; self.key = f"b{self.b_presses}"; return out
            if not self.target_locked(sy):                              # B_MAX presses spent (legacy) / the acquisition budget spent
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
            if self.CLEAR_PASS and d < self.CLEAR_KM:                  # out of room: end the run photographed or not, _clear pulls up
                self.log.append(f"photo run ended at {d:.2f} km after {self.shots} frame(s): " +
                                ("photographed" if self.photographed else "no photographed event yet"))
                self.state = "close"; return out
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
            # the frame needs score < budget. Fire from budget - 20 (the score then falls on for a second or two, the key
            # lags ~0.3 s) and KEEP FIRING, one Enter per FRAME_RETRY_S, until the event log says "photographed": film is
            # free and a few more seconds with the bay open cost less than missing the primary (2026-10-28 Prague: one frame
            # at score 235, not photographed, the bay shut and the attack was over; two such misses at 227 / 235 against six
            # frames at <= 216 that all counted).
            # CONFIRM_PHOTO False is the single-frame behaviour of scripts/phases.py (the equivalence tests pin it).
            if self.CONFIRM_PHOTO and any(e[0] == 10 and e[2] == self.record for e in events[self.events0 or 0:]):
                self.log.append(f"Target photographed after {self.shots} frame(s) (event log)"); self.photographed = True; self.state = "close"
                return out
            if score < min(0.92 * budget, budget - 20) and (self.shots == 0 or (self.CONFIRM_PHOTO and now >= self.next_ok)):
                acts.append(Action.FIRE); self.shots += 1; self.next_ok = now + (self.FRAME_RETRY_S if self.CONFIRM_PHOTO else 1.0); self.key = "enter"
                self.log.append(f"camera fired at {d:.2f} km, score {score:.0f}/{budget}, bay open" if self.shots == 1 else
                                f"camera frame {self.shots} at {d:.2f} km, score {score:.0f}/{budget}, not photographed yet")
                if not self.CONFIRM_PHOTO: self.state = "close"
            elif d < 0.1:                                              # overflown without a frame
                self.log.append("photo point passed without a frame" if not self.shots else f"photo point passed after {self.shots} frame(s), not photographed")
                self.state = "close"
            return out
        if self.state == "close":                                      # keep at it until the flag says shut
            if not want_toggle(acts, state, Action.BAY_TOGGLE, False) and self.flaps_set and sy.flags & FLAPS:
                want_toggle(acts, state, Action.FLAPS_TOGGLE, False); return out    # Elite photo: flaps back up first
            if not want_toggle(acts, state, Action.BAY_TOGGLE, False):
                if self.mode == "photo":
                    ok = self.photographed or any(e[0] == 10 and e[2] == self.record for e in events[self.events0 or 0:])
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
            if dead is not None and not self.away_logged:
                self.log.append(f"target record {self.record} destroyed flag: {dead}"); self.away_logged = True
            out["done"] = True; out.pop("want")
        return out

    def _acquire(self, state, now, out, acts, d, off):
        """Closed-loop prep (TRACKCAM_LOOP). Returns the step's `out` while it is still working on the lock, None when the
        budget is spent (the caller then shows TARGET NOT SELECTED). Order: "/" verified on ds:9740 and view 0 -> destroyed ->
        selectable (cone, range) or NO keys -> the previous key's result against its prediction -> the next key by
        rules.trackcam.best_plan (or alternating "/" and B after MISMATCHES wrong predictions)."""
        sy, cam = state.systems, state.cam
        mode = AIR_GROUND if sy.trackcam is None else sy.trackcam
        if self.t_acq is None: self.t_acq = now
        if now - self.t_acq > self.ACQ_BUDGET_S: return None
        banner = lambda why: out.__setitem__("banner", out["banner"] + f"\n  TRACKCAM: {why}")
        # 1. the trackcam must be up and looking ahead before any B
        if cam.screen not in TC.SCREENS or cam.view != 0:
            if self.slash_tries >= self.SLASH_MAX:
                if now - self.slash_t < self.SLASH_PAUSE_S: banner(f"\"/\" not taking (screen {cam.screen:#x}, view {cam.view:#x}): waiting"); return out
                self.slash_tries = 0
            self._slash(acts, now, f"screen {cam.screen:#x}, view {cam.view:#x}: trackcam not up")
            return out
        self.slash_tries = 0
        # 2. can the pick ever return the wanted record from here?
        rec = next(((x, y, fl) for k, x, y, fl in cam.records if k == self.record), None)
        if rec is None:
            banner(f"record {self.record} is not in the game's table ({len(cam.records)} records): no B"); return out
        ok, why = TC.selectable(self.record, state.xy, state.heading, cam.view, cam.night, rec, mode)
        if not ok:
            if why == "destroyed":
                self.log.append(f"target record {self.record} already destroyed: attack skipped"); out["done"] = True; out.pop("want", None)
                return out
            if why != self.wait_why:
                self.log.append(f"record {self.record} {why} ({d:.1f} km, {off:+.0f} deg off the nose, heading {state.heading:.0f}): no B until it is selectable")
            self.wait_why = why; self.pred = None
            banner(f"record {self.record} {why} ({off:+.0f} deg off the nose): no B presses")
            return out
        if self.wait_why:
            self.log.append(f"record {self.record} selectable again ({d:.1f} km, {off:+.0f} deg off the nose)"); self.wait_why = ""
        cands = TC.candidates(state.xy, state.heading, cam.view, cam.night, cam.records, mode)
        raw = sy.locked
        idx = TC.lock_index(raw)
        # 3. the last key: wait for the pick to run, then compare with the prediction
        if self.pred is not None:
            want, key, t_set, before = self.pred
            if now < t_set or (idx is None and now < t_set + self.RESOLVE_S):
                banner(f"after {key}: settling (lock {raw:#x}, expected {want})"); return out
            self.pred = None
            if idx != want:
                self.mismatches += 1
                self.log.append(f"MISMATCH after {key}: lock {idx}, the rule predicted {want} (#{self.mismatches}); pos {state.xy[0]:.0f},{state.xy[1]:.0f} "
                                f"hdg {state.heading:.0f} view {cam.view:#x} mode {mode} station {sy.station} screen {cam.screen:#x} "
                                f"flags {cam.flags:#x} cooldown {cam.cooldown} night {cam.night} cands {[(c.index, c.dist) for c in cands]}")
                if self.mismatches >= self.MISMATCHES and not self.fallback:
                    self.fallback = True; self.log.append("falling back to alternating / and B (closed loop on the lock)")
            n = self.trans[(before, key, idx)] = self.trans.get((before, key, idx), 0) + 1
            if n >= self.REPEAT_MAX:
                self.cycles += 1; self.trans = {}; self.seen_b = []; self.hold_until = now + self.HOLD_S
                self.log.append(f"lock {before} -> {idx} after {key} repeated {n} times without record {self.record} (cycle #{self.cycles}): "
                                f"no keys for {self.HOLD_S:g} s, steering on")
            elif key == "b" and idx is not None:
                if idx in self.seen_b:
                    self.cycles += 1; self.seen_b = []; self.force_slash = True
                    self.log.append(f"lock sequence repeated at {idx} without record {self.record} (cycle #{self.cycles}): restart with /")
                else: self.seen_b.append(idx)
        # 4. the next key
        if now < self.hold_until: banner(f"holding {self.hold_until - now:.0f} s after a repeating lock sequence"); return out
        if idx is None and self.pred is None and now < self.slash_t + self.RESOLVE_S:
            banner("waiting for the first pick after \"/\""); return out
        if idx is None:                                    # nothing locked and the search idle: "/" restarts it
            key = "/"
        elif self.force_slash: key = "/"
        elif self.fallback: key = "b" if self.last_key == "/" else "/"
        else:
            plan = TC.best_plan(self.record, idx, cands, TC.lock_floor(raw, state.xy, state.heading, cam.view, cam.records))
            if plan is None: banner(f"record {self.record} not reachable by B from lock {idx}: waiting"); return out
            key = "/" if plan.slash else "b"
        if key == "/":
            self.force_slash = False; self.seen_b = []
            self._slash(acts, now, f"lock {idx} -> {self.record}: restart from the nearest" if idx is not None else "nothing locked")
            self.pred = (cands[0].index if cands else None, "/", now + self.SETTLE_S, idx)
            if self.pred[0] is None: self.pred = None
        else:
            acts.append(Action.SELECT_TARGET); self.b_presses += 1; self.next_ok = now + self.KEY_GAP_S; self.key = f"b{self.b_presses}"
            nxt = TC.b_next(idx, cands, TC.lock_floor(raw, state.xy, state.heading, cam.view, cam.records))
            self.pred = (nxt, "b", now + self.SETTLE_S, idx) if nxt is not None else None
        self.last_key = key
        if self.fallback: self.next_ok = max(self.next_ok, now + self.FALLBACK_GAP_S)
        banner(f"key {key}, lock {idx} -> expected {self.pred[0] if self.pred else '?'}, want {self.record}")
        return out

    def _slash(self, acts, now, why):
        acts.append(Action.TRACKCAM_VIEW); self.slash_tries += 1; self.slash_t = now; self.next_ok = now + self.SLASH_GAP_S
        self.cam_on = True; self.key = "/"
        self.log.append(f'"/" sent ({self.slash_tries}): {why}')

    def _record(self, state):
        r = state.target_record(self.record)
        if r is None: raise KeyError(f"target record {self.record} not in the GameState (read target_reads)")
        return r
