"""FlyLeg: nav.py's route following (route_aim / next_leg / in_loiter / seg_dist and the leg-advance part of its main
loop) as a phase that reads ctx.route and keeps the leg in ctx.leg.

Per tick (nav main loop, kept in order):
  1. leg advance: within ARRIVE (OVERFLY for a target) of the leg's end, or past it along the leg -> next leg; then
     next_leg() looks up to 8 legs ahead (2 on a loiter: laps overlap) for one the plane is clearly nearer to, never
     past a fly-over waypoint (a target). nav `continue`d and re-read the state after each advance; FlyLeg repeats the
     advance on the same state.
  2. aim = route_aim(): LOOKAHEAD (LOITER_LOOKAHEAD on a loiter) ahead of the plane's projection onto the leg, carried
     over onto later legs; off the line by more than REJOIN, |xtrack| ahead instead (a ~45 deg intercept); a fly-over
     waypoint stops the carry-over (aim along this leg's line extended past it). heading = bearing to the aim.
  3. nav's own "takeoff" phase: hold the runway heading (the heading on the first tick below 150 ft) until CLIMB_DIST
     from the start point and above CLIMB_ALT (or above CLIMB_ALT with no runway heading), then skip the Climb-out legs.
     As in nav, the tick that ends it still flies the heading aimed on the old leg.
  Started in the air (first tick above CLIMB_ALT, more than 2 points): join the nearest leg (to 1 km, later on ties).
Command: heading, rate_cap (H_RATE_LOITER on a loiter leg else H_RATE_MAX: nav's default turn-rate cap), alt (the leg
end's alt_ft when the route has one: nav's hold_alt per leg).

Points: nav's load_route rule - zero-length legs (consecutive points < 1 unit apart) are merged into the first point
(its position; the first non-empty label; the earlier point's attributes win). So FlyLeg's leg numbers are nav's, and
differ from Route.legs where a route has zero-length legs. --fixed (nav's test heading) is not ported.
"""
import math

from f19.core.geometry import dist
from f19.core.types import Command

from f19.pilot.phases._common import K, bearing, game_dist


def nav_points(route):
    """[(xy, label, attrs dict)] of a Route, zero-length legs merged as nav.load_route did."""
    pts = [((float(q.x), float(q.y)), q.label, q.to_dict()) for q in route.points]
    out = [pts[0]]
    for p, name, at in pts[1:]:
        if math.dist(p, out[-1][0]) < 1.0: out[-1] = (out[-1][0], out[-1][1] or name, {**at, **out[-1][2]})
        else: out.append((p, name, at))
    return out


def fly_over(name): return name.startswith(("PRIMARY", "SECONDARY"))   # targets: pass directly over, then turn


