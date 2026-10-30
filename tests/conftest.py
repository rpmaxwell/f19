"""Shared pytest setup.

  --run-slow          also run tests marked @pytest.mark.slow (full planner runs, minutes each)
  --update-baseline   rewrite tests/replay/baseline.json from the current code instead of comparing against it

Data the tests read lives outside the repo, under F19_DATA (default ~/f19-data):
  fixtures/plans/<name>/     frozen planner inputs + expected outputs (golden tests)
  fixtures/replay/           trimmed memlog runs + nav logs + the routes they flew (replay harness)
  game/EGAME.bin             unpacked game image (radar / missile rules load it at import)
Tests that need missing data are skipped, not failed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import product  # noqa: E402  (puts src/, analysis/, scripts/ on sys.path)

S = product.settings()
FIXTURES = S.data / "fixtures"


def pytest_addoption(parser):
    parser.addoption("--run-slow", action="store_true", default=False, help="run tests marked slow (full planner runs)")
    parser.addoption("--update-baseline", action="store_true", default=False,
                     help="rewrite tests/replay/baseline.json from the current code (review the diff before committing)")


def pytest_configure(config):
    # also registered in pyproject.toml; repeated here so the marker exists even if that config moves
    config.addinivalue_line("markers", "slow: long-running (full planner runs); skipped unless --run-slow")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-slow"): return
    skip = pytest.mark.skip(reason="slow: needs --run-slow")
    for it in items:
        if "slow" in it.keywords: it.add_marker(skip)


def need(path: Path, what: str = "") -> Path:
    """Skip the calling test when a data file / directory is missing."""
    if not path.exists(): pytest.skip(f"missing {what or 'data'}: {path}")
    return path


@pytest.fixture(scope="session")
def egame():
    """The game image the rules modules load at import; skip everything that needs it when it's absent."""
    return need(S.egame_bin, "EGAME.bin (F19_DATA/game)")


@pytest.fixture(scope="session")
def update_baseline(request):
    return request.config.getoption("--update-baseline")


@pytest.fixture(autouse=True)
def single_frame_photo(monkeypatch):
    """The pilot equivalence tests compare Attack with scripts/phases.py, which takes one photo frame; the keep-going-until-
    photographed behaviour (Attack.CONFIRM_PHOTO) has its own test (test_photo_retries_until_photographed), and so has the
    pull-up over the target (Attack.CLEAR_PASS, test_photo_run_pulls_up_over_the_target)."""
    try: from f19.pilot.phases.attack import Attack
    except ImportError: return
    monkeypatch.setattr(Attack, "CONFIRM_PHOTO", False); monkeypatch.setattr(Attack, "CLEAR_PASS", False)
