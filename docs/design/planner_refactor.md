# Planner refactor: faster planning, and loiter as an opt-in

Draft, 2026-10-28. Status: **implemented on branch `planner-refactor` (WP-A to WP-F), uncommitted** (see "Results").

## Goal

Make `plan_mission` fast enough that a long planning time before takeoff is cheap and, in flight, comparing *options*
(not just replanning one route) is practical. Today a full pre-flight plan takes **~190 s**; target **under ~60 s single process
and under ~20 s with a process pool** on the Kolobrzeg plan (estimates from the profile below, to be confirmed by the benchmark in WP-A).

Second goal: stop planning a loiter with every plan. Loiter becomes an opt-in for short, straightforward missions (WP-F).

**Non-goals** (see "Not in this refactor"): the career-points objective, any change to what the planner considers a good route,
the aircraft/fighter model, new in-flight behaviour.

## Ground rules

- **Behaviour-identical until WP-F.** WP-B to WP-E must produce the same `route.json` (floats within 1e-9 relative) as the baseline. CLAUDE.md: restructuring and behaviour fixes are separate commits. WP-F is the only deliberate behaviour change and is its own commit.
- **Start from a clean baseline.** At the time of writing the working tree has *uncommitted* planner work (SA-14 hard-wall changes in `world.py`, `planner.py`, `router.py`, `alertsim.py`, `profiles.py`, `report.py`, `cli.py`, and `tests/test_planner_golden.py`).
  Do not start until the owner has committed it or named the baseline. Record the commit you measured against in this file.
- Oracle: `uv run pytest` (fast), `uv run pytest --run-slow` (the two golden full plans, ~3-10 min each), `python -m f19.planner.checks`.
- Difficulty: the profile below is one **Elite** plan on a 10-core Mac. Veteran is the default level; re-measure on a Veteran plan (e.g. `ce_liberec_1027_0916`) before claiming a general speedup. Option counts and loiter feasibility differ per plan.
- Data lives in `F19_DATA` (fixtures: `$F19_DATA/fixtures/plans/*`). Never put data in the repo.

## What is slow (measured 2026-10-28, `ce_kolobrzeg_1022_2250`, Elite, `loiter: auto`)

Instrumented `api.plan_mission` (188-190 s). Stages nest, so shares overlap.

| stage | calls | time | share |
|---|---|---|---|
| `Planner.attack_options` (ranks 12 bearings per target) | 24 | 122 s (5.1 s each) | 64 % |
| `Router.reduce` (corner DP) | 696 | 97 s | 51 % |
| of which `World.sa14_clear` | 699,524 | 30 s | 16 % |
| `Router.search` (A\*) | 744 | 50 s | 26 % |
| `Planner.plan` / `plan_with_loiter` | 32 | 62 s (1.9 s each) | 33 % |
| `Router.repair` | 32 | 20 s | 11 % |
| `fuelplan.integrate` | 64 | 12 s (0.19 s each) | 6 % |
| `report.summarise` (alert sim, runs once) | 1 | 1.6 s | 1 % |

Other measurements: Planner build 1.3 s; one `plan()` is 3.3 s cold and 0.57 s with caches warm; in-flight `api.route()` is ~1.1 s (rejoin) and ~3.3 s (goal chain).

**Repeated work.** 744 `search` calls have only **126 unique** inputs (5.9x); 696 `reduce` calls have **106 unique** inputs (6.6x). `reduce` computed once per unique input would be ~20 s instead of 96 s.
`search` is already absorbed by an `lru_cache`, but `reduce`, `expand` and `route_cost` are recomputed.

**Pointless options.** Of 32 options planned, **24 are loiter options and every one fails only on `fuel at landing`** (short by 1,700-12,000 lb). They cost a full `plan_with_loiter` each, and
loiter bases drive about 18 of the 24 `attack_options` calls. 8 options are valid.

**In flight vs pre-flight.** In-flight replanning of one route (`api.route`) is already 1-3 s. The 190 s is the option search. The gap is comparing alternatives in flight.

Re-run the instrumentation in WP-A rather than trusting these numbers: wrap `Planner.plan`, `attack_options`, `Router.search/reduce/expand/repair`, `fuelplan.integrate`, `alertsim.simulate`, `World.sa14_clear`
with call counters and `perf_counter` totals, and count unique `search` and `reduce` inputs (round coordinates to 0.1 unit; include `h0`, `h_goal`, `margin_km`).

