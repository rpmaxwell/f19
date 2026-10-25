# F-19 refactor plan

Draft, 2026-10-24. This turns the ad-hoc `scripts/` + `analysis/` tree into one layered `f19` package. It is based on a
read-only inventory of the current code (three parallel passes: pilot/runtime, planner/rules, research-vs-product).

## 1. Goals and non-goals

**Goals**
- One installable package, `f19`, with layers that only import downward (section 3).
- Behaviors that *return* commands; exactly one module sends keys.
- A flexible flight plan: a best-case route before flight, then a replan after every deviation, following the
  waypoint fallback chain and then the bail-out chain (section 4).
- Telemetry as a stream that the HUD, the logger and replay all consume. File storage sits behind an interface, so moving
  to a database later is local to that interface.
- Research code moves out of the package and the tests; it is allowed to break. Data moves out of the repo.
- Behavior stays the same through the moves. Bug fixes that change behavior go in separate, labelled steps (section 7).

**Not in this refactor** (the design leaves room; nothing is built): deadlines and schedule-keeping, secret-airstrip
turnarounds, destroying whatever ID'd us, conventional/hunt missions, a mission-manager strategy layer, a database backend,
Docker, lockstep emulation, p(detection) / p(missile hit) constraints.

## 2. What the inventory found (the facts this plan is built on)

- **f19 is not its own git repo.** It sits untracked inside `~/projects`, a repo with no commits. There is no
  `.gitignore`. `logs/` is 4.9 GB (memlog alone 4.0 GB), `dumps/` 239 MB.
- **Packaging:** `pyproject.toml` is named "notebooks"; product code needs only numpy and Pillow (plus stdlib). There are
  51 `sys.path` hacks and flat cross-imports between `scripts/` and `analysis/`. `analysis/f15se2-re` is a nested clone
  of `neuviemeporte/f15se2-re`; `analysis/.venv` is a pyghidra env for research.
- **Product→research edges:** `phases.py:19` imports `ccip` from the probe script `gun_probe.py`. When it is imported,
  `nav.py:53-54` loads `logs/calib/calibration_v2.json` and `logs/radar_sites/sites.json`, which research scripts
  produced. Those two files become versioned package data.
- **Duplication:** the octagonal `game_dist` has six copies with three signatures; `bearing`/`wrap` are in five or more
  files; `K=64` is in ten places. The game-dir path is in ten places. EGAME is loaded three different ways. The radar table
  in `read_mission.RADAR` duplicates `radar_game`. Theater codes are mixed (`CE/NC/LB/PG` vs `LIBYA/GULF`), the
  takeoff-heading rule is implemented three times, and `THEATER_INDEX` is in two files.
- **Layer mixing:** every phase in `phases.py` sends keys directly and some read memory inside `step()`. `nav.py` is
  module-level code: the heading hold, altitude hold, flaps rule and display are inline in the main loop (nav :481-812).
  `MemLog` and `combat_log` read memory. The HUD's `Telemetry` polls memory itself. Several modules reach into private
  members (`db._send`, `radar_game._EG`, `f19_state._offset`).
- **The de-facto arbiter** is merge order in nav :514-639 (later wins): route → takeoff → attack/strafe → landing →
  FighterWatch → MissileWatch → TerrainEscape. Speeds are saved and restored ad hoc (`fighter_saved_kt`, …). Rejoining is
  implicit: along-track/cross-track recomputed every tick plus `next_leg()` looking ahead 8 legs. `step()` signatures
  differ for every behavior; they share a loose "override dict" (`want`, `alt`, `vs_max`, `speed_kt`, `banner`, `done`, …).
- **Time:** wall-clock `now` drives every timer. The `ts` multiplier is read up to six times per tick and divides gains
  and timers. It has read 1 while the game ran 2.4-2.6×. `MissileWatch` already measures the real ratio from the frame
  counter (ds:5520) and `TerrainEscape` assumes 2.5.
- **Planner:** `Router.search(a, b, h0, h_goal)` can already start from any pose toward one goal. The *mission* layer is
  hard-wired to takeoff→targets→landing (`plan.py:411`). `fuelplan.integrate` always starts at t=0 with full fuel, and
  the validator exempts the first and last legs as runways. **A full plan takes 3-8 minutes** (from plan-dir mtimes), so
  in-flight replanning needs a separate fast path. The route search and the fuel integration use different burn numbers
  (fuel_model regimes vs `profiles.nav_lb_min=62.1`).
