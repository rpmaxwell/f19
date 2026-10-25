"""Every 15 s for 2 min: capture the waypoint panel; X/Y at that moment come
from the running alt_hold_mem log (the automation port serves one client at a
time, so this script must not connect). Writes logs/calib/wp_<n>.png + shots.json."""
import json, subprocess, sys, time
from pathlib import Path
from PIL import Image

def _full(path):
    """Load a window capture at Retina (1280x856) scale even from a 1x display."""
    im = Image.open(path).convert("RGB")
    return im.resize((1280, 856), Image.NEAREST) if im.width == 640 else im
WIN = sys.argv[1]; D = Path(__file__).resolve().parent.parent / "logs" / "calib"; D.mkdir(exist_ok=True)
out = []
for n in range(9):
    t0 = time.time()
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(D / "_f.png")], check=True)
    t1 = time.time()
    _full(D / "_f.png").crop((700, 545, 1130, 795)).save(D / f"wp_{n}.png")
    out.append(dict(n=n, t=(t0 + t1) / 2)); json.dump(out, open(D / "shots.json", "w"))
    time.sleep(15)
