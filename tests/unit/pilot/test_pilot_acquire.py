"""Attack prep as a closed loop on the decoded B pick (Attack.TRACKCAM_LOOP, rules.trackcam): a fake game applies the rule
(or misbehaves) and the prep must lock the wanted record, or refuse with a reason without pressing B while it is not
selectable. Scenarios: a lost "/", the wanted record outside the cone, nearer than the lock, a cycle, a destroyed target,
a game that ignores B (the 2026-10-30 station-1 case)."""
import pytest

from pilot_worlds import FlightContext, ManualClock
from f19.core.types import Action, Attitude, GameState, Position, Systems, TrackcamView
from f19.pilot.phases import Attack
from f19.rules import trackcam as TC

K = 64
ME = (3000.0, 20000.0)
WANT = 7


def rec(i, east_km, north_km, flags=0):
    return i, ME[0] + east_km * K, ME[1] - north_km * K, flags


# nearest first: 3 (10 km), 5 (12), WANT=7 (15), 9 (18), 11 (25)
CONE = [rec(3, 0, 10), rec(5, 0, 12), rec(7, 0, 15), rec(9, 0, 18), rec(11, 0, 25)]


class FakeGame:
    """ds:9740 / 9E8C / 5526 and the pick of EGAME 0xab84, one B or "/" at a time."""
    def __init__(self, records, hdg=0.0, lose_slash=0, ignore_b=False, lock=0xFFFF, screen=0, mode=2):
        self.records, self.hdg, self.lose_slash, self.ignore_b = list(records), hdg, lose_slash, ignore_b
        self.lock, self.screen, self.view, self.mode, self.keys = lock, screen, 0, mode, []
        self.pending_at = None

    def key(self, action, t):
        if action == Action.TRACKCAM_VIEW:
            self.keys.append(("/", t))
            if self.lose_slash: self.lose_slash -= 1; return
            self.view, self.screen, self.lock = 0, 0x13, 0xFFFF
        elif action == Action.SELECT_TARGET:
            self.keys.append(("b", t))
            if self.screen in TC.SCREENS and not self.ignore_b: self.lock |= 0x80
        elif action == Action.NEXT_WEAPON: pass

    def frame(self):
        if self.screen not in TC.SCREENS or not self.lock & 0x80 and self.lock != 0xFFFF: return
        cands = TC.candidates(ME, self.hdg, self.view, 0, self.records, self.mode)
        nxt = TC.b_next(self.lock, cands, TC.lock_floor(self.lock, ME, self.hdg, self.view, self.records))
        if nxt is not None: self.lock = nxt

    def state(self, t):
        cam = TrackcamView(self.screen, self.view, 0, 0, 0, tuple(self.records))
        sy = Systems(station=0, loadout=((5, 2), (0, 0), (0, 0), (0, 0)), locked=self.lock, trackcam=self.mode)
        return GameState(t=t, pos=Position(*ME, 450.0), att=Attitude(self.hdg), speed_kt=190.0, systems=sy, opp=2, cam=cam)


def prep(game, secs=60, record=WANT, dt=0.25, **kw):
    a = Attack("primary", "maverick", (ME[0], ME[1] - 15 * K), record, 14.0, None)
    ctx = FlightContext(ManualClock(0.0)); t = 0.0
    while t < secs and a.state == "prep" and not a.done:
        ctx.clock.set(t); st = game.state(t); cmd = a.step(st, ctx)
        for act in cmd.actions: game.key(act, t)
        game.frame(); t += dt
    return a, t


@pytest.fixture(autouse=True)
def loop(monkeypatch):
    monkeypatch.setattr(Attack, "TRACKCAM_LOOP", True)


def test_locks_the_wanted_record_in_the_predicted_presses():
    g = FakeGame(CONE + [rec(20, 40, 0)])                       # a record 90 deg off the nose is not a candidate
    a, t = prep(g)
    assert a.state == "track" and g.lock == WANT and a.mismatches == 0
    assert [k for k, _ in g.keys] == ["/", "b", "b"]            # "/" then the nearest (3) is locked; 3 -> 5 -> 7


def test_a_lost_slash_is_sent_again_until_the_trackcam_is_up():
    g = FakeGame(CONE, lose_slash=3)
    a, t = prep(g)
    slashes = [x for k, x in g.keys if k == "/"]
    assert a.state == "track" and g.lock == WANT
    assert len(slashes) >= 4 and all(b - a_ >= Attack.SLASH_GAP_S - 1e-9 for a_, b in zip(slashes, slashes[1:]))
    assert not any(k == "b" and x < slashes[3] for k, x in g.keys)             # no B before the trackcam is up


