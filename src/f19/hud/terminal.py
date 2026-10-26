"""Everything that touches the terminal, in one place: the alternate screen, cbreak keys (no Enter), the size, and
writing a Screen. Widgets and the Hud never see it (they draw into an in-memory Screen)."""
import atexit
import os
import select
import shutil
import sys


class Terminal:
    def __init__(self, out=None, inp=None, default_size=(160, 50)):
        self.out = out if out is not None else sys.stdout
        self.inp = inp if inp is not None else sys.stdin
        self.default_size = default_size
        self.started, self.fd, self._tty, self._size = False, None, None, None

    def start(self):
        if self.started: return
        self.started = True
        self.out.write("\033[?1049h\033[?25l\033[2J"); self.out.flush()
        try:
            import termios
            import tty
            if self.inp.isatty():
                self.fd = self.inp.fileno(); self._tty = termios.tcgetattr(self.fd); tty.setcbreak(self.fd)
        except Exception: self.fd = None              # noqa: BLE001 - no keys then
        atexit.register(self.close)

    def close(self):
        if not self.started: return
        self.started = False
        if self.fd is not None and self._tty is not None:
            try:
                import termios
                termios.tcsetattr(self.fd, termios.TCSADRAIN, self._tty)
            except Exception: pass                    # noqa: BLE001
        try: self.out.write("\033[0m\033[?25h\033[?1049l"); self.out.flush()
        except Exception: pass                        # noqa: BLE001

    def keys(self):
        """Keys pressed since the last call ('' when none, or no tty)."""
        if self.fd is None: return ""
        s = ""
        while select.select([self.fd], [], [], 0)[0]:
            b = os.read(self.fd, 64)
            if not b: break
            s += b.decode("latin1")
        return s

    def size(self):
        """(columns, rows) now: a resize reflows the next frame."""
        return tuple(shutil.get_terminal_size(self.default_size))

    def draw(self, screen):
        self.start()
        if (screen.w, screen.h) != self._size: self.out.write("\033[2J"); self._size = (screen.w, screen.h)
        self.out.write(screen.emit()); self.out.flush()


class NullTerminal:
    """No terminal (tests, headless runs): fixed size, no keys unless queued, keeps the last Screen."""
    def __init__(self, size=(160, 50), keys=""):
        self._size, self._keys, self.screen, self.frames = size, keys, None, 0

    def start(self): pass
    def close(self): pass
    def size(self): return self._size
    def push_keys(self, k): self._keys += k

    def keys(self):
        k, self._keys = self._keys, ""; return k

    def draw(self, screen): self.screen = screen; self.frames += 1
