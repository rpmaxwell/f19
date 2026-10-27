"""WP-7d: f19.cli.fly.live() and check_route() offline, on fake emulators and readers. Nothing is ever connected.

  check_route   route vs the mission in memory, N/S route switch, takeoff-point distance, --land-now sanity (nav :408-456)
  live          the whole start-up on a replayed flight and a recording fake emulator: retry connect, settle, ALT file
                reset, --plain output, the HUD fallback; SIGINT and SIGTERM (real signals, raised from inside the loop)
                release every key and centre the stick; an exit releases too
"""
import json
import shutil
import signal

import pytest

from pilot_equiv import product  # noqa: F401  (puts tests/replay on sys.path)
import harness as H  # noqa: E402
from f19.cli import fly
from f19.core.clock import ManualClock
from f19.core.types import Attitude, GameState, Position, Route, Systems
from f19.core.units import K
from f19.memory import ReplayReader
from f19.memory import mission as MM

need_fix = pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
KATOWICE = H.FIX / "plan" / "ce_katowice_1021_1458"
RUN = H.FIX / "logs" / "memlog" / "1792609731"


def _state(x, y, alt=0, hdg=0.0, spd=0):
    return GameState(t=0.0, pos=Position(x, y, alt), att=Attitude(hdg), speed_kt=spd, systems=Systems(flags=1, throttle=0), sensitivity=1)


class MemReader:
    def __init__(self): pass
    def memory(self): return b""


@pytest.fixture
def plan(tmp_path, monkeypatch):
    """A copy of the Katowice plan dir as <tmp>/plan/a/route_N.json (+ route_S.json) with its mission_mem.json."""
    d = tmp_path / "plan" / "a"
    d.mkdir(parents=True)
    shutil.copy(KATOWICE / "mission_mem.json", d / "mission_mem.json")
    for n in ("N", "S"): shutil.copy(KATOWICE / "route.json", d / f"route_{n}.json")
    planned = json.loads((d / "mission_mem.json").read_text())
    monkeypatch.setattr(MM, "parse_all", lambda mem, th, world=None: [planned])
    return d


def _args(route, *extra):
    return fly.resolve(fly.parser().parse_args(["--route", str(route), *extra]))


def _runway():
    r = Route.from_route_json(KATOWICE / "route.json").points
    return r[0].xy


@need_fix
def test_check_route_accepts_the_matching_mission(plan):
    a = _args(plan / "route_N.json")
    x, y = _runway()
    fly.check_route(a, MemReader(), _state(x, y, hdg=0.0), world=object(), say=lambda m: None)
    assert a.route == str(plan / "route_N.json")


@need_fix
def test_check_route_refuses_a_route_for_another_mission(plan, monkeypatch):
    other = json.loads((plan / "mission_mem.json").read_text())
    other["slots"] = [dict(t, x=t["x"] + 640) for t in other["slots"]]
    monkeypatch.setattr(MM, "parse_all", lambda mem, th, world=None: [other])
    x, y = _runway()
    with pytest.raises(SystemExit) as e:
        fly.check_route(_args(plan / "route_N.json"), MemReader(), _state(x, y), world=object())
    assert "wrong route file? Not flying it." in str(e.value) and "Katowice" in str(e.value)


@need_fix
def test_check_route_lists_plan_dirs_that_match(plan, monkeypatch):
    sib = plan.parent / "b"; sib.mkdir()
    shutil.copy(plan / "mission_mem.json", sib / "mission_mem.json")
    other = json.loads((plan / "mission_mem.json").read_text())
    other["slots"] = [dict(t, x=t["x"] + 640) for t in other["slots"]]
    own = json.loads((plan / "mission_mem.json").read_text())
    # memory holds two copies: a stale one and the one plan b was made for; plan a is for neither
    stale = json.loads((plan / "mission_mem.json").read_text()); stale["slots"] = [dict(t, y=t["y"] + 64) for t in stale["slots"]]
    (plan / "mission_mem.json").write_text(json.dumps(stale))
    monkeypatch.setattr(MM, "parse_all", lambda mem, th, world=None: [own])
    x, y = _runway()
    with pytest.raises(SystemExit) as e:
        fly.check_route(_args(plan / "route_N.json"), MemReader(), _state(x, y), world=object())
    assert f"Plan dirs matching this mission: {sib}" in str(e.value)


@need_fix
def test_check_route_switches_north_south(plan):
    a = _args(plan / "route_N.json")
    x, y = _runway()
    said = []
    fly.check_route(a, MemReader(), _state(x, y, hdg=180.0), world=object(), say=said.append)
    assert a.route == str(plan / "route_S.json") and "flying route_S.json" in said[0]
    (plan / "route_S.json").unlink()
    a = _args(plan / "route_N.json")
    with pytest.raises(SystemExit) as e:
        fly.check_route(a, MemReader(), _state(x, y, hdg=180.0), world=object())
    assert "there is no route_S.json" in str(e.value)


