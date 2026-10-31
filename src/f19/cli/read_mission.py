"""Read the mission from the running game (briefing screen) or from a saved memory image: the old scripts/read_mission.py as a package command.

  python -m f19.cli.read_mission [--snapshot FILE] [--out FILE] [--theater CE|NC|LB|PG] [--missions FILE]

Reads all of conventional memory (~3 s, one client on the automation port), finds the theater and the mission copy, writes the mission
dict (what f19.planner.new_plan stores as mission_mem.json) to --out (default <F19_DATA>/logs/missions/<time>.json), and prints it.
--missions also dumps START's mission table (needs START running, i.e. the options / briefing menus). The parsing is f19.memory.mission;
the .WLD names and radar table come from f19.cli.gameworld.GameWorld.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from f19.cli.gameworld import GameWorld
from f19.core.settings import settings as S
from f19.gamedata import wld
from f19.memory import mission as M
from f19.memory.reader import read_memory


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--snapshot", help="a saved memory image instead of the running game")
    ap.add_argument("--out", help="where to write the mission json")
    ap.add_argument("--theater", default="auto", help="CE / NC / LB / PG (default: detect from memory)")
    ap.add_argument("--missions", help="also dump START's mission table to this json")
    a = ap.parse_args(argv)
    world = GameWorld()
    if a.snapshot: mem = Path(a.snapshot).read_bytes()
    else:
        from f19.emulator import Emulator
        with Emulator() as emu: mem = read_memory(emu)
    a.theater = M.detect_theater(mem, world) if a.theater == "auto" else wld.code(a.theater)
    m = M.parse(mem, a.theater, world=world)
    m["theater"] = a.theater
    print(f"theater {a.theater}")
    out = Path(a.out) if a.out else S.logs / "missions" / f"{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(m, open(out, "w"), indent=1)
    print(f"world copy at phys {m['world_copy_phys']:05X}  -> {out}")
    if "times" in m:
        print(f"  {m['setup']} mission, begins {m['times']['start']}"
              + (f", primary DEADLINE {m['times']['deadline']}" if "deadline" in m["times"] else ""))
    if "takeoff" in m: print(f"  takeoff   {m['takeoff']['place']}  ({m['takeoff']['x']},{m['takeoff']['y']})  heading {m['takeoff_heading']:g}")
    for t in m["targets"]:
        print(f"  {t['role']:<9} " + (f"mission {t['mission']:2d} {t['task']} the " if "mission" in t else "")
              + f"{t['object']} at {t['place']}  ({t['x']},{t['y']})" + (f"  ONC {t['onc']}" if t.get("onc") else ""))
    if "landing" in m: print(f"  landing   {m['landing']['place']}  ({m['landing']['x']},{m['landing']['y']})")
    for r in m["radars"]:
        print(f"  {r['place']:<12} {r['system']:<7} {'doppler' if r['type'] == 'D' else 'pulse  '} {r['range_km']:3d} km  ({r['x']},{r['y']})")
    if a.missions:
        ds = M.start_ds(mem)
        if ds is None: sys.exit("--missions: START is not running")
        names = wld.places(a.theater)["names"]
        json.dump(dict(source="START.EXE data segment ds:4B0C (12 B x 56), read from memory by f19.cli.read_mission",
                       fields="theater_mask, level_mask, type, category, code, extra (code += extra<<8 when extra > 0)",
                       categories_from=f"{a.theater} names via ds:C162[class & 0x7f]",
                       missions=M.mission_table(mem, ds, names)), open(a.missions, "w"), indent=1)
        print(f"mission table -> {a.missions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
