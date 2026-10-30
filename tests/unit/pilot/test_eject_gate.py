"""The eject gate: Action.EJECT (Shift+F10) goes out by default and is dropped with fly --no-eject. Suppressed, the
flight goes on (no exit, no scancode), and the mission log and banner say so. All offline: a fake reader and a recording
emulator, with a route provider whose on_tick hands back the director's eject Order."""
import dataclasses

from f19.cli import fly
from f19.core.clock import ManualClock
from f19.core.types import Action, Command, Route, RoutePoint
from f19.pilot.controls import F10
from f19.pilot.director import Order

from test_pilot_runner import FakeReader, FakeReads, KeyLog, _state

SCAN_EJECT = ("scan", F10, True, 0.2)


def _runner(argv, order_at):
    """A Runner on a fake plane whose provider returns the eject Order on tick `order_at`."""
    clock = ManualClock(1000.0)
    a = fly.resolve(fly.parser().parse_args(["--hdg", "--alt", "1000"] + argv))
    a.dry_run = True
    fn = lambda n: _state(hdg=0.0)
    reader, emu = FakeReader(fn), KeyLog()
    route = Route((RoutePoint(10000.0, 20000.0, "start"), RoutePoint(10000.0, 10000.0, "WP"), RoutePoint(14000.0, 6000.0, "Landing")))
    r = fly.build(a, reader, emu, clock, fn(0), sleep=lambda s: clock.advance(s), wall=clock.now, say=lambda m: None, route=route)
    r.reads = r.arb.reads = FakeReads(reader)
    ticks = {"n": 0}

    def on_tick(state, dec, ctx, events):
        ticks["n"] += 1
        if ticks["n"] != order_at: return None
        return Order(Command(actions=(Action.EJECT,), banner="  DIRECTOR: BAILING OUT (enemy territory): no landing", done=True),
                     exit="director: ejected in enemy territory (no landing)", events=("EJECT in enemy territory: no landing",))
    r.routes.on_tick = on_tick
    return r, emu


def test_argument_defaults_on():
    assert fly.parser().parse_args([]).allow_eject is True
    assert fly.parser().parse_args(["--allow-eject"]).allow_eject is True
    assert fly.parser().parse_args(["--no-eject"]).allow_eject is False


def test_eject_is_suppressed_with_no_eject():
    r, emu = _runner(["--no-eject"], order_at=3)
    assert r.allow_eject is False
    results = [r.tick() for _ in range(8)]
    keys = emu.take()
    assert SCAN_EJECT not in keys and not any(e[0] == "scan" and e[1] == F10 for e in keys)
    assert all(res.exit is None for res in results)                          # the flight goes on: no exit at all
    log = results[2].events
    assert any("EJECT in enemy territory" in e.text for e in log) and any("EJECT suppressed" in e.text for e in log)
    assert "WOULD EJECT" in results[2].decision.command.banner and Action.EJECT not in results[2].decision.command.actions
    assert results[2].decision.command.done is None and results[2].decision.exit is None
    assert results[3].decision.command.banner != results[2].decision.command.banner          # only that tick


def test_eject_goes_out_by_default():
    r, emu = _runner([], order_at=3)
    assert r.allow_eject is True
    results = [r.tick() for _ in range(3)]
    keys = emu.take()
    assert keys.count(SCAN_EJECT) == 1                                       # exactly one shifted F10
    assert results[2].exit and results[2].exit.startswith("director: ejected") and not emu._held
    assert not any("suppressed" in e.text for e in results[2].events)


def test_gate_leaves_other_actions_and_commands_alone():
    r, _ = _runner(["--no-eject"], order_at=10 ** 6)
    from f19.pilot.arbiter import Decision
    cmd = Command(actions=(Action.FLARE, Action.EJECT, Action.CHAFF), heading=40.0, done=True)
    dec = Decision(command=cmd, overrides=Command(), want=40.0, route_want=40.0, phase="route", behavior="director",
                   exit="director: ejected")
    out = r._gate_eject(dec)
    assert out.command.actions == (Action.FLARE, Action.CHAFF) and out.command.heading == 40.0 and out.exit is None
    plain = Decision(command=Command(actions=(Action.FLARE,)), overrides=Command(), want=0.0, route_want=0.0, phase="route",
                     behavior="fly_leg")
    before = dataclasses.replace(plain.command)
    assert r._gate_eject(plain).command == before and plain.events == []
