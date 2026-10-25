"""core.geometry against every copy it replaces (random inputs; integer-exact where the game uses integers)."""
import math, random

import numpy as np
import pytest

from core_legacy import extract
from f19.core import geometry as g
from f19.core import units as u

R = random.Random(19)
N = 3000
F = lambda: R.uniform(-40000, 40000)                 # float units, beyond the 32768-unit map
I = lambda: R.randint(-40000, 40000)

nav = extract("scripts/nav.py", "K", "bearing", "dist", "game_dist", "wrap", "seg_dist")
phases = extract("scripts/phases.py", "K", "wrap", "bearing", "game_dist")
tui = extract("scripts/nav_tui.py", "game_dist", "bearing", "wrap")
mw = extract("scripts/missile_watch.py", "wrap", "deg", "u65")
gun = extract("scripts/gun_probe.py", "octo")
rg = extract("analysis/radar_game.py", "angle", "game_dist")
mg = extract("analysis/missile_game.py", "s16", "gdist", "atan2g")
route = extract("analysis/planner/route.py", "hdg", "unit", "from_hdg", "turn_deg")
radar = extract("analysis/planner/radar.py", "hdg65536")


def pts(n=N, f=F): return [((f(), f()), (f(), f())) for _ in range(n)]


def test_wrap():
    for d in [R.uniform(-1000, 1000) for _ in range(N)] + [-180, 180, 0, 540, -540.0]:
        assert g.wrap_deg(d) == nav["wrap"](d) == phases["wrap"](d) == tui["wrap"](d) == mw["wrap"](d)


def test_bearing_and_dist():
    for a, b in pts() + [((5, 5), (5, 5))]:
        assert g.bearing_deg(a, b) == nav["bearing"](a, b) == phases["bearing"](a, b) == tui["bearing"](a, b)
        assert g.dist(a, b) == nav["dist"](a, b)
        assert g.heading_of((b[0] - a[0], b[1] - a[1])) == route["hdg"]((b[0] - a[0], b[1] - a[1])) == g.bearing_deg(a, b)


def test_game_dist_float_copies():
    for a, b in pts():
        dx, dy = a[0] - b[0], a[1] - b[1]
        assert g.game_dist_units(a, b) == phases["game_dist"](a, b) == tui["game_dist"](dx, dy) == gun["octo"](dx, dy)
        assert g.game_len(dx, dy) == tui["game_dist"](dx, dy)
        assert g.game_dist_km(a, b) == nav["game_dist"](a, b)          # nav's copy returns km


def test_game_dist_integer_copies():
    for _ in range(N):
        dx, dy = I(), I()
        assert g.game_len16(dx, dy) == rg["game_dist"](dx, dy) == mg["gdist"](dx, dy)
    for dx, dy in [(0, 0), (1, 1), (3, 1), (32767, 0), (32767, 32767), (-7, 2)]:
        assert g.game_len16(dx, dy) == rg["game_dist"](dx, dy) == mg["gdist"](dx, dy)


def test_game_dist_int_vs_float_disagree_where_expected():
    """The float copies don't halve by shift and aren't clamped: same for even min and < 0x7FFF only."""
    assert g.game_len16(10, 4) == g.game_len(10, 4)
    assert g.game_len16(10, 3) == 11 and g.game_len(10, 3) == 11.5
    assert g.game_len16(30000, 30000) == 0x7FFF and g.game_len(30000, 30000) == 45000


def test_point_segment_dist():
    f = nav["seg_dist"]
    for a, b in pts(1000) + [((3, 4), (3, 4))]:
        p = (F(), F())
        f.__globals__["WPS"] = [(a, ""), (b, "")]
        assert g.point_segment_dist(p, a, b) == f(1, p)


def test_vectors():
    for _ in range(N):
        v = (F(), F())
        assert np.allclose(g.unit_vec(v), route["unit"](v), rtol=0, atol=1e-15)
        h = R.uniform(0, 360)
        assert np.allclose(g.heading_vec(h), route["from_hdg"](h), rtol=0, atol=0)
    assert g.unit_vec((0, 0)) == tuple(route["unit"]((0, 0)))
    for a, b in pts(1000):
        c = (F(), F())
        assert g.turn_deg(a, b, c) == pytest.approx(route["turn_deg"](a, b, c), abs=1e-6)


def test_angles16():
    for _ in range(N):
        dx, dy = I(), I()
        assert g.atan2_angle16(dx, dy) == rg["angle"](dx, dy)
        assert g.heading16(dx, dy) == rg["angle"](dx, -dy) == int(radar["hdg65536"](dx, dy))
        v = g.unit_vec((dx, dy))
        assert g.heading16(*v) == int(radar["hdg65536"](*v))
        assert g.game_atan2_16(dx, dy) == mg["atan2g"](dx, dy)
    for a, b in [(0, 0), (0, 5), (0, -5), (5, 0), (-5, 0), (3, 3), (-3, 3), (3, -3), (-3, -3)]:
        assert g.game_atan2_16(a, b) == mg["atan2g"](a, b)


def test_game_atan2_vs_exact_atan2_differ():
    """Two different angle functions on purpose: the game's polynomial runs up to ~8 % low (missile_game.atan2g)."""
    diffs = [abs(u.s16(g.game_atan2_16(a, b) - g.atan2_angle16(a, b))) for a, b in ((I(), I()) for _ in range(N))]
    assert 0 < max(diffs) < 0x200
    assert g.atan2_angle16(0, 0) == 0 and g.game_atan2_16(0, 0) == 0x8000


def test_angle_units():
    for _ in range(N):
        a = R.randint(-70000, 140000)
        assert u.angle16_to_deg(a) == mw["deg"](a)
        assert u.s16(a) == mg["s16"](a)
        d = R.uniform(-720, 720)
        assert u.deg_to_angle16(d) == mw["u65"](d)
    assert u.angle16_to_deg_signed(0xC000) == -90.0 and u.angle16_to_deg_signed(0x4000) == 90.0
