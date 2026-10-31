"""Planner side of the trackcam rule: the world's record table (checked against a recorded flight's table) and the
acquisition window of an attack run-in (rules.trackcam; docs/design/target_acquisition_task.md)."""
import copy

import numpy as np
import pytest

from conftest import S, need
from planner_golden import golden
from test_planner_sa14 import CARLSBAD, WITTENBERG, planner

K = 64


@pytest.fixture(scope="module")
def pl(egame):
    p = planner("veteran")                                           # the golden mission predates "record": give the targets theirs
    for t, r in zip(p.M["targets"], (2, 1)): t["record"] = r
    return p


def test_record_table_matches_the_recorded_game_table(pl):
    """CE: record 0 scratch, 1 and 2 the target slots, k >= 3 the .WLD places (memlog 1793126949, ds:80AA)."""
    from f19.memory import reader as R
    rr = R.ReplayReader(need(S.logs / "memlog" / "1793126949", "memlog 1793126949"))
    _, s = next(rr.states()); cam = R.read_trackcam_view(rr.emu)
    ours = {k: (x, y) for k, x, y, _ in pl.W.trackcam_records()}
    assert len(cam.records) == 75 and max(ours) <= 74
    assert all(ours[k] == (x, y) for k, x, y, _ in cam.records if k >= 3 and k in ours and k < 75)
    assert sum(1 for k in ours if k >= 3) == 72
    assert ours[0] == (0, 0)


def test_window_for_a_clear_target_and_none_for_a_buried_one(pl):
    t = dict(pl.M["targets"][0]); T = np.array([t["x"], t["y"]], float)
    u = np.array([0.0, -1.0])                                        # flying north at it
    w = pl.acquisition_window(t, T, u, 0.0, 14.0, 20.0)
    assert w is not None and w["ok"] and w["len_km"] >= 3.0 and w["presses"] <= 8
    # bury the target under nine nearer records in the cone: no window
    pl2 = planner("veteran")
    for tt, r in zip(pl2.M["targets"], (2, 1)): tt["record"] = r
    near = [(100 + i, float(T[0]) + 20 * i, float(T[1]) + (3 + i) * K, 0) for i in range(9)]
    pl2.W._cam_records = tuple(r for r in pl.W.trackcam_records() if r[0] < 99) + tuple(near)
    w2 = pl2.acquisition_window(t, T, u, 0.0, 14.0, 20.0)
    assert w2 is not None and not w2["ok"]


def test_attack_pieces_carry_the_window(pl):
    t = next(x for x in pl.M["targets"] if x["role"] == "primary")
    mode = pl.attack_mode(t)[0]
    el = dict(t="target", xy=(float(t["x"]), float(t["y"])), role="primary", label="PRIMARY: x")
    pieces = [p for p in (pl.attack_piece(el, t, mode, float(b)) for b in range(0, 360, 30)) if p]
    assert pieces
    assert all(p["pts"][0][2]["acq"]["ok"] for p in pieces if "acq" in p["pts"][0][2])
