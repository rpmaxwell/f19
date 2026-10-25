"""Shim: the mission rating (END.EXE formula) now lives in src/f19/rules/scoring.py (rating, estimate, fuel_mult and
the tables; plus ID / bail-out penalties, kill points and current_score).
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
from f19.rules.scoring import *  # noqa: E402,F401,F403
from f19.rules.scoring import FUEL_BUCKETS, LANDING, MISSION, OPPONENT, PRIMARY, estimate, fuel_mult, rating  # noqa: E402,F401

# Self-contained assignment (not an import) so tests/unit/core/core_legacy.extract finds it (see radar_game.py).
THEATER = __import__("f19.rules.scoring", fromlist=["THEATER"]).THEATER
