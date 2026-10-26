"""Speed, throttle and fuel along a route, from the game's own fuel rules (f19.rules.fuel).

Burn depends only on the throttle setting (lb per game minute: 50 % 60, 60 % 75, 70 % 90, 80 % 120);
speed at a setting depends on fuel weight, flaps, altitude and turn load. Fuel is score: every 64 game s
in an enemy cell is a sample, so the two regimes are
  enemy cells:            least lb per MINUTE that holds level (50 % + flaps: 64 lb per sample)
  friendly/neutral cells: least lb per KM (70 % clean: 956 lb/100 km full, 768 at 2000 lb)
and the radar may override either with a slower setting where it keeps a site from seeing the plane.

  regimes = Regimes(P); regimes.pick(enemy, fuel, alt, load) -> Setting
  tl = integrate(world, radar, P, pts)   timed route: t, fuel, kt, throttle, flaps, roll, bay per 1 km step
"""
import math
from functools import lru_cache
from collections import namedtuple
import numpy as np
from f19.rules import fuel as fuel_model

from .world import GROUND_KMH_PER_KT as KT_KMH, K

Setting = namedtuple("Setting", "thr flaps kt lb_min lb_km stall")
STEP_KM = 1.0
TANK_LB, TRANSFER_BELOW = fuel_model.TANK_LB, fuel_model.TRANSFER_BELOW   # Extra Fuel: +1900 lb per tank (EGAME 0x7ae6), nav transfers below 500 lb
PAD = 5                       # km a radar override is extended each way


@lru_cache(maxsize=None)
def _cruise(thr, flaps, alt100, fuel250, load):
    c = fuel_model.cruise(thr, alt100 * 100, fuel250 * 250, flaps, load=load)
    if c["stalled"]: return None
    return Setting(thr, flaps, c["kt"], c["lb_min"], c["lb_min"] / (c["kt"] * KT_KMH / 60), c["stall"])


def load_of(bank_deg):
    """ds:662A turn load (16 = wings level) for a level turn at this bank."""
    return int(round(16 / math.cos(math.radians(bank_deg))))


class Regimes:
    def __init__(self, P):
        self.P = P

    def options(self, fuel, alt, load=16, which=None):
        """All reachable settings that hold level, optionally restricted by a fixed throttle / flaps."""
        P = self.P
        thr = P[f"{which}_throttle"] if which else "auto"
        flp = P[f"{which}_flaps"] if which else "auto"
        out = []
        for t in ([thr] if thr != "auto" else P["throttles"]):
            for f in ([flp] if flp != "auto" else (False, True)):
                s = _cruise(int(t), bool(f), int(round(alt / 100)), int(round(max(fuel, 0) / 250)), load)
                if s: out.append(s)
        return out

    def pick(self, enemy, fuel, alt, load=16):
        """The regime's choice: enemy = least lb/min (ties: the most stall margin, i.e. flaps), else least lb/km."""
        opts = self.options(fuel, alt, load, "enemy" if enemy else "transit") or self.options(fuel, alt, load)
        if not opts: raise ValueError(f"no throttle holds level at {fuel:.0f} lb, {alt:.0f} ft, load {load}")
        key = (lambda s: (s.lb_min, -s.kt / s.stall)) if enemy else (lambda s: (s.lb_km, s.kt))
        return min(opts, key=key)

    def lb_per_sample(self, fuel, alt, load=16):
        return self.pick(True, fuel, alt, load).lb_min * self.P["sample_game_s"] / 60


def coldwar_credit(regimes, P):
    """Fraction of an enemy km's fuel paid back by the samples it earns (fuel profile's discount)."""
    f, a = P["fuel_ref_lb"], P["cruise_ft"]
    e = regimes.pick(True, f, a)
    km_per_sample = e.kt * KT_KMH / 3600 * P["sample_game_s"]
    return min(0.9, regimes.lb_per_sample(f, a) / km_per_sample / e.lb_km)


