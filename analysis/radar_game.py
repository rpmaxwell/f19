"""Shim: the game's own radar rules now live in src/f19/rules/radar.py (EGAME tables via f19.gamedata.exe).
This module re-exports them for the old importers and keeps the memlog check (I/O + test code).

  radar_game.py check [memlog dirs...]   re-run the memlog checks (EMV, pulse colour, duty cycle)

Units: positions in waypoint units (64 per km), distances = game metric max + min/2, alt ft, angles 65536 = 360 deg,
heading 0 = north, clockwise. opp 0 Green .. 3 Elite; war = ds:4EF2 (gameData+0x3A, 0 in every flight so far).
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import glob, math, struct, sys  # noqa: E402,F401
from pathlib import Path  # noqa: E402

from f19.rules.radar import *  # noqa: E402,F401,F403
from f19.rules.radar import (  # noqa: E402,F401
    AIRCRAFT_SPEED_PER_UNIT, AIRFIELD_SAFE_UNITS, FRIENDLY_TYPES, THRESHOLDS, aircraft_spec, alert_step, duty_active,
    duty_fraction, emv, fold, pulse_period_ticks, radar_spec, rough_ceiling, sam_range_km, seen, site_return,
    site_return_np, weapon_name)
from f19.gamedata.exe import DS_FILE, egame as _egame  # noqa: E402,F401

EGAME = S.egame_bin
# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract, which execs top-level defs and
# assignments of this file without its imports, still finds them.
angle = __import__("f19.rules.radar", fromlist=["angle"]).angle
game_dist = __import__("f19.rules.radar", fromlist=["game_dist"]).game_dist


def __getattr__(name):
    """_EG (the EGAME image, scripts/nav.py and nav_tui.py read it) is loaded on first use."""
    if name == "_EG": return _egame()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
        _check(sys.argv[2:] or sorted(glob.glob(str(S.logs / "memlog/*"))))
    else:
        print(__doc__)
