"""Theater .WLD files: named places, the territory grid and the target-category table.

Layout (CE.WLD, 2026-10-09): header u16s at 0, 16-byte place records from 0x38: u16 name_id (1-based into the
NUL-separated name list that starts at the first string, 0 = unnamed), u16 X, u16 Y in waypoint units (64 per km;
= player position >> 5), u16 kind, 3 u16 flags, u16 icon/class (294 SAM site, 277 radar, 318 city/landmark, ...).
Right before the name list: the 100-byte category table (one byte per name slot; target category = table[class &
0x7f] & 0x0f, as in the ordnance matrix), then the 16x16 territory grid (256 bytes, row-major, y rows; cell =
(x >> 11, y >> 11)), then one byte, then the names.

Territory grid low two bits: 3 = friendly (NATO), 2 = neutral, 0 = enemy (cold-war samples score; ejecting =
capture); bits 0x0C = terrain type (0 mountains, 4 rough, 8 flat, 12 sea), used by the radar return.

The name list starts at the first match of NAME_RE (a capitalised word + NUL). name_start(full=True) also backs up
over a multi-word first name ("Bandar Abbas" in GULF.WLD); the place list and the territory grid use the plain match,
the category table the backed-up one (as the old analysis/theaters, territory and ordnance modules did).
"""
import json
import re
import struct
from functools import lru_cache

import numpy as np

from f19.core.settings import settings as S
from f19.core.theater import Theater

NAME_RE = re.compile(rb"[A-Z][a-z]{2,}\x00")
WLD_FILE = {c: Theater(c).wld for c in ("CE", "NC", "LB", "PG")}     # this order: CLIs and in_memory iterate it
ALIASES = {"CENTRAL EUROPE": "CE", "EUROPE": "CE", "NORTH CAPE": "NC", "LIBYA": "LB",
           "GULF": "PG", "PERSIAN GULF": "PG"}
SIDE = {3: "friendly", 2: "neutral", 0: "enemy", 1: "1?"}


def code(name):
    """'pg', 'GULF', 'Persian Gulf', 'GULF.WLD' -> 'PG'."""
    try:
        return Theater.parse(name).value
    except ValueError:
        raise ValueError(f"unknown theater {name!r}: use one of {', '.join(WLD_FILE)}") from None


def wld_bytes(th):
    return (S.game_dir / WLD_FILE[code(th)]).read_bytes()


def name_start(b, full=False):
    """Offset of the name list in .WLD bytes b (see the module doc for `full`)."""
    i = NAME_RE.search(b).start()
    if full:
        while chr(b[i - 1]).isalpha() or b[i - 1] == 0x20: i -= 1
    return i


def parse_wld(th):
    """Named places of a theater .WLD -> dict(file, names, records)."""
    th = code(th); d = wld_bytes(th)
    first = name_start(d)                                              # start of the name list
    names = [n.decode("latin-1") for n in d[first:].split(b"\x00")]
    recs = []
    for o in range(0x38, first - 15, 16):
        nid, x, y, kind, f1, f2, f3, cls = struct.unpack_from("<8H", d, o)
        if x == y and x > 2000 and cls == x: break                    # tail padding pattern
        recs.append(dict(off=o, name_id=nid, name=names[nid - 1] if 0 < nid <= len(names) else "",
                         x=x, y=y, kind=kind, flags=[f1, f2, f3], cls=cls))
    return dict(file=WLD_FILE[th], names=names, records=recs)


def record_count(th):
    """Records the game's target table holds for the theater: .WLD header word 1 (CE 75, GULF 64, NC 46, LIBYA 37).
    Table layout (memory.mission, memlog 1793126949): record 0 = scratch, 1 and 2 = the generated target slots, then
    the place records from 0x38, so game record k is place k - 3."""
    return struct.unpack_from("<H", wld_bytes(th), 2)[0]


def places_path(th):
    return S.logs / "theater" / f"{code(th)}.json"


def write_places(th):
    """Parse the .WLD and write the place cache (logs/theater/<TH>.json) -> the parsed dict."""
    f = places_path(th); f.parent.mkdir(parents=True, exist_ok=True)
    d = parse_wld(th)
    json.dump(d, open(f, "w"), indent=1)
    return d


def places(th):
    """logs/theater/<TH>.json, written from the .WLD if it doesn't exist yet."""
    f = places_path(th)
    if not f.exists(): write_places(th)
    return json.load(open(f))


def in_memory(mem):
    """Theater codes whose .WLD record table appears in a memory image (the launcher's working copy)."""
    return [th for th in WLD_FILE if (S.game_dir / WLD_FILE[th]).exists() and wld_bytes(th)[0x38:0x3E] in mem]


@lru_cache(None)
def grid(th):
    """16 x 16 territory grid bytes of a theater, [y, x] (uint8)."""
    b = wld_bytes(th)
    i = name_start(b)
    return np.frombuffer(b[i - 1 - 256:i - 1], np.uint8).reshape(16, 16)


def side(th, x, y):
    """'friendly' | 'neutral' | 'enemy' | '1?' of the 32 km cell at (x, y) units; 'off-map' outside the grid."""
    g = grid(th); gx, gy = int(x) >> 11, int(y) >> 11
    if not (0 <= gx < 16 and 0 <= gy < 16): return "off-map"
    return SIDE[int(g[gy, gx]) & 3]


def category_table(b):
    """(names, 100-byte category table) of .WLD bytes b; names[k - 1] is name id k."""
    i = name_start(b, full=True)
    names = [n.decode("latin-1") for n in b[i:].split(b"\0")]
    return names, b[i - 1 - 256 - 100:i - 1 - 256]


def categories(th):
    """{name id: (name, category byte)} of the theater's capitalised names (f19.gamedata.wld.categories)."""
    names, cat = category_table(wld_bytes(th))
    return {k: (names[k - 1], cat[k]) for k in range(1, 100) if k <= len(names) and names[k - 1] and not names[k - 1][0].islower()}


def objects_by_category(th):
    """{category byte: [object names]} for one theater, every name slot with a non-zero category (mission catalog)."""
    names, cat = category_table(wld_bytes(th))
    names = [""] + names                                  # names[k] = name id k
    cats = {}
    for k, c in enumerate(cat):
        if c and k < len(names) and names[k] not in cats.get(c, []): cats.setdefault(c, []).append(names[k])
    return cats


def main(argv):
    """parse-theater: write logs/theater/<code>.json for the given theaters (default: all four)."""
    for th in [code(a) for a in argv] or list(WLD_FILE):
        d = write_places(th)
        print(f"{th}: {len(d['records'])} records, {len(d['names'])} names -> {places_path(th)}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1:])
