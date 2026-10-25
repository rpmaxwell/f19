"""f19.emulator.launch (argv only: nothing is started) and f19.emulator.savestate (on a temp F19_DATA)."""
from pathlib import Path

import pytest

from f19.core.settings import Settings
from f19.emulator import launch, savestate


def test_command():
    argv = launch.command(conf="/x/bare.conf", port=6123, savedir="/s", noautoexec=True, extra=["-fastlaunch"], binary="/b/dosbox-x")
    assert argv == ["/b/dosbox-x", "-conf", "/x/bare.conf", "-set", "sdl automation port=6123", "-savedir", "/s",
                    "-noautoexec", "-fastlaunch"]
    s = Settings.from_env({"F19_DOSBOX_BIN": "/opt/db", "F19_DOSBOX_CONF": "/c/f19.conf"})
    assert (s.dosbox_bin, s.dosbox_conf) == (Path("/opt/db"), Path("/c/f19.conf"))
    d = Settings.from_env({})
    assert d.dosbox_bin == Path("~/projects/dosbox-x/src/dosbox-x").expanduser() and d.saves == d.data / "saves"


def test_bare_conf(tmp_path):
    t = launch.write_bare_conf(tmp_path / "b.conf", 6123).read_text()
    assert "automation port = 6123" in t and t.rstrip().endswith("[autoexec]")


def test_checkpoints(tmp_path):
    s = Settings.from_env({"F19_DATA": str(tmp_path)})
    s.saves.mkdir()
    assert savestate.checkpoints(s) == []
    (s.saves / "1.sav").write_bytes(b"state A")
    assert savestate.keep("runway 1", settings=s) == s.saves / "checkpoint_runway_1.sav"
    assert savestate.checkpoints(s) == ["runway_1"]
    (s.saves / "1.sav").write_bytes(b"other")
    savestate.install("runway 1", settings=s)
    assert (s.saves / "1.sav").read_bytes() == b"state A"
    with pytest.raises(FileNotFoundError): savestate.install("nope", settings=s)
    assert savestate.launch_args(s) == ["-savedir", str(s.saves)]
