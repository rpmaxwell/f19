"""The one table of F-19 memory addresses (WP-4). How each was found: docs/game/memory-map.md and the comments here.

EGAME (the flight program) addresses are "old physical" addresses: where they were found on 2026-10-07/09 with no
LOADFIX. Where DOS loads the game depends on what is resident below it (LOADFIX moved everything +0x10420), so the
real address is `old + offset`, the offset found at run time by locating SIGNATURE (reader.find_offset).

EGAME's data segment: ds:46E0 = old phys 0x40250, so old phys = DS + ds offset. `ds(off)` converts.

START (the launcher / briefing program) addresses are offsets in START's own data segment (START_*), whose physical
base is found through START's DOS memory block (memory.mission.start_ds).
"""

# ---------------------------------------------------------------------------------------------- locating EGAME
SIGNATURE = b"o delivered\x00Safe Landing"     # text just before the waypoint table
SIG_OLD = 0x403B0                                # its old phys address
SCAN_LO, SCAN_HI = 0x10000, 0xA0000              # find_offset scans conventional memory
DS = 0x3BB70                                     # EGAME ds:0 in old phys


def ds(off):
    """EGAME ds offset -> old phys."""
    return DS + off


# ------------------------------------------------------------------------------------------------ player state
# (f19_state.py, HUD correlation 2026-10-07/09)
ALT = 0x40250            # u16 ft (== HUD; copy at 0x451C6) = ds:46E0
SPD = 0x440CA            # u16 kt (== HUD)
HDG = 0x4024A            # u16, 65536 = 360 deg = ds:46DA
POS = 0x451B8            # u32 X (east +), 2048 per km; u32 Y (south +) at +6
WPT = 0x403CA            # 4 x (u16 X, u16 Y) waypoint table, 64 per km (= position >> 5)
WPIDX = 0x4041C          # u8 selected waypoint 0-3
SENS = 0x417DA           # u8 keyboard sensitivity - 1 (Insert cycles; 2026-10-14)
TIME_MULT = 0x410AA      # u8 game time acceleration: 1 normal, 2 accelerated (2026-10-14; nav.time_mult)
TRACKCAM_MODE = 0x455A6  # u8: F2 cycles 2 air-to-ground -> 0 air-to-air -> 1 nav (2026-10-18)
AIR_GROUND = 2

# EGAME disassembly (FINDINGS "Weapon release", "Photo, lock and guidance", "Landing rules"); toggles and stores
# confirmed live on the runway 2026-10-17 (probe_controls.py)
PITCH, ROLL = ds(0x46DC), ds(0x46DE)             # i16, 65536 = 360 deg, pitch + = nose up
FLAGS = ds(0x684C)                               # u16 bits below
GEAR_UP, FLAPS, BAY, BRAKES, ECM, IRJ = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
LOCKED = ds(0x5526)                              # u16 locked ground target (target table index)
OPP = ds(0x4EF0)                                 # u16 opponent level 0 Green .. 3 Elite
RWY_BOX = ds(0x5534)                             # runway-box test result (nonzero = inside)
PROJ = ds(0x53FC)                                # projectile slots, 0x18 each; 0-7 enemy, 8-11 player
PROJ_SZ = 0x18
TARGETS = ds(0x80AA)                             # 16-byte target records (.WLD order): +2 X, +4 Y, +6 radar type,
TARGET_SZ = 0x10                                 #   +8 flags (0x80 destroyed, 0x02 on this duty window), +0x0A alert
N_TARGETS = ds(0x9C78)                           # u16 record count
# Trackcam target selection (EGAME 0xab84-0xacbc; docs/game/memory-map.md "Trackcam target selection", 2026-10-30)
CAM_SCREEN = ds(0x9740)                          # u16 screen id; B works on 0x13 / 0x19 only ("/" sets 0x13)
CAM_VIEW = ds(0x9E8C)                            # u16 view offset from the nose (0 ahead; 0x4000 = 90 deg)
NIGHT = ds(0x4EF4)                               # u16 night flag: the B range is 100 << (6 - night) units
CAM_FLAGS = ds(0x94E0)                           # u16: bit 0x80 set = B does nothing
CAM_COOLDOWN = ds(0x8B22)                        # u16 frames; B does nothing while nonzero
N_SEARCHED = ds(0x9500)                          # u16 records the B search walks (all world records; ds:9C78 is the enemy subset)
THROTTLE = 0x40262                               # u8 percent
STATION = 0x40A5A                                # u16 selected weapon station (& 3)
LOADOUT = 0x40A4A                                # 4 x (u16 weapon index, u16 count), indices as analysis/ordnance.py
GUN_ROUNDS = 0x40A5C                             # u16 nose-gun rounds (650 at start)
BRIEF = 0x44304                                  # 2 x 0x12 briefing blocks (+0 type, +2 target record)
HUD_LOCK = ds(0x6342)                            # u16 "Missile Lock" (disassembly; logged, not yet trusted)

