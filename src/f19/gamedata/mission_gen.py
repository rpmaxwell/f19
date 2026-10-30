"""START's mission generator, emulated offline: predict missions and choose one by its rand seed (WP-4b).

How it works (decoded 2026-10-24 from START, docs/game/mission-generation.md "Generator rules"):
  - FUEL ESTIMATE = commData+0x2e (far ptr ds:D066) = 16 x L, L = takeoff->primary + primary->secondary
    + secondary->landing in km. END scores it x9/8 from 7000, x10/8 from 9000, x11/8 from 11500 lb.
  - Every new mission is START's generator (cs:76C8 -> 7738) continuing the same MS C rand stream (seed u32
    ds:7A50), then the briefing-entry code after it (cs:B61C-B72E) draws a few more numbers. The menus in between draw
    none. So running that code in an x86 emulator (unicorn) on a memory image taken while START runs predicts the next
    missions exactly (34 of 34 live rerolls, 2026-10-24), and running it from other seeds shows what each seed deals.
  - Selection by seed (this module, replacing the screenshot-driven reroll loop): find_seed() searches seeds
    offline for one whose mission matches a Spec; select_by_seed() (live) POKEs that seed into ds:7A50, sends one
    "Select New Mission" round and reads the mission back to check it against the prediction.

unicorn is optional (the `missions` dependency group): only Generator imports it, when it is built.

Layering: gamedata may import only core, so this module carries its own small decoder of START's briefing block and
of the .WLD name list (memory.mission and analysis/theaters have the same; tests/unit/memory checks they agree).
"""
import random
import re
import struct
import time
from collections import Counter

from f19.core.settings import settings as _settings
from f19.core.theater import Theater

# START data segment (same values as f19.memory.addresses START_*; that layer can't be imported from here)
DGROUP = 0x1010
BLOCK, BLOCK_LEN, DEADLINE = 0xB946, 0x12, 0xB956
START_RECS = 0xB38E
PILOT_PTR = 0x991C
CLOCK_START, CLOCK_DEADLINE = 0x4DB6, 0x4DBC
COMM_PTR, FUEL_EST = 0xD066, 0x2E           # commData far ptr; +0x2e fuel estimate (16 lb per km of L)
SEED = 0x7A50                                # MS C rand state, u32
NEW_MISSION, NEW_MISSION_RET = 0x76C8, 0x072C   # generator entry; a return address after a real call to it
AFTER_GENERATE, AFTER_GENERATE_END = 0xB61C, 0xB72E   # briefing-entry code right after the generator call
GENERATE_CLEAR = (0x98E8, 0x9922, 0xBE48, 0xC977)
NEW_FLAG = 0xCA6A                            # 1 from "Select New Mission" until the generator has run
PILOT_THEATER, PILOT_LEVEL, PILOT_FLAGS, PILOT_OPP = 0x38, 0x3A, 0x3C, 0x3E
THEATER_CODE = {t.index: t.value for t in Theater}      # pilot +0x38 -> LB/PG/NC/CE
LEVELS = ["Cold War", "Limited War", "Conventional War"]
OPPONENTS = ["Green", "Regular", "Veteran", "Elite"]
MISSION_TYPE = {1: "photograph", 2: "destroy", 3: "supply drop", 4: "landing",
                5: "intercept (5)", 6: "intercept (6)", 7: "intercept (7)", 8: "intercept (8)"}
TASKS = {"photo": {1}, "destroy": {2}, "attack": {2}, "supply": {3}, "landing": {4}, "intercept": {5, 6, 7, 8}}
# (name, lowest fuel estimate); END's fuel multiplier steps
BUCKETS = [("x8", 0), ("x9", 7000), ("x10", 9000), ("x11", 11500)]

# MS C rand(): state = state * 214013 + 2531011 (mod 2^32), returns (state >> 16) & 0x7FFF
RAND_A, RAND_C = 214013, 2531011
RAND_A_INV = pow(RAND_A, -1, 1 << 32)


def rand_step(s, n=1):
    for _ in range(n): s = (s * RAND_A + RAND_C) & 0xFFFFFFFF
    return s


def rand_back(s, n=1):
    for _ in range(n): s = ((s - RAND_C) * RAND_A_INV) & 0xFFFFFFFF
    return s


def bucket(fuel):
    return [n for n, lo in BUCKETS if fuel >= lo][-1]


