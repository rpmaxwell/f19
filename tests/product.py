"""The ONE place the tests import product code from.

The product modules are still flat files in scripts/ and analysis/ (found through sys.path). When a refactor wave moves
a module into src/f19/..., change the import here (or make the old module a re-exporting shim) and every test follows.
Imports are lazy (functions), so a test module that needs only one of them doesn't pay for the rest.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "analysis", ROOT / "scripts"):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))


def settings():
    from f19.core.settings import settings as S
    return S


def radar_game():
    import radar_game
    return radar_game


def missile_game():
    import missile_game
    return missile_game


def missile_watch():
    import missile_watch
    return missile_watch


def phases():
    import phases
    return phases


def f19_state():
    import f19_state
    return f19_state


def territory():
    import territory
    return territory


def planner_checks():
    from f19.planner import checks
    return checks
