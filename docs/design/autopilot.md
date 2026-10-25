# Full-mission autopilot

Design and status of the takeoff/attack/landing phases (nav.py --auto). Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Full-mission autopilot: takeoff, attack, landing phases (2026-10-17, NOT flight-tested)

`nav.py --route <plan>/route_X.json --auto` flies the whole mission: Takeoff -> route -> Attack primary -> route ->
Attack secondary -> route -> Landing. The controllers live in `scripts/phases.py`; nav.py's heading and altitude holds
keep running, and each phase overrides them per cycle (heading, altitude / glide cap, VS limit, roll/alt hold off).
Phases are triggered by the planner's point kinds: `runin` (within 10 km) / `launch` / `photo` / `overfly` with a role
start an attack; the leg to `Final` starts the landing.
- **Throttle without an emulator rebuild:** the automation key table has no `-`/`=`. `Dosbox.scan_tap` sends the set-1
  scancode via the 8042 "write output buffer" command (`OUT 64 D2`, `OUT 60 <code>`). Each D2 clears the controller
  buffer and one drain writes all queued OUTs, so make and break go out separately. Shift+= / Shift+- = max / no power.
  Speed hold (`phases.Throttle`) nudges -/= every 3 s from airspeed (the throttle position byte is unknown).
- **New reads** (`f19_state.read_flight`, old phys = 0x3BB70 + ds offset): pitch 0x4024C, roll 0x4024E, flags word
  0x423BC (0x01 gear up, 0x02 flaps, 0x04 bay, 0x08 brakes), locked target 0x41096, opponent 0x40A60, runway box 0x410A4,
  projectile slots 0x40F6C, target table 0x43C1A. From the disassembly only; `scripts/probe_controls.py` checks them.
- **Takeoff:** brakes off, flaps down, max power, rotate at 110 kt holding pitch 8-13 deg, gear up > 80 ft, flaps up
  > 300 ft and 180 kt, then speed hold at the profile's cruise_kt and normal altitude hold.
- **Attack:** Maverick = 280 kt, B until locked target == the mission's target record, steer at the target, at the planner's
  launch distance: bay open, nose up to +18, fire, bay closed, nose down to level. Photo = bay open 1.5 km before the photo
  point, fire the camera while the game's photo score (r + 11.38 x angle errors) is under 90 % of (8 - 2 opp) x 64, max 4
  frames. Overfly (bombs, supply) is cue-only.
- **Landing:** gear down 12 km before the aim point (0.5 km short of the base record), flaps + 140 kt at 7 km, glide cap
  60 ft/km, flare to 2.5 ft/s below 40 ft, localizer 1.5 deg/unit (box is only +-5 units = +-78 m wide), idle + brakes at
  touchdown. Goes around and hands back if still up past the base record or > 8 units off the centreline below 80 ft.
- **Probed live on the runway 2026-10-17** (Tabor photo / Mittenwalde destroy, Veteran; `probe_controls.py`):
  pitch/roll/flags/opponent (2)/runway box (1 on the runway) read sanely. Toggle bits 0x04 bay, 0x02 flaps, 0x08 brakes
  confirmed (gear 0x01 not tested: air only). **Discrete keys must be tapped 0.2 s**: a 0.5 s press hits the typematic
  repeat (500 ms) and the game sees two presses, so toggles flipped back. D2 scancodes work: **throttle % = u8 0x40262**
  (= steps 0, 5, 10, 20, 30; Shift+= 100; Shift+- 0; 0x44168 lags it, thrust?). **Selected station = 0x40A5A** (0-3, Space
  cycles), **loadout = 0x40A4A**, 4 x (u16 weapon, u16 count): here (16 Camera 1) (17 Extra Fuel 1)
  (5 Maverick 2) (0 AIM-9M 4), the loadout the user flew (first read as 0x40A46, one entry early: corrected by the
  user). **Nose-gun rounds u16 0x40A5C** (650 at start). The catalog at 0x40A6E (20 x 26 B, ordnance.py) has a per-bay
  capacity in its qty field, not the loadout. **Briefing blocks 0x44304** in flight = (1, 12) (2, 2): record 12 in the target table is Tabor at
  (17472, 27328), so target-table index = briefing record index. Attack now selects the weapon itself (checked live:
  Camera and Maverick reached). **B did not lock anything on the runway** (ds:5526 stayed 0xFFFF): the targets were
  100-200 km away; B presumably cycles only targets in range/view. Test near a target.
