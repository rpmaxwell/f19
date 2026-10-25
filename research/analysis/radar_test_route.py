"""SUPERSEDED by analysis/planner (python analysis/planner <plan_dir>); kept to reproduce old plans.

Radar-tactics test route: splice controlled test legs into a planned route.

Tests the manual's three tactics (pp. 41-42, 75-76) at constant altitude and throttle:
  - doppler: fly AROUND it (tangential) vs straight at / away from it (radial)
  - pulse:   fly straight AT / away from it (radial) vs across it (tangential)
  - terrain: a constant-range tangential arc around a doppler that crosses from line of
             sight into a mountain's radar shadow (range and aspect fixed, only cover changes)
Each test is a block of legs around one radar: radial legs (in/out) and tangential arcs
over the same range band, so radar strength can be compared at matched range.

  radar_test_route.py <plan_dir> <theater> <blocks.json>

<plan_dir> must already hold a planned route.json (waypoints_game) + mission_mem.json.
blocks.json: list of {"after": <waypoint index in route.json>, "rejoin": <index>,
  "legs": [{"kind": "radial"|"arc", "radar": <place>, "r0": km, "r1": km, "b0": deg, "b1": deg}]}
  radial: bearing b0 (from the radar), range r0 -> r1; arc: range r0, bearing b0 -> b1.
Writes <plan_dir>/route_test.json (waypoints_game; nav.py --route), test_legs.json
(game-unit segments with labels, for the analysis) and route_test.png. Every leg is
checked for terrain clearance (no >= 200 ft terrain within 2 km) and annotated with the
radars that can see it (briefed range x 1.15, line of sight at CRUISE_FT).
"""
import json, math, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
sys.path.insert(0, str(Path(__file__).resolve().parent))
from radar_los import visible

ROOT = Path(__file__).resolve().parent.parent
K, CRUISE_FT, CLEAR_KM, ARC_STEP = 64.0, 450.0, 2.0, 4.0
plan, TH, blocks = Path(sys.argv[1]), sys.argv[2], json.load(open(sys.argv[3]))
route = json.load(open(plan / "route.json"))["waypoints_game"]
M = json.load(open(plan / "mission_mem.json")); RAD = {r["place"]: r for r in M["radars"] if r["place"] not in ("Patrol Boat",)}
z = np.load(ROOT / "logs/theater" / f"{TH}_terrain.npz"); H, ST = z["height_ft"], int(z["step_wp"])

def at(r, km, brg):
    b = math.radians(brg); return (r["x"] + km * K * math.sin(b), r["y"] - km * K * math.cos(b))
