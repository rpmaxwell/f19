"""Find a mission with the wanted shape and select it by START's rand seed (WP-4b: f19.gamedata.mission_gen).

Filters: length bucket (the briefing's FUEL ESTIMATE, which sets the score's fuel multiplier), primary task,
secondary task (or mission numbers), and no time limit. The theater, level, setup and opponents are the
pilot's current assignment (change them in the game's Mission Assignment screen).

How it works: START's generator is emulated (unicorn) on a memory image read from the running game. A survey of
random seeds says whether the combination exists; find_seed picks a seed that deals it. Live, the seed is POKEd into
START's rand state (u32 ds:7A50) and ONE "Select New Mission" round is sent (Enter, Enter on the roster pilot, Enter
on "Accept assignment"), checked through memory only (ds:CA6A and the briefing block; no screenshots, no turbo).
The old 2 s/reroll loop is gone.

  uv run --group missions python scripts/mission_finder.py --length x10 --primary destroy --secondary photo --no-time-limit
  uv run --group missions python scripts/mission_finder.py ... --check-only           (existence check, no keys)
  uv run --group missions python scripts/mission_finder.py ... --snapshot FILE --check-only   (offline, any theater)
  uv run --group missions python scripts/mission_finder.py --verify-snapshot FILE     (emulation vs a briefing image)
Live: start it with the game on START's options menu, "Select New Mission" highlighted (the menu after "Arming
Complete"). START's cursor variables are not known yet (TODO in f19.memory.addresses), so it can't check that first.
"""
import argparse, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "analysis"))
import read_mission as rm  # noqa: E402
import theaters  # noqa: E402
from f19.gamedata.mission_gen import (  # noqa: E402,F401
    BUCKETS, COMM_PTR, FUEL_EST, LEVELS, NEW_FLAG, NEW_MISSION, NEW_MISSION_RET, AFTER_GENERATE, AFTER_GENERATE_END,
    OPPONENTS, PILOT_FLAGS, PILOT_LEVEL, PILOT_OPP, PILOT_THEATER, SEED, TASKS, THEATER_CODE, Generator, Spec, block_key,
    bucket, decode, describe, find_seed, key, predict, select_by_seed, survey, verify_image)

GAME = theaters.GAME


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="length buckets (FUEL ESTIMATE -> score multiplier): x8 < 7000 lb, x9 7000+, "
                                        "x10 9000+, x11 11500+ (L >= 719 km)")
    ap.add_argument("--length", help="x8 / x9 / x10 / x11, or e.g. x10+ for that bucket or longer")
    ap.add_argument("--primary", choices=sorted(TASKS), help="primary task (attack = destroy)")
    ap.add_argument("--secondary", choices=sorted(TASKS), help="secondary task")
    ap.add_argument("--secondary-mission", type=int, nargs="+", metavar="N", help="secondary mission number(s), MISSIONS.md")
    ap.add_argument("--no-time-limit", action="store_true", help="skip missions with a deadline ('before HH:MM hours')")
    ap.add_argument("--check-only", action="store_true", help="only say whether the combination exists; send no keys")
    ap.add_argument("--snapshot", help="memory image to use instead of the running game (implies --check-only)")
    ap.add_argument("--theater", help="with --snapshot: check another theater (CE/NC/LB/PG)")
    ap.add_argument("--samples", type=int, default=1000, help="random missions for the existence check")
    ap.add_argument("--tries", type=int, default=5000, help="seeds to try when picking one")
    ap.add_argument("--verify-snapshot", metavar="FILE", help="offline: does the emulation reproduce this briefing image's mission?")
    a = ap.parse_args()

    if a.verify_snapshot:
        mem = Path(a.verify_snapshot).read_bytes()
        r = verify_image(mem)
        print(f"reproduced {r[0]} draws back: {describe(r[1])}" if r else "NOT reproduced within the search range")
        return

    spec = Spec(a.length, a.primary, a.secondary, a.secondary_mission, a.no_time_limit)
    emu = None
    if a.snapshot:
        mem = Path(a.snapshot).read_bytes()
    else:
        from f19.emulator import Emulator
        from f19.memory import MemoryReader
        emu = Emulator(); reader = MemoryReader(emu, rm.WORLD)
        mem = reader.memory()
    try:
        gen = Generator(mem)
        st = gen.settings()
        assignment = {}
        if a.theater:
            if not a.snapshot: sys.exit("--theater needs --snapshot: in the game, change it in the Mission Assignment screen")
            assignment["theater"] = {v: k for k, v in THEATER_CODE.items()}[theaters.code(a.theater)]
            st["theater"] = theaters.code(a.theater)
        print(f"assignment: {st['theater']}, {LEVELS[st['level']] if st['level'] < 3 else st['level']}, "
              f"{'strike' if st['strike'] else 'air-to-air'}, {OPPONENTS[st['opponent']] if st['opponent'] < 4 else st['opponent']}")
        print(f"looking for: {spec}")

        t = time.time()
        hits, buckets, examples = survey(gen, spec, a.samples, **assignment)
        mix = ", ".join(f"{n} {100 * buckets[n] / a.samples:.0f}%" for n, _ in BUCKETS if buckets[n])
        print(f"existence check: {hits} of {a.samples} random missions match ({time.time() - t:.0f} s); lengths: {mix}")
        if not hits:
            sys.exit(f"ERROR: no such mission in {st['theater']} with this assignment (0 of {a.samples}). "
                     "Try again with different parameters.")
        for m in examples: print("  e.g.", describe(m))
        if a.check_only or a.snapshot: return

        cur = decode(mem, gen.ds_phys)
        print("current:", describe(cur))
        if spec.match(cur): print("The current mission already matches."); return
        found = find_seed(spec, gen, tries=a.tries)
        if found is None: sys.exit(f"ERROR: no seed in {a.tries} deals a match (about 1 in {a.samples // hits} does).")
        seed, want = found
        print(f"seed {seed:#010x} deals: {describe(want)}")
        ok, mission = select_by_seed(emu, reader, seed, want, ds_phys=gen.ds_phys)
        got = decode(reader.memory(), gen.ds_phys)
        print("FOUND:" if ok else "DEALT (not the prediction):", describe(got))
        if not spec.match(got): sys.exit("ERROR: the mission in the game does not match; run again.")
    finally:
        if emu is not None: emu.close()


if __name__ == "__main__":
    main()
