#!/usr/bin/env python3
"""
Classifies byte offsets by how they respond to a HELD control key versus a
brief coast period after release, instead of trial_toggle_correlation.py's
tap-parity method (built for two-state toggles) or
trial_decrement_correlation.py's monotonic-count method (built for
consumables/fuel). Built for a distinction FINDINGS.md's "flight controls"
category conflated but which matters a lot for RL purposes:

  - the control INPUT itself (stick/throttle position right now) - expected
    to change once, immediately, and either snap back on release (momentary/
    spring control) or stay put (a lever-style setting like throttle).
  - the flight-dynamics STATE that input drives (heading, bank, altitude,
    airspeed) - expected to change gradually the whole time the key is held,
    consistent with candidate_addresses.md's g_simObjects finding that NPC
    motion is recomputed from steering logic every frame rather than read
    from a stored velocity field - the player's own state should show the
    same signature if the physics model is shared.

Protocol per trial: baseline capture (key up) -> key DOWN -> --hold-samples
captures spaced --hold-interval apart (key held the whole time - the
automation socket in dosbox_automation.py sustains key state independent of
the debugger, so each capture's break-in/resume doesn't release it) -> key UP
-> --coast-samples captures spaced --coast-interval apart. Every byte offset
is classified from the shape of its baseline -> hold[] -> coast[] trajectory:

  instant_revert   - changes once at key-down, holds flat through the rest of
                      the hold, snaps back to the baseline value at release,
                      flat through coast. Momentary/spring input candidate.
  instant_sticky   - changes once at key-down, holds flat through the hold,
                      stays at that same new value through coast (release
                      doesn't undo it). Lever/latched-setting candidate
                      (throttle steps are the expected match, since
                      increase/reduce thrust read as step keys, not a
                      spring-loaded stick per the Quick Reference Card).
  gradual_freeze   - takes multiple different values across the hold samples
                      (still changing while held), then holds flat at
                      whatever value it reached the instant release happens.
                      Kinematic-follow state, no modeled inertia.
  gradual_momentum - takes multiple different values across the hold AND
                      keeps changing into the coast phase after release.
                      Strongest signature for real physics-integrated state
                      (momentum/inertia) - the highest-value candidate class
                      for altitude/heading/bank/airspeed.

Byte offsets that don't fit any of these (e.g. background entity-churn that
moves around without any hold/release-aligned structure) are not reported -
the phase-alignment requirement is a strong filter for the same reason
trial_decrement_correlation.py's monotonic-run requirement is: matching this
exact hold/release-aligned shape by chance across background noise is
unlikely, especially at higher --hold-samples/--coast-samples.

Key-name caveat: FINDINGS.md flags the numpad-8/numpad-2 pitch keys as
"not yet confirmed live" (only inferred from the Quick Reference Card's
control-stick diagram, and only exercised so far via AppleScript virtual key
codes in trial_altitude.py, not this socket). dosbox_automation.py's key
vocabulary names these kp8/kp2 - verify the mapping against real pitch
behavior (screenshot or altitude change) before trusting a "pitch_up"-labeled
run's results.

Usage:
  python3 scripts/trial_hold_release_correlation.py --key kp8 --label pitch_up
  python3 scripts/trial_hold_release_correlation.py --key kp2 --label pitch_down
  python3 scripts/trial_hold_release_correlation.py --key left --label roll_left
  python3 scripts/trial_hold_release_correlation.py --key 9 --label throttle_up \
      --hold-samples 3 --coast-samples 3
"""
import argparse
import base64
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml
from tag_capture import capture_and_log
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox
from f19.core.settings import settings as _S
DEFAULT_HOST, DEFAULT_PORT = _S.host, _S.port

DEFAULT_HOLD_SAMPLES = 5
DEFAULT_HOLD_INTERVAL = 1.0
DEFAULT_COAST_SAMPLES = 5
DEFAULT_COAST_INTERVAL = 1.0
TAG_BASE_OFFSET = 0x030000  # matches tag_capture.py's TAG_SEGMENTS base
RESULTS_DIR = f"{ml.PROJECT_DIR}/dumps"


def _capture(win_id, label):
    rec = capture_and_log(win_id, label, print_diff=False)
    if rec is None:
        raise RuntimeError(f"capture failed: {label}")
    return base64.b64decode(rec["data_b64"])


def run_trial(win_id, dos, key, label, hold_samples, hold_interval, coast_samples, coast_interval):
    print(f"Capturing baseline for {label!r} (key up)...")
    baseline = _capture(win_id, f"{label}-hold trial: baseline (key up)")

    print(f"Holding {key!r} down for {hold_samples} sample(s)...")
    dos.key_down(key)
    hold = []
    try:
        for i in range(hold_samples):
            time.sleep(hold_interval)
            hold.append(_capture(win_id, f"{label}-hold trial: hold sample {i} (key {key!r} held)"))
            print(f"  [hold {i + 1}/{hold_samples}] captured")
    finally:
        # Always release, even if a capture raised mid-hold (debugger
        # resume stall, etc.) - a stuck key left DOWN across the exception
        # boundary silently keeps driving the control (confirmed live
        # 2026-08-14: a failed capture here left kp8/pitch-up held, which is
        # the likely cause of a stall/crash before this was caught and
        # released manually).
        dos.key_up(key)
        print(f"  released {key!r}")

    print(f"Capturing {coast_samples} coast sample(s)...")
    coast = []
    for i in range(coast_samples):
        time.sleep(coast_interval)
        coast.append(_capture(win_id, f"{label}-hold trial: coast sample {i} (key {key!r} released)"))
        print(f"  [coast {i + 1}/{coast_samples}] captured")

    return baseline, hold, coast


