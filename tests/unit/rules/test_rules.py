"""f19.rules against the tables in docs/game/*.md, plus verbatim copies of the old helpers they replaced."""
import math
import random

import pytest

from conftest import S, need
from f19.rules import flight, fuel, missiles as M, radar as R, scoring, weapons as W


# --- radar (docs/game/radar.md) -------------------------------------------------------------------------------------
# radar type: (name, R km, P, doppler, SAM range km)
RADAR_TABLE = {1: ("SA-2", 200, 3, False, 125), 2: ("SA-5", 350, 2, False, 150), 3: ("SA-8B", 125, 5, False, 65),
               4: ("SA-10", 320, 7, True, 125), 5: ("SA-11", 200, 5, False, 100), 6: ("SA-12", 290, 6, True, 150),
               7: ("SA-13", 125, 3, False, 65), 8: ("SA-N-4", 200, 4, True, 30), 9: ("SA-N-5", 150, 3, False, 30),
               10: ("SA-N-6", 320, 6, True, 125), 11: ("SA-N-7", 200, 5, False, 100)}


def test_radar_table(egame):
    for t, (name, r, p, dop, sam) in RADAR_TABLE.items():
        assert R.radar_spec(t) == (r, p, dop) and R.sam_range_km(t) == sam and R.weapon_name(t) == name


def test_emv_terms():
    alt = lambda ft: R.emv(ft, 0, 2)                                    # Veteran altitude term
    # radar.md says 56 at 10000 ft; the code floors alt // 3 first (3333 x 3 = 9999 -> 55); memlog EMV is exact +-1
    assert [alt(a) for a in (300, 1000, 2000, 10000)] == [5, 18, 31, 55]
    bank = lambda deg: R.emv(0, 0, 2, roll=int(deg / 360 * 65536)) - R.emv(0, 0, 2)
    assert bank(30) == 10 and bank(90) == 32
    assert R.emv(0, 200, 0) == 5 and R.emv(0, 400, 0) == 10
    assert R.emv(0, 0, 0, bay=True, ecm=True) == 40


def test_site_return_rules(egame):
    args = dict(px=64 * 50, py=0, alt=300, hdg=0x4000, kt=200, opp=2, sx=0, sy=0, t=1)
    assert R.site_return(clutter=0, **args) == 0                         # mountain cell: invisible
    flat, sea, rough = (R.site_return(clutter=c, **args) for c in (8, 12, 4))
    assert sea > flat > rough > 0
    assert R.site_return(clutter=8, **dict(args, px=64 * 250)) <= 0      # beyond R
    assert R.site_return(clutter=8, **dict(args, px=10, alt=30000)) == 0  # cone above the site
    # pulse radar: tangential (hdg 0 at a site due west... plane east of site flying north) > radial (flying east)
    assert R.site_return(clutter=8, **dict(args, hdg=0)) > R.site_return(clutter=8, **dict(args, hdg=0x4000))
    assert R.seen(51, 50) and not R.seen(50, 50)


def test_site_return_np_matches(egame):
    import numpy as np
    g = random.Random(3)
    for _ in range(300):
        a = [g.randrange(0, 32768), g.randrange(0, 32768), g.randrange(0, 40000), g.randrange(65536), g.randrange(100, 600),
             g.randrange(4), g.randrange(256), g.randrange(32768), g.randrange(32768), g.randrange(1, 23)]
        assert int(R.site_return_np(*[np.array([v]) if i < 5 or i == 6 else v for i, v in enumerate(a)])[0]) == R.site_return(*a)


def test_alert_and_duty():
    assert R.alert_step(0, True, 2) == 48 and R.alert_step(0, True, 2, ecm=True) == 24
    assert R.alert_step(250, True, 3) == 255 and R.alert_step(10, False, 3) == 0
    assert R.pulse_period_ticks(0) == 16
    assert R.duty_fraction() == 0.375 and round(R.duty_fraction(recent_network_alert=True), 2) == 0.56
    assert R.duty_active(5, 12345, alert=1) and R.duty_active(0, 0)
    assert R.THRESHOLDS == dict(network_alert=0x7F, launch=0xC0, radar_id=0xFA)


