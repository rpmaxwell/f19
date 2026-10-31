"""The game's .WLD files as f19.memory.mission wants them (a `world` with wld_bytes / names / radar), shared by fly and the tools."""
from f19.gamedata import wld


class GameWorld:
    """f19.memory.mission world over the game's .WLD files (was scripts/read_mission.World)."""
    def __init__(self):
        from f19.rules.radar import radar_systems
        self.R = radar_systems()
    def wld_bytes(self, th):
        try: return wld.wld_bytes(th)
        except OSError: return None
    def names(self, th): return wld.places(th)["names"]
    def radar(self, kind): return self.R.get(kind)
