"""Flight storage: the FlightStore protocol (no file paths in it, so a database store can replace the files) and
FileFlightStore over F19_DATA/logs in today's formats.

  store = FileFlightStore()                         # settings.logs
  run = store.new_run(Run(mission=mm, settings=vars(args)), layout=MemlogLayout(off, windows))
  bus.register(store.sink(run.id))                  # or store.append_frames(run.id, frames)
  store.add_route_version(run.id, RouteVersion(1, t, "replan", route))
  store.close_run(run.id)
  for run in store.list_runs(): ...;  rec = store.load_run(run_id);  for frame in rec.frames(): ...

Files of a run <id> (an epoch, as today):  nav/<id>.csv, nav/<id>.events.txt, memlog/<id>/, runs/<id>.json (the Run
record; new). Legacy runs have no record: nav CSVs are runs of their own and a memlog dir is paired with the nav
CSV of nearest epoch within PAIR_S (nav opened its CSV ~12 s before MemLog its directory); load_run takes either id.
"""
import dataclasses
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

from f19.core.types import Mission, Route

from f19.telemetry.frame import MemlogLayout, TelemetryFrame
from f19.telemetry.navcsv import EXTENDED_COLUMNS, Event
from f19.telemetry.sinks import CsvSink, MemlogSink
from f19.telemetry.writer import BackgroundWriter
from f19.telemetry.sources import MemlogSource, NavLogSource, ReplaySource, memlog_runs, nav_logs, nearest_epoch

PAIR_S = 120


def code_version(cwd=None):
    """`git describe --always --dirty` of the checkout this package runs from; None without git."""
    try:
        r = subprocess.run(["git", "describe", "--always", "--dirty"], cwd=cwd or Path(__file__).resolve().parent,
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# ------------------------------------------------------------------------------------------------------ records
def _plain(v):
    if isinstance(v, Enum): return v.value
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _plain(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, dict): return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)): return [_plain(x) for x in v]
    if isinstance(v, Path): return str(v)
    return v


def mission_to_json(m: Mission):
    """A core Mission as a mission_mem.json-style dict, so Mission.from_mission_json reads it back."""
    def base(b): return None if b is None else _plain(b)
    return {"theater": m.theater.value if m.theater else None,
            "targets": [_plain(t) for t in m.targets],
            "radars": [dict(place=r.place, system=r.system, x=r.x, y=r.y, kind=r.radar_type, type="D" if r.doppler else "P",
                            range_km=r.range_km, record=r.record) for r in m.radars],
            "takeoff": base(m.takeoff), "landing": base(m.landing), "takeoff_heading": m.takeoff_heading,
            "setup": m.setup, "level": m.level, "times": {"start": m.start_time}}


def route_to_json(r: Route):
    return {"version": r.version, "takeoff_heading": r.takeoff_heading, "points": [p.to_dict() for p in r.points],
            "profile": _plain(r.profile), "summary": _plain(r.summary)}


@dataclass(frozen=True)
class RouteVersion:
    """A route the pilot flew from `t` (epoch s) on: version 0 = the pre-flight plan; later ones are replans."""
    version: int
    t: float | None = None
    reason: str = ""
    route: dict | Route | None = None             # stored as a route.json-style dict

    def to_json(self):
        r = route_to_json(self.route) if isinstance(self.route, Route) else _plain(self.route)
        return dict(version=self.version, t=self.t, reason=self.reason, route=r)

    @classmethod
    def from_json(cls, d): return cls(d["version"], d.get("t"), d.get("reason", ""), d.get("route"))

    def route_obj(self):
        return self.route if isinstance(self.route, Route) or self.route is None else \
            Route.from_route_json(self.route, self.route.get("version", self.version))


@dataclass(frozen=True)
class Run:
    """One flight. id "" = assigned by the store (an epoch string, unique). parts: what the store holds for it
    ("nav", "events", "memlog", "record")."""
    id: str = ""
    start: float | None = None                    # epoch s
    end: float | None = None
    code_version: str | None = None
    mission: dict | Mission | None = None         # stored as a mission_mem.json-style dict
    route_versions: tuple = ()                    # RouteVersion
    settings: dict = field(default_factory=dict)  # nav args, settings, opponent, ... (JSON-able)
    parts: tuple = ()

    def to_json(self):
        m = mission_to_json(self.mission) if isinstance(self.mission, Mission) else _plain(self.mission)
        return dict(id=self.id, start=self.start, end=self.end, code_version=self.code_version, mission=m,
                    route_versions=[rv.to_json() for rv in self.route_versions], settings=_plain(self.settings))

    @classmethod
    def from_json(cls, d, parts=()):
        return cls(str(d["id"]), d.get("start"), d.get("end"), d.get("code_version"), d.get("mission"),
                   tuple(RouteVersion.from_json(r) for r in d.get("route_versions", ())), d.get("settings") or {},
                   tuple(parts))

    def mission_obj(self):
        if self.mission is None or isinstance(self.mission, Mission): return self.mission
        return Mission.from_mission_json(self.mission)

    @property
    def plan_version(self):
        return max((rv.version for rv in self.route_versions), default=None)


