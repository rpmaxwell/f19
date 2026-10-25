"""SUPERSEDED by analysis/planner (python analysis/planner <plan_dir>); kept to reproduce old plans.

Stealth route planner for the F-19 briefing map.

Radar model (from the manual, pp. 75-76): a doppler radar sees the radial
component of your motion (arc around it at constant range); a pulse radar sees
your side aspect (fly straight at/away from it). Strength falls off with range
and drops to SHADOW where terrain blocks the line of sight. Terrain is the game's own
3D world (analysis/terrain_3d.py, env THEATER): mountains are walls the route goes
around at a constant low cruise altitude, and hugging their far side from a radar
is rewarded through the shadow. Each grid move costs
    length * (1 + LAMBDA * detection(cell, heading))
and Dijkstra finds the cheapest path per leg.

Geometry comes from logs/radar_sites/geometry.json (screen px of the 1280x856
window capture); the briefing map is square: ~1.76 px/km in x, ~1.32 px/km in y.
"""
import heapq, json, math, os, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
MAP = (32, 96, 928, 768)            # map rectangle in capture px
CELL = 2.0                          # km per grid cell
LAMBDA = float(sys.argv[1]) if len(sys.argv) > 1 else 25.0
EPS = 0.15                          # residual detection at the ideal aspect

D = Path(os.environ.get("PLAN_DIR", ROOT / "logs/radar_sites"))
SITES = json.load(open(D / "sites.json"))          # from analysis/radar_geometry.py
MISSION = json.load(open(D / "mission.json"))      # named stops in briefing px
SX = float(np.nanmedian([g["px_per_km_x"] for g in SITES]))
SY = float(np.nanmedian([g["px_per_km_y"] for g in SITES]))

def to_km(px, py): return ((px - MAP[0]) / SX, (py - MAP[1]) / SY)
def to_px(kx, ky): return (kx * SX + MAP[0], ky * SY + MAP[1])

radars = [(to_km(g["cx"], g["cy"]), g["type"], g["range_km"], g["name"]) for g in SITES]

# --- terrain factor from map colours (mountains/hills blunt radar) ---
img = Image.open(sorted(list(D.glob("site_*_0.png")) or list((ROOT / "logs/radar_sites").glob("site_*_0.png")))[-1]).convert("RGB")   # terrain: briefing map (static per theater)
arr = np.asarray(img).astype(int)[MAP[1]:MAP[3], MAP[0]:MAP[2]]
W = int((MAP[2] - MAP[0]) / SX / CELL); H = int((MAP[3] - MAP[1]) / SY / CELL)
def frac(mask):
    out = np.zeros((H, W))
    for j in range(H):
        for i in range(W):
            x0, x1 = int(i * CELL * SX), int((i + 1) * CELL * SX)
            y0, y1 = int(j * CELL * SY), int((j + 1) * CELL * SY)
            out[j, i] = mask[y0:y1, x0:x1].mean()
    return out
