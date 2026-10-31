"""Loops (f19.pilot.loops: heading hold, altitude hold, speed hold, flaps rule, sensitivity fix) with Controls: a
CHARACTERISATION test. The same tick inputs that once ran next to nav's old inline loop code (the two agreed on every
tick of ~33k) now run through Loops + Controls alone, and a digest of everything they output (key events with hold
times, press / rate / rate_cmd / roll event / vs / hold altitude, the speed target, the flaps flag, log texts, timers)
is compared with the stored digest of that agreeing run. A change of loop behaviour fails here: if it was meant, re-run
with PRINT_DIGESTS=1 (pytest -s) and paste the new digests, saying why in the commit.

Inputs: the replay fixtures' nav CSV rows (dense: every nav cycle) with the bank from the nearest memlog snapshot, and a
synthetic closed loop. The overrides cycle through a fixed set covering every override the loops read, with stretches of
keyboard sensitivity 2, loiter legs, a leg alt_ft and the ALT file. Open loop on the replays. The recording emulator can
advance the clock by the hold time of a tap (blocking taps, as live)."""
import bisect
import hashlib
import math
import os

import pytest

from pilot_worlds import KeyLog
import harness as H  # noqa: E402
from f19.core.clock import ManualClock
from f19.core.types import Attitude, Command, FlightContext, GameState, Position, Systems
from f19.memory import ReplayReader
from f19.pilot.controls import Controls
from f19.pilot.loops import Loops, Modes, SpeedHold

# digest of every tick's outputs per scenario (see the module docstring); recorded 2026-10-30 from the run that agreed with nav's old loop code
GOLD = {
'1792609731-auto-False': '7de852b4c08b420c',
    '1792546698-auto-False': '7ca206e1e3e1568c',
    '1792715432-auto-False': 'df05f8c8dc1b60e9',
    '1792609731-hdg-False': '80d56dc039c82587',
    '1792546698-hdg-False': '3d8e9589ef3ccc4d',
    '1792715432-hdg-False': '7592014486a339c1',
    '1792609731-alt-False': '20ed85c65417bbc9',
    '1792546698-alt-False': '7ed560efdf0f0086',
    '1792715432-alt-False': '154c8edb2a2b9d2b',
    '1792609731-auto-True': '15024a5699c8444d',
    '1792546698-auto-True': '6073f97d0cc3acd9',
    '1792715432-auto-True': '28ab7a67e89c94f4',
    '1792609731-hdg-True': '0b8843aac6bff886',
    '1792546698-hdg-True': '106238469e1006aa',
    '1792715432-hdg-True': '64a1f9ceb17f0419',
    '1792609731-alt-True': 'a1e38a11ceef234c',
    '1792546698-alt-True': '4c53fb9d6ea16ce4',
    '1792715432-alt-True': '158db5633098bf0c',
    'synthetic': '09c7f844b77e641e',
}


OVS = [{}, {"rate_cap": 3.0}, {"rate_cap": 1.5, "floor": 0, "alt_cap": 300.0, "vs_max": 6.0},
       {"roll_hold": False, "alt_hold": False}, {"bank_max": 45.0, "alt_min": 250, "rate_cap": 2.2},
       {"alt": 1500, "vs_max": 20.0}, {"vs_target": -3.0, "deadband": 2.0, "no_thr_up": True, "floor": 0},
       {"alt": 250, "rate_cap": 3.0}, {"roll_hold": True, "no_thr_up": False}]
ARGS = {"auto": dict(auto=True, hdg=True, alt=450.0, strafe=None), "hdg": dict(auto=False, hdg=True, alt=None, strafe=None),
        "alt": dict(auto=False, hdg=False, alt=300.0, strafe=None)}


class AdvKeyLog(KeyLog):
    """KeyLog whose taps advance a clock by their hold time (the blocking emulator), when adv."""
    def __init__(self, advance, adv):
        super().__init__(); self.advance, self.adv = advance, adv
    def tap(self, key, hold_secs=0.05):
        super().tap(key, hold_secs)
        if self.adv: self.advance(hold_secs)
    def scan_tap(self, code, hold_secs=0.5, shift=False):
        super().scan_tap(code, hold_secs, shift)
        if self.adv: self.advance(hold_secs)


class Pair:
    """Loops + Controls on a recording emulator; every tick's outputs go into a digest."""
    def __init__(self, mode, alt_file, adv, thr0=190.0):
        a = ARGS[mode]
        self.clock = ManualClock(0.0)
        self.emu = AdvKeyLog(self.clock.advance, adv)
        self.speed = SpeedHold(); self.speed.target = thr0 if a["auto"] else None
        self.ctl = Controls(self.emu, self.speed)
        self.modes = Modes(hdg=a["hdg"], alt=a["alt"], auto=a["auto"])
        self.loops = Loops(self.clock, self.modes, alt_file, self.speed)
        self.ctx = FlightContext(self.clock)
        self.n = 0
        self.h = hashlib.sha256()
        self.presses = set()
        self.events = []

    def tick(self, t, alt, spd, hdg, want, ts, phase, flags, throttle, roll, sens, loiter, leg_alt, ov, tko_active):
        self.clock.set(t)
        st = GameState(t=None, pos=Position(0.0, 0.0, alt), att=Attitude(hdg, 0.0, roll), speed_kt=spd,
                       systems=Systems(flags=flags, throttle=throttle), sensitivity=sens, time_mult=ts)
        cmd = Command(heading=want, rate_cap=2.5 if loiter else 0.8, alt=leg_alt).merged(Command.from_overrides(ov))
        rep = self.loops.step(self.ctl, st, cmd, self.ctx, phase, tko_active, now=t)
        nkeys = self.emu.take()
        new = (nkeys, rep.press, rep.rate, rep.rate_cmd, rep.roll_evt, rep.vs, rep.hold_alt, self.speed.target,
               self.loops.flaps.slow_flaps, rep.events, self.loops.sens.next, self.loops.altitude.last_press,
               self.loops.heading.key, self.modes.alt)
        self.n += 1; self.h.update(repr(new).encode()); self.events += rep.events
        for p in (rep.press or "").split(): self.presses.add(p)
        if rep.roll_evt: self.presses.add(rep.roll_evt.split(" ")[0])
        return nkeys

    @property
    def digest(self): return self.h.hexdigest()[:16]


