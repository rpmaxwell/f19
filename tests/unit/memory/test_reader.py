"""f19.memory.reader against the pre-WP-4 f19_state (frozen copy), on the replay fixture memlogs.

Both read the same snapshot: the legacy readers through a SnapDB-style fake (the old `_send`/`_recv_line` line API, as
tests/replay/harness.py and nav_tui use), the new ones through the same fake and through ReplayReader (peek_many).
"""
import dataclasses
import json
import struct
import sys
from pathlib import Path

import pytest

import product
import f19_state_legacy as L
from f19.core.types import GameState, ThreatKind
from f19.memory import addresses as A
from f19.memory import reader as R
from f19.memory.reader import MemoryReader, ReplayReader, SnapshotMemory

FIX = product.settings().data / "fixtures" / "replay" / "logs" / "memlog"
RUNS = sorted(p for p in FIX.glob("*") if (p / "windows.json").exists()) if FIX.exists() else []
need = pytest.mark.skipif(not RUNS, reason=f"no replay fixtures under {FIX}")


class LineSnapDB:
    """tests/replay/harness.SnapDB's PEEK side: the old line API only."""
    def __init__(self, windows): self.windows, self.raw, self.q = windows, b"", []
    def load(self, raw): self.raw = raw
    def _read(self, phys, n):
        out, pos = bytearray(n), 0
        for _, lo, hi in self.windows:
            if lo <= phys < hi or lo < phys + n <= hi:
                a, b = max(lo, phys), min(hi, phys + n)
                out[a - phys:b - phys] = self.raw[pos + a - lo:pos + b - lo]
            pos += hi - lo
        return bytes(out)
    def _send(self, cmd):
        _, addr, n = cmd.split(); seg, off = addr.split(":")
        self.q.append("PEEK-REPLY " + self._read(int(seg, 16) * 16 + int(off, 16), int(n)).hex())
    def _recv_line(self): return self.q.pop(0)
    def peek(self, seg, off, n): return self._read(int(seg, 16) * 16 + int(off, 16), n)


READERS = ["read_state", "read_flight", "read_status", "read_alerts", "read_aircraft", "read_site_records",
           "read_waypoints", "read_sensitivity", "read_trackcam", "read_gun", "read_sites", "read_briefing",
           "read_projectiles", "read_event_list", "read_events"]


def _snaps(run, every=25):
    w = json.loads((run / "windows.json").read_text())
    files = sorted(run.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))
    return w, files[::every]


@need
@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_dict_readers_match_legacy(run):
    w, files = _snaps(run)
    windows = [tuple(x) for x in w["windows"]]
    old, new = LineSnapDB(windows), LineSnapDB(windows)
    rr = ReplayReader(run)
    for f in files:
        raw = f.read_bytes(); old.load(raw); new.load(raw); rr.emu.load(raw)
        for d, cache in ((old, L._offset), (new, R._offset)): cache[id(d)] = w["offset"]
        L._events.pop(id(old), None); R._events.pop(id(new), None); R._events.pop(id(rr.emu), None)
        for name in READERS:
            want = getattr(L, name)(old)
            assert getattr(R, name)(new) == want, (f.name, name)
            assert getattr(R, name)(rr.emu) == want, (f.name, name, "peek_many path")
        assert R.read_time_mult(rr.emu) == (lambda m: float(m[0]) if m and 1 <= m[0] <= 8 else 1.0)(
            L._peek(old, 0x410AA + w["offset"], 1))
        assert R.read_frame(rr.emu) == struct.unpack("<H", L._peek(old, L.DS + 0x5520 + w["offset"], 2))[0]
        # MissileWatch's accessor: the same bytes for every address missile_game.parse may ask for
        g_old, g_new = _legacy_world_get(old), R.missile_world_memory(rr.emu)
        for a, n in A.MISSILE_WORLD_DS + [(0x80AA, 16 * 40), (0x8854, 0x24 * 20), (0x85FC, 256), (0x9000, 4)]:
            assert g_new(a, n) == g_old(a, n), (f.name, hex(a))


def _legacy_world_get(db):
    """The `get` that the legacy read_missile_world hands to missile_game.parse (captured with a stub parse)."""
    import types
    got = {}
    stub = types.SimpleNamespace(parse=lambda get: got.setdefault("get", get), TARGETS=0x80AA, SIMOBJ=0x8854, GRID=0x85FC)
    real = sys.modules.get("missile_game")
    sys.modules["missile_game"] = stub
    try: L.read_missile_world(db)
    finally:
        if real is not None: sys.modules["missile_game"] = real
        else: sys.modules.pop("missile_game")
    return got["get"]