def test_no_b_while_the_wanted_record_is_outside_the_cone():
    g = FakeGame([rec(3, 0, 10), rec(5, 0, 12), rec(7, 30, 2), rec(9, 0, 18)], screen=0x13)   # WANT 85 deg off the nose
    a, t = prep(g, secs=20)
    assert not any(k == "b" for k, _ in g.keys) and a.state == "prep"
    assert any("out_of_cone" in l for l in a.log)
    g.hdg = 70.0                                                # the plane turns toward it: now selectable, B resumes
    g.records = [r for r in g.records]
    a2, _ = prep(g, secs=40)
    assert g.lock == WANT or any(k == "b" for k, _ in g.keys)


def test_wanted_nearer_than_the_lock_restarts_with_slash():
    g = FakeGame([rec(3, 0, 8), rec(5, 0, 12), rec(7, 0, 14), rec(9, 0, 18), rec(11, 0, 25), rec(13, 0, 30), rec(15, 0, 35)],
                 screen=0x13, lock=11)                          # lock on 11: B would walk 13, 15, wrap 3, 5, 7
    a, t = prep(g)
    assert g.lock == WANT and a.state == "track"
    assert [k for k, _ in g.keys][0] in ("/", "b") and len(g.keys) <= 3


def test_destroyed_target_skips_the_attack():
    g = FakeGame([rec(3, 0, 10), rec(7, 0, 15, flags=0x80)], screen=0x13, lock=3)
    a, t = prep(g, secs=10)
    assert a.done and any("already destroyed" in l for l in a.log) and not g.keys


def test_a_game_that_ignores_b_alternates_then_gives_up_by_game_time():
    g = FakeGame(CONE, screen=0x13, lock=3, ignore_b=True)       # the station-1 case: lock stays, B does nothing
    a, t = prep(g, secs=Attack.ACQ_BUDGET_S + 5)
    assert a.mismatches >= Attack.MISMATCHES and a.fallback
    assert any("MISMATCH" in l and "cands" in l for l in a.log)
    assert t >= Attack.ACQ_BUDGET_S                                # capped by game time, not by a press count
    assert sum(1 for k, _ in g.keys if k == "b") < Attack.B_MAX    # backs off once the model is wrong


def test_cycle_without_the_wanted_record_restarts_instead_of_pressing_on():
    class Skipper(FakeGame):                                   # the pick never lands on WANT (as if it were not a candidate)
        def frame(self):
            super().frame()
            if self.lock == WANT: self.lock = 9
    g = Skipper(CONE, screen=0x13, lock=3)
    a, t = prep(g, secs=40)
    assert a.cycles >= 1 and any("repeated" in l for l in a.log)
    held = [x for k, x in g.keys]
    assert max(b - a_ for a_, b in zip(held, held[1:])) >= Attack.HOLD_S - 1.0       # it stopped pressing for a while



# ------------------------------------------------------------------------------------------ recorded flights (open loop)
def _replay(run, record, t0, t1, mode="photo"):
    """Attack prep fed the recorded states of a memlog (the recorded memory shows what the RECORDED keys did, not ours)."""
    import dataclasses
    from conftest import S, need
    from f19.memory import reader as R
    rr = R.ReplayReader(need(S.logs / "memlog" / run, f"memlog {run}"))
    a, ctx, keys = None, FlightContext(ManualClock(0.0)), []
    for t, s in rr.states():
        if not t0 <= t <= t1: continue
        cam = R.read_trackcam_view(rr.emu)
        x, y, fl = next((x, y, fl) for k, x, y, fl in cam.records if k == record)
        st = dataclasses.replace(s, cam=cam, events=(), projectiles=(), target_records=((record, x, y, bool(fl & 0x80)),))
        a = a or Attack("primary", mode, (float(x), float(y)), record, None, 2.0)
        ctx.clock.set(t)
        keys += [(t, str(act)) for act in a.step(st, ctx).actions]
    return a, keys


def test_replay_1793126949_no_b_while_the_wanted_record_is_outside_the_cone():
    """Flight 1793126934 attack 1 (record 2, 97-126 deg off the nose): 40 B presses burned on 44 <-> -1."""
    a, keys = _replay("1793126949", 2, 1793129383, 1793129460)
    assert not any("SELECT_TARGET" in k for _, k in keys)
    assert any("out_of_cone" in l for l in a.log)


def test_replay_1793382037_slash_is_resent_until_the_trackcam_is_up():
    """Flight 1793382022 attack 1: ds:9740 stayed 0, the single "/" had been lost, 40 B were no-ops."""
    a, keys = _replay("1793382037", 2, 1793382891, 1793383100)
    slashes = [t for t, k in keys if "TRACKCAM_VIEW" in k]
    assert len(slashes) >= 5 and not any("SELECT_TARGET" in k for _, k in keys)
    assert all(b - a_ >= Attack.SLASH_GAP_S - 1e-9 for a_, b in zip(slashes, slashes[1:]))
