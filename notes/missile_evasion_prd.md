# PRD: MissileWatch — in-flight missile evasion that keeps the stealth profile (Elite)

Owner: nav agent. Requested 2026-10-23. Code: `scripts/phases.py` (new `MissileWatch`), `scripts/nav.py`,
`scripts/f19_state.py`, new `analysis/missile_game.py`.

## Why

At Veteran and below, stealth alone keeps missiles off us: 57 SA-14s in 5 Veteran flights, 0 hits. At **Elite** it
doesn't: the two Elite flights saw 6 enemy missiles and took **3 hits**, and each hit rolls **4 damage bits**. One hit
ended Liberec (bay jammed open + INS); three ended Stupsk. Nav has no missile logic: FighterWatch tries to stop the
shot, and once a missile is in the air nothing reacts.

The tension: evasion can blow stealth (hard banks cost EMV, a seen pulse costs +64 alert at Elite, and a network alert
scrambles more fighters). That in turn brings more launches that are harder to beat. Getting hit is effectively game
over, so **avoiding the hit is a constraint, never a trade-off**. Within that constraint, MissileWatch picks the response
that spends the least stealth.

**Scope:** acting at Elite (opp 3) by default. At every level it runs in *observe* mode (predict and log, no
control), so Veteran SA-14 cells give cheap validation data.

## Source of truth

- **notes/enemy_ordnance_and_countermeasures.md**: the decoded missile model (launch, seeker cone, IR tail rule,
  launcher support beyond 8 km, jamming/burn-through, flare/chaff/decoy rules, hit test, turn limits), the ordnance
  table, and what has been checked against memlogs. Read it first; this PRD doesn't repeat the derivations.
- FINDINGS.md "Radar sites: pulses, duty cycle, detection"; `analysis/radar_game.py` (emv, site_return, alert_step,
  pulse_period_ticks): use it, don't re-derive.
- notes/aircraft_profiles.md and `phases.FighterWatch` (fighter radars, the per-heading scoring pattern to copy).
- Memlog checks: `analysis/missile_scan.py`, `analysis/missile_track.py <run> [-v]`, `analysis/missile_rules_check.py`.
- Elite memlogs: **1792628244** (Liberec: 1 AA-9 hit), **1792715432** (Stupsk: SA-10 went dumb, 4 AA-9s, 3 hits).

## The Elite threat picture (what the design is driven by)

| | Elite value | consequence |
|---|---|---|
| damage per hit | 4 random bits | one hit = mission over in practice: **zero hits** is the requirement |
| alert per seen pulse | +64 sites, +80 fighters; -16 / -32 unseen | 2 seen pulses in a row = network alert; ~4 = launch-capable |
| missiles in flight per launcher gate | <= 3 homing within 16 km | multiple simultaneous missiles are normal |
| flare / chaff lifetime | **6 game s** | release timing matters; one release per missile pass |
| decoy lifetime | **48 game s**, 3 decoys | the strongest tool, scarce |
| jammer burn-through | **18 km** | radar / IR jammer useless against anything closer (except command / class -1 at any range) |
| chaff vs class 3 (SA-10/12, SA-N-6) | only with our heading 51-129 deg off the missile's line | chaff needs a beam turn |
| fighter min launch range | 2 km | fighters shoot from 4-13 km (memlog), from behind (their nose within 33.75 deg of us) |
| AA-9 from 4 km | ~4 game s, **~1.5 real s, ~12 nav cycles** | no time to plan after launch: react on the first cycle |
| SAM from 100 km | ~2 game min | lots of time: deny the launcher |

Observed at Elite: **every hit was a fighter's AA-9** (semi-active, class 2) launched inside 8 km, where the missile is
autonomous. The one long SAM shot (SA-10 at 114 km) went dumb when the site lost us. So the order of work is:
fighter missiles inside 8 km first, long-range SAMs second, IR (SA-14 cells at Elite spawn > 4 km away) third.

