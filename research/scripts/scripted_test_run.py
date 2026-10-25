#!/usr/bin/env python3
"""
Runs a prescribed, deterministic sequence of in-flight actions against an
already-airborne F-19 mission, with a tagged memory capture (see
tag_capture.py) before and after each one. Ends by quitting the mission
cleanly (Alt+Q). This isolates one controllable variable at a time instead
of relying on free-flying + after-the-fact narration.

Prerequisite: you're already airborne in a mission (mission-select menu
navigation isn't automated - too fragile to script blind). Get there
manually, then run this.

Key bindings are from the game's Quick Reference Card (keyboard, no
joystick): flaps=9, gear=6, autopilot=7, bay doors=8, brakes=0,
throttle -/=, select ordnance=Space, fire ordnance=Return, quit=Alt+Q.

Edit ACTIONS below to define a different test sequence.

Usage: python3 scripts/scripted_test_run.py
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml
from tag_capture import capture_and_log

SETTLE_SECONDS = 1.0  # let game state update before capturing
SCREENSHOT_DIR = f"{ml.PROJECT_DIR}/dumps/screenshots"


def take_screenshot(label: str) -> str:
    """Full-screen capture (not a cropped region - window-bounds math via
    System Events didn't match what screencapture -R expects, likely a
    points-vs-pixels scaling mismatch on this Retina display, and produced
    a screenshot of the wrong window entirely). Full-screen sidesteps that;
    the dosbox-x window just needs visually locating within it afterward."""
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    safe_label = "".join(c if c.isalnum() else "_" for c in label)[:60]
    path = f"{SCREENSHOT_DIR}/{time.time():.0f}_{safe_label}.png"
    subprocess.run(["screencapture", "-x", path], check=True)
    return path


# focus_dosbox/press_char/press_key_code moved to memdump_loop.py (as
# ml.focus_dosbox/ml.press_char/ml.press_key_code) so savestate.py and the
# trial_*.py scripts can share them instead of each keeping their own copy.

# (label used for the tagged capture, action to perform)
ACTIONS = [
    ("cycled ordnance #1 (space)", lambda: ml.press_char(" ")),
    ("cycled ordnance #2 (space)", lambda: ml.press_char(" ")),
    ("cycled ordnance #3 (space)", lambda: ml.press_char(" ")),  # should land on sidewinders (slot 4)
    ("opened bay doors (key 8)", lambda: ml.press_char("8")),
    ("fired sidewinder #1", lambda: ml.press_key_code(36)),
    ("fired sidewinder #2", lambda: ml.press_key_code(36)),
    ("fired sidewinder #3", lambda: ml.press_key_code(36)),
    ("fired sidewinder #4", lambda: ml.press_key_code(36)),
    ("fired sidewinder #5 (expect depleted)", lambda: ml.press_key_code(36)),
    ("fired sidewinder #6 (expect empty warning)", lambda: ml.press_key_code(36)),
    ("fired cannon #1 (backspace)", lambda: ml.press_key_code(51)),
    ("fired cannon #2 (backspace)", lambda: ml.press_key_code(51)),
]


def quit_mission():
    print("Quitting mission (Alt+Q)...")
    ml.press_key_code(12, modifiers=["option"])  # Q
    time.sleep(1.0)  # drops straight to the DOS prompt, no confirmation needed


def liveliness_check(win_id: str) -> bool:
    """Two quick single-segment captures a couple seconds apart. If they're
    identical, the game is sitting on a static screen (frozen, paused, at a
    menu/DOS prompt - e.g. a delayed Alt+Q from a previous run finally
    landing) rather than actually flying, and a full run would be wasted."""
    print("Checking the game is actually live before committing to a full run...")
    a = ml.capture_segment(win_id, "3000")
    if a is None:
        return False
    time.sleep(2.0)
    b = ml.capture_segment(win_id, "3000")
    if b is None:
        return False
    return a != b


def main():
    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    if not liveliness_check(win_id):
        print("Game appears frozen/static (two captures 2s apart were identical). "
              "Make sure you're actually airborne and flying (not paused, not at a "
              "menu/DOS prompt - a previous run's delayed Alt+Q may have finally landed). Aborting.")
        sys.exit(1)

    print(f"Running {len(ACTIONS)} prescribed actions. Make sure you're airborne first.\n")

    record = capture_and_log(win_id, "baseline before scripted run")
    if record is None:
        print("Baseline capture failed, aborting.")
        sys.exit(1)

    for label, action in ACTIONS:
        # Idle control right before the action: same elapsed time, nothing
        # done. Diffing this against the *previous* capture isolates pure
        # flight-motion churn, so it can be subtracted from the action's
        # own diff (against this idle capture) to find action-specific bytes.
        print(f"\n>>> (idle control before: {label})")
        time.sleep(SETTLE_SECONDS)
        capture_and_log(win_id, f"idle before: {label}")

        print(f">>> {label}")
        action()
        time.sleep(SETTLE_SECONDS)
        shot_path = take_screenshot(label)
        print(f"    screenshot: {shot_path}")
        capture_and_log(win_id, label)

    quit_mission()
    print("\nDone.")


if __name__ == "__main__":
    main()