@need_fix
def test_check_route_refuses_far_from_the_takeoff_point(plan):
    x, y = _runway()
    with pytest.raises(SystemExit) as e:
        fly.check_route(_args(plan / "route_N.json"), MemReader(), _state(x + 12 * K, y), world=object())
    assert "km from the route's takeoff point" in str(e.value)
    fly.check_route(_args(plan / "route_N.json"), MemReader(), _state(x + 9 * K, y), world=object())     # 9 km: fine
    fly.check_route(_args(plan / "route_N.json"), MemReader(), _state(x + 90 * K, y, alt=3000), world=object())   # airborne: no check


@need_fix
def test_check_route_land_now(plan):
    pts = Route.from_route_json(KATOWICE / "route.json").points
    fx = next(q.xy for q in pts if q.label == "Approach fix"); rw = pts[-1].xy
    d = ((rw[0] - fx[0]) ** 2 + (rw[1] - fx[1]) ** 2) ** 0.5
    ux, uy = (rw[0] - fx[0]) / d, (rw[1] - fx[1]) / d
    at = lambda km: _state(rw[0] - ux * km * K, rw[1] - uy * km * K, alt=2000, hdg=0.0)
    a = lambda: _args(plan / "route_N.json", "--auto", "--land-now")
    fly.check_route(a(), MemReader(), at(20), world=object())                                    # 20 km out: fine
    for km in (1, 70, -10):                                                                        # too close, too far, past it
        with pytest.raises(SystemExit) as e:
            fly.check_route(a(), MemReader(), at(km), world=object())
        assert "not starting a landing from here" in str(e.value)


def test_check_route_without_a_route_does_nothing():
    fly.check_route(fly.resolve(fly.parser().parse_args(["--hdg"])), MemReader(), _state(0, 0))


# ------------------------------------------------------------------------------------------------------ live
class LiveEmu(fly.RecordingEmulator):
    """The recording emulator as a context manager with a connection log."""
    def __init__(self, clock):
        super().__init__(clock); self.closed = False
    def __enter__(self): return self
    def __exit__(self, *a): self.closed = True; return False


class World:
    """Fake time + sleep: every sleep advances the wall clock; `on_sleep(t, n)` may act (raise a signal)."""
    def __init__(self, on_sleep=None):
        self.t, self.n, self.on_sleep = 5000.0, 0, on_sleep
    def wall(self): return self.t
    def sleep(self, s):
        self.t += s; self.n += 1
        if self.on_sleep: self.on_sleep(self)


def _live(tmp_path, monkeypatch, plan_args, on_sleep=None, tty=False, hud_factory=None, connect_fails=0, route=KATOWICE / "route.json"):
    rr = ReplayReader(RUN)
    w = World(on_sleep)
    emu = LiveEmu(ManualClock(0.0))
    tries = {"n": 0}
    def connect():
        tries["n"] += 1
        if tries["n"] <= connect_fails: raise TimeoutError("port not answering")
        return emu
    seen = []
    mem = json.loads((KATOWICE / "mission_mem.json").read_text())
    monkeypatch.setattr(MM, "parse_all", lambda m, th, world=None: [mem])
    logs = tmp_path / "logs"
    (logs / "nav").mkdir(parents=True)
    (logs / "nav" / "ALT").write_text("stale")
    args = fly.resolve(fly.parser().parse_args(["--route", str(route), *plan_args]))
    out = []
    def go():
        return fly.live(args, connect_fn=connect, reader_factory=lambda e: rr, say=out.append, sleep=w.sleep, wall=w.wall,
                        logs=logs, tty=tty, hud_factory=hud_factory, world=object())
    return go, emu, w, out, logs, tries


def _released(emu):
    held = set()
    for e in emu.events:
        if e[1] == "down": held.add(e[2])
        elif e[1] == "up": held.discard(e[2])
    return not held and not emu.held and emu.events[-1][1:] == ("tap", "kp5", 0.2)


def _stop_after(sig, secs=40.0):
    def on_sleep(w):
        if w.t > 5000.0 + secs and not getattr(w, "fired", False):
            w.fired = True; signal.raise_signal(sig)
    return on_sleep


@need_fix
@pytest.mark.parametrize("sig", [signal.SIGINT, signal.SIGTERM])
def test_live_signal_releases_the_keys(tmp_path, monkeypatch, sig):
    """A real SIGINT / SIGTERM in the middle of the loop: KeyboardInterrupt, every key up, stick centred, sinks closed."""
    go, emu, w, out, logs, tries = _live(tmp_path, monkeypatch, ["--hdg", "--alt", "1000"], on_sleep=_stop_after(sig), connect_fails=2)
    old = signal.getsignal(signal.SIGTERM)
    with pytest.raises(KeyboardInterrupt):
        go()
    assert tries["n"] == 3 and any("retrying" in m for m in out)          # connect retried
    assert emu.closed and _released(emu)
    assert signal.getsignal(signal.SIGTERM) is old                         # handlers restored
    assert not (logs / "nav" / "ALT").exists()                             # stale ALT file removed
    assert list((logs / "nav").glob("*.csv"))


