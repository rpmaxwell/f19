"""The plan's outputs, in memory: the summary numbers, report.txt text, the route.png picture and the route.json
(schema 2) dict. Nothing here writes a file (cli.py does).

  summ, sim, samples = summarise(pl, best)
  text = report(pl, best, results, summ, sim)        (prefix: the fuel-limited notes, planner.api)
  img = draw(pl, best, samples)                       PIL image
  route = route_json(pl, best, spec, summ)            the dict nav reads
"""
import time

import numpy as np
from PIL import Image, ImageDraw

from f19.rules import radar as rg
from f19.rules import scoring as score

from . import alertsim, fuelplan, profiles
from .radar import MODEL
from .router import hdg, turn_deg, unit
from .world import GROUND_KMH_PER_KT as KT_KMH, K


NAV_NEEDS = ("nav holds one speed (profile cruise_kt) with the throttle keys and touches flaps only on takeoff: to fly "
             "this plan's per-leg settings it needs to read each point's throttle / flaps (route.json points) and set them "
             "(throttle taps of 10 %, flaps key) at leg changes. Until then fly the throttle column by hand or accept cruise_kt.")
FLIGHT_TESTS = ["40 % throttle with flaps when light (< 4000 lb): the model says it can't hold level, but it was pessimistic "
                "near that edge before (50 % clean heavy flew at 171 kt); 40 % = 45 lb/min, +33 % loiter time.",
                "lb per sample at 50 % + flaps from the new flight log (model: 64 lb per 64 s sample)."]
LOITER_TEST = ["first flight: 10-15 game min in the loiter, then home (the logger records everything).",
               "pass: every site's alert stays 0 inside the loiter cell, V and R counts stay 0, no SAM / SA-14 events, "
               "the 64 s samples (event type 9) land in the cell, the debrief points match the formula.",
               "watch: enemy fighters on patrol (they can't ID the plane by the code, but their attack logic isn't "
               "decoded); the flat-area edge vs nav's cross-track error; drifting out of the cell."]
OPP_NAME = {0: "green", 1: "regular", 2: "veteran", 3: "elite"}


def summarise(pl, best):
    W, RM, P = pl.W, pl.RM, pl.P
    fu = best["fuel"]; steps = fu["steps"]
    km = sum(s["km"] for s in steps)
    smin, smax, samples = fuelplan.samples(W, steps, P)
    if steps:
        A = lambda k: np.array([s[k] for s in steps])
        X, Y = A("xy")[:, 0], A("xy")[:, 1]; U = A("u")
        det = RM.detection(X, Y, U[:, 0], U[:, 1], A("kt").astype(float), A("alt"), A("bank"), A("bay"))
        expo = float((det * A("km")).sum())
        sa14_min = float(sum(s["km"] / (s["kt"] * KT_KMH) * 60 for s in steps if W.sa14(*s["xy"])))
    else:
        expo = sa14_min = 0.0
    sim = alertsim.simulate(W, RM, steps)
    exp_ids = sim["summary"]["expected"]["expected_ids"] if sim.get("sites") else 0.0
    id_pen = int(round(10 * exp_ids))
    lb_ps = pl.regimes.lb_per_sample(P["fuel_ref_lb"], P["cruise_ft"])
    spare = fu["fuel_land"] - P["landing_reserve_lb"]
    roles = set(best["roles"]) if best.get("roles") else {t["role"].rstrip("?") for t in pl.M["targets"]}
    st = {**pl.spec.get("settings", {})}
    est = lambda lo, hi, pen: score.estimate(range(lo, hi + 1), pl.th, st, "primary" in roles, "secondary" in roles, pen)
    base_rng, score_rng, assumed = est(smin, smax, id_pen)
    _, score_nopen, _ = est(smin, smax, 0)
    sk = [dict(role=t["role"].rstrip("?"), object=t["object"], place=t["place"], why=w) for t, w in pl.skipped]
    if sk:                                          # what the skipped objectives would have scored (route as planned)
        all_roles = roles | {k["role"] for k in sk}
        _, score_with, _ = score.estimate(range(smin, smax + 1), pl.th, st, "primary" in all_roles, "secondary" in all_roles, id_pen)
    extra = 0.0 if best.get("loiter") else max(0.0, spare) / lb_ps      # samples still available for a loiter
    _, score_loiter, _ = est(smin + int(extra), smax + int(extra), id_pen)
    res_samples = P["landing_reserve_lb"] / lb_ps
    _, score_nores, _ = est(smin + int(res_samples), smax + int(res_samples), id_pen)
    xy = [np.asarray(p[0], float) for p in best["pts"]]
    kinks = [turn_deg(xy[i - 1], xy[i], xy[i + 1]) > 0.5 for i in range(1, len(xy) - 1)]
    corners = sum(1 for i, k in enumerate(kinks) if k and (i == 0 or not kinks[i - 1]))
    return dict(km=round(km, 1), game_min=round(fu["t_end"] / 60, 1), turns=corners, exposure_pulses=round(expo, 2),
                enemy_samples=[smin, smax], samples=len(samples), fuel_loaded_lb=round(fu["fuel0"]),
                fuel_used_lb=round(fu["fuel0"] - fu["fuel_land"]), fuel_landing_lb=round(fu["fuel_land"]),
                lb_per_sample=round(lb_ps, 1), loiter_budget_lb=round(spare), loiter_budget_samples=round(extra, 1),
                sa14_minutes=round(sa14_min, 1), expected_radar_ids=round(exp_ids, 2), id_penalty_base=id_pen,
                base_points=list(base_rng), score_est=list(score_rng), score_without_id_penalty=list(score_nopen),
                score_if_loitered=list(score_loiter), score_without_reserve=list(score_nores),
                radar_overrides_km=fu["overrides"], score_assumptions=assumed,
                alert=sim.get("summary"), cost=round(best["cost"], 1),
                sa14_launch_chance=round((sim.get("sa14") or {}).get("p_launch", 0.0), 3),
                skipped=sk, **({"score_with_skipped": list(score_with)} if sk else {})), sim, samples


