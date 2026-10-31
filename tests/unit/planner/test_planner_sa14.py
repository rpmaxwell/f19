"""Live SA-14 cells (terrain bit 0x10, daylight): at Elite they are walls, not a price. The 2026-10-27 Carlsbad plan flew
7.4 min through cell (4, 6) because its secondary (a photo of Wittenberg) sits in it and the planner only priced the cells.
Covers the world rule, the router wall, the target skip, the alert simulation and the mission finder's filter."""
import copy

import numpy as np
import pytest

from conftest import S, need
from planner_golden import golden

KOL = "ce_kolobrzeg_1022_2250"
CELL = 2048
WITTENBERG = dict(role="secondary", task="photograph", place="Wittenberg", object="Tank Farm", x=9800, y=12984)   # cell (4, 6)
CARLSBAD = dict(role="primary", task="destroy", place="Carlsbad", object="Tank Farm", x=13776, y=23488)          # cell (6, 11)


def spec_for(opponent="elite", night=False, **settings):
    ref, _ = golden(KOL)
    return {**ref["spec"], "settings": {"roe": 0, "mission": "strike", "opponent": opponent, "night": night, **settings},
            "overrides": {"loiter": "off"}}


def planner(opponent="elite", targets=(CARLSBAD, WITTENBERG), loiters=False, **kw):
    from f19.planner.planner import Planner
    _, mission = golden(KOL)
    mission = {**copy.deepcopy(mission), "targets": [dict(t) for t in targets]}
    return Planner(spec_for(opponent, **kw), mission, loiters=loiters)


@pytest.fixture(scope="module")
def elite(egame):
    return planner()


# ------------------------------------------------------------------------------------------------ the world rule
def test_hard_from_elite_by_day_only(egame):
    assert planner("elite").W.sa14_hard
    assert not planner("veteran").W.sa14_hard
    assert not planner("elite", night=True).W.sa14_hard
    assert planner("veteran", sa14="hard").W.sa14_hard
    assert not planner("elite", sa14="soft").W.sa14_hard
    with pytest.raises(ValueError): planner("elite", sa14="maybe")


def test_entries_report_visits_and_exempt_the_ends(elite):
    W = elite.W
    mid = lambda c: (c[0] * CELL + CELL / 2, c[1] * CELL + CELL / 2)
    cell = (4, 6); assert W.sa14_cells[cell[1], cell[0]]
    out_w, out_e = (mid(cell)[0] - 2 * CELL, mid(cell)[1]), (mid(cell)[0] + 2 * CELL, mid(cell)[1])
    hits = W.sa14_entries([out_w, out_e])
    assert [h[0] for h in hits] == [cell]
    assert W.sa14_entries([out_w, (out_w[0], out_w[1] - 100)]) == []
    assert not W.sa14_clear(out_w, out_e)
    # the plane that starts inside the cell may leave it, but not come back
    assert W.sa14_entries([mid(cell), out_e]) == []
    assert [h[0] for h in W.sa14_entries([mid(cell), out_e, out_w, out_e])] == [cell, cell]


def test_the_margin_counts_as_inside(elite):
    W, m = elite.W, elite.P["sa14_margin_km"] * 64
    x0, y = 4 * CELL, 6 * CELL + CELL / 2                     # the west edge of cell (4, 6); cell (3, 6) is not live
    leg = lambda dx: [(x0 + dx, y - 3 * CELL), (x0 + dx, y + 3 * CELL)]    # a north-south leg dx east of the edge
    assert W.sa14_entries(leg(-m / 2)) != [] and W.sa14_entries(leg(-m - 64)) == []
    assert not W.sa14_clear(*leg(-m / 2)) and W.sa14_clear(*leg(-m - 64))


# ---------------------------------------------------------------------------------------------------- the router
def test_search_goes_round_a_live_cell_and_validate_flags_a_route_through_it(elite):
    W, R = elite.W, elite.router
    cell = (4, 6)
    a = ((cell[0] - 2) * CELL + CELL / 2, cell[1] * CELL + CELL / 2)
    b = ((cell[0] + 2) * CELL + CELL / 2, cell[1] * CELL + CELL / 2)
    pts, _ = R.search(a, b)
    assert W.sa14_entries(pts) == [], "the grid path entered a live SA-14 cell"
    assert W.sa14_entries(R.reduce(pts)) == []
    straight = [(tuple(a), "A", {}), (tuple(b), "B", {})]
    errs = R.validate(straight, [], set(), ends=(False, False))
    assert any("SA-14 cell (4, 6)" in e for e in errs)


