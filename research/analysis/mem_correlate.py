"""Correlate every 16-bit word of guest memory against HUD readings.

  mem_correlate.py <mem_snapshot_dir> <alt_hold_csv> [top_n]

Snapshots (raw 640 KB, timestamped by filename) come from scripts/mem_sampler.py;
the CSV (t, alt, spd, ...) from scripts/alt_hold.py. HUD values are linearly
interpolated to each snapshot's time. For every byte offset, the little-endian
word there is read as both unsigned and signed 16-bit; offsets are ranked by
|Pearson r| against altitude and against speed. Also lists 'ramps': words that
change steadily (|r| with time high) - candidates for position.
"""
import csv, sys
from pathlib import Path
import numpy as np

snap_dir, csv_path = Path(sys.argv[1]), sys.argv[2]
TOP = int(sys.argv[3]) if len(sys.argv) > 3 else 15

files = sorted(snap_dir.glob("snap_*.bin"), key=lambda p: float(p.stem[5:]))
ts = np.array([float(p.stem[5:]) for p in files])
rows = [r for r in csv.DictReader(open(csv_path)) if r["alt"]]
ht = np.array([float(r["t"]) for r in rows])
alt = np.interp(ts, ht, [float(r["alt"]) for r in rows])
spd_rows = [(float(r["t"]), float(r["spd"])) for r in rows if r["spd"] not in ("", "None")]
spd = np.interp(ts, *zip(*spd_rows))
keep = (ts >= ht[0]) & (ts <= ht[-1])
files, ts, alt, spd = [f for f, k in zip(files, keep) if k], ts[keep], alt[keep], spd[keep]
print(f"{len(files)} snapshots over {ts[-1]-ts[0]:.0f}s; alt {alt.min():.0f}-{alt.max():.0f}, spd {spd.min():.0f}-{spd.max():.0f}")

BASE = int(snap_dir.name.split("_")[1], 16) if "_" in snap_dir.name else 0
M = np.stack([np.frombuffer(f.read_bytes(), np.uint8) for f in files])
W16 = M[:, :-1].astype(np.uint16) | (M[:, 1:].astype(np.uint16) << 8)        # word at each byte offset
var_offs = np.nonzero(W16.max(0) != W16.min(0))[0]
print(f"{len(var_offs)} of {W16.shape[1]} word offsets vary (base phys {BASE:05X})")
U = W16[:, var_offs].astype(np.float32)
S = np.where(U >= 32768, U - 65536, U).astype(np.float32)

def corr(X, y):
    y = (y - y.mean()) / (y.std() + 1e-9)
    Xc = X - X.mean(0); sd = Xc.std(0)
    return (Xc * y[:, None]).mean(0) / np.where(sd > 0, sd, np.inf)

def seg(i):
    off = BASE + int(var_offs[i]); return f"phys {off:05X}"

def report(name, y):
    best = {}
    for kind, X in (("u16", U), ("s16", S)):
        r = corr(X, y)
        for i in np.argsort(-np.abs(r))[:TOP * 3]:
            o = i
            if o not in best or abs(r[i]) > abs(best[o][0]): best[o] = (r[i], kind)
    print(f"\n== {name}: top |r| ==")
    for o, (r, kind) in sorted(best.items(), key=lambda kv: -abs(kv[1][0]))[:TOP]:
        col = (U if kind == "u16" else S)[:, o]
        slope, icpt = np.polyfit(y, col, 1)
        print(f"  {seg(o)} {kind} r={r:+.4f}  value={col[0]:.0f}..{col[-1]:.0f}  ~{slope:+.3f}*{name}{icpt:+.0f}")

report("alt", alt)
report("spd", spd)
report("time", ts - ts[0])
