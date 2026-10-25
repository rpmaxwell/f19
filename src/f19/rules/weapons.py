"""Weapons: the ordnance tables and weapon-vs-target matrix, low-altitude release geometry, and the gun CCIP.

Ordnance (2026-10-17, docs/game/weapons.md "Ordnance vs target strength"):
- EGAME/START data segment holds the loadout catalog (20 x 26 B: short name[10], long name[12],
  u16 spec, u16 qty) and right after it the effectiveness matrix: 20 weapons x 13 target
  categories, int8, at EGAME ds:5106.
- A target's category = WLD category table[class & 0x7f] & 0x0f; the table is the 100 bytes right
  before the 256-byte territory grid, which sits right before the name strings (f19.gamedata.wld).
- category >= 13 overflows into the next weapon's row (the game does no bounds check).

Release geometry (2026-10-17), simulated from EGAME's own update rules (research/notes/targeting_for_planner.md for
the procedures, docs/game/weapons.md "Photo, lock and guidance" for the code):
- mav_drop(kt, nose_up_deg): ft a Maverick sinks below the plane before its pitch reaches 0. It spawns 20 ft
  below, at plane pitch - 22.5 deg, and steers up 0x200*4/fs per update once the target bearing is within
  22.5 deg of its heading. alt += sin(pitch)*(speed << 8)/fs; speed starts at velocity >> 11 = kt*27 >> 11 and
  rises 1 every other frame. fs (ds:4EFC) is adaptive (4-15); fs=4 is the floor at normal time.
- photo_score(r_units, alt_ft, pitch_deg, hdg_err_deg): the game's photo test value; it passes below
  (8 - 2*opp)*64.

Gun (2026-10-18, flight-validated; docs/game/weapons.md "Guns"): ccip() = where a bullet fired now would hit.

  python -m f19.rules.weapons        the ordnance tables (as analysis/ordnance.py)
"""
import math
import struct

from f19.core.geometry import game_len
from f19.gamedata.exe import ds_base, egame
from f19.gamedata.wld import WLD_FILE as WLD, categories

NCAT = 13
GUN_V = 4                     # matrix value of the gun vs SAM Radar / Mobile SAM (kill if the miss < GUN_V*6 // (opp+2))


# --- ordnance tables --------------------------------------------------------------------------------------------------
def egame_image():
    """EGAME load image (f19.gamedata.exe.egame)."""
    return egame()


def tables():
    """-> (loadout [20 x dict(short, long, spec, qty)], matrix bytes (20 x 13 int8 + overflow slack))."""
    d = egame_image()
    ds = ds_base(d)                                             # DGROUP base (string sits at ds:0008)
    matrix = d[ds + 0x5106:ds + 0x5106 + 20 * NCAT + 3 * NCAT]  # + slack for the category overflow
    cat0 = ds + 0x5106 - 20 * 26                                # loadout catalog sits right before it
    loadout = []
    for i in range(20):
        r = d[cat0 + 26 * i:cat0 + 26 * (i + 1)]
        spec, qty = struct.unpack_from("<hh", r, 22)
        loadout.append(dict(short=r[:10].split(b"\0")[0].decode(), long=r[10:22].split(b"\0")[0].decode(), spec=spec, qty=qty))
    return loadout, matrix


def compat(matrix, weapon, category):
    """Effective value (int8) of loadout index `weapon` against target category (low 4 bits); 0 = can never kill."""
    v = matrix[weapon * NCAT + (category & 0x0f)]
    return v - 256 if v > 127 else v


# --- release geometry -------------------------------------------------------------------------------------------------
U = 65536 / 360                                       # game angle units per degree


def mav_drop(kt, nose_up, fs=4, agility=2):
    v = int(kt * 27) >> 11; p = (nose_up - 22.5) * U; drop = 20; frame = 0
    while p < 0:
        p = min(0, p + (agility << 8) * 4 / fs)       # steering runs before the move
        if frame & 1: v = min(v + 1, 12)              # Maverick speed cap: 800 >> 6
        drop -= math.sin(math.radians(p / U)) * (v * 256) / fs
        frame += 1
    return drop


def photo_score(r, alt, pitch, hdg=0.0):
    e = -math.degrees(math.atan2(alt / 32, r))        # the game mixes ft/32 with map units
    return r + 11.38 * (abs(e - pitch + 9.74) + hdg)