def test_soft_cells_still_price_only(egame):
    pl = planner("veteran")
    assert not pl.router.sa14_hard
    cell = (4, 6)
    a = ((cell[0] - 2) * CELL + CELL / 2, cell[1] * CELL + CELL / 2); b = ((cell[0] + 2) * CELL + CELL / 2, a[1])
    assert not any("SA-14" in e for e in pl.router.validate([(a, "A", {}), (b, "B", {})], [], set(), ends=(False, False)))


def test_a_plane_already_in_a_cell_can_leave_it(elite):
    cell = (4, 6)
    a = (cell[0] * CELL + CELL / 2, cell[1] * CELL + CELL / 2)
    b = ((cell[0] + 3) * CELL + CELL / 2, a[1])
    pts, _ = elite.router.search(a, b)
    assert [h for h in elite.W.sa14_entries(pts)] == []


# ------------------------------------------------------------------------------------------- skipping targets
def test_secondary_inside_a_cell_is_skipped_primary_is_an_error(egame):
    from f19.planner.router import PlanError
    pl = planner()
    forced = pl._sa14_forced()
    assert [t["role"] for t, _ in forced] == ["secondary"] and "SA-14 cell (4, 6)" in forced[0][1]
    pl._drop(*forced[0])
    assert [t["role"] for t in pl.M["targets"]] == ["primary"] and len(pl.skipped) == 1
    assert planner("veteran")._sa14_forced() == []
    assert planner(sa14="soft")._sa14_forced() == []
    inside = dict(CARLSBAD, task="photograph", x=WITTENBERG["x"], y=WITTENBERG["y"])
    with pytest.raises(PlanError, match="PRIMARY.*SA-14 cell"):
        planner(targets=(inside,))._sa14_forced()


def test_maverick_target_in_a_cell_is_not_forced(egame):
    pl = planner(targets=(dict(CARLSBAD, x=WITTENBERG["x"], y=WITTENBERG["y"]),))     # a destroy task in cell (4, 6)
    assert pl.attack_mode(pl.M["targets"][0])[0] == "maverick"
    assert pl._sa14_forced() == []          # launched from 14 km out: the route need not enter the cell


# ----------------------------------------------------------------------------------------------- alert simulation
class _World:
    def __init__(self, opp, live): self.opp, self._live = opp, live
    def sa14(self, x, y): return np.asarray(self._live(np.asarray(x), np.asarray(y)))


def _steps(x0, x1, t1, n=200):
    return [dict(t=float(t), xy=(float(x), 5000.0)) for t, x in zip(np.linspace(0, t1, n), np.linspace(x0, x1, n))]


