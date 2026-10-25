"""The game's own radar rules, transcribed from EGAME.EXE (FINDINGS.md "Radar sites: pulses, duty cycle,
detection", 2026-10-18). Integer arithmetic as the game does it, so it can be checked against memory snapshots.

  radar_game.py check [memlog dirs...]   re-run the memlog checks (EMV, pulse colour, duty cycle)

Units: positions in waypoint units (64 per km), distances = game metric max + min/2, alt ft, angles 65536 = 360 deg,
heading 0 = north, clockwise. opp 0 Green .. 3 Elite; war = ds:4EF2 (gameData+0x3A, 0 in every flight so far).
"""
import glob, math, struct, sys
from pathlib import Path

EGAME = Path(__file__).resolve().parent / "egame_re" / "EGAME.bin"
DS_FILE = 0x1ECF0
_EG = EGAME.read_bytes()

def radar_spec(t):
    """(range km, power, doppler?) of radar type t (target record +6), table ds:4876."""
    r, p, f = struct.unpack_from("<3H", _EG, DS_FILE + 0x4876 + 14 * t)
    return r, p, bool(f & 1)

def sam_range_km(t):
    """Missile range of the SAM a type-t site fires (weapon spec ds:4C10 + 0x12 t, +8)."""
    return struct.unpack_from("<H", _EG, DS_FILE + 0x4C10 + 0x12 * t + 8)[0]

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
    r = _EG[DS_FILE + 0x49B0 + 32 * typ: DS_FILE + 0x49B0 + 32 * typ + 32]
    name, rest = r[:18].split(b"\0", 1)
    nato = rest.strip(b"\0 ").split(b"\0")[0].decode("latin1")
    vmax, reach, agility, rtype, unk = struct.unpack_from("<HHHhH", r, 18)
    out = dict(type=typ, name=name.decode("latin1"), nato=nato, max_speed=vmax, max_speed_ms=round(vmax * AIRCRAFT_SPEED_PER_UNIT),
               reach_km=reach, agility=agility, radar_type=rtype if rtype > 0 else None, unknown_1a=unk)
    if rtype > 0:
        R, P, dop = radar_spec(rtype)
        out.update(radar_range=R, radar_power=P, doppler=dop, missile=_EG[DS_FILE + 0x4C10 + 0x12 * rtype:][:8].split(b"\0")[0].decode("latin1"),
                   missile_km=sam_range_km(rtype))
    return out

def angle(a, b):
    """Game atan2 (0xd17d): angle of the vector (a east, b north), 65536 = 360."""
    return int(round(math.atan2(a, b) / (2 * math.pi) * 65536)) & 0xFFFF

def game_dist(dx, dy):
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


def _check_np(n=100000, seed=1):
    """site_return_np == site_return on random inputs."""
    import numpy as np
    g = np.random.default_rng(seed)
    px, py, sx, sy = (g.integers(0, 32768, n) for _ in range(4))
    alt, hdg, kt = g.integers(0, 40000, n), g.integers(0, 65536, n), g.integers(100, 600, n)
    opp, cl, t = g.integers(0, 4, n), g.integers(0, 256, n), g.integers(1, 23, n)
    bad = 0
    for tt in range(1, 23):
        for o in range(4):
            m = (t == tt) & (opp == o)
            v = site_return_np(px[m], py[m], alt[m], hdg[m], kt[m], o, cl[m], sx[m], sy[m], tt)
            ref = [site_return(*map(int, (px[k], py[k], alt[k], hdg[k], kt[k])), o, int(cl[k]), int(sx[k]), int(sy[k]), tt)
                   for k in np.flatnonzero(m)]
            bad += int(np.sum(v != np.array(ref, np.int64)))
    print(f"site_return_np == site_return: {n - bad}/{n}")
    return bad == 0


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


def _check(dirs):
    """Replay memlog snapshots: EMV within +-1, pulse colour, duty flag."""
    ok_e = n_e = ok_c = n_c = ok_d = n_d = 0
    for dd in dirs:
        for f in sorted(glob.glob(dd + "/snap_*.bin")):
            b = Path(f).read_bytes()
            if len(b) < 0xC000: continue
            g = lambda a, t="<H": struct.unpack_from(t, b, a - 0x490)[0]
            opp = g(0x4EF0)
            if opp > 3: continue
            alt, kt, fl = g(0x46E0), g(0x7F94) // 27, g(0x684C)
            e_exact = (angle((alt // 3) * (opp + 1), 0x800) >> 8) + (lambda r: (0x80 - r if r >= 0x40 else r) // 2)(abs(((g(0x46DE, "<h") >> 8) + 128) % 256 - 128)) + (g(0x7F94) >> 10) + (24 if fl & 4 else 0) + (16 if fl & 0x10 else 0)
            n_e += 1; ok_e += abs(g(0x551C) - e_exact) <= 1
            k, col = g(0x94E4, "<h"), g(0x552C)
            px, py = g(0x94EE), g(0x94FE); cell = b[0x85FC - 0x490 + (py >> 11) * 16 + (px >> 11)]
            if 0 <= k < 70 and col in (12, 14):
                r = 0x80AA + 16 * k
                if 0 < g(r + 6) < 22:
                    s = site_return(px, py, alt, g(0x46DA), kt, opp, cell, g(r + 2), g(r + 4), g(r + 6))
                    n_c += 1; ok_c += s > 0 and s + g(0x551C) > 50
            fr, war, nsite, net = g(0x5520), g(0x4EF2), g(0x6646), g(0x95CE)
            if war <= 3 and nsite <= 80:
                for i in range(nsite):
                    r = 0x80AA + 16 * i
                    if g(r + 6) == 0 or g(r + 8) & 0x80: continue
                    n_d += 1; ok_d += duty_active(i, fr, g(r + 10), bool(g(r + 8) & 0x100), war, net >= 0x80) == bool(g(r + 8) & 2)
    print(f"EMV within +-1: {ok_e}/{n_e}   pink pulses predicted pink: {ok_c}/{n_c}   duty flag: {ok_d}/{n_d}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["check"]:
        _check_np()
        _check(sys.argv[2:] or sorted(glob.glob(str(Path(__file__).resolve().parent.parent / "logs/memlog/*"))))
    else:
        print(__doc__)
