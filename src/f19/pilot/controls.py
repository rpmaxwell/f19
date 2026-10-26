"""Controls: the ONLY key sender of the pilot (refactor plan 4.1, WP-7a). Behaviors return Commands, the loops ask for
keys through the methods here, and nothing else touches the emulator's key API.

One key table (KEYS / SCANS below; nav.py, scripts/phases.py and missile_watch.KEYS):
  stick          NOSE_UP = "down", NOSE_DOWN = "up" (arrow keys), roll "left" / "right", centre "kp5"
  toggles        gear "6", flaps "9", bay "8", brakes "0", IR jammer "3", radar jammer "4"
  weapons        Space next weapon, Enter fire, Backspace gun ("bs", held), F2 trackcam mode, B select target
  CMs            flare "1", chaff "2", decoy "5"
  throttle       set-1 scancodes through the 8042 (Emulator.scan_tap): MINUS 0x0C, EQUALS 0x0D; Shift+= max, Shift+- idle
  trackcam view  "/" scancode 0x35;  keyboard sensitivity Ins

apply(command, state, ctx) is the WP-7b contract (tests/unit/pilot/pilot_equiv.FakeControls is the reference):
  order in a tick: trigger release, pitch (band tap / one-shot tap / hold), actions in order, trigger press
  Action        a 0.2 s tap (TAP) of its key, or a scancode tap (throttle, "/"); THROTTLE_MAX / IDLE switch the speed
                hold off (target None), as Throttle.max / idle did
  Toggle        phases.set_toggle: tap only if the flags bit disagrees and that key's last tap is >= settle_s ago (the
                debounce table is ctx.toggled, shared by every behavior and loop, on the tick clock)
  pitch_min/max below the band: nose-up 0.6 s, above: nose-down 0.5 s; then no band tap for 1.0 / ts s
  pitch_deg/tol one tap toward pitch_deg this tick, held clamp(|err| / 6.0 / ts, 0.08, 0.5) s, if |err| > pitch_tol
  pitch_hold    that pitch key held while set (down the first tick, up the first tick without it)
  gun_fire      Backspace held while True; re-pressed (up, down) on gun_repress or when the set of other keys this
                Controls holds changed since the last tick it was held (the old db._held rule: roll keys count)
  speed_kt      the speed hold's new target (after the actions)

Key timing. Taps (<= 0.6 s) and scancode taps BLOCK for their hold time, exactly as nav and phases.py did (the
emulator's tap / scan_tap sleep): the game needs a minimum press, and a release scheduled for a later tick would
stretch every 0.2 s tap to the tick length (0.15-0.3 s with the memlog) - a timing change. Long holds never block:
the roll keys (HeadingHold), the pitch hold and the trigger are key_down now and key_up on a later tick, as nav did;
hold(key, until) / service(now) give the loops a scheduled release. So the emitted press / release sequence and every
duration match FakeControls and nav.

Releases: release_all() (exit, exception, signals, hand-over: every arrow key + Backspace up even if this client
doesn't think they are down, plus anything held; then keypad 5 centres the stick), after_load() (a save-state load:
release_all plus Emulator.release_arrows, the arrow BREAK codes straight to the guest) and `with Controls(...)`.
"""
import math

from f19.core.types import Action, Toggle

NOSE_UP, NOSE_DOWN = "down", "up"
ROLL_LEFT, ROLL_RIGHT = "left", "right"
STICK = ("left", "right", "up", "down")
TRIGGER = "bs"
CENTER = "kp5"
TAP = 0.2                                      # phases.TAP: a longer press auto-repeats
MINUS, EQUALS, SLASH = 0x0C, 0x0D, 0x35        # set-1 scancodes (phases.py)
F10 = 0x44                                     # set-1 scancode; Shift+F10 ejects (the user, 2026-10-26)

KEYS = {Action.GEAR_TOGGLE: "6", Action.FLAPS_TOGGLE: "9", Action.BRAKES_TOGGLE: "0", Action.BAY_TOGGLE: "8",
        Action.NEXT_WEAPON: "space", Action.FIRE: "enter", Action.GUN: TRIGGER, Action.TRACKCAM_MODE: "f2",
        Action.SELECT_TARGET: "b", Action.FLARE: "1", Action.CHAFF: "2", Action.DECOY: "5", Action.IR_JAMMER: "3",
        Action.ECM: "4", Action.CENTER_STICK: CENTER, Action.SENSITIVITY: "ins"}
SCANS = {Action.TRACKCAM_VIEW: (SLASH, False), Action.THROTTLE_MAX: (EQUALS, True), Action.THROTTLE_IDLE: (MINUS, True),
         Action.THROTTLE_UP: (EQUALS, False), Action.THROTTLE_DOWN: (MINUS, False),
         Action.EJECT: (F10, True)}              # same shifted-scancode path as Shift+= (max power); NOT yet tried live
# Actions with no key yet (DESIGNATE): apply() raises rather than guessing a key.


class SpeedTarget:
    """The speed hold's target when no SpeedHold (loops) is attached: just the value."""
    def __init__(self, target=None): self.target = target


