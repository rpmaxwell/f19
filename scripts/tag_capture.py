#!/usr/bin/env python3
"""
On-demand, labeled memory capture for building test cases: run this right
after doing one deliberate, describable thing in-game (fire a shot, note a
visual ID, hold level for a beat, etc). Captures only segments 3000h/4000h
(the region we've already identified as holding live, compact game state -
not the segment-5000h rendering buffer), so it freezes the game for only
~3-5s instead of the full survey's ~15-20s.

Appends one JSON record to dumps/test_cases.jsonl with a timestamp, your
label, and the captured bytes, then prints a diff against the previous
tagged capture so you can see immediately what changed.

Usage: python3 scripts/tag_capture.py "fired one AGM-88 at SAM site"
"""

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

TEST_CASE_LOG = f"{ml.PROJECT_DIR}/dumps/test_cases.jsonl"
TAG_SEGMENTS = ["3000", "4000"]  # 0x30000-0x4FFFF, where compact live state lives


def capture_tagged_region(win_id: str, segments: list = None) -> bytes | None:
    chunks = []
    for seg_hex in (segments or TAG_SEGMENTS):
        data = ml.capture_segment(win_id, seg_hex)
        if data is None:
            return None
        chunks.append(data)
    return b"".join(chunks)


def load_last_record():
    if not os.path.exists(TEST_CASE_LOG):
        return None
    last = None
    with open(TEST_CASE_LOG) as f:
        for line in f:
            last = line
    return json.loads(last) if last else None


def capture_and_log(win_id: str, label: str, print_diff: bool = True, segments: list = None) -> dict | None:
    """Capture the tagged region (default TAG_SEGMENTS, or a caller-supplied
    contiguous `segments` list -- e.g. trial_state_correlation.py's
    --segments), append it to TEST_CASE_LOG, print a diff against the
    previous tagged record, and return the new record (or None on capture
    failure)."""
    segments = segments or TAG_SEGMENTS
    base_offset = int(segments[0], 16) * 0x10  # real-mode paragraph->physical (matches
                                                # the original hardcoded 0x030000 for
                                                # segment "3000": 0x3000 * 0x10 = 0x30000)
    prev = load_last_record()

    print(f"Capturing ({label!r})...")
    data = capture_tagged_region(win_id, segments)
    if data is None:
        print("CAPTURE FAILED")
        return None

    now = datetime.now(timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "unix_ts": now.timestamp(),
        "label": label,
        "base_offset": base_offset,
        "segments": segments,
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
    }
    os.makedirs(os.path.dirname(TEST_CASE_LOG), exist_ok=True)
    with open(TEST_CASE_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"Saved. {record['sha256'][:12]}...")

    if print_diff and prev is not None and prev.get("segments") == segments:
        import numpy as np
        a = np.frombuffer(base64.b64decode(prev["data_b64"]), dtype=np.uint8)
        b = np.frombuffer(base64.b64decode(record["data_b64"]), dtype=np.uint8)
        diff = np.where(a != b)[0]
        print(f"\nvs previous tag ({prev['label']!r} at {prev['timestamp']}):")
        print(f"  {len(diff)} bytes changed")
        for off in diff[:40]:
            addr = record["base_offset"] + int(off)
            print(f"    0x{addr:06X}: {a[off]} -> {b[off]}")
        if len(diff) > 40:
            print(f"    ...and {len(diff)-40} more")

    return record


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 scripts/tag_capture.py "description of what you just did"')
        sys.exit(1)
    label = " ".join(sys.argv[1:])

    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    record = capture_and_log(win_id, label)
    if record is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
