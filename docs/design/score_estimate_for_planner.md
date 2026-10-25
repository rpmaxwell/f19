# Note for the flight-planning logic: an "estimated score" for a mission plan

From the session that decoded the debrief (2026-10-17). Source of truth: FINDINGS.md, section
["Mission rating formula — decoded from END.EXE"](../game/scoring-awards.md#mission-rating-formula--decoded-from-endexe-2026-10-17); disassembly in analysis/end_re/score_real.asm.
The formula below reproduces two observed debrief numbers exactly (850 -> 212, and 833).

## The formula

```python
# Cold War values; ROE = rules of engagement (0 Cold War, 1 Limited, 2 Conventional)
THEATER  = {"LB": 8, "PG": 10, "NC": 12, "CE": 13}           # gameData+0x38 (menu order 0..3)
MISSION  = {"air": 8, "strike": 9}                           # gameData+0x3c (training: same values)
OPPONENT = {"green": 8, "regular": 11, "veteran": 14, "elite": 17}   # gameData+0x3e
LANDING  = {"no_crashes": 5, "easy": 8, "realistic": 10}     # gameData+0x40
PRIMARY  = {0: 140, 1: 120, 2: 100}                          # by ROE

def fuel_mult(fuel_estimate_lb):                             # commData+0x2e (see caveat 1)
    return 11 if fuel_estimate_lb >= 11500 else 10 if fuel_estimate_lb >= 9000 else 9 if fuel_estimate_lb >= 7000 else 8

def mission_score(enemy_samples, primary, secondary, s, roe=0, ejected_in_enemy=False):
    base = 0
    if roe == 0:   base += enemy_samples
    elif roe == 1: base += enemy_samples // 4
    if primary:    base += PRIMARY[roe]
    if secondary:  base += 35 if primary else 70
    score = max(base, 0)
    for m in (THEATER[s["theater"]], MISSION[s["mission"]], OPPONENT[s["opponent"]],
              LANDING[s["landing"]], fuel_mult(s["fuel_estimate"])):
        score = score * m // 8                               # integer, truncating, in this order
    if ejected_in_enemy: score = score * 2 // 8
    return score
```

- A safe landing or a crash: no penalty. Bailing out: x2/8 in an enemy cell (confirmed). Other cells use
  x3/8, x6/8 or x4/8, but which applies where is not known yet.
- Kills: 0 points in Cold War (Limited 1-9, Conventional 3-18 base by target class). Destroying friendly or
  neutral-cell targets costs -25 base. Plan for no kills in Cold War.
- Top settings (CE, strike, elite, realistic, fuel estimate >= 11500): each enemy sample is worth ~6.7 points,
  primary + secondary ~1160.

## What the planner has to compute: enemy_samples

- The game logs the plane's position every **64 game seconds** from takeoff. A sample scores if its
  32 km cell is enemy: `analysis/territory.py side(TH, x, y) == "enemy"` (waypoint units, cell = x>>11, y>>11).
- Walk the planned route at the planned ground speed in **game time** and test one point every 64 game s.
  At ~190-225 kt that is one sample every ~6.3-7.4 km. smooth_route.py currently uses a flat 6.2 km and
  4.5 points per sample: replace that with this formula.
- An orbit/loiter's value = its samples in enemy cells. A 20 km-radius circle is wider than one 32 km cell,
  so place it where most of it is enemy, or count its samples properly. It is not "laps x 21".
- The radar exposure cost (route_planner.py) stays separate. The score gives no credit for being undetected
  (the debrief "stealth percentage" does not enter the rating).

## Fuel: how many samples the plane can afford

- Measured burn: **~60 lb per game minute** (~64 lb per sample) at ~225 kt, ~250 ft (ce_cottbus flight; the
  Change Waypoint panel shows game time and fuel: 9830 lb at 11:35:24 -> 5890 lb at 12:41:00).
  Higher altitude and lower throttle burn less (manual). Treat 60 lb/min as low-level cruise only.
- Extra Fuel tank: 1500 lb per bay. Internal capacity ~10,000 lb (unverified: 9830 lb shown 30 s after takeoff).
- Loiter budget = fuel loaded - (route fuel to landing) - reserve. Each 64 lb of loiter in enemy cells
  ~ one sample.

## Inputs to collect per mission

theater; mission type (strike / air-to-air); opponents; landing mode; ROE; the briefing's FUEL ESTIMATE;
loadout (extra tanks); which targets are planned to be done. The settings are chosen in START's menus.
Ask the user, or read them from gameData if someone locates it in memory (gameData is a far pointer at
END DGROUP 0x86ae, so the addresses during briefing/flight are unknown).

## Caveats / open items (say so in the plan output)

1. The fuel multiplier field (commData+0x2e) is presumably the briefing FUEL ESTIMATE. Not yet verified live.
   The user has only seen estimates up to ~10,000 lb, so x10/8 may be the practical ceiling.
2. Event types 6 and 7 cost -20 / -10 base in Cold War: 6 = visual ID by an aircraft, 7 = radar ID by a site
   (EGAME, 2026-10-18; [FINDINGS.md "Radar sites"](../game/radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18)). Avoiding IDs is worth points directly.
3. Burn rate is now exact: 2 + thr²//1000 lb per 4 game s, nothing else ([FINDINGS.md "Fuel burn and cruise speed"](../game/fuel.md#fuel-burn-and-cruise-speed--egame-2026-10-18),
   analysis/fuel_model.py). 50 % = 64 lb per sample at any weight/altitude. The "higher / lower throttle burn less
   (manual)" line above is superseded: altitude doesn't change the burn.
