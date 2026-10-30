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
