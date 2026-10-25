"""F-19 fuel and cruise-speed model, decoded from EGAME.EXE (FINDINGS.md "Fuel burn and cruise speed", 2026-10-18).

Burn (0x26a4): every 2 clock ticks (4 game s) while the throttle SETTING is not 0:
    fuel -= 2 + thr*thr // 1000   (+ damage count with a fuel-tank leak)
No altitude, flap, gear, weight, loadout or difficulty term. Those act only through the speed the
throttle buys (0x27b5), which the game eases toward:
    kt = 6 (spool - 120 sin(pitch)) x (1 + alt/131072) x (100 - fuel/512)/90 x (128 - load)/128
         x 15/16 with flaps x 7/8 with the gear down
Level flight holds pitch = AoA = (0xC00 stall/kt - 0x300) (65536 = 360 deg), stall = 120 (1 + alt/65536)
x sqrt(load/16) x 3/4 with flaps; load = 16 wings level. Checked against logs/memlog: burn exact in all 15
flights; steady speed within 1.5-3 %.

  fuel_model.py            print the cruise table
  from fuel_model import cruise; cruise(thr, alt_ft, fuel_lb, flaps) -> dict(kt, lb_min, lb_per_100km)
"""
import math

THROTTLES = [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100]   # reachable: -10 per tap, +5 below 10 %, else +10
STEPS_PER_MIN = 15                                          # one burn step per 4 game s


def burn_step(thr, leak=0):
    return (2 + thr * thr // 1000 + leak) if thr else 0


def cruise(thr, alt=300, fuel=8000, flaps=False, gear_up=True, load=16):
    """Steady level flight at throttle `thr` (spool = throttle once spooled up)."""
    stall = 120 * (1 + alt / 65536) * math.sqrt(load / 16) * (0.75 if flaps else 1)

    def target(kt):
        aoa = max(0.0, 0xC00 * stall / kt - 0x300) / 65536 * 2 * math.pi
        t = 6 * (thr - 120 * math.sin(aoa)) * (1 + alt / 131072) * (100 - fuel / 512) / 90 * (128 - load) / 128
        return t * (15 / 16 if flaps else 1) * (1 if gear_up else 7 / 8)
    # speed relaxes toward target(kt): the stable equilibrium is the highest kt where target falls through kt
    kt = next((v / 2 for v in range(1800, int(2 * stall), -1) if target(v / 2) >= v / 2), 0.0)
    stalled = kt < stall
    lb_min = burn_step(thr) * STEPS_PER_MIN
    km_min = kt * 1.852 / 60
    return dict(kt=round(kt), stall=round(stall), stalled=stalled, lb_min=lb_min,
                lb_per_100km=None if stalled else round(100 * lb_min / km_min))


if __name__ == "__main__":
    for flaps in (False, True):
        print(f"\n{'flaps DOWN' if flaps else 'flaps up'}, gear up, level. kt / lb per 100 km (x = below stall)")
        print(f"{'thr':>4} {'lb/min':>6} " + " ".join(f"{f'{a} ft {f//1000}k lb':>17}" for a in (300, 5000, 15000, 30000) for f in (10000, 3000)))
        for thr in THROTTLES[2:]:
            cells = []
            for a in (300, 5000, 15000, 30000):
                for f in (10000, 3000):
                    c = cruise(thr, a, f, flaps)
                    cells.append(f"{'x' if c['stalled'] else c['kt']:>8} {'' if c['stalled'] else c['lb_per_100km']:>8}")
            print(f"{thr:4d} {burn_step(thr) * STEPS_PER_MIN:6d} " + " ".join(cells))
