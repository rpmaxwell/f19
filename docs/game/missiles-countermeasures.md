# Enemy ordnance and countermeasures (F-19, EGAME)

Decoded 2026-10-23 from the EGAME disassembly (analysis/egame_re/egame_all.asm) and checked against the memlog flights
where noted. Builds on FINDINGS.md ("Photo, lock and guidance", "Flight-log variables", "Radar sites") and
notes/aircraft_profiles.md. Memlog checks: `analysis/missile_scan.py`, `analysis/missile_track.py <run> [-v]`,
`analysis/missile_rules_check.py <runs>`.

Units: positions in waypoint units (64 per km); angles 65536 = 360 deg; fs = frame scale ds:4EFC (frames per 2 s tick);
1 missile speed unit = 2 waypoint units per game s = 31.25 m/s. Distance = game metric (max + min/2).

## 1. How an enemy missile works

**Slots.** Projectile slots ds:53FC + 0x18 x i; **0-7 are enemy**, 8-11 the player's. +0 X, +2 Y, +4 alt ft, +6 speed,
+8 heading, +A pitch, +C roll, +E ttl (frames), +10 weapon spec, +14 target, +16 launcher (site record index > 0,
1 = SA-14 cell, -n = sim aircraft n). Spec table ds:4C10 + 0x12 x spec: +8 range km, +A max speed (>> 6), +C **seeker
class**, +E agility.

**Launch** (sites 0x5419, SA-14 cells 0x5740, aircraft 0x67d8):
- Site: alert > 0xC0, missiles already homing within 16 km <= opp, > 20 km from an airfield record, you within the
  missile's range, slot (site index & 7) free. Starts at the site, alt 0, **full speed at once**, pitch 90 deg (straight
  up), heading at you. ttl = range x 16 x fs / speed, i.e. it flies **exactly its range in km**, then disappears.
- Aircraft: its nose within 33.75 deg (0x1800) of the bearing to you, you closer than its missile range and farther than
  **8 - 2 x opp km** (Elite 2 km), alert > 0xC0. Starts at the fighter, 25 ft below it, on its heading/pitch, full speed;
  ttl twice as long (flies **2 x range**).
- SA-14 cell: see FINDINGS "SA-14 cells"; starts at a random point in the cell, pitch 45 deg, 16 km of flight.
- Memlog: SA-10 first-snapshot ttl 494 vs 500 predicted (fs 7); SA-14 86-89 vs 91 (fs 5).

**Guidance every frame** (0x6af4, enemy branch 0x746b):
1. **Aim point** = the player, or the **decoy** position while a decoy is alive (slot ds:520A, below).
2. **Seeker test** 0x760c: passes if the aim point is within **22.5 deg of the missile's nose** (class 3 skips this cone).
   Class 0 (IR) also needs the **missile heading within 45 deg of YOUR heading** (it must be behind you).
   If the cone fails with you > 135 deg off its nose and farther than one speed step, **the missile is destroyed** (overshoot).
3. **Jamming / network** (0x74a5): the missile can lose lock if
   - radar jammer on (ds:684C 0x10) and class > 0, or IR jammer on (0x20) and class <= 0, **or**
   - no network alert / player weapon hit for 100 game s (timer ds:5522 < ds:6EAA; SA-14 exempt).
   Then it loses lock when farther than the **burn-through distance opp x 256 + 384 units (6 / 10 / 14 / 18 km for
   Green..Elite)**, and **at any distance** if its class is 1 or -1.
4. **Flares / chaff** (slots 1-3, see below): if one is inside the seeker cone it captures the missile for that frame.
5. **Launcher support** (0x75a9): **farther than 8 km (0x200 units) from you**, a site's missile needs the site's
   "seen on its last pulse" flag (record +8 bit 0x10) and an aircraft's needs its "seen on its last check" flag (sim
   +0x16 bit 8). Without it the missile loses lock. (Bug: sites with record index 1 or 2 are exempt.) Inside 8 km
   every missile is autonomous.
6. **Steering** only while locked: heading error clamped to min((opp+1) x 256, agility x 128) and pitch to
   min((opp+1) x 512, agility x 256), applied x4/fs per frame: **turn rate = 2 x min((opp+1) x 256, agility x 128)
   units per game s**. Aims ~7 deg above your line (loft). Unlocked: flies straight and **noses over toward the ground**
   (pitch decays), so a dumb missile at low altitude soon hits terrain.
