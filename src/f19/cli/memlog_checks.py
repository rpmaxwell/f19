"""Memlog checks of the game rules against recorded flights (was analysis/radar_game.py check, analysis/missile_game.py check|replay and
scripts/missile_replay.py).

  python -m f19.cli.memlog_checks radar [memlog dirs]          EMV, pulse colour and duty flag against every snapshot
  python -m f19.cli.memlog_checks missiles [runs] [-v]         R1 gate: replay every missile snapshot-to-snapshot, then the outcomes
  python -m f19.cli.memlog_checks replay <run>... [-q]         offline MissileWatch (observe) on a memlog flight, nav's wanted heading as the plan
  python -m f19.cli.memlog_checks closed-loop <run>... [-all]  every launch flown with the game rules against MissileWatch acting

Units: waypoint units (64 per km), game distance max + min/2, alt ft, angles 65536 = 360 deg (heading 0 north, clockwise, Y grows south),
fs = frames per 2-s tick (ds:4EFC). One frame = 2 / fs game s.
"""
import bisect
import csv
import glob
import json
import struct
import sys
import time
from pathlib import Path

from f19.core.settings import settings as S
from f19.pilot.reactions.missile import Candidate, MissileWatch, make_world
from f19.rules.missiles import SPECS, frame, gdist, parse_missile_world, s16, simulate
from f19.rules.radar import angle, duty_active, site_return


