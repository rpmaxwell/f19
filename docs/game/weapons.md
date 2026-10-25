# Weapons

Ordnance vs target strength, weapon release and blast damage, photo/lock/guidance and guns (EGAME). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Ordnance vs target strength — decoded from EGAME.EXE (2026-10-17)

`analysis/ordnance.py` prints all of this from the game files. EGAME DGROUP = image 0x1ecf0 after
`unexepack.py` (START has the same tables at its own DS).
- **Missile/bomb specs** (18 B: name[8], u16 range?, speed?, mode, agility?, flags?), indexed by `spec`:
  0x16 AIM120, 0x17 AIM-9, 0x18 HARM, 0x19 Penguin, 0x1a Harpoon, 0x1b AGM-65, **0x1c LGBOMB, 0x1d RTBOMB,
  0x1e FFBOMB** (laser-guided / retarded / free-fall), plus the enemy SA-x/AA-x entries.
- **Loadout catalog** (20 x 26 B: short[10], long[12], u16 spec, u16 qty), weapon index 0-19: AIM-9M, AIM-120,
  HARM, Penguin, Harpoon, Maverick, GBU-12, Rockeye, Durandal, Mk82 Slick, Mk82 Snakeye, Rockeye II, Fireeye,
  Fuel-Air, IN Cluster, Minelets, Camera, Extra Fuel, Guns, Special. No damage field anywhere.
- **Effectiveness matrix** ds:5106, int8 [20 weapons][13 categories], right after the catalog.
  missileTargetCompat = code 0x91ae: `matrix[w*13 + (cat[class & 0x7f] & 0xf)]`. Target table ds:80AA,
  16-B records in .WLD order (class at +0xE, destroyed = byte +8 bit 0x80). Category table ds:95D2.
- **Target category** = .WLD 100-B table (just before the territory grid) indexed by name id of the
  type (= class & 0x7f); low nibble = category, 0x10 = flag (airbases, bridges, planes, camps, Sub Pen...).
  Categories: 1 AirStrip/Cargo Ship, 2 SAM Radar/Typhoon, 3 Mobile SAM, 4 Bridge/OTH/LPAR radar, 5 AirBase,
  6 AirPlane/Refinery, 7 Tank Farm/Camp, 8 Hangar, 9 HQ, 10 CV Kremlin, 12 boats, 13 SA/SS-20 missile,
  14 Oil Rig, 15 Nuclear Plant; 0 everything else (Sub Pen, Depot, Tower, Buildings, Destroyer, carriers).
  **13-15 overflow** the 13-column row and read the next weapon's row (no bounds check).
- **All three ground-kill paths are gated by the matrix** (destroyGroundTarget 0x77c3 has no other caller);
  v = matrix value, opp = opponent level (ds:4EF0 = gameData+0x3e):
  - bombs (spec 0x1c-0x1e), 0x7123: kill if dist(impact, nearest 3D object) < v*64/(opp+2) waypoint units
    (= v/(opp+2) km). The impact snaps to the nearest 3D object; a record at exactly that spot is the victim,
    else a scratch slot with the 3D shape's category (as F-15's findWaypointEntry).
  - missile direct hit, 0x7380: kill if v > random(4), else HUD "Ineffective".
  - guns, 0xb423: kill if dist < v*6/(opp+2).
  - v = 0 -> that weapon can never kill that target. Unlike F-15 SE2, bombs don't bypass the matrix.
- Sub Pen (NC class 0x146, record "Severomorsk" at 21760,13568; none at Murmansk) is category 0: only Maverick 2,
  GBU-12 4, Mk82 Slick 3, Fireeye 2, Fuel-Air 4, IN Cluster 1, guns 3 can kill it.
- HARM is 0 against SAM Radar (cat 2): it must work some other way (radar shutdown?), not checked yet.
- territory.py bug: GULF.WLD's first name is "Bandar Abbas", so the `[A-Z][a-z]{2,}\0` search lands 7 bytes late
  and the PG grid is shifted (ordnance.py backs up over the multi-word name).

## Weapon release, altitude and blast damage — EGAME (2026-10-17)

- ds:46DA/46DC/46DE/46E0 = heading/pitch/roll/altitude ft (ds:46E0 = phys 0x40250 at this DS).
- Fire routine 0x7a65: refuses only if |roll| > 0x3000 (~67 deg), no ammo, spec 0/-1, or bay closed
  (ds:684C bit 2; message "Bay closed"; extra fuel exempt). **No altitude check.**
