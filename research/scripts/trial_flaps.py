#!/usr/bin/env python3
"""
Isolates which memory byte(s), if any, encode F19's flap state.

Two problems rule out the obvious "diff a before/after capture" approach
(what tag_capture.py's own printed diff does):

1. FINDINGS.md's "World & dynamic-entity model" finding: once any input has
   happened in a session (true here - the automation-socket smoke test just
   ran 20 flap taps), the live entity tables (g_simObjects/g_particles/
   g_projectiles/g_planeTable) animate every frame regardless of what the
   player does, so any two captures taken seconds apart differ by thousands
   of unrelated bytes. A single before/after diff can't isolate a flap-
   specific byte inside that.
2. There is no known candidate address for flap state yet, so
   breakpoint_capture.py (which watches ONE already-known byte for writes)
   has nothing to point at.

This works around both by toggling flaps repeatedly via the automation
socket (dosbox_automation.py - precise DOWN/UP delivery with the emulator
running the whole time, unlike AppleScript keystrokes) and taking one tagged
debugger capture (tag_capture.py's segments 3000h/4000h - the region
FINDINGS.md already identified as holding compact live state) after each
toggle. Every sample is labeled with its toggle parity (even/odd toggle
count = the two physical flap positions, whichever is which - a byte offset
is a correlation candidate only if, across ALL samples, it takes exactly two
distinct values and those values partition EXACTLY along parity. Ordinary
per-frame simulation noise is very unlikely to happen to line up with our
toggle timing that cleanly across many samples, so this filters the churn
out instead of being buried by it.

This is a candidate filter, not proof - per this project's established
methodology (see FINDINGS.md), confirm any hit with a write breakpoint
(scripts/breakpoint_capture.py --seg <SEG> --off <OFF>, action "toggle
flaps") before trusting it.

Prerequisite: dosbox-x + F19 already running (any screen where the flaps
toggle is live) and `automation port = 6000` in dosbox-x.conf.

Usage:
  python3 scripts/trial_flaps.py [--repeats N] [--settle SECONDS]
"""
import argparse
import base64
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

FLAPS_KEY = "9"
DEFAULT_REPEATS = 10
DEFAULT_SETTLE = 1.0
TAG_BASE_OFFSET = 0x030000  # matches tag_capture.py's TAG_SEGMENTS base


def run_trial(win_id: str, dos: Dosbox, repeats: int, settle: float):
    """Returns a list of (parity, raw_bytes) samples: one baseline (parity 0,
    before any toggle in this run) plus one per toggle, alternating parity."""
    samples = []

    print("Capturing baseline (parity=0, pre-toggle)...")
    rec = capture_and_log(win_id, "flap-trial: sample 0 (parity=0, baseline)", print_diff=False)
    if rec is None:
        raise RuntimeError("baseline capture failed")
    samples.append((0, base64.b64decode(rec["data_b64"])))

    for i in range(1, repeats + 1):
        parity = i % 2
        dos.tap(FLAPS_KEY)
        time.sleep(settle)
        label = f"flap-trial: sample {i} (parity={parity}, toggle #{i})"
        rec = capture_and_log(win_id, label, print_diff=False)
        if rec is None:
            print(f"  [warn] capture {i} failed, skipping this sample")
            continue
        samples.append((parity, base64.b64decode(rec["data_b64"])))
        print(f"  [{i}/{repeats}] toggled flaps + captured (parity={parity})")

    return samples


def correlate(samples):
    """Finds byte offsets that are constant within each parity group but
    differ between groups - i.e. fully explained by flap-toggle parity."""
    sizes = {len(data) for _, data in samples}
    if len(sizes) != 1:
        print(f"  [error] inconsistent capture sizes {sizes}, cannot correlate byte-for-byte")
        return []

    arrs = np.stack([np.frombuffer(data, dtype=np.uint8) for _, data in samples])
    parities = np.array([p for p, _ in samples])

    group0 = arrs[parities == 0]
    group1 = arrs[parities == 1]
    if len(group0) == 0 or len(group1) == 0:
        print("  [error] need at least one sample of each parity to correlate")
        return []

    const0 = np.all(group0 == group0[0], axis=0)
    const1 = np.all(group1 == group1[0], axis=0)
    differs = group0[0] != group1[0]

    candidates = np.where(const0 & const1 & differs)[0]
    return [(int(off), int(group0[0][off]), int(group1[0][off])) for off in candidates]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    with Dosbox(args.host, args.port) as dos:
        samples = run_trial(win_id, dos, args.repeats, args.settle)

    print(f"\nCollected {len(samples)} samples. Correlating against toggle parity...")
    candidates = correlate(samples)
    if not candidates:
        print("No byte offsets perfectly correlate with flap-toggle parity in segments 3000h/4000h.")
        print("Either flap state lives outside this region, or per-frame noise broke strict")
        print("constancy within a parity group - consider more --repeats for a stricter filter,")
        print("or widening the captured region beyond tag_capture.py's TAG_SEGMENTS.")
    else:
        print(f"\n{len(candidates)} candidate byte(s) perfectly correlated with flap-toggle parity:")
        for off, v0, v1 in candidates:
            addr = TAG_BASE_OFFSET + off
            print(f"  0x{addr:06X}: parity0={v0} (0x{v0:02X})  parity1={v1} (0x{v1:02X})")
        print("\nNext step: confirm each with scripts/breakpoint_capture.py "
              "(--seg/--off from the address above, --action \"toggle flaps\").")


if __name__ == "__main__":
    main()
