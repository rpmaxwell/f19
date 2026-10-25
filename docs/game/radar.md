# Radar

Radar exposure flights, EMV and the decoded radar-site model (pulses, duty cycle, detection, alerts). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Radar exposure flight 1 (North Cape, easiest difficulty, 2026-10-14)

Logs: nav `logs/nav/1792028447.csv`, memlog `logs/memlog/1792028452/`, gauge `logs/emv/1792023748.csv`
(plus the first leg: nav `1792023696.csv`, memlog `1792023702/`).

- **EMV in memory: u8 at old 0x4108C** (copy at 0x451D8), r = 0.978 vs the HUD gauge.
  Gauge rows ~= 0.51 x value (full 51-row gauge ~= 100).
- What drives EMV (memlog + nav log, ~2800 samples):
  level flight 160-250 kt: ~7 (no measurable speed effect in that band); turning 0.2-0.6 deg/s: 8,
  0.6-1.2: 9, 1.2-4: 13, >4: 16; ~400 kt at full throttle: ~25-27; bay doors open: 37-38;
  gear: no visible effect; 400 vs 800 ft level: ~7 vs ~9 (climb pitch probably contributes).
- Gauge radar bars only ever drawn red (ground) / light blue (air): never pink/yellow/white, i.e.
  never detected. Bars blink ~0.5 s every ~6.5 s real (~13 s game). Smallest gap to detection on the
  gauge: ground 26 rows, air (fighter radar, MiG-31s from Monchegorsk) 11 rows -> air radar is the
  closer threat; bay doors open (EMV ~19 rows) near a fighter would have overlapped.
- Ground bars appeared well outside every briefed range (Kirovsk OTH bar visible from ~200 km).
- Candidates for the gauge's radar side: old 0x4109C alternates 4 / 11 in step with ground / air bars
  (which threat is being drawn?); s16 at old 0x4109A sits near 0..+7 when a bar is shown and ramps
  negative between blinks.
- Timing: the sim runs ~2x real time in this DOSBox setup, ~4x with in-game time acceleration.
- Keyboard sensitivity: u8 at old 0x417DA = level - 1.

## Time multiplier, turn-rate vs EMV, carrier launch (2026-10-14)

- **Accelerated time: u8 at old 0x410AA** = 1 normal, 2 accelerated (flipped exactly when the user
  toggled it in memlog 1792023702; ground speed vs airspeed doubled at the same moments). nav.py
  reads it to scale its controllers.
- **EMV vs turn rate** (EMV byte, level flight 160-235 kt, 3241 samples; rate in nav deg/s):
  level 7 | 0.3-0.5: 9 | 0.5-0.9: 10 | 0.9-1.1: 12 | 1.1-1.3: 13 | 1.3-1.6: 18 | 1.6-2.0: 20 | 2-3: 21.
  Extra EMV x time per degree turned is ~4-5 up to 1.2 deg/s and ~7.5 above 1.3, lowest near 0.8.
  -> nav H_RATE_MAX 1.5 -> 0.8, LOOKAHEAD 8 -> 12 km; planner TURN_RATE 0.8, BANK_PENALTY 0.43.