def photo_window_km(alt, opp, pitch=0.0, hdg=0.0):
    ok = [r for r in range(1, 600) if photo_score(r, alt, pitch, hdg) < (8 - 2 * opp) * 64]
    return (ok[0] / 64, ok[-1] / 64) if ok else None


def photo_pitch(alt, r_km):
    """Nose-up pitch that zeroes the camera error at range r_km."""
    return 9.74 - math.degrees(math.atan2(alt / 32, r_km * 64))


# --- gun --------------------------------------------------------------------------------------------------------------
octo = game_len                                       # octagonal length, float (scripts/gun_probe.octo; identical)


def ccip(x, y, alt, pitch, hdg, fs, nslots):
    """Impact point (units) of a bullet fired now, or None. The game keeps bullet velocities as whole units a frame
    (speed r = 112 // fs; climb in units of 32 ft), so the dive angle comes in steps of ~1/r rad and the range in
    steps of r: 2026-10-18 flight, alt 626 at -3.7 deg gave v (1, 22, -32 ft) and an impact 20 frames out (440 units),
    not the 300 of the exact nose line. Rounding fits all three components seen so far (trunc doesn't fit vY)."""
    r = 112 // max(fs, 1)
    k = round(r * math.sin(math.radians(-pitch)))               # units of descent per frame
    if k <= 0: return None
    rh = round(r * math.cos(math.radians(pitch)))
    vx, vy = round(rh * math.sin(math.radians(hdg))), -round(rh * math.cos(math.radians(hdg)))
    a0 = alt - 2 - 32 * k                                       # after the spawn step
    n = (a0 // (32 * k) + 1 if a0 >= 0 else 0) + 1              # frames until alt < 0
    if n > 2 * nslots: return None                              # the slot is reused first: out of reach
    return x + n * vx, y + n * vy, octo(n * vx, n * vy)


def gun_kill_radius(opp):
    """Octagonal miss (units) below which a gun hit on a GUN_V target kills (scripts/gun_probe: GUN_V*6 // (opp+2))."""
    return GUN_V * 6 // (opp + 2)


# --- printouts (the old CLIs) -----------------------------------------------------------------------------------------
def print_ordnance():
    """analysis/ordnance.py: matrix + every theater's target types with their effective values."""
    loadout, m = tables()
    print("weapon            spec qty | " + " ".join(f"{c:2d}" for c in range(NCAT)))
    for w, l in enumerate(loadout):
        print(f"{w:2d} {l['short']:8} {l['long']:12} {l['spec']:#06x} {l['qty']} | " + " ".join(f"{compat(m, w, c):2d}" for c in range(NCAT)))
    used = {}
    for th in WLD:
        for k, (n, c) in categories(th).items():
            if c: used.setdefault(c & 0x0f, set()).add(f"{n}({th}{'+%02x' % (c & 0xf0) if c & 0xf0 else ''})")
    print("\ncategory -> object types (theater, high flag bits)")
    for c in sorted(used): print(f"{c:2d} {', '.join(sorted(used[c]))}")
    print("\neffective value per weapon (0 = can never kill; categories 13-15 read the next weapon's row)")
    for c in sorted(used):
        print(f"{c:2d} " + " ".join(f"{l['short'][:7]}={compat(m, w, c)}" for w, l in enumerate(loadout) if compat(m, w, c)))


def print_release():
    """analysis/release_geometry.py: Maverick drop and photo window tables."""
    ups = (0, 5, 10, 12, 15, 18, 20)
    print("Maverick ft lost after launch, by nose-up pitch (fs=4; fs=8 in brackets)")
    print("kt   " + "".join(f"+{t:<9}" for t in ups))
    for kt in (200, 250, 300, 350, 400, 450, 500):
        print(f"{kt:<5}" + "".join(f"{mav_drop(kt, t):4.0f}[{mav_drop(kt, t, fs=8):3.0f}] " for t in ups))
    print("\nPhoto window km, level flight, heading error 0 / 2 deg")
    for opp, name in enumerate(("Green", "Regular", "Veteran", "Elite")):
        cells = []
        for alt in (100, 150, 200):
            for hdg in (0, 2):
                w = photo_window_km(alt, opp, 0, hdg)
                cells.append(f"{alt}ft/{hdg}: " + (f"{w[0]:.2f}-{w[1]:.2f}" if w else "none"))
        print(f"{name:8}", " | ".join(cells))
    print("\nNose-up pitch for zero camera error at 150 ft:", {r: round(photo_pitch(150, r), 1) for r in (0.5, 1, 1.5, 2, 3)})


if __name__ == "__main__":
    print_ordnance()
