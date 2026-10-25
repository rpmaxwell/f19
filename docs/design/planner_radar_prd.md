# PRD: planner radar model and fuel-efficiency optimisation from the game's own rules

Owner: planner agent. Requested 2026-10-18. Code: `analysis/planner/` (radar.py, route.py, profiles.py, plan.py).

## Why

`analysis/planner/radar.py` scores exposure with a heuristic: strength `(1.1 - d/range)^2`, an aspect weight,
`doppler_weight`, and **terrain-shadow grids**. The game's radar code has now been decoded and checked against every
recorded flight, and the heuristic is wrong in ways that change routes:

- **There is no terrain line of sight** in the game's detection. Ridges don't hide you. What matters is the
  **terrain type of the 32 km cell you're in**: mountain cells hide you from every radar, sea cells make you 1.5x as visible.
- Pulse radars see you **only side-on**, dopplers **only head-on/away and only fast** (SA-10 head-on: 0 km at 150 kt,
  15 km at 200 kt, 118 km at 400 kt, Veteran, EMV 10).
- Detection is a hard threshold (`return + EMV > 100`), and what hurts is consecutive pulses: alert +48 per seen pulse
  (Veteran), -16 per unseen one; 3 in a row → every site is told your position, 4 → SAM launch possible, 6 → radar ID.

- **Fuel is score.** Cold-war points come from 64 game-s position samples in enemy cells, and the plane burns a
  fixed amount per minute whatever its speed, weight or altitude. Fuel spent in enemy airspace at the right throttle
  buys samples; fuel spent transiting buys nothing. The planner currently assumes one cruise speed and fuel ∝ distance.

## Source of truth

- FINDINGS.md: ["Radar sites: pulses, duty cycle, detection"](../game/radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18), ["Flight-log variables"](../game/memory-map.md#flight-log-variables-fuel-damage-clock-ids-threats--egame-2026-10-18) (SA-14 cells, IDs), ["Fuel burn and
  cruise speed"](../game/fuel.md#fuel-burn-and-cruise-speed--egame-2026-10-18).
- **`analysis/radar_game.py`**: the game's formulas in integer form (`emv`, `site_return`, `seen`, `alert_step`,
  `pulse_period_ticks`, `duty_fraction`, `sam_range_km`, `radar_spec`). `python analysis/radar_game.py check` replays the
  memlog snapshots (EMV 17344/17359 within ±1, pink pulses 726/766, duty flag 97 %). Use it; don't re-derive.
- **`analysis/fuel_model.py`**: `cruise(thr, alt, fuel, flaps)` → kt, lb/min, lb/100 km.
- Territory grid: `analysis/territory.py` `grid(th)`; bits 0-1 side, **bits 2-3 terrain (0 mountain, 1 rough, 2 flat,
  3 sea)**, bit 4 SA-14 cell.

## Requirements

