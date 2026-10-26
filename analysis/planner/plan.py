"""Shim: the planner moved to src/f19/planner (WP-6): planner.py (Planner), report.py (summarise / radar_block / report /
draw / route_json), cli.py (main / emit), api.py (plan_mission, route). This module keeps plan.py's names.

  python analysis/planner/plan.py <plan_dir> [--spec plan.json]     (= python analysis/planner <plan_dir>)
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
_sys.path.insert(0, str(_P(__file__).resolve().parent))               # the sibling shims (route, world, ...) by their old names
import f19.planner.planner as _m  # noqa: E402
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})
from f19.planner import cli as _cli  # noqa: E402
from f19.planner.cli import emit, main  # noqa: E402,F401
from f19.planner.report import (FLIGHT_TESTS, LOITER_TEST, NAV_NEEDS, OPP_NAME, _t, draw, radar_block,  # noqa: E402,F401
                                report, route_json, summarise)
import route  # noqa: E402,F401  (the old top-level module names stay importable; tests read sys.modules["route"])
import world, radar, fuelplan, profiles, probes, loiter, alertsim  # noqa: E402,F401

# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract finds them (see analysis/radar_game.py).
THEATER_INDEX = __import__("f19.planner.planner", fromlist=["THEATER_INDEX"]).THEATER_INDEX
game_takeoff_heading = __import__("f19.planner.planner", fromlist=["game_takeoff_heading"]).game_takeoff_heading


class Planner(_m.Planner):
    """Planner(spec, plan_dir) as before: the mission is read from the plan dir (f19.planner.cli.load_mission)."""
    def __init__(self, spec, plan_dir):
        super().__init__(spec, _cli.load_mission(spec, plan_dir), plan_dir)


if __name__ == "__main__":
    main()
