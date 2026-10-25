"""Golden planner tests: re-plan frozen plan dirs and compare with the outputs the planner wrote then.

Fixtures: F19_DATA/fixtures/plans/<name>/ holds the inputs (plan.json, mission_mem.json) and the expected outputs
(route.json, report.txt, terrain.npz), copied from logs/plan/<name>. Each run copies only the inputs to a temp dir and
runs `python analysis/planner <dir>` in a subprocess, as a user would.

  ce_budejovice_1023_1811   CE, Elite, 3 tanks, loiter auto     ~8-10 min
  ce_kolobrzeg_1022_2250    CE, Elite, 2 tanks, loiter auto     ~3-4 min (second golden; no non-CE plan dir reproduces:
                            the NC dirs predate plan.json, pg_shiraz fails, see below)
Comparison: every route.json field equal, floats within 1e-9 relative (a 2026-10-25 re-run of budejovice differed in
the last bit of two waypoints, 9e-13 units); report.txt equal except its first line (plan dir path and time).

pg_shiraz_1024_1514 does NOT re-plan on the baseline code (refactor_plan.md section 7): the planner raises PlanError
for the primary's attack bearing. The fast test reproduces it with one attack bearing (bearing 0, the one the full
error message names), the slow one runs the whole plan. Both assert the failure, so a fix shows up as a test to update.
"""
import json, math, shutil, subprocess, sys, time

import numpy as np
import pytest

import product
from conftest import FIXTURES, need

ROOT = product.ROOT
PLANS = FIXTURES / "plans"
GOLDENS = ["ce_kolobrzeg_1022_2250", "ce_budejovice_1023_1811"]
INPUTS = ("plan.json", "mission_mem.json")


def _copy_inputs(name, dst):
    src = need(PLANS / name, f"golden plan fixture {name}")
    d = dst / name; d.mkdir()
    for f in INPUTS: shutil.copy2(src / f, d / f)
    return src, d


def _same(a, b, path=""):
    """Field-by-field equality; floats within 1e-9 relative / 1e-6 absolute. Returns a list of differences."""
    if isinstance(a, bool) or isinstance(b, bool) or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        if isinstance(a, dict) and isinstance(b, dict):
            out = [f"{path}.{k}: only in {'new' if k in a else 'golden'}" for k in sorted(set(a) ^ set(b))]
            for k in sorted(set(a) & set(b)): out += _same(a[k], b[k], f"{path}.{k}")
            return out
        if isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b): return [f"{path}: length {len(a)} != {len(b)}"]
            return [d for i, (x, y) in enumerate(zip(a, b)) for d in _same(x, y, f"{path}[{i}]")]
        return [] if a == b else [f"{path}: {a!r} != {b!r}"]
    return [] if math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6) else [f"{path}: {a!r} != {b!r}"]


def test_same_tolerates_last_bit_only():
    assert not _same({"x": [7797.254304988882]}, {"x": [7797.254304988883]})
    assert _same({"x": [7797.25]}, {"x": [7797.26]})
    assert _same({"a": 1}, {"a": 1, "b": 2})


@pytest.mark.slow
@pytest.mark.parametrize("name", GOLDENS)
def test_golden_replan(name, tmp_path):
    src, d = _copy_inputs(name, tmp_path)
    t0 = time.time()
    p = subprocess.run([sys.executable, str(ROOT / "analysis" / "planner"), str(d)], cwd=ROOT, capture_output=True, text=True)
    secs = time.time() - t0
    print(f"{name}: planned in {secs:.0f} s")
    assert p.returncode == 0, p.stdout[-2000:] + p.stderr[-4000:]
    diffs = _same(json.loads((d / "route.json").read_text()), json.loads((src / "route.json").read_text()), "route")
    assert not diffs, f"{len(diffs)} differences, first: {diffs[:10]}"
    new, old = (d / "report.txt").read_text().splitlines(), (src / "report.txt").read_text().splitlines()
    assert new[1:] == old[1:]
    a, b = np.load(d / "terrain.npz"), np.load(src / "terrain.npz")
    assert set(a) == set(b) and all(np.array_equal(a[k], b[k]) for k in a)


PG = "pg_shiraz_1024_1514"
PG_ERROR = "PRIMARY: Airplane Shiraz: no usable attack bearing"
PG_PATH = "no path from (17616,10988) to (8000,9536)"


def test_pg_shiraz_known_planerror(tmp_path, egame):
    """Known bug (refactor_plan.md 7): bearing 0 of the Shiraz attack can't be routed. ~15 s (Planner set-up)."""
    _, d = _copy_inputs(PG, tmp_path)
    plan = product.planner_plan(); route = sys.modules["route"]
    spec = json.loads((d / "plan.json").read_text())
    spec["takeoff_heading"] = round(plan.game_takeoff_heading("PG", False), 1)
    spec["overrides"] = {**spec.get("overrides", {}), "attack_bearing_step_deg": 360}     # bearing 0 only
    pl = plan.Planner(spec, d)
    plain = [pl.takeoff(), *pl.targets(("primary", "secondary")), pl.landing("N")]
    with pytest.raises(route.PlanError) as e:
        next(pl.with_attacks(plain))
    msg = str(e.value)
    assert msg.startswith(PG_ERROR) and PG_PATH in msg, msg


@pytest.mark.slow
def test_pg_shiraz_full_plan_fails(tmp_path):
    _, d = _copy_inputs(PG, tmp_path)
    p = subprocess.run([sys.executable, str(ROOT / "analysis" / "planner"), str(d)], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode != 0
    assert f"route.PlanError: {PG_ERROR} (1 hit terrain or the map edge, 11 could not be routed, e.g. 0: {PG_PATH})" in p.stderr
    assert not (d / "route.json").exists()