def _t(s):
    return "-" if s is None else f"{int(s // 60)}:{int(s % 60):02d}"


def sa14_block(pl, sim):
    """SA-14 cells (alertsim._sa14): the skipped targets, and the predicted launches when the route is in a live cell."""
    L = []
    for t, why in pl.skipped:
        L.append(f"!! SKIPPED {t['role'].rstrip('?')} {t['object']} {t['place']}: {why}. The plan flies without it "
                 f"(settings \"sa14\": \"soft\" plans it anyway).")
    a = sim.get("sa14") or {}
    if a.get("minutes", 0) > 0:
        first = f", first at {_t(a['first'])} game min" if a.get("first") is not None else ""
        L.append(f"!! PREDICTED SA-14 launch: {a['minutes']:.1f} min in live SA-14 cells = ~{a['checks']:.1f} spawn checks "
                 f"(every 32 s, {a['p_per_check']:.0%} of them launch); chance of at least one launch {a['p_launch']:.0%}{first}. "
                 f"A launch raises the network alert.")
    return L


def radar_block(pl, sim):
    L = []
    if not sim.get("sites"): return ["RADAR: no radar sites in the mission file"]
    S = sim["summary"]
    for s in sim["sites"]:
        if s["worst"]["first_c0"] is not None or s["expected"]["p_launch"] > 0:
            L.append(f"!! PREDICTED alert > 0xC0: {s['name']} at {_t(s['worst']['first_c0'])} game min (sites always on); "
                     f"expected-duty chance of a launch {s['expected']['p_launch']:.0%}, of a radar ID {s['expected']['p_id']:.0%}")
    L.append(f"RADAR (alertsim, {sim['n']} draws of pulse phase / frame counter; seen = return + EMV > 100):")
    for mode, txt in (("worst", "worst case (sites always active)"), ("expected", f"expected (duty cycle {pl.RM.duty:.0%})")):
        m = S[mode]
        L.append(f"  {txt:34s}: max alert {m['max_alert']}, network alert {m['p_any_net']:.0%}, "
                 f"launch possible {m['p_any_launch']:.0%}, radar IDs {m['expected_ids']:.2f}")
    L.append("  site                    kind  best margin  steps seen/near  max alert worst/exp  >7F    >C0    >=FA   P(launch) P(ID)")
    for s in sorted(sim["sites"], key=lambda s: -s["best_margin"]):
        if s["near_steps"] == 0 and s["worst"]["max_alert"] == 0: continue
        w, e = s["worst"], s["expected"]
        L.append(f"  {s['name'][:22]:22s}  {s['type']:4s}  {s['best_margin']:+11.0f}  {s['seen_steps']:5d} / {s['near_steps']:5d}"
                 f"  {w['max_alert']:9d} / {e['max_alert']:3d}  {_t(w['first_net']):6s} {_t(w['first_c0']):6s} {_t(w['first_fa']):6s}"
                 f" {e['p_launch']:8.0%} {e['p_id']:6.0%}")
    quiet = sum(1 for s in sim["sites"] if s["near_steps"] == 0 and s["worst"]["max_alert"] == 0)
    L.append(f"  ({quiet} other sites never within the {pl.RM.M:.0f} margin)")
    return L


