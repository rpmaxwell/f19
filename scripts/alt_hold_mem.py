"""Altitude hold using live memory reads (scripts/f19_state.py) instead of HUD
OCR; pitch via >=0.5 s arrow presses on the automation socket.

  alt_hold_mem.py [target_ft]
Live target: logs/alt_hold/TARGET. Stop: logs/alt_hold/STOP.
Logs to logs/alt_hold/mem_<start>.csv.
"""
import csv, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
from f19_state import read_state

D = Path(__file__).resolve().parent.parent / "logs" / "alt_hold"; D.mkdir(parents=True, exist_ok=True)
STOP, TARGET = D / "STOP", D / "TARGET"
NOSE_UP, NOSE_DOWN = "down", "up"
KP, VS_MAX, DEADBAND = 0.25, 15.0, 6.0      # ft/s per ft; ft per real s
PRESS, MIN_GAP = 0.5, 1.2                    # s; shorter presses can fall between game frames
FLOOR = 350

def main():
    STOP.unlink(missing_ok=True)
    hist, last = [], 0.0
    with Dosbox() as db:
        target = float(sys.argv[1]) if len(sys.argv) > 1 else read_state(db)["alt"]
        TARGET.write_text(f"{target:.0f}")
        w = csv.writer(open(D / f"mem_{int(time.time())}.csv", "w", newline="", buffering=1))
        w.writerow(["t", "alt", "spd", "hdg", "x", "y", "vs", "target_vs", "action"])
        print(f"holding {target:.0f} ft", flush=True)
        while not STOP.exists():
            try: target = float(TARGET.read_text())
            except ValueError: pass
            s = read_state(db); now = time.time()
            hist.append((now, s["alt"])); hist = [h for h in hist if h[0] >= now - 1.0]
            vs = (hist[-1][1] - hist[0][1]) / (hist[-1][0] - hist[0][0]) if len(hist) > 3 else 0.0
            tvs = max(-VS_MAX, min(VS_MAX, KP * (target - s["alt"])))
            act = ""
            if s["alt"] < FLOOR and vs < 5 and now - last >= 0.8:
                db.tap(NOSE_UP, 0.6); act = "UP!"
            elif now - last >= MIN_GAP:
                if vs < tvs - DEADBAND: db.tap(NOSE_UP, PRESS); act = "up"
                elif vs > tvs + DEADBAND: db.tap(NOSE_DOWN, PRESS); act = "down"
            if act: last = time.time()
            w.writerow([f"{now:.2f}", s["alt"], s["spd"], f"{s['hdg']:.1f}", s["x"], s["y"], f"{vs:.1f}", f"{tvs:.1f}", act])
            time.sleep(0.1)
    print("stopped", flush=True)

if __name__ == "__main__":
    main()
