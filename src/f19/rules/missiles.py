"""The game's enemy-missile rules as code, transcribed from EGAME.EXE (notes/enemy_ordnance_and_countermeasures.md §1,
re-read from the disassembly 2026-10-23). Integer steps as the game does them, one call per game frame, so it can be
replayed against memlog snapshots and used by nav's MissileWatch (scripts/phases.py) to predict a missile's outcome.

The memlog checks (R1 gate, outcomes) and the snapshot parser stay in analysis/missile_game.py:
  missile_game.py check [runs...]     R1 gate: replay every memlog missile snapshot-to-snapshot + outcomes
  missile_game.py replay <run>        offline MissileWatch (observe mode) on a memlog flight (PRD acceptance 2)
The spec table is read from EGAME on first use (specs(); `SPECS` is the same dict).

Units: waypoint units (64 per km), game distance max + min/2, alt ft, angles 65536 = 360 deg (heading 0 north,
clockwise, Y grows south), fs = frames per 2-s tick (ds:4EFC). One frame = 2 / fs game s.

Frame order of the enemy branch (0x746b -> 0x6d3f -> 0x6de5 ... 0x727d), per missile:
  aim = decoy (slot 0 alive) else player; seeker (0x760c) on it -> locked, dist, bearing (overshoot kill inside)
  jamming / network (0x74a5); flares / chaff (CM slots 1-3, 0x750a); launcher support beyond 8 km (0x75a9)
  steer if locked (heading + pitch, two clamps each, x4/fs), else nose over (pitch > 0 only, never below level)
  move, ttl -= 1; alt < 0 -> ground; hit test (0x727d) on the pre-move distance.

Found in the re-read (not in the note before 2026-10-23):
  - A flare / chaff slot keeps its kind after its ttl runs out (0x47a3 only decrements; memlog 1792546698: slot 3 stays
    kind 1 ttl 0), and the capture loop (0x750a) tests the kind, not the ttl: an EXPIRED flare/chaff keeps capturing
    until the slot is reused. Memlog 1792546698 (Veteran, one flare): an SA-14 flew dead straight for 7 snapshot pairs
    ~15 s after the flare died; with the rule all 7 pairs replay exactly, without it all 7 fail. Chaff: same code path,
    no memlog case yet. The ttl only matters for which slot the next CM takes (highest one with ttl 0).
  - The CM ager (0x47a3) ages only the LOWEST live slot each frame: two live CMs age at half speed each; a live decoy
    (slot 0) freezes flares/chaff.
  - A new flare/chaff takes the HIGHEST free slot of 1-3; none free -> the store is still spent, nothing dropped.
  - The heading clamp (0xd0da) returns +max for an error below -16384 (more than 90 deg left): only class 3 (no cone)
    ever sees that - it turns the long way round.
  - The overshoot kill sets ttl 0 and the frame's ttl -= 1 then leaves it at -1: dead for every purpose.
  - Unlocked nose-over: pitch -= 2048 / fs per frame while pitch > 0, then level: a dumb missile flies LEVEL, not into
    the ground (it ends by ttl, or by the terrain check).
  - Decoy key '5' (key code 0x635 -> 0x468f(3)); '1' flare, '2' chaff, '3' IR jammer, '4' radar jammer.
"""
import math
import struct
from functools import lru_cache

from f19.core.geometry import game_atan2_16, game_len16
from f19.core.units import s16 as _s16
from f19.gamedata.exe import DS_FILE as DSF, egame

# --- spec table (ds:4C10 + 0x12 x spec) -------------------------------------------------------------------------------
def spec(s):
    """dict(name, range_km, speed (missile speed units, max >> 6), cls, agility) of weapon spec s."""
    r = egame()[DSF + 0x4C10 + 0x12 * s: DSF + 0x4C10 + 0x12 * s + 0x12]
    rng, vmax, cls, ag = struct.unpack_from("<hhhh", r, 8)
    return dict(name=r[:8].split(b"\0")[0].decode("latin1"), range_km=rng, speed=vmax >> 6, cls=cls, agility=ag)


@lru_cache(None)
def specs():
    """{spec: spec(spec)} for every weapon spec 0..0x25 (the module attribute SPECS, built on first use)."""
    return {s: spec(s) for s in range(0x26)}


def __getattr__(name):
    if name == "SPECS": return specs()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


