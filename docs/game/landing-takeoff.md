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
