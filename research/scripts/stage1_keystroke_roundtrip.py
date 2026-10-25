#!/usr/bin/env python3
"""
Stage 1 of the keystroke-delivery test environment (FINDINGS.md, 2026-08-07):
does an automated keystroke sent via ml.press_char() - the exact mechanism
scripted_test_run.py, trial_takeoff.py, and every other automation script in
this project uses - actually reach DOSBox-X and get echoed by DOS? Tested in
complete isolation from F19 (see stage0_bareprompt.py), so a pass/fail here
localizes the problem: fail means it's upstream of the game entirely (OS
event delivery, DOSBox-X focus/mapper binding), not something specific to
F19's own screens or bindings.

No human involved and no manual timing handshake: this script starts
probe_watch's watcher and fires the keystroke itself from a background
timer partway through the watch window, then reads the result back
automatically. Compare with stage 2 (not yet built), which will do the same
watch but let a human press the key themselves at any point in the window
instead of racing a single narrow capture against reaction time.

Prerequisite: dosbox-x already running under the bare-prompt conf (run
stage0_bareprompt.py first), sitting at the C:\\MPS\\F19> prompt.

Usage: python3 scripts/stage1_keystroke_roundtrip.py [char] [delay_before_s]
"""
import sys
import threading
import time
import os

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml
import probe_watch as pw

WATCH_DURATION_S = 8.0
DEFAULT_CHAR = "9"           # arbitrary printable char - no F19 loaded here,
                              # so no mapper binding is being tested, just echo
DEFAULT_DELAY_BEFORE_S = 2.0


def _fire(ch: str):
    print(f"  sending automated keystroke via ml.press_char(): {ch!r}")
    ml.press_char(ch)


def main():
    ch = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CHAR
    delay_s = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DELAY_BEFORE_S

    if not ml.dosbox_running():
        print("dosbox-x is not running - run stage0_bareprompt.py first.")
        sys.exit(1)

    print(f"Watching for {WATCH_DURATION_S:.0f}s, firing {ch!r} at t+{delay_s:.1f}s "
          "via the same press_char() path every other script in this project uses...")

    timer = threading.Timer(delay_s, _fire, args=(ch,))
    timer.start()
    result = pw.watch(WATCH_DURATION_S, label=f"stage1_{ch}")
    timer.join(timeout=0.1)

    print("\n--- RESULT ---")
    print(f"run dir: {result['run_dir']}")
    print(f"max changed-pixel count across window: {result['max_changed_px']}")
    if result["any_occlusion"]:
        print("[warn] dosbox-x was NOT the frontmost process for at least one tick - "
              "something else may have been captured instead. Any PASS below should "
              "be treated as suspect until you've looked at the saved PNGs.")
    if result["first_persistent_change"]:
        fc = result["first_persistent_change"]
        print(f"PASS: persistent on-screen change first seen at t+{fc['elapsed']:.2f}s "
              f"({fc['changed_px']} px changed) - consistent with the keystroke fired "
              f"at t+{delay_s:.1f}s reaching DOS and being echoed.")
    else:
        print("FAIL: no persistent on-screen change detected anywhere in the watch "
              "window. The automated keystroke did not produce a visible effect at "
              "the bare DOS prompt - this localizes the problem to upstream of F19 "
              "entirely (OS event delivery to DOSBox-X, or DOSBox-X's own SDL/mapper "
              "layer), not anything specific to the game's screens or bindings.")
        print(f"Check {result['run_dir']}/baseline.png against any "
              "first_over_threshold_*.png saved there (if changed_px ever crossed "
              "the single-tick threshold without being persistent, that's the DOS "
              "cursor blink, not a real change - see probe_watch.py's docstring).")


if __name__ == "__main__":
    main()
