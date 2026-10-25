"""Save a full briefing-screen capture whenever the RADAR DATA panel changes,
so cycling radar sites with the arrow keys yields one frame per site."""
import subprocess, sys, time
from pathlib import Path
from PIL import Image, ImageChops

WIN_ID = sys.argv[1]
OUT = Path(__file__).resolve().parent.parent / "logs" / "radar_sites"
TMP = OUT / "_full.png"
PANEL = (935, 130, 1275, 290)
prev, n = None, 0
while True:
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN_ID, str(TMP)], check=False)
    try:
        im = Image.open(TMP).convert("RGB")
    except Exception:
        time.sleep(0.3); continue
    panel = im.crop(PANEL)
    if prev is None or ImageChops.difference(panel, prev).getbbox():
        time.sleep(0.3)  # let the map overlay finish drawing, then re-grab
        subprocess.run(["screencapture", "-x", "-o", "-l", WIN_ID, str(TMP)], check=False)
        im = Image.open(TMP).convert("RGB")
        im.save(OUT / f"site_{n:02d}.png"); n += 1
        prev = im.crop(PANEL)
    time.sleep(0.3)