CLASS_NAME = {0: "IR tail", -1: "IR all-aspect", 1: "command", 2: "SARH", 3: "all-aspect radar"}

CONE, OVERSHOOT, IR_TAIL = 0x1000, 0x6000, 0x2000
SUPPORT_UNITS = 0x200                    # beyond 8 km the launcher must keep seeing us
FLARE, CHAFF, DECOY = 1, 2, 3
DEAD_CM = True                           # expired flares/chaff keep capturing (module doc; memlog 1792546698: 7/7 pairs)

s16 = _s16                                # core.units.s16 (identical)
gdist = game_len16                       # core.geometry.game_len16: max + (min >> 1), clamped to 0x7FFF (identical on ints)
atan2g = game_atan2_16                   # core.geometry.game_atan2_16: 0xd17d, the game's own polynomial (identical)
def mulsin(ang, v): return int(round(v * math.sin(ang * math.pi / 32768)))  # 0xd2cc (memlog: rounds, not truncates)
def mulcos(ang, v): return mulsin(ang + 0x4000, v)                         # 0xd2e4
def clamp(v, lo, hi):
    """0xd0da, with its quirk: v < lo and v <= -16384 -> hi."""
    if v > hi: return hi
    if v >= lo: return v
    return hi if v <= -16384 else lo
def idiv(a, b): q = abs(a) // abs(b); return q if (a >= 0) == (b > 0) else -q   # x86 idiv: toward zero


# --- one missile ------------------------------------------------------------------------------------------------------
class Missile:
    __slots__ = ("slot", "x", "y", "alt", "spd", "hdg", "pitch", "ttl", "spec", "lau")
    def __init__(self, slot, x, y, alt, spd, hdg, pitch, ttl, spec, lau):
        self.slot, self.x, self.y, self.alt, self.spd = slot, x, y, alt, spd
        self.hdg, self.pitch, self.ttl, self.spec, self.lau = hdg & 0xFFFF, s16(pitch), ttl, spec, lau
    def copy(self): return Missile(*(getattr(self, k) for k in self.__slots__))
    @property
    def cls(self): return specs()[self.spec]["cls"] if self.spec in specs() else 2
    @property
    def name(self): return specs()[self.spec]["name"] if self.spec in specs() else f"spec {self.spec}"
    def __repr__(self): return f"<{self.name} slot {self.slot} ({self.x},{self.y},{self.alt}) hdg {self.hdg * 360 / 65536:.1f} ttl {self.ttl}>"


def seeker_ok(m, tx, ty, player_hdg, cls):
    """0x760c -> (ok, dist, bearing, destroyed). Cone 22.5 deg (not class 3); class 0 also needs the missile heading within
    45 deg of the player's; the aim point > 135 deg off the nose and farther than one speed step destroys the missile."""
    dx, dy = tx - m.x, ty - m.y
    d = gdist(dx, dy); brg = atan2g(dx, -dy)
    off = abs(s16(brg - m.hdg))
    if off > CONE and cls != 3:
        return False, d, brg, off > OVERSHOOT and m.spd < d
    if cls == 0 and abs(s16(m.hdg - player_hdg)) > IR_TAIL: return False, d, brg, False
    return True, d, brg, False

def jammed(cls, dist, flags, opp, net_stale, spec):
    """0x74a5: radar jammer (flags 0x10) vs class > 0, IR jammer (0x20) vs class <= 0, or no network alert / weapon hit for
    100 game s (SA-14 exempt) -> lock lost beyond the burn-through distance opp x 256 + 384, at any range for class +-1."""
    if (flags & 0x10 and cls > 0) or (flags & 0x20 and cls <= 0) or (net_stale and spec != 0x21):
        return (opp << 8) + 0x180 < dist or cls in (1, -1)
    return False

def cm_eligible(kind, cls, aim_brg, player_hdg, opp):
    """Can a CM of this kind capture a class-cls missile (0x750a)? Class 3 + chaff only with the bearing missile -> aim
    point roughly across the player's heading (51-129 deg at Elite)."""
    if kind == FLARE: return cls <= 0
    if kind == CHAFF:
        if cls in (1, 2): return True
        if cls == 3: return 0x40 - 12 * opp > abs(abs(s16(aim_brg - player_hdg) >> 8) - 0x40)
    return False

