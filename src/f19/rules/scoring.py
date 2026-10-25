"""Mission rating (formula decoded from END.EXE), penalties, kill points, and the score so far from the event list.

Source: docs/game/scoring-awards.md "Mission rating formula - decoded from END.EXE" and
docs/design/score_estimate_for_planner.md (reproduces debriefs 850 -> 212 and 833).
Cold War base: +1 per 64 game-s sample in an enemy 32 km cell, primary 140,
secondary 35 (70 without the primary); then score = score * m // 8 per multiplier.

plan.json "settings" (all optional; missing ones widen the range):
  {"roe": 0|1|2, "mission": "strike"|"air", "opponent": "green"|"regular"|"veteran"|"elite",
   "landing": "no_crashes"|"easy"|"realistic", "fuel_estimate": lb (briefing FUEL ESTIMATE)}
Caveats: the fuel multiplier is the briefing FUEL ESTIMATE (commData+0x2E is never written in flight);
radar IDs (event 7, -10 base) come in as id_penalty from the planner's alert simulation; visual IDs
(event 6, -20) are not modelled by the planner; kills are worth 0 in Cold War.
"""
import itertools

from f19.core.theater import Theater

THEATER = {t.value: t.score_mult for t in (Theater.LB, Theater.PG, Theater.NC, Theater.CE)}   # LB 8, PG 10, NC 12, CE 13
MISSION = {"air": 8, "strike": 9}
OPPONENT = {"green": 8, "regular": 11, "veteran": 14, "elite": 17}
OPPONENT_NAMES = list(OPPONENT)                   # opp 0..3 (ds:4EF0) -> name
LANDING = {"no_crashes": 5, "easy": 8, "realistic": 10}
PRIMARY = {0: 140, 1: 120, 2: 100}
FUEL_BUCKETS = (8, 9, 10, 11)

# --- penalties and caps (score_estimate_for_planner.md, END / EGAME 2026-10-17/18) -------------------------------------
BAIL_OUT_ENEMY = (2, 8)            # ejecting in an enemy cell: score * 2 // 8 (confirmed). Other cells use x3/8, x6/8
                                   # or x4/8, which where is not known yet: not modelled.
VISUAL_ID_BASE = -20               # event 6: visual ID by an aircraft, base points (Cold War)
RADAR_ID_BASE = -10                # event 7: radar ID by a site, base points (Cold War)
NON_ENEMY_KILL_BASE = -25          # destroying a target in a friendly / neutral cell
EVENT_LOG_MAX = 255                # flight-event records (EGAME 0x4b40 refuses at ds:632E >= 0xFF): later events are lost
SAMPLE_GAME_S = 64                 # one position sample (event 9) every 64 game s
SAMPLE_TICKS = 32                  # = 64 game s in clock ticks (ds:6630, 2 game s each)

# Kill points by target category (END 0x4258, 2026-10-18): category -> (Limited War, Conventional War) base points.
# Cold War: 0 for everything. 11 is not in the table; 13-15 (SA/SS-20 Missile, Oil Rig, Nuclear Plant) score 0.
KILL_POINTS = {0: (2, 4), 1: (6, 12), 2: (1, 3), 3: (1, 3), 4: (3, 6), 5: (8, 16), 6: (2, 4), 7: (2, 5), 8: (5, 11),
               9: (3, 7), 10: (1, 3), 12: (4, 8), 13: (0, 0), 14: (0, 0), 15: (0, 0)}
# gun matrix value per category (same table; 13-15 overflow into the next weapon's row)
GUN_VALUE = {0: 3, 1: 0, 2: 4, 3: 4, 4: 0, 5: 2, 6: 4, 7: 4, 8: 3, 9: 4, 10: 5, 12: 3}

# Event list (f19_state.read_event_list): (clock, x >> 7, y >> 7, type, flags, extra). Types used here:
EV_SAMPLE, EV_VISUAL_ID, EV_RADAR_ID = 9, 6, 7
EV_OBJECTIVE_TYPES = (1, 2, 3, 10, 11, 12)          # with flags 0x80 = primary, 0x40 = secondary
PRIMARY_FLAG, SECONDARY_FLAG = 0x80, 0x40


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


def id_penalty(radar_ids, visual_ids):
    """Base points lost to IDs (a positive number, as rating's id_penalty): 10 per radar ID, 20 per visual ID."""
    return -RADAR_ID_BASE * radar_ids - VISUAL_ID_BASE * visual_ids


def bail_out(score, enemy_cell=True):
    """Final score after ejecting: x2/8 in an enemy cell; elsewhere unknown (x3/8, x6/8 or x4/8): None."""
    if not enemy_cell: return None
    n, d = BAIL_OUT_ENEMY
    return score * n // d


def kill_points(category, roe):
    """Base points for destroying a target of `category` (low 4 bits) at roe 0/1/2 (enemy cell)."""
    if roe == 0: return 0
    return KILL_POINTS.get(category & 0x0F, (0, 0))[roe - 1]


def current_score(events, grid, theater, settings, opp=None):
    """The score so far from the game's own event list, as the HUD's score pane computes it (scripts/nav_tui.py
    _pane_score). events: [(clock, x >> 7, y >> 7, type, flags, extra)]; grid: 16 x 16 territory grid ([y, x]) or
    None; settings: plan.json settings; opp: 0..3 (sets settings["opponent"]), None = keep settings.
    -> dict(enemy, samples, primary, secondary, radar_ids, visual_ids, id_base, last_sample_clock; and when the
    theater is known: base, score (lo, hi), per_sample, assumed, or error)."""
    ev = events
    g = grid
    enemy = sum(1 for clk, x, y, typ, _, _ in ev if typ == EV_SAMPLE and g is not None and g[min(15, y >> 4), min(15, x >> 4)] & 3 == 0)
    samples = sum(1 for e in ev if e[3] == EV_SAMPLE)
    pri = any(e[4] & PRIMARY_FLAG and e[3] in EV_OBJECTIVE_TYPES for e in ev)
    sec = any(e[4] & SECONDARY_FLAG and e[3] in EV_OBJECTIVE_TYPES for e in ev)
    vis, rad = sum(1 for e in ev if e[3] == EV_VISUAL_ID), sum(1 for e in ev if e[3] == EV_RADAR_ID)
    out = dict(enemy=enemy, samples=samples, primary=pri, secondary=sec, radar_ids=rad, visual_ids=vis,
               id_base=RADAR_ID_BASE * rad + VISUAL_ID_BASE * vis,
               last_sample_clock=max((e[0] for e in ev if e[3] == EV_SAMPLE), default=None))
    if theater in ("CE", "NC", "LB", "PG"):
        s = dict(settings) if opp is None else dict(settings, opponent=OPPONENT_NAMES[opp])
        try:
            pen = id_penalty(rad, vis)
            (b0, b1), (s0, s1), assumed = estimate([enemy], theater, s, pri, sec, id_penalty=pen)
            _, (n0, n1), _ = estimate([enemy + 1], theater, s, pri, sec, id_penalty=pen)
            out.update(base=b0, score=(s0, s1), per_sample=(n1 - s1 + n0 - s0) / 2, assumed=assumed)
        except Exception as e:
            out["error"] = str(e)
    return out


def next_sample_s(last_sample_clock, clock):
    """Game s until the next 64 s sample (nav_tui: (last9 + 32 - clock) * 2, not below 0)."""
    return max(0, (last_sample_clock + SAMPLE_TICKS - clock) * 2)
