# Gun attacks on SAM radars: plan (2026-10-18)

Goal: Conventional War sorties where nav strafes SAM radars with the 20 mm gun: kill the radar, keep the
missiles for targets the gun can't kill, and fly home unseen.

## What the game does (decoded from EGAME, see FINDINGS.md "Guns")

- **Trigger:** Backspace (key 0x0E08) sets the "button 0" flag ds:5C6E for one frame, and joystick button 0 does
  the same. Enter is button 1 (weapons). A bullet spawns only on **odd frames** while the flag is set, so a 0.2 s
  tap may fire nothing. Hold Backspace and let typematic repeat keep the flag set.
- **No ballistics.** A bullet flies in a straight line along the **nose** (pitch ds:46DC and heading ds:46DA; not
  the flight path, and the plane's velocity isn't added). It starts at the plane's alt - 2 ft and moves
  112/fs units a frame. It has **no gravity**. Once it's fired, nothing the plane does changes where it lands.
- **Impact** happens when the bullet's altitude goes below 0, which is the flat world. A level or nose-up shot never
  hits anything on the ground, so you have to point the nose down.
- **Reach:** there are 2*fs slots (3-16), reused round-robin every other frame, so a bullet lasts about 4*fs frames,
  or **~448 units = 7 km** of slant range. That doesn't depend on the frame scale.
- **Kill test** (0xB3B4): the impact snaps to the nearest 3D object (as bombs do). That object is destroyed if
  octagonal dist(impact, object) < v*6/(opp+2) waypoint units (64/km). For the gun, v = 4 against SAM Radar and
  Mobile SAM, so:

  | opponents | kill if dist <= | radius |
  |---|---|---|
  | Green | 11 units | ~170 m |
  | Regular | 7 | ~110 m |
  | Veteran | 5 | ~80 m |
  | Elite | 3 | ~47 m |

  If a launcher or building sits closer to the impact than the radar does, the bullet "hits" that object instead.
- **Ammo:** 650 rounds, and each bullet costs 40/fs rounds (10 at fs 4, 5 at fs 8), so 65-130 bullets a sortie.
- **Score (Conventional):** SAM Radar **3** base points (END table 0x4258, indexed by category: confirmed; full
  list in notes/score_estimate_for_planner.md). At CE, Veteran and Realistic that's about x4, so roughly 12 points a radar. Most of a radar kill's value is the detection it removes, not the points.

## The aiming problem reduces to CCIP

The bullet path is fixed when it's fired, so the impact point can be computed every cycle from values nav already reads:

```
D   = (alt_ft - 2) / 32 / tan(-pitch)            # waypoint units ahead (only valid for pitch < 0)
CCIP = (X + D*sin(hdg), Y + D*cos(hdg))          # X/Y in waypoint units, the same frame as the target record
fire while octagonal(CCIP - target) < kill_radius - margin
```

There's no lead and no time of flight to account for. Lateral error is easy: 1 deg of heading at 1.5 km is ~1.7
units. Range error is the hard part. It's very sensitive to pitch:

| entry alt | -6 deg | -8 deg | -10 deg | -12 deg |
|---|---|---|---|---|
| 300 ft | 1.38 km, 15 u/deg | 1.04 km, 8 u/deg | 0.83 km, 5 u/deg | 0.69 km, 4 u/deg |
| 450 ft | 2.08 km, 22 u/deg | 1.56 km, 13 u/deg | 1.24 km, 8 u/deg | 1.03 km, 6 u/deg |
| 600 ft | 2.78 km, 30 u/deg | 2.08 km, 17 u/deg | 1.66 km, 11 u/deg | 1.37 km, 7.5 u/deg |

With the Veteran 5-unit radius, the pitch would have to be held within about +-0.5 deg. nav's tapped pitch control
can't promise that, so **don't aim the pitch; walk the impact point through the target instead:**

- Hold heading on the radar. Push the nose down slowly from level. The CCIP comes in from the horizon, about S units
  per degree of push.
- Hold the trigger only while the CCIP is inside the kill radius plus the read lag. Bullets go out about every two frames, so the
  burst spreads along the walk and some land inside the radius even when the pitch is a little off.
- With the pitch held constant the CCIP still creeps back toward the plane, because the nose sits above the flight path
  by the AoA and the CCIP converges on the flight-path impact point. Pushing and creeping both move it in the same direction,
  so a pass can't overshoot and come back. If the CCIP passes the target, the pass is over.
- Once the CCIP is past the target by more than the radius, or the altitude floor triggers, pull out.

## Profile (first version)

1. **Set-up, 6 km out:** cruise altitude (300-450 ft, the current cruise), cruise speed (user rule: no speed change),
   wings level on the radar's bearing. The radar can see us, so don't linger.
2. **Climb to entry altitude, ~4 km out:** 500-600 ft. A steeper dive gives smaller sensitivity and more time in the
   window. At 600 ft and -10 deg the window opens at ~1.7 km.
