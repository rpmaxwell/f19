"""The layout engine: the old nav_tui's stack() and _layout() reproduced by default.toml at every size; the file format.
The old HUD (scripts/nav_tui.py) is retired: its results are stored as digests (recorded 2026-10-30), over the same
random stacks and every (width, height, zoom) the test once compared one by one."""
import hashlib
import random
import types

import pytest

from f19.hud import layout as L


GOLD = {"stack": "708d5a4cecbf5012", "layout0": "840d64dd3d176e6a", "layout1": "fc8a26f39090a669"}


def test_stack_matches_old_nav_tui():
    rnd = random.Random(7)
    names = ["map", "nav", "radars", "aircraft", "fuel", "score", "log"]
    h = hashlib.sha256()
    for _ in range(3000):
        k = rnd.randint(1, 7)
        items = [(n, rnd.choice([None, rnd.randint(0, 12)]), rnd.randint(0, 10)) for n in rnd.sample(names, k)]
        y0 = rnd.randint(0, 5); y1 = y0 + rnd.randint(0, 80)
        new = L.stack(y0, y1, [(n, w, m, n == "map") for n, w, m in items])
        h.update(repr(sorted({n: (0, a, 10, b) for n, (a, b) in new.items()}.items())).encode())
    assert h.hexdigest()[:16] == GOLD["stack"]


@pytest.mark.parametrize("index", [0, 1])
def test_default_layout_matches_old_nav_tui(index):
    ls = L.load("default")
    h = hashlib.sha256()
    for W in range(30, 240, 3):
        for H in range(8, 80, 3):
            for zoom in (False, True):
                new = ls.rects(index, W, H, "radars" if zoom else None)
                h.update(repr(sorted({k: b.as_tuple() for k, b in new.items()}.items())).encode())
    assert h.hexdigest()[:16] == GOLD[f"layout{index}"]


def test_default_file():
    ls = L.load()
    assert ls.name == "default" and [l.name for l in ls.layouts] == ["two-column", "one-column"]
    assert ls.panes == ["map", "nav", "radars", "aircraft", "fuel", "score", "log"]
    assert ls.resolve(0, 109, 50).name == "one-column" and ls.resolve(0, 110, 50).name == "two-column"
    assert ls.resolve(1, 200, 50).name == "one-column"


def test_every_shipped_layout_loads():
    from f19.hud.widget import REGISTRY
    import f19.hud.widgets  # noqa: F401
    for name in L.available():
        ls = L.load(name)
        assert set(ls.widgets()) <= set(REGISTRY), name
        for i in range(len(ls.layouts)):
            for W, H in ((60, 20), (120, 40), (200, 60)):
                r = ls.rects(i, W, H)
                assert r and all(0 <= b.x0 < b.x1 < W and 0 <= b.y0 < b.y1 < H - 1 for b in r.values()), (name, i, W, H, r)


def test_find_by_path_and_name(tmp_path):
    p = tmp_path / "mine.toml"
    p.write_text('[[layout]]\nwidget = "map"\n')
    assert L.find(str(p)) == p
    assert L.load(p).layouts[0].root.widget == "map"
    assert L.find("default").name == "default.toml"
    with pytest.raises(FileNotFoundError, match="default"):
        L.find("no-such-layout")


def test_share_sizes_and_nesting():
    ls = L.parse("""
status_bar = false
[[layout]]
columns = [{ widget = "map", size = "50%" }, { rows = [{ widget = "nav", size = 3 }, { widget = "log" }] }]
""")
    r = ls.rects(0, 100, 30)
    assert r["map"].as_tuple() == (0, 0, 50, 29)            # inner = int(100 * .5) - 1 = 49 -> border at 50
    assert r["nav"].as_tuple() == (50, 0, 99, 4) and r["log"].as_tuple() == (50, 4, 99, 29)
    assert ls.panes == ["map", "nav", "log"]


def test_too_small_drops_trailing_panes():
    ls = L.parse('[[layout]]\nrows = [{ widget = "map", min = 5 }, { widget = "nav", min = 5 }, { widget = "log", min = 5 }]\n')
    assert list(ls.rects(0, 80, 14)) == ["map", "nav"]


@pytest.mark.parametrize("text, msg", [
    ('[[layout]]\nwidget = "map"\nrows = [{ widget = "nav" }]\n', "exactly one"),
    ('[[layout]]\nrows = [{ widget = "map", sise = 3 }]\n', "unknown keys"),
    ('[[layout]]\nrows = [{ widget = "map", size = "half" }]\n', "percentage"),
    ('[[layout]]\nrows = []\n', "non-empty"),
    ('name = "x"\n', "at least one"),
    ('[[layout]]\nwidget = "map"\nfallback = "nope"\n', "fallback"),
    ('cycle = "all"\n[[layout]]\nwidget = "map"\n', "cycle"),
    ('colour = 1\n[[layout]]\nwidget = "map"\n', "top-level"),
])
def test_bad_files(text, msg):
    with pytest.raises(ValueError, match=msg):
        L.parse(text)


def test_cycle_dir(tmp_path):
    (tmp_path / "a.toml").write_text('cycle = "dir"\n[[layout]]\nname = "a1"\nwidget = "map"\n')
    (tmp_path / "b.toml").write_text('[[layout]]\nname = "b1"\nmin_width = 500\nfallback = "b2"\nwidget = "nav"\n'
                                     '[[layout]]\nname = "b2"\nwidget = "log"\n')
    ls = L.load("a", tmp_path)
    assert [l.name for l in ls.layouts] == ["a1", "b1", "b2"]
    assert ls.resolve(1, 100, 40).name == "b2"