# ------------------------------------------------------------------------------------------ START image decoding
def start_ds(mem):
    """Physical address of START's data segment, or None (= f19.memory.mission.start_ds)."""
    for p in range(0x60, 0xA000):
        o = p * 16
        if mem[o] in (0x4D, 0x5A) and mem[o + 8:o + 13] == b"START" and struct.unpack_from("<H", mem, o + 1)[0] == p + 1:
            ds = (p + 1 + DGROUP) * 16
            if mem[ds + 8:ds + 19] == b"MS Run-Time" and mem[o + 16:o + 18] == b"\xcd\x20":
                return ds
    return None


def _blocks(mem, ds):
    out = []
    for k in range(2):
        typ, target, base, code, number = struct.unpack_from("<5H", mem, ds + BLOCK + BLOCK_LEN * k)
        out.append(dict(type=typ, target=target, base=base, mission=number))
    return out


_NAMES = {}


def wld_names(theater, game_dir=None):
    """The .WLD name list (name_id i -> names[i - 1]), as analysis/theaters.parse_wld reads it.
    TODO(after WP-3 merges): use gamedata's .WLD loader."""
    th = Theater.parse(theater)
    key = (th, str(game_dir or _settings.game_dir))
    if key not in _NAMES:
        d = ((game_dir or _settings.game_dir) / th.wld).read_bytes()
        first = re.search(rb"[A-Z][a-z]{2,}\x00", d).start()
        _NAMES[key] = [n.decode("latin-1") for n in d[first:].split(b"\x00")]
    return _NAMES[key]


def sa14_cell(theater, x, y):
    """(cx, cy) when the 32 km cell holding the point (game units) is an SA-14 cell (territory byte bit 0x10: by day it fires
    SA-14s at a plane inside it, docs/game/memory-map.md "SA-14 cells"), else None."""
    from .wld import grid
    cx, cy = int(x) >> 11, int(y) >> 11
    return (cx, cy) if theater and 0 <= cx < 16 and 0 <= cy < 16 and grid(theater)[cy, cx] & 0x10 else None


def decode(mem, ds, names=None):
    """The mission START holds now: tasks, mission numbers, targets, fuel estimate, time limit.
    names: the theater's name list (default: read from the game's .WLD)."""
    off, seg = struct.unpack_from("<HH", mem, ds + PILOT_PTR)
    pilot = seg * 16 + off
    th = THEATER_CODE.get(struct.unpack_from("<H", mem, pilot + PILOT_THEATER)[0])
    p, s = _blocks(mem, ds)
    deadline_raw = struct.unpack_from("<H", mem, ds + DEADLINE)[0]
    clock = lambda o: mem[ds + o:ds + o + 5].decode("ascii", "replace")
    off, seg = struct.unpack_from("<HH", mem, ds + COMM_PTR)
    fuel = struct.unpack_from("<H", mem, seg * 16 + off + FUEL_EST)[0]
    names = names if names is not None else (wld_names(th) if th else [])

    def place(i):
        n, x, y, kind, f1, f2, f3, cls = struct.unpack_from("<8H", mem, ds + START_RECS + 16 * i)
        nm = lambda k: names[k - 1] if 0 < k <= len(names) else ""
        return dict(place=nm(n) or ("carrier" if f1 & 0x200 else ""), object=nm(cls & 0x7F) or f"class {cls}", record=i,
                    x=x, y=y, sa14=sa14_cell(th, x, y))
    timed = bool(deadline_raw) or p["type"] in (5, 6, 7)
    return dict(theater=th, primary=dict(mission=p["mission"], type=p["type"], **place(p["target"])),
                secondary=dict(mission=s["mission"], type=s["type"], **place(s["target"])),
                takeoff=place(p["base"])["place"], landing=place(s["base"])["place"],
                fuel_estimate=fuel, length_km=fuel // 16, bucket=bucket(fuel), time_limit=timed,
                deadline=clock(CLOCK_DEADLINE) if deadline_raw else None, start=clock(CLOCK_START))


def key(m):
    """What identifies a generated mission (for checking the game against the prediction)."""
    return (m["primary"]["mission"], m["primary"]["record"], m["secondary"]["mission"], m["secondary"]["record"],
            m["fuel_estimate"])


def block_key(b):
    """(primary mission, primary target record, secondary mission, secondary target record) from the 0x24 bytes at
    START ds:B946."""
    pt, pm, st, sm = (struct.unpack_from("<H", b, o)[0] for o in (2, 8, 0x14, 0x1A))
    return (pm, pt, sm, st)


