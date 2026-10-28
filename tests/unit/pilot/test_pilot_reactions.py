"""WP-7b equivalence, reactions: OLD scripts/phases.py FuelTransfer / FighterWatch / TerrainEscape and
scripts/missile_watch.py MissileWatch / CmKeys vs NEW f19.pilot.reactions on synthetic sequences and on the replay
fixtures (the same inputs the WP-1 harness feeds). Each tick: the same override dict (Command.to_overrides), the same
event texts, the same keys (FuelTransfer / CmKeys, through the FakeControls adapter) and the same internal state."""
import copy
import math

import pytest

from pilot_equiv import (FakeControls, KeyLog, OldDB, apply_keys, flat_state, gamestate, product, run_phase, run_reaction,
                         world)
import harness as H  # noqa: E402  (pilot_equiv put tests/replay on sys.path)
from f19.core.clock import ManualClock
from f19.core.types import FlightContext, GameState
from f19.pilot.reactions import CmKeys, FighterWatch, FuelTransfer, MissileWatch, TerrainEscape

T0, DT = 1_800_000_000.0, 0.25
ph = product.phases()
need_fix = pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")


# ----------------------------------------------------------------------------------------------------- fuel transfer
def fuel_world(enter_works=True, space_works=True, fuel=640):
    w = world(alt=500, spd=190, fuel=fuel, station=0, loadout=[(16, 1), (17, 1), (5, 2), (17, 1)])
    st = dict(enter=enter_works)
    def after(w, keys, t, ov):
        f = w["f"]; f["fuel"] = max(0, f["fuel"] - 3)
        for k in keys:
            if k[:2] == ("tap", "space") and space_works: f["station"] = (f["station"] + 1) % 4
            if k[:2] == ("tap", "enter") and st["enter"] is not False:
                wpn, n = f["loadout"][f["station"]]
                if wpn == 17 and n == 1:
                    if st["enter"] == "late": st["enter"] = True; continue        # the first Enter is lost
                    f["loadout"][f["station"]] = (17, 0); f["fuel"] += 1900
    if enter_works == "late": st["enter"] = "late"
    return w, after


def fuel_step(o, db, w, t):
    ev = o.step(db, w["f"], t)
    if ev: o.log.append(ev)
    return {}


@pytest.mark.parametrize("case", ["two_tanks", "retry", "enter_fails", "space_fails", "above_low"])
def test_fuel_transfer_equivalent(case):
    kw = dict(two_tanks={}, retry=dict(enter_works="late"), enter_fails=dict(enter_works=False),
              space_fails=dict(space_works=False), above_low=dict(fuel=50000))[case]
    w, after = fuel_world(**kw)
    old, new = ph.FuelTransfer(), FuelTransfer()
    rep = run_phase(old, new, ((T0 + i * DT, w) for i in range(4000)), fuel_step, after=after,
                    extra=lambda b: (b.state, b.target, b.tries, b.space, tuple(sorted(b.failed)), b.busy, round(b.next_ok, 6)))
    assert rep.all_equal, rep.first_diff
    logs = " | ".join(new.log)
    expect = dict(two_tanks=["transferred from station 1", "transferred from station 3"], retry=["(try 2)"],
                  enter_fails=["FAILED after 4 Enter presses"], space_fails=["Space never reached station 1"], above_low=[])[case]
    assert all(x in logs for x in expect), logs
    if case == "above_low": assert not new.log and rep.n_keys == 0
    print(f"fuel {case}: {rep.ticks} ticks, {rep.n_keys} keys, {new.log}")


