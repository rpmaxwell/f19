# Note for the flight-planning logic: low-level targeting (stand-off distances and release technique)

From the session that decoded the weapon code (2026-10-17). Source of truth: FINDINGS.md, sections
"Ordnance vs target strength", "Weapon release, altitude and blast damage" and "Photo, lock and guidance".
Numbers come from EGAME.EXE's own rules (simulated in `analysis/release_geometry.py`), **not yet flight-tested**.

**Assumption: the plane cruises below 200 ft** (radar), and every attack is planned to stay there. Bombs are the
exception (section 4). Firing and photographing need the **bay open**, which raises the radar signature, so plan to
open the bay at the last moment and close it right after the frame or the launch.

Units: waypoint units (64 per km; game distance = max(|dx|,|dy|) + min/2), altitude ft, angles degrees,
pitch + = nose up. `opp` = opponent level: 0 Green, 1 Regular, 2 Veteran, 3 Elite.

## Summary

| Mission (briefing type) | Low-level technique | Where the action happens | After |
|---|---|---|---|
| 1 Photograph | level, nose on target; Elite: small nose-up flick | Green ≤6 km, Regular ≤4 km, Veteran ≤2.5 km, Elite 0.2-1 km level or ≤~1.8 km with nose +7° | turn right after the frame |
| 2 Destroy, Maverick | **pop the nose up 15-20° and fire at once** | 16-20 km out, target within 22° of the nose | turn at once (fire-and-forget) |
| 2 Destroy, bombs | not low (see 4) | overfly | turn after release |
| 3 Supply drop | overfly | canister within 4 km of the target | turn after release |
| 5-8 Intercept | point at the aircraft | AIM-9M ≤17 km, AIM-120 ≤32 km | free once it hits |

## 1. Photograph (type 1): low altitude is an advantage

Needs: camera selected, bay open, the photo target as the selected ground target, fire pressed. The frame
passes if

```
range + 11.38 x (camera error + heading error) < (8 - 2*opp) x 64        (units; 11.38 units = 0.18 km per degree)
camera error = |elevation - pitch + 9.74|,  elevation = -atan2(alt/32, range)   (the camera looks 9.74 deg below the nose)
heading error = |bearing to target - heading|
```

At low altitude the target sits almost on the horizon, so in **level flight** the camera error is ~9.74° minus the
small depression. That still fits in every budget, so level flight works. Windows (km, heading error 0° / 2°):

| opponents | 100 ft | 150 ft | 200 ft |
|---|---|---|---|
| Green (8 km budget) | 0.05-6.3 / 6.0 | 0.06-6.4 / 6.0 | 0.08-6.4 / 6.1 |
| Regular (6 km) | 0.06-4.4 / 4.0 | 0.09-4.4 / 4.1 | 0.11-4.5 / 4.1 |
| Veteran (4 km) | 0.09-2.5 / 2.1 | 0.12-2.6 / 2.2 | 0.17-2.6 / 2.3 |
| Elite (2 km) | 0.14-0.84 / 0.66 | 0.20-1.00 / 0.81 | 0.28-1.12 / 0.94 |

- **Heading is the expensive part:** each degree off the target's bearing costs 0.18 km. Fly the last few km
  straight at the target.
- **Extending the range (Elite):** raising the nose cancels the camera error. The pitch that zeroes it is
  `9.74 - depression`: at 150 ft that's **+5.6° at 1 km, +6.9° at 1.5 km, +7.6° at 2 km**. With that flick, only
  range + heading error count, so Elite reaches ~1.8 km with ≤1° heading error. Pitch only matters for the instant
  of the frame: flick the nose up, press fire, push back down. Altitude barely changes.
- Too close (under ~0.1-0.3 km) fails because the target drops too far below the camera.
- **Planner rule:** a straight leg pointed at the target ending at the photo point: Green-Veteran ~2 km out (inside every
  window, well clear of the minimum), Elite ~0.6-0.8 km level (or ~1.5 km with the flick). Bay open over the last ~5 s.
  Turn away right after the frame ("Target photographed.").

## 2. Destroy with a Maverick (type 2): pop-up launch