def describe(m):
    task = lambda b: MISSION_TYPE.get(b["type"], f"type {b['type']}")
    cells = ", ".join(f"{r} in SA-14 cell {m[r]['sa14']}" for r in ("primary", "secondary") if m[r].get("sa14"))
    return (f"{m['takeoff']} -> #{m['primary']['mission']} {task(m['primary'])} {m['primary']['object']} at {m['primary']['place']}"
            f" -> #{m['secondary']['mission']} {task(m['secondary'])} {m['secondary']['object']} at {m['secondary']['place']}"
            f" -> {m['landing']};  fuel est {m['fuel_estimate']} lb ({m['length_km']} km, {m['bucket']})"
            + (f";  DEADLINE {m['deadline'] or 'yes'}" if m["time_limit"] else "")
            + (f";  !! {cells}" if cells else ""))


# --------------------------------------------------------------------------------------------------- Generator
class Generator:
    """START's mission generator run in unicorn on a memory image taken while START was running."""

    def __init__(self, mem, game_dir=None):
        from unicorn import Uc, UC_ARCH_X86, UC_MODE_16, UC_HOOK_INTR
        from unicorn import x86_const as X
        self.X = X
        self.game_dir = game_dir or _settings.game_dir
        self.ds_phys = start_ds(mem)
        if self.ds_phys is None: raise RuntimeError("START is not in this memory image (take it at a briefing or menu)")
        self.ds = self.ds_phys >> 4; self.cs = self.ds - 0x1000
        self.mem0 = bytes(mem[:0xA0000])
        self.uc = Uc(UC_ARCH_X86, UC_MODE_16)
        self.uc.mem_map(0, 0x110000)
        self.uc.mem_write(0, self.mem0)
        self.uc.hook_add(UC_HOOK_INTR, self._int)
        self.files = {}                      # DOS handle -> [bytes, position]
        off, seg = struct.unpack_from("<HH", mem, self.ds_phys + PILOT_PTR)
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
            p = next((f for f in self.game_dir.iterdir() if f.name.lower() == name.lower()), None)
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

    def seed(self):
        """The rand state now (u32 ds:7A50)."""
        return struct.unpack("<I", self.uc.mem_read(self.ds_phys + SEED, 4))[0]

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
        for a in GENERATE_CLEAR: self.uc.mem_write(ds + a, b"\0")
        self.uc.mem_write(ds + 0xFEFE, struct.pack("<H", NEW_MISSION_RET))
        self._run(NEW_MISSION, NEW_MISSION_RET, 0xFEFE, 0xFF00)
        m = self.mission()
        self._run(AFTER_GENERATE, AFTER_GENERATE_END, 0xFDC0, 0xFE00)   # its locals are bp-8 .. bp-0x14
        return m

    def mission(self):
        return decode(bytes(self.uc.mem_read(0, 0xA0000)), self.ds_phys)

    def from_seed(self, seed, **assignment):
        """The mission one "Select New Mission" deals when ds:7A50 holds `seed` (the image is restored after)."""
        self.reset(seed=seed, **assignment)
        try: return self.generate()
        finally: self.reset()


# ------------------------------------------------------------------------------------------------- Spec
class Spec:
    def __init__(self, length=None, primary=None, secondary=None, secondary_missions=None, no_time_limit=False,
                 sa14_primary=None, sa14_secondary=None):
        """sa14_primary / sa14_secondary: True = that target must sit in an SA-14 cell, False = must not, None = either."""
        self.length, self.no_time_limit = length, no_time_limit
        self.sa14 = {"primary": sa14_primary, "secondary": sa14_secondary}
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
                and not (self.no_time_limit and m["time_limit"])
                and all(want is None or bool(m[role]["sa14"]) == want for role, want in self.sa14.items()))

    def __str__(self):
        parts = [f"length {self.length}" if self.length else "",
                 f"primary {'/'.join(k for k, v in TASKS.items() if v == self.primary)}" if self.primary else "",
                 f"secondary {'/'.join(k for k, v in TASKS.items() if v == self.secondary)}" if self.secondary else "",
                 f"secondary mission in {sorted(self.secondary_missions)}" if self.secondary_missions else "",
                 "no time limit" if self.no_time_limit else "",
                 *(f"{role} {'in' if want else 'not in'} an SA-14 cell" for role, want in self.sa14.items() if want is not None)]
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


