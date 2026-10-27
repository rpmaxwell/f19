"""WP-7a Arbiter vs nav's merge: nav's route + merge code (scripts/nav.py :495-642, exec'd verbatim by nav_legacy)
with the OLD behaviors (scripts/phases.py, missile_watch) next to FlyLeg + Arbiter with the NEW ones (f19.pilot), on
the same snapshots. Every tick: nav's override dict `ov` == Decision.overrides (Command.to_overrides minus the
new-only fields), the same final want, phase and leg, the same speed-hold target after Controls applied the merged
Command, the same keys (old: what the phases / CmKeys pressed; new: Controls.apply + key_ups) and the same mission-log
lines (without nav's time prefix).

Replays: all three fixtures from their first snapshot, --auto with the route the flight flew (takeoff, Maverick and
photo attacks, terrain escapes, FighterWatch evasion, MissileWatch on SA-14 / AA-9 launches). MissileWatch's
real-CPU-time search budget is lifted on both sides (deterministic search). Open loop: the recorded memory already
shows what nav's real keys did.
Synthetic: a closed loop with scripted reactions (the same script on the old interface and the new) that overlap in
every order, to pin the one saved-speed mechanism against nav's three, plus fuel transfer during an attack, the
missile hold of an attack, a gun attack and a landing hand-over.
"""
import dataclasses
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import nav_legacy as N
from pilot_equiv import NEW_ONLY, KeyLog, OldPhases, RecSnapDB, new_ov, product
import harness as H  # noqa: E402
from f19.core.clock import ManualClock
from f19.core.types import FlightContext, Route
from f19.memory import ReplayReader
from f19.pilot.arbiter import Arbiter
from f19.pilot.controls import Controls
from f19.pilot.loops import Modes, SpeedHold
from f19.pilot.phases import FlyLeg
from f19.pilot.reactions import CmKeys, FighterWatch, FuelTransfer, MissileWatch, TerrainEscape
from f19.pilot.runner import Reads

ph, fs, MW = product.phases(), product.f19_state(), product.missile_watch()
NAVDEFS = compile("\n".join([N.block("def fly_over(name):", "t -= Li; i += 1"), N.block("def load_route(path):", "return _w, _a"),
                             N.block("def leg_attrs(leg):", "def leg_attrs(leg):"), N.block("def build_attacks():", "return out"),
                             N.block("def time_mult(db):", "return float(m[0])")]), "nav.py:defs", "exec")
JOIN = compile(N.block('if s0["alt"] > CLIMB_ALT and len(WPS) > 2:', "if args.auto: thr.target = float("), "nav.py:join", "exec")