## Work packages

### WP-A: baseline and benchmark harness
- Commit a small benchmark tool (a script under `scripts/` is fine; it is a tool, not product code) that prints the table above for a given plan dir, plus unique-call counts and the option summary (how many valid, how many loiter, how many fuel-only failures).
- Run it on all four fixtures that still plan (note: `ce_budejovice` needs `sa14: soft` and `pg_shiraz` does not re-plan on the current rules, see `tests/test_planner_golden.py`). Put the table in this file.
- Confirm the goldens pass on the chosen baseline. Save the baseline timings; every later WP reports before/after.
- Acceptance: one command reproduces the table; goldens green.

### WP-B: leg-level memoization (identical output)
- Cache `search -> reduce` (and the `expand` joins where inputs repeat) per leg, keyed by `(start, end, start heading, goal heading, margin_km, ends)` rounded as `_search` already rounds. Share it between `attack_options` and `plan`.
- **Audit hidden state before caching `reduce`.** It reads `self.W` (`segment_clear`, `sa14_clear`), `self.sa14_hard`, `self.P` and possibly per-goal SA-14 wall state set by `_sa14_wall` / the SA-14 work in the working tree. The key must include everything it reads, or the cache must be per (Router, wall-state). A stale hit is a silent wrong route.
- Scope the cache to a Router instance (as the existing `lru_cache` effectively is, keyed on `r`), and clear it if the World or profile changes.
- Acceptance: golden `route.json` identical; unique-call counts equal the cache's hit statistics; `plan_mission` time down (expect `reduce` ~96 s -> ~20 s).

