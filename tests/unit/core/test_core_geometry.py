"""core.geometry against textbook reference formulas (random inputs; integer-exact where the game uses integers) and the
planner's own vector helpers. Until 2026-10-30 this also compared core with the six old copies (nav, phases, nav_tui,
missile_watch, gun_probe, radar_game, missile_game); the references below are those copies' formulas."""
import math, random

import numpy as np
import pytest

from f19.core import geometry as g
from f19.core import units as u
from f19.planner import radar as _plradar, router as _plroute

R = random.Random(19)
N = 3000
F = lambda: R.uniform(-40000, 40000)                 # float units, beyond the 32768-unit map
I = lambda: R.randint(-40000, 40000)

route = {n: getattr(_plroute, n) for n in ("hdg", "unit", "from_hdg", "turn_deg")}
radar = {"hdg65536": _plradar.hdg65536}


# the old copies' formulas, in the form they all had
def ref_wrap(d): return (d + 180) % 360 - 180
def ref_bearing(a, b): return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360
def ref_octo(dx, dy):                                   # float octagonal length: max + min / 2
    ax, ay = abs(dx), abs(dy)
    return max(ax, ay) + min(ax, ay) / 2
def ref_gdist(dx, dy):                                  # the game's integer one: max + (min >> 1), clamped to 0x7FFF
    ax, ay = abs(int(dx)), abs(int(dy))
    return min(0x7FFF, max(ax, ay) + (min(ax, ay) >> 1))
def ref_angle(east, north): return int(round(math.atan2(east, north) / (2 * math.pi) * 65536)) & 0xFFFF
def ref_seg_dist(p, a, b):
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    if L == 0: return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def pts(n=N, f=F): return [((f(), f()), (f(), f())) for _ in range(n)]


def test_wrap():
    for d in [R.uniform(-1000, 1000) for _ in range(N)] + [-180, 180, 0, 540, -540.0]:
        assert g.wrap_deg(d) == ref_wrap(d)


def test_bearing_and_dist():
    for a, b in pts() + [((5, 5), (5, 5))]:
        assert g.bearing_deg(a, b) == ref_bearing(a, b)
        assert g.dist(a, b) == math.hypot(b[0] - a[0], b[1] - a[1])
        assert g.heading_of((b[0] - a[0], b[1] - a[1])) == route["hdg"]((b[0] - a[0], b[1] - a[1])) == g.bearing_deg(a, b)


def test_game_dist_float_copies():
    for a, b in pts():
        dx, dy = a[0] - b[0], a[1] - b[1]
        assert g.game_dist_units(a, b) == ref_octo(dx, dy)
        assert g.game_len(dx, dy) == ref_octo(dx, dy)
        assert g.game_dist_km(a, b) == ref_octo(dx, dy) / u.K          # nav's old copy returned km


def test_game_dist_integer_copies():
    for _ in range(N):
        dx, dy = I(), I()
        assert g.game_len16(dx, dy) == ref_gdist(dx, dy)
    for dx, dy in [(0, 0), (1, 1), (3, 1), (32767, 0), (32767, 32767), (-7, 2)]:
        assert g.game_len16(dx, dy) == ref_gdist(dx, dy)


def test_game_dist_int_vs_float_disagree_where_expected():
    """The float copies don't halve by shift and aren't clamped: same for even min and < 0x7FFF only."""
    assert g.game_len16(10, 4) == g.game_len(10, 4)
    assert g.game_len16(10, 3) == 11 and g.game_len(10, 3) == 11.5
    assert g.game_len16(30000, 30000) == 0x7FFF and g.game_len(30000, 30000) == 45000


def test_point_segment_dist():
    for a, b in pts(1000) + [((3, 4), (3, 4))]:
        p = (F(), F())
        assert g.point_segment_dist(p, a, b) == pytest.approx(ref_seg_dist(p, a, b), abs=1e-6)


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
        assert g.atan2_angle16(dx, dy) == ref_angle(dx, dy)
        assert g.heading16(dx, dy) == ref_angle(dx, -dy) == int(radar["hdg65536"](dx, dy))
        v = g.unit_vec((dx, dy))
        assert g.heading16(*v) == int(radar["hdg65536"](*v))
    # the game's own polynomial (EGAME 0xd17d): the cardinal directions and the quadrant diagonals
    assert g.game_atan2_16(5, 0) == 0x4000 and g.game_atan2_16(0, 5) == 0 and g.game_atan2_16(0, 0) == 0x8000
    assert g.game_atan2_16(3, 3) == g.game_atan2_16(1, 1) != g.game_atan2_16(1, 2)


def test_game_atan2_vs_exact_atan2_differ():
    """Two different angle functions on purpose: the game's polynomial runs up to ~8 % low (missile_game.atan2g)."""
    diffs = [abs(u.s16(g.game_atan2_16(a, b) - g.atan2_angle16(a, b))) for a, b in ((I(), I()) for _ in range(N))]
    assert 0 < max(diffs) < 0x200
    assert g.atan2_angle16(0, 0) == 0 and g.game_atan2_16(0, 0) == 0x8000


def test_angle_units():
    for _ in range(N):
        a = R.randint(-70000, 140000)
        assert u.angle16_to_deg(a) == (a & 0xFFFF) * 360 / 65536
        assert u.s16(a) == ((a + 0x8000) & 0xFFFF) - 0x8000
        d = R.uniform(-720, 720)
        assert u.deg_to_angle16(d) == int(round(d / 360 * 65536)) & 0xFFFF
    assert u.angle16_to_deg_signed(0xC000) == -90.0 and u.angle16_to_deg_signed(0x4000) == 90.0
