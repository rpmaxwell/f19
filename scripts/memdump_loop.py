#!/usr/bin/env python3
"""
Launches (or attaches to) F-19 Stealth Fighter under dosbox-x and periodically
breaks into the DOSBox-X debugger to pull a memory snapshot covering the
640KB conventional-memory region (0x0000-0x9FFFF), recording each snapshot
as one line of JSON (JSONL) with a timestamp.

A single `MEMDUMPBIN 0:0 <len>` call turned out to only ever re-read the same
low 64KB sixteen times (DOSBox-X's debugger doesn't advance the segment as
the 16-bit offset wraps for len > 0x10000), so this issues one MEMDUMPBIN per
64KB segment (0000:0, 1000:0, ... 9000:0) and concatenates them - 10
break-ins per snapshot, ~15-20s frozen each time. Video RAM / BIOS ROM
(segments A000-F000) is skipped: an earlier cross-session diff showed it's
huge, chaotic framebuffer noise, not useful as compact state.

Requires: Accessibility permission granted to the process driving this script
(System Settings -> Privacy & Security -> Accessibility), since it drives
the dosbox-x window and Terminal via macOS System Events / AppleScript.

Stops automatically when the dosbox-x process exits.
"""

import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT_DIR = "/Users/robmaxwell/projects/f19"
CONF_PATH = os.path.expanduser("~/Library/Preferences/f19-dosbox.conf")
SAVEDIR = f"{PROJECT_DIR}/saves"
MEMDUMP_PATH = f"{PROJECT_DIR}/MEMDUMP.BIN"  # dosbox-x cwd is PROJECT_DIR
OUTPUT_JSONL = f"{PROJECT_DIR}/dumps/memdumps.jsonl"
PIDFILE = f"{PROJECT_DIR}/dumps/memdump_loop.pid"
LOG_PATH = f"{PROJECT_DIR}/logs/f19-dosbox.log"  # matches conf's [log] logfile=

INTERVAL_SECONDS = 60  # each capture itself takes ~15-20s (10 break-ins), so leave real running time between
SEGMENT_LEN_HEX = "10000"  # 64KB per MEMDUMPBIN call
SEGMENTS = [f"{seg:04X}" for seg in range(0x0000, 0xA000, 0x1000)]  # 10 segments -> 640KB;
# zero-padded (04X not X) -- segment 0 is the only value in this range under
# 0x1000, so it's the only one that formatted as a bare "0" instead of 4 hex
# digits. That produced "MEMDUMPBIN 0:0 10000" instead of "MEMDUMPBIN 0000:0
# 10000", which DOSBox-X's debugger console rejects as "*** Debugger command
# not recognized" (live-confirmed 2026-08-18, --segments full's first-ever
# exercise of segment 0 -- every prior capture in this project started at
# segment 3000 or above, so this was never hit before).
EXPECTED_CHUNK_SIZE = 0x10000
DEBUGGER_SETTLE_SECS = 0.6
FILE_WAIT_TIMEOUT_SECS = 3.0
MAX_ATTEMPTS_PER_CYCLE = 3

PROC_MATCH = "dosbox-x"  # matches both `dosbox-x -conf ...` and a bare default-config launch