def _pe(e):
    """One line of a route's points.value (objective points / aircraft)."""
    f = (f", fighter shots {e['shots']:.2f}, visual IDs {e['visual_ids']:.2f}" if "shots" in e else "")
    return (f"E[points] {e['exp_pts']:.0f} of {e['full']:.0f} ({'+'.join(e['roles'])}), P(bay hit) {e['p_bay']:.0%}, "
            f"expected hits {e['hits']:.2f}, {e['minutes']:.0f} min{f}")


def report(pl, best, results, summ, sim):
    W, RM, P, V = pl.W, pl.RM, pl.P, best["V"]
    r = pl.router
    L = [f"F-19 plan  {pl.dir}  ({time.strftime('%Y-%m-%d %H:%M')})",
         f"theater {pl.th}; mode {pl.mode}; profile {P['name']}" + (f" + {pl.spec.get('overrides')}" if pl.spec.get("overrides") else ""),
         f"settings: opponent {OPP_NAME[W.opp]}, war {W.war}, {'night' if W.night else 'day'}, ROE {W.roe}; radar model {MODEL}",
         f"objective (transit km): enemy km x{r.enemy_km:.2f} (coldwar credit {r.coldwar:.2f}), one sample = {r.sample_km:.1f} km, "
         f"one seen pulse = {P['pulse_cost_samples']:g} samples, SA-14 minute = {P['sa14_cost_samples_per_min']:g} samples, "
         f"corner {P['turn_simplicity_km']:g} km + banked-arc exposure; min turn radius {P['min_turn_radius_km']:.0f} km, "
         f"altitude {P['cruise_ft']:.0f} ft where no site would see the plane, {P['threat_ft']:.0f} ft where one would "
         f"(from {P['alt_lead_km']:g} km before)", ""]
    L += sa14_block(pl, sim) + radar_block(pl, sim) + ["", f"CHOSEN: {best['tag']}"]
    for o in results:
        if o is not best: L.append(f"  also: {o['tag']}: cost {o['cost']:.0f}" + (f", {len(o['errors'])} errors" if o["errors"] else "")
                                   + (f", {_pe(o['points_eval'])}" if "points_eval" in o else ""))
    if "points_eval" in best:
        L.append(f"  objective {pl.objective}: {_pe(best['points_eval'])}")
    for tag, why in getattr(pl, "skipped_options", ()): L.append(f"  also: {tag}: skipped, {why}")
    s = summ
    L += ["", f"route {s['km']:.0f} km, {s['game_min']:.0f} game min, {s['turns']} turns, expected seen pulses {s['exposure_pulses']:.1f}, "
              f"SA-14 cell time {s['sa14_minutes']:.1f} min{'' if W.night else ' (day)'}",
          f"fuel: {s['fuel_loaded_lb']} lb carried ({P['fuel_loaded_lb']:.0f} internal + {P['extra_fuel_bays']} Extra Fuel x 1900), "
          f"{s['fuel_used_lb']} used, {s['fuel_landing_lb']} at landing (reserve {P['landing_reserve_lb']:.0f})",
          f"cold-war samples in enemy cells: {s['enemy_samples'][0]}-{s['enemy_samples'][1]} (64 s phase unknown); "
          f"{s['lb_per_sample']:.0f} lb per sample at the enemy setting"]
    fu = best["fuel"]
    for x in fu.get("transfers", []):
        L.append(f"Extra Fuel transfer planned at {x['t'] / 60:.0f} game min ({x['km']:.0f} km): fuel below 500 lb, nav switches "
                 f"to the tank's station and presses Enter")
    if fu.get("tanks_left"): L.append(f"Extra Fuel: {fu['tanks_left']} tank(s) not needed on this route (still counted at landing)")
    if best.get("loiter"):
        lo = best["loiter"]
        L.append(f"loiter: cell {tuple(lo['fit']['cell'])}, {lo['laps']} laps of {lo['fit']['lap_km']:.0f} km "
                 f"(~{lo['lap_lb']:.0f} lb per lap), racetrack r {P['loiter_radius_km']:g} km, straights 2 x {2 * lo['fit']['half_km']} km, "
                 f"{lo['fit']['surplus_km']:.1f} km to spare beyond the {P['loiter_clear_km']:g} km wall / {P['loiter_edge_km']:g} km edge margins")
    else:
        L.append(f"loiter budget (not spent): {s['loiter_budget_lb']} lb above the reserve = ~{s['loiter_budget_samples']:.0f} samples "
                 f"in a quiet enemy cell -> score {s['score_if_loitered'][0]}-{s['score_if_loitered'][1]}")
    L += [f"rating estimate (targets as planned, safe landing, expected radar IDs {s['expected_radar_ids']:.2f} = -{s['id_penalty_base']} base): "
          f"base {s['base_points'][0]}-{s['base_points'][1]}, score {s['score_est'][0]}-{s['score_est'][1]} "
          f"(without the ID penalty {s['score_without_id_penalty'][0]}-{s['score_without_id_penalty'][1]})",
          *([f"  without the skipped {', '.join(k['role'] for k in s['skipped'])}: score {s['score_with_skipped'][0]}-{s['score_with_skipped'][1]} "
             f"if it could be flown (the skip costs ~{s['score_with_skipped'][1] - s['score_est'][1]} points at most)"] if s["skipped"] else []),
          f"  the {P['landing_reserve_lb']:.0f} lb landing reserve costs ~{P['landing_reserve_lb'] / s['lb_per_sample']:.0f} samples: "
          f"score {s['score_without_reserve'][0]}-{s['score_without_reserve'][1]} with none (a crash isn't penalised; "
          f"your call, not the planner's)"] + [f"  assumes {a}" for a in s["score_assumptions"]]
    L += ["", "speed and fuel by leg (fuel_model.py; '*' = slowed for radar):",
          "      km          game min    alt ft  thr flaps  kt        fuel lb       enemy"]
    for g in fuelplan.legs(best["fuel"]["steps"]):
        thr, fl, alt = g["key"]
        L.append(f"  {g['km0']:5.0f}-{g['km1']:5.0f}  {g['t0'] / 60:5.1f}-{g['t1'] / 60:5.1f}  {alt:6d}  {thr:3d}% {'down' if fl else 'up':5s} "
                 f"{min(g['kts']):3d}-{max(g['kts']):3d}  {g['fuel0']:6.0f}-{g['fuel1']:6.0f}  {g['enemy'] / max(1, g['n']):4.0%}"
                 + ("  *" if g["override"] else ""))
    L += ["", "fuel at named waypoints:"]
    for st_ in best["fuel"]["steps"]:
        if st_["label"]:
            L.append(f"  {st_['label'][:40]:40s} {st_['t'] / 60:6.1f} game min  {st_['fuel']:6.0f} lb  "
                     f"{st_['alt']:.0f} ft {st_['thr']}%{' flaps' if st_['flaps'] else ''} {st_['kt']} kt")
    L.append(f"  {'Landing':40s} {best['fuel']['t_end'] / 60:6.1f} game min  {best['fuel']['fuel_land']:6.0f} lb")
    tg = {t["role"].rstrip("?"): t for t in pl.M["targets"]}
    if tg:
        L += ["", "attacks (notes/targeting_for_planner.md; NOT flight-tested):"]
        for role, t in tg.items():
            mode, why = pl.attack_mode(t)
            L.append(f"  {role}: {t['object']} {t['place']} -> {mode} ({why})")
        L += [f"  {l}: {a['action']}" for p, l, a in best["pts"] if l and a.get("action")]
        for p, l, a in best["pts"]:
            if a.get("acq"):
                q = a["acq"]
                L.append(f"  {l}: trackcam lock window {q['far_km']:g}-{q['near_km']:g} km before the target ({q['len_km']:g} km), "
                         f"\"/\" then <= {q['presses']} B ({'night' if pl.W.night else 'day'}; the pick rule does not depend on the difficulty)")
    L += ["", "CHECKS: " + ("all passed" if not best["errors"] else "FAILED")] + [f"  !! {e}" for e in best["errors"]]
    notes = sorted(pl.router.notes)
    if best["log"] or pl.log or notes: L += ["", "planner log:"] + [f"  {m}" for m in notes + pl.log + best["log"]]
    L += ["", " #  from -> to                                hdg    km   turn  territory  sites within the margin (mid-leg, margin)"]
    named = [i for i, v in enumerate(V) if v["label"] or v["fillet"]]
    for a, b in zip(named, named[1:]):
        pa, pb = V[a]["xy"], V[b]["xy"]; u = unit(pb - pa); mid = (pa + pb) / 2
        tn = turn_deg(V[b - 1]["xy"], pb, V[b + 1]["xy"]) if 0 < b < len(V) - 1 else 0
        sight = "; ".join(f"{n} {d:.0f}km {asp} {m:+.0f}" for n, _, d, asp, m in RM.in_sight(mid, u))
        cell = f"{'ENEMY' if W.enemy(*mid) else '-'}{'/SA-14' if W.sa14(*mid) else ''}"
        L.append(f"{a:2d}  {(V[a]['label'] or 'corner')[:20]:20s} -> {(V[b]['label'] or 'corner')[:20]:20s} {hdg(u):4.0f} "
                 f"{np.linalg.norm(pb - pa) / K:6.1f} {tn:5.0f}  {cell:9s}  {sight}")
    L += ["", "timeline (every 20 km):"]
    for st_ in best["fuel"]["steps"][::20]:
        p, u = st_["xy"], st_["u"]
        L.append(f"{st_['t'] / 60:5.1f} min ({p[0]:5.0f},{p[1]:5.0f}) hdg {hdg(u):3.0f} {st_['alt']:4.0f} ft {st_['kt']:3d} kt {st_['fuel']:6.0f} lb "
                 f"{('ENEMY' if st_['enemy'] else '-'):6s} "
                 + "; ".join(f"{n} {d:.0f}km {a} {m:+.0f}" for n, _, d, a, m in
                             RM.in_sight(p, u, st_["kt"], st_["alt"], st_["bank"], st_["bay"])))
    L += ["", "NAV: " + NAV_NEEDS, "", "flight tests to request:"] + [f"  - {t}" for t in FLIGHT_TESTS]
    if best.get("loiter"):
        lo = best["loiter"]["fit"]
        L += ["", "loiter test plan (NOT yet flown):"] + [f"  - {t}" for t in LOITER_TEST]
        if lo.get("peak_ft"):
            hi = lo["peak_ft"] + 1000
            L.append(f"  option (not planned): orbit above the peaks at ~{hi:.0f} ft: EMV {rg.emv(int(hi), 190, W.opp)} "
                     f"level, still invisible in a mountain cell; same lb/min, climb fuel not modelled, terrain risk if the "
                     f"altitude hold sags. The low racetrack keeps the walls rule.")
    return "\n".join(L) + "\n"


