# Menu screens and flight handoff (mission loop)

Goal: a loop that flies, debriefs, re-rolls a mission, arms and flies again (plan: mission loop, 2026-10-30).
Screen / cursor variables for START and END are still unknown (`memory/addresses.py` TODO).

## End-of-flight freeze (2026-10-30)
Established by live probe (read-only PEEKs, then one key at a time), landed on the runway, speed 0, EGAME resident.
Difficulty not recorded; not expected to matter.
- Frozen state: EGAME readable (alt 0, spd 0), frame counter ds:5520 constant (24125, fs 5) for 5 s. It is the game
  waiting, not a DOSBox pause (title not PAUSED, per the user).
- **`space` un-freezes it**: after one tap the counter advanced ~7 frames/s (24129, 24133, 24136, ...). A following
  `enter` tap did not stop it. (The `enter` may have done something else; it was not tried alone.)
- ~15 s later the EGAME reads went to garbage (alt 2053, spd 2313, frame 1288): EGAME's data segment was replaced,
  presumably by END (the debrief). `f19.cli.fly`'s "not in flight" rule (speed > 900) fires on exactly this.
- Untested: whether `enter` alone also un-freezes it; the bailout and crash end states; what END shows or expects.

## END screens (2026-10-30)
Conventional memory (0-0xA0000) dumped via `emu.read`, to `F19_DATA/dumps/menu/` (not in the repo):
- `safe_landing.bin`: END's "safe landing" screen (pilot smiling in the cockpit), reached after `space` (above).
- `summary.bin`: 2 s after one `enter` tap on that screen: the mission summary screen (score, "failed to accomplish his objectives"; user-confirmed). `enter` there opens the debrief map screen, which has an "exit debrief" option that returns to the roster page (per the user; not yet walked).
  82,179 bytes differ from `safe_landing.bin`. Variables that distinguish the two are not yet extracted.
