"""Routing: grid search -> fewest legs -> fillets at the minimum turn radius -> validation.

Stages
  1. search()   A* on the 2 km grid with heading states: decides the topology (which side
                of each radar and mountain). Each move costs its objective (profiles.py);
                heading may change one direction step per move.
  2. reduce()   exact DP over the search path: the fewest corners whose straight legs stay
                clear of walls, minimising  sum(leg cost) + corner cost (turn_simplicity_km + the
                extra radar exposure of the banked arc).
                Outside radar sight a leg costs only its length, so friendly / unwatched
                airspace comes out as straight legs; corners are kept where radar makes
                them pay.
  3. expand()   fixed pieces (runway climb-out, approach, targets flown straight through,
                probes) keep their headings: they are joined to free corners by arc +
                straight and to each other by Dubins arc + straight + arc, all at R.
  4. repair()   every corner is a fillet of radius R = min_turn_radius_km, which needs
                R*tan(turn/2) of straight leg on each side. Where a leg is too short the
                planner drops or merges search corners (cheapest first) until it fits.
                It never hands this back to be fixed by hand: no fix = PlanError.
  5. densify()  arcs sampled every ~4 km for the nav.
  6. validate() independent check of the dense route: every kink flyable at R, terrain
                clear, targets overflown, fuel limit.

Runway ends: a mission route starts on the takeoff runway and ends on the landing runway, and those two legs sit on
the bases (walls may be near), so the wall checks skip them. `ends` = (first leg exempt, last leg exempt), default
RUNWAY_ENDS (both); an in-flight route from the current pose passes (False, landing?) (planner.api.route).
"""
import heapq, math, os, time
from functools import lru_cache
import numpy as np

from . import fuelplan
from .world import CELL, CELL_KM, GROUND_KMH_PER_KT as KT_KMH, K, _dilate

MOVES = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if (dx, dy) != (0, 0) and math.gcd(abs(dx), abs(dy)) == 1]
MOVES.sort(key=lambda m: math.atan2(m[0], -m[1]) % (2 * math.pi))
NDIR = len(MOVES)
DIR_ANG = [math.degrees(math.atan2(dx, -dy)) % 360 for dx, dy in MOVES]
SEARCH_TURN_KM_PER_DEG = 0.1      # keeps the grid path from zig-zagging; the real turn cost is corner_cost()
SEARCH_MARGIN_KM = 120.0          # search box around the two ends (a mission plan; api.route passes a smaller one)
RUNWAY_ENDS = (True, True)        # (first leg, last leg) sit on runways: exempt from the wall checks
ARC_STEP_KM = 4.0
SA14_PAD_CELLS = 2                # 4 km
SA14_DETOUR_KM = 600.0            # search box when live SA-14 cells (walls) leave no path in the usual one
LOITER_KINDS = ("loiter", "loiter_runin", "loiter_runout")   # loiter.KINDS: legs that keep loiter_clear_km


class PlanError(Exception):
    pass


# F19_PLAN_CACHE_CHECK=1: every memoized result is recomputed on a hit and compared (a stale hit is a silent wrong route)
CACHE_CHECK = bool(os.environ.get("F19_PLAN_CACHE_CHECK"))


class BudgetExceeded(PlanError):
    """A bounded search (api.route) ran out of its time budget."""


def _leg_range(n_legs, ends):
    """range of leg indices the wall checks look at: all but the exempt runway ends."""
    first, last = RUNWAY_ENDS if ends is None else ends
    return range(1 if first else 0, n_legs - 1 if last else n_legs)


def hdg(v): return math.degrees(math.atan2(v[0], -v[1])) % 360
def unit(v): v = np.asarray(v, float); n = np.linalg.norm(v); return v / n if n else np.array([0.0, -1.0])
def from_hdg(h): r = math.radians(h); return np.array([math.sin(r), -math.cos(r)])
def turn_deg(a, b, c):
    u, w = unit(np.subtract(b, a)), unit(np.subtract(c, b))
    return math.degrees(math.acos(max(-1.0, min(1.0, float(u @ w)))))
def nearest_dir(h): return min(range(NDIR), key=lambda k: abs((DIR_ANG[k] - h + 180) % 360 - 180))


