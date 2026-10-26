"""map: the moving map (nav_tui.MapView). Route (flown legs dim), flown track, heading and wanted-heading vectors,
game waypoints 1-4, targets, radar sites, aircraft, on the territory grid's terrain types and the decoded 3D
terrain. Overlays (keys): t territory, p radar power (cell clutter), d detection zones, c SAM rings, s SA-14 cells,
a aircraft, g 32 km grid; + / - zoom, f fit the route / follow the plane."""
import math

import numpy as np

from f19.core.units import K
from f19.hud.screen import CYAN, DIM, FG, GRN, ORG, POWER_COLOURS, RED, WHITE, YEL, BLUE
from f19.hud.widget import Widget, register
from f19.rules import radar as rg


@register
class MapWidget(Widget):
    name = "map"
    min_w, min_h = 4, 2
    keys = "tpdcsag+=-_f"
    ZOOMS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]          # km per pixel (pixel = half a character cell)

    def __init__(self, ctx):
        super().__init__(ctx)
        self.grid = np.asarray(ctx.grid).astype(np.int64) if ctx.grid is not None else None
        self.H, self.step = ctx.heights, int(ctx.height_step)
        self.overlays = set("tg")
        self.zoom, self.fit = 2, False
        self.u = self.ZOOMS[self.zoom] * K
        self._det, self._det_t = (None, None), None

    # --- keys
    def handle_key(self, ch):
        if ch in "tpdcsag": self.overlays ^= {ch}
        elif ch in "+=": self.zoom = max(0, self.zoom - 1); self.fit = False
        elif ch in "-_": self.zoom = min(len(self.ZOOMS) - 1, self.zoom + 1); self.fit = False
        elif ch == "f": self.fit = not self.fit
        else: return False
        return True

    def title(self, view):
        on = lambda k, txt: (txt, WHITE if k in self.overlays else DIM)
        z = "fit" if self.fit else f"{self.ZOOMS[self.zoom]:g} km/px"
        return [(" map ", GRN), (z + " ", FG), on("t", "[t]err "), on("p", "[p]ower "), on("d", "[d]etect "), on("c", "[c]SAM "),
                on("s", "[s]A-14 "), on("a", "[a]ir "), on("g", "[g]rid ")]

    # --- drawing
    def draw(self, view, box, screen):
        self.render(screen, box.x0 + 1, box.y0 + 1, box.inner_w, box.inner_h, view.c, self.overlays, view.history.trail)

    def render(self, buf, x0, y0, w, h, c, ov, trail):
        pw, ph = w, 2 * h
        if pw < 4 or ph < 4: return
        p = c["p"]
        if self.fit and c.get("wps"):
            xs = [q[0][0] for q in c["wps"]] + [p[0]]; ys = [q[0][1] for q in c["wps"]] + [p[1]]
            u = max((max(xs) - min(xs)) / pw, (max(ys) - min(ys)) / ph, 0.25 * K) * 1.1
            cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        else:
            u = self.ZOOMS[self.zoom] * K; cx, cy = p
        self.u = u
        I = np.arange(pw); J = np.arange(ph)
        GX, GY = np.meshgrid(cx + (I + 0.5 - pw / 2) * u, cy + (J + 0.5 - ph / 2) * u)
        inside = (GX >= 0) & (GX < 32768) & (GY >= 0) & (GY < 32768)
        gxi, gyi = np.clip(GX, 0, 32767).astype(np.int64), np.clip(GY, 0, 32767).astype(np.int64)
        ci, cj = gxi >> 11, gyi >> 11
        G = self.grid[cj, ci] if self.grid is not None else np.full(GX.shape, 8, np.int64)
        img = np.zeros((ph, pw, 3), np.float32)
        # background: the 32 km cell's terrain type (sea / flat / rough / mountains: there is no decoded coastline),
        # with the decoded 3D terrain's hills and mountains (height ft) drawn over it
        tt = (G >> 2) & 3
        img[:] = np.choose(tt[..., None], [np.array(v, np.float32) for v in ((70, 58, 40), (44, 56, 32), (30, 52, 30), (14, 30, 66))])
        if self.H is not None:
            ht = self.H[np.clip(gyi // self.step, 0, self.H.shape[0] - 1), np.clip(gxi // self.step, 0, self.H.shape[1] - 1)].astype(np.float32)
            hill = np.clip(np.log1p(ht) / np.log1p(4000), 0, 1)[..., None]
            img[:] = np.where(ht[..., None] > 0, np.array((70, 60, 38), np.float32) + hill * (110, 95, 70), img)

        def tint(mask, col, a):
            m = mask[..., None]; img[:] = np.where(m, img * (1 - a) + np.array(col, np.float32) * a, img)

        def edges(v):
            e = np.zeros(v.shape, bool); e[:, 1:] |= v[:, 1:] != v[:, :-1]; e[1:, :] |= v[1:, :] != v[:-1, :]; return e
        side = G & 3
        if "p" in ov:                                      # radar power: return factor of the plane's cell
            cl = (G >> 2) & 3
            for k, col in enumerate(POWER_COLOURS): tint(inside & (cl == k), col, 0.6)
        if "t" in ov:                                      # territory: enemy red, friendly blue, neutral grey
            tint(inside & (side == 0), (215, 40, 40), 0.28); tint(inside & (side == 3), (50, 100, 235), 0.25)
            tint(inside & (side == 2), (150, 150, 150), 0.18)
            tint(inside & edges(np.where(inside, side, -1)), (250, 215, 70), 0.85)      # the front line
        if "g" in ov: tint(inside & edges(ci * 16 + cj), (0, 0, 0), 0.35)
        if "s" in ov: tint(inside & ((G & 0x10) != 0) & ((I[None, :] + J[:, None]) % 4 == 0), (255, 140, 20), 0.75)
        live = [s for s in c.get("sites", []) if not s["dead"]]
        if "d" in ov and live:                             # detection zones for the current flight state
            m = self._detection(GX, GY, G, live, c, (round(cx / u), round(cy / u), u, pw, ph))
            tint(inside & (m > 0), (255, 30, 30), 0.5); tint(inside & (m <= 0) & (m > -15), (255, 150, 30), 0.3)
        if "c" in ov:                                      # SAM rings (mission's missile range), game distance metric
            for s in live:
                R = s.get("range_km")
                if not R: continue
                ax, ay = np.abs(GX - s["x"]), np.abs(GY - s["y"])
                dd = np.maximum(ax, ay) + np.minimum(ax, ay) / 2
                tint(np.abs(dd - R * K) < u * 0.5, (90, 220, 110) if s["doppler"] else (240, 90, 80), 0.45)
        img[~inside] = (0, 0, 0)
        P = lambda q: ((q[0] - cx) / u + pw / 2, (q[1] - cy) / u + ph / 2)

        def line(a, b, col, dash=0):
            (ax_, ay_), (bx_, by_) = P(a), P(b); n = int(max(abs(bx_ - ax_), abs(by_ - ay_))) + 1
            if n > 4000: return
            t = np.linspace(0, 1, n); xs = np.floor(ax_ + (bx_ - ax_) * t).astype(int); ys = np.floor(ay_ + (by_ - ay_) * t).astype(int)
            k = (xs >= 0) & (xs < pw) & (ys >= 0) & (ys < ph)
            if dash: k &= (np.arange(n) // dash) % 2 == 0
            img[ys[k], xs[k]] = col
        for a, b in zip(trail, trail[1:]): line(a, b, (150, 150, 160))
        wps, leg = c.get("wps") or [], c.get("leg", 1)
        for i in range(1, len(wps)):
            line(wps[i - 1][0], wps[i][0], (105, 70, 105) if i < leg else (255, 95, 255) if i == leg else (190, 70, 190))
        hr = math.radians(c["hdg"]); line(p, (p[0] + math.sin(hr) * 5 * u, p[1] - math.cos(hr) * 5 * u), (245, 245, 245))
        if c.get("want") is not None:
            wr = math.radians(c["want"]); line(p, (p[0] + math.sin(wr) * 8 * u, p[1] - math.cos(wr) * 8 * u), CYAN, dash=1)
        # pixels -> half-block cells
        img = np.clip(img, 0, 255).astype(np.uint8)
        top, bot = img[0::2], img[1::2]
        for r in range(h):
            fr, br, chr_, fgr, bgr = top[r], bot[r], buf.ch[y0 + r], buf.fg[y0 + r], buf.bg[y0 + r]
            for col in range(w):
                chr_[x0 + col] = "▀"; fgr[x0 + col] = tuple(int(v) for v in fr[col]); bgr[x0 + col] = tuple(int(v) for v in br[col])
        marks, labels = {}, []

        def mark(q, s, fg, label=None, lfg=None):
            px, py = P(q); col, row = int(px), int(py) // 2
            if not (0 <= col < w and 0 <= row < h): return False
            marks[(col, row)] = (s, fg)
            if label: labels.append((col + 1, row, label, lfg or fg))
            return True
        nxt = c.get("next_wp")
        for i, (q, name) in enumerate(wps):
            if name: mark(q, "o", (255, 120, 255) if i >= leg else (140, 90, 140), name[:14] if i == nxt else None, WHITE)
        for k, g in enumerate(c.get("gw") or []):
            mark(g, str(k + 1), YEL if k == c.get("sel") else CYAN)
        for t in c.get("targets", []):
            mark((t["x"], t["y"]), "*", YEL, {"primary": "PRI", "secondary": "SEC"}.get(t["role"].rstrip("?"), ""))
        lab = self.ZOOMS[self.zoom] <= 2 and not self.fit
        for s in c.get("sites", []):
            col = DIM if s["dead"] else RED if s["margin"] > 0 else ORG if s["alert"] else YEL if s["on"] else (200, 200, 200)
            mark((s["x"], s["y"]), "x" if s["dead"] else "^", col, (s.get("system") or "")[:6] if lab and not s["dead"] else None)
        if "a" in ov:
            for a in c.get("aircraft", []):
                fr_ = a["friendly"]
                col = BLUE if fr_ else RED if a["alert"] or a["margin"] > 0 else ORG
                mark((a["x"], a["y"]), a["name"][:1], col, f"{a['name'][1:]}{' a' + str(a['alert']) if a['alert'] else ''}" if lab else None)
        if c.get("aim"): mark(c["aim"], "+", CYAN)
        mark(p, "↑↗→↘↓↙←↖"[int(((c["hdg"] + 22.5) % 360) // 45)], WHITE)
        bgc = lambda col, row: tuple(int(v * 0.6) for v in img[min(ph - 1, row * 2 + 1), col])
        taken = set(marks)
        for col, row, text, fg in labels:                  # labels only where no marker or earlier label is
            n = 0
            while n < len(text) and col + n < w and (col + n, row) not in taken: n += 1
            if n >= 2:
                for k in range(n): buf.put(x0 + col + k, y0 + row, text[k], fg, bgc(col + k, row)); taken.add((col + k, row))
        for (col, row), (ch_, fg) in marks.items(): buf.put(x0 + col, y0 + row, ch_, fg, bgc(col, row))
        # scale bar and north
        km = self.u / K * min(12, w // 3); nice = next((v for v in (1, 2, 5, 10, 20, 50, 100, 200) if v >= km * 0.6), 200)
        buf.put(x0 + 1, y0 + h - 1, "├" + "─" * max(1, int(nice * K / self.u) - 1) + f"┤ {nice} km", WHITE, (0, 0, 0))
        buf.put(x0 + 1, y0, "N↑", WHITE, (0, 0, 0))
        if "p" in ov and w > 40:
            x = x0 + w - 33; x += buf.put(x, y0 + h - 1, " radar power ", WHITE, (0, 0, 0))
            for col, lab in zip(POWER_COLOURS, ("x0", "x½", "x1", "x1½")): x += buf.put(x, y0 + h - 1, f"█{lab} ", col, (45, 45, 50))

    def _detection(self, GX, GY, G, sites, c, key):
        """Worst margin (return + EMV - 100) over the live sites at every map point for the current state; cached for
        2 s of frame time (nav_tui: 2 s wall time) while the key (view, alt / 50, hdg / 6, kt / 10, EMV, opp) holds."""
        k = key + (int(c["alt"]) // 50, int(c["hdg"]) // 6, int(c["kt"]) // 10, c["emv"], c["opp"], len(sites))
        now = c.get("now") or 0.0
        if self._det[0] == k and self._det_t is not None and 0 <= now - self._det_t < 2.0: return self._det[1]
        hdg = int(c["hdg"] / 360 * 65536) & 0xFFFF
        m = np.full(GX.shape, -999, np.int64)
        for s in sites:
            r = rg.site_return_np(GX, GY, int(c["alt"]), hdg, int(c["kt"]), c["opp"], G, s["x"], s["y"], s["type"])
            m = np.maximum(m, r + c["emv"] - 100)
        self._det, self._det_t = (k, m), now
        return m
