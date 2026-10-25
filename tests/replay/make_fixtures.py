"""Build the replay fixtures under F19_DATA/fixtures/replay from the live logs (run once; the tests only read them).

  uv run python tests/replay/make_fixtures.py [--force]

Layout (a miniature F19_DATA, so product code that reads `settings.logs` can be pointed at it):
  fixtures/replay/logs/memlog/<run>/windows.json + snap_*.bin   (only the snapshots inside the run's windows)
  fixtures/replay/logs/nav/<nav run>.csv + .events.txt           (whole files)
  fixtures/replay/plan/<plan>/route.json, mission_mem.json, plan.json, terrain.npz   (the route the flight flew)
  fixtures/replay/manifest.json                                  (RUNS below + snapshot counts)
"""
import json, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from f19.core.settings import settings as S  # noqa: E402

DEST = S.data / "fixtures" / "replay"

# memlog run -> nav run (nearest epoch, as analysis/memlog_load.py picks it), plan dir (route + terrain it flew),
# snapshot windows (epoch s, inclusive; None = whole run) and what each window covers.
RUNS = {
    "1792609731": dict(nav="1792609719", plan="ce_katowice_1021_1458", covers="takeoff, Maverick attack, terrain escape (turn + climb)",
                       windows=[(1792609734, 1792609860, "takeoff"), (1792610760, 1792610880, "terrain escape 1 (turn)"),
                                (1792611740, 1792611900, "primary Maverick attack"),
                                (1792613400, 1792613520, "terrain escape 4 (turn + CLIMB)")]),
    "1792546698": dict(nav="1792546685", plan="ce_test_1019_2337", covers="photo attack, Maverick attack, SA-14 launches + flare",
                       windows=[(1792547660, 1792547760, "primary photo attack"),
                                (1792548290, 1792548600, "secondary Maverick attack, SA-14 missiles")]),
    "1792715432": dict(nav="1792715419", plan="ce_stupsk_1022_2025", covers="takeoff, fighter evasion (MiG-31 / IL-76), AA-9 + other missiles (Elite)",
                       windows=None),
}


def main(force=False):
    if DEST.exists() and not force: sys.exit(f"{DEST} exists (use --force to rebuild)")
    man = {}
    for run, R in RUNS.items():
        src = S.logs / "memlog" / run; dst = DEST / "logs" / "memlog" / run
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "windows.json", dst / "windows.json")
        n = 0
        for f in sorted(src.glob("snap_*.bin")):
            t = float(f.stem[5:])
            if R["windows"] is None or any(a <= t <= b for a, b, _ in R["windows"]):
                shutil.copy2(f, dst / f.name); n += 1
        nd = DEST / "logs" / "nav"; nd.mkdir(parents=True, exist_ok=True)
        for ext in (".csv", ".events.txt"):
            if (S.logs / "nav" / (R["nav"] + ext)).exists(): shutil.copy2(S.logs / "nav" / (R["nav"] + ext), nd / (R["nav"] + ext))
        pd = DEST / "plan" / R["plan"]; pd.mkdir(parents=True, exist_ok=True)
        for name in ("route.json", "mission_mem.json", "plan.json", "terrain.npz"):
            shutil.copy2(S.logs / "plan" / R["plan"] / name, pd / name)
        man[run] = dict(R, snapshots=n)
        print(run, n, "snapshots")
    (DEST / "manifest.json").write_text(json.dumps(man, indent=1))


if __name__ == "__main__":
    main("--force" in sys.argv)
