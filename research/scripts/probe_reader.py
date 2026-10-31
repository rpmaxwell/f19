"""Live check, for the USER to run with F-19 in flight (or paused on a loaded save state): does
f19.memory.MemoryReader.snapshot() (the merged read, two round trips) read the same as the individual dict readers
(f19.memory.reader.read_state / read_flight / ..., one round trip each, put together by GameState.from_dicts), field by field?
(Until 2026-10-30 the other side was the frozen pre-WP-4 f19_state; that module is retired and the reader module has the same functions.)

READ-ONLY: it sends no keys and writes no memory. It holds the automation port (one client at a time), so stop nav
first.

  uv run python research/scripts/probe_reader.py [--rounds 10] [--mission]

Each round reads the dicts (one round trip per reader) and then MemoryReader.snapshot(), and compares
GameState.from_dicts(dicts) with the snapshot field by field. The game keeps running between the two reads, so moving values (position, clock, frame,
alerts) may differ a little; a field that differs in EVERY round, or by a lot, is a bug. --mission also compares the
mission parse (one full memory read, ~3 s) with MemoryReader.mission_dict and prints the mission.
"""
import argparse, dataclasses, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from f19.cli.gameworld import GameWorld  # noqa: E402
from f19.core.types import GameState  # noqa: E402
from f19.memory import mission as M  # noqa: E402
from f19.memory import reader as RD  # noqa: E402  (the dict readers)
from f19.emulator import Emulator  # noqa: E402
from f19.memory import MemoryReader  # noqa: E402

# "hud" is HUD_MSG, the game's message build buffer AND number-formatting scratch (f19_state HUD_MSG): it is rewritten
# many times a second in flight, so two reads a moment apart differ ('0' then 'GUN 650'). Both decoders are identical.
MOVING = {"hud", "t", "pos.x", "pos.y", "pos.alt_ft", "att.heading", "att.pitch", "att.roll", "speed_kt", "systems.fuel_lb",
          "emv", "spool"}


def flat(obj, prefix=""):
    if dataclasses.is_dataclass(obj):
        out = {}
        for f in dataclasses.fields(obj): out.update(flat(getattr(obj, f.name), f"{prefix}{f.name}."))
        return out
    return {prefix.rstrip("."): obj}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--rounds", type=int, default=10); ap.add_argument("--mission", action="store_true")
    a = ap.parse_args()
    diffs, same = {}, 0
    with Emulator() as emu:
        r = MemoryReader(emu)
        o_old, o_new = RD.find_offset(emu), r.offset()
        print(f"offset (find_offset) {o_old:+#x}  (reader.offset) {o_new:+#x}  {'OK' if o_old == o_new else 'DIFFERENT'}")
        for k in range(a.rounds):
            st = RD.read_state(emu)
            old = GameState.from_dicts(st, RD.read_flight(emu), RD.read_status(emu), RD.read_alerts(emu),
                                       RD.read_aircraft(emu), RD.read_site_records(emu), RD.read_waypoints(emu),
                                       RD.read_trackcam(emu), RD.read_sensitivity(emu))
            new = r.snapshot()
            fo, fn = flat(old), flat(new)
            bad = {f: (fo[f], fn.get(f)) for f in fo if fo[f] != fn.get(f)}
            for f, v in bad.items(): diffs.setdefault(f, []).append(v)
            same += not bad
            print(f"round {k + 1}: {len(fo) - len(bad)}/{len(fo)} fields equal"
                  + (f"; differ: {', '.join(sorted(bad))}" if bad else ""))
            time.sleep(0.5)
        print(f"\n{same}/{a.rounds} rounds identical in every field")
        for f, vs in sorted(diffs.items()):
            tag = "moving (expected)" if f in MOVING or f.startswith("threats") else "CHECK"
            print(f"  {f:28s} differed {len(vs)}/{a.rounds}  [{tag}]  e.g. dicts {vs[0][0]!r} snapshot {vs[0][1]!r}"[:200])
        print(f"time_mult {r.time_mult()}  frame {r.frame()}  game time {r.game_time():.0f} s")
        if a.mission:
            world = GameWorld()
            mem = r.memory()
            th = M.detect_theater(mem, world)
            want, got = M.parse(mem, th, world=world), r.mission_dict(th, mem=mem, world=world)
            got.pop("theater")
            print("mission parse:", "IDENTICAL" if want == got else f"DIFFERENT: {sorted(k for k in want if want[k] != got.get(k))}")
            print(r.mission(th, mem=mem, world=world))


if __name__ == "__main__":
    main()