R_, G_, B_ = arr[..., 0], arr[..., 1], arr[..., 2]
mtn = ((R_ == 159) & (G_ == 90)) | ((R_ == 85) & (G_ == 85) & (B_ == 85)) | (R_ == 236) | ((R_ == 156) & (G_ == 31))
hill = (R_ == 170) & (G_ == 170) & (B_ == 170)
wood = (R_ == 137) & (G_ == 252)
# --- 3D terrain ------------------------------------------------------------------
# Real terrain comes from the game's own 3D world (analysis/terrain_3d.py ->
# logs/theater/<TH>_terrain.npz: top-of-terrain height in ft, same units as the altimeter,
# which measures height above a flat world and never shows mountains). Needs THEATER and
# the plan's px->game affine (plan_mission.py sets both). Without them, fall back to the
# briefing-map relief colours (art, not terrain: every crash happened outside them).
# Mountains are flown AROUND, never over: climbing over one exposes the plane to every
# radar around (manual p. 41-42: penetrate below 500 ft, ~450 ft, "with maximum use of
# terrain masking, such as flying between mountains"). So any solid terrain of MTN_FT or
# more is a wall, and the route keeps the cruise altitude; the ridges it threads between
# then hide it from radars on their far side (see shadowed()).
CRUISE_FT = float(os.environ.get("CRUISE_FT", 450))   # planned (constant) cruise altitude
MTN_FT = float(os.environ.get("MTN_FT", 200))         # terrain this tall is a wall (below: forest/building blocks)
RADAR_FT = float(os.environ.get("RADAR_FT", 30))      # radar antenna height for line of sight
THEATER = os.environ.get("THEATER")
_tz = ROOT / "logs/theater" / f"{THEATER}_terrain.npz"
HEIGHT = HFINE = None                                 # ft: per planner cell (max), and on a 0.5 km sub-grid
if THEATER and _tz.exists() and (D / "px_to_game.json").exists():
    _z = np.load(_tz); HG, HSTEP = _z["height_ft"].astype(float), int(_z["step_wp"])
    _A = np.array(json.load(open(D / "px_to_game.json"))["affine"])
    def _heights(sub, reduce):
        kx = (np.arange(W * sub) + 0.5) * CELL / sub; ky = (np.arange(H * sub) + 0.5) * CELL / sub
        KX, KY = np.meshgrid(kx, ky)
        gx = (KX * SX + MAP[0]) * _A[0, 0] + (KY * SY + MAP[1]) * _A[1, 0] + _A[2, 0]
        gy = (KX * SX + MAP[0]) * _A[0, 1] + (KY * SY + MAP[1]) * _A[1, 1] + _A[2, 1]
        i = np.clip((gx // HSTEP).astype(int), 0, HG.shape[1] - 1); j = np.clip((gy // HSTEP).astype(int), 0, HG.shape[0] - 1)
        h = HG[j, i]
        return h.reshape(H, sub, W, sub).max(axis=(1, 3)) if reduce else h
    HEIGHT = _heights(8, True)                        # 0.25 km samples, worst case per 2 km cell
    HFINE = _heights(4, False)                        # 0.5 km samples for radar line of sight
    MOUNTAIN = HEIGHT >= MTN_FT                       # walls: go around
    print(f"3D terrain {THEATER}: mountains (>= {MTN_FT:.0f} ft) in {MOUNTAIN.mean() * 100:.1f}% of cells, "
          f"highest {HEIGHT.max():.0f} ft; line of sight at {CRUISE_FT:.0f} ft cruise")
else:
    MOUNTAIN = frac(mtn) > 0.25                       # brown/red relief (fallback)
# Mountains are impassable sideways, with a safety margin of
# MTN_MARGIN cells (2 km each: nav cross-track error + the footprint edge).
MTN_MARGIN = int(os.environ.get("MTN_MARGIN", 1))
BLOCKED = MOUNTAIN.copy()
for _ in range(MTN_MARGIN):
    b = BLOCKED.copy()
    b[1:, :] |= BLOCKED[:-1, :]; b[:-1, :] |= BLOCKED[1:, :]; b[:, 1:] |= BLOCKED[:, :-1]; b[:, :-1] |= BLOCKED[:, 1:]
    BLOCKED = b
# Known 3D-terrain hazards (crash sites) as keep-out discs: only with CRASH_DISCS=1 now that
# the real terrain is decoded (plan_mission.py writes obstacles.json).
if (D / "obstacles.json").exists():
    jj, ii = np.mgrid[0:H, 0:W]
    for o in json.load(open(D / "obstacles.json")):
        okx, oky = to_km(*o["px"])
        BLOCKED |= np.hypot((ii + 0.5) * CELL - okx, (jj + 0.5) * CELL - oky) <= o["radius_km"]
terrain = 1 - 0.35 * frac(hill) - 0.15 * frac(wood)
SHADOW = 0.2                                    # radar strength left behind a ridge
MTN_COST = 1000.0                               # cost multiplier for crossing a mountain cell

# --- detection cost per cell per move direction ---
MOVES = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3)
         if (dx, dy) != (0, 0) and math.gcd(abs(dx), abs(dy)) == 1]
yy, xx = np.mgrid[0:H, 0:W]
cx_km, cy_km = (xx + 0.5) * CELL, (yy + 0.5) * CELL

