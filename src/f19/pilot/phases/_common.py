"""Bits the ported phases share (from scripts/phases.py): loadout weapon indices, the old float geometry, the
set_toggle replacement and the speed-hold intent.

Geometry: phases.wrap / bearing / game_dist are core.geometry.wrap_deg / bearing_deg / game_dist_units exactly
(the float octagonal distance, max + min / 2, no clamp: the variant the phases use).
"""
import dataclasses

from f19.core.geometry import bearing_deg as bearing, game_dist_units as game_dist, wrap_deg as wrap  # noqa: F401
from f19.core.types import Action, Command, Toggle

K = 64.0                                         # units per km (float, as phases.K: the banners format the same)
MAVERICK, CAMERA, EXTRA_FUEL = 5, 16, 17         # loadout weapon indices (phases.py)
SETTLE_S = 3.0                                   # phases.set_toggle default settle


def want_toggle(acts, state, action, on, settle=SETTLE_S):
    """phases.set_toggle without the key: append a Toggle request to `acts` unless the flag already reads `on`.
    Returns what set_toggle returned (True = the flag isn't as wanted yet: a tap is due or still settling)."""
    t = Toggle(action, on, settle)
    if t.satisfied(state.systems.flags): return False
    acts.append(t); return True


def tap_due(ctx, action, now, settle=SETTLE_S):
    """Would Controls tap a Toggle of `action` now (its debounce, ctx.toggled)? For counting presses only."""
    last = ctx.toggled.get(action)
    return last is None or now - last >= settle


class SpeedIntent:
    """What the phases did to the old Throttle's target inside one step (thr.target = x, thr.max(), thr.idle()),
    turned into Command fields: THROTTLE_MAX / THROTTLE_IDLE in the actions (they switch the speed hold off, as
    Throttle.max / idle did) and speed_kt = the target the step left, when its last change was a set."""
    def __init__(self, ctx):
        self.target, self.speed_kt = ctx.speed_target, None

    def set(self, kt): self.target = self.speed_kt = kt

    def max(self, acts): acts.append(Action.THROTTLE_MAX); self.target = self.speed_kt = None

    def idle(self, acts): acts.append(Action.THROTTLE_IDLE); self.target = self.speed_kt = None


def command_of(ov, events=(), actions=(), **fields):
    """An old override dict (+ event texts, actions, other Command fields) as a Command (Command.from_overrides)."""
    return dataclasses.replace(Command.from_overrides(ov), events=tuple(events), actions=tuple(actions), **fields)
