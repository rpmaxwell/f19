"""Planning profiles: hard limits plus objective weights.

A profile is a flat dict. Presets below; plan.json picks one ("profile") and may
override any key ("overrides"). Every value used by the planner lives here so a
new finding is a one-line change.

Objective, in "transit km" (the fuel one km of transit burns; one enemy sample is worth
lb_per_sample / transit lb per km of these, ~7 km):
    per km:  regime lb/km / transit lb/km x (1 - coldwar_discount x enemy_cell)
           + pulse_cost_samples x expected seen pulses per km (radar.py) x sample_km
           + SA-14 cell minutes by day x sa14_cost_samples_per_min x sample_km
    per corner: turn_simplicity_km + the extra seen pulses of its arc at turn_bank_deg
Speeds, throttles and fuel come from analysis/fuel_model.py through fuelplan.py.
"""

# The tightest turn the planner will ever draw outside a loiter. 20 km = the nav's 0.8 deg/s
# turn-rate cap at sensitivity 1 (measured 2026-10-14). Loiters (LOITER legs, nav's 1.6 deg/s
# mode) sit in cells no radar can see and use loiter_radius_km instead.
RADIUS_FLOOR_KM = 20.0

DEFAULTS = dict(
    # --- hard limits (validator rejects a route that breaks one) ---
    min_turn_radius_km=RADIUS_FLOOR_KM,
    fuel_loaded_lb=10000.0,    # internal fuel at takeoff (EGAME 0x3cce sets 10000)
    extra_fuel_bays=0,         # Extra Fuel tanks carried (0-3; plan.json "extra_fuel" sets it): +1900 lb each, transferred
                               # by nav when fuel < 500 lb (phases.FuelTransfer); no weight, no EMV until then
    landing_reserve_lb=1000.0, # must be left at landing (the score doesn't need it: see the report's cost line)
    cruise_ft=700.0,           # fuel-optimal cruise where no radar site would see the plane at this height
    threat_ft=250.0,           # below 300 ft wherever a site would see the plane at cruise_ft (EMV 13 -> 4 at Veteran)
    alt_lead_km=6.0,
    low_in_enemy_from_opp=3,   # at this opponent level (3 = Elite) and above, threat_ft over all enemy ground           # start the descent / stay low this far before / after a threat stretch
    min_high_km=15.0,          # a gap shorter than this between two low stretches stays low (no yo-yo)
                               # mountains are walls at either height (never climb over)
    mountain_ft=200.0,         # terrain this tall is a wall
    clear_km=2.0,              # no wall within this distance of the flown path (nav cross-track + margin)
    plan_margin_km=1.5,        # corridors are planned this much wider than clear_km, so 20 km arcs
                               # (which cut inside their corners) still keep clear_km
    overfly_max_turn_deg=10.0, # below this a target is just a vertex; above, the turn's arc is placed over it
    # --- fixed geometry of takeoff / landing (all runways run north-south) ---
    climb_km=8.0,
    approach_km=20.0,
    straight_in_km=12.0,       # straight on the runway axis before the approach fix: every turn onto the axis ends
                               # this far out, so nav reaches the fix aligned (2026-10-20: 2 km off at the fix after a
                               # 20 km turn that ended there -> S-turns -> go-around, twice)
    final_km=6.0,
    # --- target attack geometry (notes/targeting_for_planner.md; not flight-tested) ---
    maverick_launch_km=14.0,
    attack_runin_km=6.0,
    photo_km=None,
    attack_bearing_step_deg=30.0,
    attack_candidates=2,
    # --- radar (radar.py, analysis/radar_game.py) ---
    radar_margin=10.0,         # a site counts as seeing the plane when return + EMV > 100 - this
    pulse_cost_samples=3.0,    # one expected seen pulse costs this many enemy samples (ID = 10, launch = mission)
    turn_bank_deg=12.0,        # bank in a 0.8 deg/s turn: EMV ~7 level -> ~10 turning (2026-10-14) = ~12 deg
    loiter_bank_deg=25.0,      # bank in nav's 1.6 deg/s loiter turn (same scaling)
    bay_open_s=15.0,           # bay open before a photo point / Maverick launch (+24 EMV)
    ecm=False,                 # radar jammer on (+16 EMV, halves alert gain); not planned yet
    sa14_cost_samples_per_min=30.0,  # by day, per minute inside an SA-14 cell (bit 0x10); 0 at night
    # --- speed and fuel (fuelplan.py, analysis/fuel_model.py) ---
    throttles=(50, 60, 70, 80),  # reachable settings the planner may choose
    # nav holds one speed (~190 kt, 50 % most of the time) and can't fly per-leg throttle yet, so plan what it flies.
    # "auto" = the planner's optimum (least lb/min in enemy cells, least lb/km elsewhere) once nav can.
    enemy_throttle=50,         # or "auto"; flaps by enemy_flaps
    enemy_flaps="auto",
    transit_throttle=50,       # or "auto" (least lb per km: 70 % clean)
    transit_flaps="auto",
    burn_factor=1.0,           # planned burn x this (nav_lb_min below already is the measured burn)
    # nav flies one held airspeed (profile cruise_kt) and moves the throttle to keep it, so plan THAT: every step at
    # nav_hold_kt, burning the measured nav_lb_min. 18 logged flights (analysis/fuel_log.py summary, 2026-10-21):
    # 62.1 lb/min pooled, 191 kt mean. None = the fuel model's speed for the planned throttle (when nav flies per-leg
    # throttle). Refit both from the summary as flights accumulate.
    nav_hold_kt=190.0,
    nav_lb_min=62.1,
    fuel_ref_lb=6000.0,        # fuel weight assumed for search-time speeds (the report integrates the real one)
    # --- objective ---
    coldwar_discount=0.0,      # 0..0.9, or "auto": enemy-cell km earn their samples (fuel profile)
    turn_simplicity_km=5.0,    # every corner costs this much: fewer, longer legs for the nav
    # --- loiter (loiter.py): enemy cells no radar can see, flown in nav's LOITER mode ---
    loiter="off",              # "off" | "auto" (best quiet cell for this route) | [cx, cy] (32 km cell)
    # 2026-10-21 crash (nav 1792603373, cell (8,10)): on its FIRST loiter turn (r 4 km, 1.5 km clearance) nav fell
    # behind the circle (its aim point swung faster than its 1.6 deg/s cap with the press lag) and drifted 3.9 km
    # off the track into the ridge; nav's route tracking elsewhere is p90 2.4 km, max 2.6. So: wider turns (r 6 km
    # needs ~0.7 deg/s at 190 kt, half the cap) and a clearance above nav's worst seen error.
    # and the game clock ran 2.4-2.6x real time while nav's turn caps are per real second: its loiter cap (now
    # 2.5 deg/s) flies ~4.5 km circles at best there (1.6 deg/s: ~6.7 km). 6 km needs ~1.9 deg/s at 2.6x, 25 %
    # under the cap; 7 km fits no racetrack in (8,10) with 4 km clearance.
    loiter_radius_km=6.0,
    loiter_clear_km=4.0,       # wall clearance of the loiter, its run-in and run-out
    loiter_edge_km=3.0,        # distance from visible ground: a non-enemy or SA-14 cell, or where a site would see the
                               # plane (nav's loiter drift in replay: <= 2.3 km)
    loiter_radar_margin=15.0,  # a loiter spot is quiet when every site's margin is below -this at every heading (the
                               # route uses 10; a loiter passes each site many times). CE Veteran: 15 -> 47 % of enemy
                               # ground quiet, 25 -> 16 % (only mountain cells); the report's alert sim checks the result
    loiter_min_laps=1,
    # --- performance (for nav's speed hold; the plan's per-leg speeds are in the route points) ---
    cruise_kt=190.0,
    sample_game_s=64.0,        # cold-war sample interval
)

