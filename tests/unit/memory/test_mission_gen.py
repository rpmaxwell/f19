"""f19.gamedata.mission_gen: START decoding vs f19.memory.mission, the generator vs the pre-WP-4 mission_finder
(frozen copy), seed search, and the live selection sequence against a fake emulator (no game, no socket).

The generator tests need unicorn (uv run --group missions pytest tests/unit/memory) and a START memory image."""
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
    import read_mission
    mem = img.read_bytes()
    ds = G.start_ds(mem)
    assert ds == M.start_ds(mem) is not None
    m, br = G.decode(mem, ds), M.briefing(mem, ds)
    p, s = br["blocks"]
    assert (m["primary"]["mission"], m["primary"]["record"], m["secondary"]["mission"], m["secondary"]["record"]) == (
        p["mission"], p["target"], s["mission"], s["target"])
    assert m["start"] == br["times"]["start"] and m["deadline"] == br["times"].get("deadline")
    assert G.wld_names(m["theater"]) == read_mission.WORLD.names(m["theater"])
    assert G.block_key(mem[ds + G.BLOCK:ds + G.BLOCK + 0x24]) == G.key(m)[:4]


@need_img
@pytest.mark.parametrize("img", IMAGES[:1], ids=lambda p: p.parent.name)
def test_generator_matches_legacy_and_finds_seeds(img):
    pytest.importorskip("unicorn")
    import mission_finder_legacy as L
    mem = img.read_bytes()
    new, old = G.Generator(mem), L.Generator(mem)
    assert G.decode(mem, new.ds_phys) == L.decode(mem, old.ds_phys)
    seq_new = [new.generate() for _ in range(3)]
    assert seq_new == [old.generate() for _ in range(3)]
    spec = G.Spec("x10+")
    assert G.predict(new, spec, 5)[1][:1] == L.predict(old, L.Spec("x10+"), 5)[1][:1]
    assert G.survey(new, spec, 20)[:2] == L.survey(old, L.Spec("x10+"), 20)[:2]
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