def test_aircraft_table(egame):
    # docs/game/aircraft.md: type, name, doppler?, R, P, missile, missile km, max m/s
    for t, name, dop, r, p, mis, km, ms in ((0, "MIG-23", False, 50, 5, "AA-8", 12, 154), (3, "MIG-31", True, 100, 8, "AA-9", 82, 164),
                                           (5, "IL-76", True, 200, 8, "AA-2", 14, 83), (6, "F-4E", False, 70, 6, "AA-6", 50, 166)):
        a = R.aircraft_spec(t)
        assert a["name"].upper().startswith(name.upper()) and a["max_speed_ms"] == ms
        assert (a["doppler"], a["radar_range"], a["radar_power"], a["missile"], a["missile_km"]) == (dop, r, p, mis, km)
    assert R.aircraft_spec(8)["radar_type"] is None and 8 in R.FRIENDLY_TYPES


def _old_game_dist(dx, dy):                       # analysis/radar_game.py before WP-3
    ax, ay = abs(dx), abs(dy)
    return min(0x7FFF, max(ax, ay) + min(ax, ay) // 2)


def _old_gdist(dx, dy):                           # analysis/missile_game.py before WP-3
    ax, ay = abs(dx), abs(dy)
    return min(0x7FFF, (ax + (ay >> 1)) if ax > ay else (ay + (ax >> 1)))


def _old_s16(v): v &= 0xFFFF; return v - 0x10000 if v > 0x7FFF else v


def test_helpers_equal_old_copies():
    g = random.Random(1)
    for _ in range(20000):
        dx, dy = g.randrange(-70000, 70000), g.randrange(-70000, 70000)
        assert R.game_dist(dx, dy) == _old_game_dist(dx, dy) == M.gdist(dx, dy) == _old_gdist(dx, dy)
        assert M.s16(dx) == _old_s16(dx)
        assert R.angle(dx, dy) == int(round(math.atan2(dx, dy) / (2 * math.pi) * 65536)) & 0xFFFF
    assert M.atan2g(0, 0) == 0x8000 and M.atan2g(0, 5) == 0 and M.atan2g(5, 0) == 0x4000


# --- missiles (docs/game/missiles-countermeasures.md) ---------------------------------------------------------------
def test_specs(egame):
    s = M.SPECS
    assert s is M.specs() and len(s) == 0x26
    assert s[0x21]["name"] == "SA-14" and s[0x21]["cls"] == 0 and s[0x21]["range_km"] == 16
    assert s[4]["cls"] == 3 and s[20]["name"] == "AA-9"


def test_cm_and_jam_rules():
    assert M.cm_eligible(M.FLARE, 0, 0, 0, 2) and M.cm_eligible(M.FLARE, -1, 0, 0, 2) and not M.cm_eligible(M.FLARE, 2, 0, 0, 2)
    assert M.cm_eligible(M.CHAFF, 1, 0, 0, 0) and M.cm_eligible(M.CHAFF, 2, 0, 0, 0) and not M.cm_eligible(M.CHAFF, 0, 0, 0, 0)
    assert M.cm_eligible(M.CHAFF, 3, 0x4000, 0, 3) and not M.cm_eligible(M.CHAFF, 3, 0, 0, 3)   # class 3: only across
    assert not M.jammed(2, 10000, 0, 2, False, 4)                                           # no jammer, net fresh
    assert M.jammed(2, (2 << 8) + 0x181, 0x10, 2, False, 4) and not M.jammed(2, (2 << 8) + 0x180, 0x10, 2, False, 4)
    assert M.jammed(1, 1, 0x10, 2, False, 1)                                                # command: any range
    assert M.jammed(0, 10000, 0x20, 2, False, 7) and not M.jammed(0, 10000, 0, 2, True, 0x21)  # SA-14 exempt from net
    assert M.clamp(-20000, -100, 100) == 100 and M.clamp(-200, -100, 100) == -100 and M.idiv(-7, 2) == -3


def test_world_cm_slots():
    w = M.World(opp=2, fs=4)
    assert w.release(M.FLARE, 1, 2) and w.cms[3][:3] == [1, 2, M.FLARE] and w.cms[3][3] == (9 - 4) * 4
    assert w.release(M.CHAFF, 1, 2) and w.cms[2][2] == M.CHAFF
    assert w.release(M.FLARE, 1, 2) and not w.release(M.FLARE, 1, 2)     # slots 1-3 full: wasted
    w.age(); assert [c[3] for c in w.cms[1:]] == [19, 20, 20]             # only the lowest live slot ages
    assert w.release(M.DECOY, 0, 0) and w.decoy and w.cms[0][3] == 5 * 4 * 8


def test_simulate_head_on_hit(egame):
    m = M.Missile(0, 1000, 3000, 300, M.SPECS[1]["speed"], 0, 0, 400, 1, 5)     # SA-2 below the player, flying north
    r = M.simulate(m, M.World(opp=3, fs=4), lambda k: (1000, 1000, 300, 0x8000), 400)
    assert r["outcome"] == "hit" and r["locked_frames"] > 0
    away = M.Missile(0, 1000, 3000, 300, M.SPECS[1]["speed"], 0x8000, 0, 400, 1, 5)
    assert M.simulate(away, M.World(opp=3, fs=4), lambda k: (1000, 1000, 300, 0), 400)["outcome"] != "hit"


# --- fuel (docs/game/fuel.md) ----------------------------------------------------------------------------------------
def test_fuel():
    assert fuel.burn_step(0) == 0 and fuel.burn_step(50) == 4 and fuel.burn_step(100) == 12 and fuel.burn_step(50, leak=3) == 7
    assert fuel.burn_step(50) * 16 == 64                                   # 64 lb per 64 s sample at 50 %
    c = fuel.cruise(50, 300, 8000, True)
    assert not c["stalled"] and c["lb_min"] == 60 and c["stall"] == 90
    assert fuel.cruise(5)["stalled"]
    assert fuel.TANK_LB == 1900 and fuel.TRANSFER_BELOW == 500


def test_fuel_constants_mirror_fuelplan():
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[3] / "analysis/planner/fuelplan.py").read_text()
    tank, below = re.search(r"^TANK_LB, TRANSFER_BELOW = ([\d.]+), ([\d.]+)", src, re.M).groups()
    assert (float(tank), float(below)) == (fuel.TANK_LB, fuel.TRANSFER_BELOW)


