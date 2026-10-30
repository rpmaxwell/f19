"""The career-points objective: E[points] - lambda x P(death) of a planned route (notebooks/planner_walkthrough.ipynb, "Beyond fuel").

  score = M x (enemy samples + 140 [primary] + 35 [secondary] - 10 x radar IDs - 20 x visual IDs); M is fixed by the menus.
  A hit sets opp + 1 random damage bits: a jammed bay (0x10) means bailing out (score x 2/8 over enemy ground), a leak (0x20) shortens the flight.
  Every probability is decoded from the game (damage) or counted from the flight logs (the hit rate).

Two sources for the hits, the same value function:
  objective "points":    a constant hit rate per airborne minute (hit_per_min below: counted per opponent level, 7 Elite flights, 21 Veteran).
  objective "aircraft":  hits from the fighter model (fighters.py): the route's own network alerts, the aspect and speed it shows the
                         fighters' radars, the terrain it hides in. The rate is an output.
M cancels in the ranking while p_unlevel is 0 (lam x P(death) is the only term in absolute points), so the score multipliers use
the plan's settings where given and the notebook's defaults otherwise.
"""
from dataclasses import dataclass
from math import erf, sqrt

import numpy as np

from f19.rules import scoring

HIT_PER_MIN = {0: 0.0, 1: 0.0, 2: 0.5 / 1878, 3: 0.0288}     # by opponent level: Veteran = half an event in the 1,878 logged min (none seen), Elite = 10 hits / 347 min


@dataclass(frozen=True)
class Assumptions:
    roe: int = 0
    landing: str = "realistic"
    fuel_estimate: int = 9000
    hit_per_min: float = 0.0              # objective "points"
    p_unlevel: float = 0.0                # P(die | eject): 0 if the ejection sequence levels the plane first
    lam: float = 30000.0                  # what staying alive is worth, points
    sigma_fuel: float = 0.083             # relative error of the planned fuel burn (16 logged flights)
    p_hit_launch: float = 0.43            # objective "aircraft": P(hit | fighter missile), Elite, no countermeasure used


def dmg_probs(n):
    """P(bay), P(leak), P(both), P(neither) after one hit of n = opp + 1 damage draws."""
    return dict(bay=1 - (7 / 8) ** n, leak=1 - (7 / 8) ** n, both=1 - 2 * (7 / 8) ** n + (6 / 8) ** n, neither=(6 / 8) ** n)


def leak_phi(ac, count):
    """Fraction of the remaining plan still flyable if a leak of `count` lb per 4 game s starts at each step."""
    L = count * (ac["T_end"] - ac["t"]) / 4.0
    return np.where(ac["F"] - ac["B"] >= L, 1.0, np.clip(ac["F"] / np.maximum(ac["B"] + L, 1.0), 0, 1))


def terms(pl, r, sim, roles):
    """What value() needs of a planned route r that is not in its accrual arrays. sim: alertsim.simulate of its steps."""
    from . import fuelplan
    lo, hi, _ = fuelplan.samples(pl.W, r["fuel"]["steps"], pl.P)
    exp_ids = sim["summary"]["expected"]["expected_ids"] if sim.get("sites") else 0.0
    f_end = r["fuel"]["fuel_land"]
    return dict(samples=(lo + hi) / 2, ids=exp_ids, fuel_end=f_end, fuel_used=r["fuel"]["fuel0"] - f_end, theater=pl.th,
                settings={"mission": "strike", "opponent": "veteran", **pl.spec.get("settings", {})},
                primary="primary" in roles, secondary="secondary" in roles)


