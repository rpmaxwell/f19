"""WP-7a Controls: the real key sender against the WP-7b contract.

test_controls_match_fake_on_every_wp7b_scenario re-runs every WP-7b equivalence test (synthetic scenarios and the
replay fixtures) in a subprocess with pilot_worlds.FakeControls replaced by a dual adapter (controls_dual_plugin.py):
on every apply() the real Controls, on a recording emulator, must emit the identical key event stream (tap / scan /
down / up with hold times) and leave the same speed target. The harness-sensitivity test that mutates FakeControls'
debounce must show mismatches (proof that the dual check sees a difference). The rest: unit tests of the parts the
scenarios don't reach (roll keys held while the trigger is, releases, scheduled holds, unknown actions)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pilot_worlds import KeyLog, gamestate, world
from f19.core.clock import ManualClock
from f19.core.types import Action, Command, FlightContext, Toggle
from f19.pilot.controls import EQUALS, NOSE_DOWN, NOSE_UP, Controls

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
WP7B = ["test_pilot_phases.py", "test_pilot_reactions.py", "test_pilot_types.py", "test_pilot_fly_leg.py"]
SENSITIVITY_TEST = "test_pilot_types.py::test_harness_catches_a_change"


def test_controls_match_fake_on_every_wp7b_scenario(tmp_path, egame):
    out = tmp_path / "dual.json"
    env = dict(os.environ, F19_DUAL_OUT=str(out),
               PYTHONPATH=os.pathsep.join([str(HERE), str(ROOT / "tests"), str(ROOT / "src"), os.environ.get("PYTHONPATH", "")]))
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "controls_dual_plugin", "-p", "no:cacheprovider",
                        *[str(HERE / f) for f in WP7B]], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    res = json.loads(out.read_text())
    ticks = sum(v[0] for v in res.values())
    bad = {k: v for k, v in res.items() if v[1] and SENSITIVITY_TEST not in k}
    assert not bad, bad
    sens = [v for k, v in res.items() if SENSITIVITY_TEST in k]
    assert sens and sens[0][1] > 0, "the dual check must see the mutated debounce"
    assert ticks > 20000, ticks
    print(f"Controls == FakeControls on {ticks} applies in {len(res)} tests")


# ------------------------------------------------------------------------------------------------------- units
def _ctl():
    emu = KeyLog(); return emu, Controls(emu), FlightContext(ManualClock(100.0))


def test_trigger_repressed_when_roll_keys_change(egame):
    emu, c, ctx = _ctl()
    st = gamestate(world(alt=1500, spd=300))
    c.apply(Command(gun_fire=True), st, ctx); assert emu.take() == [("down", "bs")]
    c.apply(Command(gun_fire=True), st, ctx); assert emu.take() == []          # nothing changed: held on
    c.down("left"); emu.take()                                                 # the heading hold rolls
    c.apply(Command(gun_fire=True), st, ctx); assert emu.take() == [("up", "bs"), ("down", "bs")]
    c.apply(Command(gun_fire=True), st, ctx); assert emu.take() == []
    c.apply(Command(), st, ctx); assert emu.take() == [("up", "bs")]


def test_pitch_band_and_hold(egame):
    emu, c, ctx = _ctl()
    st = gamestate(world(alt=10, spd=120, pitch=2.0, ts=2.0))
    c.apply(Command(pitch_min=8.0, pitch_max=13.0), st, ctx); assert emu.take() == [("tap", NOSE_UP, 0.6)]
    ctx.clock.advance(0.4); c.apply(Command(pitch_min=8.0, pitch_max=13.0), st, ctx); assert emu.take() == []
    ctx.clock.advance(0.2); c.apply(Command(pitch_min=8.0, pitch_max=13.0), st, ctx); assert emu.take() == [("tap", NOSE_UP, 0.6)]
    c.apply(Command(pitch_hold="up"), st, ctx); assert emu.take() == [("down", NOSE_UP)]
    c.apply(Command(pitch_hold="down"), st, ctx); assert emu.take() == [("up", NOSE_UP), ("down", NOSE_DOWN)]
    c.apply(Command(pitch_deg=-10.0, pitch_tol=0.8), st, ctx)
    assert emu.take() == [("tap", NOSE_DOWN, 0.5), ("up", NOSE_DOWN)]


def test_toggle_debounce_shared_and_throttle_clears_speed(egame):
    emu, c, ctx = _ctl()
    st = gamestate(world(flags=0))
    c.speed.target = 190.0
    c.apply(Command(actions=(Toggle(Action.FLAPS_TOGGLE, True), Action.THROTTLE_MAX)), st, ctx)
    assert emu.take() == [("tap", "9", 0.2), ("scan", EQUALS, True, 0.2)] and c.speed.target is None
    assert c.toggle(Toggle(Action.FLAPS_TOGGLE, True), st, ctx.now() + 1.0, ctx.toggled) is True and emu.take() == []
    assert c.toggle(Toggle(Action.FLAPS_TOGGLE, True), st, ctx.now() + 3.0, ctx.toggled) is True and emu.take() == [("tap", "9", 0.2)]
    c.apply(Command(speed_kt=165.0), st, ctx); assert c.speed.target == 165.0
    c.apply(Command(actions=(Action.EJECT,)), st, ctx)
    assert emu.take() == [("scan", 0x44, True, 0.2)]                           # Shift+F10, the shifted-scancode path
    c.apply(Command(actions=(Action.DESIGNATE,)), st, ctx)                    # no key: dropped, counted, nothing sent
    assert emu.take() == [] and c.unsupported == {Action.DESIGNATE: 1}


def test_scheduled_hold_and_release_all(egame):
    emu, c, ctx = _ctl()
    c.hold("right", 101.0); assert emu.take() == [("down", "right")]
    assert c.service(100.5) == [] and c.service(101.0) == ["right"] and emu.take() == [("up", "right")]
    c.down("left"); c.down("bs"); c.down("up"); emu.take()
    with c: pass                                                               # exit releases
    keys = emu.take()
    assert keys[:5] == [("up", k) for k in ("left", "right", "up", "down", "bs")] and keys[-1] == ("tap", "kp5", 0.2)
    assert not c.held and not emu._held


def test_after_load_sends_arrow_breaks(egame):
    class Emu(KeyLog):
        def release_arrows(self): self.keys.append(("breaks",))
    emu = Emu(); c = Controls(emu); c.down("left"); emu.take()
    c.after_load()
    k = emu.take()
    assert ("breaks",) in k and k[-1] == ("tap", "kp5", 0.2) and ("up", "left") in k
