"""F-19 mission planner: one deterministic path from a plan spec to a flyable route.

  python analysis/planner <plan_dir>          (reads <plan_dir>/plan.json)

Everything is in waypoint-table units (game units, 64 per km; player position >> 5).
Modules, lowest first:
  world    theater, mission, terrain, territory, airbases (what the map is)
  radar    the radar model: the game's detection rules (analysis/radar_game.py), no terrain LOS
  profiles hard limits + objective weights, with named presets
  fuelplan throttle / flaps per leg and fuel / time along the route (analysis/fuel_model.py)
  route    search -> fewest legs -> 20 km fillets -> validation
  probes   test segments (radial runs, arcs, orbits) spliced in by meaning, not index
  loiter   racetracks in enemy cells no radar can see (score farming; not yet flown)
  alertsim per-site alert, pulse by pulse, for the report (launch / radar ID predictions)
  plan     the CLI: plan.json in, route.json + report.txt + route.png out
  checks   unit checks of the model against FINDINGS.md (python analysis/planner/checks.py)

A plan that breaks a limit is an error in the planner's output, reported by the
validator; fix the planner or the spec, never the route file.
"""
