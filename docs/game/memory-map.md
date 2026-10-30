# Memory map

Where game state lives in memory: START's briefing block, EGAME's data segment, the world/entity model, the event list and the flight-log variables. Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Mission briefing block in OUR build — CONFIRMED live (2026-10-17)

Supersedes the [second-hand v435.04 layout](../../research/notes/findings_stale_sections.md#second-hand-layout-mission-briefing-block-f-19-v43504--offsets-do-not-match-our-build) below (its offsets are 0x10 off for the blocks, and
its "cs:4B02" table is really in the data segment). Confirmed on a live CE briefing:
Primary Mission 4 (photograph the SAM Radar at Tabor, ONC DA38), Secondary Mission 18 (destroy the
AirPlane at Mittenwalde, ONC DB03), Flight Plan "depart from Neuberg ... land at Gutersloh".
Also consistent with the 2026-10-09 snapshot `logs/tmp/briefing_mem.bin` (missions 13/14,
Ramstein -> Rhein-Main). `scripts/read_mission.py` decodes it (output e.g.
`logs/missions/tabor_mittenwalde_briefing.json`).

- **Where:** START.EXE is EXEPACKed, so file offsets are useless. In memory its data segment
  (DGROUP) = START's PSP + 0x1010 paragraphs. Find the PSP through the DOS MCB named `START`.
  Check: `MS Run-Time Library` at ds:0008. This session: PSP 0x2D1A, DS 0x3D2A (phys 0x3D2A0),
  the same in the Oct 9 snapshot. The block exists only while START runs (briefing and menus
  before the flight). In flight EGAME has replaced START, so save the briefing-time json.
