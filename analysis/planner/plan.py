"""Plan a route from a plan spec, deterministically.

  python analysis/planner <plan_dir> [--spec plan.json]

<plan_dir>/plan.json:
  {
    "theater": "CE",
    "mission": "mission_mem.json",            # scripts/read_mission.py output (default)
    "takeoff": "Gutersloh" | {"x": .., "y": .., "label": "Carrier", "carrier": true},
    "takeoff_heading": 0 | 180 | "both",       # default: what the game will use (game_takeoff_heading: the theater
                                               # decides; carriers +5.6). "both" plans N and S, for experiments only
    "landing": "Leck",
    "approach": "auto" | "N" | "S",            # side of the N-S runway the approach fix is on
    "target_order": "primary-first" | "auto",  # auto: cheaper order (forced when memory can't tell roles)
    "attack": {"primary": "maverick" | "photo" | "overfly", ...},   # default from the briefing task (see below)
    "profile": "balanced",                     # profile.PRESETS
    "overrides": {"w_exposure": 4},            # any profile.DEFAULTS key
    "probes": [ ... ],                         # probes.py
    "settings": {"opponent": "veteran", "roe": 0, "night": false, "war": 0, ...}
                                               # radar rules (opponent, war), SA-14 cells (night), score.py
    "sa14_live": [[7, 13], ...],               # optional: SA-14 cells nav read at takeoff (default: all of the file's)
    "mode": "mission" | "mountain_loiter",     # mountain_loiter: no targets, takeoff -> loiter -> landing
    "loiter_cell": [4, 11],                    # mountain_loiter: the cell (default: best quiet cell)
    "extra_fuel": 0                            # Extra Fuel tanks in the loadout (0-3), decided after the briefing and
                                               # the ordnance screen: +1900 lb each, nav transfers one whenever fuel < 500 lb
  }
Fuel, speed per leg and the cold-war samples come from fuelplan.py; the radar report (alert per site,
launch / ID predictions) from alertsim.py; loiters from loiter.py (profile "loiter").

Target attack (notes/targeting_for_planner.md, not flight-tested), by the mission json's task:
  destroy    -> "maverick": straight run-in on the target's bearing to a pop-up launch point
                maverick_launch_km (14) short of the target; after the launch the route is free (no overfly).
                Falls back to "overfly" when the Maverick can't kill that target type (matrix value 0).
  photograph -> "photo": straight run-in, photo point (2 km, Elite 0.7 km), then over the target.
  anything else (supply drop, landing, intercept, task unknown) -> "overfly" as before.
The attack bearing is free: every attack_bearing_step_deg is estimated, the cheapest
attack_candidates per target are planned in full.

Writes <plan_dir>/route.json (nav.py --route), report.txt, route.png and terrain.npz.
With takeoff_heading "both": route_N.json + route_S.json (and report/png per direction);
give nav either one, it switches to the other if the plane sits on the runway the other way.
nav also refuses a route whose takeoff direction doesn't match the plane on the runway.
If the best plan breaks a limit it writes route.invalid.json instead and exits 1:
the report says what broke, and the fix belongs in the planner or the spec.
"""
import argparse, itertools, json, math, sys, time
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import profiles  # noqa: E402
import ordnance  # noqa: E402
import fuelplan  # noqa: E402
import alertsim  # noqa: E402
import loiter  # noqa: E402
from loiter import fit  # noqa: E402
import probes  # noqa: E402
import score  # noqa: E402
from world import World, K, KT_KMH  # noqa: E402
from radar import RadarModel, MODEL  # noqa: E402
from route import Router, PlanError, unit, hdg, from_hdg, turn_deg  # noqa: E402
import radar_game as rg  # noqa: E402

MAX_PROBE_TRIES = 6
MAVERICK = 5                        # ordnance.py loadout index (AGM-65D)
MAV_HALF_ANGLE = 22.0               # target must be within this of the nose at launch
MAV_WINDOW_KM = (6.0, 14.5)         # missile reach ~14.5-15.5 km (ttl = R*32 units at top speed = the 16 km cue)
PHOTO_KM = {"green": 2.0, "regular": 2.0, "veteran": 2.0, "elite": 0.7}


def fixed(pts):
    return dict(t="fixed", pts=[(tuple(map(float, p)), l, a) for p, l, a in pts])


def ends(el):
    """(entry xy, exit xy) of a sequence element."""
    if el["t"] == "fixed": return el["pts"][0][0], el["pts"][-1][0]
    return el["xy"], el["xy"]


# The game lines the plane up by theater alone (EGAME 0x1FC1, FINDINGS.md "Takeoff direction"):
# theater index bit 0 set (PG 1, CE 3) -> north, clear (LB 0, NC 2) -> south; a carrier adds 0x400 (5.6 deg).
THEATER_INDEX = {"LB": 0, "PG": 1, "NC": 2, "CE": 3}

def game_takeoff_heading(theater, carrier):
    return (0.0 if THEATER_INDEX[theater] & 1 else 180.0) + (0x400 * 360 / 65536 if carrier else 0.0)


