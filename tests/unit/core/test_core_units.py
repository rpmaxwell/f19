"""core.units against the planner / terrain constants and the literal values the old scripts all shared (K = 64 ...)."""
import random

from f19.core import units as u

from f19.planner import world as _w

world = {n: getattr(_w, n) for n in ("K", "CELL_KM", "CELL", "GROUND_PER_KT", "KT_KMH")}
R = random.Random(7)


def test_constants_match_old_copies():
    assert u.K == 64                                       # the one K the old nav, phases, nav_tui and missile_watch each defined
    assert u.PLAN_CELL_KM == world["CELL_KM"] and u.PLAN_CELL_UNITS == world["CELL"]
    assert u.GROUND_PER_KT == world["GROUND_PER_KT"] == 0.788      # pooled over 16 logged flights (f19.cli.fuel_log summary)
    assert u.GROUND_KMH_PER_KT == world["KT_KMH"]          # the planner's "KT_KMH" is ground km/h per indicated kt
    from f19.gamedata import terrain as t3d_mod
    t3d = {n: getattr(t3d_mod, n) for n in ("POS_PER_KM", "FT_PER_POS")}
    assert u.POS_PER_KM == t3d["POS_PER_KM"] and t3d["FT_PER_POS"] == 1000 / u.POS_PER_KM * u.FT_PER_M
    assert u.CELL_UNITS == 2048 and u.CELL_KM == 32 and u.MAP_UNITS == 32768


def test_cells():
    for _ in range(2000):
        x, y = R.uniform(-3000, 36000), R.uniform(-3000, 36000)
        assert u.cell_of(x, y) == (int(x) >> 11, int(y) >> 11)            # territory.side, phases
        assert u.cell_clamped(x, y) == (min(15, max(0, int(x) >> 11)), min(15, max(0, int(y) >> 11)))   # missile_watch
    assert u.cell_centre(3, 4) == (3.5 * 2048, 4.5 * 2048)                 # phases "staying in cover"


def test_conversions():
    assert u.km_to_units(2.5) == 160 and u.units_to_km(640) == 10
    assert u.pos_to_units(64000) == 2000 and u.units_to_pos(2000) == 64000
    assert u.clock_to_s(30) == 60
    kt = 190                                     # phases.TerrainEscape._km_s: kt * 0.788 * 1.852 / 3600 km per game s
    assert abs(u.ground_units_per_s(kt) / u.K - kt * 0.788 * 1.852 / 3600) < 1e-15
    assert abs(u.ground_kmh(kt) - kt * world["KT_KMH"]) < 1e-12
    assert abs(u.m_to_ft(u.ft_to_m(1234.5)) - 1234.5) < 1e-9


def test_core_is_stdlib_only():
    import ast, sys
    from core_legacy import REPO
    for f in (REPO / "src/f19/core").glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module] if isinstance(n, ast.ImportFrom) else []
            for m in mods:
                assert m.startswith("f19.core") or m.split(".")[0] in sys.stdlib_module_names, (f.name, m)
