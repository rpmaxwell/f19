"""Enemy fighters along a planned route: when they hunt us, whether their radar sees us, whether they shoot.

The rules are docs/game/aircraft.md (2026-10-21 / 10-28); the numbers that are counted, not decoded, are in HUNT below and every
one is a knob. Not modelled: scrambles of the alert-gated slots (airborne fighters only), patrol sightings with no network alert
(Veteran flew 1,878 min with none; Elite is unmeasured), the fighters' steering law (pure pursuit at max speed), decoys and
countermeasures (the autopilot's reactions would lower the hit rate; the planner takes the rate with none).

Per alertsim scenario (alertsim.simulate()["net"]: when a network alert was raised, by a site or an SA-14 launch):
  * the fighters in the air are drawn: Poisson counts in 0-100 km and 100-200 km of us (measured airborne counts), a type from the
    theater's roster (a type with two slots is drawn twice as often), a bearing, a distance;
  * a network alert makes every fighter hunt: it flies straight at us at its type's speed. The hunt lasts gate_s after the
    alert (while ds:5522 is nonzero) and is extended while the fighter itself holds an alert above 0xC0 (that is a network alert);
  * every SIGHT_S its own radar (rg.site_return with the type's radar, our altitude, speed, bank and the clutter of our cell, + EMV)
    sees us or not: alert +(war + opp) x 16 + 32 when seen, -32 when not. Far away the aspect is the closing line of sight against
    our heading (pulse radars are blind head-on, doppler radars on the beam); inside CLOSE_KM it circles us and the aspect is random;
  * above 0xC0 and inside 8 km by day / 4 km at night: a visual ID (-20 base points), and an armed type fires, at most MAX_SHOTS.
Each shot is a hit with P(hit | launch). Output per scenario: shots [(game s, type)] and visual IDs [game s].
"""
import math

import numpy as np

from f19.rules import radar as rg

from .radar import hdg65536
from .world import GROUND_KMH_PER_KT as KT_KMH, K

# aircraft type numbers: 0 MiG-23, 1 MiG-25, 2 MiG-29, 3 MiG-31, 4 Su-27, 5 IL-76, 6 F-4E, 7 F-14, 14 Yak-38, 17 F-5 (aircraft.md)
ROSTER = {    # enemy slots by theater (START builds them: the same for every seed and difficulty)
    "CE": (0, 4, 2, 5, 3, 1, 4, 1),
    "NC": (14, 4, 2, 5, 3, 14, 4, 1),
    "PG": (17, 6, 7, 17, 6, 6, 7),
    "LB": (1, 0, 2, 1, 0, 2),
}
HUNT = dict(
    near_mean=1.9, mid_mean=1.4,       # airborne fighters within 0-100 / 100-200 km (CE, game-min means, Elite; Veteran 1.8 / 1.2)
    sight_s=32.0,                      # one sighting check per aircraft per ~32 game s
    gate_s=500.0,                      # a network alert starts the hunt; it lasts while ds:5522 is nonzero. 5522 sits at 1744 just after a
                                       # SAM launch and 1400 is '100 game s since the alert', so it decays ~3.4/s: ~500 s to zero (UNMEASURED)
    close_km=12.0,                     # inside this the fighter circles us: random aspect, at orbit_km
    orbit_km=2.0,
    visual_km_day=8.0, visual_km_night=4.0,
    alert_net=0xC0,
    max_shots=2, refire_s=64.0,        # UNMEASURED: how often one fighter fires once it has the shot
    p_hit_launch=0.43,                 # Elite fighter missiles (10 of 23, no countermeasure used); Veteran has no fighter launch at all
    horizon_km=200.0,
)


def scenario_arrays(world, radar, steps):
    """Per-step arrays the sightings read: t, x, y, ux, uy, kt, alt, emv, hdg (65536), clutter byte."""
    t = np.array([s["t"] for s in steps], float)
    X = np.array([s["xy"][0] for s in steps], float); Y = np.array([s["xy"][1] for s in steps], float)
    U = np.array([s["u"] for s in steps], float)
    kt = np.array([s["kt"] for s in steps], float); alt = np.array([s["alt"] for s in steps], float)
    bank = np.array([s["bank"] for s in steps], float); bay = np.array([s["bay"] for s in steps], bool)
    E = np.array([s.get("ecm", radar.ecm) for s in steps], bool)
    return dict(t=t, x=X, y=Y, ux=U[:, 0], uy=U[:, 1], kt=kt, alt=alt, emv=radar.emv(alt, kt, bank, bay, E),
                hdg=hdg65536(U[:, 0], U[:, 1]), clutter=np.asarray(world.clutter(X, Y)))


