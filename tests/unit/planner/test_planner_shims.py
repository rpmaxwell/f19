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


def test_read_mission_command_reads_a_saved_image(tmp_path, egame):
    """python -m f19.cli.read_mission (the command new_plan runs; was scripts/read_mission.py) on a saved START image."""
    img = product.settings().logs / "tmp" / "briefing_mem.bin"
    if not img.exists(): pytest.skip("no START briefing image in F19_DATA/logs/tmp")
    out = tmp_path / "m.json"
    p = subprocess.run([sys.executable, "-m", "f19.cli.read_mission", "--snapshot", str(img), "--out", str(out)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    m = json.loads(out.read_text())
    assert m["theater"] == "CE" and [t["role"] for t in m["targets"]][:2] == ["primary", "secondary"] and m["radars"] and m["takeoff"]["place"]


@pytest.mark.slow
def test_new_plan_end_to_end_from_a_mission_file(tmp_path, egame):
    """new_plan --mission-file (no game): writes plan.json, runs the planner as a subprocess, writes route.json with the acquisition windows."""
    from conftest import FIXTURES, need
    d = need(FIXTURES / "plans" / "ce_kolobrzeg_1022_2250", "the kolobrzeg golden plan")
    out = tmp_path / "plan"
    p = subprocess.run([sys.executable, "-m", "f19.planner.new_plan", "--mission-file", str(d / "mission_mem.json"), "--extra-fuel", "2", "--out", str(out)],
                       cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout[-1500:] + p.stderr[-1500:]
    route = json.loads((out / "route.json").read_text())
    assert any(pt.get("acq") for pt in route["points"]) and "fly it:" in p.stdout and "-m f19.cli.fly" in p.stdout