class OldNav:
    """nav's main-loop state in a namespace; tick() runs nav's route + merge blocks on the state in `db`."""
    def __init__(self, db, route_path, args, fuel, fighters, escape, cmkeys, gbyte, records=None):
        self.printed = []
        ns = dict(K=64.0, np=np, json=json, math=math, sys=sys, Path=Path, print=lambda *a, **k: self.printed.append(" ".join(map(str, a))))
        exec(N.CONSTANTS, ns); exec(NAVDEFS, ns)
        self.log, self.released, self.exited = [], [], None
        ns["args"] = SimpleNamespace(**args, route=str(route_path), fixed=None)
        ns["WPS"], ns["ATTRS"] = ns["load_route"](route_path)
        ns.update(db=db, phases=ph, thr=ph.Throttle(), fuel_x=fuel, fighters=fighters, escape=escape, missile_watch=MW,
                  cmkeys=cmkeys, _gbyte=gbyte, read_sensitivity=fs.read_sensitivity, read_flight=fs.read_flight,
                  read_waypoints=fs.read_waypoints, read_aircraft=fs.read_aircraft, read_missile_world=fs.read_missile_world,
                  phase_event=self.log.append, _release=lambda db: self.released.append(ns["now"]), close_ui=lambda: None,
                  tko=None, attack=None, gun=None, landing=None, mwatch=None, msnap=None, missile_full=False, m_key="",
                  missile_saved_kt=None, fighter_saved_kt=None, esc_full=False, esc_saved_kt=None, strafe_q=list(args.get("_strafe", [])),
                  attacks_done={r for r in args.get("done", "").split(",") if r}, _attack_logged=0, ATTACKS=None,
                  phase="takeoff", start_pos=None, runway_hdg=None, leg=1, phase_log=self.log)
        if args["auto"]:
            ns["ATTACKS"] = ns["build_attacks"]()
            for role, rec in (records or {}).items():
                if role in ns["ATTACKS"] and ns["ATTACKS"][role]["record"] is None: ns["ATTACKS"][role]["record"] = rec
        self.ns, self.first = ns, True

    def tick(self, s, now):
        ns = self.ns
        ns.update(s=s, now=now)
        if self.first:
            self.first = False; ns["s0"] = s
            exec(JOIN, ns)
            if ns["phase"] == "route" and ns["args"].auto:
                ns["thr"].target = float(json.load(open(ns["args"].route)).get("profile", {}).get("cruise_kt") or 190)
        n = len(self.log)
        exec(N.ROUTE, ns)
        try: exec(N.MERGE, ns)
        except SystemExit as e: self.exited = self.printed[-1].split("\n")[0] if self.printed else str(e)
        return [m.split(" ", 1)[1] for m in self.log[n:]]


class NewNav:
    def __init__(self, reads, route_path, args, fuel, fighter, escape, cmkeys, gbyte, records=None, strafe=()):
        prof = json.loads(Path(route_path).read_text()).get("profile", {})
        route = Route.from_route_json(route_path)
        self.clock = ManualClock(0.0)
        self.ctx = FlightContext(self.clock, route=route)
        self.speed = SpeedHold()
        self.emu = KeyLog(); self.ctl = Controls(self.emu, self.speed)
        self.modes = Modes(hdg=args["hdg"], alt=args["alt"], auto=args["auto"], strafe=bool(strafe))
        self.fly = FlyLeg(); self.fly._points(self.ctx)
        attacks = None
        if args["auto"]:
            attacks = self.fly.attacks(json.loads((Path(route_path).parent / "mission_mem.json").read_text()))
            for role, rec in (records or {}).items():
                if role in attacks and attacks[role]["record"] is None: attacks[role]["record"] = rec
        self.arb = Arbiter(self.fly, self.modes, speed=self.speed, reads=reads, cruise_kt=float(prof.get("cruise_kt") or 190),
                           cruise_ft=float(prof.get("cruise_ft") or 450), attacks=attacks, attack_now=args.get("attack_now"),
                           land_now=args.get("land_now", False), done=[r for r in args.get("done", "").split(",") if r],
                           strafe=strafe, fuel=fuel, fighter=fighter, missile_mode=args.get("missile_watch"),
                           missile_factory=lambda mode: MissileWatch(mode, gbyte), cmkeys=cmkeys, escape=escape)

    def tick(self, state, now):
        self.clock.set(now)
        dec = self.arb.step(state, self.ctx)
        self.ctl.apply(dec.command, state, self.ctx)
        for k in dec.key_ups: self.ctl.up(k)
        return dec, self.emu.take()


