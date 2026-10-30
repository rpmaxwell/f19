"""Build a ROSTER.FIL from a CSV of pilots and their mission scores, replaying END.EXE's award rules.

  build_roster.py desired_roster_state.csv [--out F19_DATA/saves/ROSTER.FIL.generated] [--force]
                  [--active-index N] [--dry-run]

Nothing is installed: the output is a new file. To use it, close the game, back up the live
<F19_GAME_DIR>/ROSTER.FIL and copy the generated file over it by hand.

CSV: first column = pilot slot (0-9), then pilot_name, mission_count, avg_score, best_score, a blank column, and
one column per flown mission (the scores, in flight order, blank after the last). Only the scores drive the
record; mission_count / avg_score / best_score are cross-checked against them and a mismatch is reported.

Each score is replayed as a normal, non-training, uncaptured, survived flight, in order, with the rules decoded
from END.EXE (docs/game/scoring-awards.md, 2026-10-24):
  missions++, total += score, best = max(best, score)
  medal (at most one a flight): score > 1200 and no Medal of Honor -> MoH; else score >= 900 x (AFC+1) -> AFC;
    else >= 600 x (SS+1) -> Silver Star; else >= 300 x (DFC+1) -> DFC; else >= 100 x (AM+1) and AM < 9 -> Airman's
  ribbons from the mission count: 5-9 Short Tour; >= 10 Long Tour (clears Short); 30-59 Longevity;
    >= 60 Longevity w/ Gold Cluster (clears Longevity)  [END 0x8625..0x86bf: the "replaces" clears the flag]
  promotion, at most one rank a flight, from the running total: needs total >= T, missions >= M, total/missions >= A;
    Colonel -> Brigadier General only on exactly the 99th mission
Status (record +0x4E, found in START.EXE 2026-10-27: the roster list indexes the strings Active / Retired / KIA
by it; a retired pilot shows "This officer has retired. Please choose again"): 0 Active, 1 Retired, 2 KIA.
A pilot who flew 99 missions is Retired, the rest Active.

Not derivable from scores, so left at the game's new-pilot defaults: Purple Heart (needs damage state), Combat Readiness
Ribbon (needs a training flight, which stores score 0 and does not count as a mission), last-flight settings
(+0x38..+0x42 = the erase/new-pilot values 0,0,3,0,0,4).
"""
import argparse, csv, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from f19.core.settings import settings as S  # noqa: E402

PILOTS = 10
RECORD = 0x50
NAME_MAX = 29                      # +2..+0x1F, NUL-terminated
RETIRE_MISSIONS = 99
ACTIVE, RETIRED, KIA = 0, 1, 2
RANKS = ["2nd Lt", "1st Lt", "Captain", "Major", "Lt Col", "Colonel", "Brig Gen"]
# from rank r to r+1: (career total, missions, average)  -- docs/game/scoring-awards.md
PROMOTION = [(300, 2, 100), (1125, 5, 150), (3000, 10, 200), (7000, 20, 250), (16000, 40, 280), (27720, 99, 280)]
COLONEL = 5


class Pilot:
    def __init__(self, index, name):
        self.index, self.name = index, name
        self.rank = 0
        self.purple_heart = 0
        self.airman = self.dfc = self.silver = self.afc = 0
        self.moh = 0
        self.best = self.last = self.total = self.missions = 0
        self.combat_readiness = self.short_tour = self.long_tour = self.longevity = self.longevity_gc = 0
        self.status = ACTIVE
        self.log = []                  # (mission number, score, events), for the report

    def medal(self, s):
        """END 0x85d7: the one medal for a flight scoring s (None if none)."""
        if s > 1200 and not self.moh:
            self.moh = 1; return "Medal of Honor"
        if s >= 900 * (self.afc + 1):
            self.afc += 1; return "Air Force Cross"
        if s >= 600 * (self.silver + 1):
            self.silver += 1; return "Silver Star"
        if s >= 300 * (self.dfc + 1):
            self.dfc += 1; return "Distinguished Flying Cross"
        if s >= 100 * (self.airman + 1) and self.airman < 9:
            self.airman += 1; return "Airman's Medal"
        return None

    def ribbons(self):
        n, new = self.missions, []
        if 5 <= n < 10 and not self.short_tour:
            self.short_tour = 1; new.append("Short Tour ribbon")
        if n >= 10 and not self.long_tour:
            self.short_tour = 0; self.long_tour = 1; new.append("Long Tour ribbon")
        if 30 <= n < 60 and not self.longevity:
            self.longevity = 1; new.append("Longevity ribbon")
        if n >= 60 and not self.longevity_gc:
            self.longevity = 0; self.longevity_gc = 1; new.append("Longevity ribbon with Gold Cluster")
        return new

    def promote(self):
        """END 0x8526: at most one rank per flight, using the total that includes this flight."""
        if self.rank >= len(PROMOTION):
            return None
        total, missions, avg = PROMOTION[self.rank]
        if self.rank == COLONEL and self.missions != RETIRE_MISSIONS:
            return None
        if self.total >= total and self.missions >= missions and self.total / self.missions >= avg:
            self.rank += 1
            return "promoted to " + RANKS[self.rank]
        return None

    def fly(self, s):
        self.missions += 1
        self.total += s
        self.last = s
        self.best = max(self.best, s)
        events = [e for e in (self.medal(s), *self.ribbons(), self.promote()) if e]
        if events:
            self.log.append((self.missions, s, events))
        if self.missions == RETIRE_MISSIONS:
            self.status = RETIRED

    def pack(self):
        r = bytearray(RECORD)
        name = self.name.encode("ascii")
        r[2:2 + len(name)] = name
        struct.pack_into("<H", r, 0x00, self.index)
        struct.pack_into("<HHHHHHH", r, 0x20, self.rank, self.purple_heart, self.airman, self.dfc, self.silver,
                         self.afc, self.moh)
        struct.pack_into("<HH", r, 0x2E, self.best, self.last)
        struct.pack_into("<I", r, 0x32, self.total)
        struct.pack_into("<H", r, 0x36, self.missions)
        # last-flight settings: theater, ROE, type, opponents, landing, +0x42 (START's new-pilot values)
        struct.pack_into("<HHHHHH", r, 0x38, 0, 0, 3, 0, 0, 4)
        struct.pack_into("<HHHHH", r, 0x44, self.combat_readiness, self.short_tour, self.long_tour, self.longevity,
                         self.longevity_gc)
        struct.pack_into("<H", r, 0x4E, self.status)
        return bytes(r)


