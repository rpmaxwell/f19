"""Client for the DOSBox-X automation socket (the fork in ~/projects/dosbox-x, src/misc/automation.cpp).

Protocol: line-oriented ASCII over TCP, one client at a time, loopback only.

  DOWN <key> / UP <key>             key event (no reply); key names as ADDKEY.COM (left, enter, kp5, f1, a, 0, ...)
  OUT <hex port> <hex byte>         guest I/O port write (no reply)
  PEEK <hex seg>:<hex off> <len>    -> "PEEK-REPLY <hex bytes>"       len 1..64; phys = seg * 16 + off
                                       (a malformed PEEK gets NO reply)
  POKE <hex phys> <hex bytes>       -> "POKE-REPLY OK <addr> <n>"    1..64 bytes, guest RAM only (not A0000-FFFFF)
                                    -> "POKE-REPLY ERR <reason>"      (POKE needs the 2026-10-25 build, 0ec314d6c)

PEEK and POKE share one server queue, so their replies come back in request order; key and OUT lines have no reply.
`peek_many` pipelines up to BATCH requests per round trip (a round trip costs ~15 ms: the server's poll interval).

Reply-desync guard: the replies carry no request id, so a reply that is lost (a timeout, a malformed request) or left
over (an exception between send and receive) would shift every later read by one line. The client counts the replies
it still owes; if a call is interrupted it marks the connection dirty, and the next call first resynchronises (sends a
PEEK of a length nothing else uses and drops lines until its reply). Every reply is also checked: a PEEK reply must
hold exactly the requested number of bytes, a POKE reply must name the requested address and length.

The old private names (`_send`, `_recv_line`, `_held`) stay as aliases while scripts/ and research/ still use them.
"""
import socket
import time

from f19.core.settings import settings as _settings

PEEK_MAX = 64              # server limit per PEEK
POKE_MAX = 64              # server limit per POKE
BATCH = 64                 # requests per pipelined round trip (replies ~140 B each: stays far below socket buffers)
_SYNC_LEN = 7              # resync marker: a PEEK length nothing else uses


class AutomationError(RuntimeError):
    """The automation server answered something unexpected (or nothing)."""


class DesyncError(AutomationError):
    """A reply did not match its request; the connection resynchronises before the next call."""


class PokeError(AutomationError):
    """The server refused a POKE, or the read-back did not match."""


def segoff(phys):
    """Physical address -> (seg, off) as the PEEK line spells it (seg = phys >> 4, off = phys & 15)."""
    return phys >> 4, phys & 15


def peek_line(phys, n):
    seg, off = segoff(phys)
    return f"PEEK {seg:X}:{off:X} {n}"


