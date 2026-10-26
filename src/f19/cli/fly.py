"""f19 fly: the autopilot on the pilot layers (WP-7a) - scripts/nav.py's replacement (the cutover is WP-7d).

  python -m f19.cli.fly --route ROUTE [--auto] [--memlog] [--alt FT] [--hdg] [--attack-now ROLE] [--land-now]
                        [--done ROLES] [--strafe RECORDS|nearest] [--missile-watch act|observe|off] [--plain]
      LIVE: connects to the running game and SENDS KEYS. Only for a flight the user asked for, while they watch.

  python -m f19.cli.fly --replay <memlog dir> --dry-run [--route ROUTE] [--auto ...] [--out DIR]
      OFFLINE: the whole stack (reader -> arbiter -> Controls -> loops -> telemetry) on a recorded flight's memlog
      snapshots, with a recording fake emulator: nothing is sent anywhere. Prints / writes the key stream (keys.txt)
      and the nav CSV + events (CsvSink, with route_want / behavior / plan_version). Open loop: the recorded memory
      shows what the recorded flight's keys did, not these.

Flags as nav.py (--fixed is not ported). Wiring: MemoryReader -> Runner(Controls, Arbiter(FlyLeg + phases +
reactions), Loops, TelemetryBus[CsvSink, MemlogSink, HUD], WallClock(time.time), StaticRoute(route.json)).
"""
import argparse
import json
import sys
import time
from pathlib import Path

from f19.core.clock import ManualClock, WallClock
from f19.core.geometry import bearing_deg, dist, wrap_deg
from f19.core.settings import settings as S
from f19.core.types import FlightContext, Route, RoutePoint
from f19.core.units import K
from f19.gamedata import wld
from f19.memory import MemoryReader, ReplayReader
from f19.memory import mission as MM
from f19.memory import reader as MR
from f19.pilot.arbiter import Arbiter
from f19.pilot.controls import Controls
from f19.pilot.loops import Loops, Modes, SpeedHold
from f19.pilot.phases import FlyLeg, GunAttack
from f19.pilot.reactions import CmKeys, FighterWatch, FuelTransfer, MissileWatch, TerrainEscape
from f19.pilot.runner import GAME_WP_NAMES, MemLog, Reads, Runner, StaticRoute, terrain_ahead_probe
from f19.telemetry import EXTENDED_COLUMNS, CsvSink, MemlogSink, TelemetryBus


# ----------------------------------------------------------------------------------------------- the fake emulator
class RecordingEmulator:
    """Stands in for f19.emulator.Emulator's key API in the dry run: records (t, event) and never sleeps or sends."""
    def __init__(self, clock):
        self.clock, self.events, self._held = clock, [], set()

    def _rec(self, *e): self.events.append((self.clock.now(),) + e)
    def tap(self, key, hold_secs=0.05): self._rec("tap", key, round(hold_secs, 6))
    def scan_tap(self, code, hold_secs=0.5, shift=False): self._rec("scan", code, shift, round(hold_secs, 6))
    def key_down(self, key): self._held.add(key); self._rec("down", key)
    def key_up(self, key): self._held.discard(key); self._rec("up", key)
    def release_arrows(self): self._rec("release_arrows")

    @property
    def held(self): return frozenset(self._held)


# ------------------------------------------------------------------------------------------------------ helpers
class _World:
    """f19.memory.mission.World over the game's .WLD files (read_mission.World)."""
    def __init__(self):
        from f19.cli.hud import radar_systems
        self.R = radar_systems()
    def wld_bytes(self, th):
        try: return wld.wld_bytes(th)
        except OSError: return None
    def names(self, th): return wld.places(th)["names"]
    def radar(self, kind): return self.R.get(kind)


def _json(p):
    try: return json.loads(Path(p).read_text())
    except (OSError, ValueError): return None


def flight_files(route_path):
    """(route dict, mission_mem dict or {}, terrain probe or None) from a route.json's plan directory."""
    rd = json.loads(Path(route_path).read_text())
    mm = _json(Path(route_path).parent / "mission_mem.json") or {}
    probe = None
    tz = Path(route_path).parent / "terrain.npz"
    if tz.exists():
        import numpy as np
        t = np.load(tz)
        if "height_game" in t: probe = terrain_ahead_probe(t["height_game"], int(t["height_step"]))
    return rd, mm, probe


def game_route(state):
    """No --route: start -> the game's own WP1..WP4, skipping repeats (nav)."""
    pts = [RoutePoint(state.pos.x, state.pos.y, "start")]
    for k, w in enumerate(state.waypoints):
        if dist((pts[-1].x, pts[-1].y), w) > 3.0 * K: pts.append(RoutePoint(float(w[0]), float(w[1]), GAME_WP_NAMES[k]))
    return Route(tuple(pts))


