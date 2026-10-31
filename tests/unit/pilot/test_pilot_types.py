"""The WP-7b additions to f19.core.types (additive: tests/unit/core covers the rest), the behaviors' protocol
conformance, the layer rule for behaviors (core + rules only), and that the dual key-sender check (controls_dual_plugin) sees a change in the FakeControls contract."""
import ast
from pathlib import Path

import pytest

from pilot_worlds import FakeControls, gamestate, projectile, run_new, world
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
    """test_pilot_controls re-runs the scenarios with a dual adapter (Controls next to FakeControls) and requires zero mismatches,
    except in THIS test, where the adapter itself is mutated (every tap held 0.05 s longer): Controls does not follow, so the dual
    check has to see a difference (and does: it asserts mismatches here). On its own the test checks that the mutation shows in
    the key stream."""
    w, after = TP.takeoff_world()
    plain = run_new(Takeoff(190.0, 450), TP.ticks(w, 600), after=after)
    saved = FakeControls._tap
    def longer(self, key, hold=0.2): return saved(self, key, hold + 0.05)
    FakeControls._tap = longer
    try:
        w, after = TP.takeoff_world()
        mutated = run_new(Takeoff(190.0, 450), TP.ticks(w, 600), after=after)
    finally:
        FakeControls._tap = saved
    assert mutated.keys != plain.keys
