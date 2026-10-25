#!/usr/bin/env python3
"""
Save/load DOSBox-X "checkpoint" game states so repeated test trials can
start from an identical, known-good point (on the runway ready for
takeoff, at cruise altitude, etc.) without a human re-flying pilot select
-> mission params -> loadout -> takeoff by hand before every single run.

Drives DOSBox-X's Capture menu directly ("Save state [Ctrl+Alt+S]" /
"Load state [Ctrl+Alt+L]" menu items), NOT an AppleScript-synthesized
Ctrl+Alt+S/L keystroke. Confirmed live (2026-08-08) that the keystroke path
(ml.press_char with modifiers) is unreliable here - it uses the same
CGEventPost-based synthetic-keyboard delivery already shown elsewhere in
this project not to reliably reach DOSBox-X (see FINDINGS.md), and repeated
checkpoint-load attempts during live testing needed several retries or
failed outright. Clicking the menu item instead goes through the
accessibility API's native menu-invoke mechanism, which is a different,
more reliable code path - confirmed working across many repeated
save/load cycles in the same live session where the keystroke path kept
failing.

`~/Library/Preferences/f19-dosbox.conf` still has `hostkey = ctrlalt`
(historical - see git history if curious why), but the menu click doesn't
depend on it: it invokes DOSBox-X's SaveGameState/LoadGameState functions
directly (src/misc/savestates.cpp) regardless of whatever hotkey happens to
be configured to trigger them, so this module no longer cares what the
configured host key is.

DOSBox-X save slot N is always file `<N>.sav` in the -savedir directory
(confirmed from source: `slotname << slot+1`, 0-indexed internally). This
project's active slot has stayed at 1 (file `saves/1.sav`) through every
session observed so far, including under autosave - see below for why that
turns out not to be a coincidence - so this module targets slot 1 directly
rather than driving DOSBox-X's slot-select UI (Ctrl+Alt+,/. to cycle slots)
blind.

Important interaction with autosave, confirmed from source (this cost real
time to track down, worth recording): f19-dosbox.conf used to set
`autosave = 5 1-200`, but DOSBox-X's autosave tick (src/hardware/vga_draw.cpp)
only honors an end slot <=100. With end=200 out of range, it silently fell
back to *always* targeting the range's start slot (1) - every 5 seconds,
for the whole session, never advancing - meaning slot 1 was a shared
scratch file autosave could overwrite between this module's copy-in and its
load trigger. autosave is now disabled entirely in the conf (2026-08-08 -
its periodic log-line burst was separately found to corrupt the debugger
console commands scripts/inject_key.py depends on, an unrelated but
higher-priority reason to turn it off), so this race window no longer
exists in practice - but never treat `saves/1.sav` as durable regardless;
only the sidecar `saves/checkpoint_<name>.sav` files this module writes are
meant to persist. load_checkpoint() still copies the checkpoint file over
slot 1 immediately before clicking Load state, on the theory that a tight
window remains cheap insurance even with autosave off.

Usage:
  python3 scripts/savestate.py save <name>   # save current state as a checkpoint
  python3 scripts/savestate.py load <name>   # load a saved checkpoint
  python3 scripts/savestate.py list          # list available checkpoints
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

ACTIVE_SLOT_FILE = f"{ml.SAVEDIR}/1.sav"
LOG_SETTLE_SECS = 0.3
LOG_POLL_TIMEOUT_SECS = 20.0  # live-observed 2026-08-16: repeatedly (3/3 attempts)
                               # the "Saved."/"Loaded." log line arrived tens of
                               # seconds after the real save/load had already
                               # completed (confirmed both times by checking
                               # saves/1.sav's own mtime against the wall clock) -
                               # not "system was busy," but almost certainly
                               # f19-dosbox.log being a regular file opened with
                               # libc's default full buffering, so a single short
                               # line sits in an internal buffer until something
                               # else forces a flush, with no fixed bound on how
                               # long that takes. No timeout value here is really
                               # "correct" against an unbounded flush delay; see
                               # save_checkpoint's switch to polling the slot file's
                               # mtime directly instead, which doesn't have this
                               # problem - load_checkpoint still has to use the log
                               # line since it has no equivalent artifact to poll.
SLOT_FILE_POLL_TIMEOUT_SECS = 25.0  # bumped again 2026-08-17: live-observed a real
                                     # save land at ~34s after the click (mtime-
                                     # confirmed), past even the previous 12s value -
                                     # the whole click+save cycle is just taking
                                     # longer under current conditions than earlier
                                     # sessions assumed, not a one-off fluke.
MENU_CLICK_ATTEMPTS = 4
MENU_CLICK_RETRY_SECS = 0.7


def _click_capture_menu_item(item_name: str) -> bool:
    """Clicks a Capture-menu item (e.g. "Save state [Ctrl+Alt+S]") via the
    accessibility API's native menu-invoke, not a synthesized keystroke.
    Retries on the transient "Can't get menu ... of menu bar 1" (-1728)
    error seen repeatedly in live testing (2026-08-08) - self-resolves
    within a second or two, unrelated to whether dosbox-x is frontmost
    (seen even right after explicitly setting frontmost)."""
    script = f'''
    tell application "System Events" to tell process "dosbox-x"
        click menu item "{item_name}" of menu "Capture" of menu bar 1
    end tell
    '''
    for attempt in range(1, MENU_CLICK_ATTEMPTS + 1):
        try:
            ml.run_osa('tell application "System Events" to set frontmost of process "dosbox-x" to true')
            ml.run_osa(script)
            return True
        except RuntimeError as e:
            if attempt == MENU_CLICK_ATTEMPTS:
                print(f"  [error] could not click {item_name!r} after {MENU_CLICK_ATTEMPTS} attempts: {e}")
                return False
            time.sleep(MENU_CLICK_RETRY_SECS)
    return False


def checkpoint_path(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return f"{ml.SAVEDIR}/checkpoint_{safe}.sav"


def list_checkpoints() -> list:
    if not os.path.isdir(ml.SAVEDIR):
        return []
    names = []
    for fname in sorted(os.listdir(ml.SAVEDIR)):
        if fname.startswith("checkpoint_") and fname.endswith(".sav"):
            names.append(fname[len("checkpoint_"):-len(".sav")])
    return names


def _log_size() -> int:
    return os.path.getsize(ml.LOG_PATH) if os.path.exists(ml.LOG_PATH) else 0


def _wait_for_log_line(baseline_size: int, needle: str, timeout: float = LOG_POLL_TIMEOUT_SECS) -> bool:
    """Poll f19-dosbox.log for a line containing `needle` appended after
    baseline_size - a concrete, observable confirmation that the save/load
    actually happened, mirroring memdump_loop.wait_for_fresh_dump's
    check-a-real-signal approach rather than just sleeping and hoping."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(ml.LOG_PATH) and os.path.getsize(ml.LOG_PATH) > baseline_size:
            with open(ml.LOG_PATH) as f:
                f.seek(baseline_size)
                new_text = f.read()
            if needle in new_text:
                time.sleep(LOG_SETTLE_SECS)
                return True
        time.sleep(0.1)
    return False


