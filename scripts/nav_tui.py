"""tmux-style live display for nav.py: tiled panes around a moving map.

nav.py starts it on its own when stdout is a terminal (--plain: the old one-column text). Panes:
  map       route (flown legs dim), flown track, heading vector, game waypoints 1-4, targets, radar sites,
            aircraft; toggle-able overlays (below)
  nav       phase, banners, steering, leg, altitude / speed / throttle, territory
  radars    per site: alert level and detection margin against the plane right now
  aircraft  nearest aircraft: type, distance, radar (pulse / doppler), alert on us, detection margin
  fuel      gauge, burn (model and measured), endurance, fuel left at landing along the rest of the route
  score     the END.EXE rating so far from the game's own event list (analysis/planner/score.py)
  log       nav's mission log and the game's events (hits, IDs, photos, kills)
Keys (no Enter): t territory  p radar power (cell clutter: sea x1.5 .. mountains 0)  d detection zones
  c SAM rings  s SA-14 cells  a aircraft  g 32 km grid   + / - zoom  f fit route / follow plane
  Tab focus pane  z zoom focused pane (as tmux prefix-z)  l layout  ? help
Detection margin = the game's return + EMV - 100 (radar_game.site_return with the live EMV ds:551C, the player's
cell clutter and the opponent level): > 0 means that radar's next pulse sees the plane (when the site is switched
on: duty flag, the `on` column). Detection zones colour every map point by the worst margin a plane flying there
with the current altitude / speed / heading / EMV would get from any live site.
The extra game state is read about once a second (one pipelined PEEK batch per item), so the control loop isn't starved.

  nav_tui.py --replay logs/memlog/<id> [--route route.json] [--speed 4]   play a memlog flight in the display
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import json, math, os, re, select, shutil, struct, sys, time
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "analysis"))
import f19_state as fs
import radar_game as rg
from fuel_model import burn_step, STEPS_PER_MIN
from territory import grid as tgrid
from f19.rules.scoring import estimate as score_estimate
from read_mission import RADAR as RADAR_SYSTEMS        # radar type -> (system, P/D, missile range km)

K = 64.0                       # waypoint units per km
GROUND_PER_KT = 0.788          # map km per game s per indicated kt / (1.852 / 3600)  (planner/world.py)
EXTRA_TANK_LB = 1900          # per Extra Fuel tank (EGAME 0x7ae6; planner/fuelplan.TANK_LB)
OPP_NAMES = ["green", "regular", "veteran", "elite"]
CLUTTER = {0: (0.0, "mountains"), 1: (0.5, "rough"), 2: (1.0, "flat"), 3: (1.5, "sea")}   # territory grid bits 0x0C

BG, FG, DIM = (12, 14, 18), (205, 208, 212), (115, 118, 125)
WHITE, RED, ORG, YEL, GRN = (240, 240, 240), (240, 75, 65), (245, 150, 45), (235, 205, 70), (95, 210, 100)
CYAN, BLUE, MAG = (85, 200, 225), (95, 145, 245), (225, 95, 225)
POWER_COLOURS = [(15, 15, 20), (40, 80, 120), (70, 150, 215), (190, 235, 255)]   # x0 mountains .. x1.5 sea
BORDER, BORDER_ON, STATUS_BG, STATUS_FG = (75, 80, 88), (95, 210, 100), (0, 120, 50), (10, 10, 10)


def game_dist(dx, dy):
    ax, ay = abs(dx), abs(dy); return max(ax, ay) + min(ax, ay) / 2

def bearing(a, b): return math.degrees(math.atan2(b[0] - a[0], -(b[1] - a[1]))) % 360

def wrap(d): return (d + 180) % 360 - 180


# ---------------------------------------------------------------- cell buffer
class Buf:
    """Character cells (char, fg, bg) for one frame; emit() writes them with as few colour changes as it can."""
    def __init__(self, w, h):
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.fg = [[FG] * w for _ in range(h)]
        self.bg = [[BG] * w for _ in range(h)]
    def put(self, x, y, s, fg=None, bg=None, maxw=None):
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


# box-drawing by neighbour bits, so shared pane borders join like tmux's (├ ┬ ┼ ...)
U, D, L_, R_ = 1, 2, 4, 8
BOX = {U | D: "│", L_ | R_: "─", D | R_: "┌", D | L_: "┐", U | R_: "└", U | L_: "┘", U | D | R_: "├", U | D | L_: "┤",
       L_ | R_ | D: "┬", L_ | R_ | U: "┴", U | D | L_ | R_: "┼", U: "│", D: "│", L_: "─", R_: "─"}

def draw_borders(buf, rects, active=None):
    bits, colour = {}, {}
    def mark(x, y, b, on):
        bits[(x, y)] = bits.get((x, y), 0) | b
        if on: colour[(x, y)] = BORDER_ON
    for name, (x0, y0, x1, y1) in rects.items():
        on = name == active
        for x in range(x0, x1 + 1):
            for y in (y0, y1):
                mark(x, y, (L_ if x > x0 else 0) | (R_ if x < x1 else 0), on)
        for y in range(y0, y1 + 1):
            for x in (x0, x1):
                mark(x, y, (U if y > y0 else 0) | (D if y < y1 else 0), on)
    for (x, y), b in bits.items():
        if b: buf.put(x, y, BOX.get(b, "┼"), colour.get((x, y), BORDER), BG)


def stack(x0, x1, y0, y1, items):
    """Panes stacked top to bottom between borders y0..y1 sharing borders: items [(name, want rows, min rows)],
    want None = takes what's left. -> {name: (x0, y0, x1, y1)} (border coordinates, inclusive)."""
    avail = y1 - y0 - len(items)
    h = {n: (w if w is not None else m) for n, w, m in items}
    mins = {n: m for n, _, m in items}
    while sum(h.values()) > avail and any(h[n] > mins[n] for n in h):
        n = max(h, key=lambda n: (h[n] - mins[n], n != "map")); h[n] -= 1
    while sum(h.values()) > avail and len(h) > 1:                  # still too tall: drop the last pane
        last = [n for n, _, _ in items if n in h][-1]; h.pop(last)
        avail = y1 - y0 - len(h)
    flex = [n for n, w, _ in items if w is None and n in h] or [[n for n, _, _ in items if n in h][-1]]
    extra = avail - sum(h.values())
    for i, n in enumerate(flex): h[n] += extra // len(flex) + (1 if i < extra % len(flex) else 0)
    out, y = {}, y0
    for n, _, _ in items:
        if n not in h: continue
        out[n] = (x0, y, x1, y + h[n] + 1); y += h[n] + 1
    return out


