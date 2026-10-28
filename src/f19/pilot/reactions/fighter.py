"""FighterWatch (scripts/phases.py FighterWatch) on step(state, ctx) -> Command.

Enemy aircraft watch and evasion, every aircraft scored with ITS OWN radar (2026-10-21). Each aircraft type has a
radar/weapon type (f19.rules.radar.aircraft_spec) and the detection loop runs site_return with it for every slot,
about every 32 game s. MiG-23 / MiG-25 radars are PULSE (see a side-on F-19, never head-on / going away); MiG-29 /
MiG-31 / Su-27 / IL-76 are DOPPLER (the opposite, and blind to a slow target). Fighters fly ~1.6-2x the F-19's
speed: running doesn't work, choosing the aspect does.

Each tick: THREATS = non-friendly aircraft with a radar inside their WATCH range (the IL-76 gets 70 km) or with an
alert on us. For every candidate heading (FAN_STEP) the margin (return + EMV - 100) of every threat is computed now and
LOOKAHEAD_S game s ahead; a heading is SAFE when every margin < -MARGIN (per difficulty, margin_for). Fly the safe heading nearest the goal (a
mountain cell within COVER_KM if one is safe-ward, else the route), keep the current heading when it is safe, else the
least-seen heading, at EVADE_KT and EVADE_ALT_FT. Ends after CLEAR_S with no threat.

ALERT BUDGET (2026-10-27, not legacy): a threat only STARTS an evasion when it would see us now (margin on the present
heading, speed and bank >= -MARGIN) AND the alert it holds on us is running out: a seen pulse adds (war + opp) x 16 + 32
(Veteran 64, Elite 80) and an alert above 0xC0 is a network alert (scrambles fighters, a visual ID inside 8 km), so it
acts when alert + gain would leave room for fewer than one more seen pulse (Veteran: two pulses banked, Elite: one), or
when the fighter is inside CLOSE_KM and would see us. Unseen pulses take 32 off, so a single sighting decays. A running
evasion ends (after CLEAR_S) as soon as the route heading is safe, instead of waiting for the fighters to leave.

Inputs that were arguments: aircraft = state.aircraft (the AIRCRAFT threats), opp = state.opp (nav passed 2 when it
had no flight read, i.e. without --auto: a GameState with opp None gives 2), route_want = ctx.want (the heading the
lower-priority behaviors asked for). Command: heading / rate_cap / speed_kt / alt / fighter (the old override dict),
events. speed_kt is an evasion speed: the arbiter restores the speed hold's target when the evasion ends (nav's
fighter_saved_kt). Step it every tick nav did (route / attack / strafe above 100 ft): it tracks alerts while off.
"""
import math

from f19.rules import radar as rg

from f19.pilot.phases._common import K, bearing, command_of, wrap


