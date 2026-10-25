"""Shim: the 3D world decoder (.3DG grid, .3DT tile objects, .3D3 shapes) now lives in src/f19/gamedata/terrain.py.

  terrain_3d.py [NC|CE|LB|PG]   -> logs/theater/<TH>_terrain.npz, <TH>_mountains.json, <TH>_terrain.png
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402
from f19.gamedata.terrain import (  # noqa: E402,F401
    SHIFT, TOP_GRIDS, World, crash_check, grid_tile, load_3d3, load_3dg, load_3dt, main, scale_to_lod, write_outputs)

ROOT = Path(__file__).resolve().parent.parent
GAME = S.game_dir
# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract finds them (see radar_game.py).
POS_PER_KM = __import__("f19.gamedata.terrain", fromlist=["POS_PER_KM"]).POS_PER_KM
FT_PER_POS = __import__("f19.gamedata.terrain", fromlist=["FT_PER_POS"]).FT_PER_POS
THEATER_INDEX = __import__("f19.gamedata.terrain", fromlist=["THEATER_INDEX"]).THEATER_INDEX

if __name__ == "__main__":
    main(sys.argv[1:])
