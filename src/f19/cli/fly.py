"""f19 fly: the autopilot on the pilot layers (WP-7a). scripts/nav.py is a shim for this module (WP-7d).

  python -m f19.cli.fly --route ROUTE [--auto] [--memlog] [--alt FT] [--hdg] [--attack-now ROLE] [--land-now]
                        [--done ROLES] [--strafe RECORDS|nearest] [--missile-watch act|observe|off] [--plain]
      LIVE: connects to the running game and SENDS KEYS. Only for a flight the user asked for, while they watch.

  python -m f19.cli.fly ... --no-keys
      LIVE, READ-ONLY: reads the running game and runs the whole pilot, but Controls talks to a recorder: no key is sent.

  python -m f19.cli.fly --replay <memlog dir> --dry-run [--route ROUTE] [--auto ...] [--out DIR]
      OFFLINE: the whole stack (reader -> arbiter -> Controls -> loops -> telemetry) on a recorded flight's memlog
      snapshots, with a recording fake emulator: nothing is sent anywhere. Prints / writes the key stream (keys.txt)
      and the nav CSV + events (CsvSink, with route_want / behavior / plan_version). Open loop: the recorded memory
      shows what the recorded flight's keys did, not these.

Flags as nav.py (--fixed is not ported) plus --director, --no-keys. Wiring: MemoryReader -> Runner(Controls, Arbiter(FlyLeg + phases +
reactions), Loops, TelemetryBus[CsvSink, MemlogSink, HUD], WallClock(time.time), StaticRoute(route.json)).
"""
import argparse
import contextlib
import json
import signal
import sys
import time
from pathlib import Path

from f19.cli.gameworld import GameWorld
from f19.core.clock import ManualClock, WallClock
from f19.core.geometry import bearing_deg, dist, wrap_deg
from f19.core.settings import settings as S
from f19.core.types import FlightContext, Route, RoutePoint
from f19.core.units import K
from f19.cli.plain import FallbackSink, PlainSink
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
          sinks=(), route=None, logs=None):
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
    loops = Loops(clock, modes, (Path(logs) if logs is not None else S.logs) / "nav" / "ALT" if not args.dry_run else None, speed)
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
    provider = StaticRoute(route)
    if getattr(args, "director", False):
        provider = director_for(args, route, rd, mm, dry=bool(args.dry_run))
        ctx.route = provider.current()
    return Runner(reader, controls, arb, loops, bus, clock, provider, ctx=ctx, reads=reads, memlog=memlog,
                  sleep=sleep, side=side, terrain=probe, site_names=site_names, say=say, wall=wall,
                  allow_eject=bool(getattr(args, "allow_eject", True)))


def director_for(args, route, rd, mm, dry):
    """--director: the FlightDirector (WP-7c) as the route provider. Needs a planner --route with its plan directory's
    mission_mem.json. Live: the planning context is built now (1.5-3 s) and plans run on a worker thread; dry run: built
    on first use and planned inline (deterministic)."""
    from f19.core.types import Mission
    from f19.pilot.director import FlightDirector, InlineExecutor, ThreadExecutor
    from f19.planner import api
    if not (args.route and mm and rd.get("spec")):
        raise SystemExit("fly --director: needs --route with a plan directory (route.json with its spec, mission_mem.json)")
    mission = Mission.from_mission_json(mm)
    factory = lambda: api.context(rd["spec"], mm)
    return FlightDirector(mission, route, planner=None if dry else factory(), planner_factory=factory if dry else None,
                          executor=InlineExecutor() if dry else ThreadExecutor())


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
@contextlib.contextmanager
def stop_signals():
    """SIGINT / SIGTERM raise KeyboardInterrupt for the whole live flight (start-up included), also for a process started in
    the background (a non-interactive shell's `&` ignores SIGINT; a kill -9 mid-read left replies queued for the next
    client): the handlers unwind to the `finally` that releases the keys. Restored on exit. Main thread only."""
    def _stop(*_): raise KeyboardInterrupt
    old = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: old[sig] = signal.signal(sig, _stop)
        except ValueError: pass                           # not the main thread
    try: yield
    finally:
        for sig, h in old.items(): signal.signal(sig, h)


