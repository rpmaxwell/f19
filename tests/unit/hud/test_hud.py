"""The Hud on synthetic frames (no fixtures): drawing, keys, the log, the sink and the terminal; the CLI on a fixture."""
import io
import re

import numpy as np
import pytest

from f19.core.types import Attitude, Command, Frame, GameState, Position, Systems, Threat, ThreatKind
from f19.hud import Hud, HudContext, HudFrame, HudSink, Screen, TextWidget, register
from f19.hud import layout as L
from f19.hud.data import History
from f19.hud.terminal import NullTerminal, Terminal
from f19.telemetry import TelemetryFrame
from hud_fixtures import replay_fixtures  # noqa: F401  (fixture)

T0 = 1792609800.0


def state(clock=100, fuel=8000, x=10000.0, y=12000.0, threats=(), **kw):
    return GameState(t=clock * 2, pos=Position(x, y, 700), att=Attitude(90.0, 2.0, -5.0), speed_kt=300,
                     systems=Systems(throttle=60, fuel_lb=fuel, loadout=((17, 1), (3, 2), (0, 0), (0, 0))), opp=2, emv=30,
                     net_alert=0, radar_ids=0, vis_ids=0, threats=threats, waypoints=((9000, 11000), (12000, 9000), (0, 0), (0, 0)),
                     wp_index=1, **kw)


SITE = Threat(ThreatKind.SITE, 4, 10500, 12500, type=1, alert=90, flags=2, on=True)
PLANE = Threat(ThreatKind.AIRCRAFT, 3, 11000, 11000, 5000, type=0, alert=0, name="MIG-21")


@pytest.fixture
def ctx():
    return HudContext(theater="CE", grid=np.full((16, 16), 8, np.uint8), radar_systems={1: ("SA-2", "P", 37)},
                      mission={"radars": [{"record": 4, "place": "Testburg"}]})


def frame(t=T0, **kw):
    ev = kw.pop("events", None)
    return HudFrame(t, state(**kw), {"phase": "cruise", "banner": "HELLO\nWATCH OUT !!!"}, ev)


def text(scr): return "\n".join(scr.rows())


def test_render_before_and_after_a_frame(egame, ctx):
    hud = Hud(ctx)
    assert hud.render(120, 40) is None
    hud.update(frame(threats=(SITE, PLANE)))
    scr = hud.render(160, 50)
    s = text(scr)
    for want in ("─ map ", "─ nav cruise", "HELLO", "WATCH OUT !!!", "alt   700 ft", "thr 60%", "Testburg", "SA-2",
                 "MIG-21", "1 hostile", "+1 tank (1900)", "─ score CE", "[f19] 0:map*"):
        assert want in s, want
    assert scr.rows()[-1].rstrip().endswith(__import__("time").strftime("%H:%M:%S", __import__("time").localtime(T0)))


def test_keys(egame, ctx):
    hud = Hud(ctx)
    hud.update(frame())
    m = hud.widgets["map"]
    hud.handle("p")
    assert m.overlays == set("tgp")
    hud.handle("++f")
    assert m.zoom == 0 and m.fit
    hud.handle("\t")
    assert hud.focus == "nav"
    hud.handle("z")
    assert list(hud.rects(160, 50)) == ["nav"]
    hud.handle("zl")
    assert hud.layout_index == 1 and "log" in hud.rects(160, 50)
    hud.handle("?")
    assert "keys: t territory" in text(hud.render(160, 50))


def test_event_log_and_new_flight(egame, ctx):
    hud = Hud(ctx, history_period=0.0)
    evs = ((0, 0, 0, 8, 0, 0), (40, 10, 10, 9, 0, 0), (50, 10, 10, 10, 0x80, 3))
    hud.update(frame(events=evs[:2]))
    hud.update(frame(T0 + 1, events=evs))
    msgs = [m for _, m, _ in hud.history.log]
    assert msgs == ["[game 0:00:00] takeoff / end", "[game 0:01:40] photo PRIMARY"]
    assert "primary DONE" in text(hud.render(160, 50))
    hud.update(frame(T0 + 2, events=evs[:1]))                  # shorter list: a new flight
    assert hud.history.events == list(evs[:1])


