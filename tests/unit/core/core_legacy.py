"""Pull functions and constants out of the old scripts/ and analysis/ modules without importing them (several run
their main loop or read EGAME.bin at import): the named top-level defs and assignments are exec'd in source order."""
import ast, math, struct
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]


def extract(rel, *names, **env):
    """{name: object} of the top-level defs / assignments `names` in REPO/rel."""
    tree = ast.parse((REPO / rel).read_text())
    want, body = set(names), []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in want: body.append(node)
        elif isinstance(node, ast.Assign) and {t.id for t in node.targets if isinstance(t, ast.Name)} & want: body.append(node)
    ns = dict(math=math, np=np, struct=struct, **env)
    exec(compile(ast.Module(body=body, type_ignores=[]), str(REPO / rel), "exec"), ns)
    missing = want - set(ns)
    assert not missing, f"{rel}: not found {missing}"
    return {n: ns[n] for n in names}
