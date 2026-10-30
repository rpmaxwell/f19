"""WP-7b equivalence, phases on synthetic closed-loop worlds: OLD scripts/phases.py vs NEW f19.pilot.phases, same
world each tick (the world answers the OLD keys; with equal keys both sides see the same next tick). Every tick must
give the same key events (through the FakeControls adapter), the same override dict (Command.to_overrides minus the
new-only fields), the same speed-hold target and the same log lines. Branch coverage is asserted per scenario."""
import math

import pytest

from pilot_equiv import apply_keys, move, pos, product, projectile, run_phase, world
from f19.pilot.phases import Attack, GunAttack, Landing, Takeoff
from f19.pilot.phases._common import K, game_dist

T0, DT = 1_800_000_000.0, 0.25
ph = product.phases()


def old_step(o, db, w, t): return o.step(db, w["s"], w["f"], pos(w), t, w["ts"])


def ticks(w, n, dt=DT, t0=T0):
    for i in range(n): yield t0 + i * dt, w


# ----------------------------------------------------------------------------------------------------------- takeoff
def takeoff_world(lose_first_brakes=False, stuck_throttle_ticks=0, accel_anyway=0.0):
    w = world(alt=0, spd=0, hdg=10.0, flags=0x08, throttle=0)
    lost = {"brakes": lose_first_brakes, "thr": stuck_throttle_ticks}
    def after(w, keys, t, ov):
        s, f = w["s"], w["f"]
        lose = lost["brakes"] and any(k[1] == "0" for k in keys if k[0] == "tap")
        if lose: lost["brakes"] = False
        if any(k[0] == "scan" for k in keys): lost["rolled"] = True
        if lost["thr"] and any(k[0] == "scan" for k in keys): lost["thr"] -= 1; keys = [k for k in keys if k[0] != "scan"]
        apply_keys(w, keys, lose=lose)
        if f["throttle"] >= 100 and not f["flags"] & 0x08: s["spd"] = min(320, s["spd"] + 6)
        elif accel_anyway and lost.get("rolled") and s["spd"] < 110: s["spd"] = min(110, s["spd"] + accel_anyway)
        if s["spd"] >= 110: s["alt"] = max(0, int(s["alt"] + f["pitch"] * 2.2)); f["pitch"] -= 0.35
        elif f["pitch"] > 0: f["pitch"] = max(0.0, f["pitch"] - 0.5)
        if f["throttle"] < 100 and s["spd"] > 200: s["spd"] -= 3
        move(w, DT)
    return w, after


@pytest.mark.parametrize("cruise_ft,lose,stuck", [(450, False, 0), (300, True, 0), (450, False, 20)])
def test_takeoff_equivalent(egame, cruise_ft, lose, stuck):
    w, after = takeoff_world(lose, stuck)
    old, new = ph.Takeoff(ph.Throttle(), 190.0, cruise_ft), Takeoff(190.0, cruise_ft)
    rep = run_phase(old, new, ticks(w, 600), old_step, extra=lambda b: (b.state, b.t0), after=after)
    assert rep.all_equal, rep.first_diff
    assert {"setup", "roll", "rotate", "climb"} <= set(rep.states) and new.done, rep.states
    assert not rep.order_only
    print(f"takeoff cruise {cruise_ft}: {rep.ticks} ticks, {rep.n_keys} key events, all equal")


def test_takeoff_max_power_at_rotation_order(egame):
    """The one tick order the canonical Controls order can't give: a max-power re-press (roll state) in the tick the
    plane reaches VR is followed by the first pitch tap. Old: [Shift+=, nose-up]; Controls: pitch first. Same keys."""
    w, after = takeoff_world(stuck_throttle_ticks=99, accel_anyway=9.0)          # VR exactly 13 ticks (3.25 s > 3) after the first max
    old, new = ph.Takeoff(ph.Throttle(), 190.0, 450), Takeoff(190.0, 450)
    rep = run_phase(old, new, ticks(w, 120), old_step, extra=lambda b: (b.state, b.t0), after=after, allow_order=True)
    assert rep.all_equal, rep.first_diff
    assert len(rep.order_only) == 1 and rep.order_only[0][0] == [("scan", 0x0D, True, 0.2), ("tap", "down", 0.6)], rep.order_only


