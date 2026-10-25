"""Live nav display (+ optional altitude hold) on exact game coordinates.

  nav.py [--alt FT] [--route logs/radar_sites/route_L8.json]

Coordinates: everything in waypoint-table units (player position >> 5,
64 per km). The planned route (planner km on the briefing map) is mapped to
game units with the affine fit in logs/calib/calibration_v2.json; the game's
own waypoints are read live from phys 0x403CA. With --alt the same automation
connection also holds altitude with >=0.5 s up/down presses (the port serves
one client at a time). Logs every cycle to logs/nav/<start>.csv.
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import argparse, csv, json, math, signal, struct, sys, time
# Stop cleanly on SIGTERM / SIGINT even when started in the background (a non-interactive shell's `&` ignores SIGINT,
# and a kill -9 mid-read left replies queued for the next client): both raise KeyboardInterrupt -> finally releases keys.
def _stop(*_): raise KeyboardInterrupt
signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
from f19_state import read_state, read_waypoints, read_sensitivity, read_flight
import phases
import missile_watch

ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--alt", type=float)
ap.add_argument("--memlog", action="store_true", help="also snapshot game memory (~1/s) to logs/memlog/<start>/ for radar-exposure analysis")
ap.add_argument("--fixed", type=float, help="test mode: ignore the route and hold this heading (deg)")
ap.add_argument("--hdg", action="store_true", help="also hold heading (roll keys) to FLY HEADING; keyboard sensitivity must be level 1")
ap.add_argument("--route", help="planned route json (briefing-map km); default: follow the game's own waypoints")
ap.add_argument("--auto", action="store_true", help="full mission: automatic takeoff, target attacks and landing (scripts/phases.py);"
                " implies --hdg and --alt (route cruise_ft); needs a planner route")
ap.add_argument("--attack-now", help="--auto: start the attack on this role (primary/secondary) at once, e.g. after a manual run-in")
ap.add_argument("--land-now", action="store_true", help="--auto: start the landing phase at once (on the approach)")
ap.add_argument("--done", default="", help="--auto: roles already attacked, comma-separated (skipped)")
ap.add_argument("--strafe", help="gun attack on radar sites now (phases.GunAttack): target record numbers, comma-separated,"
                " or 'nearest'; then hands back control (with --auto: back to the route). Implies --hdg.")
ap.add_argument("--missile-watch", choices=("act", "observe", "off"), help="enemy-missile evasion (scripts/missile_watch.py):"
                " act = take evasive action / countermeasures, observe = predict and log only; default act at Elite, observe below")
ap.add_argument("--plain", action="store_true", help="old one-column text display instead of the tmux-style panes (scripts/nav_tui.py;"
                " always plain when stdout is not a terminal)")
args = ap.parse_args()
if args.auto:
    if not args.route: sys.exit("nav: --auto needs a planner --route (attack and landing points come from it)")
    args.hdg = True
    if args.alt is None: args.alt = float(json.load(open(args.route)).get("profile", {}).get("cruise_ft") or 200)
if args.strafe:
    args.hdg = True
    if args.alt is None: args.alt = float(phases.GunAttack.ENTRY_FT)

K = 64.0                                                   # table units per km
cal = np.array(json.load(open(S.logs / "calib/calibration_v2.json"))["briefing_px_to_table_units_affine"])
sites = json.load(open(S.logs / "radar_sites/sites.json"))
SX = float(np.nanmedian([s["px_per_km_x"] for s in sites])); SY = float(np.nanmedian([s["px_per_km_y"] for s in sites]))
def plan_km_to_game(kx, ky):                               # planner km -> briefing px -> game units
    return tuple(np.array([kx * SX + 32, ky * SY + 96, 1.0]) @ cal)
GAME_WP_NAMES = ["WP1 (midpoint)", "WP2 primary", "WP3 secondary", "WP4 landing"]
def load_route(path):
    """-> (WPS, ATTRS). ATTRS: per waypoint (schema-2 routes from analysis/planner): kind, alt_ft, speed_kt, probe."""
    route = json.load(open(path)); attrs = None
    if "points" in route:                                  # analysis/planner (schema 2)
        wps = [((q["x"], q["y"]), q["label"]) for q in route["points"]]; attrs = route["points"]
    elif "waypoints_game" in route:                        # planned from memory data: already game units
        wps = [(tuple(p), name) for p, name in route["waypoints_game"]]
    else:
        wps = [(plan_km_to_game(*p), name) for p, name in route["waypoints_km"]]
    attrs = attrs or [{} for _ in wps]
    # Drop zero-length legs (repeated points, e.g. where test blocks join): a leg with no
    # length has no direction, so the leg logic never arrives and the plane circles.
    _w, _a = [wps[0]], [attrs[0]]
    for (p, name), at in zip(wps[1:], attrs[1:]):
        if math.dist(p, _w[-1][0]) < 1.0: _w[-1] = (_w[-1][0], _w[-1][1] or name); _a[-1] = {**at, **_a[-1]}
        else: _w.append((p, name)); _a.append(at)
    return _w, _a
WPS, ATTRS = load_route(args.route) if args.route else (None, None)
def leg_attrs(leg): return ATTRS[leg] if ATTRS and leg < len(ATTRS) else {}

LOOKAHEAD, ARRIVE, OVERFLY = 12.0 * K, 3.0 * K, 0.5 * K   # lookahead ~ turn radius at 0.8 deg/s (~13 km)
LOITER_LOOKAHEAD = 6.0 * K
# Takeoff: hold the runway heading wings-level until CLIMB_DIST from the start point
# and above CLIMB_ALT; only then steer the route. Landing: at the approach fix the
# autopilot rolls level, releases everything and hands over (no automatic landing).
# Routes end: Approach fix (20 km out on the N/S runway axis) -> Final (6 km out) ->
# Landing; the axis segment is flown by the autopilot and the handover happens on
# reaching Final, i.e. aligned and >= 5 km out.
CLIMB_DIST, CLIMB_ALT = 6.0 * K, 300
REJOIN = 5.0 * K          # beyond this cross-track distance, intercept the route further ahead (route_aim)
def fly_over(name): return name.startswith(("PRIMARY", "SECONDARY"))   # targets: pass directly over, then turn
def bearing(a, b): return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360
def dist(a, b): return math.hypot(b[0] - a[0], b[1] - a[1])
def game_dist(a, b):
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1]); return (max(dx, dy) + min(dx, dy) / 2) / K
def wrap(d): return (d + 180) % 360 - 180

# Loiter legs (waypoint label "LOITER ..." and the unnamed points after it): orbits out of
# every radar's sight, so the turn-rate cap rises to H_RATE_LOITER and the pursuit point
# comes in to LOITER_LOOKAHEAD to follow a ~11 km circle (cold-war points, 2026-10-15).
def in_loiter(leg):
    return next((WPS[i][1] for i in range(leg - 1, -1, -1) if WPS[i][1]), "").startswith("LOITER")

def seg_dist(i, p):
    (ax, ay), (bx, by) = WPS[i - 1][0], WPS[i][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
    t = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
    return math.hypot(p[0] - ax - t * (bx - ax), p[1] - ay - t * (by - ay))

def next_leg(leg, p, window=8):
    """Move on to a later leg when the plane is clearly nearer to it than to the current
    one. The plane turns on a ~20 km radius (0.8 deg/s cap at ~2x sim speed), wider than
    tight corners, so it can miss a waypoint's arrival circle and orbit it forever
    (2026-10-15). Never skips past a fly-over waypoint (target)."""
    best, bd = leg, seg_dist(leg, p) - 1.0 * K
    for i in range(leg + 1, min(len(WPS), leg + 1 + window)):
        if fly_over(WPS[i - 1][1]): break
        d = seg_dist(i, p)
        if d < bd: best, bd = i, d
    return best

def route_aim(leg, along, xtrack):
    """Pursuit point on the route: LOOKAHEAD ahead of the plane's projection onto the
    current leg, carried over onto later legs near a turn. Off the line (> REJOIN) aim
    |xtrack| ahead instead (a ~45 deg intercept). Never aim back at a point already
    passed: with short legs (arcs) that made the plane chase points sliding past abeam
    and circle (2026-10-15). A fly-over waypoint (target) stops the carry-over: aim
    along this leg's line extended past it."""
    la = LOITER_LOOKAHEAD if in_loiter(leg) else LOOKAHEAD
    t, i = max(along, 0.0) + max(la, abs(xtrack) if abs(xtrack) > REJOIN else 0.0), leg
    while True:
        A_, B_ = WPS[i - 1][0], WPS[i][0]; Li = dist(A_, B_) or 1e-9
        if t <= Li or i == len(WPS) - 1 or fly_over(WPS[i][1]):
            if not fly_over(WPS[i][1]): t = min(t, Li)
            return (A_[0] + (B_[0] - A_[0]) * t / Li, A_[1] + (B_[1] - A_[1]) * t / Li)
        t -= Li; i += 1

