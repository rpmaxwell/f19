"""Check two decoded rules on memlog snapshots:
 A. IR (class 0) seeker: missile steers only while we're within 22.5 deg of its nose AND our heading is within 45 deg of its heading.
 B. Turn limit: |heading change| per game s <= 2 * min((opp+1)*256, agility*128) units."""
import math, struct, sys, collections
from pathlib import Path
ROOT = Path("/Users/robmaxwell/projects/f19")
eg = (ROOT/"analysis/egame_re/EGAME.bin").read_bytes(); DSF = 0x1ECF0
def spec(s): r = eg[DSF+0x4C10+0x12*s:][:18]; return r[:8].split(b"\0")[0].decode(), struct.unpack_from("<h", r, 12)[0], struct.unpack_from("<h", r, 14)[0]
def s16(b, ds): return struct.unpack_from("<h", b, ds - 0x490)[0]
def u16(b, ds): return struct.unpack_from("<H", b, ds - 0x490)[0]
def sd(a): a &= 0xFFFF; return a - 0x10000 if a > 0x7FFF else a
def bearing(dx, dy): return int(round(math.atan2(dx, -dy)/(2*math.pi)*65536)) & 0xFFFF
A = collections.Counter(); B = []
for run in sys.argv[1:]:
    prev = {}
    for f in sorted((ROOT/"logs/memlog"/run).glob("snap_*.bin")):
        b = f.read_bytes(); opp = u16(b, 0x4EF0); clk = u16(b, 0x6630)
        px, py, phdg = u16(b, 0x94EE), u16(b, 0x94FE), u16(b, 0x46DA)
        for i in range(8):
            o = 0x53FC + 0x18*i
            x, y, hdg, ttl, sp = s16(b, o), s16(b, o+2), u16(b, o+8), s16(b, o+0xE), s16(b, o+0x10)
            if ttl <= 0 or not 0 < sp < 0x26: prev.pop(i, None); continue
            name, cls, ag = spec(sp)
            if i in prev and prev[i][0] == sp and 0 < clk - prev[i][1] <= 3:
                _, c0, h0, ok0 = prev[i]
                dps = abs(sd(hdg - h0)) / ((clk - c0) * 2)          # units per game s
                lim = 2 * min((opp + 1) * 256, ag * 128)
                B.append((name, opp, dps, lim))
                if cls == 0: A[(ok0, dps > 0.05 * lim)] += 1
            off = abs(sd(bearing(px - x, py - y) - hdg)); tail = abs(sd(hdg - phdg))
            ok = off <= 0x1000 and (cls != 0 or tail <= 0x2000)
            prev[i] = (sp, clk, hdg, ok)
print("A. IR seeker rule (class 0), pairs of snapshots: (rule says tracking, heading actually changing): count")
for k, v in sorted(A.items()): print("   ", k, v)
print("B. turn rate vs decoded limit, units per game s:")
g = collections.defaultdict(list)
for n, o, d, l in B: g[(n, o, l)].append(d)
for (n, o, l), ds in sorted(g.items()): print(f"    {n:6} opp {o}: limit {l}, observed max {max(ds):.0f}, p90 {sorted(ds)[int(.9*len(ds))]:.0f}, n={len(ds)}, over limit x1.1: {sum(d > 1.1*l for d in ds)}")