def steer(w, ov, rate=1.5):
    """Turn toward the override heading (at most `rate` deg a tick); roll follows the turn."""
    if "want" not in ov: w["f"]["roll"] = 0.0; return
    d = (ov["want"] - w["s"]["hdg"] + 180) % 360 - 180
    d = max(-rate, min(rate, d)); w["s"]["hdg"] = (w["s"]["hdg"] + d) % 360; w["f"]["roll"] = max(-20.0, min(20.0, 9 * d))


# ----------------------------------------------------------------------------------------------------------- landing
BASE, FIX = (10000.0, 10000.0), (10000.0, 10000.0 + 20 * 64)


def landing_world(xt0=30.0, drift_low=0.0, float_ft=None, south=False):
    """Approach from the fix toward the base (northbound, or southbound with south=True); drift_low = units per tick
    pushed sideways below 100 ft (go-around on the centreline); float_ft = never below this (go-around past the aim)."""
    fix = (FIX[0], 2 * BASE[1] - FIX[1]) if south else FIX
    w = world(x=fix[0] + xt0, y=fix[1], alt=700, spd=176, hdg=180.0 if south else 0.0, flags=0x01, throttle=60)
    def after(w, keys, t, ov):
        s, f = w["s"], w["f"]
        apply_keys(w, keys)
        steer(w, ov)
        cap = ov.get("alt_cap")
        if ov.get("alt") == 500: s["alt"] += 15                          # go-around climb
        elif cap is not None and s["alt"] > cap: s["alt"] = max(0, int(s["alt"] - min(s["alt"] - cap, 9)))
        if float_ft is not None and s["alt"] < float_ft and ov.get("alt") != 500: s["alt"] = float_ft
        if 0 < s["alt"] < 3: s["alt"] = 0
        tgt = old_thr[0].target
        if tgt is not None: s["spd"] += max(-1.5, min(1.5, tgt - s["spd"]))
        if s["alt"] == 0 and f["throttle"] == 0: s["spd"] = max(0, s["spd"] - (8 if f["flags"] & 0x08 else 2))
        if s["alt"] < 100 and drift_low: w["s"]["x"] += int(drift_low * 32)
        f["box"] = int(s["alt"] == 0)
        move(w, DT)
    old_thr = [None]
    return w, after, old_thr


@pytest.mark.parametrize("kind", ["land", "land_south", "go_around_xtrack", "go_around_long"])
def test_landing_equivalent(egame, kind):
    kw = dict(land={}, land_south=dict(south=True, xt0=-20.0), go_around_xtrack=dict(drift_low=3.0),
              go_around_long=dict(float_ft=60))[kind]
    w, after, old_thr = landing_world(**kw)
    fix = (FIX[0], 2 * BASE[1] - FIX[1]) if kw.get("south") else FIX
    old, new = ph.Landing(BASE, fix, ph.Throttle()), Landing(BASE, fix)
    new.IDLE_ALT, new.GA_PAST_UNITS, new.LOG_GO_AROUND = 15, new.AIM_UNITS, False   # 2026-10-30 changes off: parity with scripts/phases.py
    new.BLEED_KT, new.HIGH_FT, new.IDLE_SINK_MAX = None, None, None
    old_thr[0] = old.thr
    rep = run_phase(old, new, ticks(w, 3000), old_step, extra=lambda b: (b.state, b.rwy, b.aim, getattr(b, "idled", False)), after=after)
    assert rep.all_equal, rep.first_diff
    want = {"land": ["approach", "final", "rollout", "stopped"], "land_south": ["approach", "final", "rollout", "stopped"],
            "go_around_xtrack": ["approach", "final", "go-around"], "go_around_long": ["approach", "final", "go-around"]}[kind]
    assert rep.states == want and new.done, rep.states
    print(f"landing {kind}: {rep.ticks} ticks, {rep.n_keys} key events, all equal")