class Controls:
    """Controls(emulator, speed=None). emulator: f19.emulator.Emulator or anything with tap / scan_tap / key_down /
    key_up (tests: a recording fake). speed: the SpeedHold whose .target the throttle actions and speed_kt set."""

    def __init__(self, emulator, speed=None):
        self.emu = emulator
        self.speed = speed if speed is not None else SpeedTarget()
        self.held = set()                      # keys this Controls holds down
        self.scheduled = {}                    # key -> release time (hold / service)
        self.band_next = -math.inf             # pitch band: no band tap before this (tick clock)
        self.held_other = set()                # the other held keys the last tick the trigger was held

    # ------------------------------------------------------------------------------------------------- primitives
    def tap(self, key, hold=TAP):
        """Press and release (blocks hold s, as Emulator.tap)."""
        self.emu.tap(key, hold)

    def scan(self, code, shift=False, hold=TAP):
        """A set-1 scancode tap (blocks, as Emulator.scan_tap)."""
        self.emu.scan_tap(code, hold, shift=shift)

    def down(self, key):
        self.held.add(key); self.emu.key_down(key)

    def up(self, key):
        """Release: always sent, also for a key this client doesn't think is down (the stuck-key watchdog)."""
        self.held.discard(key); self.scheduled.pop(key, None); self.emu.key_up(key)

    def hold(self, key, until):
        """Key down now, released by service() at the first tick at or after `until` (tick clock)."""
        self.down(key); self.scheduled[key] = until

    def service(self, now):
        """Release the scheduled holds that are due. -> the keys released."""
        due = [k for k, t in self.scheduled.items() if now >= t]
        for k in due: self.up(k)
        return due

    @property
    def speed_target(self): return self.speed.target

    # ---------------------------------------------------------------------------------------------------- toggles
    def toggle(self, t: Toggle, state, now, toggled):
        """phases.set_toggle: tap t's key if the flags bit disagrees and its last tap is >= settle_s ago.
        -> True if the flag isn't as wanted yet (pressed now or still settling), False if it is."""
        if t.satisfied(state.systems.flags): return False
        last = toggled.get(t.action)
        if last is not None and now - last < t.settle_s: return True
        self.tap(KEYS[t.action]); toggled[t.action] = now
        return True

    def action(self, a, state, now, toggled):
        if isinstance(a, Toggle): self.toggle(a, state, now, toggled); return
        if a in SCANS:
            code, shift = SCANS[a]; self.scan(code, shift)
            if a in (Action.THROTTLE_MAX, Action.THROTTLE_IDLE): self.speed.target = None
            return
        if a not in KEYS: raise ValueError(f"Controls: no key for {a}")
        self.tap(KEYS[a])

    # ------------------------------------------------------------------------------------------------------ apply
    def apply(self, command, state, ctx, now=None):
        """One tick of a behavior's Command (module docstring). now: the tick time (default ctx.now())."""
        now = ctx.now() if now is None else now
        cmd, ts, pitch = command, state.ts, state.att.pitch
        if cmd.gun_fire is not True and TRIGGER in self.held: self.up(TRIGGER)
        if cmd.pitch_min is not None and now >= self.band_next:
            if pitch < cmd.pitch_min: self.tap(NOSE_UP, 0.6); self.band_next = now + 1.0 / ts
            elif pitch > cmd.pitch_max: self.tap(NOSE_DOWN, 0.5); self.band_next = now + 1.0 / ts
        if cmd.pitch_deg is not None:
            err = cmd.pitch_deg - pitch
            if abs(err) > cmd.pitch_tol:
                self.tap(NOSE_UP if err > 0 else NOSE_DOWN, max(0.08, min(0.5, abs(err) / 6.0 / ts)))
        for k, key in (("up", NOSE_UP), ("down", NOSE_DOWN)):
            if cmd.pitch_hold == k and key not in self.held: self.down(key)
            elif cmd.pitch_hold != k and key in self.held: self.up(key)
        for a in cmd.actions: self.action(a, state, now, ctx.toggled)
        if cmd.speed_kt is not None: self.speed.target = cmd.speed_kt
        if cmd.gun_fire is True:
            if TRIGGER not in self.held: self.down(TRIGGER)
            else:
                other = self.held - {TRIGGER}
                if other != self.held_other or cmd.gun_repress: self.up(TRIGGER); self.down(TRIGGER)
                self.held_other = other

    # --------------------------------------------------------------------------------------------------- releases
    def release_all(self, center=True):
        """Every arrow key and the trigger up (sent even if not held: nav's _release), anything else held up, then
        keypad 5 (centre the stick: pitch and bank deflection persist). Never raises (exit paths)."""
        for k in list(dict.fromkeys(STICK + (TRIGGER,) + tuple(self.held))):
            try: self.up(k)
            except Exception: pass                      # noqa: BLE001 - a dead connection must not mask the exit
        self.held.clear(); self.scheduled.clear()
        if center:
            try: self.tap(CENTER, TAP)
            except Exception: pass                      # noqa: BLE001

    def after_load(self):
        """After a save-state load (nav.settle): a key left down survives loads, and a state saved while a key was
        down replays its press. Release everything, send the arrow BREAK codes to the guest, centre the stick."""
        self.release_all(center=False)
        rel = getattr(self.emu, "release_arrows", None)
        if rel is not None: rel()
        self.tap(CENTER, TAP)

    def __enter__(self): return self

    def __exit__(self, *exc):
        self.release_all()
        return False
