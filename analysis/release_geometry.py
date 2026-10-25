"""Low-altitude weapon release geometry, simulated from EGAME's own update rules (2026-10-17).

See notes/targeting_for_planner.md for the procedures and FINDINGS.md "Photo, lock and guidance" for the code.

- mav_drop(kt, nose_up_deg): ft a Maverick sinks below the plane before its pitch reaches 0. It spawns 20 ft
  below, at plane pitch - 22.5 deg, and steers up 0x200*4/fs per update once the target bearing is within
  22.5 deg of its heading. alt += sin(pitch)*(speed << 8)/fs; speed starts at velocity >> 11 = kt*27 >> 11 and
  rises 1 every other frame. fs (ds:4EFC) is adaptive (4-15); fs=4 is the floor at normal time.
- photo_score(r_units, alt_ft, pitch_deg, hdg_err_deg): the game's photo test value; it passes below
  (8 - 2*opp)*64.

  release_geometry.py      print the tables
"""
import math

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

if __name__ == "__main__":
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
