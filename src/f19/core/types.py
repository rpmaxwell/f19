"""Value objects shared by every layer (stdlib only). Positions in units (64 per km, y south), headings in deg,
altitudes in ft, times in game s. Field sets follow what the code reads and writes today: scripts/f19_state.py read_*
dicts, scripts/read_mission.py parse() (mission_mem.json), planner route.json "points", and the override dicts the
phases / reactions hand nav (refactor plan 4.1). Convenience constructors take those dicts and files."""
import dataclasses
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from f19.core.clock import Clock
from f19.core.geometry import dist, game_dist_units, heading_of
from f19.core.theater import Theater
from f19.core.units import CLOCK_TICK_S, K, POS_PER_UNIT, cell_of

dc = dataclass(frozen=True, slots=True)


def _load(src):
    return src if isinstance(src, dict) else json.loads(Path(src).read_text())


# --------------------------------------------------------------------------------------------------- aircraft state
@dc
class Position:
    x: float                 # units, east
    y: float                 # units, south
    alt_ft: float = 0.0

    @property
    def xy(self): return self.x, self.y

    @property
    def cell(self): return cell_of(self.x, self.y)

    @classmethod
    def from_raw(cls, x_pos, y_pos, alt_ft=0.0):
        """From the u32 player position (2048 per km, f19_state.read_state x / y): units = pos / 32 as nav does."""
        return cls(x_pos / POS_PER_UNIT, y_pos / POS_PER_UNIT, alt_ft)


@dc
class Attitude:
    heading: float           # deg 0 north clockwise (ds:46DA)
    pitch: float = 0.0       # deg, + nose up (ds:46DC)
    roll: float = 0.0        # deg (ds:46DE)


# FLAGS word ds:684C (f19_state.py; toggle bits confirmed on the runway 2026-10-17)
GEAR_UP, FLAPS, BAY, BRAKES, ECM, IRJ = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20


@dc
class Systems:
    """Switches, stores and damage (f19_state.read_flight / read_status)."""
    flags: int = 0                       # ds:684C, bits above
    throttle: int | None = None          # percent
    station: int | None = None           # selected weapon station 0-3
    loadout: tuple = ()                  # 4 x (weapon index, count), weapon indices as analysis/ordnance.py
    fuel_lb: int | None = None           # ds:4ED0
    gun_rounds: int | None = None
    flares: int | None = None            # ds:4ED4 stores
    chaff: int | None = None
    decoys: int | None = None
    dmg_bits: int = 0                    # ds:4ECE: 0x01 missile warning, 0x02 engine, 0x04 controls, 0x08 INS, 0x10 bay, 0x20 leak, 0x40 CM
    dmg_count: int | None = None         # ds:95BE, starts at 2, +opp+1 per hit
    locked: int | None = None            # ds:5526 locked ground target record
    hud_lock: int | None = None          # ds:6342
    rwy_box: int | None = None           # ds:5534, nonzero = inside the runway box
    trackcam: int | None = None          # 2 air-to-ground, 0 air-to-air, 1 nav

    gear_up = property(lambda s: bool(s.flags & GEAR_UP))
    flaps = property(lambda s: bool(s.flags & FLAPS))
    bay_open = property(lambda s: bool(s.flags & BAY))
    brakes = property(lambda s: bool(s.flags & BRAKES))
    ecm = property(lambda s: bool(s.flags & ECM))
    ir_jammer = property(lambda s: bool(s.flags & IRJ))


@dc
class TrackcamView:
    """What the B / "/" target pick depends on (reader.read_trackcam_view; rules.trackcam)."""
    screen: int = 0                      # ds:9740 (0x13 / 0x19 = trackcam screens)
    view: int = 0                        # ds:9E8C view offset (u16, 0x4000 = 90 deg)
    night: int = 0                       # ds:4EF4
    flags: int = 0                       # ds:94E0 (bit 0x80 blocks B)
    cooldown: int = 0                    # ds:8B22
    records: tuple = ()                  # (index, x, y, flags) of every record the search walks (ds:80AA, ds:9500 of them)


