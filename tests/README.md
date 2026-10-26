# Tests

The safety net for the refactor (docs/design/refactor_plan.md, WP-1): it pins what the code did BEFORE the
refactor, so each later wave can show it changed nothing.

## Running

```
uv run pytest                 # fast suite, ~22 s
uv run pytest --run-slow      # + golden planner runs and whole-corpus checks, ~13.5 min
uv run pytest tests/replay --update-baseline   # rewrite tests/replay/baseline.json (review the diff!)
```

Tests whose data is missing (no `~/f19-data`, no fixtures, no `EGAME.bin`) are skipped, not failed.

| File | What | Speed |
|---|---|---|
| `test_planner_checks.py` | every check of `analysis/planner/checks.py` as its own case; also pins each check's printed numbers | fast |
| `test_rules_checks.py` | `radar_game.py check` and `missile_game.py check` (R1 gate + outcomes) on the replay fixtures; on the whole memlog corpus with `--run-slow` | fast / slow |
| `test_planner_golden.py` | re-plans frozen plan dirs, compares `route.json` field by field, `report.txt` (minus its header line), `terrain.npz`; the known `pg_shiraz` PlanError | slow (+1 fast) |
| `replay/test_replay_reactions.py` | replay harness: reactions and phases on recorded flights vs the nav log; frozen baseline | fast |
| `unit/pilot/test_fly_live.py` | `f19 fly` live start-up on a fake emulator: check_route, N/S switch, signals release keys, `--plain`, `--no-keys`, eject path | fast |
| `unit/rules/test_radar_systems.py` | the single radar-system table vs the three copies it replaced | fast |
| `test_import_rules.py` | layer rules for `src/f19` (plan section 3), parsed from the source | fast |
| `test_settings.py` | `f19.core.settings` | fast |

Reference copies: `scripts/nav.py` is now a shim; the old nav that the equivalence tests extract code from is
`research/scripts/nav_legacy.py` (frozen; do not edit or run).

`product.py` is the only place tests import product code from. When a wave moves a module (say `phases.py` into
`f19.pilot`), change the import there; the tests and baselines must then pass unchanged.

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

Nav log = nearest epoch, as `memlog_load.py` / `missile_replay.py` pick it; route = the plan dir whose route matches
the flown track and was written just before the flight.

## The replay harness (`replay/harness.py`)

A fake connection (`SnapDB`, as `nav_tui.SnapDB`, plus key recording) answers the `f19_state` readers from a memlog
snapshot; phases get a replay clock in place of `time` (their toggle settle timer). What is compared:

| Replay | Inputs | Compared with | Tolerance |
|---|---|---|---|
| `terrain_escape` | every nav CSV row (all inputs are logged) + nav's `terrain_ahead` (copied: nav can't be imported) | `escape` column per row, `want` while both active, the events | heading 1 deg |
| `terrain_escape_margin150` | same, with `MARGIN_FT` 150 as flown on 2026-10-21 | same | same |
| `fighter_watch` | every snapshot: aircraft + player state from memory | nearest nav row's `fighter`, `want` | heading 15 deg (fan step) |
| `takeoff` | snapshots from the runway | "takeoff done" event time | 5 s |
| `attack_primary/secondary` | snapshots from "attack start" | nearest row's `attack_state`; attack log vs events | - |
| `missile_watch` | snapshots (act mode), the row's `want` as plan | nothing logged (see below): self-baseline | - |

Every replay's metrics are frozen in `replay/baseline.json` and must match exactly; `FLOORS` in the test add loose
"still looks like the flight" bounds. Baseline numbers (current code):

- TerrainEscape, 1792609731: state agrees on 16075/16216 rows (99.1 %), but only 39 of 180 logged escape rows are
  replayed, 4 of 11 events. The flight ran with `MARGIN_FT` 150; the code has 100 since (its own comment says why).
  With 150 the replay gives 16180/16216 (99.8 %), 161/180 rows, 8/11 events: the harness reproduces the flight
  when the parameters match.
- FighterWatch, 1792715432: 760/761 snapshots agree on the state, 229/229 evading snapshots both evade, heading
  within 15 deg on 190/229 (83 %).
- Takeoff: done 0.09 s before nav's event (1792609731), 3.65 s after (1792715432).
- Attack: primary Maverick 63/67 snapshots (94 %), photo 42/44 (95 %), log lines equal nav's. The secondary
  Maverick of 1792546698 sticks in `fire` (37/50, 74 %): the real shot fell between two snapshots, so the replay
  entered `fire` with the Maverick already gone and never sees the count drop. An open-loop artefact, kept as is.
- MissileWatch: 1792546698 10 SA-14s, all doomed (state `watch` only), 10/10 first predictions right;
  1792715432 5 missiles, 5/5, states watch / evade / survival. The decisions per snapshot are in the baseline.

What is NOT comparable: the logged `want` is after every override, nav's route want before a reaction isn't
logged (the harness uses the want of the last row with no reaction active, stale during long evasions); snapshots
are ~1-2 s apart against ~0.25 s nav cycles; MissileWatch's decisions were never logged with a memlog (the memlog
runs end 2026-10-22, MissileWatch and the `m_action` columns start 2026-10-23); landing and the gun attack are not
replayed (no fixture has a full landing in its windows; no strafe flight has a memlog).

## Whole-corpus numbers (slow)

`radar_game.py check` on the 34 memlog runs up to 1792724414: EMV 59625/59649, pink 2313/2433, duty 1145395/1169997.
`missile_game.py check`: R1 gate 629/631, outcomes 64/64. Later runs are left out so the numbers stay fixed.
