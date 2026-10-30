"""f19.planner.checks as pytest: every check is its own test case.

checks.main() runs once per session with its check() recorder swapped for one that collects (name, ok, detail); each
case asserts its check passed AND that its detail text (the measured numbers) is what the baseline code printed on
2026-10-25, so a refactor that moves a detection range by half a km shows up even while the check's own tolerance
still passes. A check that disappears or is renamed fails as "not run".
"""
import pytest

import product

# name -> detail printed by the baseline code (None: the check prints no detail)
EXPECTED = {
    "trackcam: dead-ahead target is the nearest (day)": None,
    "trackcam: dead-ahead target is the nearest (night)": None,
    "trackcam: 9 nearer records in the cone exceed 8 presses (day)": "Plan(slash=True, presses=9)",
    "trackcam: 9 nearer records in the cone exceed 8 presses (night)": "Plan(slash=True, presses=9)",
    "trackcam: 60 km is in range by day, not at night (50 km cap)": None,
    "trackcam: planner margin is tighter than the game's 45 deg cone / 100 km": None,
    "site_return_np == site_return (20k random inputs)": None,
    "SA-11 side-on (FINDINGS 50 km)": "45.5 km with > 100 (the game), 50.5 km with >= 100 (the table)",
    "SA-2 flying at/away: never (FINDINGS -)": None,
    "SA-10 head-on at 200 kt (FINDINGS 15 km)": "6.5 km at 200 kt, 13.5 km at 203 kt: the doppler term (27 kt >> 5) steps at "
                                                "202.7 kt, the table's 15 km sits on that step",
    "SA-10 head-on at 150 kt: never (FINDINGS 0)": None,
    "SA-8B side-on (FINDINGS 31 km)": "28.5 / 31.5",
    "rough cell, EMV 10: nothing sees the plane": None,
    "mountain cell: return 0 for every type": None,
    "EMV: 300 ft Veteran 200 kt level = 10": "10",
    "EMV: bay open +24, 30 deg bank +10": None,
    "duty fraction 0.375 at war 0": "0.375",
    "Veteran consecutive pulses: 3 -> network (> 0x7F), 5 -> launch (> 0xC0; FINDINGS says 4), 6 -> ID (>= 0xFA)":
        "[48, 96, 144, 192, 240, 255]",
    "quiet pulse period 32 game s": None,
    "50 % + flaps: 60 lb/min, holds level": "{'kt': 197, 'stall': 90, 'stalled': False, 'lb_min': 60, 'lb_per_100km': 987}",
    "transit regime at 10000 lb = 70 % clean (least lb/km)":
        "Setting(thr=70, flaps=False, kt=305, lb_min=90, lb_km=12.131841302575129, stall=121)",
    "transit regime at 2000 lb = 70 % clean (least lb/km)":
        "Setting(thr=70, flaps=False, kt=380, lb_min=90, lb_km=9.737398940224775, stall=121)",
    "enemy regime full tanks = 50 % + flaps": "Setting(thr=50, flaps=True, kt=182, lb_min=60, lb_km=13.553888634745109, stall=90)",
    "lb per sample at 50 % = 64": None,
    "R9 paper example ~117 samples (75 km each way)": "transit 1768 lb, 113 samples",
}


@pytest.fixture(scope="session")
def results(egame):
    checks = product.planner_checks()
    got = {}
    orig = checks.check
    checks.check = lambda name, ok, detail="": got.setdefault(name, (bool(ok), detail))
    try:
        with pytest.raises(SystemExit) as e:
            checks.main()
    finally:
        checks.check = orig
    got["__exit__"] = e.value.code
    return got


@pytest.mark.parametrize("name", list(EXPECTED))
def test_check(results, name):
    assert name in results, "check not run (removed or renamed in checks.py?)"
    ok, detail = results[name]
    assert ok, f"check failed: {detail}"
    if EXPECTED[name] is not None:
        assert detail == EXPECTED[name]


def test_no_unexpected_checks_and_clean_exit(results):
    extra = set(results) - set(EXPECTED) - {"__exit__"}
    assert not extra, f"new checks in checks.py, add them to EXPECTED: {extra}"
    assert results["__exit__"] == 0
