#!/usr/bin/env python3
"""
Minimal takeoff key sequence via inject_key.py's BIOS-keyboard-buffer
injection. No memory capture, no screenshots, no checkpoint management -
just sends the takeoff control inputs, with retries.

The previous version of this script called each inject_key.py action once
and never checked its return value. Every inject_key.py function can fail
transiently (confirmed throughout this project's live testing, 2026-08-08:
"could not read head/tail pointers", "debugger still showing as open after
resume timeout" - the debugger console doesn't always open/close cleanly on
the first try) - when that happened here, the failure printed from inside
inject_key.py and was silently ignored, so that action just never landed.
This version retries each step and reports failures instead of eating them.

Throttle + nose-up are combined into ONE debugger pass via
inject_key.py's inject_takeoff_sequence() rather than sent separately -
confirmed live that re-entering the debugger while the plane is already
rolling under throttle is itself disruptive to the takeoff roll.

To reload a checkpoint first, run start_flight.py (or savestate.py load
<name>) before this - that's a separate concern, not duplicated here.

Usage: python3 scripts/trial_takeoff.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml
import inject_key as ik

RETRY_ATTEMPTS = 3
RETRY_DELAY_SECS = 0.5


def run_step(label: str, fn) -> bool:
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        print(f">>> {label} (attempt {attempt}/{RETRY_ATTEMPTS})")
        if fn():
            return True
        time.sleep(RETRY_DELAY_SECS)
    print(f"[error] {label} failed after {RETRY_ATTEMPTS} attempts")
    return False


def main():
    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the terminal window running dosbox-x.")
        sys.exit(1)

    run_step("throttle + nose-up (shift+=, 12x DOWN, one debugger pass)",
              lambda: ik.inject_takeoff_sequence(win_id, nose_up_count=12, nose_up_key="DOWN"))
    run_step("flaps (9)", lambda: ik.inject_key(win_id, "9"))
    run_step("gear up (6)", lambda: ik.inject_key(win_id, "6"))


if __name__ == "__main__":
    main()
