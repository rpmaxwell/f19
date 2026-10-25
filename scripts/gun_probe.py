"""Gun probe: watch the 20 mm gun from memory while someone flies (read-only, sends no keys).

Logs every cycle to logs/gun/<time>.csv and prints:
  - the CCIP (where a bullet fired now would hit, from the nose line; see notes/gun_attack_plan.md)
  - each bullet as it spawns (from its own slot) and each ground impact (ds:94EA/94F6), with the miss between
    the CCIP at spawn time and the real impact (the number that validates the aiming model)
  - with --target X,Y (waypoint units): range, CCIP miss vs the kill radius, and FIRE / PULL cues

  gun_probe.py [--target 9920,13120] [--floor 150]
"""
import argparse, csv, math, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
from f19_state import find_offset, _peek_many, DS, GUN_ROUNDS, OPP

ALT, HDG, PITCH = DS + 0x46E0, DS + 0x46DA, DS + 0x46DC     # ft, 65536 = 360 deg
UNITS_XY = DS + 0x94EE                                       # player X (u16, 64/km); Y at +0x10 (ds:94FE)
IMPACT = DS + 0x94EA                                         # last gun impact X; Y ds:94F6, alt ds:94FC
SLOTS, NSLOTS, FS = DS + 0x9B88, DS + 0x855E, DS + 0x4EFC    # bullet slots 12 B (X, Y, alt, vX, vY, vAlt)
TARGETS = DS + 0x80AA
GUN_V = 4                                                    # matrix value vs SAM Radar / Mobile SAM

def octo(dx, dy):
    a, b = abs(dx), abs(dy); return max(a, b) + min(a, b) / 2

