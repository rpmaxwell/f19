"""f19 hud: the HUD on a recorded flight, and the adapter that turns recordings into HUD frames.

  python -m f19.cli.hud --replay <F19_DATA>/logs/memlog/<id> [--route route.json] [--speed 4] [--layout NAME|PATH]

As `scripts/nav_tui.py --replay`: every memlog snapshot is decoded (memory.ReplayReader) into a GameState plus the
game's event list, nav's view is rebuilt from the route (leg = the leg the plane is nearest, want = bearing to its
end, territory, route left), and the HUD (f19.hud, which never reads memory) draws it, paced by the snapshot times / speed.

The adapter is also where per-flight files are read (load_context): route.json, its plan dir's mission_mem.json,
plan.json and terrain.npz, the theater's territory grid (.WLD), place names and decoded terrain (F19_DATA/logs/theater).
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

from f19.core.geometry import bearing_deg
from f19.core.settings import settings as S
from f19.core.units import K
from f19.gamedata import wld
from f19.hud import Hud, HudContext, HudFrame, HudSink
from f19.hud import layout as L
from f19.hud.terminal import NullTerminal, Terminal
from f19.memory import ReplayReader
from f19.rules.radar import radar_systems
from f19.telemetry import MemlogSource, ReplaySource

def theater_of_windows(windows):
    """Theater code from a memlog's window names (world_CE, world_LIBYA, ...), None if none."""
    th = next((n.split("_", 1)[1] for n, _, _ in windows if n.startswith("world_")), None)
    return {"LIBYA": "LB", "GULF": "PG"}.get(th, th)


def route_points(route):
    """[((x, y), label)] of a route.json's points (None if it has none)."""
    if not route: return None
    r = route if isinstance(route, dict) else json.loads(Path(route).read_text())
    return [((q["x"], q["y"]), q["label"]) for q in r.get("points", [])] or None


def _json(p):
    try: return json.loads(Path(p).read_text())
    except (OSError, ValueError): return None


def load_context(theater=None, route=None, wps=None, mission=None, logs=None):
    """HudContext for a flight (nav_tui.UI.__init__'s file reads). route: route.json path (its directory is the plan
    dir); mission: a mission_mem dict (default: the plan dir's mission_mem.json)."""
    logs = Path(logs) if logs is not None else S.logs
    rd = json.loads(Path(route).read_text()) if route else {}
    rdir = Path(route).parent if route else None
    if mission is None: mission = (_json(rdir / "mission_mem.json") or {}) if rdir else {}
    th = theater or mission.get("theater")
    heights, step = None, 16
    if rdir and (rdir / "terrain.npz").exists():
        t = np.load(rdir / "terrain.npz")
        if "height_game" in t: heights, step = t["height_game"], int(t["height_step"])
    tz = logs / "theater" / f"{th}_terrain.npz"
    if heights is None and th and tz.exists():                 # theater-wide (analysis/terrain_3d.py)
        t = np.load(tz); heights, step = t["height_ft"], int(t["step_wp"])
    places = {}
    tj = logs / "theater" / f"{th}.json"
    if th and tj.exists(): places = {(r["x"], r["y"]): r["name"] for r in json.loads(tj.read_text())["records"] if r["name"]}
    settings = dict(rd.get("spec", {}).get("settings", {}))
    if rdir and (rdir / "plan.json").exists():
        plan = _json(rdir / "plan.json")
        if plan is not None: settings = {**plan.get("settings", {}), **settings}
    grid = None
    if th:
        try: grid = wld.grid(th)
        except (OSError, ValueError): grid = None
    return HudContext(theater=th, grid=grid, heights=heights, height_step=step, places=places, mission=mission,
                      route=rd, settings=settings, wps=wps, radar_systems=radar_systems())


def replay_view(state, wps, theater, run_name, i, n):
    """nav's view for a replayed snapshot (nav_tui.replay): nearest leg, want = bearing to its end, territory."""
    p = state.xy
    leg = 1
    if wps:
        leg = min(range(1, len(wps)), key=lambda j: math.dist(p, wps[j][0]) + math.dist(p, wps[j - 1][0]) - math.dist(wps[j][0], wps[j - 1][0]))
    return dict(p=p, phase="replay", leg=leg, wps=wps, want=bearing_deg(p, wps[leg][0]) if wps else None,
                banner=f"REPLAY {run_name}  snapshot {i + 1}/{n}", territory=wld.side(theater, *p) if theater else None,
                route_left_km=(math.dist(p, wps[leg][0]) + sum(math.dist(wps[j][0], wps[j + 1][0]) for j in range(leg, len(wps) - 1))) / K if wps else None,
                next_name=wps[leg][1] if wps else None, next_wp=leg)


def replay_frames(run_dir, route=None, speed=None, ctx=None, sleep=time.sleep):
    """(index, HudFrame) per memlog snapshot with a readable player position, decoded with memory.ReplayReader, paced
    like nav_tui --replay when speed is set."""
    d = Path(run_dir)
    src = MemlogSource(d)
    rr = ReplayReader(d)
    th = ctx.theater if ctx is not None else theater_of_windows(src.layout.windows)
    wps = ctx.wps if ctx is not None else route_points(route)
    n = len(src)
    for i, tf in enumerate(ReplaySource(memlog=src, speed=speed, sleep=sleep)):
        rr.emu.load(tf.snapshot.data); rr.t = tf.t
        errors = {}
        try: state = rr.snapshot()
        except RuntimeError: continue
        if not state.pos.x: continue
        try: events = tuple(rr.event_list(0)[1])
        except Exception as e:                    # noqa: BLE001 - as nav_tui's Telemetry: a failed read is shown, not fatal
            events, errors["events"] = None, str(e)[:60]
        yield i, HudFrame(tf.t, state, replay_view(state, wps, th, d.name, i, n), events, errors)


def replay(run_dir, route=None, speed=4.0, layout=None, terminal=None, hold_s=2.0):
    d = Path(run_dir)
    win = json.loads((d / "windows.json").read_text())
    th = theater_of_windows([tuple(w) for w in win["windows"]])
    ctx = load_context(th, route, route_points(route))
    hud = Hud(ctx, L.load(layout), history_period=0.0)
    sink = HudSink(hud, terminal if terminal is not None else Terminal())
    try:
        for _, fr in replay_frames(d, route, speed, ctx):
            sink.write(fr, force=True)
            if sink.failed: raise RuntimeError(f"display failed: {sink.failed}")
        time.sleep(hold_s)
    except KeyboardInterrupt: pass
    finally: sink.close()
    return hud, sink


def main(argv=None):
    ap = argparse.ArgumentParser(prog="f19 hud", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True, help="memlog run directory (windows.json + snap_*.bin)")
    ap.add_argument("--route", help="route.json the flight flew (its directory: mission_mem.json, plan.json, terrain.npz)")
    ap.add_argument("--speed", type=float, default=4.0, help="replay speed (x real time; 0 = as fast as possible)")
    ap.add_argument("--layout", default=None, help=f"layout name ({', '.join(L.available())}) or a .toml path")
    ap.add_argument("--headless", metavar="WxH", help="no terminal: render at this size and print the last frame's text")
    a = ap.parse_args(argv)
    term = None
    if a.headless:
        w, h = (int(v) for v in a.headless.lower().split("x"))
        term = NullTerminal((w, h))
    hud, sink = replay(a.replay, a.route, a.speed or None, a.layout, term, hold_s=0.0 if a.headless else 2.0)
    if a.headless:
        if term.screen is not None: print("\n".join(term.screen.rows()))
        print(f"{term.frames} frames drawn", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
