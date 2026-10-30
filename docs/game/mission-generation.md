# Mission generation

How START generates a mission (targets, bases, mission numbers, deadlines, fuel estimate, rerolls) and the .WLD mission data; the mission catalog is in [missions.md](missions.md). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Generator rules, fuel estimate and rerolls (from the briefing-block section)

- **Generator rules** (START code 0x7738-0x801A, disassembled from the live image):
  1. Primary target: if pilot +0x3C bit 0 is set (= *strike*), it's picked near a random enemy-territory
     position (0x866C, slot 1), and records with flags & 0x801 == 1 (enemy airbases) are rejected.
     If bit 0 is clear (*air-to-air*), it's a random record with flags & 0xD01 == 1, i.e. an enemy airbase,
     so the mission is always an intercept (types 5-8). The secondary is always picked by position (slot 2).
  2. Bases: for each target, the nearest suitable friendly base (flags & 0x500 and & 0x201), with jitter.
  3. Mission number: for each block, a random table entry matching theater, level and category; the
     secondary's must differ from the primary's.
  4. Training (+0x3C bit 1, ds:98C6 == 0): fixed targets; mission 5 for strike, 55 for air-to-air.
  5. Swap the blocks when the primary's base metric is smaller than the secondary's. Then reject (retry)
     if the secondary has code bit 1 or type 5-8 (so those are **primary-only**), if primary type 4 and
     ds:44E0 == 0, or if code bit 0 / bit 3 is paired with a photograph mission or with each other.
  6. Deadline ds:CA4A: code bit 1 -> 450 + dist(takeoff, primary)/16. Types 5/6 -> computed from the target
     plane's route and speed; types 7/8 set none. Stored as CA4A >> 4 at ds:B956 (spare bytes after the
     primary's ONC string). 0 = no time limit. Clock units (formatter
     0x8E9C): 1800 per hour = 2 game seconds each; minutes are rounded down to 5; the hour tens digit is
     1 + ds:44E4 (44E4 = night: forced 1 for landing missions, 0 with a photograph mission). Start time
     = (random & 0xF) << 8, i.e. 10:00-12:00 (day) or 20:00-22:00 (night). Strings built at 0x8620:
     ds:4DB6 start, 4DBC start + CA4A (the "before HH:MM hours" deadline), 4DC2 deadline + 15 min
     (airstrip-lighting window end). dist is in 64/km units, so code-bit-1 deadline = start + 15 min
     + 8 s per km. Live check 2026-10-17: mission 51 (type 7) had B956 = 0 and the start and deadline both
     10:00, with the An-72 "leaving from Tabor airbase at 10:00"; type 7 is a race from mission start.
- **Fuel estimate = mission length** (2026-10-24, START cs:7A04 / 845B): the generator writes 16 x L to
  commData+0x2e (far ptr ds:D066), L = takeoff->primary + primary->secondary + secondary->landing km. The briefing
  ("FUEL ESTIMATE") and arming ("Fuel Est.") print that word, and END scores it (x9/8 from 7000, x10/8 from 9000,
  x11/8 from 11500 lb). So the buckets are L < 438 / 438-562 / 563-718 / >= 719 km. Checked: Fuel Est. 9,152 = 572 km.
  The accept rule is L <= 740 + attempts, and the attempt counter goes to 999, so L can pass 900 km (CE).
- **Rerolling** (2026-10-24): options menu "Select New Mission" (item 0) sets ds:CA6A = 1 (cs:CE1C) and returns to
  the duty roster; pilot -> Mission Assignment -> "Accept assignment" -> the briefing entry (cs:B5F0) clears four flags,
  calls the generator (76C8), clears CA6A, then **draws more random numbers** at cs:B61C-B72E (random(3) records get
  flagged, some of them zeroed). Nothing else between rerolls touches rand. Running 76C8 + B61C-B72E in unicorn on a
  memory image reproduced all 34 live rerolls that day in order. A fresh briefing room opens with Mission Targets
  selected (targets flashing): arrows queue until Selector, Selector twice returns with the cursor on Exit Briefing
  Rm. Exit leads to arming, "Arming Complete" to the options menu (cursor on Begin Mission). Esc on the roster erases
  the pilot. `scripts/mission_finder.py` does all of this.
- **Turbo for rerolls** (2026-10-24): DOSBox-X CPU menu "Turbo (fast-forward)" (mapper_speedlock2, `ticksLocked`)
  runs emulated time 29x real time at 3000 cycles/ms (BIOS tick 0040:006C: 535/s vs 18.2). It can be clicked and
  read (AXMenuItemMarkChar) through System Events without focusing the window, and keys from the automation socket
  don't cancel it ("stop turbo on key" only sees SDL key events). Under turbo a 0.1 s key hold is ~3 s of game time,
  past the typematic delay, so taps must be bare press/release. A reroll takes 2.0 s, down from 22 s.

## Mission data (targets + radar types) — CONFIRMED (2026-10-09)
- **CE.WLD** (theater file) = 2937 B: header, then 16-byte place records from
  0x38: u16 name_id (1-based into the NUL-separated name list; one entry is
  empty), X, Y (waypoint-table units, 64/km), kind, 3 x u16 flags, u16 class
  (294 SAM site, 309 ship slot, 324 OTH radar, 277 search radar, 318 city,
  286 tank farm). Parser: `python -m f19.gamedata.wld` -> logs/theater/CE.json.
  All 16 named SAM sites match briefing positions (within the ~5 km bias of my
  briefing-map fit).
- The game's **resident launcher** (low memory; chains SU/START/missions/EGAME/END)
  keeps a working copy of the .WLD, and the mission generator writes the mission
  into it, so it is readable during the briefing and in flight:
  - copy +0x18 / +0x28: primary / secondary target records (name, X, Y, ..., class)
    (CORRECTION 2026-10-17: these are only generated target slots, not the targets in role order;
    the real primary/secondary + bases are in START's briefing block, see ["Mission briefing block in OUR build"](memory-map.md#mission-briefing-block-in-our-build--confirmed-live-2026-10-17))
  - SAM/ship/OTH records: kind = radar code, 0 = eliminated / no ship
  - codes: 1 SA-2 P37, 2 SA-5 P43, 3 SA-8B P39, 4 SA-10 D140, 5 SA-11 P62,
    6 SA-12 D108, 7 SA-13 P23, 8 SA-N-4 D50, 9 SA-N-5 P28, 11 SA-N-7 P62, 21 OTH D156
- `scripts/read_mission.py` decodes it; on mission 3's snapshot it reproduces
  the briefing exactly (2 targets, 21/21 radars incl. the eliminated Lukow and 4 boats).

## Selecting a mission by seed (2026-10-25)

- `f19.gamedata.mission_gen` (from mission_finder) emulates START cs:76C8 + cs:B61C-B72E under unicorn. `find_seed`
  searches seeds offline for a wanted mission shape.
- **Offline check:** `verify_image` steps the MS C rand state backwards from a saved START image and found the exact
  starting state for both saved images: North Cape 2413 draws back, Central Europe 1495. Both the mission and the
  image's final rand state are reproduced. The new Generator matches the old mission_finder one.
- **Live sequence, not run yet:** POKE the u32 seed at ds:7A50, press Enter ×3 for Select New Mission (it assumes the
  options menu with "Select New Mission" highlighted), then read the mission back (ds:CA6A and the briefing block) and
  compare it with the prediction. This replaces the screenshot-checked reroll loop.
- **Open:** START's menu and cursor variables aren't known yet; there are TODOs in `memory/addresses.py` and
  `mission_gen.py`. Until they're found, menu moves are fixed key sequences checked only by memory reads.

