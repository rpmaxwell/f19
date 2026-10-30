# Pricing Risk at 250 Feet: A Constrained Route-Planning Model for a Stealth Strike Mission

*2026-10-28. Describes the planner as implemented on branch `planner-refactor`. Companion to `planner_refactor.md` (measurements, results) and `notebooks/planner_points_and_aircraft.ipynb` (runnable).*

**Abstract.** We describe the route planner for a flight simulation (F-19 Stealth Fighter) and its two objectives. The first is a fuel-cost proxy, in which risk enters as a shadow price on exposure. The second maximises expected career points net of a survival penalty, with a hazard of enemy fighter fire that depends on the route. Both objectives use the game's decoded rules. The fighter-hazard parameters are not calibrated to flight data. The points objective changes the chosen route on all three test missions. In the Elite fixtures, the choice depends on how the hazard is specified.

## 1. Problem

A mission is a takeoff base, one or two targets, a landing base, and an environment: terrain walls, radar sites, daytime SA-14 cells, and a difficulty level. The agent chooses a route and its timing. The score is

S = M · (E + 140·p + 35·s − 10·R − 20·V),

where E is the number of enemy-territory samples (one per 64 game seconds), p and s indicate the primary and secondary targets, R is the number of radar IDs, V is the number of visual IDs, and M is a multiplier fixed by the menus. Three features make the problem hard. Detection is a deterministic function of altitude, speed, bank, aspect and terrain, so exposure is costly but controllable. Detection triggers an alert cascade (network alert, scramble, launch, hit), and only the end of that cascade costs points. Fuel is a hard constraint, not a payoff.

## 2. Stage one: a priced shortest-path problem

The route is the solution of a constrained least-cost problem on a heading-augmented grid. Each move is priced in transit kilometres, which is a fuel proxy:

c(route) = Σ_km [ f(regime) · (1 − δ·enemy) + λ_p · π(x, θ) + λ_s · 1{SA-14} ] + κ · corners.

Here f is the fuel burn per km in the current regime. δ is a discount for km that earn samples. π is the expected number of seen radar pulses, computed from the game's own formula (return + EMV − 100 > 0). λ_p is the shadow price of a seen pulse, in samples (3 in the default profile, 10 in the stealth profile used for the Elite fixtures). λ_s is the price of a minute inside a live SA-14 cell. κ penalises each corner.

Hard constraints are handled as walls, not prices: terrain, a 20 km turn radius, a landing fuel reserve, and, at Elite, the SA-14 cells. The solver is A\* on the grid. A corner-reduction dynamic program then simplifies the path, a repair step enforces turn radii, and a fuel integration in game time fixes speed, altitude and burn. A route is valid only if every constraint holds.

Options are enumerated over target order, approach side and attack bearing, and each is planned. This is expensive (about 190 s per mission) because the same legs recur. Memoisation of searches, reductions and segment costs, a pruning bound on loiter options, and a process pool cut it to 23–40 s with byte-identical routes. The objective enters only through one function, `Planner.rank`.

**Limitation.** λ_p is a free parameter. It prices exposure, not its consequences, so two routes with equal expected seen pulses are equivalent even if one gives an enemy fighter a network alert.

## 3. Stage two: expected points net of a survival penalty

Let hits arrive at a rate h(t). Each hit applies opp + 1 independent damage draws. Bay doors jam with probability q_b = 1 − (7/8)^(opp+1), which is 33% at Veteran and 41% at Elite. A jammed bay is treated as a decision to eject, and ejecting over enemy ground multiplies the score by 2/8. A fuel leak with probability q_l = q_b shortens the flight. With cumulative hit mass H(t), the value of a route is

V = E[ ∫ S_b(t)·q_b·h(t)·(2/8)·P(t) dt + leak term + S_b(T)·S_l(T)·P(T) ] − Λ · Pr(death),

where P(t) is the points banked by time t, S_b and S_l are survival functions, and Λ = 30,000 prices death. The decoded ejection roll depends on attitude, not altitude, so Pr(death) = p_unlevel · Pr(eject), and p_unlevel is a decision parameter set to zero. Since the multiplier M cancels, the ranking is invariant to it while p_unlevel = 0.