# ----------------------------------------------------------------------------------------------------- CmKeys
def test_cmkeys_equivalent():
    """request / jam / step on a synthetic world: a flare that lands after 1 tap, a chaff that needs a retry, a decoy
    never confirmed (given up after 3 taps), the radar jammer on and off, an empty store, a request while busy."""
    MW = product.missile_watch()
    old, new = MW.CmKeys(), CmKeys()
    db = OldDB(); ctx = FlightContext(ManualClock(0.0)); ctrl = FakeControls(ctx)
    snap = dict(stores=[3, 3, 2], world=dict(flags=0))
    st0 = gamestate(world())
    script = {0: ("request", 1), 4: ("request", 2), 5: ("request", 3), 30: ("request", 3), 70: ("jam", "ecm", True),
              71: ("request", 1), 90: ("jam", "ecm", False), 110: ("request", 2)}
    land = {2: ("stores", 0), 20: ("stores", 1), 72: ("flags", 0x10), 92: ("flags", 0)}
    n = eq = 0
    for i in range(140):
        t = T0 + i * DT
        if i in land:
            k, v = land[i]
            if k == "stores": snap["stores"][v] -= 1
            else: snap["world"]["flags"] = v
        if i == 100: snap["stores"][1] = 0
        outs_old, outs_new = [], []
        if i in script:
            op = script[i]
            if op[0] == "request":
                outs_old.append(old.request(db, op[1], snap, t)); a, e = new.request(op[1], snap, t)
            else:
                outs_old.append(old.jam(db, op[1], op[2], snap, t)); a, e = new.jam(op[1], op[2], snap, t)
            outs_new.append(e); new_keys = ctrl.apply(_cmd(a), st0, t)
        else: new_keys = []
        outs_old.append(old.step(db, snap, t)); a, e = new.step(snap, t); outs_new.append(e)
        new_keys += ctrl.apply(_cmd(a), st0, t)
        same = outs_old == outs_new and db.take() == new_keys and old.pending == new.pending and old.failed == new.failed
        n += 1; eq += same
        assert same, (i, outs_old, outs_new)
        old.failed = new.failed = None
    assert eq == n


def _cmd(actions):
    from f19.core.types import Command
    return Command(actions=tuple(actions))


# ----------------------------------------------------------------------------------------------------- terrain escape
def ridge_probe(ridge_y, top):
    """A wall of terrain `top` ft high along y = ridge_y for x < 22000 (units); the probe as nav's terrain_ahead."""
    def probe(p, hdg, alt, max_km):
        hr = math.radians(hdg)
        for i in range(1, int(max_km / 0.25) + 1):
            d = i * 0.25; x, y = p[0] + math.sin(hr) * d * 64, p[1] - math.cos(hr) * d * 64
            if y <= ridge_y and x < 22000 and top >= alt - ph.TerrainEscape.MARGIN_FT: return d, top
        return None
    return probe


def _ev1(r):
    ov, ev = r; return ov, [ev] if ev else []          # TerrainEscape returns one event string


@pytest.mark.parametrize("top,alt", [(400, 380), (420, 300), (330, 360)])
def test_terrain_escape_synthetic(top, alt):
    """Flying north at a ridge: turn (the clear heading nearest the route), climb when it isn't clearing in time,
    clear after CLEAR_S; old vs new on the same inputs every tick."""
    probe = ridge_probe(20000, top)
    old, new = ph.TerrainEscape(probe), TerrainEscape(probe)
    x, y, hdg, inputs = 20000.0, 20400.0, 0.0, []
    for i in range(400):
        t = T0 + i * 0.25
        inputs.append((t, flat_state(alt, 190, hdg, x, y, time_mult=1.0), 350.0, (dict(alt=alt, spd=190, hdg=hdg), (x, y))))
        v = 0.6; x += v * math.sin(math.radians(hdg)) * 64 * 0.25; y -= v * math.cos(math.radians(hdg)) * 64 * 0.25
        hdg = (hdg + (6 if 20 < i < 80 else 0)) % 360
    rep = run_reaction(lambda t, it: _ev1(old.step(it[0], it[1], t, 350.0, 1.0)), new, inputs)
    assert rep.all_equal, rep.first_diff
    assert "turn" in rep.states or "climb" in rep.states, rep.states
    print(f"terrain escape top {top} alt {alt}: {rep.ticks} ticks, states {rep.states}")


