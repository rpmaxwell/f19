"""WP-7a loops vs nav: nav's loop code (scripts/nav.py :643-746, exec'd verbatim by nav_legacy) and f19.pilot.loops +
Controls on the same tick inputs. Every tick: the same key events (tap / scan / down / up, hold times), the same
press / rate / rate_cmd / roll event / vs / hold altitude, the same speed target, flaps flag, mission-log texts and
timers (sens_next, last_press).

Inputs: the replay fixtures' nav CSV rows (dense: every nav cycle, 0.2-1 s apart; alt / spd / hdg / want / tscale /
phase / flags / throttle as logged) with the bank from the nearest memlog snapshot, and a synthetic closed loop. The
overrides cycle through a fixed set covering every override the loops read (rate_cap, roll_hold, bank_max, alt,
alt_cap, alt_min, vs_max, vs_target, deadband, floor, alt_hold, no_thr_up), with stretches of keyboard sensitivity 2,
loiter legs, a leg alt_ft and the ALT file. Open loop on the replays (the recorded flight already shows what nav's
keys did). The recording emulators can advance each side's clock by the hold time of a tap (blocking taps, as live):
then nav's time.time()-after-the-tap timers and the loops' clock.now() must still agree. The one timer that differs
by design: phases.set_toggle stamped the flaps debounce with time.time() after the tick's earlier blocking taps, the
Toggle contract stamps it with the tick time (WP-7b); with adv the old set_toggle is therefore given the tick clock.
"""
import bisect
import math
from types import SimpleNamespace

import pytest

import nav_legacy as N
from pilot_equiv import KeyLog, OldPhases, product
import harness as H  # noqa: E402
from f19.core.clock import ManualClock
from f19.core.types import Attitude, Command, FlightContext, GameState, Position, Systems
from f19.memory import ReplayReader
from f19.pilot.controls import Controls
from f19.pilot.loops import Loops, Modes, SpeedHold

ph = product.phases()
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


class Log:
    def __init__(self): self.rows = []
    def writerow(self, r): self.rows.append(r)