def _landing_step(alt, km_past_base, xt=0.0, spd=140, flags=0x02):
    """One Landing.step on short final, northbound: km_past_base > 0 = that far past the base record (aim is 48 units short)."""
    from pilot_equiv import gamestate
    from f19.core.types import Action, FlightContext
    from f19.core.clock import ManualClock
    w = world(x=BASE[0] + xt, y=BASE[1] - km_past_base * K, alt=alt, spd=spd, hdg=0.0, flags=flags, throttle=50)
    ld = Landing(BASE, FIX); ld.state = "final"
    cmd = ld.step(gamestate(w), FlightContext(ManualClock(0.0)))
    return ld, cmd, Action


def test_landing_idles_from_30_ft():
    ld, cmd, A = _landing_step(alt=26, km_past_base=-0.5)         # 0.5 km short of the base record, 26 ft
    assert A.THROTTLE_IDLE in cmd.actions and ld.idled and ld.state == "final"
    ld, cmd, A = _landing_step(alt=40, km_past_base=-0.5)         # above 30 ft: power stays
    assert A.THROTTLE_IDLE not in cmd.actions and not ld.idled


def test_landing_goes_around_only_past_the_new_limit():
    # the 2026-10-30 Leck float: 22 ft at the base record (48 units past the aim) used to go around; now it keeps trying
    ld, cmd, _ = _landing_step(alt=22, km_past_base=0.0)
    assert ld.state == "final" and not any("go-around" in e for e in cmd.events)
    ld, cmd, _ = _landing_step(alt=22, km_past_base=0.6)         # 80+ units past the aim (0.75 km + 0.6 km): around
    assert ld.state == "go-around" and any("landing go-around: 1.35 km past the aim point at 22 ft" in e for e in cmd.events)
    ld, cmd, _ = _landing_step(alt=15, km_past_base=0.6)         # but a plane that is nearly down (<= 20 ft) is left to land
    assert ld.state == "final"


def _approach(ld, alt, km_before_base, spd, t, clock, flags=0x02):
    """Step an existing Landing at km_before_base (km short of the base record), advancing the clock to t."""
    from pilot_equiv import gamestate
    from f19.core.types import FlightContext
    w = world(x=BASE[0], y=BASE[1] + km_before_base * K, alt=alt, spd=spd, hdg=0.0, flags=flags, throttle=50)
    clock.set(t)
    return ld.step(gamestate(w), FlightContext(clock))


def test_landing_bleeds_speed_with_idle_then_resumes_the_hold():
    from f19.core.clock import ManualClock
    from f19.core.types import Action
    c = ManualClock(0.0); ld = Landing(BASE, FIX)
    cmd = _approach(ld, 700, 15.0, 260, 0.0, c)                   # 260 kt on the approach (target 170): idle at once
    assert Action.THROTTLE_IDLE in cmd.actions and ld.bleeding and cmd.speed_kt is None
    assert any("engine idle" in e for e in cmd.events)
    cmd = _approach(ld, 690, 14.0, 230, 1.0, c)                   # still hot: nothing re-sent
    assert Action.THROTTLE_IDLE not in cmd.actions and ld.bleeding and cmd.speed_kt is None
    cmd = _approach(ld, 650, 13.0, 178, 2.0, c)                   # within RESUME_KT of the target: the hold is back
    assert not ld.bleeding and cmd.speed_kt == 170


def test_landing_goes_around_when_still_high_on_short_final():
    ld, cmd, _ = _landing_step(alt=200, km_past_base=-1.2)          # 0.45 km to the aim point at 200 ft
    assert ld.state == "go-around" and any("too high on short final: 200 ft" in e for e in cmd.events)
    ld, cmd, _ = _landing_step(alt=120, km_past_base=-1.2)          # at 120 ft it carries on
    assert ld.state == "final"
    ld, cmd, _ = _landing_step(alt=200, km_past_base=-3.0)          # 2.25 km to the aim point: still time to descend
    assert ld.state == "final"


