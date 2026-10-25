"""The game's own radar rules, transcribed from EGAME.EXE (docs/game/radar.md "Radar sites: pulses, duty cycle,
detection", 2026-10-18). Integer arithmetic as the game does it, so it can be checked against memory snapshots
(analysis/radar_game.py check).

Units: positions in waypoint units (64 per km), distances = game metric max + min/2, alt ft, angles 65536 = 360 deg,
heading 0 = north, clockwise. opp 0 Green .. 3 Elite; war = ds:4EF2 (gameData+0x3A, 0 in every flight so far).
The EGAME tables are read on first use (f19.gamedata.exe).
"""
import struct
from functools import lru_cache

from f19.core.geometry import atan2_angle16
from f19.gamedata.exe import DS_FILE, egame


@lru_cache(None)
def radar_spec(t):
    """(range km, power, doppler?) of radar type t (target record +6), table ds:4876."""
    r, p, f = struct.unpack_from("<3H", egame(), DS_FILE + 0x4876 + 14 * t)
    return r, p, bool(f & 1)


def sam_range_km(t):
    """Missile range of the SAM a type-t site fires (weapon spec ds:4C10 + 0x12 t, +8)."""
    return struct.unpack_from("<H", egame(), DS_FILE + 0x4C10 + 0x12 * t + 8)[0]


def weapon_name(spec):
    """Name of weapon spec `spec` (ds:4C10 + 0x12 spec, 8 bytes), '' if empty."""
    return egame()[DS_FILE + 0x4C10 + 0x12 * spec:][:8].split(b"\0")[0].decode("latin1")


AIRCRAFT_SPEED_PER_UNIT = 0.208     # measured game m/s per unit of the +0x12 max speed (memlog: MiG-31 790 -> 164 m/s,
                                    # Su-27 725 -> 165, IL-76 400 -> 84; 2026-10-21, 23 flights)
FRIENDLY_TYPES = {8, 10}           # F-18 Hornet (both records, no radar): the only friendly plane in any theater.
                                   # F-4E / F-14 are hostile (Iran flies them in the Gulf).


def aircraft_spec(typ):
    """Aircraft type record (ds:49B0 + 32 x type): name, NATO name, max speed (+0x12; each frame the slot's speed at
    slot +0x18 rises toward it, 0x5ebd), reach km (+0x14: airfields this close to a network alert launch the type,
    0x64c1), agility (+0x16: turn limit, 0x5e2a), radar/weapon type (+0x18: the detection call at 0x6602 passes it as
    the radar type of radar_spec(); weapon spec of the same index: AA-x missile; <= 0 = none), +0x1A unknown.
    The detection loop sets ds:6334 to each slot's own type (0x6521), so every aircraft uses its own radar."""
    eg = egame()
    r = eg[DS_FILE + 0x49B0 + 32 * typ: DS_FILE + 0x49B0 + 32 * typ + 32]
    name, rest = r[:18].split(b"\0", 1)
    nato = rest.strip(b"\0 ").split(b"\0")[0].decode("latin1")
    vmax, reach, agility, rtype, unk = struct.unpack_from("<HHHhH", r, 18)
    out = dict(type=typ, name=name.decode("latin1"), nato=nato, max_speed=vmax, max_speed_ms=round(vmax * AIRCRAFT_SPEED_PER_UNIT),
               reach_km=reach, agility=agility, radar_type=rtype if rtype > 0 else None, unknown_1a=unk)
    if rtype > 0:
        R, P, dop = radar_spec(rtype)
        out.update(radar_range=R, radar_power=P, doppler=dop, missile=eg[DS_FILE + 0x4C10 + 0x12 * rtype:][:8].split(b"\0")[0].decode("latin1"),
                   missile_km=sam_range_km(rtype))
    return out


angle = atan2_angle16
"""Game atan2 (0xd17d) as exact atan2: angle of the vector (a east, b north), 65536 = 360 (core.geometry)."""


