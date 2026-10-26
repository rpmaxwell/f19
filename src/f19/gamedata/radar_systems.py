"""The radar-system table: radar code (target record +6) -> (system name, "P" pulse / "D" doppler, range km).

BRIEFING_RADARS are the codes the briefing map draws (CE / NC circles; ranges measured there). The other codes (SA-N-6 10,
the Gulf's / Libya's Hawk 12, Rapier 13, Tiger 14, Seacat 15) come from the game's own tables, which needs EGAME decoded
through the rules: `f19.rules.radar.radar_systems()` completes this table. One copy of what used to be
scripts/read_mission.RADAR, cli/hud.py's BRIEFING_RADARS and planner/world.py's _RADAR_NAMES.
"""
BRIEFING_RADARS = {1: ("SA-2", "P", 37), 2: ("SA-5", "P", 43), 3: ("SA-8B", "P", 39), 4: ("SA-10", "D", 140),
                   5: ("SA-11", "P", 62), 6: ("SA-12", "D", 108), 7: ("SA-13", "P", 23),
                   8: ("SA-N-4", "D", 50), 9: ("SA-N-5", "P", 28), 11: ("SA-N-7", "P", 62), 21: ("OTH", "D", 156)}

RADAR_NAMES = {code: v[0] for code, v in BRIEFING_RADARS.items()}      # code -> system name (briefing-map codes)
