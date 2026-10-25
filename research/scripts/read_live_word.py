#!/usr/bin/env python3
"""
One-off: break into the DOSBox-X debugger, point the data-view panel at a
given seg:off with the `D` command, screenshot it, then resume. Used to
read a live scalar value (not to set a breakpoint) -- e.g. ds:8798, the
candidate "currently tracked threat index" scalar found via the
find_entity_loop_candidates.py / decompile_candidates.py Ghidra pass.

Usage: python3 scripts/read_live_word.py <seg>:<off> [label]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

SCREENSHOT_DIR = f"{ml.PROJECT_DIR}/dumps/screenshots"


def take_screenshot(label: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    safe_label = "".join(c if c.isalnum() else "_" for c in label)[:60]
    path = f"{SCREENSHOT_DIR}/{time.time():.0f}_read_{safe_label}.png"
    import subprocess
    subprocess.run(["screencapture", "-x", path], check=True)
    return path


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/read_live_word.py <seg>:<off> [label]")
        sys.exit(1)
    target = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else target

    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    ml.break_into_debugger()
    ml.send_raw_command(win_id, f"D {target}")
    time.sleep(0.5)
    path = take_screenshot(label)
    print(f"screenshot: {path}")
    ml.resume_execution(win_id)


if __name__ == "__main__":
    main()
