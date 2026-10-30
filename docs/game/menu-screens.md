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