class ThreatKind(Enum):
    SITE = "site"            # target record with a radar type (f19_state.read_site_records)
    AIRCRAFT = "aircraft"    # sim-object slot (read_aircraft)
    MISSILE = "missile"      # enemy projectile slot 0-7 (read_enemy_missiles)


@dc
class Threat:
    kind: ThreatKind
    id: int                  # site record / aircraft or missile slot
    x: float
    y: float
    alt_ft: float = 0.0
    type: int = 0            # radar type (site) / aircraft type (ds:49B0) / weapon spec (missile)
    alert: int = 0           # 0-255
    flags: int = 0
    name: str = ""
    on: bool | None = None   # site: switched on this duty window (flags 0x02)
    dead: bool = False       # site: destroyed (flags 0x80)

    @property
    def xy(self): return self.x, self.y

    @classmethod
    def from_site(cls, d):
        return cls(ThreatKind.SITE, d["record"], d["x"], d["y"], type=d["type"], alert=d.get("alert", 0),
                   flags=d.get("flags", 0), on=d.get("on"), dead=d.get("dead", False))

    @classmethod
    def from_aircraft(cls, d):
        return cls(ThreatKind.AIRCRAFT, d["slot"], d["x"], d["y"], d.get("alt", 0), d["type"], d.get("alert", 0),
                   d.get("flags", 0), d.get("name", ""))