- `debrief_map.bin`: 2 s after one `enter` on the summary screen: the debrief map screen (flight summary map with an
  "exit debrief" option). Not yet user-confirmed. Per the user, the cursor may need `down` before `enter` selects
  "exit debrief" (returns to the roster page); not yet tried.
  User-confirmed (screenshot): "Mission Debriefing" map, right panel lists a mission event ("Press Selector for next
  mission event"), then "Review Mission" (highlighted at entry) and "Exit Debriefing".
- `debrief_map_down.bin`: after one `down` on the debrief map. `roster.bin`: 2.5 s after `enter` following that
  `down` (expected: Exit Debriefing -> roster page; to be confirmed by the user).
- CORRECTION (user, 2026-10-30): `roster.bin` was not the roster. It is the **medals page** (renamed `medals_1.bin`),
  reached by `enter` on "Exit Debriefing". It always shows at least once ("remained at present rank"), and there can
  be several pages depending on which ribbon / medal / promotion the pilot earned. **The number of pages varies, so
  the loop cannot send a fixed count of keys: it needs a screen variable (or a roster-page detector) to know when to
  stop pressing.** Which key advances the medals page(s) and how the roster page is reached are not yet known.
- Sequence so far (Veteran, landed, no ribbon seen): landed -> `space` -> safe landing -> `enter` -> mission summary ->
  `enter` -> debrief map -> `down`, `enter` (Exit Debriefing) -> medals page(s) -> roster (not yet reached).
- `roster.bin`: 2.5 s after `enter` on the medals page (this mission: single medals page). Expected to be the roster
  page; awaiting user confirmation.
- CORRECTION (user, 2026-10-30): the previous `roster.bin` was the **bar screen** (every mission ends with the pilot in
  a bar: celebrating after a good mission, drinking alone after a bad one); renamed `bar.bin`. One more `enter` goes
  to the roster: that dump is the new `roster.bin` (awaiting user confirmation).
- Full end-of-mission sequence so far: landed -> `space` -> safe landing -> `enter` -> mission summary -> `enter` ->
  debrief map -> `down`, `enter` -> medals page(s) -> `enter` -> bar -> `enter` -> roster. Page count varies with
  medals and promotions, so a roster detector is needed.

## Roster detector (2026-10-30)
Established offline from the seven dumps above (user-confirmed screen names), Veteran, one pilot; nothing sent.
- **END vs START:** `memory.mission.start_ds(mem)` (MCB named "START") returns **None on every END screen** (safe
  landing, summary, debrief map, medals, bar) and a valid ds on the roster page (0x3d2a0 in this dump). Strings agree:
  END screens have "END.EXE" and "Mission Debrief" resident; the roster dump has neither, and has START's "Accept".
  So "the roster is reached" = `start_ds(mem) is not None` after leaving END.
- On the roster page `START_NEW_FLAG` (ds:CA6A) = 1, `START_SEED` = 0x1f8e, and the pilot pointer (ds:991C) resolves
  (0x19eae). The flag is 1 from "Select New Mission" until the generator runs (mission-generation.md), so it does not
  separate the roster from the next START menus; that needs dumps of Mission Assignment and the briefing (not taken).
- Loop consequence: from the debrief map, `down`+`enter`, then `enter` repeatedly (medals pages, bar) **until
  `start_ds` is found**, with a settle delay and a cap. Do not press `enter` blindly on the debrief map itself
  (the cursor starts on "Review Mission").
- Not done: telling END's own screens apart (not needed for the loop); the START screens after the roster; the
  frozen screen's `space` is the only key known to leave EGAME; bailout / crash end states.

## START screens (2026-10-30)
- `assignment.bin`: 2.5 s after one `enter` on the roster page (expected: Mission Assignment with "Accept assignment";
  awaiting user confirmation).
- `assignment.bin` is **Mission Assignment** (user screenshot): Cold War / Strike Missions / Veteran Opponents /
  Realistic Landings, Central Europe, "Change assignment" / "Accept assignment" (Accept highlighted on entry), Risk: 9.
- `briefing.bin`: after one `enter` on "Accept assignment" (expected: briefing room, Mission Targets selected;
  awaiting user confirmation). NEW_FLAG / briefing-block readings: see the 2026-10-30 note below.
- 2026-10-30 reading for that `enter` (Veteran, seed 8078 = 0x1f8e on the roster): `NEW_FLAG` ds:CA6A stayed 1 for
  ~3 s after the key, then went to 0 (generator ran); the briefing block ds:B946 changed (new type-1 primary:
  `0100 0c00 4800 0000 0400 ...`). So `NEW_FLAG` 1 -> 0 is the "mission rolled" signal on the live path too, and the
  settle wait after Accept is >= 3.5 s. The seed was already set on the roster (flag 1): untested whether POKEing a
  new seed there (instead of on the options menu, as `select_by_seed` does) selects the mission. Untested.

## Roster -> briefing (2026-10-30)
Read from `scripts/mission_finder.py` / `gamedata/mission_gen.py:select_by_seed` (not run live from the roster yet).
The finder predicts the mission offline (unicorn, `Generator` on a START memory image, `find_seed`), POKEs the seed to
START ds:7A50, and sends one round of Enters; the generator runs at the briefing entry. The debrief ends on the same
roster `select_by_seed` reaches after its first Enter (ds:CA6A = 1; seen on `roster.bin`), so from there only 2 Enters
remain. `gamedata.mission_gen.select_from_roster(emu, reader, seed, prediction)` does that: flag must be 1, POKE seed,
Enter (pilot), Enter (Accept), wait for ds:CA6A -> 0 (~3 s observed), compare the briefing block. Offline test only
(`tests/unit/memory/test_mission_gen.py`). **Untested live:** that a seed poked on the roster is the one the generator
uses (if not, ok comes back False and nothing is lost but one dealt mission). Arming is handled separately.