def game_dist(dx, dy):
    """max + min // 2, clamped to 0x7FFF. Kept here rather than core.geometry.game_len16, which int()s its inputs
    (planner/alertsim passes ints; floats would differ)."""
    ax, ay = abs(dx), abs(dy)
    return min(0x7FFF, max(ax, ay) + min(ax, ay) // 2)


def fold(a):
    """Angle difference (65536 units) -> 0 (same/opposite direction) .. 64 (perpendicular), as 0x55c1."""
    a &= 0xFFFF
    r = abs(a - 0x10000 if a > 0x7FFF else a) >> 8
    return 0x80 - r if r > 0x40 else r


def emv(alt, kt, opp, roll=0, bay=False, ecm=False):
    """EMV (ds:551C, 0x4607). roll in 65536 units."""
    a = angle((alt // 3) * (opp + 1), 0x800) >> 8
    r = abs(((roll >> 8) + 128) % 256 - 128); r = 0x80 - r if r >= 0x40 else r
    return a + r // 2 + ((27 * kt) >> 10) + (24 if bay else 0) + (16 if ecm else 0)


def site_return(px, py, alt, hdg, kt, opp, clutter, sx, sy, t, site_alt=0):
    """Return s of a type-t radar at (sx, sy) on the plane (0x5519). clutter = grid byte of the PLAYER's cell
    (only bits 0x0C are used: 0 mountains, 4 rough, 8 flat, 12 sea). Seen when s + EMV > 100."""
    R, P, dop = radar_spec(t)
    if not t or not R: return 0
    dx, dy = px - sx, py - sy
    d = game_dist(dx, dy) >> 6
    s = int((clutter & 0xC) * (R - d) * (P + 2 * opp + 1) / R)
    rel = fold(angle(dx, -dy) - hdg)
    if dop: rel = ((0x60 - rel) * ((27 * kt) >> 5)) >> 9
    s = ((rel + 0x20) >> 1) * (s >> 1) >> 4
    if (abs(site_alt - alt) >> 10) > d: s = 0
    return s


def site_return_np(px, py, alt, hdg, kt, opp, clutter, sx, sy, t, site_alt=0):
    """site_return() over numpy arrays (broadcast), the same integer steps; `check` asserts they agree.
    t is one radar type (scalar); kt, alt, hdg are integers (65536 units for hdg)."""
    import numpy as np
    R, P, dop = radar_spec(t)
    I = lambda v: np.asarray(v).astype(np.int64)
    px, py, alt, hdg, kt, clutter = I(px), I(py), I(alt), I(hdg), I(kt), I(clutter)
    if not t or not R: return np.zeros(np.broadcast(px, py, alt, hdg, kt, clutter).shape, np.int64)
    dx, dy = px - sx, py - sy
    ax, ay = np.abs(dx), np.abs(dy)
    d = np.minimum(0x7FFF, np.maximum(ax, ay) + np.minimum(ax, ay) // 2) >> 6
    s = np.trunc((clutter & 0xC) * (R - d) * (P + 2 * opp + 1) / R).astype(np.int64)
    ang = np.round(np.arctan2(dx, -dy) / (2 * np.pi) * 65536).astype(np.int64) & 0xFFFF
    a = (ang - hdg) & 0xFFFF
    r = np.abs(np.where(a > 0x7FFF, a - 0x10000, a)) >> 8
    rel = np.where(r > 0x40, 0x80 - r, r)
    if dop: rel = ((0x60 - rel) * ((27 * kt) >> 5)) >> 9
    s = ((rel + 0x20) >> 1) * (s >> 1) >> 4
    return np.where((np.abs(site_alt - alt) >> 10) > d, 0, s)


def seen(s, e):
    return s + e > 100


def alert_step(alert, was_seen, opp, war=0, ecm=False):
    """One pulse of a site's alert (0x51a1): +((war+opp)*16+16) (>>1 with ECM) when seen, -16 when not."""
    return min(255, alert + ((((war + opp) << 4) + 16) >> (1 if ecm else 0))) if was_seen else max(0, alert - 16)


def pulse_period_ticks(alert):
    """Clock ticks (2 game s) between a site's pulses: 256 / (alert/4 + 16) (+ index frames, ignored)."""
    return 256 / ((alert >> 2) + 16)


def duty_active(index, frame, alert=0, always=False, war=0, recent_network_alert=False):
    """Is a site scheduled this 1024-frame window (0x4f8c)? frame = ds:5520 (random at start: unknown when planning)."""
    if alert or always: return True
    return ((index * (frame >> 10) * 7) & 7) <= 2 * war + (3 if recent_network_alert else 1)


def duty_fraction(war=0, recent_network_alert=False):
    """Expected share of quiet sites active in a window when the frame counter is unknown (0.375 at war 0, 0.56 after a network alert)."""
    thr = 2 * war + (3 if recent_network_alert else 1)
    return sum(((i * k * 7) & 7) <= thr for i in range(8) for k in range(8)) / 64


THRESHOLDS = dict(network_alert=0x7F, launch=0xC0, radar_id=0xFA)      # alert levels (strictly above / at)
AIRFIELD_SAFE_UNITS = 0x500                                             # no launch / ID within 20 km of airfield records


def rough_ceiling(opp):
    """Largest return any Soviet radar type gets on a plane in a rough cell (brute force, 150-300 kt)
    (a copy of analysis/planner/loiter.rough_ceiling, which stays there until WP-6)."""
    best = 0
    for t in range(1, 23):
        R, P, _ = radar_spec(t)
        if not R or R > 1000: continue
        for d in (1, 2, 4, 8, 16):
            for h in range(0, 65536, 4096):
                for kt in (150, 200, 250, 300):
                    best = max(best, site_return(d * 64, 0, 300, h, kt, opp, 4, 0, 0, t))
    return best

