#!/usr/bin/env python3
"""
Hunts for the F-15-SE2-style scattered player-attitude scalars (altitude,
airspeed, heading -- egdata.h's g_altitude/g_setThrust/g_ourHead) using
3000:C621 (physical 0x03C621) as a confirmed ground-truth anchor, now that
it's live-confirmed (see logs/f19-dosbox.log, 2026-08-16) as the
pitch-input/elevator-deflection register: clamps 121->127 on the first tap,
plateaus at signed-byte-max (0x7F) under repeated taps.

Every prior candidate-hunting script here (trial_decrement_correlation.py,
trial_hold_release_correlation.py, the pitch quick-tap trend correlation in
candidate_addresses.md) correlated other bytes against *tap count* or
*hold/release phase* -- a proxy for the control input, not the input itself.
That's a weaker signal than it needs to be now: C621 IS the real, in-band
elevator-position value, so this script reads it directly out of every
capture and correlates every other byte/word's trajectory against C621's
*actual* value (including its own saturation kink) instead of against tap
index. A downstream state that's really driven by this input should track
the value, kink included -- not just trend the same way taps do.

Bigger upgrade over the existing single-direction correlators: this runs
the SAME control axis in BOTH directions (default: kp2 nose-up / kp8
nose-down, both live-confirmed per candidate_addresses.md's "kp8/kp2 pitch
key mapping -- CORRECTED" entry), reloading the stable_flight checkpoint
between runs so both start from an identical state. A byte that's actually
downstream of this control should correlate one sign with C621 on the
nose-up run and the OPPOSITE sign on the nose-down run. A byte that's
background world-churn (entity tables, mission tick, etc. -- see
candidate_addresses.md's "Diff-noise root cause" entry) has no reason to
flip sign with control direction, so same-signed-in-both-runs candidates
are reported separately and flagged as more likely to be background/tick
noise, not player state.

Uses short discrete taps (dos.tap(), automation socket, same delivery
trial_decrement_correlation.py's tap mode and the "retry #2" quick-tap trend
correlation already used successfully), NOT a sustained hold -- the
hold-release correlator's held-kp8 trial crashed the plane twice from a
held nose-down input (candidate_addresses.md), so multi-second holds on
this axis are a known live-flight risk this script deliberately avoids.

Also usable, without an anchor, for axes that don't have a confirmed
input-register address yet (roll/throttle): pass --pos-key/--neg-key/--label
without --anchor-off and it falls back to a tap-index two-run sign-flip
(same monotonic-run detector trial_decrement_correlation.py uses, just run
twice and intersected on sign) -- still a real upgrade over a single
direction, just without the extra precision the C621 ground truth gives the
pitch case.

Caveat that applies to sign-flip results either way, anchor or not: any
byte that simply trends monotonically with ELAPSED TIME regardless of
control direction (a mission tick, an entity-churn accumulator) will also
look like a sign-flip against a monotonically-moving anchor/tap-index, since
"correlates positively with an increasing series, negatively with a
decreasing one" is what plain elapsed time does too. The --anchor-off mode
has a real (not just cosmetic) advantage here: C621's actual trajectory
plateaus after 2-3 taps rather than climbing linearly forever, so a
candidate has to plateau in step with it to keep a high |r| across the
whole run -- pure time-noise that keeps climbing after C621 saturates loses
correlation and drops out (confirmed with a synthetic test during
development: a linearly-climbing decoy failed the 0.9 threshold on the
saturating side, r=0.78). The tap-index fallback has no such protection.
Either way, sign-flip is a strong prioritization signal, not proof --
confirm any real candidate with scripts/breakpoint_capture.py (write
breakpoint on the exact address) before trusting it, same standard the rest
of this project's candidates are held to.

Usage:
  python3 scripts/trial_state_correlation.py \
      --pos-key kp2 --neg-key kp8 --label pitch --anchor-off C621

  # no confirmed anchor yet for roll/throttle -- falls back to sign-flip only
  python3 scripts/trial_state_correlation.py \
      --pos-key right --neg-key left --label roll
  python3 scripts/trial_state_correlation.py \
      --pos-key 9 --neg-key 0 --label throttle
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
from savestate import load_checkpoint, checkpoint_path

DEFAULT_CHECKPOINT = "stable_flight"
DEFAULT_TAPS = 6
DEFAULT_SETTLE = 1.0
POST_LOAD_SETTLE_SECS = 2.0
MIN_SAMPLES = 5  # matches trial_decrement_correlation.py's guard rail
MIN_DISTINCT = 3  # a candidate needs to actually move more than twice to
                   # mean anything from a correlation coefficient
CORR_THRESHOLD = 0.9

# Crash/scene-transition guard: live-confirmed 2026-08-16 that a
# pitch_neg (kp8, nose-down) run crashed the plane mid-trial -- the
# transition memset huge swaths of the captured region (81252/131072 bytes
# changed in one tap, 41912 of them collapsing to a repeated 0x05 fill
# pattern), which produced several spuriously perfect (|r| up to 1.0)
# sign-flip "candidates" once fed into the correlator, because the
# discontinuity happened to land in the same tap index as the anchor's own
# end-of-run value swing. Confirmed by re-running the correlation with the
# post-crash samples dropped: every candidate from that run vanished. A
# per-tap diff-fraction check catches this before it ever reaches
# analyze_run, instead of relying on someone noticing after the fact.
CRASH_DIFF_FRACTION = 0.15  # background per-tap churn stayed well under this
                             # in the clean run; the crash tap hit 0.62
DEFAULT_SEGMENTS = "3000,4000"  # the originally-confirmed compact-state region;
                                 # pass --segments full for the entire 640KB
                                 # conventional-memory range (memdump_loop.SEGMENTS)
LEGACY_TAG_BASE = 0x030000  # --anchor-off, KNOWN_INPUT_OFFSETS and WATCH_RANGES
                             # below have always been expressed relative to this
                             # fixed address (physical start of segment 3000, this
                             # script's original -- and until now, only -- capture
                             # base) regardless of what --segments actually
                             # captures now; keep that meaning stable rather than
                             # recomputing everyone's saved addresses/notes.
RESULTS_DIR = f"{ml.PROJECT_DIR}/dumps"

# Interesting-neighborhood flags purely for the printed report -- both
# already-logged in candidate_addresses.md, not assumed correct here. Keyed
# relative to LEGACY_TAG_BASE (see above), not whatever --segments captures.
KNOWN_INPUT_OFFSETS = {0xC621: "confirmed pitch-input/elevator register"}
WATCH_RANGES = [
    (0x14450, 0x14460, "near the 0x044455/57/59 heading/pitch/roll triplet hypothesis"),
]


def parse_segments(spec: str) -> list:
    """'full' -> the entire 640KB conventional-memory range (memdump_loop.SEGMENTS,
    segments 0000-9000h); otherwise a comma-separated list of hex segment values,
    e.g. '1000,2000,3000,4000'. Segments must be contiguous and in ascending order
    - everything downstream (base_offset, anchor conversion, reported addresses)
    assumes the captured bytes are one contiguous physical range starting at the
    first segment, same as the original fixed 3000/4000 capture was."""
    if spec.strip().lower() == "full":
        return list(ml.SEGMENTS)
    segments = [s.strip().upper() for s in spec.split(",") if s.strip()]
    if not segments:
        raise ValueError(f"no segments parsed from {spec!r}")
    values = [int(s, 16) for s in segments]
    for a, b in zip(values, values[1:]):
        if b - a != 0x1000:
            raise ValueError(f"--segments must be contiguous, ascending 64KB steps -- "
                              f"got {segments} (gap between {a:04X} and {b:04X})")
    return segments


def _capture(win_id, label, segments=None):
    rec = capture_and_log(win_id, label, print_diff=False, segments=segments)
    if rec is None:
        raise RuntimeError(f"capture failed: {label}")
    return base64.b64decode(rec["data_b64"])


def _raw_samples_path(label: str) -> str:
    return f"{RESULTS_DIR}/raw_samples_{label}.jsonl"


def _reset_raw_samples(label: str):
    """Truncate this direction's raw-samples checkpoint file at the start of
    a fresh run_direction() call, so a stale file from a previous invocation
    of the same --label doesn't get mixed into a new run's data."""
    path = _raw_samples_path(label)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").close()


def _append_raw_sample(label: str, index: int, data: bytes):
    """Persist one capture to disk immediately, not just held in the
    in-memory `samples` list - live-confirmed 2026-08-17 that a long
    --segments full run got killed by the environment partway through the
    second direction, and since this script previously only wrote results
    at the very end (after BOTH directions + analysis complete), the entire
    ~20+ minutes of already-captured data was lost with nothing recoverable.
    Appending one JSON line per capture means a kill loses at most the one
    capture in flight, not the whole run."""
    with open(_raw_samples_path(label), "a") as f:
        f.write(json.dumps({"index": index, "data_b64": base64.b64encode(data).decode("ascii")}) + "\n")


def _diff_fraction(a: bytes, b: bytes) -> float:
    return np.count_nonzero(np.frombuffer(a, dtype=np.uint8) != np.frombuffer(b, dtype=np.uint8)) / len(a)


def run_direction(win_id, dos, key, label, checkpoint, taps, settle, pre_tap_key=None, segments=None, pre_tap_count=1):
    print(f"\nLoading checkpoint {checkpoint!r} for {label!r} run...")
    if not load_checkpoint(checkpoint):
        raise RuntimeError(f"failed to load checkpoint {checkpoint!r}")
    time.sleep(POST_LOAD_SETTLE_SECS)

    if pre_tap_key:
        # Loading a checkpoint restores the full emulated machine state, so
        # any in-game sensitivity toggle has to be re-applied after every
        # load, not just once at the start of the script -- otherwise only
        # the first direction's run would actually have it active. Per pilot
        # feedback live 2026-08-16 (repeated nose-down runs diving into
        # terrain -- see the crash-guard aborts in this module's own run
        # history), "ins" reduces per-keystroke control sensitivity in F-19.
        # A single tap (2026-08-16) didn't change anything measurable (the
        # nose-down crash still happened, one tap sooner if anything); pilot
        # feedback 2026-08-18 is that it may need two taps to actually step
        # sensitivity down, so pre_tap_count is now configurable instead of
        # hardcoded to one.
        print(f"Tapping {pre_tap_key!r} {pre_tap_count}x to reduce control sensitivity for this direction...")
        for _ in range(pre_tap_count):
            dos.tap(pre_tap_key)
            time.sleep(settle)

    _reset_raw_samples(label)
    print(f"  (raw samples checkpointing to {_raw_samples_path(label)} as captured)")
    print(f"Capturing baseline for {label!r} (before any {key!r} taps)...")
    samples = [_capture(win_id, f"{label}-state trial: sample 0 (baseline)", segments)]
    _append_raw_sample(label, 0, samples[0])

    crashed = False
    for i in range(1, taps + 1):
        dos.tap(key)
        time.sleep(settle)
        new_sample = _capture(win_id, f"{label}-state trial: sample {i} (after {i} taps of {key!r})", segments)
        diff_frac = _diff_fraction(samples[-1], new_sample)
        if diff_frac > CRASH_DIFF_FRACTION:
            print(f"  [ABORT] tap {i} changed {diff_frac:.0%} of the captured region "
                  f"(threshold {CRASH_DIFF_FRACTION:.0%}) -- looks like a crash/scene "
                  f"transition, not flight physics (see module docstring). Discarding "
                  f"this sample and stopping the '{key}' direction early with "
                  f"{len(samples)} clean sample(s).")
            crashed = True
            break
        samples.append(new_sample)
        _append_raw_sample(label, i, new_sample)
        print(f"  [{i}/{taps}] tapped {key!r} + captured (diff {diff_frac:.1%})")

    return samples, crashed


def _correlate_against_anchor(arr, anchor_vals):
    """Pearson correlation of every column in arr (n_samples x n_offsets)
    against anchor_vals (n_samples,). Returns r per offset, NaN where the
    column or anchor is constant (undefined correlation)."""
    n = arr.shape[0]
    a = anchor_vals - anchor_vals.mean()
    a_norm = np.sqrt((a ** 2).sum())
    if a_norm == 0:
        return np.full(arr.shape[1], np.nan)
    centered = arr - arr.mean(axis=0, keepdims=True)
    col_norm = np.sqrt((centered ** 2).sum(axis=0))
    with np.errstate(invalid="ignore", divide="ignore"):
        r = (centered.T @ a) / (col_norm * a_norm)
    r[col_norm == 0] = np.nan
    return r


def _words(arr):
    """Little-endian 16-bit words at every byte alignment, matching
    trial_decrement_correlation.py's convention."""
    return arr[:, :-1] + arr[:, 1:] * 256


