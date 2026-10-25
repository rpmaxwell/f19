"""Capture the DOSBox-X window once a second and save a compact strip of the
gauges (time, fuel, speed, altitude, throttle) for fuel-efficiency analysis."""
import subprocess, sys, time
from pathlib import Path
from PIL import Image

WIN_ID = sys.argv[1] if len(sys.argv) > 1 else "2000"
OUT = Path(__file__).resolve().parent.parent / "logs" / "fuel_watch"
OUT.mkdir(parents=True, exist_ok=True)
TMP = OUT / "_full.png"

# (left, top, right, bottom) in the 1280x856 retina window capture
REGIONS = {
    "time": (800, 548, 1050, 578),
    "fuel": (715, 800, 1085, 834),
    "speed": (290, 262, 375, 298),
    "alt": (900, 262, 985, 298),
    "throttle": (0, 630, 155, 800),
}

while True:
    t = time.time()
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN_ID, str(TMP)], check=False)
    try:
        im = Image.open(TMP).convert("RGB")
    except Exception:
        time.sleep(1); continue
    crops = [im.crop(r) for r in REGIONS.values()]
    w = sum(c.width for c in crops) + 4 * len(crops)
    h = max(c.height for c in crops)
    strip = Image.new("RGB", (w, h), (0, 0, 0))
    x = 0
    for c in crops:
        strip.paste(c, (x, 0)); x += c.width + 4
    strip.save(OUT / f"{t:.1f}.png")
    time.sleep(max(0, 1.0 - (time.time() - t)))