def fill_records(db, attacks):
    """Target records missing from older mission files: the in-flight briefing blocks have them (nav)."""
    try:
        for (typ, rec), A in zip(MR.read_briefing(db), sorted(attacks.values(), key=lambda A: A is not attacks.get("primary"))):
            if A["record"] is None and MR.read_target(db, rec)[:2] == tuple(map(int, A["T"])): A["record"] = rec
    except Exception:                                      # noqa: BLE001 - as nav: best effort
        pass


def build(args, reader, emulator, clock, s0, *, sleep=time.sleep, wall=time.time, say=print, memlog=None,
          sinks=(), route=None):
    """The whole stack for a flight starting at GameState s0. -> Runner."""
    modes = Modes(hdg=bool(args.hdg), alt=args.alt, auto=bool(args.auto), strafe=bool(args.strafe))
    rd, mm, probe = flight_files(args.route) if args.route else ({}, {}, None)
    prof = rd.get("profile", {})
    cruise_kt, cruise_ft = float(prof.get("cruise_kt") or 190), float(prof.get("cruise_ft") or 450)
    route = route or (Route.from_route_json(args.route) if args.route else game_route(s0))
    th = mm.get("theater")
    gbyte = side = None
    if th:
        try:
            g = wld.grid(th)
            gbyte = lambda x_, y_: int(g[min(15, max(0, int(y_) >> 11)), min(15, max(0, int(x_) >> 11))])
            side = lambda x_, y_: wld.side(th, x_, y_)
        except (OSError, ValueError, KeyError):
            pass
    speed = SpeedHold()
    controls = Controls(emulator, speed)
    loops = Loops(clock, modes, S.logs / "nav" / "ALT" if not args.dry_run else None, speed)
    reads = Reads(reader)
    fly = FlyLeg()
    ctx = FlightContext(clock, route=route, reference=route, reader=reader)
    fly._points(ctx)
    attacks = None
    if args.auto and mm.get("targets"):
        attacks = fly.attacks(mm); fill_records(reader.emu, attacks)
        say("fly --auto attacks: " + ", ".join(f"{r} {a['mode']}" for r, a in attacks.items()))
    strafe = []
    if args.strafe:
        live = MR.read_sites(reader.emu); p0 = s0.xy
        for w in args.strafe.split(","):
            rec = min((r for r in live if r not in strafe), key=lambda r: dist(p0, live[r]), default=None) if w == "nearest" else int(w)
            if rec is None or rec not in live: say(f"fly --strafe: record {w} is not a live radar site - skipped"); continue
            strafe.append(rec)
        if not strafe: raise SystemExit("fly --strafe: nothing to attack")
    escape = TerrainEscape(lambda p_, h_, a_, mk_: probe(p_, h_, a_, max_km=mk_, margin_ft=TerrainEscape.MARGIN_FT)) if probe else None
    arb = Arbiter(fly, modes, speed=speed, reads=reads, cruise_kt=cruise_kt, cruise_ft=cruise_ft, attacks=attacks,
                  attack_now=args.attack_now, land_now=args.land_now, done=[r for r in (args.done or "").split(",") if r],
                  strafe=strafe, fuel=FuelTransfer(), fighter=FighterWatch((lambda x_, y_: (gbyte(x_, y_) >> 2) & 3) if gbyte else None, gbyte),
                  missile_mode=args.missile_watch, missile_factory=lambda mode: MissileWatch(mode, gbyte), cmkeys=CmKeys(),
                  escape=escape)
    bus = TelemetryBus(sinks)
    site_names = {int(r["record"]) + 3: f"{r['place']} {r['system']}" for r in mm.get("radars", []) if "record" in r and r.get("system")}
    return Runner(reader, controls, arb, loops, bus, clock, StaticRoute(route), ctx=ctx, reads=reads, memlog=memlog,
                  sleep=sleep, side=side, terrain=probe, site_names=site_names, say=say, wall=wall)