7. **Hit** (0x727d): game distance + |dAlt|/32 < **speed x 8 / fs** (two frames of travel, ~1 s: SA-2 ~1 km at fs 4,
   SA-14 ~440 m), **and locked this frame, and ttl > 4 x fs** (no hits in the **last 8 game s** of flight). Decoy alive
   -> no damage. Otherwise "hit by <missile>", damage routine 0x7a0b: **opp + 1 random damage bits** (count += opp+1),
   event type 5 with the spec.

**Warnings:** lamp 1 (IR, class <= 0) and lamp 0 (radar, class != 0) flash while an enemy missile is locked and pointed
within 22.5 deg of you, unless damage bit 0x01 or a decoy is alive. Audio cue (0xdd7f(10)) when range < speed x 16
(~8 game s out). Class -1 lights both lamps.

## 2. All enemy ordnance

Class meanings from what counters each one (names mine): **0** IR, tail-only; **-1** IR, all-aspect, jammed at any range;
**1** command-guided, jammed at any range; **2** semi-active radar; **3** all-aspect radar (no seeker cone, chaff only on
the beam). Turn = deg per game s (Green is lower: all capped at 2.8). "Beam km" = range below which a 77 m/s
(~190 kt) crossing F-19 moves across the missile's view faster than the missile can turn (my estimate, untested).

| spec | missile | fired by | class | range km | flight s | top m/s | turn Vet/Elite | beam km |
|---|---|---|---|---|---|---|---|---|
| 0x01 | SA-2 | SA-2 site | 1 command | 125 | 129 | 969 | 5.6 | 0.8 |
| 0x02 | SA-5 | SA-5 site | 1 command | 150 | 171 | 875 | 1.4 | 3.1 |
| 0x03 | SA-8B | SA-8B site | 2 SARH | 65 | 116 | 562 | 4.2 | 1.0 |
| 0x04 | SA-10 | SA-10 site | 3 all-aspect | 125 | 143 | 875 | 2.8 | 1.6 |
| 0x05 | SA-11 | SA-11 site | 2 SARH | 100 | 139 | 719 | 4.2 | 1.0 |
| 0x06 | SA-12 | SA-12 site | 3 all-aspect | 150 | 155 | 969 | 2.8 | 1.6 |
| 0x07 | SA-13 | SA-13 site | 0 IR tail | 65 | 149 | 438 | 5.6 | 0.8 |
| 0x08 | SA-N-4 | ship | 2 SARH | 30 | 53 | 562 | 4.2 | 1.0 |
| 0x09 | SA-N-5 | ship | -1 IR all-aspect | 30 | 69 | 438 | 5.6 | 0.8 |
| 0x0a | SA-N-6 | ship | 3 all-aspect | 125 | 143 | 875 | 2.8 | 1.6 |
| 0x0b | SA-N-7 | ship | 2 SARH | 100 | 139 | 719 | 4.2 | 1.0 |
| 0x0c-0x0f | Hawk / Rapier / Tiger / Seacat | NATO sites (2, 2, 1, 1) | | 125 / 65 / 30 / 30 | | 438-562 | 4.2-5.6 | |
| 0x10 | AA-2 | IL-76 | -1 IR all-aspect | 28 (14 x 2) | 39 | 719 | 5.6 | 0.8 |
| 0x11 | AA-8 | MiG-23 | 0 IR tail | 24 | 27 | 875 | 7.0 | 0.6 |
| 0x12 | AA-6 | MiG-25 (F-4E) | 2 SARH | 100 | 86 | 1156 | 2.8 | 1.6 |
| 0x13 | AA-7 | MiG-29, Su-27 (F-14) | 2 SARH | 68 | 78 | 875 | 2.8 | 1.6 |
| 0x14 | AA-9 | MiG-31 | 2 SARH | 164 | 169 | 969 | 4.2 | 1.0 |
| 0x15 | AA-10 | type-21 slot = OTH radar, never fires | 3 | 64 | | 969 | 5.6 | |
| 0x21 | SA-14 | SA-14 cells (0x10 grid bit, daylight) | 0 IR tail | 16 | 37 | 438 | 7.0 | 0.6 |
| 0x22-0x25 | AA-6 / AA-7 / AA-9 / AA-10 | no user found yet | -1, -1, -1, 0 | 50 / 34 / 82 / 64 | | | | |

