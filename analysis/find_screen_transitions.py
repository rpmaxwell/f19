#!/usr/bin/env python3
"""
Finds candidate screen-transition / dispatcher code purely from ordered
LOGC (cs:ip-only) traces -- no external address list, no prior findings
consulted, nothing but the raw execution order in logcpu_*.txt.

Idea: within one file, a steady-state loop (idle wait, vsync poll, input
poll) repeats a small fixed set of addresses forever -- after an initial
warm-up, it contributes ~zero *new* addresses per instruction. A screen
transition looks like a burst: a run of addresses that have never
appeared before in this file, clustered at one point in the sequence,
followed by settling into a new (different) steady-state loop. That
burst region is a direct candidate for "the code that runs when this
screen changes" -- i.e. where to put a BP.

Method per file:
  1. Parse the fixed-width "CCCC:OOOOOOOO\n" records into a single
     48-bit integer key per line (vectorized, no Python-level hex
     parsing).
  2. np.unique(..., return_index=True) gives the first-occurrence line
     index of every distinct address in the file, for free.
  3. Histogram first-occurrence indices into buckets along the line
     axis, skipping an initial warm-up region (everything is "first
     occurrence" at the very start of any file, which is not signal).
  4. Buckets whose first-occurrence count is far above the local
     baseline are transition candidates. Report the addresses whose
     first occurrence falls in each flagged bucket, plus how many times
     each one recurs later in the same file (a one-shot handler recurs
     rarely; a loop body recurs constantly).

Usage:
    python3 analysis/find_screen_transitions.py logcpu_*.txt
    python3 analysis/find_screen_transitions.py logcpu_14.txt --bucket 2000 --warmup 20000
"""

import argparse
import sys
from pathlib import Path

import numpy as np

RECORD_LEN = 14  # "CCCC:OOOOOOOO\n"
HEX_COLS = [0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11, 12]  # skip ':' at 4, '\n' at 13

# Uppercase-hex-char -> nibble lookup (matches DOSBox-X's `uppercase` log format).
_LUT = np.zeros(256, dtype=np.uint64)
for d in range(10):
    _LUT[ord('0') + d] = d
for d in range(6):
    _LUT[ord('A') + d] = 10 + d


def load_keys(path):
    """
    Returns (keys, seg, off) where keys[i] is a single uint64 encoding
    cs:ip for line i (seg in the high 16 bits, offset in the low 32),
    and seg/off are the decoded uint32 arrays for display.
    """
    raw = np.fromfile(path, dtype=np.uint8)
    n = raw.size // RECORD_LEN
    raw = raw[: n * RECORD_LEN].reshape(n, RECORD_LEN)

    nibbles = _LUT[raw[:, HEX_COLS]]  # (n, 12)
    shifts = np.arange(11, -1, -1, dtype=np.uint64) * np.uint64(4)
    keys = (nibbles.astype(np.uint64) << shifts).sum(axis=1)

    seg = (keys >> np.uint64(32)).astype(np.uint32)
    off = (keys & np.uint64(0xFFFFFFFF)).astype(np.uint32)
    return keys, seg, off


def find_bursts(keys, seg, off, bucket=2000, warmup=20000, z=6.0, top=15):
    n = keys.size
    uniq, first_idx, counts_total = np.unique(keys, return_index=True, return_counts=True)

    # Only interested in addresses whose *first* appearance is after the
    # warm-up window -- i.e. genuinely new code paths, not "everything is
    # new because the file just started."
    mask = first_idx >= warmup
    fi = first_idx[mask]
    if fi.size == 0:
        print(f"  (nothing past warmup={warmup}; file may be shorter than expected)")
        return []

    n_buckets = n // bucket + 1
    bucket_idx = fi // bucket
    hist = np.bincount(bucket_idx, minlength=n_buckets)

    baseline = np.median(hist[hist > 0]) if np.any(hist > 0) else 0.0
    spread = np.std(hist) or 1.0
    threshold = max(baseline + z * spread, baseline + 5)

    flagged = np.where(hist > threshold)[0]

    # Merge adjacent flagged buckets into contiguous transition regions.
    regions = []
    for b in flagged:
        if regions and b == regions[-1][1] + 1:
            regions[-1] = (regions[-1][0], b)
        else:
            regions.append((b, b))

    results = []
    counts_by_key = dict(zip(uniq.tolist(), counts_total.tolist()))
    for start_b, end_b in regions:
        lo, hi = start_b * bucket, (end_b + 1) * bucket
        in_region = fi[(fi >= lo) & (fi < hi)]
        keys_in_region = keys[in_region]
        # Sort by how rarely each address recurs in the whole file --
        # a true one-shot dispatcher recurs a handful of times; a loop
        # body that merely *started* in this window recurs thousands of
        # times and is noise for our purposes.
        addrs = sorted(set(keys_in_region.tolist()), key=lambda k: counts_by_key[k])
        results.append((lo, hi, addrs, counts_by_key, hist[start_b:end_b + 1].sum()))

    results.sort(key=lambda r: -r[4])
    return results[:top]


def fmt_addr(key):
    seg = (key >> 32) & 0xFFFF
    off = key & 0xFFFFFFFF
    return f"{seg:04X}:{off:08X}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--bucket", type=int, default=2000, help="lines per histogram bucket (default 2000)")
    ap.add_argument("--warmup", type=int, default=20000, help="skip first-occurrences before this line (default 20000)")
    ap.add_argument("--z", type=float, default=6.0, help="stddev multiplier above baseline to flag a bucket (default 6.0)")
    ap.add_argument("--top", type=int, default=8, help="max transition regions to print per file (default 8)")
    ap.add_argument("--addrs-per-region", type=int, default=12)
    args = ap.parse_args()

    for path in args.files:
        keys, seg, off = load_keys(path)
        print(f"=== {path} ({keys.size} instructions) ===")
        regions = find_bursts(keys, seg, off, bucket=args.bucket, warmup=args.warmup, z=args.z, top=args.top)
        if not regions:
            print("  no burst regions found above threshold")
            continue
        for lo, hi, addrs, counts_by_key, weight in regions:
            print(f"  lines [{lo:>9}:{hi:<9}]  new-addr weight={weight}")
            for k in addrs[: args.addrs_per_region]:
                print(f"      {fmt_addr(k)}  total_count_in_file={counts_by_key[k]}")
            if len(addrs) > args.addrs_per_region:
                print(f"      ... and {len(addrs) - args.addrs_per_region} more")
        print()


if __name__ == "__main__":
    main()
