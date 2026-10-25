# Tooling and history

Prior art, the toolchain, and the DOSBox-X save-state automation used for repeatable trials. Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Original FINDINGS.md introduction

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
section below (["World & dynamic-entity model"](memory-map.md#world--dynamic-entity-model--confirmed-via-f15se2-re-decompilation-2026-08-05)) for what this source
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
