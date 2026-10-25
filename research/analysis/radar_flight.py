"""Join the HUD EMV-gauge log (scripts/emv_logger.py) with nav positions and radar geometry.

  radar_flight.py <emv.csv> <plan_dir> <theater> <nav.csv>...

Each HUD sample gets the plane's interpolated position/heading/altitude/turn rate and,
for every mission radar: range, aspect (angle between our track and the line to the
radar: 0 = flying straight at/away, 90 = crossing) and terrain line of sight. Ground-bar
"blinks" (runs of consecutive samples with a ground bar) are collapsed to one event with
their peak rows and worst colour. Writes <plan_dir>/radar_flight.csv (samples) and
radar_blinks.csv (events) and prints a per-minute timeline.
"""
import csv, json, math, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from radar_los import visible

ROOT = Path(__file__).resolve().parent.parent
K = 64.0
emv_f, plan, TH, navs = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4:]
M = json.load(open(plan / "mission_mem.json")); RAD = M["radars"]
z = np.load(ROOT / "logs/theater" / f"{TH}_terrain.npz"); H, ST = z["height_ft"], int(z["step_wp"])
N = []
for f in navs:
    for r in csv.DictReader(open(f)):
        try:
            if r["x"] and r["spd"] and int(r["spd"]) < 900 and int(r["alt"]) > 150:
                N.append((float(r["t"]), float(r["x"]), float(r["y"]), float(r["alt"]), float(r["hdg"]), int(r["spd"]), r.get("rate") or ""))
        except ValueError: pass
N.sort(); NT = np.array([n[0] for n in N])
SEV = {"": 0, "red": 1, "pink": 2, "yellow": 3, "lightblue": 1, "white": 3}
def unwrap_interp(t, i):
    a, b = N[i - 1], N[i]; f = (t - a[0]) / (b[0] - a[0] or 1e-9)
    dh = (b[4] - a[4] + 180) % 360 - 180
    return a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2]), a[3] + f * (b[3] - a[3]), (a[4] + f * dh) % 360
out = []
for r in csv.DictReader(open(emv_f)):
    t = float(r["t"])
    i = int(np.searchsorted(NT, t))
    if i == 0 or i >= len(N) or N[i][0] - N[i - 1][0] > 3: continue
    x, y, alt, hdg = unwrap_interp(t, i)
    # turn rate over +-3 s of nav samples
    j0, j1 = int(np.searchsorted(NT, t - 3)), min(len(N) - 1, int(np.searchsorted(NT, t + 3)))
    dh = (N[j1][4] - N[j0][4] + 180) % 360 - 180; rate = dh / max(1e-3, N[j1][0] - N[j0][0])
    row = dict(t=t, x=round(x), y=round(y), alt=round(alt), hdg=round(hdg, 1), spd=N[i][5], rate=round(rate, 2),
               emv=int(r["emv_rows"]), gbar=int(r["ground_bar_rows"]), gcol=r["ground_colour"] if r["ground_colour"] in SEV else "?",
               abar=int(r["air_bar_rows"]), acol=r["air_colour"] if r["air_colour"] in SEV else "?")
    hx, hy = math.sin(math.radians(hdg)), -math.cos(math.radians(hdg))
    for R in RAD:
        dx, dy = R["x"] - x, R["y"] - y; d = math.hypot(dx, dy) / K
        if d > R["range_km"] * 1.6: continue
        asp = math.degrees(math.acos(min(1, abs(hx * dx + hy * dy) / (d * K + 1e-9))))
        row.setdefault("radars", []).append(dict(n=f"{R['place']} {R['system']}", typ=R["type"], rng=R["range_km"], d=round(d, 1),
                                                 asp=round(asp), los=visible(H, ST, (R["x"], R["y"]), (x, y), alt)))
    out.append(row)
print(f"{len(out)} HUD samples matched to positions")
# blinks
blinks, cur = [], None
for s in out:
    if s["gbar"] > 0 and s["gcol"] != "?":
        if cur and s["t"] - cur["t1"] < 0.6: cur["t1"] = s["t"]; cur["rows"] = max(cur["rows"], s["gbar"]); cur["sev"] = max(cur["sev"], SEV[s["gcol"]]); cur["n"] += 1
        else:
            cur = dict(t0=s["t"], t1=s["t"], rows=s["gbar"], sev=SEV[s["gcol"]], n=1, s=s); blinks.append(cur)
print(f"{len(blinks)} ground-bar blinks")
w = csv.writer(open(plan / "radar_blinks.csv", "w", newline=""))
w.writerow(["t0", "dur", "rows", "sev", "x", "y", "alt", "hdg", "rate", "emv", "radars"])
for b in blinks:
    s = b["s"]; w.writerow([f"{b['t0']:.2f}", f"{b['t1'] - b['t0']:.2f}", b["rows"], b["sev"], s["x"], s["y"], s["alt"], s["hdg"], s["rate"], s["emv"],
                            "; ".join(f"{r['n']} {r['d']}km a{r['asp']} {'LOS' if r['los'] else 'hid'}" for r in s.get("radars", []) if r["d"] < r["rng"] * 1.3)])
json.dump(out, open(plan / "radar_flight.json", "w"))
# per-minute timeline
t0 = out[0]["t"]
by = {}
for s in out: by.setdefault(int((s["t"] - t0) // 60), []).append(s)
print("min  x     y     alt hdg  emv  blinks maxrows worst  air  in-range radars (d km, aspect, LOS)")
for m, ss in sorted(by.items()):
    bl = [b for b in blinks if int((b["t0"] - t0) // 60) == m]
    s = ss[len(ss) // 2]
    worst = max([b["sev"] for b in bl], default=0)
    air = max((SEV.get(q["acol"], 0) for q in ss), default=0)
    inr = [f"{r['n'].split()[0]}{r['typ']} {r['d']:.0f} a{r['asp']} {'L' if r['los'] else 'h'}" for r in s.get("radars", []) if r["d"] < r["rng"]]
    print(f"{m:3d} {s['x']:5d} {s['y']:5d} {s['alt']:4d} {s['hdg']:5.0f} {np.median([q['emv'] for q in ss]):4.0f} {len(bl):6d} {max([b['rows'] for b in bl], default=0):7d} "
          f"{['-', 'red', 'pink', 'YELLOW'][worst]:6s} {['-', 'blue', '', 'WHITE'][air]:5s} {', '.join(inr)}")