@need_fix
@pytest.mark.parametrize("margin", [None, 150])
def test_terrain_escape_replay(margin):
    """Every nav-CSV row of 1792609731 where nav called TerrainEscape (as tests/replay/harness.replay_terrain_escape)."""
    R = H.Run.load("1792609731")
    probe = H.make_terrain_ahead(R.plan_dir / "terrain.npz")
    saved = ph.TerrainEscape.MARGIN_FT, TerrainEscape.MARGIN_FT
    if margin: ph.TerrainEscape.MARGIN_FT = TerrainEscape.MARGIN_FT = margin
    try:
        mk = lambda cls: (lambda p_, h_, a_, mk_: probe(p_, h_, a_, max_km=mk_, margin_ft=cls.MARGIN_FT))
        old, new = ph.TerrainEscape(mk(ph.TerrainEscape)), TerrainEscape(mk(TerrainEscape))
        inputs, route_want = [], None
        for r in R.rows:
            if r["phase"] not in ("route", "attack", "strafe") or int(r["alt"]) <= 100: continue
            if not r.get("escape") and not r.get("fighter") and not r.get("missile"): route_want = float(r["want"])
            rw = route_want if route_want is not None else float(r["want"])
            s = dict(alt=int(r["alt"]), spd=int(r["spd"]), hdg=float(r["hdg"])); p = (float(r["x"]), float(r["y"]))
            inputs.append((float(r["t"]), flat_state(s["alt"], s["spd"], s["hdg"], *p, time_mult=float(r["tscale"] or 1)),
                           rw, (s, p, rw, float(r["tscale"] or 1))))
        rep = run_reaction(lambda t, it: _ev1(old.step(it[0], it[1], t, it[2], it[3])), new, inputs)
    finally:
        ph.TerrainEscape.MARGIN_FT, TerrainEscape.MARGIN_FT = saved
    assert rep.all_equal, rep.first_diff
    assert "turn" in rep.states, rep.states
    print(f"terrain escape replay (margin {margin or 100}): {rep.ticks} rows all equal, states {rep.states[:8]}...")


# ----------------------------------------------------------------------------------------------------- fighter watch
def _aircraft_state(s, p, aircraft, opp):
    return GameState.from_dicts(dict(alt=s["alt"], spd=s["spd"], hdg=s["hdg"], x=int(round(p[0] * 32)), y=int(round(p[1] * 32))),
                                dict(opp=opp), aircraft=aircraft)


def test_fighter_watch_synthetic():
    """A MiG-29 (doppler) closing head-on with a rising alert, a MiG-23 (pulse) abeam, a friendly F-18, then all gone:
    evade, keep, clear after CLEAR_S."""
    gbyte, ttype = H.theater_grid("CE")
    old, new = ph.FighterWatch(ttype, gbyte), FighterWatch(ttype, gbyte, descend_first=False, legacy=True)
    x, y, inputs = 15000.0, 20000.0, []
    for i in range(240):
        t = T0 + i * 0.5
        ac = [] if i > 150 else [
            dict(slot=3, x=int(x + 300), y=int(y - 2500 + 12 * i), alt=8000, type=3, name="MiG-29", flags=0, alert=min(255, i * 3)),
            dict(slot=5, x=int(x - 1800), y=int(y), alt=6000, type=1, name="MiG-23", flags=0x800 if i > 60 else 0, alert=40 if i > 30 else 0),
            dict(slot=7, x=int(x + 100), y=int(y + 100), alt=5000, type=8, name="F-18", flags=0, alert=0)]
        s = dict(alt=400, spd=190, hdg=float((i * 2) % 360)); p = (x, y)
        st = _aircraft_state(s, p, ac, 2)
        inputs.append((t, st, 10.0, (s, p, ac)))
        y -= 8
    rep = run_reaction(lambda t, it: old.step(it[0], it[1], t, 10.0, it[2], 2), new, inputs)
    assert rep.all_equal, rep.first_diff
    assert rep.states == ["off", "evade", "off"] or "evade" in rep.states, rep.states
    assert new.nearest == old.nearest
    print(f"fighter synthetic: {rep.ticks} ticks, states {rep.states}")


