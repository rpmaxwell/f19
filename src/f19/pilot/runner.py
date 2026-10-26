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
  StaticRoute           the route provider until WP-7c's FlightDirector: one route.json, a replan hook that does nothing

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

from f19.core.geometry import bearing_deg as bearing, dist, game_dist_units, wrap_deg as wrap
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
