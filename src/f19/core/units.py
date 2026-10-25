"""Game units and their conversions (stdlib only).

Positions: "units" are waypoint-table units, 64 per km (player position ds:94EE >> 5; the u32 position at old phys
0x451B8 is 2048 per km). Y grows south. Territory / terrain-type / SA-14 cells are 32 km = 2048 units, cell = x >> 11,
16 x 16 cells per theater (analysis/territory.py). The planner's search grid is 2 km (planner/world.py CELL_KM).
Angles: 65536 = 360 deg, 0 = north, clockwise (ds:46DA heading; pitch/roll are signed s16).
Speed: map distance per game s is GROUND_PER_KT x the indicated airspeed (16 logged flights, analysis/fuel_log.py,
2026-10-21). Clock: one game-clock tick (ds:6630) = 2 game s; event-list cells are x >> 7, y >> 7.
"""

K = 64                       # units per km
POS_PER_UNIT = 32            # raw player position (2048 per km) per unit
POS_PER_KM = K * POS_PER_UNIT
CELL_SHIFT = 11              # 32 km territory cell = unit >> 11
CELL_UNITS = 1 << CELL_SHIFT
CELL_KM = CELL_UNITS // K
GRID_N = 16                  # cells per side
MAP_UNITS = GRID_N * CELL_UNITS
PLAN_CELL_KM = 2.0           # planner search grid (planner/world.py CELL_KM)
PLAN_CELL_UNITS = PLAN_CELL_KM * K
EVENT_CELL_SHIFT = 7         # flight-event list stores x >> 7, y >> 7 (f19_state.read_event_list)

KT_KMH = 1.852               # km/h per knot (a plain unit, NOT the planner's world.KT_KMH: see GROUND_KMH_PER_KT)
GROUND_PER_KT = 0.788        # ground speed / indicated airspeed (planner/world.py, analysis/fuel_log.py)
GROUND_KMH_PER_KT = KT_KMH * GROUND_PER_KT     # = planner/world.py KT_KMH (map km per game hour per indicated kt)
FT_PER_M = 3.28084
CLOCK_TICK_S = 2             # game s per clock tick (ds:6630)

ANGLE_FULL = 65536
DEG_PER_ANGLE16 = 360 / ANGLE_FULL


def km_to_units(km): return km * K
def units_to_km(u): return u / K
def pos_to_units(p): return p / POS_PER_UNIT
def units_to_pos(u): return u * POS_PER_UNIT
def cell_of(x, y):
    """32 km cell (cx, cy) of a point in units, unclamped (off-map cells are < 0 or >= GRID_N)."""
    return int(x) >> CELL_SHIFT, int(y) >> CELL_SHIFT
def cell_clamped(x, y):
    """cell_of clamped to the 16 x 16 grid (nav.py _gbyte, missile_watch, planner/world.cell32)."""
    cx, cy = cell_of(x, y)
    return min(GRID_N - 1, max(0, cx)), min(GRID_N - 1, max(0, cy))
def cell_centre(cx, cy): return (cx + 0.5) * CELL_UNITS, (cy + 0.5) * CELL_UNITS

def kt_to_kmh(kt): return kt * KT_KMH
def ground_kmh(kt): return kt * GROUND_KMH_PER_KT
def ground_units_per_s(kt): return kt * GROUND_KMH_PER_KT / 3600 * K
def clock_to_s(ticks): return ticks * CLOCK_TICK_S
def ft_to_m(ft): return ft / FT_PER_M
def m_to_ft(m): return m * FT_PER_M

def s16(v):
    """u16 -> signed s16 (missile_game.s16)."""
    v &= 0xFFFF; return v - 0x10000 if v > 0x7FFF else v
def angle16_to_deg(a):
    """16-bit angle -> deg in [0, 360) (missile_watch.deg)."""
    return (int(a) & 0xFFFF) * DEG_PER_ANGLE16
def angle16_to_deg_signed(a):
    """Signed 16-bit angle (pitch, roll) -> deg in [-180, 180) (f19_state.read_flight)."""
    return s16(int(a)) * DEG_PER_ANGLE16
def deg_to_angle16(d):
    """deg -> 16-bit angle, rounded, wrapped (missile_watch.u65)."""
    return int(round(d / 360 * ANGLE_FULL)) & 0xFFFF
