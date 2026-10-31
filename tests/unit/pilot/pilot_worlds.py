"""Test worlds and a key adapter for the pilot behaviors (f19.pilot) on synthetic closed-loop worlds.

`world()` builds a synthetic game state, `gamestate()` turns it into the GameState a behavior sees, `FakeControls` is a TEST-ONLY
adapter that turns a Command into the key events Controls would send (the contract Controls and the loops are tested against), and
`apply_keys()` / `move()` are the game's side of those keys. `run_new()` steps one behavior on a sequence of worlds.

FakeControls, per tick:
  order: trigger release, pitch (band tap / one-shot tap / hold), actions in order, trigger press
  Action          -> a 0.2 s tap of its key, or a scancode tap (throttle, "/")
  Toggle          -> tap only if the flags bit disagrees and the key's last tap is >= settle_s ago (debounce per key on the tick clock)
  pitch_min/max   -> below: tap nose-up ("down") 0.6 s, above: nose-down ("up") 0.5 s; then none for 1.0 / ts s
  pitch_deg/tol   -> one tap toward it this tick, held clamp(|err| / 6.0 / ts, 0.08, 0.5) s, if |err| > tol
  pitch_hold      -> that pitch key held while set
  gun_fire        -> Backspace held while True; re-pressed when gun_repress or when the other held keys changed
  speed           -> THROTTLE_MAX / IDLE switch the speed hold off, then speed_kt (if set) is the new target
(Until 2026-10-30 this module, as pilot_worlds.py, also ran the OLD scripts/phases.py side by side; that oracle is retired.)
"""
import copy
import dataclasses
import math
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parents[2]
for _p in (TESTS, TESTS / "replay"):
    if str(_p) not in sys.path: sys.path.insert(0, str(_p))
import product  # noqa: E402

from f19.core.clock import ManualClock  # noqa: E402
from f19.core.types import Action, Command, FlightContext, GameState, Position, Attitude, Toggle  # noqa: E402

NOSE_UP, NOSE_DOWN = "down", "up"
TAP = 0.2
KEYS = {Action.GEAR_TOGGLE: "6", Action.FLAPS_TOGGLE: "9", Action.BRAKES_TOGGLE: "0", Action.BAY_TOGGLE: "8",
        Action.NEXT_WEAPON: "space", Action.FIRE: "enter", Action.TRACKCAM_MODE: "f2", Action.SELECT_TARGET: "b",
        Action.FLARE: "1", Action.CHAFF: "2", Action.DECOY: "5", Action.IR_JAMMER: "3", Action.ECM: "4",
        Action.CENTER_STICK: "kp5", Action.SENSITIVITY: "ins"}
SCANS = {Action.TRACKCAM_VIEW: (0x35, False), Action.THROTTLE_MAX: (0x0D, True), Action.THROTTLE_IDLE: (0x0C, True),
         Action.THROTTLE_UP: (0x0D, False), Action.THROTTLE_DOWN: (0x0C, False)}
# Command fields the old override dicts never had (the old phases pressed keys / mutated thr.target instead)
NEW_ONLY = {"speed_kt", "pitch_min", "pitch_max", "pitch_deg", "pitch_tol", "pitch_hold", "gun_fire", "gun_repress"}


def r6(x): return round(float(x), 6)


# ------------------------------------------------------------------------------------------------------ key recording
class KeyLog:
    """Key recording + held-key tracking, as f19.emulator.Emulator's key API (tap leaves _held unchanged)."""
    def __init__(self): self.keys, self._held = [], set()
    def tap(self, key, hold_secs=0.05): self.keys.append(("tap", key, r6(hold_secs)))
    def scan_tap(self, code, hold_secs=0.5, shift=False): self.keys.append(("scan", code, shift, r6(hold_secs)))
    def key_down(self, key): self._held.add(key); self.keys.append(("down", key))
    def key_up(self, key): self._held.discard(key); self.keys.append(("up", key))
    def take(self): k, self.keys = self.keys, []; return k


