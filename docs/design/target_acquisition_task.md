# Task: make trackcam target acquisition reliable (planner + autopilot)

Instructions for an implementing agent. Status: NOT STARTED. Written 2026-10-30. Untracked on purpose; the owner decides when to commit it.

## Goal

When the autopilot reaches an attack (Maverick or photo), the trackcam must lock the briefing record (primary or secondary), every time. The planner should guarantee the route makes that possible, and the autopilot should verify it and recover when it isn't.

## Read first (do not re-derive)

`docs/game/memory-map.md`, section **"Trackcam target selection: N, B, "/" ... (2026-10-30)"**. It holds the decoded rules and the evidence. Summary of what you rely on:

- The lock is ds:5526 (record index, -1 = none, bit 0x80 = search pending; -1 counts as "searching").
- `/` sets the view ahead, clears the lock to -1, switches to screen 0x13. The game then auto-picks the **nearest** candidate.
- `B` picks the **nearest candidate at least as far as the current lock**. Candidate = not destroyed (+8 bit 0x80), octagonal distance < 100 << (6 - night) units (6400 = 100 km day, 3200 = 50 km night), and within **+-45 deg (0x2000)** of heading + view offset (ds:9E8C). Air-to-ground (ds:9A36 == 2) also includes friendly records and scratch record 0. No candidate -> lock -1 -> next frame the nearest again (wrap).
- B does nothing unless screen ds:9740 is 0x13 or 0x19, ds:9A36 != 1, ds:94E0 bit 0x80 is clear and the cooldown ds:8B22 is 0.
- Distance = max(|dx|,|dy|) + min(|dx|,|dy|)/2 in table units (64 per km) from the player (ds:94EE / 94FE) to the record's X,Y (+2, +4). `core` already has this (`game_dist`, `K`); do not add another copy.
- N is unusable for this (scene-object snap, usually the wrong object). Do not use it.

Failures this must remove (from nav logs, see the doc): wanted record outside the 90 deg cone (97-150 deg off the nose) so B cycles the others forever; `/` never took (ds:9740 stayed 0) so 40 B presses were no-ops; cycle longer than `Attack.B_MAX` = 40; wanted record nearer than the current lock so B walks outward through everything first.

## Part 1: a pure rule module (`src/f19/rules`)

Add `rules/trackcam.py` (name your choice, keep it in `rules`; it may import only `core`). No I/O. Functions:

- `candidates(player_xy, heading, view_offset, night, records, mode)` -> the in-cone list sorted by distance, exactly the game's filter above. `records` = a sequence of (record_index, x, y, flags).
- `b_next(current_lock, candidates)` -> the record the next B press selects (including the -1 wrap and the "current lock outside the cone -> floor 0" case).
- `presses_to_lock(wanted, current_lock, candidates)` -> how many B presses reach `wanted`, or None if it is not a candidate. Also `slash_then_presses(...)` (after `/` the lock is the nearest candidate, then B steps) and return whichever is shorter.
- `selectable(wanted, player_xy, heading, view_offset, night, record)` -> bool plus the reason when False (`out_of_cone`, `out_of_range`, `destroyed`).
- Geometry helper for the planner: the region around a target T from which T is selectable = within `100 << (6-night)` units of T and the player's heading within +-45 deg of the bearing to T. Keep a safety margin (use +-35 deg, and 90 km / 45 km at night) as named constants.

Verification (required): unit tests that replay the decoded rule on the three memlogs in the doc (memlog ids 1793126949, 1792724414, 1793382037, under `F19_DATA/logs/memlog`; skip the test if the data is missing, follow how other memlog tests do it). Assert: record 44 only in the cone and record 2 not selectable at nav t~2450 of flight 1793126934; the cycle 2, 6, 34, 16, 38 for flight 1792724402. Write the rule from the doc, not from this file's summary, and report any disagreement with the disassembly (`research/analysis/egame_re/egame_all.asm`, 0xab84-0xacbc).

## Part 2: planner (guarantee the target can be found)

Where: `src/f19/planner/planner.py` (attack legs around line 255: launch point L, run-in start S; photo run near line 260; validation near line 432).

1. For every attack target (primary and secondary, Maverick and photo) the final approach must be a straight run-in whose heading points at the target, so the target sits within a few degrees of the nose. Define an **acquisition window**: a stretch of the run-in, at least `ACQ_MIN_KM` (suggest 3 km, tune) long, ending before the launch / photo-bay point, where the target is selectable by the geometry helper AND the decoded B sequence reaches it in at most N presses (suggest <= 8). Compute that count from the world's records (the planner has the target table through `world.py`; check what it exposes, add a read-only accessor if needed).
2. If the window does not exist on the planned approach (a nearer record sits in the cone and the wanted one is behind it is fine; the problem case is the wanted record outside the cone or beyond range), lengthen or realign the run-in. Prefer moving S back along the approach line; if that puts the plane in radar exposure the existing alert sim penalises, report it rather than hiding it.
3. Night (`settings["night"]`, `world.py:76`): halve the range cap. A 50 km cap rarely binds, but test it.
4. Add the acquisition window and expected press count to the route output (`route.json`, `report.txt`) so the autopilot and the HUD can read them. Keep the existing fields unchanged.
5. A planner self-check in `analysis/planner/checks.py` and a validation error (like line 432) when no acquisition window can be found for a target. State the difficulty/night the check applies to.
6. Per CLAUDE.md run `uv run pytest --run-slow` since you touch the planner. Compare the golden runs: a route that changes only because of a longer run-in is acceptable, but list every changed golden and why. Behaviour-preserving moves and behaviour changes go in separate commits.