class FlyLeg:
    name, priority = "fly_leg", 0
    LOOKAHEAD, ARRIVE, OVERFLY = 12.0 * K, 3.0 * K, 0.5 * K   # lookahead ~ turn radius at 0.8 deg/s (~13 km)
    LOITER_LOOKAHEAD = 6.0 * K
    CLIMB_DIST, CLIMB_ALT = 6.0 * K, 300
    REJOIN = 5.0 * K          # beyond this cross-track distance, intercept the route further ahead (route_aim)
    H_RATE_MAX, H_RATE_LOITER = 0.8, 2.5                      # nav's turn-rate caps, deg per real s

    def __init__(self):
        self.mode = "takeoff"                                 # nav's phase "takeoff" until the climb-out, then "route"
        self.start_pos = self.runway_hdg = None
        self.started = False
        self.done = False                                     # never: the route is the fallback behavior
        self._route, self.wps = None, None
        self.along = self.xtrack = self.aim = None

    def wants_control(self, state, ctx): return ctx.route is not None

    # --- nav's helpers, on self.wps
    def _points(self, ctx):
        if ctx.route is not self._route:
            self._route, self.wps = ctx.route, nav_points(ctx.route)
        return self.wps

    def attrs(self, leg):
        return self.wps[leg][2] if self.wps and leg < len(self.wps) else {}

    def in_loiter(self, leg):
        W = self.wps
        return next((W[i][1] for i in range(leg - 1, -1, -1) if W[i][1]), "").startswith("LOITER")

    def seg_dist(self, i, p):
        W = self.wps
        (ax, ay), (bx, by) = W[i - 1][0], W[i][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
        t = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
        return math.hypot(p[0] - ax - t * (bx - ax), p[1] - ay - t * (by - ay))

    def next_leg(self, leg, p, window=8):
        """A later leg the plane is clearly nearer to than the current one (never past a fly-over waypoint)."""
        W = self.wps
        best, bd = leg, self.seg_dist(leg, p) - 1.0 * K
        for i in range(leg + 1, min(len(W), leg + 1 + window)):
            if fly_over(W[i - 1][1]): break
            d = self.seg_dist(i, p)
            if d < bd: best, bd = i, d
        return best

    def route_aim(self, leg, along, xtrack):
        W = self.wps
        la = self.LOITER_LOOKAHEAD if self.in_loiter(leg) else self.LOOKAHEAD
        t, i = max(along, 0.0) + max(la, abs(xtrack) if abs(xtrack) > self.REJOIN else 0.0), leg
        while True:
            A_, B_ = W[i - 1][0], W[i][0]; Li = dist(A_, B_) or 1e-9
            if t <= Li or i == len(W) - 1 or fly_over(W[i][1]):
                if not fly_over(W[i][1]): t = min(t, Li)
                return (A_[0] + (B_[0] - A_[0]) * t / Li, A_[1] + (B_[1] - A_[1]) * t / Li)
            t -= Li; i += 1

    def _join(self, p):
        """nav, started in the air: the nearest leg (to 1 km), later on ties."""
        return min(range(1, len(self.wps)), key=lambda i: (round(self.seg_dist(i, p) / K), -i))

    # --- the tick
    def step(self, state, ctx):
        W = self._points(ctx); p = state.xy; alt = state.alt_ft
        if not self.started:
            self.started = True
            if alt > self.CLIMB_ALT and len(W) > 2: ctx.leg = self._join(p); self.mode = "route"
        leg = ctx.leg
        while True:
            a, b = W[leg - 1][0], W[leg][0]
            L = dist(a, b) or 1e-9; ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            along = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
            xtrack = (p[0] - a[0]) * -uy + (p[1] - a[1]) * ux
            arrive = self.OVERFLY if fly_over(W[leg][1]) else self.ARRIVE
            if (dist(p, b) < arrive or along > L) and leg < len(W) - 1:
                leg += 1; continue
            j = self.next_leg(leg, p, window=2 if self.in_loiter(leg) else 8)   # loiter laps overlap: never skip a lap
            if j != leg: leg = j; continue
            break
        self.aim = self.route_aim(leg, along, xtrack); self.along, self.xtrack = along, xtrack
        want = bearing(p, self.aim)
        if self.start_pos is None: self.start_pos = p
        if self.mode == "takeoff":
            if self.runway_hdg is None and alt < 150: self.runway_hdg = state.heading
            if (dist(p, self.start_pos) >= self.CLIMB_DIST and alt >= self.CLIMB_ALT) or (self.runway_hdg is None and alt >= self.CLIMB_ALT):
                self.mode = "route"
                while W[leg][1] == "Climb-out" and leg < len(W) - 1: leg += 1
            elif self.runway_hdg is not None:
                want = self.runway_hdg                          # straight ahead along the runway
        ctx.leg = leg
        return Command(heading=want, rate_cap=self.H_RATE_LOITER if self.in_loiter(leg) else self.H_RATE_MAX,
                       alt=self.attrs(leg).get("alt_ft"))

    # --- what nav's main loop read off the route for the phase triggers (the FlightDirector's to call)
    def to_turn(self, state, ctx):
        """Units to the current leg's end (nav dist(p, b))."""
        return dist(state.xy, self.wps[ctx.leg][0])

    def attack_role(self, state, ctx, done=(), force=None):
        """nav's attack start: the role of the current leg end when it is a launch / photo point, a run-in within
        10 km or an overfly within 5 km, and that role isn't attacked yet. force: nav --attack-now (that role now)."""
        la = self.attrs(ctx.leg); role = la.get("role")
        if force and force not in done: role, la = force, {"kind": "launch"}
        d = self.to_turn(state, ctx)
        if role and role not in done and (la.get("kind") in ("launch", "photo") or (la.get("kind") == "runin" and d < 10 * K)
                                          or (la.get("kind") == "overfly" and d < 5 * K)):
            return role
        return None

    def landing_due(self, ctx):
        """nav: the leg to Final (or Landing) starts the landing."""
        name = self.wps[ctx.leg][1]
        return name == "Final" or name.startswith("Landing")

    def landing_points(self):
        """(base, approach fix) for Landing: the last point and the "Approach fix" point."""
        ia = next(i for i in range(len(self.wps)) if self.wps[i][1] == "Approach fix")
        return self.wps[-1][0], self.wps[ia][0]

    def attacks(self, mission):
        """nav.build_attacks: {role: dict(mode, T, record, launch_km, photo_km)} from the route's attack points and the
        mission's targets (Mission or mission_mem.json dict)."""
        targets = mission["targets"] if isinstance(mission, dict) else [
            dict(role=t.role, x=t.x, y=t.y, record=t.record) for t in mission.targets]
        out = {}
        for t in targets:
            role = t["role"].rstrip("?"); T = (float(t["x"]), float(t["y"]))
            kinds = {a.get("kind"): xy for xy, _, a in self.wps if a.get("role") == role}
            mode = "maverick" if "launch" in kinds else "photo" if "photo" in kinds else "overfly"
            out[role] = dict(mode=mode, T=T, record=t.get("record"),
                             launch_km=game_dist(kinds["launch"], T) / K if "launch" in kinds else None,
                             photo_km=game_dist(kinds["photo"], T) / K if "photo" in kinds else None)
        return out
