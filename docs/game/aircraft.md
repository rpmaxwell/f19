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
