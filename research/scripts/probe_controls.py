"""Live checks for the --auto phases (scripts/phases.py): run in flight, one test at a time.

  probe_controls.py state              pitch / roll / flags / locked target for 10 s (pitch + = nose up?)
  probe_controls.py toggles            tap 8 (bay), 9 (flaps), 0 (brakes) twice each: which FLAGS bit flips
  probe_controls.py gear               same for 6 (gear) - IN THE AIR only
  probe_controls.py throttle           Shift+- , then = x4, Shift+= : scancode keys reach the game? (airspeed
                                       trend + memory bytes that step with each press = throttle position)
  probe_controls.py weapon             tap Space x5: memory bytes that change on every press (selected weapon)
  probe_controls.py target [record]    tap B x8: locked-target value after each press (+ that record's X, Y)

Memory diffs cover EGAME's data area (old phys 0x3C000-0x48000, shifted by the load offset);
results go to logs/probe/<test>_<time>.json. Addresses are FINDINGS.md "old phys".
"""
import json, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox
from f19.memory.reader import read_state, read_flight, read_target, find_offset
from f19.memory.addresses import FLAGS, LOCKED
from f19.pilot.controls import MINUS, EQUALS, TAP

ROOT = Path(__file__).resolve().parent.parent
LO, HI = 0x3C000, 0x48000

def snap(db):
    o = find_offset(db); out = bytearray()
    for a in range(LO + o, HI + o, 64 * 64):
        n = min(64, (HI + o - a) // 64)
        for i in range(n): db._send(f"PEEK {(a + i * 64) >> 4:X}:{(a + i * 64) & 15:X} 64")
        for i in range(n): out += bytes.fromhex(db._recv_line().split(" ", 1)[1])
    return bytes(out)

def steppers(snaps, min_changes):
    """Old-phys addresses of bytes that changed between at least min_changes consecutive snapshots."""
    res = []
    for i in range(len(snaps[0])):
        vals = [s[i] for s in snaps]
        if sum(a != b for a, b in zip(vals, vals[1:])) >= min_changes: res.append((LO + i, vals))
    return res

def show(f): return f"pitch {f['pitch']:+6.1f}  roll {f['roll']:+6.1f}  flags {f['flags']:04x}  locked {f['locked']}  opp {f['opp']}  box {f['box']}"

def main():
    test = sys.argv[1] if len(sys.argv) > 1 else "state"
    result = dict(test=test, t=time.time())
    with Dosbox() as db:
        if test == "state":
            for _ in range(20):
                s = read_state(db); print(f"alt {s['alt']:5d} spd {s['spd']:3d} hdg {s['hdg']:5.1f}  " + show(read_flight(db))); time.sleep(0.5)
            return
        if test in ("toggles", "gear"):
            keys = {"toggles": [("8", "bay"), ("9", "flaps"), ("0", "brakes")], "gear": [("6", "gear")]}[test]
            if test == "gear" and read_state(db)["alt"] < 100: sys.exit("gear test: climb above 100 ft first")
            for k, name in keys:
                seq = [read_flight(db)["flags"]]
                for _ in range(2): db.tap(k, TAP); time.sleep(1.5); seq.append(read_flight(db)["flags"])
                flips = [seq[0] ^ seq[1], seq[1] ^ seq[2]]
                print(f"{name:7s} key {k}: flags {' -> '.join(f'{v:04x}' for v in seq)}   flipped bits {flips[0]:04x}, {flips[1]:04x}")
                result[name] = dict(seq=seq, flipped=flips)
        elif test == "throttle":
            on_ground = read_state(db)["alt"] < 20
            if on_ground and not read_flight(db)["flags"] & 0x08:          # brakes on: power must not start the takeoff roll
                db.tap("0", TAP); time.sleep(1.0)
                if not read_flight(db)["flags"] & 0x08: sys.exit("throttle test: brakes would not set - not adding power on the runway")
            snaps, spd = [snap(db)], [read_state(db)["spd"]]
            plan = [("no power", MINUS, True)] + [("+", EQUALS, False)] * 4 + [("max power", EQUALS, True), ("no power", MINUS, True)]
            for name, code, sh in plan:
                db.scan_tap(code, TAP, shift=sh); time.sleep(2.0); snaps.append(snap(db)); spd.append(read_state(db)["spd"])
                print(f"{name:9s}: airspeed {spd[-1]} kt")
            st = steppers(snaps, 4)
            print(f"{len(st)} bytes changed with >= 4 of 6 presses; first 30:")
            for a, v in st[:30]: print(f"  {a:#07x}: {v}")
            result.update(airspeed=spd, candidates=[(a, v) for a, v in st[:200]])
        elif test == "weapon":
            snaps = [snap(db)]
            for _ in range(5): db.tap("space", TAP); time.sleep(1.2); snaps.append(snap(db))
            st = steppers(snaps, 5)
            print(f"{len(st)} bytes changed on every Space press:")
            for a, v in st[:40]: print(f"  {a:#07x}: {v}")
            result["candidates"] = [(a, v) for a, v in st[:200]]
        elif test == "target":
            want = int(sys.argv[2]) if len(sys.argv) > 2 else None
            seq = []
            for _ in range(8):
                db.tap("b", TAP); time.sleep(1.0); lk = read_flight(db)["locked"]
                x, y, dead = read_target(db, lk) if lk < 400 else (None, None, None)
                seq.append(dict(locked=lk, x=x, y=y, destroyed=dead))
                print(f"locked {lk:5d}  record X {x} Y {y} destroyed {dead}" + ("   <== mission target" if lk == want else ""))
            result["seq"] = seq
        else:
            sys.exit(__doc__)
    (ROOT / "logs/probe").mkdir(parents=True, exist_ok=True)
    out = ROOT / f"logs/probe/{test}_{int(time.time())}.json"; json.dump(result, open(out, "w"), indent=1); print("->", out)

if __name__ == "__main__":
    main()