# ---------------------------------------------------------------- telemetry (what the control loop doesn't read)
class Telemetry:
    PERIOD = 1.0
    def __init__(self):
        self.t = 0.0; self.status = None; self.flight = None; self.sites = []; self.aircraft = []
        self.n_events, self.events, self.new_events = 0, [], []
        self.fuel_hist, self.clock_hist = [], []        # (clock, fuel), (real t, clock)
        self.err = {}
    def _try(self, name, fn):
        try:
            v = fn(); self.err.pop(name, None); return v
        except Exception as e:                           # a failed read must never stop the autopilot
            self.err[name] = str(e)[:60]; return None
    def poll(self, db, now, flight=None, force=False):
        if not force and now - self.t < self.PERIOD: return
        self.t = now
        st = self._try("status", lambda: fs.read_status(db))
        if st: self.status = st
        fl = flight or self._try("flight", lambda: fs.read_flight(db))
        if fl: self.flight = fl
        si = self._try("sites", lambda: fs.read_site_records(db))
        if si is not None: self.sites = si
        ac = self._try("aircraft", lambda: fs.read_aircraft(db))
        if ac is not None: self.aircraft = ac
        ev = self._try("events", lambda: fs.read_event_list(db, self.n_events))
        if ev is not None:
            n, recs = ev
            if n < self.n_events: self.events, self.n_events = [], 0; ev = self._try("events", lambda: fs.read_event_list(db, 0)); n, recs = ev or (0, [])
            self.events += recs; self.new_events += recs; self.n_events = n
        if self.status:
            c, f = self.status["clock"], self.status["fuel"]
            if self.fuel_hist and c < self.fuel_hist[-1][0]: self.fuel_hist, self.clock_hist = [], []   # new flight
            self.fuel_hist.append((c, f)); self.fuel_hist = [h for h in self.fuel_hist if h[0] >= c - 90]   # 3 game min
            self.clock_hist.append((now, c)); self.clock_hist = [h for h in self.clock_hist if h[0] >= now - 30]
    def burn_measured(self):
        """lb per game minute over the last ~3 game min (None until 1 game min of data; tank transfers reset it)."""
        h = self.fuel_hist
        for i in range(1, len(h)):
            if h[i][1] > h[i - 1][1] + 50: h[:] = h[i:]; break   # Extra Fuel transfer: start again
        if len(h) < 2 or h[-1][0] - h[0][0] < 30: return None
        return (h[0][1] - h[-1][1]) / ((h[-1][0] - h[0][0]) * 2 / 60)
    def time_ratio(self):
        """game s per real s from the clock (1 tick = 2 game s)."""
        h = self.clock_hist
        if len(h) < 2 or h[-1][0] - h[0][0] < 3: return None
        return (h[-1][1] - h[0][1]) * 2 / (h[-1][0] - h[0][0])


