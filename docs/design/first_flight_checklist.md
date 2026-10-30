# First live flights with `f19 fly` (WP-7d)

For the user. Safest first; stop at the first "stop" and send me the logs listed. Nothing here has run against the game:
everything so far is verified offline (replays, fake emulators). `python scripts/nav.py ...` is the same program as
`uv run python -m f19.cli.fly ...` (called `fly` below). Stop a flight with Ctrl-C or `kill -TERM <pid>` (never `kill -9`): both
release every key and centre the stick. The automation port serves one client: close the HUD/probe of the previous step first.

Logs land in `~/f19-data/logs/`: `nav/<epoch>.csv` + `.events.txt` (+ `.keys.txt` with `--no-keys`), `memlog/<epoch>/` with `--memlog`.
Attach for every step: the csv, events, keys files, `memlog/<epoch>/` if made, and the terminal text.

## Known differences from the old nav (expected, not bugs)
- **Arbiter speed mechanism.** Overlapping reactions (missile, fighter, escape) now use one mechanism: the top request wins and the
  target from before the episode is restored. The old nav's three saved speeds fought each other.
- **Timers run on the tick clock** (the tick's `now`), not drifting by the length of blocking taps.
- **Short taps still block** for their hold time (as before); only roll keys, pitch hold and the trigger are non-blocking.
- **WallClock** is still in use (game clock is a later, separate step), so time-warp (`TIME xN`) behaves as in nav.
- `--fixed` is gone. `--plain` is the old one-column text; the HUD is the new tiled one (falls back to plain if it fails).
- New: `--director` (replans, bail-out/eject chain), `--no-keys`, event-list based HUD.
- **Eject is on by default** (2026-10-29; it was gated behind `--allow-eject` from 2026-10-26): the director's bail-out levels the plane
  first (`pilot/eject.py`), then sends Shift+F10. Shift+F10 was tried live twice that day (level, S 425 and S 272; both ejections succeeded).
  `--no-eject` suppresses it: the action is dropped, the mission log says `EJECT suppressed: --no-eject given - still flying`, the banner
  shows `WOULD EJECT`, and the flight goes on (no exit; the director then plans no more). `--allow-eject` is still accepted (no-op).
- Compare against the old nav's logs of the same flight type: `want` (route heading), `press` column, event texts, phase times
  ("takeoff done", "primary attack done", landing), cycle_s (should stay ~0.15-0.3 s; with `--memlog` ~0.05 s sleep).
- **Untried live:** Shift+F10 (eject), POKE, key latency/hold times, Settle + `after_load`, hand-over roll-level, the missile-world read cost
  per tick. DESIGNATE has no key (raises).

## (a) Before the game (no risk)
```
cd ~/projects/f19 && uv sync && uv run pytest
uv run python -m f19.cli.fly --replay ~/f19-data/fixtures/replay/logs/memlog/1792609731 \
   --route ~/f19-data/fixtures/replay/plan/ce_katowice_1021_1458/route.json --auto --dry-run --quiet
uv run python scripts/nav.py --help
```
Good: tests pass (a few skipped for missing data), the dry run prints `ticks ... key events` and no exit message.
Stop: any failure. Send the pytest tail.

## (b) Game at the options menu or on a save state: read-only probe
```
uv run python research/scripts/probe_reader.py --rounds 10 --mission
```
Read-only. Good: no field differs in every round; moving fields (position, clock, frame) differ a little. Stop: a field that is
always different or wildly off. Send the output.

## (c) First flight, read-only (no keys at all)
Load a save state in flight (e.g. `checkpoint_stable_flight`) or fly by hand. Then:
```
uv run python -m f19.cli.fly --route <plan>/route.json --hdg --alt 1000 --no-keys --memlog
```
The pilot runs against the live game but Controls writes to a recorder: nothing is sent (settle's hands-off/centre are recorded
too). Look at the HUD (route want, leg, territory, nearest radars), the `*.keys.txt` stream (what it WOULD press), the cycle time.
Good: HUD tracks your own flying, `want` sensible, keys.txt shows plausible roll/pitch taps, cycle_s ~0.15-0.3 s, no "display failed".
Stop: read errors ("waiting for the game" repeated), cycle time > 0.5 s, garbage position. Also try `--missile-watch observe`
(it never presses CM keys in observe) and `--plain` once.

## (d) Manual takeoff, then hold heading and altitude
Take off by hand, reach ~1000 ft with keyboard sensitivity 1, then:
```
uv run python -m f19.cli.fly --hdg --alt 1000          # the game's own waypoints
```
Good: the plane turns onto the waypoint bearing and holds altitude; Ctrl-C leaves the stick centred and no key held.
Stop: oscillation, a held key that does not release, wrong turn direction. Press Ctrl-C and take over.
Send csv/events/keys of the run.

## (e) Route flight with the director
```
uv run python -m f19.cli.fly --route <plan>/route.json --auto --hdg --alt <cruise_ft> --director --memlog
```
`--auto` is needed for the attacks (and takeoff / landing): without it only the route and the director run (first director flight, 2026-10-27,
flew over the secondary and never attacked). Start from the saved state before the mission, or already airborne on the route. The start-up checks refuse a route for another mission, a plane >10 km from the takeoff
point, or the wrong N/S file. Good: legs advance, a deviation triggers a replan line in the mission log, no eject (a `WOULD EJECT` banner or an
`EJECT suppressed` event means the director wanted to bail out: send the log, the flight kept going).
Stop: "director error" lines, a replan that sends the plane somewhere odd. Send events + csv (plan_version column).

## (f) `--auto`, one phase at a time, from save states
1. Takeoff: `checkpoint_pretakeoff` -> `fly --route R --auto` (watch rotate at 120 kt, 90 flaps).
2. Attack: a state near the target -> `fly --route R --auto --attack-now primary` (or `secondary`; `--done primary` to skip).
3. Landing: a state on the approach -> `fly --route R --auto --land-now` (must be 2..60 km before the runway along the approach).
Good: each phase emits its events ("takeoff done", "primary attack done", ...), hand-over at 6 km final releases the keys.
Stop: gear/flaps toggling repeatedly, throttle fight, mountain banner. Take over and send everything.
Gun attacks: `--strafe nearest` is separate and still unflown.

## (g) Eject test (save state, enemy or friendly territory)
`Action.EJECT` = Shift+F10, never pressed live. Easiest first: with `--no-keys` check the stream shows `scan 68 True 0.2`.
Then on a save state with the plane in the air, run `--director` with a tiny fuel/score setup or send the key once by hand with
`research/scripts/inject_key.py` to see whether Shift+F10 ejects. Good: the game ejects and fly exits ("director: ejected ...").
Stop: nothing happens (then the scancode path needs another approach).

## (h) Mission selection by seed
At the options menu: `uv run --group missions python scripts/mission_finder.py --length x10 --primary destroy --secondary photo --no-time-limit --check-only`
first (no keys), then without `--check-only` (sends keys). Plan the mission, then continue at (e)/(f).
