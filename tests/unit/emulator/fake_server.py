"""An in-process stand-in for the DOSBox-X automation server (src/misc/automation.cpp), for the client tests.

Speaks the same protocol over a real loopback socket: PEEK seg:off len -> PEEK-REPLY, POKE phys hex -> POKE-REPLY
OK/ERR (guest RAM below 16 MB and outside A0000-FFFFF), DOWN/UP/OUT recorded, no reply. A malformed PEEK gets no
reply, as on the real server. Hooks let a test drop or inject replies to exercise the desync guard.
"""
import socket
import threading


class FakeAutomationServer:
    RAM = 16 << 20

    def __init__(self):
        self.mem = bytearray(0x100000)
        self.log = []                 # every request line, in order
        self.keys = []                # ("DOWN"/"UP", key)
        self.outs = []                # (port, val)
        self.drop_next = 0            # swallow the next N PEEK replies (simulates a lost reply)
        self.stale = []               # lines sent to a client right after it connects (a previous client's leftovers)
        self.poke_supported = True
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0)); self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._serve, daemon=True); self.thread.start()

    def close(self):
        self._stop = True
        try: socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
        except OSError: pass
        self.srv.close()

    def _serve(self):
        while not self._stop:
            try: c, _ = self.srv.accept()
            except OSError: return
            if self._stop: c.close(); return
            with c:
                buf = b""
                if self.stale: c.sendall("".join(l + "\n" for l in self.stale).encode()); self.stale = []
                while True:
                    try: data = c.recv(65536)
                    except OSError: break
                    if not data: break
                    buf += data
                    out = []
                    while b"\n" in buf:
                        line, _, buf = buf.partition(b"\n")
                        r = self.handle(line.decode().strip())
                        if r is not None: out.append(r)
                    if out:
                        try: c.sendall("".join(l + "\n" for l in out).encode())
                        except OSError: break

    def handle(self, line):
        self.log.append(line)
        verb, _, rest = line.partition(" ")
        verb = verb.upper()
        if verb == "PEEK":
            try:
                addr, n = rest.split(); seg, off = addr.split(":")
                a, n = int(seg, 16) * 16 + int(off, 16), int(n)
                assert 1 <= n <= 64
            except (ValueError, AssertionError):
                return None
            if self.drop_next: self.drop_next -= 1; return None
            return "PEEK-REPLY " + bytes(self.mem[a:a + n]).ljust(n, b"\0").hex().upper()
        if verb == "POKE":
            if not self.poke_supported: return None
            try:
                a_s, _, hx = rest.partition(" ")
                a, data = int(a_s, 16), bytes.fromhex(hx.replace(" ", ""))
            except ValueError:
                return "POKE-REPLY ERR bad request"
            if not data or len(data) > 64: return "POKE-REPLY ERR length"
            if a + len(data) > self.RAM or (a < 0x100000 and a + len(data) > 0xA0000):
                return "POKE-REPLY ERR out of guest RAM"
            if a + len(data) <= len(self.mem): self.mem[a:a + len(data)] = data
            return f"POKE-REPLY OK {a:05X} {len(data)}"
        if verb in ("DOWN", "UP"): self.keys.append((verb, rest)); return None
        if verb == "OUT":
            p, v = rest.split(); self.outs.append((int(p, 16), int(v, 16))); return None
        return None
