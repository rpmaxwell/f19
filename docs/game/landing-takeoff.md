# Landing and takeoff

Takeoff direction and the landing and takeoff rules (EGAME). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Takeoff direction: decided by the theater alone — decoded from EGAME.EXE (2026-10-17)

EGAME flight init at image 0x1FC1 (after `unexepack.py`; ds offsets are EGAME DGROUP = image 0x1ecf0):
```
les bx,[6640]            ; far ptr to the pilot record (same fields as START's ds:991C one): +0x38 theater, +0x3A level
test byte es:[bx+38],1 ; ax = 0
jne  -> heading = 0       ; ds:46DA = player heading (= old phys 0x4024A), 65536 = 360 deg
else    heading = 0x8000
if takeoff record flags & 0x200 (carrier): heading high byte += 4   (+0x400 = +5.6 deg)
```
and at 0x3CFB the start point is put at the runway end: dir = +1 (theater odd) / -1 (even);
airbase: 3D Y -= 0x708 * dir (0x708 position units = 56.25 waypoint units, ~0.9 km, south of the
base record for a northbound start); carrier: X -= dir << 7. The takeoff record index is ds:8798.
- Theater index (pilot +0x38) = 0 Libya, 1 Persian Gulf, 2 North Cape, 3 Central Europe: EGAME's
  per-theater file table at ds:006E reads lb/pg/nc/ce.xxx in that order.
- **Rule:** Gulf and Central Europe take off **north (0)**; Libya and North Cape take off **south (180)**.
  Carriers: 5.6 (Gulf) / 185.6 (Libya, North Cape). Base, wind, mission and targets play no part.
- Matches every logged takeoff: CE Ramstein (start 3392,23288), Gutersloh (3392,19192), Rhein-Main
  (rolling north at 3392,21103) all 0.0; NC Gallivare (rolling south at 3392,21671, nav 1792032168) 180.0;
  NC USS Kennedy (11012,3328) 185.6 on every carrier flight (the earlier "catapult ~185").
  Libya and Gulf are unflown; the rule there comes from the code only.
- `scripts/read_mission.py` now writes `takeoff_heading` (and `takeoff.heading`, `takeoff.carrier`) in the
  mission json; the planner (analysis/planner/plan.py `game_takeoff_heading`) applies the same rule by default and
  writes one route.json ("both" -> route_N/route_S only when plan.json asks for it).

## Landing rules — EGAME (2026-10-17)

Landing mode m = gameData+0x40 (0 No Crashes, 1 Easy, 2 Realistic). ds:684C bits: 0x01 gear UP, 0x02 flaps,
0x04 bay open, 0x08 brakes. Airspeed kt = ds:7F94/27. One fs-frame step (ds:4EFC frames) = ~3.2 game s
(position += 27*kt/8 units of 2048/km per step).
- **Runway box** (0x4144, every frame): nearest friendly base (flags & 0x201 and & 0x500), used within 0x200
  units (8 km) or when on the ground. Inside if |X - baseX| <= W and |Y - baseY| <= L (units; runways are N-S):
  airbase W 5, L 64 (+-1 km); flags & 0x800 (airstrip) L 32; carrier (flags & 0x200) W 8, L 30, deck at 128 ft
  (ground level ds:94EC). Easy adds W +4, L +8. Result in ds:5534.