# --- weapons (docs/game/weapons.md) ---------------------------------------------------------------------------------
def test_ordnance_tables(egame):
    loadout, m = W.tables()
    assert len(loadout) == 20 and len(m) == 20 * W.NCAT + 3 * W.NCAT
    assert "Maverick" in loadout[5]["long"] or "65" in loadout[5]["short"]
    assert all(-128 <= W.compat(m, w, c) <= 127 for w in range(20) for c in range(16))
    assert W.compat(m, 5, 0) == 2                                          # Maverick vs category 0 (Sub Pen): 2


def test_ccip_flight_example():
    # 2026-10-18 flight: alt 626 ft at -3.7 deg, fs 5 (r 22): v (1, 22, -32 ft), impact 20 frames out = 440 units
    x, y, d = W.ccip(1000, 1000, 626, -3.7, 0.0, 5, 20)
    assert (x, y, d) == (1000, 1000 - 440, 440)
    assert W.ccip(1000, 1000, 626, 2.0, 0.0, 5, 20) is None              # nose up: never hits
    assert W.octo(3, -4) == 5.5 and W.gun_kill_radius(0) == 12


def test_release_geometry():
    assert W.mav_drop(300, 22.5) == 20                                     # no pitch to recover: just the spawn drop
    assert W.mav_drop(300, 0) > W.mav_drop(300, 15) > 20
    lo, hi = W.photo_window_km(150, 0)
    assert 0 < lo < hi and (W.photo_window_km(150, 3) or (lo, lo))[1] <= hi
    assert abs(W.photo_score(1000, 150, W.photo_pitch(150, 1000 / 64)) - 1000) < 1e-6


# --- scoring (docs/design/score_estimate_for_planner.md) ------------------------------------------------------------
TOP = dict(roe=0, mission="strike", opponent="elite", landing="realistic", fuel_estimate=11500)


def test_rating_top_settings():
    base, score = scoring.rating(0, "CE", TOP)
    assert base == 175 and 1150 < score < 1170                              # "primary + secondary ~1160"
    per = (scoring.rating(100, "CE", TOP)[1] - scoring.rating(0, "CE", TOP)[1]) / 100
    assert 6.5 < per < 6.9                                                   # "each enemy sample ~6.7 points"
    assert scoring.rating(10, "CE", dict(TOP, roe=2))[0] == 100 + 35         # samples count only in Cold War
    assert scoring.rating(0, "CE", TOP, primary=False)[0] == 70
    assert [scoring.fuel_mult(v) for v in (6999, 7000, 9000, 11500)] == [8, 9, 10, 11]


