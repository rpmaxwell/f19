"""The 2026-10-28 planner refactor (docs/design/planner_refactor.md): memoized legs, the loiter fuel bound, batched radar
costs, worker processes. Each must give the numbers the plain code gives; the full-plan comparison (identical route.json for
every worker count) is the slow test at the bottom and the goldens."""
import json, math, os, subprocess, sys

import numpy as np
import pytest

import product
from conftest import S
from planner_golden import context, golden

KOL = "ce_kolobrzeg_1022_2250"


@pytest.fixture(scope="module")
def pl(egame):
    return context(KOL)


# ------------------------------------------------------------------------------------------------ memo keys
def test_freeze_is_equal_exactly_when_the_value_is():
    from f19.planner.planner import freeze
    a = dict(t="fixed", pts=[((1.0, 2.0), "x", {"k": 1})], u=np.array([0.5, 0.25]))
    b = dict(t="fixed", pts=[((1.0, 2.0), "x", {"k": 1})], u=np.array([0.5, 0.25]))
    assert freeze(a) == freeze(b) and hash(freeze(a)) == hash(freeze(b))
    b["u"] = np.array([0.5, 0.25 + 1e-16])
    assert freeze(a) != freeze(b)
    b = dict(a, pts=[((1.0, 2.0), "x", {"k": 2})])
    assert freeze(a) != freeze(b)


# ------------------------------------------------------------------------------------------------ router
def _legs(pl):
    tk, ld = pl.takeoff(), pl.landing("S")
    t = pl.targets(("primary", "secondary"))
    return [list(sub) for sub in ([tk, t[0], t[1]], [t[0], t[1], ld])]


def test_search_and_reduce_are_memoized_and_the_memo_is_the_plain_result(pl):
    r = pl.router
    for sub in _legs(pl):
        for key in pl._legs(sub):
            pts, h = r.search(*key)                       # fills the memo
            assert r.search_peek(*key)[0] == "ok"
            pts2, h2 = r.search(*key)
            assert pts2 == pts and h2 == h
            fresh = r._search_free(key[0], key[1], key[2], key[3], key[4], None)
            assert fresh[1] == h and [tuple(map(float, p)) for p in fresh[0]] == [tuple(map(float, p)) for p in pts]
            red = r.reduce(pts)
            assert red == r._reduce(pts) and r.reduce(pts) == red


def test_seg_costs_batch_equals_seg_cost(pl):
    r, rng = pl.router, np.random.default_rng(3)
    size = pl.W.size
    chords = [(rng.uniform(1000, size - 1000, 2), rng.uniform(1000, size - 1000, 2)) for _ in range(12)]
    chords += [(c[0], c[0] + 3.0) for c in chords[:2]]            # very short ones, and a zero-length chord
    chords.append((chords[0][0], chords[0][0].copy()))
    batch = r.seg_costs(chords)
    from f19.planner.router import _seg_cost
    for (a, b), v in zip(chords, batch):
        assert v == _seg_cost(r, tuple(np.round(a, 1)), tuple(np.round(b, 1)))


def test_sa14_clear_many_equals_sa14_clear(egame):
    from planner_golden import golden
    from f19.planner.planner import Planner
    ref, mission = golden(KOL)
    pl = Planner({**ref["spec"], "settings": {**ref["spec"]["settings"], "opponent": "elite"}}, mission, loiters=False)
    W, rng = pl.W, np.random.default_rng(5)
    chords = [(rng.uniform(500, W.size - 500, 2), rng.uniform(500, W.size - 500, 2)) for _ in range(80)]
    got = W.sa14_clear_many(chords)
    assert got == [W.sa14_clear(a, b) for a, b in chords] and True in got and False in got


# ------------------------------------------------------------------------------------------------ the loiter fuel bound
def test_loiter_fuel_bound_never_exceeds_the_fuel_a_plan_burns(pl):
    """The bound is for VALID plans; the geometry of an unaffordable one is the same, so it holds for those too."""
    pl.loiters = pl._loiter_choices()
    assert pl.loiters
    order, side = ("primary", "secondary"), "S"
    for lo in [None, *pl.loiters[:1]]:
        mid = [pl.loiter_el(lo, pl.P["loiter_min_laps"])] if lo else []
        seq = [pl.takeoff(), *pl.targets(order), *mid, pl.landing(side)]
        r = pl.plan_with_loiter(seq)
        used = r["fuel"]["fuel0"] - r["fuel"]["fuel_land"]
        need = pl.loiter_fuel_need(order, side, lo)
        print(f"loiter {lo and lo['cell']}: bound {need:.0f} lb, burned {used:.0f} lb")
        assert 0 < need <= used


def test_loiter_bound_names_the_shortfall(pl):
    pl.loiters = pl._loiter_choices()
    far = max(pl.loiters, key=lambda f: pl.loiter_fuel_need(("primary", "secondary"), "S", f))
    why = pl.loiter_bound(("primary", "secondary"), "S", far)
    assert why is None or why.startswith("needs >= ")
    assert pl.loiter_bound(("primary", "secondary"), "S", None) is None            # the plan without a loiter fits (the golden)


# ------------------------------------------------------------------------------------------------ the whole plan, any worker count
@pytest.mark.slow
def test_plan_is_identical_for_one_and_several_workers(tmp_path, egame):
    """route.json (and the report but for its date line) must not depend on how many processes planned the options."""
    ref, mission = golden(KOL)
    d = S.data / "fixtures/plans" / KOL
    out = {}
    for w in ("1", "3"):
        o = tmp_path / w; o.mkdir()
        p = subprocess.run([sys.executable, str(product.ROOT / "scripts/plan_bench.py"), str(d), "--no-instrument", "--out", str(o)],
                           env={**os.environ, "F19_PLAN_WORKERS": w, "PYTHONPATH": str(product.ROOT / "src")}, capture_output=True, text=True)
        assert p.returncode == 0, p.stderr[-2000:]
        out[w] = (json.loads((o / "route.json").read_text()), (o / "report.txt").read_text().splitlines()[1:])
    assert out["1"] == out["3"]
