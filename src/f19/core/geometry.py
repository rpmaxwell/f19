"""Map geometry, one implementation each (stdlib only). Points are (x, y) in units (64 per km), y grows south;
headings in deg 0 = north clockwise unless the name says angle16 (65536 = 360).

The game's distance metric is octagonal: max(|dx|, |dy|) + min(|dx|, |dy|) / 2 (f19_state.py docstring; EGAME uses
it for radar range 0x5519, missile seeker 0x760c). Float versions are what nav / phases / the HUD use; the *16
versions are the game's integer arithmetic (halving by shift, clamped to 0x7FFF), for the rule transcriptions.
"""
import math

from f19.core.units import K


def wrap_deg(d):
    """Angle difference -> [-180, 180)."""
    return (d + 180) % 360 - 180

def bearing_deg(a, b):
    """Heading (deg) from point a to point b."""
    return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360

def heading_of(v):
    """Heading (deg) of a vector (planner/route.hdg)."""
    return math.degrees(math.atan2(v[0], -v[1])) % 360

def heading_vec(h):
    """Unit vector of heading h deg (planner/route.from_hdg)."""
    r = math.radians(h); return math.sin(r), -math.cos(r)

def unit_vec(v):
    """v / |v|; a zero vector -> north (0, -1) (planner/route.unit)."""
    n = math.hypot(v[0], v[1]); return (v[0] / n, v[1] / n) if n else (0.0, -1.0)

def turn_deg(a, b, c):
    """Unsigned turn (deg, 0..180) at b of the path a -> b -> c (planner/route.turn_deg)."""
    u, w = unit_vec((b[0] - a[0], b[1] - a[1])), unit_vec((c[0] - b[0], c[1] - b[1]))
    return math.degrees(math.acos(max(-1.0, min(1.0, u[0] * w[0] + u[1] * w[1]))))

def dist(a, b):
    """Euclidean distance between two points (same unit as the points)."""
    return math.hypot(b[0] - a[0], b[1] - a[1])

def point_segment_dist(p, a, b):
    """Euclidean distance from p to the segment a-b (nav.seg_dist; a zero-length segment is the point a)."""
    (ax, ay), (bx, by) = a, b; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
    t = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
    return math.hypot(p[0] - ax - t * (bx - ax), p[1] - ay - t * (by - ay))

# --- game distance --------------------------------------------------------------------------------------------------
def game_len(dx, dy):
    """Octagonal game length of a vector, float, unclamped (nav_tui.game_dist, gun_probe.octo)."""
    ax, ay = abs(dx), abs(dy); return max(ax, ay) + min(ax, ay) / 2

def game_dist_units(a, b):
    """Octagonal game distance between points, in units, float, unclamped (phases.game_dist)."""
    return game_len(a[0] - b[0], a[1] - b[1])

def game_dist_km(a, b):
    """game_dist_units in km (nav.game_dist)."""
    return game_dist_units(a, b) / K

def game_len16(dx, dy):
    """The game's integer octagonal length: max + (min >> 1), clamped to 0x7FFF (radar_game.game_dist,
    missile_game.gdist). Integer inputs only."""
    ax, ay = abs(int(dx)), abs(int(dy))
    return min(0x7FFF, max(ax, ay) + (min(ax, ay) >> 1))

# --- 16-bit angles -------------------------------------------------------------------------------------------------
def atan2_angle16(east, north):
    """Exact atan2 as a 16-bit angle of the vector (east, north), rounded (radar_game.angle). For map deltas use
    heading16(dx, dy). Close to, but NOT, the game's own polynomial (game_atan2_16)."""
    return int(round(math.atan2(east, north) / (2 * math.pi) * 65536)) & 0xFFFF

def heading16(dx, dy):
    """16-bit heading of a map vector (y south) (planner/radar.hdg65536, radar_game.angle(dx, -dy))."""
    return atan2_angle16(dx, -dy)

def game_atan2_16(a, b):
    """EGAME 0xd17d: angle of (a east, b north), 65536 = 360, by the game's own polynomial (missile_game.atan2g):
    r = min/max in Q14, ang = r (0x2800 - |0x1333 - r| 0xB00 >> 14) >> 14 - up to ~8 % low for small angles.
    Integer inputs. (0, 0) -> 0x8000 as the game."""
    if a == 0: return 0 if b > 0 else 0x8000
    if b == 0: return 0x4000 if a > 0 else 0xC000
    aa, ab = abs(a), abs(b)
    swap = aa > ab
    r = (ab << 14) // aa if swap else (aa << 14) // ab
    ang = (r * (0x2800 - ((abs(0x1333 - r) * 0xB00) >> 14))) >> 14
    if a > 0:
        if b > 0: v = 0x4000 - ang if swap else ang
        else: v = ang + 0x4000 if swap else 0x8000 - ang
    else:
        if b > 0: v = ang + 0xC000 if swap else -ang
        else: v = 0xC000 - ang if swap else ang + 0x8000
    return v & 0xFFFF
