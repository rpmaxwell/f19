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


@pytest.mark.parametrize("name,x,y,hdg,alt,fuel", [("t744_after_the_first_evasion", 10528.6, 16646.6, 128.9, 260, 7975),
                                                    ("t1374_after_the_third_evasion", 16197.6, 20771.4, 193.1, 510, 6367)])
def test_in_flight_replans_fly_the_full_mission_near_liberec(world, name, x, y, hdg, alt, fuel):
    """Second --auto flight (1793113975): at both of these states every plain replan raised a router mountain error
    ("passes within 2.0 km of a mountain at R 20 km") east / west of the primary, so the secondary was dropped. The router
    now tries the half-turn attack bearings and a plane already turned onto them; both states route the full mission
    (4,231 / 3,221 lb at the end, primary then secondary then landing)."""
    ref, mission, ctx = world
    st = GameState(t=1810.0, pos=Position(x, y, alt), att=Attitude(hdg), speed_kt=175.0,
                   systems=Systems(fuel_lb=fuel, loadout=((17, 2),)), opp=3)
    d = FlightDirector(mission, ref, ctx, executor=InlineExecutor(), budget_s=30.0, total_budget_s=90.0)
    d._progress(st, None, None)
    d.replan(st, "test")
    h = d.history[-1]
    assert (h.candidate, h.skipped) == ("full", ()) and h.fuel_end_lb >= 1000, [a for a in h.attempts]
