"""Shim: low-altitude weapon release geometry now lives in src/f19/rules/weapons.py (mav_drop, photo_score,
photo_window_km, photo_pitch; docs/game/weapons.md).

  release_geometry.py      print the tables
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.rules.weapons import U, mav_drop, photo_pitch, photo_score, photo_window_km, print_release  # noqa: E402,F401

if __name__ == "__main__":
    print_release()