- **Already there and reusable:** the per-point `fuel_lb` / `t_game_s` in `route.json`; the "plan here" fuel
  interpolation in `nav_tui.py:615-623`; the current-score computation in `nav_tui._pane_score`; `SnapDB` (a fake
  emulator answering PEEKs from a memlog snapshot); `missile_replay` (open- and closed-loop MissileWatch on memlogs).
- **Tests:** none under pytest. Self-checks exist in `planner/checks.py`, `radar_game.py check`,
  `missile_game.py check|replay`, `missile_rules_check.py` and `missile_replay.py`.

## 3. Target layout

```
f19/                                  (own git repo)
  pyproject.toml   name = "f19", src layout, uv, pytest, console scripts
  CLAUDE.md
  src/f19/
    core/       units.py (K, cells, kt<->game, angle conv), geometry.py (one game_dist/bearing/wrap),
                theater.py (Theater enum + per-theater data), types.py (value objects), settings.py, clock.py
    gamedata/   exe.py (one EGAME/END loader, unexepack), wld.py (places, territory), terrain.py (3DG/3DT/3D3),
                missions.py (catalog), data/ (calibration_v2.json, sites.json)
    rules/      radar.py, missiles.py, fuel.py, weapons.py (ordnance, release, ccip), flight.py (takeoff/landing limits),
                scoring.py (rating, ID/bail-out penalties, current score)
    emulator/   automation.py (socket client: public peek/peek_many/key_down/key_up/scan), savestate.py, launch.py,
                capture.py (Capture interface; mac implementation)
    memory/     addresses.py (single table), reader.py (-> GameState, Mission, Threats, EventList)
    planner/    world, route (Router), fuelplan, alertsim, loiter, probes, profiles, report;
                api.py: plan_mission(mission, profile) and route(state, goal, constraints); estimate.py
    pilot/      controls.py, loops.py, behavior.py (protocol), phases/, reactions/, arbiter.py, director.py, runner.py
    telemetry/  frame.py, sinks.py (Csv, Memlog), sources.py (Live, Replay), store.py (FlightStore interface)
    hud/        tui.py (a TelemetrySink; no memory reads)
    cli.py      f19 plan | fly | hud --replay | read-mission | replay-check
  tests/        unit/, golden/, replay/   (small fixtures only; large ones under F19_DATA/fixtures)
  docs/game/    FINDINGS split by topic;  docs/design/  PRDs from notes/ + this plan
  research/     outside the package and the tests; may break
```

Data lives under `F19_DATA` (default `~/f19-data`): `memlog/`, `nav/`, `plan/`, `missions/`, `theater/` (cache),
`fixtures/`. The game install lives under `F19_GAME_DIR`. Both are `Settings` fields, read from the environment.

**Import rule** (enforced by a test that parses imports):
`core → gamedata → rules → planner`; `core → emulator → memory`; `pilot` may use `core, rules, memory, emulator, planner, telemetry`;
`hud` may use `core, rules, telemetry` only. Nothing imports `research/`.

## 4. Key designs

### 4.1 One tick
```
MemoryReader.snapshot() -> GameState
Arbiter.choose(state, ctx) -> active Behavior          (reactions by priority, else the current phase)
behavior.step(state, ctx) -> Command                   (targets: heading/alt/speed/pitch; discrete actions)
Loops (heading/alt/speed) -> key intents ; Controls.apply() -> Emulator
Frame(state, behavior, command, plan_version, leg) -> every TelemetrySink
when a deviation ends or a constraint fails -> FlightDirector.replan(state)
```
`Command` covers today's override-dict keys as typed fields (`heading`, `rate_cap`, `bank_max`, `alt`, `alt_min`,
`vs_max`, `vs_target`, `deadband`, `speed_kt`, `throttle_max`, `no_thr_up`, `cm`, `jam`, `actions`, `banner`, `done`).
Behaviors keep their own state machines. Memory reads that phases do inside `step()` today (events, trackcam,
projectiles, gun, target) move into `GameState` or a `ctx.reader` passed in explicitly.

### 4.2 FlightDirector (the flexible flight plan)
- `MissionPlan`: ordered `Waypoint`s (TAKEOFF, PRIMARY, SECONDARY, LANDING), each with a status
  (pending/reached/skipped). Only TAKEOFF is required. Stops carry an optional `constraints` field that nothing reads yet.
