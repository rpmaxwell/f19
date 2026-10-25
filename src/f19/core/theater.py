"""The four theaters: codes, aliases, .WLD files, the game's theater index, takeoff heading and score multiplier."""
from enum import Enum

from f19.core.units import angle16_to_deg

CARRIER_OFFSET16 = 0x400      # a carrier takeoff adds 0x400 (5.6 deg) to the runway heading (EGAME 0x1FC1)


class Theater(Enum):
    LB = "LB"
    PG = "PG"
    NC = "NC"
    CE = "CE"

    @property
    def wld(self):
        """The game's .WLD file name (analysis/theaters.py WLD_FILE)."""
        return _WLD[self]

    @property
    def index(self):
        """pilot record +0x38 theater index; EGAME's per-theater file table (lb/pg/nc/ce.xxx) is indexed by it."""
        return _INDEX[self]

    @property
    def full_name(self): return _FULL[self]

    @property
    def score_mult(self):
        """END.EXE rating multiplier (score = score * m // 8; FINDINGS.md "Mission rating formula")."""
        return _SCORE[self]

    def takeoff_heading16(self, carrier=False):
        """EGAME 0x1FC1 (FINDINGS.md "Takeoff direction"): 0 (north) if the theater index has bit 0 set (PG, CE), else
        0x8000 (south, LB, NC); a carrier (takeoff record flags & 0x200) adds 0x400. Nothing else matters."""
        return ((0 if self.index & 1 else 0x8000) + (CARRIER_OFFSET16 if carrier else 0)) & 0xFFFF

    def takeoff_heading(self, carrier=False):
        """takeoff_heading16 in deg: 0 / 180, carriers 5.625 / 185.625 (planner/plan.game_takeoff_heading)."""
        return angle16_to_deg(self.takeoff_heading16(carrier))

    @classmethod
    def parse(cls, name):
        """'pg', 'GULF', 'Persian Gulf', 'GULF.WLD', 'north_cape', Theater.PG -> Theater.PG (analysis/theaters.code)."""
        if isinstance(name, cls): return name
        n = str(name).upper().removesuffix(".WLD").replace("_", " ").strip()
        try: return cls(ALIASES.get(n, n))
        except ValueError: raise ValueError(f"unknown theater {name!r}: use one of {', '.join(t.value for t in cls)}") from None

    @classmethod
    def from_index(cls, i):
        """pilot +0x38 theater index -> Theater (scripts/read_mission.THEATER_INDEX)."""
        return _BY_INDEX[i]


_WLD = {Theater.CE: "CE.WLD", Theater.NC: "NC.WLD", Theater.LB: "LIBYA.WLD", Theater.PG: "GULF.WLD"}
_INDEX = {Theater.LB: 0, Theater.PG: 1, Theater.NC: 2, Theater.CE: 3}
_BY_INDEX = {i: t for t, i in _INDEX.items()}
_FULL = {Theater.CE: "Central Europe", Theater.NC: "North Cape", Theater.LB: "Libya", Theater.PG: "Persian Gulf"}
_SCORE = {Theater.LB: 8, Theater.PG: 10, Theater.NC: 12, Theater.CE: 13}
ALIASES = {"CENTRAL EUROPE": "CE", "EUROPE": "CE", "NORTH CAPE": "NC", "LIBYA": "LB", "GULF": "PG", "PERSIAN GULF": "PG"}
