"""Altitude-step schedule for the radar-exposure test flight (drives nav.py via logs/nav/ALT).

Tails the newest logs/nav/*.csv (written by nav.py), classifies the plane's position
into a radar zone using the mission's radar sites, and runs one altitude cycle per
zone: 60 s after entering it climb to HIGH, 180 s later back to LOW.
Zones: "none" (no radar in range), "doppler" (only doppler radars in range),
"pulse" (a pulse radar in range). Events are logged to logs/nav/alt_schedule_<start>.csv.
Stop by creating logs/nav/ALT_STOP.

  alt_schedule.py <mission_mem.json> [LOW] [HIGH]
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import csv, json, math, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAV = S.logs / "nav"; ALT, STOP = NAV / "ALT", NAV / "ALT_STOP"
STOP.unlink(missing_ok=True)
M = json.load(open(sys.argv[1]))
LOW = float(sys.argv[2]) if len(sys.argv) > 2 else 400.0
HIGH = float(sys.argv[3]) if len(sys.argv) > 3 else 800.0
RADARS = [r for r in M["radars"] if r.get("system")]
K, DELAY, HOLD = 64.0, 60.0, 180.0

def zone(x, y):
    inr = [r for r in RADARS if math.hypot(x - r["x"], y - r["y"]) / K < r["range_km"]]
    if not inr: return "none"
    return "pulse" if any(r["type"] == "P" for r in inr) else "doppler"

def last_row(path):
    with open(path, "rb") as f:
        f.seek(0, 2); f.seek(max(0, f.tell() - 4096))
        lines = f.read().decode(errors="ignore").strip().splitlines()
    return lines[-1].split(",") if lines else None

log = csv.writer(open(NAV / f"alt_schedule_{int(time.time())}.csv", "w", newline="", buffering=1))
log.writerow(["t", "x", "y", "alt", "zone", "event", "target"])
done, cur, entered, step = set(), None, None, None
T_START = time.time()
while not STOP.exists():
    navs = sorted(f for f in NAV.glob("[0-9]*.csv") if int(f.stem) >= T_START - 5)   # only navs started after us
    row = last_row(navs[-1]) if navs else None
    if not row or not row[0].replace(".", "").isdigit() or not row[1] or not row[4] or int(row[4]) > 900:
        time.sleep(1); continue                                 # header, event-only rows, crashed/garbage state
    t, x, y, alt = float(row[0]), float(row[1]), float(row[2]), row[3]
    if int(alt) < 300: time.sleep(1); continue                 # not airborne yet / takeoff
    z = zone(x, y)
    if z != cur:
        if step == "high": ALT.write_text(f"{LOW:.0f}\n")         # left the zone mid-cycle: back down
        cur, entered, step = z, time.time(), None
        log.writerow([f"{t:.2f}", f"{x:.0f}", f"{y:.0f}", alt, z, "enter", ""])
    el = time.time() - entered
    if z not in done:
        if step is None and el >= DELAY:
            ALT.write_text(f"{HIGH:.0f}\n"); step = "high"
            log.writerow([f"{t:.2f}", f"{x:.0f}", f"{y:.0f}", alt, z, "climb", HIGH])
        elif step == "high" and el >= DELAY + HOLD:
            ALT.write_text(f"{LOW:.0f}\n"); step = "low"; done.add(z)
            log.writerow([f"{t:.2f}", f"{x:.0f}", f"{y:.0f}", alt, z, "descend", LOW])
    time.sleep(1)
ALT.write_text(f"{LOW:.0f}\n")