def live(args, *, connect_fn=None, reader_factory=MemoryReader, say=None, sleep=time.sleep, wall=time.time, logs=None,
         tty=None, hud_factory=None, world=None):
    """The live flight: nav's start-up (connect, settle, sanity checks on the route, ALT file reset, display) then
    Runner.run(). Everything it touches is injectable so tests can run it on a fake emulator: connect_fn() -> an emulator
    context manager (default f19.emulator.connect), reader_factory(emu) -> reader, logs: the log root (default F19_DATA/logs).
    Keys are released on every way out: an exit, an exception, SIGINT / SIGTERM (also during start-up)."""
    if say is None: say = lambda m: print(m, flush=True)
    if connect_fn is None:
        from f19.emulator import connect as connect_fn
    logs = Path(logs) if logs is not None else S.logs
    tty = sys.stdout.isatty() if tty is None else tty
    with stop_signals():
        while True:
            try: emu = connect_fn(); break
            except (TimeoutError, OSError, RuntimeError):
                say("fly: automation port not answering (emulator paused?) - retrying"); sleep(2)
        with emu:
            reader = reader_factory(emu)
            clock = WallClock(time_fn=wall)
            keys = emu
            if args.no_keys:                                  # read-only flight: Controls talks to a recorder, never the game
                keys = RecordingEmulator(clock)
                say("fly --no-keys: reading the live game, NO key is sent (the key stream goes to logs/nav/<id>.keys.txt)")
            probe = Controls(keys)
            sinks, runner, rid = [], None, str(int(wall()))
            try:
                tmp = Runner.__new__(Runner)                  # settle needs only reader / controls / say / sleep / wall
                tmp.reader, tmp.controls, tmp.say, tmp.sleep, tmp.wall = reader, probe, say, sleep, wall
                Runner.settle(tmp)
                s0 = reader.snapshot()
                check_route(args, reader, s0, world=world, say=say)
                memlog = None
                if args.memlog:
                    wb = {}
                    for th in ("CE", "NC", "LIBYA", "GULF"):
                        f = S.game_dir / f"{th}.WLD"
                        if f.exists(): wb[th] = f.read_bytes()
                    memlog = MemLog(emu, wb, clock=wall)
                    sinks.append(MemlogSink(logs / "memlog" / str(int(wall())), memlog.layout))
                sinks.append(CsvSink(logs / "nav" / f"{rid}.csv", logs / "nav" / f"{rid}.events.txt", columns=EXTENDED_COLUMNS))
                route = Route.from_route_json(args.route) if args.route else game_route(s0)
                rd, mm, _ = flight_files(args.route) if args.route else ({}, {}, None)
                plain = PlainSink(radars=mm.get("radars", []), clear=tty)
                if args.plain or not tty: sinks.append(plain)
                else:
                    try: hud = (hud_factory or hud_sink)(args, route)
                    except Exception as e:                    # noqa: BLE001
                        say(f"fly: display failed ({e!r}) - plain text from here"); hud = None
                    sinks.append(FallbackSink(hud, plain, say) if hud is not None else plain)
                (logs / "nav" / "ALT").unlink(missing_ok=True)   # a stale altitude-schedule file from an earlier flight
                runner = build(args, reader, keys, clock, s0, say=say, memlog=memlog, sinks=sinks, route=route, logs=logs,
                               sleep=sleep, wall=wall)
                runner.exit_on_done = args.exit_on_done
                result = runner.run(signals=False)                     # stop_signals() is installed; run() releases in its finally
            except BaseException:
                if runner is None: probe.release_all()        # failed or interrupted before the runner existed
                raise
            finally:
                if args.no_keys:
                    try:
                        (logs / "nav").mkdir(parents=True, exist_ok=True)
                        (logs / "nav" / f"{rid}.keys.txt").write_text("\n".join(" ".join(map(str, e)) for e in keys.events) + "\n")
                    except (OSError, NameError): pass
                for sk in sinks:
                    try: sk.close()
                    except Exception: pass                    # noqa: BLE001
            return result


def hud_sink(args, route):  # pragma: no cover - live display
    """The HUD as a telemetry sink (WP-8): the Frame's nav view + the game's event list the runner reads."""
    from f19.cli.hud import load_context
    from f19.hud import Hud, HudFrame, HudSink
    from f19.hud import layout as L
    from f19.hud.terminal import Terminal
    pts = [((q.x, q.y), q.label) for q in route.points]
    ctx = load_context(None, args.route, pts)
    sink = HudSink(Hud(ctx, L.load(None)), Terminal())

    class _Adapter:
        stats = sink.stats
        def write(self, tf):
            if tf.frame is None: return
            sink.write(HudFrame.from_telemetry(tf, event_list=tf.nav.get("event_list")))
            if sink.failed is not None: raise RuntimeError(f"HUD sink failed: {sink.failed!r}")
        def flush(self, timeout=None): return True
        def close(self, timeout=10.0): return sink.close()
    return _Adapter()


