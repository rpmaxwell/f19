"""Round trip on the replay fixtures (F19_DATA/fixtures/replay, read-only): read every run with the telemetry
readers, write it back with the sinks into a tmp F19_DATA, and compare.

  - memlog: windows.json and every snap_<t>.bin byte-identical
  - nav CSV: rows identical field by field (and the whole file byte-identical), events file byte-identical
  - the existing readers read the written runs as they read the originals: memlog_load.load, missile_game.load_run,
    missile_replay.nav_want, nav_tui.SnapDB + f19_state.read_state (what nav_tui.replay does per snapshot), also
    with the extended CSV columns the FileFlightStore writes
"""
import csv
import dataclasses
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT / "tests", ROOT / "tests" / "replay", ROOT / "src"):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))
import product  # noqa: E402

from f19.telemetry import (EXTENDED_COLUMNS, CsvSink, FileFlightStore, MemlogSink, MemlogSource, NavLogSource,  # noqa: E402
                           Run)

S = product.settings()
FIX = S.data / "fixtures" / "replay"
pytestmark = pytest.mark.skipif(not (FIX / "manifest.json").exists(), reason=f"missing replay fixtures: {FIX}")


def manifest():
    import json
    return json.loads((FIX / "manifest.json").read_text())


RUNS = sorted(manifest()) if (FIX / "manifest.json").exists() else []


def csv_rows(p):
    with open(p, newline="") as f: return list(csv.reader(f))


@pytest.fixture(scope="module")
def written(tmp_path_factory):
    """Every fixture run read and written back with the bare sinks (same columns), into a tmp F19_DATA."""
    out = tmp_path_factory.mktemp("f19data")
    for run, m in manifest().items():
        src = MemlogSource(FIX / "logs" / "memlog" / run)
        with MemlogSink(out / "logs" / "memlog" / run, src.layout, block=True) as sink:
            for fr in src: sink.write(fr)
        assert sink.stats.dropped == 0
        nav = NavLogSource(FIX / "logs" / "nav" / f"{m['nav']}.csv")
        with CsvSink(out / "logs" / "nav" / f"{m['nav']}.csv", out / "logs" / "nav" / f"{m['nav']}.events.txt",
                     nav.columns, block=True) as cs:
            for fr in nav: cs.write(fr)
        assert cs.stats.dropped == 0
    return out


@pytest.mark.parametrize("run", RUNS)
def test_memlog_byte_identical(written, run):
    a, b = FIX / "logs" / "memlog" / run, written / "logs" / "memlog" / run
    names = sorted(p.name for p in a.iterdir())
    assert sorted(p.name for p in b.iterdir()) == names
    for n in names: assert (a / n).read_bytes() == (b / n).read_bytes(), n


@pytest.mark.parametrize("run", RUNS)
def test_nav_csv_rows_identical(written, run):
    nav = manifest()[run]["nav"]
    a, b = FIX / "logs" / "nav" / f"{nav}.csv", written / "logs" / "nav" / f"{nav}.csv"
    ra, rb = csv_rows(a), csv_rows(b)
    assert len(ra) == len(rb)
    for k, (x, y) in enumerate(zip(ra, rb)): assert x == y, f"row {k}"
    assert a.read_bytes() == b.read_bytes()
    ea, eb = (p.with_name(f"{nav}.events.txt") for p in (a, b))
    assert ea.read_bytes() == eb.read_bytes()


@pytest.mark.parametrize("run", RUNS)
def test_reader_parses_without_fallback(run):
    """Every typed column parsed as its kind (the formatting rules match what nav wrote, not just str passthrough)."""
    from f19.telemetry.navcsv import KIND
    nav = NavLogSource(FIX / "logs" / "nav" / f"{manifest()[run]['nav']}.csv")
    bad = [(k, v) for fr in nav.rows() for k, v in fr.nav.items() if isinstance(v, str) and KIND.get(k, "str") != "str"]
    assert not bad[:5]
    assert all("_extra" not in fr.nav for fr in nav.rows())


def store_copy(tmp, run, columns=EXTENDED_COLUMNS):
    """The fixture run loaded through FileFlightStore and written into a tmp store under ONE id (the memlog run's)."""
    rec = FileFlightStore(FIX / "logs").load_run(run)
    assert rec.nav is not None and rec.nav.csv_path.stem == manifest()[run]["nav"]       # legacy nearest-epoch pairing
    st = FileFlightStore(tmp / "logs", columns=columns, block=True)
    r = st.new_run(Run(id=run, start=float(run), settings={"source": "fixture"}), layout=rec.layout)
    st.append_frames(r.id, rec.frames())
    assert st.close() and st.stats.dropped == 0
    return st