@dc
class GameState:
    """One snapshot of the game, as the pilot sees it."""
    t: float | None                      # game s (clock ds:6630 x 2), None if not read
    pos: Position
    att: Attitude
    speed_kt: float
    systems: Systems = Systems()
    opp: int | None = None               # ds:4EF0, 0 Green .. 3 Elite
    emv: int | None = None               # ds:551C
    threat_color: int | None = None      # ds:552C
    vis_ids: int | None = None
    radar_ids: int | None = None
    alerted: int | None = None
    net_alert: int | None = None         # ds:95CE
    ap_alt: int | None = None            # ds:4EEE game autopilot altitude
    spool: int | None = None
    n_events: int | None = None          # ds:632E
    hud: str = ""
    threats: tuple = ()                  # Threat
    waypoints: tuple = ()                # the game's 4 waypoints (x, y) units (phys 0x403CA)
    wp_index: int | None = None          # selected game waypoint 0-3
    sensitivity: int | None = None       # keyboard sensitivity 1-3
    # --- WP-7b: reads the behaviors did inside step() (Attack, GunAttack, MissileWatch); None / () = not read
    time_mult: float | None = None       # old 0x410AA game time multiplier (nav.time_mult; 1 if unreadable); None -> 1
    events: tuple | None = None          # flight-event records (type, flags, target, time) (read_events)
    projectiles: tuple = ()              # player projectile slots 8-11 in order: (spec u16 +0x10, ttl u16 +0x0E) (read_projectiles)
    target_records: tuple = ()           # (record, x, y, destroyed) target-table records the reader was asked for (read_target)
    cam: TrackcamView | None = None      # trackcam selection inputs (Reads.enrich(cam=True)); None = not read
    frame_scale: int | None = None       # ds:4EFC frames per 2 s tick (read_gun fs)
    gun_slots: int | None = None         # bullet slots in use (read_gun nslots); rounds = systems.gun_rounds
    missile_world: Any = field(default=None, compare=False, repr=False)   # missile_game.parse() dict (MissileWatch's snap)

    @property
    def xy(self): return self.pos.x, self.pos.y

    @property
    def alt_ft(self): return self.pos.alt_ft

    @property
    def heading(self): return self.att.heading

    @property
    def fuel_lb(self): return self.systems.fuel_lb

    @property
    def ts(self): return 1.0 if self.time_mult is None else self.time_mult

    def target_record(self, record):
        """(x, y, destroyed) of a target-table record in target_records, or None."""
        return next(((x, y, d) for r, x, y, d in self.target_records if r == record), None)

    @property
    def aircraft(self):
        """The AIRCRAFT threats as f19_state.read_aircraft dicts (slot order kept)."""
        return [dict(slot=th.id, x=th.x, y=th.y, alt=th.alt_ft, type=th.type, name=th.name, flags=th.flags, alert=th.alert)
                for th in self.threats if th.kind is ThreatKind.AIRCRAFT]

    @classmethod
    def from_dicts(cls, state, flight=None, status=None, alerts=None, aircraft=None, sites=None, waypoints=None,
                   trackcam=None, sensitivity=None, t=None, time_mult=None, events=None, projectiles=None, targets=None,
                   gun=None, missile_world=None):
        """From the f19_state read_* results: read_state, read_flight, read_status, read_alerts ((sites, planes)),
        read_aircraft, read_site_records, read_waypoints ((points, index)). t overrides the status clock.
        WP-7b reads: time_mult (nav.time_mult), events (read_events), projectiles (read_projectiles raw records),
        targets ({record: read_target(record)}), gun (read_gun (rounds, fs, nslots): rounds override the status
        word, same address), missile_world (missile_game.parse())."""
        f, st = flight or {}, status or {}
        rounds = gun[0] if gun else st.get("gun_rounds")
        sys_ = Systems(flags=f.get("flags", 0), throttle=f.get("throttle"), station=f.get("station"),
                       loadout=tuple(tuple(x) for x in f.get("loadout", ())), fuel_lb=f.get("fuel", st.get("fuel")),
                       gun_rounds=rounds, flares=st.get("flares"), chaff=st.get("chaff"),
                       decoys=st.get("decoys"), dmg_bits=st.get("dmg_bits", 0), dmg_count=st.get("dmg_count"),
                       locked=f.get("locked"), hud_lock=f.get("hud_lock"), rwy_box=f.get("box"), trackcam=trackcam)
        threats = [Threat.from_site(d) for d in sites or ()] + [Threat.from_aircraft(d) for d in aircraft or ()]
        if alerts:
            sa, pa = alerts
            threats = [dataclasses.replace(th, alert=(sa if th.kind is ThreatKind.SITE else pa).get(th.id, th.alert))
                       for th in threats]
        if t is None and "clock" in st: t = st["clock"] * CLOCK_TICK_S
        wps, wpi = waypoints if waypoints else ((), None)
        return cls(t=t, pos=Position.from_raw(state["x"], state["y"], state["alt"]),
                   att=Attitude(state["hdg"], f.get("pitch", 0.0), f.get("roll", 0.0)), speed_kt=state["spd"],
                   systems=sys_, opp=f.get("opp"), emv=st.get("emv"), threat_color=st.get("threat"),
                   vis_ids=st.get("vis_ids"), radar_ids=st.get("radar_ids"), alerted=st.get("alerted"),
                   net_alert=st.get("net_alert"), ap_alt=st.get("ap_alt"), spool=st.get("spool"),
                   n_events=st.get("n_events"), hud=st.get("hud", ""), threats=tuple(threats),
                   waypoints=tuple(tuple(w) for w in wps), wp_index=wpi, sensitivity=sensitivity,
                   time_mult=time_mult, events=None if events is None else tuple(tuple(e) for e in events),
                   projectiles=tuple((int.from_bytes(r[0x10:0x12], "little"), int.from_bytes(r[0xE:0x10], "little"))
                                     for r in projectiles or ()),
                   target_records=tuple((k, *v) for k, v in (targets or {}).items()),
                   frame_scale=gun[1] if gun else None, gun_slots=gun[2] if gun else None, missile_world=missile_world)


