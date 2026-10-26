# F-19 refactor plan

Draft, 2026-10-24. Done: WP-0, WP-1, WP-2, WP-9, WP-3, WP-4, WP-5 (2026-10-25); WP-6, WP-7b, WP-8, WP-7a, WP-7c (2026-10-26). Waiting on a live session: `research/scripts/probe_reader.py` (read-only, in flight), `select_by_seed`, finding START's menu/cursor variables, `unstick_keys` memory check. This turns the ad-hoc `scripts/` + `analysis/` tree into one layered `f19` package. It is based on a
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
    emulator/   automation.py (socket client: public peek/peek_many/poke/key_down/key_up/scan), savestate.py, launch.py,
                (no screen capture: screen state comes from memory, see WP-4)
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
  `read_mission.RADAR` is replaced by `rules/radar`. The generator emulation in `scripts/mission_finder.py` (START
  cs:76C8 + cs:B61C-B72E under unicorn, plus decoding) → `gamedata/missions` (`Generator`, `decode`); `unicorn` stays
  in the optional `missions` dependency group.
- Old modules become shims. No logic changes.
- **Done when:** WP-1 tests are unchanged and green; rules have unit tests built from the FINDINGS tables.

### WP-4 emulator + memory (subagent D)
- Public socket API (no outside `_send`/`_recv_line`). Batched `peek_many` is the only read path, and `poke` the only
  write path. Savestate/launch use `Settings` paths.
- **No screenshots in product code** (decided 2026-10-25). They're messy, need macOS screen-recording permission, and
  fill the disk. Screen state comes from memory instead. `scripts/mission_finder.py`'s `screencapture` menu detection
  and `unstick_keys.py`'s capture both go; "do keys work again" becomes a memory check. There is no `Capture` module.
  If a picture is ever needed for debugging, add a `SCREEN` verb that returns the VGA framebuffer over the socket, in
  memory, with no file. That isn't part of this refactor.
- **WP-4a: POKE in the DOSBox-X automation server** (the fork in `~/projects/dosbox-x`, `src/misc/automation.cpp`, next
  to `PEEK`/`OUT`):
  - `POKE <phys hex> <hex bytes>` writes guest memory. It applies on the emulation thread the way queued `OUT`s do,
    replies with an ack, and logs every write.
  - Bounds-check the address (guest RAM only).
  - Client: `Emulator.poke(phys, data)` with a read-back check.
  - Build the fork and test it on the bare-prompt config before F-19.
  - The fork is a separate repo with uncommitted local changes. Commit there on a branch of its own.
- **WP-4b: mission selection by seed.** This replaces the reroll loop and its screenshot checks.
  - `gamedata/missions.Generator` (the unicorn emulation of START cs:76C8 + cs:B61C-B72E, from mission_finder) searches
    seeds offline for one that produces the wanted mission.
  - Live sequence: POKE the seed into START's rand state (u32 at ds:7A50), then send one Select New Mission. Then read
    the mission back from memory (`MemoryReader.mission()`) and check it against the prediction.
  - Menu position: find START's menu/cursor variables by diffing PEEK snapshots while moving the cursor (a short
    research task). Until then, use fixed key sequences, checked only by memory reads.
  - The CLI becomes `f19 mission`; the 2 s/reroll loop is dropped. This is the mission selector.
  - Only the missions tool calls `poke`; flight code never writes memory.
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
- **Widget structure in WP-8, content changes afterwards** (decided 2026-10-25). WP-8 rewrites the HUD as a Frame
  consumer anyway, so it's built as a widget framework from the start:
  - a `Widget` interface: `size hints`, `render(frame, box)`
  - a layout engine: tiled regions, from a layout file chosen before flight
  - an in-flight key to switch between saved layouts
  - today's panes ported **1:1** as widgets, so `--replay` output matches the old HUD
  
  Deciding which data is useful, which is missing and which widgets to drop is a **fast follow** after WP-8, with the
  user. The Frame is extensible, so new data needs a Frame field and a widget, nothing more.
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
- Found by WP-2 (core tests against every old copy):
  - Two kinds of `game_dist`. The float copies (nav, phases, nav_tui, gun_probe) halve with `/2` and have no clamp.
    radar_game and missile_game halve with `>>1` and clamp to 0x7FFF. They differ for odd minima and beyond 512 km.
  - `nav.game_dist` returns km; every other copy returns units.
  - `radar_game.angle` and planner `hdg65536` use exact atan2. `missile_game.atan2g` is the game's polynomial,
    which differs by up to about 0x200 units, and at (0,0) gives 0x8000 rather than 0.
  - `world.KT_KMH` really means ground km/h per kt (1.852 × 0.788). Its comment says 0.784; the value is 0.788.
  - nav's `load_route` drops zero-length legs, and `Route.from_route_json` keeps them. Choose one rule in WP-7.
