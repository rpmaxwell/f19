"""Phases (the mission's main line): FlyLeg (route following), Takeoff, Attack, GunAttack, Landing.
Each: step(state: GameState, ctx: FlightContext) -> Command; no keys, no memory reads (WP-7b)."""
from f19.pilot.phases.attack import Attack
from f19.pilot.phases.fly_leg import FlyLeg
from f19.pilot.phases.gun_attack import GunAttack
from f19.pilot.phases.landing import Landing
from f19.pilot.phases.takeoff import Takeoff

__all__ = ["Attack", "FlyLeg", "GunAttack", "Landing", "Takeoff"]
