"""f19.gamedata: the EGAME/END loader, .WLD parsing, terrain decoder, mission catalog (docs/game/*.md facts)."""
import struct

import numpy as np
import pytest

from conftest import S, need
from f19.gamedata import exe, missions, terrain, wld


@pytest.fixture(scope="module")
def game():
    return need(S.game_dir / "CE.WLD", "game install (F19_GAME_DIR)").parent


# --- exe -------------------------------------------------------------------------------------------------------------
def test_unexepack_matches_stored_images(egame, game):
    for exe_name, path in (("EGAME.EXE", S.egame_bin), ("END.EXE", S.end_bin)):
        img, info = exe.unexepack((game / exe_name).read_bytes())
        assert img == need(path).read_bytes()
        assert info["size"] == len(img)
    assert exe.unexepack((game / "EGAME.EXE").read_bytes())[1]["real_ip"] == 0xE70A


def test_unexepack_rejects_unpacked(game):
    img = bytearray((game / "EGAME.EXE").read_bytes())
    cs = struct.unpack_from("<H", img, 22)[0]; hdr = struct.unpack_from("<H", img, 8)[0]
    img[hdr * 16 + cs * 16 + 12:hdr * 16 + cs * 16 + 20] = b"\0" * 8          # wipe the 'RB' signature
    with pytest.raises(ValueError):
        exe.unexepack(bytes(img))


def test_egame_ds(egame):
    assert exe.ds_base(exe.egame()) == exe.DS_FILE == 0x1ECF0
    assert exe.egame() is exe.egame()                                          # loaded once
    assert exe.egame_ds(0x4C10 + 0x12 * 0x21, 6) == b"SA-14\0"                 # weapon spec 0x21


def test_fallback_unpacks_into_data_dir(tmp_path, monkeypatch, game):
    from f19.core.settings import Settings
    monkeypatch.setattr(exe, "S", Settings(data=tmp_path, game_dir=game, host="", port=0))
    exe.end.cache_clear()
    try:
        img = exe.end()
        assert (tmp_path / "game" / "END.bin").read_bytes() == img
    finally:
        exe.end.cache_clear()


# --- wld -------------------------------------------------------------------------------------------------------------
def test_codes_and_files():
    assert wld.code("Persian Gulf") == "PG" and wld.code("libya.wld") == "LB"
    assert list(wld.WLD_FILE) == ["CE", "NC", "LB", "PG"] and wld.WLD_FILE["PG"] == "GULF.WLD"
    with pytest.raises(ValueError, match="use one of CE, NC, LB, PG"):
        wld.code("Germany")


def test_name_start(game):
    b = wld.wld_bytes("PG")
    assert b[wld.name_start(b):].startswith(b"Abbas\0")                        # plain match: second word
    assert b[wld.name_start(b, full=True):].startswith(b"Bandar Abbas\0")      # backed up over the multi-word name
    ce = wld.wld_bytes("CE")
    assert wld.name_start(ce) == wld.name_start(ce, full=True) == 2235


def test_territory_ce(game):
    g = wld.grid("CE")
    assert g.shape == (16, 16) and g.dtype == np.uint8
    assert wld.side("CE", 3392, 23288) == "friendly"                           # Ramstein (takeoff)
    assert wld.side("CE", -1, 0) == wld.side("CE", 0, 16 << 11) == "off-map"
    sides = {wld.side("CE", (i << 11) + 1, (j << 11) + 1) for i in range(16) for j in range(16)}
    assert {"friendly", "enemy"} <= sides
    assert int((g & 0x0C == 0).sum()) == 12                                     # radar.md: CE has 12 mountain cells


def test_places(game):
    d = wld.parse_wld("CE")
    assert d["file"] == "CE.WLD" and d["names"][0] == "Klaipeda"
    assert all(r["off"] % 16 == 0x38 % 16 for r in d["records"])
    named = [r for r in d["records"] if r["name"]]
    assert named and all(r["name"] == d["names"][r["name_id"] - 1] for r in named)


def test_categories(game):
    cats = wld.categories("NC")
    by_name = {n: c for n, c in cats.values()}
    assert by_name.get("Sub Pen", 0) & 0x0F == 0                               # weapons.md: Sub Pen is category 0
    obj = wld.objects_by_category("CE")
    assert all(isinstance(v, list) and v for v in obj.values())


# --- terrain ---------------------------------------------------------------------------------------------------------
def test_terrain_files(game):
    g = terrain.load_3dg("CE")
    assert [len(x) for x in g] == [256, 512, 512, 512]
    t = terrain.load_3dt("CE")
    assert len(t) == 5
    assert terrain.THEATER_INDEX == {"LB": 0, "PG": 1, "NC": 2, "CE": 3}
    assert terrain.scale_to_lod(4, 0x40) == 1 and terrain.scale_to_lod(0, 3) == 6


def test_terrain_world_ce(game):
    W = terrain.World("CE")
    assert W.objs
    H = W.heightfield(step=256)
    assert H.shape == (128, 128) and 5000 < H.max() < 20000 and (H > 0).any()


# --- missions --------------------------------------------------------------------------------------------------------
def test_unlzw_roundtrip_literals():
    # 9-bit codes LSB-first: 'A' 'B' then code 257 ('AB'): "ABAB"
    codes = [0x41, 0x42, 257]
    v = sum(c << (9 * i) for i, c in enumerate(codes))
    data = bytes([11]) + v.to_bytes(4, "little")
    assert missions.unlzw(data) == b"ABAB"


def test_briefings(game):
    t = missions.briefings()
    assert len(t) == 56 and all(t)


def test_rules_text():
    m = dict(code=2, type=0)
    assert missions.time_limit(m).startswith("yes") and missions.primary_only(m) == "yes"
    assert missions.setup(dict(type=6)) == "air-to-air" and missions.primary_only(dict(code=0, type=1)) == "no"
    assert missions.OUT.parts[-3:] == ("docs", "game", "missions.md")
