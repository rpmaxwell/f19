#!/usr/bin/env python3
"""
Gets a repeatable test session to a known starting point with no human
menu-clicking: launches dosbox-x if it isn't already running, then loads a
named checkpoint (see savestate.py) instead of scripting the pilot-select
-> mission-parameters -> loadout menu flow by hand.

Menu navigation is deliberately NOT automated here - same judgment call
scripted_test_run.py already made ("mission-select menu navigation isn't
automated - too fragile to script blind"). The checkpoint approach avoids
that fragility entirely: fly to the state you want ONCE by hand (e.g. on
the runway, throttled to idle, ready for takeoff clearance), then run
  python3 scripts/savestate.py save pretakeoff
Every future trial run then starts from a byte-identical save, not a fresh
manual playthrough - which is what makes it possible to isolate one
action's effect on memory across many repeats (see trial_takeoff.py).

Usage:
  python3 scripts/start_flight.py [checkpoint_name]   # default: pretakeoff
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml
from savestate import load_checkpoint, list_checkpoints, checkpoint_path

DEFAULT_CHECKPOINT = "pretakeoff"
POST_LAUNCH_SETTLE_SECS = 1.0
POST_LOAD_SETTLE_SECS = 2.0


def liveliness_check(win_id: str) -> bool:
    """Two quick single-segment captures a couple seconds apart - same
    test scripted_test_run.py uses. If identical, the game is sitting on a
    static screen (paused, crashed, dropped to a DOS prompt) rather than
    actually simulating a live flight."""
    a = ml.capture_segment(win_id, "3000")
    if a is None:
        return False
    time.sleep(2.0)
    b = ml.capture_segment(win_id, "3000")
    if b is None:
        return False
    return a != b


def ensure_running() -> str:
    if ml.dosbox_running():
        print("dosbox-x already running, attaching...")
        win_id = ml.find_terminal_window_id()
        if not win_id:
            raise RuntimeError("Could not find the Terminal window running dosbox-x.")
        return win_id
    win_id = ml.launch_game()
    time.sleep(POST_LAUNCH_SETTLE_SECS)
    return win_id


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CHECKPOINT

    if not os.path.exists(checkpoint_path(name)):
        print(f"No checkpoint named {name!r} yet ({checkpoint_path(name)} not found).")
        existing = list_checkpoints()
        print(f"Available checkpoints: {existing or '(none)'}")
        print(
            "\nTo create it: launch dosbox-x (dosbox-x -conf ~/Library/Preferences/"
            "f19-dosbox.conf -savedir saves, or just run this script once to launch "
            "it), fly manually to the state you want trials to start from, then run:"
            f"\n  python3 scripts/savestate.py save {name}"
        )
        sys.exit(1)

    win_id = ensure_running()

    if not load_checkpoint(name):
        print("Failed to load checkpoint - see errors above.")
        sys.exit(1)

    print(f"Settling {POST_LOAD_SETTLE_SECS:.0f}s after load...")
    time.sleep(POST_LOAD_SETTLE_SECS)

    if not liveliness_check(win_id):
        print("[warn] game appears static after load (two captures 2s apart were "
              "identical) - the checkpoint may be a paused/menu state rather than "
              "live flight, or something else went wrong. Check the dosbox-x window "
              "before running a trial.")
        sys.exit(1)

    print(f"Ready. Checkpoint {name!r} loaded and confirmed live.")


if __name__ == "__main__":
    main()
