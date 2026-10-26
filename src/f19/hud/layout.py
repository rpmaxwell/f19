"""The layout engine: tiled panes from a TOML layout file (stdlib tomllib), tmux-style shared borders.

A layout file holds one or more layouts; `l` in flight cycles through them (and `cycle = "dir"` also through every
other file in the same directory). A layout is a tree: a leaf names a widget, an inner node splits its box into
`rows` (top to bottom) or `columns` (left to right) that share their borders.

  name = "default"                       # optional; the file name otherwise
  panes = ["map", "nav", ...]            # optional: Tab / status-bar order (default: the first layout's widgets)
  cycle = "file"                         # "file" (default) or "dir"
  status_bar = true                      # the bottom row is the status bar (default true)

  [[layout]]
  name = "two-column"
  min_width = 110                        # narrower terminals use the fallback layout
  fallback = "one-column"
  columns = [
    { size = "56%", min = 49, rows = [{ widget = "map", min = 8, keep = true }, { widget = "log", size = 7, min = 3 }] },
    { rows = [{ widget = "nav", size = 9, min = 5 }, { widget = "radars", min = 3 }] },
  ]

Node keys: `widget` (leaf) or `rows` / `columns` (children); `size` = wanted inner cells along the parent's axis
(int), or a share of the parent ("56%": inner = max(min, int(span * 0.56) - 1), span = the parent's extent in cells,
borders included); no size = flexible (shares what is left); `min` = fewest inner cells (default 1); `keep = true`
= shrunk last when the box is too small (the old HUD's map). Too small even at the minimums: trailing panes are
dropped. The algorithm is scripts/nav_tui.py's stack(), applied along either axis.
"""
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LAYOUTS_DIR = Path(__file__).resolve().parent / "layouts"
DEFAULT = "default"


@dataclass(frozen=True, slots=True)
class Box:
    """A pane's border rectangle (inclusive cell coordinates)."""
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def inner_w(self): return self.x1 - self.x0 - 1

    @property
    def inner_h(self): return self.y1 - self.y0 - 1

    def as_tuple(self): return self.x0, self.y0, self.x1, self.y1


def stack(lo, hi, items):
    """Panes along one axis between borders lo..hi sharing borders: items [(key, want, min, keep)], want None = takes
    what is left. -> {key: (a, b)} border coordinates (inclusive). nav_tui.stack(): shrink the pane with the most
    slack (non-keep first) down to the minimums, then drop trailing panes, then share the rest among the flexible
    panes (or the last one)."""
    avail = hi - lo - len(items)
    h = {k: (w if w is not None else m) for k, w, m, _ in items}
    mins = {k: m for k, _, m, _ in items}
    keep = {k: kp for k, _, _, kp in items}
    while sum(h.values()) > avail and any(h[k] > mins[k] for k in h):
        k = max(h, key=lambda k: (h[k] - mins[k], not keep[k])); h[k] -= 1
    while sum(h.values()) > avail and len(h) > 1:                  # still too big: drop the last pane
        last = [k for k, _, _, _ in items if k in h][-1]; h.pop(last)
        avail = hi - lo - len(h)
    flex = [k for k, w, _, _ in items if w is None and k in h] or [[k for k, _, _, _ in items if k in h][-1]]
    extra = avail - sum(h.values())
    for i, k in enumerate(flex): h[k] += extra // len(flex) + (1 if i < extra % len(flex) else 0)
    out, a = {}, lo
    for k, _, _, _ in items:
        if k not in h: continue
        out[k] = (a, a + h[k] + 1); a += h[k] + 1
    return out


