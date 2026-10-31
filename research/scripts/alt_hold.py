"""Altitude hold: read HUD altitude from window captures, tap pitch keys via
the DOSBox-X automation socket.

  alt_hold.py <window_id> [target_ft] [--probe]

Target defaults to the altitude at start. --probe first taps nose-up twice to
confirm which arrow key raises the nose (swaps them if the plane sinks).
Stops only when logs/alt_hold/STOP exists; unreadable frames just pause tapping.
Logs every reading to logs/alt_hold/<start>.csv.
"""
import csv, subprocess, sys, time
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hud_ocr
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox

ROOT = Path(__file__).resolve().parent.parent
WIN = sys.argv[1]
args = [a for a in sys.argv[2:] if not a.startswith("--")]
PROBE = "--probe" in sys.argv
CAP = Path("/tmp") / f"alt_hold_{WIN}.png"
STOP = ROOT / "logs" / "alt_hold" / "STOP"
NOSE_UP, NOSE_DOWN = "down", "up"     # stick convention; verified by --probe

KP = 0.25         # desired climb rate (ft/s) per ft of error
VS_MAX = 20.0     # ft per real second
DEADBAND = 10.0    # ft/s of climb-rate error tolerated
TAP = 0.5         # s key held per tap
MIN_GAP = 1.5     # s between taps
FLOOR = 350       # below this, pull up hard

def read():
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(CAP)], check=False)
    try:
        return hud_ocr.read_hud(Image.open(CAP).convert("RGB"))
    except Exception:
        return None, None

def climb_rate(hist):
    """Least-squares slope (ft/s) over the last ~1.5 s of (t, alt)."""
    pts = [(t, a) for t, a in hist if t >= hist[-1][0] - 1.5]
    if len(pts) < 3: return None
    n = len(pts); mt = sum(t for t, _ in pts) / n; ma = sum(a for _, a in pts) / n
    den = sum((t - mt) ** 2 for t, _ in pts)
    return sum((t - mt) * (a - ma) for t, a in pts) / den if den else None

def main():
    global NOSE_UP, NOSE_DOWN
    STOP.parent.mkdir(parents=True, exist_ok=True); STOP.unlink(missing_ok=True)
    hist, last_tap, misses = [], 0.0, 0
    logf = open(STOP.parent / f"{int(time.time())}.csv", "w", newline="", buffering=1)
    log = csv.writer(logf)
    log.writerow(["t", "alt", "spd", "vs", "target_vs", "action"])
    with Dosbox() as db:
        def tap(k):
            db.tap(k, TAP)
        # initial readings
        while len(hist) < 4:
            spd, alt = read()
            if alt is not None: hist.append((time.time(), alt))
        target = float(args[0]) if args else hist[-1][1]
        print(f"holding {target:.0f} ft (start {hist[-1][1]} ft, {spd} kt)", flush=True)
        if PROBE:
            vs0 = climb_rate(hist) or 0.0
            tap(NOSE_UP); time.sleep(0.3); tap(NOSE_UP)
            t_end = time.time() + 2.5
            while time.time() < t_end:
                s, a = read()
                if a is not None: hist.append((time.time(), a))
            vs1 = climb_rate(hist) or 0.0
            if vs1 < vs0 - 3:
                NOSE_UP, NOSE_DOWN = NOSE_DOWN, NOSE_UP
                print(f"probe: plane sank ({vs0:.0f} -> {vs1:.0f} ft/s); swapped keys, nose-up = {NOSE_UP}", flush=True)
                tap(NOSE_UP); time.sleep(0.3); tap(NOSE_UP); time.sleep(0.3); tap(NOSE_UP); time.sleep(0.3); tap(NOSE_UP)
            else:
                print(f"probe: climb {vs0:.0f} -> {vs1:.0f} ft/s; nose-up = {NOSE_UP}", flush=True)
        while not STOP.exists():
            spd, alt = read(); now = time.time()
            if alt is None or (hist and abs(alt - hist[-1][1]) > 150):
                misses += 1
                if misses % 20 == 0: print(f"{misses} unreadable frames in a row; holding attitude", flush=True)
                continue
            misses = 0; hist.append((now, alt)); hist = hist[-40:]
            vs = climb_rate(hist)
            if vs is None: continue
            tf = STOP.parent / "TARGET"
            if tf.exists():
                try:
                    nt = float(tf.read_text().strip())
                    if nt != target: target = nt; print(f"new target {target:.0f} ft", flush=True)
                except ValueError: pass
            tvs = max(-VS_MAX, min(VS_MAX, KP * (target - alt)))
            if alt < FLOOR: tvs = VS_MAX
            action = ""
            if alt < FLOOR and vs < 10 and now - last_tap >= 1.0:
                db.tap(NOSE_UP, 0.6); action = "UP!"; last_tap = now
            elif now - last_tap >= MIN_GAP:
                if vs < tvs - DEADBAND: tap(NOSE_UP); action = "up"; last_tap = now
                elif vs > tvs + DEADBAND: tap(NOSE_DOWN); action = "down"; last_tap = now
            log.writerow([f"{now:.2f}", alt, spd, f"{vs:.1f}", f"{tvs:.1f}", action])
    print("stopped", flush=True)

if __name__ == "__main__":
    main()
