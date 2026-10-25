#!/usr/bin/env python3
"""
Thin client for DOSBox-X's automation socket (see ~/projects/dosbox-x/prd.md
and phase3_summary.md) - the "Phase 6" client the PRD describes, not yet
committed anywhere since the PRD treats it as a follow-up. This replaces the
debugger/BIOS-buffer route in inject_key.py: instead of pausing the emulator
and hand-writing BIOS ring-buffer entries via the AppleScript-driven debugger
console, it sends line-oriented `DOWN <key>` / `UP <key>` commands over a
loopback TCP socket that src/misc/automation.cpp drains directly into
KEYBOARD_AddKey() on the emulation thread. No AppleScript, no window focus,
no debugger pause - the emulator keeps running the whole time, which is what
makes held-key timing (e.g. a flap toggle at a precise 2-second cadence)
reliable instead of best-effort.

Requires `[sdl] automation port = <port>` set in dosbox-x.conf (see
~/Library/Preferences/dosbox-x.conf, currently 6000) and DOSBox-X already
running with F19 loaded.

Key vocabulary matches src/misc/automation.cpp's KeyNameTable() exactly:
left/right/up/down, enter, space, bs, tab, escape, ins, del, home, end,
pgup, pgdown, f1-f12, kp0-kp9, a-z, 0-9, and (2026-10-09 build) lctrl, rctrl,
lalt, ralt, lshift, rshift.

Also supports `PEEK <seg>:<off> <len>` -> `PEEK-REPLY <hex bytes>`, a
read-only guest-memory read added on top of the original DOWN/UP-only
protocol (see automation.cpp's HandlePeek/AUTOMATION_DrainQueue). Like key
injection, this does not pause the emulator or need window focus - unlike
memdump_loop.py's MEMDUMPBIN route, which breaks into the interactive
debugger and takes 3-5s per read. PEEK is the fast read path meant for a
live control loop (e.g. autopilot.py); memdump_loop.py/breakpoint_capture.py
remain the right tools for open-ended address hunting.
"""
import os
import socket
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("F19_AUTOMATION_PORT", 6000))   # env override: test servers (missile_fake_port.py)


