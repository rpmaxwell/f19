# FINDINGS.md: stale sections

Early action plans and the second-hand v435.04 layout, superseded by the confirmed findings in [docs/game/](../../docs/game/); kept for history. Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

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