# --------------------------------------------------------------------------------------------------------- mission
@dc
class Target:
    """A mission target (mission_mem.json "targets"). role 'primary' / 'secondary'; 'primary?' when read in flight
    (no briefing block: the +0x18 / +0x28 slots, roles unknown)."""
    role: str
    x: float
    y: float
    place: str = ""
    object: str = ""
    record: int | None = None            # START's record index (briefing block +2)
    mission: int | None = None           # mission number (START ds:4B0C table)
    task: str = ""                       # photograph, destroy, supply drop, ...
    onc: str = ""
    code: int | None = None
    deadline_raw: int | None = None      # primary only: generator ds:CA4A >> 4, 0 = none
    deadline: str | None = None          # "hh:mm"

    @property
    def xy(self): return self.x, self.y

    @property
    def role_known(self): return not self.role.endswith("?")

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dc
class Base:
    """Takeoff / landing place (mission_mem.json "takeoff" / "landing")."""
    place: str
    x: float
    y: float
    object: str = ""
    record: int | None = None
    flags: int = 0                       # .WLD flags[0]: 1025 airbase, & 0x200 carrier
    carrier: bool = False

    @property
    def xy(self): return self.x, self.y

    @classmethod
    def from_dict(cls, d):
        return cls(d["place"], d["x"], d["y"], d.get("object", ""), d.get("record"), d.get("flags", 0),
                   d.get("carrier", bool(d.get("flags", 0) & 0x200)))


@dc
class RadarSite:
    """A radar (mission_mem.json "radars"): radar_type = the record's kind = radar_game radar type (1 SA-2 .. 21 OTH);
    None in mission files written before read_mission saved it."""
    place: str
    system: str
    x: float
    y: float
    radar_type: int | None = None
    doppler: bool = False
    range_km: float = 0
    record: int | None = None

    @property
    def xy(self): return self.x, self.y

    @classmethod
    def from_dict(cls, d):
        return cls(d["place"], d["system"], d["x"], d["y"], d.get("kind"), d.get("type") == "D", d.get("range_km", 0),
                   d.get("record"))


class WaypointKind(Enum):
    TAKEOFF = "takeoff"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    LANDING = "landing"


class WaypointStatus(Enum):
    PENDING = "pending"
    REACHED = "reached"
    SKIPPED = "skipped"


@dc
class Waypoint:
    """A mission-plan stop (FlightDirector, refactor plan 4.2). Only TAKEOFF is required."""
    kind: WaypointKind
    pos: tuple                           # (x, y) units
    label: str = ""
    required: bool = False
    status: WaypointStatus = WaypointStatus.PENDING
    constraints: tuple = ()              # unused for now

    def with_status(self, status): return dataclasses.replace(self, status=status)


@dc
class Mission:
    theater: Theater | None
    targets: tuple = ()                  # Target
    radars: tuple = ()                   # RadarSite
    takeoff: Base | None = None
    landing: Base | None = None
    takeoff_heading: float | None = None
    setup: str = ""                      # strike / air-to-air
    level: int | None = None
    start_time: str | None = None        # briefing "hh:mm"

    def target(self, role): return next((t for t in self.targets if t.role.rstrip("?") == role), None)

    @property
    def primary(self): return self.target("primary")

    @property
    def secondary(self): return self.target("secondary")

    def waypoints(self):
        """TAKEOFF (required), PRIMARY, SECONDARY, LANDING - the ones the mission has."""
        out = []
        if self.takeoff: out.append(Waypoint(WaypointKind.TAKEOFF, self.takeoff.xy, self.takeoff.place, required=True))
        for k in (WaypointKind.PRIMARY, WaypointKind.SECONDARY):
            if t := self.target(k.value): out.append(Waypoint(k, t.xy, f"{t.object} {t.place}".strip()))
        if self.landing: out.append(Waypoint(WaypointKind.LANDING, self.landing.xy, self.landing.place))
        return tuple(out)

    @classmethod
    def from_mission_json(cls, src):
        """From read_mission.py output (mission_mem.json): a dict or a path."""
        d = _load(src)
        th = d.get("theater")
        th = Theater.parse(th) if th else Theater.from_index(d["theater_index"]) if "theater_index" in d else None
        return cls(theater=th, targets=tuple(Target.from_dict(t) for t in d.get("targets", ())),
                   radars=tuple(RadarSite.from_dict(r) for r in d.get("radars", ())),
                   takeoff=Base.from_dict(d["takeoff"]) if d.get("takeoff") else None,
                   landing=Base.from_dict(d["landing"]) if d.get("landing") else None,
                   takeoff_heading=d.get("takeoff_heading"), setup=d.get("setup", ""), level=d.get("level"),
                   start_time=(d.get("times") or {}).get("start"))


