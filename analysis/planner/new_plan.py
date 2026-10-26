"""Plan the mission on the briefing screen in one step: read it from memory, write plan.json, run the planner.

  python analysis/planner/new_plan.py --extra-fuel 1                 (defaults: theater from memory, Cold War, strike, Veteran, day)
  python analysis/planner/new_plan.py --extra-fuel 2 --profile fuel --night
  python analysis/planner/new_plan.py --mission-file logs/plan/X/mission_mem.json --extra-fuel 1   (no game needed)

Shim: the code is f19.planner.cli.new_plan (src/f19/planner/cli.py, WP-6).
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
from f19.planner.cli import new_plan as main  # noqa: E402

if __name__ == "__main__":
    main()
