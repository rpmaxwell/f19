"""Load nav --memlog snapshots (+ nav positions) for a set of flight runs.

  from memlog_load import load
  T, D, P = load([run, ...])   # T: (n,) times, D: (n, bytes) uint8 snapshots,
                               # P: (n, 4) x, y, alt, hdg interpolated from logs/nav/<run~>.csv
Window layout per logs/memlog/<run>/windows.json (concatenated in order); egame_data
starts at old physical 0x3C000 (+ offset).
"""
import csv, json, glob
from pathlib import Path
import numpy as np
ROOT = Path(__file__).resolve().parent.parent

def load(runs):
    T, D, N = [], [], []
    for run in runs:
        d = ROOT / "logs/memlog" / str(run)
        for f in sorted(d.glob("snap_*.bin")):
            T.append(float(f.stem[5:])); D.append(np.frombuffer(f.read_bytes(), np.uint8))
        navs = sorted(ROOT.glob("logs/nav/[0-9]*.csv"), key=lambda p: abs(int(p.stem) - int(run)))[:1]
        for r in csv.DictReader(open(navs[0])):
            try:
                if r["x"] and int(r["spd"]) < 900: N.append((float(r["t"]), float(r["x"]), float(r["y"]), float(r["alt"]), float(r["hdg"])))
            except (ValueError, TypeError): pass
    o = np.argsort(T); T = np.array(T)[o]; D = np.stack(D)[o]
    N = np.array(sorted(N))
    P = np.stack([np.interp(T, N[:, 0], N[:, k]) for k in range(1, 5)], 1)
    return T, D, P
