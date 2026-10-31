"""Score-farming loiters on enemy ground no radar site can see (notes/planner_radar_prd.md R6.3, R9).

Since 2026-10-21 any radar-quiet enemy ground qualifies, not just mountain cells: radar_quiet_mask() marks every
1 km spot where no site of THIS mission sees the plane at any heading (margin < -loiter_radar_margin at threat_ft,
loiter speed and bank), outside SA-14 cells by day. In CE at Veteran that was ~47 % of enemy ground (62 cells),
mostly flat, instead of the dozen mountain cells far from most routes. The racetrack keeps loiter_edge_km from
visible ground and loiter_clear_km from walls; the report's alert simulation checks it against every pulse.

Mountain cells (the original idea, below):

A site's return on the plane is multiplied by the terrain type of the PLANE's 32 km cell (radar_game.py):
mountain cells (terrain bits 0) give s = 0 for every ground site and fighter radar, so the plane is seen
there only if EMV alone exceeds 100. The whole cell is invisible, not just the ground behind the peaks,
so a low racetrack over the flat part of a mountain cell collects a 64 s cold-war sample per ~64 lb
(50 % + flaps) with nothing alerted. Rough cells (bits 1) are a fallback when the loiter's EMV stays under
the rough ceiling at this opponent level.

The racetrack keeps loiter_clear_km from walls (terrain >= mountain_ft at cruise altitude: mountains stay
walls) and loiter_edge_km inside the quiet cells, so no sample lands outside them and no part is over a
visible cell. nav flies it in LOITER mode (label "LOITER ...": 1.6 deg/s turn cap, 6 km lookahead).
"""
import math
import numpy as np

from f19.rules import radar as rg

from .world import K

PX_KM = 0.5                       # distance-transform resolution
PAD_KM = 12.0
CAP_KM = 10.0


rough_ceiling = rg.rough_ceiling          # rules.radar has the one copy


def loiter_emv(P, opp, kt=200):
    return rg.emv(int(P["cruise_ft"]), kt, opp, int(P["loiter_bank_deg"] / 360 * 65536))


QUIET_PX_KM = 1.0


def radar_quiet_mask(world, radar, P, kt=None):
    """Bool grid (1 km px, rows = y): enemy ground where NO site of this mission sees the plane at any heading -
    max margin over 8 headings and all sites < -loiter_radar_margin at threat_ft (where nav flies whenever a site
    could see it), loiter speed and loiter bank - and, by day, not an SA-14 cell. Mountain cells come out quiet by
    themselves (return 0); flat and rough ground is quiet away from the sites' reach for that aspect."""
    n = int(world.size / (QUIET_PX_KM * K))
    c = (np.arange(n) + 0.5) * QUIET_PX_KM * K
    X, Y = np.meshgrid(c, c)
    enemy = world.enemy(X, Y) & ~world.sa14(X, Y)
    worst = np.full(X.shape, -1e9)
    xs, ys = X[enemy], Y[enemy]
    kt = kt or P["cruise_kt"]
    for hd in range(0, 360, 45):
        u = (math.sin(math.radians(hd)), -math.cos(math.radians(hd)))
        m = radar.margins(xs, ys, u[0], u[1], kt, P["threat_ft"], P["loiter_bank_deg"])
        if len(m): worst[enemy] = np.maximum(worst[enemy], m.max(0))
    return enemy & (worst < -P["loiter_radar_margin"])


def quiet_cells(world, P):
    """16x16 bool: enemy cells the loiter can't be seen in (mountain; rough under the ceiling), no live SA-14 by day."""
    g = world.tgrid
    enemy = (g & 3) == 0
    terr = (g >> 2) & 3
    ok_rough = rough_ceiling(world.opp) + loiter_emv(P, world.opp) <= 100 - P["radar_margin"]
    q = enemy & ((terr == 0) | ((terr == 1) & ok_rough)) & ~world.sa14_cells
    return q, ok_rough


def _edt(obst, cap_px):
    """Euclidean distance (px) to the nearest True pixel, capped (brute force over a disk of offsets)."""
    D = np.where(obst, 0.0, float(cap_px))
    H, W = obst.shape
    P = np.pad(obst, cap_px, constant_values=False)
    for dy in range(-cap_px, cap_px + 1):
        for dx in range(-cap_px, cap_px + 1):
            d = math.hypot(dx, dy)
            if d >= cap_px or d == 0: continue
            s = P[cap_px + dy:cap_px + dy + H, cap_px + dx:cap_px + dx + W]
            np.minimum(D, np.where(s, d, cap_px), out=D)
    return D