class RecordedRun:
    """A stored run: the record plus its frames. frames() is a ReplaySource (the live-source iteration API)."""

    def __init__(self, run: Run, nav: NavLogSource | None, memlog: MemlogSource | None):
        self.run, self.nav, self.memlog = run, nav, memlog

    @property
    def layout(self): return self.memlog.layout if self.memlog else None

    def frames(self, speed=None): return ReplaySource(self.nav, self.memlog, speed=speed)

    def rows(self): return iter(()) if self.nav is None else self.nav.rows()

    def events(self): return iter(()) if self.nav is None else self.nav.events()

    def snapshots(self): return iter(()) if self.memlog is None else self.memlog.snapshots()


# ----------------------------------------------------------------------------------------------------- protocol
@runtime_checkable
class FlightStore(Protocol):
    def new_run(self, run: Run | None = None, layout: MemlogLayout | None = None) -> Run: ...
    def start_memlog(self, run_id: str, layout: MemlogLayout) -> None: ...
    def sink(self, run_id: str): ...                                   # a TelemetrySink appending to the run
    def append_frames(self, run_id: str, frames: Iterable[TelemetryFrame]) -> None: ...
    def append_events(self, run_id: str, events: Iterable[Event]) -> None: ...
    def add_route_version(self, run_id: str, rv: RouteVersion) -> None: ...
    def close_run(self, run_id: str, timeout: float = 10.0) -> bool: ...
    def flush(self, timeout: float | None = None) -> bool: ...
    def list_runs(self) -> list[Run]: ...
    def load_run(self, run_id: str) -> RecordedRun: ...


class _RunSink:
    """The store's per-run sink: CSV rows + events, and snapshots once a memlog layout is set."""

    def __init__(self, csv_sink, memlog=None):
        self.csv, self.memlog = csv_sink, memlog
        self.stats = csv_sink.stats

    def write(self, frame):
        self.csv.write(frame)
        if self.memlog is not None: self.memlog.write(frame)
        elif frame.snapshot is not None: self.csv.stats.inc("dropped_invalid")   # no layout yet

    def flush(self, timeout=None):
        return all([s.flush(timeout) for s in (self.csv, self.memlog) if s is not None])

    def close(self, timeout=10.0):
        return all([s.close(timeout) for s in (self.csv, self.memlog) if s is not None])