- Found by WP-1:
  - `radar_game._check` only prints; it asserts nothing. Over the whole corpus, EMV is right on 59625 of 59649
    snapshots.
  - `missile_game.check` returns False: R1 passes 629 of 631 pairs (the misses are AA-9 and SA-14).
  - TerrainEscape `MARGIN_FT` was 150 in the recorded flight and is 100 now. Was the change intended?
  - nav doesn't log the route `want` from before a reaction overrides it. Add it to the Frame in WP-5.
  - `terrain_ahead` and `build_attacks` sit in nav's module-level code; the tests carry copies of them until WP-7.
  - No memlog flight has MissileWatch decisions logged: memlogs end 10-22, and `m_action` started 10-23.
  - Goldens are `ce_kolobrzeg_1022_2250` (171 s) and `ce_budejovice_1023_1811` (505 s); there is no non-CE golden.
    `ce_stupsk_1022_2025` doesn't reproduce either.
- Found by WP-3:
  - **The PG territory grid is shifted by 7 bytes.** The name-list search finds "Abbas", not "Bandar Abbas", so the
    grid ends in the bytes `"Bandar"` and 115 of the 256 cells get the wrong side. It affects the planner's PG terrain
    and territory, and **may explain the `pg_shiraz` PlanError below**. Kept as-is during the move.
  - `terrain_3d` crashed (UnboundLocalError) from WP-0 until WP-3 fixed it (7761a93). The cause was WP-0's path rewrite
    naming the settings `S` while a local `S` shadowed it. No other file has the collision.
  - `radar.md` gives EMV 56 at 10000 ft Veteran; the code gives 55 (it floors `alt // 3` first). One of them is wrong.
- Found by WP-7c (offline; the FlightDirector in `pilot/director.py`, plan versions in `pilot/plan_state.py`):
  - The chain is implemented as designed: the estimate only rules candidates out, and every candidate that passes is
    confirmed with the real router and its integrated fuel. Real-planner timings on the golden CE plan: rejoin replan
    0.5-2.3 s, landing-only 0.6-1.7 s, a whole bail-out chain 1-2.4 s.
  - Replans run in one daemon worker thread. `on_tick` returns in about 8 ms and the plan is adopted by a later tick.
    The worker shares the GIL, so the loop saw gaps up to about 230 ms (one extra 0.15 s tick).
  - **Softened rule:** if the whole chain is lost to BudgetExceeded, the director retries up to `max_retries` times
    before it ejects in place. With no event list yet it waits instead of ejecting on a guessed score of 0.
  - Additive changes outside its files: `Runner._direct` calls `routes.on_tick(...)` when the provider has it, and
    `cli/fly.py` has a `--director` flag.
  - **Open questions:** the friendly and neutral bail-out multipliers (only enemy x2/8 is confirmed; the ranking assumes
    3/8 for both, and matters only when the score is above 0), score loiters as earning options (TODO in
    `_loiter_options`), alternate airfields for landing, whether bail-out targets a cell's nearest interior point
    (built) or its centre, and whether a waypoint skipped for fuel stays skipped (built).
  - **WP-7d must add the EJECT key to Controls** (`Action.EJECT` raises ValueError and would crash the runner's release
    path). The runner passes `event_list` (6-tuples, refreshed every 1 s) to the director.
- Found by WP-7a (offline-verified against nav's own code; nothing run live):
  - Controls vs the FakeControls reference: identical key streams on 28,677 applies. Loops vs nav's inline code:
    every tick equal over ~33k ticks. Arbiter vs nav's merge: equal on all three replays and the synthetic overlaps.
  - **One intended behavior difference: overlapping reactions.** nav's three saved speeds fought each other. A missile
    survival re-pressed full power every tick under a fighter evasion, an evasion's 165 kt target overrode an escape
    climb's full power, and a fighter restore could leave 165 kt stuck. The arbiter uses one mechanism: the top request
    wins, and the target from before the episode is restored. Documented in arbiter.py.
  - Short taps and scancode taps still block for their hold time, as in nav. Only roll keys, the pitch hold and the
    trigger are non-blocking. Toggle debounce uses the tick's clock. `--fixed` isn't ported.
  - **WP-7d must still:** make nav.py a shim for `f19.cli.fly`; port nav's start-up checks into `live()` (`check_route`
    is written but untested); switch to the game Clock as its own commit; decide the Controls API for EJECT and
    DESIGNATE (both raise ValueError now); wire the FlightDirector in place of StaticRoute (WP-7c); move
    `parse_missile_world` and `terrain_ahead_probe` into memory/rules, and the radar-system table into gamedata.
  - **First supervised flight must check:** key latency and hold durations in real time; Settle() plus
    `Controls.after_load`; hand-over and roll-level on exit; the HUD fed by the real sink; memlog timing; missile-world
    read cost per tick; the sensitivity Ins loop; SIGINT/SIGTERM release; auto takeoff, attack and landing (never flown).