class Cmp:
    """Per-tick comparison. Known difference (overlapping reactions only, the arbiter docstring): while two or more of
    FighterWatch / MissileWatch / TerrainEscape act in the same episode, nav's three saved speeds fought (a survival
    re-pressed full power every tick under a fighter evasion; an evasion speed overrode an escape climb's full power
    the next tick; a fighter's restore under a survival made 165 stick), so the speed target and the throttle presses
    are left out of the comparison from the first overlapping tick until no reaction acts; then nav's speed state is
    re-synchronised to the new one (overlap_episodes keeps (target before, target after) of each, new side)."""
    THR = (0x0C, 0x0D)

    def __init__(self):
        self.n = self.equal = self.order_only = self.overlap_ticks = 0
        self.first, self.phases, self.behaviors, self.diffs = "", [], set(), []
        self.overlap, self.before, self.overlap_episodes = False, None, []

    def tick(self, old, new, dec, okeys, nkeys, oev):
        ns = old.ns
        active = [k for k in ("fighter", "missile", "escape") if ns["ov"].get(k)]
        if not active and not new.arb.sp.active: self.before = new.speed.target
        if len(active) >= 2 and not self.overlap: self.overlap = True; self.start = self.before
        o = (new_ov_dict(ns["ov"]), round(ns["want"], 9), ns["phase"], ns["leg"], ns["thr"].target, oev, ns["m_key"], bool(old.released))
        n = (new_ov(dec.overrides), round(dec.want, 9), dec.phase, new.ctx.leg, new.speed.target, dec.events, dec.m_key,
             dec.handover or dec.exit is not None)                 # the runner releases the keys on both
        ok_, nk_ = okeys, nkeys
        if self.overlap:
            self.overlap_ticks += 1
            o, n = o[:4] + o[5:], n[:4] + n[5:]
            ok_ = [k for k in okeys if not (k[0] == "scan" and k[1] in self.THR)]
            nk_ = [k for k in nkeys if not (k[0] == "scan" and k[1] in self.THR)]
        k_ok = ok_ == nk_
        if not k_ok and sorted(map(repr, ok_)) == sorted(map(repr, nk_)): k_ok = True; self.order_only += 1
        ok = o == n and k_ok
        self.n += 1; self.equal += ok
        if not ok: self.diffs.append(f"tick {self.n}: old {o} keys {okeys}\n   new {n} keys {nkeys}")
        if not ok and not self.first: self.first = self.diffs[-1]
        if self.overlap and not active and not new.arb.sp.active:     # episode over: resync nav's speed state
            self.overlap = False; self.overlap_episodes.append((self.start, new.speed.target))
            ns["thr"].target = new.speed.target
            ns.update(fighter_saved_kt=None, missile_saved_kt=None, esc_saved_kt=None, esc_full=False)
        if not self.phases or self.phases[-1] != dec.phase: self.phases.append(dec.phase)
        self.behaviors.add(dec.behavior)
        old.released.clear()


def new_ov_dict(ov):
    return {k: v for k, v in ov.items() if k not in NEW_ONLY}


def _records(R):
    out = {}
    for t, e in R.events:
        if " attack start (" in e and "record " in e:
            role = e.split(" ", 1)[0]
            try: out[role] = int(e.rsplit("record ", 1)[1].rstrip(")"))
            except ValueError: pass
    return out


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id,margin", [("1792609731", None), ("1792546698", None), ("1792715432", None),
                                           ("1792609731", 150)])
