"""f19.memory.mission against what the pre-WP-4 read_mission returned (digests recorded 2026-10-30 from the frozen copy before it was
retired): START briefing images and an in-flight memlog."""
import contextlib
import hashlib
import io
import json
from pathlib import Path

import pytest

import product
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
    from f19.cli.gameworld import GameWorld
    return GameWorld()


def _quiet(f, *a, **k):
    err = io.StringIO()
    with contextlib.redirect_stderr(err): return f(*a, **k), err.getvalue()


def canon(x): return json.dumps(x, sort_keys=True, default=repr)


# (theater, digest of theater + parse + stderr + parse_all + START ds + briefing + mission table), from the old read_mission
GOLD_IMAGES = {"tmp": ("CE", "7728da6b54d513f7"), "northcape": ("NC", "5ff6f7954d1d4365")}
GOLD_INFLIGHT = ("CE", "705c029273dbe5b6")


@pytest.mark.skipif(not IMAGES, reason="no START memory image in F19_DATA/logs")
@pytest.mark.parametrize("img", IMAGES, ids=lambda p: p.parent.name)
def test_briefing_image_matches_the_old_parser(img, world):
    from f19.gamedata import wld
    mem = img.read_bytes()
    th = M.detect_theater(mem, world)
    got, gerr = _quiet(M.parse, mem, th, world=world)
    allm = _quiet(M.parse_all, mem, th, world=world)[0]
    ds = M.start_ds(mem)
    digest = hashlib.sha256(canon((th, got, gerr, allm, ds, M.briefing(mem, ds), M.mission_table(mem, ds, wld.places(th)["names"]))).encode()).hexdigest()[:16]
    assert (th, digest) == GOLD_IMAGES[img.parent.name]
    # MemoryReader.mission over an emulator-like view of the image
    r = MemoryReader(SnapshotMemory([("all", 0, len(mem))], mem), world)
    d = _quiet(r.mission_dict)[0]
    assert d == {**got, "theater": th}
    m = _quiet(r.mission)[0]
    assert isinstance(m, Mission) and m == Mission.from_mission_json({**got, "theater": th}) and m.primary.role == "primary"


@pytest.mark.skipif(not RUNS, reason="no replay fixtures")
def test_in_flight_memlog_matches_the_old_parser(world):
    rr = ReplayReader(RUNS[0], world)
    mem = rr.memory()
    th = M.detect_theater(mem, world)
    got, gerr = _quiet(rr.mission_dict)
    want = {k: v for k, v in got.items() if k != "theater"}
    assert got["theater"] == th and "START is not running" in gerr
    assert (th, hashlib.sha256(canon((th, want, gerr)).encode()).hexdigest()[:16]) == GOLD_INFLIGHT
    assert _quiet(rr.mission)[0].primary.role == "primary?"


def test_mission_needs_a_world():
    with pytest.raises(ValueError, match="needs a world"):
        MemoryReader(SnapshotMemory([], b"")).mission()


def test_takeoff_heading():
    assert M.takeoff_heading(3, False) == 0.0 and M.takeoff_heading(0, False) == 180.0
    assert M.takeoff_heading(1, True) == pytest.approx(5.625)
    assert M.THEATER_INDEX == {0: "LB", 1: "PG", 2: "NC", 3: "CE"}                  # as the old read_mission
    assert M.MISSION_TYPE == {1: "photograph", 2: "destroy", 3: "supply drop", 4: "landing", 5: "intercept (5)",
                              6: "intercept (6)", 7: "intercept (7)", 8: "intercept (8)"}
