"""core.types: field coverage of today's dicts, and the constructors on real plan files (read-only)."""
import ast, dataclasses, glob, json, re
from pathlib import Path

import pytest

from core_legacy import REPO
from f19.core.clock import ManualClock
from f19.core.settings import Settings
from f19.core.theater import Theater
from f19.core.types import (Action, Attitude, Base, Behavior, Command, FlightContext, Frame, GameState, Leg, Mission,
                            Position, RadarSite, Route, RoutePoint, Systems, Target, Threat, ThreatKind, Waypoint,
                            WaypointKind, WaypointStatus)

PLAN = Settings.from_env().logs / "plan"
GOLDEN = PLAN / "ce_budejovice_1023_1811"
need_data = pytest.mark.skipif(not GOLDEN.exists(), reason="F19_DATA plan logs not present")


def _decoded_keys(reqs_and_decoder):
    """Keys of the dict a reader decodes (zero bytes in). Since WP-4 the readers live in f19.memory.reader
    (scripts/f19_state.py is a shim), as (requests, decoder) pairs."""
    reqs, dec = reqs_and_decoder
    return list(dec([bytes(n) for _, n in reqs]))


def _return_keys(fn):
    """Keyword names of the dict a read_* function returns (read_flight)."""
    from f19.memory import reader as R
    return _decoded_keys({"read_flight": R._r_flight}[fn](0))


def _status_keys():
    from f19.memory import reader as R
    return _decoded_keys(R._r_status(0))


def _values(obj):
    """Every leaf value inside a (nested) dataclass."""
    if dataclasses.is_dataclass(obj):
        for f in dataclasses.fields(obj): yield from _values(getattr(obj, f.name))
    elif isinstance(obj, tuple):
        for v in obj: yield from _values(v)
    else: yield obj


def test_game_state_uses_every_f19_state_key():
    fk, sk = _return_keys("read_flight"), _status_keys()
    assert "loadout" in fk and "net_alert" in sk and "hud" in sk and "flares" in sk
    n = iter(range(1000, 100000, 7))
    flight = {k: next(n) for k in fk}; flight["loadout"] = [(5, next(n)), (16, next(n)), (17, next(n)), (0, next(n))]
    status = {k: next(n) for k in sk}; status["hud"] = "HUD TEXT"; status["fuel"] = flight["fuel"]
    state = dict(alt=500, spd=310, hdg=271.5, x=32 * 9000, y=32 * 12000)
    site = dict(record=4, x=9920, y=13120, type=3, flags=0x82, alert=40, dead=True, on=True)
    plane = dict(slot=2, x=100, y=200, alt=9000, type=3, name="MIG-31", flags=0x8b, alert=80)
    gs = GameState.from_dicts(state, flight, status, alerts=({4: 99}, {2: 160}), aircraft=[plane], sites=[site],
                              waypoints=([(1, 2), (3, 4), (5, 6), (7, 8)], 1), trackcam=2, sensitivity=1)
    vals = set(_values(gs))
    for k in fk:
        if k != "loadout": assert flight[k] in vals, k
    for k in sk:
        if k != "clock": assert status[k] in vals, k
    assert gs.t == status["clock"] * 2 and gs.pos == Position(9000, 12000, 500) and gs.speed_kt == 310
    assert gs.att == Attitude(271.5, flight["pitch"], flight["roll"]) and gs.systems.loadout == tuple(map(tuple, flight["loadout"]))
    s, p = gs.threats
    assert s == Threat(ThreatKind.SITE, 4, 9920, 13120, type=3, alert=99, flags=0x82, on=True, dead=True)
    assert p.kind is ThreatKind.AIRCRAFT and p.alert == 160 and p.alt_ft == 9000 and p.name == "MIG-31"
    assert gs.waypoints[3] == (7, 8) and gs.wp_index == 1 and gs.systems.trackcam == 2
    assert GameState.from_dicts(state, t=12.0).t == 12.0 and GameState.from_dicts(state).t is None


def test_systems_flags():
    s = Systems(flags=0x01 | 0x04 | 0x20)
    assert s.gear_up and s.bay_open and s.ir_jammer and not (s.flaps or s.brakes or s.ecm)


# every key the old override dicts used (nav's main loop, phases.py, missile_watch.py), taken from their sources on 2026-10-30
OVERRIDE_KEYS = {"abort_attack", "alt", "alt_cap", "alt_hold", "alt_min", "bank_max", "banner", "cm", "deadband", "done", "escape",
                 "fighter", "floor", "jam_off", "jam_on", "missile", "no_thr_up", "rate_cap", "roll_hold", "speed_kt", "survival",
                 "throttle_max", "vs_max", "vs_target", "want"}


def test_command_covers_nav_overrides():
    keys = OVERRIDE_KEYS
    assert {"want", "vs_target", "jam_off", "alt_min", "missile", "abort_attack", "survival"} <= keys
    ov = {k: i for i, k in enumerate(sorted(keys))}; ov["jam_off"] = ["ecm"]
    c = Command.from_overrides(ov)
    assert c.heading == ov["want"] and c.to_overrides() == ov
    with pytest.raises(KeyError): Command.from_overrides({"heading": 3})
    with pytest.raises(KeyError): Command.from_overrides({"wnat": 3})