def _wait_for_slot_file_update(baseline_mtime, timeout: float = SLOT_FILE_POLL_TIMEOUT_SECS) -> bool:
    """Poll ACTIVE_SLOT_FILE's own mtime instead of the log line - live
    testing (2026-08-16) found the log-line confirmation false-timed-out on
    a save that had, per this same file's mtime, already completed (see
    LOG_POLL_TIMEOUT_SECS's docstring for why: the log file is almost
    certainly full-buffered, with no bound on flush delay). The save-state
    file itself is the actual artifact DOSBox-X's save operation writes, not
    a buffered text stream, so its mtime is a direct, unbuffered signal -
    same category of fix as memdump_loop.wait_for_fresh_dump polling the
    memdump file's own mtime/size instead of trusting a fixed sleep."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(ACTIVE_SLOT_FILE):
            mtime = os.path.getmtime(ACTIVE_SLOT_FILE)
            if baseline_mtime is None or mtime > baseline_mtime:
                time.sleep(0.2)  # let the write finish
                return True
        time.sleep(0.1)
    return False


def save_checkpoint(name: str) -> bool:
    """Saves the live game state to slot 1, then copies that file out to a
    named checkpoint so it survives autosave's next overwrite of slot 1."""
    path = checkpoint_path(name)
    baseline_mtime = os.path.getmtime(ACTIVE_SLOT_FILE) if os.path.exists(ACTIVE_SLOT_FILE) else None
    print("Saving current state to slot 1 (Capture menu > Save state)...")
    if not _click_capture_menu_item("Save state [Ctrl+Alt+S]"):
        return False
    if not _wait_for_slot_file_update(baseline_mtime):
        print(f"  [error] {ACTIVE_SLOT_FILE} did not update within "
              f"{SLOT_FILE_POLL_TIMEOUT_SECS:.0f}s.")
        return False
    if not os.path.exists(ACTIVE_SLOT_FILE):
        print(f"  [error] {ACTIVE_SLOT_FILE} doesn't exist after the mtime check passed.")
        return False
    os.makedirs(ml.SAVEDIR, exist_ok=True)
    with open(ACTIVE_SLOT_FILE, "rb") as src, open(path, "wb") as dst:
        dst.write(src.read())
    print(f"  Saved checkpoint {name!r} -> {path}")
    return True


MISMATCH_DIALOG_YES_FRAC = (0.4375, 0.6495)  # (x, y) fraction within the dosbox-x
                                              # window's own bounds, live-measured
                                              # 2026-08-18 against the "Program name
                                              # mismatch. Load the state anyway?"
                                              # warning dialog's Yes button.
MISMATCH_DIALOG_POLL_SECS = 3.0


