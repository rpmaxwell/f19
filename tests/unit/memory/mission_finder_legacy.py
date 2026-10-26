"""Frozen copy of scripts/mission_finder.py before WP-4 (git 78752f4), for the equivalence tests. Do not edit."""
"""Find a mission with the wanted shape: reroll START's mission generator until it deals one.

Filters: length bucket (the briefing's FUEL ESTIMATE, which sets the score's fuel multiplier), primary task,
secondary task (or mission numbers), and no time limit. The theater, level, setup and opponents are the
pilot's current assignment (change them in the game's Mission Assignment screen).

How it works (decoded 2026-10-24 from START, see FINDINGS.md "Generator rules"):
  - FUEL ESTIMATE = commData+0x2e (far ptr ds:D066) = 16 x L, L = takeoff->primary + primary->secondary
    + secondary->landing in km (START cs:7A04 / cs:845B). END scores it x9/8 from 7000, x10/8 from 9000,
    x11/8 from 11500 lb, so the buckets are L < 438, 438-562, 563-718, >= 719 km. The generator accepts
    L <= 740 + attempts (up to 999), so long missions need many retries; with an Elite Cold War strike pilot
    x11 came up in 12 % of CE missions, 10 % LB, 5 % NC and never in PG (400 samples each).
  - Every new mission is START's generator (cs:76C8 -> 7738) continuing the same MS C rand stream
    (seed u32 ds:7A50), then the briefing-entry code after it (cs:B61C-B72E) draws a few more numbers. The
    menus in between draw none. So running that code in an x86 emulator (unicorn) on a memory image
    predicts the next missions exactly (all 34 live rerolls of 2026-10-24), and sampling random
    seeds shows whether a combination exists at all.
  - In game, one reroll = Exit Briefing Rm -> Arming Complete -> Select New Mission -> (roster) pilot ->
    Accept assignment -> new mission in the briefing room (cs:B619 runs the generator when ds:CA6A = 1,
    which "Select New Mission" sets). Never send Esc here: on the roster it erases the pilot.
  - Speed: while rerolling, DOSBox-X turbo (fast-forward) is on: 29x real time at 3000 cycles/ms, toggled
    through the CPU menu and restored afterwards (--no-turbo to skip). Each screen gets a key as soon as a
    screenshot identifies it. 2.0 s per reroll (57 in a row on 2026-10-24), against 22 s with fixed waits at
    normal speed. The game reaches each screen ~0.1 s after its key, so screenshots (~0.1 s each) are now
    the limit, not the emulator. This is the only place the project runs the game faster than real time.

  uv run --with unicorn python scripts/mission_finder.py --length x10 --primary destroy --secondary photo --no-time-limit
  uv run --with unicorn python scripts/mission_finder.py ... --check-only           (existence check, no keys)
  uv run --with unicorn python scripts/mission_finder.py ... --snapshot FILE --check-only   (offline, any theater)
Start it with the game in the briefing room, on the arming screen ("Arming Complete" highlighted) or on the
Select New Mission / Intelligence Briefing / Arm Your Plane / Begin Mission menu.
"""
import argparse, random, struct, subprocess, sys, tempfile, time
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "analysis"))
import read_mission_legacy as rm  # noqa: E402
import theaters  # noqa: E402

GAME = theaters.GAME
COMM_PTR, FUEL_EST = 0xD066, 0x2E           # commData far ptr; +0x2e fuel estimate (16 lb per km of L)
SEED = 0x7A50                                # MS C rand state, u32
NEW_MISSION, NEW_MISSION_RET = 0x76C8, 0x072C   # generator entry; a return address after a real call to it
AFTER_GENERATE, AFTER_GENERATE_END = 0xB61C, 0xB72E   # briefing-entry code right after the generator call
NEW_FLAG = 0xCA6A                            # 1 from "Select New Mission" until the generator has run
PILOT_THEATER, PILOT_LEVEL, PILOT_FLAGS, PILOT_OPP = 0x38, 0x3A, 0x3C, 0x3E
THEATER_CODE = rm.THEATER_INDEX              # pilot +0x38 -> CE/NC/LB/PG
LEVELS = ["Cold War", "Limited War", "Conventional War"]
OPPONENTS = ["Green", "Regular", "Veteran", "Elite"]
TASKS = {"photo": {1}, "destroy": {2}, "attack": {2}, "supply": {3}, "landing": {4}, "intercept": {5, 6, 7, 8}}
# (name, lowest fuel estimate); END's fuel multiplier steps
BUCKETS = [("x8", 0), ("x9", 7000), ("x10", 9000), ("x11", 11500)]