def ccip(x, y, alt, pitch, hdg, fs, nslots):
    """Impact point (units) of a bullet fired now, or None. The game keeps bullet velocities as whole units a frame
    (speed r = 112 // fs; climb in units of 32 ft), so the dive angle comes in steps of ~1/r rad and the range in
    steps of r: 2026-10-18 flight, alt 626 at -3.7 deg gave v (1, 22, -32 ft) and an impact 20 frames out (440 units),
    not the 300 of the exact nose line. Rounding fits all three components seen so far (trunc doesn't fit vY)."""
    r = 112 // max(fs, 1)
    k = round(r * math.sin(math.radians(-pitch)))               # units of descent per frame
    if k <= 0: return None
    rh = round(r * math.cos(math.radians(pitch)))
    vx, vy = round(rh * math.sin(math.radians(hdg))), -round(rh * math.cos(math.radians(hdg)))
    a0 = alt - 2 - 32 * k                                       # after the spawn step
    n = (a0 // (32 * k) + 1 if a0 >= 0 else 0) + 1              # frames until alt < 0
    if n > 2 * nslots: return None                              # the slot is reused first: out of reach
    return x + n * vx, y + n * vy, octo(n * vx, n * vy)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", help="X,Y in waypoint units")
    ap.add_argument("--floor", type=float, default=150.0)
    ap.add_argument("--t-pull", type=float, default=4.0, help="s of current sink to allow for the pull-out")
    a = ap.parse_args()
    tgt = tuple(float(v) for v in a.target.split(",")) if a.target else None
    db = Dosbox()
    while True:                                               # wait for EGAME (briefing / takeoff screens)
        try: o = find_offset(db); break
        except RuntimeError: print("waiting for EGAME...", flush=True); time.sleep(3)
    out = Path(__file__).resolve().parent.parent / "logs" / "gun"; out.mkdir(parents=True, exist_ok=True)
    f = open(out / f"{int(time.time())}.csv", "w", newline=""); w = csv.writer(f)
    w.writerow("t alt pitch hdg x y rounds fs ccip_x ccip_y ccip_d imp_x imp_y event".split())
    print(f"logging to {f.name}", flush=True)
    last_imp, last_rounds, seen, prev, born = None, None, {}, None, {}
    while True:
        now = time.time()
        r = _peek_many(db, [(ALT + o, 2), (HDG + o, 4), (UNITS_XY + o, 2), (UNITS_XY + 0x10 + o, 2), (GUN_ROUNDS + o, 2),
                            (FS + o, 2), (NSLOTS + o, 2), (IMPACT + o, 2), (IMPACT + 0xC + o, 2), (OPP + o, 2)] +
                       [(SLOTS + o + k, 48) for k in range(0, 240, 48)])   # PEEKs over 64 B time out
        r = r[:9] + [b"".join(r[10:]), r[9]]
        if any(len(v) == 0 for v in r): time.sleep(0.5); continue
        alt, = struct.unpack("<h", r[0]); hdg_raw, pit_raw = struct.unpack("<Hh", r[1])
        hdg, pitch = hdg_raw * 360 / 65536, pit_raw * 360 / 65536
        x, y = struct.unpack("<H", r[2])[0], struct.unpack("<H", r[3])[0]
        rounds, fs, nslots = (struct.unpack("<H", v)[0] for v in r[4:7])
        imp = (struct.unpack("<H", r[7])[0], struct.unpack("<H", r[8])[0])
        opp = struct.unpack("<H", r[10])[0]
        radius = GUN_V * 6 // (opp + 2)                       # kill if octagonal miss < radius
        c = ccip(x, y, alt, pitch, hdg, fs, nslots)
        events = []
        # bullets: a slot is a new bullet when it was empty, its velocity changed, or it jumped backwards along it
        for i in range(min(nslots + 4, 20)):
            bx, by, balt, vx, vy, va = struct.unpack_from("<hhhhhh", r[9], 12 * i)
            cur = (bx, by, vx, vy, va) if (bx or by) else None
            old = seen.get(i); seen[i] = cur
            if not cur or i >= nslots: continue                  # the last 4 slots are someone else's
            if old and old[2:] == cur[2:] and (bx - old[0]) * vx + (by - old[1]) * vy >= 0: continue
            pred = (bx + vx * balt / -va, by + vy * balt / -va) if va < 0 else None   # its own straight line
            born[i] = (now, c, pred)
            events.append(f"bullet slot {i}: alt {balt} v ({vx},{vy},{va}) own-line impact "
                          f"{'(%.0f,%.0f)' % pred if pred else 'none (nose not down)'}")
        if last_imp is not None and imp != last_imp:
            msg = f"IMPACT at ({imp[0]},{imp[1]})"
            best = min((b for b in born.values() if b[1]), key=lambda b: octo(b[1][0] - imp[0], b[1][1] - imp[1]), default=None)
            if best: msg += f"  CCIP-at-spawn miss {octo(best[1][0] - imp[0], best[1][1] - imp[1]):.1f} u"
            if tgt: msg += f"  vs target {octo(imp[0] - tgt[0], imp[1] - tgt[1]):.1f} u (kill < {radius})"
            events.append(msg)
        last_imp = imp
        if last_rounds is not None and rounds != last_rounds: events.append(f"rounds {last_rounds} -> {rounds}")
        last_rounds = rounds
        vs = 0.0
        if prev: vs = (alt - prev[1]) / max(now - prev[0], 1e-3)
        prev = (now, alt)
        line = f"alt {alt:5d}  pitch {pitch:+6.2f}  hdg {hdg:6.1f}  rounds {rounds:3d}  fs {fs}  "
        line += f"CCIP {c[2] * 15.625:5.0f} m ahead" if c else "CCIP none (nose not down)"
        if tgt:
            rng = octo(tgt[0] - x, tgt[1] - y)
            line += f"  | target {rng * 15.625 / 1000:5.2f} km"
            if c:
                miss = octo(c[0] - tgt[0], c[1] - tgt[1]); line += f"  miss {miss:5.1f} u (r {radius})"
                if miss < radius - 1: line += "  ** FIRE **"
            if alt + vs * a.t_pull < a.floor: line += "  !! PULL !!"
        print(line, flush=True)
        for e in events: print("   " + e, flush=True)
        w.writerow([f"{now:.2f}", alt, f"{pitch:.2f}", f"{hdg:.2f}", x, y, rounds, fs,
                    f"{c[0]:.1f}" if c else "", f"{c[1]:.1f}" if c else "", f"{c[2]:.1f}" if c else "",
                    imp[0], imp[1], " | ".join(events)]); f.flush()
        time.sleep(0.1)

if __name__ == "__main__":
    main()
