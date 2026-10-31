"""No code under src/ may run or open a file under scripts/ or analysis/ that does not exist (2026-10-30: planner.cli.new_plan still ran the
deleted scripts/read_mission.py and analysis/planner; nothing noticed until the user ran it, because no test starts the planner's subprocesses).
Checks every string constant that looks like a repo-relative path to a .py file or to analysis/."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = re.compile(r"^(scripts|analysis|research)/[\w./-]*$")


def test_string_paths_into_scripts_and_analysis_exist():
    bad = []
    for f in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(f.read_text(), str(f))
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
                      and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings and PATH.match(n.value.strip()):
                if not (ROOT / n.value.strip()).exists(): bad.append(f"{f.relative_to(ROOT)}:{n.lineno}: {n.value!r}")
    assert not bad, "code points at files that do not exist:\n  " + "\n  ".join(bad)
