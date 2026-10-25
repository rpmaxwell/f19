"""Read the current mission (targets with roles, bases, radar sites with types) from memory.

The game's resident launcher (the program that chains SU/START/EGAME/END)
keeps a working copy of the theater .WLD file in low memory, and the mission
generator writes the mission into it (during the briefing, START holds its own
copy of the same records elsewhere). So this works during the briefing
(START running) as well as in flight (EGAME). Found 2026-10-09 by diffing that
copy against CE.WLD with mission 3's briefing as ground truth.

World-copy layout (offsets from the start of the copy = start of CE.WLD bytes):
  record index i lives at +0x08 + 16*i (16 B: u16 name_id, X, Y, kind, 3 x u16, u16 class)
  +0x18 / +0x28 (index 1 / 2): generated target slots ("AirPlane at Mittenwalde")
  +0x38 (index 3 on): place records from the file: name_id, X, Y, kind, f1, f2, f3, class
         class 294 = SAM site  (kind = radar code below, 0 = eliminated)
         class 309 = ship slot (kind = radar code, 0 = no ship this mission)
         class 324 = OTH radar (kind 21)
         class 277 = airbases / search-radar entries (f2/f3 vary per mission; meaning TBD)
  object type name = names[(class & 0x7f) - 1] (294 SAM Radar, 286 Tank Farm, 340 AirPlane, ...)
Coordinates are waypoint-table units (64 per km; player position >> 5).

Mission briefing block (START's data segment, found 2026-10-17; see FINDINGS.md):
  ds:B946 primary, ds:B958 secondary, 0x12 bytes each:
  u16 type, u16 target (record index), u16 base (primary: takeoff, secondary: landing),
  u16 code, u16 mission number, char onc[8]
The target is often a place record (SAM Radar at Tabor = index 12), not the +0x18/+0x28
slots, so the slot order never gave the roles. The block exists only while START runs
(briefing, before the flight); in flight this falls back to the slots and marks roles '?'.
Save the briefing-time json for the plan. It carries "takeoff_heading" (0 / 180 for an airbase,
5.6 / 185.6 for a carrier) for plan.json, decided by the theater alone (see takeoff_heading()).

  read_mission.py [--snapshot FILE] [--out logs/missions/<time>.json] [--missions logs/theater/missions.json]
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import argparse, json, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent / "analysis"))
import theaters  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GAME = S.game_dir
RADAR = {1: ("SA-2", "P", 37), 2: ("SA-5", "P", 43), 3: ("SA-8B", "P", 39), 4: ("SA-10", "D", 140),
         5: ("SA-11", "P", 62), 6: ("SA-12", "D", 108), 7: ("SA-13", "P", 23),
         8: ("SA-N-4", "D", 50), 9: ("SA-N-5", "P", 28), 11: ("SA-N-7", "P", 62), 21: ("OTH", "D", 156)}
# The ranges above are the briefing-map circles (CE / NC). Other codes (SA-N-6 10, and the Gulf's / Libya's Hawk 12,
# Rapier 13, Tiger 14, Seacat 15) come from the game's own tables: name and pulse/doppler from EGAME, and the SAM's
# missile range as range_km (their briefing circles have not been measured). The planner and nav use the game
# tables by code either way; range_km is for display and radar_flight.py.
import radar_game as _rg  # noqa: E402
for _t in range(1, 16):
    if _t not in RADAR:
        _R, _P, _dop = _rg.radar_spec(_t)
        RADAR[_t] = (_rg._EG[_rg.DS_FILE + 0x4C10 + 0x12 * _t:][:8].split(b"\0")[0].decode("latin1"),
                     "D" if _dop else "P", _rg.sam_range_km(_t))
MISSION_TYPE = {1: "photograph", 2: "destroy", 3: "supply drop", 4: "landing",
                5: "intercept (5)", 6: "intercept (6)", 7: "intercept (7)", 8: "intercept (8)"}   # 5-8: all "shoot down a plane"
# START data segment (DGROUP = START's PSP + 0x1010; "MS Run-Time Library" at ds:0008)
BLOCK, BLOCK_LEN = 0xB946, 0x12          # primary; secondary at +0x12
START_RECS = 0xB38E                      # START's own .WLD copy: record i at ds:B38E + 16*i
MISSION_TABLE, N_MISSIONS = 0x4B0C, 56   # 12 B per mission number
CATEGORY = 0xC162                        # u8 per (class & 0x7f): target category the table matches
PILOT_PTR = 0x991C                       # far ptr to the active pilot record: +0x38 theater, +0x3A level
THEATER_INDEX = {0: "LB", 1: "PG", 2: "NC", 3: "CE"}   # EGAME's per-theater file table (lb/pg/nc/ce.xxx) is indexed by it

def takeoff_heading(theater_index, carrier):
    """Heading (deg) the plane sits at on the takeoff runway / catapult. EGAME 0x1FC1 (decoded
    2026-10-17, FINDINGS.md "Takeoff direction"): heading = 0 if pilot+0x38 (theater) bit 0 is set,
    else 0x8000, plus 0x400 (5.6 deg) when the takeoff record is a carrier (flags & 0x200).
    So Gulf/CE take off north, Libya/North Cape south; nothing else (base, wind, targets) matters."""
    return (0.0 if theater_index & 1 else 180.0) + (0x400 * 360 / 65536 if carrier else 0.0)

def read_memory():
    from dosbox_automation import Dosbox
    buf = bytearray()
    with Dosbox() as db:
        for a in range(0, 0xA0000, 64 * 64):
            for i in range(64): db._send(f"PEEK {(a + i * 64) >> 4:X}:0 64")
            for i in range(64): buf += bytes.fromhex(db._recv_line().split(" ", 1)[1])
    return bytes(buf)

def start_ds(mem):
    """Physical address of START's data segment, or None when START is not running.
    Found through START's DOS memory block (MCB name "START", owner = its PSP)."""
    for p in range(0x60, 0xA000):
        o = p * 16
        if mem[o] in (0x4D, 0x5A) and mem[o + 8:o + 13] == b"START" and struct.unpack_from("<H", mem, o + 1)[0] == p + 1:
            ds = (p + 1 + 0x1010) * 16
            if mem[ds + 8:ds + 19] == b"MS Run-Time" and mem[o + 16:o + 18] == b"\xcd\x20":
                return ds
    return None

