# Candidate addresses — running log

Findings from all phases of the FINDINGS.md action plan get appended here as they're
confirmed, so Phase 3 (write breakpoints) has a single place to pull targets from.

Format per entry:

```
## <field name>
- Source: <script/tool that found it>
- Offset: <file offset / segment:offset, physical formula if relevant>
- Confidence: <strict / weak / unconfirmed>
- Notes: <anything relevant>
```

---

## Mission-briefing-block fingerprint (step 4) — NEGATIVE RESULT
- Source: `analysis/find_mission_fingerprint.py`
- Offset: none found
- Confidence: n/a
- Notes: Scanned every offset in START.EXE, EGAME.EXE, SU.EXE, DS.EXE, END.EXE
  for the two-5-byte-tuple pattern 0x12 apart. 0 candidates where both tuples'
  `code` byte matches their `type` byte per the confirmed enum (0x2F=landing,
  0x3A=supply_drop). Loose-predicate pairs exist (1-166 per file) but are
  binary noise — no `code`/`type` agreement anywhere. Conclusion: the mission
  block is **not statically embedded** in any of these EXEs; it's populated
  at runtime (RNG mission gen and/or loaded from ROSTER.FIL/.WLD data), so
  Phase 1 cannot shortcut past the emulator for this field after all — the
  live-capture protocol in FINDINGS.md's "what other agents should test"
  section (capture during an active briefing screen, back out `DS`) is still
  required. Worth noting for Phase 2/3: this also means don't expect to find
  this block via static Ghidra data-segment inspection either — it needs a
  live memory capture or a breakpoint on the code that constructs it.

## ASOUND.EXE vs ASOUND_FROM_F15STRIKEEAGLE2.EXE similarity (step 5)
- Source: `analysis/diff_asound.py`
- Offset: n/a (whole-file comparison)
- Confidence: measured
- Notes: MZ headers structurally similar (same header size 512B, same
  `e_lfarlc`=30) but `e_cblp`/`e_cp`/`e_crlc`/`e_csum` all differ, and only
  2 of 12 (A) / 14 (B) relocation-table entries are shared verbatim —
  expected, since a relinked binary reassigns fixup addresses even for
  identical source. Content-body diff (post-512B-header, autojunk off):
  **73.7% similarity** (6704 equal / 2514 replace / 871 insert / 764 delete
  bytes across 440 non-equal regions). F19's ASOUND.EXE body (9512B) is
  ~10% larger than the F15 SE2 version's (8680B). Conclusion: **moderately
  patched, not near-identical** — MicroProse adapted this shared module
  rather than reusing it verbatim. Treat the public f15-se2 decompilation as
  a *directional* analog for shared subsystems (structure/approach likely
  similar) but not a byte-level or even routine-level reliable source —
  verify anything pulled from it against F-19's actual disassembly before
  relying on it.

