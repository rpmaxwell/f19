#!/usr/bin/env python3
"""
Step 4 of FINDINGS.md's action plan: statically re-verify the Hacking-F117A
mission-briefing-block fingerprint against our actual game files, without
needing the emulator.

The confirmed live layout (ds:B936-relative, from a *running* F-19 v435.04
process) is two 18-byte (0x12) blocks back-to-back, each shaped
[type, target, takeoff/landing, code, number]. Since file offset != a
runtime segment-relative offset (MZ executables load at whatever segment DOS
assigns), this scans the *entire* file content for the byte-pattern shape
instead of trusting any fixed offset arithmetic.

Per-tuple predicate:
  byte0 (type):    1-4
  byte1 (target):  <= 0x20         (plausible small theater target id)
  byte2 (takeoff/landing target): unconstrained
  byte3 (code):    0x2F when type==4 (landing), 0x3A when type==3 (supply_drop)
                   -- unconfirmed/weaker signal for type 1/2 (photograph/destroy),
                   so byte3 is NOT constrained when byte0 in {1,2}
  byte4 (number):  <= 60 (a little headroom over FINDINGS.md's "0-50ish")

A candidate offset o is reported when the predicate holds at both o and
o+0x12 (the two mission blocks, primary + secondary).

Usage: python3 analysis/find_mission_fingerprint.py
"""

import os

GAME_DIR = os.path.expanduser("~/Documents/F-19 Stealth Fighter/MPS/F19")
BLOCK_GAP = 0x12
MAX_TARGET = 0x20
MAX_NUMBER = 60
CODE_BY_TYPE = {4: 0x2F, 3: 0x3A}  # landing, supply_drop

# Mission-related first (most likely home of the block), then the rest of
# the MS-C-linked EXEs for completeness.
FILES = ["START.EXE", "EGAME.EXE", "SU.EXE", "DS.EXE", "END.EXE"]


def tuple_at(data: bytes, o: int) -> bytes:
    return data[o:o + 5]


def loose_ok(t: bytes) -> bool:
    if len(t) < 5:
        return False
    type_, target, _takeoff, _code, number = t
    return type_ in (1, 2, 3, 4) and target <= MAX_TARGET and number <= MAX_NUMBER


def strict_ok(t: bytes) -> bool | None:
    """True/False if type has a confirmed code mapping (3 or 4), None if
    unconfirmed (1 or 2, per FINDINGS.md: 'weaker signal for 1/2')."""
    type_ = t[0]
    expected = CODE_BY_TYPE.get(type_)
    if expected is None:
        return None
    return t[3] == expected


def scan_file(path: str):
    with open(path, "rb") as f:
        data = f.read()

    single_loose_count = 0
    candidates = []

    last_start = len(data) - BLOCK_GAP - 5
    for o in range(0, max(last_start, 0)):
        t1 = tuple_at(data, o)
        if not loose_ok(t1):
            continue
        single_loose_count += 1

        t2 = tuple_at(data, o + BLOCK_GAP)
        if not loose_ok(t2):
            continue

        s1, s2 = strict_ok(t1), strict_ok(t2)
        if s1 is True and s2 is True:
            confidence = "strict (both tuples' codes match type)"
        elif s1 is False or s2 is False:
            confidence = "loose (a tuple's code contradicts its type -- likely noise)"
        else:
            confidence = "loose (type 1/2, code unconfirmed by prior art)"

        candidates.append((o, t1, t2, confidence))

    return data, single_loose_count, candidates


def main():
    for fname in FILES:
        path = os.path.join(GAME_DIR, fname)
        if not os.path.exists(path):
            print(f"=== {fname}: NOT FOUND at {path}, skipping ===")
            continue

        data, single_loose_count, candidates = scan_file(path)
        print(f"=== {fname} ({len(data)} bytes) ===")
        print(f"  single-tuple loose-predicate hits (false-positive baseline): {single_loose_count}")
        print(f"  paired-tuple candidates (o and o+0x12 both pass): {len(candidates)}")
        for o, t1, t2, confidence in candidates:
            print(f"    offset 0x{o:06X}: "
                  f"tuple1={t1.hex()} tuple2={t2.hex()}  [{confidence}]")
        print()


if __name__ == "__main__":
    main()
