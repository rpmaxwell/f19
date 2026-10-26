"""WP-7c: `f19.cli.fly --replay <memlog> --dry-run --director` on the recorded flights: the whole stack with the
FlightDirector as the route provider (planned inline, real planner, nothing sent anywhere). The recorded memory is open
loop: the plane keeps flying the recorded track whatever route the director picks, so this checks that the plumbing
holds (a new route is picked up, nothing is left held, the CSV carries plan_version), not that the new route is good."""
import csv

import pytest

from test_pilot_runner import RUNS, _args, need_fix
from f19.cli import fly
from f19.core.types import WaypointKind, WaypointStatus


@need_fix
@pytest.mark.parametrize("run_id", list(RUNS))
def test_dry_run_with_the_director(run_id, tmp_path, egame):
    res = fly.dry_run(_args(run_id, tmp_path, director=True))
    runner, d = res["runner"], res["runner"].routes
    assert res["ticks"] > 200 and res["in_flight"] == res["ticks"]
    held = set()
    for e in res["keys"]:
        if e[1] == "down": held.add(e[2])
        elif e[1] == "up": held.discard(e[2])
    assert not held
    rows = list(csv.reader(open(tmp_path / f"{run_id}.csv")))
    body = [dict(zip(rows[0], r)) for r in rows[1:] if len(r) == len(rows[0])]
    versions = [int(r["plan_version"]) for r in body]
    assert versions == sorted(versions) and versions[0] == 0 and max(versions) == d.version     # every version reaches the frames
    print(f"\n{run_id}: plan versions {sorted(set(versions))}; " + "; ".join(f"v{h.version} {h.candidate} ({h.reason}, {h.plan_s:.1f} s)" for h in d.history[1:]))
    assert runner.ctx.route is d.current()
    ev = (tmp_path / f"{run_id}.events.txt").read_text()
    assert ("plan v" in ev) == (d.version > 0)
    assert d.plan.status(WaypointKind.TAKEOFF) is WaypointStatus.REACHED


@need_fix
def test_director_needs_a_plan_directory(tmp_path, egame):
    with pytest.raises(SystemExit, match="--director"):
        fly.dry_run(_args("1792715432", tmp_path, director=True, route=None, auto=False, hdg=True, alt=None))