def draw(pl, best, samples):
    """route.png as a PIL image."""
    W, S = pl.W, 32
    rx = [p[0][0] for p in best["pts"]]; ry = [p[0][1] for p in best["pts"]]
    X0, X1 = max(0, int(min(rx)) - 6400), min(W.size, int(max(rx)) + 6400)
    Y0, Y1 = max(0, int(min(ry)) - 6400), min(W.size, int(max(ry)) + 6400)
    sub = W.H[Y0 // W.step:Y1 // W.step:S // W.step, X0 // W.step:X1 // W.step:S // W.step]
    im = np.full(sub.shape + (3,), 235, np.uint8); im[sub > 0] = (200, 200, 160); im[sub >= pl.P["mountain_ft"]] = (150, 100, 55)
    img = Image.fromarray(im); d = ImageDraw.Draw(img, "RGBA")
    Q = lambda x, y: ((x - X0) / S, (y - Y0) / S)
    g = W.tgrid
    for j in range(16):
        for i in range(16):
            x0, y0 = Q(i * 2048, j * 2048); x1, y1 = Q(i * 2048 + 2047, j * 2048 + 2047)
            terr = (g[j, i] >> 2) & 3
            if g[j, i] & 3 == 0: d.rectangle((x0, y0, x1, y1), fill=(255, 0, 0, 22))
            if terr == 0: d.rectangle((x0, y0, x1, y1), outline=(90, 60, 30, 170), width=2)     # mountain cell: invisible
            elif terr == 1: d.rectangle((x0, y0, x1, y1), outline=(160, 140, 60, 130), width=1)  # rough: x0.5
            elif terr == 3: d.rectangle((x0, y0, x1, y1), fill=(0, 80, 255, 18))                # sea: x1.5
            if W.sa14_cells[j, i]:
                for k in range(0, int(x1 - x0), 8):
                    d.line([(x0 + k, y0), (x1, y1 - k)], fill=(255, 120, 0, 90))
                    d.line([(x0, y0 + k), (x1 - k, y1)], fill=(255, 120, 0, 90))
                d.text((x0 + 2, y0 + 2), "SA-14", fill=(200, 80, 0))
    for r in W.radars:
        cx, cy = Q(r["x"], r["y"]); c = (200, 0, 0) if r["type"] == "P" else (0, 130, 0)
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=c); d.text((cx + 4, cy), f"{r['place']} {r['system']}", fill=c)
    d.line([Q(*p[0]) for p in best["pts"]], fill=(220, 0, 220), width=2)
    lo = [Q(*p[0]) for p in best["pts"] if (p[2] or {}).get("kind") == "loiter"]
    if lo: d.line(lo, fill=(0, 160, 0), width=2)
    for p, l, a in best["pts"]:
        if a.get("kind") == "probe": x, y = Q(*p); d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 60, 255))
        if l: d.text(Q(*p), l, fill=(0, 0, 0))
    for v in best["V"]:
        if v["fillet"]: x, y = Q(*v["xy"]); d.ellipse((x - 3, y - 3, x + 3, y + 3), outline=(120, 0, 120))
    for p, l, a in best["pts"]:
        if a.get("kind") == "launch":
            t = next(t for t in pl.M["targets"] if t["role"].rstrip("?") == a["role"])
            d.line([Q(*p), Q(t["x"], t["y"])], fill=(255, 120, 0), width=1)
            x, y = Q(*p); d.ellipse((x - 4, y - 4, x + 4, y + 4), outline=(255, 120, 0), width=2)
    for p, e in samples:
        x, y = Q(*p); d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(0, 0, 255) if e else (120, 120, 120))
    return img


