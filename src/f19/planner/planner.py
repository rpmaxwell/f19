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
                                               # settings "sa14": "auto" (default: live SA-14 cells are walls from profile
                                               # sa14_hard_from_opp = Elite, a price below; a secondary that needs one is skipped)
                                               # | "hard" (walls at any level) | "soft" (the price only, as before)
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
import itertools, math, os, sys
import numpy as np

from f19.core.theater import Theater
from f19.gamedata.wld import categories
from f19.rules import trackcam, weapons

from . import alertsim, fighters, fuelplan, loiter, points, pool, probes, profiles
from .estimate import lb_per_km
from .loiter import fit
from .radar import RadarModel
from .router import CACHE_CHECK, PlanError, Router, from_hdg, hdg, unit
from .world import GROUND_KMH_PER_KT as KT_KMH, K, World

OPP_NAME = {0: "green", 1: "regular", 2: "veteran", 3: "elite"}

MAX_PROBE_TRIES = 6
MAVERICK = 5                        # ordnance.py loadout index (AGM-65D)
MAV_HALF_ANGLE = 22.0               # target must be within this of the nose at launch
MAV_WINDOW_KM = (6.0, 14.5)         # missile reach ~14.5-15.5 km (ttl = R*32 units at top speed = the 16 km cue)
# Trackcam acquisition (docs/design/target_acquisition_task.md; rules.trackcam): the run-in must hold a stretch of at least
# ACQ_MIN_KM where "/" then at most ACQ_MAX_PRESSES B presses lock the target. A run-in that has none is lengthened by
# ACQ_EXTEND_KM (cumulative steps) before the bearing is dropped. ACQ_PHOTO_BAY_KM: the autopilot opens the bay this far
# before the photo point (Attack.PHOTO_BAY_KM), the lock has to be there before.
ACQ_MIN_KM, ACQ_MAX_PRESSES, ACQ_STEP_KM = 3.0, 8, 0.25
ACQ_EXTEND_KM, ACQ_PHOTO_BAY_KM = (2.0, 4.0, 6.0), 3.0
PHOTO_KM = {"green": 2.0, "regular": 2.0, "veteran": 2.0, "elite": 0.7}
FUEL_FALLBACK = (0.3, 0.1)          # radar price x these when the planned profile's route doesn't fit the fuel (api)


def fixed(pts):
    return dict(t="fixed", pts=[(tuple(map(float, p)), l, a) for p, l, a in pts])


