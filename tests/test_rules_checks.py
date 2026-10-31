"""`python -m f19.cli.memlog_checks radar` and `missiles` (the old `radar_game.py check` and `missile_game.py check`) as pytest.

Fast tests run the checks on the replay fixtures (fixtures/replay/logs/memlog: 3 trimmed runs); the slow ones on the whole
memlog corpus under F19_DATA/logs/memlog, as the command lines do. Expected counts are what the baseline code printed on
2026-10-25: the checks' own pass criteria are reported but not all-pass (pink pulses, duty flag and one AA-9 pair
miss by design of the snapshots), so the tests pin the counts.
"""
import re

import pytest

from f19.cli import memlog_checks as MC
from f19.rules import radar as RG
from conftest import FIXTURES, S, need

REPLAY = FIXTURES / "replay"
FIX_RUNS = ["1792546698", "1792609731", "1792715432"]


def _radar_counts(capsys, dirs):
    MC.radar_check([str(d) for d in dirs])
    out = capsys.readouterr().out
    m = re.search(r"EMV within \+-1: (\d+)/(\d+)\s+pink pulses predicted pink: (\d+)/(\d+)\s+duty flag: (\d+)/(\d+)", out)
    assert m, out
    return tuple(map(int, m.groups()))


def test_site_return_np_matches_scalar(egame, capsys):
    assert RG.check_np(20000)


def test_radar_memlog_check_fixtures(egame, capsys):
    need(REPLAY / "logs" / "memlog", "replay fixtures")
    emv_ok, emv_n, pink_ok, pink_n, duty_ok, duty_n = _radar_counts(capsys, [REPLAY / "logs/memlog" / r for r in FIX_RUNS])
    assert emv_ok == emv_n == 1504                       # EMV formula exact (+-1) on every snapshot
    assert (pink_ok, pink_n) == (217, 222)
    assert (duty_ok, duty_n) == (26316, 28527)


def _missile(capsys, logs, runs):
    all_pass = MC.check(runs, logs=logs)
    res = MC.outcomes(runs, logs=logs)
    out = capsys.readouterr().out
    groups = {(n, int(o)): (int(a), int(b)) for n, o, a, b in re.findall(r"^\s+(\S+)\s+opp (\d): (\d+)/(\d+) pass", out, re.M)}
    ok = sum((p == "hit") == (r == "hit") for *_, p, r, _c in res)
    return all_pass, groups, ok, len(res), res


def test_missile_r1_gate_and_outcomes_fixtures(egame, capsys):
    need(REPLAY / "logs" / "memlog", "replay fixtures")
    all_pass, groups, ok, n, res = _missile(capsys, REPLAY / "logs", FIX_RUNS)
    assert groups == {("AA-9", 3): (3, 4), ("SA-10", 3): (45, 45), ("SA-14", 2): (106, 106)}
    assert not all_pass                                  # the one AA-9 pair (1792715432 clock 1054, cone) misses
    assert (ok, n) == (16, 16)
    assert [(r[1], r[4], r[5]) for r in res if r[1] != "SA-14"] == [
        ("SA-10", "spent", "no hit"), ("AA-9", "hit", "hit"), ("AA-9", "spent", "no hit"), ("AA-9", "hit", "hit"),
        ("AA-9", "overshoot", "no hit"), ("AA-9", "hit", "hit")]


CORPUS_LAST = 1792724414           # the newest memlog run when the numbers below were taken: later flights are left out


def _corpus():
    runs = sorted(p for p in need(S.logs / "memlog", "memlog corpus").iterdir() if p.name.isdigit() and int(p.name) <= CORPUS_LAST)
    if len(runs) != 34: pytest.skip(f"memlog corpus changed: {len(runs)} runs up to {CORPUS_LAST}, the numbers are for 34")
    return runs


@pytest.mark.slow
def test_radar_memlog_check_all(egame, capsys):
    runs = _corpus()
    assert _radar_counts(capsys, runs) == ALL_RADAR


@pytest.mark.slow
def test_missile_check_all(egame, capsys):
    runs = [p.name for p in _corpus()]
    all_pass, groups, ok, n, _ = _missile(capsys, S.logs, runs)
    assert sum(b for _, b in groups.values()) == ALL_MISSILE_PAIRS[1]
    assert sum(a for a, _ in groups.values()) == ALL_MISSILE_PAIRS[0]
    assert (ok, n) == ALL_MISSILE_OUTCOMES


# whole-corpus numbers (baseline code, 2026-10-25, logs/memlog as of WP-0)
# (EMV ok, n, pink ok, n, duty ok, n); 24 EMV misses are in older runs (memlog_checks radar: ~17 s)
ALL_RADAR = (59625, 59649, 2313, 2433, 1145395, 1169997)
ALL_MISSILE_PAIRS = (629, 631)          # R1 gate pass / pairs (missile_watch.py's docstring quotes the same 629/631)
ALL_MISSILE_OUTCOMES = (64, 64)
