"""Live probe: what does ONE B press do to the trackcam lock, and does the weapon station matter?

  python research/scripts/probe_trackcam_b.py [--station N] [--presses K]

Open question (docs/game/memory-map.md "Trackcam target selection", 2026-10-30): flight 1793382037 attack 2 (photo, station 1
= camera) had lock 7 and the wanted record 1 in the cone, and B never moved the lock in 40 presses, while the decoded rule
(EGAME 0xab84) predicts 7 -> 1. The only difference found against a flight where B cycled was ds:4EEA = 1 instead of 0.

SENDS KEYS TO THE RUNNING GAME (F2 until air-to-ground, "/", then B). Run it only when the owner has asked for it in this
session and is watching (CLAUDE.md "Live flights"); fly a straight, level, safe stretch with a few records ahead; do it once
per station (Space cycles the station; --station N presses Space until ds:4EEA == N, so camera = the camera's station).

Per phase it peeks ds:5526 (lock) / 9740 (screen) / 4EEA (station) / 9A36 (mode) / 684C (flags) / 94E0 / 8B22 every
~50 ms for 2 s after the key, prints the sequence, and the rule's prediction (rules.trackcam) for the same moment. Writes
F19_DATA/logs/probe/trackcam_b_<time>.json. Fill the result into docs/game/memory-map.md with a date.
"""
import argparse, json, struct, time

from f19.core.settings import settings as S
from f19.emulator import Emulator
from f19.memory import addresses as A, reader as R
from f19.rules import trackcam as TC

SLASH = 0x35


def peek_words(emu, o):
    names = dict(lock=A.LOCKED, screen=A.CAM_SCREEN, station=A.STATION, mode=A.TRACKCAM_MODE, flags684C=A.FLAGS,
                 cam_flags=A.CAM_FLAGS, cooldown=A.CAM_COOLDOWN, view=A.CAM_VIEW, frame=A.FRAME)
    got = emu.peek_many([(a + o, 2) for a in names.values()])
    return {k: struct.unpack("<H", v)[0] for k, v in zip(names, got)}


def watch(emu, o, secs, label):
    t0, rows = time.monotonic(), []
    while time.monotonic() - t0 < secs:
        r = peek_words(emu, o); r["t"] = round(time.monotonic() - t0, 3)
        if not rows or {k: v for k, v in r.items() if k not in ("t", "frame")} != {k: v for k, v in rows[-1].items() if k not in ("t", "frame")}:
            rows.append(r)
        time.sleep(0.05)
    print(f"-- {label}: " + "  ".join(f"{r['t']:.2f}s lock={r['lock']:#06x} scr={r['screen']:#x} st={r['station']}" for r in rows))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--station", type=int); ap.add_argument("--presses", type=int, default=3)
    args = ap.parse_args()
    emu = Emulator(); o = R.find_offset(emu); out = dict(station_wanted=args.station, phases=[])
    if args.station is not None:
        for _ in range(4):
            if peek_words(emu, o)["station"] & 3 == args.station: break
            emu.tap("space"); time.sleep(0.8)
    for _ in range(4):
        if peek_words(emu, o)["mode"] == A.AIR_GROUND: break
        emu.tap("f2"); time.sleep(0.8)
    emu.scan_tap(SLASH, hold_secs=0.2); out["phases"].append(dict(key="/", rows=watch(emu, o, 2.0, "/")))
    for i in range(args.presses):
        cam = R.read_trackcam_view(emu); st = peek_words(emu, o)
        pos = emu.peek_many([(A.UNITS_XY + o, 2), (A.UNITS_Y + o, 2), (A.HDG + o, 2)])
        x, y, h = (struct.unpack("<H", b)[0] for b in pos)
        cands = TC.candidates((x, y), h * 360 / 65536, cam.view, cam.night, cam.records, st["mode"])
        pred = TC.b_next(TC.lock_index(st["lock"]), cands, TC.lock_floor(st["lock"], (x, y), h * 360 / 65536, cam.view, cam.records))
        print(f"before B #{i + 1}: lock {st['lock']:#06x} station {st['station']} screen {st['screen']:#x}; rule predicts {pred}; cands {[(c.index, c.dist) for c in cands][:8]}")
        emu.tap("b", 0.1)
        out["phases"].append(dict(key="b", pred=pred, cands=[(c.index, c.dist) for c in cands], rows=watch(emu, o, 2.0, f"B #{i + 1}")))
    f = S.logs / "probe" / f"trackcam_b_{int(time.time())}.json"; f.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(f, "w"), indent=1); print("wrote", f)


if __name__ == "__main__":
    main()
