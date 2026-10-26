"""Fixtures for the HUD tests (not a conftest.py: tests import `conftest` by name, which a second one would shadow).
Test modules import these names; `egame` is tests/conftest.py's."""
from pathlib import Path

import pytest

from conftest import S, need

REPLAY = S.data / "fixtures" / "replay"


@pytest.fixture(scope="session")
def replay_fixtures(egame):
    need(REPLAY / "manifest.json", "replay fixtures (F19_DATA/fixtures/replay)")
    need(Path(S.game_dir) / "CE.WLD", "the game's CE.WLD (F19_GAME_DIR)")
    return REPLAY