def test_arbiter_matches_nav_merge_on_replays(run_id, margin, egame):
    """margin: TerrainEscape.MARGIN_FT on both sides (the 2026-10-21 flight flew with 150: its escapes happen)."""
    R = H.Run.load(run_id)
    gbyte, ttype = H.theater_grid(R.mission.get("theater", "CE"))
    probe = H.make_terrain_ahead(R.plan_dir / "terrain.npz")
    route_path = R.plan_dir / "route.json"
    args = dict(auto=True, hdg=True, alt=450.0, strafe=None, attack_now=None, land_now=False, done="", missile_watch=None)
    recs = _records(R)
    saved = MW.MissileWatch.BUDGET_S, MissileWatch.BUDGET_S
    saved_m = ph.TerrainEscape.MARGIN_FT, TerrainEscape.MARGIN_FT
    FlyLeg.takeoff_done = property(lambda self: False, lambda self, v: None)   # nav turned 6 km on; the new stack (2026-10-27) turns when the takeoff is done
    MW.MissileWatch.BUDGET_S = MissileWatch.BUDGET_S = 1e9
    if margin: ph.TerrainEscape.MARGIN_FT = TerrainEscape.MARGIN_FT = margin
    db = RecSnapDB(R)
    rr = ReplayReader(R.dir)
    cmp = Cmp()
    try:
        with OldPhases(synthetic=False) as op:
            old = OldNav(db, route_path, args, ph.FuelTransfer(), ph.FighterWatch(ttype, gbyte),
                         ph.TerrainEscape(lambda p_, h_, a_, mk_: probe(p_, h_, a_, max_km=mk_, margin_ft=ph.TerrainEscape.MARGIN_FT)),
                         MW.CmKeys(), gbyte, recs)
            new = NewNav(Reads(rr), route_path, args, FuelTransfer(), FighterWatch(ttype, gbyte, descend_first=False, legacy=True),
                         TerrainEscape(lambda p_, h_, a_, mk_: probe(p_, h_, a_, max_km=mk_, margin_ft=TerrainEscape.MARGIN_FT)),
                         CmKeys(), gbyte, recs)
            for i, (t, raw) in enumerate(R.snaps()):
                db.load(raw, t); rr.seek_index(i)
                try: s = fs.read_state(db, tries=1)
                except RuntimeError: continue
                if not s["x"]: continue
                st = rr.snapshot()
                st = dataclasses.replace(st, time_mult=rr.time_mult())
                op.clock.t = t
                oev = old.tick(s, t)
                okeys = db.take()
                dec, nkeys = new.tick(st, t)
                cmp.tick(old, new, dec, okeys, nkeys, oev)
    finally:
        MW.MissileWatch.BUDGET_S, MissileWatch.BUDGET_S = saved
        ph.TerrainEscape.MARGIN_FT, TerrainEscape.MARGIN_FT = saved_m
        FlyLeg.takeoff_done = False
    if margin: assert "terrain_escape" in cmp.behaviors
    assert cmp.equal == cmp.n, "\n".join(cmp.diffs)
    assert cmp.n > 200
    print(f"arbiter {run_id}: {cmp.n} ticks equal ({cmp.order_only} order-only, {cmp.overlap_ticks} overlap ticks), phases {cmp.phases}, behaviors {sorted(cmp.behaviors)}")


# ------------------------------------------------------------------------------------------------------ synthetic
FIGHTER = {"want": 90.0, "rate_cap": 3.0, "fighter": "evade", "speed_kt": 165, "alt": 250}
MISSILE = {"want": 200.0, "rate_cap": 1.2, "bank_max": 30.0, "speed_kt": 165, "alt_min": 250, "missile": "evade",
           "abort_attack": True, "survival": False}
SURVIVAL = {**MISSILE, "missile": "survival", "survival": True, "rate_cap": 2.0}
ESCAPE = {"want": 300.0, "rate_cap": 3.0, "escape": "climb", "alt": 800, "vs_max": 60.0, "throttle_max": True}
ESCAPE_TURN = {"want": 280.0, "rate_cap": 3.0, "escape": "turn"}


def script(i):
    """(fighter ov, missile ov, escape ov) of tick i: every overlap order of the three reactions (3 ticks a step)."""
    i = i // 3
    f = FIGHTER if 20 <= i < 60 or 100 <= i < 112 or 150 <= i < 175 else {}
    m = {}
    if 40 <= i < 80 or 120 <= i < 135 or 160 <= i < 170: m = dict(MISSILE)
    if 50 <= i < 56 or 125 <= i < 130 or 162 <= i < 166: m = dict(SURVIVAL)
    if i in (45, 126): m["cm"] = 1
    if i == 70: m["jam_on"] = "ecm"
    e = ESCAPE if 70 <= i < 90 or 104 <= i < 108 or 140 <= i < 146 else ESCAPE_TURN if 90 <= i < 95 else {}
    return f, m, e