# ----------------------------------------------------------------------------------------------------------- route
@dc
class RoutePoint:
    """One route.json "points" entry (planner schema 2). kind: takeoff, climb, cruise, runin, photo, launch, overfly,
    intercept, probe, loiter, loiter_runin, loiter_runout, approach, final, landing (None = plain corner)."""
    x: float
    y: float
    label: str = ""
    kind: str | None = None
    role: str | None = None              # primary / secondary
    action: str | None = None            # attack cue text
    throttle: int | None = None
    flaps: bool | None = None
    speed_kt: int | None = None
    fuel_lb: int | None = None           # planned fuel at this point
    t_game_s: int | None = None          # planned game time at this point
    alt_ft: int | None = None
    probe: str | None = None
    loiter: str | None = None

    @property
    def xy(self): return self.x, self.y

    @classmethod
    def from_dict(cls, d):
        extra = set(d) - set(cls.__dataclass_fields__)
        if extra: raise KeyError(f"unknown route point fields {sorted(extra)}")
        return cls(**d)

    def to_dict(self):
        """Inverse of from_dict: the fields that are set (x, y and label always)."""
        return {k: v for k in self.__dataclass_fields__ if (v := getattr(self, k)) is not None or k in ("x", "y", "label")}


@dc
class Leg:
    """Leg `index` runs points[index - 1] -> points[index] (nav.py's leg numbering)."""
    index: int
    start: RoutePoint
    end: RoutePoint

    @property
    def length_units(self): return dist(self.start.xy, self.end.xy)

    @property
    def length_km(self): return self.length_units / K

    @property
    def game_length_units(self): return game_dist_units(self.start.xy, self.end.xy)

    @property
    def heading(self): return heading_of((self.end.x - self.start.x, self.end.y - self.start.y))


@dc
class Route:
    points: tuple                        # RoutePoint
    version: int = 0
    takeoff_heading: float | None = None
    profile: dict = field(default_factory=dict, compare=False, repr=False)
    summary: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def legs(self):
        # Keeps zero-length legs (the planner's own output). The flyer (pilot/phases/fly_leg.py) merges them, as nav did: decided 2026-10-29, one rule per consumer.
        return tuple(Leg(i, self.points[i - 1], self.points[i]) for i in range(1, len(self.points)))

    def with_version(self, v): return dataclasses.replace(self, version=v)

    @classmethod
    def from_route_json(cls, src, version=0):
        """route.json (a dict or a path): planner schema 2 ("points"), or the older "waypoints_game" ([[x, y], label]).
        "waypoints_km"-only routes need the briefing-map calibration (nav.plan_km_to_game) and are refused."""
        d = _load(src)
        if "points" in d: pts = tuple(RoutePoint.from_dict(p) for p in d["points"])
        elif "waypoints_game" in d: pts = tuple(RoutePoint(float(p[0]), float(p[1]), name or "") for p, name in d["waypoints_game"])
        else: raise ValueError("route has no game-unit points (waypoints_km only: needs the briefing calibration)")
        return cls(pts, version, d.get("takeoff_heading"), dict(d.get("profile") or {}), dict(d.get("summary") or {}))