Not missiles: enemy **gunfire** (sim aircraft; air-to-air test |dX|+|dY|+|dAlt|/32 < (0x17a/fs)/(opp+1), IL-76 gun hits
seen in logs; not decoded further) and your own **bomb blast** (FINDINGS). No AAA gun sites were found in the tables.

**What this means in practice:**
- A SAM from 100+ km takes 2+ minutes and is guided only while its site keeps seeing you (until it is within 8 km).
- SA-14s fly only 16 km and can't hit in their last 8 s: the effective reach is ~12 km, less if you are running away.
- Fighter missiles are fast and launched close in (Elite flights: AA-9s from 4-13 km, hits 1-5 s later, always from
  behind, because the fighter must point at you). The fighter's own radar must keep seeing you while it is > 8 km away.

## 3. Countermeasures (decoded)

| | key / store | lifetime (Green / Reg / Vet / Elite) | affects | rule |
|---|---|---|---|---|
| Flare | 12 | (9 - 2 opp) ticks: 18 / 14 / 10 / 6 game s | class 0, -1 | captures if within the missile's 22.5 deg cone (class 0: missile also within 45 deg of your heading) |
| Chaff | 18 | same | class 1, 2; class 3 only on the beam | class 3: only when your heading is roughly across the missile's line (34-146 deg Veteran, 51-129 Elite) |
| Decoy | 3 | x8: 144 / 112 / 80 / 48 game s | everything | all missiles aim at it; **no missile damage**; no network alerts; fighters vectored to it; warning lamps dark. Only one at a time; fails with damage bit 0x40 |
| Radar jammer ('4') | - | while on | class 1 at any range; 2, 3 beyond burn-through | +16 EMV, half alert gain (bad for detection) |
| IR jammer ('3') | - | while on | class -1 at any range; 0 beyond burn-through | no EMV cost found in the EMV formula |

Flares and chaff are dropped at your position and stay there; they share 3 slots (decoys use slot 0). While captured, a
missile flies straight and can't hit. When the flare/chaff dies it looks for you again with the same 22.5 deg cone.
Damage bit 0x40 switches both jammers off and blocks decoys (not flares or chaff).

## 4. Hypotheses: avoiding each type

General (all classes):
- **H1 Stay unseen after launch.** Beyond 8 km a missile needs its launcher to keep seeing you. Turn to the launcher's
  blind aspect (beam a doppler site/fighter, fly straight at/away from a pulse one), drop low and slow, or get into a
  mountain cell: the missile goes dumb and noses into the ground. Strongest tool against long SAM shots.
- **H2 Get out of the cone.** A missile only steers while you are within 22.5 deg of its nose. A late, hard beam turn
  (inside the "beam km" of the table) should break lock; > 135 deg off its nose destroys it (not class 3).
- **H3 Outrun the clock.** No hit in the last 8 game s; running away from a short missile (SA-14, AA-8, SA-13 at range)
  uses up its flight.
- **H4 Decoy is the panic button.** Inside its 48-144 s nothing can damage you by missile and no network alert fires.
  Save the 3 decoys for launches inside 8 km or multiple missiles.
- **H5 Fly low.** Dumb missiles nose over; low altitude turns every lost lock into a ground impact sooner.

Per class:
- **IR tail (SA-13, SA-14, AA-8):** turn more than 45 deg off the missile's heading (it can then never re-lock unless you
  turn back), or flare + turn. IR jammer beyond burn-through. SA-14: also leave the cell / run.
- **IR all-aspect (AA-2, SA-N-5):** IR jammer on = always lose lock. Flares. The tail rule doesn't apply.
- **Command (SA-2, SA-5, Tiger, Seacat):** radar jammer = always lose lock; chaff. SA-5 turns only 1.4 deg/s: a beam turn
  inside ~3 km should beat it.
- **SARH (SA-8B, SA-11, SA-N-4/7, AA-6/7/9):** chaff; radar jammer beyond burn-through; H1 beyond 8 km (most
  important vs fighters: beam doppler fighters, run from pulse ones).
