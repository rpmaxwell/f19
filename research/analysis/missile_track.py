"""Per-snapshot track of each enemy missile vs the player: range, closing, seeker cone (22.5 deg), IR tail rule (45 deg)."""
import json, math, struct, sys
from pathlib import Path
ROOT = Path("/Users/robmaxwell/projects/f19")
eg = (ROOT/"analysis/egame_re/EGAME.bin").read_bytes(); DSF = 0x1ECF0
def specname(s): return eg[DSF+0x4C10+0x12*s:][:8].split(b"\0")[0].decode("latin1")
def s16(b, ds): return struct.unpack_from("<h", b, ds - 0x490)[0]
def u16(b, ds): return struct.unpack_from("<H", b, ds - 0x490)[0]
def gd(dx, dy): ax, ay = abs(dx), abs(dy); return max(ax, ay) + min(ax, ay)//2
def adiff(a, b): d = (a - b) & 0xFFFF; d = d - 0x10000 if d > 0x7FFF else d; return abs(d)*360/65536
def bearing(dx, dy): return int(round(math.atan2(dx, -dy)/(2*math.pi)*65536)) & 0xFFFF   # Y grows south (vY = -cos)
summary = []
for run in sys.argv[1:]:
    d = ROOT/"logs/memlog"/run
    tracks = {}
    for f in sorted(d.glob("snap_*.bin")):
        b = f.read_bytes(); t = float(f.stem[5:])
        px, py, palt, phdg = u16(b, 0x94EE), u16(b, 0x94FE), s16(b, 0x46E0), u16(b, 0x46DA)
        dmg, clock, cm = u16(b, 0x95BE), u16(b, 0x6630), s16(b, 0x5212)
        for i in range(8):
            o = 0x53FC + 0x18*i
            x, y, alt, spd, hdg, pit, ttl, spec, lau = (s16(b, o+k) for k in (0, 2, 4, 6, 8, 0xA, 0xE, 0x10, 0x16))
            if ttl <= 0 or not 0 < spec < 0x26: tracks.pop(i, None); continue
            dx, dy = px - x, py - y
            rng = gd(dx, dy) + abs(palt - alt)//32
            brg = bearing(dx, dy)
            row = (t, clock, rng, spd, ttl, adiff(brg, hdg), adiff(hdg, phdg), alt, palt, dmg, cm)
            tr = tracks.setdefault(i, {"spec": spec, "lau": lau, "rows": []}); tr["rows"].append(row)
            if len(tr["rows"]) == 1: summary.append((run, i, tr))
for run, i, tr in summary:
    R = tr["rows"]; mn = min(R, key=lambda r: r[2])
    print(f"\n{run} slot {i} {specname(tr['spec'])} launcher {tr['lau']}  first range {R[0][2]} u  min {mn[2]} u (speed {mn[3]})  dmg {R[0][9]}->{R[-1][9]}")
    if "-v" in sys.argv:
        for r in R: print("   t %.1f clk %d rng %4d spd %2d ttl %4d off-bore %5.1f  vs-our-hdg %5.1f  alt %5d us %5d dmg %d decoy %d" % r)
import collections
c = collections.Counter()
for run, i, tr in summary:
    if tr["spec"] != 0x21: continue
    R = tr["rows"]; first = R[0]
    tail = first[6] <= 45
    c[("first-snapshot IR tail (missile hdg within 45 of ours)" if tail else "first-snapshot outside 45 deg (seeker blind)")] += 1
    c["min range < 100 u" if min(r[2] for r in R) < 100 else "min range >= 100 u"] += 1
    c["launch range km: " + str(first[2] // 64)] += 1
print(); [print(f"  {k}: {v}") for k, v in sorted(c.items())]
