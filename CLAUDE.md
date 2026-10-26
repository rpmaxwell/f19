# f19

A mission planner, autopilot and HUD for the DOS game **F-19 Stealth Fighter** (MicroProse) running under DOSBox-X.
We read the game's memory through the DOSBox-X automation socket, plan routes from the game's own decoded rules
(radar, fuel, missiles, scoring), and fly them by sending keys.

## Layout (refactor in progress)

The target is one layered package, `src/f19/` (plan: [docs/design/refactor_plan.md](docs/design/refactor_plan.md)):

| layer | what |
|---|---|
| `core` | units (K = 64 per km), geometry, theaters, value types, settings, clock |
| `gamedata` | EGAME/END loaders, .WLD, terrain (3DG/3DT/3D3), mission catalog |
| `rules` | radar, missiles, fuel, weapons, takeoff/landing limits, scoring |
| `emulator` | automation socket client (PEEK/POKE/keys), save states, launch; no screenshots: screen state comes from memory |
| `memory` | the address table and the reader (-> GameState, Mission, Threats, EventList) |
| `planner` | world, Router, fuel plan, alert sim, loiters, `plan_mission` / `route` API |
| `pilot` | Controls, loops, behaviors (phases, reactions), arbiter, flight director, runner |
| `telemetry` | frames, sinks (CSV, memlog), live/replay sources, flight store |
| `hud` | the TUI (a telemetry sink; no memory reads) |
| `cli` | `f19 plan`, `fly`, `hud --replay`, `read-mission`, `replay-check` |

**Import rule** (to be enforced by a test): `core -> gamedata -> rules -> planner`; `core -> emulator -> memory`;
`pilot` may use `core, rules, memory, emulator, planner, telemetry`; `hud` may use `core, rules, telemetry` only.
Nothing imports `research/`.

The package now holds most product code; `scripts/` and `analysis/` keep shims, tools and the reference copies the
equivalence tests import (`scripts/phases.py`, `missile_watch.py`, `nav_tui.py`; the old nav is `research/scripts/nav_legacy.py`).
Entry points: `python -m f19.cli.fly` (= `scripts/nav.py`), `python -m f19.cli.hud --replay`, `python analysis/planner`,
`scripts/read_mission.py`, `scripts/mission_finder.py`. First live flight: `docs/design/first_flight_checklist.md`. Check the plan's WP status before moving
or restructuring anything, and keep behavior identical during moves; behavior fixes are separate commits.

`research/` holds RE tools, trials, probes and notebooks. It is outside the package and the tests and **may break**.

## Data and the game

- Data lives in `F19_DATA` (default `~/f19-data`: `logs/`, `dumps/`, `saves/`, `game/EGAME.bin`, ...), the game
  install in `F19_GAME_DIR` (default `~/Documents/F-19 Stealth Fighter/MPS/F19`). Both are read by
  `f19.core.settings`. **Never put data, logs, dumps or game files in the repo.**
- Other settings: `F19_AUTOMATION_HOST` / `F19_AUTOMATION_PORT` (default 127.0.0.1:6000), `F19_DOSBOX_BIN` (the
  DOSBox-X fork with the automation socket) and `F19_DOSBOX_CONF` (its F-19 config). The port serves one
  client at a time.

## Running

```
uv run pytest                                   # tests (tests/)
uv run pytest --run-slow                        # + golden full-planner runs (slow, 3-8 min each; added by WP-1)
python analysis/planner <plan_dir>              # plan.json -> route.json + report.txt + route.png
python analysis/planner/checks.py               # planner model self-checks
python -m f19.cli.fly --help                    # live autopilot (sends keys to the running game); scripts/nav.py is a shim for it
python -m f19.cli.fly --replay <memlog dir> --dry-run --quiet   # offline: whole stack on a recorded flight, nothing sent
python scripts/nav_tui.py --replay <F19_DATA>/logs/memlog/<id> [--route route.json]   # HUD on a recorded flight
uv run --group missions python scripts/mission_finder.py --check-only ...   # find a seed for a mission shape (unicorn)
uv run --group missions python scripts/mission_finder.py --verify-snapshot F # check the generator on a START image
```

## Where game knowledge lives

- `docs/game/`: decoded game facts by topic: memory-map, radar, missiles-countermeasures, aircraft, fuel,
  weapons, landing-takeoff, scoring-awards, terrain-theaters, mission-generation, missions (generated catalog),
  tooling-history. `FINDINGS.md` is now only an index of where its old sections went.
- `docs/design/`: the refactor plan, autopilot and planner design, PRDs and plans.
- `research/notes/`: stale or superseded notes, kept for history.
- **New findings go into the matching `docs/game/<topic>.md` with a date** (`## Topic (YYYY-MM-DD)` or a dated
  bullet). Say how it was established (disassembly address, memlog check, live flight) and what is still untested.
  Correct old text with a dated "CORRECTION" / "Superseded" note rather than silently rewriting it.

## Conventions

- Game units: waypoint-table units, **64 per km** (player position >> 5). Angles and units convert in `core`
  (target design); don't add another copy of `game_dist` / `bearing` / `K`.
- Target design for the pilot: behaviors **return commands**; only `Controls` sends keys; memory is read once per
  tick into a `GameState`. Timers move to game time (the `Clock`, frame counter ds:5520).
- The game's own rules (decoded from EGAME/END) win over heuristics; when code and a decoded rule disagree, report it.

## Live flights

- Live flights run **while the user watches** (DOSBox-X window or VNC). Nothing sends keys to a running game
  (nav, phases, probes, mission_finder) unless the user asked for that flight or action in this session.
- Read-only work (planner, replay, memlog analysis, `--check-only`, `--snapshot`) needs no game.
- Stop key-sending scripts with SIGINT/SIGTERM so they release held keys; never `kill -9` them.
