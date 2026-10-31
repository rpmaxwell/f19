"""Trackcam target selection (B and "/"): which ground record the game locks next. Pure rules, no I/O.

Decoded from EGAME 0xab84-0xacbc (the per-frame trackcam code), 2026-10-30; see docs/game/memory-map.md, section
"Trackcam target selection". The pick, run when ds:5526 has bit 0x80 set (B sets it; a lock of -1 is "searching"
by itself) and the screen ds:9740 is 0x13 / 0x19, mode ds:9A36 != 1, ds:94E0 bit 0x80 clear, cooldown ds:8B22 == 0:

  floor   = octagonal distance of the current lock, or 0 if the lock is -1 or outside the cone       (0xabd0-0xac02)
  range   = 100 << (6 - night) units (6400 = 100 km by day, 3200 at night)                           (0xabc2-0xabcd)
  for each record 0..ds:9500-1 (all 75 world records, scratch record 0 included):
      candidate = not the current lock, not destroyed (+8 bit 0x80), floor <= dist < range, strictly nearer than
                  the best so far (ties: the lower index), in the cone (|bearing - (heading + view)| < 0x2000 = 45 deg)
                  and, unless mode 2 (air-to-ground), enemy side (+8 bit 0x01)
  none found: lock -> -1 (and the next frame takes the nearest candidate: B wraps without a wasted press)

Air-to-air (mode 0) lets a record outside the cone through when the floor is above 0 (0xac2e-0xac39); modelled, never used
by the autopilot (it flies air-to-ground). Distance is the octagonal max + min / 2 in table units (64 per km).

Nothing here imports above `core`. The one deliberate simplification: bearings are exact atan2, not the game's polynomial
(core.geometry.atan2_angle16 says it is close but not identical), so a record within ~0.1 deg of the 45 deg edge may differ.
"""
from collections import namedtuple

from f19.core.geometry import bearing_deg, game_len16, wrap_deg
from f19.core.units import K

CONE_DEG = 45.0                    # 0x2000 of 65536
RANGE_UNITS = 100 << 6             # day; night halves it (100 << (6 - night))
AIR_GROUND = 2                     # ds:9A36 trackcam mode
SCREENS = (0x13, 0x19)             # ds:9740 values where the pick runs

# What the planner keeps clear of the decoded limits (a plan flown with wind, a late turn, a view slightly off 0).
SAFE_CONE_DEG = 35.0
SAFE_RANGE_KM_DAY, SAFE_RANGE_KM_NIGHT = 90.0, 45.0

Candidate = namedtuple("Candidate", "index dist")       # dist in table units (octagonal, integer)
Plan = namedtuple("Plan", "slash presses")              # slash: press "/" first; presses: B presses after it


def range_units(night):
    return RANGE_UNITS >> (1 if night else 0)


def range_km(night):
    return range_units(night) / K


def aim_deg(heading, view_offset=0):
    """Where the camera looks (deg): heading + the view offset ds:9E8C (u16, 0x4000 = 90 deg)."""
    return (heading + (view_offset & 0xFFFF) * 360 / 65536) % 360


def off_nose(player_xy, heading, view_offset, record_xy):
    """Signed angle (deg) from the camera axis to the record."""
    return wrap_deg(bearing_deg(player_xy, record_xy) - aim_deg(heading, view_offset))


def record_dist(player_xy, record_xy):
    return game_len16(record_xy[0] - player_xy[0], record_xy[1] - player_xy[1])


def candidates(player_xy, heading, view_offset, night, records, mode=AIR_GROUND, lock=-1):
    """The records B can reach, nearest first: [Candidate(index, dist)] sorted by (dist, index).
    `records` = (index, x, y, flags) for every record the game searches. This is the pick's filter without the
    floor (b_next applies that) and without excluding the current lock (b_next does)."""
    top = range_units(night)
    out = []
    for idx, x, y, flags in records:
        if flags & 0x80: continue                                  # destroyed
        if mode != AIR_GROUND and not flags & 1: continue          # modes 0/1: enemy side only
        d = record_dist(player_xy, (x, y))
        if d >= top: continue
        if abs(off_nose(player_xy, heading, view_offset, (x, y))) >= CONE_DEG: continue
        out.append(Candidate(idx, d))
    return sorted(out, key=lambda c: (c.dist, c.index))


def lock_index(raw):
    """ds:5526 (u16, or a signed -1) -> the locked record index, or None when nothing is locked (-1 = searching).
    Bit 0x80 is the search-pending flag, not part of the index."""
    if raw is None or raw < 0 or raw & 0xFFFF == 0xFFFF: return None
    return raw & 0x7F