class Planner:
    def __init__(self, spec, plan_dir):
        self.spec, self.dir = spec, plan_dir
        self.mode = spec.get("mode", "mission")
        ov = dict(spec.get("overrides") or {})
        if "extra_fuel" in spec:
            n = spec["extra_fuel"]
            if not (isinstance(n, int) and 0 <= n <= 3): raise ValueError(f"extra_fuel must be 0-3 tanks, not {n!r}")
            ov["extra_fuel_bays"] = n
        self.P = profiles.load(spec.get("profile", "mountain_loiter" if self.mode == "mountain_loiter" else "balanced"), ov)
        self.M = json.load(open(plan_dir / spec.get("mission", "mission_mem.json"))) if (plan_dir / spec.get("mission", "mission_mem.json")).exists() \
            else dict(radars=[], targets=[], theater=spec.get("theater"))
        if self.mode == "mountain_loiter": self.M = {**self.M, "targets": []}
        self.th = spec.get("theater") or self.M.get("theater")
        if not self.th: raise ValueError("no theater in plan.json or the mission file")
        self.W = World(self.th, self.M, self.P, spec.get("settings"), spec.get("sa14_live"))
        self.regimes = fuelplan.Regimes(self.P)
        self.RM = RadarModel(self.W)
        self.router = Router(self.W, self.RM, self.P, self.regimes)
        self.RM.kt_default = self.router.kt_at
        self.R = self.router.R
        self.log = list(self.W.notes)
        self.loiters = self._loiter_choices()
        if self.mode == "mountain_loiter" and self.loiters:      # default bases: the friendly airbase nearest the cell
            base = self.nearest_base(self.loiters[0]["centre"])
            self.spec = spec = {**spec, "takeoff": spec.get("takeoff") or base, "landing": spec.get("landing") or base}

    # ------------------------------------------------------------ loiter
    def _loiter_choices(self):
        """Racetracks to try, or [] when the profile / ROE says no loiter. Default: the best racetrack in each of the
        3 radar-quiet enemy cells nearest the route's end (last target and landing); mountain_loiter mode keeps the
        mountain-cell rule; an explicit cell [cx, cy] is used as given."""
        want = self.spec.get("loiter_cell") if self.mode == "mountain_loiter" else None
        want = want or self.P["loiter"]
        if want == "off": return []
        if self.W.roe != 0:
            self.log.append(f"loiter skipped: ROE {self.W.roe} (samples score only in Cold War: Limited /4, Conventional 0)")
            return []
        refs = [(t["x"], t["y"]) for t in self.M["targets"][-1:]]
        for k in ("landing", "takeoff"):
            if self.spec.get(k): refs.append(self.W.place(self.spec[k])[:2]); break
        if not refs: refs = list(self.W.airbases.values())
        mountain = self.mode == "mountain_loiter"
        if isinstance(want, (list, tuple)):
            c = [f for f in fit(self.W, self.P, tuple(want), loiter.radar_quiet_mask(self.W, self.RM, self.P)
                                if not mountain else loiter.quiet_cells(self.W, self.P)[0])[:1]]
            if not c: raise PlanError(f"loiter cell {tuple(want)}: no radar-quiet room for a racetrack there")
            c[0]["cell"] = tuple(want); return c
        cands, ok_rough = loiter.candidates(self.W, self.P, None if mountain else self.RM, refs, n_cells=3, mountain_only=mountain)
        self.loiter_cands = cands
        if mountain and not ok_rough: self.log.append("loiter: rough cells are visible at this opponent level + loiter EMV: mountain cells only")
        if not cands: self.log.append("loiter: no radar-quiet enemy ground fits a racetrack near this route")
        else: self.log.append("loiter candidates: " + ", ".join(f"{tuple(f['cell'])} {f['quadrant']} ({'mountain' if f['terrain'] == 0 else 'flat/rough'})" for f in cands))
        return cands

    def loiter_el(self, f, laps):
        return dict(t="loiter", fit=f, laps=laps, xy=tuple(map(float, f["centre"])))

    def nearest_base(self, xy):
        return min(self.W.airbases, key=lambda b: math.dist(self.W.airbases[b], xy))

    def _loiter_headings(self, seq, k):
        """Headings the route can arrive at / leave the loiter on: free searches (no heading at the
        loiter end) from the previous piece to the racetrack centre and from there to the next piece."""
        A, el, B = seq[k - 1], seq[k], seq[k + 1]
        c = tuple(np.clip(el["xy"], 64, self.W.size - 64))
        if A["t"] == "fixed": start, h0 = A["pts"][-1][0], hdg(unit(np.subtract(A["pts"][-1][0], A["pts"][-2][0])))
        else: start, h0 = A["xy"], None
        pts, h_in = self.router.search(tuple(np.clip(start, 64, self.W.size - 64)), c, h0, None)
        if B["t"] == "fixed": end, hg = B["pts"][0][0], hdg(unit(np.subtract(B["pts"][1][0], B["pts"][0][0])))
        else: end, hg = B["xy"], None
        pts2, _ = self.router.search(c, tuple(np.clip(end, 64, self.W.size - 64)), None, hg)
        far = next((q for q in pts2 if math.dist(q, c) > 6 * K), pts2[-1])      # leave toward where the path goes
        return h_in, hdg(unit(np.subtract(far, c)))

    # ------------------------------------------------------------ skeleton pieces
    def takeoff(self):
        x, y, name, carrier = self.W.place(self.spec["takeoff"])
        h = self.spec.get("takeoff_heading", game_takeoff_heading(self.th, carrier))
        c = np.array([x, y]) + from_hdg(h) * self.P["climb_km"] * K
        return fixed([((x, y), f"Takeoff ({name})", {"kind": "takeoff"}), (c, "Climb-out", {"kind": "climb"})])

    def landing(self, side):
        x, y, name, _ = self.W.place(self.spec["landing"])
        s = -1 if side == "N" else 1                       # N: fix north of the field, fly south onto it
        P = self.P
        return fixed([((x, y + s * (P["approach_km"] + P["straight_in_km"]) * K), "Intercept", {"kind": "intercept"}),
                      ((x, y + s * P["approach_km"] * K), "Approach fix", {"kind": "approach"}),
                      ((x, y + s * P["final_km"] * K), "Final", {"kind": "final"}),
                      ((x, y), f"Landing ({name})", {"kind": "landing"})])

    def targets(self, order):
        tg = {t["role"].rstrip("?"): t for t in self.M["targets"]}
        out = []
        for role in order:
            t = tg[role]
            out.append(dict(t="target", xy=(float(t["x"]), float(t["y"])), role=role,
                            label=f"{role.upper()}: {t['object']} {t['place']}", attrs={"kind": "overfly", "role": role}))
        return out

    # ------------------------------------------------------------ target attack
    def attack_mode(self, t):
        role = t["role"].rstrip("?")
        mode = self.spec.get("attack", {}).get(role)
        if mode:
            if mode not in ("maverick", "photo", "overfly"): raise ValueError(f"attack {role}: {mode!r} is not maverick / photo / overfly")
            return mode, "plan.json"
        task = t.get("task")
        if task == "photograph": return "photo", "photograph mission"
        if task == "destroy":
            v = self.maverick_value(t["object"])
            if v is None: return "maverick", "destroy mission (target type not in the matrix: Maverick assumed)"
            if v <= 0: return "overfly", f"destroy mission, but the Maverick can't kill a {t['object']} (matrix 0): bombs, overfly"
            return "maverick", f"destroy mission, Maverick kill chance {min(v, 4) * 25}% per hit"
        return "overfly", (f"{task} mission" if task else "task unknown (old mission file?)")

    def maverick_value(self, obj):
        if not hasattr(self, "_mav"):
            _, m = ordnance.tables()
            self._mav = {n: ordnance.compat(m, MAVERICK, c) for n, c in ordnance.categories(self.th).values()}
        return self._mav.get(obj)

    def attack_piece(self, el, t, mode, b):
        """Fixed piece for attacking target t on bearing b (deg), or None when it hits terrain / the map edge."""
        T, u, P = np.array(el["xy"]), from_hdg(b), self.P
        role, name = el["role"], f"{t['object']} {t['place']}"
        at = lambda kind, action: {"kind": kind, "role": role, "action": action}
        if mode == "maverick":
            L = T - u * P["maverick_launch_km"] * K; S = L - u * P["attack_runin_km"] * K
            pts = [(S, f"{role.upper()} run-in", at("runin", f"Maverick run-in on {b:.0f}: select {name}, cruise height and speed")),
                   (L, f"{role.upper()}: Maverick launch at {name}",
                    at("launch", f"LEVEL launch at {P['maverick_launch_km']:.0f} km (hexagon red): bay open, FIRE Maverick, bay closed"))]
        else:
            ph = P["photo_km"] or PHOTO_KM.get(self.spec.get("settings", {}).get("opponent", "regular"), 2.0)
            F = T - u * ph * K; S = F - u * P["attack_runin_km"] * K
            pts = [(S, f"{role.upper()} run-in", at("runin", f"photo run-in on {b:.0f}: camera + {name} selected, straight at it")),
                   (F, f"{role.upper()}: photo point {ph:g} km", at("photo", "PHOTO: bay open, nose on target, FIRE camera, bay closed")),
                   (T, el["label"], at("overfly", "frame taken? then turn away"))]
        xy = [p for p, _, _ in pts]
        if not all(64 <= c <= self.W.size - 64 for p in xy for c in p): return None
        if not all(self.W.segment_clear(a, c) for a, c in zip(xy, xy[1:])): return None
        piece = fixed(pts); piece.update(role=role, attack=mode, bearing=b); return piece

    def attack_options(self, seq, k, t, mode):
        """Cheapest attack bearings for seq[k] (a plain target): for each bearing the stretch
        previous piece -> attack piece -> next piece is searched and expanded with the real
        minimum-radius joins (plan() without the repair loop) and costed. A plain target next door
        counts as a point; the one before is already placed (see with_attacks)."""
        el = seq[k]
        clip = lambda p: tuple(np.clip(p, 64, self.W.size - 64))
        def stretch_cost(sub):
            core, h_arr = [sub[0]], None
            for A, B in zip(sub, sub[1:]):
                if A["t"] == "fixed": start, h0 = A["pts"][-1][0], hdg(unit(np.subtract(A["pts"][-1][0], A["pts"][-2][0])))
                else: start, h0 = A["xy"], h_arr
                if B["t"] == "fixed": end, hg = B["pts"][0][0], hdg(unit(np.subtract(B["pts"][1][0], B["pts"][0][0])))
                else: end, hg = B["xy"], None
                pts, h_arr = self.router.search(clip(start), clip(end), h0, hg)
                core += [dict(t="v", xy=tuple(p)) for p in self.router.reduce(pts)] + [B]
            V = self.router.expand(core)
            return self.router.route_cost(V) + 1000 * len(self.router.violations(V)) + self.bay_cost(sub[1]["pts"])
        ranked, blocked, failed = [], 0, []
        for b in np.arange(0, 360, self.P["attack_bearing_step_deg"]):
            piece = self.attack_piece(el, t, mode, float(b))
            if piece is None: blocked += 1; continue
            try: c = stretch_cost([seq[k - 1], piece, seq[k + 1]])
            except PlanError as e: failed.append(f"{b:.0f}: {e}"); continue
            ranked.append((c, float(b), piece))
        ranked.sort(key=lambda r: r[:2])
        if not ranked:
            raise PlanError(f"{el['label']}: no usable attack bearing ({blocked} hit terrain or the map edge, "
                            f"{len(failed)} could not be routed" + (f", e.g. {failed[0]}" if failed else "") + ")")
        self.log.append(f"{el['role']} attack bearings by estimate: " + ", ".join(f"{b:.0f} ({c:.0f})" for c, b, _ in ranked[:6]))
        return [p for _, _, p in ranked[:int(self.P["attack_candidates"])]]

    def with_attacks(self, base):
        """Yield (sequence, description) with each target's attack geometry. Targets are taken in
        flight order, each ranked after the previous one's attack piece is placed (a launch point
        18 km short of the last target changes where the next run-in is cheapest)."""
        tg = {t["role"].rstrip("?"): t for t in self.M["targets"]}
        def expand(seq, k):
            if k == len(seq): yield seq; return
            el = seq[k]
            if el["t"] != "target": yield from expand(seq, k + 1); return
            mode, _ = self.attack_mode(tg[el["role"]])
            for piece in ([el] if mode == "overfly" else self.attack_options(seq, k, tg[el["role"]], mode)):
                yield from expand(seq[:k] + [piece] + seq[k + 1:], k + 1)
        for seq in expand(list(base), 0):
            desc = ", ".join(f"{e['role']} {e['attack']} on {e['bearing']:.0f}" for e in seq if e.get("attack"))
            yield seq, desc

    def attack_checks(self, pts):
        """Each Maverick launch: arriving straight at the target (within MAV_HALF_ANGLE) inside the launch window."""
        errs = []
        for i, (p, l, a) in enumerate(pts):
            if a.get("kind") != "launch" or i == 0: continue
            t = next(t for t in self.M["targets"] if t["role"].rstrip("?") == a["role"])
            T = np.array([t["x"], t["y"]], float); p = np.asarray(p, float)
            d = np.linalg.norm(T - p) / K
            off = abs((hdg(T - p) - hdg(p - np.asarray(pts[i - 1][0], float)) + 180) % 360 - 180)
            if not MAV_WINDOW_KM[0] <= d <= MAV_WINDOW_KM[1]: errs.append(f"{l}: {d:.1f} km from the target (window {MAV_WINDOW_KM})")
            if off > MAV_HALF_ANGLE: errs.append(f"{l}: target {off:.0f} deg off the nose (Maverick needs <= {MAV_HALF_ANGLE:.0f})")
        return errs

    # ------------------------------------------------------------ one complete plan
    def plan(self, seq):
        log = []
        seq = [self._resolve(seq, k) for k in range(len(seq))]
        core, h_arr = [seq[0]], None
        for A, B in zip(seq, seq[1:]):
            # search from the fixed piece's end on its heading (the radius-aware search turns
            # off it at ~R), so the grid path covers the ground the plane really flies over
            if A["t"] == "fixed":
                start, h0 = A["pts"][-1][0], hdg(unit(np.subtract(A["pts"][-1][0], A["pts"][-2][0])))
            else:
                start, h0 = A["xy"], h_arr
            if B["t"] == "fixed":
                end, hg = B["pts"][0][0], hdg(unit(np.subtract(B["pts"][1][0], B["pts"][0][0])))
            else:
                end, hg = B["xy"], None
            clip = lambda p: tuple(np.clip(p, 64, self.W.size - 64))
            pts, h_arr = self.router.search(clip(start), clip(end), h0, hg)
            core += [dict(t="v", xy=tuple(p)) for p in self.router.reduce(pts)] + [B]
        core, V = self.router.repair(core, log)
        pts = self.router.densify(V)
        overfly = {e["label"] for e in seq if e["t"] == "target"}
        # Maverick targets are never overflown; overfly and photo targets are
        mav = {e["role"] for e in seq if e.get("attack") == "maverick"}
        errs = self.router.validate(pts, [t for t in self.M["targets"] if t["role"].rstrip("?") not in mav], overfly)
        errs += self.attack_checks(pts)
        bays = {l for p, l, a in pts if a.get("kind") in ("photo", "launch")}
        fuel = fuelplan.integrate(self.W, self.RM, self.P, pts, bays)
        split = fuelplan.split_at_alt(pts, fuel["steps"])           # one altitude per leg for nav
        if len(split) != len(pts):
            pts = split; fuel = fuelplan.integrate(self.W, self.RM, self.P, pts, bays)
        reserve = self.P["landing_reserve_lb"]
        if fuel["fuel_land"] < reserve:
            errs.append(f"fuel at landing {fuel['fuel_land']:.0f} lb, below the {reserve:.0f} lb reserve "
                        f"({fuel['fuel0']:.0f} loaded)")
        return dict(seq=seq, V=V, pts=pts, cost=self.router.route_cost(V) + self.bay_cost(pts), errors=errs, log=log, fuel=fuel)

    def points_proxy(self, r):
        steps = r["fuel"]["steps"]
        lo, hi, _ = fuelplan.samples(self.W, steps, self.P)
        if not steps: return 0.0
        A = lambda k: np.array([s_[k] for s_ in steps])
        xy, u = A("xy"), A("u")
        det = self.RM.detection(xy[:, 0], xy[:, 1], u[:, 0], u[:, 1], A("kt").astype(float), A("alt"), A("bank"), A("bay"))
        sa14_min = float(sum(s_["km"] / (s_["kt"] * KT_KMH) * 60 for s_ in steps if self.W.sa14(*s_["xy"])))
        # the same prices the route search pays: seen pulses and SA-14 minutes by day, in samples
        return ((lo + hi) / 2 - self.P["pulse_cost_samples"] * float((det * A("km")).sum())
                - self.P["sa14_cost_samples_per_min"] * sa14_min)

    def bay_cost(self, pts):
        """Extra exposure of the open bay (+24 EMV) for bay_open_s before each photo point / Maverick launch."""
        c = 0.0
        for i, (p, l, a) in enumerate(pts):
            if a.get("kind") not in ("photo", "launch") or i == 0: continue
            p = np.asarray(p, float); u = unit(p - np.asarray(pts[i - 1][0], float))
            kt = float(self.router.kt_at(p[0], p[1]))
            L = self.P["bay_open_s"] * kt * KT_KMH / 3600
            d = np.arange(0.25, L, 0.5)
            x, y = p[0] - u[0] * d * K, p[1] - u[1] * d * K
            extra = self.RM.detection(x, y, u[0], u[1], kt, bay=True) - self.RM.detection(x, y, u[0], u[1], kt)
            c += self.router.w_pulse * float(extra.sum()) * 0.5
        return c

    def plan_with_loiter(self, seq):
        """Plan with loiter_min_laps, then as many laps as the fuel above the reserve buys (re-planned until
        the landing fuel sits between the reserve and the reserve + one lap). Options are ranked by the
        min-lap route's cost: laps only add samples."""
        k = next((i for i, e in enumerate(seq) if e["t"] == "loiter"), None)
        r = self.plan(seq)
        if k is None: return r
        el, cost, reserve = seq[k], r["cost"], self.P["landing_reserve_lb"]
        laps, best = el["laps"], None
        for _ in range(6):
            st = [s_ for s_ in r["fuel"]["steps"] if s_["loiter"]]
            km = sum(s_["km"] for s_ in st)
            lap_lb = (st[0]["fuel"] - st[-1]["fuel"]) * el["fit"]["lap_km"] / km if km else 0.0
            spare = r["fuel"]["fuel_land"] - reserve
            if spare >= 0: best = (r, laps, lap_lb)
            if lap_lb <= 0 or 0 <= spare < lap_lb: break
            step = int(spare // lap_lb) if spare >= 0 else -int(math.ceil(-spare / lap_lb))
            if laps + step < el["laps"]: break
            laps += step
            r = self.plan(seq[:k] + [self.loiter_el(el["fit"], laps)] + seq[k + 1:])
        r, laps, lap_lb = best or (r, laps, 0.0)
        r["cost"] = cost
        r["loiter"] = dict(el, laps=laps, lap_lb=lap_lb)
        return r

    def _resolve(self, seq, k):
        el = seq[k]
        if el["t"] == "loiter":
            h_in, h_out = self._loiter_headings(seq, k)
            piece = fixed(loiter.piece(self.W, el["fit"], el["laps"], h_in, h_out, self.P))
            piece.update(loiter=el); return piece
        if el["t"] != "orbit": return el
        prev_xy, next_xy = ends(seq[k - 1])[1], ends(seq[k + 1])[0]
        pts = probes.orbit_points(el["spec"], el["rad"], prev_xy, next_xy)
        a = probes._attrs(el["spec"], el["label"])
        return fixed([(p, el["label"] if i == 0 else ("Orbit exit" if i == len(pts) - 1 else ""), a) for i, p in enumerate(pts)])

    # ------------------------------------------------------------ choices
    def run(self):
        roles_known = all(not t["role"].endswith("?") for t in self.M["targets"])
        order_mode = self.spec.get("target_order", "primary-first")
        if not roles_known and order_mode != "auto":
            self.log.append("memory can't tell primary from secondary (same target type): planning both orders")
            order_mode = "auto"
        orders = [("primary", "secondary")] + ([("secondary", "primary")] if order_mode == "auto" else [])
        sides = [self.spec["approach"]] if self.spec.get("approach", "auto") != "auto" else ["N", "S"]
        results, failures = [], []
        if self.mode == "mountain_loiter":
            if not self.loiters: raise PlanError("mountain_loiter: no loiter cell (see the planner log)")
            orders = [()]
        loiters = self.loiters + ([None] if self.mode != "mountain_loiter" else []) if self.loiters else [None]
        for order, side, lo in itertools.product(orders, sides, loiters):
            mid = [self.loiter_el(lo, self.P["loiter_min_laps"])] if lo else []
            plain = [self.takeoff(), *self.targets(order), *mid, self.landing(side)]
            for (base, attack_desc), (seq, probe_desc) in ((ba, sp) for ba in self.with_attacks(plain) for sp in self._with_probes(ba[0])):
                lo_desc = f"loiter {tuple(lo['cell'])} {lo['quadrant']}" if lo else ""
                tag = ", ".join(x for x in ((f"order {'/'.join(order)}, " if order else "") + f"approach {side}", attack_desc, lo_desc, probe_desc) if x)
                try:
                    r = self.plan_with_loiter(seq); r["tag"] = tag; results.append(r)
                    self.log.append(f"option {tag}: cost {r['cost']:.0f}, {len(r['errors'])} errors")
                except PlanError as e:
                    failures.append(f"{tag}: {e}"); self.log.append(f"option {tag}: FAILED {e}")
        if not results:
            raise PlanError("no option could be planned:\n  " + "\n  ".join(failures))
        # A plan that fails: show the one CLOSEST to working (least fuel short), not the best-scoring one
        # (2026-10-21 Lukow at Elite: every option ran dry and the report showed the longest, a loiter -5226 lb).
        short = lambda r: max(0.0, self.P["landing_reserve_lb"] - r["fuel"]["fuel_land"])
        if self.loiters:
            # loiter options earn samples the route cost doesn't see: rank valid ones by points (samples minus
            # the pulse cost of the expected seen pulses), the same currency as the objective
            for r in results: r["points"] = self.points_proxy(r)
            results.sort(key=lambda r: (len(r["errors"]) > 0, short(r) if r["errors"] else 0, -r["points"], r["cost"]))
        else:
            results.sort(key=lambda r: (len(r["errors"]) > 0, short(r) if r["errors"] else 0, r["cost"]))
        return results[0], results

    def _with_probes(self, base):
        """Yield (sequence, description) with every probe inserted; candidates ranked by detour."""
        specs = self.spec.get("probes", [])
        if not specs:
            yield base, ""; return
        seqs = [(base, [])]
        for ps in specs:
            nxt = []
            for seq, desc in seqs:
                ranked = []
                for pts, lab, radar in probes.candidates(self.W, ps, self.R / K):
                    positions = self._positions(seq, ps.get("after", "auto"))
                    for pos in positions:
                        prev_xy, next_xy = ends(seq[pos - 1])[1], ends(seq[pos])[0]
                        if pts[0] == "orbit":
                            _, sp, rad, _ = pts
                            el = dict(t="orbit", spec=sp, rad=rad, label=lab)
                            entry = exit_ = tuple(sp["center"]); length = 2 * math.pi * rad * K * sp.get("laps", 1)
                        else:
                            err = probes.check(self.W, self.RM, pts, radar, ps)
                            if err: self.log.append(f"probe candidate {lab}: rejected ({err})"); continue
                            el = fixed(pts); entry, exit_ = pts[0][0], pts[-1][0]
                            length = sum(math.dist(a[0], b[0]) for a, b in zip(pts, pts[1:]))
                        detour = math.dist(prev_xy, entry) + length + math.dist(exit_, next_xy) - math.dist(prev_xy, next_xy)
                        ranked.append((detour, lab, pos, el))
                ranked.sort(key=lambda r: (r[0], r[1]))
                if not ranked: raise PlanError(f"probe {ps.get('label') or ps['type']}: no usable candidate (see log)")
                for detour, lab, pos, el in ranked[:MAX_PROBE_TRIES]:
                    nxt.append((seq[:pos] + [el] + seq[pos:], desc + [f"{lab} (+{detour / K:.0f} km)"]))
            seqs = nxt
        for seq, desc in seqs: yield seq, "; ".join(desc)

    def _positions(self, seq, after):
        if after == "auto": return list(range(1, len(seq)))
        for k, el in enumerate(seq):
            name = el.get("role") or ("takeoff" if el["t"] == "fixed" and el["pts"][0][1].startswith("Takeoff") else None)
            if name == after: return [k + 1]
        raise ValueError(f"probe 'after': {after!r} is not takeoff / primary / secondary / auto")


# ---------------------------------------------------------------- outputs
NAV_NEEDS = ("nav holds one speed (profile cruise_kt) with the throttle keys and touches flaps only on takeoff: to fly "
             "this plan's per-leg settings it needs to read each point's throttle / flaps (route.json points) and set them "
             "(throttle taps of 10 %, flaps key) at leg changes. Until then fly the throttle column by hand or accept cruise_kt.")
FLIGHT_TESTS = ["40 % throttle with flaps when light (< 4000 lb): the model says it can't hold level, but it was pessimistic "
                "near that edge before (50 % clean heavy flew at 171 kt); 40 % = 45 lb/min, +33 % loiter time.",
                "lb per sample at 50 % + flaps from the new flight log (model: 64 lb per 64 s sample)."]
LOITER_TEST = ["first flight: 10-15 game min in the loiter, then home (the logger records everything).",
               "pass: every site's alert stays 0 inside the loiter cell, V and R counts stay 0, no SAM / SA-14 events, "
               "the 64 s samples (event type 9) land in the cell, the debrief points match the formula.",
               "watch: enemy fighters on patrol (they can't ID the plane by the code, but their attack logic isn't "
               "decoded); the flat-area edge vs nav's cross-track error; drifting out of the cell."]
OPP_NAME = {0: "green", 1: "regular", 2: "veteran", 3: "elite"}


def summarise(pl, best):
    W, RM, P = pl.W, pl.RM, pl.P
    fu = best["fuel"]; steps = fu["steps"]
    km = sum(s["km"] for s in steps)
    smin, smax, samples = fuelplan.samples(W, steps, P)
    if steps:
        A = lambda k: np.array([s[k] for s in steps])
        X, Y = A("xy")[:, 0], A("xy")[:, 1]; U = A("u")
        det = RM.detection(X, Y, U[:, 0], U[:, 1], A("kt").astype(float), A("alt"), A("bank"), A("bay"))
        expo = float((det * A("km")).sum())
        sa14_min = float(sum(s["km"] / (s["kt"] * KT_KMH) * 60 for s in steps if W.sa14(*s["xy"])))
    else:
        expo = sa14_min = 0.0
    sim = alertsim.simulate(W, RM, steps)
    exp_ids = sim["summary"]["expected"]["expected_ids"] if sim.get("sites") else 0.0
    id_pen = int(round(10 * exp_ids))
    lb_ps = pl.regimes.lb_per_sample(P["fuel_ref_lb"], P["cruise_ft"])
    spare = fu["fuel_land"] - P["landing_reserve_lb"]
    roles = {t["role"].rstrip("?") for t in pl.M["targets"]}
    st = {**pl.spec.get("settings", {})}
    est = lambda lo, hi, pen: score.estimate(range(lo, hi + 1), pl.th, st, "primary" in roles, "secondary" in roles, pen)
    base_rng, score_rng, assumed = est(smin, smax, id_pen)
    _, score_nopen, _ = est(smin, smax, 0)
    extra = 0.0 if best.get("loiter") else max(0.0, spare) / lb_ps      # samples still available for a loiter
    _, score_loiter, _ = est(smin + int(extra), smax + int(extra), id_pen)
    res_samples = P["landing_reserve_lb"] / lb_ps
    _, score_nores, _ = est(smin + int(res_samples), smax + int(res_samples), id_pen)
    xy = [np.asarray(p[0], float) for p in best["pts"]]
    kinks = [turn_deg(xy[i - 1], xy[i], xy[i + 1]) > 0.5 for i in range(1, len(xy) - 1)]
    corners = sum(1 for i, k in enumerate(kinks) if k and (i == 0 or not kinks[i - 1]))
    return dict(km=round(km, 1), game_min=round(fu["t_end"] / 60, 1), turns=corners, exposure_pulses=round(expo, 2),
                enemy_samples=[smin, smax], samples=len(samples), fuel_loaded_lb=round(fu["fuel0"]),
                fuel_used_lb=round(fu["fuel0"] - fu["fuel_land"]), fuel_landing_lb=round(fu["fuel_land"]),
                lb_per_sample=round(lb_ps, 1), loiter_budget_lb=round(spare), loiter_budget_samples=round(extra, 1),
                sa14_minutes=round(sa14_min, 1), expected_radar_ids=round(exp_ids, 2), id_penalty_base=id_pen,
                base_points=list(base_rng), score_est=list(score_rng), score_without_id_penalty=list(score_nopen),
                score_if_loitered=list(score_loiter), score_without_reserve=list(score_nores),
                radar_overrides_km=fu["overrides"], score_assumptions=assumed,
                alert=sim.get("summary"), cost=round(best["cost"], 1)), sim, samples


def _t(s):
    return "-" if s is None else f"{int(s // 60)}:{int(s % 60):02d}"


def radar_block(pl, sim):
    L = []
    if not sim.get("sites"): return ["RADAR: no radar sites in the mission file"]
    S = sim["summary"]
    for s in sim["sites"]:
        if s["worst"]["first_c0"] is not None or s["expected"]["p_launch"] > 0:
            L.append(f"!! PREDICTED alert > 0xC0: {s['name']} at {_t(s['worst']['first_c0'])} game min (sites always on); "
                     f"expected-duty chance of a launch {s['expected']['p_launch']:.0%}, of a radar ID {s['expected']['p_id']:.0%}")
    L.append(f"RADAR (alertsim, {sim['n']} draws of pulse phase / frame counter; seen = return + EMV > 100):")
    for mode, txt in (("worst", "worst case (sites always active)"), ("expected", f"expected (duty cycle {pl.RM.duty:.0%})")):
        m = S[mode]
        L.append(f"  {txt:34s}: max alert {m['max_alert']}, network alert {m['p_any_net']:.0%}, "
                 f"launch possible {m['p_any_launch']:.0%}, radar IDs {m['expected_ids']:.2f}")
    L.append("  site                    kind  best margin  steps seen/near  max alert worst/exp  >7F    >C0    >=FA   P(launch) P(ID)")
    for s in sorted(sim["sites"], key=lambda s: -s["best_margin"]):
        if s["near_steps"] == 0 and s["worst"]["max_alert"] == 0: continue
        w, e = s["worst"], s["expected"]
        L.append(f"  {s['name'][:22]:22s}  {s['type']:4s}  {s['best_margin']:+11.0f}  {s['seen_steps']:5d} / {s['near_steps']:5d}"
                 f"  {w['max_alert']:9d} / {e['max_alert']:3d}  {_t(w['first_net']):6s} {_t(w['first_c0']):6s} {_t(w['first_fa']):6s}"
                 f" {e['p_launch']:8.0%} {e['p_id']:6.0%}")
    quiet = sum(1 for s in sim["sites"] if s["near_steps"] == 0 and s["worst"]["max_alert"] == 0)
    L.append(f"  ({quiet} other sites never within the {pl.RM.M:.0f} margin)")
    return L


def report(pl, best, results, summ, sim):
    W, RM, P, V = pl.W, pl.RM, pl.P, best["V"]
    r = pl.router
    L = [f"F-19 plan  {pl.dir}  ({time.strftime('%Y-%m-%d %H:%M')})",
         f"theater {pl.th}; mode {pl.mode}; profile {P['name']}" + (f" + {pl.spec.get('overrides')}" if pl.spec.get("overrides") else ""),
         f"settings: opponent {OPP_NAME[W.opp]}, war {W.war}, {'night' if W.night else 'day'}, ROE {W.roe}; radar model {MODEL}",
         f"objective (transit km): enemy km x{r.enemy_km:.2f} (coldwar credit {r.coldwar:.2f}), one sample = {r.sample_km:.1f} km, "
         f"one seen pulse = {P['pulse_cost_samples']:g} samples, SA-14 minute = {P['sa14_cost_samples_per_min']:g} samples, "
         f"corner {P['turn_simplicity_km']:g} km + banked-arc exposure; min turn radius {P['min_turn_radius_km']:.0f} km, "
         f"altitude {P['cruise_ft']:.0f} ft where no site would see the plane, {P['threat_ft']:.0f} ft where one would "
         f"(from {P['alt_lead_km']:g} km before)", ""]
    L += radar_block(pl, sim) + ["", f"CHOSEN: {best['tag']}"]
    for o in results:
        if o is not best: L.append(f"  also: {o['tag']}: cost {o['cost']:.0f}" + (f", {len(o['errors'])} errors" if o["errors"] else ""))
    s = summ
    L += ["", f"route {s['km']:.0f} km, {s['game_min']:.0f} game min, {s['turns']} turns, expected seen pulses {s['exposure_pulses']:.1f}, "
              f"SA-14 cell time {s['sa14_minutes']:.1f} min{'' if W.night else ' (day)'}",
          f"fuel: {s['fuel_loaded_lb']} lb carried ({P['fuel_loaded_lb']:.0f} internal + {P['extra_fuel_bays']} Extra Fuel x 1900), "
          f"{s['fuel_used_lb']} used, {s['fuel_landing_lb']} at landing (reserve {P['landing_reserve_lb']:.0f})",
          f"cold-war samples in enemy cells: {s['enemy_samples'][0]}-{s['enemy_samples'][1]} (64 s phase unknown); "
          f"{s['lb_per_sample']:.0f} lb per sample at the enemy setting"]
    fu = best["fuel"]
    for x in fu.get("transfers", []):
        L.append(f"Extra Fuel transfer planned at {x['t'] / 60:.0f} game min ({x['km']:.0f} km): fuel below 500 lb, nav switches "
                 f"to the tank's station and presses Enter")
    if fu.get("tanks_left"): L.append(f"Extra Fuel: {fu['tanks_left']} tank(s) not needed on this route (still counted at landing)")
    if best.get("loiter"):
        lo = best["loiter"]
        L.append(f"loiter: cell {tuple(lo['fit']['cell'])}, {lo['laps']} laps of {lo['fit']['lap_km']:.0f} km "
                 f"(~{lo['lap_lb']:.0f} lb per lap), racetrack r {P['loiter_radius_km']:g} km, straights 2 x {2 * lo['fit']['half_km']} km, "
                 f"{lo['fit']['surplus_km']:.1f} km to spare beyond the {P['loiter_clear_km']:g} km wall / {P['loiter_edge_km']:g} km edge margins")
    else:
        L.append(f"loiter budget (not spent): {s['loiter_budget_lb']} lb above the reserve = ~{s['loiter_budget_samples']:.0f} samples "
                 f"in a quiet enemy cell -> score {s['score_if_loitered'][0]}-{s['score_if_loitered'][1]}")
    L += [f"rating estimate (targets as planned, safe landing, expected radar IDs {s['expected_radar_ids']:.2f} = -{s['id_penalty_base']} base): "
          f"base {s['base_points'][0]}-{s['base_points'][1]}, score {s['score_est'][0]}-{s['score_est'][1]} "
          f"(without the ID penalty {s['score_without_id_penalty'][0]}-{s['score_without_id_penalty'][1]})",
          f"  the {P['landing_reserve_lb']:.0f} lb landing reserve costs ~{P['landing_reserve_lb'] / s['lb_per_sample']:.0f} samples: "
          f"score {s['score_without_reserve'][0]}-{s['score_without_reserve'][1]} with none (a crash isn't penalised; "
          f"your call, not the planner's)"] + [f"  assumes {a}" for a in s["score_assumptions"]]
    L += ["", "speed and fuel by leg (fuel_model.py; '*' = slowed for radar):",
          "      km          game min    alt ft  thr flaps  kt        fuel lb       enemy"]
    for g in fuelplan.legs(best["fuel"]["steps"]):
        thr, fl, alt = g["key"]
        L.append(f"  {g['km0']:5.0f}-{g['km1']:5.0f}  {g['t0'] / 60:5.1f}-{g['t1'] / 60:5.1f}  {alt:6d}  {thr:3d}% {'down' if fl else 'up':5s} "
                 f"{min(g['kts']):3d}-{max(g['kts']):3d}  {g['fuel0']:6.0f}-{g['fuel1']:6.0f}  {g['enemy'] / max(1, g['n']):4.0%}"
                 + ("  *" if g["override"] else ""))
    L += ["", "fuel at named waypoints:"]
    for st_ in best["fuel"]["steps"]:
        if st_["label"]:
            L.append(f"  {st_['label'][:40]:40s} {st_['t'] / 60:6.1f} game min  {st_['fuel']:6.0f} lb  "
                     f"{st_['alt']:.0f} ft {st_['thr']}%{' flaps' if st_['flaps'] else ''} {st_['kt']} kt")
    L.append(f"  {'Landing':40s} {best['fuel']['t_end'] / 60:6.1f} game min  {best['fuel']['fuel_land']:6.0f} lb")
    tg = {t["role"].rstrip("?"): t for t in pl.M["targets"]}
    if tg:
        L += ["", "attacks (notes/targeting_for_planner.md; NOT flight-tested):"]
        for role, t in tg.items():
            mode, why = pl.attack_mode(t)
            L.append(f"  {role}: {t['object']} {t['place']} -> {mode} ({why})")
        L += [f"  {l}: {a['action']}" for p, l, a in best["pts"] if l and a.get("action")]
    L += ["", "CHECKS: " + ("all passed" if not best["errors"] else "FAILED")] + [f"  !! {e}" for e in best["errors"]]
    notes = sorted(pl.router.notes)
    if best["log"] or pl.log or notes: L += ["", "planner log:"] + [f"  {m}" for m in notes + pl.log + best["log"]]
    L += ["", " #  from -> to                                hdg    km   turn  territory  sites within the margin (mid-leg, margin)"]
    named = [i for i, v in enumerate(V) if v["label"] or v["fillet"]]
    for a, b in zip(named, named[1:]):
        pa, pb = V[a]["xy"], V[b]["xy"]; u = unit(pb - pa); mid = (pa + pb) / 2
        tn = turn_deg(V[b - 1]["xy"], pb, V[b + 1]["xy"]) if 0 < b < len(V) - 1 else 0
        sight = "; ".join(f"{n} {d:.0f}km {asp} {m:+.0f}" for n, _, d, asp, m in RM.in_sight(mid, u))
        cell = f"{'ENEMY' if W.enemy(*mid) else '-'}{'/SA-14' if W.sa14(*mid) else ''}"
        L.append(f"{a:2d}  {(V[a]['label'] or 'corner')[:20]:20s} -> {(V[b]['label'] or 'corner')[:20]:20s} {hdg(u):4.0f} "
                 f"{np.linalg.norm(pb - pa) / K:6.1f} {tn:5.0f}  {cell:9s}  {sight}")
    L += ["", "timeline (every 20 km):"]
    for st_ in best["fuel"]["steps"][::20]:
        p, u = st_["xy"], st_["u"]
        L.append(f"{st_['t'] / 60:5.1f} min ({p[0]:5.0f},{p[1]:5.0f}) hdg {hdg(u):3.0f} {st_['alt']:4.0f} ft {st_['kt']:3d} kt {st_['fuel']:6.0f} lb "
                 f"{('ENEMY' if st_['enemy'] else '-'):6s} "
                 + "; ".join(f"{n} {d:.0f}km {a} {m:+.0f}" for n, _, d, a, m in
                             RM.in_sight(p, u, st_["kt"], st_["alt"], st_["bank"], st_["bay"])))
    L += ["", "NAV: " + NAV_NEEDS, "", "flight tests to request:"] + [f"  - {t}" for t in FLIGHT_TESTS]
    if best.get("loiter"):
        lo = best["loiter"]["fit"]
        L += ["", "loiter test plan (NOT yet flown):"] + [f"  - {t}" for t in LOITER_TEST]
        if lo.get("peak_ft"):
            hi = lo["peak_ft"] + 1000
            L.append(f"  option (not planned): orbit above the peaks at ~{hi:.0f} ft: EMV {rg.emv(int(hi), 190, W.opp)} "
                     f"level, still invisible in a mountain cell; same lb/min, climb fuel not modelled, terrain risk if the "
                     f"altitude hold sags. The low racetrack keeps the walls rule.")
    return "\n".join(L) + "\n"


def draw(pl, best, samples, path):
    W, S = pl.W, 32
    rx = [p[0][0] for p in best["pts"]]; ry = [p[0][1] for p in best["pts"]]
    X0, X1 = max(0, int(min(rx)) - 6400), min(W.size, int(max(rx)) + 6400)
    Y0, Y1 = max(0, int(min(ry)) - 6400), min(W.size, int(max(ry)) + 6400)
    sub = W.H[Y0 // W.step:Y1 // W.step:S // W.step, X0 // W.step:X1 // W.step:S // W.step]
    im = np.full(sub.shape + (3,), 235, np.uint8); im[sub > 0] = (200, 200, 160); im[sub >= pl.P["mountain_ft"]] = (150, 100, 55)
    img = Image.fromarray(im); d = ImageDraw.Draw(img, "RGBA")
    Q = lambda x, y: ((x - X0) / S, (y - Y0) / S)
    g = W.tgrid
    for j in range(16):
        for i in range(16):
            x0, y0 = Q(i * 2048, j * 2048); x1, y1 = Q(i * 2048 + 2047, j * 2048 + 2047)
            terr = (g[j, i] >> 2) & 3
            if g[j, i] & 3 == 0: d.rectangle((x0, y0, x1, y1), fill=(255, 0, 0, 22))
            if terr == 0: d.rectangle((x0, y0, x1, y1), outline=(90, 60, 30, 170), width=2)     # mountain cell: invisible
            elif terr == 1: d.rectangle((x0, y0, x1, y1), outline=(160, 140, 60, 130), width=1)  # rough: x0.5
            elif terr == 3: d.rectangle((x0, y0, x1, y1), fill=(0, 80, 255, 18))                # sea: x1.5
            if W.sa14_cells[j, i]:
                for k in range(0, int(x1 - x0), 8):
                    d.line([(x0 + k, y0), (x1, y1 - k)], fill=(255, 120, 0, 90))
                    d.line([(x0, y0 + k), (x1 - k, y1)], fill=(255, 120, 0, 90))
                d.text((x0 + 2, y0 + 2), "SA-14", fill=(200, 80, 0))
    for r in W.radars:
        cx, cy = Q(r["x"], r["y"]); c = (200, 0, 0) if r["type"] == "P" else (0, 130, 0)
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=c); d.text((cx + 4, cy), f"{r['place']} {r['system']}", fill=c)
    d.line([Q(*p[0]) for p in best["pts"]], fill=(220, 0, 220), width=2)
    lo = [Q(*p[0]) for p in best["pts"] if (p[2] or {}).get("kind") == "loiter"]
    if lo: d.line(lo, fill=(0, 160, 0), width=2)
    for p, l, a in best["pts"]:
        if a.get("kind") == "probe": x, y = Q(*p); d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 60, 255))
        if l: d.text(Q(*p), l, fill=(0, 0, 0))
    for v in best["V"]:
        if v["fillet"]: x, y = Q(*v["xy"]); d.ellipse((x - 3, y - 3, x + 3, y + 3), outline=(120, 0, 120))
    for p, l, a in best["pts"]:
        if a.get("kind") == "launch":
            t = next(t for t in pl.M["targets"] if t["role"].rstrip("?") == a["role"])
            d.line([Q(*p), Q(t["x"], t["y"])], fill=(255, 120, 0), width=1)
            x, y = Q(*p); d.ellipse((x - 4, y - 4, x + 4, y + 4), outline=(255, 120, 0), width=2)
    for p, e in samples:
        x, y = Q(*p); d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 0, 255) if e else (120, 120, 120))
    img.save(path)


