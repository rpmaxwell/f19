"""2026-10-27 first director flight (ce_liberec_1027_0916): a manual takeoff drifted 5.6 km off the climb-out and burned
~114 lb more than planned. The plan had 84 lb of margin over the reserve, so the live fuel check fired at once and the
cheap estimate (which priced the slow pre-flight reference, not the router's own route) ruled the FULL mission out; the
director dropped the primary. The router's full route from that state ends at ~4,000 lb. Real planner, ~15 s."""
import pytest

from f19.core.types import Attitude, GameState, Mission, Position, Route, Systems, WaypointKind as K
from f19.pilot.director import FlightDirector, InlineExecutor
from f19.planner import api
from conftest import FIXTURES, need


@pytest.fixture(scope="module")
def world(egame):
    import json
    d = need(FIXTURES / "plans" / "ce_liberec_1027_0916", "liberec plan fixture")
    rd, mm = json.loads((d / "route.json").read_text()), json.loads((d / "mission_mem.json").read_text())
    return Route.from_route_json(d / "route.json"), Mission.from_mission_json(mm), api.context(rd["spec"], mm)


def test_full_mission_survives_a_thin_fuel_margin(world):
    ref, mission, ctx = world
    st = GameState(t=172.0, pos=Position(3750.2, 18875.8, 695), att=Attitude(94.5), speed_kt=147.0,
                   systems=Systems(fuel_lb=9778, loadout=((17, 2),)), opp=3)
    d = FlightDirector(mission, ref, ctx, executor=InlineExecutor())
    d._progress(st, None, None)
    assert d._live_violations(st)                                           # the live check does fire (84 lb of margin)
    route = d.replan(st, "constraint: fuel")
    h = d.history[-1]
    assert (h.candidate, h.skipped) == ("full", ()) and h.fuel_end_lb >= 1000
    assert d.plan.pending_kinds() == (K.PRIMARY, K.SECONDARY, K.LANDING)
    roles = [p.role for p in route.points if p.role]
    assert "primary" in roles and "secondary" in roles and roles.index("primary") < roles.index("secondary")
