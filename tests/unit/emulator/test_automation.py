"""f19.emulator.automation against an in-process fake of the DOSBox-X automation server."""
import socket
import time

import pytest

from fake_server import FakeAutomationServer
from f19.emulator import automation as A
from f19.emulator.automation import DesyncError, Emulator, PokeError


@pytest.fixture
def server():
    s = FakeAutomationServer()
    yield s
    s.close()


@pytest.fixture
def emu(server):
    e = Emulator("127.0.0.1", server.port, timeout=2.0)
    yield e
    e.close()


def test_connect_syncs_and_skips_stale_replies(server):
    server.stale = ["PEEK-REPLY 0102", "POKE-REPLY OK 00010 1"]       # a previous client's leftovers
    server.mem[0x1000:0x1004] = b"\x11\x22\x33\x44"
    with Emulator("127.0.0.1", server.port, timeout=2.0) as e:
        assert server.log[0] == "PEEK 0:0 7"
        assert e.peek(0x1000, 4) == b"\x11\x22\x33\x44"


def test_peek_forms_and_line_format(emu, server):
    server.mem[0x40250:0x40252] = b"\x34\x12"
    assert emu.peek(0x40250, 2) == b"\x34\x12"
    assert server.log[-1] == "PEEK 4025:0 2"                     # seg = phys >> 4, off = phys & 15, as f19_state
    assert emu.peek("4025", "0", 2) == b"\x34\x12"               # old (seg, off, n) form
    assert emu.peek(0x40251, 1) == b"\x12" and server.log[-1] == "PEEK 4025:1 1"


def test_peek_many_splits_long_reads_and_batches(emu, server):
    server.mem[0x2000:0x2000 + 300] = bytes(range(256)) + bytes(range(44))
    n0 = len(server.log)
    a, b, c = emu.peek_many([(0x2000, 300), (0x2010, 2), (0x2100, 64)])
    assert a == bytes(server.mem[0x2000:0x2000 + 300]) and b == b"\x10\x11" and c == bytes(server.mem[0x2100:0x2140])
    lines = server.log[n0:]
    assert len(lines) == 5 + 1 + 1 and all(l.startswith("PEEK ") for l in lines)
    assert max(int(l.split()[-1]) for l in lines) == 64
    big = emu.peek_many([(0x3000 + 64 * k, 64) for k in range(150)])    # > BATCH: several round trips
    assert len(big) == 150 and all(len(x) == 64 for x in big)


def test_read_range(emu, server):
    server.mem[0x5000:0x5100] = bytes(range(256))
    assert emu.read(0x5000, 0x5100) == bytes(range(256))
    assert emu.read(0x5001, 0x5042) == bytes(range(1, 0x42))


def test_poke_writes_and_verifies(emu, server):
    emu.poke(0x9F000, b"\xde\xad\xbe\xef")
    assert server.mem[0x9F000:0x9F004] == b"\xde\xad\xbe\xef"
    assert "POKE 9F000 DEADBEEF" in server.log and server.log[-1] == "PEEK 9F00:0 4"   # read back in the same round trip
    data = bytes(range(100))
    emu.poke(0x9E000, data)                                           # > 64: two POKEs, two PEEKs
    assert server.mem[0x9E000:0x9E000 + 100] == data
    assert sum(l.startswith("POKE ") for l in server.log) == 3


def test_poke_refused_and_readback_mismatch(emu, server, monkeypatch):
    with pytest.raises(PokeError, match="out of guest RAM"):
        emu.poke(0xB8000, b"A")
    assert emu.peek(0x10, 1) == b"\0"                                 # connection still usable
    orig = server.handle
    def lying(line):                                                   # acknowledges but writes nothing
        if line.startswith("POKE "):
            a = int(line.split()[1], 16); n = len(line.split()[2]) // 2
            return f"POKE-REPLY OK {a:05X} {n}"
        return orig(line)
    monkeypatch.setattr(server, "handle", lying)
    with pytest.raises(PokeError, match="read back"):
        emu.poke(0x9F000, b"\x01\x02")
    emu.poke(0x9F000, b"\0\0", verify=False)                          # no read-back requested