def shadowed(rx, ry, rng):
    """True where terrain blocks the line of sight from the radar to a plane at CRUISE_FT
    in that cell: flying on the far side of a ridge from a radar hides you from it."""
    m = np.zeros((H, W), bool)
    near = np.hypot(cx_km - rx, cy_km - ry) < rng * 1.1
    px, py = cx_km[near], cy_km[near]
    hit = np.zeros(px.shape, bool)
    if HFINE is None:                                 # fallback: any relief cell on the line
        for t in np.linspace(0.04, 0.96, 48):
            sx = np.clip(((px + t * (rx - px)) / CELL).astype(int), 0, W - 1)
            sy = np.clip(((py + t * (ry - py)) / CELL).astype(int), 0, H - 1)
            hit |= MOUNTAIN[sy, sx]
    else:
        f = HFINE.shape[0] / H                        # sub-samples per cell
        n = max(16, int(rng * 1.1 / 0.4))             # ~0.4 km steps at full range
        for t in np.linspace(0.5 / n, 1 - 0.5 / n, n):
            sx = np.clip(((px + t * (rx - px)) / CELL * f).astype(int), 0, HFINE.shape[1] - 1)
            sy = np.clip(((py + t * (ry - py)) / CELL * f).astype(int), 0, HFINE.shape[0] - 1)
            hit |= HFINE[sy, sx] > CRUISE_FT + (RADAR_FT - CRUISE_FT) * t   # t: 0 = plane, 1 = radar
    m[near] = hit
    return m

SHADOWS = [shadowed(rx, ry, rng) for (rx, ry), _, rng, _ in radars]

def detection(ux, uy):
    det = np.zeros((H, W))
    for ((rx, ry), typ, rng, _), shadow in zip(radars, SHADOWS):
        dx, dy = cx_km - rx, cy_km - ry
        d = np.hypot(dx, dy) + 1e-6
        strength = np.clip(1.1 - d / rng, 0, None) ** 2      # ~0 just beyond quoted range
        strength = np.where(shadow, strength * SHADOW, strength)
        radial = np.abs(ux * dx + uy * dy) / d                # |cos| of aspect
        aspect = radial if typ == "D" else np.sqrt(1 - radial ** 2)
        w = 1.5 if typ == "D" else 1.0
        det += w * strength * (EPS + (1 - EPS) * aspect)
    return det * terrain

COST = []
for dx, dy in MOVES:
    L = math.hypot(dx, dy)
    COST.append(L * CELL * (1 + LAMBDA * detection(dx / L, dy / L)) * np.where(MOUNTAIN, MTN_COST, 1.0))
    COST[-1][BLOCKED] = np.inf                                          # walls

# --- turn-aware search -------------------------------------------------------
# State = (cell, direction of travel). Each step may keep its direction or move to
# an angularly adjacent one (~18-27 deg over >= 2 km, i.e. turn radius >~ 5-7 km at
# sensitivity-1 rates), and every heading change costs its banked time as distance:
#   turn_cost_km = degrees / TURN_RATE * SPEED_KMS * BANK_PENALTY
# Measured 2026-10-14 (EMV byte vs turn rate, level flight 160-235 kt): EMV 7 level, ~10 at
# 0.6-0.8 deg/s, 12-13 at 1.0-1.2, 18-20 at 1.3-2.0. Extra exposure per degree turned is lowest
# around 0.8 deg/s, so the nav caps turns there and BANK_PENALTY = (10 - 7) / 7.
TURN_RATE = float(os.environ.get("TURN_RATE", 0.8))        # deg/s, the nav's turn-rate cap (H_RATE_MAX)
SPEED_KMS = float(os.environ.get("SPEED_KMS", 0.186))      # ~190 kt ground speed
BANK_PENALTY = float(os.environ.get("BANK_PENALTY", 0.43))
ORDER = sorted(range(len(MOVES)), key=lambda k: math.atan2(MOVES[k][0], -MOVES[k][1]) % (2 * math.pi))
NDIR = len(ORDER)
DIR_ANG = [math.degrees(math.atan2(MOVES[k][0], -MOVES[k][1])) % 360 for k in ORDER]
def _dang(a, b): return abs((DIR_ANG[a] - DIR_ANG[b] + 180) % 360 - 180)
TURN_KM = {(h, (h + d) % NDIR): _dang(h, (h + d) % NDIR) / TURN_RATE * SPEED_KMS * BANK_PENALTY
           for h in range(NDIR) for d in (-1, 0, 1)}