def test_command_merge():
    a = Command(heading=10, alt=500, banner="route", actions=(Action.FLARE,))
    b = Command(heading=20, speed_kt=250, actions=(Action.CHAFF,))
    m = a.merged(b)
    assert m.to_overrides() == {**a.to_overrides(), **b.to_overrides()} and m.actions == (Action.FLARE, Action.CHAFF)
    hash(m)


@need_data
def test_route_from_route_json_round_trip():
    files = [f for f in glob.glob(str(PLAN / "*" / "route*.json")) if "points" in json.load(open(f))]
    assert files
    for f in files:
        d = json.load(open(f)); r = Route.from_route_json(f, version=3)
        assert [p.to_dict() for p in r.points] == d["points"], f
        assert r.version == 3 and r.takeoff_heading == d.get("takeoff_heading") and len(r.legs) == len(d["points"]) - 1
    r = Route.from_route_json(GOLDEN / "route.json")
    assert r.points[0].kind == "takeoff" and r.points[0].fuel_lb == 10000 and r.profile["cruise_ft"]
    leg = r.legs[0]
    assert leg.index == 1 and leg.start is r.points[0] and leg.heading == 0.0 and leg.length_km == 8.0
    assert {p.kind for p in r.points} >= {"takeoff", "photo", "landing"}
    assert r.with_version(4) == Route(r.points, 4, r.takeoff_heading)


@need_data
def test_route_old_schema():
    olds = [f for f in glob.glob(str(PLAN / "*" / "route*.json")) if "points" not in (d := json.load(open(f))) and "waypoints_game" in d]
    for f in olds:
        d = json.load(open(f)); r = Route.from_route_json(d)
        assert [(p.x, p.y) for p in r.points] == [tuple(p) for p, _ in d["waypoints_game"]]
    with pytest.raises(ValueError): Route.from_route_json({"waypoints_km": [[[1, 2], "a"]], "legs": []})


@need_data
def test_mission_from_mission_json():
    files = glob.glob(str(PLAN / "*" / "mission_mem.json")) + glob.glob(str(PLAN.parent / "missions" / "*.json"))
    files = [f for f in files if "radars" in json.load(open(f))]
    assert files
    for f in files:
        d = json.load(open(f)); m = Mission.from_mission_json(f)
        assert len(m.targets) == len(d["targets"]) and len(m.radars) == len(d["radars"])
        if m.theater is None: assert "theater" not in d and "theater_index" not in d
        elif "theater" in d: assert m.theater is Theater.parse(d["theater"])
        if "theater_index" in d and m.theater: assert m.theater.index == d["theater_index"]
        if m.takeoff and d.get("takeoff_heading") is not None:      # the json's heading follows the game rule
            assert m.takeoff_heading == pytest.approx(m.theater.takeoff_heading(m.takeoff.carrier), abs=0.05)
        for r, rd in zip(m.radars, d["radars"]):
            assert (r.radar_type, r.xy, r.doppler, r.system) == (rd.get("kind"), (rd["x"], rd["y"]), rd["type"] == "D", rd["system"])
    m = Mission.from_mission_json(GOLDEN / "mission_mem.json")
    assert m.theater is Theater.CE and m.primary.place == "Budejovice" and m.primary.task == "photograph"
    assert m.secondary.object == "SAM Radar" and m.takeoff == Base("Neuberg", 5440, 27328, "AirBase", 72, 1025, False)
    wps = m.waypoints()
    assert [w.kind for w in wps] == list(WaypointKind) and wps[0].required and not any(w.required for w in wps[1:])
    assert all(w.status is WaypointStatus.PENDING for w in wps) and wps[1].pos == (15816, 27320)
    assert wps[2].with_status(WaypointStatus.SKIPPED).status is WaypointStatus.SKIPPED


def test_mission_in_flight_roles():
    d = dict(theater="NC", targets=[dict(role="primary?", place="A", object="Bridge", x=1, y=2)], radars=[])
    m = Mission.from_mission_json(d)
    assert m.primary.x == 1 and not m.primary.role_known and m.takeoff is None
    assert [w.kind for w in m.waypoints()] == [WaypointKind.PRIMARY]


def test_behavior_protocol_and_frame():
    class Hold:
        name, priority, done = "hold", 0, False
        def wants_control(self, state, ctx): return True
        def step(self, state, ctx): return Command(heading=state.heading, banner=f"t={ctx.now():g}")
    ctx = FlightContext(ManualClock(42))
    st = GameState.from_dicts(dict(alt=300, spd=200, hdg=90.0, x=0, y=0))
    b = Hold()
    assert isinstance(b, Behavior) and b.wants_control(st, ctx)
    c = b.step(st, ctx)
    fr = Frame(ctx.now(), st, b.name, c, plan_version=1, leg=ctx.leg)
    assert fr.command.heading == 90.0 and fr.command.banner == "t=42" and fr.leg == 1
    with pytest.raises(dataclasses.FrozenInstanceError): fr.leg = 2
