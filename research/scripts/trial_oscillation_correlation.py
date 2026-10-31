#!/usr/bin/env python3
"""
A different angle from trial_state_correlation.py's two-long-runs design:
instead of tapping ONE direction six times in a row (climb/dive far from the
checkpoint's starting point, which is what let external events -- an AA-10
hit, running low on fuel, terrain -- contaminate the tail of a run), this
does short (nose-up, wait, nose-down) cycles, repeated a few times, so the
plane stays close to its starting altitude/energy state throughout. A byte
that's really downstream of pitch input should ALTERNATE in sync with the
up/down pattern -- rise on every up step, fall on every down step -- not
just trend in one direction over a long climb. That's a stricter, more
specific signature than "correlates with cumulative tap count over a long
run": a background counter or a one-shot scene transition has no reason to
oscillate in lockstep with short alternating taps.

Correlates each byte/word's value across the up/down samples (baseline
excluded) against a synthetic +1 (up) / -1 (down) signal -- Pearson
correlation of an alternating series, same math trial_state_correlation.py
already uses, just against a different reference signal. Reuses that
module's segment-parsing, capture, checkpointing and address-reporting
helpers directly rather than re-implementing them (see imports below).

"Run this a few times" (--repeats): each repeat reloads the checkpoint and
captures its own independent up/down/up/down/up/down sequence, then
candidates are intersected ACROSS repeats (same offset, same sign, in
EVERY repeat) before being reported -- filters out whatever happens to
oscillate in just one run by coincidence (a threat engagement, a scripted
world event) rather than because it's actually wired to the control input.

Usage:
  python3 scripts/trial_oscillation_correlation.py \
      --up-key kp2 --down-key kp8 --cycles 3 --repeats 3 --settle 3.0
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
import trial_state_correlation as tsc
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox
from f19.core.settings import settings as _S
DEFAULT_HOST, DEFAULT_PORT = _S.host, _S.port
from savestate import load_checkpoint, checkpoint_path

DEFAULT_CHECKPOINT = "stable_flight"
DEFAULT_CYCLES = 3
DEFAULT_REPEATS = 3
DEFAULT_SETTLE = 3.0  # "wait a few seconds" per pilot's framing of this test
POST_LOAD_SETTLE_SECS = 2.0
CORR_THRESHOLD = 0.9
MIN_DISTINCT = 2  # lower than trial_state_correlation.py's 3 -- a byte that
                   # cleanly alternates between exactly two values (one for
                   # "up" phases, one for "down" phases) is the textbook
                   # signature this test is looking for, not quantization
                   # noise, so 2 distinct values is a legitimate finding here.
RESULTS_DIR = tsc.RESULTS_DIR


def run_oscillation(win_id, dos, up_key, down_key, checkpoint, cycles, settle,
                     segments, label, pre_tap_key=None, pre_tap_count=1, taps_per_phase=3,
                     intra_tap_delay=1.0):
    """One full (up, wait, down, wait) x cycles sequence from a freshly
    loaded checkpoint. Returns (samples, phases, crashed) -- samples[0] is
    the baseline, phases[0] is 'baseline', and phases[1:] alternate
    'up1','down1','up2','down2',... Raw samples are checkpointed to disk as
    captured (see tsc._append_raw_sample), same crash-recovery reasoning as
    trial_state_correlation.py.

    Each phase taps its key `taps_per_phase` times, not once -- live-observed
    2026-08-18 that a single up-tap followed by a single down-tap never
    actually reversed the climb (pilot report: altitude only ever increased
    across the whole run), unlike trial_state_correlation.py's 6-consecutive-
    same-direction-tap runs, which visibly climbed AND (in the neg direction)
    dove hard enough to crash into terrain. Matching that same per-direction
    tap magnitude (default 3, half of the 6 already shown to move the plane
    substantially) gives the down phase a real chance to cancel the up
    phase's climb instead of just slightly softening it, while still being
    short bursts rather than the sustained holds that crashed the plane in
    trial_hold_release_correlation.py."""
    print(f"\nLoading checkpoint {checkpoint!r} for {label!r}...")
    if not load_checkpoint(checkpoint):
        raise RuntimeError(f"failed to load checkpoint {checkpoint!r}")
    time.sleep(POST_LOAD_SETTLE_SECS)

    if pre_tap_key:
        print(f"Tapping {pre_tap_key!r} {pre_tap_count}x before the sequence...")
        for _ in range(pre_tap_count):
            dos.tap(pre_tap_key)
            time.sleep(settle)

    tsc._reset_raw_samples(label)
    print(f"  (raw samples checkpointing to {tsc._raw_samples_path(label)} as captured)")
    print(f"Capturing baseline for {label!r}...")
    samples = [tsc._capture(win_id, f"{label}: baseline", segments)]
    tsc._append_raw_sample(label, 0, samples[0])
    phases = ["baseline"]

    crashed = False
    idx = 1
    for cyc in range(1, cycles + 1):
        for phase_name, key in ((f"up{cyc}", up_key), (f"down{cyc}", down_key)):
            for _ in range(taps_per_phase):
                dos.tap(key)
                time.sleep(intra_tap_delay)
            time.sleep(settle)
            new_sample = tsc._capture(win_id, f"{label}: sample {idx} ({phase_name}, {key!r})", segments)
            diff_frac = tsc._diff_fraction(samples[-1], new_sample)
            if diff_frac > tsc.CRASH_DIFF_FRACTION:
                print(f"  [ABORT] {phase_name} changed {diff_frac:.0%} of the captured region "
                      f"(threshold {tsc.CRASH_DIFF_FRACTION:.0%}) -- looks like a crash/scene "
                      f"transition, not flight physics. Stopping this repeat early with "
                      f"{len(samples)} clean sample(s).")
                crashed = True
                break
            samples.append(new_sample)
            tsc._append_raw_sample(label, idx, new_sample)
            phases.append(phase_name)
            print(f"  [{phase_name}] tapped {key!r} + captured (diff {diff_frac:.1%})")
            idx += 1
        if crashed:
            break

    return samples, phases, crashed


def analyze_oscillation(samples, phases):
    """Correlates every byte/word against a +1 (up) / -1 (down) signal built
    from `phases`, using only the up/down samples (baseline excluded -- it's
    "no input yet", not a down step, so including it would blur the signal).
    Returns dicts {offset: r} for byte and word offsets that pass
    CORR_THRESHOLD and MIN_DISTINCT."""
    osc_idx = [i for i, p in enumerate(phases) if p != "baseline"]
    direction = np.array([1.0 if phases[i].startswith("up") else -1.0 for i in osc_idx])
    osc_samples = [samples[i] for i in osc_idx]

    sizes = {len(s) for s in osc_samples}
    if len(sizes) != 1:
        raise RuntimeError(f"inconsistent capture sizes {sizes}, cannot correlate byte-for-byte")

    arr = np.stack([np.frombuffer(s, dtype=np.uint8) for s in osc_samples]).astype(np.float64)
    word_arr = tsc._words(arr)
    distinct_byte = np.array([len(np.unique(arr[:, i])) for i in range(arr.shape[1])])
    distinct_word = np.array([len(np.unique(word_arr[:, i])) for i in range(word_arr.shape[1])])
    byte_r = tsc._correlate_against_anchor(arr, direction)
    word_r = tsc._correlate_against_anchor(word_arr, direction)

    def _select(r, distinct):
        out = {}
        for off in range(len(r)):
            if np.isnan(r[off]):
                continue
            if abs(r[off]) < CORR_THRESHOLD or distinct[off] < MIN_DISTINCT:
                continue
            out[off] = float(r[off])
        return out

    return _select(byte_r, distinct_byte), _select(word_r, distinct_word)


def intersect_repeats(per_repeat_candidates: list) -> dict:
    """per_repeat_candidates: list of {offset: r} dicts, one per repeat.
    Keeps only offsets present in EVERY repeat with a CONSISTENT sign
    (all-positive or all-negative across repeats) -- a real control-linked
    byte should alternate the same way every time; a coincidental one-run
    match dropping out here is exactly the point of running this more than
    once. Returns {offset: [r_repeat0, r_repeat1, ...]}."""
    if not per_repeat_candidates:
        return {}
    common = set(per_repeat_candidates[0])
    for d in per_repeat_candidates[1:]:
        common &= set(d)
    result = {}
    for off in common:
        rs = [d[off] for d in per_repeat_candidates]
        if all(r > 0 for r in rs) or all(r < 0 for r in rs):
            result[off] = rs
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--up-key", default="kp2", help="automation-socket key for 'up' (default kp2, confirmed nose-up)")
    parser.add_argument("--down-key", default="kp8", help="automation-socket key for 'down' (default kp8, confirmed nose-down)")
    parser.add_argument("--label", default="oscillate")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--cycles", type=int, default=DEFAULT_CYCLES, help="up/down cycles per repeat (default 3)")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS, help="independent repeats to cross-validate against (default 3)")
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE, help="seconds to wait after each tap (default 3.0)")
    parser.add_argument("--pre-tap", default="", help="key tapped before each repeat's sequence (default disabled)")
    parser.add_argument("--pre-tap-count", type=int, default=1)
    parser.add_argument("--taps-per-phase", type=int, default=3,
                         help="how many taps per up/down phase (default 3) -- a single tap each way "
                              "was live-observed 2026-08-18 to never actually reverse the climb")
    parser.add_argument("--segments", default="full",
                         help="comma-separated hex 64KB segments, or 'full' for the entire 640KB range (default)")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    segments = tsc.parse_segments(args.segments)
    base_offset = int(segments[0], 16) * 0x10

    if not os.path.exists(checkpoint_path(args.checkpoint)):
        print(f"No checkpoint named {args.checkpoint!r} -- see savestate.py.")
        sys.exit(1)
    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the terminal window running dosbox-x.")
        sys.exit(1)

    byte_repeats, word_repeats = [], []
    crashed_repeats = 0
    with Dosbox(args.host, args.port) as dos:
        for rep in range(1, args.repeats + 1):
            label = f"{args.label}_run{rep}"
            print(f"\n=== Repeat {rep}/{args.repeats} ===")
            samples, phases, crashed = run_oscillation(
                win_id, dos, args.up_key, args.down_key, args.checkpoint,
                args.cycles, args.settle, segments, label,
                args.pre_tap or None, args.pre_tap_count, args.taps_per_phase,
            )
            n_osc = len(phases) - 1
            if n_osc < 4:  # need at least 2 up + 2 down points for a meaningful correlation
                print(f"  [warn] only {n_osc} clean up/down sample(s) this repeat -- "
                      f"skipping (need >= 4) {'[CRASHED]' if crashed else ''}")
                if crashed:
                    crashed_repeats += 1
                continue
            byte_cands, word_cands = analyze_oscillation(samples, phases)
            print(f"  {len(byte_cands)} byte / {len(word_cands)} word candidates this repeat "
                  f"(|r| >= {CORR_THRESHOLD}, >= {MIN_DISTINCT} distinct values)")
            byte_repeats.append(byte_cands)
            word_repeats.append(word_cands)
            if crashed:
                crashed_repeats += 1

    if len(byte_repeats) < 2:
        print(f"\nOnly {len(byte_repeats)} usable repeat(s) out of {args.repeats} requested "
              f"({crashed_repeats} crashed) -- need >= 2 to cross-validate. Retry, possibly with "
              f"fewer --cycles or a calmer checkpoint.")
        sys.exit(1)

    byte_common = intersect_repeats(byte_repeats)
    word_common = intersect_repeats(word_repeats)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = f"{RESULTS_DIR}/oscillation_correlation_{args.label}.json"
    with open(out_path, "w") as f:
        json.dump({
            "label": args.label, "up_key": args.up_key, "down_key": args.down_key,
            "cycles": args.cycles, "taps_per_phase": args.taps_per_phase, "repeats_requested": args.repeats,
            "repeats_used": len(byte_repeats), "crashed_repeats": crashed_repeats,
            "segments": segments, "base_offset": base_offset,
            "byte_common": {str(base_offset + off): rs for off, rs in byte_common.items()},
            "word_common": {str(base_offset + off): rs for off, rs in word_common.items()},
        }, f, indent=2)

    print(f"\n=== Candidates consistent across all {len(byte_repeats)} usable repeats ===")
    print(f"Saved to {out_path}")
    for name, common in (("word", word_common), ("byte", byte_common)):
        items = sorted(common.items(), key=lambda kv: -min(abs(r) for r in kv[1]))
        print(f"\n{name}_common ({len(items)}):")
        for off, rs in items[:30]:
            addr = base_offset + off
            r_str = " ".join(f"{r:+.3f}" for r in rs)
            print(f"  0x{addr:06X}: r=[{r_str}]{tsc._watch_note(addr)}")
        if len(items) > 30:
            print(f"  ...and {len(items) - 30} more (see {out_path})")

    if not byte_common and not word_common:
        print("\nNo candidates survived intersection across all repeats.")


if __name__ == "__main__":
    main()
