#!/usr/bin/env python3
"""
Same test as stage1_keystroke_roundtrip.py - does a keystroke reach DOS at
the bare prompt? - but firing it via inject_key.py's BIOS-keyboard-buffer
write instead of ml.press_char()'s AppleScript-synthesized OS keystroke.
Reuses the identical, occlusion-guarded probe_watch.watch() harness so the
two results are a direct apples-to-apples comparison, not two different
measurement methods.

Prerequisite: dosbox-x already running under the bare-prompt conf (run
stage0_bareprompt.py first), sitting at the C:\\MPS\\F19> prompt.

Usage: python3 scripts/stage1b_debugger_injection.py [char] [delay_before_s]
"""
import sys
import threading
import time

sys.path.insert(0, __import__("os").path.dirname(__file__))
import memdump_loop as ml
import probe_watch as pw
import inject_key as ik

WATCH_DURATION_S = 12.0  # longer than stage1's 8s - the debugger round trip
                          # (break in, 2 commands, resume) takes a few real
                          # seconds by itself, on top of the injection delay
DEFAULT_CHAR = "9"
DEFAULT_DELAY_BEFORE_S = 2.0


def _fire(win_id: str, ch: str):
    print(f"  injecting via BIOS keyboard buffer: {ch!r}")
    ik.inject_key(win_id, ch)


def main():
    ch = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CHAR
    delay_s = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DELAY_BEFORE_S

    if not ml.dosbox_running():
        print("dosbox-x is not running - run stage0_bareprompt.py first.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the terminal window running dosbox-x.")
        sys.exit(1)

    print(f"Watching for {WATCH_DURATION_S:.0f}s, injecting {ch!r} at t+{delay_s:.1f}s "
          "via the BIOS keyboard buffer (debugger console), not OS keystrokes...")

    timer = threading.Timer(delay_s, _fire, args=(win_id, ch))
    timer.start()
    result = pw.watch(WATCH_DURATION_S, label=f"stage1b_{ch}")
    timer.join(timeout=0.1)

    print("\n--- RESULT ---")
    print(f"run dir: {result['run_dir']}")
    print(f"max changed-pixel count across window: {result['max_changed_px']}")
    if result["any_occlusion"]:
        print("[info] dosbox-x was not frontmost for at least one tick - expected "
              "during the injection itself (the debugger console runs in the "
              "terminal, not the dosbox-x window), as long as a persistent change "
              "is still found in the ticks after dosbox-x is refocused.")
    if result["first_persistent_change"]:
        fc = result["first_persistent_change"]
        print(f"PASS: persistent on-screen change first seen at t+{fc['elapsed']:.2f}s "
              f"({fc['changed_px']} px) - consistent with the injected keystroke "
              "reaching DOS and being echoed.")
    else:
        print("FAIL: no persistent on-screen change detected anywhere in the watch "
              "window, even via direct BIOS-buffer injection.")


if __name__ == "__main__":
    main()
