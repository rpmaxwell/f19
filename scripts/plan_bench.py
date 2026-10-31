"""Planner benchmark: where plan_mission spends its time, how much of it is repeated, and what the options were.

  uv run python scripts/plan_bench.py ce_kolobrzeg_1022_2250                 # a fixture under $F19_DATA/fixtures/plans
  uv run python scripts/plan_bench.py path/to/plan_dir --out /tmp/x          # any plan dir; --out writes route.json + report.txt
  uv run python scripts/plan_bench.py ce_budejovice_1023_1811 --sa14 soft    # override settings.sa14
  F19_PLAN_WORKERS=1 ...                                                     # per-stage counters only see this process

Stages nest (attack_options calls search / reduce / expand), so the shares overlap. "unique" counts distinct inputs
(coordinates rounded to 0.1 unit; search includes h0, h_goal, margin) so calls / unique is the factor a cache could win.
Tool, not product code: it wraps the planner's functions with counters and changes nothing else.
"""
import argparse, collections, json, shutil, sys, time
from pathlib import Path

from f19.core.settings import settings as S
from f19.planner import alertsim, fuelplan, report as _report
from f19.planner.api import plan_mission
from f19.planner.cli import load_mission
from f19.planner.planner import Planner
from f19.planner.router import Router
from f19.planner.world import World

STAGES = collections.OrderedDict()           # name -> [calls, seconds, set of unique keys]


def _rnd(x):
    if isinstance(x, (list, tuple)): return tuple(_rnd(v) for v in x)
    if isinstance(x, float): return round(x, 1)
    try: return tuple(_rnd(float(v)) for v in x)       # numpy arrays
    except (TypeError, ValueError): return x


def wrap(owner, name, label=None, key=None):
    if not hasattr(owner, name): return                # a stage this version of the planner does not have
    fn = getattr(owner, name)
    label = label or f"{owner.__name__ if hasattr(owner, '__name__') else owner}.{name}"
    rec = STAGES.setdefault(label, [0, 0.0, set()])

    def inner(*a, **k):
        rec[0] += 1
        if key: rec[2].add(key(*a, **k))
        t = time.perf_counter()
        try: return fn(*a, **k)
        finally: rec[1] += time.perf_counter() - t
    inner.__wrapped__ = fn
    setattr(owner, name, inner)


def instrument():
    wrap(Planner, "plan_with_loiter", "Planner.plan_with_loiter")
    wrap(Planner, "plan", "Planner.plan")
    wrap(Planner, "attack_options", "Planner.attack_options")
    wrap(Planner, "_stretch_costs", "Planner._stretch_costs")
    wrap(Planner, "_plan_batch", "Planner._plan_batch")
    wrap(Planner, "_workers", "Planner._workers")
    wrap(Router, "search", "Router.search",
         key=lambda self, a, b, h0=None, h_goal=None, margin_km=None, deadline=None: _rnd((a, b, h0, h_goal, margin_km)))
    wrap(Router, "reduce", "Router.reduce", key=lambda self, pts: _rnd(pts))
    wrap(Router, "expand", "Router.expand")
    wrap(Router, "repair", "Router.repair")
    wrap(Router, "route_cost", "Router.route_cost")
    wrap(World, "sa14_clear", "World.sa14_clear")
    wrap(fuelplan, "integrate", "fuelplan.integrate")
    wrap(alertsim, "simulate", "alertsim.simulate")
    wrap(_report, "summarise", "report.summarise")


def table(total):
    print(f"\n{'stage':28s} {'calls':>8s} {'unique':>8s} {'time s':>8s} {'share':>6s}")
    for name, (n, t, u) in STAGES.items():
        if not n: continue
        print(f"{name:28s} {n:8d} {(str(len(u)) if u else '-'):>8s} {t:8.1f} {100 * t / total:5.0f}%")


def options_summary(res):
    rows = res.results
    lo = [r for r in rows if r.get("loiter") or "loiter" in r.get("tag", "")]
    valid = [r for r in rows if not r["errors"]]
    fuel_only = [r for r in rows if r["errors"] and all(e.startswith("fuel at landing") for e in r["errors"])]
    print(f"\noptions planned {len(rows)}: valid {len(valid)}, loiter {len(lo)} "
          f"(of those fuel-only failures {sum(1 for r in lo if r in fuel_only)}), fuel-only failures {len(fuel_only)}")
    print(f"chosen: {res.best['tag']}  cost {res.best['cost']:.1f}  errors {len(res.best['errors'])}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan", help="a plan dir, or a fixture name under $F19_DATA/fixtures/plans")
    ap.add_argument("--sa14", choices=("auto", "hard", "soft"), help="override settings.sa14 (ce_budejovice needs soft)")
    ap.add_argument("--out", help="write route.json and report.txt here")
    ap.add_argument("--no-instrument", action="store_true", help="time plan_mission alone (the counters cost a little)")
    a = ap.parse_args(argv)
    D = Path(a.plan) if Path(a.plan).is_dir() else S.data / "fixtures/plans" / a.plan
    spec = json.load(open(D / "plan.json"))
    if a.sa14: spec["settings"] = {**spec.get("settings", {}), "sa14": a.sa14}
    mission = load_mission(spec, D)
    if not a.no_instrument: instrument()
    t0 = time.perf_counter()
    res = plan_mission(spec, mission, str(D), progress=lambda m: print(m, flush=True))
    total = time.perf_counter() - t0
    print(f"\n{D.name}: plan_mission {total:.1f} s, opponent {spec.get('settings', {}).get('opponent', 'veteran')}")
    if not a.no_instrument: table(total)
    options_summary(res)
    if a.out:
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        json.dump(res.route, open(out / "route.json", "w"), indent=1)
        (out / "report.txt").write_text(res.report)
        print(f"wrote {out}/route.json, report.txt")


if __name__ == "__main__":
    sys.exit(main())
