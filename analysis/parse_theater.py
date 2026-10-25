"""Parse a theater .WLD file (e.g. CE.WLD) into named places.

Layout (CE.WLD, 2026-10-09): header u16s at 0 (n_named=27 at +4?, 54 at +6),
136 records of 16 bytes from 0x38: u16 name_id (1-based into the NUL-separated
name list that starts at the first string, 0 = unnamed), u16 X, u16 Y in
waypoint-table units (64 per km; = player position >> 5), u16 kind, 3 u16
flags, u16 icon/class (294 SAM site, 277 radar, 318 city/landmark, ...).

  parse_theater.py [CE|NC|LB|PG ...]   -> logs/theater/<code>.json (default: all four)
The parser itself lives in analysis/theaters.py (parse_wld), which other tools call directly.
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from theaters import ROOT, WLD_FILE, code, parse_wld

for th in [code(a) for a in sys.argv[1:]] or list(WLD_FILE):
    out = S.logs / "theater" / f"{th}.json"; out.parent.mkdir(parents=True, exist_ok=True)
    d = parse_wld(th); json.dump(d, open(out, "w"), indent=1)
    print(f"{th}: {len(d['records'])} records, {len(d['names'])} names -> logs/theater/{th}.json")