# ------------------------------------------------------------------------------------------------------ new side
class FakeControls:
    """Command -> the key events the old code sent (module docstring). One per behavior run."""
    def __init__(self, ctx):
        self.ctx, self.keys, self.held, self.band_next, self.held_other, self.speed = ctx, [], set(), -math.inf, set(), None
    def _tap(self, key, hold=TAP): self.keys.append(("tap", key, r6(hold)))
    def _down(self, k): self.held.add(k); self.keys.append(("down", k))
    def _up(self, k): self.held.discard(k); self.keys.append(("up", k))
    def _action(self, a, state, now):
        if isinstance(a, Toggle):
            if a.satisfied(state.systems.flags): return
            last = self.ctx.toggled.get(a.action)
            if last is not None and now - last < a.settle_s: return
            self._tap(KEYS[a.action]); self.ctx.toggled[a.action] = now; return
        if a in SCANS:
            code, shift = SCANS[a]; self.keys.append(("scan", code, shift, r6(TAP)))
            if a in (Action.THROTTLE_MAX, Action.THROTTLE_IDLE): self.speed = None
            return
        self._tap(KEYS[a])
    def apply(self, cmd, state, now):
        ts, pitch = state.ts, state.att.pitch
        if cmd.gun_fire is not True and "bs" in self.held: self._up("bs")
        if cmd.pitch_min is not None and now >= self.band_next:
            if pitch < cmd.pitch_min: self._tap(NOSE_UP, 0.6); self.band_next = now + 1.0 / ts
            elif pitch > cmd.pitch_max: self._tap(NOSE_DOWN, 0.5); self.band_next = now + 1.0 / ts
        if cmd.pitch_deg is not None:
            err = cmd.pitch_deg - pitch
            if abs(err) > cmd.pitch_tol:
                self._tap(NOSE_UP if err > 0 else NOSE_DOWN, max(0.08, min(0.5, abs(err) / 6.0 / ts)))
        for k, key in (("up", NOSE_UP), ("down", NOSE_DOWN)):
            if cmd.pitch_hold == k and key not in self.held: self._down(key)
            elif cmd.pitch_hold != k and key in self.held: self._up(key)
        for a in cmd.actions: self._action(a, state, now)
        if cmd.speed_kt is not None: self.speed = cmd.speed_kt
        if cmd.gun_fire is True:
            if "bs" not in self.held: self._down("bs")
            else:
                other = self.held - {"bs"}
                if other != self.held_other or cmd.gun_repress: self._up("bs"); self._down("bs")
                self.held_other = other
        k, self.keys = self.keys, []
        return k


def new_ov(cmd, strip=NEW_ONLY):
    return {k: v for k, v in cmd.to_overrides().items() if k not in strip}


# ------------------------------------------------------------------------------------------------------ worlds
def gamestate(w, **kw):
    """GameState of a synthetic world (the same dicts the old readers return)."""
    return GameState.from_dicts(w["s"], w["f"], trackcam=w.get("trackcam"), time_mult=w.get("ts", 1.0),
                                events=w.get("events"), projectiles=w.get("projectiles"), targets=w.get("targets"),
                                gun=w.get("gun"), **kw)


def projectile(spec=0, ttl=0):
    b = bytearray(0x18); b[0x10:0x12] = spec.to_bytes(2, "little"); b[0xE:0x10] = ttl.to_bytes(2, "little"); return bytes(b)


def world(x=0.0, y=0.0, alt=0, spd=0, hdg=0.0, pitch=0.0, roll=0.0, flags=0, throttle=0, station=0, loadout=None,
          locked=0xFFFF, opp=2, box=0, hud_lock=0, fuel=8000, ts=1.0, **extra):
    """A synthetic world: s / f as f19_state.read_state / read_flight return them (x, y in UNITS here; stored raw)."""
    w = dict(s=dict(alt=alt, spd=spd, hdg=hdg, x=int(round(x * 32)), y=int(round(y * 32))),
             f=dict(pitch=pitch, roll=roll, flags=flags, locked=locked, opp=opp, box=box, throttle=throttle, station=station,
                    loadout=list(loadout or [(16, 1), (17, 1), (5, 2), (0, 4)]), hud_lock=hud_lock, fuel=fuel),
             ts=ts, events=[], trackcam=2, projectiles=[projectile() for _ in range(4)], targets={}, gun=(650, 4, 20))
    w.update(extra); return w


def pos(w): return (w["s"]["x"] / 32, w["s"]["y"] / 32)