class Router:
    def __init__(self, world, radar, profile, regimes):
        """Costs in transit km (profiles.py): fuel by regime, seen pulses and SA-14 minutes priced in samples."""
        self.W, self.RM, self.P = world, radar, profile
        self.R = profile["min_turn_radius_km"] * K
        P, f, a = profile, profile["fuel_ref_lb"], profile["cruise_ft"]
        self.e_set, self.t_set = regimes.pick(True, f, a), regimes.pick(False, f, a)
        self.sample_km = regimes.lb_per_sample(f, a) / self.t_set.lb_km       # transit km one sample is worth
        cd = P["coldwar_discount"]
        self.coldwar = fuelplan.coldwar_credit(regimes, P) if cd == "auto" else float(cd)
        self.enemy_km = self.e_set.lb_km / self.t_set.lb_km * (1 - self.coldwar)
        self.w_pulse = P["pulse_cost_samples"] * self.sample_km
        self.w_sa14 = P["sa14_cost_samples_per_min"] * self.sample_km
        n = world.n
        c = (np.arange(n) + 0.5) * CELL
        X, Y = np.meshgrid(c, c)
        E = world.enemy_grid
        kt = np.where(E, self.e_set.kt, self.t_set.kt).astype(float)
        # SA-14 cells padded by SA14_PAD_CELLS: 20 km fillets and joins cut inside the grid path's corners
        self.sa14_pad = sa14 = _dilate(world.sa14_grid, SA14_PAD_CELLS)
        base = np.where(E, self.enemy_km, 1.0) + sa14 * self.w_sa14 * 60 / (kt * KT_KMH)
        self.sa14_hard = world.sa14_hard                # live cells are walls (world.py): the price above only shapes the exits
        self.cost = []
        for dx, dy in MOVES:
            L = math.hypot(dx, dy)
            g = L * CELL_KM * (base + self.w_pulse * radar.detection(X, Y, dx / L, dy / L, kt))
            self.cost.append(g)
        self.h_scale = min(1.0, self.enemy_km)
        self._cost_lists = [g.tolist() for g in self.cost]      # the search reads these as plain lists (much faster than numpy scalars)
        self.notes = set()
        # reduce() memo: its result is a function of the search path, the World / profile (fixed for this Router) and
        # sa14_hard, which is part of the key. Per Router, so a new World or profile means a new Router and an empty memo.
        self._reduce_memo = {}
        self._seg_memo = {}                  # seg_cost() by rounded end points
        # search() memo: (a, b, h0, h_goal, margin_km, sa14_hard) -> ("ok", (points, arrival heading)) | ("err", message).
        # A bounded search (deadline) is never memoized. Planner._prefetch fills it from worker processes.
        self._search_memo = {}

    def set_sa14_walls(self, on):
        """Search with the live SA-14 cells as walls or not (validate() keeps flagging every entry either way)."""
        if self.sa14_hard != bool(on):
            self.sa14_hard = bool(on) and self.W.sa14_hard
            _search.cache_clear()

    def kt_at(self, x, y):
        return np.where(self.W.enemy(x, y), self.e_set.kt, self.t_set.kt).astype(float)

    def per_km(self, x, y, ux, uy, roll=0.0):
        """Objective per km at points (arrays) flying (ux, uy)."""
        kt = self.kt_at(x, y)
        e = self.W.enemy(x, y)
        j = np.clip((np.asarray(y) // CELL).astype(int), 0, self.W.n - 1); i = np.clip((np.asarray(x) // CELL).astype(int), 0, self.W.n - 1)
        return (np.where(e, self.enemy_km, 1.0) + self.sa14_pad[j, i] * self.w_sa14 * 60 / (kt * KT_KMH)
                + self.w_pulse * self.RM.detection(x, y, ux, uy, kt, roll=roll))

    def corner_cost(self, p, u_in, turn):
        """turn_simplicity_km + extra exposure of the fillet arc (turn deg at R) banked vs level."""
        p = np.asarray(p, float); arc_km = self.R / K * math.radians(turn)
        kt = self.kt_at(p[0], p[1])
        extra = self.RM.detection(p[0], p[1], u_in[0], u_in[1], kt, roll=self.P["turn_bank_deg"]) \
            - self.RM.detection(p[0], p[1], u_in[0], u_in[1], kt)
        return self.P["turn_simplicity_km"] + self.w_pulse * float(extra) * arc_km

    # ---------------------------------------------------------------- objective
    def seg_cost(self, a, b):
        key = (tuple(np.round(a, 1)), tuple(np.round(b, 1)))
        v = self._seg_memo.get(key)
        if v is None: v = self._seg_memo[key] = _seg_cost(self, *key)
        return v

    def seg_costs(self, chords):
        """[seg_cost(a, b) for a, b in chords], the uncached ones evaluated together: one radar call over the sample points of
        all of them (the per-point values do not depend on what else is in the call, and each chord's mean is taken over its
        own slice, so the numbers are seg_cost's own)."""
        keys = [(tuple(np.round(a, 1)), tuple(np.round(b, 1))) for a, b in chords]
        todo = [k for k in dict.fromkeys(keys) if k not in self._seg_memo]
        if len(todo) > 1:
            parts = []                                   # (key, L, start, stop) of the chords that have length
            xs, ys, uxs, uys, at = [], [], [], [], 0
            for k in todo:
                a, b = np.array(k[0]), np.array(k[1]); L = float(np.linalg.norm(b - a))
                if L == 0: self._seg_memo[k] = 0.0; continue
                n = max(1, int(L / K)); u = (b - a) / L
                t = (np.arange(n) + 0.5) / n
                xs.append(a[0] + t * (b[0] - a[0])); ys.append(a[1] + t * (b[1] - a[1]))
                uxs.append(np.full(n, u[0])); uys.append(np.full(n, u[1]))
                parts.append((k, L, at, at + n)); at += n
            if parts:
                v = self.per_km(np.concatenate(xs), np.concatenate(ys), np.concatenate(uxs), np.concatenate(uys))
                for k, L, lo, hi in parts: self._seg_memo[k] = float(v[lo:hi].mean() * L / K)
                if CACHE_CHECK:
                    for k, L, lo, hi in parts:
                        if self._seg_memo[k] != _seg_cost(self, *k): raise AssertionError(f"seg_costs batch differs from seg_cost at {k}")
        return [self.seg_cost(a, b) for a, b in chords]

    def route_cost(self, verts):
        c = sum(self.seg_cost(v["xy"], w["xy"]) for v, w in zip(verts, verts[1:]))
        for i in range(1, len(verts) - 1):
            if not verts[i]["fillet"]: continue
            t = turn_deg(verts[i - 1]["xy"], verts[i]["xy"], verts[i + 1]["xy"])
            if t > 2: c += self.corner_cost(verts[i]["xy"], unit(verts[i]["xy"] - verts[i - 1]["xy"]), t)
        return c

    # ---------------------------------------------------------------- 1. search
    def search(self, a, b, h0=None, h_goal=None, margin_km=None, deadline=None):
        """Grid path a -> b (game units). h0 / h_goal: required start / arrival heading (deg) or None.
        margin_km: search box around the two ends (default SEARCH_MARGIN_KM); deadline: time.monotonic() value after
        which the search gives up (BudgetExceeded; such searches are not cached). Returns (points, arrival heading deg)."""
        a, b = tuple(map(float, a)), tuple(map(float, b))
        if deadline is None:
            key = (a, b, h0, h_goal, margin_km, self.sa14_hard)
            hit = self._search_memo.get(key)
            if hit is not None and not CACHE_CHECK: return self._memo_result(hit)
            try: res = ("ok", self._search_free(a, b, h0, h_goal, margin_km, None))
            except BudgetExceeded: raise
            except PlanError as e: res = ("err", str(e))
            if hit is not None and not _same_search(hit, res): raise AssertionError(f"search memo stale for {key}")
            self._search_memo[key] = res
            return self._memo_result(res)
        return self._search_free(a, b, h0, h_goal, margin_km, deadline)

    def search_peek(self, a, b, h0=None, h_goal=None, margin_km=None):
        """The memoized search(a, b, ...) as ("ok", (points, heading)) / ("err", message), or None when not computed yet."""
        return self._search_memo.get((tuple(map(float, a)), tuple(map(float, b)), h0, h_goal, margin_km, self.sa14_hard))

    def seed(self, searches, reduces):
        """Add search / reduce results computed elsewhere (a worker process): {key: result} in the memos' own formats."""
        self._search_memo.update(searches); self._reduce_memo.update(reduces)

    @staticmethod
    def _memo_result(res):
        if res[0] == "err": raise PlanError(res[1])
        return list(res[1][0]), res[1][1]

    def _search_free(self, a, b, h0, h_goal, margin_km, deadline):
        m0 = SEARCH_MARGIN_KM if margin_km is None else float(margin_km)

        def attempt(m):
            find = (lambda wide: _search(self, a, b, h0, h_goal, wide, m)) if deadline is None else \
                (lambda wide: self._search(a, b, h0, h_goal, wide, m, deadline))
            try:
                return find(True)
            except BudgetExceeded:
                raise
            except PlanError:
                # the planning margin closes every gap here: plan on the real clearance instead
                pts, h = find(False)
                self.notes.add(f"no corridor with the {self.P['plan_margin_km']} km planning margin between "
                               f"({a[0]:.0f},{a[1]:.0f}) and ({b[0]:.0f},{b[1]:.0f}): planned on clear_km only")
                return pts, h
        # live SA-14 cells are walls: a front line of them (CE) can leave only a way round its end, far outside the
        # usual box, so a search that finds no path there widens the box (a bounded api.route search keeps its own)
        if not (self.sa14_hard and margin_km is None and SA14_DETOUR_KM > m0): return attempt(m0)
        try:
            return attempt(m0)
        except PlanError as e:
            if isinstance(e, BudgetExceeded): raise
            return attempt(SA14_DETOUR_KM)

    def _search(self, a, b, h0, h_goal, wide, margin_km=SEARCH_MARGIN_KM, deadline=None):
        W = self.W; sa, sb = W.cell(a), W.cell(b)
        blocked = W.blocked_plan if wide else W.blocked
        if self.sa14_hard: blocked = blocked | self._sa14_wall(sa, sb)
        m = int(margin_km / CELL_KM)
        j0, j1 = max(0, min(sa[0], sb[0]) - m), min(W.n - 1, max(sa[0], sb[0]) + m)
        i0, i1 = max(0, min(sa[1], sb[1]) - m), min(W.n - 1, max(sa[1], sb[1]) + m)
        # the heuristic at every cell of the box, with the same arithmetic as ever; cost / wall lookups on plain lists
        hs = self.h_scale
        hrow = {j: [math.hypot(j - sb[0], i - sb[1]) * CELL_KM * hs for i in range(i1 + 1)] for j in range(j0, j1 + 1)}
        goal_ok = (lambda h: True) if h_goal is None else (lambda h: abs((DIR_ANG[h] - h_goal + 180) % 360 - 180) <= 25)
        # Radius-aware: after a heading change of one direction step (<= 26.6 deg) the plane must
        # fly 2 R tan(13.3 deg) (~9.5 km at R 20 km) before the next, so a valley the search
        # threads can be flown at R. State = (cell, heading, km flown since the last turn).
        need = int(math.ceil(2 * self.R / K * math.tan(math.radians(13.3))))
        move_km = [int(round(math.hypot(dx, dy) * CELL_KM)) for dx, dy in MOVES]
        turn_pen = {d: abs(d) * (360 / NDIR) * SEARCH_TURN_KM_PER_DEG for d in (-1, 0, 1)}
        cost, blocked = self._cost_lists, blocked.tolist()
        dist, prev, pq = {}, {}, []
        heappush, heappop, dget, inf = heapq.heappush, heapq.heappop, dist.get, math.inf
        for h in (range(NDIR) if h0 is None else [nearest_dir(h0)]):
            dist[(*sa, h, need)] = 0.0; heappush(pq, (hrow[sa[0]][sa[1]], 0.0, (*sa, h, need)))
        goal = None
        pops = 0
        while pq:
            if deadline is not None:
                pops += 1
                if not pops & 0xFFF and time.monotonic() > deadline:
                    raise BudgetExceeded(f"search ({a[0]:.0f},{a[1]:.0f}) -> ({b[0]:.0f},{b[1]:.0f}) over its time budget")
            f, c, s = heappop(pq)
            j, i, h, run = s
            if j == sb[0] and i == sb[1] and goal_ok(h): goal = s; break
            if c > dget(s, inf): continue
            at_start = j == sa[0] and i == sa[1]
            for d in ((-1, 0, 1) if run >= need else (0,)):
                nh = (h + d) % NDIR; dx, dy = MOVES[nh]; nj, ni = j + dy, i + dx
                if not (j0 <= nj <= j1 and i0 <= ni <= i1): continue
                step = cost[nh][j][i]
                if blocked[nj][ni] and (nj != sb[0] or ni != sb[1]) and not at_start:
                    continue                                     # ends may sit near walls (bases, targets)
                nc = c + step + turn_pen[d]
                t = (nj, ni, nh, min(need, (0 if d else run) + move_km[nh]))
                if nc < dget(t, inf):
                    dist[t] = nc; prev[t] = s; heappush(pq, (nc + hrow[nj][ni], nc, t))
        if goal is None:
            raise PlanError(f"no path from ({a[0]:.0f},{a[1]:.0f}) to ({b[0]:.0f},{b[1]:.0f})"
                            + (f" arriving on heading {h_goal:.0f}" if h_goal is not None else "")
                            + (" with the SA-14 cells walled off" if self.sa14_hard else ""))
        path = [goal]
        while path[-1] in prev: path.append(prev[path[-1]])
        pts = [W.centre(j, i) for j, i, _, _ in reversed(path)]
        pts[0], pts[-1] = a, b
        return pts, DIR_ANG[goal[2]]

    def _sa14_wall(self, sa, sb):
        """Search-grid cells the path may not enter: the padded live SA-14 cells, except around the two ends. An end inside
        a cell (the plane already there, a target) may leave / enter it: that whole padded cell opens; an end just outside
        keeps a pad-sized box around it."""
        wall, g, pad = self.sa14_pad.copy(), self.W.g32, SA14_PAD_CELLS
        for j, i in (sa, sb):
            if self.W.sa14_grid[j, i]:
                wall &= ~_dilate(np.outer(g == g[j], g == g[i]), pad)
            else:
                wall[max(0, j - pad):j + pad + 1, max(0, i - pad):i + pad + 1] = False
        return wall

    # ---------------------------------------------------------------- 2. reduce
    def reduce(self, pts):
        """Fewest-corner polyline through a subset of pts (DP); returns the interior corners. Memoized per search path
        (a leg is reduced again for every option that shares it)."""
        key = (tuple(map(tuple, pts)), self.sa14_hard)
        hit = self._reduce_memo.get(key)
        if hit is not None and not CACHE_CHECK:
            if isinstance(hit, str): raise PlanError(hit)
            return list(hit)
        try:
            out = self._reduce(pts)
        except PlanError as e:
            if hit is not None and hit != str(e): raise AssertionError(f"reduce memo stale: {hit!r} != {str(e)!r}")
            self._reduce_memo[key] = str(e)
            raise
        if hit is not None and (isinstance(hit, str) or [tuple(map(float, q)) for q in hit] != [tuple(map(float, q)) for q in out]):
            raise AssertionError("reduce memo stale: the recomputed corners differ")
        self._reduce_memo[key] = tuple(out)
        return out

    def _reduce(self, pts):
        pts = [pts[0]] + pts[1:-1:2] + [pts[-1]]            # every 2nd cell: 4 km spacing
        n = len(pts)
        # corner cost at each candidate point, for a typical 30 deg turn on the search path's heading
        pen = [0.0] + [self.corner_cost(pts[i], unit(np.subtract(pts[i], pts[i - 1])), 30.0) for i in range(1, n)]
        best = [0.0] + [math.inf] * (n - 1); back = [-1] * n
        W = self.W
        for j in range(1, n):
            cand = []                                    # the chords into j, nearest start first (ties go to the first)
            for i in range(j - 1, -1, -1):
                if best[i] == math.inf: continue
                if j - i > 1 and not (W.segment_clear(pts[i], pts[j], plan=True) or
                                      (j - i <= 3 and W.segment_clear(pts[i], pts[j]))): continue
                cand.append(i)
            if self.sa14_hard:
                far = [i for i in cand if j - i > 1]
                ok = dict(zip(far, W.sa14_clear_many([(pts[i], pts[j]) for i in far])))
                cand = [i for i in cand if j - i == 1 or ok[i]]
            for i, sc in zip(cand, self.seg_costs([(pts[i], pts[j]) for i in cand])):
                c = best[i] + sc + (pen[i] if i > 0 else 0.0)
                if c < best[j]: best[j], back[j] = c, i
        if best[-1] == math.inf:
            raise PlanError("search path has no wall-clear straight legs")
        out, k = [], back[n - 1]
        while k > 0: out.append(pts[k]); k = back[k]
        return out[::-1]

    # ---------------------------------------------------------------- 3. expand
    def expand(self, core, ends=None):
        """core: list of items
             {"t": "fixed", "pts": [(xy, label, attrs), ...]}     flown exactly as given, no fillets
             {"t": "v", "xy": xy}                                  search corner (filleted, removable)
             {"t": "target", "xy": xy, "label": s, "attrs": {}}    overflown
           -> vertex list [{"xy", "fillet", "label", "attrs", "core"}].
        A fixed piece has a heading at each end (a pose). It is joined to its neighbours by
        minimum-radius geometry: arc + straight to a free point (CS), or arc + straight + arc
        to another pose (Dubins CSC), the shortest wall-clear option. A target whose turn is
        over overfly_max_turn_deg becomes a one-point pose flown straight through."""
        tol = self.P["overfly_max_turn_deg"]
        items = [{**it, "core": k} for k, it in enumerate(core)]
        for k, it in enumerate(items):
            if it["t"] == "target" and 0 < k < len(items) - 1:
                a, b = _end(items[k - 1], "out"), _end(items[k + 1], "in")
                if turn_deg(a, it["xy"], b) > tol:
                    T = np.array(it["xy"], float)
                    u_a, u_b = unit(T - a), unit(b - T)
                    cands = []
                    # (1) turn over the target: corner pushed outward so the arc's middle passes over T
                    q = T.copy()
                    for _ in range(4):
                        D = math.radians(min(turn_deg(a, q, b), 170.0))
                        q = T + unit(u_a - u_b) * self.R * (1 / math.cos(D / 2) - 1)
                    cands.append({**it, "t": "v", "xy": tuple(q), "over": T})
                    # (2) straight through on a fixed heading, joined by Dubins arcs
                    heads = ([unit(u_a + u_b)] if np.linalg.norm(u_a + u_b) > 1e-6 else []) + [u_a, u_b] + \
                            [from_hdg(h) for h in range(0, 360, 30)]
                    for u in heads:
                        cands.append(dict(t="fixed", pts=[(tuple(it["xy"]), it["label"], it.get("attrs", {}))], u=u, core=k))
                    best = None
                    for cand in cands:
                        V = self._join(items[:k] + [cand] + items[k + 1:])
                        L = sum(np.linalg.norm(b_["xy"] - a_["xy"]) for a_, b_ in zip(V, V[1:]))
                        key = (len(self.violations(V, ends)), not self.clear(V, ends), L)
                        if best is None or key < best[0]: best = (key, cand)
                    items[k] = best[1]
        return self._join(items)

    def _join(self, items):
        V = []
        vert = lambda xy, it, label="", attrs=None, fillet=False: dict(
            xy=np.array(xy, float), fillet=fillet, label=label, attrs=attrs if attrs is not None else {}, core=it["core"],
            over=it.get("over") if fillet else None)
        for k, it in enumerate(items):
            if it["t"] != "fixed":
                if k > 0 and items[k - 1]["t"] == "fixed":      # leave the previous pose: arc, then straight to here
                    p, u = _pose(items[k - 1], "out")
                    V += [vert(q, items[k - 1]) for q in self._best([_cs(p, u, it["xy"], self.R, s) for s in (1, -1)])[1:-1]]
                V.append(vert(it["xy"], it, it.get("label", ""), it.get("attrs", {}), fillet=it["t"] == "v"))
                continue
            p, u = _pose(it, "in")
            if k > 0:
                prev = items[k - 1]
                if prev["t"] == "fixed":
                    q0, w0 = _pose(prev, "out")
                    opts = [_csc(q0, w0, p, u, self.R, s1, s2) for s1 in (1, -1) for s2 in (1, -1)]
                else:                                              # straight from the previous point, arc into the pose
                    opts = [_rev(_cs(p, -u, prev["xy"], self.R, s)) for s in (1, -1)]
                V += [vert(q, it) for q in self._best(opts)[1:-1]]
            V += [vert(q, it, l, at) for q, l, at in it["pts"]]
        return V

    def _best(self, opts):
        """Cheapest wall-clear option by the objective (radar, SA-14 cells, fuel; falls back to the
        cheapest; validate() reports the wall)."""
        opts = [o for o in opts if o is not None]
        if not opts: raise PlanError("no minimum-radius join between two fixed headings (points too close)")
        L = lambda o: sum(self.seg_cost(a, b) for a, b in zip(o, o[1:]))
        clear = [o for o in opts if all(self.W.segment_clear(a, b) for a, b in zip(o, o[1:]))]
        return min(clear or opts, key=L)

    # ---------------------------------------------------------------- 4. fillet feasibility + repair
    def tangents(self, V):
        t = [0.0] * len(V)
        for i in range(1, len(V) - 1):
            if V[i]["fillet"]:
                t[i] = self.R * math.tan(math.radians(min(turn_deg(V[i - 1]["xy"], V[i]["xy"], V[i + 1]["xy"]), 179.0)) / 2)
        return t

    def fillet_arc(self, V, i, t=None):
        """Arc points replacing corner i (tangent start ... tangent end)."""
        p = V[i]["xy"]; a_, c_ = V[i - 1]["xy"], V[i + 1]["xy"]
        if t is None: t = self.tangents(V)[i]
        u, w = unit(p - a_), unit(c_ - p)
        st, e = p - u * t, p + w * t
        side = 1 if u[0] * w[1] - u[1] * w[0] > 0 else -1           # +1: right turn (heading increases)
        ctr = st + side * self.R * _nr(u)
        a0, a1 = _ang(st, ctr), _ang(e, ctr)
        span = side * ((side * (a1 - a0)) % (2 * math.pi))
        if V[i].get("over") is not None:            # split at the target so a point lies exactly over it
            sp1 = side * ((side * (_ang(V[i]["over"], ctr) - a0)) % (2 * math.pi))
            if 0 < sp1 / span < 1:
                return _arc(ctr, self.R, a0, sp1) + _arc(ctr, self.R, a0 + sp1, span - sp1)[1:]
        return _arc(ctr, self.R, a0, span)

    def violations(self, V, ends=None):
        """[(vertex/leg index, kind, message)]: legs too short for their turns, arcs into walls. ends: which runway
        end legs skip the wall check (default RUNWAY_ENDS)."""
        t = self.tangents(V); out = []
        for i in range(len(V) - 1):
            L = float(np.linalg.norm(V[i + 1]["xy"] - V[i]["xy"]))
            if t[i] + t[i + 1] > L + 0.05 * K:
                out.append((i, "short", f"{L / K:.1f} km leg, its turns need {(t[i] + t[i + 1]) / K:.1f} km"))
        if not out:
            for i in _leg_range(len(V) - 1, ends):           # first / last legs sit on runways
                lo = V[i]["attrs"].get("kind") in LOITER_KINDS and V[i + 1]["attrs"].get("kind") in LOITER_KINDS
                if not self.W.segment_clear_km(V[i]["xy"], V[i + 1]["xy"], self.P["loiter_clear_km"] if lo else self.P["clear_km"]):
                    a, b = V[i]["xy"], V[i + 1]["xy"]
                    out.append((i, "wall", f"leg ({a[0]:.0f},{a[1]:.0f}) -> ({b[0]:.0f},{b[1]:.0f}) passes within "
                                           f"{self.P['clear_km']} km of a mountain"))
        if not out:
            for i in range(1, len(V) - 1):
                if V[i]["fillet"] and t[i] > 1.0:
                    arc = self.fillet_arc(V, i, t[i])
                    if not all(self.W.segment_clear(q0, q1) for q0, q1 in zip(arc, arc[1:])):
                        out.append((i, "arc", f"turn at ({V[i]['xy'][0]:.0f},{V[i]['xy'][1]:.0f}) cuts within "
                                              f"{self.P['clear_km']} km of a mountain"))
        return out

    def clear(self, V, ends=None):
        lo = lambda a, b: a["attrs"].get("kind") in LOITER_KINDS and b["attrs"].get("kind") in LOITER_KINDS
        legs = list(zip(V, V[1:]))
        return all(self.W.segment_clear_km(a["xy"], b["xy"], self.P["loiter_clear_km"] if lo(a, b) else self.P["clear_km"])
                   for a, b in (legs[i] for i in _leg_range(len(legs), ends)))

    def repair(self, core, log, ends=None, deadline=None):
        """Fix violations by dropping, merging or pushing out search corners, cheapest first."""
        sig = lambda c: tuple((it["t"], tuple(np.round(it["xy"], 0))) for it in c if it["t"] == "v")
        seen = {sig(core)}
        for _ in range(200):
            if deadline is not None and time.monotonic() > deadline:
                raise BudgetExceeded("repair over its time budget")
            V = self.expand(core, ends); bad = self.violations(V, ends)
            if not bad: return core, V
            i, kind, msg = bad[0]
            opts = []
            for span in (2, 5):
                near = sorted({V[k]["core"] for k in range(max(0, i - span), min(len(V), i + span + 2))
                               if core[V[k]["core"]]["t"] == "v"})
                for c in near:
                    opts.append(("drop", core[:c] + core[c + 1:]))
                    if 0 < c and c + 2 < len(core) and core[c + 1]["t"] == "v":
                        x = _intersect(_xy(core[c - 1], "out"), core[c]["xy"], core[c + 1]["xy"], _xy(core[c + 2], "in"))
                        if x is not None: opts.append(("merge", core[:c] + [dict(t="v", xy=tuple(x))] + core[c + 2:]))
                    # push the corner outward so its arc passes where the corner was
                    k = next((k for k, v in enumerate(V) if v["core"] == c), None)
                    if k is not None and 0 < k < len(V) - 1:
                        a_, p_, b_ = V[k - 1]["xy"], V[k]["xy"], V[k + 1]["xy"]
                        D = math.radians(turn_deg(a_, p_, b_))
                        out_dir = unit(unit(p_ - a_) - unit(b_ - p_))
                        inset = max(self.R * (1 / math.cos(min(D, 2.6) / 2) - 1), 1.0 * K)
                        for f in (0.5, 1.0, 2.0, 4.0):
                            q = p_ + out_dir * inset * f
                            if not (0 <= q[0] < self.W.size and 0 <= q[1] < self.W.size): continue   # stay on the map
                            opts.append(("push", core[:c] + [dict(t="v", xy=tuple(q))] + core[c + 1:]))
                if opts: break
            where = V[i]["xy"]
            scored = []
            for name, cand in opts:
                if sig(cand) in seen: continue
                Vc = self.expand(cand, ends); bc = self.violations(Vc, ends)
                # options that clear this violation (none of its kind left within 3 km) come first
                persists = any(b[1] == kind and np.linalg.norm(Vc[b[0]]["xy"] - where) < 3 * K for b in bc)
                scored.append((persists, len(bc), self.route_cost(Vc), name, cand))
            if not scored:
                raise PlanError(f"{msg} at R {self.R / K:.0f} km, and no nearby search corner can be dropped, merged or pushed")
            scored.sort(key=lambda s: s[:3])
            log.append(f"repair: {scored[0][3]} a corner ({msg})")
            core = scored[0][4]; seen.add(sig(core))
        raise PlanError("repair did not converge")

    # ---------------------------------------------------------------- 5. densify
    def densify(self, V):
        """Vertices -> flown points [(xy, label, attrs, kind)] with arcs at radius R."""
        R, t = self.R, self.tangents(V)
        out = []
        for i, v in enumerate(V):
            p = v["xy"]
            if not (v["fillet"] and t[i] > 1.0):
                out.append((p, v["label"], v["attrs"])); continue
            arc = self.fillet_arc(V, i, t[i])
            mid = (len(arc) - 1) // 2 if v.get("over") is None else \
                int(np.argmin([np.linalg.norm(q - v["over"]) for q in arc]))
            for k, q in enumerate(arc): out.append((q, v["label"] if k == mid else "", v["attrs"]))
        # drop near-duplicates (a zero-length leg has no direction for the nav)
        ded = [out[0]]
        for q in out[1:]:
            if np.linalg.norm(q[0] - ded[-1][0]) < 0.05 * K: ded[-1] = (ded[-1][0], ded[-1][1] or q[1], ded[-1][2] or q[2])
            else: ded.append(q)
        return ded

    # ---------------------------------------------------------------- 6. validate
    def validate(self, pts, targets, overfly_labels, ends=None):
        P, R, errs = self.P, self.R, []
        xy = [np.asarray(p[0], float) for p in pts]
        L = [float(np.linalg.norm(b - a)) for a, b in zip(xy, xy[1:])]
        need = [0.0] * len(xy); kinks = [0.0] * len(xy)
        loiter = [(p[2] or {}).get("kind") == "loiter" for p in pts]
        near = [(p[2] or {}).get("kind") in LOITER_KINDS for p in pts]
        for i in range(1, len(xy) - 1):
            kinks[i] = turn_deg(xy[i - 1], xy[i], xy[i + 1])
            if pts[i][1] in overfly_labels and kinks[i] <= P["overfly_max_turn_deg"] + 0.5: continue
            if pts[i][1].startswith(("Landing", "Final", "Approach")) and kinks[i] < 1: continue
            Ri = P["loiter_radius_km"] * K if loiter[i] and loiter[i - 1] else R    # inside a loiter: nav's 1.6 deg/s mode
            need[i] = Ri * math.tan(math.radians(min(kinks[i], 179.0)) / 2)
        for i, l in enumerate(L):
            if need[i] + need[i + 1] > l * 1.02 + 0.05 * K:
                errs.append(f"turn too tight at ({xy[i][0]:.0f},{xy[i][1]:.0f}): {kinks[i]:.0f} + {kinks[i + 1]:.0f} deg on a "
                            f"{l / K:.1f} km leg needs {(need[i] + need[i + 1]) / K:.1f} km at R {R / K:.0f} km")
        legs = _leg_range(len(xy) - 1, ends)              # runway ends sit on bases
        for i, (a, b) in enumerate(zip(xy, xy[1:])):
            if i not in legs: continue
            km = P["loiter_clear_km"] if near[i] and near[i + 1] else P["clear_km"]
            if not self.W.segment_clear_km(a, b, km):
                errs.append(f"terrain within {km} km between ({a[0]:.0f},{a[1]:.0f}) and ({b[0]:.0f},{b[1]:.0f})")
        if self.W.sa14_hard:                                 # checked even when the search was let through (set_sa14_walls)
            for c, p, k in self.W.sa14_entries(xy):
                errs.append(f"SA-14 cell {c} entered (or passed within {self.P['sa14_margin_km']:g} km) at ({p[0]:.0f},{p[1]:.0f}) "
                            f"on leg {k + 1}: by day it fires at this opponent level and a launch raises the network alert")
        for t in targets:
            d = min(_point_seg(np.array([t["x"], t["y"]], float), a, b) for a, b in zip(xy, xy[1:])) / K
            if d > 0.5: errs.append(f"target {t['place']} missed by {d:.1f} km")
        return errs                                        # fuel: plan.py (fuelplan.integrate)


def _same_search(x, y):
    if x[0] != y[0]: return False
    if x[0] == "err": return x[1] == y[1]
    return x[1][1] == y[1][1] and [tuple(map(float, p)) for p in x[1][0]] == [tuple(map(float, p)) for p in y[1][0]]


@lru_cache(maxsize=200000)
def _seg_cost(r, a, b):
    a, b = np.array(a), np.array(b); L = float(np.linalg.norm(b - a))
    if L == 0: return 0.0
    n = max(1, int(L / K)); u = (b - a) / L
    t = (np.arange(n) + 0.5) / n
    x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
    return float(r.per_km(x, y, u[0], u[1]).mean() * L / K)


@lru_cache(maxsize=4096)
def _search(r, a, b, h0, h_goal, wide, margin_km=SEARCH_MARGIN_KM):
    return r._search(a, b, h0, h_goal, wide, margin_km)


# --- minimum-radius geometry (y-down plane: angle a <-> point c + R(cos a, sin a); a increasing
# = clockwise on the map = right turn; side +1 right, -1 left) ---------------------------------
def _nr(u): return np.array([-u[1], u[0]])                   # right-hand normal of heading u
def _ang(p, c): return math.atan2(p[1] - c[1], p[0] - c[0])
def _on(c, R, a): return c + R * np.array([math.cos(a), math.sin(a)])


def _arc(c, R, a0, span):
    """Arc as a circumscribed polygon: every chord tangent to the circle, so each kink is a
    turn of exactly radius R (what validate() checks); starts and ends on the circle."""
    if abs(span) < 1e-9: return [_on(c, R, a0)]
    n = max(1, int(math.ceil(abs(span) * R / (ARC_STEP_KM * K))))
    ro = R / math.cos(span / n / 2)
    return [_on(c, R, a0)] + [_on(c, ro, a0 + span * (k + 0.5) / n) for k in range(n)] + [_on(c, R, a0 + span)]


def _pose(it, end):
    p = it["pts"][0 if end == "in" else -1][0]
    if "u" in it: return np.array(p, float), it["u"]
    q = it["pts"][1][0] if end == "in" else it["pts"][-2][0]
    u = unit(np.subtract(q, p)) if end == "in" else unit(np.subtract(p, q))
    return np.array(p, float), u


def _end(it, end):
    return np.array(it["pts"][0 if end == "in" else -1][0] if it["t"] == "fixed" else it["xy"], float)


def _cs(p, u, q, R, side):
    """From pose (p, u): turn on `side`, then straight to point q. Points, or None."""
    c = p + side * R * _nr(u); d = np.asarray(q, float) - c; L = float(np.linalg.norm(d))
    if L < R * 1.001: return None
    at = math.atan2(d[1], d[0]) - side * math.acos(R / L)
    a0 = _ang(p, c)
    return _arc(c, R, a0, side * ((side * (at - a0)) % (2 * math.pi))) + [np.asarray(q, float)]


def _rev(o): return None if o is None else o[::-1]


def _csc(p1, u1, p2, u2, R, s1, s2):
    """Dubins CSC from pose (p1, u1) to pose (p2, u2) turning s1 then s2. Points, or None."""
    c1, c2 = p1 + s1 * R * _nr(u1), p2 + s2 * R * _nr(u2)
    w = c2 - c1; D = float(np.linalg.norm(w))
    k = 0.0 if s1 == s2 else 2 * s1 * R
    if D < 1e-6 or abs(k) > D: return None
    phi = math.atan2(w[1], w[0]) + math.asin(k / D)
    v = np.array([math.cos(phi), math.sin(phi)])
    t1, t2 = c1 - s1 * R * _nr(v), c2 - s2 * R * _nr(v)
    if (t2 - t1) @ v < -1e-6: return None
    a0, a1 = _ang(p1, c1), _ang(t1, c1); b0, b1 = _ang(t2, c2), _ang(p2, c2)
    arc1 = _arc(c1, R, a0, s1 * ((s1 * (a1 - a0)) % (2 * math.pi)))
    arc2 = _arc(c2, R, b0, s2 * ((s2 * (b1 - b0)) % (2 * math.pi)))
    return arc1 + arc2


def _xy(it, end):
    if it["t"] == "fixed": return it["pts"][0 if end == "in" else -1][0]
    return it["xy"]


def _intersect(p0, p1, p2, p3):
    """Corner where line p0->p1 extended meets line p3->p2 extended (ahead of both)."""
    if p3 is None: return None
    p0, p1, p2, p3 = (np.asarray(q, float) for q in (p0, p1, p2, p3))
    d1, d2 = p1 - p0, p2 - p3
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9: return None
    s = ((p3[0] - p0[0]) * d2[1] - (p3[1] - p0[1]) * d2[0]) / den
    q = ((p3[0] - p0[0]) * d1[1] - (p3[1] - p0[1]) * d1[0]) / den
    if s < 1 or q < 1: return None
    return p0 + s * d1


def _point_seg(p, a, b):
    ab = b - a; L2 = float(ab @ ab) or 1e-9
    t = max(0.0, min(1.0, float((p - a) @ ab) / L2))
    return float(np.linalg.norm(p - a - t * ab))
