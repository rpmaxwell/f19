"""The HUD: frames in, a Screen out. No terminal and no memory here (terminal.py draws the Screen; adapters make the
frames).

  hud = Hud(ctx, layout.load("default"))
  hud.update(frame)          # HudFrame (or a TelemetryFrame): histories, trail, log
  hud.handle("t")            # keys: widgets' first (map overlays / zoom), then Tab / z / l / ?
  screen = hud.render(W, H)  # the latest frame, laid out for a W x H terminal

HudSink is the telemetry-sink face of it (register on a TelemetryBus): write() updates and, at most every FRAME_S,
draws to a Terminal, never raising into the caller.
"""
import re
import time

import numpy as np

from f19.hud import layout as L
from f19.hud import widgets as _widgets  # noqa: F401  (registers them)
from f19.hud.data import History, HudFrame
from f19.hud.layout import Box, draw_borders
from f19.hud.screen import CYAN, DIM, GRN, RED, YEL, Screen
from f19.hud.view import View, build
from f19.hud.widget import create
from f19.rules import radar as rg
from f19.telemetry.writer import SinkStats


class Hud:
    STATUS = "status"

    def __init__(self, ctx, layouts=None, history_period=1.0, registry=None):
        self.ctx = ctx
        self.layouts = layouts if layouts is not None else L.load()
        self.grid = np.asarray(ctx.grid).astype(np.int64) if ctx.grid is not None else None
        self.names = ctx.names
        self.specs = {t: rg.aircraft_spec(t) for t in range(12)}
        self.history = History(history_period)
        names = list(dict.fromkeys(self.layouts.widgets() + list(self.layouts.panes)
                                   + ([self.STATUS] if self.layouts.status_bar else [])))
        self.widgets = create(names, ctx, registry)
        self.panes = list(self.layouts.panes)
        self.focus = self.panes[0] if self.panes else None
        self.zoomed, self.layout_index, self.help = False, 0, False
        self.frame = None

    # --- data
    def update(self, fr):
        """Take one frame: a HudFrame, or a TelemetryFrame (converted with HudFrame.from_telemetry; frames without
        a GameState, e.g. a lone event, only feed the histories)."""
        if not isinstance(fr, HudFrame): fr = HudFrame.from_telemetry(fr)
        self.history.poll(fr)
        p = fr.view.get("p") or (fr.state.xy if fr.state is not None else None)
        if p is None: return
        self.history.add_trail(p)
        self._collect_log(fr)
        self.frame = fr

    def _collect_log(self, fr):
        h = self.history
        pl = fr.view.get("phase_log") or []
        for m in pl[h._phase_n:]:
            mt = re.match(r"(\d{9,})\s+(.*)", m)
            t_ = float(mt.group(1)) if mt else fr.t
            h.log.append((t_, mt.group(2) if mt else m, YEL))
        h._phase_n = len(pl)
        for clk, x, y, typ, flg, tg in h.new_events:
            txt = self.event_text(typ, flg, tg)
            if txt:
                s = clk * 2; h.log.append((fr.t, f"[game {s // 3600:d}:{s // 60 % 60:02d}:{s % 60:02d}] {txt[0]}", txt[1]))
        h.new_events = []
        h.log = h.log[-h.LOG_MAX:]

    def event_text(self, typ, flg, tg):
        """(text, colour) of a game event record, None for the ones not logged (samples, ...)."""
        role = " PRIMARY" if flg & 0x80 else " SECONDARY" if flg & 0x40 else ""
        if typ == 5: return f"HIT by {rg.weapon_name(tg)}", RED
        if typ == 6: return f"VISUAL ID by {self.specs.get(tg, {}).get('name', tg)} (-20 base)", RED
        if typ == 7: return f"RADAR ID by {self.names.get(tg, {}).get('place', f'record {tg}')} (-10 base)", RED
        if typ == 10: return f"photo{role}", GRN
        if typ == 11: return f"objective done{role}", GRN
        if typ in (12, 2): return f"ground kill{role} (class {tg})", GRN
        if typ == 3: return f"air kill{role}", GRN
        if typ == 4: return f"weapon launch (station {tg})", CYAN
        if typ == 8: return "takeoff / end", DIM
        return None

    # --- keys
    def handle(self, keys):
        for ch in keys:
            if any(w.handle_key(ch) for w in self.widgets.values() if ch in w.keys): continue
            if ch == "\t" and self.panes:
                self.focus = self.panes[(self.panes.index(self.focus) + 1) % len(self.panes)] if self.focus in self.panes else self.panes[0]
            elif ch == "z": self.zoomed = not self.zoomed
            elif ch == "l": self.layout_index = (self.layout_index + 1) % len(self.layouts.layouts)
            elif ch == "?": self.help = not self.help

    # --- drawing
    def rects(self, W, H):
        return self.layouts.rects(self.layout_index, W, H, self.focus if self.zoomed else None)

    def view(self):
        return View(build(self, self.frame), self) if self.frame is not None else None

    def render(self, W, H):
        """The Screen for the latest frame (None before the first frame with a position)."""
        v = self.view()
        if v is None: return None
        scr = Screen(W, H)
        rects = self.rects(W, H)
        draw_borders(scr, rects, self.focus if len(rects) > 1 else None)
        for name, box in rects.items():
            w = self.widgets[name]
            scr.spans(box.x0 + 2, box.y0, w.title(v), box.x1 - box.x0 - 3)
            w.draw(v, box, scr)
        if self.layouts.status_bar: self.widgets[self.STATUS].draw(v, Box(0, H - 1, W - 1, H - 1), scr)
        return scr


class HudSink:
    """A TelemetrySink that drives a Hud on a terminal: write() = update, then draw if FRAME_S has passed (real time;
    keys are read each write). A failure turns the display off (counted, never raised): the caller keeps flying."""
    FRAME_S = 0.2

    def __init__(self, hud, terminal, clock=time.monotonic):
        self.hud, self.term, self.clock = hud, terminal, clock
        self.stats = SinkStats()
        self.last = None
        self.failed = None
        self.skipped = 0                          # frames taken but not drawn (inside FRAME_S)

    def write(self, frame, force=False):
        if self.failed is not None: self.stats.inc("dropped_after_error"); return
        try:
            self.hud.update(frame)
            self.hud.handle(self.term.keys())
            now = self.clock()
            if not force and self.last is not None and now - self.last < self.FRAME_S: self.skipped += 1; return
            self.last = now
            W, H = self.term.size()
            scr = self.hud.render(W, H)
            if scr is not None: self.term.draw(scr); self.stats.inc("written")
        except Exception as e:                    # noqa: BLE001 - a display failure must never stop the autopilot
            self.failed = repr(e); self.stats.error(e); self.term.close()

    def flush(self, timeout=None): return True

    def close(self, timeout=10.0):
        self.term.close(); return True
