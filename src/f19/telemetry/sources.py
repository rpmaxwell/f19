"""Sources: iterables of TelemetryFrame. A recorded run (ReplaySource) and a live flight (LiveSource) have the same
API: `for frame in source: ...`, then `source.close()`.

  NavLogSource   a nav CSV (+ events file): one frame per row (nav values exact, a core Frame rebuilt from them),
                 one per event line
  MemlogSource   a memlog run dir: MemlogLayout + one Snapshot per snap_<t>.bin, in time order
  ReplaySource   both merged by time (each file's own order kept), optionally paced like nav_tui --replay
  LiveSource     a sink (register it on the TelemetryBus) that a consumer iterates; bounded, drops when full

Nearest-epoch matching (nearest_epoch): a memlog run and its nav log have different ids (nav opens its CSV at start,
MemLog its directory ~12 s later after connecting); memlog_load.py and missile_replay.py pair them by the nearest
nav CSV epoch, unbounded. Here the same rule, with an optional bound.
"""
import csv
import heapq
import queue
import time
from pathlib import Path

from f19.telemetry.frame import MemlogLayout, Snapshot, TelemetryFrame, frame_from_values
from f19.telemetry.navcsv import EXTENDED_COLUMNS, Event, parse_row
from f19.telemetry.writer import SinkStats


def _epoch(name):
    try: return int(name)
    except ValueError: return None


def nearest_epoch(run_id, candidates, max_s=None):
    """The candidate id (numeric str / int) whose epoch is nearest run_id's (ties: the first), or None."""
    r = int(run_id)
    best = min(((abs(e - r), i, c) for i, c in enumerate(candidates) if (e := _epoch(str(c))) is not None), default=None)
    if best is None or (max_s is not None and best[0] > max_s): return None
    return best[2]


def nav_logs(logs_dir):
    """{id: csv path} of logs/nav/<epoch>.csv (the glob memlog_load uses: nav/[0-9]*.csv, numeric stems only)."""
    return {p.stem: p for p in Path(logs_dir, "nav").glob("[0-9]*.csv") if p.stem.isdigit()}


def memlog_runs(logs_dir):
    return {p.name: p for p in Path(logs_dir, "memlog").glob("[0-9]*") if p.name.isdigit() and p.is_dir()}


# ------------------------------------------------------------------------------------------------------- nav CSV
class NavLogSource:
    def __init__(self, csv_path, events_path="auto", frames=True):
        """events_path "auto": <stem>.events.txt next to the CSV if it exists. frames: rebuild a core Frame per row."""
        self.csv_path = Path(csv_path)
        if events_path == "auto":
            e = self.csv_path.with_name(self.csv_path.stem + ".events.txt")
            events_path = e if e.exists() else None
        self.events_path = None if events_path is None else Path(events_path)
        self.build_frames = frames
        with open(self.csv_path, newline="") as f: self.columns = tuple(next(csv.reader(f), ()))

    def rows(self):
        """TelemetryFrames, one per CSV row, in file order. frame.nav holds every EXTENDED column: the file's values,
        None for columns this log doesn't have (so a sink with more columns writes them blank)."""
        with open(self.csv_path, newline="") as f:
            rd = csv.reader(f); cols = tuple(next(rd, ()))
            missing = [c for c in EXTENDED_COLUMNS if c not in cols]
            for cells in rd:
                vals, width = parse_row(cells, cols)
                for c in missing: vals[c] = None     # not in this (older) log: unknown, never derived from the Frame
                t = vals.get("t")
                yield TelemetryFrame(t if isinstance(t, float) else 0.0,
                                     frame_from_values(vals) if self.build_frames else None, nav=vals,
                                     route_heading=_f(vals.get("route_want")), behavior=_s(vals.get("behavior")),
                                     plan_version=_i(vals.get("plan_version")), row_width=width)

    def events(self):
        """Event per line of the events file, in file order."""
        if self.events_path is None: return
        text = self.events_path.read_text()
        lines = text.split("\n")
        if lines and lines[-1] == "": lines.pop()
        for ln in lines: yield Event.parse(ln)

    def event_frames(self):
        last = 0.0
        for ev in self.events():
            last = ev.t if ev.t is not None else last
            yield TelemetryFrame(last, events=(ev,))

    def __iter__(self):
        return heapq.merge(self.rows(), self.event_frames(), key=lambda fr: fr.t)


def _f(v): return v if isinstance(v, float) else None
def _i(v): return v if isinstance(v, int) else None
def _s(v): return v if isinstance(v, str) else None


# -------------------------------------------------------------------------------------------------------- memlog
class MemlogSource:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.layout = MemlogLayout.from_json((self.dir / "windows.json").read_text())
        self.files = sorted(self.dir.glob("snap_*.bin"), key=lambda p: float(p.stem[5:]))   # as nav_tui.replay

    def __len__(self): return len(self.files)

    def times(self): return [float(p.stem[5:]) for p in self.files]

    def snapshots(self):
        for p in self.files: yield Snapshot(float(p.stem[5:]), p.read_bytes())

    def __iter__(self):
        for s in self.snapshots(): yield TelemetryFrame(s.t, snapshot=s)


# -------------------------------------------------------------------------------------------------------- replay
class ReplaySource:
    """A recorded run as one time-ordered stream of TelemetryFrames (rows, events, snapshots as separate frames).
    speed: None = as fast as possible; s > 0 = sleep (gap between frame times) / s, like nav_tui --replay."""

    def __init__(self, nav: NavLogSource | None = None, memlog: MemlogSource | None = None, speed=None, sleep=time.sleep):
        self.nav, self.memlog, self.speed, self._sleep = nav, memlog, speed, sleep
        self._closed = False

    @property
    def layout(self): return self.memlog.layout if self.memlog else None

    def __iter__(self):
        its = [s for s in (self.nav, self.memlog) if s is not None]
        prev = None
        for fr in heapq.merge(*its, key=lambda f: f.t):
            if self._closed: return
            if self.speed and prev is not None and fr.t > prev: self._sleep((fr.t - prev) / self.speed)
            prev = fr.t
            yield fr

    def close(self): self._closed = True


# ---------------------------------------------------------------------------------------------------------- live
class LiveSource:
    """Register on a TelemetryBus; iterate in a consumer thread (a HUD). write() never blocks: when the consumer
    falls behind by maxsize frames the newest are dropped (stats.dropped_full). Iteration ends after close(), once
    the queued frames are consumed."""
    POLL_S = 0.05

    def __init__(self, maxsize=1024):
        self.stats = SinkStats()
        self._q = queue.Queue(maxsize)
        self._closed = False

    def write(self, frame):
        if self._closed: self.stats.inc("dropped_closed"); return
        try: self._q.put_nowait(frame); self.stats.inc("queued")
        except queue.Full: self.stats.inc("dropped_full")

    def flush(self, timeout=None): return True

    def close(self, timeout=10.0):
        self._closed = True
        return True

    def get(self, timeout=None):
        """The next frame; None at the end (closed and drained) or on timeout."""
        end = None if timeout is None else time.monotonic() + timeout
        while True:
            wait = self.POLL_S if end is None else min(self.POLL_S, max(0.0, end - time.monotonic()))
            try: fr = self._q.get(timeout=wait)
            except queue.Empty:
                if self._closed or (end is not None and time.monotonic() >= end): return None
                continue
            self.stats.inc("written")
            return fr

    def __iter__(self):
        while (fr := self.get()) is not None: yield fr
