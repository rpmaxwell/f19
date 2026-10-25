"""Build MISSIONS.md: every mission number with its briefing text and the theaters it can occur in.

Sources (all static game data, no emulator needed except for the table dump):
  - BRIEFING.TXT: MicroProse LZW (byte 0 = max code width 11, codes LSB-first from 9 bits,
    first free code 257, dictionary reset when full), 56 NUL-separated texts; text n = mission n.
  - logs/theater/missions.json: START's mission table (ds:4B0C), dumped by
    `scripts/read_mission.py --missions logs/theater/missions.json` during a briefing.
  - each .WLD: 100-byte category table (one byte per name slot) just before the 256-byte territory
    grid, which sits just before the name list (CE.WLD 0x756). START copies it to ds:C162.
A mission can occur in theater t when bit t of its theater_mask is set (0 Libya, 1 Persian Gulf,
2 North Cape, 3 Central Europe) and the theater has a target object of the mission's category.

  python3 analysis/mission_catalog.py [out.md]
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GAME = S.game_dir
THEATERS = [("Libya", "LIBYA.WLD"), ("Persian Gulf", "GULF.WLD"), ("North Cape", "NC.WLD"), ("Central Europe", "CE.WLD")]
# Types 5-8 are not in the old notes; all their texts are "shoot down a plane in flight".
TASK = {5: "intercept (5)", 6: "intercept (6)", 7: "intercept (7)", 8: "intercept (8)"}
# Categories no .WLD category table lists: target named from the briefing text instead.
TEXT_TARGET = {1: "secret airstrip / drop point (placed by the generator)", 5: "LPAR radar (from text)",
               17: "merchant ship (from text)", 19: "army depot (from text)"}
# Generator rules (START ~0x7738, see FINDINGS.md):
#  - code bit 1 (0x02): deadline = 450 + takeoff->primary distance/16; type 5/6: deadline from the plane's route.
#    Type 7 sets no deadline value (the plane flies to a destination), type 8 none (patrol/orbit).
#  - secondary is rejected when it has code bit 1 or type 5-8 -> those are primary-only.
#  - setup "air-to-air" (pilot +0x3C bit 0 clear): primary = random enemy airbase record -> always type 5-8;
#    "strike" (bit set): primary picked near a random enemy position, enemy airbases excluded -> never 5-8.
def time_limit(m):
    if m["code"] & 2: return "yes: start + 15 min + ~8 s/km (takeoff to target)"
    return {5: "yes: before the plane's estimated arrival", 6: "yes: before the plane's estimated arrival",
            7: "race: plane leaves its airbase at mission start", 8: "no"}.get(m["type"], "no")
def primary_only(m): return "yes" if m["code"] & 2 or m["type"] in TASK else "no"
def setup(m): return "air-to-air" if m["type"] in TASK else "strike"
LEVELS = ["Cold War", "Limited War", "Conventional War"]   # bit 0 confirmed (clandestine briefing); 1/2 by game order

def unlzw(data):
    maxbits, bits, pos, out = data[0], 9, 8, bytearray()
    dic, nxt, prev = {i: bytes([i]) for i in range(256)}, 257, None
    while pos + bits <= len(data) * 8:
        v = int.from_bytes(data[pos // 8:pos // 8 + 3].ljust(3, b"\0"), "little") >> (pos % 8) & ((1 << bits) - 1)
        pos += bits
        if v in dic: e = dic[v]
        elif v == nxt and prev is not None: e = prev + prev[:1]
        else: break                                           # padding at the end
        out += e
        if prev is not None: dic[nxt] = prev + e[:1]; nxt += 1
        prev = e
        if nxt >= 1 << bits:
            if bits < maxbits: bits += 1
            else: dic, nxt, bits, prev = {i: bytes([i]) for i in range(256)}, 257, 9, None
    return bytes(out)

def briefings():
    return [t.decode("latin-1").strip() for t in unlzw((GAME / "BRIEFING.TXT").read_bytes()).split(b"\0")[:56]]

def categories(wld):
    """{category: [object names]} for one theater."""
    w = (GAME / wld).read_bytes()
    i = len(w)
    while i > 0 and (32 <= w[i - 1] < 127 or w[i - 1] == 0): i -= 1
    names = [n.decode("latin-1") for n in w[i:].split(b"\0")]   # names[k] = name id k (names[0] = "")
    cats = {}
    for k, c in enumerate(w[i - 356:i - 256]):
        if c and k < len(names) and names[k] not in cats.get(c, []): cats.setdefault(c, []).append(names[k])
    return cats

def main(out):
    table = json.load(open(S.logs / "theater/missions.json"))["missions"]
    text = briefings()
    cats = [categories(f) for _, f in THEATERS]
    rows, never = [], []
    for m in table:
        n, c = m["mission"], m["category"]
        allowed = [k for k in range(4) if m["theater_mask"] >> k & 1]
        have = [k for k in allowed if c in cats[k]]
        objs = []
        for k in have:
            for o in cats[k][c]:
                if o not in objs: objs.append(o)
        if c == 1 or not have:    # type check can't be applied: fall back to the mask alone
            target, theaters = TEXT_TARGET.get(c, f"category {c}"), ", ".join(THEATERS[k][0] for k in allowed) + " (unconfirmed)"
            never.append(n)
        else:
            target, theaters = " / ".join(objs), ", ".join(THEATERS[k][0] for k in have)
        if m["type"] in TASK: target = f"aircraft in flight ({target} category)"
        levels = ", ".join(LEVELS[b] for b in range(3) if m["level_mask"] >> b & 1)
        first = text[n].split(". ")[0].rstrip(".") + "."
        task = TASK.get(m["type"], m["type_name"])
        rows.append(f"| {n} | {task} | {target} | {first} | {theaters} | {levels} | {time_limit(m)} | {primary_only(m)} | {setup(m)} |")
    md = ["# F-19 missions", "",
          "Generated by `analysis/mission_catalog.py` from the game files: BRIEFING.TXT for the texts, START's mission",
          "table (`logs/theater/missions.json`) and each theater's .WLD category table. Layout notes are in FINDINGS.md",
          "under \"Mission briefing block in OUR build\".", "",
          "- **Mission number** is fixed per mission: same briefing text, same task, same target *type*. The place",
          "  varies: the generator picks any object of that type in the theater (e.g. mission 4 = photograph a SAM Radar,",
          "  seen at Tabor).",
          "- **Theaters** = the mission table's theater mask AND the theater having a target of that type. Bit order",
          "  checked against the texts (Shi'ite oil platform = Gulf only; ground offensive = North Cape + Central Europe).",
          "- **Levels**: bit 0 confirmed as the clandestine level (Cold War); Limited / Conventional War assume the game's order.",
          "- **Unconfirmed rows:** missions " + ", ".join(map(str, never)) + " have a target type that no theater's .WLD",
          "  category table lists (the secret airstrip of missions 0-3 is placed by the generator; the others may never be",
          "  offered). Their theaters are the mask alone.",
          "- **Intercept (5-8):** task types not in the old notes; every text is \"shoot down a plane in flight\". The number",
          "  is the game's type value; what separates 5/6/7/8 (escort, route, orbit?) is not known yet.",
          "- **Time limit** (from START's generator; game clock, 1 unit = 2 s): missions with table code bit 1 must reach",
          "  the primary before *mission start + 15 min + ~8 s per km* of straight-line distance from the takeoff base to",
          "  the target (450 + distance/16 units). That needs an average of 290 km/h (~155 kt) for a 200 km leg and 450 km/h",
          "  (~245 kt) on very long legs. Every leg is also longer than the straight line, so this is the hard constraint",
          "  for low, slow, radar-avoiding routes. Landing missions also close 15 minutes after the deadline (\"the",
          "  airstrip will be lighted ONLY during this period\"). Types 5/6: the deadline is the target plane's estimated",
          "  arrival. Type 7 has no deadline, but the plane leaves its airbase at mission start (seen: mission 51,",
          "  An-72 \"leaving from Tabor airbase at 10:00\", mission begins 10:00) for an unknown destination.",
          "  `read_mission.py` prints the briefing's start and deadline times.",
          "- **Primary only:** the generator throws away a secondary with a time limit (code bit 1) or an intercept",
          "  (types 5-8). Every other mission can be primary or secondary. A secondary is never the same mission",
          "  number as the primary. Landing and supply missions can't be paired with each other, or with a",
          "  photograph mission.",
          "- **Setup** (pilot record +0x3C bit 0; set = strike): *air-to-air* makes the primary a random enemy airbase,",
          "  so it is always an intercept (47-55). *Strike* picks the primary near a random enemy position and excludes",
          "  enemy airbases, so it is never an intercept. The secondary is drawn the same way in both setups (no intercepts).",
          "  Confirmed: the 2026-10-17 strike briefing had bit 0 set. Training mode (bit 1) gives mission 5 for strike",
          "  and 55 for air-to-air.", "",
          "| # | Task | Target | Description (first sentence) | Possible theaters | Levels | Time limit | Primary only | Setup |",
          "|---|---|---|---|---|---|---|---|---|", *rows, "", "## Full briefing texts", ""]
    for m in table:
        md += [f"**{m['mission']}. {TASK.get(m['type'], m['type_name'])}.** {text[m['mission']]}", ""]
    Path(out).write_text("\n".join(md))
    print(f"{len(rows)} missions -> {out}")

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / "MISSIONS.md")