def test_landing_keeps_power_below_30_ft_while_sinking_fast():
    from f19.core.clock import ManualClock
    from f19.core.types import Action
    c = ManualClock(0.0); ld = Landing(BASE, FIX); ld.state = "final"
    _approach(ld, 45, 0.9, 130, 0.0, c); cmd = _approach(ld, 28, 0.8, 130, 1.0, c)   # 17 ft in 1 s: too fast to cut the power
    assert Action.THROTTLE_IDLE not in cmd.actions and not ld.idled
    cmd = _approach(ld, 14, 0.6, 130, 2.0, c)                                       # but below 15 ft it idles regardless
    assert Action.THROTTLE_IDLE in cmd.actions and ld.idled
    c = ManualClock(0.0); ld = Landing(BASE, FIX); ld.state = "final"
    _approach(ld, 34, 0.9, 130, 0.0, c); cmd = _approach(ld, 28, 0.8, 130, 1.0, c)   # 6 ft/s: idle at 28 ft
    assert Action.THROTTLE_IDLE in cmd.actions and ld.idled


# ----------------------------------------------------------------------------------------------------------- attack
T_REC, TGT = 12, (20000.0, 20000.0)


def attack_world(mode, start_km=30.0, opp=2, b_seq=(32, 2, 6, T_REC), trackcam=0, station=0, loadout=None,
                 enter_works=True, kills_on_shot=2, lose_bay=0, photo_event=True, alt=450, pitch=3.0):
    """Flying north at the target from start_km south. B steps through b_seq; Enter fires the selected station's
    weapon (Maverick: count - 1 and a projectile in slot 1 that lives 6 ticks; the kills_on_shot-th one kills);
    the first lose_bay '8' taps are lost."""
    w = world(x=TGT[0], y=TGT[1] + start_km * 64, alt=alt, spd=190, hdg=0.0, pitch=pitch, flags=0x01, throttle=60,
              station=station, loadout=loadout, opp=opp, trackcam=trackcam)
    w["targets"] = {T_REC: (int(TGT[0]), int(TGT[1]), False)}
    st = dict(b=0, shots=0, lose=lose_bay, ttl=0)
    def after(w, keys, t, ov):
        f = w["f"]
        for k in keys:
            if k[0] == "tap" and k[1] == "b": f["locked"] = b_seq[st["b"] % len(b_seq)]; st["b"] += 1
            if k[0] == "tap" and k[1] == "8" and st["lose"]: st["lose"] -= 1; keys = [x for x in keys if x is not k]
            if k[0] == "tap" and k[1] == "enter" and enter_works and f["flags"] & 0x04:
                wpn, n = f["loadout"][f["station"]]
                if wpn == 5 and n > 0:
                    f["loadout"][f["station"]] = (5, n - 1); st["shots"] += 1; st["ttl"] = 6
                    w["events"].append((4, 0, f["station"], 0))
                if wpn == 16 and photo_event: w["events"].append((10, 0x80, T_REC, 0))
        apply_keys(w, keys)
        if st["ttl"]:
            st["ttl"] -= 1; w["projectiles"][1] = projectile(0x1B, st["ttl"] * 10)
            if st["ttl"] == 0 and st["shots"] == kills_on_shot:
                w["events"].append((1, 0x80, T_REC, 0)); w["targets"][T_REC] = (int(TGT[0]), int(TGT[1]), True)
        steer(w, ov, rate=3.0)
        move(w, DT)
    return w, after