def test_poke_on_an_old_server_times_out_cleanly(server):
    server.poke_supported = False
    with Emulator("127.0.0.1", server.port, timeout=0.5) as e:
        with pytest.raises(A.AutomationError, match="older than the POKE"):
            e.poke(0x9F000, b"\x01", verify=False)
        server.poke_supported = True
        server.mem[0x100] = 7
        assert e.peek(0x100, 1) == b"\x07"                            # resynchronised


def test_desync_guard_lost_reply(server):
    """A lost PEEK reply: the read times out, and the next call resynchronises instead of reading shifted replies."""
    server.mem[0x100:0x102] = b"\xaa\xbb"
    with Emulator("127.0.0.1", server.port, timeout=0.5) as e:
        server.drop_next = 1
        with pytest.raises(TimeoutError):
            e.peek_many([(0x100, 1), (0x101, 1)])                     # 1st reply dropped, 2nd arrives, then nothing
        assert e._dirty
        assert e.peek(0x101, 1) == b"\xbb" and not e._dirty


def test_desync_guard_wrong_length(emu, server, monkeypatch):
    orig = server.handle
    monkeypatch.setattr(server, "handle", lambda l: "PEEK-REPLY 00" if l == "PEEK 10:0 4" else orig(l))
    with pytest.raises(DesyncError, match="reply has 1 bytes"):
        emu.peek(0x100, 4)
    monkeypatch.setattr(server, "handle", orig)
    server.mem[0x100:0x104] = b"\1\2\3\4"
    assert emu.peek(0x100, 4) == b"\1\2\3\4"


def test_interrupted_batch_leftovers_are_dropped(emu, server):
    """Replies owed by an interrupted call (here: sent through the raw line API and never read) don't leak."""
    server.mem[0x200] = 0x55
    emu._exchange(["PEEK 20:0 1"], lambda i, r: r)                    # balanced: fine
    emu._owed = 2; emu.send_line("PEEK 0:0 1"); emu.send_line("PEEK 0:0 1")
    assert emu.peek(0x200, 1) == b"\x55"


def test_keys_and_out(emu, server):
    emu.key_down("left"); assert emu.held == {"left"} and emu._held == {"left"}
    emu.key_up("left"); emu.tap("f1", 0.0); emu.out(0x20, 0x20)
    emu.key_down("up"); emu.release_all()
    emu.peek(0, 1)                                                     # a round trip: everything before it arrived
    assert server.keys == [("DOWN", "left"), ("UP", "left"), ("DOWN", "f1"), ("UP", "f1"), ("DOWN", "up"), ("UP", "up")]
    assert server.outs == [(0x20, 0x20)] and emu.held == frozenset()


def test_scan_tap_and_release_arrows(emu, server, monkeypatch):
    monkeypatch.setattr(A.time, "sleep", lambda s: None)
    emu.key_down("a"); emu.key_up("a")
    emu.scan_tap(0x0D, shift=True)
    emu.peek(0, 1)
    seq = [v for p, v in server.outs if p == 0x60]
    assert seq == [0x2A, 0x0D, 0x8D, 0xAA, 0x8D, 0xAA] and all(v == 0xD2 for p, v in server.outs if p == 0x64)
    assert server.keys[-1] == ("UP", "a")                              # released keys re-released after the D2s
    server.outs.clear(); emu.release_arrows(); emu.peek(0, 1)
    assert [v for p, v in server.outs if p == 0x60] == [0xE0, 0xC8, 0xC8, 0xE0, 0xD0, 0xD0, 0xE0, 0xCB, 0xCB, 0xE0, 0xCD, 0xCD]


def test_settings_host_port(monkeypatch, server):
    from f19.core.settings import Settings
    monkeypatch.setattr(A, "_settings", Settings.from_env({"F19_AUTOMATION_PORT": str(server.port)}))
    with Emulator() as e:
        assert (e.host, e.port) == ("127.0.0.1", server.port)


def test_connect_retries(server):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); free = s.getsockname()[1]; s.close()
    t = time.time()
    with pytest.raises(OSError):
        A.connect("127.0.0.1", free, retry_s=0.6)
    assert time.time() - t >= 0.5
    with A.connect("127.0.0.1", server.port) as e: assert e.peek(0, 1) == b"\0"