def _check(name, got):
    if os.environ.get("PRINT_DIGESTS"): print(f"DIGEST {name!r}: {got!r},")
    else: assert got == GOLD[name], f"{name}: loop behaviour changed (digest {got}, stored {GOLD[name]})"


def _num(v, d=0):
    try: return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(float(v))
    except (TypeError, ValueError): return d


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id", ["1792609731", "1792546698", "1792715432"])
@pytest.mark.parametrize("mode", ["auto", "hdg", "alt"])
@pytest.mark.parametrize("adv", [False, True])
def test_loops_on_replays(run_id, mode, adv, tmp_path, egame):
    R = H.Run.load(run_id)
    rr = ReplayReader(R.dir)
    rolls = [(t, st.att.roll) for t, st in rr.states(threats=False)]
    rt = [t for t, _ in rolls]
    alt_file = tmp_path / "ALT"
    P = Pair(mode, alt_file, adv)
    if True:
        for i, r in enumerate(R.rows):
            t = float(r["t"])
            k = min(max(bisect.bisect_left(rt, t), 0), len(rt) - 1)
            if i == 1500: alt_file.write_text("650\n")
            if i == 2200: alt_file.unlink()
            P.tick(t, _num(r["alt"]), _num(r["spd"]), float(r["hdg"]), float(r["want"]), float(r["tscale"] or 1),
                   r["phase"], _num(r.get("flags")), _num(r.get("throttle"), 60), rolls[k][1],
                   2 if i % 700 < 12 else 1, (i // 300) % 4 == 1, 700 if (i // 450) % 3 == 2 else None,
                   OVS[(i // 37) % len(OVS)], r["phase"] == "takeoff")
        assert P.n > 1000
    _check(f"{run_id}-{mode}-{adv}", P.digest)
    print(f"loops {run_id} {mode} adv={adv}: {P.n} ticks; presses seen {sorted(P.presses)}")


def test_loops_closed_loop_synthetic(egame, tmp_path):
    """A crude closed loop (the keys move the plane) through the branches the replays rarely reach: a slow sag below
    140 kt (flaps rule, throttle guard, slow: no pull), a dive toward the floor (UP!, SINK!), a deep bank (bank limit),
    a stuck turn with no key held (UNSTICK), sensitivity 3."""
    P = Pair("auto", tmp_path / "ALT", True, thr0=200.0)
    alt, spd, hdg, roll, vs, thr, flags = 600.0, 200.0, 0.0, 0.0, 0.0, 60, 0x01
    if True:
        for i in range(3000):
            t = 1_800_000_000.0 + i * 0.25
            phase = "route"
            ov = OVS[(i // 53) % len(OVS)]
            if 400 <= i < 700: spd = max(110.0, spd - 0.6)                       # sag
            if 900 <= i < 960: vs = -45.0                                         # dive
            if 1200 <= i < 1260: roll = -40.0                                     # deep bank
            if 1500 <= i < 1560: hdg = (hdg + 2.0) % 360                          # stuck turn
            sens = 3 if 1800 <= i < 1830 else 1
            keys = P.tick(t, int(alt), int(spd), hdg, (hdg + 40) % 360 if i % 400 < 200 else (hdg - 25) % 360, 2.0,
                          phase, flags, thr, roll, sens, False, None, ov, False)
            for k in keys:                                                        # the game's side
                if k[0] == "tap" and k[1] == "down": vs += 12 * k[2]
                elif k[0] == "tap" and k[1] == "up": vs -= 12 * k[2]
                elif k[0] == "tap" and k[1] == "9": flags ^= 0x02
                elif k[0] == "scan" and k[1] == 0x0D: thr = 100 if k[2] else min(100, thr + 10)
                elif k[0] == "scan" and k[1] == 0x0C: thr = 0 if k[2] else max(0, thr - 10)
                elif k[0] == "down" and k[1] in ("left", "right"): roll += (-6 if k[1] == "left" else 6)
            alt = max(50.0, alt + vs * 0.25); vs *= 0.97
            if i >= 700: spd = min(260.0, spd + (thr - 55) * 0.01)
            if not (1500 <= i < 1560): hdg = (hdg + math.tan(math.radians(roll)) * 0.5) % 360
            if not (1200 <= i < 1260): roll *= 0.98
        assert {"UP!", "SINK!", "up", "down", "UNSTICK", "BANK", "THR", "thr+", "thr-"} <= P.presses, P.presses
        assert any("flaps down" in e for e in P.events)
    _check("synthetic", P.digest)
