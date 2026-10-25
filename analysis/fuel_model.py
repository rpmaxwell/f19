"""Shim: the fuel and cruise-speed model now lives in src/f19/rules/fuel.py (burn 2 + thr*thr // 1000 per 4 game s,
docs/game/fuel.md).

  fuel_model.py            print the cruise table
  from fuel_model import cruise; cruise(thr, alt_ft, fuel_lb, flaps) -> dict(kt, lb_min, lb_per_100km)
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.rules.fuel import *  # noqa: E402,F401,F403
from f19.rules.fuel import STEPS_PER_MIN, THROTTLES, burn_step, cruise, main  # noqa: E402,F401

if __name__ == "__main__":
    main()
