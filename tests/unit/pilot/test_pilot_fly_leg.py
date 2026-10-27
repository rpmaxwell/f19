"""WP-7b equivalence, FlyLeg: nav.py's route following (load_route, in_loiter, seg_dist, next_leg, route_aim and the
leg / takeoff part of its main loop, COPIED below: nav is module-level code and can't be imported) vs
f19.pilot.phases.FlyLeg, on every nav-CSV row of the replay fixtures and on synthetic routes (loiter, fly-over
targets, zero-length legs, an in-air start, a rejoin from far off the line). Exact: same leg, heading, turn-rate cap and
leg altitude every tick. Also reported: agreement with what nav logged (leg, want on rows no override touched; the
CSV positions are rounded to whole units, so the heading is compared within 0.5 deg)."""
import json
import math

import pytest

from pilot_equiv import flat_state  # first: puts tests/replay on sys.path
import harness as H  # noqa: E402
from f19.core.clock import ManualClock
from f19.core.types import FlightContext, Route
from f19.pilot.phases import FlyLeg

K = 64.0


# ------------------------------------------------------------------------------------- nav.py, copied (2689b57)
def old_nav(route_json):
    """nav.py's route code bound to one route: returns tick(s, p, now) -> (leg, want, cap, leg alt_ft) and the state."""
    def load_route(route):
        attrs = None
        if "points" in route:
            wps = [((q["x"], q["y"]), q["label"]) for q in route["points"]]; attrs = route["points"]
        else:
            wps = [(tuple(p), name) for p, name in route["waypoints_game"]]
        attrs = attrs or [{} for _ in wps]
        _w, _a = [wps[0]], [attrs[0]]
        for (p, name), at in zip(wps[1:], attrs[1:]):
            if math.dist(p, _w[-1][0]) < 1.0: _w[-1] = (_w[-1][0], _w[-1][1] or name); _a[-1] = {**at, **_a[-1]}
            else: _w.append((p, name)); _a.append(at)
        return _w, _a
    WPS, ATTRS = load_route(route_json)
    def leg_attrs(leg): return ATTRS[leg] if ATTRS and leg < len(ATTRS) else {}
    LOOKAHEAD, ARRIVE, OVERFLY = 12.0 * K, 3.0 * K, 0.5 * K
    LOITER_LOOKAHEAD = 6.0 * K
    CLIMB_DIST, CLIMB_ALT = 6.0 * K, 300
    REJOIN = 5.0 * K
    H_RATE_MAX, H_RATE_LOITER = 0.8, 2.5
    def fly_over(name): return name.startswith(("PRIMARY", "SECONDARY"))
    def bearing(a, b): return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360
    def dist(a, b): return math.hypot(b[0] - a[0], b[1] - a[1])
    def in_loiter(leg):
        return next((WPS[i][1] for i in range(leg - 1, -1, -1) if WPS[i][1]), "").startswith("LOITER")
    def seg_dist(i, p):
        (ax, ay), (bx, by) = WPS[i - 1][0], WPS[i][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
        t = max(0.0, min(1.0, ((p[0] - ax) * (bx - ax) + (p[1] - ay) * (by - ay)) / L2))
        return math.hypot(p[0] - ax - t * (bx - ax), p[1] - ay - t * (by - ay))
    def next_leg(leg, p, window=8):
        best, bd = leg, seg_dist(leg, p) - 1.0 * K
        for i in range(leg + 1, min(len(WPS), leg + 1 + window)):
            if fly_over(WPS[i - 1][1]): break
            d = seg_dist(i, p)
            if d < bd: best, bd = i, d
        return best
    def route_aim(leg, along, xtrack):
        la = LOITER_LOOKAHEAD if in_loiter(leg) else LOOKAHEAD
        t, i = max(along, 0.0) + max(la, abs(xtrack) if abs(xtrack) > REJOIN else 0.0), leg
        while True:
            A_, B_ = WPS[i - 1][0], WPS[i][0]; Li = dist(A_, B_) or 1e-9
            if t <= Li or i == len(WPS) - 1 or fly_over(WPS[i][1]):
                if not fly_over(WPS[i][1]): t = min(t, Li)
                return (A_[0] + (B_[0] - A_[0]) * t / Li, A_[1] + (B_[1] - A_[1]) * t / Li)
            t -= Li; i += 1
    S = dict(leg=1, phase="takeoff", start_pos=None, runway_hdg=None, first=True, WPS=WPS, ATTRS=ATTRS)
    def tick(s, p):
        if S["first"]:                                   # nav: s0 = the first read, before the main loop
            S["first"] = False
            if s["alt"] > CLIMB_ALT and len(WPS) > 2:
                p0 = p
                def seg_d(i):
                    (ax, ay), (bx, by) = WPS[i - 1][0], WPS[i][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
                    t = max(0.0, min(1.0, ((p0[0] - ax) * (bx - ax) + (p0[1] - ay) * (by - ay)) / L2))
                    return math.hypot(p0[0] - ax - t * (bx - ax), p0[1] - ay - t * (by - ay))
                S["leg"] = min(range(1, len(WPS)), key=lambda i: (round(seg_d(i) / K), -i))
                S["phase"] = "route"
        leg = S["leg"]
        while True:
            a, b = WPS[leg - 1][0], WPS[leg][0]
            L = dist(a, b) or 1e-9; ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            along = (p[0] - a[0]) * ux + (p[1] - a[1]) * uy
            xtrack = (p[0] - a[0]) * -uy + (p[1] - a[1]) * ux
            arrive = OVERFLY if fly_over(WPS[leg][1]) else ARRIVE
            if (dist(p, b) < arrive or along > L) and leg < len(WPS) - 1:
                leg += 1; continue
            j = next_leg(leg, p, window=2 if in_loiter(leg) else 8)
            if j != leg: leg = j; continue
            S["b"] = b
            aim = route_aim(leg, along, xtrack)
            want = bearing(p, aim)
            if S["start_pos"] is None: S["start_pos"] = p
            if S["phase"] == "takeoff":
                if S["runway_hdg"] is None and s["alt"] < 150: S["runway_hdg"] = s["hdg"]
                if (dist(p, S["start_pos"]) >= CLIMB_DIST and s["alt"] >= CLIMB_ALT) or (S["runway_hdg"] is None and s["alt"] >= CLIMB_ALT):
                    S["phase"] = "route"
                    while WPS[leg][1] == "Climb-out" and leg < len(WPS) - 1: leg += 1
                elif S["runway_hdg"] is not None:
                    want = S["runway_hdg"]
            break
        S["leg"] = leg
        return leg, want, H_RATE_LOITER if in_loiter(leg) else H_RATE_MAX, leg_attrs(leg).get("alt_ft")
    return tick, S


# ------------------------------------------------------------------------------------- runner
def run(route_json, inputs):
    """inputs: [(t, s dict, p)] -> (ticks, equal, [(leg, want) new per tick])."""
    tick, S = old_nav(route_json)
    fl = FlyLeg(); ctx = FlightContext(ManualClock(0.0), route=Route.from_route_json(route_json))
    n = eq = 0; out = []
    for t, s, p in inputs:
        ctx.clock.set(t)
        o = tick(s, p)
        c = fl.step(flat_state(s["alt"], s["spd"], s["hdg"], *p), ctx)
        nw = (ctx.leg, c.heading, c.rate_cap, c.alt)
        n += 1; eq += o == nw
        assert o == nw, (t, o, nw)
        assert fl.mode == S["phase"]
        out.append(nw)
    return n, eq, out


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id", ["1792609731", "1792546698", "1792715432"])
def test_fly_leg_replay(run_id):
    R = H.Run.load(run_id)
    rows = [r for r in R.rows if r.get("alt") not in (None, "")]
    inputs = [(float(r["t"]), dict(alt=int(r["alt"]), spd=int(r["spd"]), hdg=float(r["hdg"])), (float(r["x"]), float(r["y"])))
              for r in rows]
    n, eq, out = run(R.route, inputs)
    assert n == eq and n > 1000
    # vs the nav log (not exact: CSV positions are whole units, and the log's want is after every override)
    clean = [(r, o) for r, o in zip(rows, out) if r["phase"] in ("takeoff", "route") and not any(
        r.get(k) for k in ("attack_state", "escape", "fighter", "missile"))]
    leg_ok = sum(int(r["leg"]) == o[0] for r, o in clean)
    want_ok = sum(abs((float(r["want"]) - o[1] + 180) % 360 - 180) <= 0.5 for r, o in clean)
    print(f"fly_leg {run_id}: {n} rows, old == new on all; vs nav log on {len(clean)} un-overridden rows: "
          f"leg {leg_ok} ({leg_ok / len(clean):.1%}), want within 0.5 deg {want_ok} ({want_ok / len(clean):.1%})")
    assert leg_ok / len(clean) > 0.95 and want_ok / len(clean) > 0.9


def _synthetic_route():
    """Takeoff, Climb-out, a corner, a zero-length leg, a loiter (8 points round a 4 km circle), a target (fly-over),
    an approach and Final."""
    pts = [dict(x=10000, y=30000, label="Takeoff (Base)", kind="takeoff"), dict(x=10000, y=29000, label="Climb-out", kind="climb", alt_ft=450),
           dict(x=10000, y=26000, label="", alt_ft=300), dict(x=12000, y=24000, label=""), dict(x=12000.4, y=24000.2, label="dup", alt_ft=999),
           dict(x=14000, y=24000, label="LOITER 1", kind="loiter")]
    cx, cy = 14000, 24256
    pts += [dict(x=round(cx + 256 * math.sin(a * math.pi / 4)), y=round(cy - 256 * math.cos(a * math.pi / 4)), label="") for a in range(1, 9)]
    pts += [dict(x=18000, y=22000, label="PRIMARY run-in", kind="runin", role="primary"),
            dict(x=19000, y=21000, label="PRIMARY: photo point 1 km", kind="photo", role="primary"),
            dict(x=19500, y=20500, label="PRIMARY: Tank Farm", kind="overfly", role="primary"),
            dict(x=16000, y=18000, label=""), dict(x=10000, y=14000, label="Approach fix", kind="approach"),
            dict(x=10000, y=11000, label="Final", kind="final"), dict(x=10000, y=10000, label="Landing (Base)", kind="landing")]
    return dict(points=pts)


@pytest.mark.parametrize("start", ["runway", "air", "far_off"])
def test_fly_leg_synthetic(start):
    """Fly the synthetic route with a crude turn model (2 deg a tick toward the heading the OLD code asks for)."""
    route = _synthetic_route()
    x, y, alt, hdg = dict(runway=(10000, 30000, 0, 0.0), air=(12500, 24300, 900, 90.0), far_off=(4000, 26000, 600, 45.0))[start]
    tick, _ = old_nav(route)
    inputs, spd, t = [], 0, 0.0
    for i in range(6000):
        t += 0.25; spd = min(200, spd + 5); alt = min(500, alt + (4 if spd > 120 else 0))
        s, p = dict(alt=alt, spd=spd, hdg=hdg), (x, y)
        inputs.append((t, s, p))
        want = tick(s, p)[1]
        d = (want - hdg + 180) % 360 - 180; hdg = (hdg + max(-2, min(2, d))) % 360
        v = spd / 200 * 25
        x += v * math.sin(math.radians(hdg)); y -= v * math.cos(math.radians(hdg))
        if math.hypot(x - 10000, y - 10000) < 100: break
    n, eq, out = run(route, inputs)
    legs = {o[0] for o in out}
    assert n == eq
    assert {o[2] for o in out} == {0.8, 2.5}, legs                                  # loiter cap seen
    if start == "runway": assert {None, 450, 300} <= {o[3] for o in out}, {o[3] for o in out}   # leg altitudes
    assert max(legs) >= 18, legs                      # reached the approach legs
    print(f"fly_leg synthetic {start}: {n} ticks, legs {sorted(legs)}")


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id", ["1792609731", "1792546698", "1792715432"])
def test_fly_leg_triggers_replay(run_id):
    """FlyLeg.attacks / attack_role / landing_due vs nav's build_attacks (the harness copy) and nav's attack / landing
    start conditions (copied below) on every row; the first attack start per role vs nav's logged event."""
    R = H.Run.load(run_id)
    tick, S = old_nav(R.route)
    fl = FlyLeg(); ctx = FlightContext(ManualClock(0.0), route=Route.from_route_json(R.route))
    rows = [r for r in R.rows if r.get("alt") not in (None, "")]
    started, done = {}, set()
    for r in rows:
        s, p = dict(alt=int(r["alt"]), spd=int(r["spd"]), hdg=float(r["hdg"])), (float(r["x"]), float(r["y"]))
        tick(s, p); fl.step(flat_state(s["alt"], s["spd"], s["hdg"], *p), ctx)
        # nav main loop (phase == "route" there is the director's: here every row counts)
        leg = S["leg"]; la_ = S["ATTRS"][leg] if leg < len(S["ATTRS"]) else {}; role = la_.get("role")
        dpb = math.hypot(S["b"][0] - p[0], S["b"][1] - p[1])
        old_role = role if role and role not in done and (la_.get("kind") in ("launch", "photo") or (
            la_.get("kind") == "runin" and dpb < 10 * K) or (la_.get("kind") == "overfly" and dpb < 5 * K)) else None
        new_role = fl.attack_role(flat_state(s["alt"], s["spd"], s["hdg"], *p), ctx, done)
        if S["phase"] == "route": assert old_role == new_role, (r["t"], old_role, new_role)
        name = S["WPS"][leg][1]
        assert fl.landing_due(ctx) == (name == "Final" or name.startswith("Landing"))
        if new_role and S["phase"] == "route" and new_role not in started: started[new_role] = float(r["t"]); done.add(new_role)
    assert fl.attacks(R.mission) == H.build_attacks(R.route, R.mission)
    for role, t in started.items():
        logged = R.event_times(f"{role} attack start")
        if logged: assert abs(t - logged[0]) < 1.0, (role, t, logged)
    print(f"fly_leg triggers {run_id}: attack starts {started}, nav events "
          f"{ {ro: R.event_times(f'{ro} attack start')[:1] for ro in started} }")


def test_takeoff_done_turns_onto_the_route_at_once():
    """2026-10-27: --auto leaves the runway heading as soon as the Takeoff behavior is done, not 6 km on."""
    from f19.core.clock import ManualClock
    from f19.core.types import Attitude, FlightContext, GameState, Position, Route, RoutePoint, Systems
    route = Route((RoutePoint(10000.0, 30000.0, "Takeoff (Base)"), RoutePoint(10000.0, 29000.0, "Climb-out"),
                   RoutePoint(14000.0, 20000.0, "WP"), RoutePoint(14000.0, 10000.0, "Landing")))
    ctx = FlightContext(ManualClock(0.0), route=route)
    mk = lambda y, alt: GameState(t=0.0, pos=Position(10000.0, y, alt), att=Attitude(0.0), speed_kt=250.0, systems=Systems(flags=0x01), sensitivity=1)
    for done in (False, True):
        f = FlyLeg(); ctx.leg = 1
        f.step(mk(30000.0, 20), ctx)                          # on the ground: the runway heading is taken
        f.takeoff_done = done
        f.step(mk(29900.0, 350), ctx)                         # 1.5 km on, 350 ft
        assert (f.mode == "route") is done and (ctx.leg == 2) is done
