#!/usr/bin/env python3
"""
Direct BIOS keyboard-buffer injection via the DOSBox-X debugger console -
the "different injection layer" alternative to OS-level synthetic keyboard
events. AppleScript's `keystroke`/`key code` (and, by the same mechanism,
pyautogui/pynput) post through macOS's CGEventPost - confirmed NOT to reach
DOSBox-X even with focus and Accessibility fully verified working (see
stage0/stage1, 2026-08-07: a clean, occlusion-guarded test showed zero
persistent on-screen change from an automated keystroke at a bare DOS
prompt). Rather than chase which CGEvent tap level might get through, this
writes the keystroke directly into the emulated PC's own BIOS keyboard ring
buffer instead - the standard IBM PC BIOS data area at segment 0040, offset
0x1A-0x3D. Confirmed against DOSBox-X's own source (src/ints/bios_keyboard.cpp,
checked out to ~/.Trash/dosbox-x earlier this project - since removed from
Trash, but the addresses below are also just the universal, unchanged-since-
the-original-IBM-PC convention): boot resets BIOS_KEYBOARD_BUFFER_START/END/
HEAD/TAIL to 0x1e/0x3e/0x1e/0x1e. Any DOS program reading keyboard input via
INT 16h - which is what F19, and essentially everything else, uses - reads
from exactly this buffer, so it can't distinguish this from a real keypress.
This bypasses the OS/SDL keyboard stack entirely: no window focus, no
Accessibility permission, no CGEvent tap-level question at all.

Uses the exact debugger-console primitives memdump_loop.py already proved
reliable for MEMDUMPBIN (break_into_debugger/send_raw_command/resume_execution)
- reading the head/tail pointers, writing the new entry, and advancing the
tail pointer all happen inside ONE debugger break-in, so the emulator is
fully paused the whole time and nothing else can race the buffer update.

Scancodes below are standard PC XT/AT Set 1 make codes - unrelated to ASCII
codes; each BIOS buffer slot is a 16-bit word storing BOTH (high byte =
scancode, low byte = ASCII). This matters because F19's controls are
scancode-driven (per its Quick Reference Card), not text-input-driven -
writing only an ASCII byte wouldn't be enough for a game reading raw
scancodes via INT 16h.
"""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

BIOS_KB_SEG = 0x40
HEAD_OFF = 0x1A
TAIL_OFF = 0x1C
BUF_START = 0x1E  # DOSBox-X's default/reset value for the ring buffer
BUF_END = 0x3E    # exclusive - buffer wraps back to BUF_START at this offset
SHIFT_FLAGS_OFF = 0x17  # BIOS keyboard flag byte 1: bit1=left shift, standard convention
LEFT_SHIFT_BIT = 0x02

DEBUGGER_SETTLE_SECS = 0.6
DUMP_WAIT_TIMEOUT_SECS = 3.0

# (ascii, scancode) pairs for the keys F19's Quick Reference Card actually
# uses (scripted_test_run.py: flaps=9, gear=6, autopilot=7, bay doors=8,
# brakes=0, throttle -/=, select ordnance=space, fire ordnance=return,
# cannon=backspace) plus digits generally.
SCANCODES = {
    "1": (0x31, 0x02), "2": (0x32, 0x03), "3": (0x33, 0x04), "4": (0x34, 0x05),
    "5": (0x35, 0x06), "6": (0x36, 0x07), "7": (0x37, 0x08), "8": (0x38, 0x09),
    "9": (0x39, 0x0A), "0": (0x30, 0x0B),
    "-": (0x2D, 0x0C), "=": (0x3D, 0x0D),
    " ": (0x20, 0x39),     # space
    "\r": (0x0D, 0x1C),    # enter/return
    "\x08": (0x08, 0x0E),  # backspace
    "\x1b": (0x1B, 0x01),  # esc
    "+": (0x2B, 0x0D),     # shift+= (same physical key as "=", shifted ASCII)
    # Numeric keypad, NumLock-off/"extended" convention (ascii=0x00, scancode
    # only) - the flight-stick-style control scheme F19's QRC uses. Scancodes
    # are standard PC Set 1 regardless of NumLock state; ascii=0x00 matches
    # how BIOS reports non-printing/extended keys.
    "KP8": (0x00, 0x48),  # numpad 8 / up
    "KP2": (0x00, 0x50),  # numpad 2 / down
    "KP4": (0x00, 0x4B),  # numpad 4 / left
    "KP6": (0x00, 0x4D),  # numpad 6 / right
    # Dedicated arrow-cluster keys (separate physical keys from the numpad,
    # 101-key "enhanced" layout) - live testing (2026-08-08) found KP2 (down)
    # made the plane descend, and the user confirmed the intended control is
    # the dedicated arrow, not the numpad, which "does something different."
    # Conventional BIOS encoding for these via the basic (non-enhanced)
    # INT16h interface: ascii=0xE0 (extended-key sentinel), scancode same
    # base value as the numpad equivalent - unverified against DOSBox-X
    # source (no longer available in this checkout), best-effort standard
    # convention, confirm live.
    "UP": (0xE0, 0x48),
    "DOWN": (0xE0, 0x50),
    "LEFT": (0xE0, 0x4B),
    "RIGHT": (0xE0, 0x4D),
}


