"""Render the replay fixtures with the old HUD (scripts/nav_tui.py, via legacy_tui) and the new one (f19.hud with
default.toml, frames from f19.cli.hud's adapter) at the same size and compare them pane by pane: the text of each
pane's rectangle (border, title, contents) and its colours (fg, bg per cell), and the status line."""
import json
from dataclasses import dataclass, field
from pathlib import Path

import legacy_tui  # noqa: F401  (sys.path: src, analysis, scripts)

from f19.cli import hud as cli
from f19.hud import Hud
from f19.hud import layout as L


@dataclass
class Result:
    run: str
    size: tuple
    frames: int = 0
    panes: dict = field(default_factory=dict)          # pane -> [frames compared, text equal, colours equal]
    layout_equal: int = 0
    diffs: list = field(default_factory=list)          # first few (frame, pane, kind, old, new)

    def add(self, pane, text_ok, col_ok):
        p = self.panes.setdefault(pane, [0, 0, 0]); p[0] += 1; p[1] += text_ok; p[2] += col_ok

    @property
    def all_equal(self):
        return self.layout_equal == self.frames and all(n == t == c for n, t, c in self.panes.values())

    def summary(self):
        rows = [f"{self.run} {self.size[0]}x{self.size[1]}: {self.frames} frames, layout {self.layout_equal}/{self.frames}"]
        rows += [f"  {p:<9} text {t}/{n}  colours {c}/{n}" for p, (n, t, c) in self.panes.items()]
        return "\n".join(rows)


def fixture_runs(fixtures):
    """[(memlog dir, route.json)] of the replay fixtures (manifest.json)."""
    f = Path(fixtures)
    man = json.loads((f / "manifest.json").read_text())
    out = []
    for run, m in man.items():
        r = f / "plan" / m["plan"] / "route.json"
        out.append((f / "logs" / "memlog" / run, r if r.exists() else None))
    return out


def compare(run_dir, route, size=(160, 50), every=1, keys=None, max_diffs=8, scan=False):
    """Feed every snapshot to both HUDs, render every `every`-th one in both and compare. keys {i: str}; scan: the old
    HUD finds the data segment by scanning each snapshot, as its replay() does (slow), else from windows.json."""
    W, H = size
    res = Result(Path(run_dir).name, size)
    route = str(route) if route else None
    old = legacy_tui.replay_frames(run_dir, route, size, render=lambda i: i % every == 0, keys=keys, scan=scan)
    ctx = cli.load_context(cli.theater_of_windows(legacy_tui.snapshots(run_dir)[0]), route, cli.route_points(route))
    hud = Hud(ctx, L.load("default"), history_period=0.0)
    new = cli.replay_frames(run_dir, route, None, ctx)
    for (i, t, buf, ui), (j, fr) in zip(old, new, strict=True):
        assert i == j and t == fr.t, f"snapshot order differs: old {i} {t}, new {j} {fr.t}"
        if keys and i in keys: hud.handle(keys[i])
        hud.update(fr)
        if buf is None: continue
        scr = hud.render(W, H)
        res.frames += 1
        rects = hud.rects(W, H)
        old_rects = ui._layout(W, H)
        res.layout_equal += {k: b.as_tuple() for k, b in rects.items()} == old_rects
        regions = [(k, b.as_tuple()) for k, b in rects.items()] + [("status", (0, H - 1, W - 1, H - 1))]
        for name, (x0, y0, x1, y1) in regions:
            ot = ["".join(buf.ch[y][x0:x1 + 1]) for y in range(y0, y1 + 1)]
            nt = scr.text(x0, y0, x1, y1)
            oc = [list(zip(buf.fg[y][x0:x1 + 1], buf.bg[y][x0:x1 + 1])) for y in range(y0, y1 + 1)]
            nc = scr.colours(x0, y0, x1, y1)
            res.add(name, ot == nt, oc == nc)
            if len(res.diffs) < max_diffs:
                if ot != nt:
                    k = next(r for r in range(len(ot)) if ot[r] != nt[r])
                    res.diffs.append((i, name, "text", ot[k], nt[k]))
                elif oc != nc:
                    k = next(r for r in range(len(oc)) if oc[r] != nc[r])
                    x = next(c for c in range(len(oc[k])) if oc[k][c] != nc[k][c])
                    res.diffs.append((i, name, f"colour row {k} col {x}", oc[k][x], nc[k][x]))
    return res