### R1. Detection from the game's formula
Replace `RadarModel.detection()` internals with, per site and route point:
`s = radar_game.site_return(...)` using the **plane's** cell terrain bits, its heading, planned speed and altitude, the
mission's opp; `margin = s + EMV - 100`. Keep the signature `detection(x, y, ux, uy)` (arrays) so route.py and plan.py
are unchanged, but add the leg's speed and altitude as parameters (default: profile cruise).
- Exposure cost = expected **seen pulses** per km: `P(active) x pulses per game s x s per km x [margin > -M]`, with
  `P(active) = radar_game.duty_fraction()` (frame counter is random at start) and a safety margin M (start 10; the
  model's error is under ~30 near the threshold). Pulses: one per 32 game s per quiet site.
- Remove the shadow grids from detection (`_shadow`, `MODEL["shadow"]`, `radar_ft`, `los_step_km`). `in_sight()` in
  reports should list sites with `margin > -M` instead of line of sight. Terrain stays a collision constraint only.

### R2. EMV along the route
`radar_game.emv(alt, kt, opp, roll, bay, ecm)` per point:
- bank in turns: derive roll from the planned turn rate (nav's cap 0.8 deg/s cruise, 1.6 loiter) or make it a
  profile parameter (`turn_bank_deg`, default 20 → +7 EMV). Replace `turn_penalty_km` by this where it represents radar.
- bay open: the attack windows (photo run, Maverick launch) from plan.py's attack points, +24 for their duration.
- ECM off by default (+16, halves alert gain; a future option).

### R3. Alert simulation and report (acceptance output)
For the final route, simulate every site pulse by pulse in game time (cruise speed → time; period
`pulse_period_ticks(alert)`; worst case = always active, expected = duty fraction) with `alert_step`. Report per site:
max alert, first time > 0x7F / 0xC0 / 0xFA, and whether a launch is possible there (alert > 0xC0, inside
`sam_range_km(type)`, and > 20 km from an airfield record: radar_game.AIRFIELD_SAFE_UNITS). Add the totals to the plan
summary. A plan with any predicted 0xC0 crossing should say so first.

### R4. Cell terrain in the search
- sea cells (×1.5) cost more, rough (×0.5) less, mountain cells zero radar exposure.
- **Do not route over mountains** in normal routing. The user's rule (mountains are walls, fly around) stands. Mountain
  cells are still useful: valleys, or legs along their edge (the cell type counts, not the height under the plane).
  The user has asked to explore loitering in / over enemy mountain cells: see R9 (an exception scoped to that tactic).

### R5. Speed per leg
Speed changes EMV (+1 per 38 kt), doppler returns (rise with speed), fuel per km and time. Each leg gets a throttle
setting and flaps (reachable throttles: 50, 60, 70, 80 %; `fuel_model.cruise()` gives kt). R6 says which to pick;
radar margins may override it (e.g. slow inside a doppler site's danger margin). nav must be able to hold it: check
phases.Throttle / nav `--auto` before relying on per-leg speed and say what's needed if not.

### R6. Fuel model and fuel-efficiency optimisation (fuel = time in enemy airspace = points)
Facts ([FINDINGS.md "Fuel burn and cruise speed"](../game/fuel.md#fuel-burn-and-cruise-speed--egame-2026-10-18), memlog-verified; `analysis/fuel_model.py`):
- Burn per 4 game s = `2 + thr²//1000` lb while the throttle setting is not 0, + damage count with a tank leak.
  **Nothing else**: no altitude, flaps, gear, ordnance, fuel weight or difficulty term. lb/min: 50 % 60, 60 % 75,
  70 % 90, 80 % 120, 100 % 180.
- Speed at a throttle falls with **fuel weight** (full tanks ~20 % slower than 3000 lb), flaps (-1/16 but lower AoA
  drag), gear (-1/8), turns (load) and rises slightly with altitude. Ordnance weight isn't modelled.
- Start fuel 10000 lb. Extra Fuel = +1900 lb when transferred (works with the bay closed, no EMV cost) and weighs
  nothing until then.
- Score per enemy sample = +1 base × the settings multipliers (CE, strike, Veteran, Realistic, fuel-estimate 9/8:
  ~4.5 points; up to ~6.7 at top settings). The fuel multiplier is the **briefing** estimate (commData+0x2E is never
  written in flight), so fuel left at landing is worth nothing in itself.
- Visual ID (event 6) costs **-20 base** and radar ID (event 7) **-10 base** in Cold War (END score table): one radar ID
  = 10 samples ≈ 10 game minutes of loiter. Feed this into the exposure weight so radar cost and fuel cost are in the
  same currency (points).

Requirements:
1. **Two regimes by cell side.** In enemy cells the objective is samples per lb → **minimum lb/min that holds level**:
   50 % with flaps (60 lb/min = 64 lb per sample; holds level at every fuel weight, 182-240 kt; also slow enough to
   blind dopplers head-on). In friendly / neutral cells the objective is lb per km → **70 % clean** (best lb/km at every
   weight and altitude: 956 lb/100 km full, 768 at 2000 lb) and the shortest transit. Don't hard-code these: choose per
   leg with fuel_model + the R1 radar margins, and report the choice.
2. **Loiter budget.** `enemy_samples ≈ (fuel_loaded + extra_fuel − transit_fuel − reserve) / lb_per_sample(enemy
   throttle)` plus the samples the route itself spends in enemy cells. Count samples by walking the route in game time
   (one per 64 s, phase unknown → report a range), as [notes/score_estimate_for_planner.md](score_estimate_for_planner.md) describes, but with each
   leg's own speed from fuel_model (not a flat 6.2 km).
3. **Where to spend it.** Loiter / extra route length only in enemy cells that are radar-quiet under R1 (rough or
   mountain-edge cells, no SA-14 cell by day, away from doppler head-on and pulse side-on aspects) and not near a
   network-alerted area. A loiter that collects samples but causes an ID loses 10-20 samples: compare in points.
4. **Transit legs to and from enemy territory**: minimise lb (70 %); slower only where radar requires.
5. **Fuel along the route**: fuel at each waypoint and at landing, with fuel-weight-dependent speed (integrate, don't
   assume one speed). Reserve is a profile parameter (`landing_reserve_lb`). **Open question for the user:** the score
   doesn't penalise a crash (only ejecting in enemy cells, x2/8), so a "no reserve" plan scores more; that's a career
   decision, not the planner's. Default to a landing reserve; show the points it costs.
6. **Profiles**: `fuel` profile = maximise enemy samples per lb as above; `stealth` may give up samples for margin;
   `fast` = 70 % everywhere. Report expected samples, points (samples + targets − predicted ID penalties), fuel at landing.
7. **Flight tests to request** (in the plan output, not assumed): (a) 40 % with flaps when light (< 4000 lb) — the model
   says it can't hold level, but it is pessimistic near that edge (it said the same of 50 % clean heavy, which flew at
   171 kt); 40 % would be 45 lb/min, +33 % loiter time. (b) Measured lb per sample at 50 % flaps from the new flight log.
   A climb-and-glide sawtooth was checked on paper and doesn't beat level 50 % (convex burn), so don't plan it.

### R7. SA-14 cells
In daylight (night flag ds:4EF4 / briefing), every cell with bit 0x10 spawns an SA-14 every 32 game s you spend in it
(and triggers a network alert). Only some are live per mission (0 or 3-5 of the file's 8-10), unknown until takeoff, so
avoid all of them by day with a high cost per minute inside; free at night. Expose the live set as an optional input
(nav reads ds:85FC at takeoff) for re-planning.

### R8. Settings
`opp` (from the plan's settings), `war` = gameData+0x3A (0 in all flights so far; default 0), night flag. All of the
radar numbers scale with opp; [the table in FINDINGS](../game/radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18) is Veteran.

### R9. Exploratory tactic: score-farming loiter in an enemy mountain cell next to friendly lines
The user's idea (2026-10-18): loiter in enemy territory where radar can't see the plane, collect the 64 s samples, then
fly back across the line and land. Low risk, points limited only by fuel. Build it as a separate plan mode / profile
(`mountain_loiter`), usable alone or after the mission targets.

Why it should work (from the decoded code; **not yet flown**):
- Return `s` is multiplied by the terrain bits of the plane's **32 km cell**: mountain cells give s = 0 for every ground
  site and every aircraft radar (visual IDs use the same function). The plane is "seen" there only if EMV alone exceeds
  100, which needs extreme altitude plus bay, ECM and steep bank together. Brute-forced over all Soviet radar types,
  aspects, 150-300 kt and 1-10 km: max return 0 in mountain cells at Veteran and Elite.
- So the plane doesn't need to be *above* the mountain: the whole cell is invisible, and the candidates' median terrain is
  0 ft. **Preferred: a low racetrack (cruise altitude) over the flat part of the cell**, clear of the peaks laterally (no
  climb fuel, no terrain risk, keeps the walls rule). **Option: an orbit above the peaks** (max 9,200-12,300 ft; EMV
  at 13,000 ft ≈ 60, still invisible). Compare both in fuel and terrain margin; present the high one only as an option.
- Rough enemy cells (terrain bits 1) are a fallback: max return 57 at Veteran (SA-8B, 1 km, side-on), 69 at Elite, so
  invisible while EMV < 44 (Veteran) / < 32 (Elite). A low loiter at 30 deg bank is EMV ~20 (bay open + bank = 44: no).

Candidates (territory grid + decoded terrain, CE; other theaters have almost none: NC (10,7) 4 cells deep, PG (9,15) flat):
| cell | from friendly | peaks | flat area | SA-14 bit |
|---|---|---|---|---|
| **(4,11)** | adjacent: (4,12), (3,12) friendly | 9,216 ft | 8 km band along the whole north edge (0-96 ft); SW corner ~8-12 x 16 km at 0 ft touching the friendly cells | no |
| (5,11) | 1 cell | 12,288 | north 8 km band; SE corner | no |
| (6,13) | adjacent: (5,13) friendly | 12,288 | south 8 km band | no |
| (3,11), (5,12) | adjacent | 12,288 | — | **yes**: avoid by day, usable at night |
Nearest friendly bases to (4,11): Neuberg 88 km, Ramstein 94 km, Rhein-Main 110 km (from the cell centre; ~75 km
from Neuberg to the SW corner). Grossenhaim (SA-10, enemy airbase) is 56 km away: invisible from it inside the cell,
but the cell edges and the way in and out are not.

Requirements:
1. Find candidate cells per theater: enemy side, terrain bits 0 (fallback 1, with the EMV ceiling above for the
   mission's opp), adjacent or near friendly cells, SA-14 bit off (or night mission), and a flat area that fits a
   racetrack with terrain margin (nav's loiter turn: 1.6 deg/s → ~3.7 km radius at 200 kt, so ≥ ~8 km wide).
2. Racetrack geometry: entirely inside the cell with a margin (≥ 2 km) from every edge, so no sample falls outside it and
   no part is over a visible cell. Samples outside the cell score nothing; time over an enemy flat cell is visible.
3. Way in and out: from/to a friendly base through friendly cells, crossing into the loiter cell directly (shortest
   time in visible cells near enemy sites); R1/R3 exposure and alert simulation for those legs.
4. Fuel: loiter at 50 % + flaps (64 lb per sample) or what holds level at the chosen altitude (fuel_model.cruise with
   the turn's load); transit 70 %. Budget = fuel (+ Extra Fuel) − transit − reserve. Example to reproduce: Neuberg →
   (4,11) SW corner ~75 km each way, ~1,500 lb transit + 1,000 reserve → ~7,500 lb loiter ≈ 117 samples ≈ 520 points
   (CE, strike, Veteran, Realistic, fuel x9/8); +Extra Fuel ≈ +29 samples. Report samples and points as a range.
5. Only pays in **Cold War** ROE (+1 per enemy sample; Limited +1 per 4, Conventional 0): check the setting.
6. Combine with a mission: after the primary/secondary, return via the loiter cell with the remaining fuel.
7. Output a short test plan for the first flight (the logger records everything): 10-15 min loiter, then home. Pass if
   every site's alert stays 0 inside the cell, V and R stay 0, no SAM/SA-14 events, samples land in the cell (event list
   type 9 positions), and the debrief points match the formula. **Unknowns to watch:** whether enemy fighters on patrol
   can attack without detecting the plane (they can't ID it by the code, but attack logic isn't decoded); the exact
   flat-area boundaries (terrain_3d heights) vs nav's tracking error; drifting out of the cell.

## Not in scope
nav changes, flying the plan, re-deriving any formula. If the code disagrees with radar_game.py, radar_game.py and
[FINDINGS.md](../../FINDINGS.md) win; report the disagreement.

## Done when
- `radar_game.py check` still passes; planner unit checks reproduce three numbers from the [FINDINGS detection table](../game/radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18)
  (e.g. SA-11 side-on 50 km, SA-10 head-on 15 km at 200 kt, SA-2 head-on never; Veteran, EMV 10, flat).
- A re-plan of `logs/plan/ce_tabor_auto` and `logs/plan/ce_katowice` shows the R3 report, with routes that avoid SA-14
  cells by day, fly radial to pulse sites / tangential to dopplers near them, and prefer rough/mountain-edge cells to sea.
- profiles still work (stealth / fast / fuel); the report states the predicted max alert per site and fuel at landing.
- The report gives per-leg throttle/flaps, expected enemy samples (range), expected points with ID penalties, and the
  loiter budget; the `fuel` profile beats the current planner's sample estimate on the same mission.
- R9: a `mountain_loiter` plan for CE from Neuberg (or the nearest friendly base) to cell (4,11) with the racetrack,
  fuel budget, expected samples/points and the first-flight test plan.
- FINDINGS.md gets a short ["Planner model (date)"](planner.md#planner-model-2026-10-19) note; old planner beliefs in radar.py's docstring are replaced.