class FileFlightStore:
    def __init__(self, logs_dir=None, columns=EXTENDED_COLUMNS, maxsize=4096, block=False):
        """logs_dir: default settings.logs (F19_DATA/logs). columns: the CSV columns new runs get (EXTENDED_COLUMNS:
        nav's 32 + route_want, behavior, plan_version; NAV_COLUMNS for exactly today's file). block: sinks wait
        for queue room instead of dropping (importing / rewriting recorded runs; never in flight)."""
        if logs_dir is None:
            from f19.core.settings import settings
            logs_dir = settings.logs
        self.logs = Path(logs_dir)
        self.columns, self.maxsize, self.block = tuple(columns), maxsize, block
        self._open: dict[str, _RunSink] = {}
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._rw = BackgroundWriter("flight-store-records", 256)   # run records: off the caller's thread too
        self.stats = self._rw.stats                                  # record writes (errors counted, never raised)

    # paths (file store only) ---------------------------------------------------------------------------------------
    def _csv(self, rid): return self.logs / "nav" / f"{rid}.csv"
    def _events(self, rid): return self.logs / "nav" / f"{rid}.events.txt"
    def _memlog(self, rid): return self.logs / "memlog" / str(rid)
    def _record(self, rid): return self.logs / "runs" / f"{rid}.json"

    def _taken(self, rid):
        return any(p.exists() for p in (self._csv(rid), self._memlog(rid), self._record(rid)))

    def _save(self, run):
        try: text = json.dumps(run.to_json(), indent=1)               # serialize now: the run may change later
        except (TypeError, ValueError) as e: self.stats.error(e); return
        self._rw.submit(self._write_record, run.id, text)

    def _write_record(self, rid, text):
        p = self._record(rid); p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp"); tmp.write_text(text); tmp.replace(p)

    def flush(self, timeout=None):
        """Wait for every queued write (records and open runs)."""
        return all([self._rw.flush(timeout)] + [s.flush(timeout) for s in list(self._open.values())])

    # writing --------------------------------------------------------------------------------------------------------
    def new_run(self, run=None, layout=None):
        run = run or Run()
        with self._lock:
            rid = run.id
            if not rid:
                e = int(run.start if run.start is not None else time.time())
                while self._taken(str(e)) or str(e) in self._runs: e += 1
                rid = str(e)
            run = dataclasses.replace(run, id=rid, start=run.start if run.start is not None else time.time(),
                                      code_version=run.code_version or code_version())
            self._open[rid] = _RunSink(CsvSink(self._csv(rid), self._events(rid), self.columns, self.maxsize, self.block))
            self._runs[rid] = run
        self._save(run)
        if layout is not None: self.start_memlog(rid, layout)
        return run

    def start_memlog(self, run_id, layout):
        s = self._open[run_id]
        if s.memlog is None: s.memlog = MemlogSink(self._memlog(run_id), layout, block=self.block)

    def sink(self, run_id): return self._open[run_id]

    def append_frames(self, run_id, frames):
        s = self._open[run_id]
        for fr in frames: s.write(fr)

    def append_events(self, run_id, events):
        s = self._open[run_id]
        for ev in events: s.csv.event(ev)

    def add_route_version(self, run_id, rv):
        with self._lock:
            run = self._runs.get(run_id) or self._load_record(run_id)
            run = dataclasses.replace(run, route_versions=run.route_versions + (rv,))
            self._runs[run_id] = run
        self._save(run)

    def close_run(self, run_id, timeout=10.0):
        s = self._open.pop(run_id, None)
        ok = s.close(timeout) if s is not None else True
        run = self._runs.pop(run_id, None)
        if run is not None: self._save(dataclasses.replace(run, end=time.time()))
        return ok

    def close(self, timeout=10.0):
        ok = all([self.close_run(r, timeout) for r in list(self._open)])
        return self._rw.close(timeout) and ok

    # reading --------------------------------------------------------------------------------------------------------
    def _load_record(self, rid):
        p = self._record(rid)
        return Run.from_json(json.loads(p.read_text()), self._parts(rid, rid, rid) + ("record",)) if p.exists() else None

    def _parts(self, rid, nav_id, mem_id):
        out = []
        if nav_id is not None and self._csv(nav_id).exists(): out.append("nav")
        if nav_id is not None and self._events(nav_id).exists(): out.append("events")
        if mem_id is not None and (self._memlog(mem_id) / "windows.json").exists(): out.append("memlog")
        return tuple(out)

    def _resolve(self, run_id):
        """(nav id, memlog id) of a run id: its own files if it has a record, else legacy pairing."""
        rid = str(run_id)
        if self._record(rid).exists():
            return (rid if self._csv(rid).exists() else None), (rid if self._memlog(rid).exists() else None)
        navs, mems = nav_logs(self.logs), memlog_runs(self.logs)
        if rid in navs: return rid, (rid if rid in mems else nearest_epoch(rid, mems, PAIR_S))
        if rid in mems: return nearest_epoch(rid, navs, PAIR_S), rid
        raise KeyError(f"no run {rid} under {self.logs}")

    def list_runs(self):
        """Every run: records first, then legacy nav CSVs (with their paired memlog), then unpaired memlogs."""
        recs = {p.stem: p for p in (self.logs / "runs").glob("*.json")} if (self.logs / "runs").exists() else {}
        navs, mems = nav_logs(self.logs), memlog_runs(self.logs)
        out, used_mem = [], set(recs)
        for rid in recs:
            r = self._load_record(rid)
            if r is not None: out.append(r)
        for nid in sorted(set(navs) - set(recs), key=int):
            mid = nid if nid in mems else nearest_epoch(nid, [m for m in mems if m not in recs], PAIR_S)
            if mid is not None: used_mem.add(mid)
            out.append(Run(nid, float(nid), parts=self._parts(nid, nid, mid)))
        for mid in sorted(set(mems) - used_mem, key=int):
            out.append(Run(mid, float(mid), parts=self._parts(mid, None, mid)))
        return sorted(out, key=lambda r: (r.start or 0, r.id))

    def load_run(self, run_id):
        nav_id, mem_id = self._resolve(run_id)
        rec = self._load_record(str(run_id))
        run = rec or Run(str(run_id), float(run_id), parts=self._parts(run_id, nav_id, mem_id))
        nav = NavLogSource(self._csv(nav_id)) if nav_id is not None else None
        mem = MemlogSource(self._memlog(mem_id)) if mem_id is not None and (self._memlog(mem_id) / "windows.json").exists() else None
        return RecordedRun(run, nav, mem)