def run_attack(mode, photo_km=None, **kw):
    w, after = attack_world(mode, **kw)
    old = ph.Attack("primary", mode, TGT, T_REC, 14.0 if mode == "maverick" else None, photo_km, ph.Throttle())
    new = Attack("primary", mode, TGT, T_REC, 14.0 if mode == "maverick" else None, photo_km)
    rep = run_phase(old, new, ticks(w, 4000), old_step, after=after,
                    extra=lambda b: (b.state, b.b_presses, b.space_presses, b.f2_presses, b.bay_presses, b.flaps_set,
                                     b.shots, b.cam_on, tuple(b.locks), b.slot, b.events0, round(b.next_ok, 6)))
    return rep, old, new


@pytest.mark.parametrize("case", ["maverick_reshoot", "maverick_no_fire", "maverick_too_close", "photo_elite",
                                  "photo_veteran_no_bay", "no_lock_abandon", "no_weapon", "overfly"])
def test_attack_equivalent(egame, case):
    kw, mode = {}, "maverick"
    if case == "maverick_no_fire": kw = dict(enter_works=False)
    if case == "maverick_too_close": kw = dict(start_km=5.5, b_seq=(T_REC,))
    if case == "photo_elite": mode, kw = "photo", dict(opp=3, lose_bay=1, alt=250, pitch=3.0, photo_km=2.0)
    if case == "photo_veteran_no_bay": mode, kw = "photo", dict(lose_bay=10 ** 6, alt=250, photo_km=1.0)
    if case == "no_lock_abandon": kw = dict(b_seq=(32, 2), start_km=8.0)
    if case == "no_weapon": kw = dict(loadout=[(17, 1), (0, 4), (0, 4), (0, 4)])
    if case == "overfly": mode = "overfly"
    rep, old, new = run_attack(mode, **kw)
    assert rep.all_equal, rep.first_diff
    assert new.done, (rep.states, new.log)
    want = {"maverick_reshoot": ["prep", "track", "fire", "close", "result", "fire", "close", "result", "away"],
            "maverick_no_fire": None,
            "maverick_too_close": ["prep", "away"],
            "photo_elite": ["prep", "track", "photo", "close", "away"],
            "photo_veteran_no_bay": ["prep", "track", "photo", "close", "away"],
            "no_lock_abandon": ["prep"], "no_weapon": ["prep"], "overfly": ["prep", "past"]}[case]
    if want is None:
        assert {"fire", "close", "result", "away"} <= set(rep.states) and any("did NOT fire" in x for x in new.log), new.log
    else: assert rep.states == want, (rep.states, new.log)
    print(f"attack {case}: {rep.ticks} ticks, {rep.n_keys} key events, log {new.log}")


# ----------------------------------------------------------------------------------------------------------- gun
G_REC, G_T = 7, (30000.0, 30000.0)


def gun_world(start_km=10.0, rounds=650, kill_pass=2, repeat=True, alt=1500, low_first=False, sink=1.0):
    """Run-in at 1500 ft, 250 kt toward a radar site. Pitch follows the taps (6 deg/s held, nose-up held in the pull);
    the dive sinks at the pitch; bullets every tick while the trigger is held (repeat=False: only on a fresh press);
    the site dies at the end of pass kill_pass; low_first: no climb until the first extension (not set up at the push)."""
    w = world(x=G_T[0], y=G_T[1] + start_km * 64, alt=1100 if low_first else alt, spd=250, hdg=0.0, pitch=0.0, flags=0x01,
              throttle=70, gun=(rounds, 4, 20))
    w["targets"] = {G_REC: (int(G_T[0]), int(G_T[1]), False)}
    st = dict(held=False, passes=0, prev="")
    def after(w, keys, t, ov):
        s, f = w["s"], w["f"]
        fresh = False
        for k in keys:
            if k == ("down", "bs"): st["held"] = True; fresh = True
            if k == ("up", "bs"): st["held"] = False
            if k == ("down", "down"): st["pull"] = True
            if k == ("up", "down"): st["pull"] = False
        apply_keys(w, keys)
        if st.get("pull"): f["pitch"] += 6.0 * DT * 2
        rounds, fs, ns = w["gun"]
        if st["held"] and (repeat or fresh): w["gun"] = (max(0, rounds - 10), fs, ns)
        if old_ref[0].state == "check" and st["prev"] != "check": st["passes"] += 1
        if old_ref[0].state == "check" and st["passes"] >= kill_pass:
            w["targets"][G_REC] = (int(G_T[0]), int(G_T[1]), True)
        st["prev"] = old_ref[0].state
        steer(w, ov, rate=2.0)
        if old_ref[0].state not in ("dive", "pull"):
            f["pitch"] += max(-1.0, min(1.0, -f["pitch"]))
            if old_ref[0].state != "runin": st["climb"] = True
            tgt = ov.get("alt", 1500)
            if st.get("climb") or not low_first: s["alt"] += int(max(-20, min(20, tgt - s["alt"])))
        else:
            s["alt"] = max(0, int(s["alt"] + sink * 250 * 0.5144 * 3.281 * 2.5 * DT * math.sin(math.radians(f["pitch"]))))
        move(w, DT)
    old_ref = [None]
    return w, after, old_ref


