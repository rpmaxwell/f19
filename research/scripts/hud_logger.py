"""Passive HUD logger: read airspeed/altitude from window captures ~5x/s and
append (t, alt, spd) to logs/hud/<start>.csv until logs/hud/STOP exists.
Sends no keys.   hud_logger.py <window_id>"""
import csv, subprocess, sys, time
from pathlib import Path
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hud_ocr

WIN = sys.argv[1]
D = Path(__file__).resolve().parent.parent / "logs" / "hud"; D.mkdir(parents=True, exist_ok=True)
STOP = D / "STOP"; STOP.unlink(missing_ok=True)
CAP = Path("/tmp") / f"hud_logger_{WIN}.png"
f = open(D / f"{int(time.time())}.csv", "w", newline="", buffering=1); w = csv.writer(f)
w.writerow(["t", "alt", "spd", "vs", "target_vs", "action"])
while not STOP.exists():
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(CAP)], check=False)
    try: spd, alt = hud_ocr.read_hud(Image.open(CAP).convert("RGB"))
    except Exception: continue
    if alt is not None: w.writerow([f"{time.time():.2f}", alt, spd if spd else "", "", "", ""])