# --------------------------------------------------------------------------------------------------- dry run
def dry_run(args):
    """--replay <memlog dir> --dry-run: every snapshot through the stack; nothing is sent."""
    d = Path(args.replay)
    rr = ReplayReader(d)
    clock = ManualClock(0.0)
    emu = RecordingEmulator(clock)
    out = Path(args.out) if args.out else S.logs / "fly_dry" / d.name
    out.mkdir(parents=True, exist_ok=True)
    csv_sink = CsvSink(out / f"{d.name}.csv", out / f"{d.name}.events.txt", columns=EXTENDED_COLUMNS, block=True)
    runner, n, n_in, exit_msg = None, 0, 0, None
    lines = []
    try:
        for i, t in enumerate(rr.times()):
            rr.seek_index(i); clock.set(t)
            if runner is None:
                try: s0 = rr.snapshot()
                except RuntimeError: continue
                if not s0.pos.x: continue
                runner = build(args, rr, emu, clock, s0, sleep=lambda s: None, wall=clock.now, say=lambda m: None,
                               sinks=(csv_sink,))
            r = runner.tick(now=t); n += 1; n_in += r.in_flight
            if r.decision is not None and not args.quiet:
                dec = r.decision
                lines.append(f"{t:.2f} {dec.phase:<8} {dec.behavior:<15} want {dec.want:6.1f} route {dec.route_want:6.1f}"
                             + (f"  {r.report.press}" if r.report and r.report.press else "")
                             + "".join(f"\n      {e.text}" for e in r.events))
            if r.exit: exit_msg = r.exit; break
    finally:
        if runner is not None: runner.release()
        csv_sink.close()
    keys = [" ".join(map(str, e)) for e in emu.events]
    (out / "keys.txt").write_text("\n".join(keys) + "\n")
    if not args.quiet:
        print("\n".join(lines))
        print("\nkeys:"); print("\n".join(keys[:400]) + ("\n..." if len(keys) > 400 else ""))
    print(f"fly --dry-run {d.name}: {n} ticks ({n_in} in flight), {len(keys)} key events"
          + (f", exit: {exit_msg}" if exit_msg else "") + f"; wrote {out}/keys.txt, {d.name}.csv, {d.name}.events.txt")
    return dict(ticks=n, in_flight=n_in, keys=emu.events, out=out, exit=exit_msg, runner=runner)


# ------------------------------------------------------------------------------------------------------ live
def live(args):  # pragma: no cover - sends keys to the running game; never run in tests
    """The live flight: as nav.py's start-up (connect, settle, sanity checks on the route) then Runner.run()."""
    from f19.emulator import connect
    say = lambda m: print(m, flush=True)
    while True:
        try: emu = connect(); break
        except (TimeoutError, OSError, RuntimeError):
            say("fly: automation port not answering (emulator paused?) - retrying"); time.sleep(2)
    with emu:
        reader = MemoryReader(emu)
        clock = WallClock(time_fn=time.time)
        controls_probe = Controls(emu)
        tmp = Runner.__new__(Runner)                      # settle needs only reader / controls / say / sleep / wall
        tmp.reader, tmp.controls, tmp.say, tmp.sleep, tmp.wall = reader, controls_probe, say, time.sleep, time.time
        Runner.settle(tmp)
        s0 = reader.snapshot()
        check_route(args, reader, s0)
        memlog = sinks = None
        sinks = []
        if args.memlog:
            wb = {}
            for th in ("CE", "NC", "LIBYA", "GULF"):
                f = S.game_dir / f"{th}.WLD"
                if f.exists(): wb[th] = f.read_bytes()
            memlog = MemLog(emu, wb, clock=time.time)
            sinks.append(MemlogSink(S.logs / "memlog" / str(int(time.time())), memlog.layout))
        rid = str(int(time.time()))
        sinks.append(CsvSink(S.logs / "nav" / f"{rid}.csv", S.logs / "nav" / f"{rid}.events.txt", columns=EXTENDED_COLUMNS))
        if not args.plain and sys.stdout.isatty() and args.route:
            sinks.append(hud_sink(args, reader))
        (S.logs / "nav" / "ALT").unlink(missing_ok=True)
        runner = build(args, reader, emu, clock, s0, say=say, memlog=memlog, sinks=sinks)
        try: runner.run()
        finally:
            for s in sinks:
                try: s.close()
                except Exception: pass            # noqa: BLE001


def hud_sink(args, reader):  # pragma: no cover - live display
    """The HUD as a telemetry sink (WP-8): the Frame's nav view + the game's event list the runner reads."""
    from f19.cli.hud import load_context, route_points
    from f19.hud import Hud, HudFrame, HudSink
    from f19.hud import layout as L
    from f19.hud.terminal import Terminal
    ctx = load_context(None, args.route, route_points(args.route))
    sink = HudSink(Hud(ctx, L.load(None)), Terminal())

    class _Adapter:
        stats = sink.stats
        def write(self, tf):
            if tf.frame is None: return
            sink.write(HudFrame.from_telemetry(tf, event_list=tf.nav.get("event_list")))
        def flush(self, timeout=None): return True
        def close(self, timeout=10.0): return sink.close()
    return _Adapter()