def bucket(fuel):
    return [n for n, lo in BUCKETS if fuel >= lo][-1]


class Generator:
    """START's mission generator run in unicorn on a memory image taken while START was running."""

    def __init__(self, mem):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_INTR
        from unicorn import x86_const as X
        self.X = X
        self.ds_phys = rm.start_ds(mem)
        if self.ds_phys is None: raise RuntimeError("START is not in this memory image (take it at a briefing or menu)")
        self.ds = self.ds_phys >> 4; self.cs = self.ds - 0x1000
        self.mem0 = bytes(mem[:0xA0000])
        self.uc = Uc(UC_ARCH_X86, UC_MODE_16)
        self.uc.mem_map(0, 0x110000)
        self.uc.mem_write(0, self.mem0)
        self.uc.hook_add(UC_HOOK_INTR, self._int)
        self.files = {}                      # DOS handle -> [bytes, position]
        off, seg = struct.unpack_from("<HH", mem, self.ds_phys + rm.PILOT_PTR)
        self.pilot = seg * 16 + off

    # --- DOS file calls the generator makes (it reloads the theater .WLD and terrain each time) ---
    def _reg(self, r): return self.uc.reg_read(r)

    def _ret(self, cf=0, **regs):
        X = self.X
        for k, v in regs.items(): self.uc.reg_write(getattr(X, f"UC_X86_REG_{k.upper()}"), v & 0xFFFF)
        fl = self._reg(X.UC_X86_REG_EFLAGS)
        self.uc.reg_write(X.UC_X86_REG_EFLAGS, fl | 1 if cf else fl & ~1)

    def _int(self, uc, n, _):
        X = self.X
        ax = self._reg(X.UC_X86_REG_AX); ah = ax >> 8
        buf = self._reg(X.UC_X86_REG_DS) * 16 + self._reg(X.UC_X86_REG_DX)
        if n != 0x21: raise RuntimeError(f"generator called int {n:#x} (AX {ax:#06x})")
        if ah == 0x3D:                                                    # open
            name = bytes(uc.mem_read(buf, 64)).split(b"\0")[0].decode("latin-1")
            p = next((f for f in GAME.iterdir() if f.name.lower() == name.lower()), None)
            if p is None: return self._ret(1, ax=2)
            h = min(set(range(5, 20)) - set(self.files)); self.files[h] = [p.read_bytes(), 0]   # lowest free, like DOS
            self._ret(0, ax=h)
        elif ah == 0x3F:                                                  # read
            f = self.files[self._reg(X.UC_X86_REG_BX)]
            chunk = f[0][f[1]:f[1] + self._reg(X.UC_X86_REG_CX)]; f[1] += len(chunk)
            uc.mem_write(buf, chunk); self._ret(0, ax=len(chunk))
        elif ah == 0x42:                                                  # seek
            f = self.files[self._reg(X.UC_X86_REG_BX)]
            off = self._reg(X.UC_X86_REG_CX) << 16 | self._reg(X.UC_X86_REG_DX)
            f[1] = [off, f[1] + off, len(f[0]) + off][ax & 0xFF]
            self._ret(0, ax=f[1], dx=f[1] >> 16)
        elif ah == 0x3E: self.files.pop(self._reg(X.UC_X86_REG_BX), None); self._ret(0)   # close
        elif ah == 0x43: self._ret(0, cx=0x20)                           # attributes: plain file
        elif ah == 0x44: self._ret(0, dx=0)                              # ioctl: a disk file
        else: raise RuntimeError(f"generator called DOS function {ah:#04x}")

    # --- state ---
    def reset(self, seed=None, theater=None, level=None, opponent=None):
        """Back to the memory image; optionally another rand seed / pilot assignment."""
        self.uc.mem_write(0, self.mem0)
        if seed is not None: self.uc.mem_write(self.ds_phys + SEED, struct.pack("<I", seed))
        for field, v in ((PILOT_THEATER, theater), (PILOT_LEVEL, level), (PILOT_OPP, opponent)):
            if v is not None: self.uc.mem_write(self.pilot + field, struct.pack("<H", v))

    def u16(self, phys): return struct.unpack("<H", self.uc.mem_read(phys, 2))[0]

    def settings(self):
        return dict(theater=THEATER_CODE[self.u16(self.pilot + PILOT_THEATER)], level=self.u16(self.pilot + PILOT_LEVEL),
                    strike=bool(self.u16(self.pilot + PILOT_FLAGS) & 1), opponent=self.u16(self.pilot + PILOT_OPP))

    def _run(self, start, until, sp, bp):
        X = self.X
        for r, v in ((X.UC_X86_REG_CS, self.cs), (X.UC_X86_REG_DS, self.ds), (X.UC_X86_REG_ES, self.ds),
                     (X.UC_X86_REG_SS, self.ds), (X.UC_X86_REG_SP, sp), (X.UC_X86_REG_BP, bp)):
            self.uc.reg_write(r, v)
        self.uc.emu_start(self.cs * 16 + start, self.cs * 16 + until, count=200_000_000)

    def generate(self):
        """One new mission, as entering the briefing after "Select New Mission" makes it: cs:B5F0 clears four
        flags and calls the generator (76C8), then B61C-B72E draws more random numbers (random(3) records get
        flagged and some of them zeroed). That second part shifts the rand stream for the next reroll."""
        ds = self.ds_phys
        for a in (0x98E8, 0x9922, 0xBE48, 0xC977): self.uc.mem_write(ds + a, b"\0")
        self.uc.mem_write(ds + 0xFEFE, struct.pack("<H", NEW_MISSION_RET))
        self._run(NEW_MISSION, NEW_MISSION_RET, 0xFEFE, 0xFF00)
        m = self.mission()
        self._run(AFTER_GENERATE, AFTER_GENERATE_END, 0xFDC0, 0xFE00)   # its locals are bp-8 .. bp-0x14
        return m

    def mission(self):
        return decode(bytes(self.uc.mem_read(0, 0xA0000)), self.ds_phys)