def launcher_ok(m, dist, site_seen, ac_seen):
    """0x75a9: beyond 8 km a site's missile (launcher index > 2) needs the site's seen bit (+8 0x10), an aircraft's
    (launcher <= 0) the aircraft's seen-on-last-check bit (+0x16 0x08). site_seen(rec) / ac_seen(slot) -> bool."""
    if dist <= SUPPORT_UNITS: return True
    if m.lau > 2 and not site_seen(m.lau): return False
    if m.lau <= 0 and not ac_seen(-m.lau): return False
    return True


class World:
    """What a frame needs besides the missile: the player, flags, CM slots and launcher bits. Callbacks so a prediction
    can vary them along a candidate trajectory."""
    def __init__(self, opp, fs, flags=0, net_stale=False, cms=None, site_seen=None, ac_seen=None, dmg_bits=0):
        self.opp, self.fs, self.flags, self.net_stale, self.dmg_bits = opp, max(1, fs), flags, net_stale, dmg_bits
        self.cms = [list(c) for c in (cms or [[0, 0, 0, 0]] * 4)]       # 4 x [x, y, kind, ttl]; slot 0 = decoy
        self.site_seen = site_seen or (lambda rec: True)
        self.ac_seen = ac_seen or (lambda slot: True)
    def copy(self):
        w = World(self.opp, self.fs, self.flags, self.net_stale, self.cms, self.site_seen, self.ac_seen, self.dmg_bits)
        return w
    @property
    def decoy(self): return self.cms[0][3] > 0
    def release(self, kind, px, py):
        """0x468f: a flare/chaff takes the highest free slot 1-3 (none -> wasted), a decoy slot 0 (not with bit 0x40)."""
        if kind == DECOY:
            j = 0 if self.cms[0][3] == 0 and not self.dmg_bits & 0x40 else None
        else:
            free = [j for j in (1, 2, 3) if self.cms[j][3] == 0]; j = free[-1] if free else None
        if j is None: return False
        self.cms[j] = [px, py, kind, (9 - 2 * self.opp) * self.fs * (8 if kind == DECOY else 1)]
        return True
    def age(self):
        """0x47a3: only the lowest live slot loses a frame."""
        for c in self.cms:
            if c[3] > 0: c[3] -= 1; return


def frame(m, w, px, py, palt, phdg):
    """Advance missile m one frame against the player at (px, py, palt, phdg). Returns (outcome or None, info) where
    outcome is 'hit' / 'decoyed' (hit while a decoy lives: destroyed, no damage) / 'overshoot' / 'expired' / 'ground',
    info = dict(locked, dist, captured, why). Mutates m; does not age the CMs (call w.age() once per frame)."""
    cls, opp, fs = m.cls, w.opp, w.fs
    ax_, ay_ = (w.cms[0][0], w.cms[0][1]) if w.decoy else (px, py)
    ok, dist, brg, destroyed = seeker_ok(m, ax_, ay_, phdg, cls)
    if destroyed: m.ttl = -1; return "overshoot", dict(locked=False, dist=dist, captured=False, why="overshoot")
    locked, aim, why = ok, brg, ("" if ok else "cone" if cls != 0 or abs(s16(brg - m.hdg)) > CONE else "tail")
    if locked and jammed(cls, dist, w.flags, opp, w.net_stale, m.spec): aim, locked, why = m.hdg, False, "jammed"
    captured = False
    for j in (1, 2, 3):
        x, y, kind, ttl = w.cms[j]
        if not kind or (ttl <= 0 and not DEAD_CM): continue
        if not cm_eligible(kind, cls, aim, phdg, opp): continue
        cok, _, _, cdes = seeker_ok(m, x, y, phdg, cls)
        if cdes: m.ttl = -1; return "overshoot", dict(locked=False, dist=dist, captured=True, why="overshoot on CM")
        if cok: aim, locked, captured, why = 1, False, True, "flare" if kind == FLARE else "chaff"
    if locked and not launcher_ok(m, dist, w.site_seen, w.ac_seen): locked, why = False, "launcher"
    if aim and locked:
        herr = s16(aim - m.hdg)
        herr = clamp(herr, -((opp + 1) << 8), (opp + 1) << 8)
        a7 = specs()[m.spec]["agility"] << 7
        herr = clamp(herr, -a7, a7)
        m.hdg = (m.hdg + idiv(herr * 4, fs)) & 0xFFFF
        ad = abs(dist)
        pa = atan2g(s16((ad >> 3) + (s16(palt - m.alt) >> 5)), ad)
        perr = s16(pa - m.pitch)
        perr = clamp(perr, -((opp + 1) << 9), (opp + 1) << 9)
        a8 = specs()[m.spec]["agility"] << 8
        perr = clamp(perr, -a8, a8)
        m.pitch = s16(m.pitch + idiv(perr * 4, fs))
    elif m.pitch > 0:
        m.pitch = s16(m.pitch - idiv(1 << 11, fs))
    h = idiv(mulcos(m.pitch, m.spd) * 4, fs)
    m.x += mulsin(m.hdg, h); m.y -= mulcos(m.hdg, h)
    m.alt += mulsin(m.pitch, idiv(m.spd << 7, fs))
    m.ttl -= 1
    info = dict(locked=locked, dist=dist, captured=captured, why=why, on_player=not w.decoy)
    if m.alt < 0: m.ttl = 0; return "ground", info
    if locked and (abs(s16(palt - m.alt)) >> 5) + dist < (m.spd << 3) // fs and m.ttl > 4 * fs:
        m.ttl = 2 * fs
        return ("decoyed" if w.decoy else "hit"), info
    if m.ttl <= 0: return "expired", info
    return None, info