@pytest.fixture(scope="module")
def stored(tmp_path_factory):
    out = tmp_path_factory.mktemp("f19store")
    for run in RUNS: store_copy(out, run)
    return out


def with_data(monkeypatch, module, data):
    monkeypatch.setattr(module, "S", dataclasses.replace(module.S, data=data))


@pytest.mark.parametrize("src", ["written", "stored"])
def test_memlog_load_reads_output(src, request, monkeypatch):
    from f19.telemetry import memlog_load
    data = request.getfixturevalue(src)
    for run in RUNS:
        with_data(monkeypatch, memlog_load, FIX)
        T0, D0, P0 = memlog_load.load([run])
        with_data(monkeypatch, memlog_load, data)
        T1, D1, P1 = memlog_load.load([run])
        assert np.array_equal(T0, T1) and np.array_equal(D0, D1) and np.array_equal(P0, P1)


@pytest.mark.parametrize("src", ["written", "stored"])
def test_missile_game_load_run_reads_output(src, request, monkeypatch, egame):
    from f19.cli import memlog_checks as mg
    data = request.getfixturevalue(src)
    for run in RUNS:
        a = mg.load_run(run, logs=FIX / "logs")
        b = mg.load_run(run, logs=data / "logs")
        assert len(a) == len(b) and [s["t"] for s in a] == [s["t"] for s in b]
        assert repr(a) == repr(b)


@pytest.mark.parametrize("src", ["written", "stored"])
def test_missile_replay_nav_want_reads_output(src, request, monkeypatch, egame):
    from f19.cli import memlog_checks as missile_replay
    data = request.getfixturevalue(src)
    for run in RUNS:
        times = MemlogSource(FIX / "logs" / "memlog" / run).times()
        wa = missile_replay.nav_want(run, logs=FIX / "logs")
        wb = missile_replay.nav_want(run, logs=data / "logs")
        assert [wa(t) for t in times] == [wb(t) for t in times]


def test_snapdb_reads_stored_output(stored, egame):
    """The reader on a stored memlog run reads what it reads on the original (harness.SnapDB: PEEKs answered from a snapshot)."""
    import json
    import harness as H
    from f19.memory import MemoryReader
    for run in RUNS:
        states = []
        for d in (FIX / "logs" / "memlog" / run, stored / "logs" / "memlog" / run):
            windows = [tuple(w) for w in json.load(open(d / "windows.json"))["windows"]]
            db, out = H.SnapDB(windows), []
            reader = MemoryReader(db)
            for f in sorted(d.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))[:60]:
                db.load(f.read_bytes())
                try: gs = reader.snapshot(); out.append((f.name, gs.pos, gs.att, gs.speed_kt, gs.waypoints))
                except RuntimeError: out.append((f.name, None, None, None, None))
            states.append(out)
        assert states[0] == states[1] and any(s for _, s, *_ in states[0])



@pytest.mark.parametrize("run", RUNS)
def test_store_extended_csv_keeps_nav_columns(stored, run):
    """The store's CSV = nav's columns + route_want/behavior/plan_version (empty for a legacy run): every nav field
    identical, the extra ones blank. Events identical."""
    nav = manifest()[run]["nav"]
    with open(FIX / "logs" / "nav" / f"{nav}.csv", newline="") as f: a = list(csv.reader(f))
    with open(stored / "logs" / "nav" / f"{run}.csv", newline="") as f: b = list(csv.reader(f))
    ncol = len(a[0])
    assert b[0] == list(EXTENDED_COLUMNS) and len(a) == len(b)
    assert a[0] == list(EXTENDED_COLUMNS[:ncol])
    for x, y in zip(a[1:], b[1:]):
        if len(x) < ncol: assert y == x                  # short (sensitivity) row stays short
        else: assert y[:ncol] == x and all(c == "" for c in y[ncol:])
    assert (FIX / "logs" / "nav" / f"{nav}.events.txt").read_bytes() == (stored / "logs" / "nav" / f"{run}.events.txt").read_bytes()


@pytest.mark.parametrize("run", RUNS)
def test_replay_source_order_and_counts(run):
    rec = FileFlightStore(FIX / "logs").load_run(run)
    frames = list(rec.frames())
    n_rows, n_ev, n_snap = (sum(1 for _ in it) for it in (rec.rows(), rec.events(), rec.snapshots()))
    assert len(frames) == n_rows + n_ev + n_snap and n_snap == manifest()[run]["snapshots"]
    ts = [f.t for f in frames]
    assert ts == sorted(ts)
    rows = [f for f in frames if f.has_row]
    assert sum(f.frame is not None for f in rows) >= len(rows) - 2      # all but the sensitivity rows rebuild a Frame