3. **Push, ~2.5 km out:** nose-down taps until the CCIP is ~30 units beyond the target, then single taps to walk it in.
   Fire inside the window. Expect about 1-2 s of trigger.
4. **Pull-out:** full nose-up hold as soon as the CCIP passes the target, or when `alt + vs * T_pull < FLOOR`, whichever
   comes first. FLOOR = 150 ft, and T_pull is measured in Phase 1. Then go back to cruise altitude, turn away and resume the route.
5. **Check the result:** the target record's destroyed bit (target table 0x43C1A + 16*i + 8, bit 0x80) and the
   " destroyed by gunfire" message. If the radar is still alive and there's ammo left, extend 6 km and set up another pass.

## Phases

**0. Desk (no flying).** `analysis/gun_geometry.py`: CCIP and kill-radius functions, plus a simulator for the push-walk
(pitch steps per tap, AoA from the takeoff rules, sink 3.375*kt*sin(gamma) per step). Output: entry altitude, push
range, expected bullets in the window, and altitude lost for a given cruise speed and opponent level.

**1. Probe at a safe spot (friendly, flat, no targets).** These are new reads, phys = 0x3BB70 + ds:
- rounds 0x40A5C (ds:4EEC), frame scale 0x40A6C (ds:4EFC), slot count 0x440CE (ds:855E)
- bullet slots 0x456F8 (ds:9B88, 12 B each: X, Y, alt, vX, vY, vAlt)
- last impact 0x4505A / 0x45066 / 0x4506C (ds:94EA / 94F6 / 94FC); player X/Y in units 0x4505E / 0x4506E
- trigger flag 0x417DE (ds:5C6E)

Checks, from 1000 ft:
- Hold Backspace and confirm rounds drop, and how many bullets a second.
- Make 3-4 dives at -6 to -12 deg with short bursts and compare the logged impacts with the CCIP. Target: within 2 units.
- Measure the pitch change per nose-down tap, and the nose-up hold rate.
- Make several pull-outs from -10 deg at cruise speed and log the altitude lost and the time. This sets T_pull and the floor.

**2. Rehearsal against a virtual target.** Use any ground point on a friendly cell and fly the full push-walk-fire-pull
pass. The bullets show whether the walk crosses the point inside the radius. Repeat until 3 passes in a row cross it.

**3. Live: a lone SAM radar.** Start on **Regular** (7 units), then move to Veteran. Pick a radar with no other SAM site
covering the approach (radar_rings/radar_los). Approach from the radar's blind side if the pulse timing allows it.
Log every pass.

**4. Into nav --auto.** Add a `GunAttack` phase in scripts/phases.py, triggered by a new planner point kind `strafe`
(run-in heading, entry and push points, and a 6 km extension leg for a second pass). The planner picks the radars:
the ones whose rings cover the route, killed in order, gun first and missiles for the rest.

## Open questions, checked in order

1. Backspace fires the gun. This is decoded but not seen in flight, and the right hold length is unknown.
2. Whether a destroyed SAM radar stops emitting, meaning its rings and pulses stop. The whole idea depends on this.
3. Whether the snap to the nearest object lands on the radar record. At a multi-object SAM site, a near miss might kill a launcher
   instead (scratch slot). Log which record dies.
4. Whether ground fire (AAA or SA-14) near SAM sites punishes a pass at 150-600 ft. The 2026-10-18 debrief says SAMs that
   see you without an ID sometimes fire SA-14s.

## Status 2026-10-19: autopilot written (phase 4), not yet flown

The continuous-tan CCIP table above is superseded: velocities are whole units a frame (FINDINGS.md "Guns", flight-tested),
so pitch only matters by band. Hand-flown: Wittenberg and Cottbus radars killed (bullets 1-1.5 u off), Wittstock survived
bullets 6.5 u past it (snapped to another site object). Destroyed sites never pulse again (scheduler 0x503a).

`scripts/phases.py` `GunAttack`, run by `nav.py --strafe nearest` (or record numbers; with `--auto` it goes back to the
route afterwards, otherwise it hands back control):
- run-in at 1500 ft straight at the site; push where a -12 deg shot would land 25 u past it, if within 3 deg of the bearing
- dive: pick the descent band whose impact lands on/just past the target, fly to the band CENTRE (pitch taps, +-0.8 deg)
- fire (hold `bs`) only while the CCIP miss is < half the kill radius; no fire while the nose is moving
- cease + pull (nose-up held to +5 deg) at max(450, 300 + 1.2 x sink) ft, leading the loop lag
- check the destroyed bit; else extend 8 km, turn back (1.6 deg/s), up to 3 passes
Toy-sim (scratchpad) kills on 090/135/180/270 run-ins, bottoms ~400-425 ft. UNTESTED live: pitch change per held key at
sensitivity 1, whether a held `bs` from the automation port auto-repeats (fallback: re-press every cycle once rounds stall).
gun_probe.py and nav.py can't run together (one automation client).