> **Superseded by flight test, 2026-10-18.** Fire **level at cruise (450 ft)**, no pop-up (the user's technique; the
> pop-up climbed to 2300 ft and didn't fire), at **~14 km**: the missile's life is sized to fly the in-range distance
> (red hexagon, 16 km) at top speed, so it reaches ~14.5-15.5 km. 3/3 shots from 16.9-18.9 km were "Ineffective".
> Planner launch point 14 km, window 6-14.5 km. The sink numbers below didn't apply at 450 ft.

What the game does at launch: the missile appears **20 ft below the plane, pitched 22.5° below the plane's nose**.
If the target's bearing is within **±22.5° of the missile's heading**, the seeker takes it at once and the missile
pulls up 2.8° per update (agility 2), then lofts (it aims ~7° above the target) and needs nothing more from the plane.
It sinks until its pitch passes zero. Height lost after launch, ft (fs=4 / adaptive frame scale fs=8):

| airspeed | level | nose +10° | +12° | +15° | +18° | +20° |
|---|---|---|---|---|---|---|
| 200 kt | 300 / 446 | 84 / 113 | 60 / 80 | 37 / 45 | 24 / 27 | 20 / 21 |
| 300 kt | 387 / 540 | 108 / 140 | 77 / 99 | 44 / 55 | 26 / 30 | 20 / 22 |
| 400 kt | 561 / 726 | 157 / 195 | 109 / 137 | 59 / 73 | 29 / 35 | 20 / 23 |
| 500 kt | 648 / 817 | 181 / 223 | 126 / 156 | 66 / 82 | 31 / 38 | 20 / 24 |

- **Level flight below 200 ft never works** (300-650 ft of sink): that's the "can't fire Mavericks low" effect.
- **Technique:** at the launch point, open the bay, pull the nose up to **15-20°**, fire the moment the pitch gets
  there, close the bay and push back down. At 150 ft with nose +15° the missile clears the ground with ~70-110 ft to
  spare. **+18-20° is the safe choice** (20-38 ft of sink at any speed). The quick pull only lifts the plane a little,
  and it's only exposed for a moment.
- **Slower helps:** sink grows with launch speed (the missile starts at airspeed/76 speed units). Throttling back to
  ~250-300 kt before the pop shrinks the margin needed.
- **Aim horizontally:** the target must be within 22° left/right of the nose at launch. Outside that the missile
  never pulls up: it dives at 22.5° and hits the ground ~4x your altitude ahead.
- **Range:** launch 16-20 km out (HUD lock ≤ 28 km, in-range cue ≤ 16 km; the user fires at ~20 km). Firing doesn't
  need the HUD lock, only the selected target, so pitching up past the HUD lock box doesn't matter (assumed: not
  yet checked that the selection survives the target leaving the HUD).
- Kill chance per hit = min(v, 4)/4 from `analysis/ordnance.py` (v ≥ 4: one shot). "Ineffective" = a **direct hit** that
  failed that roll (EGAME 0x7371-0x7395, checked 2026-10-18); it isn't a miss. Bombs that miss print "<bomb> misses"
  (0x70a5); missiles that hit terrain print "<missile> ground impact" (0x7265).
- **Planner rule:** cruise ≤ 200 ft on a straight leg at the target's bearing; mark a "pop-up launch" point 16-20 km
  out (bay open, nose +18°, fire, bay closed, back down), then turn away at once. No overfly.
- Other missiles: HARM, Penguin and Harpoon share the 22.5° nose-down spawn and agility 2, so the same pop-up
  applies. HARM: v = 0 against SAM Radar in the matrix ("Ineffective"); its radar effect isn't known yet.

## 3. Supply drop (type 3)

- Success ("Equipment Delivered") if the canister lands within **4 km** of the target, before the deadline.
- The cue "Drop supplies now" appears when the target is on screen and less than 2 km ahead.
- The canister falls like a bomb. No blast check is known for it. **Planner rule:** overfly low, drop at the cue.

## 4. Bombs (the exception)

- Kill if the impact is within v/(opp+2) km of the target (matrix value v).
- **Blast damage to yourself:** if horizontal distance (units) + alt_ft/32 < opp·32 at impact. Directly overhead
  that's below 1024 / 2048 / 3072 ft on Regular / Veteran / Elite.
  - **Retarded bombs** (Snakeye, Durandal, Rockeye, IN Cluster, Minelets) fall straight down slowly while you fly on.
    The check is made when the bomb lands, so what counts is your distance by then. Low release is possible if you're
    ≥ ~0.5 km (Regular) to ~1.5 km (Elite) past the impact when it lands (unmeasured: fall time not decoded).
  - **Free-fall and laser-guided bombs** land near the plane: release above the blast floor. Laser-guided bombs
    lock at range < alt_ft/410 km (1000 ft: 2.4 km).
- **Planner rule:** overfly on a straight level leg. Free-fall/LGB: climb above the floor for the opponent level.
  Retarded: low is possible but unverified.

## 5. Intercept (types 5-8)

- AIM lock: aircraft within ~10° of the nose and closer than R km (AIM-9M 17, AIM-120 32). Missiles home on their own.

## Caveats / open items (say so in the plan output)

1. None of this is flight-tested. Test first: the Maverick pop-up (nose +15° vs +20° at 150 ft) and the Elite photo window.
2. The frame scale fs (ds:4EFC) is adaptive; the Maverick table gives fs=4 and fs=8. Use the larger number for margin.
3. Photo geometry uses the game's own angle (alt/32 against map units), not true geometry.
4. Whether the selected ground target stays selected while the nose is pitched up off the HUD is not checked.
5. The radar signature cost of an open bay hasn't been measured (EMV byte at phys 0x4108C can measure it).
