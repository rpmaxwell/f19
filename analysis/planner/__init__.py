"""Shim package: the F-19 mission planner moved to src/f19/planner (WP-6). What stays here:

  python analysis/planner <plan_dir>          (reads <plan_dir>/plan.json; = f19.planner.cli.main)
  python analysis/planner/new_plan.py ...     (read the briefing, write plan.json, plan)
  python analysis/planner/checks.py           (unit checks of the model against the decoded game rules)

and one re-exporting module per old name (world, radar, profiles, fuelplan, route -> f19.planner.router, probes, loiter,
alertsim, plan -> planner / report / cli, score -> f19.rules.scoring) for the old importers. See f19/planner/__init__.py.
"""
