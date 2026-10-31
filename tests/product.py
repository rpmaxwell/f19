"""The ONE place the tests import product code from.

Everything is a package module now (f19.*); this keeps the paths and the settings in one place, as `product.settings()` and
`product.ROOT`. (Until 2026-10-30 it also loaded the flat scripts/ and analysis/ modules the package replaced.)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src",):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))


def settings():
    from f19.core.settings import settings as S
    return S


def planner_checks():
    from f19.planner import checks
    return checks