# Sim objects (FINDINGS "Visual ID"; memlog-confirmed 2026-10-21): 20 slots x 0x24: +0 X, +2 Y, +4 alt, +0x14 type,
# +0x16 flags, +0x20 alert. AIRCRAFT (f19_state) and SIMOBJ (f19_state, missile_game) are the same table.
SIMOBJ = ds(0x8854)
AIRCRAFT = SIMOBJ                                # alias
AIRCRAFT_N, AIRCRAFT_SZ = 20, 0x24
AIRCRAFT_TYPES = {0: "MIG-23", 1: "MIG-25", 2: "MIG-29", 3: "MIG-31", 4: "Su-27", 5: "IL-76", 6: "F-4E", 7: "F-14",
                  8: "F-18", 9: "An-72", 10: "F-18", 11: "MIG-23"}       # EGAME ds:49B0 + 32 x type

# Flight-log variables (FINDINGS "Flight-log variables", EGAME disassembly 2026-10-18; clock and fuel memlog-checked)
CLOCK = ds(0x6630)                               # u16, 1 tick = 2 game s
FUEL = ds(0x4ED0)                                # u16 lb
DMG_BITS = ds(0x4ECE)                            # u16: 0x01 missile warning, 0x02 engine, 0x04 controls, 0x08 INS,
                                                 #   0x10 bay jammed, 0x20 fuel leak, 0x40 countermeasures
DMG_COUNT = ds(0x95BE)                           # u16, starts at 2, +opp+1 per hit
CM_STORES = ds(0x4ED4)                           # 3 x u16: flares, chaff, decoys
VIS_IDS, RADAR_IDS, ALERTED = ds(0x966E), ds(0x9672), ds(0x9674)
EMV_W, THREAT_COLOR, AP_ALT, SPOOL = ds(0x551C), ds(0x552C), ds(0x4EEE), ds(0x85F8)
EVENTS, N_EVENTS = ds(0x8E46), ds(0x632E)        # 6-byte records [u16 clock, x>>7, y>>7, type|flags, target]; count
HUD_MSG = ds(0x65C6)                             # message build buffer (40 B read)
NET_ALERT = ds(0x95CE)                           # network alert level (>= 0x80 recent; radar_game.duty_active)
FRAME = ds(0x5520)                               # u16 frame counter (MissileWatch's game-time source; Clock, plan 4.3)
GRID = ds(0x85FC)                                # 16 x 16 territory cell bytes

# Gun (FINDINGS "Guns", flight-tested 2026-10-18/19; gun_probe.py's names)
FRAME_SCALE = ds(0x4EFC)                         # u16 fs: bullet speed 112 // fs units a frame
GUN_SLOTS = ds(0x855E)                           # u16 bullet slot count = clamp(2 fs, 3, 16)
UNITS_XY = ds(0x94EE)                            # u16 player X in units (64 per km); Y at +0x10 (ds:94FE)
UNITS_Y = ds(0x94FE)
IMPACT = ds(0x94EA)                              # last gun impact X; Y ds:94F6, alt ds:94FC
IMPACT_Y, IMPACT_ALT = ds(0x94F6), ds(0x94FC)
BULLET_SLOTS = ds(0x9B88)                        # 12 B each (X, Y, alt, vX, vY, vAlt)
# gun_probe's ALT / HDG / PITCH / NSLOTS / FS are ds(0x46E0) = ALT, ds(0x46DA) = HDG, PITCH, GUN_SLOTS, FRAME_SCALE

