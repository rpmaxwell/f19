"""Ordnance tables and weapon-vs-target effectiveness, read statically from the game files.

Found 2026-10-17 (see FINDINGS.md "Ordnance vs target strength"):
- EGAME/START data segment holds the loadout catalog (20 x 26 B: short name[10], long name[12],
  u16 spec, u16 qty) and right after it the effectiveness matrix: 20 weapons x 13 target
  categories, int8, at EGAME ds:5106.
- A target's category = WLD category table[class & 0x7f] & 0x0f; the table is the 100 bytes right
  before the 256-byte territory grid, which sits right before the name strings.
- category >= 13 overflows into the next weapon's row (the game does no bounds check).

  ordnance.py              matrix + every theater's target types with their effective values
"""
import re, struct, subprocess, sys, tempfile
from pathlib import Path

GAME = Path("~/Documents/F-19 Stealth Fighter/MPS/F19").expanduser()
WLD = {"CE": "CE.WLD", "NC": "NC.WLD", "LB": "LIBYA.WLD", "PG": "GULF.WLD"}
NCAT = 13

def egame_image():
    out = Path(tempfile.gettempdir()) / "EGAME.bin"
    if not out.exists():
        subprocess.run([sys.executable, str(Path(__file__).with_name("unexepack.py")), str(GAME / "EGAME.EXE"), str(out)],
                       check=True, capture_output=True)
    return out.read_bytes()

def tables():
    d = egame_image()
    ds = d.index(b"MS Run-Time Library") - 8                  # DGROUP base (string sits at ds:0008)
    matrix = d[ds + 0x5106:ds + 0x5106 + 20 * NCAT + 3 * NCAT]  # + slack for the category overflow
    cat0 = ds + 0x5106 - 20 * 26                                # loadout catalog sits right before it
    loadout = []
    for i in range(20):
        r = d[cat0 + 26 * i:cat0 + 26 * (i + 1)]
        spec, qty = struct.unpack_from("<hh", r, 22)
        loadout.append(dict(short=r[:10].split(b"\0")[0].decode(), long=r[10:22].split(b"\0")[0].decode(), spec=spec, qty=qty))
    return loadout, matrix

def compat(matrix, weapon, category):
    v = matrix[weapon * NCAT + (category & 0x0f)]
    return v - 256 if v > 127 else v

def categories(th):
    b = (GAME / WLD[th]).read_bytes()
    i = re.search(rb"[A-Z][a-z]{2,}\x00", b).start()       # as parse_theater.py, then back up over
    while chr(b[i - 1]).isalpha() or b[i - 1] == 0x20: i -= 1   # a multi-word first name ("Bandar Abbas")
    names = [n.decode("latin-1") for n in b[i:].split(b"\0")]
    cat = b[i - 1 - 256 - 100:i - 1 - 256]
    return {k: (names[k - 1], cat[k]) for k in range(1, 100) if k <= len(names) and names[k - 1] and not names[k - 1][0].islower()}

if __name__ == "__main__":
    loadout, m = tables()
    print("weapon            spec qty | " + " ".join(f"{c:2d}" for c in range(NCAT)))
    for w, l in enumerate(loadout):
        print(f"{w:2d} {l['short']:8} {l['long']:12} {l['spec']:#06x} {l['qty']} | " + " ".join(f"{compat(m, w, c):2d}" for c in range(NCAT)))
    used = {}
    for th in WLD:
        for k, (n, c) in categories(th).items():
            if c: used.setdefault(c & 0x0f, set()).add(f"{n}({th}{'+%02x' % (c & 0xf0) if c & 0xf0 else ''})")
    print("\ncategory -> object types (theater, high flag bits)")
    for c in sorted(used): print(f"{c:2d} {', '.join(sorted(used[c]))}")
    print("\neffective value per weapon (0 = can never kill; categories 13-15 read the next weapon's row)")
    for c in sorted(used):
        print(f"{c:2d} " + " ".join(f"{l['short'][:7]}={compat(m, w, c)}" for w, l in enumerate(loadout) if compat(m, w, c)))
