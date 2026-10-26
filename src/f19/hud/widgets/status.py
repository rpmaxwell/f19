"""status: the bottom bar (nav_tui.UI._status): the panes (focus *, zoomed Z), time of day, alt / kt / hdg, the
frame's clock time; read errors in front when there are any."""
import time

from f19.hud.screen import STATUS_BG, STATUS_FG
from f19.hud.widget import Widget, register


@register
class StatusBar(Widget):
    name = "status"
    min_h = 1

    def title(self, view): return []

    def draw(self, view, box, screen):
        hud, c = view.hud, view.c
        W, y = box.x1 - box.x0 + 1, box.y0
        screen.put(box.x0, y, " " * W, STATUS_FG, STATUS_BG)
        x = box.x0 + screen.put(box.x0, y, "[f19] ", STATUS_FG, STATUS_BG)
        for i, n in enumerate(hud.panes):
            s = f"{i}:{n}{'*' if n == hud.focus else ''}{'Z' if n == hud.focus and hud.zoomed else ''} "
            x += screen.put(x, y, s, (255, 255, 255) if n == hud.focus else STATUS_FG, STATUS_BG)
        now = c.get("now")
        clock = time.strftime("%H:%M:%S", time.localtime(now) if now is not None else time.localtime())
        right = f" {c['tod']}  alt {c['alt']} ft  {c['kt']} kt  hdg {c['hdg']:03.0f}  {clock} "
        if hud.history.errors: right = f" read errors: {','.join(hud.history.errors)} " + right
        screen.put(max(x, box.x0 + W - len(right)), y, right, STATUS_FG, STATUS_BG)
