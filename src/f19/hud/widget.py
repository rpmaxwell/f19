"""The widget interface and registry.

A widget draws one pane: `title(view)` -> spans for the top border, `draw(view, box, screen)` fills the pane's inner
box (box = its border rectangle). Text widgets implement `lines(view, w, h)` -> [line] instead (line = str or
[(text, fg)]); `clip(lines, h)` picks the rows shown (the first h; the log keeps the last h). Size hints (inner cells)
are for layout authors and a future auto-layout: min_w / min_h, pref_w / pref_h (None = no preference).
`keys` lists the keys the widget handles in `handle_key(ch)` (map overlays, zoom); the HUD offers every key to every
widget of the layout set first, then handles Tab / z / l / ? itself.
"""
from f19.hud.screen import FG, GRN

REGISTRY = {}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


class Widget:
    name = "widget"
    min_w, min_h = 10, 1
    pref_w = pref_h = None
    keys = ""

    def __init__(self, ctx):
        self.ctx = ctx

    def title(self, view):
        return [(f" {self.name} ", GRN), (self.title_extra(view), FG)]

    def title_extra(self, view): return ""

    def draw(self, view, box, screen):
        raise NotImplementedError

    def handle_key(self, ch):
        return False

    def status(self):
        """Text for the status bar (none by default)."""
        return ""


class TextWidget(Widget):
    def lines(self, view, w, h):
        return []

    def clip(self, lines, h):
        return lines[:h]

    def draw(self, view, box, screen):
        iw, ih = box.inner_w, box.inner_h
        for i, ln in enumerate(self.clip(self.lines(view, iw, ih), ih)):
            screen.spans(box.x0 + 2, box.y0 + 1 + i, ln, iw - 1)


def create(names, ctx, registry=None):
    """{name: widget instance} for the widget names a layout set uses (unknown names raise KeyError)."""
    reg = REGISTRY if registry is None else registry
    bad = [n for n in names if n not in reg]
    if bad: raise KeyError(f"unknown widgets {bad}: have {sorted(reg)}")
    return {n: reg[n](ctx) for n in names}
