"""f19.memory.mission against the pre-WP-4 read_mission (frozen copy): START briefing images and in-flight memlogs."""
import contextlib
import io
import json
from pathlib import Path

import pytest

import product
import read_mission_legacy as L
from f19.core.types import Mission
from f19.memory import mission as M
from f19.memory.reader import MemoryReader, ReplayReader, SnapshotMemory

S = product.settings()
IMAGES = [p for p in (S.logs / "tmp" / "briefing_mem.bin", S.logs / "plan" / "northcape" / "briefing_mem.bin") if p.exists()]
FIX = S.data / "fixtures" / "replay" / "logs" / "memlog"
RUNS = sorted(p for p in FIX.glob("*") if (p / "windows.json").exists()) if FIX.exists() else []


@pytest.fixture(scope="module")
def world():
    if not (S.game_dir / "CE.WLD").exists(): pytest.skip("no game install (F19_GAME_DIR)")
    import read_mission
    return read_mission.WORLD


def _quiet(f, *a, **k):
    err = io.StringIO()
    with contextlib.redirect_stderr(err): return f(*a, **k), err.getvalue()


@pytest.mark.skipif(not IMAGES, reason="no START memory image in F19_DATA/logs")
@pytest.mark.parametrize("img", IMAGES, ids=lambda p: p.parent.name)
def test_briefing_image_matches_legacy(img, world):
    mem = img.read_bytes()
    th = L.detect_theater(mem)
    assert M.detect_theater(mem, world) == th
    want, werr = _quiet(L.parse, mem, th)
    got, gerr = _quiet(M.parse, mem, th, world=world)
    assert got == want and gerr == werr
    assert _quiet(M.parse_all, mem, th, world=world)[0] == _quiet(L.parse_all, mem, th)[0]
    ds = M.start_ds(mem)
    assert ds == L.start_ds(mem) and M.briefing(mem, ds) == L.briefing(mem, ds)
    names = world.names(th)
    assert M.mission_table(mem, ds, names) == L.mission_table(mem, ds, names)
    # MemoryReader.mission over an emulator-like view of the image
    r = MemoryReader(SnapshotMemory([("all", 0, len(mem))], mem), world)
    d = _quiet(r.mission_dict)[0]
    assert d == {**want, "theater": th}
    m = _quiet(r.mission)[0]
    assert isinstance(m, Mission) and m == Mission.from_mission_json({**want, "theater": th}) and m.primary.role == "primary"


@pytest.mark.skipif(not RUNS, reason="no replay fixtures")
def test_in_flight_memlog_matches_legacy(world):
    rr = ReplayReader(RUNS[0], world)
    mem = rr.memory()
    th = L.detect_theater(mem)
    want, werr = _quiet(L.parse, mem, th)
    got, gerr = _quiet(rr.mission_dict)
    assert got == {**want, "theater": th} and gerr == werr and "START is not running" in gerr
    assert _quiet(rr.mission)[0].primary.role == "primary?"


def test_mission_needs_a_world():
    with pytest.raises(ValueError, match="needs a world"):
        MemoryReader(SnapshotMemory([], b"")).mission()


def test_takeoff_heading():
    assert M.takeoff_heading(3, False) == 0.0 and M.takeoff_heading(0, False) == 180.0
    assert M.takeoff_heading(1, True) == pytest.approx(5.625)
    assert M.THEATER_INDEX == L.THEATER_INDEX and M.MISSION_TYPE == L.MISSION_TYPE
