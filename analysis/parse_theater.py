"""Parse theater .WLD files into named places (layout in src/f19/gamedata/wld.py, parse_wld).

  parse_theater.py [CE|NC|LB|PG ...]   -> logs/theater/<code>.json (default: all four)
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
import sys  # noqa: E402
from f19.gamedata.wld import WLD_FILE, code, write_places  # noqa: E402,F401
from f19.gamedata.wld import parse_wld  # noqa: E402,F401


def main(argv):
    for th in [code(a) for a in argv] or list(WLD_FILE):
        d = write_places(th)
        print(f"{th}: {len(d['records'])} records, {len(d['names'])} names -> logs/theater/{th}.json")


if __name__ == "__main__":
    main(sys.argv[1:])
