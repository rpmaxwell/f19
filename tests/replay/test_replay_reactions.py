"""Replay baseline: the reactions' decisions on the fixture flights must stay exactly what the baseline code produced.

Two kinds of assertion per (run, replay):
  - exact: the metrics dict == tests/replay/baseline.json (counts, rates, state sequences, the MissileWatch decision
    digest). Any behaviour change fails here; if the change is intended, re-run with --update-baseline and review the
    JSON diff in the commit.
  - floor: a looser sanity bound (FLOORS) that says what agreement with the logged nav flight we consider "working";
    it should survive an intended tweak, and if it fails the replay no longer resembles the flight at all.
"""
import json
from pathlib import Path

import pytest

from harness import REPLAYS, have_fixtures, manifest, replay_run

BASELINE = Path(__file__).with_name("baseline.json")
pytestmark = pytest.mark.skipif(not have_fixtures(), reason="replay fixtures missing (tests/replay/make_fixtures.py)")

RUNS = sorted(manifest()) if have_fixtures() else []


def _close(a, b, path=""):
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and abs(a - b) <= 1e-9, f"{path}: {a} != {b}"
    elif isinstance(a, dict) and isinstance(b, dict):
        assert set(a) == set(b), f"{path}: keys {sorted(set(a) ^ set(b))} differ"
        for k in a: _close(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"{path}: length {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)): _close(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


# replay -> (metric, minimum) checked wherever the replay has something to compare
FLOORS = {
    "terrain_escape": [("state_agree_rate", 0.99)],
    "terrain_escape_margin150": [("state_agree_rate", 0.995)],
    "fighter_watch": [("state_agree_rate", 0.95), ("want_rate", 0.75)],
    "attack_primary": [("state_agree_rate", 0.9)],
}


@pytest.fixture(scope="module")
def results():
    return json.loads(json.dumps({run: replay_run(run) for run in RUNS}))     # JSON-normalised, as the baseline


@pytest.fixture(scope="module")
def baseline(results, update_baseline):
    if update_baseline:
        BASELINE.write_text(json.dumps(results, indent=1) + "\n")
    if not BASELINE.exists(): pytest.fail("no baseline.json: run pytest --update-baseline once and commit it")
    return json.loads(BASELINE.read_text())


@pytest.mark.parametrize("run", RUNS)
def test_replays_present(results, baseline, run):
    assert sorted(results[run]) == sorted(baseline[run])


@pytest.mark.parametrize("name", list(REPLAYS))
@pytest.mark.parametrize("run", RUNS)
def test_replay_matches_baseline(results, baseline, run, name):
    if name not in baseline.get(run, {}): pytest.skip(f"nothing to compare for {name} in {run}")
    _close(results[run][name], baseline[run][name], f"{run}.{name}")


@pytest.mark.parametrize("name", list(FLOORS))
@pytest.mark.parametrize("run", RUNS)
def test_replay_floor(results, run, name):
    m = results[run].get(name)
    if not m: pytest.skip(f"no {name} in {run}")
    for key, lo in FLOORS[name]:
        if m.get(key) is None: continue
        assert m[key] >= lo, f"{name} {key} {m[key]} < {lo}"


@pytest.mark.parametrize("run", RUNS)
def test_takeoff_done_near_nav(results, run):
    m = results[run].get("takeoff")
    if not m: pytest.skip("no takeoff in this fixture")
    assert m["done_replayed"] and m["within_tol"], m


@pytest.mark.parametrize("run", RUNS)
def test_missile_first_predictions(results, run):
    m = results[run].get("missile_watch")
    if not m or not m["missiles"]: pytest.skip("no enemy missiles in this fixture")
    assert m["first_predictions_right"] == m["missiles"]