# --------------------------------------------------------------------------------------------------------- control
class Action(Enum):
    """Discrete controls (pitch / roll / heading stay continuous, via the loops). Keys from phases.py's docstring and
    missile_watch.KEYS; pilot/controls.py maps them."""
    GEAR_TOGGLE = "gear"         # 6
    FLAPS_TOGGLE = "flaps"       # 9
    BRAKES_TOGGLE = "brakes"     # 0
    BAY_TOGGLE = "bay"           # 8
    NEXT_WEAPON = "next_weapon"  # Space
    FIRE = "fire"                # Return
    GUN = "gun"                  # Backspace
    FLARE = "flare"              # 1
    CHAFF = "chaff"              # 2
    DECOY = "decoy"              # 5
    IR_JAMMER = "ir_jammer"      # 3 (toggle)
    ECM = "ecm"                  # 4 (toggle)
    TRACKCAM_MODE = "trackcam"   # F2
    SELECT_TARGET = "select_target"   # B
    DESIGNATE = "designate"
    EJECT = "eject"
    THROTTLE_UP = "throttle_up"       # =
    THROTTLE_DOWN = "throttle_down"   # -
    THROTTLE_MAX = "throttle_max"     # Shift+=
    THROTTLE_IDLE = "throttle_idle"   # Shift+-
    # WP-7b additions
    TRACKCAM_VIEW = "trackcam_view"   # / (scancode 0x35): tracking camera view up (B needs it)
    CENTER_STICK = "center_stick"     # keypad 5: centre the stick (pitch and bank deflection persist)
    SENSITIVITY = "sensitivity"       # Ins: keyboard sensitivity one step


# Toggle actions -> the Systems.flags bit they flip (Toggle.on = the bit set: GEAR_TOGGLE on = gear UP)
TOGGLE_BITS = {Action.GEAR_TOGGLE: GEAR_UP, Action.FLAPS_TOGGLE: FLAPS, Action.BAY_TOGGLE: BAY, Action.BRAKES_TOGGLE: BRAKES,
               Action.ECM: ECM, Action.IR_JAMMER: IRJ}


@dc
class Toggle:
    """A switch request inside Command.actions (phases.set_toggle): make the flags bit of `action` (TOGGLE_BITS) read
    `on`. Controls taps the key only if the flags word disagrees AND that key was last tapped >= settle_s ago (the
    debounce is per key and shared by every behavior and loop of the flight: the flags word lags a press by a cycle or
    two and a second tap flips the toggle back). No tap when the flag already reads `on`."""
    action: Action
    on: bool
    settle_s: float = 3.0

    @property
    def bit(self): return TOGGLE_BITS[self.action]

    def satisfied(self, flags): return bool(flags & self.bit) == self.on


_OV_NAMES = {"want": "heading"}          # override-dict key -> Command field where they differ
_NOT_OV = ("actions", "events")          # Command fields that are not override-dict keys