def decode(mem, ds):
    """The mission START holds now: tasks, mission numbers, targets, fuel estimate, time limit."""
    br = rm.briefing(mem, ds)
    off, seg = struct.unpack_from("<HH", mem, ds + COMM_PTR)
    fuel = struct.unpack_from("<H", mem, seg * 16 + off + FUEL_EST)[0]
    th = THEATER_CODE.get(br["theater_index"])
    names = theaters.places(th)["names"] if th else []
    def place(i):
        n, x, y, kind, f1, f2, f3, cls = struct.unpack_from("<8H", mem, ds + rm.START_RECS + 16 * i)
        nm = lambda k: names[k - 1] if 0 < k <= len(names) else ""
        return dict(place=nm(n) or ("carrier" if f1 & 0x200 else ""), object=nm(cls & 0x7F) or f"class {cls}", record=i)
    p, s = br["blocks"]
    timed = bool(p["deadline_raw"]) or p["type"] in (5, 6, 7)
    return dict(theater=th, primary=dict(mission=p["mission"], type=p["type"], **place(p["target"])),
                secondary=dict(mission=s["mission"], type=s["type"], **place(s["target"])),
                takeoff=place(p["base"])["place"], landing=place(s["base"])["place"],
                fuel_estimate=fuel, length_km=fuel // 16, bucket=bucket(fuel), time_limit=timed,
                deadline=br["times"].get("deadline"), start=br["times"]["start"])


