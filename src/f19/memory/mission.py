"""The current mission from a memory image (scripts/read_mission.py's parsing, WP-4).

The resident launcher keeps a working copy of the theater .WLD in low memory, and the generator writes the mission
into it; during the briefing START also holds the briefing blocks and its own copy of the records. Layouts:
memory.addresses (START_*, WLD_*) and docs/game/memory-map.md.

The memory layer does not read game files: the .WLD bytes, the place names and the radar table come from a `World`
(read_mission.py builds one from analysis/theaters + radar_game; later gamedata + rules provide it):

  class World(Protocol):
      def wld_bytes(self, theater: str) -> bytes | None    # None: that theater's file is missing
      def names(self, theater: str) -> list[str]           # the .WLD name list (name_id i -> names[i - 1])
      def radar(self, kind: int) -> tuple | None           # (system, "P" / "D", range_km) for a radar code

Warnings go to stderr exactly as read_mission printed them.
"""
import struct
import sys
from typing import Protocol

from f19.core.theater import Theater
from f19.memory import addresses as A

MISSION_TYPE = {1: "photograph", 2: "destroy", 3: "supply drop", 4: "landing",
                5: "intercept (5)", 6: "intercept (6)", 7: "intercept (7)", 8: "intercept (8)"}   # 5-8: shoot down a plane
THEATER_INDEX = {t.index: t.value for t in Theater}      # pilot +0x38 -> LB / PG / NC / CE


class World(Protocol):
    def wld_bytes(self, theater: str) -> bytes | None: ...
    def names(self, theater: str) -> list: ...
    def radar(self, kind: int) -> tuple | None: ...


def theater_code(th):
    """'pg', 'GULF', Theater.PG, ... -> 'PG'."""
    return Theater.parse(th).value


def takeoff_heading(theater_index, carrier):
    """Heading (deg) the plane sits at on the takeoff runway / catapult (EGAME 0x1FC1): 0 if the theater index has
    bit 0 set, else 180, plus 5.6 deg on a carrier."""
    return (0.0 if theater_index & 1 else 180.0) + (0x400 * 360 / 65536 if carrier else 0.0)


def start_ds(mem):
    """Physical address of START's data segment, or None when START is not running.
    Found through START's DOS memory block (MCB name "START", owner = its PSP)."""
    for p in range(0x60, 0xA000):
        o = p * 16
        if mem[o] in (0x4D, 0x5A) and mem[o + 8:o + 13] == b"START" and struct.unpack_from("<H", mem, o + 1)[0] == p + 1:
            ds = (p + 1 + A.START_DGROUP) * 16
            if mem[ds + 8:ds + 19] == b"MS Run-Time" and mem[o + 16:o + 18] == b"\xcd\x20":
                return ds
    return None


def pilot_phys(mem, ds):
    off, seg = struct.unpack_from("<HH", mem, ds + A.START_PILOT_PTR)
    return seg * 16 + off


def briefing(mem, ds):
    """The two mission blocks, plus the pilot's theater/level settings."""
    pilot = pilot_phys(mem, ds)
    blocks = []
    for k in range(2):
        b = ds + A.START_BLOCK + A.START_BLOCK_LEN * k
        typ, target, base, code, number = struct.unpack_from("<5H", mem, b)
        blocks.append(dict(type=typ, target=target, base=base, code=code, mission=number,
                           onc=mem[b + 10:b + 18].split(b"\0")[0].decode("ascii", "replace")))
    blocks[0]["deadline_raw"] = struct.unpack_from("<H", mem, ds + A.START_DEADLINE)[0]
    pilot_flags = struct.unpack_from("<H", mem, pilot + A.PILOT_FLAGS)[0]
    clock = lambda o: mem[ds + o:ds + o + 5].decode("ascii", "replace")
    times = dict(start=clock(A.START_CLOCK_START))
    if blocks[0]["deadline_raw"]: times.update(deadline=clock(A.START_CLOCK_DEADLINE), window_end=clock(A.START_CLOCK_END))
    return dict(setup="strike" if pilot_flags & 1 else "air-to-air", times=times,
                theater_index=struct.unpack_from("<H", mem, pilot + A.PILOT_THEATER)[0],
                level=struct.unpack_from("<H", mem, pilot + A.PILOT_LEVEL)[0], blocks=blocks)


def mission_table(mem, ds, names=None):
    """START's mission table: what each mission number can be. masks: bit t of theater_mask / bit l of level_mask =
    allowed in theater t / at level l (pilot +0x38 / +0x3A)."""
    cat = mem[ds + A.START_CATEGORY:ds + A.START_CATEGORY + 128]
    cats = {}
    for k, c in enumerate(cat):
        if c and names and 0 < k <= len(names) and names[k - 1]: cats.setdefault(c, []).append(names[k - 1])
    out = []
    for n in range(A.START_N_MISSIONS):
        tmask, lmask, typ, category, code, extra = struct.unpack_from("<6h", mem, ds + A.START_MISSION_TABLE + 12 * n)
        out.append(dict(mission=n, type=typ, type_name=MISSION_TYPE.get(typ, f"type {typ}"),
                        theater_mask=tmask, level_mask=lmask, category=category,
                        category_objects=sorted(set(cats.get(category, []))),
                        code=code + (extra << 8 if extra > 0 else 0), extra=extra))
    return out


def in_memory(mem, world):
    """Theater codes whose .WLD record table appears in a memory image."""
    out = []
    for t in ("CE", "NC", "LB", "PG"):                   # analysis/theaters.WLD_FILE order
        w = world.wld_bytes(t)
        if w and w[A.WLD_ANCHOR[0]:A.WLD_ANCHOR[1]] in mem: out.append(t)
    return out