def plan(a, b, h0=None):
    """A* from cell a to cell b; h0 = arrival direction index from the previous leg (None = free).
    Returns (polyline in km, final direction index)."""
    sa = (int(a[1] / CELL), int(a[0] / CELL)); sb = (int(b[1] / CELL), int(b[0] / CELL))
    hk = lambda j, i: math.hypot(j - sb[0], i - sb[1]) * CELL                       # admissible
    dist = np.full((H, W, NDIR), np.inf); prev = {}
    pq = []
    for h in (range(NDIR) if h0 is None else [h0]):
        dist[sa[0], sa[1], h] = 0; heapq.heappush(pq, (hk(*sa), 0.0, (sa[0], sa[1], h)))
    goal = None
    while pq:
        f, c, (j, i, h) = heapq.heappop(pq)
        if (j, i) == sb: goal = (j, i, h); break
        if c > dist[j, i, h]: continue
        for d in (-1, 0, 1):
            nh = (h + d) % NDIR; k = ORDER[nh]; dx, dy = MOVES[k]
            nj, ni = j + dy, i + dx
            if 0 <= nj < H and 0 <= ni < W:
                step = COST[k][j, i]
                if not np.isfinite(step):
                    if (j, i) != sa and (nj, ni) != sb: continue
                    step = math.hypot(dx, dy) * CELL * MTN_COST
                nc = c + step + TURN_KM[(h, nh)]
                if nc < dist[nj, ni, nh]:
                    dist[nj, ni, nh] = nc; prev[(nj, ni, nh)] = (j, i, h)
                    heapq.heappush(pq, (nc + hk(nj, ni), nc, (nj, ni, nh)))
    path = [goal]
    while path[-1] in prev: path.append(prev[path[-1]])
    return [((i + 0.5) * CELL, (j + 0.5) * CELL) for j, i, _ in reversed(path)], goal[2]

def exposure(pts):
    """Integrated detection along a polyline, km flown over mountains, and km where
    terrain hid the plane from at least one radar that had it in range."""
    tot = mtn_km = hid_km = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L == 0: continue
        n = max(1, int(L / 0.5)); ux, uy = (x1 - x0) / L, (y1 - y0) / L
        for s in range(n):
            px, py = x0 + (s + 0.5) / n * (x1 - x0), y0 + (s + 0.5) / n * (y1 - y0)
            j, i = min(H - 1, int(py / CELL)), min(W - 1, int(px / CELL))
            d_ = 0.0; hid = False
            for ((rx, ry), typ, rng, _), shadow in zip(radars, SHADOWS):
                ddx, ddy = px - rx, py - ry; d = math.hypot(ddx, ddy) + 1e-6
                hid |= bool(shadow[j, i]) and d < rng
                st = max(0.0, 1.1 - d / rng) ** 2 * (SHADOW if shadow[j, i] else 1.0)
                rad = abs(ux * ddx + uy * ddy) / d
                asp = rad if typ == "D" else math.sqrt(max(0, 1 - rad * rad))
                d_ += (1.5 if typ == "D" else 1.0) * st * (EPS + (1 - EPS) * asp)
            tot += d_ * terrain[j, i] * L / n
            mtn_km += MOUNTAIN[j, i] * L / n
            hid_km += hid * L / n
    return tot, mtn_km, hid_km

def _clear(a, b):
    """True if the straight line a->b (km) stays out of BLOCKED cells (endpoints exempt)."""
    n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / (CELL / 4)) + 1
    for t in np.linspace(0, 1, n)[1:-1]:
        j = min(H - 1, int((a[1] + (b[1] - a[1]) * t) / CELL)); i = min(W - 1, int((a[0] + (b[0] - a[0]) * t) / CELL))
        if BLOCKED[j, i]: return False
    return True

def simplify(pts, tol=2.5):
    """Douglas-Peucker, but never shortcut across mountains or their margin."""
    if len(pts) < 3: return pts
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    L = math.hypot(x1 - x0, y1 - y0) or 1e-9
    ds = [abs((x1 - x0) * (y0 - y) - (x0 - x) * (y1 - y0)) / L for x, y in pts[1:-1]]
    k = int(np.argmax(ds)) + 1
    if ds[k - 1] < tol and _clear(pts[0], pts[-1]): return [pts[0], pts[-1]]
    if ds[k - 1] < tol: k = len(pts) // 2
    return simplify(pts[:k + 1], tol)[:-1] + simplify(pts[k:], tol)

