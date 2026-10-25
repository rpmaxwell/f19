"""Locate each briefing radar site (blinking marker + range ellipse) from the
multi-grab captures written by scripts/cycle_radar_sites.py, merge with the
transcribed RADAR DATA panels, and write logs/radar_sites/sites.json."""
import json, sys
import numpy as np
from PIL import Image
from pathlib import Path

D = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "logs/radar_sites"
MAP = (32, 96, 928, 768)

def box(m, k):
    c = np.pad(m.astype(float), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]

def locate(panels):
    sites = sorted({p.stem.rsplit("_", 1)[0] for p in D.glob("site_*_*.png")})
    W = {s: np.stack([np.asarray(Image.open(p).convert("RGB").crop(MAP)).min(axis=2) > 230
                      for p in sorted(D.glob(f"{s}_*.png"))]) for s in sites}
    base = np.concatenate(list(W.values())).mean(0)
    out = []
    for s, (name, typ, rng) in zip(sites, panels):
        if rng <= 0: continue
        w = W[s]; m = ((w.any(0) & ~w.all(0)) | (w.mean(0) - base > 0.5)) & (base < 0.5)
        dn = box(m, 14); cy, cx = np.unravel_index(dn.argmax(), dn.shape); cx += 7; cy += 7
        ys, xs = np.nonzero(m)
        nb = np.hypot(xs - cx, ys - cy) < 12; cx, cy = xs[nb].mean(), ys[nb].mean()
        far = np.hypot(xs - cx, ys - cy) > 18
        sol, *_ = np.linalg.lstsq(np.c_[(xs[far] - cx) ** 2, (ys[far] - cy) ** 2], np.ones(far.sum()), rcond=None)
        a, b = 1 / np.sqrt(sol)
        out.append(dict(name=name, type=typ, range_km=rng, cx=float(cx) + MAP[0], cy=float(cy) + MAP[1],
                        a=float(a), b=float(b), px_per_km_x=float(a) / rng, px_per_km_y=float(b) / rng))
    return out

if __name__ == "__main__":
    panels = [tuple(x) for x in json.load(open(D / "panels.json"))]
    res = locate(panels)
    json.dump(res, open(D / "sites.json", "w"), indent=1)
    for r in res:
        print(f"{r['name']:<22} {r['type']} {r['range_km']:>4} km  at ({r['cx']:6.1f},{r['cy']:6.1f})  px/km {r['px_per_km_x']:.2f},{r['px_per_km_y']:.2f}")
