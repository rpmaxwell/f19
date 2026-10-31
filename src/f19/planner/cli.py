"""The planner's file side: plan dirs in, route.json / report.txt / route.png / terrain.npz out.

  python -m f19.planner <plan_dir> [--spec plan.json]            main(): plan.json -> the files (plan.py's docstring)
  python -m f19.planner.new_plan --extra-fuel 1 ...         new_plan(): read the briefing, write plan.json, plan

Everything else (planning, the report text, the picture, the route.json dict) is in memory: planner.api / report.
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

import numpy as np

from f19.core.settings import settings as S

from .api import plan_mission, resolve_takeoff_heading
from .planner import Planner

ROOT = Path(__file__).resolve().parents[3]       # the repo


def load_mission(spec, plan_dir):
    """<plan_dir>/<spec mission> (default mission_mem.json) as a dict, or None when there is none (a mission without
    targets or radars, as the Planner always allowed)."""
    f = Path(plan_dir) / spec.get("mission", "mission_mem.json")
    return json.load(open(f)) if f.exists() else None


def planner_for_dir(spec, plan_dir):
    """Planner(spec, plan_dir) as plan.py had it: the mission read from the plan dir."""
    return Planner(spec, load_mission(spec, plan_dir), plan_dir)


def emit(D, spec, suffix):
    """Plan one variant and write its files; returns True when valid."""
    res = plan_mission(spec, load_mission(spec, D), D, progress=lambda m: print(m, flush=True))
    (D / f"report{suffix}.txt").write_text(res.report); print(res.report)
    best, pl = res.best, res.planner
    name = f"route{suffix}.json" if not best["errors"] else f"route{suffix}.invalid.json"
    json.dump(res.route, open(D / name, "w"), indent=1)
    np.savez_compressed(D / "terrain.npz", height_game=pl.W.H.astype(np.uint16), height_step=pl.W.step)
    res.image().save(D / f"route{suffix}.png")
    print(("plan INVALID" if best["errors"] else "wrote") + f" {D / name}" + (f" ({len(best['errors'])} errors; see report{suffix}.txt)" if best["errors"] else ""))
    return not best["errors"]


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("plan_dir"); ap.add_argument("--spec", default="plan.json")
    a = ap.parse_args(argv); D = Path(a.plan_dir)
    spec = json.load(open(D / a.spec))
    th = spec.get("takeoff_heading")
    if th is None:                                    # what the game will do, from the theater and the takeoff place
        mission = json.load(open(D / spec.get("mission", "mission_mem.json")))
        th = resolve_takeoff_heading(spec, mission)
        theater = spec.get("theater") or mission.get("theater")
        carrier = isinstance(spec.get("takeoff"), dict) and spec["takeoff"].get("carrier", True)   # as World.place
        print(f"takeoff heading {th:g} ({theater} {'carrier' if carrier else 'airbase'})")
    variants = [("_N", 0), ("_S", 180)] if th == "both" else [("", th)]
    ok = [emit(D, {**spec, "takeoff_heading": h}, suffix) for suffix, h in variants]
    if not all(ok): sys.exit("plan INVALID: the fix belongs in the planner or plan.json, not the route file")


NEW_PLAN_DOC = """Plan the mission on the briefing screen in one step: read it from memory, write plan.json, run the planner.

  python -m f19.planner.new_plan --extra-fuel 1                 (defaults: theater from memory, Cold War, strike, Veteran, day)
  python -m f19.planner.new_plan --extra-fuel 2 --profile fuel --night
  python -m f19.planner.new_plan --mission-file logs/plan/X/mission_mem.json --extra-fuel 1   (no game needed)

