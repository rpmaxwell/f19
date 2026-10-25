#!/usr/bin/env python3
"""
Stage 0 of the keystroke-delivery test environment (FINDINGS.md, 2026-08-07):
launches dosbox-x under a bare-DOS-prompt conf (mounts C:, cd's into
MPS/F19, but does NOT run F19.COM) so keystroke tests run against the
simplest possible target - a plain "C:\\MPS\\F19>" prompt - with none of
F19's own mapper bindings, modal screens, or world-simulation noise in the
picture. Every diagnostic screenshot from the previous session's testing
showed the game frozen on the same "MISSION ORDERS" screen for 14 hours
straight; this sidesteps that class of problem entirely by not loading the
game at all yet.

Refuses to run if a dosbox-x process is already up: ml.dosbox_running() /
find_terminal_window_id() assume a single instance, and this needs to
control exactly which conf that instance was launched with (otherwise a
script could silently attach to an already-running real-game session and
every downstream assumption in this test would be wrong).

Usage: python3 scripts/stage0_bareprompt.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

BAREPROMPT_CONF = os.path.expanduser("~/Library/Preferences/f19-dosbox-bareprompt.conf")
SETTLE_SECS = 1.5  # let COMMAND.COM finish the mount/cd autoexec lines


def ensure_bareprompt_running() -> str:
    if ml.dosbox_running():
        raise RuntimeError(
            "dosbox-x is already running - can't tell whether it's on the "
            "bare-prompt conf or the real game conf, and this test needs to "
            "know for certain. Quit it first (Alt+Q from the game, or exit "
            "the DOS prompt / close the window) and re-run this script."
        )
    win_id = ml.launch_game(conf_path=BAREPROMPT_CONF)
    time.sleep(SETTLE_SECS)
    return win_id


def main():
    if not os.path.exists(BAREPROMPT_CONF):
        print(f"Missing {BAREPROMPT_CONF}.")
        sys.exit(1)
    win_id = ensure_bareprompt_running()
    print(f"dosbox-x up under the bare-prompt conf, attached to {win_id!r}.")
    print(r"Should be sitting at the C:\MPS\F19> prompt now - confirm visually "
          "before running stage1_keystroke_roundtrip.py.")


if __name__ == "__main__":
    main()
