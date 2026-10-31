"""f19.gamedata.mission_gen: START decoding vs f19.memory.mission, the generator vs what the pre-WP-4 mission_finder produced
(digest recorded 2026-10-30 from the frozen copy before it was retired), seed search, and the live selection sequence against a
fake emulator (no game, no socket).

The generator tests need unicorn (uv run --group missions pytest tests/unit/memory) and a START memory image."""
import hashlib
import json
import struct

import pytest

import product
from f19.gamedata import mission_gen as G
from f19.memory import addresses as A
from f19.memory import mission as M

S = product.settings()
IMAGES = [p for p in (S.logs / "plan" / "northcape" / "briefing_mem.bin", S.logs / "tmp" / "briefing_mem.bin") if p.exists()]
need_img = pytest.mark.skipif(not IMAGES or not (S.game_dir / "CE.WLD").exists(), reason="no START image / game install")


def test_constants_agree_with_the_address_table():
    assert (G.BLOCK, G.BLOCK_LEN, G.DEADLINE, G.START_RECS, G.PILOT_PTR) == (
        A.START_BLOCK, A.START_BLOCK_LEN, A.START_DEADLINE, A.START_RECS, A.START_PILOT_PTR)
    assert (G.SEED, G.NEW_FLAG, G.COMM_PTR, G.FUEL_EST, G.DGROUP) == (
        A.START_SEED, A.START_NEW_FLAG, A.START_COMM_PTR, A.COMM_FUEL_EST, A.START_DGROUP)
    assert (G.NEW_MISSION, G.NEW_MISSION_RET, G.AFTER_GENERATE, G.AFTER_GENERATE_END, G.GENERATE_CLEAR) == (
        A.START_GENERATE, A.START_GENERATE_RET, A.START_AFTER_GENERATE, A.START_AFTER_GENERATE_END, A.START_GENERATE_CLEAR)
    assert (G.CLOCK_START, G.CLOCK_DEADLINE) == (A.START_CLOCK_START, A.START_CLOCK_DEADLINE)
    assert G.THEATER_CODE == M.THEATER_INDEX and G.MISSION_TYPE == M.MISSION_TYPE


def test_rand_inverse():
    s = 0x12345678
    assert G.rand_back(G.rand_step(s, 1000), 1000) == s
    assert G.rand_step(0) == 2531011


def test_spec():
    m = dict(bucket="x10", primary=dict(type=2, mission=3), secondary=dict(type=1, mission=14), time_limit=False)
    assert G.Spec("x10", "destroy", "photo", no_time_limit=True).match(m)
    assert G.Spec("x9+").match(m) and not G.Spec("x11").match(m) and not G.Spec(secondary_missions=[3]).match(m)
    with pytest.raises(ValueError): G.Spec("x12")
    assert str(G.Spec()) == "any mission" and G.bucket(9000) == "x10" and G.bucket(6999) == "x8"


@need_img
@pytest.mark.parametrize("img", IMAGES, ids=lambda p: p.parent.name)
def test_decode_agrees_with_memory_layer(img):
    from f19.gamedata import wld
    mem = img.read_bytes()
    ds = G.start_ds(mem)
    assert ds == M.start_ds(mem) is not None
    m, br = G.decode(mem, ds), M.briefing(mem, ds)
    p, s = br["blocks"]
    assert (m["primary"]["mission"], m["primary"]["record"], m["secondary"]["mission"], m["secondary"]["record"]) == (
        p["mission"], p["target"], s["mission"], s["target"])
    assert m["start"] == br["times"]["start"] and m["deadline"] == br["times"].get("deadline")
    assert G.wld_names(m["theater"]) == wld.places(m["theater"])["names"]
    assert G.block_key(mem[ds + G.BLOCK:ds + G.BLOCK + 0x24]) == G.key(m)[:4]


@need_img
@pytest.mark.parametrize("img", IMAGES[:1], ids=lambda p: p.parent.name)
def test_generator_matches_the_old_finder_and_finds_seeds(img):
    pytest.importorskip("unicorn")
    mem = img.read_bytes()
    new = G.Generator(mem)
    def legacy(m):                      # the old finder predates the targets' x, y and sa14 (the SA-14 cell filter)
        return {**m, **{r: {k: v for k, v in m[r].items() if k not in ("x", "y", "sa14")} for r in ("primary", "secondary")}}
    spec = G.Spec("x10+")
    parts = [legacy(G.decode(mem, new.ds_phys)), [legacy(new.generate()) for _ in range(3)],
             [legacy(m) for m in G.predict(new, spec, 5)[1][:1]], G.survey(new, spec, 20)[:2]]
    digest = hashlib.sha256(json.dumps(parts, sort_keys=True, default=repr).encode()).hexdigest()[:16]
    if img.parent.name == "northcape": assert digest == "770bd2e2467d8b41"        # decode, 3 generate()s, predict, survey of the old finder
    seed, m = G.find_seed(spec, new, tries=200)
    assert spec.match(m) and new.from_seed(seed) == m                       # reproducible
    assert new.seed() == struct.unpack_from("<I", mem, new.ds_phys + G.SEED)[0]   # image restored
    assert G.find_seed(G.Spec(secondary_missions=[999]), new, tries=3) is None


