#!/usr/bin/env python3
"""
Generalizes trial_flaps.py into a reusable per-key correlation trial, so
different controls' keystroke->memory mappings can be compared against each
other directly instead of found in isolation.

The comparison is the point. FINDINGS.md's zero-input-control test on
3000:C5F0 already showed some bytes are generic "something happened" flags
(fire on ordnance-cycle/bay-doors/cannon actions alike, not tied to any one
of them) rather than action-specific state. Running this same correlation
method against several different keys and then set-comparing the resulting
candidate lists is how to tell apart:
  - a byte that's specific to ONE control (real state for that control)
  - a byte shared by aerodynamically-real controls (flaps, brakes) but not
    purely-cosmetic ones (ordnance-select) - a candidate "control-surface/
    physics" cluster
  - a byte shared by ALL keys tested - a generic "input processed" or
    screen-redraw flag, not specific to anything

Same mechanism as trial_flaps.py: N taps of --key via the automation socket
(dosbox_automation.py - precise delivery, emulator never paused for the
keypress itself), one tagged debugger capture (tag_capture.py's segments
3000h/4000h) after each tap, correlate byte values against tap parity (a
byte is a candidate only if it takes exactly two values across ALL samples
and those values partition exactly along even/odd tap count). Results are
saved to dumps/toggle_correlation_<label>.json so separate runs (different
keys, possibly different sessions) can be loaded and compared later instead
of only living in one run's stdout.

Usage:
  python3 scripts/trial_toggle_correlation.py --key KEY --label LABEL
      [--repeats N] [--settle SECONDS]

Example:
  python3 scripts/trial_toggle_correlation.py --key 0 --label brake
  python3 scripts/trial_toggle_correlation.py --key space --label ordnance_select
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
from dosbox_automation import Dosbox, DEFAULT_HOST, DEFAULT_PORT

DEFAULT_REPEATS = 10
DEFAULT_SETTLE = 1.0
TAG_BASE_OFFSET = 0x030000  # matches tag_capture.py's TAG_SEGMENTS base
RESULTS_DIR = f"{ml.PROJECT_DIR}/dumps"


def run_trial(win_id: str, dos: Dosbox, key: str, label: str, repeats: int, settle: float):
    samples = []

    print(f"Capturing baseline for {label!r} (parity=0, pre-toggle)...")
    rec = capture_and_log(win_id, f"{label}-trial: sample 0 (parity=0, baseline)", print_diff=False)
    if rec is None:
        raise RuntimeError("baseline capture failed")
    samples.append((0, base64.b64decode(rec["data_b64"])))

    for i in range(1, repeats + 1):
        parity = i % 2
        dos.tap(key)
        time.sleep(settle)
        lbl = f"{label}-trial: sample {i} (parity={parity}, toggle #{i})"
        rec = capture_and_log(win_id, lbl, print_diff=False)
        if rec is None:
            print(f"  [warn] capture {i} failed, skipping this sample")
            continue
        samples.append((parity, base64.b64decode(rec["data_b64"])))
        print(f"  [{i}/{repeats}] tapped {key!r} + captured (parity={parity})")

    return samples


MIN_SAMPLES_PER_GROUP = 3  # below this, "constant within group" is trivially true and every
                            # byte that merely differs between two arbitrary captures looks like
                            # a candidate - exactly the entity-churn noise this method exists to
                            # filter out (confirmed live 2026-08-13: a run that only got 1-2
                            # samples per parity group, after several debugger break-in failures,
                            # produced 823 "candidates" - almost all noise, not signal).


def correlate(samples):
    sizes = {len(data) for _, data in samples}
    if len(sizes) != 1:
        print(f"  [error] inconsistent capture sizes {sizes}, cannot correlate byte-for-byte")
        return []

    arrs = np.stack([np.frombuffer(data, dtype=np.uint8) for _, data in samples])
    parities = np.array([p for p, _ in samples])

    group0 = arrs[parities == 0]
    group1 = arrs[parities == 1]
    if len(group0) < MIN_SAMPLES_PER_GROUP or len(group1) < MIN_SAMPLES_PER_GROUP:
        print(f"  [error] only {len(group0)}/{len(group1)} samples per parity group "
              f"(need >= {MIN_SAMPLES_PER_GROUP} each) - too few for the 'constant within "
              f"group' test to mean anything; refusing to report candidates from this run")
        return []

    const0 = np.all(group0 == group0[0], axis=0)
    const1 = np.all(group1 == group1[0], axis=0)
    differs = group0[0] != group1[0]

    candidates = np.where(const0 & const1 & differs)[0]
    return [(int(off), int(group0[0][off]), int(group1[0][off])) for off in candidates]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", required=True, help="automation-socket key name, e.g. 0, space, 9")
    parser.add_argument("--label", required=True, help="short name for this trial, e.g. brake, ordnance_select")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE)
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
        samples = run_trial(win_id, dos, args.key, args.label, args.repeats, args.settle)

    print(f"\nCollected {len(samples)} samples for {args.label!r}. Correlating against toggle parity...")
    candidates = correlate(samples)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = f"{RESULTS_DIR}/toggle_correlation_{args.label}.json"
    with open(out_path, "w") as f:
        json.dump({
            "label": args.label,
            "key": args.key,
            "repeats": args.repeats,
            "candidates": [
                {"addr": TAG_BASE_OFFSET + off, "off": off, "v0": v0, "v1": v1}
                for off, v0, v1 in candidates
            ],
        }, f, indent=2)
    print(f"Saved {len(candidates)} candidate(s) to {out_path}")

    if not candidates:
        print(f"No byte offsets perfectly correlate with {args.key!r}'s toggle parity in segments 3000h/4000h.")
    else:
        print(f"\n{len(candidates)} candidate byte(s) perfectly correlated with {args.key!r} toggle parity:")
        for off, v0, v1 in candidates:
            addr = TAG_BASE_OFFSET + off
            print(f"  0x{addr:06X}: parity0={v0} (0x{v0:02X})  parity1={v1} (0x{v1:02X})")


if __name__ == "__main__":
    main()
