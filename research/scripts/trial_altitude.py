#!/usr/bin/env python3
"""
Second flight trial: from an already-airborne checkpoint, pitch up for a
fixed duration (climb), then pitch down for a fixed duration (descend),
with periodic tagged captures throughout both phases - not just one
before/after diff. Watching a value move across several samples during a
sustained climb is a much stronger signal for isolating "altitude"
specifically than a single before/after diff would be, and matches how
FINDINGS.md already found other per-entity fields behave (g_simObjects'
altitude-equivalent field is written unconditionally every frame while
airborne - the player's own altitude should behave the same way).

Prerequisite: a checkpoint named by --checkpoint already exists, captured
while already airborne and stable (e.g. straight and level at cruise
altitude - NOT trial_takeoff.py's "pretakeoff", which is still on the
runway). Create it once:
  python3 scripts/savestate.py save airborne
Then start each session with: python3 scripts/start_flight.py airborne

Pitch input, from the QRC's "KEYBOARD CONTROL STICK" diagram: numpad 8 =
nose up/climb, numpad 2 = nose down/descend. Both are re-tapped repeatedly
at --tap-interval throughout each phase rather than held down, mirroring
the QRC's own "double-press for maximum movement" idiom (a tap-based
control scheme, not press-and-hold) rather than trying to synthesize a
held key via System Events. Same caveat as trial_takeoff.py: this exact
key mapping is inferred from the QRC's arrow icons, not independently
tested - check the first repeat's screenshots before trusting it.

Note on timing: each tagged capture freezes the emulator for ~3-5s (it
breaks into the DOSBox-X debugger to dump memory), so --climb-seconds/
--descend-seconds measure wall-clock time including those freezes, not
pure in-game simulated flight time - the game does not advance while a
capture is in progress.

Usage:
  python3 scripts/trial_altitude.py [--climb-seconds N] [--descend-seconds N]
                                     [--sample-interval N] [--tap-interval N]
                                     [--repeats N] [--checkpoint NAME]
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml
from tag_capture import capture_and_log
from savestate import load_checkpoint

SCREENSHOT_DIR = f"{ml.PROJECT_DIR}/dumps/screenshots"
DEFAULT_CHECKPOINT = "airborne"
DEFAULT_CLIMB_SECONDS = 15.0
DEFAULT_DESCEND_SECONDS = 15.0
DEFAULT_SAMPLE_INTERVAL = 5.0
DEFAULT_TAP_INTERVAL = 1.0
DEFAULT_REPEATS = 1
NUMPAD_8 = 91  # nose up - see module docstring's unconfirmed-live caveat
NUMPAD_2 = 84  # nose down
POLL_SECONDS = 0.1


def take_screenshot(label: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    safe_label = "".join(c if c.isalnum() else "_" for c in label)[:60]
    path = f"{SCREENSHOT_DIR}/{time.time():.0f}_{safe_label}.png"
    subprocess.run(["screencapture", "-x", path], check=True)
    return path


def run_phase(win_id: str, rep: int, phase: str, key_code: int, duration: float,
              sample_interval: float, tap_interval: float):
    print(f"\n--- rep{rep}: {phase} for {duration:.0f}s "
          f"(tapping every {tap_interval:.1f}s, sampling every {sample_interval:.0f}s) ---")
    take_screenshot(f"rep{rep}_{phase}_start")
    capture_and_log(win_id, f"rep{rep}: {phase} start")

    start = time.time()
    next_tap = start
    next_sample = start + sample_interval
    while time.time() - start < duration:
        now = time.time()
        if now >= next_tap:
            ml.press_key_code(key_code)
            next_tap = now + tap_interval
        if now >= next_sample:
            elapsed = now - start
            capture_and_log(win_id, f"rep{rep}: {phase} +{elapsed:.0f}s")
            next_sample = now + sample_interval
        time.sleep(POLL_SECONDS)

    take_screenshot(f"rep{rep}_{phase}_end")
    capture_and_log(win_id, f"rep{rep}: {phase} end")


def run_one_trial(win_id: str, rep: int, checkpoint: str, args):
    print(f"\n=== Repeat {rep}: loading checkpoint {checkpoint!r} ===")
    if not load_checkpoint(checkpoint):
        raise RuntimeError(f"Failed to load checkpoint {checkpoint!r} for repeat {rep}")
    time.sleep(1.0)

    capture_and_log(win_id, f"rep{rep}: baseline after checkpoint load")

    run_phase(win_id, rep, "climb", NUMPAD_8, args.climb_seconds, args.sample_interval, args.tap_interval)
    run_phase(win_id, rep, "descend", NUMPAD_2, args.descend_seconds, args.sample_interval, args.tap_interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--climb-seconds", type=float, default=DEFAULT_CLIMB_SECONDS)
    parser.add_argument("--descend-seconds", type=float, default=DEFAULT_DESCEND_SECONDS)
    parser.add_argument("--sample-interval", type=float, default=DEFAULT_SAMPLE_INTERVAL)
    parser.add_argument("--tap-interval", type=float, default=DEFAULT_TAP_INTERVAL)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    args = parser.parse_args()

    if not ml.dosbox_running():
        print("dosbox-x is not running. Run start_flight.py first.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    completed = 0
    for rep in range(1, args.repeats + 1):
        try:
            run_one_trial(win_id, rep, args.checkpoint, args)
            completed += 1
        except RuntimeError as e:
            print(f"[error] repeat {rep} aborted: {e}")
            break

    print(f"\nDone. {completed}/{args.repeats} repeat(s) completed.")


if __name__ == "__main__":
    main()