# ---------------------------------------------------------------- the timed route
def dense(pts, P):
    """Flown points -> 1 km steps with heading, altitude, turn bank and bay attrs (fuel/time not yet)."""
    xy = [np.asarray(p[0], float) for p in pts]
    kink = [0.0] + [_turn(xy[i - 1], xy[i], xy[i + 1]) for i in range(1, len(xy) - 1)] + [0.0]
    out = []
    for i, (a, b) in enumerate(zip(xy, xy[1:])):
        L = float(np.linalg.norm(b - a))
        if L <= 0: continue
        u = (b - a) / L
        at = pts[i][2] or {}
        loiter = at.get("kind") == "loiter"
        arc = kink[i] > 0.5 and kink[i + 1] > 0.5
        bank = P["loiter_bank_deg"] if loiter and arc else P["turn_bank_deg"] if arc else 0.0
        alt = at.get("alt_ft")                               # a probe's own altitude, else chosen in integrate()
        n = max(1, int(math.ceil(L / (STEP_KM * K))))
        for k in range(n):
            out.append(dict(xy=a + u * L * (k + 0.5) / n, u=u, km=L / n / K, bank=bank, alt=alt, leg=i,
                            loiter=loiter, label=pts[i][1] if k == 0 else ""))
    return out


def _turn(a, b, c):
    u, w = b - a, c - b
    nu, nw = np.linalg.norm(u), np.linalg.norm(w)
    if not nu or not nw: return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, float(u @ w) / nu / nw))))


def threat_alt(threat, fixed, fixed_alt, P, step_km):
    """Altitude per step: cruise_ft (fuel-optimal) unless a site would see the plane there (threat), then
    threat_ft, from alt_lead_km before to alt_lead_km after; high gaps shorter than min_high_km stay low."""
    n = len(threat); low = np.zeros(n, bool)
    lead = int(round(P["alt_lead_km"] / step_km))
    for k in np.flatnonzero(threat): low[max(0, k - lead):k + lead + 1] = True
    gap = int(round(P["min_high_km"] / step_km)); k = 0
    while k < n:
        if not low[k]:
            j = k
            while j < n and not low[j]: j += 1
            if k > 0 and j < n and j - k < gap: low[k:j] = True
            k = j
        else: k += 1
    return np.where(fixed, fixed_alt, np.where(low, P["threat_ft"], P["cruise_ft"]))