- **Carrier catapult launches heading ~185** (every carrier flight); plan_mission's climb-out now
  follows the launch direction (TAKEOFF_HDG; 180 for px: takeoffs, 0 for airbases).
  (Superseded 2026-10-17: the direction depends on the theater, see ["Takeoff direction"](landing-takeoff.md#takeoff-direction-decided-by-the-theater-alone--decoded-from-egameexe-2026-10-17).)
- Third 3D-terrain crash (North Cape, x 10105 y 8356, level at 413 ft); with the previous one
  (x 10655 y 9416) they mark an unseen ridge. Crash sites are kept out of plans via
  logs/theater/<TH>_obstacles.json until the .3DT format is decoded.

## Veteran vs easy: own signature (2026-10-15)

Memlog 1792035792 (veteran, North Cape, autopilot part only; the gauge log was invalid - stale
window capture). EMV byte in level flight: 400 ft ~12 (easy 6-7), 800 ft ~19 (easy 8-10), turning
0.6-0.9 deg/s ~15 (easy 9). So difficulty (or the loadout - not controlled) scales the player's EMV
~1.7-2x; altitude 400 -> 800 ft adds ~40-60% on both. Radar candidates: old 0x4109C took extra
values 12 and 15 on veteran (4/11/0 on easy); the s16 at 0x4109A behaved the same on both.
Automation server bug fixed: replies were queued globally, so a client that disconnected mid-flood
left lines that shifted every reply for the next client (cause of "unpack requires a buffer" and the
failed passive logger). Server now clears queues on accept; client resyncs on connect.

## Veteran radar-tactics flight, Central Europe (2026-10-15)

Mission: primary Tank Farm Liberec, secondary Tank Farm Carlsbad, out of Rhein-Main; plan `logs/plan/ce_veteran`
(TEST_CARD.md, route_test.json). Logs: nav 1792111732 / 1792113502 / 1792114183 / 1792114900, memlog for each,
HUD logs/emv/1792110378.csv. Joined by `analysis/radar_flight.py` -> radar_flight.json, radar_blinks.csv.

- **Nav bugs found and fixed:**
  - Repeated corner points made zero-length legs.
  - Off-track "aim at leg end" made the plane orbit short legs.
  - Fixes: `route_aim` intercept and `next_leg`.
  - The real turn radius is ~20 km (0.8 deg/s cap at ~2x sim speed). Test legs must be >= ~60 km.
- **Veteran:** ground bars blink pink ("poor detection") ~1/3 of the time from takeoff, even 120+ km from the SA-10. Easy flight: never pink.
- **Colour tracks bar length:** red <~15 rows, pink >~17 rows. Colour key per the Technical Supplement: red/pink/yellow = ground radar not / poorly / has detected; light blue/white = air radar not / has detected.
- **Blinks come in pairs per ~15 s cycle:** a long pink (~22-24 rows), then ~3 s later a short red (~8-9 rows). This looks like one radar at a time on the HUD; per-radar attribution is still to do.
- **Terrain masking:** no effect seen yet. Behind the ridge from Grossenhaim: 3.1 long blinks/min, median 22.5 rows. In line of sight: 3.6/min, median 24 rows. Unattributed blinks, so not conclusive.
- **Mission-minute counter:** u16 at old 0x41E9E counts +1 every ~26 s real (= 1 game minute) from takeoff.
- **"Behind enemy lines" counter (cold-war scoring):** not found yet. No counter in the egame_data window ticks only in the eastern half of the flight. Searched u8 and u16, wrap allowed. A full 640 KB dump was taken at the END (Pravda) screen: logs/mem/ce_veteran_end_pravda.bin.

### Cold-war scoring: "behind enemy lines" (2026-10-15)

- **Flight event log.** The debrief replays the flight from a table of 6-byte records: u16 t (2 game-s units), u8 x/128, u8 y/128 (waypoint units), u8 type, u8 param.
  - Types: 0x08 = takeoff and end; 0x09 = in-flight sample every 64 game s; 0x8A param 2 = primary photographed.
  - In the END-screen dump it is at 0x401B2. Decoded: logs/plan/ce_veteran/event_log.json.
  - No running score exists in flight memory. The debrief computes the rating from these records.
- **Territory grid.** 16x16 bytes, one per 32 km cell: cell = (x >> 11, y >> 11) in waypoint units. It sits just before the .WLD name strings (CE.WLD 0x7BA). `analysis/territory.py` reads it.
  - Low two bits: 3 = friendly, 2 = neutral, 0 = enemy.
  - It is the same table F-15 SE2's END uses (`gridFlags[gx + gy*16] & 3`) for kill credit and for capture after ejecting.
- **Validated against the debrief ratings.** Every interval between known ratings gains points only if it contains an enemy-cell sample, and always does:
  - 4 enemy samples on the early foray east of Rhein-Main: +13.
  - 74 minutes of loops on the friendly side: +0.
  - Points resume at the first sample in cell (5,12).
- **Points per enemy sample: 1-7, mean ~4.5.** Single samples gave +7, +1, +2. The source of the variation is unknown (random? depth?).
  - The primary photo is worth about 630-650.
  - Ejecting in enemy territory cut 833 to 212.
- **nav.py** now shows "territory: ENEMY - behind the lines" and logs a `side` column.

## Radar pulse rings on the MAP display (CE, Gutersloh -> Cottbus, 2026-10-16)

Logs: nav 1792199506, memlog 1792199511, HUD + panel frames logs/emv/1792199394(.csv); plan logs/plan/ce_cottbus.
Analysis: analysis/radar_flight.py (blinks), analysis/radar_rings.py + radar_rings_join.py (rings).
Crashed ~27 min in at (9490,16537), flat ground, with the alt hold set to 200 ft (see nav bug below).

- **The left MAP display draws each radar pulse** as a pink (236,98,92) double ellipse expanding from the site
  (~80 km per 0.25 s real frame). Map scale at zoom 1: 0.237 px/km in x, y/x = 0.70, offset (0.5, 10.6)
  (whole theater visible); zoomed views are x4 / x8 and pan.
- **Ring = pink gauge bar.** 54 of 75 multi-frame rings start in the same 0.25 s frame as a pink ground bar
  (more within ±0.5 s). Red (short, "not detected") bars have no ring: rings seem to be drawn only for pulses
  that detect you poorly or better. So the pink bar is one radar's pulse, attributable from the ring centre.
- **Each radar pulses every ~15.5 s real (~31 game s)**: Wittstock 15.4-16.7 s, Grossenhaim 15.2, Mittenwalde
  15.4-15.9. The gaps of 31/45 s are pulses that came back red (no ring).
- **Bar length = that radar's strength at the plane**, falling with range: Wittstock SA-10 154 km -> 18-19 rows,
  110 km -> 26-27, 80 km -> 31-32. Grossenhaim SA-10 118 km -> 20-21; Mittenwalde SA-12 88 km -> 23-24.
- **Doppler aspect confirmed:** Wittstock at 34-45 km side-on (aspect 50-83 deg) -> 29-33 rows; at 55-67 km
  head-on/away (aspect 0-26) -> 35-38 rows. Head-on is worse even from further out.
- **Pulse aspect (Cottbus SA-11, 29-38 km):** aspect 44 -> 32 rows, 58-60 -> 37-38, 80 -> 44 (once 33). Side-on
  is worse, as the manual says.
- Easy-level colours: pink (>~17 rows) from 150 km; white (air radar detected) three times near Cottbus and on the
  loop; no yellow.

**nav bug (cause of the crash):** `airborne = alt > 150` turns all control off below 150 ft, and the floor guard
(FLOOR 250, "UP!") sits above a 200 ft target, so the hold pushed down toward 200 while the guard pushed up.
After 4 "down" presses in a turn the plane sank through 150 ft and nav stopped pressing anything until impact.
Keep --alt >= 300 until airborne is latched and the target is clamped above FLOOR.

## Radar sites: pulses, duty cycle, detection — EGAME (2026-10-18)

Scheduler 0x4eea (every frame), detection 0x51a1 (one call per pulse), return 0x5519. Validated against all logs/memlog
sessions where noted. Reference implementation + self-check: `analysis/radar_game.py check`. Units: d = game distance (max + min/2) in km (waypoint units >> 6); alt ft; angles 65536 = 360.

**EMV** (exact, memlog 4067/4067 within +-1):
`EMV = atan2(alt/3 x (opp+1), 2048) >> 8  +  |bank| / 2  +  27kt >> 10  +  24 bay open  +  16 ECM on`
- altitude term (Veteran): 300 ft 5, 1000 ft 18, 2000 ft 31, 10000 ft 56 (0..64 = 0..90 deg). Monotonic: **no high-altitude
  benefit** in this code (the manual's 32000 ft advice doesn't apply here). Opponent level scales the altitude (x(opp+1)/3).
- bank: |roll| in 1/256 turns folded to 0..64, halved: **30 deg bank = +10, 90 deg = +32.** Turn flat.
- speed: ds:7F94 = 27 x knots (memlog), so +1 per 38 kt (200 kt 5, 400 kt 10).

**Return of radar type t at the player** (record ds:4876 + 14t: +0 range R km, +2 power P, +4 bit 0 doppler, +6 name):
`s = C_cell x (R - d) x (P + 2opp + 1) / R`, then aspect: `s' = s x (f + 32) / 64`, where rel = angle between the site->plane
bearing and the plane's heading, folded to 0 (flying straight at/away) .. 64 (tangential):
- pulse: f = rel -> x0.5 radial .. x1.5 tangential.
- doppler: f = ((96 - rel) x (27kt >> 5)) >> 9 -> at 200 kt x0.98 radial .. x0.66 tangential; 400 kt x1.47 .. x0.83;
  150 kt x0.86 .. x0.62. **Doppler return grows with speed.**
- C_cell = (grid byte of the PLAYER's 32 km cell) & 0x0C, the same grid as territory.py: **0 mountains, 4 rough (x0.5),
  8 flat land, 12 open sea (x1.5).** CE: 12 cells 0 and 10 cells 1, the high cells (95th-pct terrain ~7500-8100 ft); NC: 82
  sea cells. Mountain cells give **s = 0 for every ground site and every fighter radar** (0x6602 uses the same function).
- cone: s = 0 if |site alt - alt| >> 10 > d (directly above a site: within alt/1024 km).
- **No terrain line-of-sight anywhere in the detection path.** Ridges do not shadow you; only the cell type matters.
- Check: memlog pulse colour (ds:552C at the last site ds:94E4) agrees with this formula on 726/766 pink pulses, misses
  within 30 of the threshold. The air-radar check writes colour 4 before testing, which explains every "4" mismatch.
- Seen = s' + EMV > 100. Pink bar = > 50 (no alert gain). With s <= 0 you are still "seen" if EMV alone > 100.

**Detection range** (seen, km) at Veteran, EMV 10, 200 kt, flat land (sea in brackets). "-" = never:
| radar | kind | R | P | flying at/away | side-on | SAM range |
|---|---|---|---|---|---|---|
| SA-2 | P | 200 | 3 | - | 12 (75) | 125 |
| SA-5 | P | 350 | 2 | - | - (100) | 150 |
| SA-8B | P | 125 | 5 | - | 31 (62) | 65 |
| SA-10 | D | 320 | 7 | 15 (117) | - (15) | 125 |
| SA-11 | P | 200 | 5 | - | 50 (100) | 100 |
| SA-12 | D | 290 | 6 | - (89) | - | 150 |
| SA-13 | P | 125 | 3 | - | 8 (47) | 65 |
| SA-N-4 / N-6 | D | 200 / 320 | 4 / 6 | - (31 / 98) | - | 30 / 125 |
| SA-N-5 / N-7 | P | 150 / 200 | 3 / 5 | - | 9 / 50 (56 / 100) | 30 / 100 |
| OTH | D | 500 | 5 | - (119) | - | - |
Rough cells: nothing sees you at EMV 10. Range grows fast with EMV and level: SA-11 side-on 43 / 50 / 67 / 90 / 117 km at
EMV 6 / 10 / 20 / 34 / 50; 0 / 12 / 50 / 75 km at Green / Regular / Veteran / Elite. SA-10 at/away: 0 / 15 / 77 / 118 km
at 150 / 200 / 300 / 400 kt. ("SAM range" = missile spec +8, km: the site can fire once alerted inside it.)

**Pulse timing** (per site, record +0x0C countdown): period = `256 x ds:4EFC / (alert/4 + 16)` frames + site index, i.e.
**16 ticks = 32 game s when quiet** (matches the ~15.5 s real rings), 8 ticks at alert 64, 4 at 192, 3.2 at 255:
an alerted site looks 5x as often. A pulse fires when the countdown reaches 4; any return (site or plane) sets a global
lockout ds:552A = ds:4EFC frames, and a pulse that comes due inside it is skipped for that whole period.

**Duty cycle** (memlog: 97 % of 316k site-snapshots match the site's "active" flag, record +8 bit 0x02): a site with alert 0
and no flag 0x100 is only active in a window of 1024 frames (~256 ticks, ~8.5 game min) when
`(index x (frame >> 10) x 7) & 7 <= 2 x ds:4EF2 + 1` (+ 3 instead of + 1 for ~1-2 min after a network alert, via
ds:9502 -> 95CE -> 963E stepped every 128 frames). ds:4EF2 = 0 in all our flights, so 37.5 % of quiet sites are on in
an average window (56 % after a network alert; analysis/radar_game.py duty_fraction). The frame counter ds:5520 starts random (0x49fd), so the schedule
is unknown at planning time but **predictable live from ds:5520**.

**Destroyed sites go silent** (0x503a): the scheduler loop skips any record with +8 bit 0x80 (or +6 == 0) and clears its
active bit 0x02, so it never counts down, never pulses and never detects. Live 2026-10-19: Wittenberg SA-13 (record 7)
killed by gunfire reads +8 = 0x84 (destroyed + seen, active clear). The map marker stays.

**Alert, launch, ID** (per pulse, 0x51a1):
- not seen: alert -= 16 (floor 0), flag 0x10 cleared. Seen: += (war + opp) x 16 + 16 (48 at Veteran, 24 with ECM).
- alert > 0x7F -> network alert (all sites raised to (war + opp) x 16 - 16 = 16, your position broadcast).
- Gate for anything more: the number of enemy missiles already homing within 16 km of you (ds:6336 -> 7F96) <= opp,
  alert > 0xC0, site type != 21 (OTH never shoots), and **you are > 20 km (0x500 units) from the nearest airfield record**
  (ds:663E, records with flags 0x201 + 0x500: no launches or IDs near those bases).
- then: not yet ID'd and alert >= 0xFA -> Radar ID (that pulse); otherwise, if enemy slot (index & 7) is free and you're
  within its missile range -> **launch** ("<site> firing <SAM>"), commData+0x2C++.
- Veteran, seen on consecutive pulses: 3 pulses -> network alert, 4 -> SAM launches possible, 6 -> radar ID. One unseen
  pulse takes 16 off. Seen once and then hidden, a site is back to 0 after 3 quiet pulses.

**SA-14 cells per mission:** only some of the file's 0x10 cells are live in a given mission (memlog: 0 or 3-5 of 8-10 at
takeoff), cleared at load; nav can read the live set from ds:85FC at takeoff.