### WP-C: prune options that cannot be valid (identical chosen route)
- Before planning a loiter option, compute an **admissible lower bound** on the fuel it needs (straight-line km takeoff -> loiter centre -> landing at the cheapest lb/km regime, plus the baseline no-loiter plan's burn)
  and skip it when the bound exceeds `fuel loaded - landing_reserve_lb`. A lower bound never rejects a feasible plan, so the chosen route is unchanged.
  `planner/estimate.py` already has a cheap fuel estimate (`estimate_direct`) to reuse.
- Skip the `attack_options` calls under pruned bases too.
- **Report:** a skipped option must still appear, as `skipped: ... needs >= N lb more fuel than carried`. The golden compares `report.txt` verbatim and it lists every option under `also:`, so this commit changes the report text.
  Update the golden comparison to ignore the option list (or compare it separately), and assert the chosen option string and `route.json` are identical. Say so in the commit message.
- Acceptance: chosen option and `route.json` identical on all goldens; options planned drops from 32 to ~8 on Kolobrzeg.

### WP-D: parallel option planning (identical output)
- Options are independent and deterministic. Plan them in a process pool; build one Planner per worker (1.3 s) from `(spec, mission)`; return compact, picklable results (route points, errors, fuel summary, cost, tag).
- macOS defaults to `spawn`: do not rely on `fork` and do not pickle the Planner. Keep the final sort key unchanged and break ties by option index so results do not depend on completion order.
- `F19_PLAN_WORKERS` (default `min(cpu_count - 1, options)`; `1` disables). Keep the `progress(msg)` callback working.
- Also consider splitting `attack_options` across bearings (12 independent stretches).
- Acceptance: identical `route.json` for `F19_PLAN_WORKERS=1` and the default; speedup measured on the benchmark.

### WP-E: vectorize the `reduce` DP inner loop (identical output)
- `reduce` tests every `(i, j)` chord with Python-level `segment_clear` and `sa14_clear` (700k `sa14_clear` calls, 30 s). Batch the sample points of all chords from one `i` into a single numpy lookup, or precompute per-cell masks.
- Property test: for random segments on real terrain, the new implementation equals the old one exactly (keep the old one in the test as the reference).
- Acceptance: `reduce` output identical on every leg of every golden; measured gain.

### WP-F: loiter becomes opt-in (BEHAVIOUR CHANGE, its own commit)
Why: loiter is a gimmick for most missions. It is "NOT flown" live (`docs/design/planner.md`), every plan in the fixtures that asks for it fails on fuel, it adds planning time to every plan,
and at Elite more time aloft is more fighter exposure (`docs/game/missiles-countermeasures.md`, 2026-10-28), which the points the loiter earns do not obviously cover.
- `planner/profiles.py` already defaults `loiter` to `"off"`. The loiter is on because `planner/cli.py` `new_plan` defaults `--loiter` to `auto` and writes it into every `plan.json`, and because the `fuel` preset sets `loiter="auto"`.
  Change the CLI default to `off` and remove `loiter` from the `fuel` preset. Keep `mountain_loiter` (a mode that exists to loiter) and `"loiter": "auto"` / `[cx, cy]` as explicit opt-ins.
- **Opt-in as a post-pass, not part of the option product.** Plan the mission without a loiter first. Only if the plan has spare fuel above the reserve do we consider a loiter, as one extra planning pass on the chosen option:
  eligible when (a) spare fuel covers at least one lap plus the detour to a quiet cell (use the WP-C bound, do not invent thresholds), and (b) a radar-quiet enemy cell is within a detour that bound allows.
  State why a loiter was or was not considered in the report. "Short, straightforward" is the outcome of that test, not a mission label.
- Update docs (`docs/design/planner.md`, the `new_plan` help text) and `tests`.
- Expected effect on the goldens: the chosen route is unchanged for both (their loiter options are all invalid), the `also:` list shrinks. Verify, do not assume; any golden whose chosen route changes needs the owner.
- Acceptance: a plan.json without `loiter` plans no loiter; `loiter: auto` plans one only if the eligibility test passes; goldens' `route.json` identical or the difference explained.

### WP-G (later, separate plan): in-flight option comparison
Not part of this refactor. Directions to scope after A-F, in order of expected value:
1. A backward **cost-to-go field per goal** (primary and secondary run-ins, landing fixes) over the (cell, heading) lattice, computed before takeoff. It gives the A\* an exact admissible heuristic (today it is straight-line x the cheapest km price, which is weak where radar dominates) and turns an in-flight route into following the field.
   After WP-B and WP-C the remaining A\* is ~126 unique searches at ~0.35 s each (~44 s); the gain from this is unmeasured.
2. **Precomputed contingencies** (long pre-flight time is fine): an abort-home route from every N km of the plan, the primary-only variant, a route for a fuel leak. In flight these are a lookup.
3. Vectorize `alertsim._run` across its 96 scenarios (1.6 s per route now). It only matters when something scores many candidates (see the points objective below).

## Order and dependencies

A first. B, C, E are independent of each other and each keeps output identical; do B and C first (largest gain, lowest risk). D after B (workers should share the same cache design). F last and alone.

| after | expected plan_mission time (Kolobrzeg, Elite) | confidence |
|---|---|---|
| baseline | ~190 s | measured |
| WP-C | ~50 s | estimate |
| WP-B + C | ~25-30 s | estimate |
| + WP-D (8 workers) | < 10 s | estimate |

Report measured numbers in the PR; do not quote these.

## Risks

- A stale memoization hit (WP-B): the main correctness risk. Audit state; add a debug mode that recomputes and compares on every hit and run the goldens with it on.
- A pruning bound that is not truly a lower bound (WP-C). Test it against the fuel of every option that *is* planned: the bound must never exceed the planned fuel use.
- Nondeterminism from parallelism (WP-D): compare `route.json` across worker counts and across repeated runs.
- The golden `report.txt` comparison will change in WP-C and WP-F (option list). Handle it explicitly and say so; do not loosen the route comparison.
- Plans differ: the 24-of-32 loiter pruning is specific to a plan whose loiter cells are 400 km off the route. Measure a Veteran plan.

## Not in this refactor: the career-points objective

**Proposal (to confirm with the owner): do not bundle it.**
- Acceptance of WP-B to E is "identical output". The points objective deliberately changes the chosen route (the notebook already finds the stored Kolobrzeg choice dominated within its candidate set), so combining them removes the golden oracle and makes any regression indistinguishable from an intended change.
- It rests on inputs that are thin or unmeasured: the fighter hit rate comes from 7 Elite flights, Veteran fighters have not been observed, the reaction effect is unmeasured, `p_unlevel` is a decision not a measurement, and the leak truncation is approximate (`notebooks/planner_walkthrough.ipynb`, last section; `docs/game/missiles-countermeasures.md`, 2026-10-28).
- It needs this speed-up first: scoring a candidate costs ~1.6 s (alert sim) and the optimiser wants many candidates.

What this refactor should do to prepare, without changing behaviour:
1. Factor the final ranking in `Planner.run` into a function `rank(results) -> best` whose default is the current sort key. That is the seam a points objective plugs into.
2. Keep `run()` returning every planned option with its timed fuel walk, so a scorer can evaluate them without re-planning.

Suggested path for the follow-on: shadow mode (compute the points ranking next to the current one, log where they disagree, use only the current one), backtest against the logged flights and fixtures, then switch the default, Veteran first (the hazard there is observed zero, so it is the easier case to validate) and Elite once more Elite flights exist.
λ is the risk parameter: with ejection safe it prices only ejecting un-level, so it needs a decision on what the level-check guarantees before it means anything.

## References

- Profile and option analysis: this file's measurements; the instrumentation approach above.
- `docs/design/planner.md` (planner model), `docs/design/refactor_plan.md` (the package refactor, WP numbering differs).
- `tests/test_planner_golden.py` (goldens, comparison rules), `src/f19/planner/checks.py`.
- `notebooks/planner_walkthrough.ipynb`: stage-by-stage walkthrough of the planner and the points-objective exercise.
- `src/f19/planner/api.py` (`plan_mission`, `route`), `planner.py` (`run`, `_run`, `attack_options`, `plan_with_loiter`), `router.py` (`search`, `reduce`, `repair`), `estimate.py` (cheap fuel estimate).

## Results (2026-10-28, 10-core Mac, 4 performance + 6 efficiency cores)

Baseline = the uncommitted working tree at the start (main 6283fba plus the SA-14 work), snapshotted before any change.
`scripts/plan_bench.py <fixture>` reproduces the table (`--sa14 soft` for budejovice); `route.json` compared to the baseline's own output.

| fixture | baseline | new, 1 process | new, default workers (9) | route.json vs baseline |
|---|---|---|---|---|
| ce_kolobrzeg_1022_2250 (Elite) | 187 s | 46 s | 23 s | identical (1e-9) |
| ce_liberec_1027_0916 (Veteran) | 303 s | 89 s | 40 s | identical |
| ce_budejovice_1023_1811 (Elite, sa14 soft) | ~300+ s | 81 s (earlier run, default workers) | golden test 40 s | identical |
| pg_shiraz | does not re-plan (known) | | | |

Both slow goldens and `test_plan_is_identical_for_one_and_several_workers` (1 vs 3 workers: same route.json and report) pass.
`F19_PLAN_CACHE_CHECK=1 F19_PLAN_NO_PRUNE=1` on kolobrzeg planned all 32 options with every memo hit recomputed: identical route, and the
loiter fuel bound held for all 24 loiter options. These targets (<60 s one process, <20 s with a pool) are met in one process, but not quite with the pool (23 s).

What was done, and where it differs from the plan above:
- **A**: `scripts/plan_bench.py`.
- **B**: memos per Router (`search`, `reduce`, `seg_cost`) and per Planner (`attack_options`), keys include `sa14_hard`; the debug mode is `F19_PLAN_CACHE_CHECK=1`.
- **C**: `Planner.loiter_fuel_need` / `loiter_bound` (straight lines through takeoff, targets, a racetrack vertex x min laps, landing; needs `nav_hold_kt`). A loiter combination is skipped only
  when some option without a loiter is valid (otherwise a pruned option could have been the "closest to working" one shown). The bound is looser than hoped: 16 of 32 options were skipped on kolobrzeg, not 24;
  reports list them as `also: ... skipped, needs >= N lb more fuel than carried`. Since F the loiter is no longer in the option product at all, so this matters for mountain_loiter and the post-pass gate.
- **D**: not `multiprocessing` (macOS `spawn` re-imports an unguarded `__main__`): persistent subprocesses, `planner/pool.py` + `worker.py`, pickled over pipes. Workers run searches first (the parent keeps the memo and
  broadcasts it, so no leg is searched twice), then plan options; results are ordered by option index. `F19_PLAN_WORKERS`.
- **E**: `Router.seg_costs` (one radar call per DP column; `CACHE_CHECK` compares with the scalar), `World.sa14_clear_many`, and the A* loop on plain lists with a precomputed heuristic (same arithmetic; ~20 % faster).
  `segment_clear` was left alone (cached, 2 s).
- **F**: `new_plan --loiter` defaults to `off`, the `fuel` preset no longer sets it, and a loiter is `Planner._loiter_pass` on the chosen plan, gated by the bound against the plan's spare fuel; the log says why per cell.
  With `loiter: auto` the ranking is still by points, so the goldens' chosen routes did not change (their loiters are unaffordable: 694 lb spare, 1,027+ lb needed). A plan with loiter off now ranks by route cost only.
- Seam for the points objective: `Planner.rank(results)`. `run()` still returns every planned option.
- Golden test: `report.txt` is compared ignoring the option list, the option / attack-bearing / loiter log lines and the per-leg "no corridor" notes (new ones must be a subset); `route.json` is unchanged-strict.

Remaining cost is the A* (about 70 unique searches, 0.4 s each). A cost-to-go field (WP-G) is the next step.

## Points objective and enemy aircraft in the planner (2026-10-28)

Follow-on to "Not in this refactor: the career-points objective". Status: **implemented on branch `planner-refactor`, uncommitted, default unchanged** (`objective: cost`).
Test run, not flown. The golden `route.json`s are unchanged because the default is still `cost` (see the end of this section for the test status).

**What it is.** The profile key `objective` (or `plan.json "objective"`) picks what `Planner.rank` sorts the valid options by:

| objective | ranks by | code |
|---|---|---|
| `cost` (default) | route cost, the fuel / transit-km proxy: what the planner always did | `Planner.rank` |
| `points` | E[career points] - lambda P(death), hits at a constant rate per airborne minute (`points.HIT_PER_MIN`: Veteran 0.0003, Elite 0.0288, counted from the logs); the notebook's "Beyond fuel" section | `planner/points.py` |
| `aircraft` | the same value, hits from a fighter model on the route's own network alerts, aspect, speed and terrain | `planner/fighters.py` |

- Points and aircraft add one option to the product: **primary only** (order `("primary",)`, planned with `roles={"primary"}`; the roles go through `plan_with_loiter`, the worker message and `r["roles"]`). The score and the report use the roles the plan flies.
- Each valid option costs one `alertsim.simulate` (~1.6 s, in the parent) to be scored: +17 to +25 s on a 25-40 s plan. Not parallelised yet.
- `alertsim.simulate()` now also returns `net`: per scenario, the game seconds at which a network alert was raised (site seen above 0x7F, SA-14 launch). Nothing else in its output changed.
- Report: the chosen line and each `also:` line carry E[points], P(bay hit), expected hits, minutes (and fighter shots / visual IDs) when the objective is not `cost`.
- `scripts/compare_objectives.py <fixture> [--opponent X] [--hunt near_mean=6,max_shots=4]` plans under each objective and scores every chosen route under both points models.

**The fighter model** (`fighters.py`; rules from `docs/game/aircraft.md`). Per alertsim scenario: airborne fighters are drawn (Poisson, 1.9 within 100 km and 1.4 in 100-200 km, from the 22-flight CE counts; type from the theater roster); a network alert makes them fly
straight at us (pure pursuit, type speed); every 32 s their own radar (`rg.site_return` with the type's radar, our altitude/speed/bank, the clutter of our cell, + EMV) sees us or not (alert +(war+opp) x 16 + 32 / -32); above 0xC0 and inside 8 km (4 at night) a visual ID (-20 base) and an armed type fires
(at most 2 shots, 64 s apart); each shot hits with 0.43 (Elite, no countermeasures; it is also the Veteran value, which has no data). Inside 12 km the fighter orbits at 2 km with a random aspect.
Mountain cells hide us (clutter 0), pulse radars are blind head-on / tail-on and doppler radars on the beam, as in the table in aircraft.md; the unit tests check those.
**Assumed, not measured:** the hunt lasts ~500 s after a network alert (ds:5522 sits at 1744 just after a SAM launch and 1400 is "100 s since", so ~3.4/s to zero), shots per fighter, the orbit, pure pursuit.
**Not modelled:** scrambles of the alert-gated slots (airborne fighters only), patrol sightings with no network alert, countermeasures and the autopilot's evasion, fighters that lose the alert and re-acquire, airbase positions (the per-mission airbase types are not in `mission_mem.json`).

**Results** (`scripts/compare_objectives.py`, 10-core Mac; value of each chosen route under each model; E[pts] is the base score for these settings, so use it for ratios, not as a debrief number):

| fixture | chosen by | min | E[pts], flat hazard | E[pts], aircraft model | shots | what it chose |
|---|---|---|---|---|---|---|
| liberec (Veteran) | cost | 205 | 1126 | 1147 | 0.00 | S, photo 30, maverick 0 |
| | points = aircraft | 183 | **1166** | **1187** | 0.00 | S, photo 30, maverick 300 |
| kolobrzeg (Elite) | cost | 195 | 284 | 1448 | 0.27 | S, photo 90, photo 270 |
| | points | 120 | **344** | 1048 | 0.19 | **primary only**, N, photo 120 |
| | aircraft | 165 | 313 | **1516** | 0.19 | N, photo 120, photo 270 |
| budejovice (Elite, sa14 soft) | cost | 158 | 276 | 1045 | 0.50 | S, photo 330, photo 240 |
| | points | 141 | **314** | 1026 | 0.73 | N, photo 30, photo 240 |
| | aircraft | 189 | 245 | **1138** | 0.75 | S, photo 30, photo 210 |
| liberec forced to Elite | cost = points | 196 | 267 | 1132 | 0.08 | primary only (the secondary is behind live SA-14 cells), S, photo 210 |
| | aircraft | 188 | 139 | 1176 | 0.06 | same, photo 330 |

What they say:
1. **Veteran** (liberec): points and aircraft agree and beat the cost choice by +3.5 % at 22 fewer minutes (a different Maverick bearing). The aircraft model sees no fighter shot on any route, as in the logs (no Veteran fighter launch in 1,878 min, 58 SA-14 launches).
2. **Elite, flat hazard** (kolobrzeg): the notebook's result reproduces (344 vs 338): drop the secondary, approach N, 120 min. The flat rate is ~0.029 hits/min, i.e. 3.5 hits expected on that route.
3. **Elite, aircraft model** disagrees with the flat rate by a lot: **0.08-0.3 expected hits** on the same routes, so the primary-only plan gives up ~30 % of the points (1048 vs 1516) for little safety, and the model keeps both targets and flies the N approach. The model is not calibrated to the logs: it predicts ~0.0012 shots/min (kolobrzeg) against 0.066 launches/min logged at Elite (7 short flights, no countermeasures used, biased high, but 50x). At 6 + 4 fighters and up to 4 shots each (`--hunt near_mean=6,mid_mean=4,max_shots=4`; expected shots rise ~2.5x, 0.27 -> 0.67 on the cost route) kolobrzeg still picks the N approach, both targets (1464 vs 1340, +9 %) and budejovice changes to N / photo 30 / photo 210 (1030 vs 843, +22 %).
   So the ranking on these fixtures is stable between 1x and 4x, the absolute hit counts are not trustworthy, and the flat-rate "primary only" answer is not supported by the mechanistic model.
4. The flat-hazard (`points`) choice is **worse than the cost choice** under the aircraft model on budejovice (1026 vs 1045): it sees no difference between a route that gives the fighters a network alert and one that does not, only minutes.
5. Every route at Elite has P(network alert) of 65-100 %: SAM sites and SA-14 cells raise it on all of them, which is what scrambles/hunts the fighters. The aircraft objective does not (yet) avoid alerts as such; it prices them through the hunters that arrive.

**Next steps, in order of value:** calibrate the hunt (counts, shots, gate) on the 7 Elite flights by replaying their tracks through `alertsim` + `fighters` (the target is the 23 launches / 10 hits); model the alert-gated scrambles (they are 2.4 of the 4.3 Elite launches per hour);
score options in the worker pool (the +17-25 s); then decide whether the default objective moves off `cost` (Veteran is the easy case: the models agree and the gain is 3-4 %; Elite needs the calibration first). Flip with `objective` in the profile; the goldens would then need new references.