def read_pilots(path):
    """-> [(Pilot with its flights replayed, claimed (missions, avg, best))], in CSV order."""
    rows = list(csv.reader(open(path, newline="")))
    header, data = rows[0], rows[1:]
    if header[1:5] != ["pilot_name", "mission_count", "avg_score", "best_score"]:
        sys.exit(f"{path}: unexpected header {header[:6]}")
    if len(data) > PILOTS:
        sys.exit(f"{path}: {len(data)} pilots, the roster holds {PILOTS}")
    out = []
    for i, row in enumerate(data):
        name = row[1].strip()
        if not name or len(name) > NAME_MAX or not all(0x20 <= ord(c) <= 0x7e for c in name):
            sys.exit(f"row {i}: name {name!r} must be 1-{NAME_MAX} printable ASCII characters")
        scores = [int(c) for c in row[5:] if c.strip()]
        p = Pilot(i, name)
        for s in scores:
            if s < 0 or s > 0xFFFF:
                sys.exit(f"{name}: score {s} does not fit the record")
            p.fly(s)
        out.append((p, (int(row[2]), float(row[3]), int(row[4]))))
    return out


def check(p, claimed):
    """Mismatches between the replay and the CSV's own summary columns (the scores win)."""
    missions, avg, best = claimed
    bad = []
    if missions != p.missions:
        bad.append(f"mission_count {missions} but {p.missions} scores")
    if p.missions and abs(p.total / p.missions - avg) > 0.001:
        bad.append(f"avg_score {avg} but the scores average {p.total / p.missions:.4f}")
    if best != p.best:
        bad.append(f"best_score {best} but the highest score is {p.best}")
    if p.total > 0xFFFFFFFF:
        bad.append("career total overflows u32")
    return bad


def build(pilots, active_index=0):
    """The 802-byte file: u16 header (active pilot's index) + 10 records; unused slots are zeroed."""
    body = b"".join(p.pack() for p, _ in pilots) + bytes(RECORD) * (PILOTS - len(pilots))
    return struct.pack("<H", active_index) + body


def report(pilots):
    for p, claimed in pilots:
        medals = f"AM {p.airman}  DFC {p.dfc}  SS {p.silver}  AFC {p.afc}  MoH {p.moh}"
        rib = [n for n, f in (("Short", p.short_tour), ("Long", p.long_tour), ("Longevity", p.longevity),
                              ("Longevity+GC", p.longevity_gc)) if f]
        print(f"[{p.index}] {p.name}: {('Active', 'Retired', 'KIA')[p.status]}, {RANKS[p.rank]}, {p.missions} missions, "
              f"total {p.total}, best {p.best}, last {p.last}")
        print(f"     {medals}; ribbons: {', '.join(rib) or 'none'}")
        for n, s, events in p.log:
            print(f"       #{n:<3} {s:>5}  {'; '.join(events)}")
        for w in check(p, claimed):
            print(f"     WARNING: {w}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("csv", type=Path)
    ap.add_argument("--out", type=Path, default=S.saves / "ROSTER.FIL.generated",
                    help="output file (default %(default)s); never the live game file unless you say so")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    ap.add_argument("--active-index", type=int, default=0, help="header: the pilot preselected on the roster screen")
    ap.add_argument("--dry-run", action="store_true", help="print the report, write nothing")
    a = ap.parse_args(argv)

    if not 0 <= a.active_index < PILOTS:
        sys.exit("--active-index must be 0-9")
    pilots = read_pilots(a.csv)
    report(pilots)
    if a.dry_run:
        return 0
    if a.out.exists() and not a.force:
        sys.exit(f"{a.out} exists; pass --force to overwrite it")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    data = build(pilots, a.active_index)
    a.out.write_bytes(data)
    print(f"wrote {a.out} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
