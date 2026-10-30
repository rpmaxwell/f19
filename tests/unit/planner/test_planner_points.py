"""The career-points objective (planner/points.py) and the fighter model (planner/fighters.py): the closed forms, the monotonicity
the ranking relies on, and the rules of aircraft.md that the model must reproduce (Veteran fighters do not see a low F-19 that is
slow on the beam; Elite doppler fighters closing head-on do)."""
import math

import numpy as np
import pytest

from f19.planner import fighters, points


def accrual(n=100, full=1000.0):
    t = np.arange(n) * 60.0
    return dict(t=t, dt=np.full(n, 60.0), pts=np.linspace(0, full, n), em=np.full(n, 2 / 8), F=np.full(n, 6000.0), B=np.linspace(4000, 0, n),
                T_end=float(t[-1] + 60), full=full, n=n, unit=13.0)


T = dict(fuel_used=5000.0, fuel_end=1500.0)
A = points.Assumptions()


def none_scenario(n): return [dict(mass=np.zeros(n), vis=np.zeros(n))]


def test_damage_probabilities():
    for n in (3, 4):
        p = points.dmg_probs(n)
        assert p["bay"] == pytest.approx(1 - (7 / 8) ** n)
        assert p["both"] + p["neither"] + 2 * (p["bay"] - p["both"]) == pytest.approx(1)
    assert points.dmg_probs(3)["bay"] == pytest.approx(0.33, abs=0.005) and points.dmg_probs(4)["bay"] == pytest.approx(0.414, abs=0.001)


def test_no_hits_lands_with_full_points():
    ac = accrual()
    v = points.value(ac, T, A, 3, none_scenario(ac["n"]))
    assert v["p_bay"] == 0 and v["hits"] == 0 and v["exp_pts"] == pytest.approx(ac["full"], rel=0.01)


def test_hits_cost_points_and_later_hits_cost_less():
    ac = accrual(); vals = []
    for k in (10, 50, 90):
        mass = np.zeros(ac["n"]); mass[k] = 1.0
        vals.append(points.value(ac, T, A, 3, [dict(mass=mass, vis=np.zeros(ac["n"]))])["exp_pts"])
    assert vals[0] < vals[1] < vals[2] < ac["full"]
    low = points.value(ac, T, points.Assumptions(hit_per_min=0.01), 3, points.constant_hazard(ac, 0.01))["exp_pts"]
    high = points.value(ac, T, points.Assumptions(hit_per_min=0.05), 3, points.constant_hazard(ac, 0.05))["exp_pts"]
    assert high < low < ac["full"]


def test_visual_ids_subtract_20_base_points_each():
    ac = accrual(); vis = np.ones(ac["n"])
    v = points.value(ac, T, A, 3, [dict(mass=np.zeros(ac["n"]), vis=vis)])
    assert v["full"] == pytest.approx(ac["full"] - 20 * ac["unit"])


def test_veteran_hazard_is_far_below_elite():
    assert points.HIT_PER_MIN[2] < points.HIT_PER_MIN[3] / 50


# ---------------------------------------------------------------- fighters: the sighting rules of aircraft.md
def flight(n=200, heading_to_fighter=True, kt=190.0, alt=250.0, emv=13.0):
    """A straight flight east at game s 0..: arrays as fighters.scenario_arrays makes them."""
    t = np.arange(n) * 16.0
    return dict(t=t, x=8000.0 + t * 0.0, y=np.full(n, 8000.0), ux=np.ones(n), uy=np.zeros(n), kt=np.full(n, kt), alt=np.full(n, alt),
                emv=np.full(n, emv), hdg=np.full(n, 0x4000, np.int64), clutter=np.full(n, 8 << 0))


def run(typ, opp, bearing, seeds=40, d0=40.0, kt=190.0, night=False):
    spec = fighters.rg.aircraft_spec(typ); out = []
    visual_km = fighters.HUNT["visual_km_night" if night else "visual_km_day"]
    for sd in range(seeds):
        rng = np.random.default_rng(sd); shots, vis = [], []
        fighters._hunter(flight(kt=kt), spec, typ, d0, bearing, [0.0], rng, opp, opp * 16 + 32, visual_km, fighters.HUNT, 3000.0, shots, vis)
        out.append((len(shots), len(vis)))
    return out


def test_unarmed_type_never_fires():
    assert all(r == (0, 0) for r in run(8, 3, 0.0, seeds=5))          # F-18: no radar


def test_elite_doppler_closing_head_on_gets_visual_ids_and_shots_veteran_far_fewer():
    el, vet = run(3, 3, 0.0), run(3, 2, 0.0)                          # MiG-31 straight at a plane flying east
    assert sum(v > 0 for _, v in el) >= 8 and sum(s > 0 for s, _ in el) >= 8       # rule-of-thumb: a quarter of the draws or more
    assert sum(s > 0 for s, _ in vet) < sum(s > 0 for s, _ in el) / 2


def test_veteran_su27_does_not_see_a_low_190kt_plane():
    # aircraft.md: Su-27 / MiG-29 never see a Veteran F-19 at 250 ft at any aspect inside the table's ranges
    r = run(4, 2, math.pi / 2, d0=30.0) + run(4, 2, 0.0, d0=30.0)
    assert all(s == 0 for s, _ in r)


def test_gate_closes_without_a_new_alert():
    # a hunter 190 km out cannot arrive within the ~500 s the alert keeps the hunt open (190 km at 164 + 77 m/s is 790 s)
    assert all(r == (0, 0) for r in run(3, 3, 0.0, d0=190.0, seeds=5))
