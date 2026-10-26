"""The Runner (WP-7a): scripts/nav.py's main loop on the pilot layers.

  per tick:  reader.snapshot() -> GameState (+ time_mult)          not in flight -> hands off, exit after 10 s
             Controls.service(now)                                  scheduled key releases
             Arbiter.step(state, ctx) -> Decision                   route, phases, reactions (their reads via Reads)
             Controls.apply(decision.command) (+ key_ups)           the behavior's keys
             hand-over -> release everything, roll level, holds off
             combat log (the game's event list every 3 s)
             Loops.step -> sensitivity / heading / altitude / flaps / throttle keys
             TelemetryFrame -> bus (CsvSink, MemlogSink, the HUD)  nav's 32 CSV columns + route_want, behavior,
                                                                    plan_version; the HUD view (WP-8's HudFrame.view)
             memlog step (a few PEEK batches of the next snapshot); sleep 0.15 s (0.05 with the memlog)
  exit / SIGINT / SIGTERM / an exception: release every key, centre the stick, roll level (nav's _release).

Also here (pilot-local until they get a home in memory / rules, see the WP-7a report):
  Reads                 the memory reads behaviors used to do inside step(): events, projectiles, target records,
                        gun state, the missile world (f19.memory readers)
  parse_missile_world   analysis/missile_game.parse (the world dict MissileWatch reads) - a copy: pilot can't import
                        analysis/; tests check it against the original on every fixture snapshot
  terrain_ahead_probe   nav's terrain_ahead on the planner's decoded terrain (terrain.npz height_game)
  MemLog                nav's --memlog snapshot reader, interleaved with the control loop
  StaticRoute           the plain route provider: one route.json, no hook. pilot.director.FlightDirector is the other
                        provider: current() / version, plus on_tick(state, decision, ctx, event_list) -> Order | None

Clock: every timer runs on the injected clock (WallClock(time_fn=time.time) = nav's epoch time; the game clock is
WP-7d). Key holds stay real time inside Controls.
"""
import dataclasses
import math
import signal
import struct
import time
from dataclasses import dataclass, field

import numpy as np

from f19.core.geometry import dist, wrap_deg as wrap
from f19.core.types import Frame
from f19.core.units import K
from f19.memory import reader as R
from f19.rules import radar as rg
from f19.rules.missiles import Missile
from f19.telemetry import Event, MemlogLayout, Snapshot, TelemetryFrame


# ------------------------------------------------------------------------------------------------------ reads
# analysis/missile_game.parse's layout (ds offsets)
PROJ, CMS, STORES, OPP, FS, FLAGS, NET_T, NET_REF, HOMING = 0x53FC, 0x520A, 0x4ED4, 0x4EF0, 0x4EFC, 0x684C, 0x5522, 0x6EAA, 0x7F96
PX, PY, PALT, PHDG, PPITCH, PROLL, PKT27, CLOCK, DMG_BITS, DMG_COUNT = 0x94EE, 0x94FE, 0x46E0, 0x46DA, 0x46DC, 0x46DE, 0x7F94, 0x6630, 0x4ECE, 0x95BE
TARGETS, N_TARGETS, SIMOBJ, FRAME, GRID = 0x80AA, 0x9C78, 0x8854, 0x5520, 0x85FC