class Emulator:
    """One connection to the automation port.

      emu = Emulator()                      # host/port from f19.core.settings (F19_AUTOMATION_HOST / _PORT)
      emu.peek(0x40250, 2)                  # bytes at a physical address
      emu.peek_many([(0x40250, 2), (0x451B8, 10)])
      emu.poke(0x9F000, b"\\x01\\x02")        # write + read-back check
      emu.tap("f1"); emu.key_down("left"); emu.key_up("left")
    """

    def __init__(self, host=None, port=None, timeout=5.0, sync=True):
        self.host = host or _settings.host
        self.port = int(port or _settings.port)
        self.timeout = timeout
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._recv_buf = b""
        self._held, self._released = set(), set()     # keys this client holds / has released (scan_tap re-releases)
        self._owed = 0                                 # replies sent for but not read yet
        self._dirty = False
        if sync: self.resync()

    # ------------------------------------------------------------------------------------------------ line I/O
    def send_line(self, line):
        """Send one raw protocol line (no reply is read)."""
        self.sock.sendall((line + "\n").encode("ascii"))

    def recv_line(self):
        """Block until one reply line is available (no desync bookkeeping: use peek/poke for that)."""
        while b"\n" not in self._recv_buf:
            chunk = self.sock.recv(65536)
            if not chunk: raise ConnectionError("dosbox-x automation socket closed")
            self._recv_buf += chunk
        line, _, self._recv_buf = self._recv_buf.partition(b"\n")
        return line.decode("ascii", "replace").rstrip("\r")

    _send, _recv_line = send_line, recv_line          # old names (nav.py, research scripts)

    def resync(self):
        """Drop every reply still in flight: send the marker PEEK and discard lines until its reply, then whatever
        arrives in the next 0.3 s (older server builds hand a dead client's replies to the next connection, and a dead
        client's last requests can still be answered just after it goes)."""
        self.send_line(f"PEEK 0:0 {_SYNC_LEN}")
        for _ in range(100000):
            line = self.recv_line()
            if line.startswith("PEEK-REPLY ") and len(line) == len("PEEK-REPLY ") + 2 * _SYNC_LEN: break
        else:
            raise AutomationError("automation port: could not resynchronise replies")
        self.sock.settimeout(0.3)
        try:
            while self.sock.recv(65536): pass
        except (TimeoutError, OSError): pass
        finally:
            self.sock.settimeout(self.timeout); self._recv_buf = b""
        self._owed, self._dirty = 0, False

    _sync = resync

    def _begin(self):
        if self._dirty or self._owed: self.resync()

    def _exchange(self, lines, check):
        """Send `lines` (each expects one reply), read the replies, check(i, reply) -> value. Any failure leaves the
        connection dirty, so the next call resynchronises first."""
        self._begin()
        out = []
        try:
            self.sock.sendall("".join(l + "\n" for l in lines).encode("ascii"))
            self._owed = len(lines)
            for i in range(len(lines)):
                r = self.recv_line(); self._owed -= 1
                out.append(check(i, r))
        except BaseException:
            self._dirty = True
            raise
        return out

    # ----------------------------------------------------------------------------------------------------- reads
    def peek(self, addr, n, *legacy):
        """Bytes of guest memory at physical address `addr` (any length: split into PEEKs of <= 64).
        Old form, still accepted: peek(seg_hex_str, off_hex_str, n)."""
        if legacy or isinstance(addr, str):
            seg, off, n = addr, n, legacy[0]
            addr = int(seg, 16) * 16 + int(off, 16)
        return self.peek_many([(addr, n)])[0]

    def peek_many(self, reqs):
        """[(phys, n)] -> [bytes]: pipelined, BATCH requests per round trip. Requests longer than 64 bytes are split
        and joined again."""
        reqs = [(int(a), int(n)) for a, n in reqs]
        parts = []                                   # (request index, phys, n) per PEEK line
        for i, (a, n) in enumerate(reqs):
            if n <= 0: raise ValueError(f"peek length {n}")
            parts += [(i, a + k, min(PEEK_MAX, n - k)) for k in range(0, n, PEEK_MAX)]
        got = [bytearray() for _ in reqs]

        def check(j, line, chunk):
            _, a, n = chunk[j]
            if not line.startswith("PEEK-REPLY "):
                raise DesyncError(f"expected PEEK-REPLY for {a:#x}+{n}, got {line[:60]!r}")
            try: b = bytes.fromhex(line[11:])
            except ValueError: raise DesyncError(f"bad PEEK-REPLY {line[:60]!r}") from None
            if len(b) != n: raise DesyncError(f"PEEK {a:#x}+{n}: reply has {len(b)} bytes (replies out of step?)")
            return b

        for s in range(0, len(parts), BATCH):
            chunk = parts[s:s + BATCH]
            res = self._exchange([peek_line(a, n) for _, a, n in chunk], lambda j, line: check(j, line, chunk))
            for (i, _, _), b in zip(chunk, res): got[i] += b
        return [bytes(g) for g in got]

    def read(self, lo, hi):
        """Guest memory [lo, hi) as one bytes object (e.g. read(0, 0xA0000): all conventional memory, ~160 round trips)."""
        return b"".join(self.peek_many([(a, min(PEEK_MAX, hi - a)) for a in range(lo, hi, PEEK_MAX)]))

    # ---------------------------------------------------------------------------------------------------- writes
    def poke(self, addr, data, verify=True):
        """Write bytes to guest RAM at physical `addr` (split into POKEs of <= 64 bytes), then (verify=True) read them
        back in the same round trip and raise PokeError if they differ. Only the missions tool writes memory; flight
        code never does."""
        data = bytes(data)
        if not data: raise ValueError("poke: no bytes")
        lines, kinds = [], []
        for k in range(0, len(data), POKE_MAX):
            lines.append(f"POKE {addr + k:X} {data[k:k + POKE_MAX].hex().upper()}"); kinds.append(("poke", addr + k, len(data[k:k + POKE_MAX])))
        if verify:
            for k in range(0, len(data), PEEK_MAX):
                n = min(PEEK_MAX, len(data) - k)
                lines.append(peek_line(addr + k, n)); kinds.append(("peek", addr + k, n))

        def check(j, line):
            kind, a, n = kinds[j]
            if kind == "peek":
                if not line.startswith("PEEK-REPLY "): raise DesyncError(f"expected PEEK-REPLY, got {line[:60]!r}")
                b = bytes.fromhex(line[11:])
                if len(b) != n: raise DesyncError(f"PEEK {a:#x}+{n}: reply has {len(b)} bytes")
                return b
            if line.startswith("POKE-REPLY ERR"): raise PokeError(f"POKE {a:#x}+{n}: {line[15:]}")
            want = f"POKE-REPLY OK {a:05X} {n}"
            if line != want:
                raise DesyncError(f"POKE {a:#x}+{n}: expected {want!r}, got {line[:60]!r}")
            return None

        try:
            res = self._exchange(lines, check)
        except TimeoutError:
            raise AutomationError("POKE got no reply: is the DOSBox-X build older than the POKE verb (2026-10-25)?") from None
        if verify:
            back = b"".join(b for b in res if b is not None)
            if back != data:
                raise PokeError(f"POKE {addr:#x}: read back {back.hex()} != written {data.hex()}")

    def out(self, port, val):
        """Guest I/O port write (byte), performed on the emulation thread (no reply)."""
        self.send_line(f"OUT {port:X} {val:X}")

    # ------------------------------------------------------------------------------------------------------ keys
    def key_down(self, key):
        self._held.add(key)
        self.send_line(f"DOWN {key}")

    def key_up(self, key):
        self._held.discard(key); self._released.add(key)
        self.send_line(f"UP {key}")

    @property
    def held(self):
        """Keys this client has pressed and not released."""
        return frozenset(self._held)

    def tap(self, key, hold_secs=0.05):
        """Press and release: what F19's toggle switches (flaps, gear, autopilot, bay doors, brakes) expect: each
        DOWN/UP pair flips the toggle once, per the Quick Reference Card."""
        self.key_down(key)
        time.sleep(hold_secs)
        self.key_up(key)

    def release_all(self):
        """key_up for every key this client holds."""
        for k in list(self._held): self.key_up(k)

    def scan_tap(self, code, hold_secs=0.5, shift=False):
        """Press a key by its set-1 scancode, through the 8042's "write output buffer" command (OUT 64 D2, then
        OUT 60 <byte>): the byte reaches the guest as if typed. Each D2 clears the controller buffer and one drain
        writes every queued OUT, so make and break codes go out separately with a pause between. shift=True wraps it
        in left shift (Shift+= max power, Shift+- no power)."""
        # D2 also CLEARS the controller's pending bytes: a key release still queued there is lost and the game keeps
        # that key held. That stuck a roll key and spiralled the plane into the ground (2026-10-17). So let queued
        # keys drain first, and afterwards re-send the release of every key this client isn't holding.
        time.sleep(0.15)
        seq = ([0x2A] if shift else []) + [code]
        for c in seq:
            self._out_scan(c); time.sleep(0.08)
        time.sleep(hold_secs)
        for c in reversed(seq):
            self._out_scan(c | 0x80); time.sleep(0.08)
        time.sleep(0.05)
        # and once more: a break lost to a later D2 leaves the key auto-repeating ('-' drained the throttle 60 -> 0 %
        # in one tap, 2026-10-19); a second break for a released key is harmless
        for c in reversed(seq):
            self._out_scan(c | 0x80); time.sleep(0.05)
        for k in self._released - self._held:
            self.send_line(f"UP {k}")

    def release_arrows(self):
        """Deliver the arrow keys' BREAK codes straight to the guest (8042 D2): E0 C8 up, E0 D0 down, E0 CB left,
        E0 CD right, plus the keypad forms C8 D0 CB CD. A save state stores the controller's buffer; one made while
        nav flew held an up-arrow press (nose down) whose release was missing, so every load dived into the ground
        (2026-10-18). key_up() can't fix that: DOSBox doesn't think the key is down, so it sends nothing."""
        time.sleep(0.15)
        for code in (0xC8, 0xD0, 0xCB, 0xCD):
            for c in (0xE0, code):
                self._out_scan(c); time.sleep(0.05)
            self._out_scan(code); time.sleep(0.05)

    def _out_scan(self, c):
        self.out(0x64, 0xD2)
        self.out(0x60, c)

    # --------------------------------------------------------------------------------------------------- lifetime
    def close(self):
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def connect(host=None, port=None, retry_s=0.0, **kw):
    """Emulator(...), retrying for up to retry_s seconds while the port doesn't answer (emulator paused/starting)."""
    end = time.time() + retry_s
    while True:
        try: return Emulator(host, port, **kw)
        except (TimeoutError, OSError, AutomationError):
            if time.time() >= end: raise
            time.sleep(0.5)