def find_seed(spec, gen, tries=2000, rng_seed=1, seeds=None, **assignment):
    """Search seeds offline for one whose next mission matches `spec`: (seed, predicted mission), or None after `tries`
    seeds. seeds: an iterable to try instead of random ones. The generator's image is restored after."""
    rng = random.Random(rng_seed)    # one stream: a Random built inside the generator repeated the same seed every time
    it = seeds if seeds is not None else (rng.getrandbits(32) for _ in range(tries))
    try:
        for n, seed in enumerate(it):
            if n >= tries: break
            gen.reset(seed=seed, **assignment)
            m = gen.generate()
            if spec.match(m): return seed, m
        return None
    finally:
        gen.reset()


def verify_image(mem, max_back=4000, gen=None):
    """Offline check of the emulation against a memory image taken in the briefing: walk the rand state back n draws
    (n = 1 ..), regenerate, and look for the n whose mission AND final rand state equal the image's. Returns
    (n, mission) or None. A hit means Generator reproduces the mission the real game dealt from that state."""
    gen = gen or Generator(mem)
    want, s_img = decode(mem, gen.ds_phys), struct.unpack_from("<I", mem, gen.ds_phys + SEED)[0]
    try:
        s = s_img
        for n in range(1, max_back + 1):
            s = rand_back(s)
            gen.reset(seed=s)
            m = gen.generate()
            if gen.seed() == s_img and key(m) == key(want): return n, m
        return None
    finally:
        gen.reset()


# ------------------------------------------------------------------------------------- live selection (not run)
# Fixed key sequence from START's options menu with "Select New Mission" highlighted (the menu after "Arming
# Complete"): Enter = Select New Mission (ds:CA6A -> 1), Enter on the duty roster = the highlighted pilot, Enter on the
# assignment screen = "Accept assignment"; entering the briefing room runs the generator (ds:CA6A -> 0). NEVER Esc on
# the roster: it erases the pilot.
# TODO(live research): START's menu/cursor variables are unknown, so this sequence cannot check WHERE the game is
# before it starts; it only checks ds:CA6A (and the briefing block) after. Find them by diffing PEEK snapshots while
# moving the cursor (memory.addresses, START_* TODO).
SELECT_NEW_MISSION_KEYS = ["enter", "enter", "enter"]


def select_by_seed(emu, reader, seed, prediction, *, ds_phys=None, keys=SELECT_NEW_MISSION_KEYS, hold=0.1,
                   settle=0.8, timeout=60.0, log=print):
    """LIVE (sends keys and writes memory; run only when the user asked for it, with the game on START's options menu,
    "Select New Mission" highlighted). emu: f19.emulator.Emulator; reader: f19.memory.MemoryReader (mission()
    needs its world). prediction: the mission find_seed returned for `seed`.

      1. POKE the u32 seed at START ds:7A50 (read-back verified), check ds:CA6A is 0
      2. Enter (Select New Mission): ds:CA6A must turn 1
      3. the remaining Enters, until ds:CA6A is back to 0 (the generator ran)
      4. compare the briefing block with the prediction, then read the whole mission with reader.mission()
    Returns (ok, Mission)."""
    if ds_phys is None:
        ds_phys = reader.start_ds()
        if ds_phys is None: raise RuntimeError("START is not running")
    flag = lambda: emu.peek(ds_phys + NEW_FLAG, 1)[0]
    if flag() != 0: raise RuntimeError("ds:CA6A is already 1: a new mission is pending; finish that round first")
    emu.poke(ds_phys + SEED, struct.pack("<I", seed))
    log(f"seed {seed:#010x} written to ds:7A50")
    emu.tap(keys[0], hold); time.sleep(settle)
    if flag() != 1: raise RuntimeError("Select New Mission did not register (ds:CA6A still 0): was it highlighted?")
    end = time.time() + timeout
    for k in keys[1:]:
        emu.tap(k, hold); time.sleep(settle)
    while flag() != 0:
        if time.time() > end: raise RuntimeError(f"no new mission after {timeout:.0f} s (ds:CA6A stays 1)")
        time.sleep(0.2)
    got = block_key(emu.peek(ds_phys + BLOCK, 0x24))
    ok = got == key(prediction)[:4]
    log(f"dealt #{got[0]} + #{got[2]}: {'as predicted' if ok else 'NOT the predicted mission ' + str(key(prediction)[:4])}")
    return ok, reader.mission()
