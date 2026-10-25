"""Shim: theater codes, .WLD files and place lists now live in src/f19/gamedata/wld.py (codes via f19.core.theater).

  from theaters import code, wld_bytes, places
  code("Persian Gulf") -> "PG";  wld_bytes("PG") -> GULF.WLD bytes
  places("PG") -> logs/theater/PG.json (generated from the .WLD on first use)
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
from pathlib import Path  # noqa: E402
from f19.gamedata.wld import in_memory, parse_wld, places, wld_bytes  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent
GAME = S.game_dir
# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract finds them (see radar_game.py).
WLD_FILE = __import__("f19.gamedata.wld", fromlist=["WLD_FILE"]).WLD_FILE
ALIASES = __import__("f19.gamedata.wld", fromlist=["ALIASES"]).ALIASES
code = __import__("f19.gamedata.wld", fromlist=["code"]).code