def analyze_run(samples, anchor_off):
    sizes = {len(s) for s in samples}
    if len(sizes) != 1:
        raise RuntimeError(f"inconsistent capture sizes {sizes}, cannot correlate byte-for-byte")
    if len(samples) < MIN_SAMPLES:
        raise RuntimeError(f"only {len(samples)} samples (need >= {MIN_SAMPLES})")

    arr = np.stack([np.frombuffer(s, dtype=np.uint8) for s in samples]).astype(np.float64)
    distinct_byte = np.array([len(np.unique(arr[:, i])) for i in range(arr.shape[1])])

    word_arr = _words(arr)
    distinct_word = np.array([len(np.unique(word_arr[:, i])) for i in range(word_arr.shape[1])])

    if anchor_off is not None:
        anchor_vals = arr[:, anchor_off]
        byte_r = _correlate_against_anchor(arr, anchor_vals)
        word_r = _correlate_against_anchor(word_arr, anchor_vals)
    else:
        # No confirmed anchor: fall back to tap-index (sample order) as the
        # reference signal -- equivalent in spirit to trial_decrement_
        # correlation.py's monotonic-run detector, expressed as correlation
        # so it composes with the same sign-flip logic below.
        idx = np.arange(len(samples), dtype=np.float64)
        byte_r = _correlate_against_anchor(arr, idx)
        word_r = _correlate_against_anchor(word_arr, idx)

    return {
        "byte_r": byte_r, "byte_vals": arr, "distinct_byte": distinct_byte,
        "word_r": word_r, "word_vals": word_arr, "distinct_word": distinct_word,
    }


