"""Build a contact sheet of fuel_watch strips, keeping only frames where the
time/fuel readout changed. Usage: fuel_sheet.py <since_epoch> <until_epoch> <out.png>"""
import sys
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw

d = Path(__file__).resolve().parent.parent / "logs" / "fuel_watch"
lo, hi, out = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
files = sorted((float(p.stem), p) for p in d.glob("1*.png") if lo <= float(p.stem) <= hi)
rows, prev = [], None
for t, p in files:
    im = Image.open(p).convert("RGB").crop((0, 0, 800, 36))
    key = im.crop((0, 0, 625, 36))
    if prev is None or ImageChops.difference(key, prev).getbbox():
        rows.append((t, im)); prev = key
sheet = Image.new("RGB", (900, 38 * len(rows)), (0, 0, 0))
dr = ImageDraw.Draw(sheet)
for i, (t, im) in enumerate(rows):
    sheet.paste(im, (100, i * 38))
    dr.text((4, i * 38 + 12), f"{t - lo:6.1f}s", fill=(255, 255, 0))
sheet.save(out)
print(len(files), "frames,", len(rows), "rows")
