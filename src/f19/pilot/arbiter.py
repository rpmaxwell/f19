"""The Arbiter (WP-7a): which behavior flies this tick, and the one merged Command - scripts/nav.py's main-loop merge
(:495-642) as explicit layers, lowest priority first (a later layer wins field by field, as nav's {**ov, **other}):

  0  fly_leg         FlyLeg: the route's heading / rate cap / leg altitude; nav's own takeoff phase (runway heading)
  5  fuel_transfer   FuelTransfer: Space / Enter only (never steers); while busy it holds the attack off the station keys
  10 takeoff         Takeoff, from the runway (with --auto) until gear and flaps are up - even past the climb-out
  20 attack          Attack on the route's attack points (--auto); on hold (heading kept, bay shut) while the fuel
                     transfer is busy or MissileWatch is evading in act mode (nav's m_hold)
  20 gun_attack      GunAttack on the --strafe queue
  30 landing         Landing from the leg to Final (--auto) or --land-now; done -> hand over (keys released, holds off)
  40 fighter         FighterWatch (route / attack / strafe above 100 ft, heading hold on)
  50 missile         MissileWatch + CmKeys: only want / rate_cap / bank_max / alt_min / missile reach the merge; its
                     countermeasure / jammer requests become CmKeys taps (Actions)
  60 terrain_escape  TerrainEscape: the last word
The phases REPLACE each other (nav assigned ov = phase.step(...)); the reactions MERGE on top. Each reaction's
ctx.want is the heading the layers below it asked for this tick (nav passed `want`, updated after every layer).

Speed. Phases set the speed hold's target for good (Command.speed_kt, THROTTLE_MAX / IDLE; an attack's end sets the
cruise speed). A reaction's speed is temporary: ONE saved-speed mechanism replaces nav's fighter_saved_kt /
missile_saved_kt / esc_saved_kt. While any reaction asks for a speed (FighterWatch / MissileWatch evade: their speed;
MissileWatch survival and a TerrainEscape climb below 170 kt: full power), the highest-priority request wins; the
target at the start of that episode is saved, and restored when no reaction asks any more (the cruise speed if the
saved target was None and the episode went to full power: nav's escape fallback). Fighter / missile requests wait
while the throttle guard is recovering the speed (nav: `not thr.guarding`); the escape's full power doesn't.
Known differences from nav's three variables (overlapping reactions only; tests/unit/pilot/test_pilot_arbiter.py):
  - a MissileWatch survival under a FighterWatch evasion: nav's fighter set the target back to 165 every tick, so the
    survival pressed Shift+= again every tick; here the top request (full power) wins: one press, the same target.
  - a fighter evasion that ends while a survival goes on: nav restored the fighter's saved speed and the survival
    then saved THAT and maxed again, so the evasion speed (165) could stick after both ended; here the target from
    before the first reaction comes back.
  - an evasion under a TerrainEscape climb: nav's escape pressed full power once (esc_full) and the evasion set its
    165 kt target again on the next tick, so the speed hold throttled back during the climb; here the escape's full
    power wins until the escape ends.

Memory reads the behaviors need (events, projectiles, a target record, gun state, the missile world) come from
`reads` at the point nav read them (Runner's Reads over f19.memory; tests: snapshots or synthetic worlds).
"""
import dataclasses
from dataclasses import dataclass, field

from f19.core.types import BAY, Action, Command, Toggle
from f19.pilot.phases import Attack, GunAttack, Landing, Takeoff
from f19.pilot.reactions.missile import KEYS as CM_KEYS

PRIORITY = {"fly_leg": 0, "fuel_transfer": 5, "takeoff": 10, "attack": 20, "gun_attack": 20, "landing": 30,
            "fighter": 40, "missile": 50, "terrain_escape": 60}
MISSILE_FIELDS = ("heading", "rate_cap", "bank_max", "alt_min", "missile")   # what nav took from MissileWatch's dict
FLYING = ("route", "attack", "strafe")
MAX = "max"


@dataclass
class Decision:
    command: Command                       # the merged Command (route base + phase + reactions); speed resolved
    overrides: Command                     # nav's `ov`: the phase + reaction layers without the route base
    want: float                            # the final heading
    route_want: float                      # the heading before the reactions (route / phase)
    phase: str                             # nav's phase: takeoff, route, attack, strafe, landing, landed, handover
    behavior: str                          # the top layer that acted this tick
    events: list = field(default_factory=list)   # mission-log texts (no time prefix)
    key_ups: tuple = ()                    # keys to release after apply (nav's gun.abort)
    handover: bool = False                 # nav's _release + holds off (landing done / route end without --auto)
    exit: str | None = None                # nav's sys.exit message (strafe done without --auto)
    m_key: str = ""
    missiles: int | None = None            # missiles in the world read (CSV "missile")
    takeoff_active: bool = False