def route_json(pl, best, spec, summ):
    """route.json (schema 2): the spec as planned, the profile, the summary and the points (route_points)."""
    pts = best["pts"]
    return dict(schema=2, spec=spec, profile=({k: v for k, v in pl.P.items() if k not in profiles.OBJECTIVE_KEYS} if pl.objective == "cost" else pl.P), summary=summ, chosen=best["tag"], errors=best["errors"],
                takeoff_heading=spec.get("takeoff_heading"),
                waypoints_game=[[[float(p[0]), float(p[1])], l] for p, l, _ in pts],
                points=route_points(pl, best))


def route_points(pl, best):
    """route.json "points": per point the planned setting from it on (fuelplan.integrate): throttle, flaps, speed, fuel
    and game time, and the altitude nav holds on the leg ending there."""
    pts = best["pts"]
    leg_set = {}
    for st in best["fuel"]["steps"]: leg_set.setdefault(st["leg"], st)
    alts = fuelplan.leg_alt(best["fuel"]["steps"], len(pts), pl.P)
    def fly(i, at):                                  # the planned setting from this point on (fuelplan.integrate)
        st = leg_set.get(i) or leg_set.get(i - 1)
        if not st: return dict(at)
        out = {**at, "throttle": st["thr"], "flaps": st["flaps"], "speed_kt": at.get("speed_kt", st["kt"]),
               "fuel_lb": round(st["fuel"]), "t_game_s": round(st["t"])}
        if "alt_ft" not in at and at.get("kind") not in fuelplan.KEEP_WHOLE[4:]:   # takeoff / landing: nav's own
            out["alt_ft"] = round(alts[i])           # nav holds the alt_ft of the point a leg ends at
        return out
    return [dict(x=float(p[0]), y=float(p[1]), label=l, **fly(i, at)) for i, (p, l, at) in enumerate(pts)]