@need_fix
def test_live_signal_during_settle_releases_too(tmp_path, monkeypatch):
    """SIGTERM while the start-up settle is still waiting (before any runner exists): keys are released."""
    go, emu, w, out, logs, tries = _live(tmp_path, monkeypatch, ["--hdg"], on_sleep=_stop_after(signal.SIGTERM, secs=2.0))
    with pytest.raises(KeyboardInterrupt):
        go()
    assert emu.closed and _released(emu)
    assert any("getting bearings" in m for m in out)
    assert not list((logs / "nav").glob("*.csv"))                          # never got to flying


@need_fix
def test_live_main_returns_130_on_interrupt(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fly, "live", lambda a: (_ for _ in ()).throw(KeyboardInterrupt))
    assert fly.main(["--hdg"]) == 130
    assert "keys released" in capsys.readouterr().out


@need_fix
def test_live_plain_display_prints_nav_text(tmp_path, monkeypatch, capsys):
    go, emu, w, out, logs, tries = _live(tmp_path, monkeypatch, ["--hdg", "--alt", "1000", "--plain"], on_sleep=_stop_after(signal.SIGINT, 12.0))
    with pytest.raises(KeyboardInterrupt):
        go()
    text = capsys.readouterr().out
    assert "F-19 NAV" in text and "FLY HEADING" in text and "game waypoints (exact):" in text and "off route" in text
    assert "nearest radars:" in text and "[HDG HOLD]" in text
    assert "\033[H" not in text                                           # no clear-screen codes when not a tty


@need_fix
def test_live_hud_failure_falls_back_to_plain(tmp_path, monkeypatch, capsys):
    class Broken:
        stats = None
        def write(self, tf): raise RuntimeError("terminal gone")
        def flush(self, timeout=None): return True
        def close(self, timeout=10.0): return True
    go, emu, w, out, logs, tries = _live(tmp_path, monkeypatch, ["--hdg", "--alt", "1000"], on_sleep=_stop_after(signal.SIGINT, 12.0),
                                         tty=True, hud_factory=lambda args, route: Broken())
    with pytest.raises(KeyboardInterrupt):
        go()
    assert sum("display failed" in m for m in out) == 1
    assert "FLY HEADING" in capsys.readouterr().out


@need_fix
def test_live_refuses_a_wrong_route_before_sending_anything(tmp_path, monkeypatch):
    go, emu, w, out, logs, tries = _live(tmp_path, monkeypatch, ["--hdg"])
    other = json.loads((KATOWICE / "mission_mem.json").read_text())
    other["slots"] = [dict(t, x=t["x"] + 640) for t in other["slots"]]
    monkeypatch.setattr(MM, "parse_all", lambda m, th, world=None: [other])
    with pytest.raises(SystemExit):
        go()
    assert emu.closed and _released(emu)
    # settle sends its hands-off releases and the centring tap only: no flight key
    assert all(e[1] in ("up", "tap", "release_arrows") and (e[1] != "tap" or e[2] == "kp5") for e in emu.events)


@need_fix
def test_live_no_keys_sends_nothing_but_records_the_stream(tmp_path, monkeypatch):
    go, emu, w, out, logs, tries = _live(tmp_path, monkeypatch, ["--hdg", "--alt", "1000", "--no-keys", "--plain"],
                                         on_sleep=_stop_after(signal.SIGINT, 20.0))
    with pytest.raises(KeyboardInterrupt):
        go()
    assert emu.events == [] and any("NO key is sent" in m for m in out)
    ks = list((logs / "nav").glob("*.keys.txt"))
    assert len(ks) == 1 and ks[0].read_text().strip()


# ------------------------------------------------------------------------------------------------------ eject
def test_eject_order_reaches_the_emulator_as_shift_f10_and_the_runner_exits(monkeypatch):
    """Director Order(EJECT, exit) -> Runner._direct -> Controls.apply -> the (recording) emulator: one shifted F10
    scancode (0x44), then the runner exits with the director's text and has released every key."""
    from f19.core.types import Action, Command
    from f19.pilot.director import Order
    from test_pilot_runner import _runner, _state
    clock = ManualClock(1000.0)
    r, emu = _runner(lambda n: _state(hdg=(n * 4.0) % 360), clock)
    r.allow_eject = True                         # fly --allow-eject: without it the eject is suppressed (test_eject_gate.py)

    class Provider:
        def __init__(self, inner): self.inner = inner
        version = property(lambda s: s.inner.version)
        def current(self): return self.inner.current()
        def on_tick(self, state, dec, ctx, event_list):
            if self.n >= 5:
                return Order(Command(actions=(Action.EJECT,), done=True), exit="director: ejected in enemy territory (test)", events=("EJECT",))
        n = 0
    prov = Provider(r.routes)
    r.routes = prov
    n = 0
    msg = None
    for _ in range(40):
        prov.n = n; n += 1
        res = r.tick()
        if res.exit: msg = res.exit; break
    assert msg == "director: ejected in enemy territory (test)"
    keys = emu.take()
    assert ("scan", 0x44, True, 0.2) in keys
    assert keys.count(("scan", 0x44, True, 0.2)) == 1
    assert not emu._held and ("tap", "kp5", 0.2) in keys[keys.index(("scan", 0x44, True, 0.2)):]