def _wait_for_dump(baseline_mtime, expected_size: int) -> bytes | None:
    deadline = time.time() + DUMP_WAIT_TIMEOUT_SECS
    while time.time() < deadline:
        if os.path.exists(ml.MEMDUMP_PATH):
            mtime = os.path.getmtime(ml.MEMDUMP_PATH)
            if baseline_mtime is None or mtime > baseline_mtime:
                time.sleep(0.15)  # let the write finish
                if os.path.getsize(ml.MEMDUMP_PATH) == expected_size:
                    with open(ml.MEMDUMP_PATH, "rb") as f:
                        return f.read()
        time.sleep(0.1)
    return None


# _is_debugger_open/_ensure_debugger_open used to be duplicated here (their
# own title-poll-and-retry loop, independent of memdump_loop.py's). That
# duplication is exactly how the two debugger paths in this project drifted:
# this file's copy got the check-before-toggle fix, memdump_loop.py's plain
# break_into_debugger() didn't, and every capture script sitting on top of
# memdump_loop.py kept hitting the "debugger left open" bug this file had
# already solved. Both are now just the shared primitives directly -
# ml.is_debugger_open() / ml.break_into_debugger(), which carries the same
# check-then-retry-then-confirm logic (see its docstring).


def inject_key(win_id: str, ch: str, refocus: bool = True) -> bool:
    """Writes one keystroke directly into the emulated BIOS keyboard buffer.
    `win_id` is the *terminal* window id (ml.find_terminal_window_id()) that
    the debugger console runs in - not dosbox-x's own GUI window. Returns
    True/False for success; prints diagnostics either way rather than
    raising, since a failed injection attempt still needs to leave DOSBox-X
    resumed and refocused, not stuck mid-debugger."""
    if ch not in SCANCODES:
        raise ValueError(f"no scancode mapping for {ch!r} - add one to SCANCODES")
    ascii_code, scancode = SCANCODES[ch]
    entry = (scancode << 8) | ascii_code

    if not ml.is_debugger_open():
        try:
            ml.resume_execution(win_id)
        except RuntimeError:
            pass

    ok = False
    try:
        if not ml.break_into_debugger():
            print("  [error] could not confirm debugger actually opened - aborting this attempt")
            return False

        baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH) if os.path.exists(ml.MEMDUMP_PATH) else None
        ml.send_raw_command(win_id, f"MEMDUMPBIN {BIOS_KB_SEG:X}:{HEAD_OFF:X} 4")
        data = _wait_for_dump(baseline_mtime, 4)
        if data is None:
            print("  [error] could not read head/tail pointers (no fresh 4-byte dump)")
            return False
        head, tail = struct.unpack("<HH", data)

        new_tail = tail + 2
        if new_tail >= BUF_END:
            new_tail = BUF_START
        if new_tail == head:
            print("  [error] BIOS keyboard buffer is full - aborting injection")
            return False

        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{tail:X} W:{entry:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{TAIL_OFF:X} W:{new_tail:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)

        print(f"  injected {ch!r} (ascii=0x{ascii_code:02X} scan=0x{scancode:02X}) "
              f"at 0040:{tail:04X}, tail 0x{tail:X} -> 0x{new_tail:X}")
        ok = True
    finally:
        ml.resume_execution(win_id)
        if refocus:
            try:
                ml.focus_dosbox()
            except RuntimeError:
                print("  [warn] could not refocus dosbox-x after injection")

    return ok


def inject_keys_batch(win_id: str, chars: list, refocus: bool = True) -> int:
    """Writes MULTIPLE keystrokes in ONE debugger break-in/resume cycle,
    instead of inject_key()'s one-pause-per-key. Matters for anything timing-
    sensitive: a live test (2026-08-07) heard the engine sound react to an
    injected throttle key but saw no visual/physical movement, and repeated
    single-key nose-up sequences kept crashing on the runway - the likely
    cause isn't insufficient input, it's that 6 separate break-ins fragment
    the takeoff roll into 6 frozen pauses, starving the plane of the
    continuous unpaused real time it needs to actually accelerate down the
    runway. Emulation is fully paused for the whole batch (nothing to race),
    so all N buffer entries get written before a single resume - the game
    then drains them in quick succession once it resumes, rather than
    getting paused again after every single one.

    Returns the number of keys successfully written (0..len(chars))."""
    for ch in chars:
        if ch not in SCANCODES:
            raise ValueError(f"no scancode mapping for {ch!r} - add one to SCANCODES")

    if not ml.is_debugger_open():
        try:
            ml.resume_execution(win_id)
        except RuntimeError:
            pass

    written = 0
    try:
        if not ml.break_into_debugger():
            print("  [error] could not confirm debugger actually opened - aborting batch")
            return 0

        baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH) if os.path.exists(ml.MEMDUMP_PATH) else None
        ml.send_raw_command(win_id, f"MEMDUMPBIN {BIOS_KB_SEG:X}:{HEAD_OFF:X} 4")
        data = _wait_for_dump(baseline_mtime, 4)
        if data is None:
            print("  [error] could not read head/tail pointers (no fresh 4-byte dump)")
            return 0
        head, tail = struct.unpack("<HH", data)

        for ch in chars:
            ascii_code, scancode = SCANCODES[ch]
            entry = (scancode << 8) | ascii_code
            new_tail = tail + 2
            if new_tail >= BUF_END:
                new_tail = BUF_START
            if new_tail == head:
                print(f"  [error] buffer full after {written} keys - stopping batch early")
                break
            ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{tail:X} W:{entry:04X}")
            time.sleep(DEBUGGER_SETTLE_SECS)
            tail = new_tail
            written += 1

        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{TAIL_OFF:X} W:{tail:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        print(f"  batch-injected {written}/{len(chars)} keys {chars}, tail now 0x{tail:X}")
    finally:
        ml.resume_execution(win_id)
        if refocus:
            try:
                ml.focus_dosbox()
            except RuntimeError:
                print("  [warn] could not refocus dosbox-x after injection")

    return written


def inject_max_throttle(win_id: str, refocus: bool = True) -> bool:
    """inject_key(win_id, '+') writes ascii='+' with the '=' key's scancode,
    but never touches the BIOS shift-state flags byte (0040:0017, bit1=left
    shift - standard convention) - so if F19 reads shift-held state directly
    (plausible for a distinct max-throttle/afterburner binding, separate
    from the plain '=' throttle-nudge) rather than relying on the ASCII
    byte, that entry could be indistinguishable from an unshifted '='. This
    properly sets the shift flag, writes the entry, and restores the
    original flags byte - all inside one debugger pause, so it costs no
    more real-time fragmentation than a normal single-key injection."""
    ch = "+"
    ascii_code, scancode = SCANCODES[ch]
    entry = (scancode << 8) | ascii_code

    if not ml.is_debugger_open():
        try:
            ml.resume_execution(win_id)
        except RuntimeError:
            pass

    ok = False
    try:
        if not ml.break_into_debugger():
            print("  [error] could not confirm debugger actually opened - aborting")
            return False

        baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH) if os.path.exists(ml.MEMDUMP_PATH) else None
        ml.send_raw_command(win_id, f"MEMDUMPBIN {BIOS_KB_SEG:X}:{HEAD_OFF:X} 4")
        data = _wait_for_dump(baseline_mtime, 4)
        if data is None:
            print("  [error] could not read head/tail pointers")
            return False
        head, tail = struct.unpack("<HH", data)
        new_tail = tail + 2
        if new_tail >= BUF_END:
            new_tail = BUF_START
        if new_tail == head:
            print("  [error] BIOS keyboard buffer is full - aborting")
            return False

        baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH)
        ml.send_raw_command(win_id, f"MEMDUMPBIN {BIOS_KB_SEG:X}:{SHIFT_FLAGS_OFF:X} 1")
        flags_data = _wait_for_dump(baseline_mtime, 1)
        if flags_data is None:
            print("  [error] could not read shift-flags byte")
            return False
        orig_flags = flags_data[0]

        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{SHIFT_FLAGS_OFF:X} B:{orig_flags | LEFT_SHIFT_BIT:02X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{tail:X} W:{entry:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{TAIL_OFF:X} W:{new_tail:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{SHIFT_FLAGS_OFF:X} B:{orig_flags:02X}")
        time.sleep(DEBUGGER_SETTLE_SECS)

        print(f"  injected max-throttle (shift+=) at 0040:{tail:04X}, "
              f"tail 0x{tail:X}->0x{new_tail:X}, shift flags 0x{orig_flags:02X}->set->restored")
        ok = True
    finally:
        ml.resume_execution(win_id)
        if refocus:
            try:
                ml.focus_dosbox()
            except RuntimeError:
                print("  [warn] could not refocus dosbox-x after injection")

    return ok


def inject_takeoff_sequence(win_id: str, nose_up_count: int = 12, nose_up_key: str = "DOWN",
                             refocus: bool = True) -> bool:
    """Combines inject_max_throttle() + a nose-up batch into ONE debugger
    pass instead of two. Live testing (2026-08-08) found throttle-then-
    separate-nose-up-batch kept crashing even once both steps individually
    worked - because the second break-in happens WHILE the plane is already
    rolling under throttle, and re-entering the debugger mid-roll is itself
    disruptive (matches the consistent "next debugger call fails" pattern
    seen right after every successful throttle injection). This writes
    shift-set / throttle-entry / shift-restore / N nose-up entries / final
    tail pointer all in one paused session, then resumes exactly once - so
    the plane never gets frozen again after it starts moving.

    nose_up_key defaults to "DOWN" (dedicated arrow-cluster key, ascii=0xE0
    extended encoding) - NOT "KP2" (numpad), which live testing confirmed
    makes the plane descend instead of climb. "DOWN" was confirmed live via
    a debugger read-back showing head==tail (DOS actually consumed all 12
    injected presses, not just queued them) while the user was in stable
    flight, before this was wired into the full takeoff sequence."""
    ch = "+"
    ascii_code, scancode = SCANCODES[ch]
    throttle_entry = (scancode << 8) | ascii_code
    if nose_up_key not in SCANCODES:
        raise ValueError(f"no scancode mapping for {nose_up_key!r}")
    nose_ascii, nose_scan = SCANCODES[nose_up_key]
    nose_entry = (nose_scan << 8) | nose_ascii

    if not ml.is_debugger_open():
        try:
            ml.resume_execution(win_id)
        except RuntimeError:
            pass

    ok = False
    try:
        if not ml.break_into_debugger():
            print("  [error] could not confirm debugger actually opened - aborting")
            return False

        baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH) if os.path.exists(ml.MEMDUMP_PATH) else None
        ml.send_raw_command(win_id, f"MEMDUMPBIN {BIOS_KB_SEG:X}:{HEAD_OFF:X} 4")
        data = _wait_for_dump(baseline_mtime, 4)
        if data is None:
            print("  [error] could not read head/tail pointers")
            return False
        head, tail = struct.unpack("<HH", data)

        baseline_mtime = os.path.getmtime(ml.MEMDUMP_PATH)
        ml.send_raw_command(win_id, f"MEMDUMPBIN {BIOS_KB_SEG:X}:{SHIFT_FLAGS_OFF:X} 1")
        flags_data = _wait_for_dump(baseline_mtime, 1)
        if flags_data is None:
            print("  [error] could not read shift-flags byte")
            return False
        orig_flags = flags_data[0]

        def next_tail(t):
            nt = t + 2
            return BUF_START if nt >= BUF_END else nt

        # throttle, with shift held around just this one entry
        new_tail = next_tail(tail)
        if new_tail == head:
            print("  [error] buffer full before writing anything - aborting")
            return False
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{SHIFT_FLAGS_OFF:X} B:{orig_flags | LEFT_SHIFT_BIT:02X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{tail:X} W:{throttle_entry:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{SHIFT_FLAGS_OFF:X} B:{orig_flags:02X}")
        time.sleep(DEBUGGER_SETTLE_SECS)
        tail = new_tail

        # nose-up burst
        written = 0
        for _ in range(nose_up_count):
            new_tail = next_tail(tail)
            if new_tail == head:
                print(f"  [warn] buffer full after {written} nose-up entries - stopping early")
                break
            ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{tail:X} W:{nose_entry:04X}")
            time.sleep(DEBUGGER_SETTLE_SECS)
            tail = new_tail
            written += 1

        ml.send_raw_command(win_id, f"SM {BIOS_KB_SEG:X}:{TAIL_OFF:X} W:{tail:04X}")
        time.sleep(DEBUGGER_SETTLE_SECS)

        print(f"  injected throttle + {written}/{nose_up_count} nose-up in ONE pass, tail now 0x{tail:X}")
        ok = True
    finally:
        ml.resume_execution(win_id)
        if refocus:
            try:
                ml.focus_dosbox()
            except RuntimeError:
                print("  [warn] could not refocus dosbox-x after injection")

    return ok


def main():
    ch = sys.argv[1] if len(sys.argv) > 1 else "9"
    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    win_id = ml.find_terminal_window_id()
    if not win_id:
        print("Could not find the terminal window running dosbox-x.")
        sys.exit(1)
    ok = inject_key(win_id, ch)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