class SpeedEpisodes:
    """The one saved-speed mechanism (module docstring)."""
    NONE = object()

    def __init__(self): self.saved, self.maxed, self.esc_full = self.NONE, False, False

    @property
    def active(self): return self.saved is not self.NONE


class Arbiter:
    """Arbiter(fly_leg, modes, ...): see the module docstring. step(state, ctx) -> Decision.

    modes: loops.Modes (auto, strafe, hdg, alt). speed: the speed hold (target, guarding). attacks: FlyLeg.attacks()
    ({role: dict(mode, T, record, launch_km, photo_km)}). strafe: target records for GunAttack. fuel / fighter /
    escape: reaction instances or None. missile_mode: nav --missile-watch (None = act at Elite, observe below; "off");
    missile_factory(mode) -> MissileWatch; cmkeys: CmKeys. reads: memory access for the behaviors (see Reads)."""

    def __init__(self, fly_leg, modes, *, speed, reads=None, cruise_kt=190.0, cruise_ft=450.0, attacks=None,
                 attack_now=None, land_now=False, done=(), strafe=(), fuel=None, fighter=None, missile_mode=None,
                 missile_factory=None, cmkeys=None, escape=None):
        self.fly_leg, self.modes, self.speed, self.reads = fly_leg, modes, speed, reads
        self.cruise_kt, self.cruise_ft = cruise_kt, cruise_ft
        self.attacks, self.attack_now, self.land_now = attacks or {}, attack_now, land_now
        self.attacks_done = set(done)
        self.strafe_q = list(strafe)
        self.fuel, self.fighter, self.escape = fuel, fighter, escape
        self.missile_mode, self.missile_factory, self.cmkeys = missile_mode, missile_factory, cmkeys
        self.mwatch, self.missile_full = None, False
        self.tko = self.attack = self.gun = self.landing = None
        self.phase = "takeoff"
        self.started = False
        self.sp = SpeedEpisodes()
        self.msnap = None

    @property
    def m_hold(self):
        mw = self.mwatch
        return mw is not None and mw.mode == "act" and mw.state in ("evade", "survival")

    @property
    def takeoff_active(self): return self.tko is not None and not self.tko.done

    def _enrich(self, state, **kw):
        return state if self.reads is None else self.reads.enrich(state, **kw)

    # ------------------------------------------------------------------------------------------------------ tick
    def step(self, state, ctx):
        m, now, alt = self.modes, ctx.now(), state.alt_ft
        events, acts, key_ups = [], [], []
        dec = dict(handover=False, exit=None)
        tgt = self.speed.target                          # the speed target as this tick changes it
        kt_out = None                                    # the last speed set this tick (Command.speed_kt)
        ctx.speed_target = tgt
        # --- 0 route
        rc = self.fly_leg.step(state, ctx)
        if not self.started:
            self.started = True
            if self.fly_leg.mode == "route":             # started in the air: nav joined the nearest leg, at cruise
                self.phase = "route"
                if m.auto: tgt = kt_out = self.cruise_kt
        if self.phase == "takeoff" and self.fly_leg.mode == "route": self.phase = "route"
        want = rc.heading
        ov, behavior = Command(), "fly_leg"
        # --- phases (replace each other)
        def take(cmd, name):
            nonlocal ov, tgt, kt_out, behavior
            ov, behavior = cmd, name
            for a in cmd.actions:
                acts.append(a)
                if a in (Action.THROTTLE_MAX, Action.THROTTLE_IDLE): tgt = kt_out = None
            if cmd.speed_kt is not None: tgt = kt_out = cmd.speed_kt
            ctx.speed_target = tgt
        if (m.auto or m.strafe) and state.sensitivity != 1:
            pass                                          # every phase waits for keyboard sensitivity 1 (the loops press Ins)
        elif m.auto or m.strafe:
            if alt > 50 and self.fuel is not None:        # Extra Fuel: fuel < 500 lb and a tank left
                fc = self.fuel.step(state, ctx)
                acts.extend(fc.actions); events.extend(fc.events)
            if m.auto and self.tko is None and self.phase == "takeoff" and alt < 50:
                self.tko = Takeoff(self.cruise_kt, self.cruise_ft)
            if self.tko is not None and not self.tko.done:
                c = self.tko.step(state, ctx); take(c, "takeoff")
                if c.done: events.append("takeoff done"); self.fly_leg.takeoff_done = True
            if m.auto and self.phase == "route" and self.attack is None:
                role = self.fly_leg.attack_role(state, ctx, self.attacks_done, self.attack_now)
                if role:
                    A = self.attacks[role]
                    self.attack = Attack(role, A["mode"], A["T"], A["record"], A["launch_km"], A["photo_km"])
                    self.phase = "attack"; events.append(f"{role} attack start ({A['mode']}, record {A['record']})")
            if self.phase == "attack" and self.fuel is not None and self.fuel.busy:
                take(Command(heading=state.heading), "attack")   # fuel transfer has the station keys: hold heading
            elif self.phase == "attack" and self.m_hold:
                # missile threat: the run / photo pass waits (an open bay is +24 EMV), resumes when MissileWatch clears
                take(Command(heading=state.heading, banner=f"  ATTACK {self.attack.role} ON HOLD: missile evasion"), "attack")
                if state.systems.flags & BAY: acts.append(Toggle(Action.BAY_TOGGLE, False))
            elif self.phase == "attack":
                at = self.attack
                st = self._enrich(state, records=at.target_reads, events=True, projectiles=True, cam=at.wants_cam)
                c = at.step(st, ctx); take(c, "attack")
                events.extend(f"{at.role}: {x}" for x in c.events)
                if c.done:
                    events.append(f"{at.role} attack done")
                    # back to cruise speed: left off, the throttle stayed at the attack setting (370-390 kt, 2026-10-18)
                    self.attacks_done.add(at.role); self.attack = None; self.phase = "route"
                    tgt = kt_out = self.cruise_kt
            if self.phase == "route" and self.strafe_q and self.gun is None:   # gun attack on the next radar site
                x_, y_, dead_ = self.reads.target(self.strafe_q[0])
                if dead_: events.append(f"record {self.strafe_q.pop(0)} already destroyed")
                else: self.gun = GunAttack(self.strafe_q[0], (float(x_), float(y_))); self.phase = "strafe"
            if self.phase == "strafe":
                g = self.gun
                st = self._enrich(state, records=g.target_reads, gun=True)
                c = g.step(st, ctx); take(c, "gun_attack")
                if c.done:
                    key_ups += ["bs", "down", "up"]       # nav's gun.abort: trigger and stick let go
                    events.extend(f"gun {g.record}: {x}" for x in g.log)
                    self.strafe_q.pop(0); self.gun = None; self.phase = "route"
                    if not self.strafe_q and not m.auto: dec["exit"] = "nav --strafe: done - you have control"
            if m.auto and self.phase == "route" and (self.land_now or self.fly_leg.landing_due(ctx)):
                base, fix = self.fly_leg.landing_points()
                self.landing = Landing(base, fix); self.phase = "landing"
            if self.phase == "landing":
                c = self.landing.step(state, ctx); take(c, "landing")
                if c.done:
                    events.append(f"landing {self.landing.state}")
                    self.phase = "landed" if self.landing.state == "stopped" else "handover"
                    dec["handover"] = True; m.hdg = False; m.alt = None
            if ov.heading is not None: want = ov.heading
        elif self.phase == "route" and self.fly_leg.wps[ctx.leg][1].startswith("Landing"):
            self.phase = "handover"; dec["handover"] = True; m.hdg = False; m.alt = None
        route_want = want
        # --- reactions (merge on top)
        req = {}                                          # reaction -> speed request (kt or MAX)
        if self.fighter is not None and self.phase in FLYING and alt > 100 and m.hdg:
            ctx.want = want
            fc = self.fighter.step(state, ctx); events.extend(fc.events)
            if fc.heading is not None:
                ov = ov.merged(dataclasses.replace(fc, events=())); want = fc.heading; behavior = "fighter"
                req["fighter"] = fc.speed_kt
            elif fc.alt is not None:                      # descend only: the route's heading and speed stay
                ov = ov.merged(Command(alt=fc.alt))
        m_key, self.msnap = "", None
        if self.missile_mode != "off" and self.phase in FLYING and alt > 100 and self.reads is not None:
            try:
                msnap = self.reads.missile_world(self.missile_full)
                if msnap["missiles"] and not self.missile_full: msnap = self.reads.missile_world(True)
                self.missile_full = bool(msnap["missiles"])
            except Exception as e:                       # noqa: BLE001 - as nav: logged, the flight goes on
                msnap = None; events.append(f"missile watch: world read failed ({e!r})")
            self.msnap = msnap
            if msnap is not None:
                if self.mwatch is None:
                    mmode = self.missile_mode or ("act" if msnap["world"]["opp"] >= 3 else "observe")
                    self.mwatch = self.missile_factory(mmode)
                    events.append(f"missile watch: {mmode} (opp {msnap['world']['opp']})")
                mw, ck_ = self.mwatch, self.cmkeys
                ctx.want = want
                mc = mw.step(dataclasses.replace(state, missile_world=msnap), ctx); events.extend(mc.events)
                a, ck = ck_.step(msnap, now); acts.extend(a)
                if ck: events.append(ck); m_key = ck.split(" ")[0]
                if ck_.failed: mw.block(ck_.failed); ck_.failed = None   # the key never took: plan without it
                if mw.mode == "act":
                    if mc.cm:
                        a, ck = ck_.request(mc.cm, msnap, now); acts.extend(a); events.append(ck); m_key = CM_KEYS[mc.cm]
                    if mc.jam_on:
                        a, ck = ck_.jam(mc.jam_on, True, msnap, now); acts.extend(a)
                        if ck: events.append(ck)
                    for j in mc.jam_off or ():
                        a, ck = ck_.jam(j, False, msnap, now); acts.extend(a)
                        if ck: events.append(ck)
                if mc.heading is not None:
                    ov = ov.merged(Command(**{k: getattr(mc, k) for k in MISSILE_FIELDS})); want = mc.heading
                    behavior = "missile"
                    req["missile"] = MAX if mc.survival else mc.speed_kt
        if self.escape is not None and self.phase in FLYING and alt > 100 and (m.hdg or m.alt is not None):
            ctx.want = want
            ec = self.escape.step(state, ctx); events.extend(ec.events)
            if ec.heading is not None:
                ov = ov.merged(dataclasses.replace(ec, events=())); want = ec.heading; behavior = "terrain_escape"
                if (ec.throttle_max and state.speed_kt < 170) or self.sp.esc_full: req["terrain_escape"] = MAX
            else: self.sp.esc_full = False
        # --- the one saved-speed mechanism
        if m.auto:
            tgt, kt_out = self._speed(req, tgt, kt_out, acts, self.phase in FLYING and alt > 100)
        cmd = self._compose(rc, ov, want, acts, kt_out)
        return Decision(cmd, dataclasses.replace(ov, actions=(), events=()), want, route_want, self.phase, behavior,
                        events, tuple(key_ups), dec["handover"], dec["exit"], m_key,
                        None if self.msnap is None else len(self.msnap["missiles"]), self.takeoff_active)

    def _speed(self, req, tgt, kt_out, acts, ran):
        """ran: the reaction layers ran this tick (nav restored a saved speed only inside their blocks: in route /
        attack / strafe above 100 ft)."""
        sp = self.sp
        guarding = getattr(self.speed, "guarding", False)
        top = next((k for k in ("terrain_escape", "missile", "fighter") if k in req), None)
        if top is not None and (top == "terrain_escape" or not guarding):
            r = req[top]
            if not sp.active: sp.saved = tgt
            if r == MAX:
                # nav: the escape went to full power once per escape (esc_full); a survival whenever the target was set
                if (not sp.esc_full) if top == "terrain_escape" else (tgt is not None):
                    acts.append(Action.THROTTLE_MAX); tgt = kt_out = None; sp.maxed = True
                if top == "terrain_escape": sp.esc_full = True
            elif r is not None and tgt != r: tgt = kt_out = r
        elif top is None and sp.active and ran:
            back = sp.saved if sp.saved is not None else (self.cruise_kt if sp.maxed else None)
            if back is not None: tgt = kt_out = back
            sp.saved, sp.maxed, sp.esc_full = SpeedEpisodes.NONE, False, False
        return tgt, kt_out

    @staticmethod
    def _compose(rc, ov, want, acts, kt_out):
        base = dataclasses.replace(rc, actions=(), events=())
        cmd = base.merged(dataclasses.replace(ov, actions=(), events=()))
        return dataclasses.replace(cmd, heading=want, actions=tuple(acts), speed_kt=kt_out)
