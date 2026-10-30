# Enemy aircraft profiles (F-19, EGAME)

Decoded 2026-10-21 from the aircraft type table ds:49B0 + 32 x type (`analysis/radar_game.py aircraft_spec`) and
measured over 23 memlog flights. nav's `phases.FighterWatch` uses these: every aircraft is scored with its own radar.

**Record:** name, NATO name, +0x12 max speed (the slot's speed at +0x18 rises toward it, 0x5ebd; ~0.208 game m/s per
unit), +0x14 reach km (when a network alert fires, airfields within this distance of the broadcast position launch
the type, 0x64c1: **network alerts scramble fighters**), +0x16 agility (turn limit, 0x5e2a), +0x18 radar / weapon
type (passed to the return routine 0x5519 at 0x6602 as the radar type; the weapon spec of the same index is its
missile), +0x1A unknown. The detection loop sets ds:6334 to each slot's own type (0x6521).

**Sighting checks:** about every 32-33 game s per aircraft (memlog, same as a quiet radar site's pulse). Seen =
return + EMV > 100 with that radar, from the aircraft's position and altitude, clutter of the F-19's cell (mountain
cells: never seen). Alert on us +64 seen / -32 unseen at Veteran, +80 at Elite ((war + opp) x 16 + 32). Over 0xC0:
network alert; inside 8 km by day / 4 km at night: visual ID (-20 base points) and, for armed types, a missile.
**Check:** the MiG-31 sightings of 2026-10-21 (36.2 / 36.7 / 37.2 game min) are predicted at 36.1 / 36.7 / 37.2.

| type | name | radar | R | P | missile | km | max speed m/s (game) | measured m/s / alt ft p10-p90 | reach km | agility | Elite 190 kt: away / 45 / beam km | Elite 165 kt | Veteran 190 kt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | MIG-23 Flogger | PULSE | 50 | 5 | AA-8 | 12 | 154 | 154 / 302-4993 | 560 | 3 | - / 4 / 18 | - / - / 18 | - / - / 12 |
| 1 | MIG-25 Foxbat | PULSE | 70 | 6 | AA-6 | 50 | 119 | 127 / 3213-4987 | 700 | 2 | - / 10 / 30 | - / 8 / 28 | - / - / 20 |
| 2 | MIG-29 Fulcrum | doppler | 80 | 7 | AA-7 | 34 | 146 | 125 / 12-6695 | 400 | 5 | 12 / - / - | 8 / - / - | - / - / - |
| 3 | MIG-31 Foxhound | doppler | 100 | 8 | AA-9 | 82 | 164 | 164 / 12-4988 | 930 | 3 | 22 / 6 / - | 16 / - / - | 8 / - / - |
| 4 | Su-27 Flanker | doppler | 80 | 7 | AA-7 | 34 | 151 | 165 / 3397-4987 | 715 | 4 | 12 / - / - | 8 / - / - | - / - / - |
| 5 | IL-76 Mainstay | doppler | 200 | 8 | AA-2 | 14 | 83 | 84 / 2918-5042 | 4000 | 1 | 44 / 14 / - | 30 / - / - | 16 / - / - |
| 6 | F-4E Phantom | PULSE | 70 | 6 | AA-6 | 50 | 166 | - | 520 | 4 | - / 10 / 30 | - / 8 / 28 | - / - / 20 |
| 7 | F-14 Tomcat | doppler | 80 | 7 | AA-7 | 34 | 166 | - | 800 | 4 | 12 / - / - | 8 / - / - | - / - / - |
| 8 | F-18 Hornet | none | | | | | 137 | - | 461 | 5 | never | never | never |
| 9 | An-72 Coaler | none | | | | | 73 | - | 620 | 2 | never | never | never |
| 10 | F-18 Hornet | none | | | | | 137 | - | 461 | 5 | never | never | never |
| 11 | MIG-23 Flogger | none | | | | | 154 | - | 560 | 3 | never | never | never |

Detection columns: km at which the type sees an F-19 at 250 ft over flat ground, the aircraft at 4000 ft; aspect =
the F-19's heading relative to the line from the aircraft (away = flying straight away / at it, beam = across).
Only types 8 and 10 (F-18 Hornet) are friendly (`radar_game.FRIENDLY_TYPES`); the F-4E and F-14 are hostile (Iran flies
them in the Gulf). The F-18 has no radar, so treating it as an enemy would change nothing.

**Rules that fall out:**
- PULSE (MiG-23, MiG-25): never see you head-on or going away; side-on 11-30 km. Fly AT or AWAY from them.
- DOPPLER (MiG-29, Su-27, MiG-31, IL-76): never see you on the beam; head-on / away they do, more when you're fast.
  Keep them 45-135 deg off the nose and stay slow (165 kt).
- **IL-76 Mainstay**: the strongest radar in the air (R200 P8 doppler): sees a 190 kt F-19 head-on / away at 43 km
  (Elite), 30 km at 165 kt; never on the beam. Slightly faster than the F-19 (84 vs ~77 m/s), reach 4000 km. Its
  sighting raises a network alert (and scrambles fighters) like any other. Beam it early.
- With mixed types the answer differs per heading: nav scores every heading against every aircraft.
- Mountain cells hide you from all of them.

## FighterWatch trigger calibration (2026-10-27)

Established from memlogs (no live test of the new rule yet): at Elite (flight 1793141481 and 5 earlier Elite flights) the computed
margin (return + EMV - 100, FighterWatch._margin) predicts the game's alert rises: 44 rises, median margin +6, 95 % at >= -5
(max over the last 3 ticks), one below -10. At Veteran (5 flights, 8 evasions) every evasion fired at a margin of -10..-2 and no
fighter ever held an alert above 0, no visual ID. So the trigger margin is 10 at Elite and 0 below (`margin_for`), and an
evasion only starts when the alert budget is running out: alert + gain > 0xC0 - gain, gain = opp x 16 + 32 (Veteran 64: two
pulses banked; Elite 80: one), or the fighter is inside 12 km and would see us. A running evasion ends 15 s after the route
heading is safe. Replay (shadow, trajectories as flown): Veteran 8 -> 3 episodes, 14.4 -> 1.4 min.
Elite flight 1793141481: fighters scrambled by the SA-14 launches at 580 / 594 closed from 36 km straight in; inside 8 km every
fighter's margin sat in -20..+15 at 250 ft / 170-190 kt, alerts went 0 -> 80 -> 160 -> visual ID within ~40 s. Aspect-based
evasion can't work once a hunting fighter is that close (2x our speed); the lever is not getting the network alert.
Untested: the budget rule in a live flight; behavior once the budget is spent (it still evades).

## Enemy sorties: airbases, launch, flight, landing (2026-10-28)

Established by static disassembly of EGAME (`research/analysis/egame_re/egame_all.asm`; addresses below are image offsets) and checked against the memlogs
(22 CE flights with the 13-slot roster: 17 Veteran, 5 Elite; 123 enemy launches) and the START emulator (`gamedata/mission_gen.Generator`, offline).
**Not tested live.** Everything measured is CE; PG is checked on one launch, NC and LB not at all. This corrects the 2026-10-21 line above that fighters launch "from airfields
within reach of the broadcast position": the position is the player's (or a live decoy's), and the type must match the airbase (below).

### The pieces

- **Sim objects**: ds:9670 slots of 0x24 bytes at base ds:8852 (the older note counts from 8854). Fields from the base: +0 **home airbase record**, +2 X, +4 Y, +6 alt, +0x10 heading, +0x16 type,
  +0x18 flags, +0x1A speed, +0x1C **sortie timer (frames)**, +0x22 alert. The last 4 slots are the friendly F-18s (the enemy loop stops at `ds:9670 - 4`); slot 0 is the briefing's AirPlane (An-72) and is never scrambled.
- **Airbase records** are target-table records (ds:80AA, 16 B each, class `0x100 | AirBase code`: CE 277, PG 288, NC 275, LB 286). For an airbase `+0xA` is the **aircraft type it hosts** (for radar sites the same
  field is the alert accumulator), `+0xC` is a count 0-4 that no EGAME code reads, `+8` flags: `0x0001` hostile, `0x0401` friendly, `0x0101` neutral. They are static during a flight.
- **Roster** (the slots: type, behaviour flags) is built by START per theater, the same for every seed and difficulty. In EGAME's first snapshot the "auto" slots read `0x83` (START hands them over as `0x483`).

| theater | slots (type: flags at START), enemy only |
|---|---|
| CE | 1 MiG-23 `0x1`, 2 Su-27 `0x483`, 3 MiG-29 `0x483`, 4 IL-76 `0x4c3`, 5 MiG-31 `0x483`, 6 MiG-25 `0x101`, 7 Su-27 `0x101`, 8 MiG-25 `0x1` |
| NC | 1 Yak-38 `0x1`, 2 Su-27 `0x483`, 3 MiG-29 `0x483`, 4 IL-76 `0x4c3`, 5 MiG-31 `0x483`, 6 Yak-38 `0x1`, 7 Su-27 `0x101`, 8 MiG-25 `0x101` |
| PG | 1 F-5 `0x483`, 2 F-4E `0x101`, 3 F-14 `0x1`, 4 F-5 `0x483`, 5 F-4E `0x483`, 6 F-4E `0x101`, 7 F-14 `0x1` |
| LB | 1 MiG-25 `0x483`, 2 MiG-23 `0x101`, 3 MiG-29 `0x483`, 4 MiG-25 `0x1`, 5 MiG-23 `0x1`, 6 MiG-29 `0x101` |

  Types 14-18 (not in the table above): 14 Yak-38 (reach 300, radar 50/P5, AA-8), 15 Tu-95, 16 Mi-24, 17 F-5 Tiger (reach 250, radar 40/P3, "AIM120"), 18 767 (civil). Flag bits: `0x80` **auto sortie** (launches without an alert),
  no `0x80` = **alert-gated**, `0x100` wingman (follows the leader slot `ds:552E` when no alert), `0x40` (IL-76) does not re-pick patrol points, `0x4` returning, `0x200` landing pattern, `0x400` on the ground roll / just launched.

### 1. Enemy airbases (where, and what they host)

Positions are fixed per theater (WLD places); **which type each hosts, and the count, are drawn per mission by START** (`Generator.generate()`; uniform count 0-4, type = the type of a random enemy roster slot, so types that
have two slots appear twice as often; `(0, 0)` is an empty field). Neither the difficulty nor the ROE level enters the draw (120 seeds each, CE: 7.0 of 9 bases occupied, ~17.6 aircraft, identical across Green..Elite and ROE 0-2).
A base can launch only if its record index is below `ds:9C78` (the target count: CE 54, PG 43, NC 32) **and** `flags & 0x181 == 1`. Eligible airbases (record index, km):

| CE | |
|---|---|
| 29 Konmarno (405,459), 30 Tabor (277,427), 31 Grossenhaim (181,331), 32 Mittenwalde (181,299), 33 Radom (405,299), 34 Wittstock (181,203), 35 Klaipeda (437,75), 36 Stupsk (309,171), 37 Kolobrzeg (261,179) | friendly 66-74 never launch enemy aircraft |

PG: 27 Abbas (435,299), 28 Bandar-E-Lengeh (395,323), 29 Bushehr (211,235), 30 Shiraz (275,171), 31 Bandar Khomenyi (115,139), 32 Kerman (467,139), 33 Masjed Soleyman (115,75), 34 Yazd (339,43), 35 Esfahan (211,11)
(24 Tarif is not an airfield for sorties; the Gulf-state fields 58-63 are neutral `0x0101`). NC: 19 Pechenga (245,211), 20 Polyarnyy (213,243), 21 Kilpyaur (277,243), 22 Kildenstroy (309,243), 23 Olenegorsk (309,275), 24 Monchegorsk (309,307),
25 Kem (309,467), 26 Severodvinsk (405,467), 27 Kushkushara (469,403), 28 Loukhi (277,403) (Lulea and Kiruna lie beyond the count). LB: airbases at records 20-24 (Port Brega (353,345), Benina (401,241), Sabha (99,483), Al Bayda (449,217) and one more); its `ds:9C78` is not known.

### 2. Takeoff and landing

- **Launch attempt** (0x6415-0x64e6), for each inactive enemy slot k: only when `((clock >> (ds:4EF2 + 4)) & 7) == (k & 7)`, i.e. a **32 game s window every 256 s per slot at ROE level 0** (64 / 512 s at level 1, 128 / 1024 s at level 2; ds:4EF2 is the conflict level, 0 in every logged flight).
  Every frame in the window: draw `r = rand(ds:9C78)`; launch if the slot has flag `0x80` **or** the network alert is on (`ds:5522 > ds:6EAA`; 5522 is a copy of the alert timer ds:86FC), **and** record r has `flags & 0x181 == 1`,
  **`+0xA == slot type`**, and `game_distance(aim point, record) >> 6 < type reach`. Reach (400-930 km, IL-76 4000) covers the whole map, so the distance never decides in practice. The aim point (ds:8B24/8B2E/8B34) is the **player, or a live decoy**.
  The type is therefore not chosen by a dice roll: **a slot launches only from an airbase hosting its own type**, picked at random among the matching ones.
  Checked on the 123 logged launches: window rule 123/123, base type == slot type 123/123, every launch base `flags == 1`; the 38 launches by slots without `0x80` all fell inside an alert, the 85 by `0x80` slots mostly outside one.
- **Launch** (0x6961): placed at the airbase (carrier records `0x200`: offset, 140 ft), heading by ds:85F6 (0 or 180, as the player's takeoff), alt 12 ft, speed 10 (100 on a carrier), flags `|= 0x403`, home = that record,
  and **sortie timer = (reach_km << 11) x fs / max_speed_raw** frames (fs = ds:4EFC, 1 per frame), i.e. a **sortie of 4096 x reach / max_speed game s**: Su-27 67 min, MiG-31 80, MiG-25 84, MiG-23 52, MiG-29 39, F-5 ~35, IL-76 ~11 h.
  Measured against the formula on 123 launches: median error +0.1 %. A HUD line "<type> taking off" is posted.
- **Return** (0x62c8): each frame the timer decrements; at 0 the aircraft sets `flags |= 4` and takes as its new home **the nearest record with `flags & 0x101 == 1` of any type** (so it often lands somewhere other than where it launched),
  flies to it (target altitude `dist + 2000 ft`, a glide path, or 12 / 140 ft for the field / a carrier), sets `0x200` near the field (~2,000 ft), lands and returns to the inactive state `0x81`. Observed: touchdown within ~2 km of the record.
  The slot can relaunch at its next window; with `0x80` that has been 2 min after landing in the logs.
- **Despawn** (0x59ef): at its periodic check an aircraft farther than **0x15E = 350 km (game distance)** from the player is reset to inactive, so **aircraft far from the player do not exist**. The check only runs at the patrol re-pick (every 128 s),
  so 1.3 % of airborne observations are beyond 360 km (max 482). 32 of 37 non-landing deactivations were at 330 km or more (the game distance is up to 12 % longer than Euclidean).

### 3. Flight paths: computed live, not precomputed

Each frame an active aircraft gets a target point and steers to it at max speed (agility sets the turn limit, see the record above):
- **Hunt**: with a network alert (`ds:5522` nonzero; for slots with `0x40|0x100` only while `5522 > 6EAA`) the target is the **aim point (player or decoy)**, altitude `clamp(player alt + 1000, 5000, 20000)` ft.
- **Patrol** (no alert): every **128 game s per slot** (staggered by 8k ticks) a slot without `0x40` re-picks its **home record**: up to 8 tries of `r = rand(ds:9500) + 1` (the 75 world records), skipping flag `0x400`; it keeps the one whose **bearing from the aircraft is closest to the bearing to the player**
  and stops at the first within `0x4000 - opp x 0x1000` of it: **90 / 67.5 / 45 / 22.5 deg for Green / Regular / Veteran / Elite**. Then it flies to that record's position at `clamp(player alt + 1000, 5000, 20000)` ft (5,000 ft for a low player). The patrol therefore **drifts toward the player**, more tightly at Elite.
  Checked on 535 logged re-picks (300 Veteran, 235 Elite): simulating the rule on each event's real geometry gives uniform PIT values (means 0.50 and 0.47).
- **Wingman** (`0x100`, no alert, leader slot `ds:552E` set): holds a formation offset behind the leader.
- **Return**: as above.

### 4. Which types launch, and how many are airborne

Type comes from three layers: the per-theater roster (START), the per-mission airbase table (START, random), and the runtime gate above (EGAME). Difficulty does **not** enter the launch logic or the airbase draw; it enters the patrol angle, the radar gains
(alert +64 at Veteran, +80 at Elite), missiles and damage. Alert-gated slots are the lever: a network alert (a site seeing you, an SA-14 launch, a fighter sighting) opens the gate for ~100 game s, and each reserve slot has a 32 s window every 256 s inside it.

Measured (CE, game minutes of flight; Veteran 17 flights / 1761 min, **Elite 5 flights / 323 min**): launches 3.4 per hour at Veteran (auto slots 2.6, alert-gated 0.85) and 4.3 at Elite (auto 1.9, alert-gated 2.4). Enemy fighters airborne: mean **3.6 (Veteran), 4.1 (Elite)**, of which
1.8 / 1.9 within 100 km of the player and 3.0 / 3.3 within 200 km; the nearest one is within 100 km 76 % / 77 % of the time and within 200 km 99 % / 100 %. At the first snapshot the four auto slots are already airborne, 68-476 km from the player.

### Open / untested
- Live test of any of this; PG and NC rosters and bases are from START only (PG: one launch seen, from record 27, type matching), LB `ds:9C78`.
- Where the four auto slots are placed at mission start, and their initial headings.
- The steering law (turn rate from agility, speed ramp) and the landing touchdown test; the hunt entry condition for `0x40`/`0x100` slots; what `+0xC` (count) is for (START or nothing).
- Elite rates rest on 5 flights; they say nothing about Regular or Green. The `ds:4EF2` cadence effects at ROE levels 1-2 are from the code only (every logged flight is level 0).
- The civil 767 traffic (type 18, neutral fields) that appears in PG launches is a separate mechanism, not examined.