def key(m):
    """What identifies a generated mission (for checking the game against the prediction)."""
    return (m["primary"]["mission"], m["primary"]["record"], m["secondary"]["mission"], m["secondary"]["record"],
            m["fuel_estimate"])


def describe(m):
    task = lambda b: rm.MISSION_TYPE.get(b["type"], f"type {b['type']}")
    return (f"{m['takeoff']} -> #{m['primary']['mission']} {task(m['primary'])} {m['primary']['object']} at {m['primary']['place']}"
            f" -> #{m['secondary']['mission']} {task(m['secondary'])} {m['secondary']['object']} at {m['secondary']['place']}"
            f" -> {m['landing']};  fuel est {m['fuel_estimate']} lb ({m['length_km']} km, {m['bucket']})"
            + (f";  DEADLINE {m['deadline'] or 'yes'}" if m["time_limit"] else ""))


class Spec:
    def __init__(self, length=None, primary=None, secondary=None, secondary_missions=None, no_time_limit=False):
        self.length, self.no_time_limit = length, no_time_limit
        self.primary = TASKS[primary] if primary else None
        self.secondary = TASKS[secondary] if secondary else None
        self.secondary_missions = set(secondary_missions or [])
        if length:
            names = [n for n, _ in BUCKETS]
            b = length.rstrip("+")
            if b not in names: raise ValueError(f"--length: one of {', '.join(names)} (add + for 'or longer')")
            self.buckets = set(names[names.index(b):]) if length.endswith("+") else {b}

    def match(self, m):
        return ((not self.length or m["bucket"] in self.buckets)
                and (not self.primary or m["primary"]["type"] in self.primary)
                and (not self.secondary or m["secondary"]["type"] in self.secondary)
                and (not self.secondary_missions or m["secondary"]["mission"] in self.secondary_missions)
                and not (self.no_time_limit and m["time_limit"]))

    def __str__(self):
        parts = [f"length {self.length}" if self.length else "",
                 f"primary {'/'.join(k for k, v in TASKS.items() if v == self.primary)}" if self.primary else "",
                 f"secondary {'/'.join(k for k, v in TASKS.items() if v == self.secondary)}" if self.secondary else "",
                 f"secondary mission in {sorted(self.secondary_missions)}" if self.secondary_missions else "",
                 "no time limit" if self.no_time_limit else ""]
        return ", ".join(p for p in parts if p) or "any mission"


def survey(gen, spec, n, **assignment):
    """Generate n missions from random seeds: how often the spec matches, and the bucket mix."""
    rng, hits, buckets, examples = random.Random(1), 0, Counter(), []
    for _ in range(n):
        gen.reset(seed=rng.getrandbits(32), **assignment)
        m = gen.generate()
        buckets[m["bucket"]] += 1
        if spec.match(m):
            hits += 1
            if len(examples) < 3: examples.append(m)
    gen.reset()
    return hits, buckets, examples


def predict(gen, spec, limit):
    """Missions the game will deal next, from its current rand state: (index of the first match or None, list)."""
    gen.reset(); seq = []
    for k in range(1, limit + 1):
        seq.append(gen.generate())
        if spec.match(seq[-1]): return k, seq
    return None, seq


# --- driving the game ---

BROWN = (159, 90, 31)          # unselected menu text
BRIEF_ROWS = [511, 551, 591, 631, 671, 711, 751]   # Mission Targets .. Exit Briefing Rm (x 945-1250)
MENU_ROWS = [435, 503, 571, 639]                   # Select New Mission .. Begin Mission (x 470-860)
HIGHLIGHT = [(85, 85, 246), (236, 98, 248), (138, 252, 253), (76, 167, 169)]   # a selected item cycles through these
BLUE, RED = (85, 85, 246), [(255, 85, 85), (236, 98, 98), (232, 96, 96)]


