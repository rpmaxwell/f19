"""Passive EMV-gauge logger (screenshots only; never touches the automation port).

The EMV scale is the vertical gauge in the middle of the cockpit panel. In game
pixels (320x200) its inner column is x 154-165, rows 123-173 (51 rows):
  - your EMV: dark red (156,31,20) rising from the bottom
  - enemy radar bars: descending from the top; ground radars in the left half,
    aircraft radars in the right half (manual p.~45). Technical Supplement
    colour key (16-colour): red = not detected you, pink = poor detection,
    yellow = detected you; light blue / white = aircraft radar not / detected.
Logs ~4 Hz to logs/emv/<start>.csv until logs/emv/STOP exists.

  emv_logger.py <window_id>
"""
import csv, subprocess, sys, time
from pathlib import Path
import numpy as np
from PIL import Image

WIN = sys.argv[1]
D = Path(__file__).resolve().parent.parent / "logs" / "emv"; D.mkdir(parents=True, exist_ok=True)
STOP = D / "STOP"; STOP.unlink(missing_ok=True)
CAP = Path("/tmp") / f"emv_{WIN}.png"
X0, X1, Y0, Y1 = 154, 166, 123, 174            # game-pixel box of the inner column
BG = (0, 0, 163)
NAMES = {(156, 31, 20): "red", (236, 98, 92): "pink", (255, 255, 115): "yellow",
         (138, 252, 253): "lightblue", (255, 255, 255): "white"}

def classify(c):
    best = min(NAMES, key=lambda k: sum(abs(a - b) for a, b in zip(k, c)))
    return NAMES[best] if sum(abs(a - b) for a, b in zip(best, c)) < 60 else f"rgb{tuple(c)}"

def gauge(img):
    a = np.asarray(img.convert("RGB"))
    if a.shape[1] == 640: a = a.repeat(2, 0).repeat(2, 1)
    g = a[58 + 4 * Y0:58 + 4 * Y1:4, 2 + 4 * X0:2 + 4 * X1:4].astype(int)    # (rows, cols, 3)
    rows = len(g)
    def is_bg(px): return abs(px[0] - BG[0]) + abs(px[1] - BG[1]) + abs(px[2] - BG[2]) < 40
    # EMV: contiguous non-background rows from the bottom across the middle columns
    emv = 0
    for r in range(rows - 1, -1, -1):
        if all(not is_bg(g[r, c]) for c in (4, 6, 8)): emv += 1
        else: break
    out = {"emv_rows": emv, "gauge_rows": rows}
    for side, cols in (("ground", range(0, 6)), ("air", range(6, 12))):
        depth, colour = 0, ""
        for r in range(0, rows - emv):
            px = [g[r, c] for c in cols]
            nb = [p for p in px if not is_bg(p)]
            if len(nb) >= 2: depth = r + 1; colour = classify(tuple(nb[0]))
        out[f"{side}_bar_rows"] = depth; out[f"{side}_colour"] = colour
    return out

START = int(time.time())
f = open(D / f"{START}.csv", "w", newline="", buffering=1); w = None
# Cockpit panel frames (game pixels, rows PANEL_Y0..199, full width) for tying the
# left display's radar pulses to the gauge bars: logs/emv/<start>/<t>.png, ~4 Hz.
PANEL = D / str(START); PANEL.mkdir(exist_ok=True); PANEL_Y0 = 100
fails = 0
while not STOP.exists():
    t0 = time.time()
    CAP.unlink(missing_ok=True)                       # never re-read a stale capture
    ok = subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(CAP)], capture_output=True).returncode == 0 and CAP.exists()
    if not ok:
        fails += 1
        if fails == 20: print(f"emv_logger: window {WIN} cannot be captured (DOSBox relaunched? new window id) - logging nothing", flush=True)
        time.sleep(1.0); continue
    fails = 0
    try:
        im = Image.open(CAP); r = gauge(im)
    except Exception: time.sleep(0.25); continue
    r = {"t": f"{(t0 + time.time()) / 2:.2f}", **r}
    try:
        a = np.asarray(im.convert("RGB"))
        if a.shape[1] == 640: a = a.repeat(2, 0).repeat(2, 1)
        Image.fromarray(a[58 + 4 * PANEL_Y0:58 + 4 * 200:4, 2:2 + 4 * 320:4]).save(PANEL / f"{r['t']}.png")
    except Exception: pass
    if w is None: w = csv.DictWriter(f, fieldnames=list(r)); w.writeheader()
    w.writerow(r)
    time.sleep(max(0, 0.25 - (time.time() - t0)))
