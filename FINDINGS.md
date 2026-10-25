# F-19 reverse engineering: research findings & test hypotheses

This file used to hold every finding. On 2026-10-25 (refactor WP-9) its sections moved verbatim into topic files;
this index maps each old section heading to its new home, so old references (`FINDINGS.md "Section"`) still resolve.
New findings go into the matching topic file under [docs/game/](docs/game/), with a date.

## Topic files

- [docs/game/memory-map.md](docs/game/memory-map.md): briefing block, EGAME data segment, world/entity model, event list, flight-log variables
- [docs/game/radar.md](docs/game/radar.md): radar flights, EMV, radar-site model
- [docs/game/missiles-countermeasures.md](docs/game/missiles-countermeasures.md): enemy ordnance, seekers, flares/chaff/decoys/jammers
- [docs/game/aircraft.md](docs/game/aircraft.md): enemy aircraft profiles
- [docs/game/fuel.md](docs/game/fuel.md): fuel burn and cruise speed
- [docs/game/weapons.md](docs/game/weapons.md): ordnance vs targets, release, photo/lock/guidance, guns
- [docs/game/landing-takeoff.md](docs/game/landing-takeoff.md): takeoff direction, landing and takeoff rules
- [docs/game/scoring-awards.md](docs/game/scoring-awards.md): rating formula, medals and promotions
- [docs/game/terrain-theaters.md](docs/game/terrain-theaters.md): mountains, altitude, Gulf/Libya data
- [docs/game/mission-generation.md](docs/game/mission-generation.md): START's generator, rerolls, .WLD mission data
- [docs/game/missions.md](docs/game/missions.md): the generated mission catalog (was MISSIONS.md)
- [docs/game/tooling-history.md](docs/game/tooling-history.md): prior art, toolchain, save-state automation
- [docs/design/](docs/design/): autopilot, planner, PRDs and plans (was notes/)
- [research/notes/](research/notes/): stale sections and the mountain-finder working notes

## Old section -> new location