def simulate(m, w, traj, frames, cm_plan=None, on_frame=None, stop_spent=True):
    """Fly copies of missile m and world w for up to `frames` frames. traj(k) -> (px, py, palt, phdg) at frame k (k = 1
    is the first frame). cm_plan: {frame: kind} releases at the player's position ('ecm' / 'irj': that jammer on from then). on_frame(k, m, info) for tracing.
    -> dict(outcome: hit / decoyed / overshoot / expired / ground / spent (in its last 8 s: can't hit) / flying, frame,
    closest (game units incl. alt/32, while it could still hit), closest_locked (the same, on frames it was locked),
    locked_frames, last_why, m = the missile at the end)."""
    m, w = m.copy(), w.copy()
    closest, closest_l, locked_n, why = 0x7FFF, 0x7FFF, 0, ""
    for k in range(1, frames + 1):
        px, py, palt, phdg = traj(k)
        if cm_plan and k in cm_plan:
            c = cm_plan[k]
            if c == "ecm": w.flags |= 0x10
            elif c == "irj": w.flags |= 0x20
            else: w.release(c, px, py)
        out, info = frame(m, w, px, py, palt, phdg)
        w.age()
        if on_frame: on_frame(k, m, info)
        locked_n += info["locked"]; why = info["why"]
        if m.ttl > 4 * w.fs or out in ("hit", "decoyed"):
            d = gdist(px - m.x, py - m.y) + (abs(palt - m.alt) >> 5)
            closest = min(closest, d)
            if (info["locked"] and info.get("on_player", True)) or out == "hit": closest_l = min(closest_l, d)
        r = dict(frame=k, closest=closest, closest_locked=closest_l, locked_frames=locked_n, last_why=why, m=m)
        if out: return dict(r, outcome=out)
        if stop_spent and m.ttl <= 4 * w.fs: return dict(r, outcome="spent")
    return dict(outcome="flying", frame=frames, closest=closest, closest_locked=closest_l, locked_frames=locked_n, last_why=why, m=m)


# ---- the raw-memory world decoder (was a copy in pilot/runner.py) ----
# analysis/missile_game.parse's layout (ds offsets)
PROJ, CMS, STORES, OPP, FS, FLAGS, NET_T, NET_REF, HOMING = 0x53FC, 0x520A, 0x4ED4, 0x4EF0, 0x4EFC, 0x684C, 0x5522, 0x6EAA, 0x7F96
PX, PY, PALT, PHDG, PPITCH, PROLL, PKT27, CLOCK, DMG_BITS, DMG_COUNT = 0x94EE, 0x94FE, 0x46E0, 0x46DA, 0x46DC, 0x46DE, 0x7F94, 0x6630, 0x4ECE, 0x95BE
TARGETS, N_TARGETS, SIMOBJ, FRAME, GRID = 0x80AA, 0x9C78, 0x8854, 0x5520, 0x85FC


def parse_missile_world(get):
    """analysis/missile_game.parse (moved here; tests/unit/rules checks it against the original): get(ds, n) -> bytes. -> dict(missiles, player, world, stores, sites,
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