- **Still open:** what B selects / when ds:5526 locks (nothing on the runway); the pitch/roll sign; whether the heading hold can keep +-78 m on final; no "photographed" success flag yet.
- **First --auto flight, 2026-10-17 (Tabor/Mittenwalde, plan logs/plan/ce_tabor_auto, nav log 1792271555.csv): crashed.**
  The takeoff worked unaided: brakes off, flaps, max power (D2 scancodes), rotate at 110 kt, airborne at ~150 kt after
  ~0.7 km, gear and flaps up, 500 ft about 18 s after brake release. Then it handed over at +8-10 deg pitch and full power,
  overshot to 1460 ft, and the altitude hold **pushed the nose down all the way into the ground** (-90 ft/s at the end).
  Cause: the extra reads made the loop ~1 s a cycle, and the vertical-speed estimate needed > 3 samples in 1 s, otherwise
  0. With vs read as 0, the hold kept asking for a descent. Keyboard sensitivity was 3 at the start; nav set it to 1
  before the roll, and the hold only presses at 1, so it wasn't the cause. Fixes: pipelined reads (`_peek_many`);
  vs over 1.5 s from >= 2 samples, no press without an estimate; a SINK! nose-up guard below -40 ft/s;
  the takeoff levels to +1..4 deg above 220 ft and keeps full power until handover; phases wait for sensitivity 1;
  the nav csv now logs vs and cycle time.
- **Second --auto flight, 2026-10-17 (nav log 1792271817.csv): crashed, stuck roll key.** Reads now ~0.12 s a cycle and vs
  read correctly (10-21 ft/s in the climb). Takeoff to 530 ft fine. Then a 1.1 s left-roll press was released in the same
  cycle as a throttle `D2` scancode: **D2 clears the 8042's pending bytes, so the left-key break code was lost** and the
  game kept left held. The turn rate grew -0.4 -> -11 deg/s through two 2.5 s right presses: a spiral into the ground.
  Fix in `Dosbox.scan_tap`: wait 0.15 s for queued keys to drain, then re-send UP for every key the client isn't holding.
  Also: nav's Ins tap 0.6 -> 0.2 s (0.6 s auto-repeats, two sensitivity steps); takeoff sets cruise throttle at flaps
  up (full power had reached 305 kt). The clean fix is adding `minus`/`equals` to DOSBox-X's automation key table.
- **A stale nav wrecked the next mission (2026-10-17).** The second flight's nav was never stopped. When the user reloaded
  the mission it resumed with the old state (phase route, nose-up presses, ~48 kt down the runway on heading 10) until it
  was killed, and the mission ended. nav now exits after 10 s out of flight. Also added: a roll watchdog that re-sends
  left/right releases when |turn rate| > 2.5 deg/s while nav holds no roll key. Always stop a test's nav before reloading.
- **Third --auto flight, 2026-10-17 (nav log 1792272329.csv): first clean automatic takeoff into cruise.** Brake release
  to 300 ft in ~13 s, levelled at 457 ft for a 450 hold, handed to the route at ~25 s, and the 0.8 deg/s turn onto the route
  tracked. Throttle presses (`thr-`/`thr+`) caused no stuck keys: a left roll released in the same cycle as a throttle
  press still landed, and the watchdog never fired. The speed hold swings 167-202 kt around 190 (5-10 % throttle
  steps): acceptable, could be smoother. Cycle 0.10-0.13 s without a key press (a press blocks ~0.6 s).
- **Third flight, Tabor photo run (2026-10-17/18):** the attack phase ran 1792273486-547. The camera fired 4 frames at
  3.11 / 2.79 / 2.48 / 2.16 km (score 229 / 209 / 191 / 173 against budget 256: all inside the window) and the bay was **left
  open**; the user closed it. User rules: **close the bay the moment "Target photographed." appears**; ground targets need
  the **tracking camera in air-ground mode with the target on screen** (the user set it; that's why B locks nothing on the
  runway). Changed: one frame at score < 0.8 budget, then the bay is closed and re-tapped until the flag reads shut
  (also checked before an attack ends); a banner asks for the trackcam until its key is mapped. nav exited when the user
  paused the emulator (socket timeout) near the secondary.
  **Open:** the "Target photographed" signal (frame state ds:5B18 = old 0x41688, or the flight-event record END.EXE scores)
  so the bay can shut on success; the trackcam air-ground key and mode byte; why the 4th-frame bay close didn't take.
