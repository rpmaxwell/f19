# Flight logging plan

Goal: one complete record per flight, so a crash, hit, ID or fuel surprise can be reconstructed afterwards without
guessing. Status 2026-10-18: addresses decoded (FINDINGS.md ["Flight-log variables"](../game/memory-map.md#flight-log-variables-fuel-damage-clock-ids-threats--egame-2026-10-18), ["Radar sites: pulses, duty cycle,](../game/radar.md#radar-sites-pulses-duty-cycle-detection--egame-2026-10-18)
detection", ["Fuel burn and cruise speed"](../game/fuel.md#fuel-burn-and-cruise-speed--egame-2026-10-18)); readers `read_status` / `read_alerts` in scripts/f19_state.py; logger not
built yet.

## Where it runs

Inside nav.py. The automation port serves one client, so a separate logger can't run during a flight. All reads go
into one batched `_peek_many` per cycle. Damage, IDs and site alerts are decoded but not yet seen changing in memory:
the first logged flight confirms them.

## Output: `logs/flight/<start>/`

| file | content |
|---|---|
| `meta.json` | plan / route path, theater, opp, war (ds:4EF2), night (ds:4EF4), loadout, memory offset, nav version, live SA-14 cells (ds:85FC bit 0x10 at takeoff), radar site table (record, name, type, x, y) |
| `telemetry.csv` | one row per nav cycle (~5-8 Hz), fields below |
| `sites.csv` | one row per change of any radar site's alert, active flag or pulse (site record, clock, alert, flags) |
| `events.jsonl` | one line per discrete change, below |
| `flight_report.py` (tool) | timeline of the last N s before a crash / around any event; plots alt, terrain, EMV, alerts, damage, fuel vs game time |

## telemetry.csv

Time: wall `t`, **game clock** ds:6630 (2 game s/tick), frame ds:5520, frames-per-tick ds:4EFC, time accel ds:553A.

1. **Flight:** alt, airspeed kt (ds:855A) and 27kt (ds:7F94), heading, pitch, roll, vertical speed, AoA ds:6616,
   stall ds:9B82, load ds:662A, terrain height under the plane (terrain_3d) and clearance.
2. **Position:** x, y (waypoint units), 32 km cell (x>>11, y>>11), cell side (territory), cell terrain bits 0x0C
   (mountain / rough / flat / sea), SA-14 cell flag.
3. **Radar exposure:**
   - EMV ds:551C plus its predicted components (altitude, bank, speed, bay, ECM; radar_game.emv): a mismatch means a
     new term.
   - threat colour ds:552C, last site with a return ds:94E4, signal ds:948C.
   - per site, every cycle in `sites.csv` (changes only): alert (record +0x0A), active flag (+8 bit 0x02), pulse
     countdown (+0x0C), flags (detected 0x04, radar-ID'd 0x1000, destroyed 0x80).
   - per plane (sim objects ds:8854): alert +0x20, flags +0x16 (visual-ID'd 0x800, alerted 0x4000), x, y, type.
   - network-alert pipeline ds:9502/95CE/963E; last reported position ds:8B24/8B2E/8B34.
   - enemy missiles homing near us ds:6336; distance to nearest airfield record ds:663E.
   - predicted return and margin (s + EMV - 100) for the nearest 4 sites (radar_game.site_return) to check the model live.
4. **Fuel:** fuel ds:4ED0, throttle setting ds:46F2, spool ds:85F8, expected burn step (2 + thr²//1000 + leak),
   predicted cruise kt and lb/km (fuel_model.cruise), bingo (distance home / 16 > fuel).
5. **Score:** live estimate from the event list + territory samples (score routine already decoded from END).
6. **IDs:** V ds:966E, R ds:9672, alerted counter ds:9674.
7. **Nav:** leg, phase, attack state, want heading, cross-track, distance to turn, keys sent this cycle.
8. **Damage:** bits ds:4ECE (named: warning, engine, controls, autopilot, bay, leak, countermeasures), count ds:95BE.
9. **Stores:** loadout 4 x (weapon, count), selected station, gun rounds, flares/chaff/decoys ds:4ED4/4ED6/4ED8.
10. **Config:** flags word ds:684C (gear, flaps, bay, brakes, ECM 0x10, IRJ 0x20), trackcam mode, locked target, HUD lock,
    game autopilot ds:4EEE, keyboard sensitivity.
11. **Session health:** cycle time, port errors/retries, offset re-finds.

## events.jsonl

- every new flight-event record (ds:8E46, count ds:632E), decoded: 2/12 kill, 3 air kill, 4 launch, **5 hit (weapon)**,
  **6 visual ID (aircraft)**, **7 radar ID (site)**, 8 takeoff/end, 9 sample, 10 photo, 11 objective.
- site: first return ("detected"), crossed 0x7F (network alert), 0xC0 (can launch), 0xFA (ID), back to 0.
- SAM launch: a new enemy projectile slot (ds:53FC, 0x18 each) with its spec, the site that fired, range.
- SA-14 spawn (enemy slot with spec 0x21) and the cell.
- damage bits / count change; fuel jump (Extra Fuel transfer +1900); fuel burn mismatch vs formula.
- flags change (gear, flaps, bay, brakes, ECM, IRJ); phase change; nav start/stop; save-state load.
- crash / end: crash code commData+0x28, final event list, final damage.

## Checks the log enables

- burn formula every step (any mismatch is news); speed vs fuel_model; EMV vs radar_game.
- each site's alert against the predicted pulse-by-pulse alert (radar_game.alert_step) → confirms the radar model live.
- every hit attributed: which site or SA-14 cell, how many pulses it saw us, what our EMV and aspect were.
