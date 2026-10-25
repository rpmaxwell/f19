# Mountain finder

The goal is to locate F-19's 3D mountains in world coordinates from the game's own files, so the route planner can keep routes away from them.

Status (2026-10-15): **decoded** (`analysis/terrain_3d.py`); see section 0. The rest of this file is the original plan, kept for reference.

## 0. Result (2026-10-15)

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

---

## 1. Why this matters

- **The altimeter can't see mountains.** The world is a flat plane and altitude is height above it. Mountains rise an unknown height above that plane, and the altimeter "will say +500' even at the moment of impact" (user).
- **Low flight is the stealth tactic.** Ground radar is beaten by flying at 300–500 ft, which is exactly where unseen mountains kill us.
- **The briefing map doesn't help.** Its brown/red relief is artwork. The planner already blocks those cells with a 2 km margin, and every crash below still happened outside them.

## 2. Crash sites (ground truth)

Coordinates are in waypoint units (player position >> 5, 64 per km), as logged in `logs/nav/<start>.csv`. Each row is the last valid row before the game reset.

| Theater | Nav log | x | y | Alt (ft) | Heading | Memory log covering the impact |
|---|---|---|---|---|---|---|
| CE | 1791599686 | 4444 | 22800 | 572 | 165 | none (before `--memlog` existed) |
| CE | 1791600017 | 3412 | 23126 | 522 | 24 | none |
| CE | 1791600619 | 4588 | 21848 | 423 | 63 | none |
| NC | 1792028447 | 10655 | 9416 | 417 | 1 | **logs/memlog/1792028452/** |
| NC | 1792032168 | 8414 | 11595 | 416 | 21 | **logs/memlog/1792032174/** |
| NC | 1792034377 | 10105 | 8356 | 413 | 170 | **logs/memlog/1792034383/** |

Notes:
- **Excluded:** crashes at altitude 0 (takeoff, the sensitivity-3 crash, the 4×-time crash, landings).
- **North Cape clusters:** all three are within about 40 km of each other (x 8400–10700, y 8300–11600). That suggests one ridge or massif west of Murmansk.
- **Central Europe clusters:** all three are within about 20 km of each other (x 3400–4600, y 21800–23100).
- **Memory logs of the impacts:** each North Cape crash has snapshots of the flight-data memory window (`egame_data`, old address 0x3C000–0x48000) up to about 1 s before impact. The nearby-object list (section 4) is inside that window, so the object we hit should be in the last snapshot.

## 3. Game files (`~/Documents/F-19 Stealth Fighter/MPS/F19/`)

Each theater has four files. CE = Central Europe, NC = North Cape, LB = Libya, PG = Persian Gulf:

| File | Size | What it is (known or suspected) |
|---|---|---|
| `<TH>.WLD` (CE, NC, LIBYA, GULF) | 2.0–2.9 KB | **Known.** Named places: 16-byte records from 0x38 (name_id, X, Y, kind, f1, f2, f3, class); SAMs, OTH, ships, search radars, airbases (f1 = 1025), targets. Coordinates in waypoint units. Holds no terrain. |
| `<TH>.3DT` | 3.1–3.9 KB | **Partly known.** Terrain/object placements (below). |
| `<TH>.3DG` | 1810 B (identical size in all four) | **New lead (2026-10-15).** Looks like a grid of tile IDs (section 3.2). |
| `<TH>.3D3` | 23–27 KB | Unexplored. Probably 3D shape data (models for buildings, mountains and so on). |

### 3.1 `.3DT` layout (checked against all four theaters)

```
0x000  12 B header      31 31 20 00 20 00 20 00 20 00 20 00   (identical in all four)
0x00C  160 x u16        counts; their sum == number of records (CE 451, NC 362, LB 373, PG 350)
0x14C  N x 8 B records  s16 dx, s16 dy, u16 z, u16 type      (file length - 0x14C is an exact multiple of 8)
```

- **Offsets are small and local.** dx and dy fall within about ±2048 (±1792 in NC, LB and PG). These can't be world coordinates; they are offsets inside a cell or tile.
- **The z field is almost always 0.** The exceptions are a handful of 32, 64, 72 and 128 values. Possibly a height or a flag.
- **Types differ completely by theater:**

  | Theater | Most common types |
  |---|---|
  | CE | 16, 14, 26, 15, 42, 41, 44 |
  | NC | 53, 52, 9, 24, 4, 55, 3 |
  | LB | 60, 61, 62, 37 |
  | PG | 11, 10, 24, 22, 40 |

  The earlier guess that the mountain types are "14, 15, 16, 41, 42, 44" came from CE only and probably doesn't carry over. Plausibly the first one or two big types per theater are terrain (NC 53/52, LB 60–62).
- **The 160 counts:** grouped in "32-wide bands" (5 × 32?). They're either one list per tile type (32 tile types × 5?) or per world cell.
- **Unsolved:** the formula from (count index, dx, dy) to world coordinates.

### 3.2 `.3DG` (new lead, not yet decoded)

- It starts `32 32 00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d ...`. That's 0x32 0x32, then what looks like a 0…31 identity table.
- Every byte in the file is 0–31, so there are exactly 32 distinct values.
- Mostly 0 (sea or empty?): NC 1466 of 1810 bytes, CE 1196.
- **Neighbouring cells repeat a lot.** About 75% of bytes in NC (about 60% in CE) equal the one beside them, whatever row width is tried. That's what a map of tile IDs looks like; random data would match far less often.
- **Hypothesis:** `.3DG` is the world grid, where each cell holds a tile ID 0–31. `.3DT` holds, for each tile ID (32 × 5 = 160 lists, perhaps five levels of detail or five variants), the objects inside that tile at (dx, dy) offsets. `.3D3` holds the shapes.
- **Unknowns:** the grid dimensions, where the data starts, and the cell size in km.
  - The North Cape world is about 0–30,000 waypoint units (about 470 km) on each axis.
  - A ±2048 offset in waypoint units would be ±32 km, which suggests cells about 64 km wide. If the offsets are in position units (2048 per km), they're ±1 km.
  - Either scale gives a small grid (8–40 cells per side), so a 1810-byte file can't be one plain square grid of bytes. It may hold several layers or a run-length encoding.

## 4. In-memory evidence (flight program, old physical addresses + `f19_state.find_offset`)

- **Nearby-object list near old address 0x42B2C:** entries of 7 words (dx, dy, 0, id, a, b, key). Each object keeps a fixed (a, b) pair, each in the range −7 to 7. It's probably the objects currently near the player, relative to the player. The world placement was never solved from it alone.
  - (a, b) in −7…7 could be a tile/cell offset, with dx and dy as the offset inside the cell. That fits the `.3DT` local offsets.
- **Player position:** u32 X/Y at old addresses 0x451B8 and 0x451BE, at 2048 per km. Heading is at 0x4024A and altitude at 0x40250.
- **The memory logs** (`logs/memlog/<start>/snap_*.bin` plus `windows.json`) cover 0x3C000–0x48000 plus offset, about every 1 s, alongside the nav log's positions. That's thousands of (player position, object list) pairs per flight. It's the data needed to solve the formula with least squares instead of guessing.
- **Ghidra:** `~/Documents/F-19 Stealth Fighter/f19_full.rep` and `analysis/ghidra_project/` (EGAME and START analysed; `analysis/ghidra_scripts/decompile_candidates.py`). The code that opens `.3DG`/`.3DT` and fills the object list will state the formula exactly.

## 5. What has been tried

1. **Briefing-map relief as terrain:** wrong. It's art; all six crashes are outside the relief cells plus the 2 km margin.
2. **Guessing a world formula for `.3DT` records directly:** no consistent fit. Expected, if the records are per-tile templates placed by `.3DG`.
3. **A few in-flight peak anchors:** not enough clean ones to fit.
4. **Stopgap now in use:** `logs/theater/<TH>_obstacles.json` holds a 6 km keep-out disc per crash site. `plan_mission.py` passes them to `route_planner.py` as blocked cells. That only covers places we've already hit.

## 6. Plan

Each step has a check, so a wrong turn shows up quickly.

### Step 1: decode `.3DG` statically (about 1 hour; files only)
- Try headers of 2 or 34 bytes and widths giving 1776 or 1808 bytes of body. Render each candidate as an image, coloured by value, next to the briefing map.
- **Check:** the right layout looks like the coastline. Value 0 should cover the sea; NC's sea is large and north and east.
- If it isn't one byte per cell, test for run-length encoding: pairs of (count, value) whose counts sum to a square.

### Step 2: link `.3DT` to `.3DG`
- Split the 160 counts into lists and check whether 32 lists line up with the 32 tile IDs, for example by matching how often each tile ID occurs against list sizes.
- Place each tile's objects at cell origin + (dx, dy) under both unit guesses (waypoint units and position units).
- **Check:** do placed objects of the big types (NC 52/53) fall where the crashes are?

### Step 3: use the crash memory logs as ground truth (no flying needed)
- For each of the three North Cape crashes, read the object-list entries (around old address 0x42B2C) in the last 5–10 snapshots before impact. Record id, (a, b), (dx, dy) and key.
- The object closest to the player at impact is the mountain. Its id/type gives the mountain type codes, and its (a, b) and (dx, dy) give one exact link between object-list values and the world position.
- Across all snapshots in a flight, regress world position against player position + f(a, b, dx, dy) to solve the formula.
- **Check:** one formula has to put the same object in the same world spot from every snapshot, whatever the plane's position.

### Step 4: Ghidra cross-check (if steps 1–3 stall)
- In EGAME, find where the `.3DG`/`.3DT` file names are referenced (string search) and follow the read buffer to the routine that fills the object list. Decompile it.
- **Check:** the decompiled maths reproduces step 3's fit.

### Step 5: mountain heights (optional, after positions)
- Look in `.3D3` for the shapes of the mountain types (vertex z extents), or test by flying high over a known mountain and stepping down slowly.
- Heights let the planner tell "go around" from "fly over at X ft".

### Step 6: put it into the tools
- Extend `analysis/parse_theater.py` (or add `analysis/terrain_3d.py`) to write `logs/theater/<TH>_mountains.json` with world positions, types and footprint radii.
- `route_planner.py` blocks those footprints plus a margin, alongside the crash discs. `nav.py`'s terrain look-ahead uses the same data.
- **Validate before trusting it:**
  - All six crash sites must fall inside a decoded mountain footprint.
  - Replan the North Cape and Central Europe missions we've flown and confirm that the old routes crossed decoded mountains.
- Once that holds, remove the crash-site discs.

### Optional live test (only if steps 1–4 leave ambiguity)
Fly straight, level and high, for example 3000 ft, over the North Cape crash cluster with `--memlog` running. Watch the object list as known mountains pass, with no risk of impact.

## 7. Files and tools

- **Game data:** `~/Documents/F-19 Stealth Fighter/MPS/F19/{CE,NC,LB,PG}.{3DT,3DG,3D3}`, `{CE,NC,LIBYA,GULF}.WLD`
- **Parsed theater data:** `logs/theater/<TH>.json` (from `analysis/parse_theater.py`)
- **Crash keep-outs:** `logs/theater/<TH>_obstacles.json`
- **Planner terrain:** `analysis/route_planner.py` (`MOUNTAIN`, `BLOCKED`, `obstacles.json`), `analysis/plan_mission.py`
- **Memory logs:** `logs/memlog/<start>/` (`windows.json`: offset and windows), matching `logs/nav/<start>.csv`
- **Live state:** `scripts/f19_state.py` (offset finder and addresses)
- **Ghidra:** `~/Documents/F-19 Stealth Fighter/f19_full.rep`, `analysis/ghidra_project/`, `analysis/ghidra_scripts/`
- **Background:** `FINDINGS.md` sections "Altitude vs mountains" and the radar-flight sections
