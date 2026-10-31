"""On the briefing's Radar Sites screen, tap RIGHT through every site via the
automation socket and save several captures per site (the highlight blinks),
stopping when the RADAR DATA panel wraps back to the first site."""
import subprocess, sys, time
from pathlib import Path
from PIL import Image, ImageChops
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox

WIN_ID = sys.argv[1]
GRABS = 8
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "logs" / "radar_sites"
OUT.mkdir(parents=True, exist_ok=True)
PANEL = (935, 130, 1275, 290)

def grab(path):
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN_ID, str(path)], check=True)
    im = Image.open(path).convert("RGB")
    if im.width == 640: im = im.resize((1280, 856), Image.NEAREST); im.save(path)
    return im

with Dosbox() as db:
    first = None
    for i in range(60):
        time.sleep(0.6)
        im = grab(OUT / f"site_{i:02d}_0.png")
        panel = im.crop(PANEL)
        if first is None:
            first = panel
        elif not ImageChops.difference(panel, first).getbbox():
            (OUT / f"site_{i:02d}_0.png").unlink()
            print(f"wrapped after {i} sites"); break
        for g in range(1, GRABS):
            time.sleep(0.25)
            grab(OUT / f"site_{i:02d}_{g}.png")
        db.tap("right", 0.1)