class Pair:
    """Old (nav's exec'd code + phases.Throttle) and new (Loops + Controls) side by side."""
    def __init__(self, mode, alt_file, adv, thr0=190.0):
        a = ARGS[mode]
        self.op = OldPhases(synthetic=False).__enter__()
        # nav's time.time() (sens_next, last_press: read after a blocking tap) advances with the taps; phases.set_toggle's
        # clock (the flaps debounce) stays at the tick time: the Toggle contract debounces on the tick clock (WP-7b)
        # where set_toggle read time.time() after the tick's earlier blocking taps
        oc = SimpleNamespace(t=0.0); oc.time = lambda: oc.t; oc.sleep = lambda s: None
        self.oc = oc
        self.odb = AdvKeyLog(lambda d: setattr(oc, "t", oc.t + d), adv)
        self.othr = ph.Throttle(); self.othr.target = thr0 if a["auto"] else None
        ns = dict(K=64.0); exec(N.CONSTANTS, ns)
        self.events_old = []
        ns.update(args=SimpleNamespace(**a, fixed=None), db=self.odb, time=oc, phases=ph, thr=self.othr, log=Log(),
                  combat_log=lambda now: None, read_sensitivity=lambda db: ns["_sens"], time_mult=lambda db: ns["_ts"],
                  in_loiter=lambda leg: ns["_loiter"], leg_attrs=lambda leg: ns["_attrs"], ALT_FILE=alt_file, hist=[],
                  last_press=0.0, sens_next=0.0, slow_flaps=False, climbed=False, phase_event=self.events_old.append,
                  tko=None, leg=1)
        self.ns = ns
        self.clock = ManualClock(0.0)
        self.emu = AdvKeyLog(self.clock.advance, adv)
        self.speed = SpeedHold(); self.speed.target = self.othr.target
        self.ctl = Controls(self.emu, self.speed)
        self.modes = Modes(hdg=a["hdg"], alt=a["alt"], auto=a["auto"])
        self.loops = Loops(self.clock, self.modes, alt_file, self.speed)
        self.ctx = FlightContext(self.clock)
        self.n = self.equal = 0
        self.first = ""
        self.presses = set()

    def close(self): self.op.__exit__(None, None, None)

    def tick(self, t, alt, spd, hdg, want, ts, phase, flags, throttle, roll, sens, loiter, leg_alt, ov, tko_active):
        ns = self.ns
        self.oc.t = self.op.clock.t = t; self.clock.set(t)
        s = dict(alt=alt, spd=spd, hdg=hdg, x=0, y=0)
        f = dict(roll=roll, flags=flags, throttle=throttle, pitch=0.0)
        attrs = {} if leg_alt is None else {"alt_ft": leg_alt}
        ns.update(s=s, ov=dict(ov), want=want, now=t, phase=phase, _sens=sens, _ts=ts, _loiter=loiter, _attrs=attrs,
                  tko=SimpleNamespace(finished=False) if tko_active else None)
        if self.modes.auto: ns["f"] = f
        n_ev = len(self.events_old)
        exec(N.LOOPS, ns)
        okeys = self.odb.take()
        st = GameState(t=None, pos=Position(0.0, 0.0, alt), att=Attitude(hdg, 0.0, roll), speed_kt=spd,
                       systems=Systems(flags=flags, throttle=throttle), sensitivity=sens, time_mult=ts)
        cmd = Command(heading=want, rate_cap=2.5 if loiter else 0.8, alt=leg_alt).merged(Command.from_overrides(ov))
        rep = self.loops.step(self.ctl, st, cmd, self.ctx, phase, tko_active, now=t)
        nkeys = self.emu.take()
        old = (okeys, ns["press"], ns["rate"], ns["rate_cmd"], ns["roll_evt"], ns.get("vs") if ns["hold_alt"] is not None else None,
               ns["hold_alt"], self.othr.target, ns["slow_flaps"], [e.split(" ", 1)[1] for e in self.events_old[n_ev:]], ns["sens_next"],
               ns["last_press"], ns["roll"]["key"], ns["args"].alt)
        new = (nkeys, rep.press, rep.rate, rep.rate_cmd, rep.roll_evt, rep.vs, rep.hold_alt, self.speed.target,
               self.loops.flaps.slow_flaps, rep.events, self.loops.sens.next, self.loops.altitude.last_press,
               self.loops.heading.key, self.modes.alt)
        self.n += 1; ok = old == new; self.equal += ok
        if not ok and not self.first: self.first = f"tick {self.n} t={t}: old {old}\n new {new}"
        for p in (rep.press or "").split(): self.presses.add(p)
        if rep.roll_evt: self.presses.add(rep.roll_evt.split(" ")[0])
        return okeys


def _num(v, d=0):
    try: return int(v, 16) if isinstance(v, str) and v.startswith("0x") else int(float(v))
    except (TypeError, ValueError): return d


@pytest.mark.skipif(not H.have_fixtures(), reason="replay fixtures missing")
@pytest.mark.parametrize("run_id", ["1792609731", "1792546698", "1792715432"])
@pytest.mark.parametrize("mode", ["auto", "hdg", "alt"])
@pytest.mark.parametrize("adv", [False, True])
def test_loops_match_nav_on_replays(run_id, mode, adv, tmp_path, egame):
    R = H.Run.load(run_id)
    rr = ReplayReader(R.dir)
    rolls = [(t, st.att.roll) for t, st in rr.states(threats=False)]
    rt = [t for t, _ in rolls]
    alt_file = tmp_path / "ALT"
    P = Pair(mode, alt_file, adv)
    try:
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
        assert P.equal == P.n, P.first
    finally:
        P.close()
    print(f"loops {run_id} {mode} adv={adv}: {P.n} ticks equal; presses seen {sorted(P.presses)}")


def test_loops_match_nav_closed_loop_synthetic(egame, tmp_path):
    """A crude closed loop (the keys move the plane) through the branches the replays rarely reach: a slow sag below
    140 kt (flaps rule, throttle guard, slow: no pull), a dive toward the floor (UP!, SINK!), a deep bank (bank limit),
    a stuck turn with no key held (UNSTICK), sensitivity 3."""
    P = Pair("auto", tmp_path / "ALT", True, thr0=200.0)
    alt, spd, hdg, roll, vs, thr, flags = 600.0, 200.0, 0.0, 0.0, 0.0, 60, 0x01
    try:
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
        assert P.equal == P.n, P.first
        assert {"UP!", "SINK!", "up", "down", "UNSTICK", "BANK", "THR", "thr+", "thr-"} <= P.presses, P.presses
        assert any("flaps down" in e for e in P.events_old)
    finally:
        P.close()
