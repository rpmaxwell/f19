"""Theater codes, their .WLD files and parsed place lists, in one place.

Codes are the ones used everywhere else (planner, territory, terrain): CE, NC, LB, PG.
The game's files are CE.WLD, NC.WLD, LIBYA.WLD and GULF.WLD.

  from theaters import code, wld_bytes, places
  code("Persian Gulf") -> "PG";  wld_bytes("PG") -> GULF.WLD bytes
  places("PG") -> logs/theater/PG.json (generated from the .WLD on first use)
"""
import json, re, struct
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME = Path("~/Documents/F-19 Stealth Fighter/MPS/F19").expanduser()
WLD_FILE = {"CE": "CE.WLD", "NC": "NC.WLD", "LB": "LIBYA.WLD", "PG": "GULF.WLD"}
ALIASES = {"CENTRAL EUROPE": "CE", "EUROPE": "CE", "NORTH CAPE": "NC", "LIBYA": "LB",
           "GULF": "PG", "PERSIAN GULF": "PG"}


def code(name):
    """'pg', 'GULF', 'Persian Gulf', 'GULF.WLD' -> 'PG'."""
    n = str(name).upper().removesuffix(".WLD").replace("_", " ").strip()
    c = ALIASES.get(n, n)
    if c not in WLD_FILE:
        raise ValueError(f"unknown theater {name!r}: use one of {', '.join(WLD_FILE)}")
    return c


def wld_bytes(th):
    return (GAME / WLD_FILE[code(th)]).read_bytes()


def parse_wld(th):
    """Named places of a theater .WLD (layout in analysis/parse_theater.py)."""
    th = code(th); d = wld_bytes(th)
    first = re.search(rb"[A-Z][a-z]{2,}\x00", d).start()            # start of the name list
    names = [n.decode("latin-1") for n in d[first:].split(b"\x00")]
    recs = []
    for o in range(0x38, first - 15, 16):
        nid, x, y, kind, f1, f2, f3, cls = struct.unpack_from("<8H", d, o)
        if x == y and x > 2000 and cls == x: break                    # tail padding pattern
        recs.append(dict(off=o, name_id=nid, name=names[nid - 1] if 0 < nid <= len(names) else "",
                         x=x, y=y, kind=kind, flags=[f1, f2, f3], cls=cls))
    return dict(file=WLD_FILE[th], names=names, records=recs)


def places(th):
    """logs/theater/<TH>.json, written from the .WLD if it doesn't exist yet."""
    th = code(th); f = ROOT / "logs/theater" / f"{th}.json"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        json.dump(parse_wld(th), open(f, "w"), indent=1)
    return json.load(open(f))


def in_memory(mem):
    """Theater codes whose .WLD record table appears in a memory image (the launcher's working copy)."""
    return [th for th in WLD_FILE if (GAME / WLD_FILE[th]).exists() and wld_bytes(th)[0x38:0x3E] in mem]
