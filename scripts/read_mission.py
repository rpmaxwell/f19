"""(WP-4: the parsing is f19.memory.mission; this is its CLI and the World it needs.)
Read the current mission (targets with roles, bases, radar sites with types) from memory.

The game's resident launcher (the program that chains SU/START/EGAME/END)
keeps a working copy of the theater .WLD file in low memory, and the mission
generator writes the mission into it (during the briefing, START holds its own
copy of the same records elsewhere). So this works during the briefing
(START running) as well as in flight (EGAME). Found 2026-10-09 by diffing that
copy against CE.WLD with mission 3's briefing as ground truth.

World-copy layout (offsets from the start of the copy = start of CE.WLD bytes):
  record index i lives at +0x08 + 16*i (16 B: u16 name_id, X, Y, kind, 3 x u16, u16 class)
  +0x18 / +0x28 (index 1 / 2): generated target slots ("AirPlane at Mittenwalde")
  +0x38 (index 3 on): place records from the file: name_id, X, Y, kind, f1, f2, f3, class
         class 294 = SAM site  (kind = radar code below, 0 = eliminated)
         class 309 = ship slot (kind = radar code, 0 = no ship this mission)
         class 324 = OTH radar (kind 21)
         class 277 = airbases / search-radar entries (f2/f3 vary per mission; meaning TBD)
  object type name = names[(class & 0x7f) - 1] (294 SAM Radar, 286 Tank Farm, 340 AirPlane, ...)
Coordinates are waypoint-table units (64 per km; player position >> 5).

Mission briefing block (START's data segment, found 2026-10-17; see FINDINGS.md):
  ds:B946 primary, ds:B958 secondary, 0x12 bytes each:
  u16 type, u16 target (record index), u16 base (primary: takeoff, secondary: landing),
  u16 code, u16 mission number, char onc[8]
The target is often a place record (SAM Radar at Tabor = index 12), not the +0x18/+0x28
slots, so the slot order never gave the roles. The block exists only while START runs
(briefing, before the flight); in flight this falls back to the slots and marks roles '?'.
Save the briefing-time json for the plan. It carries "takeoff_heading" (0 / 180 for an airbase,
5.6 / 185.6 for a carrier) for plan.json, decided by the theater alone (see takeoff_heading()).

  read_mission.py [--snapshot FILE] [--out logs/missions/<time>.json] [--missions logs/theater/missions.json]
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import argparse, json, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "analysis"))
import theaters  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GAME = S.game_dir
RADAR = {1: ("SA-2", "P", 37), 2: ("SA-5", "P", 43), 3: ("SA-8B", "P", 39), 4: ("SA-10", "D", 140),
         5: ("SA-11", "P", 62), 6: ("SA-12", "D", 108), 7: ("SA-13", "P", 23),
         8: ("SA-N-4", "D", 50), 9: ("SA-N-5", "P", 28), 11: ("SA-N-7", "P", 62), 21: ("OTH", "D", 156)}
# The ranges above are the briefing-map circles (CE / NC). Other codes (SA-N-6 10, and the Gulf's / Libya's Hawk 12,
# Rapier 13, Tiger 14, Seacat 15) come from the game's own tables: name and pulse/doppler from EGAME, and the SAM's
# missile range as range_km (their briefing circles have not been measured). The planner and nav use the game
# tables by code either way; range_km is for display and radar_flight.py.
import radar_game as _rg  # noqa: E402
for _t in range(1, 16):
    if _t not in RADAR:
        _R, _P, _dop = _rg.radar_spec(_t)
        RADAR[_t] = (_rg._EG[_rg.DS_FILE + 0x4C10 + 0x12 * _t:][:8].split(b"\0")[0].decode("latin1"),
                     "D" if _dop else "P", _rg.sam_range_km(_t))
# The parsing now lives in f19.memory.mission (WP-4); this module keeps its names and supplies the World (the .WLD
# files and names through analysis/theaters, the radar table above).
from f19.memory import mission as _M  # noqa: E402
from f19.memory import addresses as _A  # noqa: E402
from f19.memory.reader import read_memory as _read_memory  # noqa: E402
MISSION_TYPE = _M.MISSION_TYPE
BLOCK, BLOCK_LEN = _A.START_BLOCK, _A.START_BLOCK_LEN
START_RECS = _A.START_RECS
MISSION_TABLE, N_MISSIONS = _A.START_MISSION_TABLE, _A.START_N_MISSIONS
CATEGORY = _A.START_CATEGORY
PILOT_PTR = _A.START_PILOT_PTR
THEATER_INDEX = {0: "LB", 1: "PG", 2: "NC", 3: "CE"}   # EGAME's per-theater file table (lb/pg/nc/ce.xxx) is indexed by it
takeoff_heading, start_ds, briefing, mission_table = _M.takeoff_heading, _M.start_ds, _M.briefing, _M.mission_table


class World:
    """f19.memory.mission.World from analysis/theaters (.WLD bytes, names) and RADAR."""
    def wld_bytes(self, th):
        return theaters.wld_bytes(th) if (GAME / theaters.WLD_FILE[theaters.code(th)]).exists() else None
    def names(self, th): return theaters.places(th)["names"]
    def radar(self, kind): return RADAR.get(kind)


WORLD = World()


def read_memory():
    from dosbox_automation import Dosbox
    with Dosbox() as db:
        return _read_memory(db)


def detect_theater(mem):
    """Theater of the mission in memory: the .WLD copy the launcher holds (and START's pilot record
    when more than one theater's table shows up)."""
    return _M.detect_theater(mem, WORLD)


def parse(mem, theater="CE", copy=-1):
    return _M.parse(mem, theaters.code(theater), copy, world=WORLD)


def parse_all(mem, theater="CE"):
    """Every mission copy in memory (there can be stale ones; see parse)."""
    return _M.parse_all(mem, theaters.code(theater), world=WORLD)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--snapshot"); ap.add_argument("--out"); ap.add_argument("--theater", default="auto",
                                                                                                   help="CE / NC / LB / PG (default: detect from memory)")
    ap.add_argument("--missions", help="also dump START's mission table to this json")
    a = ap.parse_args()
    mem = Path(a.snapshot).read_bytes() if a.snapshot else read_memory()
    a.theater = detect_theater(mem) if a.theater == "auto" else theaters.code(a.theater)
    m = parse(mem, a.theater)
    m["theater"] = a.theater
    print(f"theater {a.theater}")
    out = Path(a.out) if a.out else S.logs / "missions" / f"{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True); json.dump(m, open(out, "w"), indent=1)
    print(f"world copy at phys {m['world_copy_phys']:05X}  -> {out}")
    if "times" in m: print(f"  {m['setup']} mission, begins {m['times']['start']}"
                           + (f", primary DEADLINE {m['times']['deadline']}" if "deadline" in m["times"] else ""))
    if "takeoff" in m: print(f"  takeoff   {m['takeoff']['place']}  ({m['takeoff']['x']},{m['takeoff']['y']})  heading {m['takeoff_heading']:g}")
    for t in m["targets"]:
        print(f"  {t['role']:<9} " + (f"mission {t['mission']:2d} {t['task']} the " if "mission" in t else "")
              + f"{t['object']} at {t['place']}  ({t['x']},{t['y']})" + (f"  ONC {t['onc']}" if t.get("onc") else ""))
    if "landing" in m: print(f"  landing   {m['landing']['place']}  ({m['landing']['x']},{m['landing']['y']})")
    for r in m["radars"]: print(f"  {r['place']:<12} {r['system']:<7} {'doppler' if r['type']=='D' else 'pulse  '} {r['range_km']:3d} km  ({r['x']},{r['y']})")
    if a.missions:
        ds = start_ds(mem)
        if ds is None: sys.exit("--missions: START is not running")
        names = theaters.places(a.theater)["names"]
        json.dump(dict(source="START.EXE data segment ds:4B0C (12 B x 56), read from memory by read_mission.py",
                       fields="theater_mask, level_mask, type, category, code, extra (code += extra<<8 when extra > 0)",
                       categories_from=f"{a.theater} names via ds:C162[class & 0x7f]",
                       missions=mission_table(mem, ds, names)), open(a.missions, "w"), indent=1)
        print(f"mission table -> {a.missions}")
