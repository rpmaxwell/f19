"""What the map is: theater terrain, territory, airbases, and the mission in memory.

All positions in game units (waypoint-table units, 64 per km). Sources:
  logs/theater/<TH>_terrain.npz   decoded 3D terrain (analysis/terrain_3d.py), ft above the flat world
  logs/theater/<TH>.json          .WLD place records (analysis/parse_theater.py): airbases
  <plan_dir>/mission_mem.json     targets + radars from scripts/read_mission.py
  analysis/territory.py           16x16 32 km cell grid: bits 0-1 side (cold-war scoring), bits 2-3 terrain
                                  (0 mountain, 1 rough, 2 flat, 3 sea: radar clutter), bit 4 SA-14 cell
  plan.json "settings"            opponent / war / night (radar and SA-14 rules), roe (score)
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import json, math, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "analysis"))
from territory import grid as territory_grid  # noqa: E402
from theaters import places  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
from read_mission import RADAR as RADAR_CODES  # noqa: E402

SYSTEM_KIND = {sa: k for k, (sa, _, _) in RADAR_CODES.items()}
OPP = {"green": 0, "regular": 1, "veteran": 2, "elite": 3}

K = 64.0                 # game units per km
CELL_KM = 2.0            # search grid
CELL = CELL_KM * K
AIRBASE_FLAG = 1025      # .WLD record flags[0] of an airbase
# Map distance per game-clock second is 0.784 x the indicated airspeed (memlog 1792546698, 2026-10-21: 2234
# two-minute windows, p10-p90 0.774-0.795; the 2 s clock tick and 64 units per km as elsewhere). Every
# knots -> time / distance conversion in the planner uses this: flight time, fuel, samples, pulses per km.
# Before it the plans ran ~25 % short on time and fuel (flown: 104 game min, 6332 lb; planned 80 min, 4786 lb).
GROUND_PER_KT = 0.788       # pooled over 16 logged flights (analysis/fuel_log.py summary), 2026-10-21
KT_KMH = 1.852 * GROUND_PER_KT


def _dilate(mask, r):
    """Square dilation by r cells (separable shift-or)."""
    out = mask.copy()
    for axis in (0, 1):
        src = out.copy()
        for s in range(1, r + 1):
            if axis == 0:
                out[s:, :] |= src[:-s, :]; out[:-s, :] |= src[s:, :]
            else:
                out[:, s:] |= src[:, :-s]; out[:, :-s] |= src[:, s:]
    return out


class World:
    def __init__(self, theater, mission, profile, settings=None, sa14_live=None):
        self.th, self.M, self.P = theater, mission, profile
        self.settings = settings = dict(settings or {})
        self.notes = []
        if "opponent" not in settings: self.notes.append("opponent not in settings: radar planned for Veteran")
        self.opp = OPP[settings.get("opponent", "veteran")]
        self.war = int(settings.get("war", 0))          # gameData+0x3A, 0 in every flight so far
        self.night = bool(settings.get("night", False)) # ds:4EF4 (briefing); day = SA-14 cells live
        self.roe = int(settings.get("roe", 0))
        tz = S.logs / "theater" / f"{theater}_terrain.npz"
        if not tz.exists():
            subprocess.run([sys.executable, str(ROOT / "analysis/terrain_3d.py"), theater], check=True)
        z = np.load(tz)
        self.H, self.step = z["height_ft"], int(z["step_wp"])
        self.size = self.H.shape[1] * self.step                     # map extent (square), game units
        self.wall = self.H >= profile["mountain_ft"]
        self._seg_cache, self._masks = {}, {}
        self.wall_near = _dilate(self.wall, int(math.ceil(profile["clear_km"] * K / self.step)))
        self.wall_plan = _dilate(self.wall, int(math.ceil((profile["clear_km"] + profile["plan_margin_km"]) * K / self.step)))
        # search grid: a cell is blocked when its centre is within clear_km + plan_margin_km of a wall
        self.n = int(self.size // CELL)
        c = ((np.arange(self.n) + 0.5) * CELL // self.step).astype(int)
        self.blocked_plan = self.wall_plan[np.ix_(c, c)]
        self.blocked = self.wall_near[np.ix_(c, c)]          # fallback where the margin closes every gap
        tg = territory_grid(theater) & 3
        g = (np.arange(self.n) * CELL // 2048).astype(int).clip(0, 15)
        self.enemy_grid = (tg[np.ix_(g, g)] == 0)
        T = places(theater)
        self.airbases = {r["name"]: (float(r["x"]), float(r["y"])) for r in T["records"]
                         if r["name"] and r["flags"][0] == AIRBASE_FLAG}
        # no launches or radar IDs within 20 km of these (ds:663E: records with flags & 0x201 and & 0x500)
        self.safe_fields = np.array([(r["x"], r["y"]) for r in T["records"]
                                     if r["name"] and r["flags"][0] & 0x201 and r["flags"][0] & 0x500], float).reshape(-1, 2)
        self.radars = []
        for r in mission["radars"]:
            k = r.get("kind") or SYSTEM_KIND.get(r.get("system"))
            if k: self.radars.append({**r, "kind": int(k)})
        self.tgrid = territory_grid(theater)
        sa14 = (self.tgrid & 0x10) > 0
        if sa14_live is not None:                       # cells nav read from ds:85FC at takeoff, [[cx, cy], ...]
            sa14 = np.zeros((16, 16), bool)
            for cx, cy in sa14_live: sa14[cy, cx] = True
        self.sa14_cells = sa14 & (not self.night)
        self.sa14_grid = self.sa14_cells[np.ix_(g, g)]
        self.targets = mission["targets"]

    # --- point queries (arrays of game-unit coordinates) ---
    def _fine(self, x, y):
        i = np.clip((np.asarray(x) // self.step).astype(int), 0, self.H.shape[1] - 1)
        j = np.clip((np.asarray(y) // self.step).astype(int), 0, self.H.shape[0] - 1)
        return j, i

    def too_close(self, x, y, plan=False):
        """True where a wall is within clear_km (plan: clear_km + plan_margin_km)."""
        j, i = self._fine(x, y); return (self.wall_plan if plan else self.wall_near)[j, i]

    def enemy(self, x, y):
        gx = (np.asarray(x) // 2048).astype(int).clip(0, 15); gy = (np.asarray(y) // 2048).astype(int).clip(0, 15)
        return (territory_grid(self.th)[gy, gx] & 3) == 0

    def cell32(self, x, y):
        """(gy, gx) index arrays of the 32 km territory cell."""
        return (np.asarray(y) // 2048).astype(int).clip(0, 15), (np.asarray(x) // 2048).astype(int).clip(0, 15)

    def clutter(self, x, y):
        """Grid byte of the 32 km cell (radar_game.site_return uses bits 0x0C)."""
        return self.tgrid[self.cell32(x, y)]

    def terrain_type(self, x, y):
        """0 mountain, 1 rough, 2 flat, 3 sea."""
        return (self.clutter(x, y) >> 2) & 3

    def sa14(self, x, y):
        return self.sa14_cells[self.cell32(x, y)]

    def segment_clear(self, a, b, plan=False):
        """No wall within clear_km (plan: + plan_margin_km) of segment a-b; cached (routes re-test
        the same arc chords many times while corners are repaired)."""
        key = (round(float(a[0])), round(float(a[1])), round(float(b[0])), round(float(b[1])), plan)
        r = self._seg_cache.get(key)
        if r is None:
            L = math.dist(a, b); n = max(2, int(L / (0.25 * K)) + 1)
            t = np.arange(n) / (n - 1)
            i = ((a[0] + t * (b[0] - a[0])) // self.step).astype(int); j = ((a[1] + t * (b[1] - a[1])) // self.step).astype(int)
            np.clip(i, 0, self.H.shape[1] - 1, out=i); np.clip(j, 0, self.H.shape[0] - 1, out=j)
            r = self._seg_cache[key] = not (self.wall_plan if plan else self.wall_near)[j, i].any()
        return r

    def segment_clear_km(self, a, b, km):
        """segment_clear with another wall clearance (loiters)."""
        if abs(km - self.P["clear_km"]) < 1e-9: return self.segment_clear(a, b)
        if km not in self._masks: self._masks[km] = _dilate(self.wall, int(math.ceil(km * K / self.step)))
        m = self._masks[km]
        L = math.dist(a, b); n = max(2, int(L / (0.25 * K)) + 1); t = np.arange(n) / (n - 1)
        j, i = self._fine(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
        return not m[j, i].any()

    def cell(self, p):
        return (int(np.clip(p[1] // CELL, 0, self.n - 1)), int(np.clip(p[0] // CELL, 0, self.n - 1)))

    def centre(self, j, i):
        return ((i + 0.5) * CELL, (j + 0.5) * CELL)

    def place(self, spec):
        """An airbase name or {"x":, "y":} (e.g. a carrier) -> (x, y, label, is_carrier)."""
        if isinstance(spec, str):
            if spec not in self.airbases:
                raise ValueError(f"unknown airbase {spec!r}; {self.th} airbases: {', '.join(self.airbases)}")
            return (*self.airbases[spec], spec, False)
        return float(spec["x"]), float(spec["y"]), spec.get("label", "Carrier"), spec.get("carrier", True)
