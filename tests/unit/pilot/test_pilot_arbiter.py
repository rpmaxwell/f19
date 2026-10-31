"""Arbiter (FlyLeg + Arbiter + Controls + the reactions, f19.pilot): a CHARACTERISATION test. The same inputs that once ran
next to nav's old route + merge code (the two agreed tick by tick, except in the overlapping-reaction episodes the arbiter
docstring explains) now run through the new stack alone; a digest of every tick's decision (override dict, final want,
phase, leg, speed target, log lines, keys) is compared with the stored digest, and the coverage of each scenario
(behaviours, phases) is asserted. A change of arbitration fails here: if it was meant, re-run with PRINT_DIGESTS=1
(pytest -s), paste the new digests and say why in the commit.

Replays: all three fixtures from their first snapshot, --auto with the route the flight flew (takeoff, Maverick and photo
attacks, terrain escapes, FighterWatch evasion, MissileWatch on SA-14 / AA-9 launches). MissileWatch's real-CPU-time search
budget is lifted (deterministic search). Open loop: the recorded memory already shows what nav's real keys did.
Synthetic: a closed loop with scripted reactions that overlap in every order, plus fuel transfer during an attack, the
missile hold of an attack, a gun attack and a landing hand-over."""
import dataclasses
import hashlib
import json
import os
from pathlib import Path

import pytest

from pilot_worlds import NEW_ONLY, KeyLog, new_ov
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

# digests recorded 2026-10-30 from the current code with its defaults (the stack the 10-30 flights flew: the attack prep loop, photo
# confirmation and pull-up, the landing changes); before that the arbiter agreed with nav's old merge tick by tick
GOLD = {
'1792609731-None': 'de06d3cee03e60ba',
    '1792546698-None': '24fee4ce32c89d72',
    '1792715432-None': '742220200954daf5',
    '1792609731-150': '2153dd6842da01a8',
    'synthetic-auto': 'f82472c3209d12ab',
    'synthetic-strafe': '4372e5e4f7a62fb9',
    'synthetic-handover': '0ccc17f84ed0097e',
}


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


class Rec:
    """Per-tick record: a digest of the decision and the keys, plus the phases and behaviours seen."""
    def __init__(self): self.n, self.h, self.phases, self.behaviors = 0, hashlib.sha256(), [], set()

    def tick(self, new, dec, nkeys):
        row = (new_ov(dec.overrides), round(dec.want, 9), dec.phase, new.ctx.leg, new.speed.target, dec.events, dec.m_key,
               dec.handover or dec.exit is not None, nkeys)
        self.n += 1; self.h.update(repr(row).encode())
        if not self.phases or self.phases[-1] != dec.phase: self.phases.append(dec.phase)
        self.behaviors.add(dec.behavior)

    @property
    def digest(self): return self.h.hexdigest()[:16]


def _check(name, got):
    if os.environ.get("PRINT_DIGESTS"): print(f"DIGEST {name!r}: {got!r},")
    else: assert got == GOLD[name], f"{name}: arbitration changed (digest {got}, stored {GOLD[name]})"


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
def test_arbiter_on_replays(run_id, margin, egame, monkeypatch):
    """margin: TerrainEscape.MARGIN_FT (the 2026-10-21 flight flew with 150: its escapes happen)."""
    R = H.Run.load(run_id)
    gbyte, ttype = H.theater_grid(R.mission.get("theater", "CE"))
    probe = H.make_terrain_ahead(R.plan_dir / "terrain.npz")
    route_path = R.plan_dir / "route.json"
    args = dict(auto=True, hdg=True, alt=450.0, strafe=None, attack_now=None, land_now=False, done="", missile_watch=None)
    recs = _records(R)
    monkeypatch.setattr(FlyLeg, "takeoff_done", property(lambda self: False, lambda self, v: None), raising=False)   # nav turned 6 km on; the new stack (2026-10-27) turns when the takeoff is done
    monkeypatch.setattr(MissileWatch, "BUDGET_S", 1e9)
    if margin: monkeypatch.setattr(TerrainEscape, "MARGIN_FT", margin)
    rr = ReplayReader(R.dir)
    rec = Rec()
    new = NewNav(Reads(rr), route_path, args, FuelTransfer(), FighterWatch(ttype, gbyte, descend_first=False, legacy=True),
                 TerrainEscape(lambda p_, h_, a_, mk_: probe(p_, h_, a_, max_km=mk_, margin_ft=TerrainEscape.MARGIN_FT)),
                 CmKeys(), gbyte, recs)
    for i, (t, raw) in enumerate(R.snaps()):
        rr.seek_index(i)
        try: st = rr.snapshot()
        except RuntimeError: continue
        if not st.pos.x: continue
        st = dataclasses.replace(st, time_mult=rr.time_mult())
        dec, nkeys = new.tick(st, t)
        rec.tick(new, dec, nkeys)
    if margin: assert "terrain_escape" in rec.behaviors
    assert rec.n > 200
    _check(f"{run_id}-{margin}", rec.digest)
    print(f"arbiter {run_id}: {rec.n} ticks, phases {rec.phases}, behaviors {sorted(rec.behaviors)}")


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


