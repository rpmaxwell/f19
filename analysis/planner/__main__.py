"""python analysis/planner <plan_dir> [--spec plan.json]  ->  f19.planner.cli.main (src/f19/planner)."""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))  # until the package move
from f19.planner.cli import main  # noqa: E402
from f19.planner.router import PlanError  # noqa: E402

try:
    main()
except PlanError as e:
    # as before the move, when PlanError lived in analysis/planner/route.py: the traceback ends "route.PlanError: ..."
    traceback.print_exc()
    print(f"route.PlanError: {e}", file=sys.stderr)
    sys.exit(1)
