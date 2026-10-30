"""What the map is: theater terrain, territory, airbases, and the mission in memory.

All positions in game units (waypoint-table units, 64 per km). Sources:
  logs/theater/<TH>_terrain.npz   decoded 3D terrain (f19.gamedata.terrain), ft above the flat world
  logs/theater/<TH>.json          .WLD place records (f19.gamedata.wld.places): airbases
  <plan_dir>/mission_mem.json     targets + radars from scripts/read_mission.py
  f19.gamedata.wld.grid           16x16 32 km cell grid: bits 0-1 side (cold-war scoring), bits 2-3 terrain
                                  (0 mountain, 1 rough, 2 flat, 3 sea: radar clutter), bit 4 SA-14 cell
  plan.json "settings"            opponent / war / night (radar and SA-14 rules), roe (score)
"""
import math

import numpy as np

from f19.core import units
from f19.core.settings import settings as S
from f19.gamedata import terrain as _terrain
from f19.gamedata.radar_systems import RADAR_NAMES
from f19.gamedata.wld import grid as territory_grid, places
from f19.rules import radar as _rr

OPP = {"green": 0, "regular": 1, "veteran": 2, "elite": 3}

K = float(units.K)       # game units per km (a float here: the planner's arithmetic has always used 64.0)
CELL_KM = units.PLAN_CELL_KM   # search grid
CELL = CELL_KM * K
AIRBASE_FLAG = 1025      # .WLD record flags[0] of an airbase
# Map distance per game-clock second is 0.784 x the indicated airspeed (memlog 1792546698, 2026-10-21: 2234
# two-minute windows, p10-p90 0.774-0.795; the 2 s clock tick and 64 units per km as elsewhere). Every
# knots -> time / distance conversion in the planner uses this: flight time, fuel, samples, pulses per km.
# Before it the plans ran ~25 % short on time and fuel (flown: 104 game min, 6332 lb; planned 80 min, 4786 lb).
GROUND_PER_KT = units.GROUND_PER_KT       # 0.788, pooled over 16 logged flights (analysis/fuel_log.py summary), 2026-10-21
GROUND_KMH_PER_KT = units.GROUND_KMH_PER_KT   # map km per game hour per indicated kt (1.852 x 0.788)
KT_KMH = GROUND_KMH_PER_KT                # the old name (NOT km/h per kt: ground km/h per indicated kt)

# Radar system name -> radar type code, for mission files written before read_mission saved "kind": the briefing names
# from f19.gamedata.radar_systems, then EGAME's weapon names for the other codes 1-15.


def system_kinds():
    """{system name: radar type} (read_mission.RADAR order: later duplicates win, as there)."""
    names = dict(RADAR_NAMES)
    for t in range(1, 16):
        if t not in names: names[t] = _rr.weapon_name(t)
    return {sa: k for k, sa in names.items()}