def window_id():
    src = ('import CoreGraphics\nlet l = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as! [[String: Any]]\n'
           'for w in l { if let o = w["kCGWindowOwnerName"] as? String, o.lowercased().contains("dosbox"), '
           '(w["kCGWindowLayer"] as? Int) == 0 { print(w["kCGWindowNumber"]!) } }\n')
    with tempfile.NamedTemporaryFile("w", suffix=".swift", delete=False) as f: f.write(src)
    out = subprocess.run(["swift", f.name], capture_output=True, text=True).stdout.split()
    if not out: raise RuntimeError("no DOSBox-X window on screen")
    return out[0]


class Screen:
    def __init__(self, win):
        self.win, self.path = win, Path(tempfile.gettempdir()) / "mission_finder.png"

    def grab(self):
        import numpy as np
        from PIL import Image
        subprocess.run(["screencapture", "-x", "-o", "-l", self.win, str(self.path)], check=True)
        im = Image.open(self.path).convert("RGB")
        if im.width != 1280: im = im.resize((1280, 856), Image.NEAREST)
        return np.asarray(im).astype(int)

    @staticmethod
    def brown(a, x0, x1, y):
        import numpy as np
        return int((np.abs(a[y - 12:y + 12, x0:x1] - BROWN).sum(2) < 30).sum())

    def highlighted(self, rows, x0, x1, a=None, ink_min=1000):
        """Index of the one highlighted menu row (no brown ink, every other row brown), else None.
        Unselected rows: >= 1449 brown pixels in the briefing room, >= 2240 on the options menu; the
        roster's names, at the options menu's rows, only 800-1500."""
        a = self.grab() if a is None else a
        ink = [self.brown(a, x0, x1, y) for y in rows]
        sel = [i for i, b in enumerate(ink) if b < 150]
        return sel[0] if len(sel) == 1 and all(b > ink_min for i, b in enumerate(ink) if i != sel[0]) else None

    def where(self):
        """("briefing", row) / ("menu", row) / ("arming" | "objectives" | "roster" | "assignment", None) / (None, None).
        Thresholds are pixel counts from 1280x856 window captures (2026-10-24); None = drawing / unknown."""
        a = self.grab()
        if (row := self.highlighted(BRIEF_ROWS, 945, 1250, a)) is not None: return "briefing", row
        if (row := self.highlighted(MENU_ROWS, 470, 860, a, 1800)) is not None: return "menu", row
        n = lambda box, cols: self.count(a, box, cols)
        if n((880, 1215, 560, 585), HIGHLIGHT) > 1500: return "arming", None          # "Arming Complete" lit
        if n((400, 860, 770, 795), [BLUE]) > 2000 and n((115, 350, 70, 110), RED) > 2000:
            return "objectives", None                                           # "Press Selector to continue"
        if n((640, 1030, 578, 606), [BROWN]) > 2000 and n((640, 1015, 622, 650), HIGHLIGHT) > 1500 \
                and n((700, 850, 812, 848), [BROWN]) > 500:
            return "assignment", None                                           # Change / Accept assignment, Risk
        if n((140, 690, 470, 760), [BROWN]) > 22000 and n((140, 470, 390, 425), [(0, 0, 0)]) > 1500:
            return "roster", None                                               # pilot names, "F19 Duty Roster"
        return None, None

    @staticmethod
    def count(a, box, cols):
        import numpy as np
        x0, x1, y0, y1 = box
        return sum(int((np.abs(a[y0:y1, x0:x1] - c).sum(2) < 30).sum()) for c in cols)


class Turbo:
    """DOSBox-X's fast-forward (CPU menu > "Turbo (fast-forward)", mapper_speedlock2): emulated time stops
    waiting for the wall clock. Measured 29x real time at 3000 cycles/ms on 2026-10-24. Toggled through the
    menu by accessibility, so it needs no DOSBox-X rebuild and no window focus; keys injected through the
    automation socket don't trip "stop turbo on key" (that watches SDL key events only)."""
    ITEM = 'first menu item of menu "CPU" of menu bar 1 whose name starts with "Turbo (fast-forward)"'

    @classmethod
    def _osa(cls, verb):
        script = (f'tell application "System Events" to tell process "dosbox-x"\nset m to {cls.ITEM}\n'
                  f'{verb} m\nend tell')
        for attempt in range(4):        # System Events sometimes can't find the menu bar for a moment (-1728)
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if not r.returncode: return r.stdout.strip()
            time.sleep(0.5)
        raise RuntimeError(f"DOSBox-X menu: {r.stderr.strip()}")

    @classmethod
    def on(cls):
        return cls._osa('return value of attribute "AXMenuItemMarkChar" of') not in ("", "missing value")

    @classmethod
    def set(cls, want):
        for _ in range(3):
            if cls.on() == want: return
            cls._osa("click")
            time.sleep(0.3)
        raise RuntimeError(f"could not switch turbo {'on' if want else 'off'}")