- `reference`: the best-case pre-flight route. `active`: what the pilot flies now. Both are `Route`s with a version.
- After every deviation (any reaction that took control), and when a constraint fails mid-route:
  1. **Mission chain** (first feasible): full remaining mission → primary→landing → secondary→landing → landing.
  2. If no landing is feasible: **score after bail-out ≤ 0** → go straight to the bail-out chain. **> 0** → rank
     earning options (reachable live targets, score loiters) by
     `net = points − (penalty where it leaves us − best bail-out penalty reachable now)`; fly the best positive one,
     then the bail-out chain.
  3. **Bail-out chain** (first feasible): friendly → neutral/ocean → enemy (eject in place; always feasible).
- `first_feasible(state, candidates, constraints)` checks each candidate with the cheap estimate (remaining planned fuel
  from `route.json` + extra distance × lb/km) and runs the real router only on the winner. The constraint list has only
  `FuelConstraint` now.
- **Fast replan path:** `planner.route(state, goal)` uses `Router.search` from a "current pose" piece, with a bounded
  search box and no option product. It then splices the reference route from the next waypoint onward and re-runs the fuel
  integration from the current fuel, time and tanks. The target is a few seconds; measure it.
- The current `next_leg` / `route_aim` rejoin stays as the *tracking* logic inside `FlyLeg`; the director decides *which*
  route is tracked.

### 4.3 Clock
`Clock.now()` returns game seconds. The source is the frame counter (ds:5520, as MissileWatch already uses), falling back to
wall time × measured ratio. Behavior timers and rate estimates use it; key-hold durations stay real time inside `Controls`.
Switching from wall time to game time changes behavior, so it is its own step (WP-7d) after parity.

## 5. Work packages and who does them

Every package lists the files it owns. Two packages never own the same file at the same time. Until the pilot cutover
(WP-7), old entry points keep working through **thin shims**: an old module re-exports from its new home. So
`python scripts/nav.py` keeps flying throughout, and research scripts keep importing what they need.

Execution: after WP-0, each subagent works in its own git worktree (`isolation: worktree`) and returns a branch. The main
session reviews, runs the test suite and merges. A wave starts only after the previous wave is merged.

| WP | What | Owns | Depends on | Who |
|---|---|---|---|---|
| **0** | Repo + scaffolding | everything (moves only) | your decisions (section 8) | **main session**, serial |
| **1** | Safety net: golden + replay tests | `tests/` | 0 | subagent A |
| **2** | `core` | `src/f19/core/` | 0 | subagent B |
| **3** | `gamedata` + `rules` | `analysis/*` product modules → `gamedata/`, `rules/` | 2 | subagent C |
| **4** | `emulator` + `memory` | `dosbox_automation`, `f19_state`, `read_mission`, `savestate`, launch → `emulator/`, `memory/` | 2 | subagent D |
| **5** | `telemetry` | new `telemetry/`; MemLog/_SafeLog/combat_log logic copied from nav (nav itself untouched) | 2 | subagent E |
| **6** | `planner` | `analysis/planner/` → `planner/` | 3 | subagent F |
| **7a** | `pilot` infrastructure | `controls`, `loops`, `arbiter`, `runner`, `behavior` | 4, 5 | subagent G |
| **7b** | Behaviors ported | `phases/`, `reactions/` (from phases.py, missile_watch.py) | 2, 3 | subagent H |
| **7c** | FlightDirector + fast replan | `director.py`, `planner/api.py`, `planner/estimate.py` | 6, 7a | subagent F (continues) |
| **7d** | Cutover + game clock | `scripts/nav.py` → `f19 fly`; Clock switch | 7a-c | main session |
| **8** | HUD as a sink | `nav_tui.py` → `hud/` | 5 | subagent I |
| **9** | Docs + CLAUDE.md | `FINDINGS.md`, `notes/`, `MISSIONS.md` → `docs/` | 0 | subagent J |
| **10** | Verification | — | all | main session + you (live flight) |

**Waves:** 0 → {1, 2, 9} → {3, 4, 5} → {6, 7b, 8} → {7a, then 7c} → 7d → 10.
At most four subagents run at once. 7b can start in wave 3 because the `Behavior` / `Command` types are defined in
`core` (WP-2).

