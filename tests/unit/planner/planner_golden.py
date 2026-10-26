"""Shared set-up for the planner unit tests: the golden plan fixtures (F19_DATA/fixtures/plans) and one in-flight
planning context (planner.api.context) per golden, built once per session."""
import json
from functools import lru_cache

import numpy as np

from conftest import FIXTURES, need

GOLDENS = ["ce_kolobrzeg_1022_2250", "ce_budejovice_1023_1811"]


@lru_cache(None)
def golden(name):
    """(route.json dict, mission_mem.json dict) of a golden plan dir."""
    d = need(FIXTURES / "plans" / name, f"golden plan fixture {name}")
    return json.loads((d / "route.json").read_text()), json.loads((d / "mission_mem.json").read_text())


@lru_cache(None)
def context(name):
    from f19.planner import api
    ref, mission = golden(name)
    return api.context(ref["spec"], mission)


def planned_tanks(points, i, bays):
    """Extra Fuel tanks the reference has not transferred yet at point i (rises of fuel_lb before it)."""
    return bays - sum(1 for a, b in zip(points[:i], points[1:i + 1]) if b["fuel_lb"] > a["fuel_lb"])


def pose_near(points, i, off_km=0.0, dh=0.0, bays=0, fuel=None):
    """A synthetic airborne pose at reference point i: off_km to the right of the leg i -> i+1, heading the leg's + dh,
    the reference's fuel, clock and tanks there."""
    from f19.planner import api
    from f19.planner.router import hdg, unit
    a, b = points[i], points[i + 1]
    u = unit(np.subtract((b["x"], b["y"]), (a["x"], a["y"])))
    p = np.array([a["x"], a["y"]], float) + np.array([-u[1], u[0]]) * off_km * 64
    return api.Pose(float(p[0]), float(p[1]), (hdg(u) + dh) % 360, 250.0, a["fuel_lb"] if fuel is None else fuel,
                    float(a["t_game_s"]), planned_tanks(points, i, bays))