def integrate(world, radar, P, pts, bay_points=(), start_fuel=None, start_t=0.0, tanks_left=None):
    """Walk the route in game time. Each step flies cruise_ft unless a site would see the plane there
    (then threat_ft), picks its regime by cell side, and where a site would still see the plane (margin > -M)
    at that setting but not at a slower one that holds level, the slower one wins (radar override, padded
    PAD km each way). Returns dict(steps, fuel_land, t_end, overrides).

    The walk starts with start_fuel lb of internal fuel (default fuel_loaded_lb: takeoff), at game time start_t
    (default 0) with tanks_left Extra Fuel tanks not yet transferred (default extra_fuel_bays); an in-flight route
    passes the current fuel, clock and tanks (planner.api.route)."""
    reg = Regimes(P)
    D = dense(pts, P)
    f0 = float(P["fuel_loaded_lb"] if start_fuel is None else start_fuel)
    tanks0 = int(P["extra_fuel_bays"] if tanks_left is None else tanks_left)
    if not D: return dict(steps=[], fuel_land=P["fuel_loaded_lb"] if start_fuel is None else f0, t_end=float(start_t),
                          overrides=0)
    X = np.array([d["xy"][0] for d in D]); Y = np.array([d["xy"][1] for d in D])
    UX = np.array([d["u"][0] for d in D]); UY = np.array([d["u"][1] for d in D])
    enemy = world.enemy(X, Y)
    # Extra Fuel tanks weigh nothing and burn nothing until nav transfers one (fuel < TRANSFER_BELOW, +TANK_LB each,
    # scripts/phases.FuelTransfer); speeds use the internal fuel only. fuel0 = everything carried.
    fuel0 = (P["fuel_loaded_lb"] if start_fuel is None else f0) + TANK_LB * tanks0
    fuel = np.full(len(D), f0)
    bay = np.zeros(len(D), bool)
    choice = [None] * len(D)
    override = np.zeros(len(D), bool)
    fixed = np.array([d["alt"] is not None for d in D])
    fixed_alt = np.array([d["alt"] if d["alt"] is not None else 0.0 for d in D], float)
    alt = np.where(fixed, fixed_alt, P["cruise_ft"])
    roll = np.array([d["bank"] for d in D])
    for it in range(3):
        # altitude: would a site see the plane at cruise_ft (at the speed it would fly there)?
        hi = [reg.pick(bool(enemy[k]), fuel[k], P["cruise_ft"], load_of(D[k]["bank"])) for k in range(len(D))]
        threat = radar.seen_count(X, Y, UX, UY, np.array([s.kt for s in hi], float), P["cruise_ft"], roll, bay) > 0
        # fighters and the IL-76 can be anywhere: at this opponent level and above, low over ALL enemy ground (2026-10-22
        # Elite: cruising at 700 ft, +13 EMV, an IL-76 saw the plane at 50-58 km and a MiG-31 at 30 km)
        if world.opp >= P["low_in_enemy_from_opp"]: threat = threat | enemy
        alt = threat_alt(threat, fixed, fixed_alt, P, STEP_KM)
        # settings at the current fuel estimate and altitude
        base = [reg.pick(bool(enemy[k]), fuel[k], alt[k], load_of(D[k]["bank"])) for k in range(len(D))]
        choice = list(base)
        kt = np.array([s.kt for s in base], float)
        seen = radar.seen_count(X, Y, UX, UY, kt, alt, roll, bay)
        override[:] = False
        for k in np.flatnonzero(seen > 0):
            opts = sorted(reg.options(fuel[k], alt[k], load_of(D[k]["bank"])), key=lambda s: s.kt)
            best = None
            for s in opts:
                if s.kt >= base[k].kt: break
                n = radar.seen_count(X[k:k + 1], Y[k:k + 1], UX[k:k + 1], UY[k:k + 1], np.array([s.kt]), alt[k:k + 1],
                                     roll[k:k + 1], bay[k:k + 1])[0]
                if n < seen[k] and (best is None or n < best[1] or (n == best[1] and _better(s, best[0], enemy[k]))):
                    best = (s, n)
            if best:
                for j in range(max(0, k - PAD), min(len(D), k + PAD + 1)):
                    if choice[j].kt > best[0].kt: choice[j] = best[0]; override[j] = True
        # one setting through each overridden stretch: the slowest chosen within PAD km (no throttle chatter)
        if override.any():
            ov = list(choice)
            for k in range(len(D)):
                win = [ov[j] for j in range(max(0, k - PAD), min(len(D), k + PAD + 1)) if override[j]]
                if win:
                    s_ = min(win, key=lambda s: s.kt)
                    if s_.kt < choice[k].kt: choice[k] = s_; override[k] = True
        # nav holds one airspeed with the throttle, whatever the plan's setting would give (2026-10-21 Katowice: 191 kt
        # mean, 40 % for a quarter of the time when light; 221 game min flown vs 193 planned at the model's 50 % speeds)
        if P.get("nav_hold_kt"):
            choice = [c._replace(kt=int(P["nav_hold_kt"]), lb_min=P["nav_lb_min"]) for c in choice]
        # integrate
        t = np.zeros(len(D)); f = f0; tt = float(start_t); tanks = tanks0; transfers = []
        for k, d in enumerate(D):
            s = choice[k]
            fuel[k] = f; t[k] = tt
            dt = d["km"] / (s.kt * KT_KMH) * 3600
            tt += dt; f -= s.lb_min * dt / 60 * P["burn_factor"]
            if f < TRANSFER_BELOW and tanks:
                f += TANK_LB; tanks -= 1; transfers.append(dict(t=tt, km=sum(x["km"] for x in D[:k + 1]), xy=d["xy"]))
        # bay open windows (photo / Maverick launch): bay_open_s before the point
        bay[:] = False
        for k, d in enumerate(D):
            if d["label"] and d["label"] in bay_points:
                bay[(t >= t[k] - P["bay_open_s"]) & (t <= t[k] + 3.0)] = True
    steps = [dict(d, alt=float(alt[k]), t=float(t[k]), fuel=float(fuel[k]), kt=choice[k].kt, thr=choice[k].thr, flaps=choice[k].flaps,
                  lb_min=choice[k].lb_min, enemy=bool(enemy[k]), bay=bool(bay[k]), override=bool(override[k]))
             for k, d in enumerate(D)]
    return dict(steps=steps, fuel_land=float(f + TANK_LB * tanks), t_end=float(tt), fuel0=fuel0, overrides=int(override.sum()),
                transfers=transfers, tanks_left=tanks)


def _better(a, b, enemy):
    return (a.lb_min, a.kt) < (b.lb_min, b.kt) if enemy else (a.lb_km, a.kt) < (b.lb_km, b.kt)


