"""Shim: Score-farming loiters on radar-quiet enemy ground. Moved to src/f19/planner/loiter.py (WP-6); this module re-exports it for the old importers."""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
import f19.planner.loiter as _m  # noqa: E402
globals().update({k: v for k, v in vars(_m).items() if not k.startswith("__")})   # every name, private ones too