Time: the Elite flights ran ~2.6 game s per real s (clock 80 ticks in 62 real s); nav cycles every ~0.13 real s
(~0.35 game s, about one game frame at fs 6-7). Read the 8 enemy slots every cycle (192 bytes; one PEEK batch).

## Requirements

### R1. `analysis/missile_game.py`: the missile model as code
Like `radar_game.py`: integer rules from notes/enemy_ordnance_and_countermeasures.md §1, per frame:
- `spec(s)` -> name, range, speed, class, agility; `seeker_ok(m, target, player_hdg, cls)`; `jammed(m, cls, dist,
  ecm, irj, net_timer, opp)`; `launcher_ok(m, sites, aircraft, dist)`; `cm_capture(m, cms, player_hdg, cls, opp)`;
  `step(m, aim, locked, opp, fs)` (steer with the clamp, loft aim, unlocked nose-over, move, ttl);
  `hit(m, player, fs, locked)`; `simulate(m, player_traj, cms, ..., frames)` -> outcome (hit at t / expired /
  overshoot / ground / captured) and the closest approach.
- **Gate (must pass before R3 acts):** replayed on every memlog missile, `simulate` from each snapshot reproduces the
  next snapshot's heading within 2 deg and position within 1 speed step, and the 3 Elite hits and the SA-10 miss come
  out right. Add it as `missile_game.py check`. Weakest area today: a *tracking* missile's steering (only weakly
  tested; collision-course missiles barely turn).

### R2. Reading and logging
- `f19_state.read_enemy_missiles(db)`: slots 0-7 decoded (x, y, alt, speed, hdg, pitch, ttl, spec, launcher), plus
  CM slots ds:520A + 12k (x, y, kind, ttl), stores ds:4ED4, net timer ds:5522 / ds:6EAA, homing count ds:7F96,
  fs ds:4EFC, launcher flags (site record +8 bit 0x10, sim aircraft +0x16 bit 8).
- CSV columns: `missile` (count live), `m_worst` (slot, spec, range km, time-to-go s, off-bore, tail, guided-why,
  predicted outcome), `m_action`. Events: launch seen, prediction change, every CM / jammer / decoy use, outcome
  (hit / expired / overshoot / ground / captured), with the predicted outcome next to the real one.

### R3. Decision: cheapest response that clears every missile
Each cycle, for each live missile, classify with `simulate` against the **current plan** (route or FighterWatch
heading, current speed/alt):
- **Doomed** (blind, launcher lost us beyond 8 km, out of reach before its last 8 s, turn-limited out): do nothing
  except log it. Reacting to these only spends stealth. (Most missiles are in this class.)
- **Threat**: predicted within the hit radius x MARGIN_K, where MARGIN_K is set by how well tested the rules the miss
  relies on are (start 2.0 for untested rules, 1.3 for memlog-checked ones; tighten as live trials pass).

For threats, evaluate candidates over the next 10-30 game s and choose the **lowest-cost candidate whose predicted
outcome is a miss for every live missile**:
- headings in 15 deg steps x bank {current, 25, 45 deg} x speed {165 kt, current, max};
- plus CM options: flare / chaff now or at time-to-go = lifetime (6 s), decoy now, IR / radar jammer on;
- combinations of one heading/bank/speed choice with up to one CM release this cycle.

Then commit: hold the chosen candidate until its window ends, unless a new prediction for it flips to "hit". No
re-choosing every cycle (the Elite crash flip-flopped headings).

If **no candidate clears**: take the one with the largest predicted miss distance, ignoring stealth cost (survival
mode), and log it loudly.

### R4. Stealth cost function (the tie-breaker among clearing candidates)
Cost = Σ over the candidate window:
1. **Threshold crossings**, by far the largest terms: any site or fighter predicted to cross **0x7F (network alert:
   all sites raised, fighters scrambled, net timer refreshed)** = 100; 0xC0 (launch-capable) = 60; 0xFA (radar ID,
   -10 base points) = 30. Network alert costs the most at Elite because fighters are what hit us.