@pytest.mark.parametrize("case", ["kill_on_pass2", "three_passes", "no_repeat", "not_set_up", "gun_empty"])
def test_gun_attack_equivalent(egame, case):
    kw = dict(kill_on_pass2={}, three_passes=dict(kill_pass=99), no_repeat=dict(repeat=False, kill_pass=1, sink=0.25),
              not_set_up=dict(low_first=True, kill_pass=1), gun_empty=dict(rounds=5))[case]
    w, after, old_ref = gun_world(**kw)
    old, new = ph.GunAttack(G_REC, G_T, ph.Throttle()), GunAttack(G_REC, G_T)
    old_ref[0] = old
    rep = run_phase(old, new, ticks(w, 6000), old_step, after=after,
                    extra=lambda b: (b.state, b.passes, b.k, b.firing, b.pulse, b.bottom, b.fired, round(b.next_ok, 6),
                                     round(b.cycle, 9)))
    assert rep.all_equal, rep.first_diff
    assert new.done, (rep.states, new.log)
    seen = set(rep.states)
    need = {"kill_on_pass2": {"runin", "dive", "pull", "check", "extend", "turn"}, "three_passes": {"dive", "pull", "check", "turn"},
            "no_repeat": {"dive", "pull", "check"}, "not_set_up": {"runin", "extend", "turn", "dive", "check"}, "gun_empty": {"runin"}}[case]
    assert need <= seen, (rep.states, new.log)
    kinds = {k if k[0] in ("down", "up") else (k[0], k[1]) for k in rep.keys}
    if case in ("kill_on_pass2", "three_passes", "no_repeat", "not_set_up"):
        assert {("down", "bs"), ("up", "bs"), ("down", "down"), ("up", "down")} <= kinds, kinds
        assert ("tap", "down") in kinds or ("tap", "up") in kinds, kinds        # band taps in the dive
    if case == "no_repeat":                                                       # pulse: re-press while held
        assert any(a == ("up", "bs") and b == ("down", "bs") for a, b in zip(rep.keys, rep.keys[1:])), rep.keys
    print(f"gun {case}: {rep.ticks} ticks, {rep.n_keys} key events, states {rep.states}, log {new.log}")


def test_photo_run_at_700_ft_has_a_firing_point():
    """First --auto flight 2026-10-27 (Veteran, 700 ft, 6.5 deg nose up): the photo score bottomed at 207 against 0.8 x 256 = 205, so
    no frame was ever fired although the game's own window (score < 256) was open from 3.5 km to 1 km."""
    from f19.core.types import Attitude, GameState, Position, Systems
    from f19.pilot.phases.attack import Attack
    T = (17352.0, 22200.0)
    a = Attack("primary", "photo", T, 2, 14.0, 2.0)
    def score(r):
        p = (T[0] - r * 0.9, T[1] - r * 0.4)
        st = GameState(t=0.0, pos=Position(p[0], p[1], 702), att=Attitude(114.0, pitch=6.5), speed_kt=190.0, systems=Systems(flags=0x205), opp=2)
        return a.photo_score(st, p)
    best = min(score(r)[0] for r in range(40, 400, 10))
    budget = score(100)[1]
    assert budget == 256 and best > 0.75 * budget                       # the 0.8 trigger had ~no margin (207 vs 205 in the flight)
    assert best < min(0.92 * budget, budget - 20)                       # the new one can


