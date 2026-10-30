"""Loadout choice for the mission loop (2026-10-30): one weapon bay for the targets, every other bay Extra Fuel.

Photo and destroy missions only. Both targets photograph -> 1 Camera bay; both destroy -> 1 Maverick bay; the
single bay serves both targets. A mixed mission (one photo, one destroy) would need two weapon bays, so it is
refused: the loop's finder rejects it and re-rolls. Not checked live yet (the arming screen keys are unknown)."""
from dataclasses import dataclass

from f19.core.types import Mission

MAVERICK, CAMERA, EXTRA_FUEL = 5, 16, 17            # loadout weapon indices (docs/game/weapons.md)
BAYS = 4
TASKS = {"photograph": CAMERA, "destroy": MAVERICK}


@dataclass(frozen=True)
class Loadout:
    weapon: int                                     # the one weapon bay: MAVERICK or CAMERA
    extra_fuel: int = BAYS - 1                      # every other bay is Extra Fuel

    @property
    def bays(self): return (self.weapon,) + (EXTRA_FUEL,) * self.extra_fuel
    @property
    def needs_mavericks(self): return self.weapon == MAVERICK


def choose_loadout(tasks):
    """tasks: the targets' task strings ("photograph", "destroy"). Raises ValueError for any other task or a mix."""
    kinds = {TASKS.get(t) for t in tasks}
    if not tasks or None in kinds: raise ValueError(f"loop missions are photograph / destroy only, got {sorted(map(str, tasks))}")
    if len(kinds) > 1: raise ValueError("mixed photograph + destroy needs two weapon bays: not supported")
    return Loadout(kinds.pop())


def mission_loadout(mission: Mission): return choose_loadout([t.task for t in mission.targets])