2. **Seen pulses** that don't cross a threshold: 5 each, plus the headroom used (alert after / 0x7F).
3. **Stores:** decoy 25 (only 3, but it also *suppresses* network alerts for 48 s: credit that against term 1);
   flare / chaff 1; radar jammer on 10 per pulse window (+16 EMV); IR jammer 1 (verify: no EMV term found).
4. **Route:** fuel / time off route, small (0.5 per game min), so that it only breaks ties.

Pulse prediction: sites from their live countdown (record +0x0C), active bit 0x02 and `pulse_period_ticks(alert)`;
seen = `radar_game.site_return + emv(alt, kt, opp, roll, bay, ecm) > 100` at each pulse time with the candidate's
position, heading and **bank at that moment**. This is what makes a hard turn cheap *between* pulses and expensive
*during* one. Fighters: their check timer isn't decoded; treat them as checking at any moment (conservative) until it is.

Expected behaviour that falls out (use these as unit tests of the cost function):
- **Fighter AA-9 inside 8 km, launched from behind:** chaff at once (free; captures it for 6 s) + beam turn at 25-45 deg
  bank; by the time the chaff dies we are > 22.5 deg off its nose, and past it > 135 deg (overshoot kill).
  Decoy only if a second missile is live or the beam turn would make a site cross 0x7F.
- **SAM beyond 8 km:** no CM; the heading / speed / altitude that makes the *launching* site stop seeing us (beam a
  doppler, fly radial on a pulse, 250 ft, 165 kt, rough / mountain cell), so the missile goes dumb (Stupsk SA-10).
  Same move as staying stealthy: usually costs nothing.
- **Command-guided (SA-2/SA-5):** radar jammer is decisive at any range; use it only if the denial heading doesn't clear.
- **IR tail (SA-14/SA-13/AA-8):** turn > 45 deg off its heading at shallow bank; flare if it can't be done in time.
- **All-aspect radar (SA-10/12) inside 8 km:** beam (turn rate 2.8 deg/s beats it within ~1.6 km) + chaff while beaming.

### R5. Integration in nav
- `MissileWatch.step(s, p, now, route_want, missiles, sites, aircraft, opp)` -> (overrides, events), same shape as
  FighterWatch; overrides: `want`, `rate_cap`, `speed_kt`, `alt`, `bank_max`, and a `cm` request
  (`"flare" | "chaff" | "decoy" | "ecm_on" | "ecm_off" | "irj_on" | "irj_off"`) that nav executes with one tap.
- Priority in nav's override chain: route / attack -> FighterWatch -> **MissileWatch** -> TerrainEscape (terrain stays
  last: hitting the ground is as fatal). MissileWatch must give TerrainEscape a floor: never below 200 ft in a
  turn; the pull-up rules of the 2026-10-22 fix stay in force.
- Abort an attack run / photo pass when a threat is predicted (bay open = +24 EMV); resume after.
- Throttle: use `thr.target` like FighterWatch (and respect the 138 kt guard); full power only in survival mode.
- Keys: flare `1`, chaff `2` (trial_decrement_correlation), IR jammer `3`, radar jammer `4` (FINDINGS), decoy:
  **find the key** (probably `5`; check with a stores read before / after). Verify each with a stores / ds:684C read.
- Flag: `--missile-watch act|observe|off`; default `act` at opp 3, `observe` otherwise.

