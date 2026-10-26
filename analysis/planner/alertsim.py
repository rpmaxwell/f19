"""Shim: Alert simulation of a timed route, pulse by pulse. Moved to src/f19/planner/alertsim.py (WP-6); this module re-exports it for the old importers."""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
import f19.planner.alertsim as _m  # noqa: E402
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})   # every name, private ones too