@dataclass(frozen=True)
class Node:
    widget: str | None = None
    axis: str | None = None              # "rows" | "columns" for inner nodes
    children: tuple = ()
    size: int | None = None
    share: float | None = None           # "56%" -> 0.56
    min: int = 1
    keep: bool = False

    @classmethod
    def parse(cls, d, where="layout"):
        if not isinstance(d, dict): raise ValueError(f"{where}: a node is a table, got {d!r}")
        kinds = [k for k in ("widget", "rows", "columns") if k in d]
        if len(kinds) != 1: raise ValueError(f"{where}: a node needs exactly one of widget / rows / columns, got {sorted(d)}")
        unknown = set(d) - {"widget", "rows", "columns", "size", "min", "keep"}
        if unknown: raise ValueError(f"{where}: unknown keys {sorted(unknown)}")
        size, share = d.get("size"), None
        if isinstance(size, str):
            if not size.endswith("%"): raise ValueError(f"{where}: size {size!r}: an int or a percentage like \"56%\"")
            share, size = float(size[:-1]) / 100, None
        elif size is not None and (not isinstance(size, int) or size < 0): raise ValueError(f"{where}: bad size {size!r}")
        mn = d.get("min", 1)
        if not isinstance(mn, int) or mn < 0: raise ValueError(f"{where}: bad min {mn!r}")
        kind = kinds[0]
        if kind == "widget":
            return cls(widget=str(d["widget"]), size=size, share=share, min=mn, keep=bool(d.get("keep", False)))
        kids = d[kind]
        if not isinstance(kids, list) or not kids: raise ValueError(f"{where}: {kind} must be a non-empty list")
        return cls(axis=kind, children=tuple(cls.parse(c, f"{where}.{kind}[{i}]") for i, c in enumerate(kids)),
                   size=size, share=share, min=mn, keep=bool(d.get("keep", False)))

    def widgets(self):
        if self.widget is not None: return [self.widget]
        return [w for c in self.children for w in c.widgets()]

    def place(self, box, out):
        """Leaves' boxes into out {widget: Box}, in tree order."""
        if self.widget is not None: out[self.widget] = box; return out
        rows = self.axis == "rows"
        lo, hi = (box.y0, box.y1) if rows else (box.x0, box.x1)
        span = hi - lo + 1
        items = []
        for i, c in enumerate(self.children):
            want = c.size if c.share is None else max(c.min, int(span * c.share) - 1)
            items.append((i, want, c.min, c.keep))
        for i, (a, b) in stack(lo, hi, items).items():
            self.children[i].place(Box(box.x0, a, box.x1, b) if rows else Box(a, box.y0, b, box.y1), out)
        return out


@dataclass(frozen=True)
class Layout:
    name: str
    root: Node
    min_width: int = 0
    min_height: int = 0
    fallback: str | None = None

    def widgets(self): return self.root.widgets()


@dataclass
class LayoutSet:
    """The layouts of one file (plus, with cycle = "dir", the other files' in its directory)."""
    name: str
    layouts: list
    panes: list
    status_bar: bool = True
    path: Path | None = None
    cycle: str = "file"
    by_name: dict = field(default_factory=dict)

    def __post_init__(self):
        self.by_name = {l.name: l for l in self.layouts}
        for l in self.layouts:
            if l.fallback is not None and l.fallback not in self.by_name:
                raise ValueError(f"layout {l.name!r}: fallback {l.fallback!r} is not in {self.name}")

    def widgets(self):
        seen = []
        for l in self.layouts:
            seen += [w for w in l.widgets() if w not in seen]
        return seen

    def resolve(self, index, W, H):
        """The layout to draw for cycle position `index` at terminal size W x H (min_width / fallback chain)."""
        lay, hops = self.layouts[index % len(self.layouts)], 0
        while (W < lay.min_width or H < lay.min_height) and lay.fallback and hops < len(self.layouts):
            lay = self.by_name[lay.fallback]; hops += 1
        return lay

    def rects(self, index, W, H, zoomed=None):
        """{widget: Box} for the frame; zoomed = a widget name drawn alone (tmux prefix-z)."""
        y1 = H - 2 if self.status_bar else H - 1
        if zoomed: return {zoomed: Box(0, 0, W - 1, y1)}
        return self.resolve(index, W, H).root.place(Box(0, 0, W - 1, y1), {})


