"""Sinks never block and never raise: queue-full drops, write errors (a full disk), thread safety, bus isolation,
LiveSource."""
import csv
import errno
import threading
import time

from f19.telemetry import (NAV_COLUMNS, BackgroundWriter, CsvSink, Event, LiveSource, MemlogLayout, MemlogSink,
                           Snapshot, TelemetryBus, TelemetryFrame)

LAYOUT = MemlogLayout(0, [("a", 0x100, 0x140), ("b", 0x400, 0x410)])


def row(t, **kw): return TelemetryFrame(t, nav={"x": 1.0, "y": 2.0, "alt": 3, **kw})


class DiskFull:
    def write(self, s): raise OSError(errno.ENOSPC, "No space left on device")
    def close(self): pass


def test_queue_full_drops_without_blocking():
    gate = threading.Event()
    w = BackgroundWriter(maxsize=2)
    w.submit(gate.wait)                         # the writer thread is stuck on this op
    time.sleep(0.05)
    t0 = time.monotonic()
    ok = [w.submit(lambda: None) for _ in range(10)]
    assert time.monotonic() - t0 < 0.5
    assert ok[:2] == [True, True] and not any(ok[2:])
    assert w.stats.dropped_full == 8
    gate.set(); assert w.flush(5) and w.close(5)
    assert w.stats.written == 3 and not w.submit(lambda: None) and w.stats.dropped_closed == 1


def test_csv_stops_after_first_error_like_safelog(tmp_path):
    s = CsvSink(tmp_path / "1.csv", tmp_path / "1.events.txt")
    s.write(row(1.0)); s.flush(5)
    s._cw = csv.writer(DiskFull())              # the disk fills up
    for k in range(5): s.write(row(2.0 + k))
    s.event("still trying", t=3.0)
    assert s.flush(5)
    assert not s.ok and s.stats.errors == 1 and s.stats.dropped_after_error == 4
    s._ev = DiskFull()                           # events keep trying one by one (nav: except OSError: pass)
    for k in range(3): s.event(f"lost {k}", t=4.0)
    assert s.close(5) and s.stats.errors == 4
    lines = (tmp_path / "1.csv").read_text().splitlines()
    assert lines[0] == ",".join(NAV_COLUMNS) and len(lines) == 2
    assert (tmp_path / "1.events.txt").read_text() == "3 still trying\n"


def test_unwritable_paths_never_raise(tmp_path):
    (tmp_path / "file").write_text("")
    c = CsvSink(tmp_path / "file" / "x.csv", tmp_path / "file" / "x.events.txt")    # parent is a file
    c.write(row(1.0)); c.event("e", t=1.0)
    m = MemlogSink(tmp_path / "file" / "mem", LAYOUT)
    m.write(TelemetryFrame(1.0, snapshot=Snapshot(1.0, bytes(LAYOUT.size))))
    assert c.close(5) and m.close(5)
    assert c.stats.errors >= 2 and c.stats.written == 0 and m.stats.errors == 2 and m.stats.written == 0


def test_memlog_sink_rejects_wrong_size(tmp_path):
    m = MemlogSink(tmp_path / "run", LAYOUT)
    m.write_snapshot(Snapshot(5.0, b"\1" * 3))
    m.write_snapshot(Snapshot(5.004, b"\2" * LAYOUT.size))
    assert m.close(5) and m.stats.dropped_invalid == 1
    assert sorted(p.name for p in (tmp_path / "run").iterdir()) == ["snap_5.00.bin", "windows.json"]
    assert (tmp_path / "run" / "windows.json").read_text() == LAYOUT.to_json()


def test_bad_frame_counted_not_raised(tmp_path):
    s = CsvSink(tmp_path / "1.csv")
    s.write(object())                           # not a frame at all
    s.write(TelemetryFrame(1.0, nav={"x": object()}))      # format error happens in the writer thread
    assert s.close(5) and s.stats.errors == 2


def test_threads_publish_concurrently(tmp_path):
    s = CsvSink(tmp_path / "1.csv", tmp_path / "1.events.txt", block=True, maxsize=64)
    bus = TelemetryBus([s])
    N, M = 8, 500

    def pub(k):
        for i in range(M):
            bus.publish(TelemetryFrame(float(i), nav={"x": float(k), "y": float(i), "press": f"thread {k}, row {i}"},
                                       events=(Event(float(i), f"t{k} e{i}"),) if i % 50 == 0 else ()))
    th = [threading.Thread(target=pub, args=(k,)) for k in range(N)]
    [t.start() for t in th]; [t.join() for t in th]
    assert bus.close(10)
    rows = list(csv.DictReader(open(tmp_path / "1.csv", newline="")))
    assert len(rows) == N * M and s.stats.dropped == 0
    assert {(r["x"], r["y"]) for r in rows} == {(str(k), str(i)) for k in range(N) for i in range(M)}
    assert all(r["press"] == f"thread {r['x']}, row {r['y']}" for r in rows)        # no torn rows
    assert len((tmp_path / "1.events.txt").read_text().splitlines()) == N * M // 50


def test_bus_isolates_failing_sink():
    class Boom:
        def write(self, f): raise RuntimeError("sink bug")
        def flush(self, timeout=None): raise RuntimeError
        def close(self, timeout=10.0): return True
    live, boom = LiveSource(), Boom()
    bus = TelemetryBus([boom, live])
    for i in range(3): bus.publish(row(float(i)))
    assert bus.failures[boom] == 3 and not bus.flush(1)
    bus.close()
    assert [f.t for f in live] == [0.0, 1.0, 2.0]


def test_live_source_iterates_in_another_thread():
    live, got = LiveSource(maxsize=4), []
    th = threading.Thread(target=lambda: got.extend(f.t for f in live)); th.start()
    for i in range(3): live.write(row(float(i))); time.sleep(0.01)
    live.close(); th.join(5)
    assert got == [0.0, 1.0, 2.0] and not th.is_alive()
    full = LiveSource(maxsize=2)
    for i in range(5): full.write(row(float(i)))           # nobody reads: never blocks
    assert full.stats.dropped_full == 3
    full.close(); assert [f.t for f in full] == [0.0, 1.0]  # queued frames are still delivered after close
