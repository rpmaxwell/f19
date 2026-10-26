"""WP-7b equivalence, Takeoff and Attack on the replay fixtures (the snapshots tests/replay/harness feeds them):
OLD phases.Takeoff / Attack with a recording SnapDB (their own reads go through f19_state on the snapshot) vs NEW
f19.pilot.phases with a GameState read from the same snapshot. Every snapshot: same keys (through FakeControls),
override dict, speed target, log lines and internal state. Open loop, as the harness: the recorded memory already
shows what nav's real presses did."""
import pytest

from pilot_equiv import RecSnapDB, product, run_phase, snapshot_world
import harness as H  # noqa: E402
from f19.pilot.phases import Attack, Takeoff

ph = product.phases()
pytestmark = pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")


def old_step(o, db, w, t): return o.step(db, w["s"], w["f"], (w["s"]["x"] / 32, w["s"]["y"] / 32), t, w["ts"])


@pytest.mark.parametrize("run_id", ["1792609731", "1792715432"])
def test_takeoff_replay(run_id):
    R = H.Run.load(run_id); prof = R.route.get("profile", {})
    db = RecSnapDB(R)
    def ticks():
        on = False
        for t, raw in R.snaps():
            db.load(raw, t)
            try: w = snapshot_world(db)
            except RuntimeError: continue
            if not on and w["s"]["alt"] >= 50: continue
            on = True; yield t, w
    kt, ft = float(prof.get("cruise_kt") or 190), float(prof.get("cruise_ft") or 450)
    old, new = ph.Takeoff(ph.Throttle(), kt, ft), Takeoff(kt, ft)
    rep = run_phase(old, new, ticks(), old_step, extra=lambda b: (b.state, b.t0), synthetic=False, db=db)
    assert rep.all_equal, rep.first_diff
    assert new.done and rep.states[-1] == "climb"
    print(f"takeoff replay {run_id}: {rep.ticks} snapshots all equal, {rep.n_keys} key events, states {rep.states}")


@pytest.mark.parametrize("run_id,role", [("1792609731", "primary"), ("1792546698", "primary"), ("1792546698", "secondary")])
def test_attack_replay(run_id, role):
    R = H.Run.load(run_id)
    starts, ends = R.event_times(f"{role} attack start"), R.event_times(f"{role} attack done")
    A = H.build_attacks(R.route, R.mission)[role]
    rec = next(int(e.rsplit("record ", 1)[1].rstrip(")")) for t, e in R.events if e.startswith(f"{role} attack start"))
    A["record"] = A["record"] if A["record"] is not None else rec
    db = RecSnapDB(R)
    def ticks():
        for t, raw in R.snaps(starts[0] - 0.5, (ends[0] + 5) if ends else None):
            db.load(raw, t); yield t, snapshot_world(db, [A["record"]])
    old = ph.Attack(role, A["mode"], A["T"], A["record"], A["launch_km"], A["photo_km"], ph.Throttle())
    new = Attack(role, A["mode"], A["T"], A["record"], A["launch_km"], A["photo_km"])
    rep = run_phase(old, new, ticks(), old_step, synthetic=False, db=db,
                    extra=lambda b: (b.state, b.b_presses, b.space_presses, b.f2_presses, b.bay_presses, b.shots,
                                     tuple(b.locks), b.slot, b.events0, round(b.next_ok, 6)))
    assert rep.all_equal, rep.first_diff
    assert rep.ticks > 30
    print(f"attack replay {run_id} {role} ({A['mode']}): {rep.ticks} snapshots all equal, {rep.n_keys} key events, "
          f"states {rep.states}, log {new.log}")
