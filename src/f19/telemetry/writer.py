"""A bounded queue drained by one background thread: the caller never blocks on disk and never sees a write error
(nav's _SafeLog rule: a full disk must never stop the autopilot). What is dropped is counted in SinkStats."""
import queue
import threading
from dataclasses import dataclass, field

_STOP = object()


@dataclass
class SinkStats:
    queued: int = 0               # accepted into the queue
    written: int = 0              # ops that completed
    dropped_full: int = 0         # queue full: dropped in the caller's thread
    dropped_closed: int = 0       # submitted after close()
    dropped_after_error: int = 0  # skipped because an earlier error stopped that file (the CSV, as _SafeLog)
    dropped_invalid: int = 0      # rejected input (e.g. a snapshot of the wrong size)
    errors: int = 0               # ops that raised (OSError: full disk, ...)
    last_error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def inc(self, name, n=1):
        with self._lock: setattr(self, name, getattr(self, name) + n)

    def error(self, e):
        with self._lock: self.errors += 1; self.last_error = repr(e)

    @property
    def dropped(self): return self.dropped_full + self.dropped_closed + self.dropped_after_error + self.dropped_invalid + self.errors


class BackgroundWriter:
    """submit(fn, *args) queues fn for the writer thread; returns False (and counts) when it was dropped.
    on_close runs in the writer thread after the last op (close files there).
    block=True: submit waits for room instead of dropping (offline use: writing a replay back; never in flight)."""

    def __init__(self, name="telemetry-writer", maxsize=4096, stats=None, on_close=None, block=False):
        self.block = block
        self.stats = stats if stats is not None else SinkStats()
        self._q = queue.Queue(maxsize)
        self._on_close, self._closed = on_close, False
        self._close_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, fn, *args):
        with self._close_lock:            # never held while blocking (block=False): close() puts _STOP outside it
            if self._closed: self.stats.inc("dropped_closed"); return False
            try: self._q.put((fn, args), block=self.block)
            except queue.Full: self.stats.inc("dropped_full"); return False
        self.stats.inc("queued")
        return True

    def _run(self):
        try:
            while True:
                item = self._q.get()
                try:
                    if item is _STOP: return
                    fn, args = item
                    try: r = fn(*args)
                    except Exception as e: self.stats.error(e)          # noqa: BLE001 - counted, never raised
                    else:
                        if r is not False: self.stats.inc("written")    # False: the op counted its own drop
                finally:
                    self._q.task_done()
        finally:
            if self._on_close is not None:
                try: self._on_close()
                except Exception as e: self.stats.error(e)              # noqa: BLE001

    def flush(self, timeout=None):
        """Wait until every queued op ran. False on timeout."""
        import time
        end = None if timeout is None else time.monotonic() + timeout
        with self._q.all_tasks_done:
            while self._q.unfinished_tasks:
                left = None if end is None else end - time.monotonic()
                if left is not None and left <= 0: return False
                self._q.all_tasks_done.wait(left)
        return True

    def close(self, timeout=10.0):
        """Run what is queued, then on_close; later submits are dropped. True if the thread finished in time."""
        with self._close_lock:
            first, self._closed = not self._closed, True
        if first:                                # every accepted op is queued before _STOP
            try: self._q.put(_STOP, timeout=timeout)
            except queue.Full: pass              # the thread is stuck on a write: give up on it (daemon thread)
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @property
    def closed(self): return self._closed
