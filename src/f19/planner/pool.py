"""Worker processes for planning the options of a mission in parallel (Planner._plan_batch).

Persistent subprocesses (`python -m f19.planner.worker`) that each build a Planner once and then plan sequences sent to them,
pickled over their stdin / stdout. Not multiprocessing: its `spawn` start method (the macOS default) re-imports the caller's
__main__, which would re-run any script without an `if __name__` guard. A worker is the same code path as the serial planner
(Planner.plan_with_loiter), so a result does not depend on which process computed it.

  F19_PLAN_WORKERS   number of workers (default: cpu_count - 1); 1 plans in this process
"""
import os, pickle, queue, subprocess, sys, threading
from pathlib import Path

SRC = str(Path(__file__).resolve().parents[2])          # the directory that holds the f19 package


def workers_wanted():
    """How many worker processes to use (1 = plan in this process)."""
    env = os.environ.get("F19_PLAN_WORKERS")
    return max(1, int(env) if env else (os.cpu_count() or 1) - 1)


class WorkerError(RuntimeError):
    pass


class Pool:
    def __init__(self, n, init):
        """Start n workers, each sent `init` (picklable) first; they build their Planner while the caller goes on (the first
        map() waits for them). Raises WorkerError if one cannot be started."""
        env = {**os.environ, "OMP_NUM_THREADS": "1", "VECLIB_MAXIMUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
               "PYTHONPATH": os.pathsep.join(filter(None, [SRC, os.environ.get("PYTHONPATH")]))}
        self.procs = []
        try:
            for _ in range(n):
                self.procs.append(subprocess.Popen([sys.executable, "-m", "f19.planner.worker"], stdin=subprocess.PIPE,
                                                   stdout=subprocess.PIPE, env=env))
            for p in self.procs: self._send(p, ("init", *init))
        except BaseException:
            self.close(); raise
        self.ready = False

    def _wait_ready(self):
        if self.ready: return
        for p in self.procs:
            msg = self._recv(p)
            if msg[0] != "ready": raise WorkerError("worker did not start: " + str(msg[1] if len(msg) > 1 else msg))
        self.ready = True

    @staticmethod
    def _send(p, msg):
        try: pickle.dump(msg, p.stdin, protocol=pickle.HIGHEST_PROTOCOL); p.stdin.flush()
        except (BrokenPipeError, OSError) as e: raise WorkerError(f"worker pipe closed: {e}") from e

    @staticmethod
    def _recv(p):
        try: return pickle.load(p.stdout)
        except (EOFError, OSError, pickle.UnpicklingError) as e: raise WorkerError(f"worker died: {e!r}") from e

    def map(self, jobs):
        """Replies to the jobs (messages: ("plan", seq), ("search", a, b, h0, h_goal, margin_km)), in job order. Workers take the next job as they
        finish one."""
        self._wait_ready()
        todo = queue.Queue()
        for i, j in enumerate(jobs): todo.put((i, j))
        out, errs = [None] * len(jobs), []

        def run(p):
            while not errs:
                try: i, j = todo.get_nowait()
                except queue.Empty: return
                try:
                    self._send(p, j); out[i] = self._recv(p)
                except WorkerError as e:
                    errs.append(e); return
        threads = [threading.Thread(target=run, args=(p,)) for p in self.procs]
        for t in threads: t.start()
        for t in threads: t.join()
        if errs: raise errs[0]
        return out

    def broadcast(self, msg):
        """Send msg to every worker and wait for each one's answer (a ("seed", ...) message: ("ack",))."""
        self._wait_ready()
        for p in self.procs: self._send(p, msg)
        for p in self.procs: self._recv(p)

    def close(self):
        for p in self.procs:
            try: p.stdin.close()
            except OSError: pass
        for p in self.procs:
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
            try: p.stdout.close()
            except OSError: pass
        self.procs = []

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
