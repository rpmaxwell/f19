"""Scan every memlog run for enemy projectile slots 0-7, CM slots, CM stores and damage count."""
import json, struct, sys
from pathlib import Path
ROOT = Path("/Users/robmaxwell/projects/f19")
eg = (ROOT/"analysis/egame_re/EGAME.bin").read_bytes(); DSF = 0x1ECF0
def specname(s): return eg[DSF+0x4C10+0x12*s:][:8].split(b"\0")[0].decode("latin1") if 0 <= s < 0x30 else f"?{s}"
def u16(b, ds): return struct.unpack_from("<H", b, ds - 0x490)[0]
def s16(b, ds): return struct.unpack_from("<h", b, ds - 0x490)[0]
for d in sorted((ROOT/"logs/memlog").iterdir()):
    w = json.loads((d/"windows.json").read_text())
    if w["windows"][0][0] != "egame_data" or w["windows"][0][1] != 312352: print(d.name, "other layout", w); continue
    snaps = sorted(d.glob("snap_*.bin"))
    if not snaps: continue
    launches, live, prev, cmuse, dmg0, dmg1, opp = {}, set(), {}, [], None, None, None
    stores0 = None
    for f in snaps:
        b = f.read_bytes(); t = float(f.stem[5:])
        opp = u16(b, 0x4EF0)
        st = tuple(u16(b, 0x4ED4 + 2*k) for k in range(3))
        if stores0 is None: stores0 = st
        if prev.get("st") and st != prev["st"]: cmuse.append((t, prev["st"], st))
        prev["st"] = st
        dc = u16(b, 0x95BE); dmg0 = dc if dmg0 is None else dmg0; dmg1 = dc
        for i in range(8):
            o = 0x53FC + 0x18*i
            ttl, spec, launcher = s16(b, o+0xE), s16(b, o+0x10), s16(b, o+0x16)
            if ttl > 0 and 0 < spec < 0x26:
                key = (i, launcher, spec)
                if key not in live: launches.setdefault(spec, []).append((round(t - snaps[0].stat().st_mtime + 0, 0), launcher))
                live.add(key)
            else:
                live = {k for k in live if k[0] != i}
    if launches or cmuse or dmg1 != dmg0:
        print(f"{d.name} opp={opp} snaps={len(snaps)} stores {stores0}->{st} dmg {dmg0}->{dmg1}")
        for s, L in launches.items(): print(f"   {specname(s):8} x{len(L)} launchers {sorted(set(l for _, l in L))}")
        for c in cmuse[:12]: print("   CM", c)
