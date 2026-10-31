"""python -m f19.planner.new_plan [--extra-fuel N ...]: read the briefing from the running game, write plan.json, plan
(f19.planner.cli.new_plan; --help lists the options)."""
from .cli import new_plan

if __name__ == "__main__":
    new_plan()
