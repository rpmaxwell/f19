"""Estimated mission rating for a planned route (formula decoded from END.EXE).

Source: FINDINGS.md "Mission rating formula - decoded from END.EXE" and
notes/score_estimate_for_planner.md (reproduces debriefs 850 -> 212 and 833).
Cold War base: +1 per 64 game-s sample in an enemy 32 km cell, primary 140,
secondary 35 (70 without the primary); then score = score * m // 8 per multiplier.

plan.json "settings" (all optional; missing ones widen the range):
  {"roe": 0|1|2, "mission": "strike"|"air", "opponent": "green"|"regular"|"veteran"|"elite",
   "landing": "no_crashes"|"easy"|"realistic", "fuel_estimate": lb (briefing FUEL ESTIMATE)}
Caveats: the fuel multiplier is the briefing FUEL ESTIMATE (commData+0x2E is never written in flight);
radar IDs (event 7, -10 base) come in as id_penalty from the planner's alert simulation; visual IDs
(event 6, -20) are not modelled; kills are worth 0 in Cold War.
"""
import itertools

THEATER = {"LB": 8, "PG": 10, "NC": 12, "CE": 13}
MISSION = {"air": 8, "strike": 9}
OPPONENT = {"green": 8, "regular": 11, "veteran": 14, "elite": 17}
LANDING = {"no_crashes": 5, "easy": 8, "realistic": 10}
PRIMARY = {0: 140, 1: 120, 2: 100}
FUEL_BUCKETS = (8, 9, 10, 11)


def fuel_mult(lb):
    return 11 if lb >= 11500 else 10 if lb >= 9000 else 9 if lb >= 7000 else 8


def rating(enemy_samples, theater, s, primary=True, secondary=True, fuel_m=None, id_penalty=0):
    roe = s.get("roe", 0)
    base = enemy_samples if roe == 0 else enemy_samples // 4 if roe == 1 else 0
    base -= id_penalty if roe == 0 else 0
    base += PRIMARY[roe] if primary else 0
    base += (35 if primary else 70) if secondary else 0
    score = max(base, 0)
    fm = fuel_m if fuel_m is not None else fuel_mult(s["fuel_estimate"])
    for m in (THEATER[theater], MISSION[s["mission"]], OPPONENT[s["opponent"]], LANDING[s["landing"]], fm):
        score = score * m // 8
    return base, score


def estimate(enemy_range, theater, settings, primary=True, secondary=True, id_penalty=0):
    """-> (base range, score range, list of assumptions for unknown settings)."""
    opts, assumed = {}, []
    for key, table in (("mission", MISSION), ("opponent", OPPONENT), ("landing", LANDING)):
        if key in settings: opts[key] = [settings[key]]
        else: opts[key] = list(table); assumed.append(f"{key} unknown: any of {', '.join(table)}")
    # unknown briefing FUEL ESTIMATE: any of x9 .. x11 (Katowice 2026-10-21 scored x11: 1830 vs a 1560 ceiling at x10)
    fms = [fuel_mult(settings["fuel_estimate"])] if "fuel_estimate" in settings else [9, 10, 11]
    if "fuel_estimate" not in settings: assumed.append("fuel estimate unknown: x9/8 .. x11/8 (give --fuel-estimate)")
    bases, scores = [], []
    for n in enemy_range:
        for combo in itertools.product(*opts.values()):
            s = {**settings, **dict(zip(opts, combo))}
            for fm in fms:
                b, sc = rating(n, theater, s, primary, secondary, fm, id_penalty); bases.append(b); scores.append(sc)
    return (min(bases), max(bases)), (min(scores), max(scores)), assumed