# Terrain look-ahead. The game's altitude is height above a flat world and never
# shows mountains, so warn when the next few km along the current heading reach
# terrain at or above the current altitude. Uses the decoded 3D terrain the planner
# saved (terrain.npz height_game: top-of-terrain ft in game units, exact); older plans
# only have briefing-map mountain cells (~1 km accurate, and not real terrain).
# Mountains are flown around, never climbed over: the warning is to turn, not climb.
TERRAIN = None
if args.route and (Path(args.route).parent / "terrain.npz").exists():
    _t = np.load(Path(args.route).parent / "terrain.npz")
    if "height_game" in _t:
        TERRAIN = dict(h=_t["height_game"], step=int(_t["height_step"]))
    else:
        _inv = np.array(json.load(open(Path(args.route).parent / "px_to_game.json"))["inverse"])
        TERRAIN = dict(m=_t["mountain"], c=float(_t["cell_km"]), sx=float(_t["sx"]), sy=float(_t["sy"]), o=_t["origin"], inv=_inv)
def terrain_ahead(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100):
    """(km, top ft) of the first terrain straight ahead (game units p, heading deg) within
    margin_ft of alt, in a corridor +-half_width_km wide; or None."""
    if TERRAIN is None: return None
    T = TERRAIN; hr = math.radians(hdg); sx, cy = math.sin(hr), -math.cos(hr)
    for d in np.arange(0.25, max_km + 0.01, 0.25):
        if "h" in T:
            tops = []
            for w in (-half_width_km, 0.0, half_width_km):
                gx, gy = p[0] + (sx * d - cy * w) * K, p[1] + (cy * d + sx * w) * K
                i, j = int(gx // T["step"]), int(gy // T["step"])
                if 0 <= j < T["h"].shape[0] and 0 <= i < T["h"].shape[1]: tops.append(int(T["h"][j, i]))
            if tops and max(tops) > 0 and max(tops) >= alt - margin_ft: return float(d), max(tops)
        else:
            gx, gy = p[0] + sx * d * K, p[1] + cy * d * K
            px, py = np.array([gx, gy, 1.0]) @ T["inv"]
            kx, ky = (px - T["o"][0]) / T["sx"], (py - T["o"][1]) / T["sy"]
            j, i = int(ky / T["c"]), int(kx / T["c"])
            if 0 <= j < T["m"].shape[0] and 0 <= i < T["m"].shape[1] and T["m"][j, i]: return float(d), None
    return None

# Runtime altitude target: write a number (ft) to logs/nav/ALT to change the hold mid-flight.
ALT_FILE = S.logs / "nav/ALT"; ALT_FILE.unlink(missing_ok=True)
# Radar sites of this mission (route dir's mission_mem.json from read_mission.py) for the display.
RADARS = []
if args.route and (Path(args.route).parent / "mission_mem.json").exists():
    RADARS = [r for r in json.load(open(Path(args.route).parent / "mission_mem.json")).get("radars", []) if r.get("system")]
# Territory (analysis/territory.py): the game scores cold-war points for every 64 s game-time
# sample spent in an enemy 32 km cell, and ejecting there means capture.
THEATER = None
if args.route and (Path(args.route).parent / "mission_mem.json").exists():
    THEATER = json.load(open(Path(args.route).parent / "mission_mem.json")).get("theater")
sys.path.insert(0, str(ROOT / "analysis"))
from territory import side as territory_side

# altitude hold (same tuning as alt_hold_mem.py)
NOSE_UP, NOSE_DOWN, KP, VS_MAX, DEADBAND, PRESS, MIN_GAP, FLOOR = "down", "up", 0.25, 15.0, 6.0, 0.5, 1.2, 250
STALL_KT = 140                    # below this (clean stall ~120 kt) the altitude hold stops pulling: see slow below
# 2026-10-23: a fighter evasion turn (3 deg/s cap) banked the plane to -32 deg at 284 ft; the nose fell 7.5 -> 1.7 deg,
# the sink hit -30 ft/s and the plane hit the ground 13 s later. Bank limits (deg) below / above BANK_LOW_ALT, and
# below LOW_ALT the altitude hold looks SINK_LOOK_S ahead and pulls at SINK_LOW_VS, and never pushes in a bank.
BANK_LIMIT_LOW, BANK_LIMIT, BANK_LOW_ALT = 20.0, 30.0, 500
LOW_ALT, SINK_LOOK_S, SINK_LOW_VS, NO_PUSH_BANK = 500, 8.0, -15.0, 15.0
SLOW_HOLD_ALT, FLAPS_SLOW_KT, FLAPS_UP_KT = 400, 140, 180   # ...above this only; flaps down below / up above these

# heading hold (sensitivity level 1, measured 2026-10-09 with no flaps at ~175-200 kt):
# a held roll key changes the steady turn rate by ~0.28 deg/s per second held, and the
# bank persists after release, so the controller sets a turn rate rather than steering.
# Turn-rate cap 0.8 deg/s (was 1.5): EMV ~10 vs ~18 at 1.5 (measured 2026-10-14), and the
# least extra exposure per degree turned. Matches the planner's TURN_RATE.
H_GAIN, H_RATE_MAX, H_PER_S = 0.1, 0.8, 0.28          # 1/s, deg/s, deg/s per second of hold
# deg/s on LOITER legs (no radar sees them, so bank is free). Was 1.6: the game clock ran 2.4-2.6x real time
# (2026-10-21), so 1.6 deg per REAL s turned only ~0.64 deg per game s = a 6.7 km circle at 186 kt; the planned 4 km
# loiter circle was impossible and nav drifted 3.9 km wide into the ridge. 2.5 -> ~4.5 km at that ratio.
H_RATE_LOITER = 2.5
H_DEAD, H_MIN, H_MAX, H_SETTLE = 0.25, 0.6, 2.5, 2.5   # long holds act more than linearly: cap at 2.5 s  # deg/s, s, s, s
roll = dict(key=None, release=0.0, next_ok=0.0); hdg_hist = []

# Time scale. The game's accelerated time (old 0x410AA: 1 normal, 2 accelerated; found
# 2026-10-14) multiplies what every key press does. All gains were tuned at x1, so rates
# are converted to x1 units, roll holds shortened (never under 0.5 s, the shortest press
# the game reliably sees) and the altitude deadband widened.
def time_mult(db):
    from f19_state import _peek, find_offset
    m = _peek(db, 0x410AA + find_offset(db), 1)
    return float(m[0]) if m and 1 <= m[0] <= 8 else 1.0
def roll_rate(now):
    pts = [(t, h) for t, h in hdg_hist if t >= now - 2.0]
    if len(pts) < 4 or pts[-1][0] - pts[0][0] < 1.0: return None
    t0, h0 = pts[0]; dh = sum(wrap(b[1] - a[1]) for a, b in zip(pts, pts[1:]))
    return dh / (pts[-1][0] - t0)

# --memlog: memory snapshots interleaved with the control loop (the automation port
# serves one client, so they must share the nav's connection). Each snapshot covers
# the flight program's data area plus the launcher's working copy of the theater
# file (radar table); a few 64-PEEK batches per loop keep every cycle short so key
# presses are never starved. Written as snap_<t>.bin + windows.json.
class MemLog:
    BATCH, PER_LOOP = 64, 4
    def __init__(self, db):
        from f19_state import find_offset
        off = find_offset(db)
        self.windows = [("egame_data", 0x3C000 + off, 0x48000 + off)]
        full = self._read(db, 0x0, 0xA0000)
        GAME = S.game_dir
        for th in ("CE", "NC", "LIBYA", "GULF"):
            wf = GAME / f"{th}.WLD"
            if not wf.exists(): continue
            w = wf.read_bytes(); i = full.find(w[0x38:0x3E])
            if i >= 0: self.windows.append((f"world_{th}", (i - 0x38) & ~0x3F, ((i - 0x38) & ~0x3F) + 0xC00))
        self.dir = S.logs / "memlog" / str(int(time.time())); self.dir.mkdir(parents=True, exist_ok=True)
        json.dump({"offset": off, "windows": self.windows}, open(self.dir / "windows.json", "w"), indent=1)
        self.addrs = [a for _, lo, hi in self.windows for a in range(lo, hi, 64)]
        self.pos, self.buf, self.t0 = 0, bytearray(), time.time()
    @staticmethod
    def _read(db, lo, hi):
        out = bytearray()
        for a in range(lo, hi, 64 * 64):
            n = min(64, (hi - a) // 64)
            for i in range(n): db._send(f"PEEK {(a + i * 64) >> 4:X}:{(a + i * 64) & 15:X} 64")
            for i in range(n): out += bytes.fromhex(db._recv_line().split(" ", 1)[1])
        return bytes(out)
    def step(self, db):
        for _ in range(self.PER_LOOP):
            chunk = self.addrs[self.pos:self.pos + self.BATCH]
            for a in chunk: db._send(f"PEEK {a >> 4:X}:{a & 15:X} 64")
            for a in chunk: self.buf += bytes.fromhex(db._recv_line().split(" ", 1)[1])
            self.pos += len(chunk)
            if self.pos >= len(self.addrs):
                try: (self.dir / f"snap_{(self.t0 + time.time()) / 2:.2f}.bin").write_bytes(self.buf)
                except OSError: pass
                self.pos, self.buf, self.t0 = 0, bytearray(), time.time()
                return

(S.logs / "nav").mkdir(exist_ok=True)
_logf = open(S.logs / f"nav/{int(time.time())}.csv", "w", newline="", buffering=1)
class _SafeLog:                                         # a full disk must never stop the autopilot
    def __init__(self, w): self.w, self.ok = w, True
    def writerow(self, r):
        if not self.ok: return
        try: self.w.writerow(r)
        except OSError: self.ok = False
log = _SafeLog(csv.writer(_logf))
log.writerow(["t", "x", "y", "alt", "spd", "hdg", "leg", "want", "xtrack_km", "to_turn_km", "press", "rate", "rate_cmd", "roll", "tscale", "side", "phase", "vs", "cycle_s", "attack_state", "flags", "throttle", "attack_key", "locked", "fuel", "fuel_key", "escape", "fighter", "fighter_near",
              "missile", "m_worst", "m_action"])
leg, hist, last_press = 1, [], 0.0
# --auto phases (scripts/phases.py): built once the route file is final (it can switch N/S below)
fuel_x = phases.FuelTransfer()
# terrain-ahead escape: turn toward the route at up to 3 deg/s, climb only as the last resort (phases.TerrainEscape)
escape = phases.TerrainEscape(lambda p_, h_, a_, mk_: terrain_ahead(p_, h_, a_, max_km=mk_, margin_ft=phases.TerrainEscape.MARGIN_FT)) \
    if TERRAIN is not None and "h" in TERRAIN else None
esc_full, esc_saved_kt = False, None
# enemy aircraft watch: evade (away / to a mountain cell) when one closes or its alert on us rises (phases.FighterWatch)
from territory import grid as _tgrid
from f19_state import read_aircraft
fighter_saved_kt = None
slow_flaps = False                     # flaps lowered by the slow-flight rule (raised again by it)
# Combat log (2026-10-23: a flight without --memlog left five missile hits unattributable): every few seconds the game's
# event list is read and new hits (type 5, by weapon), radar IDs (7, by site), visual IDs (6, by aircraft) and our own
# launches (4) go to the events file with the damage bits / count after them (throttle cap with engine damage).
import radar_game as _rg
from f19_state import read_events as _read_events, _peek_many as _pk, find_offset as _fo, DMG_BITS as _DB, DMG_COUNT as _DC
_ev_seen, _ev_next = None, 0.0
_SITE_BY_INDEX = {int(r["record"]) + 3: f"{r['place']} {r['system']}" for r in RADARS if "record" in r}
def _wname(spec):
    try: return _rg._EG[_rg.DS_FILE + 0x4C10 + 0x12 * spec:][:8].split(b"\0")[0].decode("latin1") or f"spec {spec}"
    except Exception: return f"spec {spec}"
def combat_log(now):
    global _ev_seen, _ev_next
    if now < _ev_next: return
    _ev_next = now + 3.0
    try: evs = _read_events(db)
    except Exception: return
    if _ev_seen is None: _ev_seen = len(evs); return
    new, _ev_seen = evs[_ev_seen:], len(evs)
    if not new: return
    try:
        o = _fo(db); bits, cnt = (int.from_bytes(b, "little") for b in _pk(db, [(_DB + o, 2), (_DC + o, 2)]))
    except Exception: bits = cnt = None
    dmg = "" if cnt is None else f" | damage bits {bits:#04x} count {cnt}" + (f", throttle cap {100 - 4 * cnt}%" if bits & 2 else "")
    for typ, flags, target, _ in new:
        if typ == 5: phase_event(f"{now:.0f} HIT by {_wname(target)}{dmg}")
        elif typ == 7: phase_event(f"{now:.0f} RADAR ID by {_SITE_BY_INDEX.get(target, f'site record {target}')}")
        elif typ == 6: phase_event(f"{now:.0f} VISUAL ID by {_rg.aircraft_spec(min(11, target))['name']}")
        elif typ == 4: phase_event(f"{now:.0f} weapon launched (station {target})")
_gbyte = (lambda x_, y_: int(_tgrid(THEATER)[min(15, max(0, int(y_) >> 11)), min(15, max(0, int(x_) >> 11))])) if THEATER else None
fighters = phases.FighterWatch((lambda x_, y_: (_gbyte(x_, y_) >> 2) & 3) if _gbyte else None, _gbyte)
# enemy-missile watch (scripts/missile_watch.py): built on the first world read (the mode default needs the opponent level)
mwatch, cmkeys, msnap, missile_saved_kt, missile_full, m_key = None, missile_watch.CmKeys(), None, None, False, ""
from f19_state import read_missile_world
thr = phases.Throttle(); tko = attack = landing = gun = None; strafe_q = []; attacks_done = {r for r in args.done.split(",") if r}; phase_log = []
_evf = open(S.logs / f"nav/{Path(_logf.name).stem}.events.txt", "w", buffering=1)   # mission log, written as it happens
def phase_event(m):
    phase_log.append(m)
    try: _evf.write(m + "\n")
    except OSError: pass
_attack_logged = 0                                     # attack.log lines already written
def build_attacks():
    """{role: (mode, target xy, record, launch_km, photo_km)} from the route's attack points and mission_mem.json."""
    mm = json.load(open(Path(args.route).parent / "mission_mem.json"))
    out = {}
    for t in mm["targets"]:
        role = t["role"].rstrip("?"); T = (float(t["x"]), float(t["y"]))
        kinds = {a.get("kind"): WPS[i][0] for i, a in enumerate(ATTRS) if a.get("role") == role}
        mode = "maverick" if "launch" in kinds else "photo" if "photo" in kinds else "overfly"
        out[role] = dict(mode=mode, T=T, record=t.get("record"),
                         launch_km=phases.game_dist(kinds["launch"], T) / K if "launch" in kinds else None,
                         photo_km=phases.game_dist(kinds["photo"], T) / K if "photo" in kinds else None)
    return out
ATTACKS = None
phase, start_pos, runway_hdg = "takeoff", None, None
climbed = False                                   # latched once above CLIMB_ALT: keep flying all the way down
sens_next = 0.0; dead = 0.0
# Display (scripts/nav_tui.py): built on the first frame (the route can still switch N/S before that); False = plain
# text, also after the display raised (it must never stop the autopilot).
ui = False if args.plain or not sys.stdout.isatty() else None
def close_ui():
    """Leave the display's alternate screen so what is printed next (exit messages, the mission log) stays visible."""
    if ui: ui.close()
def _release(db):
    """Release held keys, centre the stick (keypad 5: pitch and bank deflection persist), then roll out of any turn."""
    for k in ("left", "right", "up", "down", "bs"):
        try: db.key_up(k)
        except Exception: pass
    try: db.tap("kp5", 0.2)
    except Exception: pass
    if not args.hdg: return
    try:
        for _ in range(3):
            a = read_state(db); t = time.time(); time.sleep(1.5); b = read_state(db)
            r = wrap(b["hdg"] - a["hdg"]) / (time.time() - t)
            if abs(r) < 0.25 or b["alt"] < 30: break
            k = "right" if r < 0 else "left"
            db.key_down(k); time.sleep(min(6.0, abs(r) / H_PER_S)); db.key_up(k); time.sleep(2.0)
        print(f"exit: rolled level (last turn rate {r:+.2f} deg/s)", flush=True)
    except Exception as e:
        print("exit: could not roll level:", e, flush=True)

def connect():
    """Wait for the automation port (it doesn't answer while the emulator is paused)."""
    while True:
        try: return Dosbox()
        except (TimeoutError, OSError, RuntimeError):
            print("nav: automation port not answering (emulator paused?) - retrying", flush=True); time.sleep(2)
def settle(db, secs=6.0):
    """Hands off until the game's state reads sane and consistent for `secs` (right after a save-state load the reads
    were garbage/zero for a while, and the user asked nav to get its bearings first, 2026-10-18): position nonzero and
    moving plausibly, altitude < 40000, airspeed < 900. Releases every key first (a key left down survives loads)."""
    for k in ("left", "right", "up", "down"): db.key_up(k)
    good_since, last, centred = None, None, False
    while True:
        try:
            from f19_state import _offset
            s = read_state(db); now = time.time()
            ok = s["x"] and s["y"] and s["alt"] < 40000 and s["spd"] < 900
            if ok and last is not None:
                jump = math.hypot(s["x"] - last[1]["x"], s["y"] - last[1]["y"]) / 32 / K      # km since last read
                ok = jump < 0.05 * max(1.0, now - last[0]) * 10                                 # < ~0.5 km/s
            if ok: good_since = good_since or now
            else: good_since = None; _offset.clear()                                          # re-locate the data segment
            last = (now, s)
            print(f"\033[H\033[2J  F-19 NAV: getting bearings - hands off ({0 if good_since is None else now - good_since:.0f}/{secs:.0f} s)"
                  f"   alt {s['alt']} spd {s['spd']}", flush=True)
            # The keyboard stick keeps its deflection (pitch like roll): a save state made while nav was flying, or a nav
            # that stopped mid-correction, leaves it deflected and the plane dives with nobody pressing anything
            # (2026-10-18). Keypad 5 = "Centered" (Quick Reference Card). Centre it once the state reads sane.
            if good_since and not centred: db.release_arrows(); db.tap("kp5", 0.2); centred = True
            if good_since and now - good_since >= secs: return
        except RuntimeError as e:
            print(f"  F-19 NAV: waiting for the game ({e})", flush=True); good_since = None
            from f19_state import _offset; _offset.clear()
        time.sleep(0.5)
with connect() as db:
  settle(db)
  try:
      memlog = MemLog(db) if args.memlog else None
      if WPS is None:                                        # start -> game WP1..WP4, skipping repeats
          st0 = read_state(db); gw0, _ = read_waypoints(db)
          WPS = [((st0["x"] / 32, st0["y"] / 32), "start")]
          for k, w in enumerate(gw0):
              if dist(WPS[-1][0], w) > ARRIVE: WPS.append((tuple(map(float, w)), GAME_WP_NAMES[k]))
      s0 = read_state(db)
      # The route must belong to the mission in memory: its target set must match the one
      # the launcher holds (a stale route from an earlier mission crashed a flight 2026-10-14).
      mm = Path(args.route).parent / "mission_mem.json" if args.route else None
      if mm and mm.exists():
          from read_mission import parse_all
          planned = json.load(open(mm))
          # Memory can hold more than one mission copy, including a stale one with the same
          # place names (2026-10-15): accept the route if any copy matches it.
          copies = parse_all(MemLog._read(db, 0x0, 0xA0000), planned.get("theater", "NC"))
          # Compare the generated target slots: in flight the briefing block (real targets) is gone.
          key = lambda m: sorted((t["x"], t["y"]) for t in m.get("slots", m["targets"]))
          live = next((c for c in copies if key(c) == key(planned)), copies[-1] if copies else {"targets": []})
          if key(live) != key(planned):
              # Same place names recur across missions at different coordinates, so show both
              # and point at any plan directory that does match the mission in memory.
              match = [str(p.parent) for p in sorted(Path(args.route).parent.parent.glob("*/mission_mem.json"))
                       if any(key(json.load(open(p))) == key(c) for c in copies)]
              sys.exit(f"nav: {mm.parent} was planned for targets {[(t['place'], t['x'], t['y']) for t in planned['targets']]}\n"
                       f"     but the mission in memory is {[(t['place'], t['x'], t['y']) for t in live['targets']]}\n"
                       f"     - wrong route file? Not flying it."
                       + (f" Plan dirs matching this mission: {', '.join(match)}" if match else ""))
      # The planner plans the one direction the game uses (the theater decides); only a
      # takeoff_heading "both" plan has route_N.json + route_S.json: fly the one that matches the plane.
      if args.route and s0["alt"] < 150 and len(WPS) > 1 and abs(wrap(s0["hdg"] - bearing(WPS[0][0], WPS[1][0]))) > 90:
          rp = Path(args.route); other = rp.with_name(rp.name.replace("_N.", "_X.").replace("_S.", "_N.").replace("_X.", "_S."))
          if other == rp or not other.exists():
              sys.exit(f"nav: plane faces {s0['hdg']:.0f} on the runway but {rp.name} takes off on {bearing(WPS[0][0], WPS[1][0]):.0f}"
                       f" and there is no {other.name} - replan with takeoff_heading for this direction.")
          args.route = str(other); WPS, ATTRS = load_route(other)
          print(f"nav: plane faces {s0['hdg']:.0f} on the runway - flying {other.name}", flush=True)
      if args.route and s0["alt"] < 150 and dist((s0["x"] / 32, s0["y"] / 32), WPS[0][0]) > 10 * K:
          sys.exit(f"nav: on the ground {dist((s0['x'] / 32, s0['y'] / 32), WPS[0][0]) / K:.0f} km from the route's takeoff "
                   f"point ({WPS[0][1]}) - wrong route, or takeoff/landing swapped? Not flying it.")
      if args.auto and args.land_now:
          # refuse a landing from the wrong side or too far: a save state that didn't load left the plane 53 km past
          # the runway and the landing phase went straight to a go-around at full power (2026-10-18)
          ia_ = next(i for i in range(len(WPS)) if WPS[i][1] == "Approach fix")
          fx, rw = WPS[ia_][0], WPS[-1][0]; p0 = (s0["x"] / 32, s0["y"] / 32)
          ux_, uy_ = (rw[0] - fx[0]) / dist(fx, rw), (rw[1] - fx[1]) / dist(fx, rw)
          before = (rw[0] - p0[0]) * ux_ + (rw[1] - p0[1]) * uy_       # units still to go along the approach
          if before < 2 * K or dist(p0, rw) > 60 * K:
              sys.exit(f"nav --land-now: plane is {dist(p0, rw) / K:.0f} km from the runway, {before / K:+.0f} km along the "
                       f"approach (needs 2..60 km before it) - not starting a landing from here")
      if args.auto:
          ATTACKS = build_attacks()
          # target records missing from older mission files: the in-flight briefing blocks have them
          from f19_state import read_briefing, read_target
          for (typ, rec), A in zip(read_briefing(db), sorted(ATTACKS.values(), key=lambda A: A is not ATTACKS.get("primary"))):
              if A["record"] is None and read_target(db, rec)[:2] == tuple(map(int, A["T"])): A["record"] = rec
          print("nav --auto attacks: " + ", ".join(f"{r} {a['mode']}" for r, a in ATTACKS.items()), flush=True)
      if s0["alt"] > CLIMB_ALT and len(WPS) > 2:              # started in the air: join the nearest leg
          p0 = (s0["x"] / 32, s0["y"] / 32)
          def seg_d(i):
              (ax, ay), (bx, by) = WPS[i - 1][0], WPS[i][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
              t = max(0.0, min(1.0, ((p0[0] - ax) * (bx - ax) + (p0[1] - ay) * (by - ay)) / L2))
              return math.hypot(p0[0] - ax - t * (bx - ax), p0[1] - ay - t * (by - ay))
          leg = min(range(1, len(WPS)), key=lambda i: (round(seg_d(i) / K), -i))   # nearest (to 1 km), later on ties
          phase = "route"
          if args.auto: thr.target = float(json.load(open(args.route)).get("profile", {}).get("cruise_kt") or 190)   # cruise speed
      if args.strafe:
          from f19_state import read_sites
          live = read_sites(db); p0 = (s0["x"] / 32, s0["y"] / 32)
          for w in args.strafe.split(","):
              if w == "nearest":
                  rec = min((r for r in live if r not in strafe_q), key=lambda r: dist(p0, live[r]), default=None)
              else: rec = int(w)
              if rec is None or rec not in live: print(f"nav --strafe: record {w} is not a live radar site - skipped", flush=True); continue
              strafe_q.append(rec)
          print("nav --strafe: " + ", ".join(f"record {r} at {live[r]} ({dist(p0, live[r]) / K:.0f} km)" for r in strafe_q), flush=True)
          if not strafe_q: sys.exit("nav --strafe: nothing to attack")
      while True:
          s = read_state(db); now = time.time()
          if s["spd"] > 900 or (s["x"] == 0 and s["y"] == 0):     # crashed / not in flight: memory is garbage, hands off
              if not dead: _release(db); dead = now
              # Out of flight for good (crash, mission over): exit. A nav left waiting picked up the next
              # mission with this one's state (phase route, nose-up presses) and wrecked it (2026-10-17).
              # Short gaps (a save state loading) still just wait.
              if now - dead > 10: sys.exit("nav: not in flight for 10 s (crashed or mission over) - exiting")
              print("\033[H\033[2J  F-19 NAV: not in flight (crashed or between missions) - hands off; exits after 10 s.", flush=True)
              time.sleep(1.0); continue
          dead = 0.0
          gw, sel = read_waypoints(db)
          p = (s["x"] / 32, s["y"] / 32)
          a, b = WPS[leg - 1][0], WPS[leg][0]
          L = dist(a, b) or 1e-9; ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
          along = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
          xtrack = (p[0] - a[0]) * -uy + (p[1] - a[1]) * ux
          arrive = OVERFLY if fly_over(WPS[leg][1]) else ARRIVE
          if (dist(p, b) < arrive or along > L) and leg < len(WPS) - 1:
              leg += 1; continue
          j = next_leg(leg, p, window=2 if in_loiter(leg) else 8)   # loiter laps overlap: never skip a lap
          if j != leg: leg = j; continue
          # aim LOOKAHEAD ahead along the route (see route_aim); off the line, further ahead
          aim = route_aim(leg, along, xtrack)
          want = bearing(p, aim) if args.fixed is None else args.fixed % 360
          if start_pos is None: start_pos = p
          if phase == "takeoff":
              if runway_hdg is None and s["alt"] < 150: runway_hdg = s["hdg"]
              if (dist(p, start_pos) >= CLIMB_DIST and s["alt"] >= CLIMB_ALT) or (runway_hdg is None and s["alt"] >= CLIMB_ALT):
                  phase = "route"
                  while WPS[leg][1] == "Climb-out" and leg < len(WPS) - 1: leg += 1
              elif runway_hdg is not None:
                  want = runway_hdg                              # straight ahead along the runway
          ov = {}                                               # this cycle's phase overrides
          if (args.auto or args.strafe) and read_sensitivity(db) != 1:
              pass                                              # every phase waits for keyboard sensitivity 1 (set below with Ins)
          elif args.auto or args.strafe:
              f = read_flight(db)
              if s["alt"] > 50:                                          # Extra Fuel: fuel < 500 lb and a tank left
                  fev = fuel_x.step(db, f, now)
                  if fev: phase_event(f"{now:.0f} {fev}")
              if args.auto and tko is None and phase == "takeoff" and s["alt"] < 50:     # on the runway: automatic takeoff
                  _pf = json.load(open(args.route)).get("profile", {})
                  tko = phases.Takeoff(thr, float(_pf.get("cruise_kt") or 190), float(_pf.get("cruise_ft") or 450))
              if tko is not None and not tko.finished:                    # until gear and flaps are up, even past Climb-out
                  ov = tko.step(db, s, f, p, now, time_mult(db))
                  if ov.get("done"): tko.finished = True; phase_event(f"{now:.0f} takeoff done")
              la_ = leg_attrs(leg); role = la_.get("role")
              if phase == "route" and args.attack_now and args.attack_now not in attacks_done and attack is None:
                  role = args.attack_now; la_ = {"kind": "launch"}            # forced: attack this role now
              if args.auto and phase == "route" and role and role not in attacks_done and attack is None and (
                      la_.get("kind") in ("launch", "photo") or (la_.get("kind") == "runin" and dist(p, b) < 10 * K)
                      or (la_.get("kind") == "overfly" and dist(p, b) < 5 * K)):
                  A = ATTACKS[role]
                  attack = phases.Attack(role, A["mode"], A["T"], A["record"], A["launch_km"], A["photo_km"], thr)
                  phase = "attack"; _attack_logged = 0; phase_event(f"{now:.0f} {role} attack start ({A['mode']}, record {A['record']})")
              m_hold = mwatch is not None and mwatch.mode == "act" and mwatch.state in ("evade", "survival")
              if phase == "attack" and fuel_x.busy:
                  ov = {"want": s["hdg"]}                                # fuel transfer has the station keys: hold heading
              elif phase == "attack" and m_hold:
                  # missile threat: the run / photo pass waits (an open bay is +24 EMV) and resumes when MissileWatch clears
                  ov = {"want": s["hdg"], "banner": f"  ATTACK {attack.role} ON HOLD: missile evasion"}
                  if f["flags"] & phases.BAY: phases.set_toggle(db, f, phases.BAY, False, "8")
              elif phase == "attack":
                  ov = attack.step(db, s, f, p, now, time_mult(db))
                  for m in attack.log[_attack_logged:]: phase_event(f"{now:.0f} {attack.role}: {m}")
                  _attack_logged = len(attack.log)
                  if ov.get("done"):
                      phase_event(f"{now:.0f} {attack.role} attack done"); _attack_logged = 0
                      # back to cruise speed: left off, the throttle stayed at the attack setting and the plane reached
                      # 370-390 kt, too fast for the route's turn onto the runway axis (full run, 2026-10-18)
                      attacks_done.add(attack.role); attack = None; phase = "route"
                      thr.target = float(json.load(open(args.route)).get("profile", {}).get("cruise_kt") or 190)
              if phase == "route" and strafe_q and gun is None:          # gun attack on the next radar site
                  from f19_state import read_target
                  x_, y_, dead_ = read_target(db, strafe_q[0])
                  if dead_: phase_event(f"{now:.0f} record {strafe_q.pop(0)} already destroyed")
                  else: gun = phases.GunAttack(strafe_q[0], (float(x_), float(y_)), thr); phase = "strafe"
              if phase == "strafe":
                  ov = gun.step(db, s, f, p, now, time_mult(db))
                  if ov.get("done"):
                      gun.abort(db); [phase_event(f"{now:.0f} gun {gun.record}: {m}") for m in gun.log]
                      strafe_q.pop(0); gun = None; phase = "route"
                      if not strafe_q and not args.auto:
                          close_ui(); _release(db); print("\n".join(["nav --strafe: done - you have control"] + phase_log), flush=True)
                          sys.exit(0)
              if args.auto and phase == "route" and (args.land_now or WPS[leg][1] == "Final" or WPS[leg][1].startswith("Landing")):
                  ia = next(i for i in range(len(WPS)) if WPS[i][1] == "Approach fix")
                  landing = phases.Landing(WPS[-1][0], WPS[ia][0], thr); phase = "landing"
              if phase == "landing":
                  ov = landing.step(db, s, f, p, now, time_mult(db))
                  if ov.get("done"):
                      phase_event(f"{now:.0f} landing {landing.state}")
                      phase = "landed" if landing.state == "stopped" else "handover"
                      _release(db); args.hdg = False; args.alt = None
              if "want" in ov: want = ov["want"]
          elif phase == "route" and WPS[leg][1].startswith("Landing") and args.fixed is None:
              phase = "handover"; _release(db); args.hdg = False; args.alt = None
          if phase in ("route", "attack", "strafe") and s["alt"] > 100 and args.hdg:
              try: _ac = read_aircraft(db)
              except Exception: _ac = []
              fov, fevs = fighters.step(s, p, now, want, _ac, f["opp"] if args.auto and "f" in dir() else 2)
              for m in fevs: phase_event(f"{now:.0f} {m}")
              if fov:
                  ov = {**ov, **fov}; want = fov["want"]
                  if args.auto and not thr.guarding and thr.target != fov["speed_kt"]:
                      fighter_saved_kt = thr.target if fighter_saved_kt is None else fighter_saved_kt
                      thr.target = fov["speed_kt"]                      # slow: a doppler sees less of a slow target
              elif fighter_saved_kt is not None and args.auto:
                  thr.target = fighter_saved_kt; fighter_saved_kt = None
          m_key = ""
          if args.missile_watch != "off" and phase in ("route", "attack", "strafe") and s["alt"] > 100:
              try:
                  msnap = read_missile_world(db, full=missile_full)
                  if msnap["missiles"] and not missile_full: msnap = read_missile_world(db, full=True)
                  missile_full = bool(msnap["missiles"])
              except Exception as e:
                  msnap = None; phase_event(f"{now:.0f} missile watch: world read failed ({e!r})")
              if msnap is not None:
                  if mwatch is None:
                      mmode = args.missile_watch or ("act" if msnap["world"]["opp"] >= 3 else "observe")
                      mwatch = missile_watch.MissileWatch(mmode, _gbyte)
                      phase_event(f"{now:.0f} missile watch: {mmode} (opp {msnap['world']['opp']})")
                  mov, mevs = mwatch.step(s, p, now, want, msnap)
                  for m in mevs: phase_event(f"{now:.0f} {m}")
                  ck = cmkeys.step(db, msnap, now)
                  if ck: phase_event(f"{now:.0f} {ck}"); m_key = ck.split(" ")[0]
                  if cmkeys.failed: mwatch.block(cmkeys.failed); cmkeys.failed = None   # key never took: plan without it
                  if mwatch.mode == "act":
                      if mov.get("cm"):
                          ck = cmkeys.request(db, mov["cm"], msnap, now); phase_event(f"{now:.0f} {ck}"); m_key = missile_watch.KEYS[mov["cm"]]
                      if mov.get("jam_on"):
                          ck = cmkeys.jam(db, mov["jam_on"], True, msnap, now)
                          if ck: phase_event(f"{now:.0f} {ck}")
                      for j in mov.get("jam_off", []):
                          ck = cmkeys.jam(db, j, False, msnap, now)
                          if ck: phase_event(f"{now:.0f} {ck}")
                  if "want" in mov:
                      ov = {**ov, **{k: v for k, v in mov.items() if k in ("want", "rate_cap", "bank_max", "alt_min", "missile")}}
                      want = mov["want"]
                      if args.auto and not thr.guarding:
                          if mov.get("survival"):
                              if thr.target is not None: missile_saved_kt = missile_saved_kt or thr.target; thr.max(db)
                          elif thr.target != mov["speed_kt"]:
                              missile_saved_kt = thr.target if missile_saved_kt is None else missile_saved_kt
                              thr.target = mov["speed_kt"]
                  elif missile_saved_kt is not None and args.auto:
                      thr.target = fighters.EVADE_KT if fighters.state == "evade" else (fighter_saved_kt or missile_saved_kt)
                      missile_saved_kt = None
          if escape is not None and phase in ("route", "attack", "strafe") and s["alt"] > 100 and (args.hdg or args.alt is not None):
              eov, eev = escape.step(s, p, now, want, time_mult(db))
              if eev: phase_event(f"{now:.0f} {eev}")
              if eov:
                  ov = {**ov, **eov}; want = eov["want"]
                  if eov.get("throttle_max") and not esc_full and args.auto and s["spd"] < 170:
                      esc_saved_kt = thr.target; thr.max(db); esc_full = True     # full power for the climb
              elif esc_full:
                  thr.target = esc_saved_kt or float(json.load(open(args.route)).get("profile", {}).get("cruise_kt") or 190)
                  esc_full = False
          turn = wrap(want - s["hdg"])
          press = ""; rate = rate_cmd = None; roll_evt = ""
          # Never act on the ground (takeoff roll included), but once the plane has climbed above
          # CLIMB_ALT stay in control at any height: the old "alt > 150" test let go below 150 ft
          # and the 2026-10-16 flight sank into the ground with nothing pressed.
          climbed = climbed or s["alt"] > CLIMB_ALT
          airborne = climbed or s["alt"] > 150
          if airborne: combat_log(now)          # after airborne is set: before it, the first cycle raised NameError
          # Keyboard sensitivity must be level 1 (all tuning assumes it; level 3 crashed the
          # plane 2026-10-14). If it isn't: let go of the controls and press Insert until it is.
          sens = read_sensitivity(db); sens_ok = sens == 1
          if not sens_ok and (args.hdg or args.alt is not None):
              if roll["key"]: db.key_up(roll["key"]); roll["key"] = None
              if now >= sens_next:
                  db.tap("ins", 0.2); sens_next = time.time() + 1.5   # 0.2 s: a longer press auto-repeats (two steps)
                  log.writerow([f"{now:.2f}", "", "", "", "", "", "", "", "", "", f"sens {sens}->ins"])
          ts = time_mult(db)
          if args.hdg:
              hdg_hist.append((now, s["hdg"])); hdg_hist[:] = [h for h in hdg_hist if h[0] >= now - 3.0]
              if roll["key"] and now >= roll["release"]:
                  db.key_up(roll["key"]); roll["key"] = None; roll["next_ok"] = now + max(1.2, H_SETTLE / ts)
              rate = roll_rate(now); rate = None if rate is None else rate / ts; cap = H_RATE_LOITER if in_loiter(leg) else H_RATE_MAX; rate_cmd = max(-cap, min(cap, H_GAIN * turn))
              cap = ov.get("rate_cap", cap); rate_cmd = max(-cap, min(cap, H_GAIN * turn))
              # stuck-key watchdog: a turn this fast with no roll key held by us means the game still sees one
              # held (a lost break code; 2026-10-17 spiral). Re-send both releases.
              if rate is not None and abs(rate) > 2.5 and roll["key"] is None and airborne:
                  db.key_up("left"); db.key_up("right"); roll_evt = "UNSTICK"
              if ov.get("roll_hold") is False and roll["key"]: db.key_up(roll["key"]); roll["key"] = None
              # bank limit: never deeper than blim; past it (+5), roll back out whatever the turn wants
              bank = f["roll"] if args.auto and "f" in dir() else None
              blim = BANK_LIMIT_LOW if s["alt"] < BANK_LOW_ALT else BANK_LIMIT
              if "bank_max" in ov and s["alt"] >= BANK_LOW_ALT: blim = max(blim, ov["bank_max"])   # missile evasion, high enough
              deeper = None if bank is None else ("left" if bank < 0 else "right")
              if bank is not None and airborne and ov.get("roll_hold", True) and abs(bank) > blim:
                  if roll["key"] == deeper: db.key_up(roll["key"]); roll["key"] = None; roll_evt = f"BANK LIMIT {bank:+.0f}"
                  if roll["key"] is None and abs(bank) > blim + 5 and now >= roll.get("bank_next", 0.0):
                      k = "right" if bank < 0 else "left"
                      db.key_down(k); roll.update(key=k, release=now + 0.8 / ts, bank_next=now + 1.5 / ts); roll_evt = f"BANK {bank:+.0f} -> {k}"
              if sens_ok and airborne and ov.get("roll_hold", True) and rate is not None and roll["key"] is None and now >= roll["next_ok"]:
                  err = rate_cmd - rate
                  if abs(err) > H_DEAD:
                      k = "right" if err > 0 else "left"; hold = max(0.5, max(H_MIN, min(H_MAX, abs(err) / H_PER_S)) / ts)
                      if bank is not None and k == deeper and abs(bank) > 0.8 * blim:
                          roll_evt = f"bank {bank:+.0f}: no deeper"                    # at the limit: hold the bank
                      else:
                          db.key_down(k); roll.update(key=k, release=now + hold); roll_evt = f"{k} {hold:.1f}s"
          if args.alt is not None and ALT_FILE.exists():
              try: args.alt = float(ALT_FILE.read_text().split()[0])
              except (ValueError, IndexError, OSError): pass
          # a probe leg may set its own altitude (route alt_ft); --alt / logs/nav/ALT otherwise
          hold_alt = leg_attrs(leg).get("alt_ft", args.alt) if args.alt is not None else None
          if hold_alt is not None and "alt" in ov: hold_alt = ov["alt"]
          if hold_alt is not None and "alt_cap" in ov: hold_alt = min(hold_alt, ov["alt_cap"])
          if hold_alt is not None and "alt_min" in ov: hold_alt = max(hold_alt, ov["alt_min"])     # missile evasion: no turn below it
          vs_max = ov.get("vs_max", VS_MAX)
          vs = None
          if hold_alt is not None and ov.get("alt_hold", True):
              # Vertical speed over the last ~1.5 s. It used to need > 3 samples in 1 s and read 0 otherwise:
              # when --auto slowed the loop to ~1 s a cycle, vs stayed 0 while the plane dived at 90 ft/s,
              # the hold kept pushing the nose down and flew into the ground (2026-10-17). No estimate -> no press.
              hist.append((now, s["alt"])); hist = [h for h in hist if h[0] >= now - 1.5]
              span = hist[-1][0] - hist[0][0]
              vs = (hist[-1][1] - hist[0][1]) / span / ts if len(hist) >= 2 and span >= 0.4 else None
              # descents near the ground at most 8 ft/s (a 15 ft/s descent at 340 kt overshot to -40 ft/s, 2026-10-18)
              dn = min(vs_max, 8.0) if s["alt"] < 700 and "vs_max" not in ov else vs_max
              tvs = max(-dn, min(vs_max, KP * (hold_alt - s["alt"])))
              if "vs_target" in ov: tvs = ov["vs_target"]          # landing short final: a sink rate at the aim point
              dband = ov.get("deadband", DEADBAND)
              gap = MIN_GAP * max(1.0, s["spd"] / 200.0)          # pitch taps act harder at speed: space them out
              # near the stall a nose-up press only bleeds speed (2026-10-19: UP! at 130 -> 84 kt into the ground):
              # hold the attitude and let the throttle guard's full power recover, except right above the ground
              # ...but only high up and not sinking: 2026-10-22 an engine-damaged plane (throttle capped at 44 %) sank at
              # -11 ft/s from 170 ft into the ground at 120-140 kt with this rule holding the nose. Near the ground or
              # sinking, the hold pulls as usual, and the flaps (stall 120 -> 90 kt) come down below FLAPS_SLOW_KT.
              slow = phase in ("route", "attack", "strafe") and s["spd"] < STALL_KT and s["alt"] > SLOW_HOLD_ALT and (vs is None or vs > -5)
              if airborne and sens_ok and vs is not None and slow:
                  press = "slow: no pull"
              elif airborne and sens_ok and vs is not None:    # never on the ground
                  floor = ov.get("floor", max(80, min(FLOOR, hold_alt - 100)))   # guard below the target, not above it
                  look = SINK_LOOK_S if s["alt"] < LOW_ALT else 5.0
                  predict = "floor" not in ov and s["alt"] + look * vs < floor              # not on a landing (floor 0)
                  banked = args.auto and "f" in dir() and abs(f["roll"]) > NO_PUSH_BANK
                  if (s["alt"] < floor or predict) and vs < 5 and now - last_press >= 0.8:
                      db.tap(NOSE_UP, 0.6); press = "UP!"                   # predictive: 5 s (8 s low down) of the sink
                  elif vs < -40 and s["alt"] < 1000 + 15 * -vs and now - last_press >= 0.8: db.tap(NOSE_UP, 0.6); press = "SINK!"
                  elif "floor" not in ov and s["alt"] < LOW_ALT and vs < SINK_LOW_VS and now - last_press >= 0.8:
                      db.tap(NOSE_UP, 0.6); press = "SINK!"                 # low down, a fast sink is caught at once
                  elif now - last_press >= gap:
                      if vs < tvs - dband * ts: db.tap(NOSE_UP, PRESS); press = "up"
                      elif vs > tvs + dband * ts and not (phase == "takeoff" and s["alt"] < CLIMB_ALT) and not banked:
                          db.tap(NOSE_DOWN, PRESS); press = "down"
              if press and press != "slow: no pull": last_press = time.time()
          if args.auto and airborne and phase in ("route", "attack", "strafe") and (tko is None or tko.finished):
              # slow flight (engine damage, a sag): flaps down cut the stall from ~120 to ~90 kt; up again once fast
              if s["spd"] < FLAPS_SLOW_KT and not f["flags"] & phases.FLAPS:
                  if phases.set_toggle(db, f, phases.FLAPS, True, "9") and not slow_flaps:
                      phase_event(f"{now:.0f} slow ({s['spd']} kt): flaps down"); slow_flaps = True
              elif slow_flaps and s["spd"] > FLAPS_UP_KT and f["flags"] & phases.FLAPS:
                  if phases.set_toggle(db, f, phases.FLAPS, False, "9"):
                      phase_event(f"{now:.0f} flaps up ({s['spd']} kt)"); slow_flaps = False
              g = thr.guard(db, s, f, now, ts)
              if g: press = (press + " " + g).strip(); phase_event(f"{now:.0f} {g}")
          # no throttle presses while an attack holds the nose (a press blocks ~0.6 s: the pop overshot to +23.5)
          if args.auto and airborne and phase not in ("landed", "handover") and ov.get("alt_hold", True): thr_evt = thr.step(db, s, now, ts, allow_up=not ov.get("no_thr_up")); press = (press + " " + thr_evt).strip()
          log.writerow([f"{now:.2f}", f"{p[0]:.0f}", f"{p[1]:.0f}", s["alt"], s["spd"], f"{s['hdg']:.1f}", leg, f"{want:.1f}", f"{xtrack / K:.2f}", f"{dist(p, b) / K:.2f}", press,
                        "" if rate is None else f"{rate:.2f}", "" if rate_cmd is None else f"{rate_cmd:.2f}", roll_evt, f"{ts:.2f}",
                        territory_side(THEATER, *p) if THEATER else "", phase,
                        "" if hold_alt is None or vs is None else f"{vs:.1f}", f"{time.time() - now:.2f}",
                        attack.state if attack is not None else gun.state if gun is not None else "", f"{f['flags']:#x}" if args.auto and "f" in dir() else "",
                        f["throttle"] if args.auto and "f" in dir() else "",
                        attack.key if attack is not None else "", f["locked"] if args.auto and "f" in dir() else "",
                        f["fuel"] if args.auto and "f" in dir() else "", fuel_x.key, ov.get("escape", ""),
                        ov.get("fighter", ""), fighters.nearest,
                        len(msnap["missiles"]) if msnap is not None else "", mwatch.worst if mwatch else "",
                        (mwatch.action + (" key " + m_key if m_key else "")).strip() if mwatch else ""])
          fuel_x.key = ""
          if attack is not None: attack.key = ""
          nxt = next((j for j in range(leg, len(WPS)) if WPS[j][1]), len(WPS) - 1)
          arrow = "<<< LEFT" if turn < -3 else ("RIGHT >>>" if turn > 3 else "on course")
          tahead = terrain_ahead(p, s["hdg"], s["alt"])
          banner = {"takeoff": "  TAKEOFF: runway heading, climbing", "route": "", "attack": "", "landing": "", "strafe": "",
                    "landed": "  LANDED - stopped on the runway, autopilot off",
                    "handover": "  FINAL (6 km out, aligned): autopilot OFF - you have control, land"}[phase]
          if ov.get("banner"): banner = (ov["banner"] + "\n" + banner).rstrip("\n")
          if mwatch is not None and mwatch.worst:
              banner = (f"  MISSILE ({mwatch.mode}): {mwatch.worst}" + (f"  ->  {mwatch.action}" if mwatch.action else "") + "\n" + banner).rstrip("\n")
          if args.auto and phase == "handover" and landing is not None: banner = "  GO-AROUND: landing aborted - autopilot OFF, you have control"
          if tahead is not None:
              top = "" if tahead[1] is None else f" (top {tahead[1]} ft)"
              banner = f"  !!! MOUNTAIN {tahead[0]:.1f} km AHEAD{top} - TURN AWAY, altimeter will NOT show it !!!" + ("\n" + banner if banner else "")
          la = leg_attrs(leg)
          if la.get("action"):                               # planner attack cue for the waypoint ahead
              banner = (banner + "\n" if banner else "") + f"  AT {WPS[leg][1]} ({dist(p, b) / K:.1f} km): {la['action']}"
          if la.get("probe"):
              banner = (banner + "\n" if banner else "") + f"  TEST: {la['probe']}" + (f"   SET SPEED {la['speed_kt']} kt" if la.get("speed_kt") else "")
          out = ["\033[H\033[2J  F-19 NAV" + (f"   [alt hold {hold_alt:.0f} ft]" if hold_alt is not None else "") + ("   [HDG HOLD]" if args.hdg else ""), banner,
                 f"  leg {leg}/{len(WPS) - 1}   next turn in {dist(p, b) / K:5.1f} km",
                 f"  FLY HEADING  {want:4.0f}    now {s['hdg']:4.0f}   {arrow} {abs(turn):3.0f}",
                 f"  off route    {abs(xtrack) / K:5.1f} km {'right' if xtrack > 0 else 'left'}", "",
                 f"  next on route: {WPS[nxt][1]}",
                 f"  game waypoints (exact):"]
          for k in range(4):
              out.append(f"   {'>' if k == sel else ' '} {GAME_WP_NAMES[k]:<16} {game_dist(p, gw[k]):6.1f} km  brg {bearing(p, gw[k]):4.0f}")
          out += ["", f"  alt {s['alt']:5d} ft   spd {s['spd']:3d} kt" + (f"   TIME x{ts:.1f}" if ts > 1.3 else "")
                  + ("" if sens_ok else f"   KYBD SENSITIVITY {sens} -> setting 1")]
          if THEATER:
              sd = territory_side(THEATER, *p)
              out += ["", "  territory: " + {"enemy": "ENEMY - behind the lines (scoring; ejecting here = capture)",
                                              "friendly": "friendly", "neutral": "neutral"}.get(sd, sd)]
          if RADARS:
              near = sorted(RADARS, key=lambda r: dist(p, (r["x"], r["y"])))[:4]
              out += ["", "  nearest radars:"] + [f"    {r['place']:<13} {r['system']:<6} {r['type']} {dist(p, (r['x'], r['y'])) / K:6.1f} km"
                                                 f" / {r['range_km']:3d}  brg {bearing(p, (r['x'], r['y'])):4.0f}" for r in near]
          if phase_log: out += ["", "  mission log:"] + [f"    {m}" for m in phase_log[-6:]]
          if args.hdg and rate is not None:
              out.append(f"  turn rate {rate:+5.2f} deg/s  (want {rate_cmd:+5.2f})  {'rolling ' + roll['key'] if roll['key'] else ''}")
          if ui is None:
              import nav_tui; ui = nav_tui.UI(THEATER, args.route, wps=WPS)
          if ui:
              try:
                  ui.frame(db, dict(p=p, alt=s["alt"], kt=s["spd"], hdg=s["hdg"], phase=phase, banner=banner, hold_alt=hold_alt,
                                    hdg_hold=args.hdg, wps=WPS, attrs=ATTRS, leg=leg, want=want, turn=turn, aim=aim,
                                    to_turn_km=dist(p, b) / K, xtrack_km=xtrack / K, next_name=WPS[nxt][1], next_wp=nxt, gw=gw, sel=sel,
                                    ts=ts, vs=vs, territory=territory_side(THEATER, *p) if THEATER else None, rate=rate, rate_cmd=rate_cmd,
                                    rolling=f"rolling {roll['key']}" if roll["key"] else "", phase_log=phase_log,
                                    sens_msg="" if sens_ok else f"KYBD SENSITIVITY {sens} -> setting 1",
                                    flight=f if (args.auto or args.strafe) and "f" in dir() else None,
                                    route_left_km=(dist(p, b) + sum(dist(WPS[j][0], WPS[j + 1][0]) for j in range(leg, len(WPS) - 1))) / K))
              except Exception as e:
                  close_ui(); ui = False; print(f"nav: display failed ({e!r}) - plain text from here", flush=True)
          if not ui: print("\n".join(out), flush=True)
          if memlog: memlog.step(db)
          time.sleep(0.15 if memlog is None else 0.05)
  finally:
    close_ui()
    _release(db)
