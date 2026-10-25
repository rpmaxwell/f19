#!/usr/bin/env python3
"""
Correlates byte offsets against a monotonically-decreasing consumable/resource
count, rather than the two-value toggle parity trial_toggle_correlation.py
looks for. Built for two categories flagged as unmapped in FINDINGS.md /
candidate_addresses.md and untestable by the parity method:

  - Consumable ordnance/countermeasures (missiles, gun rounds, chaff, flare,
    decoys, jammer) - each use decrements a remaining count through many
    values, not two, so parity correlation can never find it (chaff/flare
    trials in candidate_addresses.md's "Pre-flight control sweep" entry
    correctly came back empty for exactly this reason).
  - Fuel - depletes continuously over elapsed time/throttle setting, not on
    any keypress at all.

Two sampling modes cover both:

  --mode tap   taps --key N times (one use per tap, e.g. chaff/flare/gun),
               one capture per tap - same delivery mechanism as
               trial_toggle_correlation.py (dosbox_automation.py's socket).
  --mode time  no key involved; captures every --interval seconds while the
               game runs freely. Use this for fuel: load a checkpoint at a
               known throttle setting (scripts/savestate.py) before running,
               so the burn rate this trial measures is attributable to that
               throttle - repeat at a different throttle to confirm rate
               scales with it, which is what actually confirms "this byte is
               fuel" rather than some other clock-driven decrementing value.

Why monotonic-decrease is actually a *stronger* noise filter here than
toggle-parity was: the entity-churn noise documented in candidate_addresses.md
("Diff-noise root cause") fluctuates - particle positions rise then reset,
projectile ttl counts down then the slot gets reused at a higher value,
threat timers recompute from alertLevel once they hit zero. A background byte
happening to decrease on every one of several consecutive samples by chance
is far less likely than happening to alternate between two values by chance
(which is why toggle-parity needed MIN_SAMPLES_PER_GROUP=3 as a guard rail) -
this method gets a comparable guard for free from run length, enforced via
MIN_SAMPLES.

Caveat worth checking before trusting a candidate: g_projectiles' ttl field
(FINDINGS.md's entity-model table) also counts down every frame a projectile
is airborne. If --mode tap fires a weapon that spawns a projectile, a byte at
a g_projectiles-shaped stride (24B) could look like a decrementing candidate
for reasons unrelated to the ammo count itself - cross-reference any hit
against the known table strides/offsets already logged in
candidate_addresses.md before trusting it as "the" ammo/fuel counter.

Reports both single-byte and little-endian 16-bit-word candidates (word pairs
at every byte alignment, not just even offsets, since no struct alignment is
assumed) since a resource like fuel that outlives a byte's 0-255 range needs
the wider read.

Usage:
  python3 scripts/trial_decrement_correlation.py --mode tap --key 2 --label chaff
  python3 scripts/trial_decrement_correlation.py --mode tap --key 1 --label flare
  python3 scripts/trial_decrement_correlation.py --mode time --label fuel_cruise \
      --interval 30 --repeats 10
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
DEFAULT_INTERVAL = 30.0
MIN_SAMPLES = 5  # a monotonic decrease surviving this many consecutive samples
                  # by chance alone is already unlikely (~1/n! if values were
                  # random) - see module docstring.
TAG_BASE_OFFSET = 0x030000  # matches tag_capture.py's TAG_SEGMENTS base
RESULTS_DIR = f"{ml.PROJECT_DIR}/dumps"


def run_trial_tap(win_id, dos, key, label, repeats, settle):
    samples = []
    print(f"Capturing baseline for {label!r} (before any {key!r} taps)...")
    rec = capture_and_log(win_id, f"{label}-decrement trial: sample 0 (baseline)", print_diff=False)
    if rec is None:
        raise RuntimeError("baseline capture failed")
    samples.append(base64.b64decode(rec["data_b64"]))

    for i in range(1, repeats + 1):
        dos.tap(key)
        time.sleep(settle)
        lbl = f"{label}-decrement trial: sample {i} (after {i} taps of {key!r})"
        rec = capture_and_log(win_id, lbl, print_diff=False)
        if rec is None:
            print(f"  [warn] capture {i} failed, skipping this sample")
            continue
        samples.append(base64.b64decode(rec["data_b64"]))
        print(f"  [{i}/{repeats}] tapped {key!r} + captured")
    return samples


def run_trial_time(win_id, label, repeats, interval):
    samples = []
    print(f"Capturing baseline for {label!r} (t=0)...")
    rec = capture_and_log(win_id, f"{label}-decrement trial: sample 0 (t=0)", print_diff=False)
    if rec is None:
        raise RuntimeError("baseline capture failed")
    samples.append(base64.b64decode(rec["data_b64"]))

    for i in range(1, repeats + 1):
        time.sleep(interval)
        lbl = f"{label}-decrement trial: sample {i} (t={i * interval:.0f}s)"
        rec = capture_and_log(win_id, lbl, print_diff=False)
        if rec is None:
            print(f"  [warn] capture {i} failed, skipping this sample")
            continue
        samples.append(base64.b64decode(rec["data_b64"]))
        print(f"  [{i}/{repeats}] waited {interval}s + captured")
    return samples


def correlate(samples, direction="down"):
    """samples: chronologically-ordered list of raw byte buffers (no parity
    grouping - order itself is the signal). direction: 'down' (the original
    consumable/fuel use case - monotonic decrease only), 'up' (monotonic
    increase only), or 'either' (accept a byte/word matching either
    direction - useful for a stepped control value like a trim/pitch tap
    count, where the sign convention isn't known ahead of time). Returns
    (byte_candidates, word_candidates), each a list of (offset, direction,
    values_list)."""
    sizes = {len(s) for s in samples}
    if len(sizes) != 1:
        print(f"  [error] inconsistent capture sizes {sizes}, cannot correlate byte-for-byte")
        return [], []
    if len(samples) < MIN_SAMPLES:
        print(f"  [error] only {len(samples)} samples (need >= {MIN_SAMPLES}) - "
              f"too few for a monotonic run to mean anything; refusing to report")
        return [], []

    arr = np.stack([np.frombuffer(s, dtype=np.uint8) for s in samples]).astype(np.int32)

    def _monotonic_candidates(vals):
        diffs = np.diff(vals, axis=0)
        non_increasing = np.all(diffs <= 0, axis=0)
        non_decreasing = np.all(diffs >= 0, axis=0)
        changed = vals[0] != vals[-1]
        out = {}
        if direction in ("down", "either"):
            for off in np.where(non_increasing & changed)[0]:
                out[int(off)] = "down"
        if direction in ("up", "either"):
            for off in np.where(non_decreasing & changed)[0]:
                out.setdefault(int(off), "up")
        return out

    # single-byte candidates
    byte_hits = _monotonic_candidates(arr)
    byte_candidates = [(off, d, arr[:, off].tolist()) for off, d in sorted(byte_hits.items())]

    # little-endian 16-bit word candidates at every byte alignment (off, off+1).
    lo = arr[:, :-1]
    hi = arr[:, 1:]
    words = lo + hi * 256
    word_hits = _monotonic_candidates(words)
    word_candidates = [(off, d, words[:, off].tolist()) for off, d in sorted(word_hits.items())]

    return byte_candidates, word_candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["tap", "time"], required=True)
    parser.add_argument("--key", help="automation-socket key name (required for --mode tap), e.g. 2, 1, 5")
    parser.add_argument("--label", required=True, help="short name for this trial, e.g. chaff, flare, fuel_cruise")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE,
                         help="[tap mode] seconds after each tap before capturing")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                         help="[time mode] seconds between captures")
    parser.add_argument("--direction", choices=["down", "up", "either"], default="down",
                         help="monotonic direction to look for (default 'down', the consumable/"
                              "fuel case; use 'either' for a stepped control value with unknown sign)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if args.mode == "tap" and not args.key:
        parser.error("--mode tap requires --key")

    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the terminal window running dosbox-x.")
        sys.exit(1)

    if args.mode == "tap":
        with Dosbox(args.host, args.port) as dos:
            samples = run_trial_tap(win_id, dos, args.key, args.label, args.repeats, args.settle)
    else:
        samples = run_trial_time(win_id, args.label, args.repeats, args.interval)

    print(f"\nCollected {len(samples)} samples for {args.label!r}. "
          f"Correlating for monotonic {args.direction}...")
    byte_candidates, word_candidates = correlate(samples, direction=args.direction)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = f"{RESULTS_DIR}/decrement_correlation_{args.label}.json"
    with open(out_path, "w") as f:
        json.dump({
            "label": args.label,
            "mode": args.mode,
            "key": args.key,
            "repeats": args.repeats,
            "interval": args.interval if args.mode == "time" else None,
            "direction": args.direction,
            "byte_candidates": [
                {"addr": TAG_BASE_OFFSET + off, "off": off, "direction": d, "values": vals}
                for off, d, vals in byte_candidates
            ],
            "word_candidates": [
                {"addr": TAG_BASE_OFFSET + off, "off": off, "direction": d, "values": vals}
                for off, d, vals in word_candidates
            ],
        }, f, indent=2)
    print(f"Saved {len(byte_candidates)} byte + {len(word_candidates)} word candidate(s) to {out_path}")

    if not byte_candidates and not word_candidates:
        print(f"No byte or word offsets monotonically trended across this {args.label!r} run.")
        return

    if byte_candidates:
        print(f"\n{len(byte_candidates)} single-byte candidate(s):")
        for off, d, vals in byte_candidates[:60]:
            addr = TAG_BASE_OFFSET + off
            print(f"  0x{addr:06X} ({d}): {vals}")
        if len(byte_candidates) > 60:
            print(f"  ...and {len(byte_candidates) - 60} more (see {out_path})")

    if word_candidates:
        print(f"\n{len(word_candidates)} 16-bit word candidate(s) (little-endian, at byte offset..offset+1):")
        for off, d, vals in word_candidates[:60]:
            addr = TAG_BASE_OFFSET + off
            print(f"  0x{addr:06X} ({d}): {vals}")
        if len(word_candidates) > 60:
            print(f"  ...and {len(word_candidates) - 60} more (see {out_path})")


if __name__ == "__main__":
    main()
