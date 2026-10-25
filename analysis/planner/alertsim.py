"""Alert simulation of a timed route: every site, pulse by pulse, with the game's rules (radar_game.py).

Per site: pulses every pulse_period_ticks(alert) x 2 game s (32 s quiet, 6.4 s at 255); seen = margin > 0
(the hard threshold; the search used margin > -M for safety); alert_step on each pulse; alert > 0x7F raises
every site to (war + opp) x 16 - 16 (network alert) and makes quiet sites more active for ~2 min; launch
possible when alert > 0xC0, inside the SAM's range, not OTH, and > 20 km from a friendly airfield record;
radar ID at >= 0xFA under the same airfield gate.

Unknown at planning time: each site's pulse phase and the frame counter (duty windows). So run
SCENARIOS random draws (fixed seed): "worst" = sites always active, "expected" = duty windows as the
game schedules them (radar_game.duty_active). Not modelled: the global return lockout (skips a few
pulses), aircraft radars and visual IDs, the homing-missile gate.
"""
import numpy as np
import radar_game as rg

SCENARIOS = 48
WINDOW_S = 1024 / 4 * 2 / 2     # 1024 frames at ~4 frames per tick (2 game s): ~512 s
NETWORK_S = 120.0               # duty threshold raised (+3) this long after a network alert


def simulate(world, radar, steps, seed=1):
    """steps: fuelplan.integrate()["steps"]. -> dict(sites=[...], worst={...}, expected={...})."""
    if not steps or not radar.R:
        return dict(sites=[], n=0)
    t = np.array([s["t"] for s in steps])
    X = np.array([s["xy"][0] for s in steps]); Y = np.array([s["xy"][1] for s in steps])
    UX = np.array([s["u"][0] for s in steps]); UY = np.array([s["u"][1] for s in steps])
    kt = np.array([s["kt"] for s in steps], float); alt = np.array([s["alt"] for s in steps], float)
    roll = np.array([s["bank"] for s in steps]); bay = np.array([s["bay"] for s in steps])
    E = np.array([s.get("ecm", radar.ecm) for s in steps], bool)     # radar jammer per step (+16 EMV, half the gain)
    M = radar.margins(X, Y, UX, UY, kt, alt, roll, bay, E)          # sites x steps
    opp, war = world.opp, world.war
    ns = len(radar.R)
    # gates per step
    sx = np.array([p[0] for p, *_ in radar.R]); sy = np.array([p[1] for p, *_ in radar.R])
    gd = np.array([[rg.game_dist(int(X[k] - sx[n]), int(Y[k] - sy[n])) >> 6 for k in range(len(t))] for n in range(ns)])
    sam = np.array([rg.sam_range_km(kind) if kind != 21 else -1 for _, _, kind, _ in radar.R])
    in_sam = gd <= sam[:, None]
    if len(world.safe_fields):
        dfield = np.min(np.hypot(X[:, None] - world.safe_fields[None, :, 0], Y[:, None] - world.safe_fields[None, :, 1]), 1)
        far_field = dfield > rg.AIRFIELD_SAFE_UNITS
    else:
        far_field = np.ones(len(t), bool)
    idx = [int(r.get("record", n)) for n, r in enumerate(world.radars)]   # the site's index in the game table
    T_end = t[-1]
    rng = np.random.default_rng(seed)
    res = {mode: [] for mode in ("worst", "expected")}
    for mode in res:
        for _ in range(SCENARIOS):
            res[mode].append(_run(t, M, in_sam, far_field, opp, war, idx, T_end, rng, always=(mode == "worst"), E=E))
    sites = []
    for n, (pos, typ, kind, name) in enumerate(radar.R):
        row = dict(name=name, type=typ, kind=kind, min_margin_km=None)
        for mode, runs in res.items():
            mx = [r["max"][n] for r in runs]
            row[mode] = dict(max_alert=int(max(mx)), mean_max=float(np.mean(mx)),
                             p_net=float(np.mean([r["first"][n][0] is not None for r in runs])),
                             p_launch=float(np.mean([r["launch"][n] is not None for r in runs])),
                             p_id=float(np.mean([r["id"][n] is not None for r in runs])),
                             first_net=_first(r["first"][n][0] for r in runs),
                             first_c0=_first(r["first"][n][1] for r in runs),
                             first_fa=_first(r["first"][n][2] for r in runs),
                             launch_at=_first(r["launch"][n] for r in runs))
        row["seen_steps"] = int((M[n] > 0).sum())
        row["near_steps"] = int((M[n] > -radar.M).sum())
        row["best_margin"] = float(M[n].max())
        sites.append(row)
    summary = {mode: dict(p_any_net=float(np.mean([any(f[0] is not None for f in r["first"]) for r in runs])),
                          p_any_launch=float(np.mean([any(x is not None for x in r["launch"]) for r in runs])),
                          expected_ids=float(np.mean([sum(x is not None for x in r["id"]) for r in runs])),
                          max_alert=int(max(max(r["max"]) for r in runs)))
               for mode, runs in res.items()}
    return dict(sites=sites, summary=summary, n=SCENARIOS, t=t)


def _first(vals):
    v = [x for x in vals if x is not None]
    return min(v) if v else None


def _run(t, M, in_sam, far_field, opp, war, idx, T_end, rng, always, E=None):
    ns = len(idx)
    alert = np.zeros(ns, int)
    nxt = rng.uniform(0, 32, ns)                               # first pulse of each site
    k0 = rng.integers(0, 8); w0 = rng.uniform(0, WINDOW_S)      # frame counter: window index and phase
    net_until = -1.0
    first = [[None, None, None] for _ in range(ns)]
    launch = [None] * ns; ids = [None] * ns; mx = np.zeros(ns, int)
    while True:
        n = int(np.argmin(nxt)); tp = nxt[n]
        if tp > T_end: break
        k = min(len(t) - 1, int(np.searchsorted(t, tp)))
        win = k0 + int((tp + w0) // WINDOW_S)
        active = always or rg.duty_active(idx[n], win << 10, alert[n], False, war, tp < net_until)
        if active:
            seen = M[n, k] > 0
            alert[n] = rg.alert_step(alert[n], seen, opp, war, bool(E[k]) if E is not None else False)
            if seen and alert[n] > 0x7F:
                if first[n][0] is None: first[n][0] = tp
                floor = (war + opp) * 16 - 16
                alert = np.maximum(alert, floor); net_until = tp + NETWORK_S
            if alert[n] > 0xC0 and first[n][1] is None: first[n][1] = tp
            if alert[n] >= 0xFA and first[n][2] is None: first[n][2] = tp
            if seen and alert[n] > 0xC0 and far_field[k]:
                if alert[n] >= 0xFA and ids[n] is None: ids[n] = tp
                elif in_sam[n, k] and launch[n] is None: launch[n] = tp
            mx[n] = max(mx[n], alert[n])
        nxt[n] = tp + rg.pulse_period_ticks(alert[n]) * 2.0
    return dict(max=mx, first=first, launch=launch, id=ids)
