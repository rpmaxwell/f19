"""FuelTransfer (scripts/phases.py FuelTransfer) on step(state, ctx) -> Command.

Extra Fuel tanks (user rule, 2026-10-21): if and only if fuel < LOW_LB and a station holds Extra Fuel with count 1,
cycle to that station (Space) and press Enter. The bay need not be open. Success = the fuel reading goes UP (+1900 lb);
retried MAX_TRIES times, then that station is left alone. Repeats for every tank (up to 3 per mission).

A side behavior: it never steers; its Command holds only actions (Space = Action.NEXT_WEAPON, Enter = Action.FIRE) and
the event text. `busy` -> while set, nav held the attack off its station keys (the arbiter's rule). nav stepped it
every tick above 50 ft (wants_control).
"""
from f19.core.types import Action, Command

from f19.pilot.phases._common import EXTRA_FUEL


class FuelTransfer:
    name, priority = "fuel_transfer", 5
    LOW_LB, MAX_TRIES, MAX_SPACE, GAP_S, VERIFY_S = 500, 4, 8, 0.8, 2.5

    def __init__(self):
        self.state, self.next_ok, self.target, self.fuel0, self.tries, self.space = "idle", 0.0, None, None, 0, 0
        self.failed, self.log, self.key = set(), [], ""
        self.done = False

    @property
    def busy(self): return self.state != "idle"

    def wants_control(self, state, ctx): return state.alt_ft > 50

    def step(self, state, ctx):
        self.key = ""; acts = []
        ev = self._step(state.systems, ctx.now(), acts)
        if ev: self.log.append(ev)
        return Command(actions=tuple(acts), events=(ev,) if ev else ())

    def _step(self, sy, now, acts):
        fuel, loadout, station = sy.fuel_lb, sy.loadout, sy.station
        if self.state == "idle":
            if fuel >= self.LOW_LB: return ""
            tanks = [i for i, (w, n) in enumerate(loadout) if w == EXTRA_FUEL and n == 1 and i not in self.failed]
            if not tanks: return ""
            self.target, self.fuel0, self.tries, self.space, self.state = tanks[0], fuel, 0, 0, "select"
            return f"fuel {fuel} lb < {self.LOW_LB}: transferring Extra Fuel from station {self.target}"
        if now < self.next_ok: return ""
        if self.state == "select":
            if station != self.target:
                if self.space >= self.MAX_SPACE:
                    self.failed.add(self.target); self.state = "idle"
                    return f"fuel transfer: Space never reached station {self.target} (at {station}) - given up"
                acts.append(Action.NEXT_WEAPON); self.space += 1; self.key = "space"; self.next_ok = now + self.GAP_S; return ""
            acts.append(Action.FIRE); self.tries += 1; self.key = "enter"; self.state = "verify"; self.next_ok = now + self.VERIFY_S
            return ""
        if self.state == "verify":
            if fuel > self.fuel0 + 500:
                self.state = "idle"
                return f"fuel transferred from station {self.target}: {self.fuel0} -> {fuel} lb (try {self.tries})"
            if self.tries < self.MAX_TRIES: self.state = "select"; return ""
            self.failed.add(self.target); self.state = "idle"
            return f"fuel transfer from station {self.target} FAILED after {self.tries} Enter presses (fuel {fuel} lb)"
        return ""
