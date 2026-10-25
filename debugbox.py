import os
import pty
import subprocess
import threading
import queue
import time
import re
from pathlib import Path

import numpy as np


class DOSBoxDebugger:
    """
    Control a DOSBox-X debugger running on a PTY.

    DOSBox-X is started with:
        -c "DEBUGBOX F19.COM"

    so it enters the debugger automatically.

    The reader thread continuously drains the PTY, preventing
    DOSBox-X from blocking on terminal output.
    """

    def __init__(
        self,
        conf_path,
        exe_path="/opt/homebrew/bin/dosbox-x",
        work_dir=None,
        dump_path=None,
        rows=24,
        cols=80,
        echo=True,
    ):
        self.conf_path = str(conf_path)
        self.exe_path = exe_path
        self.work_dir = work_dir
        self.dump_path = (
            Path.home() / "MEMDUMP.BIN"
            if dump_path is None
            else Path(dump_path)
        )
        self.rows = rows
        self.cols = cols
        self.echo = echo

        self.master = None
        self.slave = None
        self.proc = None

        self.output_queue = queue.Queue()
        self.output_buffer = bytearray()

        self.reader_thread = None
        self.reader_stop = threading.Event()

    # ---------------------------------------------------------
    # Startup / shutdown
    # ---------------------------------------------------------

    def start(self):
        self.master, self.slave = pty.openpty()

        # Give ncurses a predictable terminal size.
        import fcntl
        import struct
        import termios

        winsize = struct.pack(
            "HHHH",
            self.rows,
            self.cols,
            0,
            0,
        )
        fcntl.ioctl(
            self.slave,
            termios.TIOCSWINSZ,
            winsize,
        )

        cmd = [
            self.exe_path,
            "-conf",
            self.conf_path,
            "-c",
            "DEBUGBOX F19.COM",
        ]
        self.send("AUTOTYPE ENTER")

        self.proc = subprocess.Popen(
            cmd,
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            cwd=self.work_dir,
            close_fds=True,
        )

        # Parent only needs the master.
        os.close(self.slave)
        self.slave = None

        self.reader_stop.clear()

        self.reader_thread = threading.Thread(
            target=self._reader,
            daemon=True,
        )
        self.reader_thread.start()

        # Wait for the debugger to initialize.
        self.wait_for_output(
            "TYPE HELP",
            timeout=10,
        )

        return self

    def close(self):
        self.reader_stop.set()

        if self.master is not None:
            try:
                os.close(self.master)
            except OSError:
                pass
            self.master = None

        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()

            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()

            self.proc = None

    # ---------------------------------------------------------
    # PTY reader
    # ---------------------------------------------------------

    def _reader(self):
        while not self.reader_stop.is_set():
            try:
                data = os.read(self.master, 65536)

                if not data:
                    break

                self.output_buffer.extend(data)
                self.output_queue.put(data)

                if self.echo:
                    print(
                        data.decode(errors="replace"),
                        end="",
                        flush=True,
                    )

            except OSError:
                break

    # ---------------------------------------------------------
    # Sending commands
    # ---------------------------------------------------------

    def send(self, command):
        """
        Send a debugger command.

        CR is deliberate: this is terminal/ncurses input.
        """
        if self.master is None:
            raise RuntimeError("Debugger is not running")

        os.write(
            self.master,
            command.encode() + b"\r",
        )

    # ---------------------------------------------------------
    # Reading output
    # ---------------------------------------------------------

    def drain_output(self):
        """
        Return everything currently sitting in the output queue.
        """
        chunks = []

        while True:
            try:
                chunks.append(
                    self.output_queue.get_nowait()
                )
            except queue.Empty:
                break

        return b"".join(chunks)

    def wait_for_output(self, text, timeout=5):
        """
        Wait until 'text' appears in the PTY output.
        """
        deadline = time.monotonic() + timeout
        needle = text.encode()

        while time.monotonic() < deadline:
            current = bytes(self.output_buffer)

            if needle in current:
                return current

            time.sleep(0.01)

        raise TimeoutError(
            f"Did not see {text!r} within {timeout}s"
        )

    def wait_for_new_output(self, timeout=5):
        """
        Wait until at least one new PTY chunk arrives.
        """
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                return self.output_queue.get(
                    timeout=0.05
                )
            except queue.Empty:
                pass

        raise TimeoutError(
            "No new DOSBox-X output received"
        )

    # ---------------------------------------------------------
    # Debugger commands
    # ---------------------------------------------------------

    def command(self, command, wait=0.25):
        """
        Send a debugger command and return output that arrives
        shortly afterward.

        This is intentionally simple. For commands whose result
        is a file (e.g. MEMDUMPBIN), use the dedicated method below.
        """
        # Clear already-buffered output from the queue.
        self.drain_output()

        self.send(command)

        chunks = []
        deadline = time.monotonic() + wait

        while time.monotonic() < deadline:
            try:
                chunks.append(
                    self.output_queue.get(
                        timeout=0.05
                    )
                )
            except queue.Empty:
                pass

        return b"".join(chunks).decode(
            errors="replace"
        )

    def set_breakpoint(self, address):
        """
        address may be:
            "0814:0102"
            0x12345678 (not recommended for BP)
        """
        result = self.command(
            f"BP {address}"
        )

        return result

    def set_memory_breakpoint(self, address):
        result = self.command(
            f"BPM {address}"
        )

        return result

    def list_breakpoints(self):
        return self.command("BPLIST")

    def delete_breakpoints(self):
        return self.command("BPDEL *")

    def run(self):
        """
        Resume emulated execution.
        """
        self.send("RUN")

    def ev(self, expression):
        """
        Evaluate a debugger expression and return raw output.
        """
        return self.command(
            f"EV {expression}"
        )

    # ---------------------------------------------------------
    # Memory
    # ---------------------------------------------------------

    def dump_memory(
        self,
        segment,
        offset=0,
        length=0x10000,
    ):
        """
        Dump emulated memory using DOSBox-X's MEMDUMPBIN.

        Returns:
            numpy uint8 array
        """

        # Remove stale dump.
        try:
            self.dump_path.unlink()
        except FileNotFoundError:
            pass

        command = (
            f"MEMDUMPBIN "
            f"{segment:04X} "
            f"{offset:08X} "
            f"{length:X}"
        )

        self.send(command)

        deadline = time.monotonic() + 5

        while time.monotonic() < deadline:

            if self.dump_path.exists():

                # Make sure the file has finished being written.
                size1 = self.dump_path.stat().st_size

                time.sleep(0.02)

                size2 = self.dump_path.stat().st_size

                if (
                    size1 == size2
                    and size2 >= length
                ):
                    return np.fromfile(
                        self.dump_path,
                        dtype=np.uint8,
                        count=length,
                    )

            time.sleep(0.01)

        raise TimeoutError(
            f"MEMDUMP.BIN did not appear at "
            f"{self.dump_path}"
        )
    def wait_for_log_complete(self, timeout=60):
        marker = b"DEBUG: cpu log LOGCPU.TXT created"
        start = len(self.output_buffer)
        deadline = time.monotonic() + timeout
    
        while time.monotonic() < deadline:
            new_output = bytes(self.output_buffer[start:])
    
            if marker in new_output:
                return
    
            time.sleep(0.01)
    
        raise TimeoutError(
            "Timed out waiting for DOSBox-X to finish LOGC"
        )