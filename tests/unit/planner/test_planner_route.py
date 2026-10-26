"""planner.api.route: the in-flight path from a synthetic airborne pose (golden CE missions), and the generalized pieces
it is built from (fuelplan.integrate start state, runway-end exemptions) at their defaults."""
import math
import time

import numpy as np
import pytest

from planner_golden import context, golden, pose_near

KOL = "ce_kolobrzeg_1022_2250"


@pytest.fixture(scope="module")
def kol(egame):
    ref, mission = golden(KOL)
    return ref, context(KOL)


def _heading(a, b):
    return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360


def test_route_to_landing_starts_from_the_pose(kol):
    from f19.planner import api
    ref, ctx = kol; pts = ref["points"]
    pose = pose_near(pts, 52, 8, 45, ref["profile"]["extra_fuel_bays"])
    r = api.route(pose, api.Goal.landing(), (api.FuelConstraint(),), budget_s=30, planner=ctx)
    print(f"route to landing: {r.elapsed_s:.2f} s, {len(r.pts)} points, fuel at landing {r.fuel_end_lb:.0f} lb")
    assert np.allclose(r.pts[0][0], pose.xy) and r.pts[0][2]["kind"] == "pose"
    assert abs((_heading(r.pts[0][0], r.pts[1][0]) - pose.heading + 180) % 360 - 180) < 1e-6
    x, y, _, _ = ctx.W.place(ref["spec"]["landing"])
    assert r.lands and np.allclose(r.pts[-1][0], (x, y))
    first = r.fuel["steps"][0]
    assert first["fuel"] == pose.fuel_lb and first["t"] == pose.t
    assert r.fuel["fuel0"] == pose.fuel_lb + 1900 * pose.tanks
    assert r.points[0]["fuel_lb"] == round(pose.fuel_lb) and r.points[0]["t_game_s"] == round(pose.t)
    assert r.errors == [] and r.violations == [] and r.feasible
    assert r.fuel_end_lb < pose.fuel_lb + 1900 * pose.tanks


def test_rejoin_splices_the_reference(kol):
    from f19.planner import api
    ref, ctx = kol; pts = ref["points"]
    pose = pose_near(pts, 52, 8, 45, ref["profile"]["extra_fuel_bays"])
    r = api.route(pose, api.Goal.rejoin(pts), budget_s=30, planner=ctx)
    k = r.plan["seq"][-1]["rejoin"]
    tail = [p for p in r.pts if True][-(len(pts) - k):]
    assert [tuple(np.round(p[0], 6)) for p in tail] == [(round(p["x"], 6), round(p["y"], 6)) for p in pts[k:]]
    assert [p[1] for p in tail] == [p["label"] for p in pts[k:]]
    assert r.errors == [] and r.lands
    # fuel re-integrated from the pose: the reference's own numbers are not copied
    assert r.points[0]["fuel_lb"] == round(pose.fuel_lb)


def test_target_then_landing(kol):
    from f19.planner import api
    ref, ctx = kol; pts = ref["points"]
    pose = pose_near(pts, 47, 0, 0, ref["profile"]["extra_fuel_bays"])
    r = api.route(pose, [api.Goal.target("secondary"), api.Goal.landing()], budget_s=30, planner=ctx)
    kinds = [a.get("kind") for _, _, a in r.pts]
    assert "photo" in kinds and "overfly" in kinds and r.lands
    t = next(t for t in ctx.M["targets"] if t["role"] == "secondary")
    assert min(math.dist(p[0], (t["x"], t["y"])) for p in r.pts) / 64 < 0.5
    assert r.errors == []


def test_fuel_constraint_and_budget(kol):
    from f19.planner import api
    from f19.planner.router import BudgetExceeded
    ref, ctx = kol; pts = ref["points"]
    pose = pose_near(pts, 96, 0, 0, ref["profile"]["extra_fuel_bays"])
    r = api.route(pose, api.Goal.landing(), (api.FuelConstraint(reserve_lb=1e6),), budget_s=30, planner=ctx)
    assert r.errors == [] and len(r.violations) == 1 and not r.feasible
    with pytest.raises(BudgetExceeded):
        api.route(pose, api.Goal.landing(), budget_s=0.0, planner=ctx)