### WP-0 Repo and scaffolding (main session)
- `git init` in f19 (or the alternative you pick). Add a `.gitignore` covering data, venvs, caches, Ghidra projects and
  the big root files. Make a baseline commit of the current tree as-is.
- Move data to `F19_DATA` (`mv`, nothing deleted); move `dumps/`, `saves/`, `LOGCPU.TXT`, `log_cpu_data.json`,
  `MEMDUMP.BIN` too. Delete only `.ipynb_checkpoints/`, `__pycache__/`, the empty `bp_search/` and `MEMDUMP.TXT`, and the
  stale `.claude/scheduled_tasks.lock`, after listing them for you.
- Move research files to `research/` using the classification table (appendix A). `f15se2-re` goes outside the repo, or
  becomes a submodule.
- `pyproject.toml`: rename to `f19`, src layout, `requires-python >=3.14`, deps numpy + pillow, dev deps pytest; research
  extras (notebook, pyghidra, capstone) in a separate dependency group.
- Interim `Settings` with `F19_DATA` / `F19_GAME_DIR`, so moved data is found. The old scripts get a one-line path
  change where they build `logs/...`.
- **Done when:** the baseline commit exists; `python scripts/nav.py --help`, `python analysis/planner <dir>` and
  `nav_tui --replay` still work against the moved data.

### WP-1 Safety net (subagent A)
- `planner/checks.py` and the `radar_game` / `missile_game` checks → pytest.
- **Golden planner tests:** re-plan frozen plan dirs and compare field by field with their `route.json`. Marked `slow`.
  `ce_budejovice_1023_1811` reproduces exactly (checked in WP-0; 10 min). `pg_shiraz_1024_1514` does **not**: the
  baseline code fails on it with `PlanError: no usable attack bearing`, so its route.json came from a different code
  state or command. Pick a second golden that reproduces (a short CE or NC plan), and log the PG failure as a known bug.
- **Replay harness:** feed memlog runs through a `ReplayReader` (built from `SnapDB`) and compare each reaction's
  decisions with the logged nav CSV columns (`want`, `m_action`, phase). Builds on `missile_replay`. Pick 3-4 runs that
  cover takeoff, an attack, a missile and terrain escape; copy them to `F19_DATA/fixtures`.
- Import-rule test (section 3).
- **Done when:** `pytest` passes on the untouched code; the golden and replay baselines are recorded.

### WP-2 core (subagent B)
- `units`, `geometry` (one `game_dist` with clear unit-in/unit-out names; all six copies checked against it in tests),
  `Theater` enum (CE/NC/LB/PG with aliases, WLD file, index, takeoff-heading rule, score multiplier), `types`
  (`Position, Attitude, Systems, Threat, GameState, Mission, Target, RadarSite, Waypoint, Leg, Route, Command, Frame`),
  `Settings`, the `Clock` protocol and wall-clock implementation.
- **Done when:** unit tests pass; nothing else is changed yet.

### WP-3 gamedata + rules (subagent C)
- One EGAME/END loader (replaces the three). WLD parsing and the name-list regex once. Territory, terrain, theaters,
  mission catalog. `radar_game` → `rules/radar`; `missile_game` → `rules/missiles` (memlog utilities → `telemetry` or
  tests); `fuel_model` → `rules/fuel`; `ordnance` + `release_geometry` + `gun_probe.ccip` → `rules/weapons`; `score.py` +
  the bail-out/visual-ID/kill numbers from `notes/score_estimate_for_planner.md` + `_pane_score` → `rules/scoring`.
  `read_mission.RADAR` is replaced by `rules/radar`.
- Old modules become shims. No logic changes.
- **Done when:** WP-1 tests are unchanged and green; rules have unit tests built from the FINDINGS tables.

### WP-4 emulator + memory (subagent D)
- Public socket API (no outside `_send`/`_recv_line`). Batched `peek_many` as the only memory path. Savestate/launch with
  `Settings` paths (keep the macOS pieces behind `Capture`/launch interfaces).
- One address table; `MemoryReader.snapshot() -> GameState` merges `read_state` + `read_flight` + threats + events, plus
  `mission() -> Mission`. The time multiplier and frame counter go in the reader. A `ReplayReader` serves the same API
  from memlog snapshots.