- **Blocks:** ds:B946 primary, ds:B958 secondary, 0x12 bytes each, all u16:

  | +0 | +2 | +4 | +6 | +8 | +A..+11 |
  |---|---|---|---|---|---|
  | type | target record index | base record index (primary: **takeoff**, secondary: **landing**) | code | mission number | ONC string, NUL padded |

  - type: 1 photograph, 2 destroy, 3 supply drop (code 0x3A), 4 landing (code 0x2F), 5-8 unknown
    (only missions 47-55, all against AirBase-category targets).
  - code = table code + (extra << 8) when extra > 0 (Mittenwalde: 0x0600 = mission 18's extra 6).
- **Record index i** = 16-byte record at world-copy +0x08 + 16*i (START's own copy: ds:B38E + 16*i).
  So index 1 and 2 are the generated slots at +0x18 and +0x28, and index 3+ are the .WLD place records
  (CE: index 12 = record 9 = Tabor SAM site, 72 = Neuberg, 69 = Gutersloh). **A target can be a place
  record, so the +0x18/+0x28 slots are not the targets.** In the Tabor mission +0x18 still held an
  unnamed Mobile SAM leftover. That is why the slot order "failed" twice: it was never the role order.
- **Object type of a record:** `names[(class & 0x7f) - 1]` in the theater name list
  (294 SAM Radar, 286 Tank Farm, 340 AirPlane, 332 Mobile SAM, 277 AirBase, 318 Buildings, 309 Patrol
  Boat, 324 OTH Radar, 348/349 Special Object 1/2).
- **Mission table:** ds:4B0C, 56 entries x 12 B, indexed by mission number:
  `i16 theater_mask, level_mask, type, category, code, extra`. The generator (START code ~0x7A46)
  accepts mission n for target record t only if bit (pilot+0x38) of theater_mask and bit (pilot+0x3A)
  of level_mask are set and `ds:C162[class(t) & 0x7f] == category`. Then it writes type, mission
  number and code into the block. Dumped to `logs/theater/missions.json`
  (`read_mission.py --missions FILE`).
- **Pilot settings:** far pointer at ds:991C -> the active pilot's roster record (name at +2).
  +0x38 = theater (3 in CE; menu order would make Libya 0, Gulf 1, North Cape 2, unconfirmed),
  +0x3A = level (0 on this clandestine / cold-war briefing).
- **ds:C162 comes from the .WLD:** a 100-byte category table, one byte per name id, sits right before the
  256-byte territory grid (CE.WLD 0x756). So target categories are per theater (LPAR Radar is category 4
  in NC, OTH Radar is 4 in CE).
- **BRIEFING.TXT** is MicroProse LZW: byte 0 = max code width (11), codes LSB-first from 9 bits, first
  free code 257, dictionary reset when full. It holds 56 NUL-separated texts, and text n = mission n.
  `analysis/mission_catalog.py` decodes it and writes **[MISSIONS.md](missions.md)** (every mission, its text and
  possible theaters). Theater bit order: 0 Libya, 1 Gulf, 2 North Cape, 3 CE (checked against the texts).

- nav.py's "route belongs to this mission" check now compares the `slots` (still in memory in flight)
  rather than the targets.

## Critical caveat: these are segment offsets, not physical addresses

`ds:B936` is relative to the game's data segment register at runtime, not a
fixed file offset. Our capture pipeline (`memdump_loop.py`,
`tag_capture.py`) uses DOSBox-X's `MEMDUMPBIN <seg>:0 <len>` and writes
**physical/linear** memory — segment `3000` in a capture corresponds to file
offset `0x30000`, per `capture_and_log`'s `base_offset` field. To translate:

```
physical_offset = (DS_at_runtime * 0x10) + 0xB936   (primary block start)
```

`DS_at_runtime` is not fixed — it depends on DOS environment size, loaded
drivers, and how DOSBox-X's build differs from the environment the original
researcher used. It must be read from the live debugger (breaking in with
Option+F12 shows the register panel, including `DS`) or inferred by pattern
search ([next section](../../research/notes/findings_stale_sections.md#fingerprint-search--the-versionsegment-independent-way-to-verify-this)). Do not assume `0xB936` is a usable file offset as-is.

## World & dynamic-entity model — confirmed via f15se2-re decompilation (2026-08-05)

**Hypothesis being tested** (from a session where in-flight action→memory
mapping was going nowhere): F-19 models a live, continuously-simulated
world — other planes, radar/SAM sites, missiles in flight, objectives —
that update on their own schedule independent of player input, plus
possibly wind affecting the flight model. If true, a simple "diff the state
before vs. after one action" capture (what `tag_capture.py` /
`scripted_test_run.py` do) would pick up thousands of bytes of unrelated
background churn alongside the handful of bytes the action actually
touched, making the signal nearly impossible to isolate by eye. Went back
to the f15se2-re source tree (cloned locally, `src/*.c`, `src/*.h`,
`map/egame.map`) to check this against real, compiler-accurate ground
truth instead of guessing further.

**Confirmed: yes, there's a full live entity model, actively ticked every
frame, independent of player input.** From `src/struct.h` / `src/egdata.h`
(struct definitions + their `extern` globals in `egame.exe`'s data
segment):

| Table | Struct (size) | Count | What it is |
|---|---|---|---|
| `g_simObjects` | `SimObject` (36B) | 20 | Other aircraft/wingmen currently near the player — `worldX`/`worldY` **integrated every frame**, plus heading/pitch/bank, speed, an AI countdown `timer`, `weaponType`, and a `damage` accumulator. This is the direct "planes that move and act" entity. |
| `g_planeTable` | `GroundTargetTable` → `MapTarget[74]` (16B each) | 74 | The full mission's ground+air target roster: position, active flag, air/ground `flags`, `alertLevel` (SAM radar-alert accumulator, clamps to 255), `threatTimer` (site-fire countdown). This is the radar/missile-site table. |
| `g_projectiles` | `Projectile` (24B) | 12 | In-flight guided weapons — slots 0-7 threat-fired, 8-11 player-fired — `ttl` counts down every frame it's airborne. |
| `g_particles` | `Particle` (8B) | 8 | Short-lived effect markers (smoke trail, hit spark) — `alt` rises `+0x0A` and `posY` drifts every single frame while active. |
| `g_missionTick`, `g_frameTimingAccum`, `g_timerTickByte[0x21A]` | scalars / 538-byte array | — | A literal free-running tick counter driven off the timer ISR (`egtick.c`'s `egAdvanceFrameTick()`), incremented on **every timer interrupt** — tens of times per second, with zero relationship to player input. |

`src/egthreat.c`'s `updateThreatSites()` (called every frame) is the
concrete mechanism behind the "entities act on their own schedule" part of
the hypothesis — it loops every active site in `g_planeTable` each frame,
decrements `threatTimer`, and **autonomously calls `fireGroundThreat()`**
purely once that timer expires, with no player action involved:

```c
for (siteIdx = 0; siteIdx < g_targetEntityCount; siteIdx++) {
    if (g_planeTable.planes[siteIdx].active != 0 && ...) {
        g_planeTable.planes[siteIdx].threatTimer -= 1;
        if (g_planeTable.planes[siteIdx].threatTimer <= 0) {
            g_planeTable.planes[siteIdx].threatTimer = /* recompute from alertLevel */ ...;
        }
        if (g_planeTable.planes[siteIdx].threatTimer == 4 && g_scopeSweepTimer < 0) {
            fireGroundThreat(siteIdx);
        }
    }
}
```

**Not confirmed: wind.** Searched the whole source tree for `wind`
case-insensitively — zero hits outside of "window". No weather/wind term
anywhere near the flight-model code (`egflight.c`, which does have
`g_thrust`/`g_liftForce` globals for the actual physics). Since F-19
(1988) is the earlier, simpler title in this family and F-15 SE2 (1989)
is confirmed not to model it, treat the wind part of the hypothesis as
**not supported** — the "your thrust/vector don't fully determine airspeed"
effect, if it exists at all in F-19, is more likely just normal flight-model
inertia/momentum than an environmental wind term.

**Architecture confirmation, not just data**: `src/comm.h`'s `GameComm`
struct (the 5120-byte segment the separate overlay EXEs communicate
through — `gfxOvlAddr`, `sndOvlAddr`, `miscOvlAddr`, `weaponType[4]`,
`weaponCount[4]`, etc.) matches F-19's own executable split
(`START.EXE`/`EGAME.EXE`/`END.EXE` plus the hand-written `CGRAPHIC`/
`EGRAPHIC`/`ISOUND`/etc. overlay EXEs already identified in the toolchain
section above) field-for-field in spirit — same publisher, same era,
same overlay-linker architecture, not just "similar approach."

**Independent triangulation of the mission-briefing block already found**:
`src/struct.h`'s `Target` struct is **byte-identical in shape** to the
18-byte (`0x12`) mission block already confirmed at F-19's `ds:B936` (see
above) — `missionType`(2) / `targetIdx`(2) / `baseIdx`(2) / `missionCode`(2)
/ `missionNum`(2) match our 5 known fields exactly, **plus two fields we
hadn't mapped yet**: `coord[6]` (a grid-reference string like `"A3B2"`) and
`distance` (int16), filling out the remaining 8 bytes of the struct. In
F-19 physical terms, once `DS` is known from a live capture (per the
existing translation formula), that's:
- Primary block: `coord` at `ds:B940`-`B945`, `distance` at `ds:B946`-`B947`
- Secondary block: `coord` at `ds:B952`-`B957`, `distance` at `ds:B958`-`B959`

This is a free addition to the existing confirmed block — no new search
needed, just read those offsets on the next briefing-screen capture.
`src/struct.h`'s `MissionTableEntry` (12 bytes: `theaterMask`/`tensionMask`/
`missionType`/`objectFlag`/`missionCode`/`special`) likewise matches the
already-found 12-byte lookup table at `cs:4B02` — a second independent
confirmation from a completely different research effort.

**What this means for the capture methodology** — re-analyzed the existing
`dumps/test_cases.jsonl` scripted-run captures against this now-confirmed
model (see `analysis/candidate_addresses.md`'s new entry for the full byte
counts): the diff-size pattern matches this entity model almost exactly.
Every capture pair taken *before any action has been performed since
mission start* shows a near-zero diff (0-15 bytes) over the same ~6-7s gap
that, once a single action has occurred, produces thousands of bytes of
diff in *every subsequent pair regardless of whether that pair contains an
action or is itself an "idle control."* That's the signature of
`g_particles`/`g_projectiles`/`g_simObjects`/`g_planeTable` switching from
dormant (nothing fired, no threats engaged yet) to actively animating every
frame the instant the first weapon/control input happens — not something
tied to any specific subsequent action. **The before/after diff approach
cannot isolate action-specific bytes once any entity table is "live" — this
is now a confirmed structural property of the engine, not a tooling
mistake.** Phase 3's write-breakpoint mechanism (already validated working
end-to-end — see `candidate_addresses.md`) is the correct tool going
forward, since it catches the exact writing instruction regardless of how
much unrelated per-frame churn is happening elsewhere in memory.

## What's still unmapped (no prior art found for any of this)

Everything needed for actually *flying* a mission — position, heading,
altitude, airspeed, fuel, weapons remaining, radar/target-lock state,
damage — has no published source. This is genuinely new territory; the
mission-briefing block above only gets us pre-flight context (what the
mission is), not in-flight telemetry (how to fly it). The differential /
tagged-capture approach already built in `tag_capture.py` (freeze state,
take one deliberate action, diff against previous capture) is the same core
technique the Hacking-F117A author used via Cheat Engine — continue using
it methodically across flight-control actions (pitch/roll/throttle inputs,
not just weapons) to build out the telemetry map, since no shortcut exists
for that part.

## Player state addresses — CONFIRMED (2026-10-07)
- Method: `scripts/mem_sampler.py` snapshotted phys 0x30000-0x60000 twice a
  second (pipelined automation-socket PEEKs, small batches) while
  `scripts/hud_logger.py` read HUD altitude/airspeed from window captures
  (`scripts/hud_ocr.py`); the user hand-flew climbs, descents, two 90° turns
  and a throttle change. `analysis/mem_correlate.py` ranked every 16-bit word
  by correlation with the HUD values; heading was found by matching words to
  the direction of travel derived from the position words.
- Results (physical addresses; live reader in `scripts/f19_state.py`):
  | addr | type | meaning | evidence |
  |---|---|---|---|
  | 0x40250 | u16 | altitude, ft (copies 0x40252, 0x451C6) | r=1.0000, slope 1.001 vs HUD |
  | 0x440CA | u16 | airspeed, knots | r=1.0000, slope 1.001 vs HUD |
  | 0x4024A | u16 | heading, 65536 = 360° | circular agreement 0.999 with track |
  | 0x451B8 | u32 | X position, east + | steady ramp on straight legs |
  | 0x451BE | u32 | Y position, south + | steady ramp on straight legs |
  | 0x43B00.. | 16 × 16B | ring buffer of past states (+0 alt, +2 heading, +0xD time) | |
- Position scale: ~2.0 units per real second per knot (not yet calibrated to km).
- Verified live against the HUD the same day (alt/speed exact, heading matches track).
- Key-injection caveats learned the same session: presses shorter than one game
  frame (~0.3 s at low level) can be missed — use >= 0.5 s; and a PEEK flood
  (thousands per drain) makes DOWN/UP land in the same frame.
- Map calibration (same day, logs/calib/calibration.json): **~2308 memory
  units per km** (rms 0.24 km fit). Runway at Ramstein = memory (108546, 745224).
  Change Waypoint panel: only WAYPOINT 1's DIST is live distance from the plane;
  WAYPOINT 2-4 DIST are leg lengths between waypoints. Per the manual, WP1 is the
  midpoint of takeoff->primary.

## Waypoint table + coordinate system — CONFIRMED (2026-10-09)
- Waypoint table at **phys 0x403CA**: 4 entries of (u16 X, u16 Y), stride 4
  (WP1 0x403CA, WP2 0x403CE, WP3 0x403D2, WP4 0x403D6). Found by recording
  memory while the user moved WP2/WP3 right x4 / up x4 in the Change Waypoint
  screen: X stepped +1024 and Y -1024 per press.
- Table units = player position (0x451B8/0x451BE u32) >> 5.
- **Game distance metric is the octagonal approximation**
  `d = max(|dx|,|dy|) + min(|dx|,|dy|)/2`, with **64 table units = 1 km**
  (=> 2048 position units per km). Reproduces every panel DIST/leg value
  (114, 19->115, 130, 314 km). The earlier 2308 units/km Euclidean fit was ~13% off for this reason.
- Selected waypoint index (0-3): phys 0x4041C.
- Change Waypoint panel: WAYPOINT 1 DIST is live from the plane; WAYPOINT 2-4 DIST are leg lengths.
- Move step in the waypoint editor: 1024 table units = 16 km.

## Mission event log, trackcam and target lock — live (2026-10-18)

- **Flight-event list** (what END.EXE scores) is readable in flight: 6-byte records `[status, target record, u16 time,
  cell x, cell y]` at old phys ~0x54DDA this load (in low memory, not EGAME's DS): type 8 takeoff at t=32, then type 9
  position samples every 0x20 time units (64 s), until an empty slot. **The Tabor photo is logged: `8A 0C E0 05` = type 10
  (photographed) | 0x80 primary flag, target record 12, t=1504.** So "Target photographed." = a new type-10 record
  for our target: the photo controller can close the bay on it. Locate the list by its pattern (08 00 20 00 then 09 00 40 00);
  u16 at ~0x54106 read 80 = the record count (one sample; unconfirmed).
- **Trackcam mode = u8 old phys 0x455A6**, F2 cycles it 2 -> 0 -> 1 -> 2. With the user's order nav -> air-to-ground ->
  air-to-air and the user's air-to-ground as the starting 2: **1 nav, 2 air-to-ground, 0 air-to-air** (inferred, check on the HUD).
- **CORRECTION (2026-10-30):** the N and B descriptions below are observations; the decoded rules are in "Trackcam target selection: N, B, \"/\"" further down (N snaps to the 3D object under the nose line, not the nearest on-screen one; B steps outward by distance inside a +-45 deg cone).
- **N (designate new target)** locked nothing in any mode while Mittenwalde was 25-40 deg off the nose at 33-40 km. With the
  target off screen the lock read 0 for an object 11 km dead ahead: **record 0 = the scratch slot for a generic 3D object**
  (the "other items" the user mentions). `scripts/acquire_target.py RECORD` keeps the trackcam in air-to-ground and presses N
  only while the target is within 20 deg of the nose and 40 km, logging every lock (logs/probe/acquire_*.csv).
- "/" (trackcam ahead) has no automation key name: it needs a D2 scancode (0x35), which clears the controller buffer and so
  can eat the pilot's own key releases while a human flies. Not used yet.
- **B (select target) is the reliable lock** (2026-10-18, trackcam air-to-ground, nose on Mittenwalde): it steps through the
  target table **nearest first and wraps**: 32 (Mittenwalde AirBase, class 277, 18.0 km), **2 (the AirPlane slot, the briefing
  target, 17.8 km)**, 14, 5, 31, 13, 45, 4 (87 km), 0, 32, 2 ... Three presses re-locked record 2 at 13.4 km. N instead
  picks the nearest on-screen object (record 0 clutter at ~12 km). Record 2 = 05 00 | 2D28 | 4AB8 ... class 0x15C;
  record 32 = 05 00 | 2D40 | 4AC0 | ... class 0x115: two records 0.4 km apart, so check the record, not the position.
  The Attack controller now sets the trackcam (F2) and presses B until locked == the briefing record (up to 40).
  The photo close now also checks the event log for the type-10 record (`f19_state.read_events`; not yet run live:
  the port stopped answering right after, emulator presumably paused).
  **CORRECTION (2026-10-29):** the type-10 record is in the list ~1 s after the camera frame (checked in the memlog snapshots of
  the Kolobrzeg and Katowice flights: `8A 01 <t>` / `<xx> <yy>`, record 1; time field 0 until the next sample). The list sits at
  **ds:0x89BA** in those loads (it is inside the memlog `egame_data` window); `addresses.EVENTS` ds:0x8E46 / `N_EVENTS` ds:0x632E
  read zeros there, so `read_event_list` is stale. The live miss: `find_events` cached "not found" for the whole session when
  the first read came before the list held its takeoff + first sample records; fixed by retrying a miss every 5 s. Not yet
  confirmed live (replays pop the cache on every seek, so they never showed it).
- **Trackcam must be ON for B (2026-10-18):** with the left CRT on the waypoint screen, B did nothing (the lock sat on 140,
  not a target record). `/` (trackcam ahead, D2 scancode 0x35) brought the camera up, locked on 32 (Mittenwalde AirBase),
  and one B press locked record 2. Attack prep order: F2 to air-to-ground, `/`, then B until locked == record.
- **First automatic Maverick attempt did NOT fire** (nav 1792298024..035): locked 2, Maverick station, 9.1 km, nose +23.5,
  Enter pressed, but the Maverick count stayed 2 and no kill event. The fire most likely needs the HUD missile lock,
  which the pop-up loses (the "firing doesn't need the HUD lock" assumption in [notes/targeting_for_planner.md](../design/targeting_for_planner.md) was
  unchecked); HUD lock byte ds:6342 read 0 throughout (address unverified). The pop overshot (+23.5, 560 -> 965 ft at
  the shot, ~2300 ft afterwards) because throttle presses blocked the loop: throttle is now frozen while the nose is held.
  Flaps were found down afterwards (flags 0x4003); source unknown.
- **Pop-up dropped (user, 2026-10-18):** "the maverick doesn't even need a pop up, it can fire just fine at 450 feet", and
  the pop-up climbed to ~2300 ft ("a christmas tree on radar"). Attack now fires LEVEL at cruise altitude with the altitude
  and heading holds on: inside 20 km and 10 deg, bay open -> Enter (2 s apart, max 3, until the Maverick count drops) -> bay closed.
  The sink table in [notes/targeting_for_planner.md](../design/targeting_for_planner.md) section 2 overstates the sink at 450 ft (or the game's floor check differs):
  revisit. The user lowers flaps at low altitude; the takeoff controller raises them (revisit).
- **First automatic Maverick launch (2026-10-18, nav 1792298647):** level shot at 18.9 km, 480 ft, pitch +2.9, 1 deg off
  the nose; the Maverick count dropped 2 -> 1; the bay closed again; the altitude held 454-480 ft throughout. **HUD lock
  ds:6342 (old 0x41EB2) read 1 at the shot (0 before): confirmed.** Prep at 43 km: F2/"/"/B locked record 2 at once.
  Result: event **type 4, target 2, no role flag**, at t=2592, and record 2 not destroyed. Type 4 isn't scored by END
  (kills are 1/2/3): probably "hit, Ineffective" (75 % kill chance, matrix 3) or a miss. To confirm against the HUD
  message. One Maverick left. Next: retry on type 4 if a Maverick is left (re-run in from 20 km).
- **The user saw "Ineffective"** for that shot. So event **type 4 = hit, Ineffective** (target record in byte 1). Per the user
  the HUD hexagon turns red at the in-range distance, and 20 km is beyond it: decoded in-range = D < 32 R units = **16 km** for
  the Maverick (lock at 56 R = 28 km). The attack window is now 6-15.5 km (phases.Attack, planner MAV_WINDOW_KM, launch point
  14 km). The decoded hit check (v > random(4)) has no range term, so 18.9 km may not have caused this result, but the shot
  now follows the game's cue.
- **What decides the hit message (EGAME disassembly re-read 2026-10-18):** missile direct hit on a ground target, 0x735f:
  `r = random(4)` (0xd34b), `v = matrix(weapon, target)` (0x91ae); **v > r -> destroyGroundTarget 0x77c3 ("<target> destroyed
  by <weapon>"), else "Ineffective"** (ds:562F). No range or speed term. Maverick vs AirPlane v = 3 -> 25 % Ineffective;
  v >= 4 never. "<x> misses " (ds:55D6, 0x70a5) is only in the **bomb** branch (spec 0x1c-0x1e, not the supply canister);
  a missile that reaches the ground prints "<x> ground impact" (ds:5618, 0x7265). So the 18.9 km shot **hit** and lost the
  25 % roll: range didn't cause it. The 6-15.5 km window stays only to follow the game's in-range cue.
- **Where the 20 km came from:** not the file parsing (that gave lock 28 km / in-range 16 km, correctly). The 16-20 km window
  in [notes/targeting_for_planner.md](../design/targeting_for_planner.md) was built from the remark "the user fires at ~20 km" and the pop-up model (fire early,
  turn away). The wrong part that came from parsing was the release_geometry.py sink model, which said a level shot below
  200 ft sinks 300-650 ft into the ground and so produced the pop-up.
- **CORRECTION: event type 4 = weapon launched at a target, not "Ineffective"** (2026-10-18). Two Maverick launches at 18.8 and
  16.9 km logged `(4, 0, 2, 3187)` and `(4, 0, 2, 3200)` within seconds of each launch; the earlier `(4, 0, 2, 2592)` was also the
  launch. Neither "Ineffective" nor misses get an event. The retry built on the wrong reading fired the 2nd Maverick 3 s after
  the 1st. Both missiles: no kill (record 2 alive). The retry now waits FLIGHT_S (placeholder 60 s, unmeasured) for a kill
  event / the destroyed bit before re-shooting. Still needed: the outcome of each shot (HUD message) and the Maverick's
  flight time and reach (ttl/speed from the spawn code), for the user's "fire as far as possible with >= 80 % to connect".
- **CORRECTION: range DOES matter, through the missile's life** (EGAME fire routine 0x7b48-0x7bc2, read 2026-10-18 after the user
  pushed back: 3/3 shots outside the red hexagon "Ineffective", never inside). At launch: slot +0x0E ttl =
  `R*32*fs / ((spec+0xA >> 6) + 1) + 6` frames (999 if that is <= 6), i.e. the time to fly R*32 units, **the HUD in-range distance**,
  at top speed. Maverick R 32, max speed 12: cue 16 km; it starts at kt/76 (~97-99 % of a full-speed flight) and lofts ~7 deg, so
  it reaches ~14.5-15.5 km. Beyond that it expires before the target. The hit routine (0x735f: matrix vs random(4)) has
  no range term; the range effect is all here. (How expiry ends up printing "Ineffective" isn't traced.) Other specs: HARM R 20 /
  vmax 18 (10 km cue), AIM-9 R 17 / vmax 31, AIM-120 R 32 / vmax 37 (air: lock at R km, so their cues differ).
  **Rule: Maverick at 14 km** (nav MAV_MAX_KM, planner launch 14 / window 6-14.5). A 2nd shot is decided when the first
  missile's slot (spec 0x1B, ttl > 0) empties, not by a fixed wait. My earlier "range doesn't change the kill" was wrong:
  I had read only the hit routine.
- **First automatic Maverick KILL (2026-10-18, nav 1792299892):** prep at 45 km (F2 / "/" / B -> record 2), level shot at
  **13.1 km**, 467 ft, pitch +3.1, off 0, HUD lock 1; **record 2 destroyed 13 s after the launch** (the missile slot watch
  saw it). The 14 km rule holds. Flight ~1 km/s, so firing at 14 km gives the result with ~11 km to go: room for a
  second shot when a hit is "Ineffective" (v = 3: 25 %).
- **First automatic landing attempt, Gutersloh (2026-10-18, nav log of auto_land1, --land-now from 33 km, 2.5 km off the axis):**
  the new intercept (aim at the centreline ahead, cut <= 35 deg) reached the axis, overshot ~0.5 km and settled to ~14 units off.
  **Vertical worked:** down the 60 ft/km glide to **36 ft at the aim point**, sink 3-7 ft/s at the bottom, speed 155 kt.
  **Lateral failed:** 14 units (~220 m) east of the centreline at the aim point (box +-5): the go-around (> 8 units below
  80 ft) fired correctly, full power, handed back. The localizer asked for its 20 deg cap (200), but the turn-rate roll
  controller held ~174-175 for seconds: too slow and dead-banded for a +-78 m target. Needed: a cross-track controller
  with rate damping (xt and d(xt)/dt), aligned within ~3 units by 4 km out, finer roll presses on final, and the
  "wings level below 60 ft" rule only once aligned.
- **Centreline controller rebuilt (2026-10-18):** the first try S-turned (heading 197 -> 164 -> 200, x 3412 -> 3362 -> 3407 units):
  1.5 deg/unit made the lateral loop (~7 s) faster than the press-based heading hold (~10 s + roll reversal). A model with the
  real roll presses (0.28 deg/s per s held, 0.6-2.5 s holds, 0.25 deg/s deadband, 2.5 s settle, 2 s rate window) reproduces
  +-40 units at 1.5 and settles within +-1-3 units at 0.4-0.6 from 2.5 km off or 20 deg misaligned. Now: heading offset =
  -0.5 x xt, cap 35 deg beyond 4 km and 10 deg inside, one law from intercept to touchdown; wings-level-below-60-ft only when
  |xt| <= 5. `--land-now` starts the landing phase at once. Not flown yet.
- **Second automatic landing attempt (2026-10-18, auto_land4):** lateral **fixed**: from 2.1 km west at 28 km, overshoot to +65 units,
  then **+3 .. 0 units from 4 km in** (box +-5). Vertical failed: lowering the flaps at 7 km and slowing ballooned the plane to 544 ft
  at ~6 km (glide cap ~320); the hold's 8 ft/s limit couldn't catch it up; 120 ft at the aim point, 86 ft over the base record ->
  go-around (correct: later touchdown would overrun the box, 150 kt needs ~0.84 km). Now: flaps + final speed at 11 km; sink
  limit 12 ft/s above 100 ft, 6 to 40 ft, 2.5 in the flare.
- **Reply desync after killing a client:** `kill -9` of nav mid-batch left PEEK replies queued; the next client read shifted
  replies and `find_offset` failed ("data segment not found") although the game was in flight. Stop nav with SIGINT.
- **Dives after every restart (2026-10-18):** with no nav running, the user's restarts kept flying into the ground. Most likely a
  key left down in the emulator by a nav killed with SIGTERM/-9 before the clean-shutdown handler (DOSBox keeps a held key across
  save-state loads). Fix: released every autopilot key (arrows, Space, Enter, B, N, shifts, and break scancodes for - = /).
  nav now: `settle()` at start, which releases keys and stays hands off until >= 6 s of sane, consistent state (re-locating the
  data segment on garbage); SIGTERM/SIGINT exit through the key release. Reads during a load are garbage (BIOS tick went
  backwards between two scans 1 s apart while the user was restarting).
- **Correction to the stuck-key theory: the keyboard stick keeps its deflection.** Bank persisting after release was measured
  long ago; pitch does the same. The ~40 km save state was made while nav was flying (stick slightly deflected by the
  altitude hold's taps), so every load dived with nobody pressing anything; releasing keys can't fix that. **Keypad 5 =
  "Centered"** (QRC keyboard stick). nav now taps kp5 when its settle period first reads sane and in _release (every exit
  and hand-back). Not yet verified live: the game wasn't in flight when this was found.
- **ROOT CAUSE of the dives after every load (2026-10-18): the save state's keyboard buffer.** Slot 6
  (~/Library/Preferences/save/6.sav, a zip: Memory, Keyboard, Joystick, CPU, ...) holds a healthy plane: 488 ft, 298 kt,
  heading 242.2, **pitch +2.2**, roll -1.8, (3819, 16826), throttle 60 %. Its `Keyboard` entry is the 8042 buffer full of
  nav's arrow traffic: E0 48 (up = nose down) / E0 C8, E0 50 / E0 D0, E0 4D / E0 4B. Replayed on load, an up-arrow make
  without its break leaves the game holding nose-down: the first frame read after a load was pitch -8.8 at 304 ft, heading
  242.4, ground 2 s later. key_up() can't clear it (DOSBox doesn't think the key is down) and keypad 5 only centres the stick.
  Fix: `Dosbox.release_arrows()` sends the break codes straight to the guest (D2: E0 C8 / E0 D0 / E0 CB / E0 CD + keypad
  C8 D0 CB CD); nav's settle runs it at the first sane read; scripts/release_arrows.py by hand; watch_load.py --release.
  Lesson: a save made while nav is flying carries its key traffic. Save with nav stopped, or always release after loading.
- **ACTUAL root cause of the dives (supersedes the stick and buffer theories above): DOSBox's own auto-repeat is saved.**
  DOSBox-X serialises `keyb` (src/hardware/keyboard.cpp SerializeKeyboard): buffer u16[96], used, pos (8-byte Bitu each),
  **repeat.key (u32 KBD_KEYS enum, byte 208 of the "Keyboard" zip entry)**, repeat.wait / pause / rate, command, p60data, ...
  Slot 6 was saved while nav held the up arrow: **repeat.key = KBD_up** (pause 500, rate 33). After every load DOSBox
  auto-repeats the up arrow (nose down) every 33 ms until it gets an UP for that key. Releases sent before a load are
  undone by it; D2 break codes don't touch DOSBox's repeat; keypad 5 doesn't either. Fix for the file: **slot 7 = slot 6
  with repeat.key = KBD_NONE** (all 26 zip entries copied, only that u32 changed). At run time: key_up("up") AFTER the
  load cancels it (nav's settle does this when nav starts after a load). Slot check: 1 = KBD_right (rolls right after
  load), 2-5 clean. Never save a state while nav (or anything) holds a key.
- **Post-load fix verified (2026-10-18):** slot 7 still dived (the game itself held the up arrow: DOSBox's repeat was only part
  of it). `scripts/fix_after_load.py` polls the position at the known offset 20x/s and, 1.0 s after the load, sent key_up +
  the D2 arrow breaks + keypad 5: the plane then held 476-488 ft for 30 s. Arm it BEFORE loading a state saved while nav flew.
- **Third landing try (nav 1792339790):** nav took control 32 km out heading 242 (away from the axis); turning ~100 deg at the
  approach turn rate put it ~12 km west, and a 35 deg cut only reached 3 km off at the runway: go-around (correct). Another
  flap balloon to 535 ft. Added a **capture gate** to Landing: established = |xt| < 1 km and within 30 deg of the runway
  heading; cut up to 60 deg beyond 12 km; not established inside 16 km -> fly back out to the approach fix (20 km on the axis)
  and come in again; gear, flaps and glide only once established.
- **First end-to-end --auto run (2026-10-18, nav 1792340681.., Neuberg -> Tabor -> Mittenwalde -> Gutersloh):** takeoff clean
  (360 ft in ~16 s, then 464 ft); cruise held 464 ft for ~1 h; **Tabor photographed with one frame** at 3.13 km (event log
  confirmed, bay closed); **Mittenwalde destroyed** by a level Maverick at 12.8 km, 12 s after launch (crept to 533 ft at
  the shot). **Landing failed:** after the attack the speed hold was off (`thr.target = None`), so the plane flew on at
  370-390 kt; the route's turn onto the axis (planned for ~190 kt at 0.8 deg/s) went wide, it reached the approach fix
  ~4.7 km west, and the capture gate (which returned before the speed/gear/flaps code) never slowed it and flip-flopped
  between intercept and reposition: circling at 390 kt. Fixes: cruise speed restored after every attack; capture gate
  removed (landing = slow down, gear, flaps, glide, centreline K 0.5, brake: the route delivers the plane onto the axis).
- **FIRST AUTOMATIC LANDING, Gutersloh (2026-10-18, nav full_run2, from 80 km out after both attacks):** at cruise speed (198 kt)
  the route's turn put the plane on heading 193, 2.3 km east of the axis at the approach fix; the centreline law (K 0.5) held
  -2 units (box +-5) from ~4 km in, heading 180.3, wings level; glide 60 ft/km, sink 2-5 ft/s through the last 50 ft; floated at
  ~7 ft for ~2 s; **touchdown 26 units past the base record at 149 kt**; idle + brakes; **stopped at y+59 units, 5 units
  (~80 m) short of the box end**, in the box (ds:5534 = 1), gear down, flaps, brakes, throttle 0. The margin was thin.
  Tuning now: aim 48 units short (was 32), final 135 kt (the speed hold added power on the glide: 127 -> 151 kt), flare from
  25 ft at 4 ft/s, idle below 15 ft. Not yet flown.
- Attack phases no longer change speed (user: it upsets nav and adds exposure for nothing); cruise speed is restored after
  every attack and set when nav starts in the air. Event type 12 with flag 0x40 = kill of the secondary (record 2).
- **Confirmed by the game: "Safe Landing" and the mission debrief** (user, 2026-10-18). So within one save-to-debrief chain
  the autopilot has now flown every phase: takeoff, cruise, photo (primary, one frame), Maverick kill (secondary), landing.
- **Debrief: "Mission end: Good Landing", rating 1,042** (CE, Tabor photo + Mittenwalde kill, Veteran, Cold War). The END.EXE
  formula reproduces it exactly only as base **232** = 140 (primary photo) + 35 (secondary with the primary done) + **57 enemy
  position samples**, x 13/8 (CE) x 9/8 (strike) x 14/8 (Veteran) x **10/8 (Realistic landing)** x **9/8 (fuel estimate
  7000-8999)**. The planner predicted base 230-231 (55-56 enemy samples) and 515-1150: the sample model is within 1-2 samples.
  For this pilot: landing = Realistic, fuel factor 9/8 (set these in plan.json settings to narrow the estimate).
- **Second new-mission run (2026-10-18, Neuberg -> Budejovice Maverick -> Carlsbad photo -> Ramstein; nav full_run3): shot down
  during the primary attack** (user: several radars plus an IL-76 visual ID). Takeoff and cruise fine (457 ft for ~1 h). The
  attack: prep locked at ~26 km; the Maverick fired at ~13.4 km, -0 deg off; then the phase stuck in "close" for 100+ s:
  key 8 tapped every 3 s but the bay flag never read shut (bay open = EMV 37-38). The altitude hold went unstable from the
  launch leg (~25 km, before the shot): 81 pitch presses in ~3 min vs 27 in the hour before, 195-640 ft, speed 164-211 kt,
  19 throttle presses (each a D2 that wipes pending keys). Both causes unconfirmed. The nav csv now also logs the attack
  state, the flags word and the throttle each cycle.
- **Correction from the debrief (user, 2026-10-18):** 12:38:16 hit by SA-14; 12:39:36 IL-76 visual ID; 12:40:00 Maverick fired;
  12:42:12 ID'd by the Carlsbad SAM; 12:42:48 by the Tabor SAM; 12:43:06 SA-14 hit; 12:43:36 SA-11 hit; 12:44:32 SA-10 hit;
  12:45:24 crashed. SAM sites that see you on radar without an ID sometimes fire SA-14s (low accuracy, can get lucky). The
  first hit came BEFORE the shot: the stuck-open bay and the rough flying were **battle damage** (bay doors, avionics/controls),
  not autopilot bugs. Lesson: don't get noticed. Next missions cruise at 300 ft (plan.json overrides cruise_ft) to cut radar
  detection.
- **Katowice run at 300 ft (2026-10-18, nav full_run4): crashed 33 s after takeoff (my side).** The takeoff's fixed thresholds
  (level from 220 ft, hand over above 300 ft, full power until then) handed over at ~570 ft and ~355 kt; the altitude hold
  (tuned at ~190 kt) descended at up to -40 ft/s and its floor guard (below 200 ft) fired at 197 ft, too late. Fixes: takeoff
  level-off = min(220, cruise - 100), handover = min(300, cruise - 30), cruise power once the gear is up; descents below 700 ft
  at most 8 ft/s; pitch-tap gap scaled by airspeed/200 kt; predictive floor guard (alt + 5 s x vs < floor), off when a phase
  sets its own floor (landing). Not yet flown.

## Trackcam target selection: N, B, "/" — EGAME disassembly + memlog replay (2026-10-30)

How it was established: EGAME disassembly (key switch 0xd3a6, selection 0xab84-0xacbc, N 0xb858-0xb8f6, distance 0xc954/0xc994/0xd11b,
scene snap 0xc892) and the decoded B rule replayed against memlog snapshots of three flights (1793126949, 1792724414, 1793382037).
None of it depends on the difficulty (the only variable is night, ds:4EF4 = 0 in all three); every flight here was day. Untested live: a B probe.

- **State:** ds:5526 = locked ground target (record index; -1 = none; **bit 0x80 set = a search is pending**, so -1 is itself "searching"),
  ds:9A36 = trackcam mode (F2: 0 -> 1 -> 2 -> 0; 2 = air-to-ground), ds:9E8C = view offset (0 ahead, 0x4000/0x8000/0xC000 = the other
  views), ds:9740 = screen id (0x13 / 0x19 = trackcam screens), ds:9500 = record count searched (75, all world records; ds:9C78 is only the
  "enemy" subset), ds:8B22 = 4-frame retry cooldown, ds:4EF4 = night.
- **"/" (0x352f)** = view ahead (9E8C = 0), **clears the lock to -1** (unless the screen is 0x19), switches to screen 0x13. Because -1 means
  "searching", the next frame auto-acquires the **nearest** record in the cone. It is the "start the cycle over" key.
- **B (0x3062)** only sets bit 0x80 on ds:5526 (and 5524). The pick runs in the per-frame trackcam code at 0xab84. It does nothing unless:
  ds:9740 is 0x13 or 0x19, mode != 1, ds:94E0 bit 0x80 clear, cooldown 8B22 = 0. Then, over records 0..9499:
  **candidate = not destroyed (+8 bit 0x80), octagonal dist >= dist(current lock), within +-45 deg (0x2000) of heading + view, dist < 100 << (6 - night)
  units (6400 = 100 km day, 50 km night), and in modes 0/1 only enemy-side (+8 bit 0x01); mode 2 takes friendlies too. It takes the NEAREST
  candidate.** If the current lock is -1 or itself outside the cone the floor is 0 (nearest of all). If there is none, the lock goes to -1 and the
  next frame re-acquires the nearest, so B **wraps** (no wasted press).
  So B only ever steps **outward** by distance through the **+-45 deg cone**; it never leaves the cone, and the set it walks is every record in the
  cone, friendlies and the scratch record 0 (stale X,Y) included. 75 records means a lap can exceed Attack.B_MAX (40).
- **N (0x316e)** is not "nearest on screen": it toggles ds:684C bit 0x100, which makes the frame code project the nose line to the ground
  (alt / tan(-pitch) ahead, 640 units if the nose is level or up) and call 0xc892, which snaps to the 3D scene object at that point (0x11080, undecoded)
  and takes the record **only if that object's X,Y equal a record's X,Y exactly**; otherwise it fills scratch record 0 and locks 0. A radar site is
  several scene objects, so the nose point near a site usually snaps to a site object, not the neighbouring target: that is why N "locks the SAM".
- **Replay checks (day):** 1793126949: at prep only record 44 is inside the cone and the wanted record 2 (11.6 km) is 97-126 deg off the nose, so
  B reads 44 -> -1 -> 44 for 40 presses. 1792724414: wanted 19 at 65-150 deg; the in-cone set 2, 6, 34, 16, 38 is exactly the observed cycle
  (6, 34, 16, 38, 2, ...). 1793382037 attack 1: ds:9740 = 0 for the whole run (the "/" never took), lock -1 for 40 presses.
  Nav logs without a memlog fit the same rule: 51 <-> 4 and "47 x24" are a cone holding one or two records.
- **Not explained:** 1793382037 attack 2 (secondary, record 1): the "/" took (9740 = 0x13), lock = 7 (the SA-14 site, 528 units), and
  the wanted record 1 (848 units, 1 deg off the nose, flags 0, in the cone) was never selected in 40 presses. The decoded rule predicts 7 -> 1.
  Only difference found vs the cycling flight: ds:4EEA (weapon station) = 1 (camera) instead of 0. Needs a live probe (peek 5526 / 4EEA around one B).


## Flight-log variables: fuel, damage, clock, IDs, threats — EGAME (2026-10-18)

Decoded from the EGAME disassembly for the flight logger. ds offsets; phys = 0x3BB70 + ds + find_offset (as f19_state).
"memlog" = verified offline against logs/memlog snapshots (file index = ds - 0x490); the rest is static only.

**Clock and frames**
- **ds:6630 u16 game clock**, 1 tick = 2 game s (1800 per hour, same units as the briefing). Ticks once every
  ds:4EFC frames; ds:5520 = frame counter. ds:4EFC (4 by default) is re-derived from the measured frame rate (0x4599) and
  halved by time acceleration (0xd590, ds:553A). A debug key adds/subtracts 0x3C (2 min) at 0xdc82. memlog: event records
  every 32 ticks carry exactly this clock.

**Fuel** (memlog: starts ~10000, -64 lb per 32 ticks at 50 % throttle = the formula)
- **ds:4ED0 u16 fuel, lb** (main tanks). Set to 10000 at mission start (0x3cce). Burn (0x268c): every 2 x ds:4EFC frames
  (= every 2 ticks = 4 game s) while throttle ds:46F2 != 0: `fuel -= 2 + thr*thr/1000 (+ ds:95BE if damage bit 0x20)`,
  integer division. So 50 % = 4 lb / 4 s (60 lb/game min), 100 % = 12 lb / 4 s (180 lb/min). Fuel 0 -> clamped to 0,
  spool ds:85F8 = 0 (engines out). Altitude, flaps and weight act only through speed: see ["Fuel burn and cruise speed"](fuel.md#fuel-burn-and-cruise-speed--egame-2026-10-18).
- Extra Fuel: firing station with weapon -2 ("Fuel transfer", 0x7ae6) adds 0x76C = **1900 lb** (string "1900lbs").
- Fuel weight feeds the flight model at 0x2834 ((100 - fuel/512) term).
- **Bingo lamp** (0x8476, lamp 9): yellow when dist(player, home base)/16 > fuel, i.e. fuel buys 16 units (0.25 km) per lb.
- END gets the crash code at commData+0x28 via 0x4acd(code): 1 ground (0x2159, 0x42db), 2 hill (0x43e9), 5 runway
  (0x2d9c), 0 = ended normally (0x254c, 0x439a).

**Damage**
- **ds:4ECE u16 damage bits**, **ds:95BE u16 damage count**. Hit routine 0x7a0b: unless ds:684C bit 0x1000 (no-damage
  mode), repeat opp+1 times: `bits |= 1 << random(8); count++`; then sound + flag ds:663A. Called for every enemy missile /
  gun / blast hit on the player. Count starts at **2** (0x3cd4; memlog: 2 throughout undamaged flights).
- Bit meanings (from what each bit disables):
  - 0x01: incoming-missile warning lamp (lamp 1) no longer lights (0x6d6c). Probably the threat warning receiver.
  - 0x02: **engine**: throttle capped at 100 - 4 x count (0x2624).
  - 0x04: **flight controls**: random 0..4 x count added to the control input each frame (0x241e). The "rough flying".
  - 0x08: **autopilot / INS**: game autopilot ds:4EEE forced off (0xdd05), waypoint steering cue skipped (0x835b).
  - 0x10: **bay doors jammed OPEN**: ds:684C bit 0x04 forced on (0xdcf9) -> EMV +24 for the rest of the flight.
  - 0x20: **fuel tank leak**: + count lb per burn step (0x26a4) and in the consumption gauge (0x99ee).
  - 0x40: **countermeasures**: radar + IR jammers forced off (ds:684C &= ~0x30, 0xdb4f); decoy launch fails (0x46ca).
  - 0x80: rolled but tested nowhere (the S display draws bits 0-6 only).
- count > 8 (or Realistic landing) doubles the stall nose-drop rate (0x2b47). No Crashes forgives ground contact only with
  count <= 4 and fuel left (0x4280).
- At END: commData+0x34 = bits, +0x36 = count (0x4b0c).
- R key in no-damage (training) mode (0x4a96): bits = count = 0, every station 9 rounds, guns 1000, fuel 5000.

**Stores**
- Countermeasures ds:4ED2 + 2k, k = 1 flares (ds:4ED4), 2 chaff (ds:4ED6), 3 decoys (ds:4ED8). Release routine 0x468f(k):
  "Stores exhausted" at 0. memlog: 12 flares, 18 chaff, 3 decoys at start.
- Loadout ds:4EDA 4 x (weapon, count) = 0x40A4A, station ds:4EEA, gun rounds ds:4EEC (already in f19_state).
- ds:684C bits beyond gear/flaps/bay/brakes: **0x10 radar jammer (ECM, key '4', lamp 5)**, **0x20 IR jammer (key '3')**,
  0x100 boresight, 0x1000 no-damage mode, 0x4000 >> n = objective n done.
- **ds:4EEE game autopilot altitude** (A key: max(alt, 500); 0 = off).

**EMV** (ds:551C, = the known 0x4108C), rebuilt each frame at 0x4607 — exact formula in the [radar section below](radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18)
(memlog: 4067/4067 snapshots within +-1).

**Radar detection** (per radar site, 0x51a1). Target table ds:80AA, 16 B/record, count ds:9C78:
+0 name, +2 X, +4 Y, +6 radar type (0 = none; spec table ds:4876, 14 B: +0 range, +2 power, +4 bit 0 doppler),
+8 flags, **+0x0A alert level (0-255)**, +0x0E class.
- Return 0x5519 = f(range - dist, power + 2 x opp, terrain clutter = cell-map bits 0x0C); doppler radars scale by aspect to
  the site and by speed. No return when the altitude difference >> 10 exceeds the distance (directly overhead).
- **Seen** when return + EMV > 100 (> 50 = pink threat bar; ds:552C = threat colour 4/0xC/0xE). Each such **pulse**:
  `alert += ((ds:4EF2 + opp) x 16 + 16) >> (ECM on ? 1 : 0)`, cap 255. First return: flag 0x04, HUD "<site> detected".
  (Corrected 2026-10-18: per pulse, not per frame; full model in ["Radar sites: pulses, duty cycle, detection"](radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18) below.)
- alert > 0x7F -> **network alert 0x566b**: your position, heading and altitude go into ds:8B24/8B2E/8B34/663C, and every
  site's alert is raised to at least (opp + ds:4EF2) x 16 - 16. memlog 1792199511: all 21 sites jumped to 16 together at
  clock 1921 (opp 2, ds:4EF2 0).
- alert >= 0xFA and not yet -> **Radar ID**: flag 0x1000, **ds:9672 R count**++, HUD "<site> - Radar ID", event type 7
  (target = site record). Destroying that site later does R-- (0x77f1).
- ds:9674 counts "alerted" moments (any site > 0xC0 with the conditions at 0x5398, or a plane > 0xC0).
- ds:4EF2 = gameData+0x3A (probably the conflict level); ds:4EF0 = opp.

**Visual ID** (aircraft, 0x6650..). Sim objects ds:8854, 0x24 B each (20): +0 X, +2 Y, +4 alt, +0x14 type (name =
ds:49B0 + 32 x type: memlog decodes An-72, MIG-29, IL-76 ...), +0x16 flags, **+0x20 alert**.
- Same return + EMV > 100 test; alert += ((ds:4EF2 + opp) x 16 + 32) >> ECM; > 0xC0 -> flag 0x4000 + network alert;
  then if not yet and distance < 8 >> ds:4EF4 (**ds:4EF4 = night**: halves the range): flag 0x800, **ds:966E V count**++,
  HUD "Visual ID - <type>", event type 6 (target = aircraft type). Shooting that plane down does V-- (0x76e2).
- The S page's "Sightings V n R n" shows ds:966E / ds:9672.

**SA-14 cells** (0x56ec): every 16 ticks (32 game s), **in daylight only**, if the player's 32 km cell has bit **0x10** in
the territory grid (ds:85FC, the same 256 bytes as analysis/territory.py) and enemy slot (clock >> 4) & 7 is free, an
**SA-14** (spec 0x21) is spawned at a random point in the cell; if it starts more than (4 - opp) x 256 units away it flies,
"SA-14 fired" is shown, and the network alert fires. CE has 8 such cells, down the front line (column 4-7, rows 5-13).
**The Tabor crash flight (nav 1792346075) entered SA-14 cell (7,13) 227 s before the crash; the debrief's first SA-14 hit
was ~214 s before it.** The "lucky SA-14s" are these cells, not radar sites. Planner: avoid 0x10 cells by day.

- **SA-14 cells are walls for the planner at Elite (2026-10-27).** The Elite Carlsbad plan (`ce_carlsbad_1027_1842`) flew
  7.4 min through cell (4, 6) because its secondary, a photo of Wittenberg at (9800, 12984), sits in that cell and the
  router only priced cells (30 samples per minute). Now (`settings.sa14` auto/hard/soft, profile `sa14_hard_from_opp` = 3):
  live cells are walls in the route search and in `validate`; a secondary whose photo/overfly attack has to enter a cell is
  skipped (a Maverick target in a cell is not: the launch point is 14 km out); the primary in a cell, or no route that
  avoids them, makes the plan INVALID. Chance that one spawn check launches = P(random point of the cell is more than
  (4 - opp) x 256 units from us) = 1 - pi (4-opp)^2 256^2 / 2048^2 (centre of the cell): green ~21 %, regular ~56 %,
  veteran ~80 %, elite ~95 % (derived from the spawn rule above, not measured; the 57 Veteran SA-14s fit it). Checks
  come every 32 game s, so 7 min in a cell is ~13 checks: a launch (and the network alert) is certain at Elite and likely at
  Veteran. `alertsim` now models it (`!! PREDICTED SA-14 launch` in the report). Untested live: the skip/wall rules have
  only been run offline. CE's cells (3,10) and (3,11) plug the only pass between the western mountains and Carlsbad's
  pocket, so Carlsbad-type targets in the east pocket cannot be struck and left without entering a cell at Elite.
  `scripts/mission_finder.py --no-sa14 / --sa14-primary / --sa14-secondary yes|no` filters missions by it.

**Event list** (memlog-confirmed layout): ds:8E46, 6 B records `[u16 clock, u8 x>>7, u8 y>>7, u8 type|flags, u8 target]`,
count ds:632E (max 255). Recorder 0x4b40(type, target). Types:
2 enemy plane hit/destroyed by player (target = class), 3 air kill (+0x80 if objective), 4 weapon launch (station),
5 **player hit** (target = enemy weapon spec; name = ds:4C10 + 0x12 x spec), 6 **visual ID** (aircraft type),
7 **radar ID** (site record), 8 takeoff / end, 9 64 s position sample, 10 photo (+0x80 primary / +0x40 secondary),
11 objective done for supply/landing (+0x80 / +0x40), 12 / 2 ground-target kill (+0x80 / +0x40 objectives).
Score: types 6 / 7 cost -20 / -10 base in Cold War (END), so every ID is worth 20 / 10 enemy samples.
(f19_state.read_events starts 4 bytes into the first record, so its fields are (type, target, clock of the next record); phases.py only uses type and target.)

**HUD messages**: built in ds:65C6 then shown by 0x9192 (templates at ds:5540-5C00). ds:65C6 is also scratch for number
formatting (a snapshot read back '0'), so polling it is not a message log; the event list and counters are the record.

## Writing memory: the automation socket's POKE (2026-10-25)

- The DOSBox-X fork (`~/projects/dosbox-x`, branch `f19-automation`, commit 0ec314d6c) has
  `POKE <hex phys addr> <hex bytes>` (1-64 bytes, e.g. `POKE 9F000 DEADBEEF`). It always replies with exactly one
  line: `POKE-REPLY OK <addr %05X> <n>` or `POKE-REPLY ERR <reason>`.
- PEEKs and POKEs share one queue, so replies come back in request order. Writes run on the emulation thread through
  `mem_writeb_checked`. Only guest RAM is accepted: below `MEM_TotalPages*4K` and outside A0000-FFFFF. Every write is
  logged.
- Client: `f19.emulator` `poke(phys, data)`, which reads the bytes back to check. Tested on a bare DOS prompt
  (`dosbox-x -tests` 59/59, plus live round trips on port 6123/6124). **Not yet used on F-19.**
- Only the mission selector writes memory (START's rand seed, see
  [mission-generation](mission-generation.md#selecting-a-mission-by-seed-2026-10-25)); flight code never does.

