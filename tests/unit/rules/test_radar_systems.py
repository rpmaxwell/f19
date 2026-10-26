"""The radar-system table (WP-7d): one copy in gamedata + rules.radar.radar_systems, equal to the three it replaced."""
import pytest

import product
from conftest import S


def _old_hud_table():
    """cli/hud.py's pre-WP-7d radar_systems(), verbatim."""
    from f19.rules import radar as rg
    briefing = {1: ("SA-2", "P", 37), 2: ("SA-5", "P", 43), 3: ("SA-8B", "P", 39), 4: ("SA-10", "D", 140),
                5: ("SA-11", "P", 62), 6: ("SA-12", "D", 108), 7: ("SA-13", "P", 23),
                8: ("SA-N-4", "D", 50), 9: ("SA-N-5", "P", 28), 11: ("SA-N-7", "P", 62), 21: ("OTH", "D", 156)}
    out = dict(briefing)
    for t in range(1, 16):
        if t not in out:
            _, _, dop = rg.radar_spec(t)
            out[t] = (rg.weapon_name(t), "D" if dop else "P", rg.sam_range_km(t))
    return out


def test_radar_systems_equal_old_hud_copy(egame):
    from f19.rules.radar import radar_systems
    assert radar_systems() == _old_hud_table()


def test_radar_systems_equal_read_mission_radar(egame):
    if not (S.game_dir / "CE.WLD").exists(): pytest.skip("no game install")
    import read_mission
    from f19.rules.radar import radar_systems
    assert radar_systems() == read_mission.RADAR


def test_planner_name_table_is_gamedata_table(egame):
    from f19.gamedata.radar_systems import BRIEFING_RADARS, RADAR_NAMES
    from f19.planner import world
    assert RADAR_NAMES == {k: v[0] for k, v in BRIEFING_RADARS.items()}
    old = {1: "SA-2", 2: "SA-5", 3: "SA-8B", 4: "SA-10", 5: "SA-11", 6: "SA-12", 7: "SA-13", 8: "SA-N-4",
           9: "SA-N-5", 11: "SA-N-7", 21: "OTH"}
    assert RADAR_NAMES == old
    kinds = world.system_kinds()
    assert all(kinds[n] == k for k, n in old.items())
