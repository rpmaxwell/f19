"""Shim: the ordnance tables and weapon-vs-target matrix now live in src/f19/rules/weapons.py, the .WLD category
table in src/f19/gamedata/wld.py (docs/game/weapons.md "Ordnance vs target strength").

  ordnance.py              matrix + every theater's target types with their effective values
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
from f19.rules.weapons import NCAT, compat, egame_image, print_ordnance, tables  # noqa: E402,F401
from f19.gamedata.wld import categories  # noqa: E402,F401

GAME = S.game_dir
WLD = __import__("f19.gamedata.wld", fromlist=["WLD_FILE"]).WLD_FILE

if __name__ == "__main__":
    print_ordnance()
