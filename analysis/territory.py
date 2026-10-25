"""Territory grid ("behind enemy lines") from the theater .WLD file.

Found 2026-10-15 from the debrief: the game samples the plane every 64 game seconds
(event type 9 records in the flight log), and a sample scores cold-war points when its
32 km cell is enemy territory. The grid is 16x16 bytes, cell = (x >> 11, y >> 11) in
waypoint units (64 per km; player position >> 5), stored row-major (y rows) just
before the .WLD name strings (CE.WLD 0x7BA). F-15 SE2's END uses the same table
(gridFlags[gx + gy * 16] & 3) for kills and for capture after ejecting.
Low two bits: 3 = friendly (NATO), 2 = neutral, 0 = enemy (scores; ejecting = capture).

  territory.py <TH>          print the grid
  from territory import side  side(TH, x, y) -> "friendly" | "neutral" | "enemy" | "1?"
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import re, sys
from functools import lru_cache
from pathlib import Path
import numpy as np

GAME = S.game_dir
WLD = {"CE": "CE.WLD", "NC": "NC.WLD", "LB": "LIBYA.WLD", "PG": "GULF.WLD"}
SIDE = {3: "friendly", 2: "neutral", 0: "enemy", 1: "1?"}

@lru_cache(None)
def grid(th):
    b = (GAME / WLD[th]).read_bytes()
    i = re.search(rb"[A-Z][a-z]{2,}\x00", b).start()          # first name string (as parse_theater.py)
    return np.frombuffer(b[i - 1 - 256:i - 1], np.uint8).reshape(16, 16)

def side(th, x, y):
    g = grid(th); gx, gy = int(x) >> 11, int(y) >> 11
    if not (0 <= gx < 16 and 0 <= gy < 16): return "off-map"
    return SIDE[int(g[gy, gx]) & 3]

if __name__ == "__main__":
    th = sys.argv[1]
    for j, row in enumerate(grid(th)): print(f"{j:2d} " + "".join({3: "F", 2: "n", 0: "E", 1: "?"}[v & 3] for v in row))
