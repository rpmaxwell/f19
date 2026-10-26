"""Layer rules for src/f19 (refactor_plan.md section 3), checked by parsing every module's imports.

  core      -> (nothing in f19 but core)
  gamedata  -> core                      rules    -> core, gamedata
  emulator  -> core                      memory   -> core, emulator
  planner   -> core, gamedata, rules     telemetry -> core
  pilot     -> core, rules, memory, emulator, planner, telemetry
  hud       -> core, rules, telemetry
  nothing imports research (the top-level research/ tree, by any name)

Data-driven: every directory under src/f19 is a layer and must appear in ALLOWED, so a new package can't slip in
unruled; modules directly in src/f19 (cli.py, __init__.py) may import any layer. Relative imports are resolved.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "f19"

ALLOWED = {
    "core": set(),
    "gamedata": {"core"},
    "rules": {"core", "gamedata"},
    "emulator": {"core"},
    "memory": {"core", "emulator"},
    "planner": {"core", "gamedata", "rules"},
    "telemetry": {"core"},
    "pilot": {"core", "rules", "memory", "emulator", "planner", "telemetry"},
    "hud": {"core", "rules", "telemetry"},
    # the entry points sit on top: they may wire any layer together, and no layer imports them
    "cli": {"core", "gamedata", "rules", "emulator", "memory", "planner", "telemetry", "pilot", "hud"},
}
FORBIDDEN_TOP = {"research"}            # never importable from the package
# the flat product modules the package replaces: importing them from src/f19 would undo the move
LEGACY = {p.stem for d in ("scripts", "analysis") for p in (ROOT / d).glob("*.py")}


def modules():
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: Path) -> list[str]:
    parts = list(path.relative_to(PKG.parent).with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def imports(path: Path):
    """[(lineno, absolute dotted name)] of every import in the file (relative ones resolved against the package)."""
    tree = ast.parse(path.read_text(), str(path))
    me = module_name(path)
    pkg = me if path.name == "__init__.py" else me[:-1]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                mod = ".".join(base + ([node.module] if node.module else []))
                if node.module: out.append((node.lineno, mod))
                else: out += [(node.lineno, f"{mod}.{a.name}") for a in node.names]
            else:
                out.append((node.lineno, node.module))
                out += [(node.lineno, f"{node.module}.{a.name}") for a in node.names if node.module == "f19"]
    return out


def layer_of(path: Path):
    rel = path.relative_to(PKG).parts
    return rel[0] if len(rel) > 1 else None


def test_every_layer_has_a_rule():
    layers = {p.name for p in PKG.iterdir() if p.is_dir() and p.name != "__pycache__"}
    assert layers <= set(ALLOWED), f"src/f19 packages without an import rule: {sorted(layers - set(ALLOWED))}"


@pytest.mark.parametrize("path", modules(), ids=lambda p: str(p.relative_to(PKG)))
def test_layer_imports(path):
    me = layer_of(path)
    bad = []
    for line, name in imports(path):
        top = name.split(".")[0]
        if top in FORBIDDEN_TOP: bad.append(f"{line}: {name} (research is outside the package)"); continue
        if top in LEGACY: bad.append(f"{line}: {name} (flat scripts/ or analysis/ module)"); continue
        if top != "f19" or me is None: continue
        parts = name.split(".")
        if len(parts) < 2: continue                         # `import f19`: harmless
        target = parts[1]
        if target == me or target not in ALLOWED: continue  # own layer; a module directly in f19 (e.g. f19.cli)
        if target not in ALLOWED[me]: bad.append(f"{line}: {name} ({me} may import {sorted(ALLOWED[me]) or 'only core'})")
    assert not bad, f"{path.relative_to(PKG.parent)}:\n  " + "\n  ".join(bad)


def test_rule_checker_catches_violations(tmp_path, monkeypatch):
    """The checker itself: a fake tree with one legal and three illegal imports."""
    fake = tmp_path / "src" / "f19"
    (fake / "core").mkdir(parents=True); (fake / "hud").mkdir()
    (fake / "core" / "units.py").write_text("from f19.planner import api\n")
    (fake / "hud" / "tui.py").write_text("from ..telemetry import frame\nfrom ..memory import reader\nimport research.x\n")
    monkeypatch.setattr(__import__(__name__), "PKG", fake)
    msgs = {}
    for p in sorted(fake.rglob("*.py")):
        try: test_layer_imports(p); msgs[p.name] = None
        except AssertionError as e: msgs[p.name] = str(e)
    assert "1: f19.planner (core may import only core)" in msgs["units.py"]
    tui = msgs["tui.py"]
    assert "2: f19.memory (hud may import" in tui and "3: research.x" in tui and "1: " not in tui    # telemetry is allowed