def classify(baseline, hold, coast):
    sizes = {len(baseline)} | {len(h) for h in hold} | {len(c) for c in coast}
    if len(sizes) != 1:
        print(f"  [error] inconsistent capture sizes {sizes}, cannot classify byte-for-byte")
        return {}
    if len(hold) < 2 or len(coast) < 2:
        print(f"  [error] need >= 2 hold and >= 2 coast samples to tell 'flat' from "
              f"'still changing' - got {len(hold)}/{len(coast)}")
        return {}

    base = np.frombuffer(baseline, dtype=np.uint8).astype(np.int32)
    hold_arr = np.stack([np.frombuffer(h, dtype=np.uint8) for h in hold]).astype(np.int32)
    coast_arr = np.stack([np.frombuffer(c, dtype=np.uint8) for c in coast]).astype(np.int32)

    changed_at_start = hold_arr[0] != base
    hold_flat = np.all(hold_arr == hold_arr[0], axis=0)
    hold_varies = ~hold_flat
    coast_flat = np.all(coast_arr == coast_arr[0], axis=0)
    coast_matches_hold_end = coast_arr[0] == hold_arr[-1]
    coast_matches_baseline = coast_arr[0] == base

    instant_revert = changed_at_start & hold_flat & coast_flat & coast_matches_baseline
    instant_sticky = changed_at_start & hold_flat & coast_flat & coast_matches_hold_end & ~coast_matches_baseline
    gradual_freeze = hold_varies & coast_flat & coast_matches_hold_end
    gradual_momentum = hold_varies & ~coast_flat

    buckets = {
        "instant_revert": instant_revert,
        "instant_sticky": instant_sticky,
        "gradual_freeze": gradual_freeze,
        "gradual_momentum": gradual_momentum,
    }

    result = {}
    for name, mask in buckets.items():
        offsets = np.where(mask)[0]
        result[name] = [
            {
                "off": int(off),
                "addr": TAG_BASE_OFFSET + int(off),
                "baseline": int(base[off]),
                "hold": hold_arr[:, off].tolist(),
                "coast": coast_arr[:, off].tolist(),
            }
            for off in offsets
        ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", required=True,
                         help="automation-socket key name, e.g. kp8, kp2, left, right, 9, 0")
    parser.add_argument("--label", required=True,
                         help="short name for this trial, e.g. pitch_up, roll_left, throttle_up")
    parser.add_argument("--hold-samples", type=int, default=DEFAULT_HOLD_SAMPLES)
    parser.add_argument("--hold-interval", type=float, default=DEFAULT_HOLD_INTERVAL)
    parser.add_argument("--coast-samples", type=int, default=DEFAULT_COAST_SAMPLES)
    parser.add_argument("--coast-interval", type=float, default=DEFAULT_COAST_INTERVAL)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the terminal window running dosbox-x.")
        sys.exit(1)

    with Dosbox(args.host, args.port) as dos:
        baseline, hold, coast = run_trial(
            win_id, dos, args.key, args.label,
            args.hold_samples, args.hold_interval,
            args.coast_samples, args.coast_interval,
        )

    print("\nClassifying against hold/release phase shape...")
    result = classify(baseline, hold, coast)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = f"{RESULTS_DIR}/hold_release_correlation_{args.label}.json"
    with open(out_path, "w") as f:
        json.dump({"label": args.label, "key": args.key, **result}, f, indent=2)

    total = sum(len(v) for v in result.values())
    print(f"Saved {total} classified candidate(s) to {out_path}")
    if not total:
        print(f"No byte offsets matched a hold/release-aligned pattern for {args.key!r}.")
        return

    descriptions = {
        "instant_revert": "momentary/spring input (snaps back on release)",
        "instant_sticky": "latched/lever setting (stays after release)",
        "gradual_freeze": "kinematic state, no inertia (freezes at release)",
        "gradual_momentum": "physics-integrated state, has inertia (best altitude/heading/speed candidate)",
    }
    for name, items in result.items():
        if not items:
            continue
        print(f"\n{name} - {descriptions[name]} - {len(items)} candidate(s):")
        for item in items[:20]:
            print(f"  0x{item['addr']:06X}: baseline={item['baseline']} hold={item['hold']} coast={item['coast']}")
        if len(items) > 20:
            print(f"  ...and {len(items) - 20} more (see {out_path})")


if __name__ == "__main__":
    main()
