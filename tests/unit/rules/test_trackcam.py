"""f19.rules.trackcam: the decoded B / "/" pick (EGAME 0xab84-0xacbc), synthetic cases + the recorded flights.

The memlog tests replay the rule on snapshots of three DAY flights (ds:4EF4 = 0; difficulty does not enter the rule):
1793126949 (record 44 alone in the cone, the wanted record 2 not selectable), 1792724414 (the cycle 2, 6, 34, 16, 38),
1793382037 (second attack, the unexplained station-1 case: the rule predicts 7 -> 1, the game never moved).
"""
import pytest

from conftest import S, need
from f19.memory import reader as R
from f19.rules import trackcam as T

K = 64


def rec(i, east_km, north_km, flags=1, me=(0, 0)):
    """A record `east_km` east and `north_km` north of the player at `me` (y grows south)."""
    return i, me[0] + east_km * K, me[1] - north_km * K, flags


def cands(records, heading=0, night=0, view=0, mode=2):
    return T.candidates((0, 0), heading, view, night, records, mode)


# ------------------------------------------------------------------------------------------------ the filter
def test_cone_is_45_degrees_either_side():
    c = cands([rec(1, 0, 10), rec(2, 9.9, 10), rec(3, 10.1, 10), rec(4, -9.9, 10), rec(5, 0, -10)])
    assert {x.index for x in c} == {1, 2, 4}                    # 9.9 km east at 10 km north is 44.7 deg; 10.1 is past 45


def test_sorted_by_octagonal_distance_then_index():
    c = cands([rec(9, 0, 20), rec(3, 0, 10), rec(7, 0, 10), rec(5, 4, 5)])
    assert [x.index for x in c] == [5, 3, 7, 9]                 # 5 + 4/2 = 7 km; 10, 10 (tie: lower index first); 20
    assert [x.dist for x in c] == [448, 640, 640, 1280]


def test_range_100_km_day_50_km_night():
    r = [rec(1, 0, 99), rec(2, 0, 101), rec(3, 0, 49), rec(4, 0, 51)]
    assert {x.index for x in cands(r)} == {1, 3, 4}
    assert {x.index for x in cands(r, night=1)} == {3}
    assert T.range_km(0) == 100 and T.range_km(1) == 50


def test_destroyed_and_mode_filters():
    r = [rec(1, 0, 10, flags=0x81), rec(2, 0, 11, flags=0), rec(3, 0, 12, flags=1)]
    assert [x.index for x in cands(r)] == [2, 3]                # air-to-ground takes friendlies, never the destroyed
    assert [x.index for x in cands(r, mode=0)] == [3]           # air-to-air: enemy side only


def test_view_offset_turns_the_cone():
    r = [rec(1, 0, 10), rec(2, 10, 0)]
    assert [x.index for x in cands(r)] == [1]
    assert [x.index for x in cands(r, view=0x4000)] == [2]      # looking 90 deg right


# ------------------------------------------------------------------------------------------------- the stepping
R5 = [rec(2, 0, 10), rec(6, 0, 12), rec(34, 0, 15), rec(16, 0, 18), rec(38, 0, 25)]


def test_b_walks_outward_and_wraps():
    c = cands(R5)
    seq, lock = [], 2
    for _ in range(6):
        lock = T.b_next(lock, c); seq.append(lock)
    assert seq == [6, 34, 16, 38, 2, 6]                         # the cycle seen in flight 1792724414 (wrap = nearest)


def test_lock_outside_cone_restarts_from_nearest():
    c = cands(R5)
    assert T.b_next(99, c, None) == 2                           # a lock not in the cone: floor 0
    assert T.b_next(-1, c) == 2 and T.b_next(0xFFFF, c) == 2 and T.b_next(None, c) == 2


def test_pending_bit_is_not_part_of_the_index():
    assert T.lock_index(0x86) == 6 and T.lock_index(0xFFFF) is None and T.lock_index(-1) is None and T.lock_index(0) == 0


def test_single_candidate_wraps_onto_itself():
    c = cands([rec(44, 0, 10)])
    assert T.b_next(44, c) == 44 and T.b_next(-1, c) == 44
    assert T.b_next(44, []) is None


def test_presses_to_lock():
    c = cands(R5)
    assert T.presses_to_lock(38, 2, c) == 4 and T.presses_to_lock(2, 38, c) == 1 and T.presses_to_lock(2, 2, c) == 0
    assert T.presses_to_lock(34, -1, c) == 2                    # -1 resolves to the nearest on its own
    assert T.presses_to_lock(77, 2, c) is None                  # not a candidate


