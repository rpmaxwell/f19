"""The old analysis/planner entry points and module names still work and are the package's objects."""
import json
import subprocess
import sys

import pytest

import product

ROOT = product.ROOT
PLANNER_DIR = str(ROOT / "analysis" / "planner")


@pytest.fixture(scope="module")
def old():
    if PLANNER_DIR not in sys.path: sys.path.insert(0, PLANNER_DIR)
    import alertsim, fuelplan, loiter, plan, probes, profiles, radar, route, world   # noqa: E401
    return dict(alertsim=alertsim, fuelplan=fuelplan, loiter=loiter, plan=plan, probes=probes, profiles=profiles,
                radar=radar, route=route, world=world)


def test_old_names_are_the_package_objects(old):
    import f19.planner as P
    from f19.planner import cli, fuelplan, planner, profiles, radar, report, router, world
    from f19.core import units
    assert old["route"].Router is router.Router and old["route"].PlanError is P.PlanError is router.PlanError
    assert old["route"]._search is router._search                       # private names re-exported too
    assert old["fuelplan"].integrate is fuelplan.integrate and old["profiles"].load is profiles.load
    assert old["radar"].RadarModel is radar.RadarModel and old["radar"].MODEL is radar.MODEL
    assert old["world"].World is world.World and old["world"].KT_KMH == units.GROUND_KMH_PER_KT == world.GROUND_KMH_PER_KT
    assert old["world"].K == 64.0 and isinstance(old["world"].K, float)
    plan = old["plan"]
    assert issubclass(plan.Planner, planner.Planner) and plan.summarise is report.summarise and plan.main is cli.main
    assert plan.emit is cli.emit and sys.modules["route"] is old["route"]
    assert old["fuelplan"].TANK_LB == fuelplan.TANK_LB == 1900.0


def test_takeoff_heading_unchanged(old):
    for th, idx in old["plan"].THEATER_INDEX.items():
        for carrier in (False, True):
            want = (0.0 if idx & 1 else 180.0) + (0x400 * 360 / 65536 if carrier else 0.0)      # the pre-move formula
            assert old["plan"].game_takeoff_heading(th, carrier) == want


def test_mission_dict_from_core_mission(egame):
    from conftest import FIXTURES, need
    from f19.core.types import Mission
    from f19.planner.planner import mission_dict
    d = json.loads((need(FIXTURES / "plans" / "ce_kolobrzeg_1022_2250") / "mission_mem.json").read_text())
    m = mission_dict(Mission.from_mission_json(d))
    assert m["theater"] == d["theater"]
    for a, b in zip(m["targets"], d["targets"]):
        assert all(a[k] == b[k] for k in ("role", "x", "y", "place", "object", "task"))
    for a, b in zip(m["radars"], d["radars"]):
        assert all(a[k] == b[k] for k in ("place", "system", "x", "y", "kind", "type"))
    assert mission_dict(d) is d


@pytest.mark.parametrize("args", [["analysis/planner", "--help"], ["analysis/planner/new_plan.py", "--help"],
                                  ["analysis/planner/plan.py", "--help"]])
def test_entry_points_run(args):
    p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "usage" in p.stdout


def test_checks_script(egame):
    p = subprocess.run([sys.executable, "analysis/planner/checks.py"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "all checks passed" in p.stdout, p.stdout[-2000:] + p.stderr[-2000:]
