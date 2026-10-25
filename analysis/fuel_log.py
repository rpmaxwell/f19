"""Per-flight fuel log: throttle changes, fuel burned, time and distance, from a memlog (nav --memlog).

The planner predicts fuel from the throttle it plans; nav flies a speed hold that keeps nudging the throttle
(2026-10-20: 50 % for 81 % of the time, 60 % 13 %, 40 % 6 %), and the plane covers 0.784 x its indicated
airspeed in map distance per game-clock second. This keeps a record of what each flight really did, so the
planner's numbers (planner/world.GROUND_PER_KT, the throttle mix it assumes) can be refit from many flights.

  fuel_log.py <memlog dir>      log one flight -> logs/fuel/<id>.json, and its row in logs/fuel/flights.jsonl
  fuel_log.py all               every logs/memlog/* flight not logged yet
  fuel_log.py summary           pooled numbers over all logged flights (what the planner should assume)

Per flight: every throttle-setting change (game s since takeoff, from -> to, kt, alt, fuel, flaps), the time
share of each setting, airborne game minutes, fuel used (measured) vs the burn rule's prediction from the
throttle time (fuel_model.burn_step), distance flown, mean indicated kt and the ground/indicated ratio.
Memory offsets (ds, file index = ds - 0x490) as analysis/radar_game.py; FINDINGS.md "Flight-log variables".
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import glob, json, math, struct, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fuel_model import burn_step  # noqa: E402

OUT = S.logs / "fuel"
CLOCK, FUEL, THR, ALT, KT27, PX, PY, FLAGS = 0x6630, 0x4ED0, 0x46F2, 0x46E0, 0x7F94, 0x94EE, 0x94FE, 0x684C
FLAPS, GEAR_UP = 0x02, 0x01


def _snaps(d):
    fs = sorted(glob.glob(str(Path(d) / "snap_*.bin")), key=lambda f: float(f.split("_")[-1][:-4]))
    for f in fs:
        b = Path(f).read_bytes()
        if len(b) < 0xA000: continue
        g = lambda a: struct.unpack_from("<H", b, a - 0x490)[0]
        yield dict(t=float(f.split("_")[-1][:-4]), clock=g(CLOCK), fuel=g(FUEL), thr=g(THR), alt=g(ALT),
                   kt=g(KT27) / 27, x=g(PX), y=g(PY), flags=g(FLAGS))


def log_flight(d):
    S = [s for s in _snaps(d)]
    air = [s for s in S if s["alt"] > 0 and s["thr"] <= 100]
    if len(air) < 10: return None
    # one airborne stretch: first to last airborne snapshot (a crash or landing ends it)
    a0, a1 = air[0], air[-1]
    span = [s for s in S if a0["t"] <= s["t"] <= a1["t"]]
    changes, time_at = [], defaultdict(float)
    km = kt_s = 0.0; added = 0; skipped = 0
    for p, q in zip(span, span[1:]):
        dt = (q["clock"] - p["clock"]) * 2.0                         # game s (1 tick = 2 s)
        if dt <= 0 or dt > 120: skipped += 1; continue               # frozen game / clock reset / long gap (reload)
        step = math.hypot(q["x"] - p["x"], q["y"] - p["y"]) / 64
        if step > 2 * max(p["kt"], q["kt"], 100) * 1.852 * dt / 3600: skipped += 1; continue   # position jump (state load)
        if q["fuel"] > p["fuel"] + 100: added += q["fuel"] - p["fuel"]   # Extra Fuel transfer (+1900)
        time_at[p["thr"]] += dt; km += step; kt_s += (p["kt"] + q["kt"]) / 2 * dt
        if q["thr"] != p["thr"]:
            changes.append(dict(s=round((q["clock"] - a0["clock"]) * 2.0), frm=p["thr"], to=q["thr"], kt=round(q["kt"]),
                                alt=q["alt"], fuel=q["fuel"], flaps=bool(q["flags"] & FLAPS)))
    game_min = sum(time_at.values()) / 60
    used = a0["fuel"] - a1["fuel"] + added
    pred = sum(burn_step(th) * t / 4.0 for th, t in time_at.items())  # one burn step per 4 game s
    mean_kt = kt_s / sum(time_at.values()) if time_at else 0.0
    while len(span) > 2 and span[-1]["clock"] == span[-2]["clock"]: span.pop()   # a frozen game repeats its last state
    frozen = len([s for s in air if s["t"] > span[-1]["t"]]) > 30
    after = [s for s in S if s["t"] > a1["t"]]
    ended = ("landed" if any(s["alt"] == 0 and s["kt"] < 5 for s in after) else
             "game froze" if frozen else "log ends airborne")
    flaps_share = sum(1 for s in span if s["flags"] & FLAPS) / len(span)
    out = dict(id=Path(d).name, airborne_game_min=round(game_min, 1), fuel_start=a0["fuel"], fuel_end=a1["fuel"],
               fuel_used=used, burn_rule_predicted=round(pred), measured_lb_min=round(used / game_min, 1) if game_min else None,
               km=round(km, 1), mean_indicated_kt=round(mean_kt, 1),
               ground_per_kt=round(km / (game_min / 60) / 1.852 / mean_kt, 3) if game_min else None,
               throttle_share={str(k): round(v / 60 / game_min, 3) for k, v in sorted(time_at.items())} if game_min else {},
               throttle_changes_per_hour=round(len(changes) / (game_min / 60), 1) if game_min else None,
               flaps_share=round(flaps_share, 3), ended=ended, extra_fuel_added=added, skipped_steps=skipped,
               changes=changes)
    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(OUT / f"{out['id']}.json", "w"), indent=1)
    rows = [json.loads(l) for l in open(OUT / "flights.jsonl")] if (OUT / "flights.jsonl").exists() else []
    rows = [r for r in rows if r["id"] != out["id"]] + [{k: v for k, v in out.items() if k != "changes"}]
    with open(OUT / "flights.jsonl", "w") as f:
        for r in sorted(rows, key=lambda r: r["id"]): f.write(json.dumps(r) + "\n")
    return out


def summary():
    rows = [json.loads(l) for l in open(OUT / "flights.jsonl")] if (OUT / "flights.jsonl").exists() else []
    rows = [r for r in rows if r["airborne_game_min"] >= 10]
    if not rows: print("no flights logged yet"); return
    T = sum(r["airborne_game_min"] for r in rows)
    w = lambda k: sum(r[k] * r["airborne_game_min"] for r in rows if r[k] is not None) / T
    share = defaultdict(float)
    for r in rows:
        for k, v in r["throttle_share"].items(): share[k] += v * r["airborne_game_min"] / T
    print(f"{len(rows)} flights, {T:.0f} airborne game min")
    print(f"  fuel: measured {sum(r['fuel_used'] for r in rows)} lb vs burn rule {sum(r['burn_rule_predicted'] for r in rows)} lb;"
          f" {w('measured_lb_min'):.1f} lb/min")
    print(f"  ground / indicated speed {w('ground_per_kt'):.3f} (planner/world.GROUND_PER_KT), mean {w('mean_indicated_kt'):.0f} kt")
    print("  throttle time share: " + ", ".join(f"{k} % {v:.0%}" for k, v in sorted(share.items(), key=lambda x: -x[1]) if v >= 0.005))
    print(f"  throttle changes {w('throttle_changes_per_hour'):.0f} per game hour, flaps down {w('flaps_share'):.0%} of the time")
    for r in rows:
        print(f"  {r['id']}: {r['airborne_game_min']:5.1f} min {r['fuel_used']:5d} lb ({r['measured_lb_min']} lb/min, rule "
              f"{r['burn_rule_predicted']}), {r['km']:4.0f} km, {r['mean_indicated_kt']:.0f} kt, ground x{r['ground_per_kt']}, {r['ended']}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a: print(__doc__)
    elif a[0] == "summary": summary()
    elif a[0] == "all":
        done = {p.stem for p in OUT.glob("*.json")} if OUT.exists() else set()
        for d in sorted(glob.glob(str(S.logs / "memlog/*/"))):
            if Path(d).name in done: continue
            r = log_flight(d)
            print(f"{Path(d).name}: " + ("too short / no airborne data" if r is None else
                  f"{r['airborne_game_min']} min, {r['fuel_used']} lb, {len(r['changes'])} throttle changes"))
        summary()
    else:
        r = log_flight(a[0]); print(json.dumps({k: v for k, v in r.items() if k != "changes"}, indent=1) if r else "no airborne data")