def test_slash_is_cheaper_when_the_wanted_is_nearer_than_the_lock():
    c = cands(R5)
    assert T.best_plan(6, 38, c) == T.Plan(False, 2)            # 38 -> 2 -> 6
    assert T.slash_then_presses(2, c) == T.Plan(True, 0)
    assert T.best_plan(2, 16, c) == T.Plan(True, 0)             # 16 -> 38 -> 2 is 2 keys; "/" is 1
    assert T.best_plan(2, 38, c) == T.Plan(False, 1)            # one wrap press ties "/" (1 key): keep B
    far = cands([rec(i, 0, 10 + i) for i in range(1, 12)])
    assert T.best_plan(1, 11, far) == T.Plan(False, 1)          # wraps in one press
    assert T.best_plan(1, 5, far) == T.Plan(True, 0)            # 7 presses outward vs "/" and done


def test_lock_floor_ignores_a_lock_outside_the_cone():
    recs = R5 + [rec(50, 20, 0)]                                # 90 deg off the nose
    assert T.lock_floor(6, (0, 0), 0, 0, recs) == 12 * K
    assert T.lock_floor(50, (0, 0), 0, 0, recs) is None and T.lock_floor(-1, (0, 0), 0, 0, recs) is None


def test_selectable_reasons():
    ok = lambda x, y, fl=1, **kw: T.selectable(1, (0, 0), kw.get("hdg", 0), 0, kw.get("night", 0), (x * K, -y * K, fl))
    assert ok(0, 50) == (True, "")
    assert ok(0, 50, 0x81) == (False, "destroyed")
    assert ok(0, 120) == (False, "out_of_range")
    assert ok(0, 60, night=1) == (False, "out_of_range")
    assert ok(30, 5) == (False, "out_of_cone")
    assert ok(30, 5, hdg=80) == (True, "")


def test_planner_geometry_margins():
    assert T.SAFE_CONE_DEG < T.CONE_DEG and T.SAFE_RANGE_KM_DAY < 100 and T.SAFE_RANGE_KM_NIGHT < 50
    assert T.acquisition_ok((0, 0), 0, (0, -80 * K), 0)
    assert not T.acquisition_ok((0, 0), 0, (0, -95 * K), 0)
    assert T.acquisition_ok((0, 0), 0, (0, -47 * K), 1) is False
    assert not T.acquisition_ok((0, 0), 40, (0, -10 * K), 0)    # 40 deg off the nose is inside the game's cone, outside ours
    lo, hi = T.heading_window((0, 0), (0, -10 * K))
    assert (lo, hi) == (325.0, 35.0)


# ------------------------------------------------------------------------------------------ recorded flights
def _states(run):
    path = need(S.logs / "memlog" / run, f"memlog {run}")
    rr = R.ReplayReader(path)
    for t, s in rr.states():
        yield t, s, R.read_trackcam_view(rr.emu)


def _near(run, t0):
    return next((s, cam) for t, s, cam in _states(run) if t >= t0)


def _cset(s, cam):
    return [(c.index, c.dist) for c in T.candidates(s.xy, s.heading, cam.view, cam.night, cam.records, 2)]


def test_flight_1793126949_wanted_record_2_is_outside_the_cone():
    s, cam = _near("1793126949", 1793129383.5)               # nav t ~ 2450 of flight 1793126934
    assert cam.screen == 0x13 and cam.night == 0
    assert [i for i, _ in _cset(s, cam)] == [44]
    rec2 = next(r for r in cam.records if r[0] == 2)
    assert T.selectable(2, s.xy, s.heading, cam.view, cam.night, rec2[1:]) == (False, "out_of_cone")
    assert T.selectable(44, s.xy, s.heading, cam.view, cam.night, cam.records[44][1:])[0]
    c = T.candidates(s.xy, s.heading, cam.view, cam.night, cam.records, 2)
    assert T.b_next(44, c) == 44 and T.presses_to_lock(2, 44, c) is None      # B reads 44 -> -1 -> 44 forever


def test_flight_1792724414_cycle_2_6_34_16_38():
    s, cam = _near("1792724414", 1792725742.3)
    assert [i for i, _ in _cset(s, cam)] == [2, 6, 34, 16, 38]
    c = T.candidates(s.xy, s.heading, cam.view, cam.night, cam.records, 2)
    lock, seq = 38, []
    for _ in range(5): lock = T.b_next(lock, c); seq.append(lock)
    assert seq == [2, 6, 34, 16, 38]
    locks = {s2.systems.locked & 0x7F for t, s2, c2 in _states("1792724414") if 1792725731 <= t <= 1792725780 and s2.systems.locked != 0xFFFF}
    assert locks <= {2, 6, 34, 16, 38}                          # every lock the game took in that stretch is in the cone set


def test_flight_1793382037_second_attack_rule_predicts_7_then_1():
    s, cam = _near("1793382037", 1793383628.0)
    assert cam.screen == 0x13 and s.systems.station == 1 and s.systems.locked == 7
    c = T.candidates(s.xy, s.heading, cam.view, cam.night, cam.records, 2)
    assert [(x.index, x.dist) for x in c][:2] == [(7, 527), (1, 847)]
    assert T.presses_to_lock(1, 7, c) == 1                      # the rule says one press; the game never did it (open question)