def detect_theater(mem, world):
    """Theater of the mission in memory: the .WLD copy the launcher holds (and START's pilot record when more than one
    theater's table shows up)."""
    found = in_memory(mem, world)
    if len(found) > 1:
        ds = start_ds(mem)
        pilot = THEATER_INDEX.get(briefing(mem, ds)["theater_index"]) if ds is not None else None
        if pilot in found: return pilot
        raise RuntimeError(f"record tables of {', '.join(found)} are all in memory; pass --theater")
    if not found: raise RuntimeError("no theater record table in memory - is F-19 running (briefing or flight)?")
    return found[0]


def _anchor(world, theater):
    w = world.wld_bytes(theater)
    if not w: raise FileNotFoundError(f"no .WLD for {theater}")
    return w[A.WLD_ANCHOR[0]:A.WLD_ANCHOR[1]]


def parse(mem, theater="CE", copy=-1, *, world):
    """The mission dict (mission_mem.json) from a memory image. copy: which .WLD copy (-1 = the highest)."""
    theater = theater_code(theater)
    anchor = _anchor(world, theater)
    hits, t = [], mem.find(anchor)
    while t >= 0: hits.append(t); t = mem.find(anchor, t + 1)
    if not hits: raise RuntimeError(f"{theater} record table not found - is F-19 running in this theater?")
    t = hits[copy]
    if copy == -1 and len(hits) > 1 and len({mem[h - 0x38 + 0x18:h + 0x200] for h in hits}) > 1:
        print(f"WARNING: {len(hits)} differing mission copies at {[hex(h) for h in hits]}; using {hex(t)} - check against the briefing",
              file=sys.stderr)
    base = t - A.WLD_PLACES
    names = world.names(theater)
    nm = lambda i: names[i - 1] if 0 < i <= len(names) else ""
    obj = lambda cls: nm(cls & 0x7F) or f"class {cls}"
    rec = lambda off: struct.unpack_from("<8H", mem, base + off)
    slots = []
    for off in A.WLD_SLOTS:
        n, x, y, kind, f1, f2, f3, cls = rec(off)
        slots.append(dict(place=nm(n), object=obj(cls), x=x, y=y))
    ds = start_ds(mem) if copy == -1 else None
    out = dict(world_copy_phys=base, slots=slots)
    if ds is not None:
        br = briefing(mem, ds)
        if THEATER_INDEX.get(br["theater_index"], theater) != theater:
            print(f"WARNING: pilot theater index {br['theater_index']} is not {theater}", file=sys.stderr)
        srec = lambda i: struct.unpack_from("<8H", mem, ds + A.START_RECS + 16 * i)

        def place(i):
            n, x, y, kind, f1, f2, f3, cls = srec(i)
            return dict(place=nm(n), object=obj(cls), x=x, y=y, record=i, flags=f1)
        targets = []
        for role, b in zip(("primary", "secondary"), br["blocks"]):
            p = place(b["target"])
            targets.append(dict(role=role, mission=b["mission"], task=MISSION_TYPE.get(b["type"], f"type {b['type']}"),
                                place=p["place"], object=p["object"], x=p["x"], y=p["y"], record=p["record"],
                                onc=b["onc"], code=b["code"]))
        targets[0]["deadline_raw"] = br["blocks"][0]["deadline_raw"]
        if "deadline" in br["times"]: targets[0]["deadline"] = br["times"]["deadline"]
        out.update(start_ds_phys=ds, setup=br["setup"], times=br["times"], theater_index=br["theater_index"], level=br["level"], targets=targets,
                   takeoff=place(br["blocks"][0]["base"]), landing=place(br["blocks"][1]["base"]))
        out["takeoff"]["carrier"] = bool(out["takeoff"]["flags"] & 0x200)
        out["takeoff_heading"] = out["takeoff"]["heading"] = round(takeoff_heading(br["theater_index"], out["takeoff"]["carrier"]), 1)
    else:
        if copy == -1: print("WARNING: START is not running (no briefing in memory): primary/secondary and bases unknown; "
                             "using the +0x18/+0x28 slots, which are not always the targets", file=sys.stderr)
        out["targets"] = [dict(role=r + "?", **s) for r, s in zip(("primary", "secondary"), slots)]
    # Radar records, by table structure rather than class numbers (classes differ per theater): SAM sites (all
    # sharing record 0's class), then the OTH radar (kind 21), then ship slots (f1 == 8). kind = radar code.
    radars = []
    sam_cls = rec(A.WLD_PLACES)[7]
    for k in range(0, 60):
        n, x, y, kind, f1, f2, f3, cls = rec(A.WLD_PLACES + 16 * k)
        is_sam, is_oth, is_ship = cls == sam_cls, kind == 21, f1 == 8
        if not (is_sam or is_oth or is_ship):
            if k > 0 and not is_ship:
                if k > 30: break
                continue
        if kind == 0: continue
        sa, typ, rng = world.radar(kind) or (f"code {kind}", "?", 0)
        pl = nm(n) or ("Patrol Boat" if is_ship else "OTH Radar" if is_oth else "")
        radars.append(dict(place=pl, system=sa, type=typ, range_km=rng, kind=kind, x=x, y=y, record=k))
    out["radars"] = radars
    return out


def parse_all(mem, theater="CE", *, world):
    """Every mission copy in memory (there can be stale ones; see parse)."""
    theater = theater_code(theater)
    anchor = _anchor(world, theater); n, t = 0, mem.find(anchor)
    while t >= 0: n += 1; t = mem.find(anchor, t + 1)
    return [parse(mem, theater, k, world=world) for k in range(n)]