def briefing(mem, ds):
    """The two mission blocks, plus the pilot's theater/level settings."""
    seg_off = struct.unpack_from("<HH", mem, ds + PILOT_PTR)
    pilot = seg_off[1] * 16 + seg_off[0]
    blocks = []
    for k in range(2):
        b = ds + BLOCK + BLOCK_LEN * k
        typ, target, base, code, number = struct.unpack_from("<5H", mem, b)
        blocks.append(dict(type=typ, target=target, base=base, code=code, mission=number,
                           onc=mem[b + 10:b + 18].split(b"\0")[0].decode("ascii", "replace")))
    # Deadline offset (generator's ds:CA4A >> 4, CA4A in 2 s units) kept after the primary's ONC string; 0 = none.
    blocks[0]["deadline_raw"] = struct.unpack_from("<H", mem, ds + BLOCK + 0x10)[0]
    pilot_flags = struct.unpack_from("<H", mem, pilot + 0x3C)[0]
    # Briefing clock strings START formats from the start time and CA4A (1 unit = 2 game s):
    # 4DB6 mission start, 4DBC deadline (= start when there is none), 4DC2 deadline + 15 min.
    clock = lambda o: mem[ds + o:ds + o + 5].decode("ascii", "replace")
    times = dict(start=clock(0x4DB6))
    if blocks[0]["deadline_raw"]: times.update(deadline=clock(0x4DBC), window_end=clock(0x4DC2))
    return dict(setup="strike" if pilot_flags & 1 else "air-to-air", times=times,
                theater_index=struct.unpack_from("<H", mem, pilot + 0x38)[0],
                level=struct.unpack_from("<H", mem, pilot + 0x3A)[0], blocks=blocks)