def test_route_timing(kol):
    """The fast path's runtime on synthetic airborne states (refactor plan 4.2: a few seconds). Printed with -s."""
    from f19.planner import api
    ref, ctx = kol; pts = ref["points"]; bays = ref["profile"]["extra_fuel_bays"]
    times = []
    for i, off, dh in ((14, 8, 45), (52, -10, -90), (96, 8, 45), (119, 5, -30)):
        pose = pose_near(pts, i, off, dh, bays)
        for goal in (api.Goal.rejoin(pts), api.Goal.landing()):
            t0 = time.monotonic(); api.route(pose, goal, budget_s=30, planner=ctx); times.append(time.monotonic() - t0)
    print("route() s:", ", ".join(f"{t:.2f}" for t in times))
    assert max(times) < 30 and sorted(times)[len(times) // 2] < 10


# ---- the generalized pieces at their defaults
def test_leg_range_matches_the_old_runway_exemptions():
    from f19.planner.router import _leg_range
    for n_v in range(0, 9):                          # vertices
        n = max(0, n_v - 1)
        assert list(_leg_range(n, None)) == list(range(1, n_v - 2))                          # violations()
        assert list(_leg_range(n, None)) == [i for i in range(n)][1:-1]                     # clear()
        assert list(_leg_range(n, None)) == [i for i in range(n) if not (i == 0 or i == n_v - 2)]   # validate()
    assert list(_leg_range(5, (False, True))) == [0, 1, 2, 3] and list(_leg_range(5, (False, False))) == [0, 1, 2, 3, 4]


def test_validate_first_leg_exempt_only_on_a_runway(kol):
    _, ctx = kol
    W = ctx.W
    j, i = np.argwhere(W.wall)[len(np.argwhere(W.wall)) // 2]
    a = (float(i * W.step), float(j * W.step)); b = (a[0] + 640.0, a[1])
    pts = [(a, "", {}), (b, "", {})]
    assert not [e for e in ctx.router.validate(pts, [], set()) if e.startswith("terrain")]
    assert not [e for e in ctx.router.validate(pts, [], set(), ends=(True, False)) if e.startswith("terrain")]
    assert [e for e in ctx.router.validate(pts, [], set(), ends=(False, False)) if e.startswith("terrain")]   # a pose start


def test_integrate_start_state(kol):
    from f19.planner import fuelplan
    ref, ctx = kol
    pts = [((p["x"], p["y"]), p["label"], {"kind": p.get("kind")} if p.get("kind") else {}) for p in ref["points"][:40]]
    a = fuelplan.integrate(ctx.W, ctx.RM, ctx.P, pts)
    b = fuelplan.integrate(ctx.W, ctx.RM, ctx.P, pts, (), ctx.P["fuel_loaded_lb"], 0.0, ctx.P["extra_fuel_bays"])
    key = lambda st: [(s["t"], s["fuel"], s["alt"], s["kt"], s["thr"], s["bay"]) for s in st]
    assert key(a["steps"]) == key(b["steps"])
    assert (a["fuel_land"], a["t_end"], a["fuel0"]) == (b["fuel_land"], b["t_end"], b["fuel0"])
    c = fuelplan.integrate(ctx.W, ctx.RM, ctx.P, pts, (), 5000.0, 1234.0, 0)
    assert c["steps"][0]["fuel"] == 5000.0 and c["steps"][0]["t"] == 1234.0 and c["fuel0"] == 5000.0
    burn_a = a["fuel0"] - a["fuel_land"]; burn_c = c["fuel0"] - c["fuel_land"]
    assert abs(burn_a - burn_c) < 1e-6 and abs((c["t_end"] - 1234.0) - a["t_end"]) < 1e-6