# --- radar -------------------------------------------------------------------------------------------------------------
def radar_check(dirs):
    """Replay memlog snapshots: EMV within +-1, pulse colour, duty flag."""
    ok_e = n_e = ok_c = n_c = ok_d = n_d = 0
    for dd in dirs:
        for f in sorted(glob.glob(dd + "/snap_*.bin")):
            b = Path(f).read_bytes()
            if len(b) < 0xC000: continue
            g = lambda a, t="<H": struct.unpack_from(t, b, a - 0x490)[0]
            opp = g(0x4EF0)
            if opp > 3: continue
            alt, kt, fl = g(0x46E0), g(0x7F94) // 27, g(0x684C)
            e_exact = (angle((alt // 3) * (opp + 1), 0x800) >> 8) + (lambda r: (0x80 - r if r >= 0x40 else r) // 2)(abs(((g(0x46DE, "<h") >> 8) + 128) % 256 - 128)) + (g(0x7F94) >> 10) + (24 if fl & 4 else 0) + (16 if fl & 0x10 else 0)
            n_e += 1; ok_e += abs(g(0x551C) - e_exact) <= 1
            k, col = g(0x94E4, "<h"), g(0x552C)
            px, py = g(0x94EE), g(0x94FE); cell = b[0x85FC - 0x490 + (py >> 11) * 16 + (px >> 11)]
            if 0 <= k < 70 and col in (12, 14):
                r = 0x80AA + 16 * k
                if 0 < g(r + 6) < 22:
                    s = site_return(px, py, alt, g(0x46DA), kt, opp, cell, g(r + 2), g(r + 4), g(r + 6))
                    n_c += 1; ok_c += s > 0 and s + g(0x551C) > 50
            fr, war, nsite, net = g(0x5520), g(0x4EF2), g(0x6646), g(0x95CE)
            if war <= 3 and nsite <= 80:
                for i in range(nsite):
                    r = 0x80AA + 16 * i
                    if g(r + 6) == 0 or g(r + 8) & 0x80: continue
                    n_d += 1; ok_d += duty_active(i, fr, g(r + 10), bool(g(r + 8) & 0x100), war, net >= 0x80) == bool(g(r + 8) & 2)
    print(f"EMV within +-1: {ok_e}/{n_e}   pink pulses predicted pink: {ok_c}/{n_c}   duty flag: {ok_d}/{n_d}")


# --- missiles: loading memlog snapshots ---------------------------------------------------------------------------------
def load_snap(path):
    b = Path(path).read_bytes()
    if len(b) < 0xC000: return None
    snap = parse_missile_world(lambda a, n: b[a - 0x490: a - 0x490 + n])
    if not 0 <= snap["world"]["opp"] <= 3 or not 1 <= snap["world"]["fs"] <= 16: return None
    snap["t"] = float(Path(path).stem[5:]); return snap

def load_run(run, logs=None):
    d = (logs or S.logs) / "memlog" / str(run)
    w = json.loads((d / "windows.json").read_text())
    if w["windows"][0][0] != "egame_data": return []
    return [s for s in (load_snap(f) for f in sorted(d.glob("snap_*.bin"))) if s]


# --- missiles: the R1 gate and the outcome check -------------------------------------------------------------------------
def lerp_traj(p0, p1, n):
    """Player trajectory between two snapshots, n frames, linear in position / alt / heading (shortest way)."""
    dh = s16(p1["hdg"] - p0["hdg"])
    def f(k):
        a = k / n
        return (int(round(p0["x"] + (p1["x"] - p0["x"]) * a)), int(round(p0["y"] + (p1["y"] - p0["y"]) * a)),
                int(round(p0["alt"] + (p1["alt"] - p0["alt"]) * a)), int(round(p0["hdg"] + dh * a)) & 0xFFFF)
    return f

def tracks(snaps):
    """[(spec, launcher, [(snap index, Missile)])] - one per missile flight (slot reused -> new track)."""
    out, live = [], {}
    for k, sn in enumerate(snaps):
        seen = set()
        for m in sn["missiles"]:
            key = live.get(m.slot)
            prev = key[-1][1] if key else None
            if prev is not None and prev.spec == m.spec and prev.lau == m.lau and 0 < prev.ttl - m.ttl <= 80:
                key.append((k, m))
            else:
                live[m.slot] = [(k, m)]; out.append((m.spec, m.lau, live[m.slot]))
            seen.add(m.slot)
        for sl in list(live):
            if sl not in seen: del live[sl]
    return out

def check(runs, logs=None):
    import collections
    st = collections.defaultdict(lambda: [0, 0, 0, 0, 0.0, 0.0])   # n, hdg ok, pos ok, both ok, max dh, max dpos/step
    turning = [0, 0]
    rows, skew = [], [0]
    for run in runs:
        snaps = load_run(run, logs)
        if not snaps: continue
        for sp, lau, tr in tracks(snaps):
            for (k0, m0), (k1, m1) in zip(tr, tr[1:]):
                n = m0.ttl - m1.ttl
                if k1 != k0 + 1 or not 0 < n <= 60: continue
                sn0, sn1 = snaps[k0], snaps[k1]
                w = make_world(sn0)
                r = simulate(m0, w, lerp_traj(sn0["player"], sn1["player"], n), n, stop_spent=False)
                mp = r["m"]
                dh = abs(s16(mp.hdg - m1.hdg)) * 360 / 65536
                step = max(1, m0.spd * 4 // w.fs)
                dp = gdist(mp.x - m1.x, mp.y - m1.y) / step
                if dh > 2 or dp > 1:          # snapshots aren't atomic (MemLog reads 64-B chunks in batches of 64 across
                    for nn in (n - 1, n + 1):  # loop cycles; slot 6 straddles a batch edge): allow one frame of skew
                        rr = simulate(m0, make_world(sn0), lerp_traj(sn0["player"], sn1["player"], nn), nn, stop_spent=False)["m"]
                        if abs(s16(rr.hdg - m1.hdg)) * 360 / 65536 <= 2 and gdist(rr.x - m1.x, rr.y - m1.y) / step <= 1:
                            skew[0] += 1; dh, dp = 0.0, 0.0; break
                g = st[(m0.name, w.opp)]
                g[0] += 1; g[1] += dh <= 2; g[2] += dp <= 1; g[3] += dh <= 2 and dp <= 1; g[4] = max(g[4], dh); g[5] = max(g[5], dp)
                actual_turn = abs(s16(m1.hdg - m0.hdg)) * 360 / 65536
                if actual_turn > 1.0: turning[0] += 1; turning[1] += dh <= 2 and dp <= 1
                if dh > 2 or dp > 1: rows.append((run, m0.name, m0.slot, sn0["clock"], n, f"{actual_turn:.1f}", f"{dh:.1f}", f"{dp:.2f}", r["last_why"]))
    print("R1 gate, snapshot -> next snapshot (heading within 2 deg, position within one frame's travel):")
    tot = [0, 0]
    for (name, opp), g in sorted(st.items()):
        print(f"  {name:7} opp {opp}: {g[3]}/{g[0]} pass   (heading {g[1]}, position {g[2]}; worst {g[4]:.1f} deg, {g[5]:.2f} steps)")
        tot[0] += g[0]; tot[1] += g[3]
    print(f"  all: {tot[1]}/{tot[0]} ({skew[0]} of them only with one frame of snapshot skew)   pairs where the missile "
          f"really turned > 1 deg: {turning[1]}/{turning[0]}")
    for r in rows[:25]: print("    miss:", *r)
    return tot[0] == tot[1]

def outcomes(runs, verbose=False, logs=None):
    """Each missile flight from its FIRST snapshot, flown against the logged player track (snapshots interpolated) and the
    logged launcher bits, to the next snapshot it isn't in. Real outcome: damage count up by opp + 1 = hit."""
    res = []
    for run in runs:
        snaps = load_run(run, logs)
        for sp, lau, tr in tracks(snaps):
            k0, m0 = tr[0]; kl, ml = tr[-1]
            if kl + 1 >= len(snaps): continue
            nxt = snaps[kl + 1]
            real = "hit" if nxt["dmg"] - snaps[kl]["dmg"] == snaps[kl]["world"]["opp"] + 1 and \
                   any(sum(1 for t in tracks(snaps[kl:kl + 2]) if t[0] == sp) for _ in [0]) else "no hit"
            # fly it: piecewise between snapshots, launcher bits / CMs from each snapshot
            m, outc, w = m0.copy(), None, None
            for k in range(k0, kl + 1):
                a, b = snaps[k], snaps[k + 1]
                nm = next((x for x in b["missiles"] if x.slot == m.slot and x.spec == m.spec), None)
                n = (a["clock"] and nm and (next(x for x in a["missiles"] if x.slot == m.slot).ttl - nm.ttl)) or \
                    max(1, round((b["t"] - a["t"]) * 2.6 * a["world"]["fs"] / 2))
                w = make_world(a)
                if k > k0: w.cms = [list(c) for c in a["world"]["cms"]]
                r = simulate(m, w, lerp_traj(a["player"], b["player"], n), n)
                m = r["m"]
                if r["outcome"] != "flying": outc = r; break
            pred = outc["outcome"] if outc else "flying"
            res.append((run, m0.name, m0.slot, a["clock"], pred, real, outc["closest"] if outc else r["closest"]))
    print("Outcomes (predicted from the first snapshot along the logged track / real):")
    for run, name, slot, clk, pred, real, cl in res:
        mark = "OK " if (pred in ("hit",)) == (real == "hit") else "BAD"
        if verbose or name != "SA-14" or mark == "BAD":
            print(f"  {mark} {run} {name:6} slot {slot}  predicted {pred:9} real {real:6}  closest {cl} u")
    ok = sum((p == "hit") == (r == "hit") for *_, p, r, _c in res)
    print(f"  {ok}/{len(res)} outcomes right")
    return res


# --- missiles: MissileWatch replays -------------------------------------------------------------------------------------
def nav_want(run, logs=None):
    navs = sorted((logs or S.logs).glob("nav/[0-9]*.csv"), key=lambda p: abs(int(p.stem) - int(run)))[:1]
    T, W = [], []
    for r in csv.DictReader(open(navs[0])) if navs else []:
        try: T.append(float(r["t"])); W.append(float(r["want"]))
        except (ValueError, TypeError, KeyError): pass
    return lambda t: W[min(len(W) - 1, bisect.bisect_left(T, t))] if T else None

def replay(runs, quiet=False, logs=None):
    for run in runs:
        snaps = load_run(run, logs)
        if not snaps: print(run, "no snapshots"); continue
        if not any(sn["missiles"] for sn in snaps): print(f"=== {run}: no enemy missiles"); continue
        want = nav_want(run, logs)
        mw = MissileWatch("observe")
        first, real = {}, {}
        print(f"=== {run}: {len(snaps)} snapshots, opp {snaps[0]['world']['opp']}")
        t_cpu = []
        for k, sn in enumerate(snaps):
            P = sn["player"]
            w = want(sn["t"])
            rw = w if w is not None else P["hdg"] * 360 / 65536
            t0 = time.perf_counter()
            _, ev = mw._step(dict(alt=P["alt"], spd=P["kt"], hdg=P["hdg"] * 360 / 65536), (P["x"], P["y"]), sn["t"], rw, sn)
            if sn["missiles"]: t_cpu.append(time.perf_counter() - t0)
            for e in ev:
                if not quiet or "SA-14" not in e: print(f"  clk {sn['clock']:5d} t {sn['t'] - snaps[0]['t']:7.1f}  {e}")
            for m in sn["missiles"]:
                key = mw._key(m)
                if key not in first: first[key] = (sn["clock"], mw.known.get(key, {}).get("pred"))
                if k + 1 < len(snaps) and snaps[k + 1]["dmg"] - sn["dmg"] == sn["world"]["opp"] + 1 and \
                        not any(x.slot == m.slot and x.spec == m.spec for x in snaps[k + 1]["missiles"] if x.ttl > 2 * sn["world"]["fs"]):
                    real[key] = "hit"
        print(f"  per missile: first prediction vs real   (MissileWatch cpu per cycle with missiles: "
              f"median {sorted(t_cpu)[len(t_cpu) // 2] * 1000 if t_cpu else 0:.0f} ms, max {max(t_cpu) * 1000 if t_cpu else 0:.0f} ms)")
        ok = 0
        for key, (clk, pred) in first.items():
            rl = real.get(key, "no hit"); good = (pred == "HIT") == (rl == "hit")
            ok += good
            if not quiet or SPECS[key[1]]["name"] != "SA-14" or not good:
                print(f"    {'OK ' if good else 'BAD'} {SPECS[key[1]]['name']:6} slot {key[0]} clk {clk}: first prediction {pred}, real {rl}")
        print(f"  {ok}/{len(first)} first predictions right")


def closed_loop(sn, mode="act", cycle_real=0.13, ratio=2.45, latency_real=0.3, secs=40.0, verbose=False):
    """Fly a snapshot's live missiles forward with the game rules while MissileWatch flies the player model (its own
    turn / roll / speed model) and drops CMs: a nav cycle every cycle_real s, each CM / jammer appearing latency_real s
    after the request. mode 'off' = straight on, nothing dropped (baseline). Launcher bits and aircraft stay as in the
    snapshot. -> ({missile: outcome}, [events], stores used)."""
    fs = sn["world"]["fs"]; fdt = 2.0 / fs
    w = make_world(sn); ms = [m.copy() for m in sn["missiles"]]
    P = dict(sn["player"]); st = [float(P["x"]), float(P["y"]), P["hdg"] * 360 / 65536, s16(P["roll"]) * 360 / 65536, float(P["kt"])]
    route = st[2]
    mw = MissileWatch(mode if mode != "off" else "observe")
    cmd = Candidate(route, 20, st[4])
    stores, pend, out, evs = list(sn["stores"]), [], {}, []
    names = {id(m): f"{m.name} s{m.slot}" for m in ms}
    next_cycle, lat = 0.0, latency_real * ratio
    for k in range(1, int(secs / fdt)):
        gt = k * fdt
        if gt >= next_cycle and mode != "off":
            next_cycle = gt + cycle_real * ratio
            snap = dict(sn, missiles=[m for m in ms if m.ttl > 0], stores=tuple(stores), frame=(sn["frame"] + k) & 0xFFFF,
                        world=dict(sn["world"], cms=[list(c) for c in w.cms], flags=w.flags),
                        player=dict(P, x=int(round(st[0])), y=int(round(st[1])), fx=st[0], fy=st[1], hdg=int(st[2] / 360 * 65536) & 0xFFFF,
                                    roll=int(st[3] / 360 * 65536), kt=int(st[4])))
            ov, ev = mw._step(dict(alt=P["alt"], spd=int(st[4]), hdg=st[2]), (st[0], st[1]), gt / ratio, route, snap)
            evs += [f"{gt:5.1f}s {e}" for e in ev]
            if mode == "act":
                if "want" in ov: cmd = Candidate(ov["want"], ov.get("bank_max", 20), ov.get("speed_kt", st[4]))
                elif not mw.plan: cmd = Candidate(route, 20, st[4])
                if ov.get("cm"): pend.append((gt + lat, ov["cm"]))
                if ov.get("jam_on"): pend.append((gt + lat, ov["jam_on"]))
        for t, kind in [q for q in pend if q[0] <= gt]:
            pend.remove((t, kind))
            if kind == "ecm": w.flags |= 0x10
            elif kind == "irj": w.flags |= 0x20
            elif stores[kind - 1] > 0: stores[kind - 1] -= 1; w.release(kind, int(round(st[0])), int(round(st[1])))
        px, py, ph = int(round(st[0])), int(round(st[1])), int(st[2] / 360 * 65536) & 0xFFFF
        for m in [m for m in ms if m.ttl > 0]:
            o, info = frame(m, w, px, py, P["alt"], ph)
            if verbose and k % 3 == 0: print(f"   {gt:5.1f}s {names[id(m)]} d {info['dist']} {info['why'] or 'locked'}")
            if o: out[names[id(m)]] = f"{o} at {gt:.1f}s"; m.ttl = 0      # first outcome only (a hit leaves ttl 2 fs)
        w.age()
        if all(m.ttl <= 0 for m in ms): break
        mw.advance(st, cmd, fdt)
    for m in ms:
        if names[id(m)] not in out: out[names[id(m)]] = "flying (no hit)" if m.ttl > 4 * fs else "spent"
    used = tuple(a - b for a, b in zip(sn["stores"], stores))
    return out, evs, used

def closed_loop_main(runs, **kw):
    """Every launch in the runs: baseline (straight on) vs MissileWatch acting."""
    for run in runs:
        snaps = load_run(run); seen = set()
        for sn in snaps:
            new = [m for m in sn["missiles"] if (m.slot, m.spec, m.lau) not in seen]
            seen = {(m.slot, m.spec, m.lau) for m in sn["missiles"]}
            if not new or all(m.spec == 0x21 for m in new) and "-all" not in sys.argv: continue
            base, _, _ = closed_loop(sn, "off", **kw)
            act, evs, used = closed_loop(sn, "act", **kw)
            print(f"{run} clk {sn['clock']}: {', '.join(f'{m.name} s{m.slot} {gdist(m.x - sn['player']['x'], m.y - sn['player']['y']) / 64:.1f} km' for m in new)}")
            print(f"   straight on: {base}")
            print(f"   MissileWatch: {act}   stores used (flares, chaff, decoys) {used}")
            for e in evs:
                if "MISSILE EVASION" in e or "re-plan" in e or "missile evasion:" in e: print("     ", e)


def main(argv):
    cmd, rest = (argv[0] if argv else ""), argv[1:]
    args = [a for a in rest if not a.startswith("-")]
    if cmd == "radar":
        from f19.rules.radar import check_np
        check_np()
        radar_check(args or sorted(glob.glob(str(S.logs / "memlog/*"))))
    elif cmd == "missiles":
        runs = args or sorted(Path(p).name for p in glob.glob(str(S.logs / "memlog/*")))
        check(runs); outcomes(runs, verbose="-v" in rest)
    elif cmd == "replay":
        replay(args, quiet="-q" in rest)
    elif cmd == "closed-loop":
        kw = {}
        for x in rest:
            if x.startswith("--latency="): kw["latency_real"] = float(x.split("=")[1])
            if x.startswith("--cycle="): kw["cycle_real"] = float(x.split("=")[1])
        closed_loop_main(args, **kw)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