## WLD file structure (step 7) — partial
- Source: `analysis/parse_wld_roster.py`
- Offset: header = bytes 0x00-0x37 (56 bytes), consistent across all 4 theaters
- Confidence: header boundary strict; record layout unresolved
- Notes: All four `.WLD` files share a 56-byte header: bytes 0-3 non-zero
  (unidentified — checksum/seed?), a `u16` count at offset 4 and another at
  offset 6 (Libya 20/33, Gulf 25/43, NC 18/32, CE 27/54 — plausibly
  "targets"/"something else" counts, scale sensibly with file size), then
  zero-padding through offset 0x37. Real data resumes at 0x38. No clean
  fixed-record-width division was found against `n1`, `n2`, or `n1+n2` for
  any candidate — record table is probably fixed-width entries interleaved
  with variable-length data (e.g. name/label strings), not a pure linear
  array. Needs Phase 2 (START.EXE's file-loading code) to resolve fully.

## North Cape byte-fix (step 7) — CONFIRMED PRESENT
- Source: `analysis/parse_wld_roster.py`
- Offset: `NC.WLD` bytes `0x1F1` and `0x201`
- Confidence: strict (direct read)
- Notes: Both candidate offsets currently read `0x08` — matches the known
  bug (should be `0x09`) from the Hacking-F117A precedent. The bug is
  present in our copy of `NC.WLD`; objective-completion detection in the
  North Cape theater is expected to silently fail, per FINDINGS.md's
  caveat. Not yet determined which of the two offsets is the "real" one
  vs. a byproduct of two-tuple structure — both read identically broken.

## ROSTER.FIL record layout — RECOVERED (from backup; live file is a separate issue, see below)
- Source: `analysis/parse_wld_roster.py`
- Offset: 2-byte file header, then 10× 80-byte (0x50) fixed-width records
  starting at file offset 0x02
- Confidence: strict — record boundaries confirmed via 10 exact pilot-name
  matches with zero leftover bytes ((802-2)/80 = 10.0 exactly)
- Notes: name field starts at record-relative offset 2, ASCIIZ up to ~30
  bytes, followed by ~48 bytes of unparsed stats (rank/mission counts/etc,
  not yet decoded — visible structure in the hex dump: small u16 fields
  before the name and a block of u16/u32-looking values after it, worth a
  pass once Phase 2 tooling exists). Recovered from
  `ROSTER.FIL.corrupted-20260803`, not the live file — see below.

## ⚠️ ROSTER.FIL data-loss finding (not part of the RE plan, flagged separately)
- Source: `analysis/parse_wld_roster.py`
- Notes: The **live** `ROSTER.FIL` in the actual game install
  (`~/Documents/F-19 Stealth Fighter/MPS/F19/ROSTER.FIL`) is **802/802 bytes
  entirely zero**. The file named `ROSTER.FIL.corrupted-20260803` sitting
  next to it (same size, slightly older mtime) is fully intact and contains
  the classic MicroProse designer-name pilot roster (Sid "Slime" Meier,
  Bruce "Whammer" Shelley, etc.) — i.e. the naming is backwards from what it
  implies: the file labeled "corrupted" is the good one, and the live file
  is the one that's actually blank/broken. Not touched by any script in
  this project — flagged to the user directly rather than restored
  automatically, since it's a live file in their actual game installation,
  outside this repo.

## Unexplained files `B` and `Y` — inconclusive (low priority)
- Source: `analysis/parse_wld_roster.py`
- Notes: `B` (10234B) does NOT start with the `MZ` magic, so despite its
  size being close to `ASOUND.EXE`'s (10024B) it is not simply a renamed
  copy — content unidentified (starts `33 33 16 00 00 00 E8 04 E7 07 EF 08
  BD 09 D0 0B ...`, could be a numeric/pointer table). `Y` (6B) is exactly
  `79 0D 0A 0D 0A 1A` = ASCII `"y\r\n\r\n"` + a DOS Ctrl-Z EOF marker —
  looks like captured console input (someone typing "y" + Enter at a
  prompt), almost certainly install-time cruft rather than game data. Not
  pursued further per plan (low priority, non-gating).

## Phase 2 setup — Ghidra installed and working
- Source: `brew install ghidra` (pulls `openjdk@21` automatically), confirmed
  `x86:LE:16:Real Mode` language works with Ghidra's built-in "Old-style DOS
  Executable (MZ)" loader — auto-detected cleanly on both `START.EXE` and
  `EGAME.EXE`, no manual loader override needed.
- Gotcha: Ghidra 12.x dropped Jython — `.py` post-analysis scripts now
  require **PyGhidra** (real Python 3, via JPype1 bridging into the JVM),
  not the legacy Jython interpreter `analyzeHeadless -postScript` used to
  use. Set up with: `python3 -m venv analysis/.venv && source
  analysis/.venv/bin/activate && pip install pyghidra` (JPype1 built from
  source against Python 3.14 without issue, ~30s). Run scripts via
  `pyghidra.run_script(binary_path=None, script_path=..., project_location=,
  project_name=, program_name=, analyze=False, nested_project_location=False)`
  — **must** pass `nested_project_location=False` when pointing at a project
  `analyzeHeadless` already created (its default `True` assumes a doubly-
  nested layout `analyzeHeadless` doesn't use, and will silently create an
  empty shadow project + return `currentProgram=None` instead of erroring
  clearly).
- Environment needed: `JAVA_HOME=$(brew --prefix openjdk@21)/libexec/openjdk.jdk/Contents/Home`,
  `GHIDRA_INSTALL_DIR=$(brew --prefix ghidra)/libexec`.
- Project lives at `analysis/ghidra_project/F19Project` (gitignore-worthy —
  large binary DB files, not source).

## START.EXE string-anchor candidates (step 3, partial)
- Source: `analysis/ghidra_scripts/find_string_xrefs.py` via PyGhidra against
  the analyzed `START.EXE` program
- Confidence: strings confirmed real and relevant; xrefs NOT yet resolved
- Notes: 12 keyword-matching strings found, most plausible for briefing-
  screen telemetry/status: `RADAR DATA` @ `2000:0472`, `RDR RNG:` @
  `2000:049a`, `Fuel Est.: ` @ `2000:4278`, `Fuel Loaded: ` @ `2000:4b9a`,
  `FUEL TANK` @ `2000:421e`, `Extra Fuel` @ `2000:1ba0`, `Radar Sites` @
  `2000:3741`, `Missile Ranges` @ `2000:374d`. **All show 0 references** via
  `getReferencesTo()` despite Ghidra's Data Reference / x86 Constant
  Reference analyzers having run — this is the expected automation ceiling
  flagged in the plan: MS-C 5.1 large-model calling convention likely pushes
  segment/offset as separate immediate operands or reads them through a
  jump/string table Ghidra's default analyzers don't resolve to formal data
  refs. Next step here is manual GUI review (open the program in the full
  Ghidra UI and search for the string's raw address value, e.g. `0472`, as
  an immediate operand across the code section) rather than further
  headless scripting.
- These are still segment-relative (`2000:` is Ghidra's load-segment
  assignment for the MZ image, not necessarily the runtime `DS`/`CS` value)
  — same physical-offset-translation caveat as the mission-briefing block
  applies before these are usable against a live capture.

## EGAME.EXE string-anchor candidates (step 3, partial) — HIGH VALUE, in-flight labels
- Source: `analysis/ghidra_scripts/find_string_xrefs.py` via PyGhidra against
  the analyzed `EGAME.EXE` program
- Confidence: strings confirmed real; xrefs unresolved (same as START.EXE)
- Notes: 16 matches, and unlike START.EXE's briefing-screen strings these
  read like genuine **in-flight HUD/status text**: `Speed ` @ `2000:549e`,
  `Max Speed- ` @ `2000:53b5`, `Missile Lock` @ `2000:54df`, `Weapons` @
  `2000:521e`, `Damage` / `Blast damage` @ `2000:523a` / `2000:4eeb`,
  `stall warning` @ `2000:5140`, `BRAKE` @ `2000:5173`, `Missile range- ` @
  `2000:5376`/`0x53a1`, `Fuel transfer` @ `2000:4f6a`. This is the most
  promising concrete lead toward in-flight telemetry addresses found so far
  (nothing in prior art covers this). Followed up with a raw byte-pattern
  scan of the file for each string offset's little-endian immediate
  encoding (a poor-man's xref check, independent of Ghidra's analysis DB):
  mostly 0 occurrences too, except `Damage` (7 hits), `Blast damage` (2),
  `Weapons` (1) — worth a manual look, but the overall pattern confirms
  these aren't referenced via simple literal immediate loads. Most likely
  explanation: a table-driven/indexed string-print system (print message
  #N) rather than direct `mov dx, offset str` call sites, which neither
  Ghidra's static analyzers nor a flat byte scan will resolve — **this is
  the real ceiling for static methods on this codebase.** Next productive
  step is Phase 3 (live write-breakpoints) or manual Ghidra GUI review, not
  further headless/static scripting.

## Phase 3 prototype target (step 6)
- Source: offline re-analysis of `dumps/test_cases.jsonl` (existing tagged
  captures, no new capture needed) — background-noise floor computed from
  the 3 consecutive "control: no action" captures came back **empty** (the
  game is fully frozen while broken into the debugger, so no clock/animation
  churn contaminates the diff — good, simplifies things).
- Offset: physical `0x03C5F0` (segment `3000h`, offset `0xC5F0`)
- Confidence: byte changed in ALL 9 repeats of "cycled ordnance (space)"
  across 3 separate sessions, zero false-negatives
- Notes: same offset also changes on "opened bay doors", "fired sidewinder",
  and "fired cannon" — almost certainly a generic "input processed" or
  screen-redraw flag, NOT something ordnance-count-specific. That's fine for
  Phase 3's purpose: `scripts/breakpoint_capture.py` uses this as
  `DEFAULT_SEG`/`DEFAULT_OFF` purely to validate the send/detect/capture
  mechanism end-to-end (does DOSBox-X's `BPM` command work via the same
  UI-automation pattern, does the window title actually flip on an
  *automatic* break the way it does on manual break-in). Once confirmed
  working, re-target against Phase 2's actual telemetry candidates (still
  needs the DS-translation math from the mission-block caveat above) or dig
  for narrower per-action offsets by intersecting/subtracting more of the
  existing tagged-capture action sets.
- **Not yet run against a live session** — needs you to have F-19 running
  under dosbox-x. Script is written and syntax-checked but unvalidated.

## Phase 3 — LIVE VALIDATION SUCCEEDED (step 6)
- Source: `scripts/breakpoint_capture.py`, run against a live session
- Confidence: confirmed working end-to-end
- Notes: Two bugs found and fixed before this worked:
  1. **All the UI-automation scripts (`memdump_loop.py`,
     `breakpoint_capture.py`) assumed dosbox-x was launched inside
     Terminal.app.** This user's environment uses iTerm2, which has a
     different AppleScript object model. Fixed by adding iTerm2 support to
     `memdump_loop.py`: `get_dosbox_tty()` gets the process's controlling
     tty via `ps`, `find_iterm_window_id()` locates the matching iTerm2
     session by tty and returns it encoded as `"iterm:<tty>"`,
     `find_terminal_window_id()` now tries Terminal.app first (unchanged
     behavior when that's what's used) and falls back to iTerm2. New
     generic dispatchers `send_raw_command(win_id, text)` (iTerm2's
     `write text` needs no window focus at all — more robust than
     Terminal's `do script ... in window id`, which also doesn't need focus
     but works differently) and `focus_terminal_window(win_id)` (needed
     before raw keystrokes like F5, which go to whatever's OS-level
     frontmost) replace the old Terminal-only inline AppleScript in both
     `send_memdump_command` and `breakpoint_capture.py`'s
     `send_bpm_command`. This is a **generally useful fix**, not
     session-specific — any future script using these primitives now works
     regardless of which terminal app launched dosbox-x.
  2. `breakpoint_capture.py`'s `set_breakpoint_and_arm()` sent the F5
     resume keystroke without calling `focus_terminal_window()` first —
     latent bug, masked previously because `send_raw_command`'s Terminal
     path doesn't need focus either, so nothing had ever exercised the
     "raw keystroke needs focus" path from inside this function before.
     Fixed by adding the focus call, mirroring `resume_execution`'s
     existing pattern.
- **Result**: with those fixed, `python3 scripts/breakpoint_capture.py`
  (default target `3000:C5F0`) armed the breakpoint, the user pressed Space
  in-game, and DOSBox-X broke back into the debugger within the 15s window.
  Debugger log confirmed the exact write: `Memory breakpoint : 3000:C5F0 -
  00 -> 42`. Screenshot captured `CS:IP = F000:D10B`, disassembly showing a
  tight `loop 0000D10B ($-2)` — plausibly the tail of a BIOS INT9
  keyboard-handler chain the game's own hook jumps into after writing its
  state, consistent with this offset being a generic "key event" byte
  (matches the earlier finding that it changes on multiple unrelated
  actions). Script correctly left the debugger open for inspection, then
  was manually resumed via `ml.resume_execution()` to let the game continue.
- **CORRECTION**: the arm → trigger → auto-break → screenshot path is
  confirmed working, but the **resume step failed** — after calling
  `ml.resume_execution(win_id)` against the `iterm:/dev/ttys002` session,
  the user reported the game did not actually resume (stuck frozen in the
  debugger). `focus_terminal_window`'s iTerm2 branch (`select t` / `select
  w` on the session/tab/window) apparently doesn't reliably grab OS-level
  frontmost focus the way Terminal.app's `activate` + `set index ... to 1`
  does, so the subsequent F5 (sent via System Events with no target
  process, goes to whatever's actually frontmost) likely landed elsewhere
  instead of the iTerm2 session. **iTerm2 support is only half-validated**:
  window-finding and text-sending (`write text`, which doesn't need focus)
  both work; focus-dependent raw keystrokes (F5 resume) do not yet work
  reliably. Not fixed further this session — user relaunched dosbox-x under
  Terminal.app instead (new PID confirmed running under
  `/System/Applications/Utilities/Terminal.app`), sidestepping the issue by
  using the originally-tested code path. If iTerm2 support matters later,
  `focus_terminal_window`'s iTerm2 branch needs a more reliable focusing
  method (e.g. `tell application "iTerm2" to activate` alone first, then
  verify with System Events which app is actually frontmost before sending
  the keystroke, retrying if not).
- Next step: re-validate against the Terminal.app session, then point it at
  a more action-specific offset (or a real Phase-2 telemetry candidate,
  once DS-translated) to get exact-instruction-level ground truth instead
  of statistical diffing.

## Diff-noise root cause — CONFIRMED (world/entity model via f15se2-re decompile)
- Source: byte-diff-count re-analysis of existing `dumps/test_cases.jsonl`
  (108 tagged captures, no new capture needed) + `neuviemeporte/f15se2-re`
  decompiled source (see FINDINGS.md's new "World & dynamic-entity model"
  section for the full struct-level detail).
- Confidence: strict — direct byte counts from existing data, cross-checked
  against real compiler-accurate struct definitions from a sibling title.
- Notes: computed per-consecutive-pair byte-diff counts across all 108
  records (`np.sum(a != b)` on the 128KB segment-3000h/4000h tagged
  region). Pattern: every pair captured *before the first action of a
  scripted run* diffs 0-15 bytes over a ~6-7s gap; every pair *after* the
  first action — action or "idle before" alike — diffs 1,000-11,000+
  bytes over the same gap length. Example run (records 26-36): baseline
  15 diff -> fired#1 3984 -> **idle**#2 4447 (!) -> fired#2 8291 ->
  **idle**#3 3734 -> fired#3 4008... the "idle, no action" gaps are just
  as noisy as the action gaps once anything has fired. This matches the
  f15se2-re-confirmed entity model exactly: `g_particles`/`g_projectiles`/
  `g_simObjects` are dormant (no diff) until something fires, then animate
  every single frame thereafter (particle altitude/position integration,
  projectile ttl countdown, threat-site `alertLevel`/`threatTimer`
  ticking in `updateThreatSites()`) regardless of subsequent player input.
  **Conclusion: the before/after tagged-diff method cannot isolate
  action-specific bytes once any entity table goes live — confirmed
  structural, not a capture-timing bug.** Phase 3 write-breakpoints are
  the correct tool from here; don't invest further in refining the
  statistical-diff approach (e.g. subtracting an "idle baseline" won't
  work either, since the idle baseline is only clean before first contact
  and becomes just as noisy as the action data afterward).
- Actionable, no-capture-needed follow-up: `struct Target` (f15se2-re,
  `src/struct.h`) is byte-identical in shape to F-19's already-confirmed
  18-byte mission block at `ds:B936`, and has 8 trailing bytes
  (`coord[6]` grid-ref string + `distance` int16) beyond the 5 fields we
  already track. Next briefing-screen capture should also read
  `ds:B940`-`B947` (primary) / `ds:B952`-`B959` (secondary) — no new
  search needed, just read those offsets once `DS` is known.
- Wind: searched f15se2-re's full source tree case-insensitively for
  "wind" — zero hits outside "window". No weather/wind term near the
  flight-model code (`egflight.c`). Treat that part of the original
  hypothesis as **not supported** by the closest available analog;
  F-19 predates F-15 SE2 and is the simpler title, so unlikely to have
  a system the later game lacks.
## 3000:C5F0 zero-input control test — RESOLVED: not input-latched, likely DAC/marker-cycle tied
- Source: `scripts/breakpoint_capture.py` run live, action = "control: zero
  input, hands off" (no keypress for the full 15s arm window)
- Confidence: strict — direct live observation
- Notes: breakpoint FIRED with zero player input, ruling out the "input
  event flag" hypothesis. Break state this time: `CS:IP = 2CC0:00001E60`
  (`inc bx` / `shr bx,1` / `mov si,[3F03]`) — the game's own code segment
  doing index arithmetic into a table, **not** the `F000:` BIOS INT9
  keyboard-handler tail seen on the earlier keypress-triggered break. Two
  different code paths write this byte.
- Refined hypothesis (reconciles this result with the *zero*-diff control
  captures at test_cases.jsonl records 7-9, which had comparable hands-off
  windows but showed no change at all): this is likely tied to
  `egtick.c`'s `gfx_dacCycle()` — per f15se2-re's own docstring, the timer
  tick hook's second job is cycling the DAC palette specifically for
  "animated palette entries (target markers, fire warning)... so they
  pulse," i.e. it only writes when a target/threat marker is actively
  showing. This live test had an active tracked contact on screen (`SAM
  RADAR AT DESSAU`); the old zero-diff control captures did not have an
  active contact. Not a universal every-frame counter — conditional on
  HUD marker/threat-indicator state. Deprioritize this address for generic
  action-mapping; it's telling us about marker/palette animation state,
  not player input.
- **Bonus**: live register panel showed `DS=3BB7` (`SS=3BB7` too) for this
  EGAME.EXE session — the exact value FINDINGS.md's physical-offset
  translation formula needs. Not yet confirmed stable across relaunches;
  record per-session until proven otherwise.
## g_planeTable equivalent — FOUND, byte-precise, via structural decompile match to f15se2-re
- Source: `analysis/ghidra_scripts/find_entity_loop_candidates.py` (immediate-
  constant scan, tightened v2) + `analysis/ghidra_scripts/decompile_candidates.py`
  (Ghidra decompiler on the shortlist), run against F-19's own `EGAME.EXE`
  in the existing Ghidra project.
- Confidence: strict — not a guess, a field-by-field structural match.
- Notes: `FUN_1000_4ede` (Ghidra addr `1000:4ede`) decompiles to an
  algorithm essentially identical to f15se2-re's `updateThreatSites()`
  (`src/egthreat.c`): same `siteIdx*(frameTick>>10)*7&7` arc-timing
  expression, same `flags & 0x100` / `flags &= ~2` / `flags |= 2` bit
  operations, same "`threatTimer -= 1`; if `<=0` recompute from
  `alertLevel`; if `threatTimer==4` fire" sequence, calling a second
  function (`FUN_1000_5195`) at the fire point that is F-19's equivalent of
  `fireGroundThreat()` — confirming ground/air threat sites in F-19 really
  do act autonomously on a timer, independent of player input, exactly
  matching the session's original hypothesis, now traced to real code
  instead of inferred.
- The struct field offsets it indexes (all computed as `local_c*0x10 +
  <negative literal>`, i.e. 16-bit wraparound addresses) decode to a
  **16-byte-stride table starting at `ds:80AC`**, matching `MapTarget`
  from f15se2-re's `struct.h` field-for-field:

  | Field | F19 offset (ds:) | Δ from base | matches `MapTarget` |
  |---|---|---|---|
  | (mapX) | `80AC` | +0x0 | `mapX` +0x00 |
  | (mapY, inferred) | `80AE` | +0x2 | `mapY` +0x02 |
  | active | `80B0` | +0x4 | `active` +0x04 |
  | flags | `80B2` | +0x6 | `flags` +0x06 |
  | alertLevel | `80B4` | +0x8 | `alertLevel` +0x08 |
  | threatTimer | `80B6` | +0xA | `threatTimer` +0x0A |

  (nameIndex/secondaryNameIndex at +0xC/+0xE not yet observed in this
  function's code path, inferred by struct-shape continuity only.)
- Two supporting scalars found in the same pass:
  - **`ds:6646`** — the table's live loop bound (`if (*(int*)0x6646 <=
    local_c) break`), F-19's `g_targetEntityCount` equivalent. Should be a
    roster-size constant fixed at mission load, not something that
    changes mid-flight — a quick negative-result breakpoint test here
    (should NOT fire during normal flight) would corroborate this reading.
  - **`ds:8798`** — found in a separate function (`FUN_1000_3c2c`, a
    mission-init/reset routine) indexing the *same* table the identical
    way (`*(int*)0x8798 * 0x10 + <flags offset>`), checking flags bit
    `0x200` ("large"/ground target per `WorldObject.targetFlags` in
    f15se2-re's `struct.h`). This is almost certainly F-19's
    `g_closestThreatIndex`/`g_trackedEnemyIdx` equivalent — the **index of
    whichever threat is currently tracked/closest**, i.e. reading this one
    scalar tells us which table row corresponds to whatever's on the HUD
    right now (e.g. "SAM RADAR AT DESSAU" from the live session).
  - A third scalar, `ds:9740` (compared `==0x14`/20 inside `FUN_1000_4ede`,
    guarding a call to `FUN_1000_984d()`), is likely related to the
    *other* entity table (`g_simObjects`, count 20) rather than this one —
    lower confidence, not yet traced to a specific field.
- **Live translation for the current session** (`DS=3BB7`, confirmed via
  the zero-input control-test register panel above — re-verify per-session,
  not guaranteed stable across relaunches):
  - Target table base: `3BB7:80AC`
  - Live entity count: `3BB7:6646`
  - Tracked/closest index: `3BB7:8798`
- Next step: read `3BB7:8798` live to get the table index of the
  currently-tracked contact (e.g. Dessau), compute that row's `threatTimer`
  address (`80AC + idx*0x10 + 0xA`), and watch it with a write breakpoint
  under **zero player input** — if it counts down and the record's `flags`
  bit `0x02` toggles on its own, that's a direct, named, structurally-
  grounded live confirmation of the autonomous-entity hypothesis this
  whole investigation started from, not just a generic noisy byte.
- **Follow-up live test result — DS stability caveat (important)**: armed
  `breakpoint_capture.py` on `3BB7:6646` (predicted quiet -- expected this
  to be a fixed roster-size constant). It **fired**: `00 -> 0x1B` (27). But
  the register panel on this break showed **`DS=0A72`, not `DS=3BB7`** --
  i.e. `DS` had already moved from the value read ~20 minutes earlier in
  the same continuous session (no relaunch in between). This means: (a)
  the fired breakpoint was watching a fixed physical address (segment
  `3BB7` regardless of current `DS`), so we can't tell whether it caught
  a genuine write to "`ds:6646`-the-count" or a write to unrelated memory
  that now lives at that physical location because `DS` moved out from
  under it -- **the result is inconclusive, not a refutation**; (b) more
  importantly, **`DS` is not stable for the life of an EGAME.EXE session**,
  contradicting the hopeful note above. Given F-19 is confirmed built with
  an MS-C overlay linker (see FINDINGS.md toolchain section), this is
  plausibly overlay-driven (graphics/sound overlay calls, mission
  save/reload, or a screen-mode change reassigning the data segment).
  **Practical consequence**: any `ds:`-relative offset found via static
  Ghidra analysis (like the `80AC`/`6646`/`8798` table above) must be
  re-translated against a **freshly read `DS`** immediately before each
  live test, not reused from an earlier reading in the same session --
  treat every prior "live translation" in this doc as needing
  re-verification at time of use, not a standing fact. Worth a dedicated
  follow-up: read `DS` at several points through a single session
  (briefing, in-flight, after a save/reload, after a theater-map screen)
  to characterize exactly when/why it moves, since that determines whether
  "read DS once per test" is sufficient or whether it can drift
  *mid-test*.
- Not yet found: `g_projectiles` (24B x12, in-flight missiles) equivalent.
  `FUN_1000_6ae8` (47 candidate-immediate hits, heavy `0x18`/24-stride and
  `0xc`/12-count use) is the next function to decompile and trace.

## g_simObjects per-frame AI/movement update — FOUND, `FUN_1000_5828` — KEY FINDING: no stored speed field, motion is recomputed each frame
- Source: manual GUI decompile of `FUN_1000_5828`, reached via Search → For
  Scalars on `8854` (the g_simObjects table base literal) — this function
  had by far the most hits (12 instruction sites) of the 13 functions
  touching that table, all `MOV`/`ADD`/`SUB` (not just reads), flagging it
  as the update/physics function rather than a renderer or one-shot
  init/query.
- Confidence: strict for control-flow/field-role structure (same
  `for (local_24=0; local_24<*(int*)0x9670; ...)` loop over the same table
  confirmed in `FUN_1000_ab20`); field semantics inferred from usage, not
  struct-labeled.
- Extended field map (offsets relative to the `ds:8854` table base,
  36-byte stride, continuing from the `FUN_1000_ab20` entry above):
  | Offset | Field (inferred) |
  |---|---|
  | `+0x0E` | heading (yaw) — matches the "orientation param" role already seen in `ab20`'s draw call |
  | `+0x12` | turn-rate accumulator; `heading += (turnRate>>3)/frameDivisor` each frame |
  | `+0x10` | pitch/bank angle — clamped/sign-flipped near ±0x4000 (90°) boundaries |
  | `+0x06,+0x08` / `+0x0A,+0x0C` | two 32-bit position accumulators, advanced via `FUN_1000_d2c0`/`FUN_1000_d2d8` (cos/sin-shaped call pair) of the heading — the actual per-frame position integration |
  | `+0x04` | altitude — advanced the same way using the pitch angle |
  | `+0x1A` | countdown timer; at 0, re-scans for nearest target (mirrors the threat table's `threatTimer` pattern) |
  | `+0x1E` | cached bearing, written via `FUN_1000_8abf(x,y)` — likely feeds engine-sound stereo panning |
  | `+0x14` | type index — cached into scratch global `ds:6334` at the top of each iteration, used to index the `ds:49CA` (32B stride) type/sprite table |
- **Key finding**: there is no simple stored "current speed" scalar read
  anywhere in this function. The magnitude driving position integration
  each frame is combined with a computed target waypoint
  (`local_32/local_38/local_3c`, built from branching logic that looks like
  formation-position-offset or AI-steer-toward-target-point logic).
  **Aircraft motion here is recomputed from steering logic every frame, not
  read from a static per-record velocity field.** This is a stronger, more
  literal confirmation of the project's founding "dynamic,
  continuously-simulated world" hypothesis than a static field would have
  been.
  **CORRECTION**: originally attributed this magnitude to
  `FUN_1000_fd6a()` (a call assigned to `local_22` right before this
  block), speculating it was a type-indexed base-speed table lookup.
  Decompiling `FUN_1000_fd6a` directly disproved that — it's a `void`
  busy-wait/spin loop (`for (uVar1 = in_CX & 0xff; uVar1 != 0; uVar1--) {}`,
  likely CPU-speed calibration or frame-pacing), not a value-producing
  function. Ghidra's decompiler assigning its non-existent "return value"
  to `local_22` is a mis-analysis artifact (the callee never writes `AX`
  meaningfully) — `local_22`'s real source is whatever earlier code in
  `FUN_1000_5828` last set `AX`, not traced yet. Doesn't affect the
  control-flow-confirmed finding below (unconditional per-frame heading/
  position writes) — only the speculative "where the magnitude ultimately
  comes from" claim, now retracted.
- **Recommended live test** (supersedes "cycle ordnance" as the validation
  target): the heading field `+0x0E` is written **unconditionally every
  call** for any active, non-just-spawned entity — no player-input
  dependency. Translated raw `ds:`-relative addresses (NOT Ghidra GUI
  addresses — these are for the live DOSBox-X `BPM <freshly-read-DS>:<off>`
  breakpoint, per the physical-address formula; do not confuse with the
  `2576:xxxx` Ghidra-internal aliased addresses used for static navigation):
  - entity 0 heading: `ds:8862`
  - entity 1 heading: `ds:8886`
  - entity 2 heading: `ds:88aa`
  - entity 3 heading: `ds:88ce`
  - entity 4 heading: `ds:88f2`
  (general formula: `0x8862 + N*0x24` for entity index N)
  Procedure: break into the debugger, **read `DS` fresh** (do not reuse an
  old session reading — see the DS-instability caveat above), arm
  `BPM <DS>:8862` (or try a few entity indices if slot 0 is inactive), zero
  player input, confirm it fires purely from background AI simulation while
  any wingman/bogey is on screen. This is the strongest, most directly
  code-traced live-validation candidate found so far.

## g_simObjects equivalent — FOUND (render-loop trace), speed field not yet located
- Source: manual GUI decompile of `FUN_1000_ab20` (Ghidra CodeBrowser,
  EGAME.EXE), following up on the `Speed ` string block found at `2000:549e`
  (see below) — this function was the 36-byte-stride candidate flagged
  earlier by `find_entity_loop_candidates.py`.
- Confidence: strict for table location/stride/count; field offsets are
  usage-inferred (not struct-labeled), speed field NOT identified.
- Notes: `FUN_1000_ab20` is a **render loop**, not an update/physics loop —
  calls `FUN_1000_c667`/`FUN_1000_c988` (world→screen projection, same
  signature pattern as the threat-table code), `FUN_1000_cb52` (sprite/HUD
  item blit — first arg is a draw-op id, sometimes a literal like `5`/`0xe`/
  `0x15`, sometimes computed via an index into a **32-byte-stride table at
  `ds:49CA`** — likely an aircraft-type→sprite lookup), and `FUN_1000_fcb0`
  (looks like numeric-readout formatting). The loop bound is `ds:9670`
  (`g_simObjectCount` equivalent), table base **`ds:8854`**, stride `0x24`
  (36, confirms `g_simObjects` shape). Field offsets resolved from the
  literal cluster (`iVar3 + -0x77ac` .. `-0x7794`, all 16-bit wraparound
  from the same base):
  | Offset | Field (inferred) |
  |---|---|
  | `+0x00,+0x02` | X, Y world position |
  | `+0x04` | Z/altitude (compared `< 999`) |
  | `+0x06,+0x08,+0x0A,+0x0C` | 4 params fed to the draw call — plausibly orientation |
  | `+0x14` | index into `ds:49CA` (32B stride) — aircraft-type/sprite selector |
  | `+0x16` | flags byte: bit `0x02`=active, bit `0x20`=large/hidden (same pattern as threat table) |
  | `+0x18` | nonzero check (id/alive?) |
  No field read here maps to "speed" — expected, since a sprite-draw routine
  only needs position + orientation, not velocity. Untouched offsets in the
  36B record (`0x10`,`0x12`,`0x1A`-`0x22`) are the remaining candidates for
  speed/heading-rate/whatever else `SimObject` carries.
- **EGAME.EXE string-anchor context**: the `Speed ` string at `2000:549e`
  sits in a contiguous block with `Primary Target`/`Secondary Target`/
  `Civilian or Friendly`/`Radio Beacon`/`Radio beacon bearing`/`Drop
  supplies now`/`Missile Lock`/`Range ` — reads as a **radar-contact
  info-card** (shown when a contact is locked), not a permanent HUD
  airspeed tape. So "Speed" here is almost certainly *the locked contact's*
  speed, tied to `g_simObjects`, not the player's own airspeed. Manual GUI
  check confirmed **zero references** to `2000:549e` (Search → For Scalars
  and the reference graph both empty), consistent with the earlier
  raw-byte-scan finding — matches the standing "table-driven indexed
  string-print" hypothesis, not a literal-load site.
- Why the stride/count immediate-scanner missed the actual info-card
  renderer: it required `0x24` (stride) *and* the count co-occurring in the
  same function. A single-selected-record "draw contact info card" routine
  would only ever multiply by `0x24` once (for the tracked index) and never
  reference the loop-bound count at all — invisible to that filter.
- Next step: in the Ghidra GUI, `2000:8854` (table base) and `2000:9670`
  (count) → right-click → References → Show References To, to get the full
  set of functions touching this table directly from Ghidra's own analysis
  (broader than the heuristic scanner) — should surface the info-card
  renderer the loop-based scan missed, which is the one likely to actually
  reference/print `2000:549e`.
  **Superseded — see the addressing-model entry immediately below: the real
  address to do this at is `2576:30f4`, not `2000:8854`.**

## Ghidra segment-aliasing addressing model — SOLVED (all prior ds: candidates need retranslation)
- Source: live GUI observation — Navigation → Go To `2000:8854` landed at
  `2576:30f4` instead (not an error page / not found); same for `2000:9670`
  -> `2576:3f10`.
- Confidence: strict — verified by exact linear-address arithmetic, cross-
  checked against two independent redirects.
- Notes: real-mode `segment:offset` addressing is non-unique
  (`linear = segment*16 + offset`); Ghidra's Go To computes the linear
  address for whatever segment:offset you type and jumps to wherever that
  byte is *actually* owned in its address-space model, which need not be
  the segment name you typed. Checked: `0x2000*16 + 0x8854 = 0x28854`;
  `0x2576*16 + 0x30f4 = 0x28854`. Same linear byte — confirms this is
  correct aliasing, not a broken lookup.
- **Root cause**: Ghidra's `2000`-named block (holding the EGAME.EXE string
  pool, confirmed by the `549e` Speed string landing there directly with no
  redirect) is only `0x5760` bytes long. Any `ds:`-relative literal at or
  past offset `0x5760` isn't actually inside `2000` at all — it silently
  aliases into the next block Ghidra separately named `2576`, at
  `real_offset = ds_offset - 0x5760`. Verified against both known redirects
  (`8854-0x5760=0x30f4` ✓, `9670-0x5760=0x3f10` ✓).
- **Practical rule going forward**: for any `ds:XXXX` literal found via
  decompile, if `XXXX >= 0x5760`, the real Ghidra GUI address is
  `2576:(XXXX-0x5760)`; if `XXXX < 0x5760`, `2000:XXXX` is directly correct,
  no translation needed (this is why `549e` and the not-yet-confirmed
  `49CA` sprite-table literal worked/should work with no redirect).
- **Retranslated table of all prior ds: candidates** (untranslated value ->
  real GUI address):
  - `80AC` (g_planeTable/threat table base) -> `2576:294c`
  - `6646` (threat count) -> `2576:0ee6`
  - `8798` (tracked-threat index) -> `2576:3038`
  - `9740` (secondary/simObj-related scalar) -> `2576:3fe0`
  - `B936` (mission block, already live-confirmed via a different route
    earlier in the project) -> `2576:61d6`
  - `8854` (g_simObjects base) -> `2576:30f4` (confirmed live)
  - `9670` (g_simObjects count) -> `2576:3f10` (confirmed live)
  - `49CA` (sprite/aircraft-type table) -> `2000:49ca` (untranslated, < threshold)
  - `549e` (Speed string) -> `2000:549e` (confirmed live, no translation)
- Next step: use these corrected addresses for all further GUI reference-
  tracing (e.g. `2576:30f4` / `2576:3f10` -> References -> Show References
  To, per the still-open action item above) and for any future live
  breakpoint physical-address math — this also means the earlier "DS
  instability" live-test caveat needs re-examination: what we thought was
  `DS` drifting mid-session (`3BB7` -> `0A72`) might instead (or also)
  reflect this same block-aliasing effect depending on how the physical
  translation was done, worth reconsidering once GUI work settles.
- Next step: point Phase 3 write-breakpoints at candidate structures using
  known struct sizes as a search shape in Ghidra (36B x20, 16B x74, 24B
  x12, 8B x8 fixed-stride tables in EGAME.EXE's data segment) rather than
  further string-xref work (which already hit its ceiling per the
  EGAME.EXE entry above). Also worth a direct empirical test of the
  `0x03C5F0` "generic input processed" byte against `g_timerTickByte`/
  `g_frameSyncPending` from `egtick.c`: set a breakpoint and check whether
  it changes on a fixed timer cadence even with **zero player input**
  (the control captures at record 7-9 suggest not, since those went to a
  full stop with the plane throttled to zero — worth re-testing at
  cruise throttle with hands off controls to be sure).

## Flap-state candidates (parity correlation) — Phase 3 target list, NOT YET breakpoint-confirmed
- Source: `scripts/trial_flaps.py`, live session 2026-08-13. 10 flap toggles
  via the automation socket (`scripts/dosbox_automation.py` — precise
  DOWN/UP delivery, emulator kept running between toggles, unlike
  AppleScript keystrokes) interleaved with 11 tagged debugger captures
  (`tag_capture.py`'s segments 3000h/4000h, 1s settle after each toggle
  before capturing).
- Offset: segment 4000h in all 10 cases (physical = 0x040000 + off)
- Confidence: weak-but-strong-signal — each byte took **exactly two**
  values across all 11 samples and those values partitioned **perfectly**
  along toggle parity (even/odd toggle count = the two physical flap
  positions). Given the entity-churn noise documented elsewhere in this
  file, a random unrelated byte lining up that cleanly across 11 samples
  by chance is very unlikely — but this is still correlation, not a
  confirmed write site. **Not yet run through `breakpoint_capture.py`.**
- Notes, in three groups by apparent structure:

  **1. Repeating 3-byte field, two instances 24 bytes apart** (24B matches
  `g_projectiles`' documented `Projectile` struct size from FINDINGS.md's
  f15se2-re-derived entity table — plausible but unconfirmed structural
  coincidence, could easily be an unrelated 24B-stride table instead):
  ```
  4000:1312  parity0=0xE8  parity1=0x10
  4000:1313  parity0=0x00  parity1=0x01
  4000:1314  parity0=0x36  parity1=0x66
  4000:132A  parity0=0xE8  parity1=0x10   (+0x18 from the block above)
  4000:132B  parity0=0x00  parity1=0x01
  4000:132C  parity0=0x36  parity1=0x66
  ```
  Reading 1312/132A as little-endian words with 1313/132B: 232 -> 272
  (+40). Byte 1314/132C: 54 -> 102 (+48). Two identical deltas in two
  slots reads like a per-entity physics term (drag/lift coefficient,
  control-surface deflection) recomputed identically for two tracked
  entities — plausible for "player's own aircraft, tracked twice" (e.g. a
  live copy + a HUD/display copy) but not confirmed.

  **2. A clean single-bit flag** — the strongest single candidate:
  ```
  4000:23BC  parity0=0x00  parity1=0x02
  ```
  Only bit 0x02 changes; nothing else about the byte does. This is
  exactly the shape you'd expect from a boolean control-surface-state
  flags byte (gear/flaps/bay-doors/autopilot are all binary toggles per
  the Quick Reference Card) — the highest-priority candidate to confirm
  first.

  **3. Two more single-value-pair bytes**, plausibly downstream
  recalculations (flap deployment changing drag → airspeed/limit terms)
  rather than the state flag itself:
  ```
  4000:3ABE  parity0=0xA8  parity1=0x7E
  4000:3ABF  parity0=0x0C  parity1=0x09     (word 1312/132A pattern doesn't repeat here — different field)
  4000:56F2  parity0=0x78  parity1=0x5A     (120 -> 90, a plausible "reduced speed/G-limit while flaps down" shape)
  ```
- Next step: confirm with `scripts/breakpoint_capture.py --seg 4000 --off 23BC --action "toggle flaps"` first (cleanest candidate, single bit) — arm it, tap the flaps key once, confirm it fires and check the write instruction's code location. Then repeat for the other 4000h offsets above to see which, if any, share a caller (would confirm they're all written by the same flap-handling routine) versus firing from unrelated code (would argue for coincidental correlation on some of the group-1/group-3 bytes despite the clean 2-value pattern).

## Cross-key comparison: flaps vs brake vs ordnance-select — SIGNIFICANT UPGRADE to the above
- Source: `scripts/trial_toggle_correlation.py` (generalizes `trial_flaps.py` to
  any key), live session 2026-08-13. Same method as above, repeated for
  key `0` (brake, label `brake`) and key `space` (per the Quick Reference
  Card, `space` is **SELECT ORDNANCE** — cycling the selected weapon type —
  not `BAY DOORS`, which is key `8`; labeled `ordnance_select`). 10 taps +
  11 tagged captures each, results saved to
  `dumps/toggle_correlation_{flaps,brake,ordnance_select}.json` so runs are
  directly comparable by physical address.
- Confidence: correlation-only per key (same caveat as above — not yet
  breakpoint-confirmed), but the *cross-key pattern* is a much stronger
  signal than any single run, since it distinguishes control-specific vs.
  shared vs. generic-input bytes directly instead of by inference.
- Result table (baseline/active value per trial; `-` = not a parity-perfect
  candidate in that trial):

  | addr | flaps (0/1) | brake (0/1) | ordnance_select (0/1) |
  |---|---|---|---|
  | 4000:1312 | 232/16 | 232/248 | - |
  | 4000:1313 | 0/1 | - | - |
  | 4000:1314 | 54/102 | 54/102 | - |
  | 4000:132A | 232/16 | - | - |
  | 4000:132B | 0/1 | - | - |
  | 4000:132C | 54/102 | - | - |
  | 4000:23BC | 0/2 | 0/8 | - |
  | 4000:3ABE | 168/126 | - | - |
  | 4000:3ABF | 12/9 | - | - |
  | 4000:56F2 | 120/90 | - | - |

- **`4000:23BC` is now a strong candidate for an actual control-surface
  status bitmask**, not just "a clean single-bit flag" in isolation: same
  baseline (0) in both real trials, but flaps sets bit `0x02` and brake
  sets bit `0x08` — two controls, two distinct non-overlapping bits at the
  *same* address, exactly the shape of a flags register with one bit per
  physical toggle (gear/autopilot/bay-doors/flaps/brakes per the QRC's
  Toggles row). Ordnance-select touches neither bit — consistent with
  ordnance selection having no aerodynamic/control-surface effect, as
  expected.
- **`4000:1314` is shared by flaps and brake with the identical transition
  (0x36→0x66) but untouched by ordnance-select.** Not control-specific
  (same value change either way), but also not a fully generic "any key
  processed" flag (`breakpoint_capture.py`'s known `3000:C5F0` byte fires
  on ordnance/bay-doors/cannon actions too, generic across the board) —
  this one is silent on ordnance-select. Best current hypothesis: a
  "flight-model/control-surface state changed" dirty flag or generation
  counter, set whenever something that affects aerodynamics changes,
  regardless of which control did it. Needs a third aerodynamically-real
  control (e.g. gear, key `6`) to test whether it keeps firing — if gear
  also produces 0x36→0x66 here, that further supports "aero-dirty flag";
  if not, it may just be a flaps/brake-specific coincidence with only two
  data points so far.
- **`4000:1312` is ambiguous**: same baseline (232) in both trials but a
  *different* active value per control (16 for flaps, 248 for brake) —
  either one field encoding "which control last changed" (an ID/enum, not
  a plain flag) or two unrelated fields that happen to share a resting
  value. Untouched by ordnance-select either way.
- **`4000:132A-132C` (the 24-byte-offset duplicate), `4000:3ABE-3ABF`, and
  `4000:56F2` are flaps-only** — brake and ordnance-select leave them
  alone. These are the best candidates for something genuinely
  flaps-specific (e.g. flap deflection/drag term) rather than a
  general "a control surface changed" signal, though group-1's 24-byte
  duplicate-stride coincidence with `Projectile` (noted above) is still
  unexplained.
- **Ordnance-select (space) produced zero parity-perfect candidates** in
  segments 3000h/4000h. Read cautiously: `SELECT ORDNANCE`'s own icon on
  the QRC implies cycling through more than two weapon states, and this
  method's parity filter only detects a clean two-value alternation — a
  real ordnance-select byte cycling through 3+ values across 10 taps would
  never pass this filter regardless of whether it exists. This result
  supports "ordnance selection has no aerodynamic-state footprint in this
  region" but does NOT yet support "ordnance selection touches nothing in
  memory" — that needs a cycle-aware method (track byte value against
  taps-mod-N for candidate N, not just even/odd), not this binary-parity
  one.
- Next step: (1) run `breakpoint_capture.py` against `4000:23BC` first —
  if confirmed as a real flags byte, the exact write instruction's
  surrounding code should reveal the full bit layout (which bit is
  gear/autopilot/bay-doors/etc, confirmable by then testing those keys
  too) in one disassembly read, cheaper than further statistical trials.
  (2) Run this same `trial_toggle_correlation.py` method against gear
  (key `6`) and autopilot (key `7`) to extend the table and test the
  `4000:1314` "aero-dirty-flag" hypothesis. (3) For ordnance-select
  specifically, a cycle-aware variant (label each sample by `taps % N` for
  N=3/4 instead of `% 2`) is needed before ruling anything in or out.

## Pre-flight control sweep (round 2): bay doors, chaff, flare, jammer — 2026-08-13
- Source: `scripts/trial_toggle_correlation.py`, same live session, extending
  the flaps/brake/ordnance-select sweep above to the rest of the QRC's
  Toggles and Defenses rows. Goal: keep mapping keystrokes to addresses and
  use each new control as a further filter on the hypotheses above (which
  bytes are control-specific vs. shared-aerodynamic vs. generic-input).

### Bay doors (key `8`) — CONFIRMS the `4000:23BC` bitmask hypothesis
```
4000:108C  parity0=0x00  parity1=0x18
4000:23BC  parity0=0x00  parity1=0x04
4000:51D8  parity0=0x00  parity1=0x18
```
- `4000:23BC` now has **three** independently-confirmed, non-overlapping
  bits across three separate live trials: flaps=`0x02`, brake=`0x08`, bay
  doors=`0x04`. Same resting value (0) every time. This is no longer "a
  clean single-bit flag that might be flaps" — it's a strong candidate for
  the actual multi-control status/flags register, and the highest-priority
  breakpoint target in this whole file.
- Bay doors does **not** touch `4000:1312`/`1314` (the flaps+brake shared
  pair) — refines that hypothesis: `1312`/`1314` tracks something specific
  to controls that change the *flight model* (flaps, brakes both affect
  lift/drag/speed), not just "any physical toggle." Bay doors changes
  drag too in real aircraft, but evidently isn't modeled that way here (or
  is modeled through a different byte this scope doesn't cover) — worth
  keeping in mind, not fully resolved.
- `4000:108C` and `4000:51D8` both show the identical `0x00->0x18` (24)
  transition, echoing the flaps trial's own duplicate-pair pattern
  (`4000:1312`/`132A`, 24 bytes apart) — plausibly a primary state +
  display/HUD copy of bay-door position, unconfirmed.

### Chaff (key `2`) and flare (key `1`) — clean negative results, as expected
- Both returned **zero** parity-perfect candidates in segments 3000h/4000h.
- Consistent with the pre-trial prediction: chaff/flare are Defenses on the
  QRC, i.e. consumable countermeasures (each press likely decrements a
  remaining-count) rather than a two-state toggle. A decrementing counter
  takes many different values across 10 taps, not two, so it can never
  satisfy this method's "exactly 2 values, matching parity" filter even if
  a real chaff/flare-count byte exists in this region. Absence of evidence
  here is not evidence of absence for these two — a different method
  (track byte value against a monotonically-changing expected count,
  rather than parity) would be needed to actually find them.

### Jammer (key `3`) — FAILED, not a real result; methodology + environment notes
- **First attempt**: only 3/11 samples captured (repeated debugger
  break-in/capture failures mid-run) yet still printed **823
  "candidates."** This exposed a real bug in `trial_toggle_correlation.py`'s
  `correlate()`: with only 1-2 samples in a parity group, "constant within
  group" is trivially true (nothing to contradict it), so *any* byte that
  merely differs between two arbitrary captures gets reported — exactly
  the entity-churn noise this whole method exists to filter out (see the
  "Diff-noise root cause" entry above). **Fixed**: added
  `MIN_SAMPLES_PER_GROUP = 3` — the function now refuses to report
  candidates at all unless both parity groups meet that bar. The 823-entry
  result was discarded, not logged as data.
- **Retry attempt**: failed before even completing the baseline capture.
  Live diagnosis (manual AppleScript probing, not just script retries):
  the dosbox-x process and the automation socket were both still healthy
  (port 6000 answered, SDL window rendered the cockpit/HUD/weapons panel
  normally), but the *debugger's own text console* (in the iTerm2 session
  driving it) was wedged — its on-screen input line showed several
  `MEMDUMPBIN ...` commands concatenated together with no newline between
  them (`...4000:0 10000MEMDUMPBIN 4000:0 10000MEMDUMPBIN 3000:0 10000...`)
  and a `*** Debugger command not recognized` message, and neither further
  typed text, nor 80× Backspace, nor a real Return keystroke (via System
  Events, not just `iTerm2 write text`) changed it at all. The console
  was not accepting input by any channel tried.
- **Likely cause**: cumulative stress, not a single bad command. By this
  point in the session, the automation had driven roughly 90 debugger
  break-in/resume cycles back-to-back across 7 prior trials (flaps, brake,
  ordnance-select, chaff, flare, bay doors, plus the failed jammer
  attempts) with essentially no rest between them. Whether this is an
  iTerm2/AppleScript-side desync, a DOSBox-X debugger console bug under
  sustained rapid break-in/resume, or terminal scrollback/redraw
  corruption was not root-caused — flagged for whoever picks this up next
  rather than guessed at further.
- **Not attempted**: ECM (key `4`) and decoy (key `5`) were never run —
  blocked by the same console hang.
- **Recovery not yet done**: the debugger console was left in this wedged
  state; a DOSBox-X restart (or some other console-reset method, TBD) is
  needed before resuming this sweep. The game itself was left running
  normally otherwise.

### Round 2 summary table
| control | key | result |
|---|---|---|
| Flaps | 9 | 10 candidates, incl. `4000:23BC` bit `0x02` |
| Brake | 0 | 3 candidates, incl. `4000:23BC` bit `0x08` |
| Bay doors | 8 | 3 candidates, incl. `4000:23BC` bit `0x04` |
| Ordnance select | space | 0 (likely a 3+-state cycle, method-blind) |
| Chaff | 2 | 0 (expected — consumable, not a toggle) |
| Flare | 1 | 0 (expected — consumable, not a toggle) |
| Jammer | 3 | FAILED — debugger console hung, no valid data |
| ECM | 4 | not attempted |
| Decoy | 5 | not attempted |

- Next step: restart/reset the debugger console before attempting
  ECM/decoy or the still-pending gear (`6`)/autopilot (`7`) trials from
  round 1's next-step list. Then run `breakpoint_capture.py --seg 4000
  --off 23BC` — with three independent controls now pointing at the same
  address, this is the single highest-value confirmation left to do.

## kp8/kp2 pitch key mapping — CORRECTED (was backwards)
- Source: live observation, 2026-08-14. `scripts/trial_hold_release_correlation.py`
  labeled `kp8` as "pitch_up" per FINDINGS.md's untested QRC-inferred guess.
  Held live, the plane crashed almost immediately from ~9000ft - user
  confirmed `kp8` actually noses the aircraft **down**, not up.
- Confidence: strict - direct live behavioral confirmation (two separate
  crashes from held/effectively-held kp8 input).
- Correction: `kp8` = nose down, `kp2` = nose up (inferred as the opposite of
  the confirmed key, not yet independently tested). Update any future trial
  labels accordingly - `trial_altitude.py`/`trial_takeoff.py` used the same
  unconfirmed 91/84 mapping and should be re-checked before trusting their
  prior output for direction (their climb/descend *magnitude* data is likely
  still fine, just the up/down labels may be swapped).
- **Practical lesson for held-key trials on this control**: a multi-second
  continuous hold (the original hold-release correlator design) is enough to
  crash the plane, apparently from *any* altitude tried so far (9000ft
  included) - likely compounded by debugger-resume stalls silently
  extending real hold duration well past the nominal setting (see the
  automation-pipeline entry below). Switched to short discrete taps
  (`ml.press_key_code`, a single AppleScript keystroke per tap, ~0.5s apart)
  for this control going forward - much lower risk, see the trend-correlation
  result immediately below for what that method found.

## Automation-socket bind mystery — UNRESOLVED, flagged for whoever picks this up next
- Source: live troubleshooting, 2026-08-14.
- Notes: `dosbox_automation.py`'s TCP automation port (6000) bound fine when
  dosbox-x was launched with **no** `-conf`/`-savedir` args (picking up the
  generic `~/Library/Preferences/dosbox-x.conf`, which already had
  `automation port = 6000` under `[sdl]`). After relaunching with this
  project's proper `-conf f19-dosbox.conf -savedir .../f19/saves` (needed
  for `hostkey=ctrlalt` and `savestate.py` compatibility) - even after adding
  the identical `automation port = 6000` line to `f19-dosbox.conf`'s `[sdl]`
  section - the socket refused connections and **zero** `AUTOMATION:`-prefixed
  lines appeared in `logs/f19-dosbox.log`, which per `src/misc/automation.cpp`
  should be impossible unless `AUTOMATION_Init` returned early on `port <= 0`
  (i.e. the conf value wasn't actually reaching the property as 6000) -
  every other explanation checked out fine: binary has the feature compiled
  in (`strings` confirms both the property help text and the
  `C_SDL_NET`/`C_SDL2_NET`-gated real implementation, not the stub), the conf
  line itself is byte-clean (checked via `od -c`, no hidden characters), and
  no stray leftover process was already holding the port.
- Not root-caused - possibilities not yet checked: whether `-conf` also
  implicitly loads/merges a primary default conf in some order that could
  matter even for matching values, whether `Property::Changeable::OnlyAtStart`
  interacts oddly with multi-conf loads, or something about `-savedir`
  specifically (untested in isolation - only ever tried with both `-conf` and
  `-savedir` together).
- **Workaround used instead**: skip the automation-socket key delivery
  entirely for this control; `memdump_loop.press_key_code()` (a single plain
  AppleScript "key code" keystroke, no socket) delivered the taps for the
  trend-correlation result below just fine. `dosbox_automation.py` remains
  necessary for true held-key trials (hold-release correlator) and precise
  DOWN/UP timing (flaps/brake/bay-doors trials) - those can't use this
  workaround - so this is worth actually root-causing before the next
  hold-release attempt, not just routing around indefinitely.

## Pitch (kp8, confirmed nose-down) quick-tap trend correlation — promising cluster, NOT YET breakpoint-confirmed
- Source: ad-hoc live trial, 2026-08-14 (6 quick taps of `kp8` via
  `ml.press_key_code`, ~0.5s apart, one tagged capture after each,
  reconstructed from `dumps/test_cases.jsonl` and analyzed with
  `trial_decrement_correlation.py`'s `correlate(direction="either")`).
  Level flight, ~9175ft, no crash this run (taps only, no sustained hold).
- Confidence: weak-but-structurally-suggestive - large raw candidate count
  (2838 byte + 4431 word) as expected from `direction="either"` against a
  live, already-churning entity model (see "Diff-noise root cause" above) -
  most of this is certainly noise. But one **tight, correlated cluster**
  stands out from the noise floor:
  ```
  physical range 0x03C68B-0x03C6BB (~48 bytes), ~15 addresses, ALL smoothly
  monotonic across all 6 samples (not flat-then-random like isolated noise):
    0x03C68E (down): [44, 40, 28, 12, 10, 9, 8]
    0x03C692 (down): [179, 178, 173, 167, 166, 165, 165]
    0x03C696 (down): [102, 96, 77, 42, 37, 35, 33]
    0x03C69A (down): [72, 68, 58, 36, 32, 30, 29]
    0x03C69E (down): [13, 13, 10, 6, 5, 4, 4]
    0x03C6A3 (up):   [1, 1, 1, 2, 2, 2, 2]
    ...(full list in this session's output; word-level pairs at the same
    offsets confirm the same smooth trend when read as 16-bit values)
  ```
  Many neighboring bytes moving together with smooth (not erratic)
  trajectories, several decelerating toward an asymptote rather than jumping
  randomly, is the qualitative signature this session's whole search has
  been looking for under FINDINGS.md's "what's still unmapped" section -
  position/attitude/velocity state integrated every frame, not a single
  isolated flag. This is the best lead so far for the player's own
  continuous flight-dynamics state (as opposed to `g_simObjects`, which is
  confirmed to be *other* aircraft only).
- **Real confound, not yet controlled for**: sample 3's capture needed a
  retry after a debugger-pipeline hiccup, so more real/simulated time elapsed
  between taps 2 and 3 than between the other consecutive pairs - the
  larger jump at that specific step in several of these sequences may
  partly reflect elapsed time rather than tap count. Doesn't invalidate the
  monotonic-trend finding itself (direction and smoothness hold regardless)
  but means don't over-read the *magnitude* pattern yet.
- **Not controlled against background churn**: no zero-tap control run was
  taken in this same live window to confirm this cluster stays flat with
  no input - given the entity-churn caveats elsewhere in this file, that's
  the first thing to check before trusting this as pitch-specific rather
  than just "something else was live and ticking during this window."
- Next steps: (1) a no-tap control trial (same capture cadence, zero kp8
  taps) to rule out background churn; (2) if it stays flat under control,
  `breakpoint_capture.py` against one of these offsets (e.g. `0x03C696`) to
  get the exact writing instruction and settle this for real; (3) cross-check
  against the HUD altitude/pitch-ladder reading via screenshot at each tap to
  identify which specific field this is.

## Automation-socket bind mystery — RESOLVED: was never a DOSBox-X bug, `dosbox-x` on PATH shadows the patched binary
- Source: user correction, 2026-08-14/15.
- Root cause: `/opt/homebrew/bin/dosbox-x` (a stock Homebrew build, confirmed via
  `strings` to have **no** `automation port` support at all) sits earlier on
  `$PATH` than the actual patched binary at
  `/Users/robmaxwell/projects/dosbox-x/src/dosbox-x`. Every launch command in
  this session that typed the bare word `dosbox-x` silently ran the wrong,
  unpatched binary - not a conf-loading-order bug, not a `-set`/`-conf`
  interaction, not a race condition. All of that investigation (the `-set`
  syntax deep-dive, the primary-vs-additional-conf theory, the `-savedir`
  suspicion) was chasing a phantom; the fix is simply **always invoke the
  full path**, never the bare `dosbox-x` command, in this project.
- Confirmed working launch: `/Users/robmaxwell/projects/dosbox-x/src/dosbox-x`
  (`-savedir <path>` is safe to add back - it was never actually the problem
  either). Terminal.app vs iTerm2 was also a red herring for this specific
  issue - the automation port worked under iTerm2 all along once the correct
  binary was used; the separate debugger-console flakiness (`[warn] debugger
  still showing as open after resume timeout`) happens under both terminal
  apps and remains unresolved (see below), unrelated to which binary or
  terminal is in use.
- **Lesson for any future script/doc in this project**: never reference
  `dosbox-x` by bare name (in scripts, docs, or suggested commands) - always
  the full `/Users/robmaxwell/projects/dosbox-x/src/dosbox-x` path, since
  `$PATH` will silently resolve to the wrong build otherwise with no error of
  any kind.

## Debugger-console resume flakiness — still unresolved, now confirmed terminal-independent
- Source: repeated observation across this whole session, both iTerm2 and
  Terminal.app launches, both before and after the PATH/binary fix above.
- Notes: `memdump_loop.py`'s break-in/resume cycle reliably prints
  `[warn] debugger still showing as open after resume timeout` on most
  captures (self-resolves after 1-4 retries almost every time, occasionally
  needs a manual `resume_execution()` call to unstick - see the
  `pitch_up`-hold-trial crash earlier in this file for the one case where it
  stuck long enough to matter). Since this happens identically under both
  terminal apps, the earlier "switch to Terminal.app for reliability"
  recommendation was based on an incorrect diagnosis - don't repeat it.
  Root cause still unknown; `wait_for_debugger_closed`'s 3s timeout may
  simply be too short under current system load, or the F5 keystroke
  delivery itself is intermittently lossy regardless of which terminal sent
  it. Not blocking (captures still succeed after retries in the large
  majority of cases), but worth a real fix before scaling up to longer
  automated trial sequences.

## Pitch (kp8, nose-down) quick-tap trend correlation, retry #2 via automation socket — large structured cascade found
- Source: `scripts/trial_decrement_correlation.py --mode tap --key kp8
  --direction either --repeats 6 --settle 0.75`, live session 2026-08-15,
  **delivered entirely via the automation socket** (`dos.tap()`, 0.05s
  press) - no AppleScript keystroke involved anywhere in this run, per
  explicit project requirement (AppleScript key delivery is no longer
  permitted for live testing; capture-pipeline AppleScript - debugger
  break-in/resume - is a separate, still-necessary mechanism and unaffected
  by this rule). Level flight ~10600ft, no crash.
- Confidence: weak-but-highly-structured - `direction="either"` against a
  live, churning entity model again produces a large raw count (2370 byte +
  3568 word candidates), most of which is presumably noise per the
  "Diff-noise root cause" entry above. But a large contiguous block from
  roughly `0x03C6A5` through past `0x03C7xx` (hundreds of bytes, dozens of
  candidate addresses at a fairly regular stride) shows a **strikingly
  consistent shape**: smooth ramp for the first 3-4 taps, then a hard
  plateau (identical value) for the remaining taps, e.g.:
  ```
  0x03C6C6 (up):   [103, 105, 107, 108, 108, 108, 108]
  0x03C6CE (up):   [126, 128, 130, 132, 132, 132, 132]
  0x03C6DA (down): [105, 103, 100, 100, 100, 100, 100]
  0x03C6FE (up):   [8, 13, 18, 19, 19, 19, 19]
  ```
  This is a materially different shape from the previous trial's smooth
  monotonic-no-plateau cluster at `0x03C68B-0x03C6BB` (still present here
  too, e.g. `0x03C6BA`/`0x03C6C2` show small monotonic moves) - the NEW
  plateau pattern spans more addresses and looks like it's hit a limit
  (max control deflection, a stall/G-limit clamp, or a derived value that
  stabilizes once the aircraft reaches a new steady attitude) rather than
  still-accumulating state.
- **Alternative hypothesis worth ruling out before trusting this as "the"
  player-state region**: the sheer number of co-moving addresses at a
  repeating stride is also consistent with a **per-entity table of
  values *derived from* player attitude** (e.g. relative bearing/range to
  each of several tracked contacts, recomputed from the player's new pitch)
  rather than the player's own raw state directly - `FINDINGS.md`'s
  `g_simObjects` notes already describe a per-entity cached-bearing field
  fed by a projection function, which is exactly the kind of thing that
  would shift in lockstep across many entities when the player's own
  attitude changes, without itself being the attitude scalar.
- Next steps (unchanged in kind from the previous entry, now with a bigger/
  better-defined target): (1) no-tap control trial to confirm this region
  stays flat with zero input; (2) `breakpoint_capture.py` against one
  plateau address (e.g. `0x03C6C6`) to get the writing instruction and
  settle player-state-vs-derived-projection directly from the disassembly;
  (3) HUD screenshot correlation per tap.

## f15se2-re re-clone + how F-15 SE2 actually stores player attitude — re-targets the search
- Source: `analysis/f15se2-re` re-cloned 2026-08-15 (the earlier clone referenced
  throughout this file was in a temp location and had been lost - it's now a
  permanent part of this repo, `git clone --depth 1
  https://github.com/neuviemeporte/f15se2-re.git`). Read `src/struct.h`,
  `src/egdata.h`, `src/egflight.c`, `src/stdata.c`/`stgen.c`, `src/comm.h`
  directly rather than re-deriving from memory of the earlier session.
- **Key finding: F-15 SE2's live player attitude is NOT a struct/table at
  all - it's scattered individual scalar globals**, unlike every other entity
  system in this codebase (`SimObject`, `MapTarget`, `Projectile`, `Particle`
  are all fixed-stride tables). From `egdata.h`:
  ```
  extern int16 g_ourHead;   // heading
  extern int16 g_ourPitch;  // pitch attitude
  extern int16 g_ourRoll;   // roll/bank attitude
  extern int16 g_viewZ;     // altitude-Z (immediately after, per egdata.h's
  extern uint16 g_altitude; //   declaration order - this project's decompiled
                             //   header order tracks the real map-file address
                             //   order, not arbitrary source grouping)
  ```
  This is a tight, ~3-6-scalar cluster (heading/pitch/roll each 2 bytes,
  probably contiguous or near-contiguous with altitude) - a completely
  different shape from a repeating multi-entry table. **This means our
  observed F-19 cascade (dozens of co-moving addresses at a regular ~8-byte
  stride) is structurally the WRONG SHAPE to be the player's own raw
  attitude state** - it's almost certainly something else. Two named
  candidates for what it actually is, both found in the same source pass:
  - `struct ViewSnapshot` (16B: `worldX`(4)/`worldY`(4)/`alt`(2)/`heading`(2)/
    `pitch`(2)/`roll`(2)) backing `g_viewSnapshotRing[]`, written every frame
    (`g_viewSnapshotRing[idx].pitch = g_ourPitch` in `egflight.c`) - a
    **ring buffer of recent camera/view snapshots**. A ring like this,
    sampled while pitch is actively changing, would produce exactly the
    "many nearby addresses, similar smooth-then-settling shape" pattern we
    saw, without any one entry being the canonical live state.
  - `struct VertexProj` (`in[121]`, 4B-stride pairs, part of the `VtxScratch`
    rendering-scratch region) - world→screen vertex projection for cockpit/
    HUD rendering, recomputed from the player's current orientation every
    frame. Also a strong candidate for "many co-moving derived values,"
    especially given our result's actual ~8-byte apparent spacing could be
    every-other-field of a 4B-stride table surviving the `direction="either"`
    filter.
  - Both interpretations agree with the caveat already logged in the
    previous entry (per-entity/derived values, not raw player state) - this
    just supplies two concrete, named, structurally-typed F-15 analogs
    instead of a generic guess.
- **Bonus, independently confirms the input-vs-state split
  `trial_hold_release_correlation.py` was built around**: `g_pitchInput`
  (declared far away from `g_ourPitch` - a genuinely separate variable, not
  part of the same cluster) is recomputed **every frame directly from the
  raw joystick axis** (`egflight.c:193`,
  `g_pitchInput = ((uint16)joyAxes[1] >> 4) - 8`), small range
  (roughly -16..32 after clamping/trim adjustments), then integrated into
  `g_ourPitch` via `pitchAngle = (g_pitchInput << 7) / g_frameRateScaling`
  and related trim/stall/gee-load adjustments. Confirms by name, not
  inference: **input and attitude-state really are two separate scalars at
  two separate addresses** in this engine family, exactly the distinction
  the hold-release correlator was designed to detect. `g_ourPitch`'s own
  range is large (boundary checks against `0x38e3`/`0x4001`/`0xc71d` - most
  of the signed 16-bit range), consistent with a full-circle fixed-point
  angle encoding, same style as the already-confirmed F-19 heading fields
  in `g_simObjects`.
- **Revised next step, supersedes the previous entry's plan**: stop treating
  the big cascade as the prize. Instead (1) re-examine the earlier, *much
  smaller* cluster from the first trial (`0x03C68B-0x03C6BB`, ~48 bytes) for
  a **3-consecutive-int16 (6-byte) triplet** that changes together but each
  takes only a handful of distinct values (matching heading/pitch/roll's
  shape) rather than a long smooth ramp; (2) if not there, a fresh narrow
  search near the already-confirmed player-adjacent addresses (mission
  block, mission-briefing coord/distance fields) specifically for a small,
  isolated 3-4-scalar group, not a table; (3) separately, hunt for a
  *second*, distant, small-range (~-16..32-ish) scalar that changes
  instantly on tap and doesn't accumulate - that would be F-19's
  `g_pitchInput` analog, the actual control-input signal for category D.
## Cross-trial re-analysis (no new flight) — found a much better-shaped candidate: 0x044455/0x044457/0x044459
- Source: offline re-analysis of the two already-captured pitch trials
  (`pitch_quicktap` 2026-08-14 AppleScript-delivered, `pitch_down_tap2`
  2026-08-15 socket-delivered) - no new capture needed, just re-ran
  `correlate(direction="either")` against both saved sample sets and
  intersected the results.
- Method: of 2838/2370 raw `either`-direction byte candidates per trial,
  1106 offsets appear in both, 949 agree on direction in both - itself
  decent cross-session reproducibility evidence for the broad cascade
  logged above. Then grouped the 949 by proximity looking specifically for
  **small isolated groups (2-3 fields, tightly packed, NOT part of the
  periodic 4/8-byte-stride table run)** - the shape predicted by the
  f15se2-re `g_ourHead`/`g_ourPitch`/`g_ourRoll` finding above, as opposed
  to a repeating table.
- **Found exactly one clean match**: `0x044455`, `0x044457`, `0x044459` -
  three fields at an even 2-byte stride, each reproducible in both
  independent sessions:
  ```
              trial1 (2026-08-14)          trial2 (2026-08-15)
  0x044455:   [78,78,77,74,72,71,71]  down  [52,51,51,50,50,49,48]  down
  0x044457:   [41,41,41,40,40,40,40]  down  [87,86,86,85,85,84,84]  down
  0x044459:   [3,4,4,7,9,10,10]       up    [5,11,21,29,35,44,45]   up
  ```
  Qualitatively exactly what you'd expect from a heading/pitch/roll triplet
  under sustained nose-down taps: one field (`0x044459`) moves a lot and
  accelerates (the commanded axis - pitch), the other two drift slowly in
  the opposite direction (secondary heading/roll coupling from the dive).
  The bytes *between* them (`0x044454`/`56`/`58`) are noisy/uncorrelated in
  both trials, not constant - so this isn't simply the low bytes of 3
  interleaved 16-bit words with idle high bytes; more likely 3 single-byte
  (or oddly-packed) fields in a small per-frame telemetry record, with
  unrelated fields interleaved. Segment `4000h` (physical `0x0444xx`),
  same live-state region as the already-confirmed `4000:23BC` control-flags
  byte.
- Confidence: weak-to-moderate - reproducible across two independent
  sessions (different day/altitude/key-delivery mechanism) with a
  qualitatively sensible shape, but still correlation, not a confirmed
  write site, and only 2 sessions isn't a large N.
- **This directly answers "do we need another flight test?" - no, not for
  this refinement.** Both existing captures already had the full segments
  3000h/4000h; the better candidate came from re-analyzing data already on
  disk, not from new keypresses. A flight test *would* be needed for the
  logical next step: `breakpoint_capture.py --seg 4000 --off 4459` (the
  most dynamic of the three) to get the actual writing instruction and
  settle this for real, or a zero-tap control run to rule out the
  possibility this triplet just happens to drift over elapsed time/frames
  regardless of pitch input.

- Also noted in passing, not yet acted on: `struct Game` at
  `GameComm + 0x120e` (`pilotIdx`/`pilotName`/`rank`/`medals`/`lastScore`)
  is a **direct, named analog for F-19's completely unmapped "mission score"
  category** - `GameComm` itself also has `bombDamage`/`gunHits` fields
  egame.exe writes for the debrief, and `setupDone`/`continueFlag` get
  reinterpreted in end.exe as `landingType`/`bailoutSurvived`. Worth its own
  dedicated pass later (same string-anchor method already used on
  `START.EXE`/`EGAME.EXE`, applied to `END.EXE`, per FINDINGS.md's existing
  "what's still unmapped" section) - not pursued this session, flagged only.