def racetrack_path(c, theta, half_km, r_km, step_km=1.0):
    """Closed racetrack (game units): straight on the +n side from A to B, turn around B, back, turn around A.
    Returns points starting and ending at the same point (the first straight's start)."""
    u = np.array([math.sin(theta), -math.cos(theta)]); n = np.array([-u[1], u[0]])
    A, B = np.asarray(c, float) - u * half_km * K, np.asarray(c, float) + u * half_km * K
    R = r_km * K
    pts = []
    m = max(1, int(math.ceil(2 * half_km / step_km)))
    for k in range(m): pts.append(A + n * R + (B - A) * k / m)
    a0 = math.atan2(n[1], n[0])
    na = max(4, int(math.ceil(math.pi * r_km / step_km)))
    sgn = -1                                     # turn from +n around to -n through +u
    if np.dot(np.array([math.cos(a0 + sgn * math.pi / 2), math.sin(a0 + sgn * math.pi / 2)]), u) < 0: sgn = 1
    for k in range(na): pts.append(B + R * np.array([math.cos(a0 + sgn * math.pi * k / na), math.sin(a0 + sgn * math.pi * k / na)]))
    for k in range(m): pts.append(B - n * R + (A - B) * k / m)
    for k in range(na): pts.append(A + R * np.array([math.cos(a0 + math.pi * sgn + sgn * math.pi * k / na),
                                                     math.sin(a0 + math.pi * sgn + sgn * math.pi * k / na)]))
    pts.append(pts[0].copy())
    out = [pts[0]]
    for q in pts[1:]:                            # a circle (half_km 0) repeats the straights' end points
        if np.linalg.norm(q - out[-1]) > 1.0: out.append(q)
    if np.linalg.norm(out[-1] - out[0]) > 1.0: out.append(out[0].copy())
    else: out[-1] = out[0].copy()
    return out