- **Done when:** a live read of `GameState` on a savestate matches the old dicts field by field (a probe script
  in research/ the user runs once); replay tests are green.

### WP-5 telemetry (subagent E)
- `Frame`; sinks: `CsvSink` (today's 32-column nav CSV + events.txt, format kept so research scripts still read it),
  `MemlogSink` (today's windows.json + snap_*.bin); a `FlightStore` interface with a file implementation; a `Run` record
  (id, code version, mission, route versions, settings). Writes never block: a bounded queue and a background writer,
  and failures are dropped (today's `_SafeLog` rule).
- **Done when:** a replayed run through the sinks reproduces byte-compatible CSV/memlog layouts.

### WP-6 planner (subagent F)
- Package imports, no `sys.path`; I/O (subprocess calls, file writes) moved to `cli`/`report`; uses core/gamedata/rules.
- Generalize without changing results: a `current_pose` start piece; `integrate(start_fuel, start_t, tanks)`; optional
  landing; validator runway exemptions made explicit per end. `plan_mission()` produces the same routes as before.
- **Done when:** the golden planner tests are identical.

### WP-7a pilot infrastructure (subagent G)
- `Controls`: the only key sender. Key names in one table (`NOSE_UP="down"`, gear "6", flaps "9", bay "8", brakes "0",
  the throttle scancodes, `/` 0x35, CM keys). Holds and taps, held-key tracking, release on exit and after a savestate
  load (absorbs `release_arrows`/`unstick_keys`), toggle debounce (`set_toggle`).
- `loops`: the heading hold (nav :657-685), altitude hold (:686-731), speed/throttle (`Throttle`), flaps rule and
  sensitivity fix moved out of nav, keeping the same gains.
- `Arbiter`: today's merge order as explicit priorities; one saved-speed mechanism instead of three.
- `runner`: the loop, pacing, signal handling, Frame publishing.

### WP-7b behaviors (subagent H)
- `Takeoff, FlyLeg, Attack, GunAttack, Landing` and `FuelTransfer, FighterWatch, TerrainEscape, MissileWatch` (+ `CmKeys`
  → Controls actions) on `step(state, ctx) -> Command`. FighterWatch, TerrainEscape and MissileWatch are already
  nearly pure; the phases need their `db.tap` calls turned into `Command.actions`.
- **Done when:** each behavior's replay test matches the old decisions.

### WP-7c FlightDirector (subagent F, continuing from WP-6)
- `MissionPlan`, `Waypoint` status, plan versions, `first_feasible`, `FuelConstraint`, the cheap estimate, the
  mission chain → bail-out decision → earning options → bail-out chain (section 4.2), the fast `route(state, goal)`
  path with a timing test, and `EJECT` in Controls.
- **Done when:** unit tests over synthetic states cover every branch of the chain; `route(state, goal)` timing is
  measured and reported.

### WP-7d cutover + clock (main session)
- `f19 fly` replaces `scripts/nav.py` (left as a shim that calls it). Replay parity first, then switch to the game
  `Clock` as its own commit, with the replay diffs explained.

### WP-8 HUD (subagent I)
- `nav_tui` becomes a `TelemetrySink`: it draws Frames and never reads memory. `--replay` reads a `FlightStore` run. The
  score pane uses `rules/scoring`.

### WP-9 docs (subagent J)
- Split FINDINGS.md by the section map in appendix B into `docs/game/<topic>.md` (memory-map, radar, missiles, fuel,
  weapons, landing-takeoff, scoring-awards, terrain-theaters, mission-generation, tooling-history); stale action plans
  go to `research/`. `notes/*` → `docs/design/`. Write `CLAUDE.md`: layers and the import rule, where things live, how
  to run/test, data locations, "research may break".
- No content rewriting beyond headings and cross-links.

### WP-10 verification (main session + you)
- Full pytest including the slow goldens; replay suite; then **one live flight**: plan → `f19 fly --auto` from the
  pre-takeoff checkpoint. `--auto` has never been flown before or after this refactor, so it needs a supervised flight
  regardless; run `probe_controls` equivalents first.

## 6. Risks

- **No live flight in the loop until WP-10.** The replay harness compares decisions, not closed-loop flight. Mitigation:
  WP-4 includes one live read check, and anything that sends keys changes only in 7a/7d.
- **Replay fidelity:** memlog snapshots are sparser than the nav loop, so a match is "same decision within a tolerance".
  WP-1 sets the tolerances.
- **Planner runtime** makes the golden tests slow, and in-flight replans infeasible on the full path. The fast path is
  a new piece of work (7c), measured, not assumed.
- **Shims hide breakage** if left too long. Every shim is listed in WP-0's tracking issue and deleted in 7d.

## 7. Known bugs to fix after parity (separate commits, not during moves)
- The route search and the fuel integration use different burn rates (fuel_model regimes vs `nav_lb_min=62.1`).
- `ts` reads 1 while the game runs 2.4-2.6×; `TerrainEscape.ASSUMED_RATIO`; timers on wall time (fixed by the Clock, 7d).
- `abort_attack` from MissileWatch is never read by nav (nav uses `m_hold`).
- `plan.py:694 takeoff_heading` is dead code; `parse_theater.py` runs at import.
- `GROUND_PER_KT` 0.788 vs `fuel_model`'s 1.852 with no ground factor.
- `pg_shiraz_1024_1514` no longer plans: `PRIMARY: Airplane Shiraz: no usable attack bearing (… no path from
  (17616,10988) to (8000,9536))`, on the baseline code as well.

## WP-0 status (2026-10-24, done)
- f19 is its own repo (`main`); the baseline commit holds the tree as it was.
- Data is in `~/f19-data` (`logs/`, `dumps/`, `saves/`, `raw/`, `game/EGAME.bin` + `END.bin`, `attic/`). The DOSBox-X
  config's `logfile` now points there.
- Research is in `research/`; `f15se2-re` is at `~/projects/f15se2-re`.
- `pyproject` is `f19` (src layout, uv, pytest); `src/f19/core/settings.py` holds the paths. Product scripts insert
  `src/` into `sys.path` until the package move.
- Checked: `nav.py --help`, `planner/checks.py`, all product imports, `nav_tui --replay`, `radar_game check`,
  `missile_game check` (64/64), the Budejovice golden (identical route.json).

## 8. Decisions needed before WP-0
1. **Git:** make f19 its own repo (recommended), or track it inside `~/projects`?
2. **Data root:** `~/f19-data` (recommended) or elsewhere (external disk)?
3. **f15se2-re:** a submodule under `research/`, or a sibling clone outside the repo?
4. **Golden plan dirs:** which missions to freeze as references (suggested: `ce_budejovice_1023_1811` and `pg_shiraz_1024_1514`).

## Appendix A: file classification (from the inventory)
- **Product:** dosbox_automation, f19_state, nav, nav_tui, phases, missile_watch, read_mission, launch_f19.sh;
  analysis/planner/*; radar_game, missile_game, fuel_model, territory, theaters, terrain_3d, ordnance, unexepack,
  release_geometry, mission_catalog (docs generator), parse_theater (thin CLI); `ccip` from gun_probe.
- **Product tools / tests:** missile_replay, alt_schedule, fix_after_load, release_arrows, unstick_keys (merge into
  Controls/savestate), memlog_load and fuel_log (→ telemetry/tools).
- **Research:** every `trial_*`, `stage*`, memdump/inject/breakpoint/tag/probe/survey script, OCR-era altitude hold,
  live probes (acquire_target, probe_controls, watch_load, toggle_flaps, gun_probe), screenshot surveys, savestate.py
  and start_flight.py (AppleScript era), superseded planners, radar_los, all log-analysis scripts, ghidra_scripts,
  egame_re, end_re, ghidra_project, root breakpoint.py/debugbox.py, the notebooks.
- **Delete candidates:** `.ipynb_checkpoints/`, `__pycache__/`, `bp_search/`, `MEMDUMP.TXT`, `.DS_Store`,
  `.claude/scheduled_tasks.lock`.

## Appendix B: FINDINGS.md → docs/game/
tooling-history: 10-75, 415-481 · memory-map: 111-183, 225-242, 295-414, 482-523, 1013-1286 · mission-generation: 524-543 ·
terrain-theaters: 544-560, 1439-1498 · radar: 561-673, 1287-1360 · scoring-awards: 674-752 · weapons: 753-785, 811-898 ·
landing-takeoff: 786-810, 899-940 · fuel: 1361-1409 · design/autopilot: 941-1012 · design/planner: 1410-1438 ·
research/ (stale): 76-110, 184-224, 243-294.