The ranking now sees a trade-off the cost objective cannot express. An extra target adds points and adds airborne minutes, which are exposure to the hazard. A primary-only plan is added to the option set so that this trade-off can be resolved.

We use two hazard specifications:

1. **Flat.** h is constant per airborne minute: 0.0003 at Veteran (half an event in 1,878 logged minutes with none) and 0.0288 at Elite (10 hits in 347 minutes). Elite flights ended at the first serious damage, so that rate is biased upward.
2. **Mechanistic.** Each alert simulation scenario gives the times at which a network alert is raised, either by a radar site seen above the alert threshold or by an SA-14 launch. This opens a hunt. Airborne fighters are drawn (Poisson, 1.9 within 100 km and 1.4 within 100–200 km, with types from the theater roster) and close at their type speed. Every 32 s, each fighter's own radar sees the plane or not, using the game's formula with the fighter's radar type, our altitude, speed and bank, and the clutter of our cell. Pulse radars are blind head-on, doppler radars are blind on the beam, and mountain cells hide the plane. The fighter's alert gains 16·(war + opp) + 32 when it sees us and falls by 32 when it does not. Above the network threshold and inside 8 km, a visual ID is incurred and an armed type fires, with hit probability 0.43. Hits therefore depend on the route's alerts, aspect and terrain.

## 4. Results

Three fixtures were planned under each objective and every chosen route was scored under both hazards. Values are base points and are meaningful as ratios.

| fixture | chosen by | minutes | value, flat hazard | value, fighter model | fighter shots |
|---|---|---|---|---|---|
| Liberec (Veteran) | cost | 205 | 1126 | 1147 | 0.00 |
| | points (= fighter model) | 183 | 1166 | 1187 | 0.00 |
| Kolobrzeg (Elite) | cost | 195 | 284 | 1448 | 0.27 |
| | points (flat) | 120, primary only | 344 | 1048 | 0.19 |
| | fighter model | 165 | 313 | 1516 | 0.19 |
| Budejovice (Elite) | cost | 158 | 276 | 1045 | 0.50 |
| | points (flat) | 141 | 314 | 1026 | 0.73 |
| | fighter model | 189 | 245 | 1138 | 0.75 |

At Veteran the two points specifications agree and beat the cost choice by 3.5% in 22 fewer minutes. The fighter model predicts no shots, which matches the logs (no Veteran fighter launch in 1,878 minutes, despite 58 SA-14 launches).

At Elite, the flat hazard reproduces the earlier analysis: drop the secondary and cut the flight short. The mechanistic model rejects that. It predicts 0.08–0.3 expected hits, compared with 3–5 under the flat rate, so giving up about 30% of the points buys little safety. On Budejovice the flat-hazard choice scores below the cost choice (1026 vs 1045) under the fighter model, because the flat hazard sees only minutes aloft and not the alerts that bring fighters.

## 5. Caveats

The fighter model has not been calibrated. Against the logs it implies about 0.0012 shots per minute, where the logged Elite rate is 0.066. That is a factor of about 50. The logged rate is biased upward, but not by that much. Scrambles of alert-gated fighters, patrol sightings with no network alert, and countermeasures are not modelled. The hunt duration (about 500 s), shots per fighter and the close-in aspect are assumptions. Raising the fighter count and shots by about 2.5× leaves the Kolobrzeg choice (both targets, northern approach) unchanged, so the ranking appears more robust than the levels. The 0.43 hit probability is an Elite value and is also applied at Veteran, where there is no data. Nothing here has been flown.

## 6. Conclusion

Replacing a shadow price on exposure with an explicit model of its consequences changes the chosen route on every test mission. It supports a higher-value, less defensive Elite plan than a flat hazard rate does. The next step is to calibrate the hunt against the seven logged Elite flights and add scrambles, before changing the default objective. The planner's default remains the cost objective.
