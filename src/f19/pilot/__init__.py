"""The pilot layer (refactor plan 4.1): behaviors return Commands (phases/, reactions/; WP-7b); Controls, loops,
arbiter, runner and the flight director (WP-7a / 7c) turn them into keys.

Behavior priorities follow nav.py's override merge order (later wins): FlyLeg 0, FuelTransfer 5 (actions only),
Takeoff 10, Attack / GunAttack 20, Landing 30, FighterWatch 40, MissileWatch 50, TerrainEscape 60."""
