"""Equivalence harness for WP-7b: the OLD behaviors (scripts/phases.py, scripts/missile_watch.py, nav's route code)
and the NEW ones (f19.pilot) side by side on the same inputs.

OLD side: a fake `db` (OldDB) records every tap / scan_tap / key_down / key_up (with hold times) and answers the reads
phases.py does inside step() (read_events, read_trackcam, read_projectiles, read_target, read_gun) from the same
world the new side gets as a GameState; phases' `time` module is replaced by the tick clock (set_toggle's settle and
GunAttack's pitch spacing read time.time(): the replay harness does the same).

NEW side: FakeControls is a TEST-ONLY adapter that turns a Command into the key events the old code would have sent
given the same state. It is the contract WP-7a's loops / Controls must meet to reproduce the old key timing:
  order in a tick: trigger release, pitch (band tap / one-shot tap / hold), actions in order, trigger press
  Action          -> a 0.2 s tap (phases.TAP) of its key, or a scancode tap (throttle, "/")
  Toggle          -> phases.set_toggle: tap only if the flags bit disagrees and the key's last tap is >= settle_s ago
                     (debounce per key, shared, on the tick clock; written to ctx.toggled)
  pitch_min/max   -> below: tap nose-up ("down") 0.6 s, above: nose-down ("up") 0.5 s; then none for 1.0 / ts s
  pitch_deg/tol   -> one tap toward it this tick, held clamp(|err| / 6.0 / ts, 0.08, 0.5) s, if |err| > tol
  pitch_hold      -> that pitch key held while set (down on the first tick, up on the first tick without it)
  gun_fire        -> Backspace held while True; re-pressed (up, down) when gun_repress or when the other held keys
                     changed since the last tick it was held (the old db._held rule)
  speed           -> THROTTLE_MAX / IDLE switch the speed hold off, then speed_kt (if set) is the new target
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


# ------------------------------------------------------------------------------------------------------ old side
class KeyLog:
    """Key recording + held-key tracking, as f19.emulator.Emulator's key API (tap leaves _held unchanged)."""
    def __init__(self): self.keys, self._held = [], set()
    def tap(self, key, hold_secs=0.05): self.keys.append(("tap", key, r6(hold_secs)))
    def scan_tap(self, code, hold_secs=0.5, shift=False): self.keys.append(("scan", code, shift, r6(hold_secs)))
    def key_down(self, key): self._held.add(key); self.keys.append(("down", key))
    def key_up(self, key): self._held.discard(key); self.keys.append(("up", key))
    def take(self): k, self.keys = self.keys, []; return k


class OldDB(KeyLog):
    """The old phases' connection: keys recorded, reads answered from `self.w` (a synthetic world, see World)."""
    def __init__(self): super().__init__(); self.w = None


class OldTime:
    def __init__(self): self.t = 0.0
    def time(self): return self.t
    def sleep(self, s): pass


class OldPhases:
    """Context manager: phases' time -> OldTime, its in-step readers -> the OldDB world (synthetic runs only)."""
    def __init__(self, synthetic=True): self.syn, self.clock = synthetic, OldTime()
    def __enter__(self):
        ph, fs = product.phases(), product.f19_state()
        self.saved = {k: getattr(ph, k) for k in ("time", "read_events", "read_trackcam", "read_projectiles", "read_target")}
        self.saved_fs = {k: getattr(fs, k) for k in ("read_gun", "read_target")}
        ph.time = self.clock; ph._toggled.clear()
        if self.syn:
            ph.read_events = lambda db, n=200: list(db.w["events"])
            ph.read_trackcam = lambda db: db.w["trackcam"]
            ph.read_projectiles = lambda db: list(db.w["projectiles"])
            ph.read_target = fs.read_target = lambda db, i: db.w["targets"][i]
            fs.read_gun = lambda db: db.w["gun"]
        return self
    def __exit__(self, *a):
        ph, fs = product.phases(), product.f19_state()
        for k, v in self.saved.items(): setattr(ph, k, v)
        for k, v in self.saved_fs.items(): setattr(fs, k, v)
        ph._toggled.clear()


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


# ------------------------------------------------------------------------------------------------------ runners
@dataclasses.dataclass
class Report:
    ticks: int = 0
    keys_equal: int = 0
    ov_equal: int = 0
    speed_equal: int = 0
    events_equal: int = 0
    extra_equal: int = 0
    first_diff: str = ""
    n_keys: int = 0
    order_only: list = dataclasses.field(default_factory=list)   # ticks whose keys differ only in their order
    states: list = dataclasses.field(default_factory=list)
    keys: list = dataclasses.field(default_factory=list)         # every (old) key event

    @property
    def all_equal(self):
        n = self.ticks
        return n > 0 and self.keys_equal == self.ov_equal == self.speed_equal == self.events_equal == self.extra_equal == n

    def note(self, ok, what):
        if not ok and not self.first_diff: self.first_diff = f"tick {self.ticks}: {what}"


