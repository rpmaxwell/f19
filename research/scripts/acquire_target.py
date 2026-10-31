"""Lock a mission ground target with the tracking camera: trackcam mode air-to-ground (F2 cycles
nav 1 -> air-ground 2 -> air-air 0 at old phys 0x455A6), then N ("designate new target") until the
locked target (0x41096) is the wanted record, pressing only while the target is within CONE deg of
the nose (it must be on screen). No flight controls. Logs every lock to logs/probe/acquire_<t>.csv.

  acquire_target.py RECORD [--cone 20] [--max-km 40] [--minutes 20]
"""
import argparse, csv, math, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox
from f19.memory.reader import read_state, read_flight, read_target, _peek, find_offset

TRACKCAM_MODE, AIR_GROUND = 0x455A6, 2
ap = argparse.ArgumentParser(); ap.add_argument("record", type=int); ap.add_argument("--cone", type=float, default=20)
ap.add_argument("--max-km", type=float, default=40); ap.add_argument("--minutes", type=float, default=20)
a = ap.parse_args()
ROOT = Path(__file__).resolve().parent.parent; (ROOT / "logs/probe").mkdir(parents=True, exist_ok=True)
w = csv.writer(open(ROOT / f"logs/probe/acquire_{int(time.time())}.csv", "w", newline="", buffering=1))
w.writerow(["t", "km", "off_deg", "mode", "key", "locked", "locked_km", "locked_brg"])
with Dosbox() as db:
    o = find_offset(db); T = read_target(db, a.record)[:2]; t_end = time.time() + 60 * a.minutes; got = 0
    while time.time() < t_end:
        s = read_state(db)
        if s["spd"] > 900 or s["x"] == 0: sys.exit("not in flight")
        p = (s["x"] >> 5, s["y"] >> 5); d = math.hypot(T[0] - p[0], T[1] - p[1]) / 64
        off = (math.degrees(math.atan2(T[0] - p[0], -(T[1] - p[1]))) - s["hdg"] + 180) % 360 - 180
        mode = _peek(db, TRACKCAM_MODE + o, 1)[0]; key = ""
        lk = read_flight(db)["locked"]
        if lk == a.record:
            got += 1
            if got == 1: print(f"LOCKED record {a.record} at {d:.1f} km, {off:+.0f} deg", flush=True)
        elif mode != AIR_GROUND: db.tap("f2", 0.2); key = "f2"
        elif abs(off) < a.cone and d < a.max_km: db.tap("n", 0.2); key = "n"
        lk = read_flight(db)["locked"]
        lx = read_target(db, lk) if lk < 600 else None
        w.writerow([f"{time.time():.1f}", f"{d:.1f}", f"{off:.0f}", mode, key, lk,
                    "" if lx is None else f"{math.hypot(lx[0] - p[0], lx[1] - p[1]) / 64:.1f}",
                    "" if lx is None else f"{(math.degrees(math.atan2(lx[0] - p[0], -(lx[1] - p[1]))) - s['hdg'] + 180) % 360 - 180:.0f}"])
        time.sleep(1.0)
