"""One-off WP-4 check, for the USER to run once with F-19 in flight (or paused on a loaded save state): does
f19.memory.MemoryReader.snapshot() read the same as the pre-WP-4 f19_state readers, field by field?

READ-ONLY: it sends no keys and writes no memory. It holds the automation port (one client at a time), so stop nav
first.

  uv run python research/scripts/probe_reader.py [--rounds 10] [--mission]

Each round reads the old dicts with the frozen pre-WP-4 code (tests/unit/memory/f19_state_legacy.py, one round trip
per reader) and then MemoryReader.snapshot() (two round trips), and compares GameState.from_dicts(old) with the
snapshot field by field. The game keeps running between the two reads, so moving values (position, clock, frame,
alerts) may differ a little; a field that differs in EVERY round, or by a lot, is a bug. --mission also compares the
mission parse (one full memory read, ~3 s) with the old read_mission.
"""
import argparse, dataclasses, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "src", ROOT / "scripts", ROOT / "analysis", ROOT / "tests" / "unit" / "memory"):
    sys.path.insert(0, str(p))

import f19_state_legacy as OLD  # noqa: E402
from f19.core.types import GameState  # noqa: E402
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
        o_old, o_new = OLD.find_offset(emu), r.offset()
        print(f"offset old {o_old:+#x}  new {o_new:+#x}  {'OK' if o_old == o_new else 'DIFFERENT'}")
        for k in range(a.rounds):
            st = OLD.read_state(emu)
            old = GameState.from_dicts(st, OLD.read_flight(emu), OLD.read_status(emu), OLD.read_alerts(emu),
                                       OLD.read_aircraft(emu), OLD.read_site_records(emu), OLD.read_waypoints(emu),
                                       OLD.read_trackcam(emu), OLD.read_sensitivity(emu))
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
            print(f"  {f:28s} differed {len(vs)}/{a.rounds}  [{tag}]  e.g. old {vs[0][0]!r} new {vs[0][1]!r}"[:200])
        print(f"time_mult {r.time_mult()}  frame {r.frame()}  game time {r.game_time():.0f} s")
        if a.mission:
            import read_mission, read_mission_legacy as OLDM
            mem = r.memory()
            th = OLDM.detect_theater(mem)
            want, got = OLDM.parse(mem, th), r.mission_dict(th, mem=mem, world=read_mission.WORLD)
            got.pop("theater")
            print("mission parse:", "IDENTICAL" if want == got else f"DIFFERENT: {sorted(k for k in want if want[k] != got.get(k))}")
            print(r.mission(th, mem=mem, world=read_mission.WORLD))


if __name__ == "__main__":
    main()