- Projectile slots ds:53FC + 0x18*i (player 8-11): +0 X, +2 Y, +4 alt ft, +6 speed (= velocity >> 11),
  +8 heading, +A pitch, +C roll, +E ttl, +10 spec, +12 weapon, +14 locked target, +16 pitch floor.
  Spawned at plane alt - 20 ft. Pitch: missiles and LGBs = plane pitch - 0x1000 (22.5 deg nose-down);
  free-fall (0x1e) keeps the plane's pitch and noses over 0x800/frame-unit to a floor; retarded (0x1d)
  = straight down (0xC000) at speed 1. LGB pitch is kept <= -0x800 (always >= 11 deg down).
- Guided steering 0x6df7: pitch/heading error clamped per update to agility (spec +6) << 8 / << 7;
  enemy missiles get an extra opponent-level clamp. Unlocked missiles never pull up.
- Impact: alt < 0 or terrain -> projectile ends ("<weapon> ground impact" for player missiles).
  Missile proximity uses |dAlt| / 32 + horizontal; bombs kill on horizontal distance only (at ground).
- **Blast damage 0x7185** (after every bomb impact): horizontal dist(plane, impact) + alt_ft/32 < opp*32 ->
  damage via 0x7a0b (opp+1 random damage bits, skipped if ds:684C bit 0x1000). Directly overhead that's
  < 1024 / 2048 / 3072 ft for Regular / Veteran / Elite; never on Green.

## Photo, lock and guidance — EGAME (2026-10-17)

Planner-facing summary: [notes/targeting_for_planner.md](../design/targeting_for_planner.md).
- Briefing blocks in EGAME: ds:8794 + 0x12*k (+0 type, +2 target index), as in START.
- **Photo** (0xba36): camera selected (spec -1), bay open, frame taken (ds:5B18 == 3). For each type-1 block whose
  target == locked ground target ds:5526 and not destroyed: r = range (0xc954, octagonal waypoint units, also sets
  bearing ds:6348); e = atan2(-alt/32, r) (0xd17d, 65536 = 360 deg); pass if
  r + |e - pitch + 0x6ef|/16 + |bearing - heading|/16 < (8 - 2*opp)*64 -> "Target photographed.".
  Boresight mode (ds:684C bit 0x100) picks the target where the nose meets the ground (10 km ahead if not nose-down).
- **HUD lock** (ds:6342, "Missile Lock"): HUD projection 0xc673 stores depth ds:9642 = camDepth >> 3 (negative ahead).
  Ground: target within 0x30 (octagonal px) of the HUD centre and -depth/7 < spec range (+0x8) -> D < 56*R units;
  in-range colour at -depth/4 < R. LGB (class 0x1c): range < alt*5/32 units. Air (class 7, sim objects
  ds:8854 + 0x24*i): -depth/8 < R -> D < R km.
- **Spec table** ds:4C10 + 0x12*spec: +8 range R, +A max speed (>> 6 = speed cap), +C class (7 AAM, 4 HARM,
  5 anti-ship, 6 Maverick, 0x1c/1d/1e bombs), +E agility.
- **Guidance** (0x6af4): projectile speed starts at velocity >> 11 and rises 1 every other frame to max >> 6.
  Classes 4/5/6/0x1c steer to the projectile's own target (+0x14, set at launch for class 6, or class 5 when the
  target has flag 8); without one they search the target table (4: active radars; 5: category & 0xC; 0x1c:
  category & 0xC == 0; 6: any) through the seeker test 0x760c. Nothing depends on the plane after launch.
- **Supply drop:** the canister impact (spec 0x26) within 0x100 units of a type-3 block target before the deadline
  (ds:6630 < ds:9638) -> "Equipment Delivered", else "Bad Drop". Cue "Drop supplies now": on screen and depth > -16.
- **Seeker test** 0x760c: passes if the bearing from projectile to target is within 0x1000 (22.5 deg) of the
  projectile heading. Player classes skip the plane-heading check (class 0 only). No range or elevation limit.
- **Guided aim point** (0x6e75): desired pitch = atan2((target_alt - alt)/32 + range/8, range), i.e. ~7 deg above
  the target line, so missiles loft.
- **Airspeed** kt = ds:7F94 / 27 (ds:855A). Missile start speed = ds:7F94 >> 11 = kt/76.
- **Frame scale** ds:4EFC = (measured frame value + 2)/4, clamped [4 - accel, 15]; halved in time acceleration.
- analysis/release_geometry.py simulates the low-altitude Maverick sink and the photo windows from these rules.

## Guns — EGAME (2026-10-18, from the disassembly; not yet seen in flight)