def parse_missile_world(get):
    """analysis/missile_game.parse, copied: get(ds, n) -> bytes. -> dict(missiles, player, world, stores, sites,
    aircraft, grid, clock, dmg, homing, frame) - GameState.missile_world, what MissileWatch / CmKeys read."""
    u = lambda a: struct.unpack("<H", get(a, 2))[0]
    s = lambda a: struct.unpack("<h", get(a, 2))[0]
    raw = get(PROJ, 0x18 * 8)
    ms = []
    for i in range(8):
        x, y, alt, spd, hdg, pit, _roll, ttl, sp, _t, _x14, lau = struct.unpack_from("<hhhhHhhhhhhh", raw, 0x18 * i)
        if ttl > 0 and 0 < sp < 0x26: ms.append(Missile(i, x, y, alt, spd, hdg, pit, ttl, sp, lau))
    cmr = get(CMS, 48)
    cms = [list(struct.unpack_from("<hhhhhh", cmr, 12 * j)[k] for k in (0, 1, 3, 4)) for j in range(4)]
    n = min(u(N_TARGETS), 120)
    recs = get(TARGETS, 16 * n)
    sites = {}
    for k in range(n):
        x, y, t, fl, al, cd = struct.unpack_from("<HHHHHh", recs, 16 * k + 2)
        if t: sites[k] = dict(record=k, x=x, y=y, type=t, flags=fl, alert=al, countdown=cd, dead=bool(fl & 0x80),
                              on=bool(fl & 2), seen=bool(fl & 0x10))
    sim = get(SIMOBJ, 0x24 * 20)
    aircraft = {}
    for k in range(20):
        x, y, alt = struct.unpack_from("<HHH", sim, 0x24 * k)
        typ, fl = struct.unpack_from("<HH", sim, 0x24 * k + 0x14)
        al = struct.unpack_from("<H", sim, 0x24 * k + 0x20)[0]
        if x or y: aircraft[k] = dict(slot=k, x=x, y=y, alt=alt, type=typ, flags=fl, alert=al, seen=bool(fl & 8))
    player = dict(x=u(PX), y=u(PY), alt=s(PALT), hdg=u(PHDG), pitch=s(PPITCH), roll=s(PROLL), kt=u(PKT27) // 27)
    world = dict(opp=u(OPP), fs=u(FS), flags=u(FLAGS), net_stale=s(NET_T) < s(NET_REF), cms=cms, dmg_bits=u(DMG_BITS))
    return dict(missiles=ms, player=player, world=world, stores=struct.unpack("<3H", get(STORES, 6)), sites=sites,
                aircraft=aircraft, grid=bytes(get(GRID, 256)), clock=u(CLOCK), dmg=u(DMG_COUNT), homing=u(HOMING), frame=u(FRAME))


class Reads:
    """The reads behaviors did inside step() (refactor plan 4.1), at the point the arbiter needs them. reader: an
    f19.memory MemoryReader (live) or ReplayReader."""

    def __init__(self, reader): self.reader = reader

    @property
    def db(self): return self.reader.emu

    def enrich(self, state, records=(), events=False, projectiles=False, gun=False):
        kw = {}
        if records: kw["target_records"] = tuple((r, *R.read_target(self.db, r)) for r in records)
        if events: kw["events"] = tuple(tuple(e) for e in R.read_events(self.db))
        if projectiles:
            kw["projectiles"] = tuple((int.from_bytes(r[0x10:0x12], "little"), int.from_bytes(r[0xE:0x10], "little"))
                                      for r in R.read_projectiles(self.db))
        if gun:
            rounds, fs, nslots = R.read_gun(self.db)
            kw.update(frame_scale=fs, gun_slots=nslots, systems=dataclasses.replace(state.systems, gun_rounds=rounds))
        return dataclasses.replace(state, **kw) if kw else state

    def target(self, record): return R.read_target(self.db, record)

    def missile_world(self, full=True): return parse_missile_world(R.missile_world_memory(self.db, full))

    def time_mult(self): return R.read_time_mult(self.db)

    def events(self): return R.read_events(self.db)

    def damage(self):
        """(dmg bits, dmg count) u16s (nav's combat log)."""
        o = R.find_offset(self.db)
        b = R._peek_many(self.db, [(R.A.DMG_BITS + o, 2), (R.A.DMG_COUNT + o, 2)])
        return tuple(int.from_bytes(x, "little") for x in b)


def terrain_ahead_probe(heights, step):
    """nav.terrain_ahead (the height_game branch) on decoded terrain heights (ft, [y, x] cells of `step` units):
    probe(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100) -> (km, top ft) of the first terrain straight
    ahead within margin_ft of alt, in a corridor +-half_width_km wide, or None."""
    H = heights

    def probe(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100):
        hr = math.radians(hdg); sx, cy = math.sin(hr), -math.cos(hr)
        for d in np.arange(0.25, max_km + 0.01, 0.25):
            tops = []
            for w in (-half_width_km, 0.0, half_width_km):
                gx, gy = p[0] + (sx * d - cy * w) * K, p[1] + (cy * d + sx * w) * K
                i, j = int(gx // step), int(gy // step)
                if 0 <= j < H.shape[0] and 0 <= i < H.shape[1]: tops.append(int(H[j, i]))
            if tops and max(tops) > 0 and max(tops) >= alt - margin_ft: return float(d), max(tops)
        return None
    return probe


# ----------------------------------------------------------------------------------------------- route provider
class StaticRoute:
    """The route provider until WP-7c's FlightDirector: one Route (route.json). replan() is the hook the runner calls
    when a deviation ends; it does nothing yet (returns None = keep the route)."""

    def __init__(self, route): self.route = route

    def current(self): return self.route

    @property
    def version(self): return self.route.version

    def replan(self, state, reason=""): return None


# ------------------------------------------------------------------------------------------------------ memlog
class MemLog:
    """nav --memlog: memory snapshots interleaved with the control loop (the automation port serves one client, so
    they share the pilot's connection). Each snapshot covers the flight program's data area plus the launcher's
    working copy of the theater file (radar table); PER_LOOP batches of BATCH 64-byte PEEKs a tick keep every cycle
    short. step() -> a Snapshot when one is complete (named by the midpoint of its read, as nav), else None.
    The MemlogSink (bus) writes it; layout -> windows.json."""
    BATCH, PER_LOOP = 64, 4

    def __init__(self, emulator, wld_bytes, clock=time.time):
        """wld_bytes: {theater dir name ("CE", "NC", "LIBYA", "GULF"): .WLD bytes} (the cli reads the game dir)."""
        self.emu, self.clock = emulator, clock
        off = R.find_offset(emulator)
        self.windows = [("egame_data", 0x3C000 + off, 0x48000 + off)]
        full = R.read_memory(emulator, 0x0, 0xA0000)
        for th, w in wld_bytes.items():
            i = full.find(w[0x38:0x3E])
            if i >= 0: self.windows.append((f"world_{th}", (i - 0x38) & ~0x3F, ((i - 0x38) & ~0x3F) + 0xC00))
        self.layout = MemlogLayout(off, tuple(self.windows))
        self.addrs = [a for _, lo, hi in self.windows for a in range(lo, hi, 64)]
        self.pos, self.buf, self.t0 = 0, bytearray(), self.clock()

    def step(self):
        for _ in range(self.PER_LOOP):
            chunk = self.addrs[self.pos:self.pos + self.BATCH]
            self.buf += b"".join(R._peek_many(self.emu, [(a, 64) for a in chunk]))
            self.pos += len(chunk)
            if self.pos >= len(self.addrs):
                now = self.clock()
                snap = Snapshot(round((self.t0 + now) / 2, 2), bytes(self.buf))
                self.pos, self.buf, self.t0 = 0, bytearray(), now
                return snap
        return None


# ------------------------------------------------------------------------------------------------------ runner
PHASE_BANNER = {"takeoff": "  TAKEOFF: runway heading, climbing", "route": "", "attack": "", "landing": "", "strafe": "",
                "landed": "  LANDED - stopped on the runway, autopilot off",
                "handover": "  FINAL (6 km out, aligned): autopilot OFF - you have control, land"}
GAME_WP_NAMES = ["WP1 (midpoint)", "WP2 primary", "WP3 secondary", "WP4 landing"]


@dataclass
class TickResult:
    """What one tick did (tests, the dry run)."""
    t: float
    in_flight: bool
    decision: object = None
    report: object = None
    frame: TelemetryFrame | None = None
    exit: str | None = None
    events: list = field(default_factory=list)


class Runner:
    """Runner(reader, controls, arbiter, loops, bus, clock, route_provider, ...). See the module docstring.

    reader: f19.memory MemoryReader / ReplayReader (snapshot(); reads go through Reads(reader)). controls: Controls.
    arbiter: Arbiter (its fly_leg, modes and reactions). loops: Loops (same Modes and SpeedHold). bus: a TelemetryBus
    (or anything with publish). clock: WallClock(time_fn=time.time) live. route_provider: StaticRoute until WP-7c.
    side(x, y) -> territory side text (the cli builds it from the theater's grid); terrain: a terrain_ahead_probe
    for the MOUNTAIN banner; site_names {target record index + 3: "place system"} for the combat log; memlog: MemLog;
    say(text): status lines (not-in-flight, exit)."""
    DEAD_EXIT_S, COMBAT_S, PERIOD_S, PERIOD_MEMLOG_S = 10.0, 3.0, 0.15, 0.05

    def __init__(self, reader, controls, arbiter, loops, bus, clock, route_provider, *, ctx=None, reads=None,
                 memlog=None, sleep=time.sleep, side=None, terrain=None, site_names=None, say=print, event_list_s=1.0,
                 wall=time.time):
        from f19.core.types import FlightContext
        self.reader, self.controls, self.arb, self.loops, self.bus = reader, controls, arbiter, loops, bus
        self.clock, self.routes = clock, route_provider
        self.reads = reads if reads is not None else Reads(reader)
        self.ctx = ctx if ctx is not None else FlightContext(clock, route=route_provider.current(),
                                                             reference=route_provider.current(), reader=reader)
        self.memlog, self.sleep, self.side, self.terrain = memlog, sleep, side, terrain
        self.site_names, self.say, self.wall = site_names or {}, say, wall
        self.event_list_s = event_list_s
        self.dead = 0.0
        self.phase_log = []
        self._ev_seen, self._ev_next = None, 0.0
        self._evl_next, self.event_list = 0.0, None
        self.modes = arbiter.modes

    # ------------------------------------------------------------------------------------------------ releases
    def release(self, roll_level=None):
        """nav's _release: every key up, the stick centred (keypad 5: pitch and bank deflection persist), then - with
        the heading hold on - roll out of any turn (three tries: measure the turn rate over 1.5 s, hold the opposite
        roll key rate / 0.28 s, up to 6 s). Never raises."""
        self.controls.release_all()
        self.loops.heading.roll["key"] = None
        if not (self.modes.hdg if roll_level is None else roll_level): return
        try:
            r = 0.0
            for _ in range(3):
                a = self.reader.snapshot(threats=False); t = self.wall(); self.sleep(1.5)
                b = self.reader.snapshot(threats=False)
                r = wrap(b.heading - a.heading) / max(1e-3, self.wall() - t)
                if abs(r) < 0.25 or b.alt_ft < 30: break
                k = "right" if r < 0 else "left"
                self.controls.down(k); self.sleep(min(6.0, abs(r) / self.loops.heading.H_PER_S)); self.controls.up(k)
                self.sleep(2.0)
            self.say(f"exit: rolled level (last turn rate {r:+.2f} deg/s)")
        except Exception as e:                       # noqa: BLE001
            self.say(f"exit: could not roll level: {e}")

    def settle(self, secs=6.0):
        """nav.settle: hands off until the state reads sane and consistent for `secs` (right after a save-state load
        the reads were garbage for a while): position nonzero, moving < ~0.5 km/s, altitude < 40000, airspeed < 900.
        Releases every key first and, once sane, Controls.after_load (arrow BREAK codes + keypad 5)."""
        for k in ("left", "right", "up", "down"): self.controls.up(k)
        good_since, last, centred = None, None, False
        while True:
            try:
                s = self.reader.snapshot(threats=False); now = self.wall()
                ok = s.pos.x and s.pos.y and s.alt_ft < 40000 and s.speed_kt < 900
                if ok and last is not None:
                    jump = math.hypot(s.pos.x - last[1].pos.x, s.pos.y - last[1].pos.y) / K
                    ok = jump < 0.05 * max(1.0, now - last[0]) * 10
                if ok: good_since = good_since or now
                else: good_since = None; self.reader.offset(refresh=True)
                last = (now, s)
                self.say(f"  F-19 NAV: getting bearings - hands off ({0 if good_since is None else now - good_since:.0f}/{secs:.0f} s)"
                         f"   alt {s.alt_ft} spd {s.speed_kt}")
                if good_since and not centred: self.controls.after_load(); centred = True
                if good_since and now - good_since >= secs: return
            except RuntimeError as e:
                self.say(f"  F-19 NAV: waiting for the game ({e})"); good_since = None
                try: self.reader.offset(refresh=True)
                except RuntimeError: pass
            self.sleep(0.5)

    # ---------------------------------------------------------------------------------------------- combat log
    def combat_log(self, now):
        """Every COMBAT_S: new hits (type 5, by weapon), radar IDs (7, by site), visual IDs (6, by aircraft) and our own
        launches (4) from the game's event list, with the damage bits / count (2026-10-23)."""
        if now < self._ev_next: return []
        self._ev_next = now + self.COMBAT_S
        try: evs = self.reads.events()
        except Exception: return []                  # noqa: BLE001
        if self._ev_seen is None: self._ev_seen = len(evs); return []
        new, self._ev_seen = evs[self._ev_seen:], len(evs)
        if not new: return []
        try: bits, cnt = self.reads.damage()
        except Exception: bits = cnt = None          # noqa: BLE001
        dmg = "" if cnt is None else f" | damage bits {bits:#04x} count {cnt}" + (f", throttle cap {100 - 4 * cnt}%" if bits & 2 else "")
        out = []
        for typ, flags, target, _ in new:
            if typ == 5:
                try: name = rg.weapon_name(target) or f"spec {target}"
                except Exception: name = f"spec {target}"   # noqa: BLE001
                out.append(f"HIT by {name}{dmg}")
            elif typ == 7: out.append(f"RADAR ID by {self.site_names.get(target, f'site record {target}')}")
            elif typ == 6: out.append(f"VISUAL ID by {rg.aircraft_spec(min(11, target))['name']}")
            elif typ == 4: out.append(f"weapon launched (station {target})")
        return out

    # ------------------------------------------------------------------------------------------------------ tick
    def _event(self, now, text, events):
        self.phase_log.append(f"{now:.0f} {text}"); events.append(Event(now, text))

    def tick(self, now=None):
        now = self.clock.now() if now is None else now
        m, ctx = self.modes, self.ctx
        try:
            state = self.reader.snapshot()
        except RuntimeError:
            state = None
        if state is None or state.speed_kt > 900 or (state.pos.x == 0 and state.pos.y == 0):
            # crashed / not in flight: memory is garbage, hands off; out for good after 10 s (a nav left waiting picked
            # up the next mission with this one's state and wrecked it, 2026-10-17); a save-state load just waits
            if not self.dead: self.release(); self.dead = now
            if now - self.dead > self.DEAD_EXIT_S:
                return TickResult(now, False, exit="nav: not in flight for 10 s (crashed or mission over) - exiting")
            self.say("  F-19 NAV: not in flight (crashed or between missions) - hands off; exits after 10 s.")
            self.sleep(1.0)
            return TickResult(now, False)
        self.dead = 0.0
        state = dataclasses.replace(state, time_mult=self.reads.time_mult())
        events = []
        self.controls.service(now)
        route = self.routes.current()
        if ctx.route is not route:                   # a new plan version (FlightDirector): FlyLeg joins it from its start
            ctx.route = route; ctx.leg = 1
        hdg_on = m.hdg
        dec = self.arb.step(state, ctx)
        dec = self._direct(state, dec, ctx)
        for e in dec.events: self._event(now, e, events)
        self.controls.apply(dec.command, state, ctx, now)
        for k in dec.key_ups: self.controls.up(k)
        if dec.handover or dec.exit: self.release(roll_level=hdg_on)
        if dec.exit:
            return TickResult(now, True, dec, exit=dec.exit, events=events)
        if self.loops.airborne(state):
            for e in self.combat_log(now): self._event(now, e, events)
        rep = self.loops.step(self.controls, state, dec.command, ctx, dec.phase, dec.takeoff_active, now)
        for e in rep.events: self._event(now, e, events)
        if rep.sens_tap: self.bus.publish(TelemetryFrame.sensitivity_row(now, rep.sens))
        snap = self.memlog.step() if self.memlog is not None else None
        tf = self._frame(now, state, dec, rep, route, snap, events)
        self.bus.publish(tf)
        if self.arb.fuel is not None: self.arb.fuel.key = ""
        if self.arb.attack is not None: self.arb.attack.key = ""
        self.sleep(self.PERIOD_S if self.memlog is None else self.PERIOD_MEMLOG_S)
        return TickResult(now, True, dec, rep, tf, events=events)

    def _direct(self, state, dec, ctx):
        """The route provider's on_tick hook (FlightDirector, WP-7c): it may hand back an Order (a Command to merge into
        the tick's, an exit text, log lines). StaticRoute has no hook. A provider error never stops the flight."""
        hook = getattr(self.routes, "on_tick", None)
        if hook is None: return dec
        try: order = hook(state, dec, ctx, self.event_list)
        except Exception as e:                       # noqa: BLE001
            dec.events.append(f"director error: {type(e).__name__}: {e}"); return dec
        if order is None: return dec
        dec.events.extend(order.events)
        if order.command is not None:
            dec.command = dec.command.merged(order.command); dec.behavior = "director"
        if order.exit: dec.exit = order.exit
        return dec

    # ----------------------------------------------------------------------------------------------- telemetry
    def _view(self, now, state, dec, rep, route):
        """nav's ui.frame dict (WP-8's HudFrame.view) and the banner."""
        fl, ctx, m = self.arb.fly_leg, self.ctx, self.modes
        W, leg, p = fl.wps, ctx.leg, state.xy
        b = W[leg][0]
        nxt = next((j for j in range(leg, len(W)) if W[j][1]), len(W) - 1)
        tahead = self.terrain(p, state.heading, state.alt_ft) if self.terrain is not None else None
        banner = PHASE_BANNER.get(dec.phase, "")
        if dec.command.banner: banner = (dec.command.banner + "\n" + banner).rstrip("\n")
        mw = self.arb.mwatch
        if mw is not None and mw.worst:
            banner = (f"  MISSILE ({mw.mode}): {mw.worst}" + (f"  ->  {mw.action}" if mw.action else "") + "\n" + banner).rstrip("\n")
        if m.auto and dec.phase == "handover" and self.arb.landing is not None:
            banner = "  GO-AROUND: landing aborted - autopilot OFF, you have control"
        if tahead is not None:
            top = "" if tahead[1] is None else f" (top {tahead[1]} ft)"
            banner = f"  !!! MOUNTAIN {tahead[0]:.1f} km AHEAD{top} - TURN AWAY, altimeter will NOT show it !!!" + ("\n" + banner if banner else "")
        la = fl.attrs(leg)
        if la.get("action"):
            banner = (banner + "\n" if banner else "") + f"  AT {W[leg][1]} ({dist(p, b) / K:.1f} km): {la['action']}"
        if la.get("probe"):
            banner = (banner + "\n" if banner else "") + f"  TEST: {la['probe']}" + (f"   SET SPEED {la['speed_kt']} kt" if la.get("speed_kt") else "")
        hk = self.loops.heading.key
        return dict(p=p, alt=state.alt_ft, kt=state.speed_kt, hdg=state.heading, phase=dec.phase, banner=banner,
                    hold_alt=rep.hold_alt, hdg_hold=m.hdg, wps=[(xy, name) for xy, name, _ in W], attrs=[a for _, _, a in W],
                    leg=leg, want=dec.want, turn=rep.turn, aim=fl.aim, to_turn_km=dist(p, b) / K,
                    xtrack_km=(fl.xtrack or 0.0) / K, next_name=W[nxt][1], next_wp=nxt,
                    gw=list(state.waypoints), sel=state.wp_index, ts=rep.ts, vs=rep.vs,
                    territory=self.side(*p) if self.side else None, rate=rep.rate, rate_cmd=rep.rate_cmd,
                    rolling=f"rolling {hk}" if hk else "", phase_log=list(self.phase_log),
                    sens_msg="" if rep.sens_ok else f"KYBD SENSITIVITY {rep.sens} -> setting 1",
                    route_left_km=(dist(p, b) + sum(dist(W[j][0], W[j + 1][0]) for j in range(leg, len(W) - 1))) / K)

    def _frame(self, now, state, dec, rep, route, snap, events):
        a, m, fl = self.arb, self.modes, self.arb.fly_leg
        sy = state.systems
        b = fl.wps[self.ctx.leg][0]
        mw = a.mwatch
        nav = dict(
            xtrack_km=(fl.xtrack or 0.0) / K, to_turn_km=dist(state.xy, b) / K, press=rep.press, rate=rep.rate,
            rate_cmd=rep.rate_cmd, roll=rep.roll_evt, tscale=rep.ts, side=self.side(*state.xy) if self.side else "",
            phase=dec.phase, vs=rep.vs if rep.hold_alt is not None else None, cycle_s=None,
            attack_state=a.attack.state if a.attack is not None else a.gun.state if a.gun is not None else "",
            flags=sy.flags if m.auto else None, throttle=sy.throttle if m.auto else None,
            attack_key=a.attack.key if a.attack is not None else "", locked=sy.locked if m.auto else None,
            fuel=sy.fuel_lb if m.auto else None, fuel_key=a.fuel.key if a.fuel is not None else "",
            escape=dec.overrides.escape or "", fighter=dec.overrides.fighter or "",
            fighter_near=a.fighter.nearest if a.fighter is not None else "",
            missile=dec.missiles, m_worst=mw.worst if mw else "",
            m_action=(mw.action + (" key " + dec.m_key if dec.m_key else "")).strip() if mw else "")
        nav["cycle_s"] = max(0.0, self.clock.now() - now)
        nav.update(self._view(now, state, dec, rep, route))
        if self.event_list_s is not None and now >= self._evl_next:
            self._evl_next = now + self.event_list_s
            try: self.event_list = tuple(self.reader.event_list(0)[1])
            except Exception: self.event_list = None       # noqa: BLE001
            nav["event_list"] = self.event_list
        core = Frame(state.t, state, dec.behavior, dec.command, plan_version=route.version, leg=self.ctx.leg,
                     events=tuple(e.text for e in events))
        return TelemetryFrame(now, core, nav=nav, route_heading=dec.route_want, behavior=dec.behavior,
                              plan_version=route.version, snapshot=snap, events=tuple(events))

    # ------------------------------------------------------------------------------------------------------- run
    def run(self, max_ticks=None, signals=True):
        """The main loop until an exit (returned as text) or SIGINT / SIGTERM (KeyboardInterrupt, re-raised after the
        release). Keys are always released on the way out."""
        old = {}
        if signals:
            def _stop(*_): raise KeyboardInterrupt
            for sig in (signal.SIGINT, signal.SIGTERM):
                try: old[sig] = signal.signal(sig, _stop)
                except ValueError: pass                  # not the main thread
        try:
            n = 0
            while max_ticks is None or n < max_ticks:
                r = self.tick(); n += 1
                if r.exit: self.say(r.exit); return r.exit
            return None
        finally:
            self.release()
            for sig, h in old.items(): signal.signal(sig, h)