class Game:
    def __init__(self, turbo=False):
        from dosbox_automation import Dosbox
        self.db = Dosbox()
        self.screen = Screen(window_id())
        self.turbo = turbo
        # Under turbo 0.1 s of wall time is ~3 s of game time, past the keyboard's auto-repeat delay, so a
        # tap has to be a bare press + release. Settle: wall time a new screen gets before it is sent a key.
        # Under turbo the confirming screenshot alone is ~3 s of game time, so no extra settle.
        self.hold, self.settle = (0.0, 0.0) if turbo else (0.1, 0.6)

    def block(self):
        return self.db.peek("%X" % self.ds, "B946", 0x24)

    def tap(self, k):
        self.db.tap(k, self.hold)

    def flag(self):
        return self.db.peek("%X" % self.ds, "%X" % NEW_FLAG, 1)[0]

    def reroll(self, timeout=90):
        """One "Select New Mission" round trip, from wherever in the loop the game is; returns once START has
        generated the new mission (ds:CA6A back to 0). Each screen gets one key once it has been seen
        settled, and the same key again only if the screen hasn't changed 5 s later (a key it dropped).
        Keys never go to a screen it can't identify: an Enter that landed on the options menu while it
        was still drawing would start the flight."""
        end, chosen, acted = time.time() + timeout, False, None
        while time.time() < end:
            if self.flag(): chosen = True
            elif chosen: return
            state = self.screen.where()
            if state[0] is None: time.sleep(0.1); continue
            if acted and acted[0] == state and time.time() - acted[1] < 5: time.sleep(0.1); continue
            if not acted or acted[0] != state:
                time.sleep(self.settle)
                if self.screen.where() != state: continue
            screen, row = state
            keys = ["enter"]                                     # objectives -> room, Arming Complete, pilot, Accept
            if screen == "briefing" and row not in (0, 6):      # 0 = targets flashing (Enter shows objectives)
                keys = ["down"] * (6 - row)                      # to Exit Briefing Rm; neither menu wraps
            elif screen == "menu" and row != 0:
                keys = ["up"] * row                              # to Select New Mission
            for k in keys: self.tap(k)
            acted = (state, time.time())
        raise RuntimeError(f"no new mission after {timeout} s (screen {self.screen.where()}; see {self.screen.path})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="length buckets (FUEL ESTIMATE -> score multiplier): x8 < 7000 lb, x9 7000+, "
                                        "x10 9000+, x11 11500+ (L >= 719 km)")
    ap.add_argument("--length", help="x8 / x9 / x10 / x11, or e.g. x10+ for that bucket or longer")
    ap.add_argument("--primary", choices=sorted(TASKS), help="primary task (attack = destroy)")
    ap.add_argument("--secondary", choices=sorted(TASKS), help="secondary task")
    ap.add_argument("--secondary-mission", type=int, nargs="+", metavar="N", help="secondary mission number(s), MISSIONS.md")
    ap.add_argument("--no-time-limit", action="store_true", help="skip missions with a deadline ('before HH:MM hours')")
    ap.add_argument("--check-only", action="store_true", help="only say whether the combination exists; send no keys")
    ap.add_argument("--snapshot", help="memory image to use instead of the running game (implies --check-only)")
    ap.add_argument("--theater", help="with --snapshot: check another theater (CE/NC/LB/PG)")
    ap.add_argument("--samples", type=int, default=1000, help="random missions for the existence check")
    ap.add_argument("--max-rerolls", type=int, default=300, help="look this many rerolls ahead")
    ap.add_argument("--no-turbo", action="store_true", help="reroll at normal speed (default: DOSBox-X turbo while rerolling)")
    a = ap.parse_args()
    spec = Spec(a.length, a.primary, a.secondary, a.secondary_mission, a.no_time_limit)

    mem = Path(a.snapshot).read_bytes() if a.snapshot else rm.read_memory()
    gen = Generator(mem)
    st = gen.settings()
    assignment = {}
    if a.theater:
        if not a.snapshot: sys.exit("--theater needs --snapshot: in the game, change it in the Mission Assignment screen")
        assignment["theater"] = {v: k for k, v in THEATER_CODE.items()}[theaters.code(a.theater)]
        st["theater"] = theaters.code(a.theater)
    print(f"assignment: {st['theater']}, {LEVELS[st['level']] if st['level'] < 3 else st['level']}, "
          f"{'strike' if st['strike'] else 'air-to-air'}, {OPPONENTS[st['opponent']] if st['opponent'] < 4 else st['opponent']}")
    print(f"looking for: {spec}")

    t = time.time()
    hits, buckets, examples = survey(gen, spec, a.samples, **assignment)
    mix = ", ".join(f"{n} {100 * buckets[n] / a.samples:.0f}%" for n, _ in BUCKETS if buckets[n])
    print(f"existence check: {hits} of {a.samples} random missions match ({time.time() - t:.0f} s); lengths: {mix}")
    if not hits:
        sys.exit(f"ERROR: no such mission in {st['theater']} with this assignment (0 of {a.samples}). "
                 "Try again with different parameters.")
    for m in examples: print("  e.g.", describe(m))
    if a.check_only or a.snapshot: return

    cur = decode(mem, gen.ds_phys)
    print("current:", describe(cur))
    if spec.match(cur): print("The current mission already matches."); return
    turbo_before = Turbo.on() if not a.no_turbo else None
    try:
        if not a.no_turbo: Turbo.set(True)
        follow(gen, spec, a, hits, turbo=not a.no_turbo)
    finally:
        if turbo_before is not None: Turbo.set(turbo_before)    # turbo is for rerolling only