@need_fix
def test_fighter_watch_replay():
    """Every snapshot of 1792715432 where nav called FighterWatch (as harness.replay_fighter_watch)."""
    R = H.Run.load("1792715432"); fs = product.f19_state()
    gbyte, ttype = H.theater_grid(R.mission.get("theater", "CE"))
    old, new = ph.FighterWatch(ttype, gbyte), FighterWatch(ttype, gbyte, descend_first=False, legacy=True)
    db = R.db(); inputs, route_want, ri = [], None, 0
    for t, raw in R.snaps():
        db.load(raw, t)
        try: st = fs.read_state(db, tries=1)
        except RuntimeError: continue
        if not st["x"]: continue
        row = R.row_at(t)
        while ri < len(R.rows) and R.row_t[ri] <= t:
            r = R.rows[ri]; ri += 1
            if not r.get("fighter") and not r.get("escape") and not r.get("missile"): route_want = float(r["want"])
        if row["phase"] not in ("route", "attack", "strafe") or st["alt"] <= 100: continue
        s = dict(alt=st["alt"], spd=st["spd"], hdg=st["hdg"]); p = (st["x"] / 32, st["y"] / 32)
        flight, ac = fs.read_flight(db), fs.read_aircraft(db)
        gs = GameState.from_dicts(st, flight, aircraft=ac)
        rw = route_want if route_want is not None else float(row["want"])
        inputs.append((t, gs, rw, (s, p, rw, ac, flight["opp"])))
    rep = run_reaction(lambda t, it: old.step(it[0], it[1], t, it[2], it[3], it[4]), new, inputs)
    assert rep.all_equal, rep.first_diff
    assert "evade" in rep.states
    print(f"fighter replay: {rep.ticks} snapshots all equal, states {rep.states}")


# ----------------------------------------------------------------------------------------------------- missile watch
def _missile_snaps(run):
    G = product.missile_game()
    saved = G.S
    G.S = type("FixtureSettings", (), {"logs": H.FIX / "logs"})()
    try: return G.load_run(run)
    finally: G.S = saved


@need_fix
@pytest.mark.parametrize("run,mode", [("1792546698", "act"), ("1792715432", "act"), ("1792715432", "observe")])
def test_missile_watch_replay(run, mode):
    """MissileWatch on every snapshot (the harness's inputs: the row's want as the plan), old and new side by side.
    BUDGET_S (a real-CPU-time cap on the candidate search) is lifted on both so the search is deterministic."""
    MW = product.missile_watch()
    R = H.Run.load(run); snaps = _missile_snaps(run)
    gbyte, _ = H.theater_grid(R.mission.get("theater", "CE"))
    saved = MW.MissileWatch.BUDGET_S, MissileWatch.BUDGET_S
    MW.MissileWatch.BUDGET_S = MissileWatch.BUDGET_S = 1e9
    try:
        old, new = MW.MissileWatch(mode, gbyte), MissileWatch(mode, gbyte)
        n = eq = 0; states = []
        ctx = FlightContext(ManualClock(0.0))
        for sn in snaps:
            P = sn["player"]; row = R.row_at(sn["t"]); want = float(row["want"])
            a, b = copy.deepcopy(sn), copy.deepcopy(sn)
            ov, ev = old.step(dict(alt=P["alt"], spd=P["kt"], hdg=P["hdg"] * 360 / 65536), (P["x"], P["y"]), sn["t"], want, a)
            ctx.clock.set(sn["t"]); ctx.want = want
            gs = flat_state(P["alt"], P["kt"], P["hdg"] * 360 / 65536, P["x"], P["y"], missile_world=b)
            cmd = new.step(gs, ctx)
            same = (ov == cmd.to_overrides() and list(ev) == list(cmd.events) and
                    (old.state, old.action, old.worst, old.gt, old.ratio) == (new.state, new.action, new.worst, new.gt, new.ratio))
            n += 1; eq += same
            assert same, (sn["t"], ov, cmd.to_overrides(), ev, cmd.events, old.state, new.state)
            if not states or states[-1] != new.state: states.append(new.state)
    finally:
        MW.MissileWatch.BUDGET_S, MissileWatch.BUDGET_S = saved
    assert eq == n and len(snaps) > 100
    print(f"missile watch {run} {mode}: {n} snapshots all equal, states {states}")


