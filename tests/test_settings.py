from pathlib import Path

from f19.core.settings import Settings


def test_defaults_and_overrides():
    s = Settings.from_env({})
    assert s.data == Path("~/f19-data").expanduser() and s.port == 6000 and s.host == "127.0.0.1"
    s = Settings.from_env({"F19_DATA": "/x", "F19_AUTOMATION_PORT": "7001"})
    assert s.logs == Path("/x/logs") and s.egame_bin == Path("/x/game/EGAME.bin") and s.port == 7001
