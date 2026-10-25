#!/usr/bin/env python3
"""
One-time investigative tool: takes N *correctly* full 1MB memory snapshots
of F-19 Stealth Fighter under dosbox-x.

memdump_loop.py's single `MEMDUMPBIN 0:0 100000` call turned out to only ever
capture the same low 64KB sixteen times over (DOSBox-X's debugger doesn't
advance the segment as the 16-bit offset wraps for len > 0x10000). This tool
works around that by issuing one MEMDUMPBIN call per 64KB segment
(0000:0, 1000:0, 2000:0, ... F000:0) and concatenating the results, so we
actually see the full real-mode address space.

This is slow (~16 debugger break-ins per snapshot, ~20-30s) so it's meant for
a short investigative session, not continuous collection. Fly the plane while
this runs so game state actually changes between snapshots.

Output: raw 1,048,576-byte binary files in dumps/survey/, one per snapshot,
named by capture time. Stops after N snapshots, on dosbox-x exit, or Ctrl+C.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml  # reuse launch/attach/break/resume helpers

SURVEY_DIR = f"{ml.PROJECT_DIR}/dumps/survey"
SEGMENT_LEN_HEX = "10000"  # 64KB per call
# 0x0000-0x9000: conventional memory below the 640KB video boundary (0xA0000),
# where program code/data - and any compact telemetry state - actually live.
# Segments A000-F000 (video RAM + BIOS ROM) are confirmed from an earlier
# cross-session diff to be huge/chaotic (framebuffer redraw noise), not useful
# as compact state, and skipping them roughly halves the freeze time per pass.
SEGMENTS = [f"{seg:X}" for seg in range(0x0000, 0xA000, 0x1000)]  # 10 segments -> 640KB
EXPECTED_CHUNK_SIZE = 0x10000
DEFAULT_SNAPSHOT_COUNT = 5

# Breaking into the debugger pauses the whole emulator. Each full 16-segment
# pass already freezes the game for ~25-30s; without an explicit gap here,
# back-to-back passes would compare state that's barely a tick apart. This is
# how long we let the game actually *run* (unpaused) between passes.
RUN_GAP_SECONDS = 10
MAX_CONSECUTIVE_SURVEY_FAILURES = 3


def wait_for_fresh_chunk(baseline_mtime) -> bool:
    deadline = time.time() + ml.FILE_WAIT_TIMEOUT_SECS
    while time.time() < deadline:
        if os.path.exists(ml.MEMDUMP_PATH):
            mtime = os.path.getmtime(ml.MEMDUMP_PATH)
            if baseline_mtime is None or mtime > baseline_mtime:
                time.sleep(0.15)
                if os.path.getsize(ml.MEMDUMP_PATH) == EXPECTED_CHUNK_SIZE:
                    return True
        time.sleep(0.1)
    return False


def capture_segment(win_id: str, seg_hex: str) -> bytes | None:
    baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH) if os.path.exists(ml.MEMDUMP_PATH) else None

    for attempt in range(1, ml.MAX_ATTEMPTS_PER_CYCLE + 1):
        # Option+F12 is a *toggle*. If a previous cycle's resume didn't land
        # (e.g. focus stolen by a cutscene/crash sequence), the debugger can
        # be left open, and the next break-in press would close it instead -
        # sending our MEMDUMPBIN text into the live DOS session instead of
        # the debugger console. Force a resync to "running" first so the
        # subsequent break-in press is always opening from a known state.
        try:
            ml.resume_execution(win_id)
        except RuntimeError:
            pass
        ml.break_into_debugger()
        ml.run_osa(
            f'tell application "Terminal" to do script '
            f'"MEMDUMPBIN {seg_hex}:0 {SEGMENT_LEN_HEX}" in window id {win_id}'
        )
        if wait_for_fresh_chunk(baseline_mtime):
            with open(ml.MEMDUMP_PATH, "rb") as f:
                data = f.read()
            ml.resume_execution(win_id)
            return data
        print(f"    [warn] segment {seg_hex} did not appear "
              f"(attempt {attempt}/{ml.MAX_ATTEMPTS_PER_CYCLE}), retrying...")
        ml.resume_execution(win_id)
        time.sleep(0.3)

    return None


def capture_full_survey(win_id: str) -> bytes | None:
    chunks = []
    expected_total = len(SEGMENTS) * EXPECTED_CHUNK_SIZE
    for i, seg_hex in enumerate(SEGMENTS, 1):
        print(f"    segment {i}/{len(SEGMENTS)} (seg {seg_hex}:0)...", end=" ", flush=True)
        data = capture_segment(win_id, seg_hex)
        if data is None:
            print("FAILED")
            return None
        print("ok")
        chunks.append(data)
    full = b"".join(chunks)
    assert len(full) == expected_total, f"expected {expected_total} bytes, got {len(full)}"
    return full


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SNAPSHOT_COUNT

    if not ml.dosbox_running():
        print("dosbox-x is not running. Launch the game first (via memdump_loop.py or manually).")
        sys.exit(1)

    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    os.makedirs(SURVEY_DIR, exist_ok=True)
    print(f"Attached to Terminal window {win_id}. Taking {n} full-range survey snapshots.")
    print(f"Output dir: {SURVEY_DIR}")
    print(f"Each pass freezes the game for ~15-20s ({len(SEGMENTS)} debugger break-ins), then runs "
          f"freely for {RUN_GAP_SECONDS}s before the next pass. Keep flying through the freezes.\n")

    taken = 0
    consecutive_failures = 0
    try:
        while taken < n and ml.dosbox_running():
            ts = time.time()
            print(f"[survey {taken+1}/{n}] capturing...")
            try:
                data = capture_full_survey(win_id)
            except RuntimeError as e:
                if not ml.dosbox_running():
                    break
                data = None
                print(f"[survey {taken+1}/{n}] error: {e}")

            if data is None:
                consecutive_failures += 1
                print(f"[survey {taken+1}/{n}] FAILED "
                      f"({consecutive_failures}/{MAX_CONSECUTIVE_SURVEY_FAILURES} consecutive failures)\n")
                if consecutive_failures >= MAX_CONSECUTIVE_SURVEY_FAILURES:
                    print("Too many consecutive failures - the debugger toggle is probably desynced "
                          "or the game is in an unexpected state (cutscene/crash/menu). Stopping "
                          "rather than hammering keystrokes indefinitely. Check the game manually.")
                    break
                continue

            consecutive_failures = 0
            fname = f"{SURVEY_DIR}/survey_{ts:.3f}.bin"
            with open(fname, "wb") as f:
                f.write(data)
            print(f"[survey {taken+1}/{n}] saved {fname}\n")
            taken += 1

            if taken < n and ml.dosbox_running():
                print(f"    (letting it run for {RUN_GAP_SECONDS}s before next pass...)\n")
                time.sleep(RUN_GAP_SECONDS)
    finally:
        if not ml.dosbox_running():
            print("dosbox-x process no longer running.")
        print(f"Done. {taken} survey snapshot(s) captured in {SURVEY_DIR}")


if __name__ == "__main__":
    main()