class FighterWatch:
    name, priority = "fighter", 40
    WATCH_KM_MIN, IL76_WATCH_KM, CLEAR_S = 25.0, 70.0, 15.0
    # 2026-10-27: the margin a heading must stay under, per difficulty. Elite flights: the margin predicts the game's alert
    # rises well (44 rises, 95 % at margin >= -5, one below -10), so -10 stays there. Veteran (5 flights, 8 evasions, none
    # with a margin >= 0, no fighter alert ever above 0): the -10 only cost minutes off the route, so 0 = "would be seen".
    MARGIN, MARGIN_ELITE = 0.0, 10.0
    FAN_STEP, LOOKAHEAD_S, RATE, EVADE_KT = 15, 30.0, 3.0, 165
    # 2026-10-22: the evasion flew on at the planner's 700 ft (+13 EMV at Elite) and flipped its heading 15 -> 195 ->
    # 180 -> 15 -> 0 -> 135 -> 315 in two minutes, never settling into an aspect. Now: down to EVADE_ALT_FT, and the
    # chosen heading is kept while it stays safe (or, with nothing safe, within KEEP_MARGIN of the best).
    EVADE_ALT_FT, KEEP_MARGIN = 250, 3.0
    COVER_KM, COVER_INSET_KM = 45.0, 4.0
    NET_ALERT, CLOSE_KM = 0xC0, 12.0

    def __init__(self, terrain_type=None, clutter=None, descend_first=True, legacy=False):
        """terrain_type(x, y) -> 0 mountain .. 3 sea; clutter(x, y) -> territory grid byte (radar clutter).
        legacy: the trigger of scripts/phases.py FighterWatch (margin 10 at every difficulty), for the equivalence tests."""
        self.tt, self.cl, self.descend_first, self.legacy = terrain_type, clutter, descend_first, legacy
        self.specs = {t: rg.aircraft_spec(t) for t in range(12)}
        self.hist, self.alerts, self.state, self.clear_since, self.want, self.mode = {}, {}, "off", None, None, ""
        self.nearest = ""
        self.done = False

    def wants_control(self, state, ctx): return self.state != "off"

    def margin_for(self, opp): return self.MARGIN_ELITE if self.legacy or opp >= 3 else self.MARGIN

    def _press(self, threats, s, p, opp, M):
        """The threat that starts an evasion (alert budget, see the module docstring): 'MIG-31 7km margin +3 alert 80', or None."""
        gain = 16 * opp + 32
        for d, a in sorted(threats, key=lambda t: t[0]):
            m = self._margin(a, p, s.heading, s.speed_kt, s.alt_ft, opp, s.att.roll)
            if m >= -M and (d < self.CLOSE_KM or a["alert"] + gain > self.NET_ALERT - gain):
                return f"{a['name']} {d:.0f}km margin {m:+.0f} alert {a['alert']}"
        return None

    def _clear(self, now, ev):
        if self.state == "off": return {}, ev
        if self.clear_since is None: self.clear_since = now
        if now - self.clear_since < self.CLEAR_S: return self._ov(), ev
        self.state, self.clear_since = "off", None
        return {}, ev + ["fighter evasion: clear, back to the route"]

    def _margin(self, a, p, hdg, kt, alt, opp, bank=0.0):
        sp = self.specs.get(a["type"], {})
        if not sp.get("radar_type"): return -999.0
        cl = self.cl(*p) if self.cl else 8
        e = rg.emv(int(alt), int(kt), opp, int(abs(bank) / 360 * 65536))
        return rg.site_return(int(p[0]), int(p[1]), int(alt), int(hdg / 360 * 65536) & 0xFFFF, int(kt), opp, int(cl),
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

    def step(self, state, ctx):
        opp = 2 if state.opp is None else state.opp
        ov, ev = self._step(state, state.xy, ctx.now(), ctx.want, state.aircraft, opp)
        return command_of(ov, ev)

    def _step(self, s, p, now, route_want, aircraft, opp):
        ev, threats, near = [], [], None
        alt, hdg = s.alt_ft, s.heading
        for a in aircraft:
            if a["type"] in rg.FRIENDLY_TYPES: continue
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
        if not threats: return self._clear(now, ev)
        kt, M = self.EVADE_KT, self.margin_for(opp)
        ahead = [self._ahead(a, p, self.LOOKAHEAD_S) for _, a in threats]
        def worst(h, bank=0.0, a_ft=None):
            a_ft = alt if a_ft is None else a_ft
            return max(max(self._margin(a, p, h, kt, a_ft, opp, bank), self._margin(b, p, h, kt, a_ft, opp, bank))
                       for (_, a), b in zip(threats, ahead))
        inside = self.tt is not None and self.tt(*p) == 0
        cur = worst(hdg)
        press = None
        if not self.legacy:
            press = self._press(threats, s, p, opp, M)
            route_ok = self.state != "off" and route_want is not None and worst(route_want) < -M
            if press is None and (self.state == "off" or route_ok): return self._clear(now, ev)
            self.clear_since = None
        # 2026-10-27: going down is cheaper than going around. Seen-margin is altitude dependent (400 -> 800 ft adds
        # 40-60% EMV): when the present heading is safe at EVADE_ALT_FT, only descend and keep the route (the first
        # flights' evasions flew minutes off it at 500-700 ft with a margin of -10).
        if self.descend_first and self.state in ("off", "low") and alt > self.EVADE_ALT_FT + 20:
            if worst(hdg, 0.0, self.EVADE_ALT_FT) < -M:
                if self.state == "off":
                    names = ", ".join(f"{x['name']} {dd:.0f}km" for dd, x in sorted(threats, key=lambda t: t[0])[:3])
                    if cur >= -M or not self.legacy:
                        ev.append(f"fighter watch ({names}{'; ' + press if press else ''}): margin {cur:+.0f} at {alt:.0f} ft, {worst(hdg, 0.0, self.EVADE_ALT_FT):+.0f} at "
                                  f"{self.EVADE_ALT_FT} - descending, route kept")
                        self.state, self.mode = "low", "low"
                if self.state == "low": return self._ov(), ev
        if self.legacy and cur < -M and (self.state == "off" or inside):
            if self.state == "off": return {}, ev                # nothing sees us on this heading: no need to act
        cover = None if inside else self._cover(p)
        goal = bearing(p, cover[1]) if cover else route_want
        fan = [(h, worst(h, 25.0)) for h in range(0, 360, self.FAN_STEP)]
        safe = [h for h, m in fan if m < -M]
        best_m = min(m for _, m in fan)
        prev = worst(self.want, 25.0) if self.state == "evade" and self.want is not None else None
        if prev is not None and (prev < -M or (not safe and prev <= best_m + self.KEEP_MARGIN)):
            want, why = self.want, self.mode or "keeping"                     # no flip-flopping while it still works
        elif cur < -M: want, why = hdg, "holding (safe)"
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
            ev.append(f"fighter evasion ({names}{'; ' + press if press else ''}): {why}, heading {want:.0f}; worst margin now {cur:+.0f}, on the new heading "
                      f"{worst(want):+.0f}")
        self.state, self.mode, self.want = "evade", mode, want
        return self._ov(), ev

    def _ov(self):
        if self.state == "low": return dict(fighter="low", alt=self.EVADE_ALT_FT)
        return dict(want=self.want, rate_cap=self.RATE, fighter=self.state, speed_kt=self.EVADE_KT, alt=self.EVADE_ALT_FT)

