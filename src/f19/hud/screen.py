"""The in-memory cell buffer every widget draws into (testable without a terminal), and the HUD's colours.

A Screen holds one frame's character cells (char, fg, bg); emit() turns it into ANSI with as few colour changes as it
can (scripts/nav_tui.py Buf, unchanged). A "line" is a str or a list of spans [(text, fg)].
"""

BG, FG, DIM = (12, 14, 18), (205, 208, 212), (115, 118, 125)
WHITE, RED, ORG, YEL, GRN = (240, 240, 240), (240, 75, 65), (245, 150, 45), (235, 205, 70), (95, 210, 100)
CYAN, BLUE, MAG = (85, 200, 225), (95, 145, 245), (225, 95, 225)
POWER_COLOURS = [(15, 15, 20), (40, 80, 120), (70, 150, 215), (190, 235, 255)]   # x0 mountains .. x1.5 sea
BORDER, BORDER_ON, STATUS_BG, STATUS_FG = (75, 80, 88), (95, 210, 100), (0, 120, 50), (10, 10, 10)


class Screen:
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.fg = [[FG] * w for _ in range(h)]
        self.bg = [[BG] * w for _ in range(h)]

    def put(self, x, y, s, fg=None, bg=None, maxw=None):
        """Write s at (x, y), clipped to the screen (and to maxw cells) -> the cells used (clipped or not, as Buf)."""
        if not 0 <= y < self.h: return 0
        n = len(s) if maxw is None else max(0, min(len(s), maxw))
        for i in range(n):
            xi = x + i
            if 0 <= xi < self.w:
                self.ch[y][xi] = s[i]
                if fg is not None: self.fg[y][xi] = fg
                if bg is not None: self.bg[y][xi] = bg
        return n

    def spans(self, x, y, line, maxw, bg=None):
        """line: str or [(text, fg)]; clipped to maxw cells."""
        if isinstance(line, str): line = [(line, FG)]
        for text, fg in line:
            if maxw <= 0: break
            n = self.put(x, y, text, fg, bg, maxw); x += n; maxw -= n

    def emit(self):
        out, cur = [], None
        for y in range(self.h):
            out.append(f"\033[{y + 1};1H")
            for x in range(self.w):
                st = (self.fg[y][x], self.bg[y][x])
                if st != cur:
                    (fr, fg_, fb), (br, bg_, bb) = st
                    out.append(f"\033[38;2;{fr};{fg_};{fb};48;2;{br};{bg_};{bb}m"); cur = st
                out.append(self.ch[y][x])
        return "".join(out) + "\033[0m"

    # --- reading back (tests, parity)
    def rows(self):
        return ["".join(r) for r in self.ch]

    def text(self, x0, y0, x1, y1):
        """Characters of the region x0..x1, y0..y1 (inclusive), one str per row."""
        return ["".join(self.ch[y][x0:x1 + 1]) for y in range(max(0, y0), min(self.h, y1 + 1))]

    def colours(self, x0, y0, x1, y1):
        """(fg, bg) rows of a region (inclusive), for colour comparisons."""
        return [list(zip(self.fg[y][x0:x1 + 1], self.bg[y][x0:x1 + 1])) for y in range(max(0, y0), min(self.h, y1 + 1))]