# Flight-event list as END.EXE scores it, located by its first records (takeoff 08 00 20 00, then a 64 s sample
# 09 00 40 00); read_events. Its fixed home is EVENTS above (read_event_list).
EVENT_SCAN_LO, EVENT_SCAN_HI = 0x40000, 0x80000
EVENT_START, EVENT_NEXT = bytes.fromhex("08002000"), bytes.fromhex("09004000")

# MissileWatch's read (f19_state.read_missile_world -> analysis/missile_game.parse): raw ds offsets, (start, length)
MISSILE_WORLD_DS = [(0x46D8, 0x10), (0x4EC0, 0x40), (0x5200, 0x40), (0x53FC, 0xC0), (0x5520, 8), (0x6630, 2),
                    (0x684C, 2), (0x6EAA, 2), (0x7F94, 4), (0x94EE, 2), (0x94FE, 2), (0x95BE, 2), (0x9C78, 2)]
MISSILE_WORLD_FULL_DS = dict(targets=0x80AA, simobj=0x8854, grid=0x85FC)   # + 16 x n records, 20 x 0x24, 256

# nav --memlog windows (nav.MemLog): EGAME's data area, plus the launcher's working copy of the theater .WLD
MEMLOG_EGAME = (0x3C000, 0x48000)                # + offset
MEMLOG_WORLD_LEN = 0xC00                         # from the copy's start (aligned down to 64)

# ------------------------------------------------------------------------------------------------------- START
# START's data segment = START's PSP + START_DGROUP ("MS Run-Time Library" at ds:0008); read_mission.py, 2026-10-17
START_DGROUP = 0x1010                            # paragraphs from the PSP
START_BLOCK, START_BLOCK_LEN = 0xB946, 0x12      # primary briefing block; secondary at +0x12:
                                                 #   u16 type, target (record), base, code, mission number, char onc[8]
START_DEADLINE = START_BLOCK + 0x10              # u16 deadline offset (generator's ds:CA4A >> 4), 0 = none
START_RECS = 0xB38E                              # START's own .WLD copy: record i at +16 i
START_MISSION_TABLE, START_N_MISSIONS = 0x4B0C, 56   # 12 B per mission number
START_CATEGORY = 0xC162                          # u8 per (class & 0x7f): target category the table matches
START_PILOT_PTR = 0x991C                         # far ptr to the active pilot record
PILOT_THEATER, PILOT_LEVEL, PILOT_FLAGS, PILOT_OPP = 0x38, 0x3A, 0x3C, 0x3E   # pilot record fields (u16)
START_CLOCK_START, START_CLOCK_DEADLINE, START_CLOCK_END = 0x4DB6, 0x4DBC, 0x4DC2   # "hh:mm" briefing strings
START_COMM_PTR, COMM_FUEL_EST = 0xD066, 0x2E     # commData far ptr; +0x2E fuel estimate (16 lb per km of L)
START_SEED = 0x7A50                              # u32 MS C rand state (srand once at START load)
START_NEW_FLAG = 0xCA6A                          # u8 1 from "Select New Mission" until the generator has run
# Generator code (START cs = ds - 0x1000 paragraphs): entry, a return address after a real call, the briefing-entry
# code after it that draws more random numbers
START_GENERATE, START_GENERATE_RET = 0x76C8, 0x072C
START_AFTER_GENERATE, START_AFTER_GENERATE_END = 0xB61C, 0xB72E
START_GENERATE_CLEAR = (0x98E8, 0x9922, 0xBE48, 0xC977)   # bytes cs:B5F0 clears before calling the generator
# TODO(live research, WP-4b): START's menu / cursor variables (which screen, which row is highlighted) are not
# known yet. Find them by diffing PEEK snapshots while moving the cursor on the briefing room menu, the arming
# screen, the options menu and the roster. Until then the mission tool uses fixed key sequences checked only
# through START_NEW_FLAG and the briefing block.

# Theater .WLD working copy kept by the resident launcher (read_mission.py): records at +0x08 + 16 i from the copy's
# start; generated target slots at +0x18 / +0x28; place records from +0x38. Located by the file's bytes 0x38:0x3E.
WLD_ANCHOR = (0x38, 0x3E)
WLD_SLOTS = (0x18, 0x28)
WLD_PLACES = 0x38
