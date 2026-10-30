"""A planning worker process (pool.py): reads pickled messages on stdin, answers on stdout.

  ("init", spec, mission, mission_now, walls)   build the Planner (loiters=False: it never searches for loiters, sequences
                                                bring their own) -> ("ready",)
  ("plan", seq, roles)                          Planner.plan_with_loiter(seq, roles) -> ("ok", result, notes) | ("err", message, notes)
                                                | ("exc", traceback)
  ("search", a, b, h0, h_goal, margin)          router.search + router.reduce of its path -> ("ok", (points, heading), reduced,
                                                notes) | ("err", message, notes); reduced is the corners or ("err", message)
  ("seed", searches, reduces)                   add results computed elsewhere to the router's memos -> ("ack",)
mission is what the parent's Planner was built with, mission_now its current mission (targets skipped since), walls whether its
router searches with the live SA-14 cells as walls.
"""
import os, pickle, signal, sys, time, traceback


def main():
    inp, out = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr                  # a stray print must not corrupt the pickle stream
    signal.signal(signal.SIGINT, signal.SIG_IGN)     # a Ctrl-C belongs to the parent, which closes the pipe
    from f19.planner.planner import Planner
    from f19.planner.router import PlanError
    pl = None

    def reply(*msg):
        pickle.dump(msg, out, protocol=pickle.HIGHEST_PROTOCOL); out.flush()
    while True:
        try: msg = pickle.load(inp)
        except EOFError: return
        t0 = time.perf_counter()
        try:
            if msg[0] == "init":
                _, spec, mission, now, walls = msg
                pl = Planner(spec, mission, loiters=False)
                pl.M = now
                pl.router.set_sa14_walls(walls)
                reply("ready")
            elif msg[0] == "plan":
                try: reply("ok", pl.plan_with_loiter(msg[1], msg[2] if len(msg) > 2 else None), sorted(pl.router.notes))
                except PlanError as e: reply("err", str(e), sorted(pl.router.notes))
            elif msg[0] == "search":
                try:
                    pts, h = pl.router.search(*msg[1:])
                    try: red = ("ok", pl.router.reduce(pts))
                    except PlanError as e: red = ("err", str(e))
                    reply("ok", (pts, h), red, sorted(pl.router.notes))
                except PlanError as e: reply("err", str(e), sorted(pl.router.notes))
            elif msg[0] == "seed":
                pl.router.seed(msg[1], msg[2]); reply("ack")
        except Exception:                                            # noqa: BLE001
            reply("exc", traceback.format_exc())
        if os.environ.get("F19_PLAN_TRACE"): print(f"worker {os.getpid()} {msg[0]} {time.perf_counter() - t0:.2f} s", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
