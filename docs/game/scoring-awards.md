# Scoring and awards

The mission rating formula and the medal, ribbon and promotion rules, decoded from END.EXE. Moved verbatim from FINDINGS.md on 2026-10-25 (WP-9); see [the index](../../FINDINGS.md).

## Mission rating formula — decoded from END.EXE (2026-10-17)

END.EXE is EXEPACK'd: `analysis/unexepack.py END.EXE END.bin` (DGROUP = seg 0x9d9). Score routine at
image 0x5666 (`analysis/end_re/score_real.asm`; Ghidra project analysis/end_re). gameData = far ptr [0x86ae],
commData = far ptr [0x9ed6]. Tables are int16 in DGROUP; "/8" steps are integer (truncating) long math.

Per flight-event record (status & 0x3f = type; 0x80 = primary flag, 0x40 = secondary flag), indexed by
ROE = gameData+0x3a (0 Cold War, 1 Limited, 2 Conventional):
- type 9 (64 s position sample) in an enemy cell (grid & 3 == 0): **+1** (Cold), +1 every 4th (Limited), 0 (Conv).
- types 10/11/1/2/3 with the primary flag: **+140 / 120 / 100**. Secondary: **+70** alone, **+35** if the primary
  is also done (net 175 for both, any order).
- kills (types 1,2,3,12): Cold War 0 for every class; Limited 1-9; Conventional 3-18 by target class
  (table 0x4258 [roe][class & 15]); air kills 0/3/6 (Cold/Lim/Conv) and up to 12; friendly or neutral-cell
  targets -25; other penalties -2 to -15.
- type 6 **visual ID**: -20/-12/0, type 7 **radar ID**: -10/-6/0 (types identified in EGAME 2026-10-18: one radar ID = 10 enemy samples). Type 8 (takeoff/end) clamps the running score at >= 0.
Then: score = score x T[theater]/8 x T[mission type]/8 x T[opponents]/8 x T[landing]/8, with
- theater (gameData+0x38): Libya 8, Persian Gulf 10, North Cape 12, Central Europe 13 (5th entry 8).
- mission type (+0x3c, START list): Air-to-Air 8, Strike 9, A-A Training 8, Strike Training 9.
- opponents (+0x3e): Green 8, Regular 11, Veteran 14, Elite 17.
- landing (+0x40): No Crashes 5, Easy 8, Realistic 10.
- ejected (landingType commData+0x26 == 2): x [2,3,6,4][byte 0x978c]/8 (index 0 = enemy -> 2/8, confirmed).
  A crash or safe landing costs nothing.
- fuel (commData+0x2e, presumably the briefing's FUEL ESTIMATE): >= 7000 x9/8, >= 9000 x10/8, >= 11500 x11/8.
Check: CE veteran flight = (140 + 50 enemy samples) x 13 x 9 x 14 x 10 x 9 / 8^5 = 850 -> x2/8 after ejecting
in enemy = 212 (observed 212; the earlier 833 is base 186). Top settings: one enemy sample is worth ~6.7 points.

Fuel: the Change Waypoint panel shows game time and fuel: 9830 lb at 11:35:24 -> 5890 lb at 12:41:00
(ce_cottbus, ~225 kt at ~250 ft) = **~60 lb per game minute** (~64 lb per sample). Extra Fuel tank: 1500 lb.

## Medals, ribbons, promotions — decoded from END.EXE (2026-10-24)

All of it lives in END.EXE; ROSTER.FIL only stores the results. END main (image 0x93) does `missions++` for every
non-training flight on entry (death and capture included), then scores (0x7328 at 0x208), then awards (0x75b0 at 0x211):
promotion check 0x8526, medal/ribbon check 0x85d7. Decomp addresses are image - 0xc.

**ROSTER.FIL** = u16 header + 10 x 0x50-byte pilot records (gameData = far ptr [0x86ae] points at the active one):
+0 index, +2 name, +0x20 rank (0 2nd Lt .. 6 Brig Gen), +0x22 Purple Heart flag, +0x24/26/28/2A counts of Airman's
Medal / DFC / Silver Star / Air Force Cross, +0x2C Medal of Honor flag, +0x2E best mission score, +0x30 last mission
score (0 in training), +0x32 u32 career total, +0x36 missions flown, +0x38.. theater/ROE/type/opp/landing,
+0x44 Combat Readiness, +0x46 Short Tour, +0x48 Long Tour, +0x4A Longevity, +0x4C Longevity w/ Gold Cluster.
Flags: commData+0x30 = training mission, ds:9A1B / ds:9DDE = primary / secondary objective done,
ds:99F4 = captured (set by the TASS screen 0x7088), commData+0x28 != 0 = dead ("posthumously").

**Medals**: at most one per mission, from s = this mission's score:
- s > 1200 and no Medal of Honor yet -> **Medal of Honor** (once per career).
- else s >= 900 x (AFC + 1) -> **Air Force Cross**; else s >= 600 x (SS + 1) -> **Silver Star**;
  else s >= 300 x (DFC + 1) -> **DFC**; else s >= 100 x (AM + 1) and AM < 9 -> **Airman's Medal**.
  Each repeat of a medal needs a higher score.
- Captured: no medal and the count is not raised; the commander "will not recommend you for the X".
  The training version ("If this had been a real mission...") cannot fire, because training scores are stored as 0.
- **Purple Heart** (once per career, still awarded when captured): damage count (commData+0x36) > 6 and damage bits
  0x04 (flight controls) and 0x08 (autopilot/INS) both set. Shown as "... and the PURPLE HEART for being wounded".

**Ribbons** (missions = +0x36 after this flight's increment, non-training only):
5-9 Short Tour; >= 10 Long Tour (replaces Short); 30-59 Longevity; >= 60 Longevity w/ Gold Cluster (replaces Longevity).
**Combat Readiness Ribbon**: a training mission with the primary or secondary objective done (once).

**Promotion** (non-training, at most one rank per mission; uses the career total that already includes this mission):
from rank r you need total >= T[r], missions >= M[r] and total / missions >= A[r] (ds:66C6 / 66DE / 66D2):

| from -> to | T total | M missions | A average |
|---|---|---|---|
| 2nd Lt -> 1st Lt | 300 | 2 | 100 |
| 1st Lt -> Captain | 1125 | 5 | 150 |
| Captain -> Major | 3000 | 10 | 200 |
| Major -> Lt Col | 7000 | 20 | 250 |
| Lt Col -> Colonel | 16000 | 40 | 280 |
| Colonel -> Brig Gen | 27720 | 99 | 280 |

Colonel -> Brigadier General happens only on exactly the 99th mission (the retirement mission). Captured below Colonel:
no promotion this time ("will delay the promotion"); it can still come on a later mission. Dead pilots are still
promoted ("posthumously"). With no award at all: "remained at his present rank". After the 99th mission, when not dead,
END shows the retirement message.
Cross-checked with the live ROSTER.FIL (Fred McGriff: 277 -> Airman's, then 1211 -> Medal of Honor, 1st Lt after 2 missions).
**File I/O** is only in START.EXE: load at image 0xa4fb (`fopen "rb"`, fread 2 bytes into ds:9B52, then 10 x fread 0x50
into ds:9D54), save at 0xa376 (same layout, `"wb"`, header = active pilot's index). No checksum, magic or size check,
so hand edits are safe. Name = +2..+0x1F (30 bytes, NUL-terminated; the name editor takes chars 0x20-0x7F and
caps the name by pixel width). +0x42 and +0x4E are unidentified.