### R6. Things to measure before trusting the candidate model
- F-19 turn performance under nav control at 250 ft: roll-in time to 25 / 45 deg, sustained turn rate at 165 / 250 kt,
  altitude loss per turn (nav's roll keys change the turn rate by only ~0.28 deg/s per second held). The candidate
  trajectories in R3 must use these, not ideal turns.
- Key latency: real s from tap to stores decrement and CM slot appearing.

## Acceptance

1. `missile_game.py check` passes on all memlog missiles (R1 gate).
2. **Offline replay** of 1792628244 and 1792715432 (MissileWatch fed the logged snapshots, observe mode): each of the
   3 hitting AA-9s is predicted "hit" on the first snapshot it appears; the SA-10 and every blind SA-14 in the Veteran
   logs are "doomed" (no action); the chosen action is logged for each.
3. **Live, no-damage mode** (R key; ds:684C 0x1000: hits still show "hit by", damage skipped, so runs repeat), Elite, on
   VNC 5900, `--memlog`, from save states:
   a. fighter AA-7/AA-9 inside 8 km: chaff + beam -> no "hit by" in 5/5;
   b. SA-11 / SA-10 launch > 30 km: launcher-denial heading -> missile dumb (heading frozen) in 5/5;
   c. SA-14 cell: tail-off turn / flare -> no hit;
   d. decoy: missile turns to the decoy point, no network alert while it lives;
   e. stealth: per engagement, report seen pulses and threshold crossings vs a "max evasion" baseline run of the
      same save state; MissileWatch must have no more network alerts than the baseline and zero hits.
4. Then one full Elite mission with damage on, MissileWatch in `act`: zero missile hits, all engagements logged.

## Open questions (decode as needed; none block R1-R2)

- Fighters' radar-check timer (for pulse prediction of aircraft).
- Decoy key; whether the IR jammer has any detection cost.
- Who fires specs 0x22-0x25 (class -1 copies of AA-6/7/9).
- How fast an unlocked missile noses over and hits the ground (favours low flight; rate unmeasured).

## Status (2026-10-23, implementation)

Code: `analysis/missile_game.py` (R1), `scripts/missile_watch.py` (MissileWatch + CmKeys; kept out of phases.py so it can
move as a unit in the nav refactor), `scripts/f19_state.read_missile_world` / `read_enemy_missiles` (R2),
`scripts/nav.py` (`--missile-watch act|observe|off`, chain route/attack -> FighterWatch -> MissileWatch -> TerrainEscape,
attack on hold + bay shut while acting, CSV `missile`, `m_worst`, `m_action`, banner), `scripts/missile_replay.py`
(acceptance 2 + closed loop). Decoy key: **'5'** (disassembly key table). Details: notes/enemy_ordnance §7.

- R1 gate: 629/631 snapshot pairs, 26/27 turning pairs, 64/64 outcomes (the two misses are explained in §7). Two rules
  the PRD didn't know matter most: expired CMs keep capturing (memlog-confirmed, 7/7 pairs) and a CM the missile flies
  past destroys it.
- Acceptance 2 (offline, observe): every hitting AA-9 (1 Liberec, 3 Stupsk) predicted HIT on its first snapshot; the
  Stupsk SA-10 "no hit in 30 s" then "spent (launcher)"; 57/57 Veteran SA-14s doomed, 0 evasions; 64/64 first predictions
  right. Closed loop: 0/4 hits at key latency <= 1.0 real s (4/4 straight on).
- R6 turn model measured (TURN_K 337, roll 2 deg/game s): beam turns can't beat a missile inside 8 km; the PRD's
  "chaff + beam turn" becomes "chaff (or a decoy) at once"; the heading choice still matters for launcher denial > 8 km.
- Robustness: a candidate counts as robust if it still clears with expired CMs NOT capturing, half the turn rate,
  launchers never losing us, and twice the key latency (KEY_LATENCY_S = 1.0 real s until measured).
- NOT done: acceptance 3 / 4 (live, VNC 5900, no-damage mode) - nothing has been flown. First live items: key latency
  ('2' tap -> ds:4ED6 decrement -> CM slot), the decoy key, and a chaff capture vs an SA-11 / AA-x.
- Smoke test: nav run against a fake automation port serving the Stupsk 1167 snapshot: launch seen, HIT predicted,
  chaff key tapped, retry / give-up / block path exercised, CSV columns written.