def check_route(args, reader, s0, world=None, say=print):
    """nav's start-up checks (scripts/nav.py :408-456), before any key is sent:
      1. the route must belong to the mission in memory (its plan directory's mission_mem.json target slots equal one of the
         mission copies in memory; a stale route crashed a flight 2026-10-14); the error lists plan dirs that match
      2. N/S: on the ground facing away from the route's takeoff direction -> fly the sibling route_S / route_N file
         (args.route is switched), or refuse when there is none
      3. on the ground more than 10 km from the route's takeoff point -> refuse
      4. --auto --land-now: the plane must be 2..60 km before the runway along the approach (a save state that did not
         load left the plane 53 km past it, 2026-10-18) -> refuse otherwise
    Raises SystemExit with nav's messages; may set args.route."""
    if not args.route: return
    rp = Path(args.route)
    mm = rp.parent / "mission_mem.json"
    key = lambda m: sorted((t["x"], t["y"]) for t in m.get("slots", m["targets"]))
    if mm.exists():
        planned = json.loads(mm.read_text())
        # memory can hold more than one mission copy, including a stale one with the same place names (2026-10-15):
        # accept the route if any copy matches it
        copies = MM.parse_all(reader.memory(), planned.get("theater", "NC"), world=world or GameWorld())
        live_ = next((c for c in copies if key(c) == key(planned)), copies[-1] if copies else {"targets": []})
        if key(live_) != key(planned):
            match = []
            for p in sorted(rp.parent.parent.glob("*/mission_mem.json")):
                try:
                    if any(key(json.loads(p.read_text())) == key(c) for c in copies): match.append(str(p.parent))
                except (OSError, ValueError, KeyError): pass
            raise SystemExit(f"fly: {mm.parent} was planned for targets {[(t['place'], t['x'], t['y']) for t in planned['targets']]}\n"
                             f"     but the mission in memory is {[(t['place'], t['x'], t['y']) for t in live_['targets']]}\n"
                             f"     - wrong route file? Not flying it."
                             + (f" Plan dirs matching this mission: {', '.join(match)}" if match else ""))
    pts = Route.from_route_json(rp).points
    if s0.alt_ft < 150 and len(pts) > 1 and abs(wrap_deg(s0.heading - bearing_deg(pts[0].xy, pts[1].xy))) > 90:
        other = rp.with_name(rp.name.replace("_N.", "_X.").replace("_S.", "_N.").replace("_X.", "_S."))
        if other == rp or not other.exists():
            raise SystemExit(f"fly: plane faces {s0.heading:.0f} on the runway but {rp.name} takes off on "
                             f"{bearing_deg(pts[0].xy, pts[1].xy):.0f} and there is no {other.name} - replan with takeoff_heading for this direction.")
        args.route = str(other); say(f"fly: plane faces {s0.heading:.0f} on the runway - flying {other.name}")
        pts = Route.from_route_json(other).points
    if s0.alt_ft < 150 and dist(s0.xy, pts[0].xy) > 10 * K:
        raise SystemExit(f"fly: on the ground {dist(s0.xy, pts[0].xy) / K:.0f} km from the route's takeoff point ({pts[0].label}) "
                         f"- wrong route, or takeoff/landing swapped? Not flying it.")
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
    ap.add_argument("--director", action="store_true", help="replan with the FlightDirector after deviations (WP-7c; needs --route)")
    ap.add_argument("--allow-eject", dest="allow_eject", action="store_true", default=True,
                    help="(default, kept for old command lines) the director may eject (Shift+F10) when it decides to bail out; "
                         "the plane is levelled first (pilot/eject.py)")
    ap.add_argument("--no-eject", dest="allow_eject", action="store_false",
                    help="suppress the eject: it is logged and the plane keeps flying (the director then plans no more)")
    ap.add_argument("--exit-on-done", action="store_true", help="exit once landed and stopped (status 0); 10 after an eject, 11 if not in flight")
    ap.add_argument("--plain", action="store_true", help="no HUD")
    ap.add_argument("--no-keys", action="store_true", help="LIVE, READ-ONLY: read the game and run the whole pilot, but Controls "
                    "talks to a recorder - no key is sent; the would-be key stream is written to logs/nav/<id>.keys.txt")
    ap.add_argument("--fixed", type=float, help="nav's test heading mode: NOT ported (refused)")
    ap.add_argument("--replay", help="OFFLINE: a memlog run directory to fly over (with --dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="OFFLINE: recording fake emulator, nothing sent (needs --replay)")
    ap.add_argument("--out", help="--dry-run: output directory (default F19_DATA/logs/fly_dry/<run>)")
    ap.add_argument("--quiet", action="store_true", help="--dry-run: print only the summary")
    return ap


def resolve(args):
    if args.fixed is not None: raise SystemExit("fly: --fixed is not ported (the old nav is research/scripts/nav_legacy.py; do not run it live)")
    if args.auto:
        if not args.route: raise SystemExit("fly: --auto needs a planner --route")
        args.hdg = True
        if args.alt is None: args.alt = float(json.loads(Path(args.route).read_text()).get("profile", {}).get("cruise_ft") or 200)
    if args.strafe:
        args.hdg = True
        if args.alt is None: args.alt = float(GunAttack.ENTRY_FT)
    return args


def exit_code(result):
    """--exit-on-done status: 0 landed (or no exit text), 10 ejected, 11 not in flight (crashed or mission over)."""
    if result and "ejected" in result: return 10
    if result and "not in flight" in result: return 11
    return 0


def main(argv=None):
    args = resolve(parser().parse_args(argv))
    if args.replay or args.dry_run:
        if not (args.replay and args.dry_run): raise SystemExit("fly: --replay and --dry-run go together (offline mode)")
        dry_run(args); return 0
    try: result = live(args)
    except KeyboardInterrupt:
        print("fly: stopped (SIGINT / SIGTERM) - keys released", flush=True); return 130
    return exit_code(result) if args.exit_on_done else 0


if __name__ == "__main__":
    sys.exit(main())
