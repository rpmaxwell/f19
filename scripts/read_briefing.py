"""From the briefing's Radar Sites item: find the flashing mission targets,
capture the objectives and flight-plan text screens, and return the cursor to
Radar Sites. Verifies the highlighted menu item before every Enter."""
import sys, time, subprocess, json
from pathlib import Path
import numpy as np
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox

WIN = sys.argv[1]
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "logs" / "radar_sites"
ROWS = {"Mission Targets": 511, "Radar Sites": 551, "Missile Ranges": 591, "Air Bases": 631,
        "Flight Plan": 671, "Special Events": 711, "Exit": 751}

def grab(name):
    p = OUT / f"{name}.png"
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(p)], check=True)
    im = Image.open(p).convert("RGB")
    if im.width == 640: im = im.resize((1280, 856), Image.NEAREST); im.save(p)
    return np.asarray(im).astype(int)

def selected(a):
    for k, y in ROWS.items():
        band = a[y - 12:y + 12, 945:1250].reshape(-1, 3)
        ink = band[np.abs(band - [255, 255, 255]).sum(1) > 60]
        if len(ink) and (np.abs(ink - [159, 90, 31]).sum(1) < 40).mean() < 0.5:
            return k

def move_to(db, item):
    order = list(ROWS)
    for _ in range(10):
        cur = selected(grab("_menu"))
        if cur == item: return
        db.tap("up" if order.index(cur) > order.index(item) else "down", 0.1); time.sleep(0.8)
    raise RuntimeError(f"could not reach {item}")

with Dosbox() as db:
    move_to(db, "Mission Targets")
    time.sleep(0.5)
    fr = np.stack([grab("_tgt")[96:768, 32:928] for _ in range(10)])
    db.tap("enter", 0.1); time.sleep(1.5); grab("objectives")
    db.tap("enter", 0.1); time.sleep(1.5)
    move_to(db, "Flight Plan")
    db.tap("enter", 0.1); time.sleep(1.5); grab("flight_plan")
    db.tap("enter", 0.1); time.sleep(1.5)
    move_to(db, "Radar Sites")
var = (fr.max(0) - fr.min(0)).sum(2) > 60
cl = []
for x, y in zip(*np.nonzero(var)[::-1]):
    for c in cl:
        if abs(c[0] / c[2] - x) < 15 and abs(c[1] / c[2] - y) < 15: c[0] += x; c[1] += y; c[2] += 1; break
    else: cl.append([x, y, 1])
tg = [(round(c[0] / c[2] + 32), round(c[1] / c[2] + 96)) for c in cl if c[2] > 5]
json.dump(tg, open(OUT / "targets_px.json", "w"))
print("flashing targets (px):", tg)