def accrual(pl, r, T, A):
    """Per step of the timed route: dt, points if the flight ended here with a safe landing (`pts`), the ejection multiplier, the leak
    feasibility inputs (F fuel on board, B planned burn still to come), and the per-point score weight (`unit`: score per base point)."""
    fu = r["fuel"]; st = fu["steps"]; n = len(st)
    t = np.array([s["t"] for s in st]); dt = np.diff(np.append(t, fu["t_end"]))
    enemy = np.array([s["enemy"] for s in st]); xy = np.array([s["xy"] for s in st])
    st_t = np.arange(64.0, fu["t_end"], 64.0)                         # the game logs a position every 64 game s
    idx = np.clip(np.searchsorted(t, st_t, side="right") - 1, 0, n - 1)
    cum_s = np.cumsum(np.bincount(idx[enemy[idx]], minlength=n))
    done = {}
    for role in ("primary", "secondary"):
        cand = [i for i, s in enumerate(st) if s["label"].upper().startswith(role.upper() + ":") and ("photo point" in s["label"] or "launch" in s["label"])]
        cand = cand or [i for i, s in enumerate(st) if s["label"].upper().startswith(role.upper() + ":")]
        done[role] = cand[0] if cand and T[role] else n + 1
    km = np.cumsum([s["km"] for s in st]); ids_k = T["ids"] * km / km[-1]
    sett = {**T["settings"], "landing": A.landing, "roe": A.roe}; fm = scoring.fuel_mult(A.fuel_estimate)
    pts = np.array([scoring.rating(cum_s[k], T["theater"], sett, done["primary"] <= k, done["secondary"] <= k, fm, 10 * ids_k[k])[1]
                    for k in range(n)], float)
    unit = scoring.rating(1000, T["theater"], sett, False, False, fm, 0)[1] / 1000.0
    side = (pl.W.tgrid[np.clip((xy[:, 1] // 2048).astype(int), 0, 15), np.clip((xy[:, 0] // 2048).astype(int), 0, 15)] & 3).astype(int)
    em = np.array([2, 3, 6, 4])[side] / 8                              # ds:978C; only 0 (enemy) = 2/8 is confirmed
    tr = fu.get("transfers", []); tanks0 = pl.P["extra_fuel_bays"]
    tank_left = np.array([tanks0 - sum(1 for x in tr if x["t"] <= t[k]) for k in range(n)])
    F = np.array([s["fuel"] for s in st]) + 1900.0 * tank_left
    return dict(t=t, dt=dt, pts=pts, em=em, F=F, B=F - fu["fuel_land"], T_end=fu["t_end"], full=pts[-1], n=n, unit=unit)


def constant_hazard(ac, hit_per_min):
    """One scenario: expected hits in each step at a constant rate per airborne minute."""
    return [dict(mass=hit_per_min / 60.0 * ac["dt"], vis=np.zeros(ac["n"]))]


def fighter_hazard(ac, scen, A):
    """The fighter model's scenarios (fighters.simulate) -> hit mass (expected hits) and cumulative visual IDs per step."""
    out = []
    for s in scen:
        mass, vis = np.zeros(ac["n"]), np.zeros(ac["n"])
        for tt, _ in s["shots"]: mass[min(ac["n"] - 1, int(np.searchsorted(ac["t"], tt)))] += A.p_hit_launch
        for tt in s["visual"]: vis[min(ac["n"] - 1, int(np.searchsorted(ac["t"], tt))):] += 1
        out.append(dict(mass=mass, vis=vis))
    return out


def value(ac, T, A, opp, scenarios):
    """E[points] - lam x P(death), averaged over the hit scenarios. Returns the pieces too."""
    pd = dmg_probs(opp + 1)
    phi = leak_phi(ac, 2 + opp + 1)                                    # first leak: count = 2 + (opp + 1), one hit (a lower bound)
    sigma = A.sigma_fuel * T["fuel_used"]
    p_fuel = 0.5 * (1 + erf((0 - T["fuel_end"]) / (sigma * sqrt(2)))) if sigma > 0 else 0.0
    rows = []
    for sc in scenarios:
        cum = np.cumsum(sc["mass"]); before = cum - sc["mass"]
        pts = np.maximum(0.0, ac["pts"] - 20.0 * ac["unit"] * sc["vis"])      # visual IDs: -20 base points each
        full = pts[-1]
        S_b, S_l = np.exp(-pd["bay"] * before), np.exp(-pd["leak"] * before)
        bay_k = S_b * (1 - np.exp(-pd["bay"] * sc["mass"])); leak_k = S_l * (1 - np.exp(-pd["leak"] * sc["mass"]))
        pts_leak = pts + phi * (full - pts)
        S_b_T, S_l_T = np.exp(-pd["bay"] * cum[-1]), np.exp(-pd["leak"] * cum[-1])
        full_f = full * (1 - p_fuel) + full * 2 / 8 * p_fuel
        e_bay = float((bay_k * pts * ac["em"]).sum())
        e_leak = float((leak_k * pts_leak).sum()) * S_b_T
        e_land = S_b_T * S_l_T * full_f
        p_bay = 1 - S_b_T
        p_eject = p_bay + (1 - p_bay) * p_fuel
        rows.append((e_bay + e_leak + e_land, p_bay, 1 - S_l_T, p_eject * A.p_unlevel, float(cum[-1]), full))
    m = np.mean(rows, axis=0)
    return dict(value=float(m[0] - A.lam * m[3]), exp_pts=float(m[0]), p_bay=float(m[1]), p_leak=float(m[2]), p_fuel=p_fuel,
                p_death=float(m[3]), hits=float(m[4]), full=float(m[5]))
