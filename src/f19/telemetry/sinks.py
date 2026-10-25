"""Sinks: where TelemetryFrames go. Every sink is non-blocking (BackgroundWriter) and never raises into the caller.

  CsvSink      logs/nav/<id>.csv + <id>.events.txt, byte-compatible with scripts/nav.py (navcsv.py has the format)
  MemlogSink   logs/memlog/<id>/windows.json + snap_<t>.bin, byte-compatible with nav.MemLog. It writes the bytes the
               caller hands it (TelemetryFrame.snapshot); reading memory is not telemetry's job.
  TelemetryBus publish(frame) -> every registered sink; one sink's failure never reaches the others or the caller.

Queue full: the frame is dropped (stats.dropped_full); block=True waits instead (offline writing only).
Error rules, as nav: after the first failed CSV write the CSV stops (_SafeLog.ok = False; later rows are counted in
dropped_after_error), event lines and snapshot files keep trying one by one (nav: `except OSError: pass`).
"""
import csv
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from f19.telemetry.frame import MemlogLayout, TelemetryFrame
from f19.telemetry.navcsv import NAV_COLUMNS, Event, format_row
from f19.telemetry.writer import BackgroundWriter, SinkStats


@runtime_checkable
class TelemetrySink(Protocol):
    stats: SinkStats

    def write(self, frame: TelemetryFrame) -> None: ...
    def flush(self, timeout: float | None = None) -> bool: ...
    def close(self, timeout: float = 10.0) -> bool: ...


class _Sink:
    def __init__(self, name, maxsize, on_close, block):
        self.stats = SinkStats()
        self._w = BackgroundWriter(name, maxsize, self.stats, on_close, block)

    def flush(self, timeout=None): return self._w.flush(timeout)
    def close(self, timeout=10.0): return self._w.close(timeout)
    def __enter__(self): return self
    def __exit__(self, *a): self.close()


class CsvSink(_Sink):
    """The nav CSV (+ events file). columns: NAV_COLUMNS (today's 32), EXTENDED_COLUMNS, or a legacy file's header.
    events_path None: no events file (events in frames are then dropped and counted as invalid)."""

    def __init__(self, csv_path, events_path=None, columns=NAV_COLUMNS, maxsize=4096, block=False):
        self.csv_path, self.events_path = Path(csv_path), None if events_path is None else Path(events_path)
        self.columns = tuple(columns)
        self._csv = self._ev = self._cw = None
        self._csv_ok = True
        super().__init__(f"csv-sink {self.csv_path.name}", maxsize, self._close_files, block)
        self._w.submit(self._open)

    # writer thread ------------------------------------------------------------------------------------------------
    def _open(self):
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            self._csv = open(self.csv_path, "w", newline="", buffering=1)
            self._cw = csv.writer(self._csv)
            self._cw.writerow(self.columns)
        except Exception:
            self._csv_ok = False
            raise
        finally:
            if self.events_path is not None:
                try:
                    self.events_path.parent.mkdir(parents=True, exist_ok=True)
                    self._ev = open(self.events_path, "w", buffering=1)
                except OSError: self._ev = None

    def _row(self, values, width):
        if not self._csv_ok: self.stats.inc("dropped_after_error"); return False
        try: self._cw.writerow(format_row(values, self.columns, width))
        except Exception:
            self._csv_ok = False
            raise

    def _event(self, ev):
        if self._ev is None:
            if self.events_path is None: self.stats.inc("dropped_invalid"); return False
            raise OSError(f"events file not open: {self.events_path}")
        self._ev.write(ev.line() + "\n")

    def _close_files(self):
        for f in (self._csv, self._ev):
            if f is not None:
                try: f.close()
                except OSError: pass

    # caller -------------------------------------------------------------------------------------------------------
    def write(self, frame):
        try:
            if frame.has_row: self._w.submit(self._row, frame.values(), frame.row_width)
            for ev in frame.events: self.event(ev)
        except Exception as e:                   # noqa: BLE001 - a bad frame is counted, never raised
            self.stats.error(e)

    def event(self, ev, t=None):
        """An events-file line: an Event, or a message with its epoch t (nav's phase_event(f"{now:.0f} {m}"))."""
        if not isinstance(ev, Event): ev = Event(t, str(ev))
        self._w.submit(self._event, ev)

    def row(self, values, width=None):
        """A raw row (dict column -> typed value), bypassing TelemetryFrame."""
        self._w.submit(self._row, dict(values), width)

    @property
    def ok(self): return self._csv_ok


class MemlogSink(_Sink):
    """A memlog run directory: windows.json from `layout` at once, then one snap_<t>.bin per TelemetryFrame.snapshot
    (or write_snapshot). A snapshot whose size is not layout.size is dropped (dropped_invalid)."""

    def __init__(self, directory, layout: MemlogLayout, maxsize=256, block=False):
        self.dir, self.layout = Path(directory), layout
        super().__init__(f"memlog-sink {self.dir.name}", maxsize, None, block)
        self._w.submit(self._open)

    def _open(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "windows.json").write_text(self.layout.to_json())

    def _snap(self, snap):
        (self.dir / snap.name).write_bytes(snap.data)

    def write(self, frame):
        if frame.snapshot is not None: self.write_snapshot(frame.snapshot)

    def write_snapshot(self, snap):
        try:
            if len(snap.data) != self.layout.size: self.stats.inc("dropped_invalid"); return
            self._w.submit(self._snap, snap)
        except Exception as e:                   # noqa: BLE001
            self.stats.error(e)


class TelemetryBus:
    """Fan-out: publish(frame) hands the frame to every registered sink, in registration order. A sink that raises
    is counted in failures[sink] and skipped for that frame only. Thread-safe (register / publish from any thread)."""

    def __init__(self, sinks=()):
        self._lock = threading.Lock()
        self._sinks = tuple(sinks)
        self.failures = {}

    @property
    def sinks(self): return self._sinks

    def register(self, sink):
        with self._lock: self._sinks = self._sinks + (sink,)
        return sink

    def unregister(self, sink):
        with self._lock: self._sinks = tuple(s for s in self._sinks if s is not sink)

    def publish(self, frame):
        for s in self._sinks:                    # a snapshot of the tuple: register() never disturbs this loop
            try: s.write(frame)
            except Exception:                    # noqa: BLE001 - isolated
                with self._lock: self.failures[s] = self.failures.get(s, 0) + 1

    def event(self, ev, t=None):
        """Publish one event as its own frame."""
        if not isinstance(ev, Event): ev = Event(t, str(ev))
        self.publish(TelemetryFrame(ev.t if ev.t is not None else 0.0, events=(ev,)))

    def flush(self, timeout=None):
        return all([self._call(s, "flush", timeout) for s in self._sinks])

    def close(self, timeout=10.0):
        return all([self._call(s, "close", timeout) for s in self._sinks])

    def _call(self, s, name, timeout):
        try: return bool(getattr(s, name)(timeout))
        except Exception:                        # noqa: BLE001
            with self._lock: self.failures[s] = self.failures.get(s, 0) + 1
            return False
