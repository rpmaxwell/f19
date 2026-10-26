"""Shim: What the map is: theater terrain, territory, airbases, the mission's radars and targets. Moved to src/f19/planner/world.py (WP-6); this module re-exports it for the old importers."""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
import f19.planner.world as _m  # noqa: E402
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})   # every name, private ones too
# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract finds them (see analysis/radar_game.py).
K = __import__("f19.planner.world", fromlist=["K"]).K
CELL_KM = __import__("f19.planner.world", fromlist=["CELL_KM"]).CELL_KM
CELL = __import__("f19.planner.world", fromlist=["CELL"]).CELL
GROUND_PER_KT = __import__("f19.planner.world", fromlist=["GROUND_PER_KT"]).GROUND_PER_KT
KT_KMH = __import__("f19.planner.world", fromlist=["KT_KMH"]).KT_KMH
