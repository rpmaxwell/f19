# Terrain and theaters

Mountains (decoded 3D terrain), altitude rules and the per-theater data (Gulf, Libya). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Altitude vs mountains (game mechanic, from the user, 2026-10-09)
- All sea and land sit at one level; the altitude value (0x40250, == HUD) is height
  above that flat world. Mountains rise an undetermined height above it and the
  altimeter never reflects them ("+500 ft at the moment of impact"); near-misses can
  sometimes clip through (fuzzy collision).
- Consequences in the tools: route_planner.py treats mountain cells (briefing-map
  brown/red/dark grey) as impassable with a 1-cell (2 km) margin and never simplifies
  a leg across them; nav.py warns "MOUNTAINS x km AHEAD" from the planner's
  terrain.npz look-ahead along the current heading.
- **Decoded 2026-10-15** (`python -m f19.gamedata.terrain`, details in [`mountain_finder.md` §0](#mountain-finder-result-2026-10-15)): the mountains
  are the game's 3D solids from .3DG/.3DT/.3D3. Heights are in altimeter feet; 3D-world Y is flipped
  vs map Y. 5 of the 6 crashes are inside decoded solids (the 6th was not terrain). The planner now
  routes around them at constant low altitude and rewards radar shadow behind ridges; the briefing-map
  relief is no longer used when THEATER is set.
- Save states: start F19 first, THEN load a state. Loading at the bare DOS prompt
  leaves the game frozen (timer runs, game loop does not).

## Mountain finder: result (2026-10-15)

- **Source of truth:** the F-15 Strike Eagle II decompilation in `analysis/f15se2-re/src` (same engine): `eg3dload.c` (file loaders), `eg3dgrid.c` (`process3dg`), `eg3dproj.c` (`scaleCoordToLod`), `eg3drast.c` (model format, `rotatePoint3d` inside-test), `eg3dmap.c` (`drawNearestTileObject`, `testWorldPosVisible`).
- **`.3DG`:** `"22"`, 16 skipped bytes, then a 16×16 LOD-3 grid (0x100), then three 0x200 tables for LOD 2, 1 and 0. Each table holds 32 tile ids × a 4×4 block of child tile ids. LOD 4 is an 8×8 grid built into EGAME.EXE (EXEPACKed; unpacked at 0x1f546), indexed by theater: LB 0, PG 1, NC 2, CE 3.
- **`.3DT`:** `"11"`, 5 sizes (all 32), then 5×32 object counts (one list per LOD × tile id), then (x, y, z, shape) records. These are offsets from the tile centre in that LOD's units (4096 per tile).
- **Scale:** tile = 0x1000 << SHIFT, with SHIFT = 0/2/4/6 for LOD 1/2/3/4. That gives 2, 8, 32 and 128 km tiles; position units are 2048 per km.
- **Y axis:** the 3D world's Y is flipped: `y3d = (0x8000 − mapY) << 5`. This was the missing piece of the earlier formula hunts.
- **Heights** are in altimeter feet (vertex z << SHIFT).
- **`.3D3`:** shape offset table, then model streams. Each stream is a render byte, then LOD-jump entries (0x80), then an opcode/face-count byte, then faces of 8 bytes (nx, ny, nz, thr), then a vertex count (bit 0x80 means indices into the shared tables), then vertices (mask + x, y, z).
- **Collision:** the game counts you as crashed when you are inside a solid. A solid is a shape with at least 4 faces, and inside means `n·d < thr` for every face (`rotatePoint3d`). `terrain_3d.World.solids_at()` reproduces this test.
- **Validation:** all 3 NC crashes and 2 of 3 CE crashes lie inside decoded solids. Random points at the same altitude land inside one only 0.8% (NC) and 5.5% (CE) of the time. The CE miss (nav 1791600017) was a 100 ft/s climb 2.5 km after the Ramstein takeoff, with no terrain within 900 m, so it wasn't terrain.
- **Altitude units:** with heights in feet, every crash flight enters terrain only in its last rows, and no surviving flight in `logs/nav` ever passes through terrain. Heights scaled ×0.62 or ×1.6 break this check.
- **Outputs (`terrain_3d.py`):** `logs/theater/<TH>_terrain.npz` (top-of-terrain ft every 16 waypoint units, i.e. 0.25 km), `<TH>_mountains.json` (solids ≥ 200 ft) and `<TH>_terrain.png` (preview with crash rings).
- **Heights:** NC up to 6144 ft (Finnmark, Khibiny); CE Alps 9216–12288 ft; LB 8192 ft; PG 8192 ft.
- **Planner:** `route_planner.py` (THEATER set by `plan_mission.py`) treats every cell with terrain ≥ MTN_FT (200) as a wall plus a 1-cell margin. Routes go around mountains at a constant CRUISE_FT (450) and never climb over them (climbing is what exposes you; manual pp. 41–42). Radar shadow is a real line-of-sight test from the radar (RADAR_FT 30) to the plane at cruise altitude, so the cost rewards staying in the lee of ridges. The output reports km flown "terrain-masked".
- **Crash discs:** off by default (`CRASH_DISCS=1` restores them).
- **Nav:** `scripts/nav.py` warns "MOUNTAIN x km AHEAD (top N ft) - TURN AWAY" from the exact game-unit heightfield (6 km ahead, ±0.5 km corridor). Replaying the logs, it fires 17–53 s before every terrain crash.
- **Katowice (CE):** the old flown plan crossed 104 km of mountains (up to 7040 ft). The replan crosses none, and its exposure falls from 78.6 to 37.8.
- **Open:** whether the game itself reduces radar detection behind terrain is the manual's claim, not yet measured. Check EMV and detection in the memlogs with the new shadow map. LB/PG theater indices for the LOD-4 grid are assumed from the menu order.

## Other theaters, terrain over-statement and the 20 km turn radius (2026-10-24)

### Gulf / Libya data (kept)
- Theater codes CE / NC / LB / PG map to CE.WLD / NC.WLD / LIBYA.WLD / GULF.WLD (`analysis/theaters.py`). `read_mission.py`
  and `new_plan.py` detect the theater from the .WLD record table in memory (`--theater auto`, the default); `--theater`
  still overrides. Place lists: `logs/theater/<code>.json` for all four (`python -m f19.gamedata.wld`; CE/NC unchanged).
- Airbases: PG Ras Shaffaniyah, As Salamiyah, Al Hufuf, Dhahran, Bahrain; LB Sigonella only; NC Lakselv, Kautokenio, Gallivare.
- SAM record class differs per theater (record 0's class: CE 294, NC 292, LB 305, PG 308); read_mission finds the radars
  by table structure, so this needs no table.
- Radar codes outside the CE/NC briefing table come from EGAME's own tables (`radar_game.radar_spec`, weapon names at
  ds:4C10): 10 SA-N-6 D, 12 Hawk D, 13 Rapier P, 14 Tiger P, 15 Seacat P. Their `range_km` is the SAM's missile range
  (`sam_range_km`); their briefing-map circles have not been measured. The planner and nav already used the game tables
  by code, so only display / radar_flight.py were affected.
- **Clouds.** Each of PG (2) and LB (1) has a LOD-4 object (shape 21 / 19), a 96 x 80 km, 4-face box topping out at 8192 ft,
  lying on cells the game's terrain grid marks flat or sea. The user identified them as the game's large rectangular
  clouds. The height field filled them from the ground up, which walled off open Gulf water (Bahrain unreachable from the
  north). `terrain_3d.py` now leaves out LOD-4 objects wider than 64 km on non-mountain cells (`World.is_backdrop`); CE and
  NC terrain is byte-identical. Open: whether the game's inside test counts flying into a cloud as a crash (~8000 ft only).

### Why the Gulf looks over-mountained (measured; planner NOT changed)
Mission in memory: Elite, photo the airplane at Shiraz (17616,10988), then the SAM radar at Bandar Khomenyi (8000,9536),
Bahrain -> Ras Shaffaniyah. The planner failed: "no usable attack bearing" for Shiraz.
- **Knolls.** Between Shiraz and the coast there are ~140 LOD-2 solids (shapes 10/11): 1-1.5 km wide, 256-384 ft. They
  are 16 % of the Gulf's wall pixels. None of their 32 km cells is a mountain cell in the game's own grid (clutter
  rough/flat). The bigger relief is the same shapes at LOD 3 (4-6 km, 1000-1500 ft) and LOD 4 (16-24 km, 4-6000 ft).
- **Padding.** Every solid >= mountain_ft (200 ft) gets clear_km 2 km, plus plan_margin_km 1.5 km while planning. A 1.5 km
  knoll becomes a ~8.5 km blocked blob; near Shiraz 23 % of the grid is blocked on clear_km and 33 % with the margin.
  The dilation is a square, so it reaches 2.83 km (41 % further) on the diagonals.
- **But the knolls are real obstacles at Elite.** At Elite the planner flies threat_ft (250 ft) over all enemy ground
  (low_in_enemy_from_opp), so 256-384 ft knolls can't be overflown without climbing.
- **Nav cross-track error** (25 recent nav logs): median 0.04 km overall, p95 ~2.5-2.8 km in normal flights. Clearance
  much below 2 km is risky.
- **No evidence solids are passable.** In no logged flight (theater tagged by takeoff base) was the plane more than 30 ft
  below the top of a decoded solid in its own theater, apart from two small edge cases (NC LOD 3 x15 samples, CE LOD 4 x5).
  But there is no low-level Gulf flight, so nothing tests the knolls either way.
- The pocket was still connected under every rule tried (flood fill Shiraz -> Bandar Khomenyi). What fails is the
  **20 km turn radius** in it: see below.

### 20 km turn radius in tight terrain (what broke; all reverted)
A reversal at R 20 km needs a ~40 km wide clear area. Shiraz sits in a pocket narrower than that, and the photo run has to
be flown straight over the target on one bearing. What happened, step by step:
1. The radius-aware grid search did find a way onto the run-in heading between the hills. Leg reduction (`Router.reduce`)
   then kept only the fewest corners, and the minimum-radius join into the fixed run-in rebuilt the turn as a full 20 km
   loop wherever it landed (through the mountains east of Shiraz).
2. `Router.violations()` stops after the first kind it finds (short legs before terrain), so a leg through a mountain was
   invisible while a short leg existed. `repair()` then rejected every fix as "more problems".
3. `repair()` pushes corners outward up to 4x the arc inset per step with no limit; on Shiraz it ran legs out to 300 km.

Tried on 2026-10-24 and **reverted** (planner logic is back to the 2026-10-23 state):
- knolls (< 500 ft) with 1 km clearance and no plan margin; disc instead of square dilation;
- keeping the search's corner 0.6 R before/after every fixed heading in `reduce()`;
- `violations()` counting all kinds; repair: no pushes for short legs, push steps <= 2x, "slide" corners for short legs,
  never accept a fix with more problems, route may grow <= 25 %;
- attack bearings with 12 km of clear lead-in and run-out preferred / added as candidates.
Together they planned the Shiraz mission (980 km, all checks passed, primary photo on 150 from the east), but they changed
Central Europe: ce_liberec_1023_2032 went from 736 km (cost 2284) to 907 km (~3700, 7 min in SA-14 cells), because cheap
options that used to pass came out with errors. The CE change was not isolated to one of the edits before stopping.
Open ideas: knoll clearance (data-driven, nav p95 2.5 km), disc dilation and the violations() counting bug are each
small and probably right on their own, but each needs a CE regression run before it lands; the Shiraz-type pocket
probably needs attack bearings checked for 20 km turning room rather than repair heuristics.
