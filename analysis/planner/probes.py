"""Test segments ("probes") spliced into an otherwise optimal route.

A probe is a fixed piece of flight (points + per-point attrs such as alt_ft / speed_kt)
that answers one question about radar. plan.json lists probes; each may name several
candidates (radars, bearings) and the planner picks the one that costs the route least
and still routes cleanly. Placement is by meaning ("after": "primary"), never by
waypoint index, so probes survive any change to the base route.

  {"type": "radial", "radar": "Cottbus" | ["Cottbus", "Dessau"] | "pulse" | "doppler",
   "heading": 90 | "bearing": 270 | (neither = try every 30 deg),
   "r0": 60, "r1": 5,                     km from the radar, flown r0 -> r1
   "alt_ft": 450, "speed_kt": 190, "isolate": false, "after": "primary" | "auto"}
      straight run at / away from a radar. heading = direction flown; bearing = from the radar.
  {"type": "arc", "radar": ..., "range_km": 40, "b0": 200, "b1": 300, ...}
      constant-range arc around a radar (range_km >= min turn radius), bearings b0 -> b1.
  {"type": "orbit", "center": [x, y], "laps": 2, "dir": "L"|"R", "radius_km": 20, ...}
      full circles, entered and left on tangents.
Common keys: "label", "alt_ft", "speed_kt", "after", "isolate" (no other radar in sight),
"min_los" (fraction of the run on which the radar model says the site sees the plane, margin > -M;
default 0.8 for radial/arc). There is no terrain line of sight in the game: only the cell type matters.
"""
import math
import numpy as np
from world import K

STEP_KM = 4.0


def _at(r, km, brg):
    b = math.radians(brg); return (r["x"] + km * K * math.sin(b), r["y"] - km * K * math.cos(b))


def _radars(world, spec):
    sel = spec.get("radar")
    pool = [r for r in world.radars if r["place"] != "Patrol Boat"]
    if sel in ("pulse", "doppler"): return [r for r in pool if r["type"] == sel[0].upper()]
    names = [sel] if isinstance(sel, str) else list(sel or [])
    out = [r for r in pool if r["place"] in names]
    missing = set(names) - {r["place"] for r in out}
    if missing: raise ValueError(f"probe radar(s) not in this mission: {', '.join(sorted(missing))}")
    return out


def _attrs(spec, label):
    a = {"kind": "probe", "probe": label}
    for k in ("alt_ft", "speed_kt"):
        if k in spec: a[k] = spec[k]
    return a


def candidates(world, spec, R_km):
    """All concrete versions of one probe spec: [(points [(xy, label, attrs)], description)]."""
    t = spec["type"]; out = []
    if t == "radial":
        for r in _radars(world, spec):
            inbound = spec["r1"] < spec["r0"]
            if "bearing" in spec: brgs = [spec["bearing"]]
            elif "heading" in spec: brgs = [(spec["heading"] + (180 if inbound else 0)) % 360]
            else: brgs = list(range(0, 360, 30))
            for b in brgs:
                lab = spec.get("label") or f"TEST radial {r['place']} {r['system']} b{b:.0f} {spec['r0']:.0f}->{spec['r1']:.0f}km"
                n = max(1, int(abs(spec["r1"] - spec["r0"]) / 8))
                pts = [_at(r, spec["r0"] + (spec["r1"] - spec["r0"]) * k / n, b) for k in range(n + 1)]
                out.append(([(p, lab if k == 0 else "", _attrs(spec, lab)) for k, p in enumerate(pts)], lab, r))
    elif t == "arc":
        if spec["range_km"] < R_km: raise ValueError(f"arc range {spec['range_km']} km is tighter than the {R_km:.0f} km turn radius")
        for r in _radars(world, spec):
            span = (spec["b1"] - spec["b0"] + 540) % 360 - 180
            n = max(1, int(abs(math.radians(span)) * spec["range_km"] / STEP_KM))
            lab = spec.get("label") or f"TEST arc {r['place']} {r['system']} r{spec['range_km']:.0f}km b{spec['b0']:.0f}->{spec['b1']:.0f}"
            pts = [_at(r, spec["range_km"], spec["b0"] + span * k / n) for k in range(n + 1)]
            out.append(([(p, lab if k == 0 else "", _attrs(spec, lab)) for k, p in enumerate(pts)], lab, r))
    elif t == "orbit":
        rad = spec.get("radius_km", R_km)
        if rad < R_km: raise ValueError(f"orbit radius {rad} km is tighter than the {R_km:.0f} km turn radius")
        lab = spec.get("label") or f"ORBIT {spec.get('laps', 1)}x r{rad:.0f}km"
        out.append((("orbit", spec, rad, lab), lab, None))
    else:
        raise ValueError(f"unknown probe type {t!r}")
    return out


def orbit_points(spec, rad_km, prev_xy, next_xy):
    """Tangent entry from prev, laps, tangent exit toward next."""
    c, R = np.array(spec["center"], float), rad_km * K
    ccw = spec.get("dir", "L") == "L"                    # L = counter-clockwise on the map (y down: left turns)
    def tangent(p, entering):
        d = np.asarray(p, float) - c; L = np.linalg.norm(d)
        if L <= R * 1.05: raise ValueError("orbit: the neighbouring route point is inside the circle")
        a, b = math.atan2(d[1], d[0]), math.acos(R / L)
        # left = ccw on screen = decreasing angle in the y-down plane
        return (a - b if ccw else a + b) if entering else (a + b if ccw else a - b)
    a_in, a_out = tangent(prev_xy, True), tangent(next_xy, False)
    sgn = -1 if ccw else 1
    span = sgn * ((sgn * (a_out - a_in)) % (2 * math.pi)) + sgn * 2 * math.pi * int(spec.get("laps", 1))
    n = max(2, int(abs(span) * R / (STEP_KM * K)))
    return [tuple(c + R * np.array([math.cos(a_in + span * k / n), math.sin(a_in + span * k / n)])) for k in range(n + 1)]


def check(world, radar_model, pts, radar, spec):
    """Terrain clear and (for radar runs) the radar can see enough of it. -> error string or None."""
    xy = [np.asarray(p[0], float) for p in pts]
    for a, b in zip(xy, xy[1:]):
        if not world.segment_clear(a, b): return "terrain"
    if radar is None and not spec.get("isolate"): return None
    samples = [(a + (b - a) * t, (b - a) / (np.linalg.norm(b - a) or 1)) for a, b in zip(xy, xy[1:]) for t in (0.25, 0.75)]
    at = pts[0][2] or {}
    kt, alt = at.get("speed_kt"), at.get("alt_ft")
    name = None if radar is None else f"{radar['place']} {radar['system']}"
    if radar is not None:
        seen = np.mean([any(n == name for n, *_ in radar_model.in_sight(s, u, kt, alt)) for s, u in samples])
        if seen < spec.get("min_los", 0.8): return f"radar sees only {seen:.0%} of it"
    if spec.get("isolate"):
        for s, u in samples:
            others = [n for n, *_ in radar_model.in_sight(s, u, kt, alt) if n != name]
            if others: return f"also seen by {others[0]}"
    return None