class Dosbox:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.sock = socket.create_connection((host, port), timeout=5)
        self.sock.settimeout(5)
        self._recv_buf = b""
        self._held, self._released = set(), set()     # for scan_tap: keys to re-release after a D2
        self._sync()

    def _sync(self) -> None:
        """Skip any replies left queued by an earlier client (older server builds
        hand them to the next connection, shifting every reply by N lines): send a
        PEEK of a length nothing else uses and discard lines until its reply."""
        self._send("PEEK 0:0 7")
        for _ in range(100000):
            line = self._recv_line()
            if line.startswith("PEEK-REPLY ") and len(line) == len("PEEK-REPLY ") + 14:
                break
        else:
            raise RuntimeError("automation port: could not resynchronise replies")
        # a dead client's last requests can still be answered just after: drop anything that arrives in 0.3 s
        self.sock.settimeout(0.3)
        try:
            while self.sock.recv(65536): pass
        except (TimeoutError, OSError): pass
        finally:
            self.sock.settimeout(5); self._recv_buf = b""
        return

    def _send(self, line: str) -> None:
        self.sock.sendall((line + "\n").encode("ascii"))

    def _recv_line(self) -> str:
        """Blocks until a full newline-terminated reply is available. Only
        PEEK produces a reply on this socket (DOWN/UP are fire-and-forget),
        so this is only ever called right after a peek() send - nothing
        else contends for bytes on this connection."""
        while b"\n" not in self._recv_buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("dosbox-x automation socket closed")
            self._recv_buf += chunk
        line, _, self._recv_buf = self._recv_buf.partition(b"\n")
        return line.decode("ascii")

    def peek(self, seg: str, off: str, length: int) -> bytes:
        """Reads `length` bytes of live guest memory at seg:off (hex
        strings, e.g. peek("4000", "4459", 2)). Physical address is
        computed as (seg << 4) + off, matching MEMDUMPBIN's own real-mode
        address math - see FINDINGS.md's segment/physical-offset note and
        automation.cpp's HandlePeek docstring for why."""
        self._send(f"PEEK {seg}:{off} {length}")
        line = self._recv_line()
        prefix = "PEEK-REPLY "
        if not line.startswith(prefix):
            raise RuntimeError(f"unexpected automation reply: {line!r}")
        return bytes.fromhex(line[len(prefix):])

    def key_down(self, key: str) -> None:
        self._held.add(key)
        self._send(f"DOWN {key}")

    def key_up(self, key: str) -> None:
        self._held.discard(key); self._released.add(key)
        self._send(f"UP {key}")

    def tap(self, key: str, hold_secs: float = 0.05) -> None:
        """Press and release - what F19's toggle switches (flaps, gear,
        autopilot, bay doors, brakes) expect: each DOWN/UP pair flips the
        toggle once, per the Quick Reference Card."""
        self.key_down(key)
        time.sleep(hold_secs)
        self.key_up(key)

    def out(self, port: int, val: int) -> None:
        """Guest I/O port write (byte), performed on the emulation thread.
        Needs the 2026-10-09 automation build (OUT command)."""
        self._send(f"OUT {port:X} {val:X}")

    def scan_tap(self, code: int, hold_secs: float = 0.5, shift: bool = False) -> None:
        """Press a key the automation key table lacks ('-' = 0x0C, '=' = 0x0D: the
        throttle) by its set-1 scancode, through the 8042's "write output buffer"
        command (OUT 64 D2, then OUT 60 <byte>): the byte reaches the guest as if
        typed, no emulator rebuild needed. Each D2 clears the controller buffer and
        one drain writes every queued OUT, so make and break codes go out
        separately with a pause between. shift=True wraps it in left shift
        (Shift+= max power, Shift+- no power)."""
        # D2 also CLEARS the controller's pending bytes: a key release still queued there is lost and
        # the game keeps that key held. That stuck a roll key and spiralled the plane into the ground
        # (2026-10-17). So let queued keys drain first, and afterwards re-send the release of every
        # key this client isn't holding (UP for a key that is already up is harmless).
        time.sleep(0.15)
        seq = ([0x2A] if shift else []) + [code]
        for c in seq:
            self._out_scan(c); time.sleep(0.08)
        time.sleep(hold_secs)
        for c in reversed(seq):
            self._out_scan(c | 0x80); time.sleep(0.08)
        time.sleep(0.05)
        # and once more: a break lost to a later D2 leaves the key auto-repeating ('-' drained the
        # throttle 60 -> 0 % in one tap, 2026-10-19); a second break for a released key is harmless
        for c in reversed(seq):
            self._out_scan(c | 0x80); time.sleep(0.05)
        for k in self._released - self._held:
            self._send(f"UP {k}")

    def release_arrows(self) -> None:
        """Deliver the arrow keys' BREAK codes straight to the guest (8042 D2): E0 C8 up, E0 D0 down, E0 CB left,
        E0 CD right, plus the keypad forms C8 D0 CB CD. A save state stores the controller's buffer; one made while
        nav flew held an up-arrow press (nose down) whose release was missing, so every load dived into the ground
        (2026-10-18). key_up() can't fix that: DOSBox doesn't think the key is down, so it sends nothing."""
        time.sleep(0.15)
        for code in (0xC8, 0xD0, 0xCB, 0xCD):
            for c in (0xE0, code):
                self._out_scan(c); time.sleep(0.05)
            self._out_scan(code); time.sleep(0.05)

    def _out_scan(self, c: int) -> None:
        self.out(0x64, 0xD2)
        self.out(0x60, c)

    def close(self) -> None:
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