def classify(pos, neg, label, base_offset):
    """Sign-flip candidates: |r| >= CORR_THRESHOLD in both runs, opposite
    sign, both with enough distinct values to be a real trend and not
    quantization noise. same_direction candidates: strong correlation with
    the SAME sign in both runs -- reported separately, flagged as likely
    background/tick rather than control-driven state (see module docstring).
    `base_offset` is the physical address of the first captured segment
    (--segments' first entry * 0x10, real-mode paragraph->physical), not the
    fixed LEGACY_TAG_BASE - it
    varies with --segments so reported addresses are always correct for
    whatever region was actually captured this run."""
    def _scan(pos_r, neg_r, pos_distinct, neg_distinct, base_addr_fn):
        sign_flip, same_direction = [], []
        n = len(pos_r)
        for off in range(n):
            pr, nr = pos_r[off], neg_r[off]
            if np.isnan(pr) or np.isnan(nr):
                continue
            if abs(pr) < CORR_THRESHOLD or abs(nr) < CORR_THRESHOLD:
                continue
            if pos_distinct[off] < MIN_DISTINCT or neg_distinct[off] < MIN_DISTINCT:
                continue
            entry = {"off": off, "addr": base_addr_fn(off), "pos_r": float(pr), "neg_r": float(nr)}
            if (pr > 0) != (nr > 0):
                sign_flip.append(entry)
            else:
                same_direction.append(entry)
        sign_flip.sort(key=lambda e: -(abs(e["pos_r"]) + abs(e["neg_r"])))
        same_direction.sort(key=lambda e: -(abs(e["pos_r"]) + abs(e["neg_r"])))
        return sign_flip, same_direction

    byte_flip, byte_same = _scan(
        pos["byte_r"], neg["byte_r"], pos["distinct_byte"], neg["distinct_byte"],
        lambda off: base_offset + off,
    )
    word_flip, word_same = _scan(
        pos["word_r"], neg["word_r"], pos["distinct_word"], neg["distinct_word"],
        lambda off: base_offset + off,
    )
    return {
        "byte_sign_flip": byte_flip, "byte_same_direction": byte_same,
        "word_sign_flip": word_flip, "word_same_direction": word_same,
    }