- **Touchdown** (0x2cf2, the frame altitude reaches ground level, inside the box): crash code 5 ("crashed onto
  the runway, cartwheeled and exploded") if m * sink > 128 (carrier 256), or m > 0 and gear up, or
  |roll| > (24/m) << 8 (Easy 33.75 deg, Realistic 16.9 deg). m = 0 skips all three.
  sink = -ds:9E90, vs = (velocity/16 * sin(pitch - AoA)) >> 14 with a Q15 sine = 3.375 * kt * sin(gamma) ft per step.
  So kt * sin(pitch - AoA) <= 19 (Realistic) / 38 (Easy), x2 on a carrier: <= 64 / 128 ft of altitude per 3.2 game s.
  AoA ds:6616 = (0xC00 * stall_kt / kt - 0x300) * cos(roll) (~12.7 deg at stall, less when faster); stall ds:9B82,
  -1/4 with flaps. No airspeed test as such: speed enters only through the sink.
- **On the ground:** roll forced to 0, negative pitch to 0, nose can't rise below stall speed. Brakes (bit 3) on the
  ground: velocity -= (32 - 8m) kt per step (Realistic 5 kt/game s, Easy 7.5); in the air they're an airbrake
  (v/16 per step). Speed also lags toward the thrust target (v/16 per step at idle).
- **Off the box** (alt 0 at ground level, outside the box, includes overrunning the end): crash code 1 ("Flying into
  the ground") if kt > 50, except No Crashes with damage <= 4 and fuel left.
- **Safe Landing:** in the box, kt <= 1, friendly base (flags & 0x500, not 0x800) for fs frames -> end code 0.
  Type-4 landing missions: "Cargo delivered" if this base is the block's target, before the deadline.
- Carrier wire (0x41a2): deck altitude, kt > 80, 16-20 units along the deck from the centre, heading within 45 deg.
- END crash codes (commData+0x28): 1 ground, 2 hill, 3 fuel, 4 missiles, 5 runway cartwheel, 6 ejection failed.
  END.EXE has no "tire"/"careened" text in this build.
- Rollout with brakes and idle (simulated): Realistic 150 kt stops in 0.84 km (below 50 kt after 0.73), 180 kt 1.15 km;
  without brakes 150 kt still does 50 kt after 2.6 km.

## Takeoff rules — EGAME (2026-10-17)

- Stall speed ds:9B82 = 120 kt * (1 + alt/65536) * isqrt(4 * load16) / 8, then -1/4 with flaps (684C bit 1).
  load16 = ds:662A = table ds:45FE[|roll| >> 8] (16 = 1 g wings level, up to 208) + elevator pull when airborne,
  capped at 128 (8 g). On the ground roll is forced to 0, so **stall = 120 kt (90 kt with flaps)**.
- On the ground the nose can't rise above 0 below stall speed (0x2b9e): **rotation speed = stall speed.**
- The plane climbs once pitch > AoA (vs = 3.375 kt sin(pitch - AoA)); AoA = 0xC00*stall/kt - 0x300:
  12.7 deg at 120 kt clean, 10.3 deg at 140, 5.9 deg at 200; with flaps 8.4 deg at 120 kt, 4.2 deg at 180.
- Runway left: start point 56 units (0.88 km) before the base record (see ["Takeoff direction"](#takeoff-direction-decided-by-the-theater-alone--decoded-from-egameexe-2026-10-17)); the runway box ends
  64 units past it, so **~1.9 km** (Easy +0.125 km). Touching the ground outside the box above 50 kt = crash code 1.
- Logged nav takeoffs (CE, full throttle): lift-off at 123 / 129 kt; 139 kt after 0.53 km, 190 kt after 1.03 km,
  206 kt after 1.12 km (~20 kt per real second).

## Ejection: when it kills you — EGAME + END (2026-10-28)

Established by static disassembly of EGAME.bin / END (`research/analysis/egame_re/egame_all.asm`, `end_re/END_decomp.c`) and a sanity check
of the inputs on the logged flights. **Not tested live** (Shift+F10 has not been pressed in a flight yet, see first_flight_checklist.md (g)).
Addresses are image offsets in EGAME.

- **The key:** BIOS key code 0x5D00 (Shift+F10) in the key dispatch (0xD9EF) jumps to the handler at 0xD6F5. Its only gate is
  `ds:944A == 0` (not already ejecting). No altitude, speed, gear or damage precondition.
- **The death roll is made once, at the instant of ejection** (0xD6FF-0xD75F):
  `T = 500 + rand(300)`; `S = (|pitch16| >> 5) + (|roll16| >> 5) + kt`; **if S > T: crash code 6 ("ejection failed"), else landingType (+0x26) = 2.**
  Then `ds:944A = 1` and the seat starts. pitch16 / roll16 are ds:46DC / ds:46DE as signed 16-bit angles (65536 = 360 deg, so
  each degree of |pitch| or |roll| adds 5.69 to S); kt is ds:855A = ds:7F94 / 27 (computed every frame at 0x29D4);
  `0xFB7C` is `abs(int16)`; `rand(n)` (0xD34B) is `(lcg15 * n) >> 15` with the MSVC LCG `seed * 0x343FD + 0x269EC3` (0xFBA4), uniform on 0..n-1.
  Hence **P(death | eject command) = clamp(S - 500, 0, 300) / 300**. **It does not depend on altitude.**
  Level at 190 kt it is 0; it starts when |pitch| + |roll| exceeds about 54 deg (190 kt) / 47 deg (230 kt) / 35 deg (300 kt) and is certain
  above about 107 / 100 / 88 deg. Example: a 60 deg bank at 230 kt is S = 571, 24 %; a 90 deg bank is S = 742, 81 %.
- **Nothing after a successful ejection kills.** Every later crash code is ignored: the setter 0x4ACD does nothing for a non-zero code while
  `ds:944A != 0`, and the ground-crash (0x4291, code 1) and missile-damage (0x486A) paths are themselves gated on `944A == 0`. The seat
  (0x24F1-0x2552) integrates `ds:966A` (altitude + 8 at ejection) by `clamp(32 - n, -160/fs, 128/fs)` per frame; reaching 0 calls the setter with
  **code 0** (no crash), so landing under the seat at any altitude is survived.
- **Setter callers (all of them, by a scan for near calls to 0x4ACD):** code 1 at 0x2159 and 0x42DB (ground), 2 at 0x43E9 (hill), 5 at 0x2D9C (runway cartwheel),
  6 at 0xD753 (ejection failed), 0 at 0x254C and 0x439A (no crash). Codes 3 (fuel) and 4 (missiles) are **not** set through this setter (no other
  write to commData+0x28 exists in EGAME.bin); where END gets them is open.
- **Debrief (END 0x449C, case 1):** landingType 2 shows "Good Bailout" if +0x28 == 0 and ds:978C != 0, "Captured" if +0x28 == 0 and ds:978C == 0
  (the territory byte already used for the x2/8 rule), and **"Bailed & Died" if +0x28 != 0**. With the guard above that needs a non-zero code set *before*
  the ejection began (a plane already crashing or destroyed). A failed roll leaves landingType unset and prints "your attempt to eject ... failed" instead.
  commData+0x28 != 0 is the "dead" flag (see scoring-awards.md).
- **Sanity check of the inputs, 34 logged flights (ReplayReader), 19,357 airborne states at 100-450 kt:** S median 259, p99 469; the roll would fail in 0.07 % of
  states, mean P(fail) 0.02 %. Only states with |roll| > 30 deg (69 of them) are at risk: 11.6 % of those have P > 0. These are autopilot flights, mostly level,
  so they say nothing about missile evasion. (About 5,000 snapshots in one run are a frozen junk state, 1285 kt, and were excluded.)
- **Still open:** the live effect of the roll (it should match the formula; try ejecting from a save state in a steep bank and in level flight); whether the damage state
  also feeds S (ds:855A is the only non-attitude term found); where codes 3 and 4 are set; what "Captured" does to the next mission beyond no medal and no promotion.

## Safe bail-out: level first, then Shift+F10 (2026-10-28)

Built on the death-roll formula above, so it is only as good as that disassembly read. `src/f19/pilot/eject.py` (`LevelOut`), called by
`Runner._level_then_eject` for every permitted `Action.EJECT` (so the director's bail-outs too; on by default in `fly` since 2026-10-29, `--no-eject` turns it off):
steer pitch and roll to 0 with key taps (keypad 5 stops the rates when the coast would overshoot), **arm** when both are within 5 deg, both
rates are under 1.5 deg/s and S <= 470, and send the key on the **next** read only if the plane is still inside those limits. Fallbacks, because
not ejecting is worse than a risky eject: 25 s without level, altitude under 40 ft, or a failed read all eject at once.
- Established: offline only (a toy plane with key gains different from the controller's guesses, `tests/unit/pilot/test_eject_level.py`).
- **Untested live:** the roll and pitch key gains (`ROLL_GAIN`, `PITCH_GAIN` are guesses), the roll-key sign (taken from the bank-limit code in
  `loops.py`), and whether centring with keypad 5 behaves as the toy plane assumes. Try it from a save state in a steep bank first.
- Not handled: S has a speed term, so above ~450 kt even a level plane can fail the roll; the sequence does not slow the plane down.

## Hill crash (END code 2) = a solid hit while airborne; photo-run pull-up (2026-10-29)

- END's two crash texts: code 1 "Flying into the ground…", code 2 "Flying into that hill…". There is no "mountain" string in END.
- Code 2 (EGAME 0x43A9-0x43E9): the renderer's collision flag ds:6EAC (= ds:33C6 after the terrain/object draw call at 0x3A15) is set,
  ds:94E0 bit 0x80 is clear, landing mode (gameData+0x40) is not No Crashes, and altitude ds:46E2 is non-zero. With altitude 0 the same
  flag only bumps altitude by +500 (0x43F1). Code 1 is the altitude-reaches-ground-level path instead.
- Established by disassembly (`research/analysis/egame_re/egame_all.asm` 0x43a9) and two live flights (Veteran-or-below, `level` 0 in
  the mission file; difficulty not otherwise recorded): Kolobrzeg (nav 1793275501) and Katowice (nav 1793240391) both ended in
  "hill" 0.11-0.12 km from the Tank Farm's coordinates at 247-251 ft, 196-204 kt, mid photo run. No decoded terrain solid (`World.solids_at`)
  exists at either point, so the solid was most likely the Tank Farm's own model. Liberec (nav 1793236937) passed 0.08 km from its
  Tank Farm at 314 ft and survived, so the model tops out between ~251 and ~314 ft (two data points; not measured).
- Cause in our code: the photo run held a straight line at 250 ft and only ended on the event-log photo record, which was never seen
  (below), and nothing climbed over the target. `Attack` now ends the run inside `CLEAR_KM` (1.0 km) whether or not the photo is
  confirmed, climbs `CLEAR_UP_FT` (150 ft) over the run-in altitude and keeps control until `CLEAR_PAST_KM` past the target.
  Untested live: whether +150 ft clears every Tank Farm, and the radar cost of the climb.
- **First live run (2026-10-29, Veteran-agnostic: the roll does not depend on difficulty as read), dry: Shift+F10 replaced by a log line.** From a -34 deg bank at 289 kt and 1900 ft the
  loop reached roll -4 / pitch +4.5 (S 537 -> 362) in ~10 s and armed; keyboard sensitivity 1 -> 2 worked (Ins); learned key gains at sensitivity 2: roll ~32,
  pitch ~17 deg/s per s held (baseline now 25 / 15). The first version levelled roll first and let the nose drop 510 ft; the vertical-speed term
  (nose-up target 0.15 deg per ft/s of sink + a creeping bias, no arming while sinking > 15 ft/s, eject at once under 2 s from the ground) and tapping both
  axes in one pass came after. The roll swung +-10 deg while converging (taps too long for the gain); re-test pending. **The Shift+F10 itself is still untried.**
