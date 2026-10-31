"""SUPERSEDED by src/f19/planner (python -m f19.planner <plan_dir>); kept to reproduce old plans.

Build a nav route from hand-placed corners, rounding every corner to the turn radius.

The nav caps turns at 0.8 deg/s (H_RATE_MAX; higher rates raise EMV sharply), which is a
~20 km turn radius at cruise. A corner of angle D needs R*tan(D/2) of straight leg on each
side to fly as an arc, so short legs between big turns can't be flown as drawn.

  smooth_route.py <plan_dir> <theater> <corners.json> [R_km]

corners.json: {"corners": [[x, y, "label"], ...], "orbit": {...} optional}
  x, y in waypoint units (64/km). A label starting with "*" marks an overfly point
  (target): the route must pass straight through it, so its turn must be < 10 deg.
  orbit: {"after": <corner index>, "center": [x, y], "laps": n, "dir": "L"|"R",
          "label": "ORBIT ..."} - laps of a circle of radius R joined tangentially
          between corner <after> and the next corner (the corners either side must
          lie outside the circle).
Writes <plan_dir>/route_smooth.json (waypoints_game; nav.py --route) and route_smooth.png,
and prints per leg: length, turn fits, terrain, territory, radars in range + line of sight
with aspect (radial / tangential), and cold-war samples in enemy cells.
"""
import json, math, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).resolve().parent))
from radar_los import visible
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.gamedata.wld import side, grid

ROOT = Path(__file__).resolve().parent.parent
K, CRUISE_FT, CLEAR_KM, STEP_KM = 64.0, 450.0, 2.0, 4.0
SAMPLE_KM = 6.2      # one 64 game-s cold-war sample per ~6 km at ~190 kt
plan, TH, spec = Path(sys.argv[1]), sys.argv[2], json.load(open(sys.argv[3]))
R = (float(sys.argv[4]) if len(sys.argv) > 4 else 20.0) * K
M = json.load(open(plan / "mission_mem.json"))
z = np.load(ROOT / "logs/theater" / f"{TH}_terrain.npz"); H, ST = z["height_ft"], int(z["step_wp"])
C = [(np.array(c[:2], float), c[2] if len(c) > 2 else "") for c in spec["corners"]]