def fit(world, P, cell, quiet):
    """quiet: radar_quiet_mask() (1 km px) or a 16x16 cell grid (mountain_loiter)."""
    """Best racetrack per quadrant of `cell` (cx, cy) (spine centre in that quadrant), best first. -> [dict]"""
    cx, cy = cell
    px = PX_KM * K
    x0, y0 = cx * 2048 - PAD_KM * K, cy * 2048 - PAD_KM * K
    n = int((32 + 2 * PAD_KM) / PX_KM)
    xs = x0 + (np.arange(n) + 0.5) * px; ys = y0 + (np.arange(n) + 0.5) * px
    X, Y = np.meshgrid(xs, ys)
    j, i = world._fine(X, Y)
    wall = world.wall[j, i] | (X < 0) | (Y < 0) | (X >= world.size) | (Y >= world.size)
    gy, gx = world.cell32(X, Y)
    out = (X < 0) | (Y < 0) | (X >= world.size) | (Y >= world.size)
    if quiet.shape == (16, 16): notq = ~quiet[gy, gx] | out
    else:
        qn = quiet.shape[0]; step = world.size / qn
        notq = ~quiet[np.clip((Y // step).astype(int), 0, qn - 1), np.clip((X // step).astype(int), 0, qn - 1)] | out
    cap = int(CAP_KM / PX_KM)
    Dw, Dq = _edt(wall, cap) * PX_KM, _edt(notq, cap) * PX_KM
    r, cw, ce = P["loiter_radius_km"], P["loiter_clear_km"], P["loiter_edge_km"]
    look = lambda pts: (Dw[np.clip(((np.array(pts)[:, 1] - y0) / px).astype(int), 0, n - 1),
                           np.clip(((np.array(pts)[:, 0] - x0) / px).astype(int), 0, n - 1)],
                        Dq[np.clip(((np.array(pts)[:, 1] - y0) / px).astype(int), 0, n - 1),
                           np.clip(((np.array(pts)[:, 0] - x0) / px).astype(int), 0, n - 1)])
    inside = (gx == cx) & (gy == cy) & (Dw >= r + cw) & (Dq >= r + ce)
    quad = ((X - cx * 2048) >= 1024).astype(int) + 2 * ((Y - cy * 2048) >= 1024).astype(int)
    found = {}
    for jj, ii in zip(*np.nonzero(inside[::4, ::4])):           # 2 km grid of spine centres
        c = (X[jj * 4, ii * 4], Y[jj * 4, ii * 4])
        qd = int(quad[jj * 4, ii * 4]); best = found.get(qd)
        for th in range(0, 180, 15):
            for h in range(0, 16, 2):
                pts = racetrack_path(c, math.radians(th), h, r, 0.5)
                w, q = look(pts)
                surplus = min(float(w.min()) - cw, float(q.min()) - ce)
                if surplus < 0: break
                key = (min(surplus, 1.0), h)
                if best is None or key > best["key"]:
                    # the router's own wall test (square dilation at the terrain step) on the flown polygon
                    poly = racetrack_path(c, math.radians(th), h, r, 1.5)
                    if not all(world.segment_clear_km(p0, p1, cw) for p0, p1 in zip(poly, poly[1:])): continue
                    best = found[qd] = dict(key=key, centre=c, theta=th, half_km=h, surplus_km=surplus,
                                            lap_km=2 * h * 2 + 2 * math.pi * r, cell=cell,
                                            quadrant=("NW", "NE", "SW", "SE")[qd])
    return sorted(found.values(), key=lambda f: f["key"], reverse=True)


def candidates(world, P, radar=None, refs=None, n_cells=3, mountain_only=False):
    """Racetracks in the n_cells candidate cells nearest the refs (route points the loiter detours from), best fit
    per cell first. Candidate cells: mostly radar-quiet enemy cells (radar_quiet_mask; mountain_only: the old
    mountain / rough-cell rule). -> ([fit dicts], notes)"""
    side = world.tgrid & 3
    if mountain_only or radar is None:
        q, ok_rough = quiet_cells(world, P); cellq = q.astype(float)
    else:
        q = radar_quiet_mask(world, radar, P); qn = q.shape[0] // 16
        cellq = q.reshape(16, qn, 16, qn).mean((1, 3)); ok_rough = True
    cells = [(cx, cy) for cy in range(16) for cx in range(16) if cellq[cy, cx] >= 0.5]
    if refs:
        ctr = lambda c: np.array([(c[0] + 0.5) * 2048, (c[1] + 0.5) * 2048])
        cells.sort(key=lambda c: sum(np.linalg.norm(ctr(c) - np.asarray(r, float)) for r in refs))
    out = []
    for cx, cy in cells:
        fr = [(abs(i - cx) + abs(j - cy)) for j in range(16) for i in range(16) if side[j, i] == 3]
        fits = fit(world, P, (cx, cy), q)
        for f in fits[:1]:                                    # the best racetrack of the cell
            f["friendly_cells_away"] = min(fr) if fr else None
            f["terrain"] = int((world.tgrid[cy, cx] >> 2) & 3)
            f["peak_ft"] = float(world.H[cy * 128:(cy + 1) * 128, cx * 128:(cx + 1) * 128].max()) if world.step == 16 else None
            out.append(f)
        if len(out) >= n_cells: break
    return out, ok_rough


def _hdg(v):
    return math.degrees(math.atan2(v[0], -v[1])) % 360


def piece(world, fit_, laps, entry_hdg, exit_hdg, P):
    """Fixed-piece points: a straight run-in, `laps` laps of the racetrack, on to the exit, a straight run-out.
    Entry / exit are the racetrack points whose tangent best matches the heading the route arrives on /
    leaves on (free searches, plan.py) AND that have a wall-clear straight of >= 6 km outside the loop,
    so the route's 20 km turns join out where there's room. Both directions of travel are tried."""
    base = racetrack_path(fit_["centre"], math.radians(fit_["theta"]), fit_["half_km"], P["loiter_radius_km"], 1.5)[:-1]
    dif = lambda h, h0: abs((h - h0 + 180) % 360 - 180)
    best = None
    for lap in (base, base[::-1]):
        n = len(lap)
        tang = [_unit(lap[(k + 1) % n] - lap[k]) for k in range(n)]
        def score(k, h, out):
            u = tang[k] if out else tang[k]
            L = _straight(world, lap[k], u if out else -u, P)
            return dif(_hdg(u), h) + (1000 if L < 6 else 0) - 2 * L, L
        ie = min(range(n), key=lambda k: score(k, entry_hdg, False)[0])
        ix = min(range(n), key=lambda k: score(k, exit_hdg, True)[0])
        tot = score(ie, entry_hdg, False)[0] + score(ix, exit_hdg, True)[0]
        if best is None or tot < best[0]: best = (tot, lap, ie, ix, tang)
    _, lap, ie, ix, tang = best
    n = len(lap)
    seq = [lap[(ie + k) % n] for k in range(n * int(laps))]
    seq += [lap[(ie + k) % n] for k in range((ix - ie) % n + 1)]
    cx, cy = fit_["cell"]
    lab = f"LOITER ({cx},{cy}) {int(laps)} laps"
    a = {"kind": "loiter", "loiter": lab}
    out = [(tuple(p), lab if i == 0 else ("Loiter exit" if i == len(seq) - 1 else ""), a) for i, p in enumerate(seq)]
    u_in, u_out = tang[ie], tang[ix]
    pre = _straight(world, seq[0], -u_in, P); post = _straight(world, seq[-1], u_out, P)
    return ([(tuple(seq[0] - u_in * d * K), "Loiter run-in" if d == pre else "", {"kind": "loiter_runin"}) for d in _steps(pre)]
            + out + [(tuple(seq[-1] + u_out * d * K), "", {"kind": "loiter_runout"}) for d in _steps(post)[::-1]])


RUN_KM = (15.0, 10.0, 6.0, 3.0)
KINDS = ("loiter", "loiter_runin", "loiter_runout")      # legs between two of these keep loiter_clear_km


def _unit(v):
    v = np.asarray(v, float); n = np.linalg.norm(v); return v / n if n else v


def _straight(world, p, u, P):
    """Longest RUN_KM straight from p along u that keeps loiter_clear_km from walls (0 = none)."""
    for L in RUN_KM:
        q = np.asarray(p, float) + u * L * K
        if 64 <= q[0] < world.size - 64 and 64 <= q[1] < world.size - 64 and world.segment_clear_km(p, q, P["loiter_clear_km"]): return L
    return 0.0


def _steps(L):
    """Distances (km, far first) of the run points: every 3 km plus the far end."""
    if not L: return []
    return sorted({L, *np.arange(3.0, L, 3.0)}, reverse=True)
