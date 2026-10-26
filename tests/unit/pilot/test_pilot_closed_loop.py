"""SMOKE TEST, NOT A FIDELITY CLAIM: the whole pilot stack (Runner -> Arbiter -> Controls -> Loops) closed around a
toy kinematic plane, to check that the loops converge on a route and an altitude, hold a speed, and that Controls never
leaves a key held. The toy's numbers only echo nav's tuning assumptions (a held roll key changes the turn rate by
~0.28 deg/s per second held and the rate persists; pitch taps change the vertical speed and it persists; a throttle
tap is 10 %); nothing here says how F-19 itself responds."""
import math

from pilot_equiv import KeyLog
from f19.cli import fly
from f19.core.clock import ManualClock
from f19.core.geometry import bearing_deg, wrap_deg
from f19.core.types import Attitude, GameState, Position, Route, RoutePoint, Systems
from f19.pilot.runner import Reads


class Toy:
    """x, y units; hdg deg; rate deg/s (persists); vs ft/s (persists, damped); spd kt toward 100 + 1.6 x throttle."""
    def __init__(self, clock):
        self.clock = clock
        self.x, self.y, self.hdg, self.rate, self.alt, self.vs, self.spd, self.thr = 10000.0, 20000.0, 90.0, 0.0, 600.0, 0.0, 200.0, 60
        self.held, self.t = {}, clock.now()

    def advance(self, dt):
        for k, t0 in self.held.items():
            if k in ("left", "right"): self.rate += (-1 if k == "left" else 1) * 0.28 * dt
        self.rate = max(-3.0, min(3.0, self.rate))
        self.hdg = (self.hdg + self.rate * dt) % 360
        self.vs *= 0.995 ** (dt / 0.05)
        self.alt = max(0.0, self.alt + self.vs * dt)
        self.spd += max(-2 * dt, min(2 * dt, 100 + 1.6 * self.thr - self.spd))
        v = self.spd * 0.788 * 1.852 / 3600 * 64 * 2.0 * dt                       # units at 2x game time
        self.x += v * math.sin(math.radians(self.hdg)); self.y -= v * math.cos(math.radians(self.hdg))

    def sync(self):
        now = self.clock.now()
        if now > self.t: self.advance(now - self.t); self.t = now

    def state(self, n=0):
        self.sync()
        bank = math.degrees(math.atan(self.rate * self.spd / 337.0))
        return GameState(t=None, pos=Position(self.x, self.y, int(self.alt)), att=Attitude(self.hdg, 0.0, bank),
                         speed_kt=int(self.spd), systems=Systems(flags=0x01, throttle=self.thr, fuel_lb=8000, loadout=((5, 2), (0, 4))), sensitivity=1)


class ToyEmu(KeyLog):
    def __init__(self, toy, clock): super().__init__(); self.toy, self.clock = toy, clock
    def tap(self, key, hold_secs=0.05):
        super().tap(key, hold_secs); self.toy.sync()
        if key in ("up", "down"): self.toy.vs += (12.0 if key == "down" else -12.0) * hold_secs
        self.clock.advance(hold_secs)
    def scan_tap(self, code, hold_secs=0.5, shift=False):
        super().scan_tap(code, hold_secs, shift); self.toy.sync()
        up = code == 0x0D
        self.toy.thr = (100 if up else 0) if shift else max(0, min(100, self.toy.thr + (10 if up else -10)))
        self.clock.advance(hold_secs)
    def key_down(self, key): super().key_down(key); self.toy.sync(); self.toy.held[key] = self.clock.now()
    def key_up(self, key): super().key_up(key); self.toy.sync(); self.toy.held.pop(key, None)


class ToyReader:
    def __init__(self, toy): self.toy, self.emu = toy, None
    def snapshot(self, threats=True): return self.toy.state()
    def event_list(self, start=0): return 0, []
    def offset(self, refresh=False): return 0


class ToyReads(Reads):
    def time_mult(self): return 1.0
    def events(self): return []
    def damage(self): return 0, 2


def test_closed_loop_smoke():
    clock = ManualClock(1_800_000_000.0)
    toy = Toy(clock)
    a = fly.resolve(fly.parser().parse_args(["--hdg", "--alt", "1000", "--missile-watch", "off"]))
    a.auto, a.dry_run = True, True
    route = Route((RoutePoint(10000.0, 20000.0, "start"), RoutePoint(13000.0, 20000.0, "east"),
                   RoutePoint(13000.0, 15000.0, "north"), RoutePoint(9000.0, 12000.0, "west")))
    emu = ToyEmu(toy, clock)
    r = fly.build(a, ToyReader(toy), emu, clock, toy.state(), sleep=clock.advance, wall=clock.now, say=lambda m: None, route=route)
    r.reads = r.arb.reads = ToyReads(None)
    errs, alts, spds, legs = [], [], [], set()
    for i in range(9000):
        res = r.tick()
        d = res.decision
        legs.add(r.ctx.leg)
        if i > 2000:
            errs.append(abs(wrap_deg(d.want - toy.hdg))); alts.append(toy.alt); spds.append(toy.spd)
    r.release(roll_level=False)
    errs.sort()
    assert legs >= {1, 2, 3}, legs                                  # the route's corners were flown
    assert errs[len(errs) // 2] < 8, errs[len(errs) // 2]           # heading: median error a few deg
    assert abs(sorted(alts)[len(alts) // 2] - 1000) < 120           # altitude: median near the hold
    assert abs(sorted(spds)[len(spds) // 2] - 190) < 25             # speed hold on the cruise speed
    assert not emu._held and not toy.held                           # no key left down
    assert bearing_deg((0, 0), (1, 0)) == 90.0