# ---------------------------------------------------------------- map
class MapView:
    ZOOMS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]          # km per pixel (pixel = half a character cell)
    def __init__(self, theater, route_dir=None):
        self.th = theater
        self.grid = tgrid(theater).astype(np.int64) if theater else None
        self.H, self.step = None, 16
        if route_dir and (Path(route_dir) / "terrain.npz").exists():
            t = np.load(Path(route_dir) / "terrain.npz")
            if "height_game" in t: self.H, self.step = t["height_game"], int(t["height_step"])
        tz = S.logs / "theater" / f"{theater}_terrain.npz"
        if self.H is None and theater and tz.exists():                 # theater-wide (analysis/terrain_3d.py)
            t = np.load(tz); self.H, self.step = t["height_ft"], int(t["step_wp"])
        self.trail, self.zoom, self.fit = [], 2, False
        self._det = (None, None)

    def add_trail(self, p):
        if not self.trail or math.dist(self.trail[-1], p) > 0.25 * K: self.trail.append(p); self.trail = self.trail[-4000:]

    def render(self, buf, x0, y0, w, h, c, ov):
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
        for a, b in zip(self.trail, self.trail[1:]): line(a, b, (150, 150, 160))
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
        """Worst margin (return + EMV - 100) over the live sites at every map point for the current state; cached ~1 s."""
        k = key + (int(c["alt"]) // 50, int(c["hdg"]) // 6, int(c["kt"]) // 10, c["emv"], c["opp"], len(sites))
        if self._det[0] == k and time.time() - self._det_t < 2.0: return self._det[1]
        hdg = int(c["hdg"] / 360 * 65536) & 0xFFFF
        m = np.full(GX.shape, -999, np.int64)
        for s in sites:
            r = rg.site_return_np(GX, GY, int(c["alt"]), hdg, int(c["kt"]), c["opp"], G, s["x"], s["y"], s["type"])
            m = np.maximum(m, r + c["emv"] - 100)
        self._det, self._det_t = (k, m), time.time()
        return m


# ---------------------------------------------------------------- the display
class UI:
    FRAME_S = 0.2
    PANES = ["map", "nav", "radars", "aircraft", "fuel", "score", "log"]
    HELP = ["keys: t territory  p radar power  d detection zones  c SAM rings  s SA-14 cells  a aircraft  g grid",
            "      + - zoom  f fit route / follow  Tab focus  z zoom pane  l layout  ? help off"]

    def __init__(self, theater=None, route=None, mission=None, wps=None, out=sys.stdout):
        self.out, self.th, self.wps = out, theater, wps
        self.route = json.load(open(route)) if route else {}
        rdir = Path(route).parent if route else None
        self.mission = mission if mission is not None else (json.load(open(rdir / "mission_mem.json")) if rdir and (rdir / "mission_mem.json").exists() else {})
        self.th = self.th or self.mission.get("theater")
        self.map = MapView(self.th, rdir)
        self.tel = Telemetry()
        self.names = {t["record"]: dict(place=t["place"], system=t.get("object", "")[:6]) for t in self.mission.get("targets", []) if t.get("record") is not None}
        self.names.update({r["record"]: r for r in self.mission.get("radars", []) if "record" in r})
        tj = S.logs / "theater" / f"{self.th}.json"
        self.place_xy = {(r["x"], r["y"]): r["name"] for r in json.load(open(tj))["records"] if r["name"]} if self.th and tj.exists() else {}
        spec = self.route.get("spec", {})
        self.settings = dict(spec.get("settings", {}))
        if rdir and (rdir / "plan.json").exists():
            try: self.settings = {**json.load(open(rdir / "plan.json")).get("settings", {}), **self.settings}
            except (OSError, ValueError): pass
        prof = self.route.get("profile", {})
        self.capacity = float(prof.get("fuel_loaded_lb") or 0); self.reserve = float(prof.get("landing_reserve_lb") or 1000)
        self.overlays = set("tg"); self.focus, self.zoomed, self.layout = "map", False, 0
        self.help, self.started, self.last, self.size = False, False, 0.0, None
        self.log, self._phase_n, self.fd, self._tty = [], 0, None, None
        self.specs = {t: rg.aircraft_spec(t) for t in range(12)}

    # terminal
    def start(self):
        if self.started: return
        self.started = True
        self.out.write("\033[?1049h\033[?25l\033[2J"); self.out.flush()
        try:
            import termios, tty
            if sys.stdin.isatty():
                self.fd = sys.stdin.fileno(); self._tty = termios.tcgetattr(self.fd); tty.setcbreak(self.fd)
        except Exception: self.fd = None
        import atexit; atexit.register(self.close)
    def close(self):
        if not self.started: return
        self.started = False
        if self.fd is not None and self._tty is not None:
            try:
                import termios; termios.tcsetattr(self.fd, termios.TCSADRAIN, self._tty)
            except Exception: pass
        try: self.out.write("\033[0m\033[?25h\033[?1049l"); self.out.flush()
        except Exception: pass
    def keys(self):
        if self.fd is None: return ""
        s = ""
        while select.select([self.fd], [], [], 0)[0]:
            b = os.read(self.fd, 64)
            if not b: break
            s += b.decode("latin1")
        return s
    def handle(self, k):
        for ch in k:
            if ch in "tpdcsag": self.overlays ^= {ch}
            elif ch in "+=": self.map.zoom = max(0, self.map.zoom - 1); self.map.fit = False
            elif ch in "-_": self.map.zoom = min(len(MapView.ZOOMS) - 1, self.map.zoom + 1); self.map.fit = False
            elif ch == "f": self.map.fit = not self.map.fit
            elif ch == "\t": self.focus = self.PANES[(self.PANES.index(self.focus) + 1) % len(self.PANES)]
            elif ch == "z": self.zoomed = not self.zoomed
            elif ch == "l": self.layout = (self.layout + 1) % 2
            elif ch == "?": self.help = not self.help

    # one frame
    def frame(self, db, v):
        """v: nav's view of this cycle (see nav.py). Polls telemetry, draws at most every FRAME_S."""
        now = time.time()
        self.tel.poll(db, now, v.get("flight"))
        self.map.add_trail(v["p"])
        self._collect_log(v)
        self.handle(self.keys())
        if now - self.last < self.FRAME_S: return
        self.last = now
        self.start()
        W, H = shutil.get_terminal_size((160, 50))
        if (W, H) != self.size: self.out.write("\033[2J"); self.size = (W, H)
        c = self._context(v)
        buf = Buf(W, H)
        rects = self._layout(W, H)
        draw_borders(buf, rects, self.focus if len(rects) > 1 else None)
        for name, (x0, y0, x1, y1) in rects.items():
            title = self._title(name, c)
            buf.spans(x0 + 2, y0, title, x1 - x0 - 3)
            iw, ih = x1 - x0 - 1, y1 - y0 - 1
            if name == "map": self.map.render(buf, x0 + 1, y0 + 1, iw, ih, c, self.overlays); continue
            lines = getattr(self, "_pane_" + name)(c, iw, ih)
            if name == "log": lines = lines[-ih:]
            for i, ln in enumerate(lines[:ih]): buf.spans(x0 + 2, y0 + 1 + i, ln, iw - 1)
        self._status(buf, W, H, c)
        self.out.write(buf.emit()); self.out.flush()

    def _layout(self, W, H):
        y1 = H - 2                                           # status line below
        if self.zoomed: return {self.focus: (0, 0, W - 1, y1)}
        right = [("nav", 9, 5), ("radars", None, 3), ("aircraft", None, 2), ("fuel", 5, 3), ("score", 4, 3)]
        if W >= 110 and self.layout == 0:
            mw = max(50, int(W * 0.56))
            out = stack(0, mw, 0, y1, [("map", None, 8), ("log", 7, 3)])
            out.update(stack(mw, W - 1, 0, y1, right)); return out
        return stack(0, W - 1, 0, y1, [("map", None, 10)] + right + [("log", 5, 2)])

    def _title(self, name, c):
        on = lambda k, txt: (txt, WHITE if k in self.overlays else DIM)
        if name == "map":
            z = "fit" if self.map.fit else f"{MapView.ZOOMS[self.map.zoom]:g} km/px"
            return [(" map ", GRN), (z + " ", FG), on("t", "[t]err "), on("p", "[p]ower "), on("d", "[d]etect "), on("c", "[c]SAM "),
                    on("s", "[s]A-14 "), on("a", "[a]ir "), on("g", "[g]rid ")]
        extra = {"radars": f"EMV {c['emv']}  opp {OPP_NAMES[c['opp']] if c['opp'] < 4 else c['opp']} ",
                 "aircraft": f"{sum(1 for a in c['aircraft'] if not a['friendly'])} hostile ",
                 "score": f"{self.th or ''} ", "fuel": "", "nav": f"{c['phase']} ", "log": ""}.get(name, "")
        return [(f" {name} ", GRN), (extra, FG)]

    def _status(self, buf, W, H, c):
        y = H - 1; buf.put(0, y, " " * W, STATUS_FG, STATUS_BG)
        x = buf.put(0, y, "[f19] ", STATUS_FG, STATUS_BG)
        for i, n in enumerate(self.PANES):
            s = f"{i}:{n}{'*' if n == self.focus else ''}{'Z' if n == self.focus and self.zoomed else ''} "
            x += buf.put(x, y, s, (255, 255, 255) if n == self.focus else STATUS_FG, STATUS_BG)
        right = f" {c['tod']}  alt {c['alt']} ft  {c['kt']} kt  hdg {c['hdg']:03.0f}  {time.strftime('%H:%M:%S')} "
        if self.tel.err: right = f" read errors: {','.join(self.tel.err)} " + right
        buf.put(max(x, W - len(right)), y, right, STATUS_FG, STATUS_BG)

    # data for a frame
    def _context(self, v):
        t = self.tel; st, fl = t.status or {}, v.get("flight") or t.flight or {}
        p = v["p"]
        opp = fl.get("opp", 4)
        if not 0 <= opp <= 3: opp = OPP_NAMES.index(self.settings["opponent"]) if self.settings.get("opponent") in OPP_NAMES else 2
        alt, kt, hdg = int(v["alt"]), int(v["kt"]), float(v["hdg"])
        emv = st.get("emv")
        if emv is None or emv > 400: emv = rg.emv(alt, kt, opp, int(abs(fl.get("roll", 0)) / 360 * 65536))
        gb = int(self.map.grid[min(15, int(p[1]) >> 11), min(15, int(p[0]) >> 11)]) if self.map.grid is not None else 8
        h65 = int(hdg / 360 * 65536) & 0xFFFF
        sites = []
        for s in t.sites:
            R, P, dop = rg.radar_spec(s["type"]) if s["type"] < 23 else (0, 0, False)
            m = rg.site_return(int(p[0]), int(p[1]), alt, h65, kt, opp, gb, s["x"], s["y"], s["type"]) + emv - 100 if R else -999
            meta = self.names.get(s["record"], {})
            sy = RADAR_SYSTEMS.get(s["type"], (f"type {s['type']}", None, None))
            sites.append(dict(s, margin=m, doppler=dop, power=P, place=meta.get("place") or self.place_xy.get((s["x"], s["y"])) or f"record {s['record']}",
                              system=sy[0], range_km=meta.get("range_km") or sy[2], dist=game_dist(s["x"] - p[0], s["y"] - p[1]) / K))
        acs = []
        for a in t.aircraft:
            sp = self.specs.get(a["type"], {})
            m = -999
            if sp.get("radar_type"):
                m = rg.site_return(int(p[0]), int(p[1]), alt, h65, kt, opp, gb, a["x"], a["y"], sp["radar_type"], a["alt"]) + emv - 100
            acs.append(dict(a, margin=m, spec=sp, friendly=a["type"] in rg.FRIENDLY_TYPES, dist=math.dist(p, (a["x"], a["y"])) / K))
        start = (self.mission.get("times") or {}).get("start")
        tod = ""
        if st.get("clock") is not None:
            sec = st["clock"] * 2
            if start and re.match(r"\d+:\d+", start): sec += int(start.split(":")[0]) * 3600 + int(start.split(":")[1]) * 60
            tod = f"{sec // 3600 % 24:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"
        return dict(v, p=p, alt=alt, kt=kt, hdg=hdg, opp=opp, emv=emv, cell=gb, sites=sites, aircraft=acs, st=st, fl=fl, tod=tod,
                    wps=v.get("wps") or self.wps, targets=self.mission.get("targets", []), phase=v.get("phase", ""))

    def _collect_log(self, v):
        pl = v.get("phase_log") or []
        for m in pl[self._phase_n:]:
            mt = re.match(r"(\d{9,})\s+(.*)", m)
            t_ = float(mt.group(1)) if mt else time.time()
            self.log.append((t_, mt.group(2) if mt else m, YEL))
        self._phase_n = len(pl)
        for clk, x, y, typ, flg, tg in self.tel.new_events:
            txt = self._event_text(typ, flg, tg)
            if txt:
                s = clk * 2; self.log.append((time.time(), f"[game {s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}] {txt[0]}", txt[1]))
        self.tel.new_events = []
        self.log = self.log[-200:]

    def _event_text(self, typ, flg, tg):
        role = " PRIMARY" if flg & 0x80 else " SECONDARY" if flg & 0x40 else ""
        if typ == 5:
            nm = rg._EG[rg.DS_FILE + 0x4C10 + 0x12 * tg:][:8].split(b"\0")[0].decode("latin1")
            return f"HIT by {nm}", RED
        if typ == 6: return f"VISUAL ID by {self.specs.get(tg, {}).get('name', tg)} (-20 base)", RED
        if typ == 7: return f"RADAR ID by {self.names.get(tg, {}).get('place', f'record {tg}')} (-10 base)", RED
        if typ == 10: return f"photo{role}", GRN
        if typ == 11: return f"objective done{role}", GRN
        if typ in (12, 2): return f"ground kill{role} (class {tg})", GRN
        if typ == 3: return f"air kill{role}", GRN
        if typ == 4: return f"weapon launch (station {tg})", CYAN
        if typ == 8: return "takeoff / end", DIM
        return None

    # panes
    def _pane_nav(self, c, w, h):
        out = []
        for b in (c.get("banner") or "").split("\n"):
            if b.strip(): out.append([(b.strip(), RED if "!!!" in b or "GO-AROUND" in b else YEL)])
        holds = (f"alt hold {c['hold_alt']:.0f}  " if c.get("hold_alt") is not None else "") + ("HDG HOLD  " if c.get("hdg_hold") else "")
        ts = c.get("ts", 1.0); tr = self.tel.time_ratio()
        out.append([(holds, CYAN), (f"TIME x{ts:.0f}  " if ts > 1.3 else "", ORG), (f"game {tr:.1f}x real" if tr else "", DIM)])
        if c.get("wps"):
            turn = c.get("turn", 0.0)
            arrow = "<<< LEFT" if turn < -3 else "RIGHT >>>" if turn > 3 else "on course"
            out.append([("FLY ", FG), (f"{c.get('want', 0):03.0f}", WHITE), (f"  now {c['hdg']:03.0f}  ", FG), (f"{arrow} {abs(turn):.0f}", YEL if abs(turn) > 3 else GRN)])
            out.append(f"leg {c.get('leg')}/{len(c['wps']) - 1}  turn in {c.get('to_turn_km', 0):.1f} km  off route {abs(c.get('xtrack_km', 0)):.1f} km "
                       f"{'R' if c.get('xtrack_km', 0) > 0 else 'L'}")
            out.append([("next: ", DIM), (c.get("next_name") or "", WHITE)])
        fl = c["fl"]; vs = c.get("vs")
        out.append([(f"alt {c['alt']:5d} ft", WHITE), (f" vs {vs:+.0f}" if vs is not None else "", FG), (f"   {c['kt']:3d} kt", WHITE),
                    (f"   thr {fl['throttle']}%" if "throttle" in fl else "", FG),
                    (f"   bank {fl['roll']:+.0f} pitch {fl['pitch']:+.0f}" if "roll" in fl else "", DIM)])
        side = c.get("territory")
        if side: out.append([("territory ", DIM), ({"enemy": "ENEMY (scoring; ejecting = capture)"}.get(side, side), RED if side == "enemy" else BLUE if side == "friendly" else FG)])
        if c.get("rate") is not None: out.append([(f"turn rate {c['rate']:+.2f} (want {c['rate_cmd']:+.2f}) {c.get('rolling') or ''}", DIM)])
        if c.get("sens_msg"): out.append([(c["sens_msg"], ORG)])
        if c.get("gw"):
            out.append([(" ".join(f"{'>' if k == c.get('sel') else ' '}{k + 1} {game_dist(g[0] - c['p'][0], g[1] - c['p'][1]) / K:.0f}km/{bearing(c['p'], g):03.0f}"
                                  for k, g in enumerate(c["gw"])), DIM)])
        return out

    def _alert_bar(self, a, n=8):
        col = RED if a > 0xC0 else ORG if a > 0x7F else YEL if a else DIM
        k = round(a / 255 * n); return [("█" * k + "░" * (n - k), col), (f" {a:3d}", col)]

    def _margin(self, m):
        if m <= -999: return [("   -  ", DIM)]
        return [(f"{m:+4d}", RED if m > 0 else ORG if m > -15 else YEL if m > -40 else GRN), (" SEEN" if m > 0 else "     ", RED)]

    def _pane_radars(self, c, w, h):
        st = c["st"]; net = st.get("net_alert", 0)
        cf, cn = CLUTTER[(c["cell"] >> 2) & 3]
        hdr = [(f"cell {cn} x{cf:g}  ", RED if cf > 1 else FG), ("network ", DIM), (f"{net}", RED if net >= 0x80 else FG),
               (" ALERT" if net >= 0x80 else "", RED), (f"  IDs R{st.get('radar_ids', 0)} V{st.get('vis_ids', 0)}", RED if st.get("radar_ids") or st.get("vis_ids") else DIM)]
        out = [hdr, [("site          sys    P/D   km/rng  brg  alert         margin    on", DIM)]]
        live = [s for s in c["sites"] if not s["dead"]]
        live.sort(key=lambda s: (-(s["margin"] > 0), -s["alert"], s["dist"]))
        for s in live[:max(0, h - 2)]:
            rng = f"{s['range_km']}" if s.get("range_km") else "-"
            out.append([(f"{s['place'][:13]:<13} {s['system'][:6]:<6} {'D' if s['doppler'] else 'P'}  {s['dist']:6.1f}/{rng:<3} {bearing(c['p'], (s['x'], s['y'])):03.0f}  ", FG)]
                       + self._alert_bar(s["alert"]) + [("  ", FG)] + self._margin(s["margin"]) + [("  *" if s["on"] else "  .", GRN if s["on"] else DIM)])
        if not live: out.append([("no radar sites read yet", DIM)])
        return out

    def _pane_aircraft(self, c, w, h):
        out = [[("type        km  brg   alt  radar       alert         margin   ", DIM)]]
        acs = sorted(c["aircraft"], key=lambda a: (a["friendly"], a["dist"]))
        for a in acs[:max(0, h - 1)]:
            sp = a["spec"]
            radar = (f"{'doppler' if sp.get('doppler') else 'pulse'} {sp.get('radar_range', 0)}" if sp.get("radar_type") else "none")
            col = BLUE if a["friendly"] else FG
            out.append([(f"{a['name'][:8]:<8} {a['dist']:6.1f}  {bearing(c['p'], (a['x'], a['y'])):03.0f} {a['alt']:5d}  {radar:<11} ", col)]
                       + (self._alert_bar(a["alert"]) + [("  ", FG)] + self._margin(a["margin"]) if not a["friendly"] else [("friendly", BLUE)])
                       + [(" VIS-ID" if a["flags"] & 0x800 else "", RED)])
        if not acs: out.append([("no aircraft in the sim slots", DIM)])
        return out

    def _pane_fuel(self, c, w, h):
        st, fl = c["st"], c["fl"]
        fuel = st.get("fuel", fl.get("fuel"))
        if fuel is None: return [[("fuel not read yet", DIM)]]
        self.capacity = max(self.capacity, fuel)
        tanks = sum(n for wpn, n in fl.get("loadout", []) if wpn == 17)
        total = fuel + tanks * EXTRA_TANK_LB
        n = max(8, min(30, w - 34)); k = round(fuel / max(self.capacity, 1) * n)
        col = RED if fuel < 1500 else YEL if fuel < 3000 else GRN
        out = [[("[", DIM), ("█" * k, col), ("░" * (n - k), DIM), ("] ", DIM), (f"{fuel:5d} lb", WHITE),
                (f"  +{tanks} tank{'s' if tanks != 1 else ''} ({tanks * EXTRA_TANK_LB})" if tanks else "", CYAN)]]
        thr = fl.get("throttle")
        leak = st.get("dmg_count", 0) if st.get("dmg_bits", 0) & 0x20 else 0
        model = burn_step(thr, leak) * STEPS_PER_MIN if thr is not None else None
        meas = self.tel.burn_measured()
        burn = meas if meas and meas > 0 else model
        out.append([(f"thr {thr}%  " if thr is not None else "", FG), ("burn ", DIM), (f"{burn:.0f} lb/min" if burn else "-", WHITE),
                    (f"  (model {model}{', measured' if meas else ''})" if model is not None else "", DIM), ("  LEAK" if leak else "", RED)])
        if burn:
            mins = total / burn; gkm = c["kt"] * 1.852 / 60 * GROUND_PER_KT; tr = self.tel.time_ratio()
            out.append([("endurance ", DIM), (f"{mins:.0f} game min", WHITE), (f" ({mins / tr:.0f} real)" if tr else "", DIM),
                        ("  range ", DIM), (f"{mins * gkm:.0f} km", WHITE), (f" at {c['kt']} kt", DIM)])
            left = c.get("route_left_km")
            if left is not None and gkm > 0:
                eta = left / gkm; at = total - eta * burn
                col = GRN if at >= self.reserve else YEL if at >= 0 else RED
                out.append([("route left ", DIM), (f"{left:.0f} km", WHITE), (f"  ETA {eta:.0f} min  ", FG), ("at landing ", DIM),
                            (f"{at:.0f} lb", col), (" OK" if at >= self.reserve else " BELOW RESERVE" if at >= 0 else " SHORT", col)])
        at = c.get("attrs") or []; leg = c.get("leg", 1); wps = c.get("wps") or []
        if 0 < leg < min(len(at), len(wps)) and "fuel_lb" in at[leg] and "fuel_lb" in at[leg - 1]:
            (ax, ay), (bx, by) = wps[leg - 1][0], wps[leg][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
            f_ = max(0.0, min(1.0, ((c["p"][0] - ax) * (bx - ax) + (c["p"][1] - ay) * (by - ay)) / L2))
            plan = at[leg - 1]["fuel_lb"] + (at[leg]["fuel_lb"] - at[leg - 1]["fuel_lb"]) * f_
            d_ = fuel - plan
            out.append([("plan here ", DIM), (f"{plan:.0f} lb", FG), (f" (you {d_:+.0f})", GRN if d_ >= -200 else YEL if d_ >= -800 else RED),
                        ("  plan landing ", DIM), (f"{at[-1].get('fuel_lb', 0):.0f}", FG), (" (internal)", DIM)])
        return out

    def _pane_score(self, c, w, h):
        ev = self.tel.events
        if not ev and not self.tel.status: return [[("event list not read yet", DIM)]]
        g = self.map.grid
        enemy = sum(1 for clk, x, y, typ, _, _ in ev if typ == 9 and g is not None and g[min(15, y >> 4), min(15, x >> 4)] & 3 == 0)
        samples = sum(1 for e in ev if e[3] == 9)
        pri = any(e[4] & 0x80 and e[3] in (1, 2, 3, 10, 11, 12) for e in ev)
        sec = any(e[4] & 0x40 and e[3] in (1, 2, 3, 10, 11, 12) for e in ev)
        vis, rad = sum(1 for e in ev if e[3] == 6), sum(1 for e in ev if e[3] == 7)
        out = [[("enemy samples ", DIM), (f"{enemy}", WHITE), (f" of {samples}", DIM)]]
        clk = c["st"].get("clock")
        last9 = max((e[0] for e in ev if e[3] == 9), default=None)
        if clk is not None and last9 is not None:
            nxt = max(0, (last9 + 32 - clk) * 2)
            here = c.get("territory") == "enemy"
            out[0] += [("   next in ", DIM), (f"{nxt:2d} s", FG), (" +1" if here else " (not enemy: 0)", GRN if here else DIM)]
        out.append([("primary ", DIM), ("DONE" if pri else "--", GRN if pri else DIM), ("  secondary ", DIM), ("DONE" if sec else "--", GRN if sec else DIM),
                    ("  IDs ", DIM), (f"radar {rad} visual {vis} ({-10 * rad - 20 * vis:+d})", RED if rad or vis else DIM)])
        if self.th in ("CE", "NC", "LB", "PG"):
            s = dict(self.settings, opponent=OPP_NAMES[c["opp"]])
            try:
                (b0, b1), (s0, s1), assumed = score_estimate([enemy], self.th, s, pri, sec, id_penalty=10 * rad + 20 * vis)
                _, (n0, n1), _ = score_estimate([enemy + 1], self.th, s, pri, sec, id_penalty=10 * rad + 20 * vis)
                out.append([("base ", DIM), (f"{b0}", WHITE), ("  score ", DIM), (f"{s0}" if s0 == s1 else f"{s0}-{s1}", GRN),
                            (f"   +{(n1 - s1 + n0 - s0) / 2:.1f}/sample", DIM)])
                if assumed: out.append([("? " + "; ".join(a.split(":")[0] for a in assumed), DIM)])
            except Exception as e: out.append([(f"score: {e}", DIM)])
        return out

    def _pane_log(self, c, w, h):
        out = [[(time.strftime("%H:%M:%S ", time.localtime(t_)), DIM), (m, col)] for t_, m, col in self.log]
        if self.help: out += [[(l, CYAN)] for l in self.HELP]
        return out or [[("? for keys", DIM)]]


# ---------------------------------------------------------------- memlog replay (test the display without the game)
class SnapDB:
    """A stand-in for the automation connection that answers PEEKs from one memlog snapshot."""
    def __init__(self, windows): self.windows, self.raw, self.q = windows, b"", []
    def load(self, raw): self.raw = raw
    def _read(self, phys, n):
        out, pos = bytearray(n), 0
        for _, lo, hi in self.windows:
            if lo <= phys < hi or lo < phys + n <= hi:
                a, b = max(lo, phys), min(hi, phys + n)
                out[a - phys:b - phys] = self.raw[pos + a - lo:pos + b - lo]
            pos += hi - lo
        return bytes(out)
    def _send(self, cmd):
        _, addr, n = cmd.split(); seg, off = addr.split(":")
        self.q.append("PEEK-REPLY " + self._read(int(seg, 16) * 16 + int(off, 16), int(n)).hex())
    def _recv_line(self): return self.q.pop(0)
    def peek(self, seg, off, n): return self._read(int(seg, 16) * 16 + int(off, 16), n)


def replay(d, route=None, speed=4.0):
    d = Path(d); win = json.load(open(d / "windows.json"))
    windows = [tuple(w) for w in win["windows"]]
    th = next((n.split("_", 1)[1] for n, _, _ in windows if n.startswith("world_")), None)
    th = {"LIBYA": "LB", "GULF": "PG"}.get(th, th)
    snaps = sorted(d.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))
    db = SnapDB(windows)
    wps = None
    if route:
        r = json.load(open(route)); wps = [((q["x"], q["y"]), q["label"]) for q in r.get("points", [])] or None
    ui = UI(th, route, wps=wps)
    ui.tel.PERIOD = 0.0
    try:
        t0 = float(snaps[0].stem[5:]) if snaps else 0
        for i, f in enumerate(snaps):
            db.load(f.read_bytes()); fs._offset.clear()
            try: s = fs.read_state(db, tries=1)
            except RuntimeError: continue
            if not s["x"]: continue
            p = (s["x"] / 32, s["y"] / 32)
            leg = 1
            if wps:
                leg = min(range(1, len(wps)), key=lambda j: math.dist(p, wps[j][0]) + math.dist(p, wps[j - 1][0]) - math.dist(wps[j][0], wps[j - 1][0]))
            gw, sel = fs.read_waypoints(db)
            v = dict(p=p, alt=s["alt"], kt=s["spd"], hdg=s["hdg"], phase="replay", leg=leg, wps=wps, gw=gw, sel=sel,
                     want=bearing(p, wps[leg][0]) if wps else None, banner=f"REPLAY {d.name}  snapshot {i + 1}/{len(snaps)}",
                     territory=__import__("territory").side(th, *p) if th else None,
                     route_left_km=(math.dist(p, wps[leg][0]) + sum(math.dist(wps[j][0], wps[j + 1][0]) for j in range(leg, len(wps) - 1))) / K if wps else None,
                     next_name=wps[leg][1] if wps else None, next_wp=leg)
            ui.last = 0; ui.frame(db, v)
            if i + 1 < len(snaps): time.sleep(max(0.0, (float(snaps[i + 1].stem[5:]) - float(f.stem[5:])) / speed))
        time.sleep(2)
    except KeyboardInterrupt: pass
    finally: ui.close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", required=True); ap.add_argument("--route"); ap.add_argument("--speed", type=float, default=4.0)
    a = ap.parse_args(); replay(a.replay, a.route, a.speed)