def samples(world, steps, P, phases=8):
    """Cold-war samples every 64 game s from takeoff, for `phases` unknown offsets -> (min, max, [(xy, enemy)])."""
    if not steps: return 0, 0, []
    t = np.array([s["t"] for s in steps]); X = np.array([s["xy"][0] for s in steps]); Y = np.array([s["xy"][1] for s in steps])
    T = t[-1]; counts = []; first = []
    for ph in np.arange(phases) / phases:
        ts = (np.arange(int(T / P["sample_game_s"] - ph) + 1) + ph) * P["sample_game_s"]
        px, py = np.interp(ts, t, X), np.interp(ts, t, Y)
        e = world.enemy(px, py)
        counts.append(int(e.sum()))
        if not first: first = list(zip(zip(px, py), e))
    return min(counts), max(counts), first


def legs(steps):
    """Runs of one (throttle, flaps) setting: [(from km, to km, thr, flaps, kt range, enemy share, overridden)]."""
    out, cum = [], 0.0
    for s in steps:
        key = (s["thr"], s["flaps"], int(s["alt"]))
        if out and out[-1]["key"] == key:
            o = out[-1]; o["km1"] = cum + s["km"]; o["kts"].append(s["kt"]); o["enemy"] += s["enemy"]; o["n"] += 1
            o["override"] |= s["override"]; o["t1"] = s["t"]; o["fuel1"] = s["fuel"]
        else:
            out.append(dict(key=key, km0=cum, km1=cum + s["km"], kts=[s["kt"]], enemy=int(s["enemy"]), n=1,
                            override=s["override"], t0=s["t"], t1=s["t"], fuel0=s["fuel"], fuel1=s["fuel"]))
        cum += s["km"]
    # merge runs shorter than 3 km into the previous one for the report
    merged = []
    for o in out:
        if merged and o["km1"] - o["km0"] < 3 and not o["override"]:
            m = merged[-1]; m["km1"] = o["km1"]; m["kts"] += o["kts"]; m["t1"] = o["t1"]; m["fuel1"] = o["fuel1"]
        else: merged.append(o)
    return merged


KEEP_WHOLE = ("runin", "launch", "photo", "overfly", "takeoff", "climb", "approach", "final", "landing")
CARRY = ("probe", "loiter", "loiter_runin", "loiter_runout")


def split_at_alt(pts, steps):
    """Insert collinear points where the planned altitude changes, so each leg has one altitude for nav
    (nav holds the alt_ft of the point a leg ends at). Legs ending at an attack / takeoff / landing point
    are never split: nav keys its attack and landing logic on that point's kind; they fly the lowest
    altitude any of their steps needs (leg_alt)."""
    cuts = {}
    for a, b in zip(steps, steps[1:]):
        if a["alt"] == b["alt"] or b["leg"] != a["leg"]: continue
        i = b["leg"]
        if (pts[i + 1][2] or {}).get("kind") in KEEP_WHOLE: continue
        q = np.asarray(b["xy"], float) - np.asarray(b["u"], float) * b["km"] / 2 * K
        if min(np.linalg.norm(q - np.asarray(pts[i][0], float)), np.linalg.norm(q - np.asarray(pts[i + 1][0], float))) < 0.5 * K:
            continue
        cuts.setdefault(i, []).append(q)
    if not cuts: return pts
    out = []
    for i, p in enumerate(pts):
        out.append(p)
        if i in cuts:
            end = pts[i + 1][2] or {}
            at = {k: v for k, v in end.items() if k in ("probe", "alt_ft", "speed_kt", "loiter")}
            at["kind"] = end.get("kind") if end.get("kind") in CARRY else "cruise"
            for q in sorted(cuts[i], key=lambda q: np.linalg.norm(q - np.asarray(p[0], float))):
                out.append((tuple(q), "", at))
    return out


def leg_alt(steps, n_pts, P):
    """alt_ft per route point = the altitude of the leg ending there (the lowest of its steps)."""
    alt = [None] * n_pts
    for s_ in steps:
        i = s_["leg"] + 1
        alt[i] = s_["alt"] if alt[i] is None else min(alt[i], s_["alt"])
    alt[0] = P["cruise_ft"]
    return [a if a is not None else P["cruise_ft"] for a in alt]