def test_photo_retries_until_photographed(egame, monkeypatch):
    """2026-10-28 Prague: one frame was taken, the game did not count it, the bay shut and the primary was lost. Now Enter is
    pressed every FRAME_RETRY_S until the event log shows the target photographed, and the bay stays open meanwhile."""
    monkeypatch.setattr(Attack, "CONFIRM_PHOTO", True)
    from pilot_equiv import FakeControls, FlightContext, ManualClock, gamestate, new_ov

    def fly(photo_event):
        w, after = attack_world("photo", photo_event=photo_event, alt=250)
        a = Attack("primary", "photo", TGT, T_REC, None, 2.0)
        ctx = FlightContext(ManualClock(0.0)); ctrl = FakeControls(ctx); enters = 0
        for t, w_ in ticks(w, 4000):
            ctx.clock.set(t); st = gamestate(w_); cmd = a.step(st, ctx); keys = ctrl.apply(cmd, st, t)
            enters += sum(1 for k in keys if k[:2] == ("tap", "enter"))
            after(w_, keys, t, new_ov(cmd))
            if a.done: break
        return a, enters
    a, enters = fly(photo_event=False)               # the game never counts a frame: keep clicking until the photo point is passed
    assert enters >= 3 and a.done, (enters, a.log)
    assert any("not photographed" in l for l in a.log) and not a.photographed
    a, enters = fly(photo_event=True)                # counted at once: one frame, then the bay shuts
    assert enters == 1 and a.photographed and any("photographed after 1 frame" in l for l in a.log), (enters, a.log)


@pytest.mark.parametrize("photo_event", [True, False])
def test_photo_run_pulls_up_over_the_target(egame, monkeypatch, photo_event):
    """2026-10-29 Kolobrzeg / Katowice: the run flew on at 250 ft into the Tank Farm (END: 'flying into that hill'). Inside
    CLEAR_KM the run ends photographed or not, the plane is told to climb CLEAR_UP_FT, and Attack keeps control until it is
    CLEAR_PAST_KM past the target."""
    monkeypatch.setattr(Attack, "CONFIRM_PHOTO", True); monkeypatch.setattr(Attack, "CLEAR_PASS", True)
    from pilot_equiv import FakeControls, FlightContext, ManualClock, gamestate, new_ov

    w, after = attack_world("photo", photo_event=photo_event, alt=250)
    a = Attack("primary", "photo", TGT, T_REC, None, 2.0)
    ctx = FlightContext(ManualClock(0.0)); ctrl = FakeControls(ctx); climbs = []; done_km = None
    for t, w_ in ticks(w, 4000):
        ctx.clock.set(t); st = gamestate(w_); cmd = a.step(st, ctx); keys = ctrl.apply(cmd, st, t)
        km = game_dist(st.xy, TGT) / K
        if cmd.alt is not None: climbs.append((km, cmd.alt))
        after(w_, keys, t, new_ov(cmd))
        if a.done: done_km = km; break
    assert climbs and all(km < Attack.CLEAR_KM + 0.3 for km, _ in climbs[:1]), climbs[:3]
    assert {alt for _, alt in climbs} == {250 + Attack.CLEAR_UP_FT}
    assert done_km is not None and done_km >= Attack.CLEAR_PAST_KM, done_km     # held until past the target
    assert any(l.startswith("pull-up over the target") for l in a.log)
    assert a.photographed == photo_event
