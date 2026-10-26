"""Drive the OLD HUD (scripts/nav_tui.py) offline, as its replay() does, rendering into a buffer.

Patched for determinism (both HUDs then see the same clock): nav_tui's `time` answers time() with the snapshot's
time (its name, epoch s) and strftime/localtime from it, `shutil.get_terminal_size` returns a fixed size, and the cell
buffer of every drawn frame is kept. Nothing else of nav_tui is changed: its Telemetry polls a SnapDB, its UI.frame
builds the context, lays out and draws.
"""
import math
import sys
import time as _time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for _p in (ROOT / "src", ROOT / "analysis", ROOT / "scripts"):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))


class FakeTime:
    def __init__(self): self.now = 0.0
    def time(self): return self.now
    def sleep(self, s): pass
    def localtime(self, t=None): return _time.localtime(self.now if t is None else t)
    def strftime(self, fmt, tm=None): return _time.strftime(fmt, tm if tm is not None else _time.localtime(self.now))
    def monotonic(self): return self.now


class _Null:
    def write(self, s): pass
    def flush(self): pass


def load():
    import nav_tui
    return nav_tui


def snapshots(run_dir):
    import json
    d = Path(run_dir); win = json.load(open(d / "windows.json"))
    windows = [tuple(w) for w in win["windows"]]
    snaps = sorted(d.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))
    return windows, snaps


def replay_frames(run_dir, route=None, size=(160, 50), render=lambda i: True, keys=None, scan=True):
    """Yield (snapshot index, snapshot t, Buf or None, ui) per snapshot nav_tui.replay would show. render(i): draw
    this one (else only feed the telemetry, trail and log, as a frame inside FRAME_S does). keys: {i: str} pressed
    before frame i. scan=False: seed f19_state's offset cache with windows.json's offset after replay()'s per-snapshot
    clear instead of scanning memory for the signature (~15 ms a snapshot; the slow tests scan and find the same)."""
    nt = load()
    import f19_state as fs
    clock = FakeTime()
    bufs = []

    class RecBuf(nt.Buf):
        def __init__(self, w, h):
            super().__init__(w, h); bufs.append(self)

    saved = nt.time, nt.shutil, nt.Buf
    nt.time = clock
    nt.shutil = types.SimpleNamespace(get_terminal_size=lambda fallback=None: size)
    nt.Buf = RecBuf
    try:
        yield from _frames(nt, fs, clock, bufs, run_dir, route, render, keys, scan)
    finally:
        nt.time, nt.shutil, nt.Buf = saved


def _frames(nt, fs, clock, bufs, run_dir, route, render, keys, scan):
    d = Path(run_dir)
    windows, snaps = snapshots(d)
    import json
    offset = json.loads((d / "windows.json").read_text())["offset"]
    th = next((n.split("_", 1)[1] for n, _, _ in windows if n.startswith("world_")), None)
    th = {"LIBYA": "LB", "GULF": "PG"}.get(th, th)
    db = nt.SnapDB(windows)
    wps = None
    if route:
        import json
        r = json.load(open(route)); wps = [((q["x"], q["y"]), q["label"]) for q in r.get("points", [])] or None
    ui = nt.UI(th, route, wps=wps, out=_Null())
    ui.tel.PERIOD = 0.0
    ui.started = True                                   # no alt screen, no tty
    import territory
    for i, f in enumerate(snaps):
        clock.now = float(f.stem[5:])
        db.load(f.read_bytes()); fs._offset.clear()
        if not scan: fs._offset[id(db)] = offset
        try: s = fs.read_state(db, tries=1)
        except RuntimeError: continue
        if not s["x"]: continue
        p = (s["x"] / 32, s["y"] / 32)
        leg = 1
        if wps:
            leg = min(range(1, len(wps)), key=lambda j: math.dist(p, wps[j][0]) + math.dist(p, wps[j - 1][0]) - math.dist(wps[j][0], wps[j - 1][0]))
        gw, sel = fs.read_waypoints(db)
        v = dict(p=p, alt=s["alt"], kt=s["spd"], hdg=s["hdg"], phase="replay", leg=leg, wps=wps, gw=gw, sel=sel,
                 want=nt.bearing(p, wps[leg][0]) if wps else None, banner=f"REPLAY {d.name}  snapshot {i + 1}/{len(snaps)}",
                 territory=territory.side(th, *p) if th else None,
                 route_left_km=(math.dist(p, wps[leg][0]) + sum(math.dist(wps[j][0], wps[j + 1][0]) for j in range(leg, len(wps) - 1))) / nt.K if wps else None,
                 next_name=wps[leg][1] if wps else None, next_wp=leg)
        if keys and i in keys: ui.handle(keys[i])
        draw = render(i)
        ui.last = 0 if draw else clock.now
        bufs.clear()
        ui.frame(db, v)
        yield i, clock.now, (bufs[-1] if draw and bufs else None), ui
