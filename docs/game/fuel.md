# Fuel

Fuel burn and cruise speed (EGAME, memlog-verified). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Fuel burn and cruise speed — EGAME (2026-10-18)

Model: analysis/fuel_model.py (`cruise(thr, alt, fuel, flaps)`; run it for the table). Checked against all logs/memlog flights.

**Burn** (0x26a4, the only place fuel goes down): one step every 2 clock ticks (4 game s), while the throttle *setting*
ds:46F2 != 0 (not the spooled power ds:85F8): `2 + thr*thr // 1000` lb, + damage count with a tank leak (bit 0x20).
Steps: thr 0-30 -> 2, 40 -> 3, 50 -> 4, 60 -> 5, 70 -> 6, 80 -> 8, 90 -> 10, 100 -> 12 lb (x15 per game minute).
- **No altitude, flap, gear, ordnance-weight, fuel-weight or difficulty term.** Verified: observed/predicted burn
  0.98-1.005 in 14 of 15 memlog flights, at Green (opp 0) and Veteran (opp 2), 2-10 frames per tick, time acceleration x1
  and x2. The 15th (0.11) contains an Extra Fuel transfer (+1900 lb, 703 -> 2488) and matches exactly either side of it.
- Fuel is written only at 0x3cce (10000 at start), 0x4ac3 (5000, training refill), 0x26d4 (0 when empty) and 0x7ae6
  (+1900, Extra Fuel). It doesn't depend on difficulty or loadout. The Extra Fuel tank weighs nothing until transferred.
- Throttle settings reachable by keys (0x2065/0x20b6): -10 per tap, +5 below 10 %, else +10; clamp 0-100; no afterburner.
- Engine damage (bit 0x02) caps throttle at 100 - 4 x count. A leak adds `count` lb per step: one Veteran hit takes the
  count from 2 to 5, so a leak alone is +75 lb/min, more than the whole 50 % burn.

**Speed** (0x27b5..0x2907, memlog steady states within 1.5-3 %): the game eases ds:7F94 (27 x kt) toward
`T = 6 (spool - 120 sin(pitch)) x (1 + alt/131072) x (100 - fuel/512)/90 x (128 - load)/128`, then T -= T/16 with flaps,
T -= T/8 with the gear down; `v += ((27T - v)/16)/fpt` each frame (integer, so it settles 2-4 kt short). Level flight
needs pitch = AoA = 0xC00 x stall/kt - 0x300 (x cos roll), stall = 120 (1 + alt/65536) sqrt(load/16), x3/4 with flaps;
load ds:662A = 16 wings level. Brakes airborne: -1/16 per tick. Nothing reads the loadout.

**So, on the three claims:**
1. *Lower burns more:* not per minute. Per km, slightly: a throttle buys +0.76 % speed per 1000 ft but stall (and AoA
   drag) also rises, net (70 %): 956 lb/100 km at 300 ft, 932 at 5000, 885 at 15000, 824 at 30000. 300 -> 5000 ft saves
   ~2.5 %; going high only pays on long transits.
2. *Flaps burn more:* not per minute. Flaps cost 1/16 of speed but cut stall by 1/4, which lowers the AoA penalty; at
   50 % and heavy they are the difference between holding ~185 kt and not holding level (memlog: 50 % flaps 196 kt,
   pitch 3.5; 50 % clean 171 kt, pitch 7.2). At 60-70 % they're within ~1 %; at 80-100 % they cost 3-6 % range.
3. *Heavier loadouts burn more:* ordnance weight is not modelled at all. **Fuel** weight is: full tanks fly ~20 %
   slower at the same throttle than 3000 lb (so ~20 % more fuel per km), and the burn is the same.
4. *Difficulty:* no effect on fuel anywhere.

**Range table** (level, gear up, flaps up, lb per 100 km; heavy = 10000 lb / light = 3000 lb, 300 ft):
50 % can't hold level heavy (with flaps: 182 kt, 1068); 60 % 240 kt 1010 / 300 kt 809; **70 % 305 kt 956 / 370 kt 787
(most efficient)**; 80 % 1072 / 894; 100 % 469 kt 1243 / 550 kt 1060. The integer burn steps and the fixed AoA power
cost make 70 % the best lb/km at every altitude and weight, and it's also fast. Typical 50 %-with-flaps cruise: ~60
lb/min, 10000 lb lasts ~2.7 game h.
- **Katowice re-run (2026-10-18, nav full_run5): both targets done, then out of fuel ~24-30 km short of Rhein-Main.**
  Flown 945 km in 83.8 real min (plan 983 km / 167.7 game min, so the sim ran ~2x real time), mean 190 kt, mean 287 ft,
  throttle 40/50/60 % for 24/61/14 % of the time (mean 49 %). Photo: one frame at 3.35 km, confirmed. Maverick: 13.4 km, 303 ft,
  kill in 14 s. Route following: cross-track mean 0.84 km, > 2 km for 15 % of the time. Fuel: the plan's 10,060 lb comes from
  60 lb/game min, ONE sample (ce_cottbus, ~225 kt, ~250 ft); fuel_loaded_lb was unset, so the 1,000 lb reserve was never applied.
  If ~11,500 lb was loaded (internal + 1,500 tank), the real burn was ~71 lb/game min (+19 %). The burn rate needs measuring
  (fuel address unknown; scripts/fuel_watch.py reads the gauge by OCR) against throttle/speed/altitude.
  Altitude hold at 300 ft: 894 up / 284 down / 43 UP! presses; unprovoked sinks at -15 ft/s down to 56 ft (route) and 144 ft
  (after takeoff); the floor guard's single 0.6 s taps recover too slowly. Speed hold: 502 throttle presses (each a D2 wipe),
  hunting between 40 and 60 %.