def mission_table(mem, ds, names=None):
    """START's mission table: what each mission number can be. masks: bit t of theater_mask /
    bit l of level_mask = allowed in theater t / at level l (pilot +0x38 / +0x3A)."""
    cat = mem[ds + CATEGORY:ds + CATEGORY + 128]
    cats = {}
    for k, c in enumerate(cat):
        if c and names and 0 < k <= len(names) and names[k - 1]: cats.setdefault(c, []).append(names[k - 1])
    out = []
    for n in range(N_MISSIONS):
        tmask, lmask, typ, category, code, extra = struct.unpack_from("<6h", mem, ds + MISSION_TABLE + 12 * n)
        out.append(dict(mission=n, type=typ, type_name=MISSION_TYPE.get(typ, f"type {typ}"),
                        theater_mask=tmask, level_mask=lmask, category=category,
                        category_objects=sorted(set(cats.get(category, []))),
                        code=code + (extra << 8 if extra > 0 else 0), extra=extra))
    return out

def detect_theater(mem):
    """Theater of the mission in memory: the .WLD copy the launcher holds (and START's pilot record
    when more than one theater's table shows up)."""
    found = theaters.in_memory(mem)
    if len(found) > 1:
        ds = start_ds(mem)
        pilot = THEATER_INDEX.get(briefing(mem, ds)["theater_index"]) if ds is not None else None
        if pilot in found: return pilot
        raise RuntimeError(f"record tables of {', '.join(found)} are all in memory; pass --theater")
    if not found: raise RuntimeError("no theater record table in memory - is F-19 running (briefing or flight)?")
    return found[0]

def parse(mem, theater="CE", copy=-1):
    theater = theaters.code(theater)
    wld = theaters.wld_bytes(theater)
    # Locate the record table by its first record's static name/X/Y (the copy's
    # header and name list are not always laid out as in the file: during the
    # briefing START keeps the names elsewhere and the header's first bytes differ).
    # There can be more than one copy: 2026-10-15 a low-memory copy (0x18D52) still held a
    # different mission while the launcher copy (0x4865E) matched the briefing. Use the
    # highest one and warn when they differ.
    hits, t = [], mem.find(wld[0x38:0x3E])
    while t >= 0: hits.append(t); t = mem.find(wld[0x38:0x3E], t + 1)
    if not hits: raise RuntimeError(f"{theater} record table not found - is F-19 running in this theater?")
    t = hits[copy]
    if copy == -1 and len(hits) > 1 and len({mem[h - 0x38 + 0x18:h + 0x200] for h in hits}) > 1:
        print(f"WARNING: {len(hits)} differing mission copies at {[hex(h) for h in hits]}; using {hex(t)} - check against the briefing",
              file=sys.stderr)
    base = t - 0x38
    names = theaters.places(theater)["names"]
    nm = lambda i: names[i - 1] if 0 < i <= len(names) else ""
    obj = lambda cls: nm(cls & 0x7F) or f"class {cls}"
    rec = lambda off: struct.unpack_from("<8H", mem, base + off)
    # The generated target slots (index 1/2). nav.py matches a route to the mission in flight by
    # these, since the briefing block is gone by then.
    slots = []
    for off in (0x18, 0x28):
        n, x, y, kind, f1, f2, f3, cls = rec(off)
        slots.append(dict(place=nm(n), object=obj(cls), x=x, y=y))
    ds = start_ds(mem) if copy == -1 else None
    out = dict(world_copy_phys=base, slots=slots)
    if ds is not None:
        br = briefing(mem, ds)
        if THEATER_INDEX.get(br["theater_index"], theater) != theater:
            print(f"WARNING: pilot theater index {br['theater_index']} is not {theater}", file=sys.stderr)
        # The block's indices point into START's own copy of the records.
        srec = lambda i: struct.unpack_from("<8H", mem, ds + START_RECS + 16 * i)
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
    # Radar records, by table structure rather than class numbers (classes differ
    # per theater: SAM sites are class 294 in CE, 292 in NC): the table starts with
    # the SAM sites (all sharing record 0's class), then the OTH radar (kind 21),
    # then ship slots (f1 == 8). kind = radar code; 0 = eliminated / no ship.
    radars = []
    sam_cls = rec(0x38)[7]
    for k in range(0, 60):
        n, x, y, kind, f1, f2, f3, cls = rec(0x38 + 16 * k)
        is_sam, is_oth, is_ship = cls == sam_cls, kind == 21, f1 == 8
        if not (is_sam or is_oth or is_ship):
            if k > 0 and not is_ship:
                if k > 30: break
                continue
        if kind == 0: continue
        sa, typ, rng = RADAR.get(kind, (f"code {kind}", "?", 0))
        place = nm(n) or ("Patrol Boat" if is_ship else "OTH Radar" if is_oth else "")
        radars.append(dict(place=place, system=sa, type=typ, range_km=rng, kind=kind, x=x, y=y, record=k))
    out["radars"] = radars
    return out

