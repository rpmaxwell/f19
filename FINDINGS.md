# F-19 reverse engineering: research findings & test hypotheses

Context for any agent picking up this project: we're mapping F-19 Stealth
Fighter's live memory to gameplay state so we can eventually drive/train an
agent to fly missions. This file summarizes prior art found on the public
web and turns it into concrete, testable hypotheses against the memdumps in
`dumps/` and `MEMDUMP.BIN`. Nobody has published a full F-19 RAM map; what
follows is the closest confirmed prior art plus a verification protocol.

## Prior art

**Primary source — [debugcom/Hacking-F117A](https://github.com/debugcom/Hacking-F117A)**
(4-part writeup, 2022-2024). Investigates mission generation in F-117A
Nighthawk 2.0 *and* F-19 v435.04 specifically. Confirms the two games share
an (almost) identical in-memory mission-parameter layout. Method used:
Cheat Engine differential scanning to find candidate addresses, `debug.com`
to trace call stacks back to the writing function, IDA Freeware 5.0 (one of
the only tools left with real 16-bit real-mode DOS support) to read the
disassembly and find the mission lookup table.

**Secondary source — [neuviemeporte/f15se2-re](https://github.com/neuviemeporte/f15se2-re)**
(site: [neuviemeporte.github.io/f15-se2](https://neuviemeporte.github.io/f15-se2/))
This is much more directly useful than first assessed: it's not just a
tooling-constraints analog, it's an actual **reconstructed C source tree**
for F-15 Strike Eagle II's `egame.exe` (same filename as F-19's flight-sim
executable) with typed struct definitions for the live in-mission world
state, recovered via the same real-mode disassembly grind we're doing. Its
own comments explicitly cite **F-117A** reverse engineering as the source
for some struct shapes ("same engine"), which chains directly to this
project's primary source above — i.e. F-19 / F-117A / F-15 SE2 form one
triangulated family, not three independent unknowns. See the dedicated
section below ("World & dynamic-entity model") for what this source
confirms about the hypothesis that in-flight diffs are dominated by
autonomous world simulation, not player input.

No DataCrystal-style RAM map, no leaked source, no existing RL/agent project
against this game were found.

## Toolchain identification (confirmed locally, 2026-08-04)

Ran `strings` across every `.EXE`/`.COM` in the actual game install
(`~/Documents/F-19 Stealth Fighter/MPS/F19/`), rather than relying on the
web-sourced F-15 SE2 finding alone:

- `DS.EXE`, `EGAME.EXE`, `END.EXE`, `START.EXE`, and `SU.EXE` all contain:
  `MS Run-Time Library - Copyright (c) 1988, Microsoft Corp`, plus the
  standard MS C runtime error table (`R6000` stack overflow, `R6001`,
  `R6002` floating point not loaded, `R6003` integer divide by 0, `R6009`
  not enough space for environment), plus overlay-manager strings
  (`Overlay load failed for some reason`, `MS-DOS lied to us about how much
  memory was available`) — a large-memory-model build linked with the MS C
  overlay linker.
- F-19 shipped October 1988; Microsoft C 5.1 was Microsoft's current
  release at exactly that point, and the F-15 Strike Eagle II
  decompilation project (1989, same-era MicroProse) identified the same
  runtime-library generation via this exact copyright string. This is
  strong circumstantial confirmation — not yet a byte-certified version
  match — that F-19 was built with **Microsoft C 5.1**, large model, same
  as F-15 SE2.
- **Direct local evidence of cross-title code sharing**: `ASOUND.EXE` sits
  next to `ASOUND_FROM_F15STRIKEEAGLE2.EXE` in the install directory —
  literal proof MicroProse reused compiled modules between F-19 and F-15
  Strike Eagle II, not just toolchain and team.
- **Important split for prioritization**: the graphics/sound utility EXEs
  (`CGRAPHIC`, `EGRAPHIC`, `MGRAPHIC`, `TGRAPHIC`, `ISOUND`, `TSOUND`,
  `BSOUND`, `ASOUND`, `MISC`, `SCENERY0`) carry only the generic
  MicroProse copyright string, **no** MS runtime signature — these are
  almost certainly hand-written assembly, not compiled C, so FLIRT
  signature stripping won't help there; they need direct disassembly
  either way. The 5 EXEs with the MS C signature are where the
  FLIRT-based shortcut actually pays off. Of those, `EGAME.EXE` (89KB, by
  far the largest) is the highest-value target — per the boot chain in
  `F19.COM` (`START.EXE` briefing → `EGAME.EXE` flight sim → `END.EXE`
  debrief), it's almost certainly where in-flight telemetry lives.

## Action plan (ordered, built on the MS C 5.1 finding)

1. Get IDA Freeware 5.0 (last version with real 16-bit DOS support) and MS
   C 5.1 FLIRT signatures. Load `EGAME.EXE` first — largest MS-C-linked
   module, most likely to hold the flight-sim main loop.
2. Apply the FLIRT signatures to strip recognized MS C runtime/library
   functions from the disassembly, leaving only MicroProse's game-specific
   code — should shrink the effective search space in `EGAME.EXE`
   substantially.
3. String-anchor: search `EGAME.EXE` (in-flight) and `START.EXE`
   (briefing) for literal ASCII HUD/UI labels, then walk xrefs backward
   from each string reference to find the code reading the corresponding
   live value. This is the direct route to telemetry addresses (position,
   heading, altitude, fuel, weapons) that no prior-art source covers.
4. Statically re-verify the Hacking-F117A mission-block fingerprint (two
   5-byte tuples 0x12 bytes apart — type/target/code/number, see below)
   against this actual `START.EXE` binary before running anything — confirm
   whether v435.04-era offsets still apply to this build without needing
   the emulator at all.
5. Binary-diff `ASOUND.EXE` vs `ASOUND_FROM_F15STRIKEEAGLE2.EXE`. Since one
   is confirmed sourced from F-15 SE2, the diff tells us exactly how much
   MicroProse modified shared modules for F-19 — near-identical calibrates
   how much we can trust the F-15 SE2 decompiled C source as an analog for
   other shared subsystems (physics, rendering, input); heavily patched
   means treat it as a weaker signal.
6. Once steps 1-5 surface address candidates, set DOSBox-X memory **write
   breakpoints** on them instead of the current before/after diff polling
   — gets the exact writing instruction and call stack in one break,
   replacing the statistical guessing `tag_capture.py` currently relies on.
7. Parse `*.WLD` (`LIBYA.WLD`, `GULF.WLD`, `NC.WLD`, `CE.WLD`) and
   `ROSTER.FIL` statically, no emulator needed, per the North Cape
   byte-fix precedent in Hacking-F117A Part 2 — cross-check the recovered
   struct shape against whatever `START.EXE`'s file-loading code (from
   step 1-3) reveals.

## Mission briefing block in OUR build — CONFIRMED live (2026-10-17)

Supersedes the second-hand v435.04 layout below (its offsets are 0x10 off for the blocks, and
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
  `analysis/mission_catalog.py` decodes it and writes **MISSIONS.md** (every mission, its text and
  possible theaters). Theater bit order: 0 Libya, 1 Gulf, 2 North Cape, 3 CE (checked against the texts).
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
- nav.py's "route belongs to this mission" check now compares the `slots` (still in memory in flight)
  rather than the targets.

## Second-hand layout: mission briefing block (F-19 v435.04) — offsets do NOT match our build

Segment-relative addresses (`ds:` offsets), as found via `debug.com` /
Cheat Engine against a *running* F-19 v435.04 process:

| Offset (ds:) | Field                  |
|--------------|-------------------------|
| `B936`       | Primary Mission Type    |
| `B938`       | Primary Mission Target  |
| `B93A`       | Takeoff Target          |
| `B93C`       | Primary Mission Code    |
| `B93E`       | Primary Mission Number  |
| `B948`       | Secondary Mission Type  |
| `B94A`       | Secondary Mission Target|
| `B94C`       | Landing Target          |
| `B94E`       | Secondary Mission Code  |
| `B950`       | Secondary Mission Number|

Structure: two 18-byte (`0x12`) blocks back-to-back, each laid out as
`[type, target, takeoff/landing, code, number]`. Backed by a 12-byte-wide
lookup table at `cs:4B02` indexed by mission number.

```
enum mission_type { photograph = 1, destroy = 2, supply_drop = 3, landing = 4 };
mission_code: 0x2F = landing, 0x3A = supply_drop  (other codes unconfirmed)
```

Secret-airstrip target IDs by theater (F-19 v435.04):

| Theater       | Target A | Target B |
|---------------|----------|----------|
| Libya         | `0x19`   | `0x1A`   |
| Persian Gulf  | `0x19`   | `0x1A`   |
| North Cape    | `0x1E`   | `0x1F`   |

Known North Cape caveat: the North Cape `.wld` file is missing a property
byte (offset `0x1F1`/`0x201`, should be `0x09` not `0x08`) that other
theaters have, which breaks objective-completion detection there. Not
relevant to flight telemetry, but worth knowing if a training episode in
that theater silently never reports mission success.

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
search (next section). Do not assume `0xB936` is a usable file offset as-is.

## Fingerprint search — the version/segment-independent way to verify this

Because `DS` is unknown a priori, the fastest way to confirm this block
exists in our captures is a **content-based search**, not an address
lookup. Look for two 5-byte tuples exactly `0x12` (18) bytes apart matching:

```
byte0 (type):    1-4
byte1 (target):  plausible small hex id (theater-dependent, roughly 0x00-0x20)
byte2 (takeoff/landing target): any
byte3 (code):    0x2F or 0x3A when byte0 is 4 or 3 respectively (weaker signal for 1/2)
byte4 (number):  0-50ish
```

repeated at `offset+0x12`. This pattern should appear (a) only while a
mission briefing is on screen, (b) not appear during in-flight or menu
states, and (c) sit at a *stable* offset relative to whatever `DS` resolves
to across multiple captures in the same session (same run = same load
address = same physical offset every time; a different session may shift
if DOS environment size etc. differs).

## What other agents should test

1. **Does the block exist at all in our build?** Take a memdump during an
   active mission-briefing screen (any theater), search for the fingerprint
   above across the full 640KB region in `memdumps.jsonl` or a fresh
   `memdump_loop.py` capture. If found, record the physical offset and back
   out `DS = (offset - 0xB936) / 0x10` — sanity check it's a plausible
   segment value (should land somewhere in the loaded-program range, likely
   between `0x1000`-`0x4000` given DOSBox-X's default DOS memory layout).
2. **Does it move between sessions?** Capture the same briefing state after
   a full dosbox-x restart. If the offset changes, `DS` is being
   re-assigned per launch (likely, since DOS relocates based on
   environment/driver load order) — record it per-session rather than
   hardcoding.
3. **Cross-check against `tag_capture.py` labels.** The existing tagged
   captures in `dumps/test_cases.jsonl` only cover segments `3000`/`4000`
   (physical `0x30000`-`0x4FFFF`) during in-flight action tests (firing
   ordnance, cycling weapons, bay doors) — none of those were captured
   during a briefing screen, so this block won't show up in the existing
   data. A new tagged capture specifically at the briefing screen is
   needed; consider adding a `"briefing"` tag pass across all 10 segments
   (not just 3000/4000) once, to locate which segment the block actually
   lives in for this build, then narrow future captures to that segment for
   speed.
4. **Validate the enum against observed UI.** Accept a landing mission
   (`type=4`), confirm `code=0x2F` in memory; accept a supply-drop
   (`type=3`), confirm `code=0x3A`. Photograph/destroy codes are
   unconfirmed by the source material — worth filling in since we'll need
   the general mission-type/target/code relationship for arbitrary (not
   just secret-airstrip) missions.

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

## Automation: repeatable flight trials via DOSBox-X save states (2026-08-06)

Problem this solves: every prior tagged-capture run (`scripted_test_run.py`,
manual `tag_capture.py` sessions) required a human to fly manually from a
cold boot through pilot select → mission params → loadout → takeoff before
any capture could start, and each run started from a *different* point in
the mission (whatever the human happened to do). That made it impossible to
tell "byte changed because of this specific action" from "byte changed
because of unrelated background world simulation" (see the diff-noise
section above) without a repeatable, identical starting point.

**Confirmed: DOSBox-X save states solve this**, and the mechanism needed
verifying rather than assuming — a full DOSBox-X source checkout happened
to be sitting in `~/.Trash/dosbox-x` (version close enough to the installed
2026.08.02 binary to trust), so this was checked against `src/misc/
savestates.cpp`, `src/dosbox.cpp`, and `src/hardware/vga_draw.cpp` directly
rather than guessed from web search results (which were inconclusive/
contradictory on default hotkeys):

- Save/load-state are mapper events (`savestate`/`loadstate`) bound to
  "host key" + S / host key + L. Host key defaults to F12 on macOS
  (`hostkeys[]` in `dosbox.cpp`) — not usable as a scripted AppleScript
  modifier (only command/option/control/shift are valid in `keystroke ...
  using {}`). Fixed by adding `hostkey = ctrlalt` to
  `~/Library/Preferences/f19-dosbox.conf`'s `[dosbox]` section, which makes
  the combo **Ctrl+Alt+S** (save) / **Ctrl+Alt+L** (load) — both driveable
  with standard modifiers. Confirmed independent of the debugger toggle
  (`memdump_loop.py`'s Option+F12): that's hardcoded to `MMOD2+F12` in
  `sdlmain.cpp`, not gated by the `hostkey` setting.
- Save slot N is always file `<N>.sav` in `-savedir` (`slotname << slot+1`
  in `savestates.cpp`). The active slot has stayed at 1 (`saves/1.sav`)
  in every session observed — confirmed why, see next point.
- **Autosave bug, confirmed from source**: `f19-dosbox.conf` has
  `autosave = 5 1-200`, but the autosave tick in `vga_draw.cpp` only
  accepts an end slot `<=100`. With 200 out of range, it silently falls
  back to always targeting the range's *start* slot (1) — every 5s, for
  the whole session, never advancing through 1-200 as the conf implies.
  It saves-and-restores the active-slot pointer around itself, so it
  doesn't fight manual Ctrl+Alt+S/L slot selection, but it does mean
  `saves/1.sav` is a shared scratch file autosave silently overwrites
  every 5s — not safe to treat as durable storage. (Not fixed — flagging
  only; the 1-200 range may be intentional headroom for a future DOSBox-X
  version, not worth changing without asking.)

**New scripts** (`scripts/savestate.py`, `start_flight.py`,
`trial_takeoff.py`, `trial_altitude.py`): `savestate.py` saves/loads named
checkpoints to `saves/checkpoint_<name>.sav` (copied out of/into the
autosave-contested slot-1 file immediately around the Ctrl+Alt+S/L
keypress, confirmed via polling `logs/f19-dosbox.log` for the "Saved."/
"Loaded." line rather than a blind sleep). `start_flight.py` launches/
attaches dosbox-x and loads a checkpoint — **menu navigation is still
deliberately not automated** (same call `scripted_test_run.py` already
made: too fragile to script blind); instead the human flies to the target
state once, saves a checkpoint, and every future run reloads it exactly.
`trial_takeoff.py` and `trial_altitude.py` are the two requested trials
(throttle/flaps/nose-up/gear-up sequence; climb-then-descend with periodic
sampling), each reloading its checkpoint on every repeat so N independent
samples of the identical action, from the identical starting state, can be
diffed against each other — not just a single noisy before/after pass.

**Not yet confirmed live**: the numpad-8/numpad-2 pitch-control key codes
used for "nose up"/"nose down" in both new trial scripts (macOS virtual key
codes 91/84, inferred from the Quick Reference Card's up/down arrow icons
on its separate "KEYBOARD CONTROL STICK" diagram, distinct from the
top-row digit keys used for defenses/toggles). Verify against the first
live repeat's screenshots/pitch behavior before trusting the label.

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

## Mission data (targets + radar types) — CONFIRMED (2026-10-09)
- **CE.WLD** (theater file) = 2937 B: header, then 16-byte place records from
  0x38: u16 name_id (1-based into the NUL-separated name list; one entry is
  empty), X, Y (waypoint-table units, 64/km), kind, 3 x u16 flags, u16 class
  (294 SAM site, 309 ship slot, 324 OTH radar, 277 search radar, 318 city,
  286 tank farm). Parser: `analysis/parse_theater.py` -> logs/theater/CE.json.
  All 16 named SAM sites match briefing positions (within the ~5 km bias of my
  briefing-map fit).
- The game's **resident launcher** (low memory; chains SU/START/missions/EGAME/END)
  keeps a working copy of the .WLD, and the mission generator writes the mission
  into it, so it is readable during the briefing and in flight:
  - copy +0x18 / +0x28: primary / secondary target records (name, X, Y, ..., class)
    (CORRECTION 2026-10-17: these are only generated target slots, not the targets in role order;
    the real primary/secondary + bases are in START's briefing block, see "Mission briefing block in OUR build")
  - SAM/ship/OTH records: kind = radar code, 0 = eliminated / no ship
  - codes: 1 SA-2 P37, 2 SA-5 P43, 3 SA-8B P39, 4 SA-10 D140, 5 SA-11 P62,
    6 SA-12 D108, 7 SA-13 P23, 8 SA-N-4 D50, 9 SA-N-5 P28, 11 SA-N-7 P62, 21 OTH D156
- `scripts/read_mission.py` decodes it; on mission 3's snapshot it reproduces
  the briefing exactly (2 targets, 21/21 radars incl. the eliminated Lukow and 4 boats).

## Altitude vs mountains (game mechanic, from the user, 2026-10-09)
- All sea and land sit at one level; the altitude value (0x40250, == HUD) is height
  above that flat world. Mountains rise an undetermined height above it and the
  altimeter never reflects them ("+500 ft at the moment of impact"); near-misses can
  sometimes clip through (fuzzy collision).
- Consequences in the tools: route_planner.py treats mountain cells (briefing-map
  brown/red/dark grey) as impassable with a 1-cell (2 km) margin and never simplifies
  a leg across them; nav.py warns "MOUNTAINS x km AHEAD" from the planner's
  terrain.npz look-ahead along the current heading.
- **Decoded 2026-10-15** (`analysis/terrain_3d.py`, details in `mountain_finder.md` §0): the mountains
  are the game's 3D solids from .3DG/.3DT/.3D3. Heights are in altimeter feet; 3D-world Y is flipped
  vs map Y. 5 of the 6 crashes are inside decoded solids (the 6th was not terrain). The planner now
  routes around them at constant low altitude and rewards radar shadow behind ridges; the briefing-map
  relief is no longer used when THEATER is set.
- Save states: start F19 first, THEN load a state. Loading at the bare DOS prompt
  leaves the game frozen (timer runs, game loop does not).

## Radar exposure flight 1 (North Cape, easiest difficulty, 2026-10-14)

Logs: nav `logs/nav/1792028447.csv`, memlog `logs/memlog/1792028452/`, gauge `logs/emv/1792023748.csv`
(plus the first leg: nav `1792023696.csv`, memlog `1792023702/`).

- **EMV in memory: u8 at old 0x4108C** (copy at 0x451D8), r = 0.978 vs the HUD gauge.
  Gauge rows ~= 0.51 x value (full 51-row gauge ~= 100).
- What drives EMV (memlog + nav log, ~2800 samples):
  level flight 160-250 kt: ~7 (no measurable speed effect in that band); turning 0.2-0.6 deg/s: 8,
  0.6-1.2: 9, 1.2-4: 13, >4: 16; ~400 kt at full throttle: ~25-27; bay doors open: 37-38;
  gear: no visible effect; 400 vs 800 ft level: ~7 vs ~9 (climb pitch probably contributes).
- Gauge radar bars only ever drawn red (ground) / light blue (air): never pink/yellow/white, i.e.
  never detected. Bars blink ~0.5 s every ~6.5 s real (~13 s game). Smallest gap to detection on the
  gauge: ground 26 rows, air (fighter radar, MiG-31s from Monchegorsk) 11 rows -> air radar is the
  closer threat; bay doors open (EMV ~19 rows) near a fighter would have overlapped.
- Ground bars appeared well outside every briefed range (Kirovsk OTH bar visible from ~200 km).
- Candidates for the gauge's radar side: old 0x4109C alternates 4 / 11 in step with ground / air bars
  (which threat is being drawn?); s16 at old 0x4109A sits near 0..+7 when a bar is shown and ramps
  negative between blinks.
- Timing: the sim runs ~2x real time in this DOSBox setup, ~4x with in-game time acceleration.
- Keyboard sensitivity: u8 at old 0x417DA = level - 1.

## Time multiplier, turn-rate vs EMV, carrier launch (2026-10-14)

- **Accelerated time: u8 at old 0x410AA** = 1 normal, 2 accelerated (flipped exactly when the user
  toggled it in memlog 1792023702; ground speed vs airspeed doubled at the same moments). nav.py
  reads it to scale its controllers.
- **EMV vs turn rate** (EMV byte, level flight 160-235 kt, 3241 samples; rate in nav deg/s):
  level 7 | 0.3-0.5: 9 | 0.5-0.9: 10 | 0.9-1.1: 12 | 1.1-1.3: 13 | 1.3-1.6: 18 | 1.6-2.0: 20 | 2-3: 21.
  Extra EMV x time per degree turned is ~4-5 up to 1.2 deg/s and ~7.5 above 1.3, lowest near 0.8.
  -> nav H_RATE_MAX 1.5 -> 0.8, LOOKAHEAD 8 -> 12 km; planner TURN_RATE 0.8, BANK_PENALTY 0.43.
- **Carrier catapult launches heading ~185** (every carrier flight); plan_mission's climb-out now
  follows the launch direction (TAKEOFF_HDG; 180 for px: takeoffs, 0 for airbases).
  (Superseded 2026-10-17: the direction depends on the theater, see "Takeoff direction".)
- Third 3D-terrain crash (North Cape, x 10105 y 8356, level at 413 ft); with the previous one
  (x 10655 y 9416) they mark an unseen ridge. Crash sites are kept out of plans via
  logs/theater/<TH>_obstacles.json until the .3DT format is decoded.

## Veteran vs easy: own signature (2026-10-15)

Memlog 1792035792 (veteran, North Cape, autopilot part only; the gauge log was invalid - stale
window capture). EMV byte in level flight: 400 ft ~12 (easy 6-7), 800 ft ~19 (easy 8-10), turning
0.6-0.9 deg/s ~15 (easy 9). So difficulty (or the loadout - not controlled) scales the player's EMV
~1.7-2x; altitude 400 -> 800 ft adds ~40-60% on both. Radar candidates: old 0x4109C took extra
values 12 and 15 on veteran (4/11/0 on easy); the s16 at 0x4109A behaved the same on both.
Automation server bug fixed: replies were queued globally, so a client that disconnected mid-flood
left lines that shifted every reply for the next client (cause of "unpack requires a buffer" and the
failed passive logger). Server now clears queues on accept; client resyncs on connect.

## Veteran radar-tactics flight, Central Europe (2026-10-15)

Mission: primary Tank Farm Liberec, secondary Tank Farm Carlsbad, out of Rhein-Main; plan `logs/plan/ce_veteran`
(TEST_CARD.md, route_test.json). Logs: nav 1792111732 / 1792113502 / 1792114183 / 1792114900, memlog for each,
HUD logs/emv/1792110378.csv. Joined by `analysis/radar_flight.py` -> radar_flight.json, radar_blinks.csv.

- **Nav bugs found and fixed:**
  - Repeated corner points made zero-length legs.
  - Off-track "aim at leg end" made the plane orbit short legs.
  - Fixes: `route_aim` intercept and `next_leg`.
  - The real turn radius is ~20 km (0.8 deg/s cap at ~2x sim speed). Test legs must be >= ~60 km.
- **Veteran:** ground bars blink pink ("poor detection") ~1/3 of the time from takeoff, even 120+ km from the SA-10. Easy flight: never pink.
- **Colour tracks bar length:** red <~15 rows, pink >~17 rows. Colour key per the Technical Supplement: red/pink/yellow = ground radar not / poorly / has detected; light blue/white = air radar not / has detected.
- **Blinks come in pairs per ~15 s cycle:** a long pink (~22-24 rows), then ~3 s later a short red (~8-9 rows). This looks like one radar at a time on the HUD; per-radar attribution is still to do.
- **Terrain masking:** no effect seen yet. Behind the ridge from Grossenhaim: 3.1 long blinks/min, median 22.5 rows. In line of sight: 3.6/min, median 24 rows. Unattributed blinks, so not conclusive.
- **Mission-minute counter:** u16 at old 0x41E9E counts +1 every ~26 s real (= 1 game minute) from takeoff.
- **"Behind enemy lines" counter (cold-war scoring):** not found yet. No counter in the egame_data window ticks only in the eastern half of the flight. Searched u8 and u16, wrap allowed. A full 640 KB dump was taken at the END (Pravda) screen: logs/mem/ce_veteran_end_pravda.bin.

### Cold-war scoring: "behind enemy lines" (2026-10-15)

- **Flight event log.** The debrief replays the flight from a table of 6-byte records: u16 t (2 game-s units), u8 x/128, u8 y/128 (waypoint units), u8 type, u8 param.
  - Types: 0x08 = takeoff and end; 0x09 = in-flight sample every 64 game s; 0x8A param 2 = primary photographed.
  - In the END-screen dump it is at 0x401B2. Decoded: logs/plan/ce_veteran/event_log.json.
  - No running score exists in flight memory. The debrief computes the rating from these records.
- **Territory grid.** 16x16 bytes, one per 32 km cell: cell = (x >> 11, y >> 11) in waypoint units. It sits just before the .WLD name strings (CE.WLD 0x7BA). `analysis/territory.py` reads it.
  - Low two bits: 3 = friendly, 2 = neutral, 0 = enemy.
  - It is the same table F-15 SE2's END uses (`gridFlags[gx + gy*16] & 3`) for kill credit and for capture after ejecting.
- **Validated against the debrief ratings.** Every interval between known ratings gains points only if it contains an enemy-cell sample, and always does:
  - 4 enemy samples on the early foray east of Rhein-Main: +13.
  - 74 minutes of loops on the friendly side: +0.
  - Points resume at the first sample in cell (5,12).
- **Points per enemy sample: 1-7, mean ~4.5.** Single samples gave +7, +1, +2. The source of the variation is unknown (random? depth?).
  - The primary photo is worth about 630-650.
  - Ejecting in enemy territory cut 833 to 212.
- **nav.py** now shows "territory: ENEMY - behind the lines" and logs a `side` column.

## Radar pulse rings on the MAP display (CE, Gutersloh -> Cottbus, 2026-10-16)

Logs: nav 1792199506, memlog 1792199511, HUD + panel frames logs/emv/1792199394(.csv); plan logs/plan/ce_cottbus.
Analysis: analysis/radar_flight.py (blinks), analysis/radar_rings.py + radar_rings_join.py (rings).
Crashed ~27 min in at (9490,16537), flat ground, with the alt hold set to 200 ft (see nav bug below).

- **The left MAP display draws each radar pulse** as a pink (236,98,92) double ellipse expanding from the site
  (~80 km per 0.25 s real frame). Map scale at zoom 1: 0.237 px/km in x, y/x = 0.70, offset (0.5, 10.6)
  (whole theater visible); zoomed views are x4 / x8 and pan.
- **Ring = pink gauge bar.** 54 of 75 multi-frame rings start in the same 0.25 s frame as a pink ground bar
  (more within ±0.5 s). Red (short, "not detected") bars have no ring: rings seem to be drawn only for pulses
  that detect you poorly or better. So the pink bar is one radar's pulse, attributable from the ring centre.
- **Each radar pulses every ~15.5 s real (~31 game s)**: Wittstock 15.4-16.7 s, Grossenhaim 15.2, Mittenwalde
  15.4-15.9. The gaps of 31/45 s are pulses that came back red (no ring).
- **Bar length = that radar's strength at the plane**, falling with range: Wittstock SA-10 154 km -> 18-19 rows,
  110 km -> 26-27, 80 km -> 31-32. Grossenhaim SA-10 118 km -> 20-21; Mittenwalde SA-12 88 km -> 23-24.
- **Doppler aspect confirmed:** Wittstock at 34-45 km side-on (aspect 50-83 deg) -> 29-33 rows; at 55-67 km
  head-on/away (aspect 0-26) -> 35-38 rows. Head-on is worse even from further out.
- **Pulse aspect (Cottbus SA-11, 29-38 km):** aspect 44 -> 32 rows, 58-60 -> 37-38, 80 -> 44 (once 33). Side-on
  is worse, as the manual says.
- Easy-level colours: pink (>~17 rows) from 150 km; white (air radar detected) three times near Cottbus and on the
  loop; no yellow.

**nav bug (cause of the crash):** `airborne = alt > 150` turns all control off below 150 ft, and the floor guard
(FLOOR 250, "UP!") sits above a 200 ft target, so the hold pushed down toward 200 while the guard pushed up.
After 4 "down" presses in a turn the plane sank through 150 ft and nav stopped pressing anything until impact.
Keep --alt >= 300 until airborne is latched and the target is clamped above FLOOR.

## Mission rating formula — decoded from END.EXE (2026-10-17)

END.EXE is EXEPACK'd: `analysis/unexepack.py END.EXE END.bin` (DGROUP = seg 0x9d9). Score routine at
image 0x5666 (`analysis/end_re/score_real.asm`; Ghidra project analysis/end_re). gameData = far ptr [0x86ae],
commData = far ptr [0x9ed6]. Tables are int16 in DGROUP; "/8" steps are integer (truncating) long math.

Per flight-event record (status & 0x3f = type; 0x80 = primary flag, 0x40 = secondary flag), indexed by
ROE = gameData+0x3a (0 Cold War, 1 Limited, 2 Conventional):
- type 9 (64 s position sample) in an enemy cell (grid & 3 == 0): **+1** (Cold), +1 every 4th (Limited), 0 (Conv).
- types 10/11/1/2/3 with the primary flag: **+140 / 120 / 100**. Secondary: **+70** alone, **+35** if the primary
  is also done (net 175 for both, any order).
- kills (types 1,2,3,12): Cold War 0 for every class; Limited 1-9; Conventional 3-18 by target class
  (table 0x4258 [roe][class & 15]); air kills 0/3/6 (Cold/Lim/Conv) and up to 12; friendly or neutral-cell
  targets -25; other penalties -2 to -15.
- type 6 **visual ID**: -20/-12/0, type 7 **radar ID**: -10/-6/0 (types identified in EGAME 2026-10-18: one radar ID = 10 enemy samples). Type 8 (takeoff/end) clamps the running score at >= 0.
Then: score = score x T[theater]/8 x T[mission type]/8 x T[opponents]/8 x T[landing]/8, with
- theater (gameData+0x38): Libya 8, Persian Gulf 10, North Cape 12, Central Europe 13 (5th entry 8).
- mission type (+0x3c, START list): Air-to-Air 8, Strike 9, A-A Training 8, Strike Training 9.
- opponents (+0x3e): Green 8, Regular 11, Veteran 14, Elite 17.
- landing (+0x40): No Crashes 5, Easy 8, Realistic 10.
- ejected (landingType commData+0x26 == 2): x [2,3,6,4][byte 0x978c]/8 (index 0 = enemy -> 2/8, confirmed).
  A crash or safe landing costs nothing.
- fuel (commData+0x2e, presumably the briefing's FUEL ESTIMATE): >= 7000 x9/8, >= 9000 x10/8, >= 11500 x11/8.
Check: CE veteran flight = (140 + 50 enemy samples) x 13 x 9 x 14 x 10 x 9 / 8^5 = 850 -> x2/8 after ejecting
in enemy = 212 (observed 212; the earlier 833 is base 186). Top settings: one enemy sample is worth ~6.7 points.

Fuel: the Change Waypoint panel shows game time and fuel: 9830 lb at 11:35:24 -> 5890 lb at 12:41:00
(ce_cottbus, ~225 kt at ~250 ft) = **~60 lb per game minute** (~64 lb per sample). Extra Fuel tank: 1500 lb.

## Medals, ribbons, promotions — decoded from END.EXE (2026-10-24)

All of it lives in END.EXE; ROSTER.FIL only stores the results. END main (image 0x93) does `missions++` for every
non-training flight on entry (death and capture included), then scores (0x7328 at 0x208), then awards (0x75b0 at 0x211):
promotion check 0x8526, medal/ribbon check 0x85d7. Decomp addresses are image - 0xc.

**ROSTER.FIL** = u16 header + 10 x 0x50-byte pilot records (gameData = far ptr [0x86ae] points at the active one):
+0 index, +2 name, +0x20 rank (0 2nd Lt .. 6 Brig Gen), +0x22 Purple Heart flag, +0x24/26/28/2A counts of Airman's
Medal / DFC / Silver Star / Air Force Cross, +0x2C Medal of Honor flag, +0x2E best mission score, +0x30 last mission
score (0 in training), +0x32 u32 career total, +0x36 missions flown, +0x38.. theater/ROE/type/opp/landing,
+0x44 Combat Readiness, +0x46 Short Tour, +0x48 Long Tour, +0x4A Longevity, +0x4C Longevity w/ Gold Cluster.
Flags: commData+0x30 = training mission, ds:9A1B / ds:9DDE = primary / secondary objective done,
ds:99F4 = captured (set by the TASS screen 0x7088), commData+0x28 != 0 = dead ("posthumously").

**Medals**: at most one per mission, from s = this mission's score:
- s > 1200 and no Medal of Honor yet -> **Medal of Honor** (once per career).
- else s >= 900 x (AFC + 1) -> **Air Force Cross**; else s >= 600 x (SS + 1) -> **Silver Star**;
  else s >= 300 x (DFC + 1) -> **DFC**; else s >= 100 x (AM + 1) and AM < 9 -> **Airman's Medal**.
  Each repeat of a medal needs a higher score.
- Captured: no medal and the count is not raised; the commander "will not recommend you for the X".
  The training version ("If this had been a real mission...") cannot fire, because training scores are stored as 0.
- **Purple Heart** (once per career, still awarded when captured): damage count (commData+0x36) > 6 and damage bits
  0x04 (flight controls) and 0x08 (autopilot/INS) both set. Shown as "... and the PURPLE HEART for being wounded".

**Ribbons** (missions = +0x36 after this flight's increment, non-training only):
5-9 Short Tour; >= 10 Long Tour (replaces Short); 30-59 Longevity; >= 60 Longevity w/ Gold Cluster (replaces Longevity).
**Combat Readiness Ribbon**: a training mission with the primary or secondary objective done (once).

**Promotion** (non-training, at most one rank per mission; uses the career total that already includes this mission):
from rank r you need total >= T[r], missions >= M[r] and total / missions >= A[r] (ds:66C6 / 66DE / 66D2):

| from -> to | T total | M missions | A average |
|---|---|---|---|
| 2nd Lt -> 1st Lt | 300 | 2 | 100 |
| 1st Lt -> Captain | 1125 | 5 | 150 |
| Captain -> Major | 3000 | 10 | 200 |
| Major -> Lt Col | 7000 | 20 | 250 |
| Lt Col -> Colonel | 16000 | 40 | 280 |
| Colonel -> Brig Gen | 27720 | 99 | 280 |

Colonel -> Brigadier General happens only on exactly the 99th mission (the retirement mission). Captured below Colonel:
no promotion this time ("will delay the promotion"); it can still come on a later mission. Dead pilots are still
promoted ("posthumously"). With no award at all: "remained at his present rank". After the 99th mission, when not dead,
END shows the retirement message.
Cross-checked with the live ROSTER.FIL (Fred McGriff: 277 -> Airman's, then 1211 -> Medal of Honor, 1st Lt after 2 missions).
**File I/O** is only in START.EXE: load at image 0xa4fb (`fopen "rb"`, fread 2 bytes into ds:9B52, then 10 x fread 0x50
into ds:9D54), save at 0xa376 (same layout, `"wb"`, header = active pilot's index). No checksum, magic or size check,
so hand edits are safe. Name = +2..+0x1F (30 bytes, NUL-terminated; the name editor takes chars 0x20-0x7F and
caps the name by pixel width). +0x42 and +0x4E are unidentified.

## Ordnance vs target strength — decoded from EGAME.EXE (2026-10-17)

`analysis/ordnance.py` prints all of this from the game files. EGAME DGROUP = image 0x1ecf0 after
`unexepack.py` (START has the same tables at its own DS).
- **Missile/bomb specs** (18 B: name[8], u16 range?, speed?, mode, agility?, flags?), indexed by `spec`:
  0x16 AIM120, 0x17 AIM-9, 0x18 HARM, 0x19 Penguin, 0x1a Harpoon, 0x1b AGM-65, **0x1c LGBOMB, 0x1d RTBOMB,
  0x1e FFBOMB** (laser-guided / retarded / free-fall), plus the enemy SA-x/AA-x entries.
- **Loadout catalog** (20 x 26 B: short[10], long[12], u16 spec, u16 qty), weapon index 0-19: AIM-9M, AIM-120,
  HARM, Penguin, Harpoon, Maverick, GBU-12, Rockeye, Durandal, Mk82 Slick, Mk82 Snakeye, Rockeye II, Fireeye,
  Fuel-Air, IN Cluster, Minelets, Camera, Extra Fuel, Guns, Special. No damage field anywhere.
- **Effectiveness matrix** ds:5106, int8 [20 weapons][13 categories], right after the catalog.
  missileTargetCompat = code 0x91ae: `matrix[w*13 + (cat[class & 0x7f] & 0xf)]`. Target table ds:80AA,
  16-B records in .WLD order (class at +0xE, destroyed = byte +8 bit 0x80). Category table ds:95D2.
- **Target category** = .WLD 100-B table (just before the territory grid) indexed by name id of the
  type (= class & 0x7f); low nibble = category, 0x10 = flag (airbases, bridges, planes, camps, Sub Pen...).
  Categories: 1 AirStrip/Cargo Ship, 2 SAM Radar/Typhoon, 3 Mobile SAM, 4 Bridge/OTH/LPAR radar, 5 AirBase,
  6 AirPlane/Refinery, 7 Tank Farm/Camp, 8 Hangar, 9 HQ, 10 CV Kremlin, 12 boats, 13 SA/SS-20 missile,
  14 Oil Rig, 15 Nuclear Plant; 0 everything else (Sub Pen, Depot, Tower, Buildings, Destroyer, carriers).
  **13-15 overflow** the 13-column row and read the next weapon's row (no bounds check).
- **All three ground-kill paths are gated by the matrix** (destroyGroundTarget 0x77c3 has no other caller);
  v = matrix value, opp = opponent level (ds:4EF0 = gameData+0x3e):
  - bombs (spec 0x1c-0x1e), 0x7123: kill if dist(impact, nearest 3D object) < v*64/(opp+2) waypoint units
    (= v/(opp+2) km). The impact snaps to the nearest 3D object; a record at exactly that spot is the victim,
    else a scratch slot with the 3D shape's category (as F-15's findWaypointEntry).
  - missile direct hit, 0x7380: kill if v > random(4), else HUD "Ineffective".
  - guns, 0xb423: kill if dist < v*6/(opp+2).
  - v = 0 -> that weapon can never kill that target. Unlike F-15 SE2, bombs don't bypass the matrix.
- Sub Pen (NC class 0x146, record "Severomorsk" at 21760,13568; none at Murmansk) is category 0: only Maverick 2,
  GBU-12 4, Mk82 Slick 3, Fireeye 2, Fuel-Air 4, IN Cluster 1, guns 3 can kill it.
- HARM is 0 against SAM Radar (cat 2): it must work some other way (radar shutdown?), not checked yet.
- territory.py bug: GULF.WLD's first name is "Bandar Abbas", so the `[A-Z][a-z]{2,}\0` search lands 7 bytes late
  and the PG grid is shifted (ordnance.py backs up over the multi-word name).

## Takeoff direction: decided by the theater alone — decoded from EGAME.EXE (2026-10-17)

EGAME flight init at image 0x1FC1 (after `unexepack.py`; ds offsets are EGAME DGROUP = image 0x1ecf0):
```
les bx,[6640]            ; far ptr to the pilot record (same fields as START's ds:991C one): +0x38 theater, +0x3A level
test byte es:[bx+38],1 ; ax = 0
jne  -> heading = 0       ; ds:46DA = player heading (= old phys 0x4024A), 65536 = 360 deg
else    heading = 0x8000
if takeoff record flags & 0x200 (carrier): heading high byte += 4   (+0x400 = +5.6 deg)
```
and at 0x3CFB the start point is put at the runway end: dir = +1 (theater odd) / -1 (even);
airbase: 3D Y -= 0x708 * dir (0x708 position units = 56.25 waypoint units, ~0.9 km, south of the
base record for a northbound start); carrier: X -= dir << 7. The takeoff record index is ds:8798.
- Theater index (pilot +0x38) = 0 Libya, 1 Persian Gulf, 2 North Cape, 3 Central Europe: EGAME's
  per-theater file table at ds:006E reads lb/pg/nc/ce.xxx in that order.
- **Rule:** Gulf and Central Europe take off **north (0)**; Libya and North Cape take off **south (180)**.
  Carriers: 5.6 (Gulf) / 185.6 (Libya, North Cape). Base, wind, mission and targets play no part.
- Matches every logged takeoff: CE Ramstein (start 3392,23288), Gutersloh (3392,19192), Rhein-Main
  (rolling north at 3392,21103) all 0.0; NC Gallivare (rolling south at 3392,21671, nav 1792032168) 180.0;
  NC USS Kennedy (11012,3328) 185.6 on every carrier flight (the earlier "catapult ~185").
  Libya and Gulf are unflown; the rule there comes from the code only.
- `scripts/read_mission.py` now writes `takeoff_heading` (and `takeoff.heading`, `takeoff.carrier`) in the
  mission json; the planner (analysis/planner/plan.py `game_takeoff_heading`) applies the same rule by default and
  writes one route.json ("both" -> route_N/route_S only when plan.json asks for it).

## Weapon release, altitude and blast damage — EGAME (2026-10-17)

- ds:46DA/46DC/46DE/46E0 = heading/pitch/roll/altitude ft (ds:46E0 = phys 0x40250 at this DS).
- Fire routine 0x7a65: refuses only if |roll| > 0x3000 (~67 deg), no ammo, spec 0/-1, or bay closed
  (ds:684C bit 2; message "Bay closed"; extra fuel exempt). **No altitude check.**
- Projectile slots ds:53FC + 0x18*i (player 8-11): +0 X, +2 Y, +4 alt ft, +6 speed (= velocity >> 11),
  +8 heading, +A pitch, +C roll, +E ttl, +10 spec, +12 weapon, +14 locked target, +16 pitch floor.
  Spawned at plane alt - 20 ft. Pitch: missiles and LGBs = plane pitch - 0x1000 (22.5 deg nose-down);
  free-fall (0x1e) keeps the plane's pitch and noses over 0x800/frame-unit to a floor; retarded (0x1d)
  = straight down (0xC000) at speed 1. LGB pitch is kept <= -0x800 (always >= 11 deg down).
- Guided steering 0x6df7: pitch/heading error clamped per update to agility (spec +6) << 8 / << 7;
  enemy missiles get an extra opponent-level clamp. Unlocked missiles never pull up.
- Impact: alt < 0 or terrain -> projectile ends ("<weapon> ground impact" for player missiles).
  Missile proximity uses |dAlt| / 32 + horizontal; bombs kill on horizontal distance only (at ground).
- **Blast damage 0x7185** (after every bomb impact): horizontal dist(plane, impact) + alt_ft/32 < opp*32 ->
  damage via 0x7a0b (opp+1 random damage bits, skipped if ds:684C bit 0x1000). Directly overhead that's
  < 1024 / 2048 / 3072 ft for Regular / Veteran / Elite; never on Green.

## Photo, lock and guidance — EGAME (2026-10-17)

Planner-facing summary: notes/targeting_for_planner.md.
- Briefing blocks in EGAME: ds:8794 + 0x12*k (+0 type, +2 target index), as in START.
- **Photo** (0xba36): camera selected (spec -1), bay open, frame taken (ds:5B18 == 3). For each type-1 block whose
  target == locked ground target ds:5526 and not destroyed: r = range (0xc954, octagonal waypoint units, also sets
  bearing ds:6348); e = atan2(-alt/32, r) (0xd17d, 65536 = 360 deg); pass if
  r + |e - pitch + 0x6ef|/16 + |bearing - heading|/16 < (8 - 2*opp)*64 -> "Target photographed.".
  Boresight mode (ds:684C bit 0x100) picks the target where the nose meets the ground (10 km ahead if not nose-down).
- **HUD lock** (ds:6342, "Missile Lock"): HUD projection 0xc673 stores depth ds:9642 = camDepth >> 3 (negative ahead).
  Ground: target within 0x30 (octagonal px) of the HUD centre and -depth/7 < spec range (+0x8) -> D < 56*R units;
  in-range colour at -depth/4 < R. LGB (class 0x1c): range < alt*5/32 units. Air (class 7, sim objects
  ds:8854 + 0x24*i): -depth/8 < R -> D < R km.
- **Spec table** ds:4C10 + 0x12*spec: +8 range R, +A max speed (>> 6 = speed cap), +C class (7 AAM, 4 HARM,
  5 anti-ship, 6 Maverick, 0x1c/1d/1e bombs), +E agility.
- **Guidance** (0x6af4): projectile speed starts at velocity >> 11 and rises 1 every other frame to max >> 6.
  Classes 4/5/6/0x1c steer to the projectile's own target (+0x14, set at launch for class 6, or class 5 when the
  target has flag 8); without one they search the target table (4: active radars; 5: category & 0xC; 0x1c:
  category & 0xC == 0; 6: any) through the seeker test 0x760c. Nothing depends on the plane after launch.
- **Supply drop:** the canister impact (spec 0x26) within 0x100 units of a type-3 block target before the deadline
  (ds:6630 < ds:9638) -> "Equipment Delivered", else "Bad Drop". Cue "Drop supplies now": on screen and depth > -16.
- **Seeker test** 0x760c: passes if the bearing from projectile to target is within 0x1000 (22.5 deg) of the
  projectile heading. Player classes skip the plane-heading check (class 0 only). No range or elevation limit.
- **Guided aim point** (0x6e75): desired pitch = atan2((target_alt - alt)/32 + range/8, range), i.e. ~7 deg above
  the target line, so missiles loft.
- **Airspeed** kt = ds:7F94 / 27 (ds:855A). Missile start speed = ds:7F94 >> 11 = kt/76.
- **Frame scale** ds:4EFC = (measured frame value + 2)/4, clamped [4 - accel, 15]; halved in time acceleration.
- analysis/release_geometry.py simulates the low-altitude Maverick sink and the photo windows from these rules.

## Guns — EGAME (2026-10-18, from the disassembly; not yet seen in flight)

Plan built on this: notes/gun_attack_plan.md.
- **Trigger:** key 0x0E08 (Backspace) sets ds:5C6E = 1 ("button 0", OR'd with joystick button 0 in 0xd364); Enter (0x1C0D)
  sets ds:5C70 (button 1). Both are cleared at the start of each key-processing pass (0xd3b0).
- **Spawn** (0x47f4, odd frames only: ds:5520 & 1): if button 0, rounds ds:4EEC > 0 and ds:944A == 0: rounds -= 40/fs,
  slot = (ds:5520 >> 1) mod ds:855E; speed r = 0x70/fs units/frame along the **nose** (ds:46DC pitch, ds:46DA heading):
  vAlt = r*sin(pitch) << 5 (ft), vX/vY from r*cos(pitch) and heading (Y negated); position = player units ds:94EE/94FE
  + one step, alt = ds:46E0 + vAlt - 2. With no trigger the odd-frame slot is cleared. Slots ds:9B88 + 12*i
  (X, Y, alt, vX, vY, vAlt), count ds:855E = clamp(2*fs, 3, 16), +4 more in the loops (other shooters?).
- **Motion:** every frame pos += v. There is no gravity and the plane's velocity isn't added, so the impact is the nose line meeting alt 0.
  Life ~4*fs frames x 112/fs = ~448 units (~7 km) of slant range.
- **Ground impact** (0xb37a: alt < 0): the point goes to ds:94EA/94F6/94FC; 0xc892 snaps to the nearest 3D object (0x11080) and returns
  the target record at exactly that spot, or 0 = a scratch slot with the object's category. If it isn't destroyed, kill when
  octagonal dist(impact, object) < matrix[18][cat]*6/(opp+2) -> 0x77c3 + "<name> destroyed by gunfire". SAM Radar / Mobile SAM
  (v 4): dist <= 11 / 7 / 5 / 3 units for Green / Regular / Veteran / Elite.
- **Flight-tested 2026-10-18 (Leck, Regular, logs/gun/):** Backspace held fires (8 rounds/bullet at fs 5, 10 at fs 4).
  **Velocities are whole units a frame, rounded:** r = 112//fs, k = round(r*sin(-pitch)) units (x32 ft) of descent,
  rh = round(r*cos pitch), vX = round(rh*sin hdg), vY = -round(rh*cos hdg). Impact after n = (alt-2-32k)//(32k) + 2 frames
  at player + n*v (reach n <= 2*slots). gun_probe.ccip() predicted 8 of 8 impacts to 0-3.5 units over -3.7/-15/-24 deg
  dives; the exact nose line was ~140 units off. So pitch only has to sit in a band, range comes in r-unit steps set by
  altitude, and only a heading within ~asin(0.5/rh) (~1-1.3 deg) of N/S/E/W fires with no drift. fs changed 5 -> 4 mid-dive.
  Pull-outs from -15 / -24 deg lost ~125 ft (bottoms 367 / 202 ft).
- **Live 2026-10-19 (Regular):** Wittenberg SA-13 (record 7) killed by a burst landing 6.5 / 1.5 u from its record.
  Wittstock SA-10 (record 16, 11328,12992) survived 20 bullets on its exact X line, all 6-55 u *past* it (three at
  6.5 u). After that run the scratch record 0 held an unnamed object (class 0x21) at (11344,13016), so bullets snapped to
  another site object. The distance test uses the snapped 3D object's own position ([ds:6E58]+4/+8 >> 5), not the record.
  Unresolved: how 0x11080 (far call, not decoded) picks the object. Aim for the record itself (miss <= 3), not the edge.
  ds:94EA is only written while ds:6C54 <= 0 (set to 8 after a kill) and also by enemy gunfire hitting the player
  (IL-76 hits logged at the player's own X/Y).
  Cottbus SA-13 (radar record at 14016,15168) killed on heading ~133-134 deg (diagonal) in a -12..-19 deg dive from ~2000 ft
  at fs 4: the burst clustered 1-40 u around it and a bullet 1 u off killed it (ds:94EA then froze, as the 6C54 cooldown
  predicts). Off-cardinal rounding costs < 0.5 u/frame per axis, so with a steep dive (n ~ 8 frames) it's only ~3-4 u:
  cardinal headings matter for shallow, long shots, not steep ones. Bottom after that pass: 126 ft.
  Impacts also appeared 3.5 u from the Cottbus Mobile SAM record ~400 u from the player with no rounds fired: other
  shooters (the 4 extra slots) write ds:94EA too.
- **Air:** bullets vs sim objects with flag 2: |dX| + |dY| + |dAlt|/32 (octagonal-ish) < (0x17a/fs)/(opp+1) -> "Hit by gunfire" damage.
- Kill points (END 0x4258 [roe][cat], cat = END ds:998A[class & 0x7f] & 0xf, the .WLD 100-B category table read at
  END_decomp.c:132): Limited 2,6,1,1,3,8,2,2,5,3,1,9,4,0,0,0; Conventional 4,12,3,3,6,16,4,5,11,7,3,18,8,0,0,0.
  Before the table: type flag 0x40 or a target in a non-enemy cell -> -25; flag 0x80 -> ds:4222 (-10/-4/+1).

## Landing rules — EGAME (2026-10-17)

Landing mode m = gameData+0x40 (0 No Crashes, 1 Easy, 2 Realistic). ds:684C bits: 0x01 gear UP, 0x02 flaps,
0x04 bay open, 0x08 brakes. Airspeed kt = ds:7F94/27. One fs-frame step (ds:4EFC frames) = ~3.2 game s
(position += 27*kt/8 units of 2048/km per step).
- **Runway box** (0x4144, every frame): nearest friendly base (flags & 0x201 and & 0x500), used within 0x200
  units (8 km) or when on the ground. Inside if |X - baseX| <= W and |Y - baseY| <= L (units; runways are N-S):
  airbase W 5, L 64 (+-1 km); flags & 0x800 (airstrip) L 32; carrier (flags & 0x200) W 8, L 30, deck at 128 ft
  (ground level ds:94EC). Easy adds W +4, L +8. Result in ds:5534.
- **Touchdown** (0x2cf2, the frame altitude reaches ground level, inside the box): crash code 5 ("crashed onto
  the runway, cartwheeled and exploded") if m * sink > 128 (carrier 256), or m > 0 and gear up, or
  |roll| > (24/m) << 8 (Easy 33.75 deg, Realistic 16.9 deg). m = 0 skips all three.
  sink = -ds:9E90, vs = (velocity/16 * sin(pitch - AoA)) >> 14 with a Q15 sine = 3.375 * kt * sin(gamma) ft per step.
  So kt * sin(pitch - AoA) <= 19 (Realistic) / 38 (Easy), x2 on a carrier: <= 64 / 128 ft of altitude per 3.2 game s.
  AoA ds:6616 = (0xC00 * stall_kt / kt - 0x300) * cos(roll) (~12.7 deg at stall, less when faster); stall ds:9B82,
  -1/4 with flaps. No airspeed test as such: speed enters only through the sink.
- **On the ground:** roll forced to 0, negative pitch to 0, nose can't rise below stall speed. Brakes (bit 3) on the
  ground: velocity -= (32 - 8m) kt per step (Realistic 5 kt/game s, Easy 7.5); in the air they're an airbrake
  (v/16 per step). Speed also lags toward the thrust target (v/16 per step at idle).
- **Off the box** (alt 0 at ground level, outside the box, includes overrunning the end): crash code 1 ("Flying into
  the ground") if kt > 50, except No Crashes with damage <= 4 and fuel left.
- **Safe Landing:** in the box, kt <= 1, friendly base (flags & 0x500, not 0x800) for fs frames -> end code 0.
  Type-4 landing missions: "Cargo delivered" if this base is the block's target, before the deadline.
- Carrier wire (0x41a2): deck altitude, kt > 80, 16-20 units along the deck from the centre, heading within 45 deg.
- END crash codes (commData+0x28): 1 ground, 2 hill, 3 fuel, 4 missiles, 5 runway cartwheel, 6 ejection failed.
  END.EXE has no "tire"/"careened" text in this build.
- Rollout with brakes and idle (simulated): Realistic 150 kt stops in 0.84 km (below 50 kt after 0.73), 180 kt 1.15 km;
  without brakes 150 kt still does 50 kt after 2.6 km.

## Takeoff rules — EGAME (2026-10-17)

- Stall speed ds:9B82 = 120 kt * (1 + alt/65536) * isqrt(4 * load16) / 8, then -1/4 with flaps (684C bit 1).
  load16 = ds:662A = table ds:45FE[|roll| >> 8] (16 = 1 g wings level, up to 208) + elevator pull when airborne,
  capped at 128 (8 g). On the ground roll is forced to 0, so **stall = 120 kt (90 kt with flaps)**.
- On the ground the nose can't rise above 0 below stall speed (0x2b9e): **rotation speed = stall speed.**
- The plane climbs once pitch > AoA (vs = 3.375 kt sin(pitch - AoA)); AoA = 0xC00*stall/kt - 0x300:
  12.7 deg at 120 kt clean, 10.3 deg at 140, 5.9 deg at 200; with flaps 8.4 deg at 120 kt, 4.2 deg at 180.
- Runway left: start point 56 units (0.88 km) before the base record (see "Takeoff direction"); the runway box ends
  64 units past it, so **~1.9 km** (Easy +0.125 km). Touching the ground outside the box above 50 kt = crash code 1.
- Logged nav takeoffs (CE, full throttle): lift-off at 123 / 129 kt; 139 kt after 0.53 km, 190 kt after 1.03 km,
  206 kt after 1.12 km (~20 kt per real second).

## Full-mission autopilot: takeoff, attack, landing phases (2026-10-17, NOT flight-tested)

`nav.py --route <plan>/route_X.json --auto` flies the whole mission: Takeoff -> route -> Attack primary -> route ->
Attack secondary -> route -> Landing. The controllers live in `scripts/phases.py`; nav.py's heading and altitude holds
keep running, and each phase overrides them per cycle (heading, altitude / glide cap, VS limit, roll/alt hold off).
Phases are triggered by the planner's point kinds: `runin` (within 10 km) / `launch` / `photo` / `overfly` with a role
start an attack; the leg to `Final` starts the landing.
- **Throttle without an emulator rebuild:** the automation key table has no `-`/`=`. `Dosbox.scan_tap` sends the set-1
  scancode via the 8042 "write output buffer" command (`OUT 64 D2`, `OUT 60 <code>`). Each D2 clears the controller
  buffer and one drain writes all queued OUTs, so make and break go out separately. Shift+= / Shift+- = max / no power.
  Speed hold (`phases.Throttle`) nudges -/= every 3 s from airspeed (the throttle position byte is unknown).
- **New reads** (`f19_state.read_flight`, old phys = 0x3BB70 + ds offset): pitch 0x4024C, roll 0x4024E, flags word
  0x423BC (0x01 gear up, 0x02 flaps, 0x04 bay, 0x08 brakes), locked target 0x41096, opponent 0x40A60, runway box 0x410A4,
  projectile slots 0x40F6C, target table 0x43C1A. From the disassembly only; `scripts/probe_controls.py` checks them.
- **Takeoff:** brakes off, flaps down, max power, rotate at 110 kt holding pitch 8-13 deg, gear up > 80 ft, flaps up
  > 300 ft and 180 kt, then speed hold at the profile's cruise_kt and normal altitude hold.
- **Attack:** Maverick = 280 kt, B until locked target == the mission's target record, steer at the target, at the planner's
  launch distance: bay open, nose up to +18, fire, bay closed, nose down to level. Photo = bay open 1.5 km before the photo
  point, fire the camera while the game's photo score (r + 11.38 x angle errors) is under 90 % of (8 - 2 opp) x 64, max 4
  frames. Overfly (bombs, supply) is cue-only.
- **Landing:** gear down 12 km before the aim point (0.5 km short of the base record), flaps + 140 kt at 7 km, glide cap
  60 ft/km, flare to 2.5 ft/s below 40 ft, localizer 1.5 deg/unit (box is only +-5 units = +-78 m wide), idle + brakes at
  touchdown. Goes around and hands back if still up past the base record or > 8 units off the centreline below 80 ft.
- **Probed live on the runway 2026-10-17** (Tabor photo / Mittenwalde destroy, Veteran; `probe_controls.py`):
  pitch/roll/flags/opponent (2)/runway box (1 on the runway) read sanely. Toggle bits 0x04 bay, 0x02 flaps, 0x08 brakes
  confirmed (gear 0x01 not tested: air only). **Discrete keys must be tapped 0.2 s**: a 0.5 s press hits the typematic
  repeat (500 ms) and the game sees two presses, so toggles flipped back. D2 scancodes work: **throttle % = u8 0x40262**
  (= steps 0, 5, 10, 20, 30; Shift+= 100; Shift+- 0; 0x44168 lags it, thrust?). **Selected station = 0x40A5A** (0-3, Space
  cycles), **loadout = 0x40A4A**, 4 x (u16 weapon, u16 count): here (16 Camera 1) (17 Extra Fuel 1)
  (5 Maverick 2) (0 AIM-9M 4), the loadout the user flew (first read as 0x40A46, one entry early: corrected by the
  user). **Nose-gun rounds u16 0x40A5C** (650 at start). The catalog at 0x40A6E (20 x 26 B, ordnance.py) has a per-bay
  capacity in its qty field, not the loadout. **Briefing blocks 0x44304** in flight = (1, 12) (2, 2): record 12 in the target table is Tabor at
  (17472, 27328), so target-table index = briefing record index. Attack now selects the weapon itself (checked live:
  Camera and Maverick reached). **B did not lock anything on the runway** (ds:5526 stayed 0xFFFF): the targets were
  100-200 km away; B presumably cycles only targets in range/view. Test near a target.
- **Still open:** what B selects / when ds:5526 locks (nothing on the runway); the pitch/roll sign; whether the heading hold can keep +-78 m on final; no "photographed" success flag yet.
- **First --auto flight, 2026-10-17 (Tabor/Mittenwalde, plan logs/plan/ce_tabor_auto, nav log 1792271555.csv): crashed.**
  The takeoff worked unaided: brakes off, flaps, max power (D2 scancodes), rotate at 110 kt, airborne at ~150 kt after
  ~0.7 km, gear and flaps up, 500 ft about 18 s after brake release. Then it handed over at +8-10 deg pitch and full power,
  overshot to 1460 ft, and the altitude hold **pushed the nose down all the way into the ground** (-90 ft/s at the end).
  Cause: the extra reads made the loop ~1 s a cycle, and the vertical-speed estimate needed > 3 samples in 1 s, otherwise
  0. With vs read as 0, the hold kept asking for a descent. Keyboard sensitivity was 3 at the start; nav set it to 1
  before the roll, and the hold only presses at 1, so it wasn't the cause. Fixes: pipelined reads (`_peek_many`);
  vs over 1.5 s from >= 2 samples, no press without an estimate; a SINK! nose-up guard below -40 ft/s;
  the takeoff levels to +1..4 deg above 220 ft and keeps full power until handover; phases wait for sensitivity 1;
  the nav csv now logs vs and cycle time.
- **Second --auto flight, 2026-10-17 (nav log 1792271817.csv): crashed, stuck roll key.** Reads now ~0.12 s a cycle and vs
  read correctly (10-21 ft/s in the climb). Takeoff to 530 ft fine. Then a 1.1 s left-roll press was released in the same
  cycle as a throttle `D2` scancode: **D2 clears the 8042's pending bytes, so the left-key break code was lost** and the
  game kept left held. The turn rate grew -0.4 -> -11 deg/s through two 2.5 s right presses: a spiral into the ground.
  Fix in `Dosbox.scan_tap`: wait 0.15 s for queued keys to drain, then re-send UP for every key the client isn't holding.
  Also: nav's Ins tap 0.6 -> 0.2 s (0.6 s auto-repeats, two sensitivity steps); takeoff sets cruise throttle at flaps
  up (full power had reached 305 kt). The clean fix is adding `minus`/`equals` to DOSBox-X's automation key table.
- **A stale nav wrecked the next mission (2026-10-17).** The second flight's nav was never stopped. When the user reloaded
  the mission it resumed with the old state (phase route, nose-up presses, ~48 kt down the runway on heading 10) until it
  was killed, and the mission ended. nav now exits after 10 s out of flight. Also added: a roll watchdog that re-sends
  left/right releases when |turn rate| > 2.5 deg/s while nav holds no roll key. Always stop a test's nav before reloading.
- **Third --auto flight, 2026-10-17 (nav log 1792272329.csv): first clean automatic takeoff into cruise.** Brake release
  to 300 ft in ~13 s, levelled at 457 ft for a 450 hold, handed to the route at ~25 s, and the 0.8 deg/s turn onto the route
  tracked. Throttle presses (`thr-`/`thr+`) caused no stuck keys: a left roll released in the same cycle as a throttle
  press still landed, and the watchdog never fired. The speed hold swings 167-202 kt around 190 (5-10 % throttle
  steps): acceptable, could be smoother. Cycle 0.10-0.13 s without a key press (a press blocks ~0.6 s).
- **Third flight, Tabor photo run (2026-10-17/18):** the attack phase ran 1792273486-547. The camera fired 4 frames at
  3.11 / 2.79 / 2.48 / 2.16 km (score 229 / 209 / 191 / 173 against budget 256: all inside the window) and the bay was **left
  open**; the user closed it. User rules: **close the bay the moment "Target photographed." appears**; ground targets need
  the **tracking camera in air-ground mode with the target on screen** (the user set it; that's why B locks nothing on the
  runway). Changed: one frame at score < 0.8 budget, then the bay is closed and re-tapped until the flag reads shut
  (also checked before an attack ends); a banner asks for the trackcam until its key is mapped. nav exited when the user
  paused the emulator (socket timeout) near the secondary.
  **Open:** the "Target photographed" signal (frame state ds:5B18 = old 0x41688, or the flight-event record END.EXE scores)
  so the bay can shut on success; the trackcam air-ground key and mode byte; why the 4th-frame bay close didn't take.

## Mission event log, trackcam and target lock — live (2026-10-18)

- **Flight-event list** (what END.EXE scores) is readable in flight: 6-byte records `[status, target record, u16 time,
  cell x, cell y]` at old phys ~0x54DDA this load (in low memory, not EGAME's DS): type 8 takeoff at t=32, then type 9
  position samples every 0x20 time units (64 s), until an empty slot. **The Tabor photo is logged: `8A 0C E0 05` = type 10
  (photographed) | 0x80 primary flag, target record 12, t=1504.** So "Target photographed." = a new type-10 record
  for our target: the photo controller can close the bay on it. Locate the list by its pattern (08 00 20 00 then 09 00 40 00);
  u16 at ~0x54106 read 80 = the record count (one sample; unconfirmed).
- **Trackcam mode = u8 old phys 0x455A6**, F2 cycles it 2 -> 0 -> 1 -> 2. With the user's order nav -> air-to-ground ->
  air-to-air and the user's air-to-ground as the starting 2: **1 nav, 2 air-to-ground, 0 air-to-air** (inferred, check on the HUD).
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
- **Trackcam must be ON for B (2026-10-18):** with the left CRT on the waypoint screen, B did nothing (the lock sat on 140,
  not a target record). `/` (trackcam ahead, D2 scancode 0x35) brought the camera up, locked on 32 (Mittenwalde AirBase),
  and one B press locked record 2. Attack prep order: F2 to air-to-ground, `/`, then B until locked == record.
- **First automatic Maverick attempt did NOT fire** (nav 1792298024..035): locked 2, Maverick station, 9.1 km, nose +23.5,
  Enter pressed, but the Maverick count stayed 2 and no kill event. The fire most likely needs the HUD missile lock,
  which the pop-up loses (the "firing doesn't need the HUD lock" assumption in notes/targeting_for_planner.md was
  unchecked); HUD lock byte ds:6342 read 0 throughout (address unverified). The pop overshot (+23.5, 560 -> 965 ft at
  the shot, ~2300 ft afterwards) because throttle presses blocked the loop: throttle is now frozen while the nose is held.
  Flaps were found down afterwards (flags 0x4003); source unknown.
- **Pop-up dropped (user, 2026-10-18):** "the maverick doesn't even need a pop up, it can fire just fine at 450 feet", and
  the pop-up climbed to ~2300 ft ("a christmas tree on radar"). Attack now fires LEVEL at cruise altitude with the altitude
  and heading holds on: inside 20 km and 10 deg, bay open -> Enter (2 s apart, max 3, until the Maverick count drops) -> bay closed.
  The sink table in notes/targeting_for_planner.md section 2 overstates the sink at 450 ft (or the game's floor check differs):
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
  in notes/targeting_for_planner.md was built from the remark "the user fires at ~20 km" and the pop-up model (fire early,
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
  spool ds:85F8 = 0 (engines out). Altitude, flaps and weight act only through speed: see "Fuel burn and cruise speed".
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

**EMV** (ds:551C, = the known 0x4108C), rebuilt each frame at 0x4607 — exact formula in the radar section below
(memlog: 4067/4067 snapshots within +-1).

**Radar detection** (per radar site, 0x51a1). Target table ds:80AA, 16 B/record, count ds:9C78:
+0 name, +2 X, +4 Y, +6 radar type (0 = none; spec table ds:4876, 14 B: +0 range, +2 power, +4 bit 0 doppler),
+8 flags, **+0x0A alert level (0-255)**, +0x0E class.
- Return 0x5519 = f(range - dist, power + 2 x opp, terrain clutter = cell-map bits 0x0C); doppler radars scale by aspect to
  the site and by speed. No return when the altitude difference >> 10 exceeds the distance (directly overhead).
- **Seen** when return + EMV > 100 (> 50 = pink threat bar; ds:552C = threat colour 4/0xC/0xE). Each such **pulse**:
  `alert += ((ds:4EF2 + opp) x 16 + 16) >> (ECM on ? 1 : 0)`, cap 255. First return: flag 0x04, HUD "<site> detected".
  (Corrected 2026-10-18: per pulse, not per frame; full model in "Radar sites: pulses, duty cycle, detection" below.)
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

## Radar sites: pulses, duty cycle, detection — EGAME (2026-10-18)

Scheduler 0x4eea (every frame), detection 0x51a1 (one call per pulse), return 0x5519. Validated against all logs/memlog
sessions where noted. Reference implementation + self-check: `analysis/radar_game.py check`. Units: d = game distance (max + min/2) in km (waypoint units >> 6); alt ft; angles 65536 = 360.

**EMV** (exact, memlog 4067/4067 within +-1):
`EMV = atan2(alt/3 x (opp+1), 2048) >> 8  +  |bank| / 2  +  27kt >> 10  +  24 bay open  +  16 ECM on`
- altitude term (Veteran): 300 ft 5, 1000 ft 18, 2000 ft 31, 10000 ft 56 (0..64 = 0..90 deg). Monotonic: **no high-altitude
  benefit** in this code (the manual's 32000 ft advice doesn't apply here). Opponent level scales the altitude (x(opp+1)/3).
- bank: |roll| in 1/256 turns folded to 0..64, halved: **30 deg bank = +10, 90 deg = +32.** Turn flat.
- speed: ds:7F94 = 27 x knots (memlog), so +1 per 38 kt (200 kt 5, 400 kt 10).

**Return of radar type t at the player** (record ds:4876 + 14t: +0 range R km, +2 power P, +4 bit 0 doppler, +6 name):
`s = C_cell x (R - d) x (P + 2opp + 1) / R`, then aspect: `s' = s x (f + 32) / 64`, where rel = angle between the site->plane
bearing and the plane's heading, folded to 0 (flying straight at/away) .. 64 (tangential):
- pulse: f = rel -> x0.5 radial .. x1.5 tangential.
- doppler: f = ((96 - rel) x (27kt >> 5)) >> 9 -> at 200 kt x0.98 radial .. x0.66 tangential; 400 kt x1.47 .. x0.83;
  150 kt x0.86 .. x0.62. **Doppler return grows with speed.**
- C_cell = (grid byte of the PLAYER's 32 km cell) & 0x0C, the same grid as territory.py: **0 mountains, 4 rough (x0.5),
  8 flat land, 12 open sea (x1.5).** CE: 12 cells 0 and 10 cells 1, the high cells (95th-pct terrain ~7500-8100 ft); NC: 82
  sea cells. Mountain cells give **s = 0 for every ground site and every fighter radar** (0x6602 uses the same function).
- cone: s = 0 if |site alt - alt| >> 10 > d (directly above a site: within alt/1024 km).
- **No terrain line-of-sight anywhere in the detection path.** Ridges do not shadow you; only the cell type matters.
- Check: memlog pulse colour (ds:552C at the last site ds:94E4) agrees with this formula on 726/766 pink pulses, misses
  within 30 of the threshold. The air-radar check writes colour 4 before testing, which explains every "4" mismatch.
- Seen = s' + EMV > 100. Pink bar = > 50 (no alert gain). With s <= 0 you are still "seen" if EMV alone > 100.

**Detection range** (seen, km) at Veteran, EMV 10, 200 kt, flat land (sea in brackets). "-" = never:
| radar | kind | R | P | flying at/away | side-on | SAM range |
|---|---|---|---|---|---|---|
| SA-2 | P | 200 | 3 | - | 12 (75) | 125 |
| SA-5 | P | 350 | 2 | - | - (100) | 150 |
| SA-8B | P | 125 | 5 | - | 31 (62) | 65 |
| SA-10 | D | 320 | 7 | 15 (117) | - (15) | 125 |
| SA-11 | P | 200 | 5 | - | 50 (100) | 100 |
| SA-12 | D | 290 | 6 | - (89) | - | 150 |
| SA-13 | P | 125 | 3 | - | 8 (47) | 65 |
| SA-N-4 / N-6 | D | 200 / 320 | 4 / 6 | - (31 / 98) | - | 30 / 125 |
| SA-N-5 / N-7 | P | 150 / 200 | 3 / 5 | - | 9 / 50 (56 / 100) | 30 / 100 |
| OTH | D | 500 | 5 | - (119) | - | - |
Rough cells: nothing sees you at EMV 10. Range grows fast with EMV and level: SA-11 side-on 43 / 50 / 67 / 90 / 117 km at
EMV 6 / 10 / 20 / 34 / 50; 0 / 12 / 50 / 75 km at Green / Regular / Veteran / Elite. SA-10 at/away: 0 / 15 / 77 / 118 km
at 150 / 200 / 300 / 400 kt. ("SAM range" = missile spec +8, km: the site can fire once alerted inside it.)

**Pulse timing** (per site, record +0x0C countdown): period = `256 x ds:4EFC / (alert/4 + 16)` frames + site index, i.e.
**16 ticks = 32 game s when quiet** (matches the ~15.5 s real rings), 8 ticks at alert 64, 4 at 192, 3.2 at 255:
an alerted site looks 5x as often. A pulse fires when the countdown reaches 4; any return (site or plane) sets a global
lockout ds:552A = ds:4EFC frames, and a pulse that comes due inside it is skipped for that whole period.

**Duty cycle** (memlog: 97 % of 316k site-snapshots match the site's "active" flag, record +8 bit 0x02): a site with alert 0
and no flag 0x100 is only active in a window of 1024 frames (~256 ticks, ~8.5 game min) when
`(index x (frame >> 10) x 7) & 7 <= 2 x ds:4EF2 + 1` (+ 3 instead of + 1 for ~1-2 min after a network alert, via
ds:9502 -> 95CE -> 963E stepped every 128 frames). ds:4EF2 = 0 in all our flights, so 37.5 % of quiet sites are on in
an average window (56 % after a network alert; analysis/radar_game.py duty_fraction). The frame counter ds:5520 starts random (0x49fd), so the schedule
is unknown at planning time but **predictable live from ds:5520**.

**Destroyed sites go silent** (0x503a): the scheduler loop skips any record with +8 bit 0x80 (or +6 == 0) and clears its
active bit 0x02, so it never counts down, never pulses and never detects. Live 2026-10-19: Wittenberg SA-13 (record 7)
killed by gunfire reads +8 = 0x84 (destroyed + seen, active clear). The map marker stays.

**Alert, launch, ID** (per pulse, 0x51a1):
- not seen: alert -= 16 (floor 0), flag 0x10 cleared. Seen: += (war + opp) x 16 + 16 (48 at Veteran, 24 with ECM).
- alert > 0x7F -> network alert (all sites raised to (war + opp) x 16 - 16 = 16, your position broadcast).
- Gate for anything more: the number of enemy missiles already homing within 16 km of you (ds:6336 -> 7F96) <= opp,
  alert > 0xC0, site type != 21 (OTH never shoots), and **you are > 20 km (0x500 units) from the nearest airfield record**
  (ds:663E, records with flags 0x201 + 0x500: no launches or IDs near those bases).
- then: not yet ID'd and alert >= 0xFA -> Radar ID (that pulse); otherwise, if enemy slot (index & 7) is free and you're
  within its missile range -> **launch** ("<site> firing <SAM>"), commData+0x2C++.
- Veteran, seen on consecutive pulses: 3 pulses -> network alert, 4 -> SAM launches possible, 6 -> radar ID. One unseen
  pulse takes 16 off. Seen once and then hidden, a site is back to 0 after 3 quiet pulses.

**SA-14 cells per mission:** only some of the file's 0x10 cells are live in a given mission (memlog: 0 or 3-5 of 8-10 at
takeoff), cleared at load; nav can read the live set from ds:85FC at takeoff.

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

## Planner model (2026-10-19)

`analysis/planner` now plans on the game's own radar and fuel rules (notes/planner_radar_prd.md). The old heuristic
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

## Other theaters, terrain over-statement and the 20 km turn radius (2026-10-24)

### Gulf / Libya data (kept)
- Theater codes CE / NC / LB / PG map to CE.WLD / NC.WLD / LIBYA.WLD / GULF.WLD (`analysis/theaters.py`). `read_mission.py`
  and `new_plan.py` detect the theater from the .WLD record table in memory (`--theater auto`, the default); `--theater`
  still overrides. Place lists: `logs/theater/<code>.json` for all four (`analysis/parse_theater.py`; CE/NC unchanged).
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
