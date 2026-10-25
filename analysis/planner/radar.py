"""The radar model: the game's own detection rules (analysis/radar_game.py, FINDINGS.md "Radar sites:
pulses, duty cycle, detection", EGAME 2026-10-18). Every planner stage calls this, so a new finding
changes radar_game.py or this file and nothing else.

What the game does (and so what the planner believes):
  - A site's return s on the plane depends on range, the site's power, the opponent level, the plane's
    aspect to the site and the TERRAIN TYPE OF THE PLANE'S 32 km CELL: mountain cells x0 (invisible to
    every radar), rough x0.5, flat x1, sea x1.5. There is NO terrain line of sight: ridges hide nothing.
  - Pulse radars see the side aspect (x0.5 flying at/away .. x1.5 tangential); dopplers see the radial
    aspect and only when fast (their return grows with speed: SA-10 head-on 0 km at 150 kt, 15 at 200, 118 at 400).
  - Seen = s + EMV > 100, a hard threshold. EMV = altitude + |bank|/2 + speed/38 kt + 24 bay open + 16 ECM.
  - Each quiet site pulses every 32 game s and is active ~37.5 % of the time (duty cycle, frame counter
    random at start). Seen pulses raise the site's alert (+48 at Veteran, -16 unseen); alertsim.py
    simulates that for the report.
The search cost is the expected number of seen pulses per km, counting a site when its margin
(s + EMV - 100) is above -radar_margin (the formula's error near the threshold is under ~30).
"""
import math
import numpy as np
import radar_game as rg
from world import KT_KMH

MODEL = dict(source="radar_game.py (EGAME decode 2026-10-18)", margin=None, duty=None, opp=None, war=None)
QUIET_PULSE_S = 32.0          # 16 ticks between pulses of a quiet site


def hdg65536(ux, uy):
    """Unit heading in the y-down map plane -> game angle (0 north, clockwise, 65536 = 360)."""
    return np.round(np.arctan2(ux, -np.asarray(uy)) / (2 * np.pi) * 65536).astype(np.int64) & 0xFFFF


class RadarModel:
    def __init__(self, world, kt_default=None):
        """kt_default(x, y) -> planned kt at those points (fuelplan regime); used when detection() gets no kt."""
        self.W, P = world, world.P
        self.P = P
        self.opp, self.war = world.opp, world.war
        self.M = float(P["radar_margin"])
        self.duty = rg.duty_fraction(world.war)
        self.ecm = bool(P["ecm"])
        self.kt_default = kt_default
        self.R = [(np.array([r["x"], r["y"]], float), r["type"], r["kind"], f"{r['place']} {r['system']}")
                  for r in world.radars]
        MODEL.update(margin=self.M, duty=round(self.duty, 3), opp=self.opp, war=self.war)
        self._emv = {}

    # ---------------------------------------------------------------- the game's numbers
    def emv(self, alt, kt, roll_deg=0.0, bay=False, ecm=None):
        """EMV per point (arrays broadcast); exact radar_game.emv on the few distinct inputs. ecm: per point (None:
        the profile's)."""
        ecm = self.ecm if ecm is None else ecm
        alt, kt, roll, bay, ecm = np.broadcast_arrays(np.asarray(alt, float), np.asarray(kt, float),
                                                      np.asarray(roll_deg, float), np.asarray(bay, bool), np.asarray(ecm, bool))
        key = np.stack([alt.ravel().round(), kt.ravel().round(), roll.ravel().round(1), bay.ravel(), ecm.ravel()], 1)
        uniq, inv = np.unique(key, axis=0, return_inverse=True)
        vals = np.empty(len(uniq))
        for i, (a, k, r, b, c) in enumerate(uniq):
            kk = (a, k, r, b, c)
            if kk not in self._emv:
                self._emv[kk] = rg.emv(int(a), int(k), self.opp, int(round(r / 360 * 65536)), bool(b), bool(c))
            vals[i] = self._emv[kk]
        return vals[inv.ravel()].reshape(alt.shape)

    def margins(self, x, y, ux, uy, kt=None, alt=None, roll=0.0, bay=False, ecm=None):
        """(sites, *points) array of s + EMV - 100 (> 0: seen on a pulse)."""
        x, y, ux, uy = np.broadcast_arrays(*(np.asarray(v, float) for v in (x, y, ux, uy)))
        if kt is None: kt = self.kt_default(x, y) if self.kt_default else self.P["cruise_kt"]
        if alt is None: alt = self.P["threat_ft"]       # where a site could see the plane, it flies threat_ft
        kt = np.broadcast_to(np.asarray(kt, float), x.shape); alt = np.broadcast_to(np.asarray(alt, float), x.shape)
        e = self.emv(alt, kt, roll, bay, ecm)
        h = hdg65536(ux, uy)
        cl = self.W.clutter(x, y)
        out = np.empty((len(self.R),) + x.shape)
        ix, iy, ia, ik = x.astype(np.int64), y.astype(np.int64), alt.astype(np.int64), np.round(kt).astype(np.int64)
        for n, (pos, _, kind, _) in enumerate(self.R):
            out[n] = rg.site_return_np(ix, iy, ia, h, ik, self.opp, cl, int(pos[0]), int(pos[1]), kind) + e - 100
        return out

    def seen_count(self, x, y, ux, uy, kt=None, alt=None, roll=0.0, bay=False):
        """Number of sites with margin > -M at each point."""
        return (self.margins(x, y, ux, uy, kt, alt, roll, bay) > -self.M).sum(0)

    def detection(self, x, y, ux, uy, kt=None, alt=None, roll=0.0, bay=False):
        """Expected seen pulses per km at points (x, y) flying unit heading (ux, uy); arrays broadcast."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        if kt is None: kt = self.kt_default(x, y) if self.kt_default else self.P["cruise_kt"]
        n = self.seen_count(x, y, ux, uy, kt, alt, roll, bay)
        s_per_km = 3600.0 / (np.asarray(kt, float) * KT_KMH)
        return n * self.duty / QUIET_PULSE_S * s_per_km

    def in_sight(self, p, heading_u, kt=None, alt=None, roll=0.0, bay=False):
        """Report list: (name, type, km, aspect word, margin) of sites with margin > -M."""
        p = np.asarray(p, float)
        m = self.margins(p[0], p[1], heading_u[0], heading_u[1], kt, alt, roll, bay)
        out = []
        for (pos, typ, _, name), mg in zip(self.R, m):
            if mg <= -self.M: continue
            v = p - pos; d = float(np.hypot(*v)) / 64
            a = abs(float(v @ heading_u)) / (d * 64 + 1e-9)
            out.append((name, typ, d, "radial" if a > 0.7 else "tangential" if a < 0.4 else "oblique", float(mg)))
        return out

    def detection_range_km(self, kind, aspect, kt=200, emv=None, terrain=2, alt=None, step_km=0.5):
        """Farthest range at which a type-`kind` site sees the plane (None = never): aspect 'radial' or 'side'.
        For checks against the FINDINGS table (EMV forced when given)."""
        alt = self.P["cruise_ft"] if alt is None else alt
        e = emv if emv is not None else rg.emv(int(alt), int(kt), self.opp)
        clutter = terrain << 2
        best = None
        for d in np.arange(0.5, 520, step_km):
            px = int(d * 64)                     # site at the origin, plane east of it
            hdg = 0x4000 if aspect == "radial" else 0
            s = rg.site_return(px, 0, int(alt), hdg, int(kt), self.opp, clutter, 0, 0, kind)
            if s + e > 100: best = d
        return best
