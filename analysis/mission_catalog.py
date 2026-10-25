"""Shim: the mission catalog builder now lives in src/f19/gamedata/missions.py. It writes docs/game/missions.md.

  python3 analysis/mission_catalog.py [out.md]
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
import sys  # noqa: E402
from f19.gamedata.missions import *  # noqa: E402,F401,F403
from f19.gamedata.missions import (  # noqa: E402,F401
    LEVELS, OUT, TASK, TEXT_TARGET, THEATERS, briefings, categories, main, primary_only, setup, time_limit, unlzw)

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else OUT)
