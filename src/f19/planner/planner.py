"""The mission planner: a plan spec + the mission in memory -> the chosen route, deterministically.

  Planner(spec, mission).run() -> (best, results)      (planner.api.plan_mission wraps it; cli.py reads / writes files)
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

The CLI (cli.py) writes <plan_dir>/route.json (nav.py --route), report.txt, route.png and terrain.npz.
With takeoff_heading "both": route_N.json + route_S.json (and report/png per direction);
give nav either one, it switches to the other if the plane sits on the runway the other way.
nav also refuses a route whose takeoff direction doesn't match the plane on the runway.
If the best plan breaks a limit it writes route.invalid.json instead and exits 1:
the report says what broke, and the fix belongs in the planner or the spec.
"""
import itertools, math
import numpy as np

from f19.core.theater import Theater
from f19.gamedata.wld import categories
from f19.rules import weapons

from . import fuelplan, loiter, probes, profiles
from .loiter import fit
from .radar import RadarModel
from .router import PlanError, Router, from_hdg, hdg, unit
from .world import GROUND_KMH_PER_KT as KT_KMH, K, World

MAX_PROBE_TRIES = 6
MAVERICK = 5                        # ordnance.py loadout index (AGM-65D)
MAV_HALF_ANGLE = 22.0               # target must be within this of the nose at launch
MAV_WINDOW_KM = (6.0, 14.5)         # missile reach ~14.5-15.5 km (ttl = R*32 units at top speed = the 16 km cue)
PHOTO_KM = {"green": 2.0, "regular": 2.0, "veteran": 2.0, "elite": 0.7}
FUEL_FALLBACK = (0.3, 0.1)          # radar price x these when the planned profile's route doesn't fit the fuel (api)


def fixed(pts):
    return dict(t="fixed", pts=[(tuple(map(float, p)), l, a) for p, l, a in pts])


def ends(el):
    """(entry xy, exit xy) of a sequence element."""
    if el["t"] == "fixed": return el["pts"][0][0], el["pts"][-1][0]
    return el["xy"], el["xy"]


# The game lines the plane up by theater alone (EGAME 0x1FC1, FINDINGS.md "Takeoff direction"):
# theater index bit 0 set (PG 1, CE 3) -> north, clear (LB 0, NC 2) -> south; a carrier adds 0x400 (5.6 deg).
THEATER_INDEX = {t.value: t.index for t in Theater}

def game_takeoff_heading(theater, carrier):
    """core.theater's rule in deg (0 / 180, carriers +5.625; exact in binary, so equal to the old formula)."""
    return Theater.parse(theater).takeoff_heading(bool(carrier))


def mission_dict(mission):
    """The planner works on the mission_mem.json dict (scripts/read_mission.py); a core Mission is converted."""
    if mission is None or isinstance(mission, dict): return mission
    tg = [dict(role=t.role, x=t.x, y=t.y, place=t.place, object=t.object, task=t.task,
               **({"record": t.record} if t.record is not None else {})) for t in mission.targets]
    rd = [dict(place=r.place, system=r.system, x=r.x, y=r.y, kind=r.radar_type, type="D" if r.doppler else "P",
               range_km=r.range_km, **({"record": r.record} if r.record is not None else {})) for r in mission.radars]
    out = dict(theater=mission.theater.value if mission.theater else None, targets=tg, radars=rd)
    for k in ("takeoff", "landing"):
        b = getattr(mission, k)
        if b: out[k] = dict(place=b.place, x=b.x, y=b.y, object=b.object, flags=b.flags, carrier=b.carrier)
    return out


class Planner:
    def __init__(self, spec, mission=None, label="", loiters=True):
        """spec: plan.json (dict); mission: mission_mem.json dict or a core Mission (None: no targets, no radars);
        label: shown in the report header (the CLI passes the plan dir); loiters=False skips the loiter search
        (in-flight routing builds a Planner once for its World / Router and never plans a loiter)."""
        self.spec, self.dir = spec, label
        self.mode = spec.get("mode", "mission")
        ov = dict(spec.get("overrides") or {})
        if "extra_fuel" in spec:
            n = spec["extra_fuel"]
            if not (isinstance(n, int) and 0 <= n <= 3): raise ValueError(f"extra_fuel must be 0-3 tanks, not {n!r}")
            ov["extra_fuel_bays"] = n
        self.P = profiles.load(spec.get("profile", "mountain_loiter" if self.mode == "mountain_loiter" else "balanced"), ov)
        self.M = mission_dict(mission) or dict(radars=[], targets=[], theater=spec.get("theater"))
        if self.mode == "mountain_loiter": self.M = {**self.M, "targets": []}
        self.th = spec.get("theater") or self.M.get("theater")
        if not self.th: raise ValueError("no theater in plan.json or the mission file")
        self.th = Theater.parse(self.th).value
        self.W = World(self.th, self.M, self.P, spec.get("settings"), spec.get("sa14_live"))
        self.regimes = fuelplan.Regimes(self.P)
        self.RM = RadarModel(self.W)
        self.router = Router(self.W, self.RM, self.P, self.regimes)
        self.RM.kt_default = self.router.kt_at
        self.R = self.router.R
        self.log = list(self.W.notes)
        self.loiters = self._loiter_choices() if loiters else []
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

    def landing(self, side, base=None):
        """Landing piece on the N or S side of `base` (default: the spec's landing)."""
        x, y, name, _ = self.W.place(self.spec["landing"] if base is None else base)
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
            _, m = weapons.tables()
            self._mav = {n: weapons.compat(m, MAVERICK, c) for n, c in categories(self.th).values()}
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
    def plan(self, seq, start_fuel=None, start_t=0.0, tanks_left=None, ends=None, margin_km=None, deadline=None,
             roles=None):
        """One complete plan of the sequence. Defaults: a mission from takeoff (full fuel at t 0, runway legs at both
        ends, every mission target checked for overflight). planner.api.route passes the current fuel / clock / tanks,
        which ends are runways, a smaller search box, a deadline and the target roles its goals contain (roles)."""
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
            pts, h_arr = self.router.search(clip(start), clip(end), h0, hg, margin_km, deadline)
            core += [dict(t="v", xy=tuple(p)) for p in self.router.reduce(pts)] + [B]
        core, V = self.router.repair(core, log, ends, deadline)
        pts = self.router.densify(V)
        overfly = {e["label"] for e in seq if e["t"] == "target"} | {l for e in seq for l in e.get("overfly_labels", ())}
        # Maverick targets are never overflown; overfly and photo targets are
        mav = {e["role"] for e in seq if e.get("attack") == "maverick"}
        errs = self.router.validate(pts, [t for t in self.M["targets"] if t["role"].rstrip("?") not in mav and
                                          (roles is None or t["role"].rstrip("?") in roles)], overfly, ends)
        errs += self.attack_checks(pts)
        bays = {l for p, l, a in pts if a.get("kind") in ("photo", "launch")}
        start = dict(start_fuel=start_fuel, start_t=start_t, tanks_left=tanks_left)
        fuel = fuelplan.integrate(self.W, self.RM, self.P, pts, bays, **start)
        split = fuelplan.split_at_alt(pts, fuel["steps"])           # one altitude per leg for nav
        if len(split) != len(pts):
            pts = split; fuel = fuelplan.integrate(self.W, self.RM, self.P, pts, bays, **start)
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