def clear(p):
    o = int(CLEAR_KM * K / ST); i, j = int(p[0] // ST), int(p[1] // ST)
    return H[max(0, j - o):j + o + 1, max(0, i - o):i + o + 1].max() < 200
def seen_by(p):
    out = []
    for r in M["radars"]:
        d = math.hypot(p[0] - r["x"], p[1] - r["y"]) / K
        if d < r["range_km"] * 1.15 and visible(H, ST, (r["x"], r["y"]), p, CRUISE_FT):
            out.append(f"{r['place']} {r['system']} {d:.0f}km")
    return out

def leg_points(L):
    if L["kind"] == "wps": return [tuple(q) for q in L["points"]]       # explicit waypoints (e.g. a loiter)
    r = RAD[L["radar"]]
    if L["kind"] == "radial":
        n = max(1, int(abs(L["r1"] - L["r0"]) / 8))
        return [at(r, L["r0"] + (L["r1"] - L["r0"]) * k / n, L["b0"]) for k in range(n + 1)]
    span = (L["b1"] - L["b0"] + 540) % 360 - 180
    n = max(1, int(abs(span) * math.pi / 180 * L["r0"] / ARC_STEP))
    return [at(r, L["r0"], L["b0"] + span * k / n) for k in range(n + 1)]

out, legs, ok, cur = [], [], True, 0
for B in sorted(blocks, key=lambda b: b["after"]):
    out += route[cur:B["after"] + 1]
    for L in B["legs"]:
        pts = leg_points(L)
        lab = L["label"] if "label" in L else (f"TEST {L['kind']} {L['radar']} " +
               (f"b{L['b0']:.0f} {L['r0']:.0f}->{L['r1']:.0f}km" if L["kind"] == "radial" else f"r{L['r0']:.0f}km b{L['b0']:.0f}->{L['b1']:.0f}"))
        samples = [tuple(a + (b - a) * t for a, b in zip(p, q)) for p, q in zip(pts, pts[1:]) for t in np.linspace(0, 1, 9)]
        bad = [s for s in samples if not clear(s)]
        if bad: ok = False; print(f"!! terrain within {CLEAR_KM} km on {lab} at {bad[0][0]:.0f},{bad[0][1]:.0f}")
        mid = samples[len(samples) // 2]
        print(f"{lab:48s} {len(pts):2d} pts  seen by: start {seen_by(samples[0])}\n{'':48s}           mid {seen_by(mid)}\n{'':48s}           end {seen_by(samples[-1])}")
        legs.append({**L, "label": lab, "points": pts})
        if out and math.dist(out[-1][0], pts[0]) < 1.0: out[-1][1] = lab; pts_new = pts[1:]   # shared corner point
        else: pts_new = pts
        out += [[list(p), ""] for p in pts_new]
        if pts_new is pts: out[-len(pts)][1] = lab
    # connector back to the route: check the straight line to the rejoin waypoint
    a, b = out[-1][0], route[B["rejoin"]][0]
    for t in np.linspace(0, 1, 40):
        s = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        if not clear(s): ok = False; print(f"!! terrain on rejoin to wp {B['rejoin']} at {s[0]:.0f},{s[1]:.0f}"); break
    cur = B["rejoin"]
    if B.get("rejoin_label"): route[cur] = [route[cur][0], B["rejoin_label"]]   # e.g. ends a LOITER section in nav
out += route[cur:]
km = sum(math.dist(p[0], q[0]) for p, q in zip(out, out[1:])) / K
base = sum(math.dist(p[0], q[0]) for p, q in zip(route, route[1:])) / K
print(f"route {base:.0f} km -> test route {km:.0f} km; terrain {'OK' if ok else 'CONFLICTS'}")
json.dump({"waypoints_game": out}, open(plan / "route_test.json", "w"), indent=1)
json.dump(dict(cruise_ft=CRUISE_FT, legs=legs), open(plan / "test_legs.json", "w"), indent=1)

# preview: terrain, radar rings, base route (grey) and test route (magenta / test legs blue)
S = ST
xs = [p[0][0] for p in out]; ys = [p[0][1] for p in out]
X0, X1 = max(0, int(min(xs)) - 6400), int(max(xs)) + 6400; Y0, Y1 = max(0, int(min(ys)) - 6400), int(max(ys)) + 6400
sub = H[Y0 // ST:Y1 // ST, X0 // ST:X1 // ST]
im = np.full(sub.shape + (3,), 235, np.uint8); im[sub > 0] = (200, 200, 160); im[sub >= 200] = (150, 100, 55)
img = Image.fromarray(im); d = ImageDraw.Draw(img); P = lambda x, y: ((x - X0) / S, (y - Y0) / S)
for r in M["radars"]:
    cx, cy = P(r["x"], r["y"]); R = r["range_km"] * K / S
    c = (200, 0, 0) if r["type"] == "P" else (0, 130, 0)
    d.ellipse((cx - R, cy - R, cx + R, cy + R), outline=c); d.text((cx + 3, cy), f"{r['place']} {r['system']}", fill=c)
d.line([P(*p[0]) for p in route], fill=(150, 150, 150), width=1)
d.line([P(*p[0]) for p in out], fill=(220, 0, 220), width=2)
for L in legs: d.line([P(*p) for p in L["points"]], fill=(0, 60, 255), width=3)
for p, n in out:
    if n: d.text(P(*p), n.replace("TEST ", ""), fill=(0, 0, 0))
img.save(plan / "route_test.png")