class FakeStart:
    """An emulator stand-in for select_by_seed: START's flag, seed and briefing block in a bytearray; Enter #1 sets
    ds:CA6A, Enter #3 'runs the generator' (writes the predicted block, clears the flag)."""
    def __init__(self, ds, block):
        self.mem, self.ds, self.block, self.enters, self.pokes = bytearray(0xA0000), ds, block, 0, []
    def peek(self, a, n): return bytes(self.mem[a:a + n])
    def poke(self, a, data, verify=True): self.mem[a:a + len(data)] = data; self.pokes.append((a, bytes(data)))
    def tap(self, k, hold=0.05):
        assert k == "enter"
        self.enters += 1
        if self.enters == 1: self.mem[self.ds + G.NEW_FLAG] = 1
        if self.enters == 3:
            self.mem[self.ds + G.BLOCK:self.ds + G.BLOCK + 0x24] = self.block; self.mem[self.ds + G.NEW_FLAG] = 0


class FakeReader:
    def mission(self): return "MISSION"


def _block(pm, pt, sm, st):
    b = bytearray(0x24)
    struct.pack_into("<H", b, 2, pt); struct.pack_into("<H", b, 8, pm)
    struct.pack_into("<H", b, 0x14, st); struct.pack_into("<H", b, 0x1A, sm)
    return bytes(b)


def test_select_by_seed_sequence(monkeypatch):
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    ds = 0x3D2A0
    want = dict(primary=dict(mission=13, record=40), secondary=dict(mission=14, record=41), fuel_estimate=9000)
    emu = FakeStart(ds, _block(13, 40, 14, 41))
    ok, m = G.select_by_seed(emu, FakeReader(), 0xDEADBEEF, want, ds_phys=ds, log=lambda *a: None)
    assert ok and m == "MISSION" and emu.pokes == [(ds + G.SEED, struct.pack("<I", 0xDEADBEEF))] and emu.enters == 3
    emu = FakeStart(ds, _block(13, 40, 2, 7))
    assert not G.select_by_seed(emu, FakeReader(), 1, want, ds_phys=ds, log=lambda *a: None)[0]
    emu = FakeStart(ds, b""); emu.mem[ds + G.NEW_FLAG] = 1
    with pytest.raises(RuntimeError, match="already 1"):
        G.select_by_seed(emu, FakeReader(), 1, want, ds_phys=ds)
    emu = FakeStart(ds, b""); emu.tap = lambda k, h=0: None              # the Enter didn't land
    with pytest.raises(RuntimeError, match="did not register"):
        G.select_by_seed(emu, FakeReader(), 1, want, ds_phys=ds, log=lambda *a: None)


def test_select_from_roster_sequence(monkeypatch):
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    ds = 0x3D2A0
    want = dict(primary=dict(mission=13, record=40), secondary=dict(mission=14, record=41), fuel_estimate=9000)
    emu = FakeStart(ds, _block(13, 40, 14, 41)); emu.mem[ds + G.NEW_FLAG] = 1; emu.enters = 1   # the roster: flag 1
    ok, m = G.select_from_roster(emu, FakeReader(), 0xDEADBEEF, want, ds_phys=ds, log=lambda *a: None)
    assert ok and m == "MISSION" and emu.pokes == [(ds + G.SEED, struct.pack("<I", 0xDEADBEEF))] and emu.enters == 3
    emu = FakeStart(ds, b"")                                                   # flag 0: not on the roster
    with pytest.raises(RuntimeError, match="not on the roster"):
        G.select_from_roster(emu, FakeReader(), 1, want, ds_phys=ds)
    assert emu.enters == 0 and emu.pokes == []                                 # nothing sent or written


def _m(pt, st, bucket="x9", limit=False, sa=(False, False)):
    return dict(bucket=bucket, time_limit=limit, primary=dict(type=pt, sa14=sa[0]), secondary=dict(type=st, sa14=sa[1]))


def test_same_task_spec():
    sp = G.SameTaskSpec(no_time_limit=True, length="x9+", sa14_primary=False)
    assert sp.match(_m(1, 1)) and sp.match(_m(2, 2, "x11"))                  # photo+photo, destroy+destroy
    assert not sp.match(_m(1, 2)) and not sp.match(_m(3, 3)) and not sp.match(_m(5, 5))   # mixed, supply, intercept
    assert not sp.match(_m(1, 1, limit=True)) and not sp.match(_m(1, 1, "x8"))           # timed / too short
    assert not sp.match(_m(1, 1, sa=(True, False)))
    assert G.SameTaskSpec(kinds=("photo",)).match(_m(1, 1)) and not G.SameTaskSpec(kinds=("photo",)).match(_m(2, 2))
    with pytest.raises(ValueError): G.SameTaskSpec(kinds=("supply",))
    assert "both targets photo/destroy" in str(sp) and "no time limit" in str(sp)


NC_IMAGE = S.logs / "plan" / "northcape" / "briefing_mem.bin"


@pytest.mark.slow
@pytest.mark.skipif(not NC_IMAGE.exists(), reason="no North Cape briefing image")
def test_generator_reproduces_the_dealt_mission():
    """The real game's mission in a briefing image is reproduced: 2413 rand draws back from the image's state, the
    emulated generator deals the image's mission and ends on the image's rand state (~60 s; the CE image in
    logs/tmp reproduces 1495 draws back, ~5 min, checked 2026-10-25)."""
    pytest.importorskip("unicorn")
    r = G.verify_image(NC_IMAGE.read_bytes(), max_back=2500)
    assert r is not None and r[0] == 2413
