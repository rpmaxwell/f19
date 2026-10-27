"""Takeoff, target attack and landing phases for nav.py --auto.

nav.py keeps flying the route (heading hold by roll keys, altitude hold by pitch taps);
a phase runs alongside it and, each cycle, returns overrides for that loop plus doing
its own discrete actions (toggles, throttle, fire). Order in a mission:

  Takeoff -> route (cruise) -> Attack primary -> route -> Attack secondary -> route -> Landing

The rules come from the EGAME disassembly (FINDINGS.md "Takeoff rules", "Weapon release",
"Photo, lock and guidance", "Landing rules"; notes/targeting_for_planner.md). None of it
is flight-tested yet: every number that needs a live check is marked UNTESTED.

Keys: nose up = "down" arrow, nose down = "up"; 6 gear, 8 bay, 9 flaps, 0 brakes (toggles);
Return fire, Space next weapon, B next target; throttle -/= and Shift+-/Shift+= (no/max
power) go in as scancodes (Dosbox.scan_tap) since the automation key table has no -/=.
"""
import math, struct, time
from f19_state import read_flight, read_projectiles, read_target, read_trackcam, read_events, AIR_GROUND, GEAR_UP, FLAPS, BAY, BRAKES
from gun_probe import ccip

K = 64.0
MAVERICK, CAMERA, EXTRA_FUEL = 5, 16, 17        # loadout weapon indices (17: memlog 1792114189, station 1 (17, 1) -> (17, 0), +1895 lb)
MINUS, EQUALS = 0x0C, 0x0D
NOSE_UP, NOSE_DOWN = "down", "up"
# Discrete keys (toggles, fire, Space, B, throttle steps) are tapped TAP s: a 0.5 s press runs into
# the keyboard's typematic repeat (500 ms delay) and the game sees two presses, so a toggle flips
# back (measured on the runway 2026-10-17; 0.2 s flips 6/8/9/0 exactly once).
TAP = 0.2
def wrap(d): return (d + 180) % 360 - 180
def bearing(a, b): return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360
def game_dist(a, b):
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1]); return max(dx, dy) + min(dx, dy) / 2


_toggled = {}
def set_toggle(db, f, bit, on, key, settle=3.0):
    """Tap a toggle key if the flag isn't already as wanted. Returns True if pressed or still
    settling: the flags word can lag a press by a cycle or two, and a second tap would flip
    the toggle back, so each key waits `settle` s before it may be pressed again."""
    if bool(f["flags"] & bit) == on: return False
    if time.time() - _toggled.get(key, 0.0) < settle: return True
    db.tap(key, TAP); _toggled[key] = time.time(); return True