def parse_all(mem, theater="CE"):
    theater = theaters.code(theater)
    """Every mission copy in memory (there can be stale ones; see parse)."""
    wld = theaters.wld_bytes(theater); n, t = 0, mem.find(wld[0x38:0x3E])
    while t >= 0: n += 1; t = mem.find(wld[0x38:0x3E], t + 1)
    return [parse(mem, theater, k) for k in range(n)]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--snapshot"); ap.add_argument("--out"); ap.add_argument("--theater", default="auto",
                                                                                                   help="CE / NC / LB / PG (default: detect from memory)")
    ap.add_argument("--missions", help="also dump START's mission table to this json")
    a = ap.parse_args()
    mem = Path(a.snapshot).read_bytes() if a.snapshot else read_memory()
    a.theater = detect_theater(mem) if a.theater == "auto" else theaters.code(a.theater)
    m = parse(mem, a.theater)
    m["theater"] = a.theater
    print(f"theater {a.theater}")
    out = Path(a.out) if a.out else S.logs / "missions" / f"{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True); json.dump(m, open(out, "w"), indent=1)
    print(f"world copy at phys {m['world_copy_phys']:05X}  -> {out}")
    if "times" in m: print(f"  {m['setup']} mission, begins {m['times']['start']}"
                           + (f", primary DEADLINE {m['times']['deadline']}" if "deadline" in m["times"] else ""))
    if "takeoff" in m: print(f"  takeoff   {m['takeoff']['place']}  ({m['takeoff']['x']},{m['takeoff']['y']})  heading {m['takeoff_heading']:g}")
    for t in m["targets"]:
        print(f"  {t['role']:<9} " + (f"mission {t['mission']:2d} {t['task']} the " if "mission" in t else "")
              + f"{t['object']} at {t['place']}  ({t['x']},{t['y']})" + (f"  ONC {t['onc']}" if t.get("onc") else ""))
    if "landing" in m: print(f"  landing   {m['landing']['place']}  ({m['landing']['x']},{m['landing']['y']})")
    for r in m["radars"]: print(f"  {r['place']:<12} {r['system']:<7} {'doppler' if r['type']=='D' else 'pulse  '} {r['range_km']:3d} km  ({r['x']},{r['y']})")
    if a.missions:
        ds = start_ds(mem)
        if ds is None: sys.exit("--missions: START is not running")
        names = theaters.places(a.theater)["names"]
        json.dump(dict(source="START.EXE data segment ds:4B0C (12 B x 56), read from memory by read_mission.py",
                       fields="theater_mask, level_mask, type, category, code, extra (code += extra<<8 when extra > 0)",
                       categories_from=f"{a.theater} names via ds:C162[class & 0x7f]",
                       missions=mission_table(mem, ds, names)), open(a.missions, "w"), indent=1)
        print(f"mission table -> {a.missions}")