def lock_floor(lock, player_xy, heading, view_offset, records):
    """The distance floor B uses: the lock's distance, or None (= floor 0) for no lock / a lock outside the cone."""
    lock = lock_index(lock)
    if lock is None: return None
    for idx, x, y, _ in records:
        if idx == lock:
            if abs(off_nose(player_xy, heading, view_offset, (x, y))) > CONE_DEG: return None
            return record_dist(player_xy, (x, y))
    return None


def b_next(current_lock, cands, lock_dist=None):
    """The record the next B press settles on (the -1 wrap included: nearest candidate), or None with no candidates.
    cands = candidates(...). lock_dist = lock_floor(...) for a lock that is not in cands (destroyed / out of range but
    in the cone); a lock in cands uses its own distance; otherwise the floor is 0."""
    if not cands: return None
    lock = lock_index(current_lock)
    if lock_dist is None and lock is not None:
        lock_dist = next((c.dist for c in cands if c.index == lock), None)
    floor = lock_dist or 0
    best = None
    for c in cands:                                                # sorted by (dist, index): the first hit is the pick
        if c.index != lock and c.dist >= floor: best = c; break
    return (best or cands[0]).index                                # none beyond the floor: lock -1, next frame the nearest


def presses_to_lock(wanted, current_lock, cands, lock_dist=None, limit=None):
    """B presses that bring the lock to `wanted`, or None when it is not a candidate / never comes up.
    A current lock of -1 (searching) resolves to the nearest candidate by itself, with no press."""
    if not any(c.index == wanted for c in cands): return None
    limit = limit or len(cands) + 2
    lock = lock_index(current_lock)
    d = lock_dist
    if lock is None:
        lock = cands[0].index; d = cands[0].dist                   # the free frame-after-frame search
    n = 0
    while n <= limit:
        if lock == wanted: return n
        nxt = b_next(lock, cands, d)
        if nxt is None: return None
        lock, d = nxt, next(c.dist for c in cands if c.index == nxt); n += 1
    return None


def slash_then_presses(wanted, cands):
    """"/" clears the lock, so the lock is the nearest candidate; then B steps. Plan(True, n) or None."""
    n = presses_to_lock(wanted, -1, cands)
    return None if n is None else Plan(True, n)


def best_plan(wanted, current_lock, cands, lock_dist=None):
    """The cheaper way to `wanted` in keys pressed: B x n from the current lock, or "/" then B x m. None if unreachable."""
    direct = presses_to_lock(wanted, current_lock, cands, lock_dist)
    again = slash_then_presses(wanted, cands)
    if direct is None: return again
    if again is None: return Plan(False, direct)
    return Plan(False, direct) if direct <= 1 + again.presses else again


def selectable(wanted, player_xy, heading, view_offset, night, record, mode=AIR_GROUND):
    """(True, "") when the pick can ever return the record from this position, else (False, reason):
    "destroyed", "not_enemy" (modes 0/1), "out_of_range", "out_of_cone". `record` = (x, y, flags)."""
    x, y, flags = record
    if flags & 0x80: return False, "destroyed"
    if mode != AIR_GROUND and not flags & 1: return False, "not_enemy"
    if record_dist(player_xy, (x, y)) >= range_units(night): return False, "out_of_range"
    if abs(off_nose(player_xy, heading, view_offset, (x, y))) >= CONE_DEG: return False, "out_of_cone"
    return True, ""


# --------------------------------------------------------------------------------------------------- planner side
def safe_range_km(night):
    return SAFE_RANGE_KM_NIGHT if night else SAFE_RANGE_KM_DAY


def acquisition_ok(player_xy, heading, target_xy, night, view_offset=0):
    """Planner test with margins: is the target well inside the cone and the range from here, flying `heading`?
    (The planner flies the run-in straight at the target, so this reads the range and the heading error.)"""
    d_km = record_dist(player_xy, target_xy) / K
    return d_km < safe_range_km(night) and abs(off_nose(player_xy, heading, view_offset, target_xy)) <= SAFE_CONE_DEG


def heading_window(player_xy, target_xy):
    """(low, high) headings (deg) from `player_xy` that keep the target inside the planner's safe cone."""
    b = bearing_deg(player_xy, target_xy)
    return (b - SAFE_CONE_DEG) % 360, (b + SAFE_CONE_DEG) % 360