@need
@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_snapshot_equals_from_dicts_of_legacy(run):
    w, files = _snaps(run, every=40)
    old = LineSnapDB([tuple(x) for x in w["windows"]]); L._offset[id(old)] = w["offset"]
    rr = ReplayReader(run)
    n = 0
    for f in files:
        raw = f.read_bytes(); old.load(raw); rr.emu.load(raw)
        st = L.read_state(old, tries=1)
        if not st["x"]: continue
        want = GameState.from_dicts(st, L.read_flight(old), L.read_status(old), L.read_alerts(old), L.read_aircraft(old),
                                    L.read_site_records(old), L.read_waypoints(old), L.read_trackcam(old),
                                    L.read_sensitivity(old))
        assert rr.snapshot() == want, f.name
        n += 1
    assert n > 0


@need
def test_replay_reader_iterates():
    rr = ReplayReader(RUNS[0])
    ts = rr.times()
    assert len(rr) == len(ts) > 0 and ts == sorted(ts)
    rr.seek(ts[len(ts) // 2] + 0.01); assert rr.t == ts[len(ts) // 2]
    states = list(zip(range(5), rr.states()))
    assert states and all(isinstance(s, GameState) and s.pos.x for _, (t, s) in states)
    s = states[0][1][1]
    assert any(th.kind is ThreatKind.SITE for th in s.threats)
    assert rr.offset() == json.loads((RUNS[0] / "windows.json").read_text())["offset"]


def test_snapshot_memory_overlap():
    m = SnapshotMemory([("a", 0x100, 0x140), ("b", 0x200, 0x210)], bytes(range(0x40)) + bytes(range(100, 116)))
    assert m.peek(0x13E, 4) == bytes([0x3E, 0x3F, 0, 0])
    assert m.peek(0x1F0, 0x40)[0x10:0x20] == bytes(range(100, 116))     # a request spanning a whole window
    assert m.peek_many([(0x200, 2), (0x300, 1)]) == [bytes([100, 101]), b"\0"]


def test_find_offset_scans_and_caches():
    mem = bytearray(0xA0000); shift = 0x10420
    mem[A.SIG_OLD + shift:A.SIG_OLD + shift + len(A.SIGNATURE)] = A.SIGNATURE
    m = SnapshotMemory([("all", 0, 0xA0000)], bytes(mem))
    assert R.find_offset(m) == shift and R._offset[id(m)] == shift
    assert MemoryReader(m).offset() == shift
    with pytest.raises(RuntimeError, match="not found"):
        R.find_offset(SnapshotMemory([("all", 0, 0xA0000)], bytes(0xA0000)))


def test_addresses_aliases():
    assert A.AIRCRAFT == A.SIMOBJ == A.ds(0x8854)
    assert A.ALT == A.ds(0x46E0) and A.HDG == A.ds(0x46DA) and A.FRAME == A.ds(0x5520)
    for name in ("ALT", "SPD", "HDG", "POS", "WPT", "WPIDX", "SENS", "PITCH", "FLAGS", "TARGETS", "CLOCK", "FUEL",
                 "EVENTS", "N_EVENTS", "SIMOBJ", "AIRCRAFT", "NET_ALERT", "FRAME_SCALE", "GUN_SLOTS", "TRACKCAM_MODE"):
        assert getattr(A, name) == getattr(L, name), name


def test_f19_state_shim_reexports():
    import f19_state as S
    assert S._offset is R._offset and S.read_state is R.read_state and S.AIRCRAFT == S.SIMOBJ
    assert S.read_missile_world.__module__ == "f19_state"


def test_find_events_retries_a_miss(monkeypatch):
    """2026-10-29: a miss was cached for good, so a first read on the runway (list still empty) made read_events return []
    all flight and the photographed record (type 10) was never seen. A miss is now retried every EVENT_RETRY_S."""
    lo, hi = R.A.EVENT_SCAN_LO, R.A.EVENT_SCAN_HI
    mem = bytearray(hi - lo)
    monkeypatch.setattr(R, "_peek_many", lambda db, reqs: [bytes(mem[a - lo:a - lo + n]) for a, n in reqs])
    clock = [100.0]; monkeypatch.setattr(R.time, "monotonic", lambda: clock[0])
    db = object(); R._events.pop(id(db), None)
    assert R.find_events(db) is None and R.read_events(db) == []
    rec = bytes.fromhex("08002000" "1e44" "09004000" "1f40") + bytes.fromhex("0a80" "0100")[:0]
    mem[0x1234:0x1234 + len(rec)] = rec
    mem[0x1234 + 12:0x1234 + 18] = bytes.fromhex("8a01e005" "8181")        # type 10 | primary, target 1, t=1504
    clock[0] += 1.0
    assert R.find_events(db) is None                                           # inside the retry window: not rescanned
    clock[0] += R.EVENT_RETRY_S
    assert R.find_events(db) == lo + 0x1234
    assert (10, 0x80, 1, 1504) in R.read_events(db)
    R._events.pop(id(db), None)
