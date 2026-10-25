"""SUPERSEDED by analysis/planner (python analysis/planner <plan_dir>); kept to reproduce old plans.

Plan a stealth route for the mission read from memory (scripts/read_mission.py).

  plan_mission.py <plan_dir> <mission.json> <takeoff> <landing> [lambda] [theater]

<takeoff>/<landing> = an airbase name from the theater file, or "px:X,Y" for a
point on the briefing map (e.g. a carrier, which the theater file doesn't hold).
Briefing-map positions of the sites come from <plan_dir>/sites_seen.json if
present (analysis/radar_geometry.py on this theater's briefing captures),
else logs/radar_sites/sites.json (Central Europe).

Writes <plan_dir>/sites.json + mission.json in the briefing-map pixel space the
route planner uses (terrain still comes from a saved briefing-map image), plus
px_to_game.json, then runs analysis/route_planner.py with PLAN_DIR=<plan_dir>.
The px<->game affine is refit from exact WLD site positions (game units) vs
their circle centres on the briefing map (logs/radar_sites/sites.json).
"""
import json, os, subprocess, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
plan, mission_path, takeoff, landing = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
lam = sys.argv[5] if len(sys.argv) > 5 else "3"
theater = sys.argv[6] if len(sys.argv) > 6 else "CE"
plan.mkdir(parents=True, exist_ok=True)
M = json.load(open(mission_path)); T = json.load(open(ROOT / "logs/theater" / f"{theater}.json"))

# refit briefing px <-> game units from named SAM sites seen on a briefing map
seen_path = plan / "sites_seen.json" if (plan / "sites_seen.json").exists() else ROOT / "logs/radar_sites/sites.json"
seen = json.load(open(seen_path))
sam_cls = T["records"][0]["cls"]
wld = {r["name"]: r for r in T["records"] if r["cls"] == sam_cls and r["name"]}
P, G = [], []
for s in seen:
    nm = s["name"].split(" SA")[0].split(" (")[0].replace("Grossenhain", "Grossenhaim").replace("Komarno", "Konmarno")
    if nm in wld: P.append((s["cx"], s["cy"], 1.0)); G.append((wld[nm]["x"], wld[nm]["y"]))
P, G = np.array(P), np.array(G, float)
A = np.linalg.lstsq(P, G, rcond=None)[0]                               # game = [px,py,1] @ A
res = np.hypot(*(P @ A - G).T) / 64
Ainv = np.linalg.lstsq(np.c_[G, np.ones(len(G))], P[:, :2], rcond=None)[0]   # px = [gx,gy,1] @ Ainv
to_px = lambda x, y: tuple(np.array([x, y, 1.0]) @ Ainv)
pxkm_x = float(np.hypot(*(np.array([64, 0]) @ Ainv[:2])))           # px per km along each axis
pxkm_y = float(np.hypot(*(np.array([0, 64]) @ Ainv[:2])))
print(f"px<->game fit from {len(P)} sites: rms {np.sqrt((res**2).mean()):.2f} km; {pxkm_x:.3f} / {pxkm_y:.3f} px per km")
json.dump({"affine": A.tolist(), "inverse": Ainv.tolist(), "rms_km": float(np.sqrt((res**2).mean()))}, open(plan / "px_to_game.json", "w"), indent=1)

sites = []
for r in M["radars"]:
    cx, cy = to_px(r["x"], r["y"])
    sites.append(dict(name=f"{r['place']} {r['system']}", type=r["type"], range_km=r["range_km"], cx=cx, cy=cy,
                      px_per_km_x=pxkm_x, px_per_km_y=pxkm_y))
json.dump(sites, open(plan / "sites.json", "w"), indent=1)
base = {r["name"]: r for r in T["records"] if r["name"] and r["flags"][0] == 1025}   # airbases
def where(spec):
    if spec.startswith("px:"):
        px, py = map(float, spec[3:].split(",")); return tuple(np.array([px, py, 1.0]) @ A)
    return base[spec]["x"], base[spec]["y"]
tg = {t["role"]: t for t in M["targets"]}
# All runways run north-south. Takeoff: straight climb-out CLIMB_KM north along the
# runway (the save states start pointing 000). Landing is north/south only: an
# approach fix APPROACH_KM out on the runway axis (on the side the route arrives
# from), a straight final along the axis to a FINAL_KM point where the autopilot
# hands over aligned and wings-level, then the runway.
CLIMB_KM, APPROACH_KM, FINAL_KM, K = 8.0, 20.0, 6.0, 64.0
tx, ty = where(takeoff); lx, ly = where(landing)
# Launch direction: airbase runways are flown northbound (save states point 000); the
# carrier catapult launches south (~185 deg, every carrier flight so far). Override: TAKEOFF_HDG=0|180.
th = float(os.environ.get("TAKEOFF_HDG", 180 if takeoff.startswith("px:") else 0))
cdx, cdy = np.sin(np.radians(th)) * CLIMB_KM * K, -np.cos(np.radians(th)) * CLIMB_KM * K
# The primary target always comes first (game rule). On the briefing's flight plan the
# takeoff end is therefore the end joined to the primary - check that before planning.
order = ["primary", "secondary"]
sx, sy = tg[order[1]]["x"], tg[order[1]]["y"]                # last target before landing
side = -1 if sy < ly else 1                                  # arriving from the north (smaller y) -> fix north
# The route can arrive from the other side than the last target suggests (e.g. a corridor
# around mountains): APPROACH_SIDE=N|S forces the approach fix's side.
side = {"N": -1, "S": 1}.get(os.environ.get("APPROACH_SIDE", ""), side)
stops = [[f"Takeoff ({takeoff})", to_px(tx, ty)],
         ["Climb-out", to_px(tx + cdx, ty + cdy)],
         *[[f"{r.upper()}: {tg[r]['object']} {tg[r]['place']}", to_px(tg[r]["x"], tg[r]["y"])] for r in order],
         ["Approach fix", to_px(lx, ly + side * APPROACH_KM * K)],
         ["Final", to_px(lx, ly + side * FINAL_KM * K)],
         [f"Landing ({landing})", to_px(lx, ly)]]
# Terrain: the planner reads the decoded 3D terrain (logs/theater/<TH>_terrain.npz from
# analysis/terrain_3d.py) via THEATER. The old crash-site keep-out discs are only used
# with CRASH_DISCS=1 (they're now a validation set; every terrain crash is inside a
# decoded mountain).
if not (ROOT / "logs/theater" / f"{theater}_terrain.npz").exists():
    subprocess.run([sys.executable, str(ROOT / "analysis/terrain_3d.py"), theater], check=True)
obs_f = ROOT / "logs/theater" / f"{theater}_obstacles.json"
obs = json.load(open(obs_f))["obstacles"] if obs_f.exists() and os.environ.get("CRASH_DISCS") == "1" else []
json.dump([dict(px=to_px(o["x"], o["y"]), radius_km=o["radius_km"]) for o in obs], open(plan / "obstacles.json", "w"), indent=1)
json.dump({"name": f"from memory: {mission_path}", "stops": stops}, open(plan / "mission.json", "w"), indent=1)
subprocess.run([sys.executable, str(ROOT / "analysis/route_planner.py"), lam], env={**os.environ, "PLAN_DIR": str(plan), "THEATER": theater}, check=True)
