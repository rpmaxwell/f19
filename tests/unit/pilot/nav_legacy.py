"""scripts/nav.py's main-loop code, VERBATIM, for the WP-7a equivalence tests. nav is module-level code (it connects
and flies on import), so the blocks are cut out of the file's text by marker lines and exec'd per tick in a namespace
that holds nav's globals. Any edit to those blocks in nav.py changes what the tests run: the comparison is always
with nav as it is (until the cutover, WP-7d).

  constants()          nav's loop constants (altitude / heading hold), wrap, roll_rate, CLIMB_* (exec'd once)
  LOOPS                nav :643-746  turn ... thr.step   (sensitivity fix, heading hold, altitude hold, flaps, throttle)
  ROUTE                nav :495-516  route following + nav's takeoff phase (wrapped in `while True: ... break`, so
                                     nav's `continue` after a leg advance re-runs it on the same state, as FlyLeg does)
  MERGE                nav :517-642  ov = {} ... the TerrainEscape merge (phases, reactions, saved speeds)
"""
import textwrap
from pathlib import Path

NAV = Path(__file__).resolve().parents[3] / "scripts" / "nav.py"


def _lines(): return NAV.read_text().splitlines()


def block(start, end, include_end=True):
    """The dedented text from the first line starting (stripped) with `start` to the next starting with `end`."""
    L = _lines()
    i = next(k for k, l in enumerate(L) if l.strip().startswith(start))
    j = i if end == start else next(k for k in range(i + 1, len(L)) if L[k].strip().startswith(end))
    return textwrap.dedent("\n".join(L[i:j + 1 if include_end else j]))


def constants():
    """nav's constants and helpers the loops use, as source."""
    src = [block("NOSE_UP, NOSE_DOWN, KP,", "roll = dict(key=None"),
           block("def roll_rate(now):", "return dh /"),
           block("def wrap(d):", "def wrap(d):"),
           block("CLIMB_DIST, CLIMB_ALT =", "REJOIN = "),
           block("LOOKAHEAD, ARRIVE, OVERFLY =", "LOITER_LOOKAHEAD")]
    return "\n".join(src)


def loops_src():
    return block('turn = wrap(want - s["hdg"])', "if args.auto and airborne and phase not in (\"landed\", \"handover\")")


def route_src():
    body = block('gw, sel = read_waypoints(db)', 'ov = {}', include_end=False)
    return "while True:\n" + textwrap.indent(body, "    ") + "\n    break"


def merge_src():
    return block("ov = {}", "turn = wrap(want - s[\"hdg\"])", include_end=False)


LOOPS = compile(loops_src(), "nav.py:loops", "exec")
ROUTE = compile(route_src(), "nav.py:route", "exec")
MERGE = compile(merge_src(), "nav.py:merge", "exec")
CONSTANTS = compile(constants(), "nav.py:constants", "exec")
