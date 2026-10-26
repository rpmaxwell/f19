"""F-19 mission planner: one deterministic path from a plan spec to a flyable route (refactor plan 4.2, WP-6).

Everything is in waypoint-table units (game units, 64 per km; player position >> 5). Modules, lowest first:
  world     theater, mission, terrain, territory, airbases (what the map is)
  radar     the radar model: the game's detection rules (f19.rules.radar), no terrain LOS
  profiles  hard limits + objective weights, with named presets
  fuelplan  throttle / flaps per leg and fuel / time along the route (f19.rules.fuel)
  router    Router: search -> fewest legs -> 20 km fillets -> validation; PlanError
  probes    test segments (radial runs, arcs, orbits) spliced in by meaning, not index
  loiter    racetracks in enemy cells no radar can see (score farming; not yet flown)
  alertsim  per-site alert, pulse by pulse, for the report (launch / radar ID predictions)
  planner   Planner: the mission skeleton, attack geometry, options (order, approach, bearings, loiters, probes)
  report    summary, report text, route.png, route.json dict (in memory)
  estimate  the cheap fuel estimate and FuelConstraint (FlightDirector's first_feasible)
  api       plan_mission(spec, mission) and route(state, goal, constraints, budget_s, planner=context(...))
  cli       the files: python analysis/planner <plan_dir>, analysis/planner/new_plan.py

A plan that breaks a limit is an error in the planner's output, reported by the validator; fix the planner or the
spec, never the route file.
"""
from .api import FuelConstraint, Goal, PlanResult, Pose, RouteResult, context, plan_mission, route  # noqa: F401
from .router import BudgetExceeded, PlanError  # noqa: F401