def run_osa(script: str) -> str:
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"osascript failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _detect_dosbox_cwd(pid: str) -> str | None:
    result = subprocess.run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def dosbox_running() -> bool:
    """True if dosbox-x is running. Side effect: resyncs MEMDUMP_PATH to
    the process's *actual* current working directory via `lsof`, rather
    than assuming it's PROJECT_DIR -- confirmed live 2026-08-13 that a
    dosbox-x launched directly from its own build dir
    (~/projects/dosbox-x/src, not via this project's launch_game(), which
    always `cd`s to PROJECT_DIR first) writes MEMDUMPBIN's output there
    instead, and every capture silently timed out polling the wrong path.
    Every script in this project calls dosbox_running() as its first gate
    before any capture, so this keeps MEMDUMP_PATH correct without every
    caller needing its own cwd-detection logic. Falls back to leaving
    MEMDUMP_PATH unchanged if lsof can't read the process's cwd (e.g. a
    sandboxing/permission issue) rather than failing the running check."""
    global MEMDUMP_PATH
    result = subprocess.run(["pgrep", "-f", PROC_MATCH], capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return False
    pid = result.stdout.strip().splitlines()[0]
    cwd = _detect_dosbox_cwd(pid)
    if cwd:
        MEMDUMP_PATH = f"{cwd}/MEMDUMP.BIN"
    return True


def get_dosbox_window_bounds():
    """Returns (x, y, w, h) of dosbox-x's window 1, or None if it can't be
    read. `get {position, size} of window 1` returns a flat 4-number
    string like "595, 186, 640, 428" (confirmed live), not a nested list."""
    try:
        out = run_osa(
            'tell application "System Events" to tell process "dosbox-x" '
            'to get {position, size} of window 1'
        )
        x, y, w, h = (int(p.strip()) for p in out.split(","))
        return (x, y, w, h)
    except (RuntimeError, ValueError):
        return None


def _dosbox_focus_click_lines() -> str:
    """AppleScript lines (no enclosing tell block) that focus dosbox-x and
    click inside its window's content area - NOT just the title bar, and
    NOT a separate `focus_dosbox()` call before the keystroke.

    Found live (2026-08-07) that `set frontmost of process "dosbox-x" to
    true` can report success (and even survive an explicit AXRaise on the
    window) yet real OS keyboard focus stayed on the terminal running this
    automation - a keystroke sent as a *separate* subsequent osascript call
    landed in the terminal's own input, not dosbox-x, despite every focus
    check in between passing. A live click inside the window, in the SAME
    osascript invocation as the keystroke that follows it, is what
    actually moves real key focus - `focus_dosbox()`'s old "set frontmost,
    then trust it" approach doesn't move it reliably by itself, and
    focus set by one osascript call was not observed to survive to a
    later, separate call.

    Clicks near the top of the content area (inside the sky/compass HUD
    region), not the bottom instrument panel, since several of those
    bottom-panel icons (AUTO/GEAR/TRAK/MISSION/ECM/BAY/IRJ/DCY) are drawn
    like clickable buttons and F-19 is otherwise an all-keyboard interface
    per the Quick Reference Card - avoid the risk of a stray click
    actually toggling something in-game."""
    bounds = get_dosbox_window_bounds()
    lines = ['set frontmost of process "dosbox-x" to true']
    if bounds:
        x, y, w, h = bounds
        click_x = x + w // 2
        click_y = y + int(h * 0.15)
        lines.append(f"click at {{{click_x}, {click_y}}}")
    return "\n".join(lines)


def focus_dosbox(max_attempts: int = 5):
    """Focus dosbox-x (frontmost + a click inside its window) and *verify*
    it actually became frontmost before returning. Only useful as a
    standalone check now - press_char/press_key_code do their own
    focus+click combined with the actual keystroke in one osascript call,
    since focus set here was found not to reliably survive to a later,
    separate call (see _dosbox_focus_click_lines's docstring)."""
    for attempt in range(1, max_attempts + 1):
        run_osa(f'tell application "System Events"\n{_dosbox_focus_click_lines()}\nend tell')
        time.sleep(0.2)
        front = run_osa('tell application "System Events" to name of first process whose frontmost is true')
        if front == "dosbox-x":
            return
        print(f"    [warn] focus landed on {front!r} instead of dosbox-x (attempt {attempt}/{max_attempts}), retrying...")
        time.sleep(0.2)
    raise RuntimeError(f"Could not focus dosbox-x after {max_attempts} attempts")


def press_char(ch: str, modifiers=None, repeat: int = 1, hold_delay: float = 0.15):
    """Types `ch` (a literal character, e.g. '9' or '+' - use the actual
    shifted character rather than modifiers=['shift'] for shifted keys,
    since AppleScript's keystroke synthesizes whatever's needed to produce
    that character) into dosbox-x. `modifiers` is a list of standard
    AppleScript modifier names: 'command'/'option'/'control'/'shift'.
    Focus+click and the keystroke are sent as ONE osascript call (see
    _dosbox_focus_click_lines) - splitting them was the root cause of
    every action in a checkpoint-reload trial silently doing nothing."""
    mod_clause = f" using {{{', '.join(m + ' down' for m in modifiers)}}}" if modifiers else ""
    escaped = ch.replace('"', '\\"')
    for _ in range(repeat):
        script = (
            'tell application "System Events"\n'
            f"{_dosbox_focus_click_lines()}\n"
            f'keystroke "{escaped}"{mod_clause}\n'
            "end tell"
        )
        run_osa(script)
        time.sleep(hold_delay)


def press_key_code(code: int, modifiers=None):
    """Sends a raw macOS virtual key code (for keys with no simple
    character representation, e.g. the numeric keypad's ANSI_Keypad*
    codes 82-92, which are physically distinct from the top-row digit
    keys used elsewhere in this game's controls). Focus+click and the key
    code are sent as one osascript call - see press_char's docstring."""
    mod_clause = f" using {{{', '.join(m + ' down' for m in modifiers)}}}" if modifiers else ""
    script = (
        'tell application "System Events"\n'
        f"{_dosbox_focus_click_lines()}\n"
        f"key code {code}{mod_clause}\n"
        "end tell"
    )
    run_osa(script)


def get_dosbox_tty() -> str | None:
    """Controlling tty of the running dosbox-x process, e.g. '/dev/ttys002'.
    Used to locate its session in terminal apps (like iTerm2) that don't
    expose a 'processes of window contains X' style lookup the way
    Terminal.app does."""
    result = subprocess.run(["pgrep", "-f", PROC_MATCH], capture_output=True, text=True)
    pid = result.stdout.strip().splitlines()[0] if result.stdout.strip() else None
    if not pid:
        return None
    result = subprocess.run(["ps", "-o", "tty=", "-p", pid], capture_output=True, text=True)
    tty = result.stdout.strip()
    return f"/dev/{tty}" if tty and tty != "??" else None


def find_iterm_window_id() -> str:
    """Returns 'iterm:<tty>' if an iTerm2 session with dosbox-x's tty is
    found, else ''. Encoding the app + tty into the single win_id string
    keeps every downstream function (capture_segment, tag_capture.py,
    scripted_test_run.py, breakpoint_capture.py) working unchanged -- they
    just pass this string back into send_raw_command/focus_terminal_window,
    which branch on the 'iterm:' prefix."""
    tty = get_dosbox_tty()
    if not tty:
        return ""
    script = f'''
    tell application "iTerm2"
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    if (tty of s) is "{tty}" then
                        return "found"
                    end if
                end repeat
            end repeat
        end repeat
    end tell
    return ""
    '''
    found = run_osa(script)
    return f"iterm:{tty}" if found == "found" else ""


def find_terminal_window_id() -> str:
    """Locates whichever terminal app is actually running dosbox-x. Tries
    Terminal.app first (the original assumption), falls back to iTerm2 --
    dosbox-x doesn't care which terminal launched it, only our own
    automation needs to know where to type debugger commands."""
    script = '''
    tell application "Terminal"
        set winList to every window
        repeat with w in winList
            try
                if (processes of w) contains "dosbox-x" then
                    return id of w
                end if
            end try
        end repeat
    end tell
    return ""
    '''
    win_id = run_osa(script)
    if win_id:
        return win_id
    return find_iterm_window_id()


def send_raw_command(win_id: str, text: str):
    """Types `text` + Return into whichever terminal is running dosbox-x's
    debugger console (its stdin), dispatching on win_id's encoding."""
    if win_id.startswith("iterm:"):
        tty = win_id[len("iterm:"):]
        escaped = text.replace('\\', '\\\\').replace('"', '\\"')
        script = f'''
        tell application "iTerm2"
            repeat with w in windows
                repeat with t in tabs of w
                    repeat with s in sessions of t
                        if (tty of s) is "{tty}" then
                            tell s to write text "{escaped}"
                            return
                        end if
                    end repeat
                end repeat
            end repeat
        end tell
        '''
        run_osa(script)
    else:
        escaped = text.replace('\\', '\\\\').replace('"', '\\"')
        run_osa(f'tell application "Terminal" to do script '
                f'"{escaped}" in window id {win_id}')


def focus_terminal_window(win_id: str, max_attempts: int = 5):
    """Brings the terminal window running dosbox-x's debugger console to
    the front -- needed before raw keystrokes (like F5) sent via System
    Events with no target process, since those go to whatever's OS-level
    frontmost. send_raw_command's iTerm2 path (write text) does NOT need
    this -- it writes directly to the session regardless of focus.

    The iTerm2 branch verifies real OS-level frontmost and retries instead
    of trusting the AppleScript call blindly -- candidate_addresses.md's
    "Phase 3 LIVE VALIDATION" entry documented a real F5-resume failure
    where `activate` + `select t`/`select w` in one go reported success but
    left OS focus elsewhere (game stuck frozen in the debugger), confirmed
    live again 2026-08-13: a bare `tell application "iTerm2" to activate`
    landed real focus reliably where the combined select-tab/select-window
    script hadn't. Falling back to plain `activate` alone on retry matches
    that finding."""
    if win_id.startswith("iterm:"):
        tty = win_id[len("iterm:"):]
        select_script = f'''
        tell application "iTerm2"
            activate
            repeat with w in windows
                repeat with t in tabs of w
                    repeat with s in sessions of t
                        if (tty of s) is "{tty}" then
                            select t
                            select w
                            return
                        end if
                    end repeat
                end repeat
            end repeat
        end tell
        '''
        front = ""
        for attempt in range(1, max_attempts + 1):
            run_osa(select_script)
            time.sleep(0.2)
            front = run_osa('tell application "System Events" to name of first process whose frontmost is true')
            if front == "iTerm2":
                return
            run_osa('tell application "iTerm2" to activate')
            time.sleep(0.2)
            front = run_osa('tell application "System Events" to name of first process whose frontmost is true')
            if front == "iTerm2":
                return
            print(f"    [warn] iTerm2 focus attempt {attempt}/{max_attempts} landed on {front!r}, retrying...")
        print(f"    [warn] could not confirm iTerm2 focus after {max_attempts} attempts (last frontmost: {front!r})")
    else:
        script = f'''
        tell application "Terminal"
            activate
            set index of window id {win_id} to 1
        end tell
        '''
        run_osa(script)


DOSBOX_X_BIN = "/Users/robmaxwell/projects/dosbox-x/src/dosbox-x"  # NEVER the bare
# "dosbox-x" name -- /opt/homebrew/bin/dosbox-x sits earlier on $PATH and
# silently resolves to the stock Homebrew build instead (confirmed live,
# see analysis/candidate_addresses.md's "Automation-socket bind mystery -
# RESOLVED" entry): that build lacks this project's automation-socket patch
# (dosbox_automation.py's DOWN/UP key injection), so scripts relying on it
# fail with no obvious error tying back to "wrong binary."


def launch_game(conf_path: str = None) -> str:
    conf_path = conf_path or CONF_PATH
    print(f"Launching dosbox-x (conf={conf_path})...")
    script = f'''
    tell application "Terminal"
        do script "cd {PROJECT_DIR} && {DOSBOX_X_BIN} -conf {conf_path} -savedir {SAVEDIR}"
        activate
    end tell
    '''
    run_osa(script)

    for _ in range(30):
        if dosbox_running():
            win_id = find_terminal_window_id()
            if win_id:
                return win_id
        time.sleep(0.5)
    raise RuntimeError("dosbox-x did not start within 15s")


def is_debugger_open() -> bool:
    """True if the dosbox-x window title currently shows the debugger.
    Promoted from a local `_is_debugger_open()` duplicated in inject_key.py
    so break_into_debugger() below can also use it (see its docstring) -
    only checking state on the inject_key.py side while every other caller
    blind-pressed Option+F12 was the other half of the "debugger left
    open" bug: a blind press when it's already open closes it again
    instead of opening it."""
    try:
        title = run_osa('tell application "System Events" to tell process "dosbox-x" to get name of window 1')
        return "DEBUGGER" in title
    except RuntimeError:
        return False


def break_into_debugger(max_attempts: int = 4) -> bool:
    """Option+F12 is a TOGGLE, not an "open the debugger" command -
    pressing it while the debugger is already open closes it instead.
    Blindly pressing it on every call (the old behavior) was only safe as
    long as resume_execution() always left the debugger closed first,
    which it didn't reliably do (see that function's docstring) - a failed
    resume followed by this blind toggle would silently *invert* debugger
    state instead of leaving it merely stuck open, sending whatever command
    came next into the live DOS session instead of the debugger console.
    Now checks state first (a no-op if already open) and confirms the
    press actually took effect, retrying like inject_key.py's former
    `_ensure_debugger_open` (now just this function - see is_debugger_open
    for the other half of that consolidation)."""
    if is_debugger_open():
        return True
    for attempt in range(1, max_attempts + 1):
        run_osa('tell application "System Events" to set frontmost of process "dosbox-x" to true')
        time.sleep(0.3)
        # Left Option+F12, per DOSBox-X's Debug menu ("Start DOSBox-X Debugger")
        run_osa('tell application "System Events" to key code 111 using {option down}')
        time.sleep(DEBUGGER_SETTLE_SECS)
        if is_debugger_open():
            return True
        print(f"    [warn] break-in didn't take (title still not DEBUGGER), "
              f"attempt {attempt}/{max_attempts}")
    print(f"    [error] could not confirm debugger opened after {max_attempts} attempts.")
    return False


def send_memdump_command(win_id: str, seg_hex: str):
    send_raw_command(win_id, f"MEMDUMPBIN {seg_hex}:0 {SEGMENT_LEN_HEX}")


RESUME_CONFIRM_TIMEOUT_SECS = 6.0  # live-measured F5->title-clear latency hit
                                    # 3.75s in one sample -- the old 3.0s
                                    # single-shot timeout below was tight
                                    # enough to miss that and declare the
                                    # debugger closed when it wasn't.
MAX_RESUME_ATTEMPTS = 3


def wait_for_debugger_closed(timeout: float = RESUME_CONFIRM_TIMEOUT_SECS) -> bool:
    """F5 resumes the emulator, but the debugger window can take a moment
    to actually close. If the next action (a game keypress) fires before
    it does, that keypress gets swallowed by the still-open debugger
    instead of reaching the game - confirmed via screenshot as the cause
    of a missed ordnance-cycle press. Poll the dosbox-x window title until
    it no longer says DEBUGGER, returning True/False instead of only
    warning, so resume_execution() can tell whether it needs to retry
    (see its docstring)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            title = run_osa('tell application "System Events" to tell process "dosbox-x" to get name of window 1')
        except RuntimeError:
            return True
        if "DEBUGGER" not in title:
            return True
        time.sleep(0.1)
    return False


def resume_execution(win_id: str) -> bool:
    """Resume with F5 and CONFIRM the debugger window title actually
    clears, retrying up to MAX_RESUME_ATTEMPTS times before giving up.

    This used to send F5 exactly once and only *warn* if
    wait_for_debugger_closed() timed out, still returning control to the
    caller either way - every caller of this shared primitive
    (capture_segment's own pre-attempt resync below, tag_capture.py's
    per-segment captures via capture_segment, and every injection function
    in inject_key.py) then treated "resumed" as a given and went on to send
    the next game keystroke, which landed in the still-open debugger
    console instead of the game whenever F5's first attempt didn't take.
    That's the "debugger stays open / eats the next keypress" bug reported
    across trial runs. breakpoint_capture.py's write-breakpoint path hit
    the identical failure mode and fixed it with exactly this
    retry-with-confirmation pattern (see its former `_resume_and_confirm`,
    now just this function - see that module for the reasoning this was
    ported from); this brings the same fix to the plain (non-breakpoint)
    debugger path every capture/injection script in this project sits on
    top of, instead of leaving two copies of the same fix to drift apart
    again."""
    for attempt in range(1, MAX_RESUME_ATTEMPTS + 1):
        focus_terminal_window(win_id)
        time.sleep(0.2)
        run_osa('tell application "System Events" to key code 96')  # F5
        if wait_for_debugger_closed():
            return True
        print(f"    [warn] resume not confirmed within {RESUME_CONFIRM_TIMEOUT_SECS:.0f}s "
              f"(attempt {attempt}/{MAX_RESUME_ATTEMPTS}), retrying F5...")
    print(f"    [error] could not confirm debugger closed after {MAX_RESUME_ATTEMPTS} "
          "attempts -- it is likely still open and will swallow the next keypress.")
    return False


def wait_for_fresh_dump(baseline_mtime) -> bool:
    deadline = time.time() + FILE_WAIT_TIMEOUT_SECS
    while time.time() < deadline:
        if os.path.exists(MEMDUMP_PATH):
            mtime = os.path.getmtime(MEMDUMP_PATH)
            if baseline_mtime is None or mtime > baseline_mtime:
                # small settle to make sure the write is complete
                time.sleep(0.15)
                if os.path.getsize(MEMDUMP_PATH) == EXPECTED_CHUNK_SIZE:
                    return True
        time.sleep(0.1)
    return False


def capture_segment(win_id: str, seg_hex: str) -> bytes | None:
    baseline_mtime = os.path.getmtime(MEMDUMP_PATH) if os.path.exists(MEMDUMP_PATH) else None

    for attempt in range(1, MAX_ATTEMPTS_PER_CYCLE + 1):
        # Force a resync to "running" before every attempt, then break in
        # fresh - both steps now check+confirm real state instead of
        # blindly toggling (see resume_execution/break_into_debugger).
        try:
            resume_execution(win_id)
        except RuntimeError:
            pass
        if not break_into_debugger():
            print(f"  [warn] could not confirm debugger opened for segment {seg_hex} "
                  f"(attempt {attempt}/{MAX_ATTEMPTS_PER_CYCLE}), retrying...")
            time.sleep(0.3)
            continue
        send_memdump_command(win_id, seg_hex)
        if wait_for_fresh_dump(baseline_mtime):
            with open(MEMDUMP_PATH, "rb") as f:
                data = f.read()
            resume_execution(win_id)
            return data
        print(f"  [warn] segment {seg_hex} did not appear "
              f"(attempt {attempt}/{MAX_ATTEMPTS_PER_CYCLE}), retrying...")
        resume_execution(win_id)
        time.sleep(0.3)

    return None


def capture_one_dump(win_id: str) -> bytes | None:
    chunks = []
    for seg_hex in SEGMENTS:
        data = capture_segment(win_id, seg_hex)
        if data is None:
            return None
        chunks.append(data)
    return b"".join(chunks)


def write_record(data: bytes):
    now = datetime.now(timezone.utc)
    record = {
        "timestamp": now.isoformat(),
        "unix_ts": now.timestamp(),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
    }
    os.makedirs(os.path.dirname(OUTPUT_JSONL), exist_ok=True)
    with open(OUTPUT_JSONL, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"  wrote snapshot: {record['timestamp']}  sha256={record['sha256'][:12]}...")


def main():
    os.makedirs(os.path.dirname(PIDFILE), exist_ok=True)
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

    def cleanup(*_):
        if os.path.exists(PIDFILE):
            os.remove(PIDFILE)
        print("Stopped.")
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    if dosbox_running():
        print("dosbox-x already running, attaching...")
        win_id = find_terminal_window_id()
        if not win_id:
            print("Could not find the Terminal window running dosbox-x. "
                  "Make sure it was launched via `do script` in Terminal.app.")
            sys.exit(1)
    else:
        win_id = launch_game()

    print(f"Attached to Terminal window {win_id}. Dumping every {INTERVAL_SECONDS}s. Ctrl+C to stop early.")
    print(f"Output: {OUTPUT_JSONL}")

    try:
        while dosbox_running():
            cycle_start = time.time()
            try:
                data = capture_one_dump(win_id)
                if data is not None:
                    write_record(data)
                else:
                    print("  [error] failed to capture dump this cycle, skipping")
            except RuntimeError as e:
                # Most likely dosbox-x (or its window) vanished mid-cycle.
                if not dosbox_running():
                    break
                print(f"  [warn] transient error this cycle, will retry next cycle: {e}")

            elapsed = time.time() - cycle_start
            time.sleep(max(0.0, INTERVAL_SECONDS - elapsed))
    finally:
        print("dosbox-x process no longer running.")
        if os.path.exists(PIDFILE):
            os.remove(PIDFILE)
        print("Stopped.")


if __name__ == "__main__":
    main()