def terrain_npz(theater):
    """logs/theater/<TH>_terrain.npz, decoded from the game files (f19.gamedata.terrain) when missing."""
    tz = S.logs / "theater" / f"{theater}_terrain.npz"
    if not tz.exists(): _terrain.write_outputs(theater)
    return tz


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
        z = np.load(terrain_npz(theater))
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
        kinds = None
        for r in mission["radars"]:
            k = r.get("kind")
            if not k:
                kinds = kinds or system_kinds()
                k = kinds.get(r.get("system"))
            if k: self.radars.append({**r, "kind": int(k)})
        self.tgrid = territory_grid(theater)
        sa14 = (self.tgrid & 0x10) > 0
        if sa14_live is not None:                       # cells nav read from ds:85FC at takeoff, [[cx, cy], ...]
            sa14 = np.zeros((16, 16), bool)
            for cx, cy in sa14_live: sa14[cy, cx] = True
        self.sa14_cells = sa14 & (not self.night)
        self.sa14_grid = self.sa14_cells[np.ix_(g, g)]
        self.g32 = g                                    # search-grid index -> 32 km cell index
        self.sa14_margin = profile["sa14_margin_km"] * K
        mode = settings.get("sa14", "auto")
        if mode not in ("auto", "hard", "soft"): raise ValueError(f"settings sa14: {mode!r} is not auto / hard / soft")
        # live cells are walls (not a price) when the rule says so: Elite spawns launch ~95 % of the time and a launch
        # raises the network alert (docs/game/memory-map.md "SA-14 cells")
        self.sa14_hard = bool(self.sa14_cells.any()) and (mode == "hard" or (mode == "auto" and self.opp >= profile["sa14_hard_from_opp"]))
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

    def sa14_cell_of(self, x, y):
        """(cx, cy) of the live SA-14 cell containing the point, or None."""
        return (int(x) // 2048, int(y) // 2048) if self.sa14(x, y) else None

    def sa14_near(self, x, y, margin=None):
        """(cx, cy) arrays: a live SA-14 cell the point is in or within margin (units; default the profile's
        sa14_margin_km) of, -1 where none. The point's own cell is preferred."""
        m = self.sa14_margin if margin is None else margin
        x, y = np.asarray(x, float), np.asarray(y, float)
        cx, cy = np.full(x.shape, -1), np.full(x.shape, -1)
        for dx, dy in ((0, 0), (-m, -m), (m, -m), (-m, m), (m, m)):
            i = ((x + dx) // 2048).astype(int).clip(0, 15); j = ((y + dy) // 2048).astype(int).clip(0, 15)
            hit = self.sa14_cells[j, i] & (cx < 0)
            cx, cy = np.where(hit, i, cx), np.where(hit, j, cy)
        return cx, cy

    def sa14_clear(self, a, b, step_km=1.0, margin=None):
        """Segment a-b stays out of the live SA-14 cells and their margin."""
        n = max(2, int(math.dist(a, b) / (step_km * K)) + 1); t = np.arange(n) / (n - 1)
        return not (self.sa14_near(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), margin)[0] >= 0).any()

    def sa14_clear_many(self, chords, step_km=1.0, margin=None):
        """[sa14_clear(a, b) for a, b in chords] in one sa14_near call over all their samples (the same samples, point by point)."""
        if not chords: return []
        xs, ys, cut = [], [], [0]
        for a, b in chords:
            n = max(2, int(math.dist(a, b) / (step_km * K)) + 1); t = np.arange(n) / (n - 1)
            xs.append(a[0] + t * (b[0] - a[0])); ys.append(a[1] + t * (b[1] - a[1])); cut.append(cut[-1] + n)
        hit = self.sa14_near(np.concatenate(xs), np.concatenate(ys), margin)[0] >= 0
        return [not hit[lo:hi].any() for lo, hi in zip(cut, cut[1:])]

    def sa14_entries(self, pts, step_km=0.5):
        """Where the polyline pts (xy list) is in or within the margin of a live SA-14 cell: [(cell, xy of the first sample, index
        of the leg)], one per visit. A visit that is the route's own start (the plane already sits there) or end (landing
        field) is not reported while the route is still in that cell's neighbourhood. Samples every step_km."""
        out, cur, seen = [], None, []                       # seen: (cell or None, xy, leg) per sample
        for k, (a, b) in enumerate(zip(pts, pts[1:])):
            n = max(1, int(math.dist(a, b) / (step_km * K)))
            t = np.arange(n) / n
            x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            cx, cy = self.sa14_near(x, y)
            seen += [((int(c), int(r)) if c >= 0 else None, (float(xi), float(yi)), k) for c, r, xi, yi in zip(cx, cy, x, y)]
        if pts:
            cx, cy = self.sa14_near(*map(float, pts[-1]))
            seen.append(((int(cx), int(cy)) if cx >= 0 else None, tuple(map(float, pts[-1])), max(0, len(pts) - 2)))
        lo, hi = 0, len(seen)
        if seen and seen[0][0] is not None:                 # leading run in the start's own cell
            while lo < hi and seen[lo][0] == seen[0][0]: lo += 1
        if hi > lo and seen[-1][0] is not None:             # trailing run in the end's own cell
            end = seen[-1][0]
            while hi > lo and seen[hi - 1][0] == end: hi -= 1
        for c, xy, k in seen[lo:hi]:
            if c is not None and c != cur: out.append((c, xy, k))
            cur = c
        return out

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