class OldFighter:
    EVADE_KT = 165
    def __init__(self): self.state, self.nearest, self.i = "off", "", 0
    def step(self, s, p, now, want, ac, opp):
        ov = script(self.i)[0]; self.state = "evade" if ov else "off"
        return dict(ov), ([f"fighter {self.i}"] if ov and not script(self.i - 1)[0] else [])


class NewFighter(OldFighter):
    def step(self, state, ctx):
        from f19.core.types import Command
        ov, ev = OldFighter.step(self, None, None, None, None, None, None)
        return dataclasses.replace(Command.from_overrides(ov), events=tuple(ev))


class OldMissile:
    def __init__(self, mode): self.mode, self.state, self.worst, self.action, self.i, self.blocked = mode, "off", "", "", 0, []
    def block(self, k): self.blocked.append(k)
    def step(self, s, p, now, want, snap):
        ov = dict(script(self.i)[1]); self.state = ov.get("missile", "watch")
        return ov, ([f"missile {self.i}"] if ov and not script(self.i - 1)[1] else [])


class NewMissile(OldMissile):
    def step(self, state, ctx):
        from f19.core.types import Command
        ov, ev = OldMissile.step(self, None, None, None, None, None)
        return dataclasses.replace(Command.from_overrides(ov), events=tuple(ev))


class OldEscape:
    def __init__(self): self.i = 0
    def step(self, s, p, now, want, ts=1.0):
        ov = dict(script(self.i)[2]); return ov, ("escape on" if ov and not script(self.i - 1)[2] else "")


class NewEscape(OldEscape):
    def step(self, state, ctx):
        from f19.core.types import Command
        ov, ev = OldEscape.step(self, None, None, None, None)
        return dataclasses.replace(Command.from_overrides(ov), events=(ev,) if ev else ())


class WReads:
    """Reads on a synthetic world (pilot_equiv.world): everything is in the GameState already."""
    def __init__(self): self.w = None
    def enrich(self, state, **kw): return state
    def target(self, rec): return self.w["targets"][rec]
    def missile_world(self, full=True): return self.w["msnap"]


def _route(tmp_path):
    pts = [dict(x=10000.0, y=20000.0, label="start"), dict(x=10000.0, y=17000.0, label="Run-in", kind="runin", role="primary"),
           dict(x=10000.0, y=15000.0, label="PRIMARY overfly", kind="overfly", role="primary"),
           dict(x=10000.0, y=9000.0, label=""), dict(x=10000.0, y=7280.0, label="Approach fix", kind="approach"),
           dict(x=10000.0, y=6384.0, label="Final", kind="final"), dict(x=10000.0, y=6000.0, label="Landing", kind="landing")]
    d = tmp_path / "plan"; d.mkdir()
    (d / "route.json").write_text(json.dumps(dict(points=pts, profile=dict(cruise_kt=190, cruise_ft=450))))
    (d / "mission_mem.json").write_text(json.dumps(dict(theater="CE", targets=[dict(role="primary", x=10000, y=15000, record=7)])))
    return d / "route.json"


