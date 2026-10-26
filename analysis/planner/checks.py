"""Unit checks of the planner's game model (notes/planner_radar_prd.md "Done when").

  python analysis/planner/checks.py

Radar numbers come from radar_game.py (the game's integer code). The FINDINGS.md detection table
("Radar sites: pulses, duty cycle, detection") was computed with return + EMV >= 100; the game (and
radar_game.seen) uses > 100, so pulse ranges come out a few km shorter here. Both are printed.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parents[1] / "src")]   # radar_game (its _check_np), f19 until the package move
import numpy as np  # noqa: E402
import radar_game as rg  # noqa: E402
from f19.rules import fuel as fuel_model  # noqa: E402
from f19.planner import fuelplan, profiles  # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    if not ok: FAIL.append(name)


def det_range(kind, aspect, kt=200, emv=10, terrain=2, opp=2, ge=False):
    """Farthest km (0.5 km steps) at which a type-`kind` site sees the plane; None = never."""
    best = None
    for d in np.arange(0.5, 520, 0.5):
        s = rg.site_return(int(d * 64), 0, 300, 0x4000 if aspect == "radial" else 0, kt, opp, terrain << 2, 0, 0, kind)
        if (s + emv >= 100) if ge else (s + emv > 100): best = float(d)
    return best


def main():
    # 1. the numpy port the planner uses == the game's scalar formula
    check("site_return_np == site_return (20k random inputs)", rg._check_np(20000))

    # 2. FINDINGS detection table, Veteran, EMV 10, 200 kt, flat land
    sa11, sa11_ge = det_range(5, "side"), det_range(5, "side", ge=True)
    check("SA-11 side-on (FINDINGS 50 km)", sa11_ge is not None and abs(sa11_ge - 50) <= 1,
          f"{sa11} km with > 100 (the game), {sa11_ge} km with >= 100 (the table)")
    check("SA-2 flying at/away: never (FINDINGS -)", det_range(1, "radial") is None and det_range(1, "radial", ge=True) is None)
    sa10 = det_range(4, "radial")
    sa10_203 = det_range(4, "radial", kt=203)
    check("SA-10 head-on at 200 kt (FINDINGS 15 km)", sa10 is not None and sa10 < 20,
          f"{sa10} km at 200 kt, {sa10_203} km at 203 kt: the doppler term (27 kt >> 5) steps at 202.7 kt, "
          f"the table's 15 km sits on that step")
    check("SA-10 head-on at 150 kt: never (FINDINGS 0)", det_range(4, "radial", kt=150, emv=rg.emv(300, 150, 2)) is None)
    check("SA-8B side-on (FINDINGS 31 km)", abs((det_range(3, "side", ge=True) or 0) - 31) <= 1, f"{det_range(3, 'side')} / {det_range(3, 'side', ge=True)}")
    check("rough cell, EMV 10: nothing sees the plane", all(det_range(k, a, terrain=1) is None for k in range(1, 22) for a in ("side", "radial")
                                                         if rg.radar_spec(k)[0]))
    check("mountain cell: return 0 for every type", all(rg.site_return(d * 64, 0, 300, h, kt, 3, 0, 0, 0, k) == 0
                                                       for k in range(1, 23) for d in (1, 5, 20) for h in (0, 0x4000) for kt in (150, 400)))
    check("EMV: 300 ft Veteran 200 kt level = 10", rg.emv(300, 200, 2) == 10, f"{rg.emv(300, 200, 2)}")
    check("EMV: bay open +24, 30 deg bank +10", rg.emv(300, 200, 2, bay=True) == 34 and
          rg.emv(300, 200, 2, roll=int(30 / 360 * 65536)) == 20)

    # 3. pulses, alert, duty
    check("duty fraction 0.375 at war 0", abs(rg.duty_fraction(0) - 0.375) < 1e-9, f"{rg.duty_fraction(0)}")
    a, seq = 0, []
    for _ in range(6): a = rg.alert_step(a, True, 2); seq.append(a)
    # FINDINGS says "4 -> SAM launches possible", but 4 x 48 = 192 = 0xC0 and the gate is alert > 0xC0
    # (radar_game.THRESHOLDS, FINDINGS' own gate text): by the code it is the 5th pulse. Reported, code wins.
    check("Veteran consecutive pulses: 3 -> network (> 0x7F), 5 -> launch (> 0xC0; FINDINGS says 4), 6 -> ID (>= 0xFA)",
          seq[2] > 0x7F >= seq[1] and seq[4] > 0xC0 >= seq[3] and seq[5] >= 0xFA > seq[4], f"{seq}")
    check("quiet pulse period 32 game s", rg.pulse_period_ticks(0) * 2 == 32)

    # 4. fuel
    c = fuel_model.cruise(50, 300, 8000, True)
    check("50 % + flaps: 60 lb/min, holds level", c["lb_min"] == 60 and not c["stalled"], f"{c}")
    P = profiles.load(overrides=dict(enemy_throttle="auto", transit_throttle="auto"))   # the optimiser itself
    reg = fuelplan.Regimes(P)
    for f in (10000, 2000):
        t = reg.pick(False, f, 300)
        check(f"transit regime at {f} lb = 70 % clean (least lb/km)", t.thr == 70 and not t.flaps, f"{t}")
    e = reg.pick(True, 10000, 300)
    check("enemy regime full tanks = 50 % + flaps", e.thr == 50 and e.flaps, f"{e}")
    check("lb per sample at 50 % = 64", abs(reg.lb_per_sample(8000, 300) - 64) < 1e-9)
    # R9 example: Neuberg -> (4,11) ~75 km each way at 70 %, 1000 reserve -> ~117 samples
    transit = 2 * 75 * reg.pick(False, 9000, 300).lb_km
    samples = (10000 - transit - 1000) / 64
    check("R9 paper example ~117 samples (75 km each way)", 105 <= samples <= 125, f"transit {transit:.0f} lb, {samples:.0f} samples")

    print(f"\n{'all checks passed' if not FAIL else f'{len(FAIL)} FAILED: ' + '; '.join(FAIL)}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
