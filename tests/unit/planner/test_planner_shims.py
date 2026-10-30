"""The planner's entry points (python -m f19.planner, f19.planner.new_plan, f19.planner.checks) and the mission dict."""
import json
import subprocess
import sys

import pytest

import product

ROOT = product.ROOT


def test_takeoff_heading_unchanged():
    from f19.planner.planner import THEATER_INDEX, game_takeoff_heading
    for th, idx in THEATER_INDEX.items():
        for carrier in (False, True):
            want = (0.0 if idx & 1 else 180.0) + (0x400 * 360 / 65536 if carrier else 0.0)      # the pre-move formula
            assert game_takeoff_heading(th, carrier) == want


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


@pytest.mark.parametrize("args", [["-m", "f19.planner", "--help"], ["-m", "f19.planner.new_plan", "--help"]])
def test_entry_points_run(args):
    p = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    assert "usage" in p.stdout


def test_checks_script(egame):
    p = subprocess.run([sys.executable, "-m", "f19.planner.checks"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0 and "all checks passed" in p.stdout, p.stdout[-2000:] + p.stderr[-2000:]