def hdg(v): return math.degrees(math.atan2(v[0], -v[1])) % 360
def unit(v): return v / np.linalg.norm(v)
def clear(p):
    o = int(CLEAR_KM * K / ST); i, j = int(p[0] // ST), int(p[1] // ST)
    return H[max(0, j - o):j + o + 1, max(0, i - o):i + o + 1].max() < 200

def arc(c, r, a0, a1, ccw):
    """Points on a circle (centre c) from angle a0 to a1 (math angles in the y-down plane)."""
    span = (a1 - a0) % (2 * math.pi) if not ccw else -((a0 - a1) % (2 * math.pi))
    n = max(1, int(abs(span) * r / (STEP_KM * K)))
    return [c + r * np.array([math.cos(a0 + span * k / n), math.sin(a0 + span * k / n)]) for k in range(1, n + 1)]

def fillet(p0, p1, p2):
    """Arc points replacing corner p1 (between legs p0-p1 and p1-p2), and its tangent length."""
    u, w = unit(p1 - p0), unit(p2 - p1)
    D = math.acos(max(-1, min(1, float(u @ w))))
    if D < math.radians(2): return [p1], 0.0, 0.0
    t = R * math.tan(D / 2)
    a, b = p1 - u * t, p1 + w * t
    cross = u[0] * w[1] - u[1] * w[0]              # >0: right turn in the y-down plane
    n = np.array([-u[1], u[0]]) if cross > 0 else np.array([u[1], -u[0]])
    c = a + n * R
    ang = lambda q: math.atan2(q[1] - c[1], q[0] - c[0])
    return [a] + arc(c, R, ang(a), ang(b), ccw=cross < 0), t, math.degrees(D)

def tangent_entry(p, c, ccw):
    """Point where a line from p meets the circle (c, R) tangentially, flying ccw/cw."""
    d = p - c; L = np.linalg.norm(d); a = math.atan2(d[1], d[0]); b = math.acos(R / L)
    return a - b if ccw else a + b                # angle of the tangent point on the circle

# --- corners -> polyline with fillets; optional orbit spliced between two corners
pts, ok, report = [(C[0][0], C[0][1])], True, []
tan_prev = 0.0
orb = spec.get("orbit")
def orbit_entry(p, o):
    c, ccw = np.array(o["center"], float), o.get("dir", "L") == "L"
    a = tangent_entry(p, c, ccw); return c + R * np.array([math.cos(a), math.sin(a)])
for i in range(1, len(C)):
    p1, lab = C[i]
    p0 = C[i - 1][0]
    if orb and i - 1 == orb["after"]:
        # leave corner i-1 straight to the circle, fly the laps, leave tangentially toward corner i
        c, ccw = np.array(orb["center"], float), orb.get("dir", "L") == "L"
        a_in, a_out = tangent_entry(p0, c, ccw), tangent_entry(p1, c, not ccw)
        pts.append((c + R * np.array([math.cos(a_in), math.sin(a_in)]), orb["label"]))
        full = []
        for _ in range(int(orb.get("laps", 1))):
            full += arc(c, R, a_in, a_in + (1e-6 if ccw else -1e-6), ccw)
        full += arc(c, R, a_in, a_out, ccw)
        pts += [(p, "") for p in full]
        pts[-1] = (pts[-1][0], orb.get("exit_label", "Orbit exit"))
        p0, tan_prev = pts[-1][0], 0.0              # the next corner's incoming leg starts at the exit
    if i == len(C) - 1:
        pts.append((p1, lab)); break
    p2 = orbit_entry(p1, orb) if orb and i == orb["after"] else C[i + 1][0]   # next leg goes to the orbit
    arcpts, t, D = fillet(p0, p1, p2)
    leg = np.linalg.norm(p1 - p0)
    if lab.startswith("*") and D > 10: ok = False; report.append(f"!! overfly point {lab} has a {D:.0f} deg turn")
    if tan_prev + t > leg + 1: ok = False; report.append(f"!! leg into {lab or i} is {leg / K:.0f} km but its turns need {(tan_prev + t) / K:.0f} km")
    report.append(f"corner {i:2d} {lab:28s} turn {D:5.0f} deg, tangent {t / K:4.1f} km, leg in {leg / K:4.0f} km")
    tan_prev = t
    for k, p in enumerate(arcpts): pts.append((p, lab.lstrip("*") if k == len(arcpts) // 2 else ""))
P = [p for p, _ in pts]

# --- per-segment analysis between labelled points
def radars_at(p, v):
    out = []
    for r in M["radars"]:
        rv = p - np.array([r["x"], r["y"]], float); d = np.linalg.norm(rv) / K
        if d < r["range_km"] * 1.15 and visible(H, ST, (r["x"], r["y"]), p, CRUISE_FT):
            asp = abs(float(unit(rv) @ v))
            out.append((f"{r['place']} {r['system']}", r["type"], d, "radial" if asp > 0.7 else "tangential" if asp < 0.4 else "oblique"))
    return out
dense, cum = [], 0.0
for a, b in zip(P, P[1:]):
    L = np.linalg.norm(b - a); v = unit(b - a) if L > 0 else np.array([1.0, 0])
    for t in np.arange(0, L, K): dense.append((a + v * t, v, cum + t));
    cum += L
terrain_bad = [p for p, _, _ in dense if not clear(p)]
if terrain_bad: ok = False; report.append(f"!! terrain within {CLEAR_KM} km at {len(terrain_bad)} points, first {terrain_bad[0].round()}")
samples = [d for d in dense if int(d[2] / K / SAMPLE_KM) != int((d[2] - K) / K / SAMPLE_KM)]
enemy = sum(side(TH, *p) == "enemy" for p, _, _ in samples)
print("\n".join(report))
print(f"\nroute {cum / K:.0f} km, {len(samples)} samples, {enemy} in enemy cells (~{enemy * 4.5:.0f} cold-war points)")
print(f"terrain {'OK' if not terrain_bad else 'CONFLICTS'}; turns {'OK' if ok else 'PROBLEMS'}\n")
# summary every ~20 km
last = None
for p, v, s in dense[::20]:
    rs = radars_at(p, v)
    print(f"{s / K:5.0f} km  ({p[0]:5.0f},{p[1]:5.0f}) hdg {hdg(v):3.0f}  {side(TH, *p):8s}  " +
          "; ".join(f"{n} {d:.0f}km {a}" for n, t, d, a in rs))
json.dump({"waypoints_game": [[list(map(float, p)), l] for p, l in pts]}, open(plan / "route_smooth.json", "w"), indent=1)

# --- preview
S = 32; xs, ys = [p[0] for p in P], [p[1] for p in P]
X0, Y0 = max(0, int(min(xs)) - 4096) // 2048 * 2048, max(0, int(min(ys)) - 4096) // 2048 * 2048
X1, Y1 = min(32768, int(max(xs)) + 4096), min(32768, int(max(ys)) + 4096)
sub = H[Y0 // ST:Y1 // ST, X0 // ST:X1 // ST]
im = np.full(sub.shape + (3,), 235, np.uint8); im[sub > 0] = (200, 200, 160); im[sub >= 200] = (150, 100, 55)
img = Image.fromarray(im).resize(((X1 - X0) // S, (Y1 - Y0) // S), Image.NEAREST); d = ImageDraw.Draw(img, "RGBA")
Q = lambda x, y: ((x - X0) / S, (y - Y0) / S)
g = grid(TH)
for j in range(16):
    for i in range(16):
        if g[j, i] & 3 == 0: d.rectangle((*Q(i * 2048, j * 2048), *Q(i * 2048 + 2047, j * 2048 + 2047)), fill=(255, 0, 0, 25))
for r in M["radars"]:
    cx, cy = Q(r["x"], r["y"]); rr = r["range_km"] * K / S; c = (200, 0, 0) if r["type"] == "P" else (0, 130, 0)
    d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), outline=c); d.text((cx + 3, cy), f"{r['place']} {r['system']}", fill=c)
d.line([Q(*p) for p in P], fill=(220, 0, 220), width=2)
for p, l in pts:
    if l: d.text(Q(*p), l, fill=(0, 0, 0))
for p, _, _ in samples:
    x, y = Q(*p); d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 0, 255) if side(TH, *p) == "enemy" else (120, 120, 120))
img.save(plan / "route_smooth.png")
