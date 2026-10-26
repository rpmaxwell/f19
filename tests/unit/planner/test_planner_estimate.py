"""planner.estimate: the cheap fuel estimate against a full fuelplan.integrate on the golden routes."""
import pytest

from planner_golden import GOLDENS, context, golden, planned_tanks, pose_near


def test_lb_per_km():
    from f19.planner import estimate as E, profiles
    P = profiles.load()
    assert abs(E.lb_per_km(P) - 62.1 / (190 * 1.852 * 0.788 / 60)) < 1e-12
    assert 13.4 < E.lb_per_km(P) < 13.5


@pytest.mark.parametrize("name", GOLDENS)
def test_estimate_matches_full_integration_on_the_route(name, egame):
    """From every 10th point of the golden route (its planned fuel, clock and tanks there), the rest of the route
    integrated in full lands with the fuel the estimate gives (rounding of route.json fuel_lb only)."""
    from f19.planner import api, estimate as E, fuelplan
    ref, _ = golden(name); ctx = context(name); pts = ref["points"]; R = E.Reference(ref)
    bays = ref["profile"]["extra_fuel_bays"]
    flown = [((p["x"], p["y"]), p["label"],
              {k: v for k, v in p.items() if k not in ("x", "y", "label") + api._COMPUTED or p.get("kind") == "probe"})
             for p in pts]
    errs = []
    for i in range(0, len(pts) - 2, 10):
        pose = api.Pose(pts[i]["x"], pts[i]["y"], 0.0, 0.0, pts[i]["fuel_lb"], pts[i]["t_game_s"], planned_tanks(pts, i, bays))
        sub = flown[i:]
        f = fuelplan.integrate(ctx.W, ctx.RM, ctx.P, sub, {l for _, l, a in sub if a.get("kind") in ("photo", "launch")},
                               pose.fuel_lb, pose.t, pose.tanks)
        errs.append(E.estimate(R, pose, ctx.P, leg=i + 1).fuel_end_lb - f["fuel_land"])
    print(f"{name}: estimate - integrate over {len(errs)} start points: max |err| {max(map(abs, errs)):.2f} lb")
    assert max(map(abs, errs)) < 5


def test_estimate_rejoin_is_optimistic(egame):
    """Off the route, straight to the rejoin point: never more pessimistic than the routed join (turns, detours)."""
    from f19.planner import api, estimate as E
    name = GOLDENS[0]; ref, _ = golden(name); ctx = context(name); pts = ref["points"]; R = E.Reference(ref)
    for i, off, dh in ((52, 8, 45), (119, 5, -30)):
        pose = pose_near(pts, i, off, dh, ref["profile"]["extra_fuel_bays"])
        r = api.route(pose, api.Goal.rejoin(pts), budget_s=30, planner=ctx)
        est = E.estimate_rejoin(R, pose, ctx.P, r.plan["seq"][-1]["rejoin"]).fuel_end_lb
        print(f"pt {i}: estimate {est:.0f} lb, routed {r.fuel_end_lb:.0f} lb ({est - r.fuel_end_lb:+.0f})")
        assert est >= r.fuel_end_lb - 25
        assert E.FuelConstraint().ok(E.estimate_rejoin(R, pose, ctx.P, r.plan["seq"][-1]["rejoin"]), ctx.P) == (est >= 1000)