def test_fighter_watch_descends_before_it_turns():
    """2026-10-27 (Elite, margin 10): a MiG-31 whose margin at 700 ft is within 10 of detection but safe at 250 ft: only the altitude changes
    (state low, no heading), and where 250 ft is not enough either the heading evasion runs as before."""
    gbyte, ttype = H.theater_grid("CE")
    found = {}
    for km in range(30, 4, -1):
        fw = FighterWatch(ttype, gbyte)
        p = (15000.0, 20000.0)
        ac = [dict(slot=3, x=int(p[0] + 64 * km * 0.6), y=int(p[1] - 64 * km * 0.8), alt=4000, type=3, name="MiG-31", flags=0, alert=80)]     # one seen pulse banked: Elite budget spent
        s = dict(alt=700, spd=190, hdg=0.0)
        ov, ev = fw._step(_aircraft_state(s, p, ac, 3), p, 100.0, 0.0, ac, 3)
        if fw.state == "low": found.setdefault("low", (km, ov, ev))
        if fw.state == "evade": found.setdefault("evade", km)
    assert "low" in found, found
    km, ov, ev = found["low"]
    assert ov == {"fighter": "low", "alt": 250} and "want" not in ov and any("descending, route kept" in e for e in ev)


def test_fighter_watch_margin_per_difficulty():
    """Elite keeps the 10 margin, Veteran and below act only at 0 (a fighter that would not see us is left alone)."""
    fw = FighterWatch()
    assert [fw.margin_for(o) for o in range(4)] == [0.0, 0.0, 0.0, 10.0]
    assert FighterWatch(legacy=True).margin_for(2) == 10.0


def _mig(opp, km, alert, alt):
    """FighterWatch, state, p and one MiG-31 (doppler, 4000 ft) km ahead-right of an F-19 flying north at 190 kt."""
    gbyte, ttype = H.theater_grid("CE")
    fw = FighterWatch(ttype, gbyte, descend_first=False); p = (15000.0, 20000.0)
    ac = [dict(slot=3, x=int(p[0] + 64 * km * 0.6), y=int(p[1] - 64 * km * 0.8), alt=4000, type=3, name="MiG-31", flags=0, alert=alert)]
    return fw, _aircraft_state(dict(alt=alt, spd=190, hdg=0.0), p, ac, opp), p, ac


def _press(opp, km, alert, alt):
    fw, st, p, ac = _mig(opp, km, alert, alt)
    return fw._press([(float(km), ac[0])], st, p, opp, fw.margin_for(opp))


def test_fighter_watch_alert_budget():
    """2026-10-27: a fighter that sees us only starts an evasion when its alert on us is running out (Veteran: two seen
    pulses banked, Elite: one) or when it is inside CLOSE_KM; a fighter that does not see us never does."""
    # Elite, 700 ft: the MiG-31 is seen from 20 km (margin -4 >= -10) to the close range
    assert _press(3, 20, 0, 700) is None and _press(3, 20, 80, 700) is not None       # the first sighting is tolerated
    assert _press(3, 11, 0, 700) is not None                                           # inside CLOSE_KM
    assert _press(3, 25, 255, 250) is None                                             # margin -20: nothing sees us
    # Veteran: needs a margin >= 0; scan the altitude at 8 km
    fw = FighterWatch()
    alt = next(a for a in range(250, 4000, 50) if fw._margin(_mig(2, 20, 0, a)[3][0], (15000.0, 20000.0), 0.0, 190, a, 2) >= 0)
    assert _press(2, 20, 0, alt) is None and _press(2, 20, 64, alt) is None           # one or two pulses: fine
    assert "alert 128" in _press(2, 20, 128, alt)                                      # two banked: act
    assert _press(2, 20, 255, 250) is None                                             # not seen at 250 ft: left alone


def test_fighter_watch_releases_when_the_route_is_safe():
    """The evasion ends CLEAR_S after the route heading is safe, not when the fighters leave the watch range: a MiG-31
    seen (Elite, 700 ft, alert 80) starts one; at 250 ft it can't see the route heading, so 15 s later it is over."""
    fw, st, p, ac = _mig(3, 20, 80, 700)
    ov, ev = fw._step(st, p, 100.0, 0.0, ac, 3)
    assert fw.state != "off" and any("alert 80" in e for e in ev)
    st2 = _aircraft_state(dict(alt=250, spd=190, hdg=0.0), p, ac, 3)
    fw._step(st2, p, 101.0, 0.0, ac, 3)
    ov, ev = fw._step(st2, p, 120.0, 0.0, ac, 3)
    assert fw.state == "off" and any("clear" in e for e in ev)
