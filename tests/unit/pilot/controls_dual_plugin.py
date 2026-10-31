"""pytest plugin (loaded with -p by test_pilot_controls.py in a subprocess): replaces pilot_worlds.FakeControls by
DualControls, which runs the real f19.pilot.controls.Controls (on a recording KeyLog emulator) next to FakeControls on
every apply() of every WP-7b equivalence scenario and replay, and records each tick where the key events or the
speed target differ. The summary goes to the JSON file named by F19_DUAL_OUT: {test nodeid: [ticks, mismatches,
first mismatch]}."""
import json
import os

import pilot_worlds as PE

from f19.pilot.controls import Controls

RESULTS = {}
_current = {"id": None}


class _Shadow:
    """Controls' own context: the tick clock and a separate toggle-debounce table (FakeControls writes ctx.toggled
    first in the same tick, which would make Controls see its own key as just tapped)."""
    def __init__(self, ctx): self.ctx, self.toggled = ctx, {}
    def now(self): return self.ctx.now()


class DualControls(PE.FakeControls):
    def __init__(self, ctx):
        super().__init__(ctx)
        self.emu = PE.KeyLog()
        self.ctrl = Controls(self.emu)
        self.shadow = _Shadow(ctx)

    def apply(self, cmd, state, now):
        self.ctrl.speed.target = self.speed          # run_phase seeds FakeControls.speed directly
        fk = super().apply(cmd, state, now)
        self.ctrl.apply(cmd, state, self.shadow, now=now)
        nk = self.emu.take()
        r = RESULTS.setdefault(_current["id"], [0, 0, ""])
        r[0] += 1
        if fk != nk or self.speed != self.ctrl.speed.target:
            r[1] += 1
            if not r[2]: r[2] = f"fake {fk} speed {self.speed} / controls {nk} speed {self.ctrl.speed.target}"
        return fk


PE.FakeControls = DualControls


def pytest_runtest_setup(item): _current["id"] = item.nodeid


def pytest_sessionfinish(session, exitstatus):
    out = os.environ.get("F19_DUAL_OUT")
    if out:
        with open(out, "w") as f: json.dump(RESULTS, f, indent=1)