def test_estimate_ranges():
    (b0, b1), (s0, s1), assumed = scoring.estimate([20], "CE", dict(roe=0))
    assert b0 == b1 == 195 and s0 < s1 and len(assumed) == 4
    _, (t0, t1), none = scoring.estimate([20], "CE", TOP)
    assert t0 == t1 and not none


def test_penalties_and_kills():
    assert scoring.id_penalty(radar_ids=2, visual_ids=1) == 40
    assert scoring.bail_out(1000) == 250 and scoring.bail_out(1000, enemy_cell=False) is None
    assert scoring.kill_points(5, 0) == 0 and scoring.kill_points(5, 1) == 8 and scoring.kill_points(5, 2) == 16
    assert scoring.kill_points(8, 2) == 11 and scoring.kill_points(14, 2) == 0 and scoring.kill_points(0x81, 1) == 6
    assert scoring.EVENT_LOG_MAX == 255 and scoring.NON_ENEMY_KILL_BASE == -25


def test_current_score():
    import numpy as np
    g = np.full((16, 16), 3, np.uint8); g[5, 5] = 0                          # one enemy cell (x, y cell 5)
    cx = (5 << 11) >> 7                                                      # event cells are x >> 7
    ev = [(100, cx, cx, 9, 0, 0), (132, cx, cx, 9, 0, 0), (164, 0, 0, 9, 0, 0), (170, 0, 0, 2, 0x80, 0),
          (180, 0, 0, 7, 0, 0), (190, 0, 0, 6, 0, 0)]
    r = scoring.current_score(ev, g, "CE", dict(TOP), opp=3)
    assert (r["enemy"], r["samples"], r["primary"], r["secondary"], r["radar_ids"], r["visual_ids"]) == (2, 3, True, False, 1, 1)
    assert r["id_base"] == -30 and r["last_sample_clock"] == 164
    assert r["base"] == 2 - 30 + 140 and r["score"][0] == r["score"][1] == scoring.rating(2, "CE", TOP, True, False, None, 30)[1]
    assert r["per_sample"] > 0 and scoring.next_sample_s(164, 180) == 32
    assert "base" not in scoring.current_score(ev, g, "XX", {}) and scoring.current_score([], None, "CE", TOP)["enemy"] == 0


# --- flight (docs/game/landing-takeoff.md) --------------------------------------------------------------------------
def test_landing_limits():
    assert flight.roll_limit_deg(flight.EASY) == 33.75 and flight.roll_limit_deg(flight.REALISTIC) == 16.875
    assert flight.roll_limit16(flight.NO_CRASHES) is None
    assert flight.sink_limit(flight.REALISTIC) == 64 and flight.sink_limit(flight.EASY, carrier=True) == 256
    assert not flight.touchdown_crash(flight.NO_CRASHES, 1000, 0x4000, True)
    assert flight.touchdown_crash(flight.REALISTIC, 65, 0, False) and not flight.touchdown_crash(flight.REALISTIC, 64, 0, False)
    assert flight.touchdown_crash(flight.EASY, 0, 0, True)
    assert flight.touchdown_crash(flight.REALISTIC, 0, -((12 << 8) + 1), False)
    assert flight.runway_box() == (5, 64) and flight.runway_box(0x800) == (5, 32) and flight.runway_box(0x200) == (8, 30)
    assert flight.runway_box(mode=flight.EASY) == (9, 72) and flight.in_runway_box(5, -64, 0, 0)
    assert flight.brake_kt_per_step(flight.REALISTIC) == 16 and flight.OVERRUN_CRASH_KT == 50


def test_takeoff():
    assert flight.stall_kt() == 120 and flight.stall_kt(flaps=True) == 90 and flight.ROTATE_KT == 120
    assert round(flight.aoa_deg(120, 120), 1) == 12.7 and round(flight.aoa_deg(200, 120), 1) == 5.9
    assert round(flight.aoa_deg(120, 90), 1) == 8.4
    assert flight.takeoff_heading("CE") == 0 and flight.takeoff_heading("LB") == 180
    assert flight.takeoff_heading16("Persian Gulf", carrier=True) == 0x400 and flight.takeoff_heading("NC", True) == 185.625