def parse(text, name="layout", path=None):
    d = tomllib.loads(text)
    unknown = set(d) - {"name", "panes", "cycle", "status_bar", "layout"}
    if unknown: raise ValueError(f"{name}: unknown top-level keys {sorted(unknown)}")
    raw = d.get("layout")
    if not isinstance(raw, list) or not raw: raise ValueError(f"{name}: needs at least one [[layout]]")
    lays = []
    for i, ld in enumerate(raw):
        lname = str(ld.get("name", f"{d.get('name', name)}-{i}"))
        node = {k: v for k, v in ld.items() if k in ("widget", "rows", "columns")}
        extra = set(ld) - {"name", "min_width", "min_height", "fallback", "widget", "rows", "columns"}
        if extra: raise ValueError(f"{name}: layout {lname!r}: unknown keys {sorted(extra)}")
        lays.append(Layout(lname, Node.parse(node, f"{name}:{lname}"), int(ld.get("min_width", 0)),
                           int(ld.get("min_height", 0)), ld.get("fallback")))
    cycle = d.get("cycle", "file")
    if cycle not in ("file", "dir"): raise ValueError(f"{name}: cycle must be \"file\" or \"dir\"")
    ls = LayoutSet(str(d.get("name", name)), lays, [], bool(d.get("status_bar", True)), path, cycle)
    ls.panes = list(d.get("panes") or ls.widgets())
    return ls


def find(name_or_path=None, layouts_dir=LAYOUTS_DIR):
    """A layout file: a path, or a name in the layouts directory (default.toml when None)."""
    if name_or_path is None: name_or_path = DEFAULT
    p = Path(name_or_path)
    if p.suffix == ".toml" and p.exists(): return p
    q = Path(layouts_dir) / f"{name_or_path}.toml"
    if q.exists(): return q
    have = ", ".join(sorted(x.stem for x in Path(layouts_dir).glob("*.toml")))
    raise FileNotFoundError(f"no layout {name_or_path!r} (a .toml path, or one of: {have})")


def available(layouts_dir=LAYOUTS_DIR):
    return sorted(p.stem for p in Path(layouts_dir).glob("*.toml"))


def load(name_or_path=None, layouts_dir=LAYOUTS_DIR):
    """The LayoutSet of a layout file; with cycle = "dir" the other files' layouts follow (fallbacks stay per file)."""
    p = find(name_or_path, layouts_dir)
    ls = parse(p.read_text(), p.stem, p)
    if ls.cycle == "dir":
        for q in sorted(p.parent.glob("*.toml")):
            if q == p: continue
            other = parse(q.read_text(), q.stem, q)
            for l in other.layouts:
                if l.name in ls.by_name: continue
                ls.layouts.append(l); ls.by_name[l.name] = l
                if l.fallback and l.fallback not in ls.by_name: ls.by_name[l.fallback] = other.by_name[l.fallback]
    return ls


# box-drawing by neighbour bits, so shared pane borders join like tmux's (├ ┬ ┼ ...)
U, D, L_, R_ = 1, 2, 4, 8
BOX = {U | D: "│", L_ | R_: "─", D | R_: "┌", D | L_: "┐", U | R_: "└", U | L_: "┘", U | D | R_: "├", U | D | L_: "┤",
       L_ | R_ | D: "┬", L_ | R_ | U: "┴", U | D | L_ | R_: "┼", U: "│", D: "│", L_: "─", R_: "─"}


def draw_borders(screen, rects, active=None, colour=None, active_colour=None, bg=None):
    """Pane borders for {name: Box}; the active pane's border in active_colour (nav_tui.draw_borders)."""
    from f19.hud.screen import BG, BORDER, BORDER_ON
    colour, active_colour, bg = colour or BORDER, active_colour or BORDER_ON, bg or BG
    bits, col = {}, {}

    def mark(x, y, b, on):
        bits[(x, y)] = bits.get((x, y), 0) | b
        if on: col[(x, y)] = active_colour
    for name, bx in rects.items():
        x0, y0, x1, y1 = bx.as_tuple()
        on = name == active
        for x in range(x0, x1 + 1):
            for y in (y0, y1):
                mark(x, y, (L_ if x > x0 else 0) | (R_ if x < x1 else 0), on)
        for y in range(y0, y1 + 1):
            for x in (x0, x1):
                mark(x, y, (U if y > y0 else 0) | (D if y < y1 else 0), on)
    for (x, y), b in bits.items():
        if b: screen.put(x, y, BOX.get(b, "┼"), col.get((x, y), colour), bg)
