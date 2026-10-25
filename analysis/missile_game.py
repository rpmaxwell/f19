"""Shim: the game's enemy-missile rules now live in src/f19/rules/missiles.py (Missile, World, frame, simulate, the
seeker / jammer / CM / launcher rules, the spec table). This module re-exports them and keeps the memlog side:
the snapshot parser (also used by scripts/f19_state.read_missile_world), the R1 gate and the outcome check.

  missile_game.py check [runs...]     R1 gate: replay every memlog missile snapshot-to-snapshot + outcomes
  missile_game.py replay <run>        offline MissileWatch (observe mode) on a memlog flight (PRD acceptance 2)

Units: waypoint units (64 per km), game distance max + min/2, alt ft, angles 65536 = 360 deg (heading 0 north,
clockwise, Y grows south), fs = frames per 2-s tick (ds:4EFC). One frame = 2 / fs game s.
DEAD_CM and SPECS are the rules module's own: `missile_game.DEAD_CM = False` switches the rule there.
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import json, math, struct, sys  # noqa: E402,F401
import types as _types  # noqa: E402
from pathlib import Path  # noqa: E402

import f19.rules.missiles as _rules  # noqa: E402
from f19.rules.missiles import *  # noqa: E402,F401,F403
from f19.rules.missiles import (  # noqa: E402,F401
    CHAFF, CLASS_NAME, CONE, DECOY, FLARE, IR_TAIL, OVERSHOOT, SUPPORT_UNITS, Missile, World, clamp, cm_eligible, frame,
    idiv, jammed, launcher_ok, mulcos, mulsin, seeker_ok, simulate, spec, specs)
from f19.gamedata.exe import DS_FILE as DSF  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract finds them (see radar_game.py).
s16 = __import__("f19.rules.missiles", fromlist=["s16"]).s16
gdist = __import__("f19.rules.missiles", fromlist=["gdist"]).gdist
atan2g = __import__("f19.rules.missiles", fromlist=["atan2g"]).atan2g

_FORWARD = ("DEAD_CM", "SPECS")                  # live in f19.rules.missiles; read and written there
globals().pop("DEAD_CM", None)


def __getattr__(name):
    if name in _FORWARD: return getattr(_rules, name)
    if name == "_EG": return _rules.egame()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _Shim(_types.ModuleType):
    def __setattr__(self, k, v):
        if k in _FORWARD: setattr(_rules, k, v)
        else: super().__setattr__(k, v)


_sys.modules[__name__].__class__ = _Shim


# --- memory layout (shared by the memlog check and f19_state.read_missile_world) ---------------------------------------
PROJ, CMS, STORES, OPP, FS, FLAGS, NET_T, NET_REF, HOMING = 0x53FC, 0x520A, 0x4ED4, 0x4EF0, 0x4EFC, 0x684C, 0x5522, 0x6EAA, 0x7F96
PX, PY, PALT, PHDG, PPITCH, PROLL, PKT27, CLOCK, DMG_BITS, DMG_COUNT = 0x94EE, 0x94FE, 0x46E0, 0x46DA, 0x46DC, 0x46DE, 0x7F94, 0x6630, 0x4ECE, 0x95BE
TARGETS, N_TARGETS, SIMOBJ, FRAME, GRID = 0x80AA, 0x9C78, 0x8854, 0x5520, 0x85FC   # GRID: 16 x 16 territory cell bytes

def parse(get):
    """get(ds, n) -> bytes. -> dict(missiles, player, world kwargs, stores, sites, aircraft, clock, dmg)."""
    u = lambda a: struct.unpack("<H", get(a, 2))[0]
    s = lambda a: struct.unpack("<h", get(a, 2))[0]
    raw = get(PROJ, 0x18 * 8)
    ms = []
    for i in range(8):
        x, y, alt, spd, hdg, pit, _roll, ttl, sp, _t, _x14, lau = struct.unpack_from("<hhhhHhhhhhhh", raw, 0x18 * i)
        if ttl > 0 and 0 < sp < 0x26: ms.append(Missile(i, x, y, alt, spd, hdg, pit, ttl, sp, lau))
    cmr = get(CMS, 48)
    cms = [list(struct.unpack_from("<hhhhhh", cmr, 12 * j)[k] for k in (0, 1, 3, 4)) for j in range(4)]
    n = min(u(N_TARGETS), 120)
    recs = get(TARGETS, 16 * n)
    sites = {}
    for k in range(n):
        x, y, t, fl, al, cd = struct.unpack_from("<HHHHHh", recs, 16 * k + 2)
        if t: sites[k] = dict(record=k, x=x, y=y, type=t, flags=fl, alert=al, countdown=cd, dead=bool(fl & 0x80),
                              on=bool(fl & 2), seen=bool(fl & 0x10))
    sim = get(SIMOBJ, 0x24 * 20)
    aircraft = {}
    for k in range(20):
        x, y, alt = struct.unpack_from("<HHH", sim, 0x24 * k)
        typ, fl = struct.unpack_from("<HH", sim, 0x24 * k + 0x14)
        al = struct.unpack_from("<H", sim, 0x24 * k + 0x20)[0]
        if x or y: aircraft[k] = dict(slot=k, x=x, y=y, alt=alt, type=typ, flags=fl, alert=al, seen=bool(fl & 8))
    player = dict(x=u(PX), y=u(PY), alt=s(PALT), hdg=u(PHDG), pitch=s(PPITCH), roll=s(PROLL), kt=u(PKT27) // 27)
    world = dict(opp=u(OPP), fs=u(FS), flags=u(FLAGS), net_stale=s(NET_T) < s(NET_REF), cms=cms, dmg_bits=u(DMG_BITS))
    return dict(missiles=ms, player=player, world=world, stores=struct.unpack("<3H", get(STORES, 6)), sites=sites,
                aircraft=aircraft, grid=bytes(get(GRID, 256)), clock=u(CLOCK), dmg=u(DMG_COUNT), homing=u(HOMING), frame=u(FRAME))

def make_world(snap):
    W = snap["world"]; S, A = snap["sites"], snap["aircraft"]
    return World(W["opp"], W["fs"], W["flags"], W["net_stale"], W["cms"],
                 site_seen=lambda r: S.get(r, {}).get("seen", False), ac_seen=lambda k: A.get(k, {}).get("seen", False),
                 dmg_bits=W["dmg_bits"])

def load_snap(path):
    b = Path(path).read_bytes()
    if len(b) < 0xC000: return None
    snap = parse(lambda a, n: b[a - 0x490: a - 0x490 + n])
    if not 0 <= snap["world"]["opp"] <= 3 or not 1 <= snap["world"]["fs"] <= 16: return None
    snap["t"] = float(Path(path).stem[5:]); return snap

def load_run(run):
    d = S.logs / "memlog" / str(run)
    w = json.loads((d / "windows.json").read_text())
    if w["windows"][0][0] != "egame_data": return []
    return [s for s in (load_snap(f) for f in sorted(d.glob("snap_*.bin"))) if s]


# --- R1 gate ----------------------------------------------------------------------------------------------------------
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

def check(runs):
    import collections
    st = collections.defaultdict(lambda: [0, 0, 0, 0, 0.0, 0.0])   # n, hdg ok, pos ok, both ok, max dh, max dpos/step
    turning = [0, 0]
    rows, skew = [], [0]
    for run in runs:
        snaps = load_run(run)
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

def outcomes(runs, verbose=False):
    """Each missile flight from its FIRST snapshot, flown against the logged player track (snapshots interpolated) and the
    logged launcher bits, to the next snapshot it isn't in. Real outcome: damage count up by opp + 1 = hit."""
    res = []
    for run in runs:
        snaps = load_run(run)
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


if __name__ == "__main__":
    import glob
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    runs = sys.argv[2:] or sorted(Path(p).name for p in glob.glob(str(S.logs / "memlog/*")))
    if cmd == "check":
        check(runs); outcomes(runs, verbose="-v" in sys.argv)
    elif cmd == "replay":
        sys.path.insert(0, str(ROOT / "scripts"))
        import missile_replay; missile_replay.main(runs)
    else:
        print(__doc__)