if __name__ == "__main__":
    stops = [(n, to_km(*xy)) for n, xy in MISSION["stops"]]
    full, wps = [], []
    def nearest_dir(a, b):
        ang = math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360
        return min(range(NDIR), key=lambda h: abs((DIR_ANG[h] - ang + 180) % 360 - 180))
    hdir = None
    for (na, a), (nb, b) in zip(stops, stops[1:]):
        if na.startswith("Takeoff") or na in ("Approach fix", "Final") or nb.startswith("Landing"):
            # runway climb-out / final approach: fixed straight segments along the runway
            p, sp, hdir = [a, b], [a, b], nearest_dir(a, b)
        else:
            p, hdir = plan(a, b, hdir); sp = simplify(p)
        full += p if not full else p[1:]
        wps += [(sp[0], na)] + [(q, "") for q in sp[1:-1]] if not wps else [(sp[0], na)] + [(q, "") for q in sp[1:-1]]
    wps.append((stops[-1][1], stops[-1][0]))
    route = [w for w, _ in wps]
    direct = [s for _, s in stops]
    def length(p): return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(p, p[1:]))
    print(f"px/km x={SX:.3f} y={SY:.3f}  grid {W}x{H} @ {CELL} km  LAMBDA={LAMBDA}")
    for label, p in (("default", direct), ("planned", full)):
        e, m, hk = exposure(p)
        print(f"{label} route: {length(p):6.0f} km  exposure {e:8.1f}  over mountains {m:5.1f} km  terrain-masked {hk:5.1f} km")
    print("\n#   heading  dist_km  cum_km  note")
    cum = 0.0
    legs = []
    for k, ((a, na), (b, nb)) in enumerate(zip(wps, wps[1:])):
        dx, dy = b[0] - a[0], b[1] - a[1]
        hdg = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
        d = math.hypot(dx, dy); cum += d
        legs.append((hdg, d)); print(f"{k+1:<3d} {hdg:6.0f}   {d:6.1f}  {cum:6.1f}  -> {nb}")
    out = {"waypoints_km": [(w, n) for w, n in wps], "legs": legs}
    if (D / "px_to_game.json").exists():                    # also emit game coordinates
        A = np.array(json.load(open(D / "px_to_game.json"))["affine"])
        out["waypoints_game"] = [(list(np.array([*to_px(*w), 1.0]) @ A), n) for w, n in wps]
    json.dump(out, open(D / "route.json", "w"), indent=1)
    # terrain for the nav's look-ahead warning (cells in planner km; px = km*S + map origin)
    extra = dict(height_game=HG.astype(np.uint16), height_step=HSTEP) if HEIGHT is not None else {}
    np.savez_compressed(D / "terrain.npz", mountain=MOUNTAIN, blocked=BLOCKED, cell_km=CELL, sx=SX, sy=SY, origin=np.array(MAP[:2]), **extra)
    # overlay
    ov = img.copy(); dr = ImageDraw.Draw(ov)
    if HEIGHT is not None:                                  # decoded mountains (walls) in brown
        tint = Image.new("RGBA", ov.size, (0, 0, 0, 0)); dt = ImageDraw.Draw(tint)
        for j, i in zip(*np.nonzero(MOUNTAIN)):
            (x0, y0), (x1, y1) = to_px(i * CELL, j * CELL), to_px((i + 1) * CELL, (j + 1) * CELL)
            dt.rectangle((x0, y0, x1, y1), fill=(120, 60, 0, 150))
        ov = Image.alpha_composite(ov.convert("RGBA"), tint).convert("RGB"); dr = ImageDraw.Draw(ov)
    for (rx, ry), typ, rng, n in radars:
        px, py = to_px(rx, ry); a, b = rng * SX, rng * SY
        col = (200, 0, 0) if typ == "D" else (255, 140, 0)
        if typ == "D": dr.ellipse((px - a, py - b, px + a, py + b), outline=col, width=3)
        else:
            for t in range(0, 360, 6):
                q = math.radians(t); dr.ellipse((px + a * math.cos(q) - 2, py + b * math.sin(q) - 2, px + a * math.cos(q) + 2, py + b * math.sin(q) + 2), fill=col)
    dr.line([to_px(*p) for p in direct], fill=(80, 80, 80), width=2)
    dr.line([to_px(*p) for p in full], fill=(255, 0, 255), width=4)
    for k, (w, n) in enumerate(wps):
        px, py = to_px(*w); dr.ellipse((px - 6, py - 6, px + 6, py + 6), fill=(255, 255, 255), outline=(0, 0, 0))
        dr.text((px + 8, py - 14), str(k), fill=(0, 0, 0))
    ov.save(D / "route_overlay.png")