def test_history_period_and_ratios():
    h = History(period=1.0)
    for k in range(40):
        h.poll(HudFrame(T0 + k * 0.5, state(clock=100 + k, fuel=8000 - 5 * k)))
    assert len(h.fuel_hist) == 20                              # every other frame: 1 s apart
    assert h.time_ratio() == pytest.approx(4.0)                # 2 ticks (4 game s) per real s
    assert h.burn_measured() == pytest.approx(150.0)           # 5 lb per tick (2 game s)


def test_from_telemetry(egame, ctx):
    st = state()
    tf = TelemetryFrame(T0, Frame(200, st, "attack", Command(heading=123.0, banner="GO"), leg=3), nav={"xtrack_km": 0.5, "press": "x"})
    fr = HudFrame.from_telemetry(tf)
    assert fr.state is st and fr.view == {"leg": 3, "want": 123.0, "phase": "attack", "banner": "GO", "xtrack_km": 0.5}
    hud = Hud(ctx)
    hud.update(tf)                                             # a TelemetryFrame is converted
    assert "─ nav attack" in text(hud.render(160, 50))
    hud.update(TelemetryFrame(T0 + 1))                         # no state (a lone event): histories only
    assert hud.frame.t == T0


def test_custom_widget_and_layout(egame, ctx, tmp_path):
    @register
    class Hello(TextWidget):
        name = "hello-test"

        def lines(self, view, w, h): return [f"alt is {view.c['alt']}"]
    p = tmp_path / "hello.toml"
    p.write_text('status_bar = false\n[[layout]]\ncolumns = [{ widget = "hello-test", size = 20 }, { widget = "map" }]\n')
    hud = Hud(ctx, L.load(p))
    hud.update(frame())
    scr = hud.render(80, 20)
    assert "alt is 700" in scr.rows()[1] and "─ hello-test " in scr.rows()[0]
    assert "[f19]" not in text(scr)
    with pytest.raises(KeyError, match="nope"):
        Hud(ctx, L.parse('[[layout]]\nwidget = "nope"\n'))


def test_sink_paces_and_survives(egame, ctx):
    now = [0.0]
    term = NullTerminal((120, 40), keys="a")
    sink = HudSink(Hud(ctx), term, clock=lambda: now[0])
    sink.write(frame())
    assert term.frames == 1 and "a" in sink.hud.widgets["map"].overlays
    now[0] = 0.1; sink.write(frame(T0 + 0.1))
    assert term.frames == 1 and sink.skipped == 1
    now[0] = 0.3; sink.write(frame(T0 + 0.3))
    assert term.frames == 2
    sink.hud.widgets["nav"].lines = lambda *a: 1 / 0
    now[0] = 1.0; sink.write(frame(T0 + 1))                     # a broken widget turns the display off, no raise
    assert sink.failed and sink.stats.errors == 1
    sink.write(frame(T0 + 2))
    assert sink.stats.dropped_after_error == 1


def test_terminal_draw_without_tty():
    out = io.StringIO()
    term = Terminal(out=out, inp=io.StringIO())
    scr = Screen(4, 2); scr.put(0, 0, "ab", (1, 2, 3))
    term.draw(scr)
    s = out.getvalue()
    assert s.startswith("\033[?1049h") and "\033[38;2;1;2;3;" in s and term.keys() == ""
    term.close()
    assert out.getvalue().endswith("\033[?1049l")
    assert re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", scr.emit()) == "ab      "   # 2 rows of 4


def test_cli_headless(replay_fixtures, capsys):
    from f19.cli import hud as cli
    run = replay_fixtures / "logs" / "memlog" / "1792546698"
    route = replay_fixtures / "plan" / "ce_test_1019_2337" / "route.json"
    assert cli.main(["--replay", str(run), "--route", str(route), "--speed", "0", "--headless", "120x40"]) == 0
    out, err = capsys.readouterr()
    assert "REPLAY 1792546698  snapshot 313/313" in out and "313 frames drawn" in err
