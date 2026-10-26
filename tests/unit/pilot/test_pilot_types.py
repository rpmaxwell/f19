"""The WP-7b additions to f19.core.types (additive: tests/unit/core covers the rest), the behaviors' protocol
conformance, the layer rule for behaviors (core + rules only), and that the equivalence harness does catch a change."""
import ast
from pathlib import Path

import pytest

from pilot_equiv import FakeControls, gamestate, projectile, run_phase, world
import test_pilot_phases as TP
from f19.core.clock import ManualClock
from f19.core.types import Action, Behavior, Command, FlightContext, GameState, Toggle
from f19.pilot.phases import Attack, FlyLeg, GunAttack, Landing, Takeoff
from f19.pilot.reactions import FighterWatch, FuelTransfer, MissileWatch, TerrainEscape

SRC = Path(__file__).resolve().parents[3] / "src" / "f19" / "pilot"


def test_command_new_fields_and_overrides():
    c = Command(heading=10, pitch_min=8.0, pitch_max=13.0, events=("x",), actions=(Toggle(Action.BAY_TOGGLE, True, 1.5),))
    assert c.to_overrides() == {"want": 10, "pitch_min": 8.0, "pitch_max": 13.0}          # events / actions aren't keys
    with pytest.raises(KeyError): Command.from_overrides({"events": ("x",)})
    m = c.merged(Command(heading=20, events=("y",), actions=(Action.FIRE,)))
    assert m.heading == 20 and m.events == ("x", "y") and m.actions == (Toggle(Action.BAY_TOGGLE, True, 1.5), Action.FIRE)
    hash(m)


def test_toggle():
    t = Toggle(Action.GEAR_TOGGLE, True)
    assert t.bit == 0x01 and t.settle_s == 3.0 and t.satisfied(0x01) and not t.satisfied(0x02)
    assert Toggle(Action.FLAPS_TOGGLE, False).satisfied(0x01) and not Toggle(Action.BAY_TOGGLE, True).satisfied(0)


def test_gamestate_reads():
    w = world(x=100, y=200, alt=300, spd=190, hdg=90.0, ts=2.0)
    w["events"] = [(10, 0x80, 12, 5)]; w["targets"] = {12: (1, 2, True)}; w["projectiles"][1] = projectile(0x1B, 40)
    w["gun"] = (600, 4, 20)
    st = gamestate(w)
    assert st.ts == 2.0 and st.events == ((10, 0x80, 12, 5),) and st.target_record(12) == (1, 2, True)
    assert st.target_record(3) is None and st.projectiles[1] == (0x1B, 40) and st.projectiles[0] == (0, 0)
    assert (st.systems.gun_rounds, st.frame_scale, st.gun_slots) == (600, 4, 20) and st.xy == (100, 200)
    plain = GameState.from_dicts(w["s"], w["f"])
    assert plain.ts == 1.0 and plain.events is None and plain.projectiles == () and plain.aircraft == []
    ac = [dict(slot=3, x=1, y=2, alt=5000, type=3, name="MiG-29", flags=8, alert=7)]
    assert GameState.from_dicts(w["s"], w["f"], aircraft=ac).aircraft == ac
    hash(st)


def test_behaviors_follow_the_protocol():
    bs = [FlyLeg(), Takeoff(190), Attack("primary", "photo", (0, 0), 1, None, 1.0), GunAttack(1, (0, 0)), Landing((0, 0), (0, 100)),
          FuelTransfer(), FighterWatch(), TerrainEscape(lambda *a: None), MissileWatch()]
    for b in bs: assert isinstance(b, Behavior), b
    prios = [b.priority for b in bs]
    assert prios == [0, 10, 20, 20, 30, 5, 40, 60, 50]


def test_behaviors_import_only_core_and_rules():
    for f in list((SRC / "phases").glob("*.py")) + list((SRC / "reactions").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) else []
            for n in names:
                if n and n.startswith("f19."):
                    assert n.split(".")[1] in ("core", "rules", "pilot"), (f.name, n)
                    if n.startswith("f19.pilot."): assert n.split(".")[2] in ("phases", "reactions"), (f.name, n)


def test_harness_catches_a_change(egame):
    """Mutations the comparison must flag: a rotation speed, the photo bay's re-press settle, and the adapter's debounce."""
    class VR111(Takeoff): VR = 116                       # 6 kt a tick: crosses one tick later
    w, after = TP.takeoff_world()
    rep = run_phase(TP.ph.Takeoff(TP.ph.Throttle(), 190.0, 450), VR111(190.0, 450), TP.ticks(w, 600), TP.old_step,
                    extra=lambda b: (b.state, b.t0), after=after)
    assert not rep.all_equal
    class Retry2(Attack): BAY_RETRY_S = 2.0
    w, after = TP.attack_world("photo", opp=3, lose_bay=1, alt=250, pitch=3.0)
    old = TP.ph.Attack("primary", "photo", TP.TGT, TP.T_REC, None, 2.0, TP.ph.Throttle())
    rep = run_phase(old, Retry2("primary", "photo", TP.TGT, TP.T_REC, None, 2.0), TP.ticks(w, 4000), TP.old_step, after=after)
    assert not rep.all_equal
    saved = FakeControls._action
    def no_debounce(self, a, state, now):
        if isinstance(a, Toggle): a = Toggle(a.action, a.on, 0.0)
        return saved(self, a, state, now)
    FakeControls._action = no_debounce
    try:
        w, after = TP.takeoff_world(lose_first_brakes=True)
        rep = run_phase(TP.ph.Takeoff(TP.ph.Throttle(), 190.0, 450), Takeoff(190.0, 450), TP.ticks(w, 600), TP.old_step,
                        after=after)
        assert not rep.all_equal
    finally:
        FakeControls._action = saved
