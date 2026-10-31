# f19

A mission planner, autopilot and HUD for the DOS game **F-19 Stealth Fighter** (MicroProse) running under DOSBox-X.
We read the game's memory through the DOSBox-X automation socket, plan routes from the game's own decoded rules
(radar, fuel, missiles, scoring), and fly them by sending keys.

## Before you research the game: check what we already know

Most game mechanics are already decoded and written down. **Look in `docs/game/` before reading disassembly,
EGAME/END, or memlogs to answer a question about how the game works.** Grep it (`grep -rni <term> docs/game/`);
`memory-map.md` is a 500-line reference, so grep it rather than reading it whole. Lookup order:
`docs/game/` -> `src/f19/rules` and `gamedata` (the decoded rules as code) -> disassembly / memlogs.

| question about... | start in |
|---|---|
| detection, radar sites, ranges, stealth | `radar.md` |
| missiles, flares/chaff, evasion | `missiles-countermeasures.md` |
| damage to systems, hit effects, leaks | `memory-map.md` (section "Flight-log variables: fuel, damage, clock, IDs, threats"), `fuel.md`, `landing-takeoff.md` |
| fuel burn, tanks, tankers | `fuel.md` |
| weapons, ordnance, release geometry | `weapons.md` |
| aircraft performance | `aircraft.md` |
| takeoff, landing, crash, eject | `landing-takeoff.md` |
| score, awards, difficulty scaling | `scoring-awards.md` |
| terrain, theaters, map data | `terrain-theaters.md` |
| how missions are generated, seeds | `mission-generation.md`; `missions.md` is the generated catalog |
| memory addresses, structs | `memory-map.md` |
| how a tool or finding came about | `tooling-history.md` |

Other docs: `docs/design/` holds the autopilot and planner design, PRDs and plans (check the date; some are
historical). `research/notes/` is stale, kept for history. `FINDINGS.md` at the root is only an index of where
its old sections went.

**New findings go into the matching `docs/game/<topic>.md` with a date** (`## Topic (YYYY-MM-DD)` or a dated
bullet). Say how it was established (disassembly address, memlog check, live flight) and what is still untested.
Correct old text with a dated "CORRECTION" / "Superseded" note rather than silently rewriting it.

## Gotchas

- **Difficulty matters a great deal.** Radar, missiles, fighters, damage and scoring all change with the game
  difficulty. Never assume something that works or is true on one level holds on another. **Veteran is our default;
  Elite is the challenge / stretch goal.** State which difficulty a result, test or flight applies to, and when you
  generalize, say what you have and haven't checked.
- **Units:** waypoint-table units are **64 per km** (player position >> 5). Convert in `core`; don't add another
  copy of `game_dist` / `bearing` / `K`.
- **The game's own rules win.** Rules decoded from EGAME/END beat heuristics. When code and a decoded rule
  disagree, report it.
- **The automation port serves one client at a time.** A second connection (a probe, the HUD, nav) fails or
  steals the first.
- **No screenshots.** Screen state comes from game memory, not from the display.
- **Game time, not wall time.** Timers use the `Clock` (frame counter ds:5520).

## Architecture

One layered package, `src/f19/`:

| layer | what |
|---|---|
| `core` | units, geometry, theaters, value types, settings, clock |
| `gamedata` | EGAME/END loaders, .WLD, terrain (3DG/3DT/3D3), mission catalog |
| `rules` | radar, missiles, fuel, weapons, takeoff/landing limits, scoring |
| `emulator` | automation socket client (PEEK/POKE/keys), save states, launch |
| `memory` | the address table and the reader (-> GameState, Mission, Threats, EventList) |
| `planner` | world, Router, fuel plan, alert sim, loiters, `plan_mission` / `route` API |
| `pilot` | Controls, loops, behaviors (phases, reactions), arbiter, flight director, runner |
| `telemetry` | frames, sinks (CSV, memlog), live/replay sources, flight store |
| `hud` | the TUI (a telemetry sink; no memory reads) |
| `cli` | `f19 plan`, `fly`, `hud --replay`, `read-mission`, `replay-check` |

**Import rule** (enforced by `tests/test_import_rules.py`): `core -> gamedata -> rules -> planner`;
`core -> emulator -> memory`; `pilot` may use `core, rules, memory, emulator, planner, telemetry`; `hud` may use
`core, rules, telemetry` only. Nothing imports `research/`.

**Pilot design:** behaviors **return commands**; only `Controls` sends keys; memory is read once per tick into a
`GameState`.

**Other trees:**
- `scripts/` holds `nav.py` (a one-line shim for `f19.cli.fly`) and a few tools (`mission_finder.py`, `plan_bench.py`,
  `compare_objectives.py`, `build_roster.py`, `alt_schedule.py`, `unstick_keys.py`, `launch_f19.sh`). There is no `analysis/` any more:
  the planner is `python -m f19.planner[.checks|.new_plan]`, the memlog checks `python -m f19.cli.memlog_checks`, the fuel log
  `python -m f19.cli.fuel_log`, and the rules and data tables `python -m f19.rules.weapons|fuel`, `f19.gamedata.exe|missions|terrain|wld`.
  Don't add new product code to `scripts/`; put it in the package. Nothing under `src/` imports it (enforced).
- `research/` holds RE tools, trials, probes and notebooks. It is outside the package and the tests and **may break**.

## Data and settings

- Data lives in `F19_DATA` (default `~/f19-data`: `logs/`, `dumps/`, `saves/`, `game/EGAME.bin`, ...), the game
  install in `F19_GAME_DIR` (default `~/Documents/F-19 Stealth Fighter/MPS/F19`). Both are read by
  `f19.core.settings`. **Never put data, logs, dumps or game files in the repo.**
- Other settings: `F19_AUTOMATION_HOST` / `F19_AUTOMATION_PORT` (default 127.0.0.1:6000), `F19_DOSBOX_BIN` (the
  DOSBox-X fork with the automation socket) and `F19_DOSBOX_CONF` (its F-19 config).

## Running and testing

```
uv run pytest                                   # tests (tests/); run before calling work done
uv run pytest --run-slow                        # + golden full-planner runs (3-8 min each); run when you touch the planner
python -m f19.planner <plan_dir>                 # plan.json -> route.json + report.txt + route.png
python -m f19.planner.checks                    # planner model self-checks
python -m f19.planner.new_plan --extra-fuel 2    # read the briefing from the running game, write plan.json, plan
python -m f19.cli.fly --help                    # live autopilot (sends keys to the running game)
python -m f19.cli.fly --replay <memlog dir> --dry-run --quiet   # offline: whole stack on a recorded flight, nothing sent
python -m f19.cli.hud --replay <F19_DATA>/logs/memlog/<id> [--route route.json]   # HUD on a recorded flight
uv run --group missions python scripts/mission_finder.py --check-only ...   # find a seed for a mission shape
uv run --group missions python scripts/mission_finder.py --verify-snapshot F # check the generator on a START image
```

First live flight: `docs/design/first_flight_checklist.md`.

## Working rules

- Keep behavior identical during moves and restructuring; behavior fixes are separate commits.
- Read-only work (planner, replay, memlog analysis, `--check-only`, `--snapshot`) needs no game.

## Live flights

- Live flights run **while the user watches** (DOSBox-X window or VNC). Nothing sends keys to a running game
  (nav, phases, probes, mission_finder) unless the user asked for that flight or action in this session.
- Stop key-sending scripts with SIGINT/SIGTERM so they release held keys; never `kill -9` them.