def freeze(x):
    """x (sequence elements: dicts, lists, tuples, numpy arrays, numbers, strings) as a hashable value that is equal exactly
    when x is, for memo keys."""
    if isinstance(x, dict): return tuple((k, freeze(v)) for k, v in sorted(x.items(), key=lambda kv: str(kv[0])))
    if isinstance(x, (list, tuple)): return tuple(freeze(v) for v in x)
    if isinstance(x, np.ndarray): return (x.shape, x.tobytes())
    if isinstance(x, np.generic): return x.item()
    return x


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
        self.skipped = []                  # [(target, why)] left out of the plan (run: SA-14 cells)
        self.skipped_options = []          # [(tag, why)] loiter combinations ruled out by loiter_bound (not planned)
        self._mission0 = self.M            # what pool workers rebuild this Planner from (self.M loses skipped targets)
        self._sa14_blocked = 0
        self._acq_blocked = 0              # attack bearings dropped for having no trackcam acquisition window
        self._attack_memo = {}             # attack_options() results by (previous piece, target, next piece, mode)
        self._pool, self._pool_dead, self._in_run = None, False, False     # worker processes live only inside _run
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
        """Fixed piece for attacking target t on bearing b (deg), or None when it hits terrain / the map edge, or when the
        trackcam cannot be sure to lock the target on the run-in (acquisition_window). The run-in is lengthened by
        ACQ_EXTEND_KM steps while that helps; the piece carries the window in the run-in point's "acq" attribute."""
        T, u, P = np.array(el["xy"]), from_hdg(b), self.P
        role, name = el["role"], f"{t['object']} {t['place']}"
        at = lambda kind, action: {"kind": kind, "role": role, "action": action}
        if mode == "maverick": near = P["maverick_launch_km"]
        else: near = (P["photo_km"] or PHOTO_KM.get(self.spec.get("settings", {}).get("opponent", "regular"), 2.0)) + ACQ_PHOTO_BAY_KM
        acq, runin = None, P["attack_runin_km"]
        for extra in (0.0,) + ACQ_EXTEND_KM:
            runin = P["attack_runin_km"] + extra
            acq = self.acquisition_window(t, T, u, b, near, near + runin)
            if acq is None or acq["ok"]: break
        if acq is not None and not acq["ok"]: self._acq_blocked += 1; return None
        if mode == "maverick":
            L = T - u * P["maverick_launch_km"] * K; S = L - u * runin * K
            pts = [(S, f"{role.upper()} run-in", at("runin", f"Maverick run-in on {b:.0f}: select {name}, cruise height and speed")),
                   (L, f"{role.upper()}: Maverick launch at {name}",
                    at("launch", f"LEVEL launch at {P['maverick_launch_km']:.0f} km (hexagon red): bay open, FIRE Maverick, bay closed"))]
        else:
            ph = P["photo_km"] or PHOTO_KM.get(self.spec.get("settings", {}).get("opponent", "regular"), 2.0)
            F = T - u * ph * K; S = F - u * runin * K
            pts = [(S, f"{role.upper()} run-in", at("runin", f"photo run-in on {b:.0f}: camera + {name} selected, straight at it")),
                   (F, f"{role.upper()}: photo point {ph:g} km", at("photo", "PHOTO: bay open, nose on target, FIRE camera, bay closed")),
                   (T, el["label"], at("overfly", "frame taken? then turn away"))]
        if acq is not None: pts[0][2]["acq"] = {k: acq[k] for k in ("far_km", "near_km", "len_km", "presses", "ok")}
        xy = [p for p, _, _ in pts]
        if not all(64 <= c <= self.W.size - 64 for p in xy for c in p): return None
        if not all(self.W.segment_clear(a, c) for a, c in zip(xy, xy[1:])): return None
        if self.W.sa14_hard and not all(self.W.sa14_clear(a, c) for a, c in zip(xy, xy[1:])):
            self._sa14_blocked += 1; return None             # the run-in / photo point would sit in a live SA-14 cell
        piece = fixed(pts); piece.update(role=role, attack=mode, bearing=b); return piece

    def acquisition_window(self, t, T, u, b, near_km, far_km):
        """Where on the straight run-in (distances near_km..far_km before the target T, heading b) the trackcam can lock the
        target by "/" then B: the target is inside the game's cone and range with the planner's margin
        (rules.trackcam.SAFE_*) and the decoded pick reaches it in at most ACQ_MAX_PRESSES B presses. Returns the longest
        such stretch as dict(far_km, near_km, len_km, presses (the most in the stretch), ok (len_km >= ACQ_MIN_KM)), or None
        when the target has no record in the world's table (nothing to check). Every record is assumed alive; the
        aircraft records and the scratch record's later position are not known to the planner (the autopilot checks live)."""
        rec = t.get("record")
        recs = self.W.trackcam_records()
        if rec is None or not any(r[0] == rec for r in recs): return None
        Tt, night, best, run = (float(T[0]), float(T[1])), self.W.night, None, None
        d = near_km
        while d <= far_km + 1e-9:
            p = (Tt[0] - u[0] * d * K, Tt[1] - u[1] * d * K)
            n = None
            if trackcam.acquisition_ok(p, b, Tt, night):
                n = trackcam.slash_then_presses(rec, trackcam.candidates(p, b, 0, night, recs))
                n = None if n is None or n.presses > ACQ_MAX_PRESSES else n.presses
            if n is None: run = None
            elif run is None: run = [d, d, n]
            else: run[1] = d; run[2] = max(run[2], n)
            if run and (best is None or run[1] - run[0] > best[1] - best[0]): best = list(run)
            d += ACQ_STEP_KM
        if best is None: return dict(far_km=0.0, near_km=0.0, len_km=0.0, presses=0, ok=False)
        return dict(far_km=round(best[1], 2), near_km=round(best[0], 2), len_km=round(best[1] - best[0], 2),
                    presses=best[2], ok=best[1] - best[0] >= ACQ_MIN_KM)

    def attack_options(self, seq, k, t, mode):
        """Cheapest attack bearings for seq[k] (a plain target): for each bearing the stretch
        previous piece -> attack piece -> next piece is searched and expanded with the real
        minimum-radius joins (plan() without the repair loop) and costed. A plain target next door
        counts as a point; the one before is already placed (see with_attacks)."""
        key = freeze((seq[k - 1], seq[k], seq[k + 1], t, mode, self.router.sa14_hard))
        hit = self._attack_memo.get(key)
        if hit is not None and not CACHE_CHECK:
            out, line, n14 = hit
            self.log.append(line); self._sa14_blocked += n14
            return list(out)
        log_n, blocked_before = len(self.log), self._sa14_blocked
        out = self._attack_options(seq, k, t, mode)
        if hit is not None and freeze(hit[0]) != freeze(out): raise AssertionError("attack_options memo stale")
        self._attack_memo[key] = (tuple(out), self.log[log_n], self._sa14_blocked - blocked_before)
        return out

    def stretch_cost(self, sub):
        """Cost of the stretch sub[0] -> sub[1] -> ...: searched, reduced and expanded with the real minimum-radius joins
        (plan() without the repair loop), plus the open-bay exposure of sub[1]. A PlanError if a leg cannot be routed."""
        clip = lambda p: tuple(np.clip(p, 64, self.W.size - 64))
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

    def _attack_options(self, seq, k, t, mode):
        el = seq[k]
        ranked, blocked, failed = [], 0, []
        sa14_before, acq_before = self._sa14_blocked, self._acq_blocked
        cands = []
        for b in np.arange(0, 360, self.P["attack_bearing_step_deg"]):
            piece = self.attack_piece(el, t, mode, float(b))
            if piece is None: blocked += 1; continue
            cands.append((float(b), piece))
        for (b, piece), c in zip(cands, self._stretch_costs([[seq[k - 1], piece, seq[k + 1]] for _, piece in cands])):
            if isinstance(c, PlanError): failed.append(f"{b:.0f}: {c}"); continue
            ranked.append((c, b, piece))
        ranked.sort(key=lambda r: r[:2])
        if not ranked:
            n14 = self._sa14_blocked - sa14_before
            acq = self._acq_blocked - acq_before
            raise PlanError(f"{el['label']}: no usable attack bearing ({blocked} hit terrain"
                            + (f", the map edge or an SA-14 cell ({n14} of them SA-14)" if self.W.sa14_hard else " or the map edge")
                            + (f", {acq} had no trackcam acquisition window (target not lockable by / and <= {ACQ_MAX_PRESSES} B "
                               f"over {ACQ_MIN_KM:g} km of run-in)" if acq else "")
                            + f", {len(failed)} could not be routed" + (f", e.g. {failed[0]}" if failed else "") + ")")
        self.log.append(f"{el['role']} attack bearings by estimate: " + ", ".join(f"{b:.0f} ({c:.0f})" for c, b, _ in ranked[:6]))
        return [p for _, _, p in ranked[:int(self.P["attack_candidates"])]]

    def _stretch_costs(self, subs):
        """stretch_cost of each sub, or the PlanError it raised. The legs are searched and reduced in workers first
        (_prefetch); what is left is joining them, which is quick."""
        self._prefetch(subs)
        out = []
        for sub in subs:
            try: out.append(self.stretch_cost(sub))
            except PlanError as e: out.append(e)
        return out

    def _legs(self, seq):
        """The searches plan() / stretch_cost() make for seq, as router.search argument tuples, as far as they are known
        without running a search: a leg that starts at a plain point (a target flown straight through) starts on the arrival
        heading of the leg before, so it waits for that one."""
        clip = lambda p: tuple(np.clip(p, 64, self.W.size - 64))
        out, h_arr, known = [], None, True
        for A, B in zip(seq, seq[1:]):
            if A["t"] == "fixed": start, h0 = A["pts"][-1][0], hdg(unit(np.subtract(A["pts"][-1][0], A["pts"][-2][0])))
            elif not known: break
            else: start, h0 = A["xy"], h_arr
            if B["t"] == "fixed": end, hg = B["pts"][0][0], hdg(unit(np.subtract(B["pts"][1][0], B["pts"][0][0])))
            else: end, hg = B["xy"], None
            key = (clip(start), clip(end), h0, hg, None)
            out.append(key)
            hit = self.router.search_peek(*key)
            if hit is None or hit[0] == "err": known = False
            else: h_arr = hit[1][1]
        return out

    def _prefetch(self, seqs):
        """Run the searches (and the reduces of their paths) the sequences need that are not memoized yet in worker
        processes, put the results in this router's memos and in the workers'. Several waves when a leg's start heading
        comes from the leg before. A no-op without workers (the planning then runs them as it goes)."""
        wp = self._workers()
        if wp is None: return
        seqs = [self._resolve_static(seq) for seq in seqs]
        try:
            while True:
                todo = {}
                for seq in seqs:
                    for key in self._legs(seq):
                        if key not in todo and self.router.search_peek(*key) is None: todo[key] = None
                if not todo: return
                replies = wp.map([("search", *key) for key in todo])
                searches, reduces = {}, {}
                for key, rep in zip(todo, replies):
                    if rep[0] == "exc": raise RuntimeError("planner worker failed:\n" + rep[1])
                    self.router.notes.update(rep[-1])
                    sk = (tuple(map(float, key[0])), tuple(map(float, key[1])), key[2], key[3], key[4], self.router.sa14_hard)
                    if rep[0] == "err": searches[sk] = ("err", rep[1]); continue
                    searches[sk] = ("ok", rep[1])
                    rk = (tuple(map(tuple, rep[1][0])), self.router.sa14_hard)
                    reduces[rk] = tuple(rep[2][1]) if rep[2][0] == "ok" else rep[2][1]
                self.router.seed(searches, reduces)
                wp.broadcast(("seed", searches, reduces))
        except pool.WorkerError as e:
            self._workers_failed(e)

    def _resolve_static(self, seq):
        """seq for _legs: the loiter and orbit elements need searches of their own to be resolved, so the legs next to them
        are left to the planning itself (the sequence is cut at the first one)."""
        k = next((i for i, e in enumerate(seq) if e["t"] in ("loiter", "orbit")), None)
        return seq if k is None else seq[:k]

    # ---- worker processes (pool.py)
    def _workers(self):
        """The pool of worker processes for this run, started on first use; None when one process is to do the work."""
        if self._pool is None and not self._pool_dead and self._in_run:
            n = pool.workers_wanted()
            if n > 1:
                try: self._pool = pool.Pool(n, (self.spec, self._mission0, self.M, self.router.sa14_hard))
                except (pool.WorkerError, OSError) as e: self._workers_failed(e)
        return self._pool

    def _workers_failed(self, e):
        print(f"planner: worker processes failed ({e}); continuing in this process", file=sys.stderr)
        self._pool_dead = True
        self._close_workers()

    def _close_workers(self):
        if self._pool is not None: self._pool.close(); self._pool = None

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
        have = {t["role"].rstrip("?") for t in self.M["targets"]}
        return dict(seq=seq, V=V, pts=pts, cost=self.router.route_cost(V) + self.bay_cost(pts), errors=errs, log=log, fuel=fuel,
                    roles=have if roles is None else have & set(roles))

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

    def plan_with_loiter(self, seq, roles=None):
        """Plan with loiter_min_laps, then as many laps as the fuel above the reserve buys (re-planned until
        the landing fuel sits between the reserve and the reserve + one lap). Options are ranked by the
        min-lap route's cost: laps only add samples."""
        k = next((i for i, e in enumerate(seq) if e["t"] == "loiter"), None)
        r = self.plan(seq, roles=roles)
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
            r = self.plan(seq[:k] + [self.loiter_el(el["fit"], laps)] + seq[k + 1:], roles=roles)
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

    # ------------------------------------------------------------ SA-14 cells
    def _drop(self, t, why):
        """Leave target t out of the plan (validation, ordering and the report then work on the targets that remain)."""
        self.M = {**self.M, "targets": [x for x in self.M["targets"] if x is not t]}
        self.skipped.append((t, why))
        self.log.append(f"SKIPPED {t['role'].rstrip('?')} {t['object']} {t['place']}: {why}")

    def _sa14_forced(self):
        """[(target, why)] to skip: a target inside a live (hard) SA-14 cell whose attack has to fly over it (photo, overfly;
        a Maverick is launched from outside). Only the secondary can go: the primary in a cell, or roles memory can't tell
        apart, is a PlanError."""
        if not (self.W.sa14_hard and self.mode == "mission"): return []
        out = []
        for t in self.M["targets"]:
            c = self.W.sa14_cell_of(t["x"], t["y"])
            if c is None: continue
            mode = self.attack_mode(t)[0]
            if mode == "maverick": continue
            role = t["role"]
            why = f"{t['object']} {t['place']} lies inside SA-14 cell {c} and its {mode} attack has to fly over it"
            if role != "secondary":
                raise PlanError(f"{role.upper()}: {why}. By day at {OPP_NAME[self.W.opp]} the cell fires on us (a launch raises the network "
                                f"alert).{' The roles are not known (memory read in flight?), so it may be the primary.' if role.endswith('?') else ''} "
                                f"Plan it anyway with settings \"sa14\": \"soft\", or fly it at night.")
            out.append((t, why))
        return out

    # ------------------------------------------------------------ choices
    def run(self):
        """-> (best, results). With live SA-14 cells as walls (world.sa14_hard) a secondary the route can only reach through a
        cell is skipped, and so is one whose every option failed or broke the SA-14 rule; the primary never is (PlanError)."""
        for t, why in self._sa14_forced(): self._drop(t, why)
        out = err = None
        try: out = self._run()
        except PlanError as e: err = e
        sec = next((t for t in self.M["targets"] if t["role"] == "secondary"), None)
        hit = "SA-14" in str(err) if err else any("SA-14" in e for e in out[0]["errors"])
        if self.W.sa14_hard and self.mode == "mission" and sec is not None and hit:
            keep, log = self.M, list(self.log)
            self._drop(sec, "no valid route to it without entering a live SA-14 cell")
            try:
                alt = self._run()
                if not alt[0]["errors"]: return alt
            except PlanError: pass
            self.M, self.log = keep, log; self.skipped.pop()      # skipping it didn't help: report the original failure
        if err and self.W.sa14_hard and self.router.sa14_hard and "SA-14" in str(err):
            # no route keeps clear of the live cells (a pocket whose only way out is through them): plan with them as a
            # price so the report shows what the best route does. Validation still flags every entry, so such a plan fails.
            self.router.set_sa14_walls(False)
            try:
                out = self._run()
                bad = [e for e in out[0]["errors"] if "SA-14" in e]
                self.log.append(f"NO ROUTE KEEPS {self.P['sa14_margin_km']:g} KM CLEAR OF THE LIVE SA-14 CELLS ({str(err).splitlines()[0][:150]}): "
                                f"planned with them as a price instead" + (f", {len(bad)} entries: INVALID" if bad else "")
                                + ". Re-roll the mission (mission_finder --no-sa14), fly it at night, or plan with settings "
                                  "\"sa14\": \"soft\" to accept the launches.")
                return out
            except PlanError:
                pass
            finally:
                self.router.set_sa14_walls(True)
        if err: raise err
        return out

    def _run(self):
        self._in_run = True
        try: return self._run_options()
        finally: self._in_run = False; self._close_workers()

    def _run_options(self):
        roles_known = all(not t["role"].endswith("?") for t in self.M["targets"])
        order_mode = self.spec.get("target_order", "primary-first")
        if not roles_known and order_mode != "auto":
            self.log.append("memory can't tell primary from secondary (same target type): planning both orders")
            order_mode = "auto"
        have = {t["role"].rstrip("?") for t in self.M["targets"]}
        orders = [tuple(r for r in ("primary", "secondary") if r in have)]
        if order_mode == "auto" and len(have) == 2: orders.append(("secondary", "primary"))
        if self.objective != "cost" and have == {"primary", "secondary"} and self.mode == "mission":
            orders.append(("primary",))               # the secondary is optional once time in the air costs points
        sides = [self.spec["approach"]] if self.spec.get("approach", "auto") != "auto" else ["N", "S"]
        if self.mode == "mountain_loiter":
            if not self.loiters: raise PlanError("mountain_loiter: no loiter cell (see the planner log)")
            orders = [()]
        # A loiter is a post-pass on the plan without one (_loiter_pass); only mountain_loiter plans a loiter from the start.
        loiters = self.loiters if self.mode == "mountain_loiter" else [None]
        combos = list(itertools.product(orders, sides, loiters))
        opts, _, skipped = (self._run_serial(combos) if os.environ.get("F19_PLAN_LEGACY") else self._run_phased(combos))
        if self.loiters and self.mode != "mountain_loiter": opts += self._loiter_pass(opts, len(combos))
        for o in opts:                                # the log keeps the order the options were found in
            if "r" in o:
                r = o["r"]; r["tag"] = o["tag"]
                self.log[o["at"]] = f"option {o['tag']}: cost {r['cost']:.0f}, {len(r['errors'])} errors"
            else:
                self.log[o["at"]] = f"option {o['tag']}: FAILED {o['err']}"
        results = [o["r"] for o in opts if "r" in o]
        failures = [f"{o['tag']}: {o['err']}" for o in opts if "err" in o]
        self.skipped_options = skipped
        for tag, why in skipped: self.log.append(f"option {tag}: SKIPPED {why}")
        if not results:
            raise PlanError("no option could be planned:\n  " + "\n  ".join(failures))
        # A plan that fails: show the one CLOSEST to working (least fuel short), not the best-scoring one
        # (2026-10-21 Lukow at Elite: every option ran dry and the report showed the longest, a loiter -5226 lb).
        return self.rank(results)

    @property
    def objective(self):
        return self.spec.get("objective") or self.P["objective"]

    def assumptions(self):
        st = self.spec.get("settings") or {}
        hz = self.P["hit_per_min"]
        return points.Assumptions(roe=st.get("roe", 0), landing=st.get("landing", "realistic"), fuel_estimate=st.get("fuel_estimate", 9000),
                                  hit_per_min=points.HIT_PER_MIN[self.W.opp] if hz is None else hz, p_hit_launch=self.P["p_hit_launch"])

    def points_eval(self, r):
        """E[points] - lambda P(death) of a planned route under the objective (points.value), cached on r["points_eval"]."""
        if "points_eval" in r: return r["points_eval"]
        steps = r["fuel"]["steps"]; A = self.assumptions()
        sim = alertsim.simulate(self.W, self.RM, steps)
        T = points.terms(self, r, sim, r["roles"])
        ac = points.accrual(self, r, T, A)
        extra = {}
        if self.objective == "aircraft":
            net = (sim.get("net") or {}).get("expected") or [list(l) for l in sim["sa14"]["launches"]]
            scen = fighters.simulate(self.W, self.RM, steps, net, self.th, hunt=self.P["fighter_hunt"])
            extra = fighters.summary(scen); sc = points.fighter_hazard(ac, scen, A)
        else:
            sc = points.constant_hazard(ac, A.hit_per_min)
        r["points_eval"] = dict(points.value(ac, T, A, self.W.opp, sc), minutes=r["fuel"]["t_end"] / 60, roles=sorted(r["roles"]),
                                p_net=(sim.get("summary") or {}).get("expected", {}).get("p_any_net"), **extra)
        return r["points_eval"]

    def rank(self, results):
        """-> (best, results sorted best first). The seam an objective other than route cost plugs into: objective "points" /
        "aircraft" order the valid options by points.value, "cost" by route cost."""
        short = lambda r: max(0.0, self.P["landing_reserve_lb"] - r["fuel"]["fuel_land"])
        if self.objective != "cost":
            for r in results:
                if not r["errors"]: self.points_eval(r)
            results.sort(key=lambda r: (len(r["errors"]) > 0, short(r) if r["errors"] else 0,
                                        -r["points_eval"]["value"] if not r["errors"] else 0, r["cost"]))
            return results[0], results
        if self.loiters:
            # loiter options earn samples the route cost doesn't see: rank valid ones by points (samples minus
            # the pulse cost of the expected seen pulses), the same currency as the objective
            for r in results: r["points"] = self.points_proxy(r)
            results.sort(key=lambda r: (len(r["errors"]) > 0, short(r) if r["errors"] else 0, -r["points"], r["cost"]))
        else:
            results.sort(key=lambda r: (len(r["errors"]) > 0, short(r) if r["errors"] else 0, r["cost"]))
        return results[0], results

    # ---- options: enumerate (attack bearings, probes), prune, plan (here or in workers)
    def _tag(self, order, side, lo, attack_desc, probe_desc):
        lo_desc = f"loiter {tuple(lo['cell'])} {lo['quadrant']}" if lo else ""
        return ", ".join(x for x in ((f"order {'/'.join(order)}, " if order else "") + f"approach {side}", attack_desc, lo_desc, probe_desc) if x)

    def _roles(self, order):
        """The roles an option has to satisfy: None = every mission target, else the subset it flies (primary only)."""
        have = {t["role"].rstrip("?") for t in self.M["targets"]}
        return None if set(order) >= have else frozenset(order)

    def _enumerate(self, ci, order, side, lo, out):
        """Append one dict per option of the combination (order, side, loiter) to out: key (combination, n), tag, seq, and
        the log slot its outcome line will fill. Lazy over with_attacks / _with_probes: a PlanError there leaves out as far
        as it got."""
        mid = [self.loiter_el(lo, self.P["loiter_min_laps"])] if lo else []
        plain = [self.takeoff(), *self.targets(order), *mid, self.landing(side)]
        n = 0
        for (base, attack_desc), (seq, probe_desc) in ((ba, sp) for ba in self.with_attacks(plain) for sp in self._with_probes(ba[0])):
            out.append(dict(key=(ci, n), tag=self._tag(order, side, lo, attack_desc, probe_desc), seq=seq, lo=lo, order=order, side=side,
                            roles=self._roles(order), at=len(self.log)))
            self.log.append("")                                      # filled when the option has been planned
            n += 1

    def _run_serial(self, combos):
        """The options one after the other, each planned as soon as it is found (what the planner always did)."""
        opts, skipped = [], []
        for ci, (order, side, lo) in enumerate(combos):
            new = []
            try: self._enumerate(ci, order, side, lo, new)
            finally: self._plan_batch(new, parallel=False); opts += new
        return opts, [], skipped

    def _run_phased(self, combos):
        """Options without a loiter first; then the loiter combinations that the fuel bound (loiter_bound) cannot rule out,
        provided some option without one is valid (a loiter option that cannot be valid cannot be chosen then). The options of
        a phase are planned together, in workers when there are enough of them. A PlanError while finding options is the
        serial planner's: the whole run is redone that way so the error (and the log) is the one it always gave."""
        log0, blocked0, notes0 = len(self.log), self._sa14_blocked, set(self.router.notes)
        opts, skipped = [], []
        try:
            for phase in (False, True):
                new = []
                for ci, (order, side, lo) in enumerate(combos):
                    if (lo is not None) != phase: continue
                    if phase and not os.environ.get("F19_PLAN_NO_PRUNE") and any(not o["r"]["errors"] for o in opts if "r" in o):
                        why = self.loiter_bound(order, side, lo)
                        if why:
                            skipped.append((self._tag(order, side, lo, "", ""), why)); continue
                    self._enumerate(ci, order, side, lo, new)
                self._plan_batch(new)
                opts += new
        except PlanError:
            del self.log[log0:]; self._sa14_blocked = blocked0; self.router.notes &= notes0
            return self._run_serial(combos)
        opts.sort(key=lambda o: o["key"])
        return opts, [], skipped

    def _loiter_pass(self, opts, n_combos):
        """The loiter as an opt-in: with the plan planned without one, see whether the fuel above the reserve buys a loiter, and
        if so plan it as one more pass on the chosen option (the same sequence with the racetrack before the landing). A loiter
        is considered when its straight-line lower bound (loiter_fuel_need: the detour to the racetrack plus the minimum laps)
        is no more than the spare fuel of the chosen plan. The planner log says which, and why. -> the new options."""
        valid = [o for o in opts if "r" in o and not o["r"]["errors"]]
        if not valid:
            self.log.append("loiter not considered: no plan without one is valid")
            return []
        best, _ = self.rank([o["r"] for o in valid])
        chosen = next(o for o in valid if o["r"] is best)
        spare = best["fuel"]["fuel_land"] - self.P["landing_reserve_lb"]
        base = self.loiter_fuel_need(chosen["order"], chosen["side"], None)
        new = []
        for lo in self.loiters:
            need = self.loiter_fuel_need(chosen["order"], chosen["side"], lo)
            what = f"loiter {tuple(lo['cell'])} {lo['quadrant']}"
            extra = 0.0 if need is None else need - base
            if extra <= spare:
                self.log.append(f"loiter considered: {what} needs >= {extra:.0f} lb more than the plan without one, "
                                f"{spare:.0f} lb spare above the reserve")
                new.append(dict(key=(n_combos, len(new)), tag=f"{chosen['tag']}, {what}", lo=lo, order=chosen["order"], side=chosen["side"],
                                seq=chosen["seq"][:-1] + [self.loiter_el(lo, self.P["loiter_min_laps"]), chosen["seq"][-1]], at=len(self.log)))
                self.log.append("")
            else:
                self.log.append(f"loiter not considered: {what} needs >= {extra:.0f} lb more than the plan without one, only "
                                f"{spare:.0f} lb spare above the reserve")
        self._plan_batch(new)
        return new

    def _plan_batch(self, opts, parallel=True):
        """Set o["r"] (the plan_with_loiter result) or o["err"] (its PlanError text) on each option. In worker processes
        when F19_PLAN_WORKERS allows; the results do not depend on where they were computed."""
        wp = self._workers() if parallel and len(opts) > 1 else None
        if wp is not None:
            self._prefetch([o["seq"] for o in opts])
            wp = self._pool
        if wp is not None:
            try: replies = wp.map([("plan", o["seq"], o.get("roles")) for o in opts])
            except pool.WorkerError as e: self._workers_failed(e)
            else:
                for o, rep in zip(opts, replies):
                    if rep[0] == "exc": raise RuntimeError("planner worker failed:\n" + rep[1])
                    self.router.notes.update(rep[2])
                    if rep[0] == "ok": o["r"] = rep[1]
                    else: o["err"] = rep[1]
                self._check_bounds(opts)
                return
        for o in opts:
            try: o["r"] = self.plan_with_loiter(o["seq"], o.get("roles"))
            except PlanError as e: o["err"] = str(e)
        self._check_bounds(opts)

    def _check_bounds(self, opts):
        """F19_PLAN_CACHE_CHECK: every planned loiter option must have burned at least what loiter_fuel_need says."""
        if not CACHE_CHECK: return
        for o in opts:
            if o["lo"] is None or "r" not in o: continue
            used = o["r"]["fuel"]["fuel0"] - o["r"]["fuel"]["fuel_land"]
            need = self.loiter_fuel_need(o["order"], o["side"], o["lo"])
            if need is not None and need > used + 1e-6:
                raise AssertionError(f"loiter fuel bound {need:.1f} lb exceeds the {used:.1f} lb option {o['tag']} used")

    # ---- the fuel bound
    def loiter_fuel_need(self, order, side, lo):
        """A lower bound (lb) on the fuel any VALID plan of this order / approach side / loiter burns, or None when the profile
        has no single burn per km (no nav_hold_kt). With nav_hold_kt every step burns nav_lb_min at nav_hold_kt, so fuel is
        lb_per_km x the route's length; the length is at least the straight lines through what the route must visit: the
        takeoff and landing pieces, each target (a valid plan passes within 0.5 km of an overfly / photo target; a Maverick
        target only within maverick_launch_km of its launch point), and a vertex of the racetrack, then loiter_min_laps laps."""
        P = self.P
        if not P.get("nav_hold_kt"): return None
        tg = {t["role"].rstrip("?"): t for t in self.M["targets"]}
        way = [(self.takeoff()["pts"][-1][0], 0.0)]                  # (xy, slack): the route passes within slack of xy
        for el in self.targets(order):
            mode = self.attack_mode(tg[el["role"]])[0]
            way.append((el["xy"], (P["maverick_launch_km"] if mode == "maverick" else 0.5) * K))
        poly = lambda pts: sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
        tk, ld = [p for p, _, _ in self.takeoff()["pts"]], [p for p, _, _ in self.landing(side)["pts"]]
        way.append((ld[0], 0.0))
        gap = lambda a, b: max(0.0, math.dist(a[0], b[0]) - a[1] - b[1])
        km = poly(tk) + poly(ld)
        if lo is None:
            km += sum(gap(a, b) for a, b in zip(way, way[1:]))
        else:
            V = loiter.racetrack_path(lo["centre"], math.radians(lo["theta"]), lo["half_km"], P["loiter_radius_km"], 1.5)[:-1]
            edges = [math.dist(a, b) for a, b in zip(V, V[1:] + V[:1])]
            near = lambda a: min(math.dist(a[0], v) - a[1] for v in V)
            km += sum(gap(a, b) for a, b in zip(way[:-1], way[1:-1]))
            km += max(0.0, near(way[-2])) + max(0.0, near(way[-1])) + P["loiter_min_laps"] * sum(edges) - max(edges)
        return 0.999 * km / K * lb_per_km(P)

    def loiter_bound(self, order, side, lo):
        """Why a loiter combination cannot be valid ("needs >= N lb more fuel than carried"), or None if it might be."""
        need = self.loiter_fuel_need(order, side, lo)
        if need is None: return None
        P = self.P
        short = need - (P["fuel_loaded_lb"] + fuelplan.TANK_LB * P["extra_fuel_bays"] - P["landing_reserve_lb"])
        if short <= 0: return None
        return f"needs >= {short:.0f} lb more fuel than carried (the burn alone, straight lines, is {need:.0f} lb; reserve {P['landing_reserve_lb']:.0f} lb)"

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