@dc
class Command:
    """What a behavior wants this tick: the typed form of nav's override dict (later behaviors win key by key).
    None = no opinion (nav's default)."""
    heading: float | None = None         # "want", deg
    rate_cap: float | None = None        # max turn rate, deg per real s
    bank_max: float | None = None        # deg
    roll_hold: bool | None = None        # False: release the roll keys
    alt: float | None = None             # hold altitude, ft
    alt_cap: float | None = None
    alt_min: float | None = None
    vs_max: float | None = None          # ft per ...  as nav's VS_MAX
    vs_target: float | None = None
    deadband: float | None = None
    floor: float | None = None           # altitude floor guard, ft
    alt_hold: bool | None = None         # False: no pitch keys
    no_thr_up: bool | None = None
    speed_kt: float | None = None
    throttle_max: bool | None = None
    cm: int | str | None = None          # countermeasure to drop: missile_game FLARE 1 / CHAFF 2 / DECOY 3
    jam_on: str | None = None            # "ecm" / "irj"
    jam_off: tuple | None = None
    survival: bool | None = None
    abort_attack: bool | None = None
    escape: str | None = None            # TerrainEscape state (display / log tag)
    fighter: str | None = None           # FighterWatch state
    missile: str | None = None           # MissileWatch state
    banner: str | None = None
    done: bool | None = None
    actions: tuple = ()                  # Action (tap once) or Toggle (set a switch, debounced), in order
    # --- WP-7b. Pitch: what the pitch loop (WP-7a) turns into nose-up ("down" arrow) / nose-down ("up") keys.
    pitch_min: float | None = None       # band hold (Takeoff): pitch < pitch_min -> tap nose-up 0.6 s; pitch > pitch_max ->
    pitch_max: float | None = None       #   tap nose-down 0.5 s; then no band tap for 1.0 / time_mult s (loop's own timer)
    pitch_deg: float | None = None       # one tap THIS tick toward pitch_deg if |pitch_deg - pitch| > pitch_tol, held
    pitch_tol: float | None = None       #   clamp(|err| / 6.0 / time_mult, 0.08, 0.5) s (GunAttack; it spaces them itself)
    pitch_hold: str | None = None        # "up" / "down": keep that pitch key held while set; released the tick it isn't
    gun_fire: bool | None = None         # Backspace trigger: True held, False released, None no opinion (released on done)
    gun_repress: bool | None = None      # with gun_fire True: release + press the trigger this tick (no typematic repeat)
    events: tuple = ()                   # mission-log lines this tick (phase .log lines / reaction event texts)

    @classmethod
    def from_overrides(cls, ov):
        """From an override dict (phases / missile_watch step() results). Unknown keys raise KeyError."""
        inv = {v: k for k, v in _OV_NAMES.items()}
        keys = {inv.get(k, k) for k in cls.__dataclass_fields__ if k not in _NOT_OV}
        if bad := set(ov) - keys: raise KeyError(f"unknown override keys {sorted(bad)}")
        kw = {_OV_NAMES.get(k, k): v for k, v in ov.items()}
        if "jam_off" in kw: kw["jam_off"] = tuple(kw["jam_off"])
        return cls(**kw)

    def to_overrides(self):
        """Inverse of from_overrides (jam_off as a list; actions and events are not part of the dict)."""
        inv = {v: k for k, v in _OV_NAMES.items()}
        out = {inv.get(k, k): v for k in self.__dataclass_fields__ if k not in _NOT_OV and (v := getattr(self, k)) is not None}
        if "jam_off" in out: out["jam_off"] = list(out["jam_off"])
        return out

    def merged(self, other):
        """self overridden by other's set fields (nav's {**ov, **other}); actions and events concatenate."""
        kw = {k: v for k in self.__dataclass_fields__ if k not in _NOT_OV and (v := getattr(other, k)) is not None}
        return dataclasses.replace(self, **kw, actions=self.actions + other.actions, events=self.events + other.events)


@dc
class Frame:
    """One tick, for every telemetry sink."""
    t: float | None                      # game s
    state: GameState
    behavior: str
    command: Command
    plan_version: int | None = None
    leg: int | None = None
    events: tuple = ()                   # text


# -------------------------------------------------------------------------------------------------------- behaviors
@dataclass(slots=True)
class FlightContext:
    """What a behavior may use besides the GameState (mutable, one per flight)."""
    clock: Clock
    mission: Mission | None = None
    route: Route | None = None           # the active route
    reference: Route | None = None       # the pre-flight best-case route
    leg: int = 1
    settings: Any = None
    reader: Any = None                   # memory reader for reads not in GameState yet (events, trackcam, projectiles)
    log: list = field(default_factory=list)
    # WP-7b: what a behavior needs from the tick so far (the arbiter / runner keeps them current)
    want: float | None = None            # heading the lower-priority behaviors asked for this tick (reactions' route_want)
    speed_target: float | None = None    # the speed hold's target as the loops left it last tick (None = off)
    toggled: dict = field(default_factory=dict)   # Controls' Toggle debounce: Action -> ctx time of its last tap
                                                  # (Controls writes it; behaviors only read it to predict a tap)

    def now(self): return self.clock.now()


@runtime_checkable
class Behavior(Protocol):
    name: str
    priority: int                        # higher wins in the arbiter
    done: bool

    def wants_control(self, state: GameState, ctx: FlightContext) -> bool: ...
    def step(self, state: GameState, ctx: FlightContext) -> Command: ...