- **All-aspect radar (SA-10, SA-12, SA-N-6):** no cone, no overshoot kill: only turn rate (2.8 deg/s) beats it - beam it
  inside ~1.6 km; chaff only while beaming; radar jammer beyond burn-through; H1.

## 5. Tracking proximity / intercept

Read slots 0-7 each cycle (f19_state already has PROJ = ds:53FC; extend `read_projectiles` to the enemy slots). Per live
missile (ttl > 0, spec 1-0x25):
- range r = game_dist(dX, dY) + |dAlt|/32 units; **time to go** ~ r / (2 x speed) game s; inside the **hit radius**
  8 x speed / fs; **dead time** ttl < 4 x fs (can't hit).
- **off-bore** = |bearing(missile -> you) - missile heading| (< 22.5 deg = can lock; > 135 deg = overshoot);
  **tail angle** = |missile heading - your heading| (IR class 0 needs <= 45).
- **guided?** = cone ok, launcher still sees you or r < 512, not jammed (jammer + class + burn-through), network timer.
- **intercept score** = will it be within the hit radius before ttl < 4 fs, given its turn limit? The same per-heading
  scoring FighterWatch uses can pick the heading that breaks the cone or the launcher's track soonest.
- Missiles homing within 16 km: ds:7F96 (launch gate: <= opp). Warning lamps / message "<site> firing <SAM>" as backup.

## 6. Test plan

**Already checked against the memlogs (2026-10-23)** - 7 flights with enemy missiles: 57 SA-14s (Veteran, 0 hits),
1 SA-10 and 5 AA-9s (Elite, 2 flights, 3 hits):
- IR tail/cone rule: on 409 snapshot pairs where the rule says an SA-14 is blind, its heading changed in **1** (408 flew
  straight). 42 of 57 SA-14s started blind (you more than 45 deg off their heading). Confirms the rule.
- Launcher support: the Stupsk SA-10 (Wittstock, record 16) launched at 114 km; the site's bit 0x10 cleared 5 ticks later
  and the missile's heading changed by **~0** for its whole 140 s flight; it passed 16 km off. Confirms H1 (one case).
- Turn limit: no observed heading change above the decoded limit (AA-9 max 448 vs 768, SA-14 404 vs 1280 units/s).
- Launch ttl formula matches (SA-10 494 vs 500, SA-14 86-89 vs 91).
- 3 AA-9 hits at Elite each added 4 to the damage count (= opp + 1).

**Still to do on existing logs:**
1. Replay the guidance step (aim point + loft + clamp) per snapshot and compare predicted vs actual heading/pitch on
   the 22 "tracking" SA-14 pairs and the AA-9s (the tracking case is only weakly tested: missiles on a collision course
   barely turn).
2. Hit test: range just before each of the 3 hits vs 8 x speed / fs; event type 5 spec = AA-9.
3. Launch gates: fighter nose-to-bearing < 33.75 deg and range > 2 km at each AA-9 launch; SAM launch range; ds:7F96.
4. Find the source of the 0x22-0x25 specs (which shooter uses them).
5. Check the network timer ds:5522 at every launch (SA-10: 1744, above the 1400 threshold, as expected).

**Live trials** (container on VNC 5900, save states, memlog at the fastest rate; no-damage mode, R key / ds:684C
0x1000, keeps the hit message but skips damage, so each run can be repeated):
1. SA-14 cell, Veteran CE: (a) hold heading tail-on, (b) break > 45 deg at launch, (c) flare then hold, (d) IR jammer at
   > 14 km. Expect hits only in (a).
2. Chaff vs SA-11 (class 2) inside 14 km; chaff vs SA-10 head-on vs beaming (class 3 rule).
3. Radar jammer vs SA-2 inside burn-through (should still lose lock: class 1) vs SA-11 inside (should not).
4. Launcher denial: after an SA-11/SA-10 launch at > 30 km, turn to the site's blind aspect / drop into a rough or
   mountain cell; expect the missile to go straight and fall.
5. Late beam turn vs an SA-10 / SA-11 inside 2 km (cone break / turn-rate).
6. Decoy with a missile inbound: missile turns to the decoy point, no damage, no network alert while it lives.
7. Fighter: beam a MiG-29/31 after it fires from > 8 km; expect the AA-7/AA-9 to go dumb when the fighter's flag 8 clears.

## 7. Re-read for analysis/missile_game.py (2026-10-23, later)

The frame rules are now code: `analysis/missile_game.py` (`check` = R1 gate). Replayed snapshot to snapshot on every
memlog missile: **629/631 pairs** within 2 deg / one frame of travel (13 of them need one frame of snapshot skew: MemLog
reads 64-B chunks in 64-chunk batches, and slot 6 straddles a batch edge), **26/27 pairs where the missile really
turned**, and **64/64 outcomes** flown from each missile's first snapshot along the logged track (all 4 Elite AA-9 hits,
the SA-10 miss, 57 SA-14 misses). The two misses: an AA-9 on the edge of its cone (the interpolated player position
decides the frame it loses lock) and one SA-14 pair at a batch edge.

