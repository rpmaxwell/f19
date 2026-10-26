"""Shim: Speed, throttle and fuel along a route (f19.rules.fuel). Moved to src/f19/planner/fuelplan.py (WP-6); this module re-exports it for the old importers."""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
import f19.planner.fuelplan as _m  # noqa: E402
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})   # every name, private ones too
# A literal (not an import) so tests/unit/rules/test_rules.py's regex finds it; = f19.rules.fuel's values.
TANK_LB, TRANSFER_BELOW = 1900.0, 500.0
assert (TANK_LB, TRANSFER_BELOW) == (_m.TANK_LB, _m.TRANSFER_BELOW)