def _synthetic(tmp_path, args, strafe=(), n=400, dy=10.0, fuel_at=None):
    import pilot_equiv as PE
    route_path = _route(tmp_path)
    w = PE.world(x=10000.0, y=19800.0, alt=500, spd=200, hdg=0.0, flags=0x01, throttle=60, fuel=3000,
                 loadout=[(5, 2), (16, 1), (17, 1), (0, 4)], station=0, targets={3: (11000, 15000, True), 4: (11000, 14000, False), 7: (10000, 15000, False)},
                 gun=(5, 4, 20))
    w["sens"] = 1
    w["msnap"] = dict(missiles=[], world=dict(opp=3, flags=0), stores=[3, 3, 3])
    args = dict(dict(auto=True, hdg=True, alt=450.0, strafe=None, attack_now=None, land_now=False, done="", missile_watch=None), **args)
    if strafe: args["_strafe"], args["strafe"] = list(strafe), ",".join(map(str, strafe))
    db = PE.OldDB(); reads = WReads()
    of, om, oe = OldFighter(), [], OldEscape()
    nf, ne = NewFighter(), NewEscape()
    def mfac(mode): m = NewMissile(mode); nm.append(m); return m
    nm = []
    cmp = Cmp()
    with OldPhases(synthetic=True) as op:
        old = OldNav(db, route_path, args, ph.FuelTransfer(), of, oe, MW.CmKeys(), None)
        ns = old.ns
        ns.update(read_sensitivity=lambda db: db.w["sens"], read_flight=lambda db: db.w["f"], read_aircraft=lambda db: [],
                  read_waypoints=lambda db: ([(0, 0)] * 4, 0),
                  read_missile_world=lambda db, full=True: db.w["msnap"], time_mult=lambda db: db.w["ts"],
                  missile_watch=SimpleNamespace(MissileWatch=lambda mode, g: (om.append(OldMissile(mode)), om[-1])[1], KEYS=MW.KEYS))
        new = NewNav(reads, route_path, args, FuelTransfer(), nf, ne, CmKeys(), None, strafe=strafe)
        new.arb.missile_factory = mfac
        for i in range(n):
            t = 1_800_000_000.0 + i * 0.5
            for o in [of, oe, nf, ne] + om + nm: o.i = i
            if fuel_at is not None and i == fuel_at: w["f"]["fuel"] = 450
            w["msnap"]["missiles"] = [1] if script(i)[1] else []
            w["s"]["spd"] = 160 if 210 <= i < 270 else 200
            db.w = reads.w = w; op.clock.t = t
            s = dict(w["s"])
            oev = old.tick(s, t)
            okeys = db.take()
            st = PE.gamestate(w, sensitivity=w["sens"])
            dec, nkeys = new.tick(st, t)
            cmp.tick(old, new, dec, okeys, nkeys, oev)
            assert (old.exited is None) == (dec.exit is None) and (old.exited or "") == (dec.exit or ""), (old.exited, dec.exit, cmp.diffs)
            if dec.exit: break
            PE.apply_keys(w, okeys)
            if ("tap", "enter", 0.2) in okeys and w["f"]["station"] == 2 and w["f"]["loadout"][2] == [17, 1]:
                w["f"]["fuel"] += 1900; w["f"]["loadout"][2] = [17, 0]
            for k in okeys:
                if k[:2] == ("tap", "1"): w["msnap"]["stores"] = [w["msnap"]["stores"][0] - 1] + w["msnap"]["stores"][1:]
                if k[:2] == ("tap", "4"): w["msnap"]["world"]["flags"] ^= 0x10
            w["s"]["y"] -= int(dy * 32)
    return cmp, new


def test_arbiter_matches_nav_merge_synthetic_auto(tmp_path, egame):
    cmp, new = _synthetic(tmp_path, {}, n=1500, fuel_at=300)
    assert cmp.equal == cmp.n, cmp.first
    assert {"fighter", "missile", "terrain_escape", "attack", "landing"} <= cmp.behaviors, cmp.behaviors
    assert "attack" in cmp.phases and cmp.phases[-1] in ("handover", "landed"), cmp.phases
    print(f"arbiter synthetic auto: {cmp.n} ticks equal ({cmp.overlap_ticks} overlap ticks), phases {cmp.phases}")


def test_arbiter_matches_nav_merge_synthetic_strafe(tmp_path, egame):
    cmp, new = _synthetic(tmp_path, dict(auto=False, alt=1500.0), strafe=[3, 4], n=50)
    assert cmp.equal == cmp.n, "\n".join(cmp.diffs)
    assert "gun_attack" in cmp.behaviors and cmp.n == 2


def test_arbiter_matches_nav_merge_synthetic_handover(tmp_path, egame):
    cmp, new = _synthetic(tmp_path, dict(auto=False), n=1500)
    assert cmp.equal == cmp.n, "\n".join(cmp.diffs)
    assert cmp.phases[-1] == "handover" and not new.modes.hdg and new.modes.alt is None