PRESETS = {
    "stealth": dict(pulse_cost_samples=10.0),
    "balanced": dict(),
    "fast": dict(pulse_cost_samples=0.5, turn_simplicity_km=3.0, enemy_throttle=70, enemy_flaps=False,
                 transit_throttle=70, transit_flaps=False),
    "fuel": dict(pulse_cost_samples=2.0, coldwar_discount="auto", loiter="auto"),
    "coldwar": dict(coldwar_discount=0.5),
    "mountain_loiter": dict(pulse_cost_samples=10.0, loiter="auto"),
}


def load(name="balanced", overrides=None):
    if name not in PRESETS:
        raise ValueError(f"unknown profile {name!r}; presets: {', '.join(PRESETS)}")
    p = {**DEFAULTS, **PRESETS[name], **(overrides or {})}
    unknown = set(overrides or {}) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"unknown profile keys: {', '.join(sorted(unknown))}")
    if p["min_turn_radius_km"] < RADIUS_FLOOR_KM:
        raise ValueError(f"min_turn_radius_km below the {RADIUS_FLOOR_KM:.0f} km floor (profiles.RADIUS_FLOOR_KM)")
    if p["coldwar_discount"] != "auto" and not 0 <= p["coldwar_discount"] < 1:
        raise ValueError("coldwar_discount must be in [0, 1) or 'auto'")
    p["name"] = name
    return p