def check_route(args, reader, s0):  # pragma: no cover - live start-up checks (nav.py :408-456)
    """The route must belong to the mission in memory; fly the N/S variant that matches the plane on the runway; refuse
    a takeoff point far away or a --land-now from the wrong side."""
    if not args.route: return
    rp = Path(args.route)
    mm = rp.parent / "mission_mem.json"
    if mm.exists():
        planned = json.loads(mm.read_text())
        copies = MM.parse_all(reader.memory(), planned.get("theater", "NC"), world=_World())
        key = lambda m: sorted((t["x"], t["y"]) for t in m.get("slots", m["targets"]))
        live_ = next((c for c in copies if key(c) == key(planned)), copies[-1] if copies else {"targets": []})
        if key(live_) != key(planned):
            raise SystemExit(f"fly: {mm.parent} was planned for targets {[(t['place'], t['x'], t['y']) for t in planned['targets']]}\n"
                             f"     but the mission in memory is {[(t['place'], t['x'], t['y']) for t in live_['targets']]}\n"
                             f"     - wrong route file? Not flying it.")
    pts = Route.from_route_json(rp).points
    if s0.alt_ft < 150 and len(pts) > 1 and abs(wrap_deg(s0.heading - bearing_deg(pts[0].xy, pts[1].xy))) > 90:
        other = rp.with_name(rp.name.replace("_N.", "_X.").replace("_S.", "_N.").replace("_X.", "_S."))
        if other == rp or not other.exists():
            raise SystemExit(f"fly: plane faces {s0.heading:.0f} on the runway but {rp.name} takes off on "
                             f"{bearing_deg(pts[0].xy, pts[1].xy):.0f} and there is no {other.name} - replan.")
        args.route = str(other); print(f"fly: plane faces {s0.heading:.0f} on the runway - flying {other.name}", flush=True)
        pts = Route.from_route_json(other).points
    if s0.alt_ft < 150 and dist(s0.xy, pts[0].xy) > 10 * K:
        raise SystemExit(f"fly: on the ground {dist(s0.xy, pts[0].xy) / K:.0f} km from the route's takeoff point - not flying it.")
    if args.auto and args.land_now:
        fx = next(q.xy for q in pts if q.label == "Approach fix"); rw = pts[-1].xy
        ux, uy = (rw[0] - fx[0]) / dist(fx, rw), (rw[1] - fx[1]) / dist(fx, rw)
        before = (rw[0] - s0.xy[0]) * ux + (rw[1] - s0.xy[1]) * uy
        if before < 2 * K or dist(s0.xy, rw) > 60 * K:
            raise SystemExit(f"fly --land-now: plane is {dist(s0.xy, rw) / K:.0f} km from the runway, {before / K:+.0f} km "
                             f"along the approach (needs 2..60 km before it) - not starting a landing from here")


# ------------------------------------------------------------------------------------------------------- main
def parser():
    ap = argparse.ArgumentParser(prog="f19 fly", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alt", type=float)
    ap.add_argument("--memlog", action="store_true", help="also snapshot game memory (~1/s) to logs/memlog/<start>/")
    ap.add_argument("--hdg", action="store_true", help="also hold heading (roll keys); keyboard sensitivity must be 1")
    ap.add_argument("--route", help="planner route.json (default: the game's own waypoints)")
    ap.add_argument("--auto", action="store_true", help="full mission: takeoff, attacks, landing; implies --hdg and --alt (cruise_ft)")
    ap.add_argument("--attack-now", help="--auto: start the attack on this role at once")
    ap.add_argument("--land-now", action="store_true", help="--auto: start the landing phase at once")
    ap.add_argument("--done", default="", help="--auto: roles already attacked, comma-separated")
    ap.add_argument("--strafe", help="gun attack on radar sites now: record numbers, comma-separated, or 'nearest'")
    ap.add_argument("--missile-watch", choices=("act", "observe", "off"), help="enemy-missile evasion (default act at Elite)")
    ap.add_argument("--plain", action="store_true", help="no HUD")
    ap.add_argument("--fixed", type=float, help="nav's test heading mode: NOT ported (refused)")
    ap.add_argument("--replay", help="OFFLINE: a memlog run directory to fly over (with --dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="OFFLINE: recording fake emulator, nothing sent (needs --replay)")
    ap.add_argument("--out", help="--dry-run: output directory (default F19_DATA/logs/fly_dry/<run>)")
    ap.add_argument("--quiet", action="store_true", help="--dry-run: print only the summary")
    return ap


def resolve(args):
    if args.fixed is not None: raise SystemExit("fly: --fixed is not ported (nav.py still has it)")
    if args.auto:
        if not args.route: raise SystemExit("fly: --auto needs a planner --route")
        args.hdg = True
        if args.alt is None: args.alt = float(json.loads(Path(args.route).read_text()).get("profile", {}).get("cruise_ft") or 200)
    if args.strafe:
        args.hdg = True
        if args.alt is None: args.alt = float(GunAttack.ENTRY_FT)
    return args


def main(argv=None):
    args = resolve(parser().parse_args(argv))
    if args.replay or args.dry_run:
        if not (args.replay and args.dry_run): raise SystemExit("fly: --replay and --dry-run go together (offline mode)")
        dry_run(args); return 0
    live(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
