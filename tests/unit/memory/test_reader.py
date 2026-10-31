"""f19.memory.reader on the replay fixture memlogs. The expected values are digests of what the pre-WP-4 f19_state returned
on the same snapshots (recorded 2026-10-30 from the frozen copy before it was retired): every dict reader, the time multiplier
and the frame counter on every 25th snapshot, and the GameState that GameState.from_dicts built from those dicts on every
40th. The new readers run through a SnapDB-style fake (the old `_send`/`_recv_line` line API) and through ReplayReader
(peek_many)."""
import dataclasses
import hashlib
import json
import struct
from pathlib import Path

import pytest

import product
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


# run -> (digest of the dict readers, digest of the GameState, snapshots in the second), from the retired f19_state
GOLD = {"1792546698": ("a5307b1c777f7406", "98f0475c70cf7d55", 8),
        "1792609731": ("d11b0b3c0bb9fbf8", "979af32ba6cf7f72", 11),
        "1792715432": ("546c1e294ba1f7a0", "3f2e80ab11b179f6", 20)}


@need
@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_dict_readers_match_the_old_ones(run):
    w, files = _snaps(run)
    new = LineSnapDB([tuple(x) for x in w["windows"]])
    rr = ReplayReader(run)
    h = hashlib.sha256()
    for f in files:
        raw = f.read_bytes(); new.load(raw); rr.emu.load(raw)
        R._offset[id(new)] = w["offset"]
        R._events.pop(id(new), None); R._events.pop(id(rr.emu), None)
        for name in READERS: h.update(repr((f.name, name, getattr(R, name)(new))).encode())
        h.update(repr(R._peek(new, 0x410AA + w["offset"], 1)).encode())
        h.update(repr(struct.unpack("<H", R._peek(new, A.DS + 0x5520 + w["offset"], 2))[0]).encode())
        for name in READERS: assert getattr(R, name)(rr.emu) == getattr(R, name)(new), (f.name, name, "peek_many path")
        assert R.read_time_mult(rr.emu) == (lambda m: float(m[0]) if m and 1 <= m[0] <= 8 else 1.0)(R._peek(new, 0x410AA + w["offset"], 1))
        assert R.read_frame(rr.emu) == struct.unpack("<H", R._peek(new, A.DS + 0x5520 + w["offset"], 2))[0]
        # MissileWatch's accessor: the same bytes as a plain peek of each address MissileWatch may ask for
        get = R.missile_world_memory(rr.emu)
        for a, n in A.MISSILE_WORLD_DS + [(0x80AA, 16 * 40), (0x8854, 0x24 * 20), (0x85FC, 256)]:
            assert get(a, n) == rr.emu.peek(A.DS + a + w["offset"], n), (f.name, hex(a))
        assert get(0x9000, 4) == bytes(4)                                   # outside the windows it reads: zeros
    assert h.hexdigest()[:16] == GOLD[run.name][0], "a dict reader changed: its output no longer matches the old f19_state's"


@need
@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_snapshot_equals_the_old_from_dicts(run):
    w, files = _snaps(run, every=40)
    rr = ReplayReader(run)
    h, n = hashlib.sha256(), 0
    for f in files:
        rr.emu.load(f.read_bytes())
        s = rr.snapshot()
        if not s.pos.x: continue
        h.update(repr((f.name, s)).encode()); n += 1
    assert (h.hexdigest()[:16], n) == GOLD[run.name][1:], "MemoryReader.snapshot() no longer equals GameState.from_dicts of the old readers"


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


OLD_ADDRESSES = {'ALT': 262736, 'SPD': 278730, 'HDG': 262730, 'POS': 283064, 'WPT': 263114, 'WPIDX': 263196, 'SENS': 268250, 'PITCH': 262732, 'FLAGS': 271292, 'TARGETS': 277530, 'CLOCK': 270752, 'FUEL': 264768, 'EVENTS': 281014, 'N_EVENTS': 269982, 'SIMOBJ': 279492, 'AIRCRAFT': 279492, 'NET_ALERT': 282942, 'FRAME_SCALE': 264812, 'GUN_SLOTS': 278734, 'TRACKCAM_MODE': 284070}           # the constants the retired f19_state defined


def test_addresses_aliases():
    assert A.AIRCRAFT == A.SIMOBJ == A.ds(0x8854)
    assert A.ALT == A.ds(0x46E0) and A.HDG == A.ds(0x46DA) and A.FRAME == A.ds(0x5520)
    for name, value in OLD_ADDRESSES.items(): assert getattr(A, name) == value, name


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