def _watch_note(addr):
    off = addr - LEGACY_TAG_BASE
    if off in KNOWN_INPUT_OFFSETS:
        return f"  <-- {KNOWN_INPUT_OFFSETS[off]}"
    for lo, hi, note in WATCH_RANGES:
        if lo <= off < hi:
            return f"  <-- {note}"
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pos-key", default="kp2", help="automation-socket key for the '+' direction (default kp2, confirmed nose-up)")
    parser.add_argument("--neg-key", default="kp8", help="automation-socket key for the '-' direction (default kp8, confirmed nose-down)")
    parser.add_argument("--label", default="pitch")
    parser.add_argument("--anchor-off", default="C621",
                         help="hex tag-relative offset of a confirmed input register to correlate against "
                              "(default C621, the confirmed elevator register); pass '' to disable and fall "
                              "back to tap-index correlation")
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--taps", type=int, default=DEFAULT_TAPS)
    parser.add_argument("--settle", type=float, default=DEFAULT_SETTLE)
    parser.add_argument("--pre-tap", default="ins",
                         help="key tapped once after each checkpoint load, before any pos/neg taps -- "
                              "default 'ins', which per live pilot feedback (2026-08-16) reduces "
                              "per-keystroke control sensitivity and should prevent the sharp "
                              "altitude swings that crashed prior nose-down runs into terrain; "
                              "pass '' to disable")
    parser.add_argument("--pre-tap-count", type=int, default=1,
                         help="how many times to tap --pre-tap after each checkpoint load "
                              "(default 1); pilot feedback 2026-08-18 is that 'ins' may need "
                              "two taps to actually register a lower sensitivity step")
    parser.add_argument("--segments", default=DEFAULT_SEGMENTS,
                         help="comma-separated hex 64KB segments to capture (default '3000,4000', "
                              "the originally-confirmed compact-state region), or 'full' for the "
                              "entire 640KB conventional-memory range (0000-9000). Must be "
                              "contiguous/ascending. Wider ranges multiply capture time per sample "
                              "roughly linearly with segment count.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    segments = parse_segments(args.segments)
    base_offset = int(segments[0], 16) * 0x10  # real-mode paragraph->physical
    capture_len = len(segments) * 0x10000  # each captured segment is 64KB of bytes

    if args.anchor_off:
        anchor_physical = LEGACY_TAG_BASE + int(args.anchor_off, 16)
        anchor_off = anchor_physical - base_offset
        if not (0 <= anchor_off < capture_len):
            print(f"anchor address 0x{anchor_physical:06X} (--anchor-off {args.anchor_off}) falls "
                  f"outside the captured range 0x{base_offset:06X}-0x{base_offset + capture_len:06X} "
                  f"for --segments {segments} -- pick --segments that include it, or pass "
                  f"--anchor-off '' to fall back to tap-index correlation.")
            sys.exit(1)
    else:
        anchor_off = None

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

    with Dosbox(args.host, args.port) as dos:
        pos_samples, pos_crashed = run_direction(win_id, dos, args.pos_key, f"{args.label}_pos",
                                                  args.checkpoint, args.taps, args.settle,
                                                  args.pre_tap or None, segments, args.pre_tap_count)
        neg_samples, neg_crashed = run_direction(win_id, dos, args.neg_key, f"{args.label}_neg",
                                                  args.checkpoint, args.taps, args.settle,
                                                  args.pre_tap or None, segments, args.pre_tap_count)

    if len(pos_samples) < MIN_SAMPLES or len(neg_samples) < MIN_SAMPLES:
        print(f"\nToo few clean (pre-crash) samples to correlate: "
              f"pos={len(pos_samples)}{' [CRASHED]' if pos_crashed else ''}, "
              f"neg={len(neg_samples)}{' [CRASHED]' if neg_crashed else ''} "
              f"(need >= {MIN_SAMPLES} each). Retry with fewer --taps or a higher-altitude "
              f"checkpoint for whichever direction crashed.")
        sys.exit(1)
    if pos_crashed or neg_crashed:
        print(f"\n[warn] one direction crashed before completing all {args.taps} taps -- "
              f"proceeding with the {len(pos_samples)}/{len(neg_samples)} clean samples "
              f"collected before the crash, which is a shorter (weaker) trial than requested.")

    print(f"\nAnalyzing {len(pos_samples)} '+' and {len(neg_samples)} '-' samples "
          f"{'against anchor 0x' + format(anchor_off, 'X') if anchor_off is not None else 'against tap index (no anchor)'}...")
    pos = analyze_run(pos_samples, anchor_off)
    neg = analyze_run(neg_samples, anchor_off)
    result = classify(pos, neg, args.label, base_offset)

    os.makedirs(RESULTS_DIR, exist_ok=True)
    out_path = f"{RESULTS_DIR}/state_correlation_{args.label}.json"
    with open(out_path, "w") as f:
        json.dump({
            "label": args.label, "pos_key": args.pos_key, "neg_key": args.neg_key,
            "anchor_off": args.anchor_off or None, "taps": args.taps,
            "checkpoint": args.checkpoint, "pre_tap": args.pre_tap or None,
            "pre_tap_count": args.pre_tap_count,
            "segments": segments, "base_offset": base_offset,
            "pos_crashed": pos_crashed, "neg_crashed": neg_crashed,
            "pos_samples_used": len(pos_samples), "neg_samples_used": len(neg_samples),
            **result,
        }, f, indent=2)

    total = sum(len(v) for v in result.values())
    print(f"\nSaved {total} classified candidate(s) to {out_path}")
    if not total:
        print("No byte or word offsets survived the correlation threshold in both directions.")
        return

    print(f"\n=== SIGN-FLIP candidates (best physics-state signature) ===")
    for name in ("word_sign_flip", "byte_sign_flip"):
        items = result[name]
        if not items:
            continue
        print(f"\n{name} ({len(items)}):")
        for e in items[:25]:
            print(f"  0x{e['addr']:06X}: pos_r={e['pos_r']:+.3f} neg_r={e['neg_r']:+.3f}{_watch_note(e['addr'])}")
        if len(items) > 25:
            print(f"  ...and {len(items) - 25} more (see {out_path})")

    print(f"\n=== SAME-DIRECTION candidates (likely background/tick, not control-driven) ===")
    for name in ("word_same_direction", "byte_same_direction"):
        items = result[name]
        if not items:
            continue
        print(f"\n{name} ({len(items)}):")
        for e in items[:10]:
            print(f"  0x{e['addr']:06X}: pos_r={e['pos_r']:+.3f} neg_r={e['neg_r']:+.3f}{_watch_note(e['addr'])}")
        if len(items) > 10:
            print(f"  ...and {len(items) - 10} more (see {out_path})")

    print("\nSign-flip is a prioritization signal, not proof (see module docstring's "
          "elapsed-time-noise caveat) -- confirm top candidates with "
          "scripts/breakpoint_capture.py before trusting them as altitude/speed/heading.")


if __name__ == "__main__":
    main()