class Throttle:
    """Speed hold with the throttle keys: nudges -/= every few seconds from airspeed and its trend
    (one tap = 10 %, 5 % below 10 %).

    guard(): in cruise / attack flight, full power at once when the throttle setting reads below
    GUARD_THR or the speed below GUARD_KT. 2026-10-19: one '-' tap read 60 -> 0 % (a lost break code
    left '-' auto-repeating, or Shift seen held), speed fell 200 -> 84 kt in 15 s, and the altitude
    hold's nose-up presses stalled the plane into the ground while the speed hold added 5 % per 3 s.
    The speed target is kept and resumed once the speed is back to it.

    2026-10-22 (Elite crash): the trigger (150 kt) sat just under the evasion's 165 kt, so a sag set it off; it went to
    full power and handed back at 156 kt, and with one '-' tap per 3 s the plane ran on to 286 kt and 1277 ft for
    ~40 s - in reach of a doppler SA-10 (radar ID) and of the MiG-31 / IL-76 radars (three AA-9 hits). Now: the
    trigger is GUARD_KT (below every speed nav flies on purpose), and at RECOVER_KT the throttle is stepped straight
    down to RECOVER_THR before the speed hold takes over. With engine damage the game caps the throttle at
    100 - 4 x damage count: when full power doesn't raise the reading, the guard stops pressing (capped)."""
    GAP, BAND = 3.0, 10.0
    GUARD_THR, GUARD_KT, GUARD_RETRY = 30, 138, 2.0
    RECOVER_KT, RECOVER_THR = 155, 60
    def __init__(self):
        self.target, self.next_ok, self.hist = None, 0.0, []
        self.guarding, self.saved, self.guard_next = False, None, 0.0
        self.capped, self.pressed_at = None, None
    def max(self, db): db.scan_tap(EQUALS, TAP, shift=True); self.target = None
    def idle(self, db): db.scan_tap(MINUS, TAP, shift=True); self.target = None
    def guard(self, db, s, f, now, ts):
        """-> event string ("" when nothing done). Call every cycle while airborne in cruise / attack."""
        thr = f["throttle"]
        low = thr < self.GUARD_THR or s["spd"] < self.GUARD_KT
        if self.pressed_at is not None and now >= self.pressed_at + self.GUARD_RETRY / ts:
            if thr < 95 and self.capped is None:                     # full power asked, the setting didn't follow
                self.capped = thr; self.pressed_at = None
                return f"throttle capped at {thr}% (engine damage: 100 - 4 x damage count) - guard stops pressing"
            self.pressed_at = None
        if low and now >= self.guard_next and self.capped is None:
            if not self.guarding: self.saved = self.target
            db.scan_tap(EQUALS, TAP, shift=True)                     # Shift+= : 100 %
            self.guarding, self.target, self.guard_next, self.pressed_at = True, None, now + self.GUARD_RETRY / ts, now
            return f"THR GUARD ({thr}% {s['spd']} kt)"
        if self.guarding and not low and s["spd"] >= self.RECOVER_KT:
            taps = max(0, (thr - self.RECOVER_THR) // 10)
            for _ in range(int(taps)): db.scan_tap(MINUS, TAP)       # straight down, not one tap per 3 s
            self.guarding, self.target, self.next_ok = False, self.saved, now + self.GAP / ts
            return f"thr guard off ({s['spd']} kt, throttle {thr} -> {thr - 10 * taps}%)"
        if self.capped is not None and thr > self.capped: self.capped = None   # repaired / reloaded
        return ""
    def step(self, db, s, now, ts, allow_up=True):
        self.hist.append((now, s["spd"])); self.hist = [h for h in self.hist if h[0] >= now - 3.0]
        if self.guarding or self.target is None or now < self.next_ok or len(self.hist) < 3: return ""
        trend = (self.hist[-1][1] - self.hist[0][1]) / max(0.5, self.hist[-1][0] - self.hist[0][0]) / ts   # kt/s
        err = self.target - s["spd"]
        if err > self.BAND and trend < 1.0:
            if not allow_up: return ""
            db.scan_tap(EQUALS, TAP); evt = "thr+"
        elif err < -self.BAND and trend > -1.0: db.scan_tap(MINUS, TAP); evt = "thr-"
        else: return ""
        self.next_ok = now + self.GAP / ts; return evt


class FuelTransfer:
    """Extra Fuel tanks (user rule, 2026-10-21): if and only if fuel < LOW_LB and a station holds Extra Fuel with
    count 1, cycle to that station (Space) and press Enter. The bay need not be open. Success = the fuel reading
    goes UP (+1900 lb, FINDINGS "Flight-log variables"); retried MAX_TRIES times, then that station is left alone.
    Repeats for every tank (up to 3 per mission). busy -> nav holds the attack controller off its station keys."""
    LOW_LB, MAX_TRIES, MAX_SPACE, GAP_S, VERIFY_S = 500, 4, 8, 0.8, 2.5
    def __init__(self):
        self.state, self.next_ok, self.target, self.fuel0, self.tries, self.space = "idle", 0.0, None, None, 0, 0
        self.failed, self.log, self.key = set(), [], ""
    @property
    def busy(self): return self.state != "idle"
    def step(self, db, f, now):
        """-> event text for the mission log ("" when nothing to report)."""
        if self.state == "idle":
            if f["fuel"] >= self.LOW_LB: return ""
            tanks = [i for i, (w, n) in enumerate(f["loadout"]) if w == EXTRA_FUEL and n == 1 and i not in self.failed]
            if not tanks: return ""
            self.target, self.fuel0, self.tries, self.space, self.state = tanks[0], f["fuel"], 0, 0, "select"
            return f"fuel {f['fuel']} lb < {self.LOW_LB}: transferring Extra Fuel from station {self.target}"
        if now < self.next_ok: return ""
        if self.state == "select":
            if f["station"] != self.target:
                if self.space >= self.MAX_SPACE:
                    self.failed.add(self.target); self.state = "idle"
                    return f"fuel transfer: Space never reached station {self.target} (at {f['station']}) - given up"
                db.tap("space", TAP); self.space += 1; self.key = "space"; self.next_ok = now + self.GAP_S; return ""
            db.tap("enter", TAP); self.tries += 1; self.key = "enter"; self.state = "verify"; self.next_ok = now + self.VERIFY_S
            return ""
        if self.state == "verify":
            if f["fuel"] > self.fuel0 + 500:
                self.state = "idle"
                return f"fuel transferred from station {self.target}: {self.fuel0} -> {f['fuel']} lb (try {self.tries})"
            if self.tries < self.MAX_TRIES: self.state = "select"; return ""
            self.failed.add(self.target); self.state = "idle"
            return f"fuel transfer from station {self.target} FAILED after {self.tries} Enter presses (fuel {f['fuel']} lb)"
        return ""


class FighterWatch:
    """Enemy aircraft watch and evasion, every aircraft scored with ITS OWN radar (2026-10-21).

    Decoded (radar_game.aircraft_spec, notes/aircraft_profiles.md): each aircraft type has a radar/weapon type at
    ds:49B0 + 32 x type + 0x18, and the detection loop runs radar_game.site_return with it for every slot (0x6521 /
    0x6602), checking about every 32 game s (memlog: +64 seen / -32 unseen at Veteran, +80 at Elite; > 0xC0 =
    network alert and, inside 8 km by day (4 at night), a visual ID and a shot). MiG-23 / MiG-25 radars are PULSE
    (see a side-on F-19, never head-on / going away); MiG-29 / MiG-31 / Su-27 / IL-76 are DOPPLER (the opposite, and
    blind to a slow target). IL-76 Mainstay: R200 P8, sees a 190 kt F-19 head-on / away at 43 km at Elite.
    Fighters fly ~1.6-2x the F-19's speed, the IL-76 slightly faster: running doesn't work, choosing the aspect does.

    Each cycle: THREATS = non-friendly aircraft with a radar inside their WATCH range (6 x the 190 kt Elite detection
    range is plenty; the IL-76 gets 70 km) or with an alert on us. For every candidate heading (FAN_STEP) the margin
    (return + EMV - 100) of every threat is computed now and LOOKAHEAD_S game s ahead (each threat closing at its
    type's speed); a heading is SAFE when every margin < -MARGIN. nav flies the safe heading nearest the goal (a
    mountain cell within COVER_KM if one is safe-ward - no radar gets a return there - else the route), keeps the
    current heading when it is safe (no needless bank), else the least-seen heading, at EVADE_KT (slower = less
    doppler return and EMV). Ends after CLEAR_S with no threat. Distances / times from positions in REAL s."""
    WATCH_KM_MIN, IL76_WATCH_KM, CLEAR_S, MARGIN = 25.0, 70.0, 15.0, 10.0
    FAN_STEP, LOOKAHEAD_S, RATE, EVADE_KT = 15, 30.0, 3.0, 165
    # 2026-10-22: the evasion flew on at the planner's 700 ft (+13 EMV at Elite) and flipped its heading 15 -> 195 ->
    # 180 -> 15 -> 0 -> 135 -> 315 in two minutes, never settling into an aspect. Now: down to EVADE_ALT_FT, and the
    # chosen heading is kept while it stays safe (or, with nothing safe, within KEEP_MARGIN of the best).
    EVADE_ALT_FT, KEEP_MARGIN = 250, 3.0
    COVER_KM, COVER_INSET_KM = 45.0, 4.0
    def __init__(self, terrain_type=None, clutter=None):
        """terrain_type(x, y) -> 0 mountain .. 3 sea; clutter(x, y) -> territory grid byte (radar_game clutter)."""
        import radar_game
        self.rg, self.tt, self.cl = radar_game, terrain_type, clutter
        self.specs = {t: radar_game.aircraft_spec(t) for t in range(12)}
        self.hist, self.alerts, self.state, self.clear_since, self.want, self.mode = {}, {}, "off", None, None, ""
        self.nearest = ""
    def _margin(self, a, p, hdg, kt, alt, opp, bank=0.0):
        sp = self.specs.get(a["type"], {})
        if not sp.get("radar_type"): return -999.0
        cl = self.cl(*p) if self.cl else 8
        e = self.rg.emv(int(alt), int(kt), opp, int(abs(bank) / 360 * 65536))
        return self.rg.site_return(int(p[0]), int(p[1]), int(alt), int(hdg / 360 * 65536) & 0xFFFF, int(kt), opp, int(cl),
                                   int(a["x"]), int(a["y"]), sp["radar_type"], int(a["alt"])) + e - 100
    def _ahead(self, a, p, secs):
        """The aircraft moved secs game s straight at us at its type's speed."""
        v = self.specs.get(a["type"], {}).get("max_speed_ms", 150) * secs / 1000 * K
        d = math.dist(p, (a["x"], a["y"])) or 1
        f = min(v, d - 0.5 * K) / d if d > 0.5 * K else 0
        return dict(a, x=a["x"] + (p[0] - a["x"]) * f, y=a["y"] + (p[1] - a["y"]) * f)
    def _cover(self, p):
        if self.tt is None: return None
        best = None; cx0, cy0 = int(p[0]) >> 11, int(p[1]) >> 11
        for cy in range(max(0, cy0 - 2), min(16, cy0 + 3)):
            for cx in range(max(0, cx0 - 2), min(16, cx0 + 3)):
                if self.tt((cx + 0.5) * 2048, (cy + 0.5) * 2048) != 0: continue
                ins = self.COVER_INSET_KM * K
                q = (min(max(p[0], cx * 2048 + ins), (cx + 1) * 2048 - ins), min(max(p[1], cy * 2048 + ins), (cy + 1) * 2048 - ins))
                d = math.dist(p, q) / K
                if d <= self.COVER_KM and (best is None or d < best[0]): best = (d, q, (cx, cy))
        return best
    def step(self, s, p, now, route_want, aircraft, opp=2):
        """-> (overrides, [event texts]). aircraft: f19_state.read_aircraft(); opp: read_flight()["opp"]."""
        ev, threats, near = [], [], None
        for a in aircraft:
            if a["type"] in self.rg.FRIENDLY_TYPES: continue
            sp = self.specs.get(a["type"], {}); d = math.dist(p, (a["x"], a["y"])) / K
            if near is None or d < near[0]: near = (d, a)
            if a["alert"] != self.alerts.get(a["slot"], 0):
                ev.append(f"{a['name']} (slot {a['slot']}, {'doppler' if sp.get('doppler') else 'pulse' if sp.get('radar_type') else 'no radar'}) "
                          f"alert on us {self.alerts.get(a['slot'], 0)} -> {a['alert']} at {d:.1f} km" + (" - VISUAL ID" if a["flags"] & 0x800 else ""))
                self.alerts[a["slot"]] = a["alert"]
            if not sp.get("radar_type"): continue
            watch = self.IL76_WATCH_KM if a["type"] == 5 else max(self.WATCH_KM_MIN, sp["radar_range"] * 0.3)
            if a["alert"] > 0 or d < watch: threats.append((d, a))
        self.nearest = f"{near[1]['name']} {near[0]:.1f}km a{near[1]['alert']}" if near else ""
        if not threats:
            if self.state == "off": return {}, ev
            if self.clear_since is None: self.clear_since = now
            if now - self.clear_since < self.CLEAR_S: return self._ov(), ev
            self.state, self.clear_since = "off", None
            return {}, ev + ["fighter evasion: clear, back to the route"]
        alt, kt = s["alt"], self.EVADE_KT
        ahead = [self._ahead(a, p, self.LOOKAHEAD_S) for _, a in threats]
        def worst(h, bank=0.0):
            return max(max(self._margin(a, p, h, kt, alt, opp, bank), self._margin(b, p, h, kt, alt, opp, bank))
                       for (_, a), b in zip(threats, ahead))
        inside = self.tt is not None and self.tt(*p) == 0
        cur = worst(s["hdg"])
        if cur < -self.MARGIN and (self.state == "off" or inside):
            if self.state == "off": return {}, ev                # nothing sees us on this heading: no need to act
        cover = None if inside else self._cover(p)
        goal = bearing(p, cover[1]) if cover else route_want
        fan = [(h, worst(h, 25.0)) for h in range(0, 360, self.FAN_STEP)]
        safe = [h for h, m in fan if m < -self.MARGIN]
        best_m = min(m for _, m in fan)
        prev = worst(self.want, 25.0) if self.state == "evade" and self.want is not None else None
        if prev is not None and (prev < -self.MARGIN or (not safe and prev <= best_m + self.KEEP_MARGIN)):
            want, why = self.want, self.mode or "keeping"                     # no flip-flopping while it still works
        elif cur < -self.MARGIN: want, why = s["hdg"], "holding (safe)"
        elif safe: want = min(safe, key=lambda h: abs(wrap(h - goal))); why = "to cover" if cover else "safe heading nearest the route"
        else: want = min(fan, key=lambda hm: hm[1])[0]; why = "least seen"
        if inside and self.tt is not None:                       # in a mountain cell: just stay in it
            cx, cy = int(p[0]) >> 11, int(p[1]) >> 11
            nose = (p[0] + 4 * K * math.sin(math.radians(want)), p[1] - 4 * K * math.cos(math.radians(want)))
            if (int(nose[0]) >> 11, int(nose[1]) >> 11) != (cx, cy): want, why = bearing(p, ((cx + .5) * 2048, (cy + .5) * 2048)), "staying in cover"
        d, a = min(threats, key=lambda t: t[0])
        mode = why.split(" ")[0]
        if self.state == "off" or mode != self.mode:
            names = ", ".join(f"{x['name']} {dd:.0f}km" for dd, x in sorted(threats, key=lambda t: t[0])[:4])
            ev.append(f"fighter evasion ({names}): {why}, heading {want:.0f}; worst margin now {cur:+.0f}, on the new heading "
                      f"{worst(want):+.0f}")
        self.state, self.mode, self.want = "evade", mode, want
        return self._ov(), ev
    def _ov(self):
        return dict(want=self.want, rate_cap=self.RATE, fighter=self.state, speed_kt=self.EVADE_KT, alt=self.EVADE_ALT_FT)


class TerrainEscape:
    """Terrain-ahead escape (2026-10-21 crash: first loiter turn in cell (8,10), nav fell behind the circle, drifted
    3.9 km off the route and flew into the ridge at 704 ft while its banner said MOUNTAIN AHEAD).

    probe(p, hdg, alt, max_km) -> (km, top ft) of the first terrain within MARGIN_FT of alt straight ahead (nav's
    terrain_ahead, the planner's decoded terrain) or None. Time to impact = km / ground speed (0.788 x kt).
    All times are REAL seconds, the unit nav's rates and loop run in: the game clock ran 2.4-2.6x real time in that
    flight (nav's time multiplier read 1), so the closing speed is measured from the plane's own track (units per
    real s over the last few s), with the game-physics estimate (0.788 x kt x the clock ratio) only until then.
      - inside WARN_S: TURN. Probe headings +-FAN_DEG around the nose; fly the clear one nearest to where the route
        wants to go (route_want), at ESCAPE_RATE deg/s (above the 0.8 / 1.6 caps). Mountains stay walls.
      - still pointed at terrain inside CLIMB_S (the turn isn't clearing it in time): CLIMB as the last resort -
        full power, hold top + CLIMB_ABOVE_FT at up to CLIMB_VS ft/s - and keep turning to the best heading.
    Ends after CLEAR_S with nothing inside WARN_S; the altitude goes back to the route's."""
    WARN_S, CLIMB_S, CLEAR_S = 40.0, 12.0, 4.0           # real s (at 2.5x: ~100 / 30 game s); 40 > 25 saved a 2 deg/s turn in replay
    # 100 ft: at 150 a 96 ft rise under a 220-244 ft cruise set it off 6 times on 2026-10-21 (one CLIMB to 596 ft)
    MARGIN_FT, MAX_KM, FAN_DEG, FAN_STEP = 100, 8.0, 150, 15
    # 2026-10-23: a 16 ft rise ahead of a plane sagged to 116 ft set off a full-power climb to 980 ft / 219 kt (inside
    # an SA-14 cell, by SAM sites). Now the climb is only for terrain within CLIMB_MARGIN_FT of our height, to
    # CLIMB_ABOVE_FT over it; nav adds full power only when slow.
    ESCAPE_RATE, CLIMB_ABOVE_FT, CLIMB_VS, CLIMB_MARGIN_FT = 3.0, 300, 60.0, 50
    GROUND_PER_KT = 0.788                               # map distance per game s / indicated kt (analysis/fuel_log.py)
    ASSUMED_RATIO = 2.5                                  # game s per real s until the track gives a speed
    def __init__(self, probe):
        self.probe, self.state, self.clear_since, self.climb_to, self.log = probe, "off", None, None, []
        self.track = []
    def _km_s(self, kt, p, now):
        """Ground speed, km per REAL s: from the plane's own track over the last 2-6 s if there is one."""
        self.track.append((now, p)); self.track = [q for q in self.track if q[0] >= now - 6.0]
        t0, p0 = self.track[0]
        if now - t0 >= 2.0:
            v = math.dist(p, p0) / K / (now - t0)
            if v > 0.02: return v
        return max(kt, 100) * self.GROUND_PER_KT * 1.852 / 3600 * self.ASSUMED_RATIO
    def step(self, s, p, now, route_want, ts=1.0):
        """-> (overrides for nav, event text or "")."""
        v = self._km_s(s["spd"], p, now)
        hit = self.probe(p, s["hdg"], s["alt"], self.MAX_KM)
        t_hit = hit[0] / v if hit else math.inf
        if t_hit > self.WARN_S:
            if self.state == "off": return {}, ""
            if self.clear_since is None: self.clear_since = now
            if now - self.clear_since < self.CLEAR_S / ts: return self._hold(s), ""
            self.state, self.climb_to, self.clear_since = "off", None, None
            return {}, "terrain escape: clear, back to the route"
        self.clear_since = None
        # clear distance for each heading of the fan
        best = None
        for k in range(-self.FAN_DEG // self.FAN_STEP, self.FAN_DEG // self.FAN_STEP + 1):
            h = (s["hdg"] + k * self.FAN_STEP) % 360
            r = self.probe(p, h, s["alt"], self.MAX_KM)
            clear = r[0] if r else self.MAX_KM
            # a turn takes time: |k*step| / rate s, flown on the old heading-ish; demand that much more room
            need = v * abs(k * self.FAN_STEP) / self.ESCAPE_RATE * 0.5
            ok = clear - need
            key = (ok >= v * self.WARN_S * 0.5, -abs(wrap(h - route_want)) if ok >= v * self.WARN_S * 0.5 else ok)
            if best is None or key > best[0]: best = (key, h, ok)
        ev = ""
        if self.state == "off":
            ev = f"terrain escape: {hit[1]} ft ridge {hit[0]:.1f} km ahead ({t_hit:.0f} s) at {s['alt']} ft - turning to {best[1]:.0f}"
            self.state = "turn"
        self.want = best[1]
        if t_hit < self.CLIMB_S and (hit[1] is None or hit[1] >= s["alt"] - self.CLIMB_MARGIN_FT):   # really in the way
            top = hit[1] if hit[1] is not None else s["alt"] + 1000
            target = top + self.CLIMB_ABOVE_FT
            if self.climb_to is None or target > self.climb_to:
                if self.state != "climb": ev = (ev + "; " if ev else "") + f"terrain escape: CLIMB to {target} ft (ridge {t_hit:.0f} s ahead, turn not clearing it)"
                self.climb_to = target
            self.state = "climb"
        return self._hold(s), ev
    def _hold(self, s):
        ov = dict(want=getattr(self, "want", s["hdg"]), rate_cap=self.ESCAPE_RATE, escape=self.state)
        if self.climb_to is not None:
            ov.update(alt=self.climb_to, vs_max=self.CLIMB_VS, throttle_max=True)
        return ov


class Takeoff:
    """Runway start -> climbing through CLIMB_ALT on the runway heading.
    Brakes off, flaps down (stall 120 -> 90 kt), max power; rotate at VR holding
    PITCH_LO..PITCH_HI; gear up above GEAR_ALT; flaps up above FLAPS_ALT and FLAPS_KT;
    then speed hold to cruise and nav's altitude hold takes over pitch."""
    VR, PITCH_LO, PITCH_HI, GEAR_ALT, FLAPS_ALT, FLAPS_KT = 110, 8.0, 13.0, 80, 300, 180
    LEVEL_ALT, LEVEL_PITCH = 220, (1.0, 4.0)      # nose down to a shallow climb before handing over (first flight
                                                  # handed over at +10 deg, full power: 1450 ft overshoot)
    def __init__(self, thr, cruise_kt, cruise_ft=450):
        self.thr, self.cruise_kt, self.state, self.next_ok, self.t0 = thr, cruise_kt, "setup", 0.0, None
        # relative to the cruise height: with a 300 ft cruise the fixed 220/300 ft thresholds handed over at 570 ft and
        # ~355 kt, and the altitude hold's descent at that speed flew into the ground (2026-10-18)
        self.LEVEL_ALT = min(220, cruise_ft - 100); self.FLAPS_ALT = min(300, cruise_ft - 30)
        self.finished = False
    def step(self, db, s, f, p, now, ts):
        out = dict(alt_hold=False, roll_hold=s["alt"] > 50, banner=f"  AUTO TAKEOFF: {self.state}")
        if self.state == "setup":
            if set_toggle(db, f, BRAKES, False, "0") or set_toggle(db, f, FLAPS, True, "9"): return out
            self.thr.max(db); self.state, self.t0 = "roll", now
        elif self.state == "roll":
            if f["throttle"] < 100 and now - self.t0 > 3:            # max power didn't take: press again
                self.thr.max(db); self.t0 = now
            if s["spd"] >= self.VR: self.state = "rotate"
        if self.state in ("rotate", "climb") and now >= self.next_ok:
            # own the pitch until CLIMB_ALT: nav's alt hold is off (alt_hold=False)
            lo, hi = self.LEVEL_PITCH if s["alt"] > self.LEVEL_ALT else (self.PITCH_LO, self.PITCH_HI)
            if f["pitch"] < lo: db.tap(NOSE_UP, 0.6); self.next_ok = now + 1.0 / ts
            elif f["pitch"] > hi: db.tap(NOSE_DOWN, 0.5); self.next_ok = now + 1.0 / ts
        if self.state == "rotate" and s["alt"] > 20: self.state = "climb"
        if self.state == "climb":
            if s["alt"] > self.GEAR_ALT: set_toggle(db, f, GEAR_UP, True, "6")
            if f["flags"] & GEAR_UP and self.thr.target is None: self.thr.target = self.cruise_kt   # cruise power once gear is up
            if s["alt"] > self.FLAPS_ALT and s["spd"] > self.FLAPS_KT and f["flags"] & FLAPS:
                set_toggle(db, f, FLAPS, False, "9"); self.thr.target = self.cruise_kt   # full power ran up to 305 kt
            if s["alt"] > self.FLAPS_ALT and not f["flags"] & FLAPS and f["flags"] & GEAR_UP and f["pitch"] <= self.LEVEL_PITCH[1] + 1:
                self.thr.target = self.cruise_kt; out["alt_hold"] = True; out["done"] = True
        out["banner"] = f"  AUTO TAKEOFF: {self.state}   pitch {f['pitch']:+.1f}   gear {'up' if f['flags'] & GEAR_UP else 'DOWN'}" \
                        f"   flaps {'DOWN' if f['flags'] & FLAPS else 'up'}"
        return out


class Attack:
    """One target: run-in -> weapon + target selected -> launch / photo -> back to cruise.

    Prep: weapon station (Space), trackcam air-to-ground (F2), trackcam on ("/"), B until the briefing record
    is locked. mode "maverick": steer straight at the target, holding cruise altitude; inside 20 km and 10 deg
    of the nose open the bay, fire (Enter until the Maverick count drops, 3 tries), close the bay. Level, no
    pop-up (2026-10-18). mode "photo": straight at the target, bay open over the last ~1.5 km, one frame in the
    game's photo window (notes/targeting_for_planner.md section 1), close the bay after. mode "overfly" (bombs / supply): cue only, the
    autopilot doesn't release (blast floor needs a climb the planner doesn't plan yet)."""
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
    def __init__(self, role, mode, target, record, launch_km, photo_km, thr):
        self.role, self.mode, self.T, self.record = role, mode, target, record
        self.launch_km, self.photo_km, self.thr = launch_km, photo_km, thr
        self.state, self.next_ok, self.b_presses, self.shots, self.log = "prep", 0.0, 0, 0, []
        self.before = None; self.space_presses = self.f2_presses = 0; self.events0 = None; self.cam_on = False; self.seen4 = 0; self.slot = self.gone = None
        self.key = ""                                  # the key pressed this step, for nav's log (attack_key column)
        self.bay_presses = 0
        self.flaps_set = False
        self.locks = []                                # distinct records the game locked while B was pressed
        self.weapon = MAVERICK if mode == "maverick" else CAMERA if mode == "photo" else None
    def target_locked(self, f):
        return self.record is not None and f["locked"] == self.record
    def photo_score(self, s, f, p):
        """Game photo test (units): r + (|e - pitch + 9.74| + |brg - hdg|) * 65536/360/16 < (8 - 2 opp) * 64."""
        r = game_dist(p, self.T); e = math.degrees(math.atan2(-s["alt"] / 32, max(r, 1)))
        err = abs(e - f["pitch"] + 9.74) + abs(wrap(bearing(p, self.T) - s["hdg"]))
        return r + err * 65536 / 360 / 16, (8 - 2 * f["opp"]) * 64
    def step(self, db, s, f, p, now, ts):
        d = game_dist(p, self.T) / K; brg = bearing(p, self.T); off = wrap(brg - s["hdg"])
        out = dict(want=brg, banner=f"  AUTO ATTACK {self.role.upper()} ({self.mode}): {self.state}   {d:.1f} km, {off:+.0f} deg off the nose")
        if self.mode == "overfly":
            out["banner"] += "   - release by hand (autopilot doesn't drop bombs / supplies yet)"
            if d < 0.3 or self.state == "past": self.state = "past"
            if self.state == "past" and d > 1.0: out["done"] = True
            return out
        if now < self.next_ok: return out
        if self.state == "prep" and self.events0 is None: self.events0 = len(read_events(db))
        if self.state == "prep" and f["locked"] not in self.locks: self.locks.append(f["locked"])
        # Give up a target that can't be locked once it is passed (behind, or the presses are spent and it is
        # close): 2026-10-19 the lock sat on the Cottbus SA-11 site (record 6) and nav, still in prep, steered
        # back at the target for 4 minutes at 250 ft beside the site.
        if self.state == "prep" and not self.target_locked(f) and (
                (d < 3.0 and abs(off) > 90) or (self.b_presses >= self.B_MAX and d < 2.0)):
            self.log.append(f"no lock on record {self.record} after {self.b_presses} B presses (locked {self.locks}): "
                            f"attack abandoned {d:.1f} km from the target")
            out["done"] = True; out.pop("want"); return out
        if self.state == "prep":
            # Ground targets lock only with the trackcam in air-to-ground (F2 cycles it). There, B steps through
            # the target table nearest first (Mittenwalde 2026-10-18: airbase 32, then the AirPlane 2, ...);
            # N grabs the nearest on-screen object instead (often clutter, record 0), so B it is.
            # no speed change for an attack (user, 2026-10-18): it upsets nav and adds radar exposure for nothing
            if read_trackcam(db) != AIR_GROUND and self.f2_presses < 4:
                db.tap("f2", TAP); self.f2_presses += 1; self.next_ok = now + 0.8; self.key = "f2"; return out
            have = [i for i, (w, n) in enumerate(f["loadout"]) if w == self.weapon and n > 0]
            if not have:
                out["banner"] += f"\n  NO {'CAMERA' if self.mode == 'photo' else 'MAVERICK'} LEFT IN THE LOADOUT {f['loadout']} - attack skipped"
                self.log.append("weapon not loaded / empty: skipped"); out["done"] = True; out.pop("want"); return out
            if f["station"] not in have and self.space_presses < 8:   # Space cycles the 4 stations
                db.tap("space", TAP); self.space_presses += 1; self.next_ok = now + 0.8; self.key = "space"; return out
            if not self.target_locked(f) and not self.cam_on:         # B only works with the trackcam up ("/", scancode)
                db.scan_tap(0x35, TAP); self.cam_on = True; self.next_ok = now + 1.0; self.key = "/"; return out
            if not self.target_locked(f) and self.b_presses < self.B_MAX:   # B cycles the ground targets (wraps)
                db.tap("b", TAP); self.b_presses += 1; self.next_ok = now + 0.8; self.key = f"b{self.b_presses}"; return out
            if not self.target_locked(f):
                out["banner"] += f"\n  TARGET NOT SELECTED (locked {f['locked']}, want record {self.record}) - select it by hand (B)"
                if self.record is not None: return out
            self.state = "track"
        if self.state == "track":
            out["banner"] += f"   HUD lock {f['hud_lock']}"
            # docs: target within 22 deg of the nose, launch inside 20 km (16-20 planned; closer is fine)
            if self.mode == "maverick" and d < self.MAV_MIN_KM:
                self.log.append(f"too close for a Maverick ({d:.1f} km < {self.MAV_MIN_KM}): not fired"); self.state = "away"; return out
            if self.mode == "maverick" and d <= self.MAV_MAX_KM and abs(off) < 10:
                # LEVEL shot at cruise altitude (the user fires Mavericks level at 450 ft; the pop-up from the planner
                # notes climbed to 2300 ft and still didn't fire, 2026-10-18): alt and heading holds stay on.
                self.state = "fire"; self.before = sum(n for w, n in f["loadout"] if w == MAVERICK); self.tries = 0
                set_toggle(db, f, BAY, True, "8"); self.next_ok = now + 0.6
            elif self.mode == "photo" and f["opp"] >= self.PHOTO_FLAPS_OPP and d <= self.photo_km + self.PHOTO_BAY_KM + self.PHOTO_FLAPS_KM \
                    and not f["flags"] & FLAPS and not self.flaps_set:
                set_toggle(db, f, FLAPS, True, "9"); self.flaps_set = True; self.key = "9"
                self.log.append(f"flaps down for the Elite photo window ({d:.1f} km out)")
            if self.mode == "photo" and self.flaps_set and not f["flags"] & FLAPS and self.state == "track":
                set_toggle(db, f, FLAPS, True, "9")                     # re-press until the flag shows them down
            if self.mode == "photo" and d <= self.photo_km + self.PHOTO_BAY_KM:
                set_toggle(db, f, BAY, True, "8", settle=self.BAY_RETRY_S); self.key = "8"; self.state = "photo"
            return out
        if self.state == "fire":
            if not f["flags"] & BAY:                                    # bay must be open before Enter
                set_toggle(db, f, BAY, True, "8"); self.next_ok = now + 0.6; return out
            have = sum(n for w, n in f["loadout"] if w == MAVERICK)
            if have < self.before:
                self.log.append(f"Maverick fired at {d:.1f} km, off {off:+.0f}, alt {s['alt']}, pitch {f['pitch']:+.1f}, HUD lock {f['hud_lock']}")
                self.state = "close"; return out
            if self.tries >= 3 or d < self.MAV_MIN_KM:
                self.log.append(f"Maverick did NOT fire after {self.tries} presses ({d:.1f} km, locked {f['locked']}, HUD lock {f['hud_lock']}, station {f['station']})")
                self.state = "close"; return out
            db.tap("enter", TAP); self.tries += 1; self.next_ok = now + 2.0   # let the count drop: a 2nd Enter = a 2nd Maverick
            return out
        if self.state == "photo":
            score, budget = self.photo_score(s, f, p)
            out["banner"] += f"   photo score {score:.0f} / {budget}"
            # ONE frame well inside the window, then the bay shuts at once: an open bay is a radar beacon,
            # and the first flight's 4 frames (3.1 -> 2.2 km, all inside the window) were 3 too many.
            # A 2nd frame only if the 1st was taken outside the window (shouldn't happen at 0.8).
            if self.events0 is None: self.events0 = len(read_events(db))
            # the bay must be OPEN before Enter (2026-10-19: the one "8" tap was lost, the camera fired with the
            # bay shut and no frame was taken): re-press until the flag shows it, never fire with it shut
            if not f["flags"] & BAY:
                before = _toggled.get("8")
                set_toggle(db, f, BAY, True, "8", settle=self.BAY_RETRY_S)
                if _toggled.get("8") != before: self.key = "8"; self.bay_presses += 1
                out["banner"] += f"   BAY NOT OPEN ({self.bay_presses} presses)"
                if d < 0.1 or (score >= budget and d < self.photo_km):
                    self.log.append(f"bay never opened ({self.bay_presses} presses of 8): no frame"); self.state = "close"
                return out
            if score < min(0.92 * budget, budget - 20) and self.shots == 0:   # 2026-10-27: see f19.pilot.phases.attack
                db.tap("enter", TAP); self.shots += 1; self.next_ok = now + 1.0; self.key = "enter"
                self.log.append(f"camera fired at {d:.2f} km, score {score:.0f}/{budget}, bay open"); self.state = "close"
            elif d < 0.1:                                              # overflown without a frame
                self.log.append("photo point passed without a frame"); self.state = "close"
            return out
        if self.state == "close":                                      # keep at it until the flag says shut
            if not set_toggle(db, f, BAY, False, "8") and self.flaps_set and f["flags"] & FLAPS:
                set_toggle(db, f, FLAPS, False, "9"); return out             # Elite photo: flaps back up first
            if not set_toggle(db, f, BAY, False, "8"):
                if self.mode == "photo":
                    ok = [e for e in read_events(db)[self.events0 or 0:] if e[0] == 10 and e[2] == self.record]
                    self.log.append("Target photographed (event log)" if ok else "no photographed event in the log yet")
                    self.state = "away"
                else:
                    self.state, self.t_fired = "result", now
                    # our missile = a player slot (8-11) holding the Maverick spec with time to live left
                    self.slot = next((i for i, r in enumerate(read_projectiles(db))
                                      if struct.unpack_from("<H", r, 0x10)[0] == self.MAV_SPEC and struct.unpack_from("<H", r, 0xE)[0]), None)
            out["banner"] += "   closing bay"
            return out
        if self.state == "result":                                     # wait for the hit: kill or "Ineffective" (event 4)
            self.next_ok = now + 2.0
            # Event type 4 is the LAUNCH (logged ~at once), not the result: the log has no "Ineffective"/miss record
            # (2026-10-18: the retry read type 4 as Ineffective and wasted the 2nd Maverick). So: a kill event or
            # the destroyed bit = done; still alive after the flight time = re-shoot if a Maverick and room are left.
            ev = [e for e in read_events(db)[self.events0 or 0:] if e[2] == self.record and e[0] in (1, 2, 3, 12)]
            dead = read_target(db, self.record)[2] if self.record is not None else False
            left = sum(n for w, n in f["loadout"] if w == MAVERICK)
            r = read_projectiles(db)[self.slot] if self.slot is not None else None
            flying = r is not None and struct.unpack_from("<H", r, 0x10)[0] == self.MAV_SPEC and struct.unpack_from("<H", r, 0xE)[0]
            out["banner"] += f"   missile {'in flight' if flying else 'gone'} ({now - self.t_fired:.0f} s)"
            if flying: self.gone = None
            else: self.gone = self.gone or now
            if dead or ev:
                self.log.append(f"target destroyed ({now - self.t_fired:.0f} s after the shot)"); self.state = "away"
            elif (self.gone and now - self.gone > 2.0) or now - self.t_fired > 120 / ts:
                self.log.append(f"missile gone after {now - self.t_fired:.0f} s, target alive ({d:.1f} km), {left} Maverick(s) left")
                if left and d >= self.MAV_MIN_KM:
                    self.state = "fire"; self.before = left; self.tries = 0; set_toggle(db, f, BAY, True, "8")
                else: self.state = "away"
            return out
        if self.state == "away" and f["flags"] & BAY:                   # Maverick path too: never leave it open
            set_toggle(db, f, BAY, False, "8"); return out
        if self.state == "away":
            x, y, dead = read_target(db, self.record) if self.record is not None else (0, 0, None)
            if dead is not None: self.log.append(f"target record {self.record} destroyed flag: {dead}")
            out["done"] = True; out.pop("want")
        return out

class GunAttack:
    """Strafe one radar site with the gun: run-in -> dive -> pull -> check, then extend and come back for another pass
    until it's dead, the gun is empty or MAX_PASSES are flown.

    Aiming is the flight-validated CCIP (scripts/gun_probe.ccip, FINDINGS.md "Guns"): bullet velocities are whole units
    a frame, so the descent per frame k = round(r sin(-pitch)) picks the range and the pitch only has to sit inside a
    band. The dive aims for the CENTRE of the band whose impact lands on (or just past) the target: the Wittstock pass
    (2026-10-19) sat 0.1 deg outside the band it needed and every bullet landed long. With pitch held the impact point
    walks back towards the plane (nose above the flight path), so a slightly long start rakes through the target.
    Fire only while the predicted miss is inside half the kill radius: bullets 6.5 u past Wittstock (radius 8) snapped
    to another site object and killed nothing; both kills were 1-1.5 u.
    Cease fire and pull at CEASE_FT + 1.2 s of sink (with the loop's lag on top): hand-flown pulls from -10..-24 deg lost
    ~125 ft and three hand passes that fired below that bottomed at 221 / 89 / 126 ft.
    UNTESTED: pitch rate per held key at sensitivity 1 (PITCH_RATE, from the user's -7.8 -> -18.8 deg push at ~6 deg/s)
    and whether a held "bs" auto-repeats through the automation port (falls back to a fresh press every cycle)."""
    ENTRY_FT, PUSH_PITCH, LEAD_U = 1500, 12.0, 25      # run-in height; push where a -12 deg shot would land LEAD_U past
    CEASE_FT, CEASE_SINK_S, HARD_FLOOR = 300, 1.2, 450 # cease/pull at CEASE_FT + sink * 1.2 s (never below HARD_FLOOR)
    PULL_TO, PULL_MAX_S = 5.0, 4.0                     # hold nose-up until this pitch (or this long)
    PITCH_RATE, TAP_MIN, TAP_MAX = 6.0, 0.08, 0.5      # deg/s while a pitch key is held (UNTESTED at sens 1)
    MAX_OFF, EXTEND_KM, MAX_PASSES = 3.0, 8.0, 3       # run-in heading error allowed at the push; extension before a re-attack
    def __init__(self, record, target, thr):
        self.record, self.T, self.thr = record, target, thr
        self.state, self.passes, self.log, self.next_ok = "runin", 0, [], 0.0
        self.k = None; self.firing = False; self.fire_t = None; self.rounds_at = None; self.pulse = False
        self.hist = []; self.last_t = None; self.cycle = 0.5; self.held_other = set(); self.pull_t = None
        self.run_hdg = None; self.bottom = None; self.fired = 0; self.rounds0 = None
    # -- helpers
    def _fire(self, db, on, now, rounds):
        if on and not self.firing:
            db.key_down("bs"); self.firing, self.fire_t, self.rounds_at = True, now, rounds
        elif not on and self.firing:
            db.key_up("bs"); self.firing = False
        elif on:
            # another key pressed or released since the last cycle stops the typematic repeat: press again. And if the
            # rounds haven't moved in 0.8 s of trigger, assume no repeat at all and press afresh every cycle.
            other = set(db._held) - {"bs"}
            if rounds != self.rounds_at: self.rounds_at, self.fire_t = rounds, now
            elif now - self.fire_t > 0.8: self.pulse = True
            if other != self.held_other or self.pulse: db.key_up("bs"); db.key_down("bs")
            self.held_other = other
    def _pitch_to(self, db, f, want, tol, now, ts):
        err = want - f["pitch"]
        if abs(err) <= tol or now < self.next_ok: return ""
        hold = max(self.TAP_MIN, min(self.TAP_MAX, abs(err) / self.PITCH_RATE / ts))
        db.tap(NOSE_UP if err > 0 else NOSE_DOWN, hold); self.next_ok = time.time() + 0.15
        return f"pitch {'up' if err > 0 else 'down'} {hold:.2f}s"
    def _bands(self, s, p, fs, nslots):
        """[(k, pitch, impact, along error)] for every descent band: along error > 0 = impact past the target."""
        r = 112 // max(fs, 1); out = []
        ux, uy = math.sin(math.radians(s["hdg"])), -math.cos(math.radians(s["hdg"]))
        for k in range(1, int(r * 0.6) + 1):                       # up to ~-37 deg
            pit = -math.degrees(math.asin(k / r))
            c = ccip(p[0], p[1], s["alt"], pit, s["hdg"], fs, nslots)
            if c: out.append((k, pit, c, (c[0] - self.T[0]) * ux + (c[1] - self.T[1]) * uy))
        return out
    def step(self, db, s, f, p, now, ts):
        from f19_state import read_gun, read_target
        rounds, fs, nslots = read_gun(db)
        if self.rounds0 is None: self.rounds0 = rounds
        if self.last_t is not None: self.cycle = 0.7 * self.cycle + 0.3 * (now - self.last_t)
        self.last_t = now
        self.hist.append((now, s["alt"])); self.hist = [h for h in self.hist if h[0] >= now - 1.0]
        sink = max(0.0, -(self.hist[-1][1] - self.hist[0][1]) / max(0.3, self.hist[-1][0] - self.hist[0][0]) / ts)
        d = game_dist(p, self.T); brg = bearing(p, self.T); off = wrap(brg - s["hdg"])
        radius = 24 // (f["opp"] + 2)                              # gun v 4 vs SAM radar: kill if dist < 24/(opp+2)
        fire_miss = max(1.5, radius / 2)
        c = ccip(p[0], p[1], s["alt"], f["pitch"], s["hdg"], fs, nslots)
        miss = game_dist(c, self.T) if c else None
        out = dict(want=brg, banner=f"  AUTO GUN record {self.record}: {self.state}   pass {self.passes + 1}/{self.MAX_PASSES}"
                   f"   {d / K:.2f} km, {off:+.1f} deg off   rounds {rounds}   CCIP miss {'-' if miss is None else f'{miss:.1f}'} u (fire < {fire_miss:.1f})")
        if self.state == "runin":
            out.update(alt=self.ENTRY_FT, vs_max=20.0, rate_cap=0.8)
            if rounds < 40 // max(fs, 1):
                self.log.append("gun empty"); out["done"] = True; out.pop("want"); return out
            r = 112 // max(fs, 1); k0 = round(r * math.sin(math.radians(self.PUSH_PITCH)))
            c0 = ccip(p[0], p[1], s["alt"], -math.degrees(math.asin(k0 / r)), s["hdg"], fs, nslots)
            push_at = (game_dist(p, c0) if c0 else 0) + self.LEAD_U
            out["banner"] += f"   push at {push_at / K:.2f} km"
            if d <= push_at:
                if abs(off) > self.MAX_OFF or s["alt"] < self.ENTRY_FT - 300:
                    self.log.append(f"pass {self.passes + 1}: not set up at the push ({off:+.1f} deg off, {s['alt']} ft) - extending")
                    self.state, self.run_hdg = "extend", s["hdg"]
                else:
                    self.state, self.k, self.bottom, self.fired = "dive", None, None, rounds
            return out
        if self.state == "dive":
            out.update(alt_hold=False, rate_cap=0.4)
            cease = max(self.HARD_FLOOR, self.CEASE_FT + self.CEASE_SINK_S * sink)
            bands = self._bands(s, p, fs, nslots)
            ok = [b for b in bands if b[3] >= -2]                      # long or on: the burst walks back through it
            low = s["alt"] - sink * (self.cycle + 0.2) <= cease
            if low or not ok:                                          # too low, or so close that every band lands short
                self._fire(db, False, now, rounds); db.key_down(NOSE_UP); self.pull_t = now; self.state = "pull"
                self.log.append(f"pass {self.passes + 1}: pull ({'cease altitude' if low else 'too close'}) at {s['alt']} ft,"
                                f" pitch {f['pitch']:+.1f}, sink {sink:.0f} ft/s, {(self.fired - rounds) // max(1, 40 // max(fs, 1))} bullets")
                return out
            best = min(ok, key=lambda b: b[3])
            cur = next((b for b in bands if b[0] == self.k), None)
            if best and (cur is None or abs(cur[3]) > radius and abs(best[3]) < abs(cur[3]) - 3): self.k = best[0]; cur = best
            if cur:
                r = 112 // max(fs, 1); half = math.degrees(0.5 / r)       # half a band, deg
                out["banner"] += f"   band k={cur[0]} pitch {cur[1]:+.1f} (now {f['pitch']:+.1f})  impact {cur[3]:+.0f} u along"
                # pitch moves in ~1.58 deg steps (hand-flown logs: -7.80 -9.38 -10.95 -12.53), so +-0.8 is the best
                # reachable; bands are +-1.0 (fs 4) .. +-2 deg (fs 8) wide, so that's still inside
                tol = max(0.8, 0.6 * half)
                if abs(cur[1] - f["pitch"]) > tol and now >= self.next_ok:
                    self._fire(db, False, now, rounds)                 # no bullets while the nose moves
                    out["banner"] += "   " + self._pitch_to(db, f, cur[1], tol, now, ts); return out
            out["roll_hold"] = not self.firing                         # roll keys would end the trigger's repeat
            self._fire(db, miss is not None and miss < fire_miss, now, rounds)
            if self.firing: out["banner"] += "   FIRING"
            return out
        if self.state == "pull":
            out.update(alt_hold=False, roll_hold=False)
            self.bottom = s["alt"] if self.bottom is None else min(self.bottom, s["alt"])
            if f["pitch"] >= self.PULL_TO or now - self.pull_t > self.PULL_MAX_S / ts:
                db.key_up(NOSE_UP); self.state, self.next_ok = "check", now + 1.5
            return out
        if self.state == "check":
            out.update(alt=self.ENTRY_FT, vs_max=20.0)
            self.bottom = min(self.bottom, s["alt"])
            if now < self.next_ok: return out                          # let the last bullets land
            self.passes += 1; dead = read_target(db, self.record)[2]
            self.log.append(f"pass {self.passes}: bottom {self.bottom} ft, {self.rounds0 - rounds} rounds used so far, "
                            f"target {'DESTROYED' if dead else 'alive'}")
            if dead or self.passes >= self.MAX_PASSES or rounds < 40 // max(fs, 1):
                out["done"] = True; out.pop("want"); return out
            self.state, self.run_hdg = "extend", s["hdg"]
            return out
        if self.state == "extend":                                     # straight on past it, then turn back
            out.update(want=self.run_hdg, alt=self.ENTRY_FT, vs_max=20.0)
            if d > self.EXTEND_KM * K and abs(wrap(brg - s["hdg"])) > 90: self.state = "turn"
            return out
        if self.state == "turn":
            out.update(alt=self.ENTRY_FT, vs_max=20.0, rate_cap=1.6)
            if abs(off) < self.MAX_OFF: self.state = "runin"
            return out
        return out

    def abort(self, db):
        """Let go of the trigger and the stick (nav exit / phase switch)."""
        for k in ("bs", NOSE_UP, NOSE_DOWN): db.key_up(k)


class Landing:
    """Approach fix -> Final -> touchdown -> stopped, on the planner's N-S axis.

    Runway box (FINDINGS "Landing rules"): within +-5 units (+-78 m) of the base record's
    X and +-64 units (+-1 km) along Y. Touchdown limits (Realistic): kt*sin(glide) <= 19,
    bank <= 16.9 deg, gear down. Aims AIM_UNITS short of the base record, glides down
    ALT_PER_KM, flares to FLARE_VS, then idle + brakes (150 kt stops in ~0.84 km).
    Goes around (max power, climb, hand back) if it isn't down by the base record or is
    more than GA_XTRACK units off the centreline low down."""
    # First automatic landing (2026-10-18): touched down 26 units past the base record at 149 kt after floating at ~7 ft,
    # stopped 5 units short of the box end. So: aim 48 units short (box starts 64 short), 135 kt, shallower float.
    APPROACH_KT, FINAL_KT, ALT_PER_KM, AIM_UNITS = 170, 135, 60.0, 48
    # flaps + final speed at 11 km (was 7): lowering them on the glide ballooned the plane to 544 ft at 6 km and it reached
    # the aim point at 120 ft -> go-around (2026-10-18)
    GEAR_KM, FLAPS_KM, FLARE_ALT, FLARE_VS, GA_XTRACK = 12.0, 11.0, 25, 4.0, 8
    IDLE_ALT = 15                                  # power to idle below this (the float came from the speed hold adding power)
    # Short final (2026-10-20 go-around): below GLIDE_ALT the hold flies a SINK RATE aimed at the touchdown point
    # (height / time to the aim point, at least MIN_SINK) with a GLIDE_DEADBAND ft/s deadband. Before, it held the
    # glide altitude with the cruise deadband (6 ft/s), as wide as the whole allowed sink (6, flare 4), so level
    # flight never earned a down press: level at 62 ft from 0.9 km out, aim point crossed at 53 ft, still 30 ft
    # up 0.8 km past it -> go-around. And no power added below GLIDE_ALT unless slower than NO_THR_UP_KT.
    GLIDE_ALT, MIN_SINK, GLIDE_DEADBAND, NO_THR_UP_KT = 150, 1.5, 2.0, 112
    K_XT = 0.5
    # Lead on the drift: steer on where the plane will be LEAD_S game s from now (xt + LEAD_S x drift rate). Without it
    # (2026-10-20, twice) a 2 km offset at the approach fix gave a 35 deg intercept that crossed to -76 units, S-turned
    # +-40 units to 4 km and arrived 17 units off at 50 ft -> go-around. A press-based roll model with the logged ~8 s
    # turn lag: overshoot 16 units without the lead, < 1 with 15 s.
    LEAD_S = 15.0
    def __init__(self, base, approach_fix, thr):
        self.base, self.thr, self.state = base, thr, "approach"
        self.rwy = bearing(approach_fix, base)                   # heading flown on final (0 or 180)
        self.s = 1 if abs(wrap(self.rwy)) < 90 else -1           # +1: landing northbound (approach from y larger)
        self.aim = (base[0], base[1] + self.s * self.AIM_UNITS)
    def step(self, db, s, f, p, now, ts):
        to_aim = (p[1] - self.aim[1]) * self.s / K               # km before the aim point (negative: past it)
        xt = p[0] - self.base[0] if self.s > 0 else self.base[0] - p[0]   # + = right of the centreline, units
        # One centreline law from intercept to touchdown: heading offset = -K * xt (units), capped 35 deg far out and
        # 10 deg inside 4 km. K = 0.5 deg/unit keeps the lateral loop ~4x slower than the press-based heading hold;
        # 1.5 S-turned +-40 units on the first try (2026-10-18), and a model with the real roll presses (0.6-2.5 s,
        # 0.25 deg/s deadband, 2.5 s settle) reproduced that and settles within +-1-3 units at 0.4-0.6.
        cap = 35.0 if to_aim > 4.0 else 10.0
        drift = s["spd"] * 0.5144 * K / 1000 * math.sin(math.radians(wrap(s["hdg"] - self.rwy)))   # units per s, + = rightward
        want = (self.rwy - max(-cap, min(cap, self.K_XT * (xt + self.LEAD_S * drift)))) % 360
        out = dict(want=want, rate_cap=1.5, floor=0)
        if self.state in ("approach", "final"):
            if to_aim < self.GEAR_KM: set_toggle(db, f, GEAR_UP, False, "6")
            if to_aim < self.FLAPS_KM: set_toggle(db, f, FLAPS, True, "9")
            self.thr.target = self.FINAL_KT if to_aim < self.FLAPS_KM else self.APPROACH_KT
            if to_aim < self.FLAPS_KM: self.state = "final"
            out["alt_cap"] = max(0.0, to_aim * self.ALT_PER_KM)   # glide path: never above it, never climb to it
            # sink limits by height: 12 ft/s above 100 ft (touchdown limit is ~30 ft/s at 145 kt, and only at the ground),
            # 6 ft/s down to the flare, 2.5 ft/s in the flare
            out["vs_max"] = self.FLARE_VS if s["alt"] < self.FLARE_ALT else 6.0 if s["alt"] < 100 else 12.0
            if self.state == "final" and s["alt"] < self.GLIDE_ALT:
                t_aim = max(2.0, to_aim * 1000 / max(30.0, s["spd"] * 0.5144))      # s to the aim point (2 s once past it)
                out["vs_target"] = max(-out["vs_max"], min(-self.MIN_SINK, -s["alt"] / t_aim))
                out["deadband"] = self.GLIDE_DEADBAND
                out["no_thr_up"] = s["spd"] >= self.NO_THR_UP_KT
            if s["alt"] < 60 and abs(f["roll"]) > 8 and abs(xt) <= 5: out["want"] = s["hdg"]   # aligned: wings level first
            if 0 < s["alt"] < self.IDLE_ALT and not getattr(self, "idled", False):
                self.thr.idle(db); self.idled = True
            if s["alt"] == 0: self.thr.idle(db); self.state = "rollout"
            elif (to_aim < -self.AIM_UNITS / K and s["alt"] > 20) or (s["alt"] < 80 and abs(xt) > self.GA_XTRACK):
                self.thr.max(db); self.state = "go-around"
        if self.state == "rollout":
            out.update(alt_hold=False, roll_hold=False)
            set_toggle(db, f, BRAKES, True, "0")
            if s["spd"] <= 1: self.state = "stopped"; out["done"] = True
        if self.state == "go-around":
            out.update(alt=500, vs_max=10.0); set_toggle(db, f, GEAR_UP, True, "6")
            if s["alt"] > 400: out["done"] = True
        out["banner"] = (f"  AUTO LANDING: {self.state}   aim {to_aim:5.2f} km   centreline {xt:+.0f} units (box +-5)"
                         f"   roll {f['roll']:+.0f}   gear {'up' if f['flags'] & GEAR_UP else 'down'}"
                         f"   flaps {'down' if f['flags'] & FLAPS else 'up'}   box {f['box']}")
        return out