def test_sa14_launches_in_a_live_cell_and_not_outside():
    from f19.planner import alertsim
    inside = lambda x, y: (x // CELL == 4) & (y // CELL == 2)             # one cell, columns 4
    steps = _steps(3 * CELL, 6 * CELL, 600)                              # 10 min, a third of it inside
    elite = alertsim._sa14(_World(3, inside), steps, 1)
    assert 3.0 < elite["minutes"] < 3.6 and elite["checks"] > 4
    assert elite["p_launch"] > 0.9 and elite["p_per_check"] > 0.9 and elite["first"] is not None
    green = alertsim._sa14(_World(0, inside), steps, 1)                  # flies only from > 1024 units away: ~20-40 % of spawns
    assert 0.15 < green["p_per_check"] < 0.6 < elite["p_per_check"]
    none = alertsim._sa14(_World(3, lambda x, y: np.zeros(np.shape(x), bool)), steps, 1)
    assert none["minutes"] == 0 and none["p_launch"] == 0 and all(not l for l in none["launches"])


def test_sa14_launch_is_a_network_alert_for_the_sites():
    from f19.planner import alertsim
    t = np.arange(0, 600.0, 4)
    M = np.full((1, len(t)), -50.0)                                      # site never sees the plane
    r = alertsim._run(t, M, np.ones((1, len(t)), bool), np.ones(len(t), bool), 3, 0, [0], 600.0, np.random.default_rng(1),
                      always=True, E=np.zeros(len(t), bool), sa14_t=[100.0])
    assert r["max"][0] > 0 and r["sa14"] == [100.0]            # the network floor ((war + opp) x 16 - 16) lifts the quiet site
    q = alertsim._run(t, M, np.ones((1, len(t)), bool), np.ones(len(t), bool), 3, 0, [0], 600.0, np.random.default_rng(1),
                      always=True, E=np.zeros(len(t), bool))
    assert q["max"][0] == 0 and q["sa14"] == []


# --------------------------------------------------------------------------------------------- the mission finder
def test_sa14_cell_lookup_and_spec_filter():
    from f19.gamedata import mission_gen as mg
    need(S.game_dir / "CE.WLD", "CE.WLD")
    assert mg.sa14_cell("CE", WITTENBERG["x"], WITTENBERG["y"]) == (4, 6)
    assert mg.sa14_cell("CE", CARLSBAD["x"], CARLSBAD["y"]) is None
    assert mg.sa14_cell(None, 0, 0) is None
    mk = lambda p, s: dict(bucket="x9", time_limit=False, primary=dict(type=1, mission=1, sa14=p), secondary=dict(type=2, mission=2, sa14=s))
    A, B, C, D = mk(None, None), mk((1, 2), None), mk(None, (4, 6)), mk((1, 2), (4, 6))
    got = lambda **kw: [m for m in (A, B, C, D) if mg.Spec(**kw).match(m)]
    assert got() == [A, B, C, D]
    assert got(sa14_primary=False, sa14_secondary=False) == [A]
    assert got(sa14_secondary=True) == [C, D]
    assert got(sa14_primary=True, sa14_secondary=False) == [B]
    assert "secondary in an SA-14 cell" in str(mg.Spec(sa14_secondary=True))


# ------------------------------------------------------------------------------ the whole planner (slow, minutes)
@pytest.mark.slow
def test_secondary_in_a_cell_is_skipped_and_the_plan_stays_clear(egame):
    """Kolobrzeg's mission with the secondary moved to Wittenberg (photo, inside cell (4, 6)) at Elite by day."""
    from f19.planner import api
    ref, mission = golden(KOL)
    mission = copy.deepcopy(mission)
    for t in mission["targets"]:
        if t["role"] == "secondary": t.update(WITTENBERG)
    res = api.plan_mission({**spec_for(), "takeoff_heading": None}, mission, "test")
    assert res.best["errors"] == []
    assert [t["role"] for t, _ in res.planner.skipped] == ["secondary"]
    assert res.planner.W.sa14_entries([p[0] for p in res.best["pts"]]) == []
    assert res.summary["sa14_minutes"] == 0 and res.summary["skipped"][0]["role"] == "secondary"
    assert "SKIPPED secondary" in res.report


@pytest.mark.slow
def test_elite_carlsbad_plan_fails_loudly(egame):
    """The 2026-10-27 Elite mission: the Wittenberg photo (secondary, in cell (4, 6)) is skipped, but Carlsbad lies in a pocket
    whose only way out is the pass plugged by cells (3, 10) / (3, 11), so no route keeps clear of them: the plan is INVALID and
    says which cells it enters (it used to fly 7.4 min through cell (4, 6) and call it fine)."""
    import json
    from f19.planner import api
    d = need(S.logs / "plan" / "ce_carlsbad_1027_1842", "the 2026-10-27 Carlsbad plan dir")
    spec = json.loads((d / "plan.json").read_text()); mission = json.loads((d / "mission_mem.json").read_text())
    res = api.plan_mission({**spec, "takeoff_heading": None}, mission, "test")
    assert [t["role"] for t, _ in res.planner.skipped] == ["secondary"]
    assert any("SA-14 cell" in e for e in res.best["errors"])
    assert "NO ROUTE KEEPS" in res.report and "PREDICTED SA-14 launch" in res.report