def move(w, dt, ratio=2.5):
    """Fly straight on at the world's heading and speed for dt real s (ground speed 0.788 x kt at `ratio` game s / s)."""
    v = w["s"]["spd"] * 0.788 * 1.852 / 3600 * 64 * ratio * dt
    h = math.radians(w["s"]["hdg"])
    x, y = pos(w)
    w["s"]["x"], w["s"]["y"] = int(round((x + v * math.sin(h)) * 32)), int(round((y - v * math.cos(h)) * 32))


def apply_keys(w, keys, lose=False):
    """The game's side of the keys (synthetic): toggles flip their flag bit (unless lose), throttle scancodes, Space
    cycles the station, F2 the trackcam mode, pitch taps change the pitch (6 deg/s held)."""
    f = w["f"]
    for k in keys:
        if k[0] == "tap" and k[1] in ("6", "8", "9", "0") and not lose:
            f["flags"] ^= {"6": 0x01, "9": 0x02, "8": 0x04, "0": 0x08}[k[1]]
        elif k[0] == "scan" and k[1] == 0x0D: f["throttle"] = 100 if k[2] else min(100, f["throttle"] + 10)
        elif k[0] == "scan" and k[1] == 0x0C: f["throttle"] = 0 if k[2] else max(0, f["throttle"] - 10)
        elif k[0] == "tap" and k[1] == "space": f["station"] = (f["station"] + 1) % 4
        elif k[0] == "tap" and k[1] == "f2": w["trackcam"] = (w["trackcam"] + 1) % 3
        elif k[0] == "tap" and k[1] in (NOSE_UP, NOSE_DOWN):
            f["pitch"] += (1 if k[1] == NOSE_UP else -1) * 6.0 * k[2]


# ------------------------------------------------------------------------------------------------------ runner
@dataclasses.dataclass
class Report:
    ticks: int = 0
    n_keys: int = 0
    states: list = dataclasses.field(default_factory=list)      # the behavior's .state, each change once
    keys: list = dataclasses.field(default_factory=list)        # every key event, in order
    log: list = dataclasses.field(default_factory=list)         # every Command.events line


def run_new(new, ticks, after=None, speed_cell=None):
    """Step the behavior `new` on the (t, world) pairs of `ticks`; keys through FakeControls. after(w, keys, t, ov): the closed loop,
    the world's response to the tick's keys and overrides (new_ov). speed_cell: any object whose .target is kept equal to the speed
    hold's target before `after` runs (the world may fly toward it). Stops at the first Command with done."""
    rep = Report()
    ctx = FlightContext(ManualClock(0.0)); ctrl = FakeControls(ctx)
    for t, w in ticks:
        ctx.clock.set(t)
        st = gamestate(w); ctx.speed_target = ctrl.speed
        cmd = new.step(st, ctx)
        keys = ctrl.apply(cmd, st, t)
        ov = new_ov(cmd)
        rep.ticks += 1; rep.n_keys += len(keys); rep.keys += keys; rep.log += list(cmd.events)
        if not rep.states or rep.states[-1] != getattr(new, "state", ""): rep.states.append(getattr(new, "state", ""))
        if speed_cell is not None: speed_cell.target = ctrl.speed
        if after: after(w, keys, t, ov)
        if ov.get("done"): break
    return rep


def run_react(new, inputs):
    """Reactions: step `new` on (t, state, route_want, _) inputs; -> Report with states, log (event texts), ovs (Command.to_overrides)."""
    rep = Report(); rep.ovs = []
    ctx = FlightContext(ManualClock(0.0))
    for t, st, want, _ in inputs:
        ctx.clock.set(t); ctx.want = want
        cmd = new.step(st, ctx)
        rep.ticks += 1; rep.log += list(cmd.events); rep.ovs.append(cmd.to_overrides())
        if not rep.states or rep.states[-1] != new.state: rep.states.append(new.state)
    return rep


def deep(x): return copy.deepcopy(x)


def flat_state(alt, spd, hdg, x, y, **kw):
    """A GameState from nav-CSV style inputs (units)."""
    return GameState(t=None, pos=Position(x, y, alt), att=Attitude(hdg), speed_kt=spd, **kw)