- Found by WP-6:
  - `route(state, goal)` timings over 64 synthetic airborne CE states:
    - rejoining the reference route: median 1.9 s, p90 4.8 s, max 12 s
    - straight to landing: median 1.2 s, max 5.0 s
    - 8 of the 64 raised PlanError, all with the start or the goal against a ridge
  - The planner's `context()` takes 1.5-3 s and is built once per flight.
  - **The cheap fuel estimate is a lower bound.** On the route it is within 0.6 lb. Off route it is optimistic: median
    +165 lb for a rejoin, +1.0k lb straight to landing, worst +4.6k. So it can only rule candidates out; a candidate
    that passes still needs the real router (WP-7c must do that).
  - A rejoin within about 2 turn radii (40 km) often joins through a loop, so `route()` tries farther rejoin points. The
    real fix is in expand/repair, and that would change mission plans: post-parity.
  - There's no radar-system→kind table in rules yet (it stays local to planner/world). The planner keeps `K = 64.0`.
- Found by WP-7b (the contract for WP-7a): `tests/unit/pilot/pilot_equiv.py` `FakeControls` is the reference for
  Controls and the loops:
  - Order within a tick: trigger release, then pitch, then actions, then trigger press.
  - Actions are 0.2 s taps. Throttle and "/" are sent as scancodes.
  - A `Toggle` taps only when the flag disagrees and that key's last tap was at least `settle_s` ago. The debounce
    table `ctx.toggled` is shared by all behaviors.
  - Pitch band: 0.6 s nose up below `pitch_min`, 0.5 s nose down above `pitch_max`, then a 1.0/ts s gap. `pitch_deg`
    is one tap of clamp(|err|/6/ts, 0.08, 0.5) s.
  - The gun trigger is held while `gun_fire` is set, and re-pressed on `gun_repress`.
  - Speed: max/idle clear the target, then `speed_kt` sets it. A reaction's speed is temporary, and the arbiter restores
    the target when the reaction stops.
  - Known live differences:
    - timers now use the tick's `now` (the old code drifted by the length of blocking taps)
    - FighterWatch uses the real `opp`
    - FlyLeg keeps nav's rule of dropping zero-length legs
    - `--fixed` isn't ported
- Found by WP-8: the HUD adapter (`cli/hud.py`) supplies data that Frame/GameState don't carry yet. That's nav's
  per-cycle view (phase, banner, leg, want, xtrack, holds, ...), the game's event list, read errors, and a context of
  territory, terrain, places, mission and route. The radar-system table is a copy of `read_mission.RADAR` and belongs in
  gamedata. WP-7a/7d should put these in the Frame. In replays the HUD clock shows the recorded time.
- Found by WP-5: legacy logs keep wall time only (no game time), event times rounded to whole seconds, a memlog paired
  with its nav log by nearest epoch (~12 s apart), and no route `want` from before overrides. New runs written through
  `FileFlightStore` record the route want, the behavior and the plan version.
- `pg_shiraz_1024_1514` no longer plans: `PRIMARY: Airplane Shiraz: no usable attack bearing (… no path from
  (17616,10988) to (8000,9536))`, on the baseline code as well.

## WP-7d status (2026-10-26, offline part done; live checks and the clock switch remain)
- **Done (branch wp7d-cutover):**
  - `scripts/nav.py` is a shim for `f19.cli.fly.main` (same flags; `--fixed` refused). The old nav is
    `research/scripts/nav_legacy.py`; tests read it (nav_legacy.py extractor, core geometry / units / types tests). Not run live.
  - `scripts/phases.py`, `missile_watch.py`, `nav_tui.py`, `f19_state.py`, `read_mission.py` are unchanged: the equivalence
    and replay tests import them as the reference. They can go once the old-vs-new comparisons are retired.
  - `fly.live()` takes nav's start-up: connect retry, settle, `check_route` (route vs mission in memory, N/S switch,
    takeoff-point distance, `--land-now` window), ALT file reset, memlog, CSV, HUD or `--plain` (`cli/plain.py`, with the
    HUD falling back to it on failure), SIGINT/SIGTERM from the first line of `live()`, `main` returns 130. Tested in
    `tests/unit/pilot/test_fly_live.py` on a replayed flight with a fake emulator and real signals.
  - New `--no-keys`: live read-only flight (Controls over a recorder, keys written to `logs/nav/<id>.keys.txt`).
  - Copies moved: radar-system table -> `gamedata/radar_systems.py` + `rules.radar.radar_systems()` (hud, fly, planner/world
    use it; the golden plans are unchanged), `parse_missile_world` -> `rules/missiles.py`, `terrain_ahead_probe` ->
    `rules/terrain.py`. Equality tests: `tests/unit/rules/test_radar_systems.py`, `test_pilot_runner.py`.
  - EJECT path tested offline (director Order -> runner -> Controls -> one Shift+F10 scancode, clean exit). DESIGNATE still
    has no key and raises ValueError.
  - Copies still in src/f19: `rules/radar.py` rough_ceiling (copy of analysis/planner/loiter, until WP-6 retires it);
    `gamedata/mission_gen.py` has its own .WLD read (TODO use gamedata.wld); START cursor variables unknown (TODO WP-4b).
- **Left:** the game-clock switch (WallClock -> game Clock, its own commit with replay diffs explained); the first live
  flights in `docs/design/first_flight_checklist.md` (Shift+F10, POKE and key latency are untried live); deciding when to
  delete the legacy scripts.

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