| Old FINDINGS.md section | Now in |
|---|---|
| Prior art | [docs/game/tooling-history.md](docs/game/tooling-history.md#prior-art) |
| Toolchain identification (confirmed locally, 2026-08-04) | [docs/game/tooling-history.md](docs/game/tooling-history.md#toolchain-identification-confirmed-locally-2026-08-04) |
| Action plan (ordered, built on the MS C 5.1 finding) | [research/notes/findings_stale_sections.md](research/notes/findings_stale_sections.md#action-plan-ordered-built-on-the-ms-c-51-finding) |
| Mission briefing block in OUR build — CONFIRMED live (2026-10-17) | [docs/game/memory-map.md](docs/game/memory-map.md#mission-briefing-block-in-our-build--confirmed-live-2026-10-17) |
| Second-hand layout: mission briefing block (F-19 v435.04) — offsets do NOT match our build | [research/notes/findings_stale_sections.md](research/notes/findings_stale_sections.md#second-hand-layout-mission-briefing-block-f-19-v43504--offsets-do-not-match-our-build) |
| Critical caveat: these are segment offsets, not physical addresses | [docs/game/memory-map.md](docs/game/memory-map.md#critical-caveat-these-are-segment-offsets-not-physical-addresses) |
| Fingerprint search — the version/segment-independent way to verify this | [research/notes/findings_stale_sections.md](research/notes/findings_stale_sections.md#fingerprint-search--the-versionsegment-independent-way-to-verify-this) |
| What other agents should test | [research/notes/findings_stale_sections.md](research/notes/findings_stale_sections.md#what-other-agents-should-test) |
| World & dynamic-entity model — confirmed via f15se2-re decompilation (2026-08-05) | [docs/game/memory-map.md](docs/game/memory-map.md#world--dynamic-entity-model--confirmed-via-f15se2-re-decompilation-2026-08-05) |
| What's still unmapped (no prior art found for any of this) | [docs/game/memory-map.md](docs/game/memory-map.md#whats-still-unmapped-no-prior-art-found-for-any-of-this) |
| Automation: repeatable flight trials via DOSBox-X save states (2026-08-06) | [docs/game/tooling-history.md](docs/game/tooling-history.md#automation-repeatable-flight-trials-via-dosbox-x-save-states-2026-08-06) |
| Player state addresses — CONFIRMED (2026-10-07) | [docs/game/memory-map.md](docs/game/memory-map.md#player-state-addresses--confirmed-2026-10-07) |
| Waypoint table + coordinate system — CONFIRMED (2026-10-09) | [docs/game/memory-map.md](docs/game/memory-map.md#waypoint-table--coordinate-system--confirmed-2026-10-09) |
| Mission data (targets + radar types) — CONFIRMED (2026-10-09) | [docs/game/mission-generation.md](docs/game/mission-generation.md#mission-data-targets--radar-types--confirmed-2026-10-09) |
| Altitude vs mountains (game mechanic, from the user, 2026-10-09) | [docs/game/terrain-theaters.md](docs/game/terrain-theaters.md#altitude-vs-mountains-game-mechanic-from-the-user-2026-10-09) |
| Radar exposure flight 1 (North Cape, easiest difficulty, 2026-10-14) | [docs/game/radar.md](docs/game/radar.md#radar-exposure-flight-1-north-cape-easiest-difficulty-2026-10-14) |
| Time multiplier, turn-rate vs EMV, carrier launch (2026-10-14) | [docs/game/radar.md](docs/game/radar.md#time-multiplier-turn-rate-vs-emv-carrier-launch-2026-10-14) |
| Veteran vs easy: own signature (2026-10-15) | [docs/game/radar.md](docs/game/radar.md#veteran-vs-easy-own-signature-2026-10-15) |
| Veteran radar-tactics flight, Central Europe (2026-10-15) | [docs/game/radar.md](docs/game/radar.md#veteran-radar-tactics-flight-central-europe-2026-10-15) |
| Radar pulse rings on the MAP display (CE, Gutersloh -> Cottbus, 2026-10-16) | [docs/game/radar.md](docs/game/radar.md#radar-pulse-rings-on-the-map-display-ce-gutersloh---cottbus-2026-10-16) |
| Mission rating formula — decoded from END.EXE (2026-10-17) | [docs/game/scoring-awards.md](docs/game/scoring-awards.md#mission-rating-formula--decoded-from-endexe-2026-10-17) |
| Medals, ribbons, promotions — decoded from END.EXE (2026-10-24) | [docs/game/scoring-awards.md](docs/game/scoring-awards.md#medals-ribbons-promotions--decoded-from-endexe-2026-10-24) |
| Ordnance vs target strength — decoded from EGAME.EXE (2026-10-17) | [docs/game/weapons.md](docs/game/weapons.md#ordnance-vs-target-strength--decoded-from-egameexe-2026-10-17) |
| Takeoff direction: decided by the theater alone — decoded from EGAME.EXE (2026-10-17) | [docs/game/landing-takeoff.md](docs/game/landing-takeoff.md#takeoff-direction-decided-by-the-theater-alone--decoded-from-egameexe-2026-10-17) |
| Weapon release, altitude and blast damage — EGAME (2026-10-17) | [docs/game/weapons.md](docs/game/weapons.md#weapon-release-altitude-and-blast-damage--egame-2026-10-17) |
| Photo, lock and guidance — EGAME (2026-10-17) | [docs/game/weapons.md](docs/game/weapons.md#photo-lock-and-guidance--egame-2026-10-17) |
| Guns — EGAME (2026-10-18, from the disassembly; not yet seen in flight) | [docs/game/weapons.md](docs/game/weapons.md#guns--egame-2026-10-18-from-the-disassembly-not-yet-seen-in-flight) |
| Landing rules — EGAME (2026-10-17) | [docs/game/landing-takeoff.md](docs/game/landing-takeoff.md#landing-rules--egame-2026-10-17) |
| Takeoff rules — EGAME (2026-10-17) | [docs/game/landing-takeoff.md](docs/game/landing-takeoff.md#takeoff-rules--egame-2026-10-17) |
| Full-mission autopilot: takeoff, attack, landing phases (2026-10-17, NOT flight-tested) | [docs/design/autopilot.md](docs/design/autopilot.md#full-mission-autopilot-takeoff-attack-landing-phases-2026-10-17-not-flight-tested) |
| Mission event log, trackcam and target lock — live (2026-10-18) | [docs/game/memory-map.md](docs/game/memory-map.md#mission-event-log-trackcam-and-target-lock--live-2026-10-18) |
| Flight-log variables: fuel, damage, clock, IDs, threats — EGAME (2026-10-18) | [docs/game/memory-map.md](docs/game/memory-map.md#flight-log-variables-fuel-damage-clock-ids-threats--egame-2026-10-18) |
| Radar sites: pulses, duty cycle, detection — EGAME (2026-10-18) | [docs/game/radar.md](docs/game/radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18) |
| Fuel burn and cruise speed — EGAME (2026-10-18) | [docs/game/fuel.md](docs/game/fuel.md#fuel-burn-and-cruise-speed--egame-2026-10-18) |
| Planner model (2026-10-19) | [docs/design/planner.md](docs/design/planner.md#planner-model-2026-10-19) |
| Other theaters, terrain over-statement and the 20 km turn radius (2026-10-24) | [docs/game/terrain-theaters.md](docs/game/terrain-theaters.md#other-theaters-terrain-over-statement-and-the-20-km-turn-radius-2026-10-24) |
| (mountain_finder.md) 0. Result (2026-10-15) | [docs/game/terrain-theaters.md](docs/game/terrain-theaters.md#mountain-finder-result-2026-10-15) |

The "Generator rules" bullets and the 2026-10-24 fuel-estimate / reroll / turbo bullets of "Mission briefing block in OUR build" are in [docs/game/mission-generation.md](docs/game/mission-generation.md#generator-rules-fuel-estimate-and-rerolls-from-the-briefing-block-section); the rest of that section is in memory-map.
