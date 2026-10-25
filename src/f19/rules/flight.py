"""Takeoff and landing rules as named constants, from docs/game/landing-takeoff.md (EGAME, decoded 2026-10-17).

Landing mode m = gameData+0x40: 0 No Crashes, 1 Easy, 2 Realistic. ds:684C bits: 0x01 gear UP, 0x02 flaps,
0x04 bay open, 0x08 brakes. Units: waypoint units (64 per km); one fs-frame step = ~3.2 game s.
Nothing here is used by the pilot yet (phases.py keeps its own numbers until WP-7b).
"""
import math

from f19.core.theater import Theater

# --- landing modes and flag bits ---------------------------------------------------------------------------------------
NO_CRASHES, EASY, REALISTIC = 0, 1, 2
LANDING_MODE = {"no_crashes": NO_CRASHES, "easy": EASY, "realistic": REALISTIC}
GEAR_UP, FLAPS, BAY_OPEN, BRAKES = 0x01, 0x02, 0x04, 0x08              # ds:684C

# --- runway box (0x4144): |X - baseX| <= W and |Y - baseY| <= L (runways run N-S) --------------------------------------
BOX_RANGE_UNITS = 0x200                 # nearest friendly base is used within 8 km (or on the ground)
AIRBASE_BOX = (5, 64)                   # (W, L) units: +-1 km along the runway
AIRSTRIP_BOX = (5, 32)                  # base flags & 0x800: L 32
CARRIER_BOX = (8, 30)                   # base flags & 0x200
CARRIER_DECK_FT = 128                   # carrier ground level (ds:94EC)
EASY_BOX_EXTRA = (4, 8)                 # Easy adds W +4, L +8
AIRSTRIP_FLAG, CARRIER_FLAG = 0x800, 0x200

# --- touchdown (0x2cf2) ----------------------------------------------------------------------------------------------
SINK_LIMIT, SINK_LIMIT_CARRIER = 128, 256        # crash 5 if m * sink > limit (sink = -ds:9E90)
ROLL_LIMIT_NUM = 24                              # crash 5 if |roll| > (24 // m) << 8 (65536 units)
OVERRUN_CRASH_KT = 50                            # touching the ground outside the box above 50 kt: crash code 1
SAFE_STOP_KT = 1                                 # Safe Landing: in the box, kt <= 1, friendly base, for fs frames
CARRIER_WIRE_MIN_KT = 80                         # carrier wire (0x41a2): kt > 80, 16-20 units along the deck,
CARRIER_WIRE_UNITS = (16, 20)                    #   heading within 45 deg
CARRIER_WIRE_HDG_DEG = 45
BRAKE_KT_PER_STEP = 32                           # on the ground: velocity -= (32 - 8 m) kt per step
BRAKE_KT_PER_STEP_MODE = 8

# --- END crash codes (commData+0x28) -----------------------------------------------------------------------------------
CRASH_CODES = {1: "ground", 2: "hill", 3: "fuel", 4: "missiles", 5: "runway cartwheel", 6: "ejection failed"}

# --- takeoff -----------------------------------------------------------------------------------------------------------
STALL_KT = 120                          # on the ground (roll 0, 1 g): stall = rotation speed
STALL_FLAPS_KT = 90                     # -1/4 with flaps
ROTATE_KT, ROTATE_FLAPS_KT = STALL_KT, STALL_FLAPS_KT
START_BEFORE_BASE_UNITS = 56            # start point 0.88 km before the base record (0x708 position units)
RUNWAY_LEFT_KM = 1.9                    # start point to the end of the runway box (Easy +0.125 km)


def roll_limit16(mode):
    """|roll| (65536 units) above which a touchdown crashes; None for No Crashes (m = 0 skips the tests)."""
    if mode <= 0: return None
    return (ROLL_LIMIT_NUM // mode) << 8


def roll_limit_deg(mode):
    """roll_limit16 in deg: Easy 33.75, Realistic 16.875."""
    r = roll_limit16(mode)
    return None if r is None else r * 360 / 65536


def sink_limit(mode, carrier=False):
    """Largest sink (ft per step, -ds:9E90) that survives a touchdown: limit / m; None for No Crashes."""
    if mode <= 0: return None
    return (SINK_LIMIT_CARRIER if carrier else SINK_LIMIT) / mode


def touchdown_crash(mode, sink, roll16, gear_up, carrier=False):
    """Does a touchdown inside the runway box crash (code 5)? m * sink > limit, gear up, or |roll| too large."""
    if mode <= 0: return False
    s16 = roll16 - 0x10000 if roll16 & 0xFFFF > 0x7FFF else roll16
    return (mode * sink > (SINK_LIMIT_CARRIER if carrier else SINK_LIMIT) or bool(gear_up)
            or abs(s16) > roll_limit16(mode))


def runway_box(flags=0, mode=REALISTIC):
    """(W, L) half-sizes in units of the runway box of a base with these record flags."""
    w, l = CARRIER_BOX if flags & CARRIER_FLAG else AIRSTRIP_BOX if flags & AIRSTRIP_FLAG else AIRBASE_BOX
    if mode == EASY: w, l = w + EASY_BOX_EXTRA[0], l + EASY_BOX_EXTRA[1]
    return w, l


def in_runway_box(x, y, base_x, base_y, flags=0, mode=REALISTIC):
    w, l = runway_box(flags, mode)
    return abs(x - base_x) <= w and abs(y - base_y) <= l


def brake_kt_per_step(mode):
    """Brakes on the ground take (32 - 8 m) kt off per step."""
    return BRAKE_KT_PER_STEP - BRAKE_KT_PER_STEP_MODE * mode


def stall_kt(alt=0, load16=16, flaps=False):
    """Stall speed (ds:9B82): 120 kt (1 + alt/65536) isqrt(4 load16) / 8, -1/4 with flaps (float, as fuel.cruise)."""
    s = STALL_KT * (1 + alt / 65536) * math.isqrt(4 * int(load16)) / 8
    return s * 0.75 if flaps else s


def aoa_deg(kt, stall):
    """AoA = 0xC00 stall / kt - 0x300 (65536 units) in deg: 12.7 at 120 kt clean."""
    return (0xC00 * stall / kt - 0x300) * 360 / 65536


def takeoff_heading16(theater, carrier=False):
    """EGAME 0x1FC1: north for PG / CE, south for LB / NC, carriers +0x400 (core.theater)."""
    return Theater.parse(theater).takeoff_heading16(carrier)


def takeoff_heading(theater, carrier=False):
    """takeoff_heading16 in deg (0 / 180, carriers 5.625 / 185.625)."""
    return Theater.parse(theater).takeoff_heading(carrier)