class NewFighter:
    EVADE_KT = 165
    def __init__(self): self.state, self.nearest, self.i = "off", "", 0
    def step(self, state, ctx):
        from f19.core.types import Command
        ov = script(self.i)[0]; self.state = "evade" if ov else "off"
        ev = [f"fighter {self.i}"] if ov and not script(self.i - 1)[0] else []
        return dataclasses.replace(Command.from_overrides(dict(ov)), events=tuple(ev))


class NewMissile:
    def __init__(self, mode): self.mode, self.state, self.worst, self.action, self.i, self.blocked = mode, "off", "", "", 0, []
    def block(self, k): self.blocked.append(k)
    def step(self, state, ctx):
        from f19.core.types import Command
        ov = dict(script(self.i)[1]); self.state = ov.get("missile", "watch")
        ev = [f"missile {self.i}"] if ov and not script(self.i - 1)[1] else []
        return dataclasses.replace(Command.from_overrides(ov), events=tuple(ev))


class NewEscape:
    def __init__(self): self.i = 0
    def step(self, state, ctx):
        from f19.core.types import Command
        ov = dict(script(self.i)[2]); ev = "escape on" if ov and not script(self.i - 1)[2] else ""
        return dataclasses.replace(Command.from_overrides(ov), events=(ev,) if ev else ())


class WReads:
    """Reads on a synthetic world (pilot_worlds.world): everything is in the GameState already."""
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
    import pilot_worlds as PE
    route_path = _route(tmp_path)
    w = PE.world(x=10000.0, y=19800.0, alt=500, spd=200, hdg=0.0, flags=0x01, throttle=60, fuel=3000,
                 loadout=[(5, 2), (16, 1), (17, 1), (0, 4)], station=0, targets={3: (11000, 15000, True), 4: (11000, 14000, False), 7: (10000, 15000, False)},
                 gun=(5, 4, 20))
    w["sens"] = 1
    w["msnap"] = dict(missiles=[], world=dict(opp=3, flags=0), stores=[3, 3, 3])
    args = dict(dict(auto=True, hdg=True, alt=450.0, strafe=None, attack_now=None, land_now=False, done="", missile_watch=None), **args)
    reads = WReads()
    nf, ne = NewFighter(), NewEscape()
    nm = []
    def mfac(mode): m = NewMissile(mode); nm.append(m); return m
    rec = Rec()
    new = NewNav(reads, route_path, args, FuelTransfer(), nf, ne, CmKeys(), None, strafe=strafe)
    new.arb.missile_factory = mfac
    for i in range(n):
        t = 1_800_000_000.0 + i * 0.5
        for o in [nf, ne] + nm: o.i = i
        if fuel_at is not None and i == fuel_at: w["f"]["fuel"] = 450
        w["msnap"]["missiles"] = [1] if script(i)[1] else []
        w["s"]["spd"] = 160 if 210 <= i < 270 else 200
        reads.w = w
        st = PE.gamestate(w, sensitivity=w["sens"])
        dec, nkeys = new.tick(st, t)
        rec.tick(new, dec, nkeys)
        if dec.exit: break
        PE.apply_keys(w, nkeys)
        if ("tap", "enter", 0.2) in nkeys and w["f"]["station"] == 2 and w["f"]["loadout"][2] == [17, 1]:
            w["f"]["fuel"] += 1900; w["f"]["loadout"][2] = [17, 0]
        for k in nkeys:
            if k[:2] == ("tap", "1"): w["msnap"]["stores"] = [w["msnap"]["stores"][0] - 1] + w["msnap"]["stores"][1:]
            if k[:2] == ("tap", "4"): w["msnap"]["world"]["flags"] ^= 0x10
        w["s"]["y"] -= int(dy * 32)
    return rec, new


def test_arbiter_synthetic_auto(tmp_path, egame):
    rec, new = _synthetic(tmp_path, {}, n=1500, fuel_at=300)
    assert {"fighter", "missile", "terrain_escape", "attack", "landing"} <= rec.behaviors, rec.behaviors
    assert "attack" in rec.phases and rec.phases[-1] in ("handover", "landed"), rec.phases
    _check("synthetic-auto", rec.digest)
    print(f"arbiter synthetic auto: {rec.n} ticks, phases {rec.phases}")


def test_arbiter_synthetic_strafe(tmp_path, egame):
    rec, new = _synthetic(tmp_path, dict(auto=False, alt=1500.0), strafe=[3, 4], n=50)
    assert "gun_attack" in rec.behaviors and rec.n == 2
    _check("synthetic-strafe", rec.digest)


def test_arbiter_synthetic_handover(tmp_path, egame):
    rec, new = _synthetic(tmp_path, dict(auto=False), n=1500)
    assert rec.phases[-1] == "handover" and not new.modes.hdg and new.modes.alt is None
    _check("synthetic-handover", rec.digest)
