"""Shim: live player state from F-19 memory now comes from f19.memory (WP-4).

Addresses: f19.memory.addresses (one table). Readers: f19.memory.reader (same names, same requests, same dicts).
`_offset`, `_events`, `_peek`, `_peek_many` stay importable for nav.py, gun_probe and the research scripts; `_offset`
is the reader's own cache dict, so seeding or clearing it here still works.
The two readers that decode missiles (they need analysis/missile_game, a rules module) stay here on top of the
reader's raw accessors.
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
import sys, time  # noqa: E402,F401
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox  # noqa: E402

from f19.memory.addresses import (  # noqa: E402,F401
    SIGNATURE, SIG_OLD, ALT, SPD, HDG, POS, WPT, WPIDX, SENS, DS, PITCH, ROLL, FLAGS, LOCKED, OPP, RWY_BOX, PROJ,
    TARGETS, GEAR_UP, FLAPS, BAY, BRAKES, THROTTLE, STATION, LOADOUT, GUN_ROUNDS, BRIEF, HUD_LOCK, TRACKCAM_MODE,
    AIR_GROUND, AIRCRAFT, AIRCRAFT_N, AIRCRAFT_SZ, AIRCRAFT_TYPES, CLOCK, FUEL, DMG_BITS, DMG_COUNT, CM_STORES, VIS_IDS,
    RADAR_IDS, ALERTED, EMV_W, THREAT_COLOR, AP_ALT, SPOOL, EVENTS, N_EVENTS, N_TARGETS, SIMOBJ, HUD_MSG, ECM, IRJ,
    NET_ALERT, FRAME_SCALE, GUN_SLOTS, FRAME, TIME_MULT)
from f19.memory.reader import (  # noqa: E402,F401
    _offset, _events, _peek, _peek_many, find_offset, find_events, read_state, read_waypoints, read_sensitivity,
    read_flight, read_trackcam, read_aircraft, read_events, read_briefing, read_projectiles, read_target, read_status,
    read_alerts, read_gun, read_site_records, read_event_list, read_sites, read_time_mult, read_frame,
    enemy_projectiles_raw, missile_world_memory, MemoryReader)


def _missile_game():
    p = str(Path(__file__).resolve().parent.parent / "analysis")
    if p not in sys.path: sys.path.insert(0, p)
    import missile_game
    return missile_game


def read_enemy_missiles(db):
    """Live enemy missiles (projectile slots 0-7, one 192-byte read): [analysis/missile_game.Missile]."""
    G = _missile_game()
    raw = enemy_projectiles_raw(db)
    return G.parse(lambda a, n: raw[a - 0x53FC: a - 0x53FC + n] if 0x53FC <= a < 0x54BC else bytes(max(n, 2)))["missiles"]


def read_missile_world(db, full=True):
    """Everything MissileWatch needs (analysis/missile_game.parse) in one pipelined batch (~50 PEEKs with full, ~12
    without): f19.memory.reader.missile_world_memory."""
    return _missile_game().parse(missile_world_memory(db, full))


if __name__ == "__main__":
    with Dosbox() as db:
        print(f"memory offset {find_offset(db):+#x}")
        print("waypoints:", read_waypoints(db))
        for _ in range(int(sys.argv[1]) if len(sys.argv) > 1 else 3):
            st = read_state(db)
            print(f"alt {st['alt']:5d} ft  spd {st['spd']:3d} kt  hdg {st['hdg']:5.1f}  x {st['x']:9d}  y {st['y']:9d}")
            time.sleep(0.5)