Writes logs/plan/<theater>_<primary place>_<MMDD_HHMM>/ (plan.json, mission_mem.json, route.json, report.txt,
route.png) and prints the report and the nav command. --extra-fuel is the number of Extra Fuel tanks you'll load
(0-3): pick it after the briefing and the ordnance screen; nav transfers one whenever fuel drops below 500 lb.
Change it later by editing "extra_fuel" in plan.json and running `python -m f19.planner <dir>` again.
"""


def new_plan(argv=None):
    ap = argparse.ArgumentParser(description=NEW_PLAN_DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extra-fuel", type=int, default=0, choices=range(4), help="Extra Fuel tanks in the loadout (0-3)")
    ap.add_argument("--profile", default="stealth", help="planner profile: stealth / balanced / fast / fuel / coldwar")
    ap.add_argument("--loiter", choices=("auto", "off"), default="off",
                    help="after planning without one, add a loiter in a radar-quiet enemy cell to farm cold-war samples if the "
                         "spare fuel buys one (default off: a loiter is not flown yet and costs planning time)")
    ap.add_argument("--theater", default="auto", help="CE / NC / LB / PG (default: detect from the game's memory)")
    ap.add_argument("--roe", type=int, default=0, help="0 Cold War, 1 Limited, 2 Conventional")
    ap.add_argument("--mission", default="strike", choices=("strike", "air"))
    ap.add_argument("--opponent", default="veteran", choices=("green", "regular", "veteran", "elite"))
    ap.add_argument("--landing-level", choices=("no_crashes", "easy", "realistic"), help="score estimate only")
    ap.add_argument("--fuel-estimate", type=int, help="briefing FUEL ESTIMATE, lb (score estimate only)")
    ap.add_argument("--night", action="store_true", help="night mission: SA-14 cells are harmless")
    ap.add_argument("--sa14", choices=("auto", "hard", "soft"), default="auto",
                    help="live SA-14 cells by day: auto = walls at Elite, a price below; hard = walls at any level "
                         "(a secondary that needs one is skipped); soft = the price only")
    ap.add_argument("--objective", choices=("cost", "points", "aircraft"), default="cost",
                    help="how options are ranked: cost = route cost (default), points = expected career points, "
                         "aircraft = points with the enemy-fighter model (docs/design/planner_refactor.md)")
    ap.add_argument("--mission-file", help="use a saved mission_mem.json instead of reading the game")
    ap.add_argument("--out", help="plan directory (default logs/plan/<theater>_<place>_<time>)")
    a = ap.parse_args(argv)

    tmp = S.logs / "plan/_briefing_read.json"
    if a.mission_file:
        M = json.load(open(a.mission_file))
    else:
        r = subprocess.run([sys.executable, "-m", "f19.cli.read_mission", "--theater", a.theater, "--out", str(tmp)])
        if r.returncode: sys.exit("could not read the mission (is F-19 on the briefing screen, and no other script "
                                  "holding the automation port? `lsof -nP -iTCP:6000`)")
        M = json.load(open(tmp))
    a.theater = M.get("theater") or (a.theater if a.theater != "auto" else sys.exit("the mission file has no theater: pass --theater"))
    if any(t["role"].endswith("?") for t in M["targets"]) or "takeoff" not in M:
        sys.exit("the mission read has no briefing data (roles end in '?'): read it on the briefing screen, not in flight")
    place = M["targets"][0]["place"].lower().replace(" ", "_") if M["targets"] else "mission"
    D = Path(a.out) if a.out else S.logs / "plan" / f"{a.theater.lower()}_{place}_{time.strftime('%m%d_%H%M')}"
    D.mkdir(parents=True, exist_ok=True)
    json.dump(M, open(D / "mission_mem.json", "w"), indent=1)
    tk = M["takeoff"]
    takeoff = tk["place"] if not tk.get("carrier") else dict(x=tk["x"], y=tk["y"], label=tk["place"] or "Carrier", carrier=True)
    settings = dict(roe=a.roe, mission=a.mission, opponent=a.opponent, night=a.night)
    if a.sa14 != "auto": settings["sa14"] = a.sa14
    if a.landing_level: settings["landing"] = a.landing_level
    if a.fuel_estimate: settings["fuel_estimate"] = a.fuel_estimate
    spec = dict(theater=a.theater, takeoff=takeoff, landing=M["landing"]["place"], profile=a.profile,
                extra_fuel=a.extra_fuel, settings=settings, overrides=dict(loiter=a.loiter))
    if a.objective != "cost": spec["objective"] = a.objective
    json.dump(spec, open(D / "plan.json", "w"), indent=1)
    print(f"mission: {', '.join(f'{t['role']} {t.get('task', '?')} {t['object']} at {t['place']}' for t in M['targets'])}; "
          f"{M['takeoff']['place']} -> {M['landing']['place']}; plan in {D}", flush=True)
    r = subprocess.run([sys.executable, "-m", "f19.planner", str(D)])
    route = D / "route.json"
    if r.returncode == 0 and route.exists():
        rel = route.resolve().relative_to(ROOT) if route.resolve().is_relative_to(ROOT) else route.resolve()
        print(f"\nfly it:\n  uv run python -m f19.cli.fly --route {rel} --auto --hdg --director --memlog")
    sys.exit(r.returncode)