Corrections and additions to §1-3 from the re-read (each is in the code):
- **Expired flares / chaff keep capturing.** The ager (0x47a3) only counts ttl down; the slot keeps its kind, and the
  capture loop (0x750a) tests the kind, not the ttl. Memlog 1792546698 (Veteran, one flare): an SA-14 flew dead straight
  for 7 snapshot pairs ~15 s after the flare's ttl hit 0; with the rule all 7 replay exactly, without it all 7 fail.
  A slot is only freed by being reused. Chaff: same code path, no memlog case yet.
- **A CM behind a missile destroys it.** The capture test calls the same seeker (0x760c) on the CM's position, overshoot
  rule included: once the missile has flown past the flare/chaff point (> 135 deg off its nose, farther than one speed
  step), ttl = 0. So a missile captured by a CM dropped on our track usually dies passing it - the AA-9 traces end
  "overshoot on CM". Only eligible CMs count (flare vs class <= 0, chaff vs 1/2, class 3 only on the beam).
- The ager ages only the **lowest live slot** per frame: two live CMs age at half speed each; a live decoy (slot 0)
  freezes every flare / chaff. A new flare/chaff takes the **highest free** slot of 1-3; none free -> the store is spent
  and nothing drops.
- **Unlocked nose-over** = pitch -= 2048 / fs per frame while pitch > 0, then stop: a dumb missile ends up level (memlog:
  -397, -2.2 deg, at 7 deg/s), it does not dive into the ground (H5 is weaker than thought).
- The game's **atan2 (0xd17d) is a polynomial**: r = min/max in Q14, angle = r (0x2800 - |0x1333 - r| 0xB00 >> 14) >> 14,
  up to ~8 % low for small angles. The Stupsk SA-10 at 114 km held a heading 26 units left of the true bearing - exactly
  this. Movement uses rounded sin/cos (truncation is 1-2 units/frame off).
- Heading-error clamp (0xd0da): an error below -16384 (> 90 deg left) clamps to +max: only class 3 (no cone) sees it.
- The hit test uses the seeker's PRE-move distance and the post-move altitude; a decoy hit sets ttl = 2 fs (dies) with
  no damage. The overshoot kill leaves ttl at -1.
- **Decoy key '5'** (key code 0x635 -> 0x468f(3)); '1' flare 0x231, '2' chaff 0x332, '3' IR jammer, '4' radar jammer.

**F-19 turn performance** (R6, 25 756 memlog pairs): turn rate ~ 337 x tan(bank) / kt deg per game s - a third of a
coordinated turn (20 deg bank at 190 kt = 0.6 deg/s; 45 deg at 175 kt = 1.9 deg/s); roll rate under nav <= 2 deg per
game s (p99). The game ran 2.45 game s per real s (median; 10-90 % 2.0-2.9). No heading change outturns a missile
(2.8-7 deg/s), so inside 8 km the countermeasures carry the defence (H2 "late beam turn" does not work for the F-19).

**Closed-loop replay** (`scripts/missile_replay.py <runs> --closed-loop`): from each Elite launch snapshot, the game's
missile rules flown against the player model under MissileWatch, CMs landing after a key latency. Straight on: 4/4
AA-9 hits (as in the real flights). MissileWatch: 0/4 at a 0.3-1.0 real s key latency (one chaff each for the close
shots, a decoy for the two where chaff left < 2x the hit radius). At 2.0 s latency the 3.5-3.9 km shots (~4 game s of
flight) get through whatever is done: **the key latency is the first thing to measure live.**