Plan built on this: [notes/gun_attack_plan.md](../design/gun_attack_plan.md).
- **Trigger:** key 0x0E08 (Backspace) sets ds:5C6E = 1 ("button 0", OR'd with joystick button 0 in 0xd364); Enter (0x1C0D)
  sets ds:5C70 (button 1). Both are cleared at the start of each key-processing pass (0xd3b0).
- **Spawn** (0x47f4, odd frames only: ds:5520 & 1): if button 0, rounds ds:4EEC > 0 and ds:944A == 0: rounds -= 40/fs,
  slot = (ds:5520 >> 1) mod ds:855E; speed r = 0x70/fs units/frame along the **nose** (ds:46DC pitch, ds:46DA heading):
  vAlt = r*sin(pitch) << 5 (ft), vX/vY from r*cos(pitch) and heading (Y negated); position = player units ds:94EE/94FE
  + one step, alt = ds:46E0 + vAlt - 2. With no trigger the odd-frame slot is cleared. Slots ds:9B88 + 12*i
  (X, Y, alt, vX, vY, vAlt), count ds:855E = clamp(2*fs, 3, 16), +4 more in the loops (other shooters?).
- **Motion:** every frame pos += v. There is no gravity and the plane's velocity isn't added, so the impact is the nose line meeting alt 0.
  Life ~4*fs frames x 112/fs = ~448 units (~7 km) of slant range.
- **Ground impact** (0xb37a: alt < 0): the point goes to ds:94EA/94F6/94FC; 0xc892 snaps to the nearest 3D object (0x11080) and returns
  the target record at exactly that spot, or 0 = a scratch slot with the object's category. If it isn't destroyed, kill when
  octagonal dist(impact, object) < matrix[18][cat]*6/(opp+2) -> 0x77c3 + "<name> destroyed by gunfire". SAM Radar / Mobile SAM
  (v 4): dist <= 11 / 7 / 5 / 3 units for Green / Regular / Veteran / Elite.
- **Flight-tested 2026-10-18 (Leck, Regular, logs/gun/):** Backspace held fires (8 rounds/bullet at fs 5, 10 at fs 4).
  **Velocities are whole units a frame, rounded:** r = 112//fs, k = round(r*sin(-pitch)) units (x32 ft) of descent,
  rh = round(r*cos pitch), vX = round(rh*sin hdg), vY = -round(rh*cos hdg). Impact after n = (alt-2-32k)//(32k) + 2 frames
  at player + n*v (reach n <= 2*slots). gun_probe.ccip() predicted 8 of 8 impacts to 0-3.5 units over -3.7/-15/-24 deg
  dives; the exact nose line was ~140 units off. So pitch only has to sit in a band, range comes in r-unit steps set by
  altitude, and only a heading within ~asin(0.5/rh) (~1-1.3 deg) of N/S/E/W fires with no drift. fs changed 5 -> 4 mid-dive.
  Pull-outs from -15 / -24 deg lost ~125 ft (bottoms 367 / 202 ft).
- **Live 2026-10-19 (Regular):** Wittenberg SA-13 (record 7) killed by a burst landing 6.5 / 1.5 u from its record.
  Wittstock SA-10 (record 16, 11328,12992) survived 20 bullets on its exact X line, all 6-55 u *past* it (three at
  6.5 u). After that run the scratch record 0 held an unnamed object (class 0x21) at (11344,13016), so bullets snapped to
  another site object. The distance test uses the snapped 3D object's own position ([ds:6E58]+4/+8 >> 5), not the record.
  Unresolved: how 0x11080 (far call, not decoded) picks the object. Aim for the record itself (miss <= 3), not the edge.
  ds:94EA is only written while ds:6C54 <= 0 (set to 8 after a kill) and also by enemy gunfire hitting the player
  (IL-76 hits logged at the player's own X/Y).
  Cottbus SA-13 (radar record at 14016,15168) killed on heading ~133-134 deg (diagonal) in a -12..-19 deg dive from ~2000 ft
  at fs 4: the burst clustered 1-40 u around it and a bullet 1 u off killed it (ds:94EA then froze, as the 6C54 cooldown
  predicts). Off-cardinal rounding costs < 0.5 u/frame per axis, so with a steep dive (n ~ 8 frames) it's only ~3-4 u:
  cardinal headings matter for shallow, long shots, not steep ones. Bottom after that pass: 126 ft.
  Impacts also appeared 3.5 u from the Cottbus Mobile SAM record ~400 u from the player with no rounds fired: other
  shooters (the 4 extra slots) write ds:94EA too.
- **Air:** bullets vs sim objects with flag 2: |dX| + |dY| + |dAlt|/32 (octagonal-ish) < (0x17a/fs)/(opp+1) -> "Hit by gunfire" damage.
- Kill points (END 0x4258 [roe][cat], cat = END ds:998A[class & 0x7f] & 0xf, the .WLD 100-B category table read at
  END_decomp.c:132): Limited 2,6,1,1,3,8,2,2,5,3,1,9,4,0,0,0; Conventional 4,12,3,3,6,16,4,5,11,7,3,18,8,0,0,0.
  Before the table: type flag 0x40 or a target in a non-enemy cell -> -25; flag 0x80 -> ds:4222 (-10/-4/+1).