def takeoff_heading(theater, carrier):
    """The theater alone sets the takeoff direction (EGAME 0x1FC1, FINDINGS.md "Takeoff direction",
    2026-10-17): Central Europe / Gulf north, Libya / North Cape south; carriers +5.6 deg."""
    return (0.0 if theater in ("CE", "PG") else 180.0) + (0x400 * 360 / 65536 if carrier else 0.0)


FUEL_FALLBACK = (0.3, 0.1)          # radar price x these when the planned profile's route doesn't fit the fuel


def emit(D, spec, suffix):
    """Plan one variant and write its files; returns True when valid."""
    pl = Planner(spec, D)
    best, results = pl.run()
    head = []
    if best["errors"] and all(e.startswith("fuel at landing") for e in best["errors"]):
        # FUEL-LIMITED (2026-10-21 Lukow at Elite: every stealth route ran dry): re-plan with cheaper radar prices and no
        # loiter until a route fits, and say what that trades away. Stops at the first that fits.
        s0, sim0 = summarise(pl, best)[:2]
        P0 = pl.P; tried = []; options = [(s0["fuel_landing_lb"], pl, best, results)]
        for f in FUEL_FALLBACK:
            pc = round(P0["pulse_cost_samples"] * f, 2)
            spec2 = {**spec, "overrides": {**(spec.get("overrides") or {}), "pulse_cost_samples": pc, "loiter": "off"}}
            print(f"fuel-limited: re-planning with the radar price at {pc:g} samples per seen pulse, no loiter", flush=True)
            pl2 = Planner(spec2, D); b2, r2 = pl2.run(); s2 = summarise(pl2, b2)[0]
            tried.append(f"  radar price {pc:g} (was {P0['pulse_cost_samples']:g}): {s2['km']:.0f} km, fuel at landing {s2['fuel_landing_lb']} lb, "
                         f"launch possible {s2['alert']['expected']['p_any_launch']:.0%}, radar IDs {s2['alert']['expected']['expected_ids']:.2f}"
                         + ("" if b2["errors"] else "  <- this plan"))
            if not b2["errors"]: pl, best, results = pl2, b2, r2; break
            options.append((s2["fuel_landing_lb"], pl2, b2, r2))
        else:                                       # nothing fits: show the one closest to fitting
            _, pl, best, results = max(options, key=lambda o: o[0])
        e0 = s0["alert"]["expected"]
        head = ["!! FUEL-LIMITED: the " + P0["name"] + f" route needs more fuel than carried ({s0['km']:.0f} km, {s0['fuel_landing_lb']} lb at landing "
                f"against a {P0['landing_reserve_lb']:.0f} lb reserve; launch possible {e0['p_any_launch']:.0%}, radar IDs {e0['expected_ids']:.2f})."]
        head += tried
        head.append("!! " + ("using the first cheaper-radar plan that fits: more radar risk for less distance."
                             if not best["errors"] else
                             "nothing fits. Options: more Extra Fuel, a lower landing_reserve_lb (your call; a crash isn't scored), "
                             "or a different mission."))
        head.append("")
    summ, sim, samples = summarise(pl, best)
    rep = "\n".join(head) + report(pl, best, results, summ, sim)
    (D / f"report{suffix}.txt").write_text(rep); print(rep)
    pts = best["pts"]
    leg_set = {}
    for st in best["fuel"]["steps"]: leg_set.setdefault(st["leg"], st)
    alts = fuelplan.leg_alt(best["fuel"]["steps"], len(pts), pl.P)
    def fly(i, at):                                  # the planned setting from this point on (fuelplan.integrate)
        st = leg_set.get(i) or leg_set.get(i - 1)
        if not st: return dict(at)
        out = {**at, "throttle": st["thr"], "flaps": st["flaps"], "speed_kt": at.get("speed_kt", st["kt"]),
               "fuel_lb": round(st["fuel"]), "t_game_s": round(st["t"])}
        if "alt_ft" not in at and at.get("kind") not in fuelplan.KEEP_WHOLE[4:]:   # takeoff / landing: nav's own
            out["alt_ft"] = round(alts[i])           # nav holds the alt_ft of the point a leg ends at
        return out
    out = dict(schema=2, spec=spec, profile=pl.P, summary=summ, chosen=best["tag"], errors=best["errors"],
               takeoff_heading=spec.get("takeoff_heading"),
               waypoints_game=[[[float(p[0]), float(p[1])], l] for p, l, _ in pts],
               points=[dict(x=float(p[0]), y=float(p[1]), label=l, **fly(i, at)) for i, (p, l, at) in enumerate(pts)])
    name = f"route{suffix}.json" if not best["errors"] else f"route{suffix}.invalid.json"
    json.dump(out, open(D / name, "w"), indent=1)
    np.savez_compressed(D / "terrain.npz", height_game=pl.W.H.astype(np.uint16), height_step=pl.W.step)
    draw(pl, best, samples, D / f"route{suffix}.png")
    print(("plan INVALID" if best["errors"] else "wrote") + f" {D / name}" + (f" ({len(best['errors'])} errors; see report{suffix}.txt)" if best["errors"] else ""))
    return not best["errors"]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("plan_dir"); ap.add_argument("--spec", default="plan.json")
    a = ap.parse_args(); D = Path(a.plan_dir)
    spec = json.load(open(D / a.spec))
    th = spec.get("takeoff_heading")
    if th is None:                                    # what the game will do, from the theater and the takeoff place
        theater = spec.get("theater") or json.load(open(D / spec.get("mission", "mission_mem.json"))).get("theater")
        carrier = isinstance(spec.get("takeoff"), dict) and spec["takeoff"].get("carrier", True)   # as World.place
        th = round(game_takeoff_heading(theater, carrier), 1)
        print(f"takeoff heading {th:g} ({theater} {'carrier' if carrier else 'airbase'})")
    variants = [("_N", 0), ("_S", 180)] if th == "both" else [("", th)]
    ok = [emit(D, {**spec, "takeoff_heading": h}, suffix) for suffix, h in variants]
    if not all(ok): sys.exit("plan INVALID: the fix belongs in the planner or plan.json, not the route file")


if __name__ == "__main__":
    main()
