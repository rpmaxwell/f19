"""Offline MissileWatch on memlog flights (PRD acceptance 2): every snapshot goes through MissileWatch.step in observe
mode, with nav's own wanted heading at that moment (logs/nav/<run>.csv, nearest start time) as the plan.

  uv run python scripts/missile_replay.py <memlog run> [...] [-q]     (or: analysis/missile_game.py replay <run>)

Prints the module's events (launch seen, prediction, chosen action) and, per missile, the first prediction against
the real outcome (damage count up by opp + 1 while it was live = hit)."""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import bisect, csv, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis")); sys.path.insert(0, str(ROOT / "scripts"))
import missile_game as G
from missile_watch import MissileWatch

def nav_want(run):
    navs = sorted(S.logs.glob("nav/[0-9]*.csv"), key=lambda p: abs(int(p.stem) - int(run)))[:1]
    T, W = [], []
    for r in csv.DictReader(open(navs[0])) if navs else []:
        try: T.append(float(r["t"])); W.append(float(r["want"]))
        except (ValueError, TypeError, KeyError): pass
    return lambda t: W[min(len(W) - 1, bisect.bisect_left(T, t))] if T else None

def main(runs, quiet=False):
    for run in runs:
        snaps = G.load_run(run)
        if not snaps: print(run, "no snapshots"); continue
        if not any(sn["missiles"] for sn in snaps): print(f"=== {run}: no enemy missiles"); continue
        want = nav_want(run)
        mw = MissileWatch("observe")
        first, real = {}, {}
        print(f"=== {run}: {len(snaps)} snapshots, opp {snaps[0]['world']['opp']}")
        t_cpu = []
        for k, sn in enumerate(snaps):
            P = sn["player"]
            w = want(sn["t"])
            rw = w if w is not None else P["hdg"] * 360 / 65536
            t0 = time.perf_counter()
            _, ev = mw.step(dict(alt=P["alt"], spd=P["kt"], hdg=P["hdg"] * 360 / 65536), (P["x"], P["y"]), sn["t"], rw, sn)
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
            if not quiet or G.SPECS[key[1]]["name"] != "SA-14" or not good:
                print(f"    {'OK ' if good else 'BAD'} {G.SPECS[key[1]]['name']:6} slot {key[0]} clk {clk}: first prediction {pred}, real {rl}")
        print(f"  {ok}/{len(first)} first predictions right")

def closed_loop(sn, mode="act", cycle_real=0.13, ratio=2.45, latency_real=0.3, secs=40.0, verbose=False):
    """Fly a snapshot's live missiles forward with the game rules while MissileWatch flies the player model (its own
    turn / roll / speed model) and drops CMs: a nav cycle every cycle_real s, each CM / jammer appearing latency_real s
    after the request. mode 'off' = straight on, nothing dropped (baseline). Launcher bits and aircraft stay as in the
    snapshot. -> ({missile: outcome}, [events], stores used)."""
    fs = sn["world"]["fs"]; fdt = 2.0 / fs
    w = G.make_world(sn); ms = [m.copy() for m in sn["missiles"]]
    P = dict(sn["player"]); st = [float(P["x"]), float(P["y"]), P["hdg"] * 360 / 65536, G.s16(P["roll"]) * 360 / 65536, float(P["kt"])]
    route = st[2]
    mw = MissileWatch(mode if mode != "off" else "observe")
    from missile_watch import Candidate
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
            ov, ev = mw.step(dict(alt=P["alt"], spd=int(st[4]), hdg=st[2]), (st[0], st[1]), gt / ratio, route, snap)
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
            o, info = G.frame(m, w, px, py, P["alt"], ph)
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
        snaps = G.load_run(run); seen = set()
        for sn in snaps:
            new = [m for m in sn["missiles"] if (m.slot, m.spec, m.lau) not in seen]
            seen = {(m.slot, m.spec, m.lau) for m in sn["missiles"]}
            if not new or all(m.spec == 0x21 for m in new) and "-all" not in sys.argv: continue
            base, _, _ = closed_loop(sn, "off", **kw)
            act, evs, used = closed_loop(sn, "act", **kw)
            print(f"{run} clk {sn['clock']}: {', '.join(f'{m.name} s{m.slot} {G.gdist(m.x - sn['player']['x'], m.y - sn['player']['y']) / 64:.1f} km' for m in new)}")
            print(f"   straight on: {base}")
            print(f"   MissileWatch: {act}   stores used (flares, chaff, decoys) {used}")
            for e in evs:
                if "MISSILE EVASION" in e or "re-plan" in e or "missile evasion:" in e: print("     ", e)

if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("-")]
    if "--closed-loop" in sys.argv:
        kw = {}
        for x in sys.argv:
            if x.startswith("--latency="): kw["latency_real"] = float(x.split("=")[1])
            if x.startswith("--cycle="): kw["cycle_real"] = float(x.split("=")[1])
        closed_loop_main(a, **kw)
    else:
        main(a, quiet="-q" in sys.argv)
