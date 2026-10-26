"""Parity: the new HUD (f19.hud, default.toml) draws the replay fixtures exactly as scripts/nav_tui.py does, pane by
pane, text and colours (parity.py). Fast: every 25th snapshot of two runs at 160x50, every 20th of the third with keys
pressed (overlays, zoom, help, layout toggle, focus, pane zoom), one narrow run (one-column, no route); the old HUD
gets the data-segment offset from windows.json. Slow: every snapshot, three sizes, keys, the old HUD scanning."""
import pytest

import parity
from hud_fixtures import replay_fixtures  # noqa: F401  (fixture)

KEYS = {0: "pdcsa", 60: "+", 120: "?", 200: "l", 260: "\t\t", 300: "z", 360: "z-f", 420: "l", 480: "f?", 560: "\tpd"}


def _check(res):
    assert res.frames > 0
    assert res.all_equal, res.summary() + "\n" + "\n".join(map(str, res.diffs))


@pytest.mark.parametrize("k", range(2))
def test_parity_sampled(replay_fixtures, k):
    run, route = parity.fixture_runs(replay_fixtures)[k]
    _check(parity.compare(run, route, (160, 50), every=25))


def test_parity_keys(replay_fixtures):
    run, route = next(r for r in parity.fixture_runs(replay_fixtures) if r[0].name == "1792715432")
    _check(parity.compare(run, route, (160, 50), every=20, keys=KEYS))


def test_parity_narrow_no_route(replay_fixtures):
    run, _ = parity.fixture_runs(replay_fixtures)[1]
    _check(parity.compare(run, None, (100, 40), every=30))


@pytest.mark.slow
@pytest.mark.parametrize("size", [(160, 50), (100, 40), (220, 64)])
@pytest.mark.parametrize("k", range(3))
def test_parity_every_snapshot(replay_fixtures, k, size):
    run, route = parity.fixture_runs(replay_fixtures)[k]
    _check(parity.compare(run, route, size, every=1, keys=KEYS, scan=True))
