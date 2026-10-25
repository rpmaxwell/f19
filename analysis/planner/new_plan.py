"""Plan the mission on the briefing screen in one step: read it from memory, write plan.json, run the planner.

  python analysis/planner/new_plan.py --extra-fuel 1                 (defaults: theater from memory, Cold War, strike, Veteran, day)
  python analysis/planner/new_plan.py --extra-fuel 2 --profile fuel --night
  python analysis/planner/new_plan.py --mission-file logs/plan/X/mission_mem.json --extra-fuel 1   (no game needed)

Writes logs/plan/<theater>_<primary place>_<MMDD_HHMM>/ (plan.json, mission_mem.json, route.json, report.txt,
route.png) and prints the report and the nav command. --extra-fuel is the number of Extra Fuel tanks you'll load
(0-3): pick it after the briefing and the ordnance screen; nav transfers one whenever fuel drops below 500 lb.
Change it later by editing "extra_fuel" in plan.json and running `python analysis/planner <dir>` again.
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[2] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import argparse, json, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extra-fuel", type=int, default=0, choices=range(4), help="Extra Fuel tanks in the loadout (0-3)")
    ap.add_argument("--profile", default="stealth", help="planner profile: stealth / balanced / fast / fuel / coldwar")
    ap.add_argument("--loiter", choices=("auto", "off"), default="auto",
                    help="farm cold-war samples in an invisible enemy mountain cell if the fuel allows (default auto)")
    ap.add_argument("--theater", default="auto", help="CE / NC / LB / PG (default: detect from the game's memory)")
    ap.add_argument("--roe", type=int, default=0, help="0 Cold War, 1 Limited, 2 Conventional")
    ap.add_argument("--mission", default="strike", choices=("strike", "air"))
    ap.add_argument("--opponent", default="veteran", choices=("green", "regular", "veteran", "elite"))
    ap.add_argument("--landing-level", choices=("no_crashes", "easy", "realistic"), help="score estimate only")
    ap.add_argument("--fuel-estimate", type=int, help="briefing FUEL ESTIMATE, lb (score estimate only)")
    ap.add_argument("--night", action="store_true", help="night mission: SA-14 cells are harmless")
    ap.add_argument("--mission-file", help="use a saved mission_mem.json instead of reading the game")
    ap.add_argument("--out", help="plan directory (default logs/plan/<theater>_<place>_<time>)")
    a = ap.parse_args()

    tmp = S.logs / "plan/_briefing_read.json"
    if a.mission_file:
        M = json.load(open(a.mission_file))
    else:
        r = subprocess.run([sys.executable, str(ROOT / "scripts/read_mission.py"), "--theater", a.theater, "--out", str(tmp)])
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
    if a.landing_level: settings["landing"] = a.landing_level
    if a.fuel_estimate: settings["fuel_estimate"] = a.fuel_estimate
    spec = dict(theater=a.theater, takeoff=takeoff, landing=M["landing"]["place"], profile=a.profile,
                extra_fuel=a.extra_fuel, settings=settings, overrides=dict(loiter=a.loiter))
    json.dump(spec, open(D / "plan.json", "w"), indent=1)
    print(f"mission: {', '.join(f'{t['role']} {t.get('task', '?')} {t['object']} at {t['place']}' for t in M['targets'])}; "
          f"{M['takeoff']['place']} -> {M['landing']['place']}; plan in {D}", flush=True)
    r = subprocess.run([sys.executable, str(ROOT / "analysis/planner"), str(D)])
    route = D / "route.json"
    if r.returncode == 0 and route.exists():
        rel = route.resolve().relative_to(ROOT) if route.resolve().is_relative_to(ROOT) else route.resolve()
        print(f"\nfly it:\n  python3 scripts/nav.py --route {rel} --auto --hdg --memlog")
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
