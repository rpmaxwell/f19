"""FileFlightStore: a live-style run (frames from the bus, events, snapshots, route versions) written and read back;
the Run record; legacy runs (no record) listed and paired by nearest epoch."""
import csv
import json
import time
from pathlib import Path

import pytest

from f19.core.settings import settings as S
from f19.core.types import Attitude, Command, Frame, GameState, Mission, Position, Route, Systems
from f19.telemetry import (EXTENDED_COLUMNS, NAV_COLUMNS, Event, FileFlightStore, FlightStore, MemlogLayout,
                           RouteVersion, Run, Snapshot, TelemetryBus, TelemetryFrame, code_version)

FIX = S.data / "fixtures" / "replay"
PLAN = FIX / "plan" / "ce_stupsk_1022_2025"
LAYOUT = MemlogLayout(16, [("egame_data", 0x100, 0x180), ("world_CE", 0x400, 0x440)])


def tick(i, t0=1800000000.0):
    st = GameState(t=100.0 + i, pos=Position(1000.0 + i, 2000.0, 400), att=Attitude(90.0), speed_kt=300,
                   systems=Systems(flags=1, throttle=80, fuel_lb=9000 - i, locked=65535))
    beh = "FighterWatch" if 3 <= i < 6 else "route"
    cmd = Command(heading=45.0 if beh == "FighterWatch" else 90.0, fighter="evade" if beh == "FighterWatch" else None)
    return TelemetryFrame(t0 + i * 0.25, Frame(100.0 + i, st, beh, cmd, plan_version=1 if i >= 5 else 0, leg=2),
                          nav={"phase": "route", "xtrack_km": 0.1 * i, "tscale": 1.0}, route_heading=90.0,
                          snapshot=Snapshot(t0 + i * 0.25, bytes([i]) * LAYOUT.size) if i % 4 == 0 else None,
                          events=(Event(round(t0 + i * 0.25), "fighter evasion"),) if i == 3 else ())


def test_live_run_round_trip(tmp_path):
    store = FileFlightStore(tmp_path / "logs")
    assert isinstance(store, FlightStore)
    mission = Mission.from_mission_json(PLAN / "mission_mem.json") if PLAN.exists() else None
    route = Route.from_route_json(PLAN / "route.json") if PLAN.exists() else None
    run = store.new_run(Run(start=1800000000.0, mission=mission, settings={"auto": True, "memlog": True}),
                        layout=LAYOUT)
    assert run.id == "1800000000" and run.code_version == code_version()
    store.add_route_version(run.id, RouteVersion(0, 1800000000.0, "pre-flight", route))
    bus = TelemetryBus([store.sink(run.id)])
    for i in range(10): bus.publish(tick(i))
    bus.event(Event(1800000003.0, "replan: back to the route"))
    store.add_route_version(run.id, RouteVersion(1, 1800000001.25, "after FighterWatch", {"points": []}))
    assert store.close_run(run.id) and store.flush(5)

    rec = store.load_run(run.id)
    assert rec.run.id == run.id and rec.run.end is not None and rec.run.settings == {"auto": True, "memlog": True}
    assert {"nav", "events", "memlog", "record"} <= set(rec.run.parts)
    assert [rv.version for rv in rec.run.route_versions] == [0, 1] and rec.run.plan_version == 1
    if mission is not None:
        assert rec.run.mission_obj() == mission
        assert rec.run.route_versions[0].route_obj().points == route.points
    rows = list(rec.rows())
    assert len(rows) == 10 and rec.nav.columns == EXTENDED_COLUMNS
    assert [r.behavior for r in rows] == [tick(i).frame.behavior for i in range(10)]
    assert [r.route_heading for r in rows] == [90.0] * 10                    # the want before the override
    assert [r.nav["want"] for r in rows][3:6] == [45.0] * 3
    assert [r.plan_version for r in rows] == [0] * 5 + [1] * 5
    assert rows[3].frame.command.fighter == "evade" and rows[3].frame.state.pos.x == 1003.0
    assert [e.text for e in rec.events()] == ["fighter evasion", "replan: back to the route"]
    snaps = list(rec.snapshots())
    assert [s.data[0] for s in snaps] == [0, 4, 8] and rec.layout == LAYOUT
    assert len(list(rec.frames())) == 10 + 2 + 3
    j = json.loads((tmp_path / "logs" / "runs" / f"{run.id}.json").read_text())
    assert j["id"] == run.id and len(j["route_versions"]) == 2


def test_ids_unique_and_nav_columns(tmp_path):
    store = FileFlightStore(tmp_path / "logs", columns=NAV_COLUMNS)
    (tmp_path / "logs" / "nav").mkdir(parents=True)
    (tmp_path / "logs" / "nav" / "1800000000.csv").write_text("")         # a nav log already there
    a = store.new_run(Run(start=1800000000.0)); b = store.new_run(Run(start=1800000000.0))
    assert (a.id, b.id) == ("1800000001", "1800000002")
    store.append_frames(a.id, [tick(0)]); store.append_events(a.id, [Event(1800000000.0, "takeoff done")])
    assert store.close()
    with open(tmp_path / "logs" / "nav" / f"{a.id}.csv", newline="") as f: hdr, row = list(csv.reader(f))
    assert tuple(hdr) == NAV_COLUMNS and row[:6] == ["1800000000.00", "1000", "2000", "400", "300", "90.0"]
    assert (tmp_path / "logs" / "nav" / f"{a.id}.events.txt").read_text() == "1800000000 takeoff done\n"


def test_unwritable_store_never_raises(tmp_path):
    (tmp_path / "logs").write_text("not a dir")
    store = FileFlightStore(tmp_path / "logs")
    run = store.new_run(Run(start=1.0), layout=LAYOUT)
    sink = store.sink(run.id)
    store.append_frames(run.id, [tick(i) for i in range(5)])
    store.add_route_version(run.id, RouteVersion(1))
    assert store.close(5)
    assert store.stats.errors >= 1 and sink.stats.errors >= 1 and sink.memlog.stats.errors >= 1


@pytest.mark.skipif(not (FIX / "manifest.json").exists(), reason="missing replay fixtures")
def test_legacy_runs_listed_and_paired():
    man = json.loads((FIX / "manifest.json").read_text())
    store = FileFlightStore(FIX / "logs")
    runs = {r.id: r for r in store.list_runs()}
    for mem, m in man.items():
        assert set(runs[m["nav"]].parts) == {"nav", "events", "memlog"}
        assert mem not in runs                       # the memlog is part of its nav run, not listed twice
        for rid in (mem, m["nav"]):                  # either id loads the pair
            rec = store.load_run(rid)
            assert rec.nav.csv_path.stem == m["nav"] and rec.memlog.dir.name == mem
    with pytest.raises(KeyError): store.load_run("1")