def run_phase(old, new, ticks, old_step, extra=None, synthetic=True, after=None, allow_order=False):
    """Step OLD and NEW phases on the same ticks; compare keys, overrides, the speed target, log lines.
    ticks: iterable of (t, w-or-db-loader) yielding (t, w) for synthetic worlds (a generator may read `last_keys`).
    old_step(old, db, w, t) -> ov.  extra(behavior) -> a comparable tuple (state, counters, ...); .key is added.
    allow_order: count a tick whose keys differ only in order as equal (listed in Report.order_only).
    after(w, keys, t, ov): closed loop - the world's response to the (old) keys and overrides of the tick."""
    rep = Report()
    ctx = FlightContext(ManualClock(0.0)); ctrl = FakeControls(ctx)
    db = OldDB()
    thr = getattr(old, "thr", None)
    ctrl.speed = thr.target if thr is not None else None
    with OldPhases(synthetic) as op:
        for t, w in ticks:
            op.clock.t = t; ctx.clock.set(t); db.w = w
            n_log = len(getattr(old, "log", []))
            ov = old_step(old, db, w, t)
            okeys = db.take()
            ev_old = list(getattr(old, "log", [])[n_log:])
            xo = (extra(old) if extra else ()) + ((old.key,) if hasattr(old, "key") else ())
            if hasattr(old, "key"): old.key = ""                     # nav cleared it after logging each cycle
            st = gamestate(w); ctx.speed_target = ctrl.speed
            cmd = new.step(st, ctx)
            nkeys = ctrl.apply(cmd, st, t)
            rep.ticks += 1; rep.n_keys += len(okeys); rep.keys += okeys
            k_ok = okeys == nkeys
            if not k_ok and sorted(okeys, key=repr) == sorted(nkeys, key=repr): rep.order_only.append((okeys, nkeys)); k_ok = allow_order
            rep.keys_equal += k_ok; rep.note(k_ok, f"keys old {okeys} new {nkeys}")
            o_ok = ov == new_ov(cmd); rep.ov_equal += o_ok; rep.note(o_ok, f"ov old {ov} new {new_ov(cmd)}")
            s_ok = (thr.target if thr is not None else None) == ctrl.speed or thr is None
            rep.speed_equal += s_ok; rep.note(s_ok, f"speed old {thr.target if thr else None} new {ctrl.speed}")
            e_ok = ev_old == list(cmd.events); rep.events_equal += e_ok; rep.note(e_ok, f"log old {ev_old} new {cmd.events}")
            xn = (extra(new) if extra else ()) + ((new.key,) if hasattr(new, "key") else ())
            x_ok = xo == xn; rep.extra_equal += x_ok; rep.note(x_ok, f"extra old {xo} new {xn}")
            if not rep.states or rep.states[-1] != getattr(new, "state", ""): rep.states.append(getattr(new, "state", ""))
            if after: after(w, okeys, t, ov)
            if ov.get("done"): break
    return rep


def run_reaction(old_call, new, inputs):
    """Reactions: old_call(t, item) -> (ov, events); the NEW Command from (state, ctx). inputs: (t, state, want, item)."""
    rep = Report()
    ctx = FlightContext(ManualClock(0.0))
    for t, st, want, item in inputs:
        ctx.clock.set(t); ctx.want = want
        ov, ev = old_call(t, item)
        cmd = new.step(st, ctx)
        rep.ticks += 1
        o_ok = ov == cmd.to_overrides(); rep.ov_equal += o_ok; rep.note(o_ok, f"ov old {ov} new {cmd.to_overrides()}")
        e_ok = list(ev) == list(cmd.events); rep.events_equal += e_ok; rep.note(e_ok, f"events old {ev} new {cmd.events}")
        rep.keys_equal += 1; rep.speed_equal += 1; rep.extra_equal += 1
        if not rep.states or rep.states[-1] != new.state: rep.states.append(new.state)
    return rep


def deep(x): return copy.deepcopy(x)


def flat_state(alt, spd, hdg, x, y, **kw):
    """A GameState from nav-CSV style inputs (units)."""
    return GameState(t=None, pos=Position(x, y, alt), att=Attitude(hdg), speed_kt=spd, **kw)