4. Present the estimate as a range: route samples +- a few for the timing of the 64 s grid, plus the fuel
   bucket uncertainty.

## Kill points by target type (END 0x4258, decoded 2026-10-18)

Indexed by target category (the .WLD 100-byte table, as in the ordnance matrix). Base points before the
theater/mission/opponent/landing multipliers. Cold War: 0 for everything. A target in a non-enemy cell scores -25.

| category | types | Limited | Conventional | gun v |
|---|---|---|---|---|
| 0 | Depot, Tower, Buildings, Sub Pen, Destroyer, carriers, ... | 2 | 4 | 3 |
| 1 | AirStrip, Cargo Ship | 6 | 12 | 0 |
| 2 | SAM Radar, Typhoon | 1 | 3 | 4 |
| 3 | Mobile SAM | 1 | 3 | 4 |
| 4 | Bridge, OTH/LPAR Radar | 3 | 6 | 0 |
| 5 | AirBase | 8 | 16 | 2 |
| 6 | AirPlane, Refinery | 2 | 4 | 4 |
| 7 | Tank Farm, Camp, POW Camp | 2 | 5 | 4 |
| 8 | Hangar | 5 | 11 | 3 |
| 9 | HQ | 3 | 7 | 4 |
| 10 | CV Kremlin | 1 | 3 | 5 |
| 12 | Krivak, Missile/Patrol Boat | 4 | 8 | 3 |
| 13-15 | SA/SS-20 Missile, Oil Rig, Nuclear Plant | 0 | 0 | (overflow) |

## Hard caps (decoded 2026-10-18)

- **Flight-event log: 255 records** (EGAME append 0x4b40 refuses once ds:632E >= 0xFF; END reads the same list at ds:9186).
  Every 64 s sample, kill, launch and objective takes one record. Anything logged after the list is full is lost,
  **including the primary/secondary**. So Cold War base <= ~250 samples + 175, and long loiters must do the objectives first.
- **Unnamed 3D objects score:** a bomb or gun kill that snaps to a 3D object with no target record (destroyGroundTarget 0x77c3, idx 0)
  logs a type-2 event with the object's shape class, which END scores by category like a record (hangars 11 in Conventional).
  Each object counts once, because it becomes a wreck. So Conventional kills aren't limited to the ~43 enemy WLD records in CE.
