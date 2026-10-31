"""Re-plan a fixture under each planner objective and score every chosen route under every objective.

  uv run python scripts/compare_objectives.py ce_liberec_1027_0916 [--sa14 soft] [--out DIR] [--objectives cost,points,aircraft]

objective "cost"     route cost (the fuel / transit-km proxy: what the planner has always ranked by)
objective "points"   E[career points] - lambda P(death), a constant fighter hit rate per airborne minute (notebook, "Beyond fuel")
objective "aircraft" the same value with hits from the fighter model (planner/fighters.py)
The table's rows are the chosen routes, its columns how each objective values them. Tool, not product code.
"""
import argparse, json, time
from pathlib import Path

from f19.core.settings import settings as S
from f19.planner.api import plan_mission
from f19.planner.cli import load_mission


def evaluate(pl, r, objective):
    """r's points.value under `objective` (the planner `pl` is switched for the call; r["points_eval"] is restored)."""
    keep, spec = r.pop("points_eval", None), pl.spec
    pl.spec = {**spec, "objective": objective}
    try: return pl.points_eval(r)
    finally:
        pl.spec = spec; r.pop("points_eval", None)
        if keep is not None: r["points_eval"] = keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture"); ap.add_argument("--sa14"); ap.add_argument("--out"); ap.add_argument("--objectives", default="cost,points,aircraft")
    ap.add_argument("--hunt", help="fighters.HUNT overrides, e.g. near_mean=6,mid_mean=4,max_shots=4 (sensitivity of the aircraft objective)")
    ap.add_argument("--opponent", help="re-plan the fixture at another opponent level (green..elite)")
    a = ap.parse_args()
    D = Path(a.fixture) if Path(a.fixture).exists() else S.data / "fixtures" / "plans" / a.fixture
    spec = json.load(open(D / "plan.json"))
    if a.sa14: spec["settings"] = {**spec.get("settings", {}), "sa14": a.sa14}
    if a.opponent: spec["settings"] = {**spec.get("settings", {}), "opponent": a.opponent}
    if a.hunt: spec["overrides"] = {**spec.get("overrides", {}), "fighter_hunt": {k: float(v) for k, v in (kv.split("=") for kv in a.hunt.split(","))}}
    mission = load_mission(spec, D)
    objs = a.objectives.split(",")
    chosen = {}
    for ob in objs:
        t = time.time()
        res = plan_mission({**spec, "objective": ob}, mission, str(D))
        chosen[ob] = (res, time.time() - t)
        print(f"{ob:9s}: {time.time() - t:5.1f} s  valid={res.valid}  {res.best['tag']}", flush=True)
        if a.out:
            o = Path(a.out) / ob; o.mkdir(parents=True, exist_ok=True)
            (o / "route.json").write_text(json.dumps(res.route)); (o / "report.txt").write_text(res.report)
    print(f"\n{D.name} (opponent {spec.get('settings', {}).get('opponent', 'veteran')}): value of each chosen route under each objective")
    print(f"{'chosen by':10s} {'min':>4s} {'km':>5s} {'roles':>17s}  " + "  ".join(f"{'E[pts] ' + ob:>26s}" for ob in ("points", "aircraft")) + "   fighters (aircraft model)")
    rows = {}
    for ob, (res, _) in chosen.items():
        r = res.best; pl = res.planner
        ev = {o2: evaluate(pl, r, o2) for o2 in ("points", "aircraft")}
        rows[ob] = ev
        km = sum(s["km"] for s in r["fuel"]["steps"])
        e = ev["aircraft"]
        print(f"{ob:10s} {e['minutes']:4.0f} {km:5.0f} {'+'.join(e['roles']):>17s}  "
              + "  ".join(f"{ev[o2]['exp_pts']:7.0f} ({ev[o2]['p_bay']:3.0%} bay, {ev[o2]['hits']:4.2f} hits)" for o2 in ("points", "aircraft"))
              + f"   shots {e['shots']:.2f}, visual IDs {e['visual_ids']:.2f}, P(net alert) {e['p_net'] if e['p_net'] is not None else float('nan'):.0%}")
    print("\nchosen routes:")
    for ob, (res, _) in chosen.items(): print(f"  {ob:9s} {res.best['tag']}")
    return rows


if __name__ == "__main__":
    main()
