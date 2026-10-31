# Tests

The safety net. It started as the refactor's (docs/design/refactor_plan.md, WP-1): pin what the code did before, so each wave could
show it changed nothing, with the old code kept as an oracle. **That oracle is retired (2026-10-30)**: the old `scripts/phases.py`,
`missile_watch.py`, `nav_tui.py`, `f19_state.py`, `read_mission.py`, `analysis/radar_game.py` ... are gone, and what they were compared with
is now stored in the tests as digests and literals (recorded from the old code on that day; each is named in its test's docstring) or as
characterisation digests of the new code (loops, arbiter, fly-leg: `GOLD` in the file, refresh with `PRINT_DIGESTS=1 pytest -s`, and say why
in the commit).

## Running

```
uv run pytest                 # fast suite, ~22 s
uv run pytest --run-slow      # + golden planner runs and whole-corpus checks, ~13.5 min
```

Tests whose data is missing (no `~/f19-data`, no fixtures, no `EGAME.bin`) are skipped, not failed.

| File | What | Speed |
|---|---|---|
| `test_planner_checks.py` | every check of `f19.planner.checks` as its own case; also pins each check's printed numbers | fast |
| `test_rules_checks.py` | `python -m f19.cli.memlog_checks radar` and `missiles` (R1 gate + outcomes) on the replay fixtures; on the whole memlog corpus with `--run-slow` | fast / slow |
| `test_planner_golden.py` | re-plans frozen plan dirs, compares `route.json` field by field, `report.txt` (minus its header line), `terrain.npz`; the known `pg_shiraz` PlanError | slow (+1 fast) |
| `unit/pilot/test_pilot_*.py` | phases and reactions on synthetic closed-loop worlds (outcomes asserted); loops, arbiter and fly-leg as characterisation digests over the replay fixtures and synthetic worlds; Controls vs the FakeControls contract on 30k key applies | fast |
| `unit/pilot/test_fly_live.py` | `f19 fly` live start-up on a fake emulator: check_route, N/S switch, signals release keys, `--plain`, `--no-keys`, eject path | fast |
| `unit/rules/test_radar_systems.py` | the single radar-system table vs the three copies it replaced | fast |
| `test_import_rules.py` | layer rules for `src/f19` (plan section 3), parsed from the source | fast |
| `test_settings.py` | `f19.core.settings` | fast |

`scripts/nav.py` is a one-line shim for `f19.cli.fly`. Test helpers: `unit/pilot/pilot_worlds.py` (synthetic worlds, `FakeControls`,
`run_new` / `run_react`), `replay/harness.py` (the recorded flights: `Run`, `SnapDB`, `theater_grid`, a copy of nav's terrain probe and
`build_attacks`).

`product.py` holds the settings and the import path for the tests.

## Fixtures (outside the repo, under `F19_DATA/fixtures`)

```
fixtures/plans/<name>/       plan.json, mission_mem.json (inputs); route.json, report.txt, terrain.npz (expected)
fixtures/replay/             a miniature F19_DATA: logs/memlog/<run>/, logs/nav/<run>.csv + .events.txt,
                             plan/<plan>/ (the route + terrain the flight flew), manifest.json
```

Built by copying (nothing in `logs/` was changed): the plan dirs by hand from `logs/plan/`, the replay set by
`uv run python tests/replay/make_fixtures.py` (snapshot windows are listed in its `RUNS`; ~81 MB).

### Golden plans

| Plan | Theater / settings | Runtime (2026-10-25) |
|---|---|---|
| `ce_kolobrzeg_1022_2250` | CE, Elite, 2 tanks, loiter auto | 171 s alone (250 s with 4 other plans in parallel) |
| `ce_budejovice_1023_1811` | CE, Elite, 3 tanks, loiter auto | 505 s alone (617 s in parallel) |

How they were chosen: copies of five plan dirs were re-planned in parallel on the baseline code. budejovice,
kolobrzeg and liberec_1023_2032 reproduce; stupsk_1022_2025 does not (radar-site rows and the route differ, so its stored route
came from another code state); `pg_shiraz_1024_1514` fails with `PlanError: PRIMARY: Airplane Shiraz: no usable attack
bearing ...`. No non-CE plan dir can be used: the `northcape*` dirs predate `plan.json`, the only PG one fails.
A re-run of budejovice differed from the stored route in the last bit of two coordinates (9e-13 units), so floats
are compared at 1e-9 relative.

`test_pg_shiraz_known_planerror` (fast, ~15 s) reproduces the PG failure with only attack bearing 0; the slow test
runs the whole plan. Both assert the error: when the bug is fixed (plan section 7), update them.

### Replay runs

| memlog run | nav log | route | covers | snapshots kept |
|---|---|---|---|---|
| 1792609731 | 1792609719 | ce_katowice_1021_1458 | takeoff, Maverick attack, terrain escapes (turn, climb) | 404 of 4927 (4 windows) |
| 1792546698 | 1792546685 | ce_test_1019_2337 | photo + Maverick attacks, 10 SA-14 launches, flare | 313 of 2657 (2 windows) |
| 1792715432 | 1792715419 | ce_stupsk_1022_2025 | takeoff, fighter evasion (MiG-31, IL-76), AA-9 / SA-10 missiles, Elite | all 787 |

Nav log = nearest epoch, as `f19.telemetry.memlog_load` / `missile_replay.py` pick it; route = the plan dir whose route matches
the flown track and was written just before the flight.

## The replay harness (`replay/harness.py`)

A fake connection (`SnapDB`, as `nav_tui.SnapDB`, plus key recording) answers the `f19_state` readers from a memlog
snapshot; phases get a replay clock in place of `time` (their toggle settle timer). What is compared:

The recorded flights (`fixtures/replay`) feed the tests above: `test_pilot_arbiter.py` runs the whole new stack (takeoff, Maverick and photo
attacks, terrain escapes, FighterWatch evasion, MissileWatch on SA-14 / AA-9 launches) over all three and digests every tick;
`test_pilot_reactions.py` runs TerrainEscape, FighterWatch and MissileWatch alone (states asserted); `test_pilot_fly_leg.py` checks FlyLeg
against the logged leg and `want`. Until 2026-10-30 a frozen baseline also recorded the OLD code's agreement with the nav log
(TerrainEscape 99.1 %, FighterWatch 760/761 states, Attack 94-95 %, MissileWatch 15/15 first predictions); that went with the old code.

What is NOT comparable: the logged `want` is after every override, nav's route want before a reaction isn't
logged (the harness uses the want of the last row with no reaction active, stale during long evasions); snapshots
are ~1-2 s apart against ~0.25 s nav cycles; MissileWatch's decisions were never logged with a memlog (the memlog
runs end 2026-10-22, MissileWatch and the `m_action` columns start 2026-10-23); landing and the gun attack are not
replayed (no fixture has a full landing in its windows; no strafe flight has a memlog).

## Whole-corpus numbers (slow)

`python -m f19.cli.memlog_checks radar` on the 34 memlog runs up to 1792724414: EMV 59625/59649, pink 2313/2433, duty 1145395/1169997.
`memlog_checks missiles`: R1 gate 629/631, outcomes 64/64. Later runs are left out so the numbers stay fixed.
