"""core.theater against analysis/theaters.py, planner/plan.py, read_mission.py, terrain_3d.py, territory.py, score.py."""
import pytest

from core_legacy import extract
from f19.core.theater import Theater

from f19.gamedata import terrain as _terrain, wld as _wld
from f19.planner import planner as _planner
from f19.rules import scoring as _scoring

th = {"WLD_FILE": _wld.WLD_FILE, "ALIASES": _wld.ALIASES, "code": _wld.code}
plan = {"THEATER_INDEX": _planner.THEATER_INDEX, "game_takeoff_heading": _planner.game_takeoff_heading}
rm = extract("scripts/read_mission.py", "THEATER_INDEX", "takeoff_heading")
t3d = {"THEATER_INDEX": _terrain.THEATER_INDEX}
terr = {"WLD": _wld.WLD_FILE}
score = {"THEATER": _scoring.THEATER}

NAMES = ["CE", "ce", "NC", "nc", "LB", "lb", "PG", "pg", "LIBYA", "Libya", "GULF", "gulf", "Persian Gulf", "PERSIAN_GULF",
         "persian_gulf", "Central Europe", "central_europe", "EUROPE", "North Cape", "north_cape", " ce ", "GULF.WLD",
         "LIBYA.WLD", "CE.WLD", "NC.WLD", "pg.WLD"]
BAD = ["", "XX", "Germany", "WLD", "CE.wld", "LB.WLD.WLD", "G ULF"]


def test_codes():
    for n in NAMES:
        assert Theater.parse(n).value == th["code"](n)
    for n in BAD:
        try: old = th["code"](n)
        except ValueError: old = None
        if old is None:
            with pytest.raises(ValueError): Theater.parse(n)
        else: assert Theater.parse(n).value == old
    assert Theater.parse(Theater.NC) is Theater.NC


def test_per_theater_data():
    assert {t.value for t in Theater} == set(th["WLD_FILE"])
    for t in Theater:
        assert t.wld == th["WLD_FILE"][t.value] == terr["WLD"][t.value]
        assert t.index == plan["THEATER_INDEX"][t.value] == t3d["THEATER_INDEX"][t.value]
        assert Theater.from_index(t.index) is t and rm["THEATER_INDEX"][t.index] == t.value
        assert t.score_mult == score["THEATER"][t.value]
        assert Theater.parse(t.full_name) is t


def test_takeoff_heading():
    for t in Theater:
        for carrier in (False, True):
            h = t.takeoff_heading(carrier)
            assert h == plan["game_takeoff_heading"](t.value, carrier) == rm["takeoff_heading"](t.index, carrier)
    assert Theater.CE.takeoff_heading() == 0 and Theater.LB.takeoff_heading() == 180
    assert Theater.PG.takeoff_heading16(True) == 0x400 and Theater.NC.takeoff_heading16(True) == 0x8400
