#!/usr/bin/env python3
"""
Step 6 of FINDINGS.md's action plan: DOSBox-X memory write breakpoints,
replacing tag_capture.py's statistical before/after diff polling with an
exact break at the writing instruction.

Extends the same UI-automation primitives memdump_loop.py already uses
(osascript/System Events driving the Terminal window running dosbox-x) --
does not modify memdump_loop.py itself, only imports from it.

Mechanism (UNCONFIRMED until run against a live session -- see module
docstring note below): while broken into the debugger, send DOSBox-X's
`BPM <seg>:<off>` command to set a break-on-memory-write, then resume with
F5. If the byte gets written, DOSBox-X should auto-break back into the
debugger -- detected by polling the window title for "DEBUGGER" reappearing
(the mirror image of memdump_loop.wait_for_debugger_closed, which already
polls for it *disappearing* after a manual resume). Whether the title
actually flips on an *automatic* breakpoint break the same way it does on a
manual Option+F12 break-in is the open question this script is meant to
validate.

There's no existing register/call-stack text capture in this pipeline
(MEMDUMPBIN only dumps memory) and logs/f19-dosbox.log was checked -- it
only contains autosave lines, no debugger echo. So on a detected break, this
takes a full-screen screenshot (same approach as scripted_test_run.py's
take_screenshot(), via `screencapture -x`) to capture the debugger's
on-screen register/disassembly panel.

DO NOT generalize this against unconfirmed Phase-2 (Ghidra) address
candidates first. Validate against DEFAULT_TARGET below -- a byte at
physical offset 0x03C5F0 (segment 3000h, offset 0xC5F0) that changed
consistently across all 9 repeats of the "cycled ordnance" (Space key)
action in the existing dumps/test_cases.jsonl tagged captures, with zero
contamination from background/idle noise (see analysis/candidate_addresses.md).
It's very likely a generic "input processed" or screen-redraw flag rather
than something ordnance-specific (the same offset also changes on bay-doors/
sidewinder/cannon actions), but that's fine for proving send/detect/capture
end-to-end -- precision comes later once the mechanism is confirmed.

Usage: python3 scripts/breakpoint_capture.py [--seg SEG] [--off OFF] [--action "description of what you'll do"]
  (requires dosbox-x + F-19 already running, per memdump_loop.py's other tools)
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

SCREENSHOT_DIR = f"{ml.PROJECT_DIR}/dumps/screenshots"
DEFAULT_SEG = "3000"
DEFAULT_OFF = "C5F0"
BREAK_POLL_TIMEOUT_SECS = 15.0
BREAK_POLL_INTERVAL_SECS = 0.2


def take_screenshot(label: str) -> str:
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    safe_label = "".join(c if c.isalnum() else "_" for c in label)[:60]
    path = f"{SCREENSHOT_DIR}/{time.time():.0f}_bp_{safe_label}.png"
    subprocess.run(["screencapture", "-x", path], check=True)
    return path


def send_bpm_command(win_id: str, seg_hex: str, off_hex: str):
    """Set a break-on-memory-write while already broken into the debugger."""
    ml.send_raw_command(win_id, f"BPM {seg_hex}:{off_hex}")


def get_window_title(timeout_ok: bool = True) -> str | None:
    try:
        return ml.run_osa(
            'tell application "System Events" to tell process "dosbox-x" '
            'to get name of window 1'
        )
    except RuntimeError:
        return None if timeout_ok else ""


def wait_for_debugger_reopened(timeout: float = BREAK_POLL_TIMEOUT_SECS) -> bool:
    """Mirror of memdump_loop.wait_for_debugger_closed: poll for the
    dosbox-x window title showing DEBUGGER again after a resume, which
    would indicate the BPM breakpoint fired. Returns False on timeout
    (either the breakpoint never fired, or the title doesn't change the
    way we're assuming -- both are useful findings)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        title = get_window_title()
        if title and "DEBUGGER" in title:
            return True
        time.sleep(BREAK_POLL_INTERVAL_SECS)
    return False


# Resume-with-confirmation used to be duplicated here as `_resume_and_confirm`
# (RESUME_CONFIRM_TIMEOUT_SECS / MAX_RESUME_ATTEMPTS retry loop). That fix has
# now been ported into the shared primitive, memdump_loop.resume_execution -
# see its docstring - so this module calls `ml.resume_execution` directly
# instead of keeping its own copy that could drift out of sync again.


def set_breakpoint_and_arm(win_id: str, seg_hex: str, off_hex: str) -> bool:
    """Assumes we're already broken into the debugger (call
    ml.break_into_debugger() first). Sets BPM, then resumes with F5 so the
    breakpoint is live and the game is running again, waiting to trigger.
    Not confirming the resume landed was the actual cause of
    breakpoint_capture.py reporting near-instant, bogus "fired" results -
    it was catching its own stale DEBUGGER title, not a real memory-write
    break. Returns False if resume couldn't be confirmed after
    MAX_RESUME_ATTEMPTS - the caller should NOT proceed to
    wait_for_debugger_reopened() in that case."""
    send_bpm_command(win_id, seg_hex, off_hex)
    time.sleep(0.3)
    return ml.resume_execution(win_id)


def capture_break(label: str, win_id: str, seg_hex: str, off_hex: str) -> dict:
    """Full cycle: break in, set + arm the breakpoint, prime past its
    guaranteed-spurious first fire, wait for the action to happen (caller
    triggers it manually in-game during the wait window), detect the auto-
    break, screenshot it, then leave the debugger open for manual
    inspection (does NOT auto-resume, unlike the memdump pipeline -- you
    want to look at the break state before continuing)."""
    print(f"Arming breakpoint at {seg_hex}:{off_hex} ({label!r})...")
    ml.break_into_debugger()
    armed = set_breakpoint_and_arm(win_id, seg_hex, off_hex)
    if not armed:
        print("Could not confirm the debugger actually resumed after arming -- "
              "aborting before the wait, since a stuck-open debugger would "
              "otherwise be indistinguishable from a genuine breakpoint hit "
              "(see set_breakpoint_and_arm's docstring). Check the dosbox-x "
              "window / debugger console by hand; it's likely still paused.")
        return {"label": label, "seg": seg_hex, "off": off_hex, "fired": False,
                "screenshot": None, "resume_failed": True}

    # DOSBox-X's BKPNT_MEMORY breakpoints initialize their "last known
    # value" baseline to 0 in CBreakpoint's constructor and never seed it
    # from the byte's real value in AddMemBreakpoint() (confirmed by
    # reading dosbox-x/src/debug/debug.cpp directly). So the first time an
    # armed memory breakpoint gets checked, it compares that hardcoded 0
    # against whatever the byte's real value already is and (unless the
    # byte genuinely is 0) fires immediately -- a guaranteed artifact, not
    # a real write. DOSBox-X does call bp->SetValue() on that match, which
    # seeds the breakpoint with the real baseline going forward, so this
    # first fire just needs to be discarded (and resumed past, without
    # re-arming) before any later fire can be trusted.
    print("Priming: discarding the guaranteed-spurious first breakpoint "
          "check (see capture_break's docstring)...")
    primed_fire = wait_for_debugger_reopened(timeout=ml.RESUME_CONFIRM_TIMEOUT_SECS)
    if primed_fire:
        print("  spurious first fire observed, as expected -- resuming past it...")
        if not ml.resume_execution(win_id):
            print("Could not confirm resume after the priming fire -- aborting. "
                  "Check the dosbox-x window / debugger console by hand.")
            return {"label": label, "seg": seg_hex, "off": off_hex, "fired": False,
                    "screenshot": None, "resume_failed": True}
    else:
        print("  no priming fire seen within the timeout (byte's real value "
              "was likely already 0) -- proceeding directly.")

    print(f"Waiting up to {BREAK_POLL_TIMEOUT_SECS:.0f}s for the breakpoint to fire -- "
          f"trigger the action in-game now.")
    fired = wait_for_debugger_reopened()

    result = {"label": label, "seg": seg_hex, "off": off_hex, "fired": fired}
    if fired:
        print("Breakpoint FIRED -- debugger reopened. Capturing screenshot...")
        time.sleep(0.3)  # let the debugger panel finish rendering
        path = take_screenshot(label)
        result["screenshot"] = path
        print(f"  screenshot: {path}")
        print("  Debugger left OPEN for manual inspection (CS:IP / registers "
              "visible on screen). Resume manually (F5 in the dosbox-x window) "
              "when done.")
    else:
        print("Breakpoint did NOT fire within timeout -- either the action "
              "wasn't triggered, the byte doesn't actually get written by "
              "that action, or (most likely, per this script's docstring) "
              "the window-title-based detection doesn't work the way "
              "wait_for_debugger_closed's mirror image assumes. Check the "
              "dosbox-x window manually.")
        result["screenshot"] = None

    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seg", default=DEFAULT_SEG, help="segment hex, e.g. 3000")
    parser.add_argument("--off", default=DEFAULT_OFF, help="offset hex, e.g. C5F0")
    parser.add_argument("--action", default="cycled ordnance (space)",
                         help="description of what you'll do in-game to trigger the write")
    args = parser.parse_args()

    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    capture_break(args.action, win_id, args.seg, args.off)


if __name__ == "__main__":
    main()
