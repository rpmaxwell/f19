"""Shim: the territory grid ("behind enemy lines") now lives in src/f19/gamedata/wld.py (grid, side).

The grid is 16x16 bytes, cell = (x >> 11, y >> 11) in waypoint units, stored row-major (y rows) just before the .WLD
name strings. Low two bits: 3 = friendly (NATO), 2 = neutral, 0 = enemy (scores; ejecting = capture).

  territory.py <TH>          print the grid
  from territory import side  side(TH, x, y) -> "friendly" | "neutral" | "enemy" | "1?"
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import sys  # noqa: E402
from f19.gamedata.wld import SIDE, grid, side  # noqa: E402,F401

GAME = S.game_dir
WLD = __import__("f19.gamedata.wld", fromlist=["WLD_FILE"]).WLD_FILE

if __name__ == "__main__":
    th = sys.argv[1]
    for j, row in enumerate(grid(th)): print(f"{j:2d} " + "".join({3: "F", 2: "n", 0: "E", 1: "?"}[v & 3] for v in row))
