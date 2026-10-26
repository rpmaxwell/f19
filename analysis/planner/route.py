"""Shim: Routing (Router: search, reduce, expand, repair, densify, validate; PlanError). Moved to src/f19/planner/router.py (WP-6); this module re-exports it for the old importers."""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
import f19.planner.router as _m  # noqa: E402
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})   # every name, private ones too
# Self-contained assignments (not imports) so tests/unit/core/core_legacy.extract finds them (see analysis/radar_game.py).
hdg = __import__("f19.planner.router", fromlist=["hdg"]).hdg
unit = __import__("f19.planner.router", fromlist=["unit"]).unit
from_hdg = __import__("f19.planner.router", fromlist=["from_hdg"]).from_hdg
turn_deg = __import__("f19.planner.router", fromlist=["turn_deg"]).turn_deg
