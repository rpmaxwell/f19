# Planner model

How analysis/planner models radar, altitude and fuel from the game's own rules. Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Planner model (2026-10-19)

`analysis/planner` now plans on the game's own radar and fuel rules ([notes/planner_radar_prd.md](planner_radar_prd.md)). The old heuristic
(range-squared falloff, aspect weights, terrain-shadow LOS grids, flat 60 lb/min at one speed) is gone.
- **Radar** (planner/radar.py → radar_game.py, numpy port `site_return_np` checked equal to the scalar code): a site
  counts where return + EMV − 100 > −10 (`radar_margin`); cost = expected seen pulses (duty 37.5 %, one per 32 s) ×
  `pulse_cost_samples`. Cell terrain (mountain 0 / rough ×0.5 / sea ×1.5) comes from the formula; no LOS anywhere,
  probes included. EMV per point: altitude, speed, bank on arcs (`turn_bank_deg` 12, from the 2026-10-14 EMV 7 → 10),
  bay open 15 s before photo/launch points. Radar codes: mission files now carry `kind` (scripts/read_mission.py).
- **Altitude per leg** (2026-10-19, user's rule): 700 ft (`cruise_ft`, fuel-optimal) wherever no site would see the
  plane at 700 ft; 250 ft (`threat_ft`) where one would, from 6 km before (`alt_lead_km`), high gaps < 15 km stay low.
  Legs are split at the changes and each route point carries `alt_ft` (nav holds the alt_ft of the point a leg ends at);
  attack and runway legs aren't split and fly the lower height. Veteran EMV altitude term: 700 ft 13, 250 ft 4.
- **SA-14 cells** (bit 0x10) cost 30 samples/min by day, padded 4 km (fillets cut corners); `sa14_live` overrides.
- **Fuel/speed** (planner/fuelplan.py): enemy cells least lb/min that holds level (50 % + flaps), elsewhere least
  lb/km (70 % clean); slower where that keeps a site under the margin. Integrated with fuel weight; landing reserve checked.
- **Alert simulation** (planner/alertsim.py): every site pulse by pulse, worst (always on) and duty-cycle draws; report
  starts with any predicted > 0xC0 crossing. Radar-ID penalty (−10 base each) goes into the score estimate.
- **Loiters** (planner/loiter.py; `fuel` profile, `"mode": "mountain_loiter"`): racetracks in enemy mountain cells
  (r 4 km, nav's LOITER 1.6 deg/s mode, 1.5 km from walls, 2 km inside the cell), lap count from the fuel budget. NOT flown.

Disagreements found (radar_game.py / the code wins):
- FINDINGS detection table used return + EMV **>=** 100; the code is > 100. SA-11 side-on 45 km (table 50), SA-8B 28 (31),
  SA-2 6 (12). SA-10 head-on at 200 kt: 6.5 km, not 15; the doppler term (27 kt >> 5) steps at 202.7 kt (13.5 km at 203 kt).
- "4 pulses → SAM launch possible" (Veteran): 4 × 48 = 192 = 0xC0, and the gate is alert > 0xC0, so it is the **5th** pulse.
- Cell (4,11) (CE): the PRD's SW-corner racetrack doesn't fit by day (r 4 km + margins); the only fit is a circle in
  the north band, reached only through SA-14 cell (4,9) or radar-visible flat cells. At night (SA-14 cells usable) a
  45 km racetrack fits in the SW: logs/plan/ce_mountain_loiter_night, 108 samples, no site alerted.