def _types(theater):
    return ROSTER.get(theater, ROSTER["CE"])


def _specs(types):
    return {t: rg.aircraft_spec(t) for t in set(types)}


def simulate(world, radar, steps, net_events, theater, seed=7, hunt=None):
    """-> list (one per scenario) of dict(shots=[(t, type)], visual=[t]). net_events: per scenario the sorted game s of every
    network alert (alertsim.simulate()["net"]["expected"]); an empty list is a scenario with no hunt."""
    H = {**HUNT, **(hunt or {})}
    A = scenario_arrays(world, radar, steps)
    roster = _types(theater); specs = _specs(roster)
    rng = np.random.default_rng(seed)
    opp, war = world.opp, world.war
    gain = (war + opp) * 16 + 32
    visual_km = H["visual_km_night"] if world.night else H["visual_km_day"]
    T_end = float(A["t"][-1])
    out = []
    for nets in net_events:
        shots, vis = [], []
        if nets:
            bands = [(10.0, 100.0)] * rng.poisson(H["near_mean"]) + [(100.0, H["horizon_km"])] * rng.poisson(H["mid_mean"])
            for lo, hi in bands:
                typ = int(roster[rng.integers(len(roster))])
                _hunter(A, specs[typ], typ, rng.uniform(lo, hi), rng.uniform(0, 2 * math.pi), nets, rng, opp, gain, visual_km, H, T_end, shots, vis)
        out.append(dict(shots=sorted(shots), visual=sorted(vis)))
    return out


def _hunter(A, sp, typ, d0, bearing, nets, rng, opp, gain, visual_km, H, T_end, shots, vis):
    """One fighter, from the first network alert on. Appends to shots / vis."""
    if not sp.get("radar_type"): return                              # no radar: never sees us (F-18, An-72)
    v_f = sp["max_speed_ms"] / 1000.0                                # map km per game s (the F-19's 190 kt is 77 m/s on the same scale)
    t = nets[0]; alert = 0; d = d0; e = np.array([math.cos(bearing), math.sin(bearing)])
    gate_end = t + H["gate_s"]; ni = 1; fired = 0; last_shot = -1e9; seen_vis = False
    t += rng.uniform(0, H["sight_s"])
    while t < T_end:
        while ni < len(nets) and nets[ni] <= t:
            gate_end = max(gate_end, nets[ni] + H["gate_s"]); ni += 1
        if t > gate_end: return                                      # lost the alert: back to patrol, out of the model
        k = min(len(A["t"]) - 1, int(np.searchsorted(A["t"], t)))
        px, py = A["x"][k], A["y"][k]
        if d < H["close_km"]:
            th = rng.uniform(0, 2 * math.pi); r = max(d, 1.0)
            fx, fy = px + r * K * math.cos(th), py + r * K * math.sin(th)
        else:
            fx, fy = px + d * K * e[0], py + d * K * e[1]
        ret = rg.site_return(int(px), int(py), int(A["alt"][k]), int(A["hdg"][k]), int(A["kt"][k]), opp, int(A["clutter"][k]),
                             int(fx), int(fy), sp["radar_type"], 5000)
        seen = ret + A["emv"][k] - 100 > 0
        alert = min(255, alert + gain) if seen else max(0, alert - 32)
        if alert > H["alert_net"]:
            gate_end = max(gate_end, t + H["gate_s"])                # its own sighting is a network alert
            if d <= visual_km:
                if not seen_vis: vis.append(t); seen_vis = True
                if fired < H["max_shots"] and t - last_shot >= H["refire_s"] and sp.get("missile"):
                    shots.append((t, typ)); fired += 1; last_shot = t
        vp = A["kt"][k] * KT_KMH / 3600.0                             # map km per game s
        closing = v_f + (vp * (A["ux"][k] * e[0] + A["uy"][k] * e[1]) if d >= H["close_km"] else 0.0)
        d = max(d - max(closing, 0.2 * v_f) * H["sight_s"], H["orbit_km"])      # inside close_km it holds an orbit around us
        t += H["sight_s"]


def summary(res):
    """Expected shots and visual IDs per scenario, and the chance of at least one shot."""
    n = max(len(res), 1)
    return dict(shots=sum(len(r["shots"]) for r in res) / n, visual_ids=sum(len(r["visual"]) for r in res) / n,
                p_shot=sum(bool(r["shots"]) for r in res) / n, scenarios=len(res))
