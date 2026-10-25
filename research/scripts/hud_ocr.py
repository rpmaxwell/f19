"""Read HUD airspeed and altitude from a 1280x856 DOSBox-X window capture.
The game is 320x200 scaled 4x (content starts 56px down, below the title bar),
and the HUD uses a fixed-pitch 3x5 font, so digits are read by exact cell
lookup on the game-pixel grid."""
import numpy as np

FONT = {
    "0": "####.##.##.####", "1": ".#..#..#..#..#.", "2": "###..#####..###",
    "3": "###..#.##..####", "4": "#.##.####..#..#", "5": "####..###..####",
    "6": "#..#..####.####", "7": "###..#.#..#..#.", "8": "####.#####.####",
    "9": "####.####..#..#",
}
FONT_ARR = {k: np.array([c == "#" for c in v]) for k, v in FONT.items()}
ROWS = (54, 59)                      # game-pixel rows of the digit glyphs
ALT_CELLS = [228, 232, 236, 240, 244]   # numbers are left-aligned at x=228
SPD_CELLS = [80, 84, 88, 92]             # left-aligned at x=80

def game_pixels(img):
    """Full-window capture (PIL or ndarray) -> 200x320 bool 'white' mask."""
    a = np.asarray(img)[..., :3].astype(int)
    if a.shape[1] == 640:                     # window on a non-Retina display: 1x capture
        a = a.repeat(2, axis=0).repeat(2, axis=1)
    g = a[58:58 + 800:4, 2:1280:4]
    return g.min(axis=2) > 220

def read_cells(mask, cells):
    out = ""
    for x in cells:
        cell = mask[ROWS[0]:ROWS[1], x:x + 3].reshape(-1)
        dist = sorted(((FONT_ARR[k] != cell).sum(), k) for k in FONT_ARR)
        best = dist[0][1]
        if dist[0][0] > 1 or (dist[0][0] == 1 and dist[1][0] <= 1):
            if out and cell.sum() <= 3: break  # number ended (blank, or a tape tick mark)
            return None                        # partly covered / mid-redraw: reject
        out += best
    return int(out) if out else None

def read_hud(img):
    m = game_pixels(img)
    spd, alt = read_cells(m, SPD_CELLS), read_cells(m, ALT_CELLS)
    if spd is not None and spd < 100: spd = None   # truncated read
    return spd, alt
