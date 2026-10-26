"""scripts/nav.py is now a shim for `python -m f19.cli.fly` (WP-7d). Same flags; --fixed is refused.
The old nav, frozen as the equivalence tests' reference: research/scripts/nav_legacy.py (do not run it)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # until `uv run` / an installed package
from f19.cli.fly import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
