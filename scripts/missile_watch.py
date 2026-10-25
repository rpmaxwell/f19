"""MissileWatch: in-flight enemy-missile evasion that keeps the stealth profile (notes/missile_evasion_prd.md).

Pure decision code (no automation port): nav reads the game (f19_state.read_missile_world) and feeds `step()` each
cycle; the memlog replay (missile_replay.py) feeds it snapshots. Same shape as phases.FighterWatch:
step(...) -> (overrides, events).

Each cycle, every live enemy missile is flown with analysis/missile_game.simulate (the game's own frame rules, R1 gate
629/631 memlog snapshot pairs, 64/64 outcomes) against the CURRENT PLAN (nav's want, current speed and altitude):
  - doomed  (misses on its own: blind, launcher lost, out of reach, overshoot): log only - reacting spends stealth;
  - threat  (hit predicted, or a locked pass inside MARGIN_K x the hit radius).
With a threat, candidates = heading (FAN_STEP) x bank x speed, each with up to one countermeasure (now / at time-to-go
minus the CM lifetime) or a jammer; the cheapest (stealth_cost) whose predicted outcome is a miss for EVERY live missile
wins, preferring one that still misses with the less-tested rules perturbed (ROBUST). Nothing clears -> survival mode:
largest predicted miss distance, any cost. The choice is COMMITTED until its window ends, unless a re-prediction of it
flips to a hit or a new missile appears (no per-cycle re-choosing: the 2026-10-22 Elite crash flip-flopped headings).

Turn model (R6, fitted on 25 756 memlog snapshot pairs, 2026-10-23): turn rate = TURN_K x tan(bank) / kt deg per game s
(TURN_K 337, about a third of a coordinated turn: 20 deg bank at 190 kt = 0.6 deg/s, 45 deg at 175 kt = 1.9 deg/s); roll
rate under nav <= ROLL_RATE deg per game s (p99 of the logs). A heading change can't outturn a missile (2.8-7 deg/s), so
inside 8 km the CMs carry the defence; beyond it, the heading decides whether the launcher keeps seeing us.

Time: everything inside is GAME time (frames, fs per 2 s); the game ran ~2.45x real time (memlog median, 10-90 % 2.0-2.9)
and the ratio is measured live from the frame counter ds:5520. Rates handed to nav are converted to its deg per REAL s.
"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import missile_game as G
import radar_game as R

K = 64.0
def wrap(d): return (d + 180) % 360 - 180
def deg(a65536): return (a65536 & 0xFFFF) * 360 / 65536
def u65(d): return int(round(d / 360 * 65536)) & 0xFFFF

FLARE, CHAFF, DECOY = G.FLARE, G.CHAFF, G.DECOY
CM_NAME = {FLARE: "flare", CHAFF: "chaff", DECOY: "decoy", "ecm": "radar jammer", "irj": "IR jammer"}
KEYS = {FLARE: "1", CHAFF: "2", DECOY: "5", "irj": "3", "ecm": "4"}       # disassembly key table 0xd3be (decoy '5' = 0x635)


class CmKeys:
    """Runs MissileWatch's countermeasure requests with one tap each and checks them: a CM by its store count going down
    (ds:4ED4), a jammer by its ds:684C bit. Re-pressed after RETRY_S real s if nothing changed, at most TRIES taps; logs
    the latency from tap to store decrement (PRD R6 "key latency"). Taps are 0.2 s (phases.TAP: longer ones auto-repeat)."""
    RETRY_S, TRIES, TAP = 2.0, 3, 0.2
    def __init__(self):
        self.pending = None                      # dict(kind, t0, last, tries, before)
        self.failed = None                       # kind given up on this cycle (nav passes it to MissileWatch.block)
    def busy(self): return self.pending is not None
    def request(self, db, kind, snap, now):
        if self.pending: return f"cm {CM_NAME[kind]} not pressed: {CM_NAME[self.pending['kind']]} still being checked"
        before = snap["stores"][kind - 1] if kind in (FLARE, CHAFF, DECOY) else None
        if before == 0: return f"cm {CM_NAME[kind]}: store EMPTY - not pressed"
        db.tap(KEYS[kind], self.TAP)
        self.pending = dict(kind=kind, t0=now, last=now, tries=1, before=before, on=True)
        return f"cm key '{KEYS[kind]}' ({CM_NAME[kind]}) pressed, store {before}"
    def jam(self, db, kind, on, snap, now):
        bit = 0x10 if kind == "ecm" else 0x20
        if bool(snap["world"]["flags"] & bit) == on: return ""
        if self.pending: return ""
        db.tap(KEYS[kind], self.TAP)
        self.pending = dict(kind=kind, t0=now, last=now, tries=1, before=None, on=on, bit=bit)
        return f"jammer key '{KEYS[kind]}' ({CM_NAME[kind]} {'on' if on else 'off'}) pressed"
    def step(self, db, snap, now):
        """-> event text ("" when nothing to report). Call every cycle after reading the world."""
        p = self.pending
        if not p: return ""
        if p["before"] is not None:
            done = snap["stores"][p["kind"] - 1] < p["before"]
        else:
            done = bool(snap["world"]["flags"] & p["bit"]) == p["on"]
        if done:
            self.pending = None
            return f"{CM_NAME[p['kind']]} confirmed {now - p['t0']:.2f} real s after the first tap ({p['tries']} tap(s))"
        if now - p["last"] < self.RETRY_S: return ""
        if p["tries"] >= self.TRIES:
            self.pending = None; self.failed = p["kind"]
            return f"{CM_NAME[p['kind']]} NOT confirmed after {p['tries']} taps - given up"
        db.tap(KEYS[p["kind"]], self.TAP); p["tries"] += 1; p["last"] = now
        return f"{CM_NAME[p['kind']]}: no change after {self.RETRY_S:.0f} s - key '{KEYS[p['kind']]}' again"


class Candidate:
    __slots__ = ("hdg", "bank", "kt", "cm", "cm_frame", "jam", "label", "cost", "res", "clear", "robust", "margin", "why")
    def __init__(self, hdg, bank, kt, cm=None, cm_frame=None, jam=None, label=""):
        self.hdg, self.bank, self.kt, self.cm, self.cm_frame, self.jam, self.label = hdg, bank, kt, cm, cm_frame, jam, label
        self.cost, self.res, self.clear, self.robust, self.margin, self.why = 0.0, {}, False, False, 0.0, ""
    def text(self):
        t = f"hdg {self.hdg:.0f} bank {self.bank:.0f} {self.kt:.0f} kt"
        if self.cm: t += f" + {CM_NAME[self.cm]}" + (" now" if (self.cm_frame or 1) <= 1 else f" in {self.cm_frame} fr")
        if self.jam: t += f" + {CM_NAME[self.jam]} on"
        return t


class MissileWatch:
    WINDOW_S, HORIZON_S = 20.0, 30.0          # game s: commit window; prediction horizon
    MARGIN_TESTED, MARGIN_UNTESTED = 1.3, 2.0  # x hit radius a locked pass must clear (PRD R3)
    FAN_STEP, BANKS, EVADE_KT = 15, (20, 30, 45), 165
    BANK_LOW_ALT, MIN_TURN_ALT = 500, 250     # bank > 20 only above BANK_LOW_ALT (nav's 2026-10-23 rule); floor in turns
    TURN_K, ROLL_RATE, KT_RATE = 337.0, 2.0, 2.0   # deg/game s x kt / tan(bank); deg/game s; kt/game s (UNTESTED)
    GROUND = 0.788 * 0.5144 / 15.625          # waypoint units per game s per kt (ground speed 0.788 x indicated)
    AC_CHECK_S = 32.0                          # fighters' radar check period (game s; their timer isn't decoded)
    CLEAR_S = 8.0                              # game s with no threat before handing back
    COST = dict(net=100, launch=60, radar_id=30, seen=5, decoy=25, cm=1, ecm=10, irj=1, route_per_min=0.5)
    MAX_CANDIDATES = 400
    BUDGET_S = 0.25                            # real s of candidate search per cycle (nav's roll-key releases wait on it)
    SURVIVAL_REPLAN_S = 2.0                    # game s between searches while nothing clears
    BLOCK_S = 20.0                             # game s a CM / jammer that never confirmed is left out of the candidates
    # Real s from the key to the CM / jammer existing in memory: unmeasured (R6). The closed-loop replay of the Elite
    # launches (missile_replay.py --closed-loop) clears all 4 AA-9 hits at 0.3 s and loses the 3.9 km one at 1.0 s when
    # the prediction ignores it, so every request is predicted to land KEY_LATENCY_S later (robustness: twice that).
    KEY_LATENCY_S = 1.0

    def __init__(self, mode="observe", clutter=None):
        """mode act / observe / off. clutter(x, y) -> territory grid byte of that cell (radar_game.site_return); None ->
        the snapshot's own cell map (ds:85FC)."""
        self.mode, self.clutter = mode, clutter
        self.state, self.plan, self.plan_end, self.clear_since = "off", None, None, None
        self.known = {}                 # (slot, spec, launcher) -> first-seen info
        self.gt, self.last_frame, self.ratio, self.hist = 0.0, None, 2.45, []
        self.worst, self.action, self.banner = "", "", ""
        self.cm_pending = []            # [(game time, kind)] releases the plan still owes
        self.jam_on = set()             # jammers this module switched on (switched off again when clear)
        self.log = []
        self.ids, self.next_id = {}, 1
        self.released = None            # (game time, kind, stores before) of the last CM asked for
        self.blocked = {}               # kind -> game time until which it isn't offered (key never confirmed)

    def block(self, kind):
        """nav: CmKeys gave up on this kind (store / flag never changed) - stop asking for it for BLOCK_S."""
        self.blocked[kind] = self.gt + self.BLOCK_S
        if self.plan is not None and (self.plan.cm == kind or self.plan.jam == kind): self.plan = None   # re-plan without it   # slot -> (spec, launcher, last ttl, launch id): a slot is reused for each launch

    def _id(self, m):
        """Launch identity (slot, spec, launcher, n): the same fighter re-fires into the same slot."""
        old = self.ids.get(m.slot)
        if old is None or old[0] != m.spec or old[1] != m.lau or m.ttl > old[2]:
            old = (m.spec, m.lau, m.ttl, self.next_id); self.next_id += 1
        self.ids[m.slot] = (old[0], old[1], m.ttl, old[3])
        return (m.slot, m.spec, m.lau, old[3])

    # ----- time ------------------------------------------------------------------------------------------------------
    def _clock(self, snap, now):
        fs = max(1, snap["world"]["fs"])
        if self.last_frame is not None:
            df = (snap["frame"] - self.last_frame) & 0xFFFF
            if df < 2000: self.gt += df * 2 / fs
        self.last_frame = snap["frame"]
        self.hist.append((now, self.gt)); self.hist = [h for h in self.hist if h[0] >= now - 6.0]
        if len(self.hist) >= 3 and self.hist[-1][0] - self.hist[0][0] > 1.5:
            r = (self.hist[-1][1] - self.hist[0][1]) / (self.hist[-1][0] - self.hist[0][0])
            if 0.5 < r < 8: self.ratio = r
        return fs

    # ----- player trajectory for a candidate -------------------------------------------------------------------------
    def advance(self, st, cand, dt):
        """One step of dt game s of the player model toward the candidate: st = [x, y, hdg deg, roll deg, kt] (floats,
        updated in place). Roll toward +-bank at ROLL_RATE (proportional to the heading error inside 30 deg), turn rate
        TURN_K tan(roll) / kt, speed toward cand.kt at KT_RATE."""
        x, y, h, roll, kt = st
        err = wrap(cand.hdg - h)
        want_roll = math.copysign(cand.bank * min(1.0, abs(err) / 30.0), err) if abs(err) > 0.5 else 0.0
        roll += max(-self.ROLL_RATE * dt, min(self.ROLL_RATE * dt, want_roll - roll))
        kt += max(-self.KT_RATE * dt, min(self.KT_RATE * dt, cand.kt - kt))
        h = (h + self.TURN_K * math.tan(math.radians(roll)) / max(kt, 100) * dt) % 360
        v = kt * self.GROUND * dt
        st[:] = [x + v * math.sin(math.radians(h)), y - v * math.cos(math.radians(h)), h, roll, kt]

    def trajectory(self, P, cand, fs, n):
        """[(x, y, alt, hdg65536, roll deg, kt)] for frames 1..n from player P (missile_game.parse "player")."""
        st = [float(P["x"]), float(P["y"]), deg(P["hdg"]), G.s16(P["roll"]) * 360 / 65536, float(P["kt"])]
        if "fx" in P: st[0], st[1] = P["fx"], P["fy"]            # closed-loop test: sub-unit position
        out = []
        for _ in range(n):
            self.advance(st, cand, 2.0 / fs)
            out.append((int(round(st[0])), int(round(st[1])), P["alt"], u65(st[2]), st[3], st[4]))
        return out

    # ----- radar along a trajectory: launcher bits + stealth cost ----------------------------------------------------
    def _clut(self, snap, x, y):
        if self.clutter: return self.clutter(x, y)
        g = snap.get("grid")
        if not g: return 8
        cx, cy = min(15, max(0, int(x) >> 11)), min(15, max(0, int(y) >> 11))
        return g[cy * 16 + cx]

    def radar(self, snap, tr, fs, opp, cand, decoy_frames=0):
        """Predict every relevant radar over the trajectory: site pulses from their live countdown (+0x0C, pulse at 4,
        period 256 fs / (alert/4 + 16) + index frames, active bit 0x02 only), fighters checked every fs frames
        (conservative: any moment). -> (site_seen {rec: [bool per frame]}, ac_seen {slot: [...]}, cost, notes)."""
        n = len(tr); flags = snap["world"]["flags"]
        ecm = bool(flags & 0x10) or cand.jam == "ecm"
        bay = bool(flags & 4)
        cost, notes = 0.0, []
        need_sites = {m.lau for m in snap["missiles"] if m.lau > 2}
        site_seen = {}
        P = snap["player"]
        for rec, st in snap["sites"].items():
            if st["dead"]: continue
            rng = R.radar_spec(st["type"])[0]
            if not rng: continue
            if math.dist((P["x"], P["y"]), (st["x"], st["y"])) > (rng + 5) * K and rec not in need_sites: continue
            seen, row, alert = st["seen"], [], st["alert"]
            nxt = st["countdown"] - 4 if st["on"] else None
            if nxt is not None and nxt < 1: nxt = 1
            for k in range(1, n + 1):
                if nxt is not None and k >= nxt:
                    x, y, alt, h, roll, kt = tr[k - 1]
                    e = R.emv(int(alt), int(kt), opp, u65(abs(roll)), bay, ecm)
                    s = R.site_return(x, y, int(alt), h, int(kt), opp, self._clut(snap, x, y), st["x"], st["y"], st["type"])
                    seen = s + e > 100
                    a1 = R.alert_step(alert, seen, opp, ecm=ecm)
                    if seen:
                        c = self.COST["seen"] + a1 / 0x7F
                        for thr, key in ((0x7F, "net"), (0xC0, "launch"), (0xFA, "radar_id")):
                            if alert <= thr < a1 and not (key == "net" and k <= decoy_frames): c += self.COST[key]; notes.append(f"site {rec} {key}")
                        cost += c
                    alert = a1
                    nxt = k + int(256 * fs / ((alert >> 2) + 16)) + rec
                row.append(seen)
            site_seen[rec] = row
        ac_seen = {}
        CH = max(1, int(self.AC_CHECK_S * fs / 2))
        for slot, a in snap["aircraft"].items():
            sp = R.aircraft_spec(a["type"]) if a["type"] < 12 else {}
            if not sp.get("radar_type") or a["type"] in R.FRIENDLY_TYPES: continue
            d0 = math.dist((P["x"], P["y"]), (a["x"], a["y"]))
            if d0 > (sp["radar_range"] + 5) * K: continue
            vis_any, row, last_vis = False, [], (0 if a["seen"] else -10 ** 6)
            v = sp["max_speed_ms"] * 2 / fs / 1000 * K                       # units per frame, flying at us
            ax, ay, vis = float(a["x"]), float(a["y"]), a["seen"]
            for k in range(1, n + 1):
                x, y, alt, h, roll, kt = tr[k - 1]
                d = math.dist((x, y), (ax, ay)) or 1
                if d > 0.5 * K: ax += (x - ax) / d * v; ay += (y - ay) / d * v
                if k % fs == 1 or fs == 1:
                    e = R.emv(int(alt), int(kt), opp, u65(abs(roll)), bay, ecm)
                    s = R.site_return(x, y, int(alt), h, int(kt), opp, self._clut(snap, x, y), int(ax), int(ay), sp["radar_type"], a["alt"])
                    vis = s + e > 100
                    if vis: last_vis = k; vis_any = True
                row.append(k - last_vis < CH)
            ac_seen[slot] = row
            if vis_any:
                gain = 16 * opp + 32
                c = self.COST["seen"]
                for thr, key in ((0x7F, "net"), (0xC0, "launch"), (0xFA, "radar_id")):
                    if a["alert"] <= thr < a["alert"] + gain and not (key == "net" and decoy_frames > 0): c += self.COST[key]; notes.append(f"{sp['name']} {key}")
                cost += c
        return site_seen, ac_seen, cost, notes

    # ----- evaluate one candidate ------------------------------------------------------------------------------------
    def lat_frames(self, fs, perturb=None):
        return max(1, int(round(self.KEY_LATENCY_S * (2 if perturb == "latency" else 1) * self.ratio * fs / 2)))

    def evaluate(self, snap, cand, fs, opp, n, route_want, perturb=None, landed=False):
        """Fly every live missile against the candidate. perturb: None, 'nodead' (expired CMs don't capture), 'slowturn'
        (half the fitted turn rate), 'support' (launchers never lose us), 'latency' (keys take twice KEY_LATENCY_S) -
        the robustness checks. cand.cm_frame = the frame the key is pressed; it lands lat_frames later (landed=True: at
        cm_frame itself, for a CM already pressed whose remaining delay the caller worked out)."""
        tr = self.trajectory(snap["player"], cand, fs, n)
        if perturb == "slowturn":
            k0 = self.TURN_K; self.TURN_K = k0 / 2
            try: tr = self.trajectory(snap["player"], cand, fs, n)
            finally: self.TURN_K = k0
        life = (9 - 2 * opp) * fs
        decoy_frames = life * 8 if cand.cm == DECOY else (snap["world"]["cms"][0][3] if snap["world"]["cms"][0][3] > 0 else 0)
        site_seen, ac_seen, rcost, notes = self.radar(snap, tr, fs, opp, cand, decoy_frames)
        cur = [0]
        sup_site = (lambda rec: True) if perturb == "support" else \
                   (lambda rec: site_seen[rec][cur[0] - 1] if rec in site_seen else snap["sites"].get(rec, {}).get("seen", False))
        sup_ac = (lambda sl: True) if perturb == "support" else \
                 (lambda sl: ac_seen[sl][cur[0] - 1] if sl in ac_seen else snap["aircraft"].get(sl, {}).get("seen", False))
        def traj(k):
            cur[0] = k; t = tr[k - 1]; return t[0], t[1], t[2], t[3]
        dead0 = G.DEAD_CM
        if perturb == "nodead": G.DEAD_CM = False
        try:
            res, worst_margin, clear = {}, math.inf, True
            for m in snap["missiles"]:
                key = (m.slot, m.spec, m.lau, self.ids.get(m.slot, (0, 0, 0, 0))[3])
                w = G.make_world(snap); w.site_seen, w.ac_seen = sup_site, sup_ac
                lat = 0 if landed else self.lat_frames(fs, perturb)
                plan = {}
                if cand.jam: plan[1 + (0 if cand.jam in self.jam_on else lat)] = cand.jam   # a jammer on now works from lat on
                if cand.cm: plan[max(1, cand.cm_frame or 1) + lat] = cand.cm
                r = G.simulate(m, w, traj, min(n, max(1, m.ttl)), cm_plan=plan)
                radius = max(1, (m.spd << 3) // w.fs)
                r["radius"] = radius
                r["margin"] = r.get("closest_locked", r["closest"]) / radius
                res[key] = r
                k = self.MARGIN_UNTESTED if (cand.cm or cand.jam or r["outcome"] in ("captured",)) else self.MARGIN_TESTED
                if r["outcome"] in ("hit",) or r["margin"] < k: clear = False
                worst_margin = min(worst_margin, r["margin"] if r["outcome"] != "hit" else -1.0)
        finally:
            G.DEAD_CM = dead0
        stores = 0.0
        if cand.cm == DECOY: stores += self.COST["decoy"]
        elif cand.cm: stores += self.COST["cm"]
        if cand.jam == "ecm": stores += self.COST["ecm"]
        if cand.jam == "irj": stores += self.COST["irj"]
        route = self.COST["route_per_min"] * (n * 2 / fs / 60) * (1 - math.cos(math.radians(wrap(cand.hdg - route_want)))) \
            + 0.02 * abs(cand.kt - snap["player"]["kt"]) / 10 + 0.5 * (cand.bank > 20)
        return dict(res=res, clear=clear, margin=worst_margin, cost=rcost + stores + route, notes=notes)

    # ----- the decision ----------------------------------------------------------------------------------------------
    def candidates(self, snap, opp, route_want, base, threats):
        P = snap["player"]; cls = {m.cls for m in snap["missiles"]}
        stores = snap["stores"]; dmg40 = snap["world"]["dmg_bits"] & 0x40
        kts = sorted({self.EVADE_KT, int(P["kt"]), min(450, int(P["kt"]) + 80)})
        hi_ok = P["alt"] >= self.BANK_LOW_ALT
        banks = [b for b in self.BANKS if b <= 20 or hi_ok]
        cms = [(None, None, None)]
        ok_kind = lambda k: self.blocked.get(k, -1) < self.gt
        ttg = min((r["frame"] for r in base["res"].values() if r["outcome"] == "hit"), default=None)
        life = (9 - 2 * opp) * snap["world"]["fs"]
        for kind, ok in ((FLARE, any(c <= 0 for c in cls) and stores[0] > 0), (CHAFF, any(c > 0 for c in cls) and stores[1] > 0)):
            if not ok or not ok_kind(kind): continue
            cms.append((kind, 1, None))
            if ttg and ttg - life > 1: cms.append((kind, ttg - life, None))
            if ttg and ttg // 2 > 1: cms.append((kind, ttg // 2, None))
        if stores[2] > 0 and not dmg40 and snap["world"]["cms"][0][3] == 0 and ok_kind(DECOY): cms.append((DECOY, 1, None))
        if not dmg40:
            if any(c > 0 for c in cls) and not snap["world"]["flags"] & 0x10 and ok_kind("ecm"): cms.append((None, None, "ecm"))
            if any(c <= 0 for c in cls) and not snap["world"]["flags"] & 0x20 and ok_kind("irj"): cms.append((None, None, "irj"))
        out = []
        heads = sorted({(round(route_want / self.FAN_STEP) * self.FAN_STEP + d) % 360 for d in range(0, 360, self.FAN_STEP)}
                       | {round(route_want) % 360, round(deg(P["hdg"])) % 360})
        # stage 1: CM options on the current plan, and the heading fan with no CM, at the nav bank and current speed
        for kind, fr, jam in cms:
            out.append(Candidate(route_want, 20, P["kt"], kind, fr, jam, "plan"))
        for h in heads: out.append(Candidate(h, 20, P["kt"], label="fan"))
        return out, heads, banks, kts, cms

    def choose(self, snap, fs, opp, route_want, base, threats):
        import time
        t0 = time.perf_counter()
        n = int(self.HORIZON_S * fs / 2)
        stage1, heads, banks, kts, cms = self.candidates(snap, opp, route_want, base, threats)
        evals = []
        def run(c):
            e = self.evaluate(snap, c, fs, opp, n, route_want)
            c.cost, c.res, c.clear, c.margin = e["cost"], e["res"], e["clear"], e["margin"]; c.why = ", ".join(e["notes"][:3])
            evals.append(c); return c
        for c in stage1: run(c)
        if not any(c.clear for c in evals):       # stage 2: the full grid, one CM option each
            for h in heads:
                for b in banks:
                    for kt in kts:
                        for kind, fr, jam in cms:
                            if len(evals) >= self.MAX_CANDIDATES or time.perf_counter() - t0 > self.BUDGET_S: break
                            if b == 20 and kt == int(snap["player"]["kt"]) and kind is None and jam is None: continue
                            run(Candidate(h, b, kt, kind, fr, jam, "grid"))
        clear = sorted((c for c in evals if c.clear), key=lambda c: c.cost)
        for c in clear[:6]:                         # robustness: the less-tested rules perturbed
            c.robust = all(self.evaluate(snap, c, fs, opp, n, route_want, perturb=pb)["clear"]
                           for pb in ("nodead", "slowturn", "support", "latency"))
            if c.robust: return c, "robust", len(evals)
        if clear: return clear[0], "clears (not robust to the untested rules)", len(evals)
        best = max(evals, key=lambda c: (c.margin, -c.cost))
        return best, "SURVIVAL (no candidate clears every missile)", len(evals)

    def step(self, s, p, now, route_want, snap, opp=None):
        """-> (overrides, [events]). s: nav read_state; snap: missile_game.parse() of live memory; route_want: the heading
        nav would fly without this module (route / attack / FighterWatch)."""
        ev = []
        if self.mode == "off": return {}, ev
        opp = snap["world"]["opp"] if opp is None else opp
        fs = self._clock(snap, now)
        live = {self._id(m): m for m in snap["missiles"]}
        for sl in [sl for sl in self.ids if sl not in {m.slot for m in snap["missiles"]}]: del self.ids[sl]   # slot freed
        if self.plan is not None and self.cm_pending and not any(k in live for k in self.plan.res):
            ev.append("missile evasion: the missiles the plan was for are gone - pending CM cancelled"); self.cm_pending = []
        P = snap["player"]
        for key, m in live.items():
            if key not in self.known:
                d = G.gdist(P["x"] - m.x, P["y"] - m.y) / K
                lau = f"site record {m.lau}" if m.lau > 2 else "SA-14 cell" if m.lau in (1, 2) else f"aircraft slot {-m.lau}"
                self.known[key] = dict(t=self.gt, d=d, pred=None)
                ev.append(f"MISSILE LAUNCH seen: {m.name} ({G.CLASS_NAME.get(m.cls, m.cls)}) slot {m.slot} from {lau}, {d:.1f} km, ttl {m.ttl}")
        for key in [k for k in self.known if k not in live]:
            info = self.known.pop(key)
            ev.append(f"missile {G.SPECS[key[1]]['name']} slot {key[0]} gone (predicted {info['pred']}; real: see damage / event log)")
        if not live:
            self.worst = ""
            if self.state == "off": return {}, ev
            self.clear_since = self.clear_since if self.clear_since is not None else self.gt
            if self.gt - self.clear_since < self.CLEAR_S: return self._ov(fs), ev
            off = [j for j in self.jam_on]
            self.state, self.plan, self.clear_since, self.cm_pending, self.action = "off", None, None, [], ""
            ov = {}
            if off and self.mode == "act": ov["jam_off"] = off; self.jam_on.clear()
            return ov, ev + ["missile evasion: clear, back to the plan"]
        self.clear_since = None
        n = int(self.HORIZON_S * fs / 2)
        base = self.evaluate(snap, Candidate(route_want, 20, P["kt"]), fs, opp, n, route_want)
        threats = []
        for key, r in base["res"].items():
            m = live[key]
            kk = self.MARGIN_TESTED
            pred = "HIT" if r["outcome"] == "hit" else ("near" if r["margin"] < kk else
                   "no hit in %ds" % self.HORIZON_S if r["outcome"] == "flying" else r["outcome"])
            if self.known[key]["pred"] != pred:
                ev.append(f"{m.name} slot {m.slot}: prediction {self.known[key]['pred']} -> {pred} "
                          f"(closest locked {r.get('closest_locked', r['closest'])} u, hit radius {r['radius']}, why {r['last_why'] or '-'})")
                self.known[key]["pred"] = pred
            if pred in ("HIT", "near"): threats.append((r["frame"], m, r))
        threats.sort(key=lambda t: t[0])
        if threats:
            fr, m, r = threats[0]
            d = G.gdist(P["x"] - m.x, P["y"] - m.y) / K
            self.worst = (f"{m.name} s{m.slot} {d:.1f}km ttg {fr * 2 / fs:.1f}s off {abs(G.s16(G.atan2g(P['x'] - m.x, m.y - P['y']) - m.hdg)) * 360 / 65536:.0f} "
                          f"tail {abs(G.s16(m.hdg - P['hdg'])) * 360 / 65536:.0f} {r['last_why'] or 'locked'} -> {r['outcome']}")
        else:
            m = min(live.values(), key=lambda m: G.gdist(P["x"] - m.x, P["y"] - m.y))
            self.worst = f"{m.name} s{m.slot} {G.gdist(P['x'] - m.x, P['y'] - m.y) / K:.1f}km doomed ({base['res'][self._key(m)]['outcome']})"
        # keep the committed plan while it works
        if self.plan is not None:
            if self.gt >= self.plan_end:
                ev.append(f"missile evasion: plan window over ({self.plan.text()})"); self.plan = None
            else:
                cm, fr = self._cm_in_flight(snap, fs); landed = cm is not None
                if cm is None and self.cm_pending:          # the plan's own deferred release, still to come
                    t, cm = self.cm_pending[0]; fr = max(1, int(round((t - self.gt) * fs / 2)) + 1)
                keep = Candidate(self.plan.hdg, self.plan.bank, self.plan.kt, cm, fr if cm else None, self.plan.jam)
                e = self.evaluate(snap, keep, fs, opp, n, route_want, landed=landed)
                new = any(k not in self.plan.res for k in live)
                flipped = any(r["outcome"] == "hit" for r in e["res"].values())
                if self.state == "survival":             # it predicted a hit already: look again now and then, not every cycle
                    flipped = self.gt - self.plan_t >= self.SURVIVAL_REPLAN_S
                if flipped or new:
                    ev.append(f"missile evasion: re-plan ({'new missile' if new else 'survival: looking again' if self.state == 'survival' else 'committed plan now predicts a hit'})"); self.plan = None
        if self.plan is None and threats:
            c, how, nev = self.choose(snap, fs, opp, route_want, base, threats)
            self.plan, self.plan_end, self.plan_t = c, self.gt + self.WINDOW_S, self.gt
            self.cm_pending = []
            if c.cm: self.cm_pending.append((self.gt + max(0, (c.cm_frame or 1) - 1) * 2 / fs, c.cm))
            outs = "; ".join(f"{G.SPECS[k[1]]['name']} s{k[0]} {r['outcome']} {r['margin']:.1f}r" for k, r in c.res.items())
            ev.append(f"MISSILE EVASION ({how}, {nev} candidates): {c.text()}, cost {c.cost:.1f}"
                      + (f" [{c.why}]" if c.why else "") + f" -> {outs}")
            self.state = "survival" if how.startswith("SURVIVAL") else "evade"
        if self.plan is None:
            self.state = "watch"; self.action = ""
            return {}, ev
        ov = self._ov(fs)
        due = [k for t, k in self.cm_pending if t <= self.gt]
        if due:
            self.cm_pending = [(t, k) for t, k in self.cm_pending if t > self.gt]
            ov["cm"] = due[0]; ev.append(f"missile evasion: {CM_NAME[due[0]]} ({'key ' + KEYS[due[0]]})")
            self.released = (self.gt, due[0], snap["stores"][due[0] - 1])
        if self.plan.jam and self.plan.jam not in self.jam_on:
            ov["jam_on"] = self.plan.jam; self.jam_on.add(self.plan.jam); ev.append(f"missile evasion: {CM_NAME[self.plan.jam]} ON")
        if self.mode != "act": return {}, ev
        return ov, ev

    CM_LATENCY_S = 6.0                         # game s after which an unconfirmed CM is taken as lost
    def _cm_in_flight(self, snap, fs):
        """A CM this module asked for that memory doesn't show yet (key latency; or never, in observe mode) -> (kind,
        frame it should land) for re-checking the committed plan, so the plan isn't re-chosen (and the CM re-dropped)."""
        if not self.released: return None, None
        t, kind, stores0 = self.released
        left = max(1, int(round((t + self.KEY_LATENCY_S * self.ratio - self.gt) * fs / 2)))
        if self.mode != "act": return (kind, left) if self.gt - t < self.WINDOW_S else (None, None)
        if snap["stores"][kind - 1] < stores0 or self.gt - t > self.CM_LATENCY_S: return None, None
        return kind, left

    def _key(self, m): return (m.slot, m.spec, m.lau, self.ids.get(m.slot, (0, 0, 0, 0))[3])

    def _ov(self, fs):
        c = self.plan
        if c is None: return {}
        rate = self.TURN_K * math.tan(math.radians(c.bank)) / max(c.kt, 100) * self.ratio   # deg per REAL s for nav
        self.action = c.text() + (" SURVIVAL" if self.state == "survival" else "")
        return dict(want=c.hdg, rate_cap=max(0.8, rate), bank_max=float(c.bank), speed_kt=c.kt,
                    alt_min=self.MIN_TURN_ALT, missile=self.state, abort_attack=True, survival=self.state == "survival")