def follow(gen, spec, a, hits, turbo):
    """Predict the first match, reroll the game that many times, checking each mission against the prediction."""
    for attempt in range(3):                         # re-predict from a fresh image if the game ever drifts
        k, seq = predict(gen, spec, a.max_rerolls)
        if k is None:
            sys.exit(f"ERROR: none of the next {a.max_rerolls} missions match (about 1 in {a.samples // hits} does). "
                     "Try again with a higher --max-rerolls or different parameters.")
        print(f"match on reroll {k}: {describe(seq[k - 1])}")
        game = Game(turbo); game.ds = gen.ds; t0 = time.time()
        for i, want in enumerate(seq, 1):
            game.reroll()
            got = block_key(game.block())
            print(f"  reroll {i}/{k}: #{got[0]} + #{got[2]}  ({(time.time() - t0) / i:.1f} s each)"
                  + ("" if got == key(want)[:4] else "  NOT the predicted mission"))
            if got != key(want)[:4]: break
        game.db.close()                              # the automation port serves one client at a time
        mem = rm.read_memory()
        if i == k and got == key(want)[:4]:
            found = decode(mem, gen.ds_phys)
            print("FOUND:", describe(found))
            if not spec.match(found): sys.exit("ERROR: the mission in the game does not match after all; run again.")
            return
        print("The game left the predicted sequence; re-predicting from its memory.")
        gen = Generator(mem)
    sys.exit("ERROR: could not follow the game's mission sequence; run the tool again from the briefing room.")

def block_key(b):
    """(primary mission, primary target record, secondary mission, secondary target record) from ds:B946."""
    pt, pm, st, sm = (struct.unpack_from("<H", b, o)[0] for o in (2, 8, 0x14, 0x1A))
    return (pm, pt, sm, st)

if __name__ == "__main__":
    main()
