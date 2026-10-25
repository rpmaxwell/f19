"""f19.telemetry formats without data files: per-column formatting as nav.py does it, events lines, the memlog layout,
TelemetryFrame <-> nav CSV values."""
import json

import pytest

from f19.core.types import Attitude, Command, Frame, GameState, Position, Systems
from f19.telemetry import (EXTENDED_COLUMNS, NAV_COLUMNS, Event, MemlogLayout, Snapshot, TelemetryFrame,
                           format_value, frame_from_values, parse_value)
from f19.telemetry.navcsv import KIND, format_row, parse_row


def test_columns():
    assert len(NAV_COLUMNS) == 32 and NAV_COLUMNS[0] == "t" and NAV_COLUMNS[-1] == "m_action"
    assert EXTENDED_COLUMNS[:32] == NAV_COLUMNS
    assert EXTENDED_COLUMNS[32:] == ("route_want", "behavior", "plan_version")
    assert set(KIND) == set(EXTENDED_COLUMNS)


@pytest.mark.parametrize("col,v,s", [
    ("t", 1792546701.25, "1792546701.25"), ("x", 3392.4, "3392"), ("x", -0.4, "-0"), ("alt", 487, "487"),
    ("hdg", 359.96, "360.0"), ("want", 13.7, "13.7"), ("xtrack_km", -25.3, "-25.30"), ("flags", 3, "0x3"),
    ("flags", 0, "0x0"), ("locked", 65535, "65535"), ("press", "UP!", "UP!"), ("vs", None, ""),
    ("missile", 0, "0"), ("route_want", 195.04, "195.0"), ("plan_version", 2, "2")])
def test_format_value_as_nav(col, v, s):
    assert format_value(col, v) == s
    assert format_value(col, parse_value(col, s)) == s


def test_parse_value_types_and_fallback():
    assert parse_value("alt", "487") == 487 and isinstance(parse_value("t", "1.50"), float)
    assert parse_value("flags", "0x1f") == 0x1F
    assert parse_value("alt", "") is None
    assert parse_value("alt", "n/a") == "n/a"          # unparsable stays a str and is written back as is
    assert format_value("alt", "n/a") == "n/a"


def test_short_row():
    vals, width = parse_row(["1.25", "", "", "", "", "", "", "", "", "", "sens 3->ins"], NAV_COLUMNS)
    assert width == 11 and vals["press"] == "sens 3->ins" and vals["x"] is None
    assert format_row(vals, NAV_COLUMNS, width) == ["1.25"] + [""] * 9 + ["sens 3->ins"]
    tf = TelemetryFrame.sensitivity_row(1.25, 3)
    assert format_row(tf.values(), NAV_COLUMNS, tf.row_width) == ["1.25"] + [""] * 9 + ["sens 3->ins"]


def test_event_lines():
    e = Event.parse("1792546720 takeoff done")
    assert e == Event(1792546720.0, "takeoff done") and e.line() == "1792546720 takeoff done"
    assert Event(1792546720.4, "x").line() == "1792546720 x"            # nav: f"{now:.0f} ..."
    for raw in ("no prefix here", "0123 leading zero", "", "12345"):
        assert Event.parse(raw).t is None and Event.parse(raw).line() == raw


def test_memlog_layout_json_and_read():
    lay = MemlogLayout(66592, [("egame_data", 312352, 361504), ("world_CE", 101632, 104704)])
    nav_text = json.dumps({"offset": 66592, "windows": [("egame_data", 312352, 361504), ("world_CE", 101632, 104704)]},
                          indent=1)                                      # what nav.MemLog's json.dump writes
    assert lay.to_json() == nav_text and MemlogLayout.from_json(nav_text) == lay
    assert lay.size == 49152 + 3072
    data = bytes(range(256)) * (lay.size // 256)
    assert lay.read(data, 312352, 4) == data[:4]
    assert lay.read(data, 101632, 2) == data[49152:49154]
    assert lay.read(data, 0, 4) == b"\0" * 4                             # outside every window
    assert lay.window("world_CE") == (101632, 104704, 49152)
    assert Snapshot(1792715434.849, b"").name == "snap_1792715434.85.bin"


def _frame():
    st = GameState(t=812.0, pos=Position(3394.2, 20851.0, 487), att=Attitude(0.4), speed_kt=324,
                   systems=Systems(flags=1, throttle=80, fuel_lb=9784, locked=65535))
    return Frame(812.0, st, "FighterWatch", Command(heading=13.7, fighter="evade"), plan_version=2, leg=2)


def test_values_from_frame_and_overrides():
    tf = TelemetryFrame(1792816683.36, _frame(), nav={"xtrack_km": 0.03, "phase": "route", "flags": None},
                        route_heading=21.0)
    v = tf.values()
    row = dict(zip(EXTENDED_COLUMNS, format_row(v, EXTENDED_COLUMNS)))
    assert row["t"] == "1792816683.36" and row["x"] == "3394" and row["alt"] == "487" and row["hdg"] == "0.4"
    assert row["want"] == "13.7" and row["leg"] == "2" and row["fighter"] == "evade" and row["fuel"] == "9784"
    assert row["flags"] == ""                       # nav dict wins, None = blank (nav without --auto)
    assert row["route_want"] == "21.0" and row["behavior"] == "FighterWatch" and row["plan_version"] == "2"
    assert row["phase"] == "route" and row["xtrack_km"] == "0.03" and row["m_action"] == ""


def test_frame_from_values():
    vals, _ = parse_row("1.00,3394,20851,487,324,0.4,2,13.7,,,,,,,,,route,,,,0x1,80,,65535,9784,,,evade".split(","),
                        NAV_COLUMNS)
    f = frame_from_values(vals)
    assert f.state.pos == Position(3394.0, 20851.0, 487) and f.state.speed_kt == 324
    assert f.command.heading == 13.7 and f.command.fighter == "evade" and f.behavior == "route" and f.leg == 2
    assert f.state.systems.flags == 1 and f.state.systems.fuel_lb == 9784 and f.t is None
    assert frame_from_values(parse_row(["1.0"] + [""] * 9 + ["sens 3->ins"], NAV_COLUMNS)[0]) is None