## Part 3: autopilot (`src/f19/pilot/phases/attack.py`, `controls.py`, `memory/reader.py`)

The `prep` state today: F2, Space, `/` once, then B every 0.8 s up to `B_MAX` = 40, with no feedback. Change it to a closed loop:

1. **Read what the game exposes.** Add to the reader (and `GameState.systems` or a new field, keep "memory is read once per tick") : ds:9740 (screen id), ds:9E8C (view offset), ds:4EF4 (night), and the target table (record index, x, y, flags for all ds:9500 records, 16 B each, ds:80AA; it is about 1.2 KB, read it in the pipelined round trip, or at a low rate while in prep). Add the addresses to `memory/addresses.py` with a comment pointing at the doc.
2. **Verify `/`.** After sending `/`, require ds:9740 in (0x13, 0x19) and view offset 0 before any B press; re-send `/` (spacing 1.0 s, max 5) until true. Log it. Today a lost `/` burned all 40 presses (flight 1793382022).
3. **Gate on selectability.** Compute `selectable(...)` each tick in prep. If the wanted record is not selectable (out of cone/range/destroyed), send NO B presses; steer to put it in the cone (the behavior already steers at the target; make sure it does not turn away) and say why in the banner/log. If it is destroyed, skip the attack.
4. **Plan the presses.** Use `presses_to_lock` / the `/`-then-B choice: if the wanted record is nearer than the current lock, press `/` (it restarts from the nearest) rather than cycling outward. Press B exactly the predicted number of times, one at a time, re-reading the lock after each (allow the game 2 frames: the pending bit resolves on the next trackcam frame; settle >= 0.4 s, game time not wall time, use `Clock`).
5. **Check against the prediction.** After each B, compare the new lock with `b_next`. A mismatch means the model is wrong for this case: log the full context (position, heading, view, mode, station ds:4EEA, ds:9740, the candidate list) as an event so we can learn from it. Do not loop blindly: after 2 mismatches fall back to alternating `/` and B with the closed-loop check, and cap the total by game time, not by press count.
6. **Cycle detection.** If the lock sequence repeats without the wanted record, stop and re-plan (steer, `/`), do not keep pressing.
7. Keep `target_reads` working; the wanted record is still needed for the destroyed flag. Keep behaviours returning commands only; `Controls` alone sends keys. Keep `scripts/phases.py` equivalence tests passing (`CLEAR_PASS`-style class flag if the old path must be preserved for them).
8. Unit-test the new prep logic with a fake state sequence (lost `/`, wanted outside the cone, wanted nearer than the lock, cycle, destroyed). Then run `python -m f19.cli.fly --replay <memlog dir> --dry-run --quiet` on the three memlogs and confirm the new prep either locks the wanted record in the replay's states or refuses with a stated reason, and never presses B while unselectable.

## Open question you must resolve before trusting Part 3

Flight 1793382037, second attack (secondary, record 1): `/` took, lock = record 7 (SA-14 site, 528 units), the wanted record 1 (848 units, 1 deg off the nose, flags 0, in the cone) was never selected in 40 presses, although the decoded rule predicts 7 -> 1. The only difference found against a flight where B cycled normally is the weapon station ds:4EEA = 1 (camera) instead of 0. Until explained, step 5 above is the safety net. Add a **probe** (research/, not the package) that on a running game, with the owner's go-ahead, peeks ds:5526 / 9740 / 4EEA / 9A36 / 684C, taps B once and reads the lock back each frame for 2 s, for station 0 and station 1 in photo mode. Do not send keys to a running game unless the owner asked for that flight in the current session (CLAUDE.md "Live flights"). Record the result in `docs/game/memory-map.md` with a dated note.

## Definition of done

- `uv run pytest` passes; `--run-slow` goldens reviewed.
- The rule module reproduces the three recorded flights' B behaviour (tests above).
- A replay of flights 1793126934 and 1793382022 through the new prep shows: no B presses while the wanted record is out of the cone; `/` is re-sent until ds:9740 is 0x13.
- The planner reports an acquisition window for every attack leg in the current golden plans, or an explicit validation error.
- Findings and any disagreement with the decoded rule are written into `docs/game/` with a date and how they were established (disassembly address, memlog, or live flight), plus what is still untested. Veteran is the default; state what you checked at Elite and at night.
