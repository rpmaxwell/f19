"""WP-7c: the worker-thread hand-off (ThreadExecutor) with a router the test holds: on_tick never waits for the planner,
the pilot keeps the current route while it plans, the finished plan is adopted by a later tick, a planner crash is
contained, and the worker is a daemon (a hung planner cannot hold the process)."""
import threading
import time

import pytest

from director_fakes import Dec, FakeRouter, fake_planner, golden_reference, state_at
from f19.pilot.director import FlightDirector, ThreadExecutor


@pytest.fixture(scope="module")
def gold(egame):
    ref, mission, _ = golden_reference()
    return ref, mission


def _wait(cond, secs=5.0):
    t0 = time.monotonic()
    while not cond():
        if time.monotonic() - t0 > secs: return False
        time.sleep(0.005)
    return True


def test_on_tick_does_not_wait_for_a_slow_planner(gold):
    ref, mission = gold
    gate, started = threading.Event(), threading.Event()

    def rule(goals, pose, b):
        started.set(); gate.wait(10); return 5000.0
    d = FlightDirector(mission, ref, fake_planner(), route_fn=FakeRouter(rule), executor=ThreadExecutor(), off_route_km=-1.0)
    st = lambda t: state_at(ref, 10, t=t)
    d.on_tick(st(1000), Dec("missile"))
    t0 = time.monotonic(); d.on_tick(st(1001), Dec("fly_leg")); dt = time.monotonic() - t0
    assert dt < 0.2 and d.busy and started.wait(2) and d.version == 0
    route0 = d.current()
    for k in range(20):                                           # the pilot keeps ticking on the old route
        t0 = time.monotonic(); assert d.on_tick(st(1002 + k), Dec("fly_leg")) is None; assert time.monotonic() - t0 < 0.2
        assert d.current() is route0
    gate.set()
    assert _wait(lambda: d._job is not None and d._job.done())
    o = d.on_tick(st(1030), Dec("fly_leg"))                       # the next tick adopts it
    assert d.version == 1 and d.current() is not route0 and o.events[0].startswith("plan v1") and not d.busy


def test_only_one_job_at_a_time_and_new_triggers_mark_it_stale(gold):
    ref, mission = gold
    gate = threading.Event()
    r = FakeRouter(lambda g, p, b: (gate.wait(10), 5000.0)[1])
    d = FlightDirector(mission, ref, fake_planner(), route_fn=r, executor=ThreadExecutor(), off_route_km=-1.0, min_gap_s=1.0)
    st = lambda t: state_at(ref, 10, t=t)
    d.on_tick(st(1000), Dec("missile")); d.on_tick(st(1001), Dec("fly_leg"))
    d.on_tick(st(1005), Dec("terrain_escape")); d.on_tick(st(1006), Dec("fly_leg"))        # a new deviation while planning
    assert d._stale and d.busy
    gate.set()
    assert _wait(lambda: d._job.done())
    o = d.on_tick(st(1010), Dec("fly_leg"))
    assert d.version == 0 and o.events[0].startswith("plan discarded")
    for k in range(200):                                          # the restarted job (from the newer state) lands soon after
        d.on_tick(st(1011 + k * 0.01), Dec("fly_leg"))
        if d.version == 1: break
        time.sleep(0.01)
    assert d.version == 1 and "deviation ended" in d.history[-1].reason and d.history[-1].t >= 1006


def test_a_planner_crash_in_the_worker_is_contained(gold):
    ref, mission = gold
    d = FlightDirector(mission, ref, fake_planner(), route_fn=FakeRouter(lambda g, p, b: KeyError("bug")), executor=ThreadExecutor(), off_route_km=-1.0)
    st = lambda t: state_at(ref, 10, t=t)
    d.on_tick(st(1000), Dec("missile")); outs = [d.on_tick(st(1001), Dec("fly_leg"))]
    assert _wait(lambda: d._job is None or d._job.done())
    outs.append(d.on_tick(st(1002), Dec("fly_leg")))
    assert d.version == 0 and d.current().version == 0 and not d.terminal
    assert any(o and o.events[0].startswith("plan kept") and "KeyError" in o.events[0] for o in outs)


def test_worker_threads_are_daemons(gold):
    ref, mission = gold
    gate = threading.Event()
    d = FlightDirector(mission, ref, fake_planner(), route_fn=FakeRouter(lambda g, p, b: (gate.wait(10), 1.0)[1]), executor=ThreadExecutor(), off_route_km=-1.0)
    d.on_tick(state_at(ref, 10, t=1000), Dec("missile")); d.on_tick(state_at(ref, 10, t=1001), Dec("fly_leg"))
    assert _wait(lambda: any(t.name == "f19-director-plan" for t in threading.enumerate()))
    assert all(t.daemon for t in threading.enumerate() if t.name == "f19-director-plan")
    gate.set()