def _dismiss_program_mismatch_dialog(timeout: float = MISMATCH_DIALOG_POLL_SECS) -> bool:
    """DOSBox-X pops an in-window 'Program name mismatch. Load the state
    anyway?' warning (Yes/No) whenever a checkpoint saved while one overlay
    EXE was running (e.g. EGAME, mid-flight) gets loaded while a DIFFERENT
    one is current (e.g. END, the debrief screen a crash leaves you on).
    Confirmed live 2026-08-18: this pauses the whole emulator (window title
    gains a ' PAUSED' suffix) until answered, and since nothing in this
    project's automation ever clicked it, every checkpoint load attempted
    right after a crash (i.e. exactly the situation run_direction() hits
    every time a prior direction crashed the plane) silently hung here -
    this was the actual, previously-unexplained cause of the repeated 'no
    Loaded. (Slot N) line appeared' timeouts logged throughout this
    project's session history, not slow log flushing as earlier fixes
    assumed (those fixes -- polling the slot file's mtime, longer timeouts
    -- were real improvements but were papering over this, not fixing it).
    Our named checkpoints are always the authoritative desired state
    regardless of what's currently running, so this always answers Yes.
    Returns True if a dialog was seen and dismissed, False if none appeared
    within timeout (the normal case when no crash preceded this load)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            title = ml.run_osa('tell application "System Events" to tell process "dosbox-x" to get name of window 1')
        except RuntimeError:
            return False
        if "PAUSED" in title:
            bounds = ml.get_dosbox_window_bounds()
            if not bounds:
                print("  [warn] program-mismatch dialog detected (title shows PAUSED) but "
                      "could not read window bounds to click Yes -- check the dosbox-x "
                      "window by hand.")
                return False
            x, y, w, h = bounds
            click_x = x + int(w * MISMATCH_DIALOG_YES_FRAC[0])
            click_y = y + int(h * MISMATCH_DIALOG_YES_FRAC[1])
            # Focus + click MUST be one osascript call, not two separate
            # ones - live-confirmed 2026-08-18 that a click sent in its own
            # separate run_osa() after a prior "set frontmost" call landed
            # nowhere useful (dialog stayed open, PAUSED persisted). This is
            # the exact same pitfall memdump_loop.py's
            # _dosbox_focus_click_lines already documented and worked around
            # for game keystrokes - focus set by one osascript invocation
            # doesn't reliably survive to a later, separate one. Also
            # confirmed live that Return/Enter selects NO (the dialog's safe
            # default), not Yes - don't substitute a keypress here.
            for attempt in range(1, 4):
                print(f"  [info] program-mismatch dialog detected (title shows PAUSED) -- "
                      f"clicking Yes at ({click_x}, {click_y}) to load anyway "
                      f"(attempt {attempt}/3)")
                ml.run_osa(
                    'tell application "System Events"\n'
                    'set frontmost of process "dosbox-x" to true\n'
                    f'click at {{{click_x}, {click_y}}}\n'
                    'end tell'
                )
                time.sleep(0.5)
                try:
                    title = ml.run_osa('tell application "System Events" to tell process "dosbox-x" to get name of window 1')
                except RuntimeError:
                    return False
                if "PAUSED" not in title:
                    return True
            print("  [warn] dialog still showing after 3 click attempts -- check the dosbox-x window by hand.")
            return False
        time.sleep(0.1)
    return False


def load_checkpoint(name: str) -> bool:
    """Copies a named checkpoint into slot 1, then tells DOSBox-X (via the
    Capture menu, not a keystroke) to load slot 1."""
    path = checkpoint_path(name)
    if not os.path.exists(path):
        print(f"  [error] no checkpoint named {name!r} at {path}. "
              f"Available: {list_checkpoints() or '(none)'}")
        return False
    os.makedirs(ml.SAVEDIR, exist_ok=True)
    with open(path, "rb") as src, open(ACTIVE_SLOT_FILE, "wb") as dst:
        dst.write(src.read())
    baseline = _log_size()
    print(f"Loading checkpoint {name!r} into slot 1 (Capture menu > Load state)...")
    if not _click_capture_menu_item("Load state [Ctrl+Alt+L]"):
        return False
    _dismiss_program_mismatch_dialog()
    if not _wait_for_log_line(baseline, "Loaded. (Slot"):
        print(f"  [error] no 'Loaded. (Slot N)' line appeared in {ml.LOG_PATH} within "
              f"{LOG_POLL_TIMEOUT_SECS:.0f}s.")
        return False
    print(f"  Loaded checkpoint {name!r}")
    return True


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("save", "load", "list"):
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        names = list_checkpoints()
        print("Checkpoints:" if names else "No checkpoints saved yet.")
        for n in names:
            print(f"  {n}")
        return

    if len(sys.argv) < 3:
        print(f"Usage: python3 scripts/savestate.py {cmd} <name>")
        sys.exit(1)
    name = sys.argv[2]

    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    if not ml.find_terminal_window_id():
        print("Could not find the Terminal window running dosbox-x.")
        sys.exit(1)

    ok = save_checkpoint(name) if cmd == "save" else load_checkpoint(name)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
