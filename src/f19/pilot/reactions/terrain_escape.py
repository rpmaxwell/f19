"""TerrainEscape (scripts/phases.py TerrainEscape) on step(state, ctx) -> Command.

Terrain-ahead escape (2026-10-21 crash: first loiter turn in cell (8,10), nav fell behind the circle, drifted 3.9 km
off the route and flew into the ridge at 704 ft while its banner said MOUNTAIN AHEAD).

probe(p, hdg, alt, max_km) -> (km, top ft) of the first terrain within MARGIN_FT of alt straight ahead (nav's
terrain_ahead on the planner's decoded terrain) or None. Time to impact = km / ground speed. All times are REAL
seconds (the caller's now): the closing speed is measured from the plane's own track (units per real s over the last
few s), with the game-physics estimate (0.788 x kt x ASSUMED_RATIO x ts) only until then.
  - inside WARN_S: TURN. Probe headings +-FAN_DEG around the nose; fly the clear one nearest to where the route wants
    to go (ctx.want), at ESCAPE_RATE deg/s. Mountains stay walls.
  - still pointed at terrain inside CLIMB_S: CLIMB as the last resort - hold top + CLIMB_ABOVE_FT at up to CLIMB_VS ft/s
    (Command.throttle_max: nav added full power below 170 kt) - and keep turning to the best heading.
Ends after CLEAR_S with nothing inside WARN_S. ts = state.time_mult (None -> 1). Step it every tick nav did (route /
attack / strafe above 100 ft): its track speed needs the samples.
"""
import math

from f19.core.units import GAME_PER_WALL, GROUND_PER_KT

from f19.pilot.phases._common import K, command_of, wrap


class TerrainEscape:
    name, priority = "terrain_escape", 60
    WARN_S, CLIMB_S, CLEAR_S = 40.0, 12.0, 4.0           # real s (at 2.5x: ~100 / 30 game s); 40 > 25 saved a 2 deg/s turn in replay
    # 100 ft: at 150 a 96 ft rise under a 220-244 ft cruise set it off 6 times on 2026-10-21 (one CLIMB to 596 ft)
    MARGIN_FT, MAX_KM, FAN_DEG, FAN_STEP = 100, 8.0, 150, 15
    # 2026-10-23: a 16 ft rise ahead of a plane sagged to 116 ft set off a full-power climb to 980 ft / 219 kt (inside
    # an SA-14 cell, by SAM sites). Now the climb is only for terrain within CLIMB_MARGIN_FT of our height, to
    # CLIMB_ABOVE_FT over it; nav adds full power only when slow.
    ESCAPE_RATE, CLIMB_ABOVE_FT, CLIMB_VS, CLIMB_MARGIN_FT = 3.0, 300, 60.0, 50
    GROUND_PER_KT = GROUND_PER_KT                        # 0.788: map distance per game s / indicated kt
    ASSUMED_RATIO = GAME_PER_WALL                        # game s per real s at ts 1, until the track gives a speed (x ts when time is accelerated)

    def __init__(self, probe):
        self.probe, self.state, self.clear_since, self.climb_to, self.log = probe, "off", None, None, []
        self.track = []
        self.done = False

    def wants_control(self, state, ctx): return self.state != "off"

    def _km_s(self, kt, p, now, ts=1.0):
        """Ground speed, km per REAL s: from the plane's own track over the last 2-6 s if there is one, else from the game physics at
        ASSUMED_RATIO x ts (the time byte is relative to the nominal 2.46 game s per real s)."""
        self.track.append((now, p)); self.track = [q for q in self.track if q[0] >= now - 6.0]
        t0, p0 = self.track[0]
        if now - t0 >= 2.0:
            v = math.dist(p, p0) / K / (now - t0)
            if v > 0.02: return v
        return max(kt, 100) * self.GROUND_PER_KT * 1.852 / 3600 * self.ASSUMED_RATIO * ts

    def step(self, state, ctx):
        ov, ev = self._step(state, state.xy, ctx.now(), ctx.want, state.ts)
        return command_of(ov, (ev,) if ev else ())

    def _step(self, s, p, now, route_want, ts=1.0):
        alt, hdg = s.alt_ft, s.heading
        v = self._km_s(s.speed_kt, p, now, ts)
        hit = self.probe(p, hdg, alt, self.MAX_KM)
        t_hit = hit[0] / v if hit else math.inf
        if t_hit > self.WARN_S:
            if self.state == "off": return {}, ""
            if self.clear_since is None: self.clear_since = now
            if now - self.clear_since < self.CLEAR_S / ts: return self._hold(hdg), ""
            self.state, self.climb_to, self.clear_since = "off", None, None
            return {}, "terrain escape: clear, back to the route"
        self.clear_since = None
        # clear distance for each heading of the fan
        best = None
        for k in range(-self.FAN_DEG // self.FAN_STEP, self.FAN_DEG // self.FAN_STEP + 1):
            h = (hdg + k * self.FAN_STEP) % 360
            r = self.probe(p, h, alt, self.MAX_KM)
            clear = r[0] if r else self.MAX_KM
            # a turn takes time: |k*step| / rate s, flown on the old heading-ish; demand that much more room
            need = v * abs(k * self.FAN_STEP) / self.ESCAPE_RATE * 0.5
            ok = clear - need
            key = (ok >= v * self.WARN_S * 0.5, -abs(wrap(h - route_want)) if ok >= v * self.WARN_S * 0.5 else ok)
            if best is None or key > best[0]: best = (key, h, ok)
        ev = ""
        if self.state == "off":
            ev = f"terrain escape: {hit[1]} ft ridge {hit[0]:.1f} km ahead ({t_hit:.0f} s) at {alt} ft - turning to {best[1]:.0f}"
            self.state = "turn"
        self.want = best[1]
        if t_hit < self.CLIMB_S and (hit[1] is None or hit[1] >= alt - self.CLIMB_MARGIN_FT):   # really in the way
            top = hit[1] if hit[1] is not None else alt + 1000
            target = top + self.CLIMB_ABOVE_FT
            if self.climb_to is None or target > self.climb_to:
                if self.state != "climb": ev = (ev + "; " if ev else "") + f"terrain escape: CLIMB to {target} ft (ridge {t_hit:.0f} s ahead, turn not clearing it)"
                self.climb_to = target
            self.state = "climb"
        return self._hold(hdg), ev

    def _hold(self, hdg):
        ov = dict(want=getattr(self, "want", hdg), rate_cap=self.ESCAPE_RATE, escape=self.state)
        if self.climb_to is not None:
            ov.update(alt=self.climb_to, vs_max=self.CLIMB_VS, throttle_max=True)
        return ov
