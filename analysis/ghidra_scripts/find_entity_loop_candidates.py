# Ghidra headless script (PyGhidra / real Python 3): scan EGAME.EXE's
# disassembly for instructions using immediate constants that match the
# entity-table shapes recovered from the f15se2-re decompilation (see
# FINDINGS.md's "World & dynamic-entity model" section):
#
#   g_simObjects   36-byte records x 20   (other planes/wingmen)
#   g_planeTable   16-byte records x 74   (radar/SAM/ground+air targets)
#   g_projectiles  24-byte records x 12   (in-flight missiles)
#   g_particles     8-byte records x  8   (smoke/hit-spark markers)
#
# v2: v1's blind immediate scan (any operand == candidate value) was
# swamped by ADD/SUB SP,N call-cleanup noise (66/150 functions matched,
# unusable). Tightened to: (a) skip SP as a destination entirely -- that's
# stack accounting, never array indexing; (b) only look at MOV/CMP/IMUL
# (indexing math and loop-bound checks use these; ADD/SUB/ADC/SBB/XOR/TEST/OR
# on 8/16 are overwhelmingly incidental flag/bit work); (c) drop the two
# low-signal solo constants (8, 16) unless they co-occur with a high-signal
# stride/count pair in the same function; (d) treat 74 (g_planeTable's
# count -- the least likely value to appear by coincidence anywhere else in
# an unrelated program) as a standalone strong anchor on its own.

from collections import defaultdict

STRIDE_COUNT_PAIRS = [
    (36, 20, "g_simObjects (36B x20)"),
    (24, 12, "g_projectiles (24B x12)"),
]
SOLO_STRONG = {74: "count:g_planeTable (x74 @ 16B) -- rare constant, strong on its own"}
WEAK = {8, 16}  # only reported when co-occurring with a strong hit in the same function
STRONG_VALUES = {36, 20, 24, 12, 74}
RELEVANT_MNEMONICS = {"MOV", "CMP", "IMUL"}

fm = currentProgram.getFunctionManager()
listing = currentProgram.getListing()

hits_by_func = defaultdict(list)

for func in fm.getFunctions(True):
    body = func.getBody()
    for instr in listing.getInstructions(body, True):
        mnem = instr.getMnemonicString().upper()
        if mnem not in RELEVANT_MNEMONICS:
            continue
        # skip stack-pointer destinations (call-cleanup, not indexing)
        op0 = instr.getRegister(0) if instr.getNumOperands() > 0 else None
        if op0 is not None and op0.getName().upper() in ("SP", "ESP"):
            continue
        for op_idx in range(instr.getNumOperands()):
            scalar = instr.getScalar(op_idx)
            if scalar is None:
                continue
            val = scalar.getValue()
            if val in STRONG_VALUES or val in WEAK:
                hits_by_func[func].append((instr.getAddress(), val, str(instr)))

print("Functions with candidate entity-table immediates (EGAME.EXE), v2 tightened:")
print("=" * 70)

reported = 0
for func, hits in hits_by_func.items():
    vals_present = {v for _, v, _ in hits}
    strong_present = vals_present & STRONG_VALUES
    if not strong_present:
        continue
    # require an actual stride+count pair co-occurrence, or the rare solo 74
    pair_match = any(s in vals_present and c in vals_present for s, c, _ in STRIDE_COUNT_PAIRS)
    solo74 = 74 in vals_present
    if not (pair_match or solo74):
        continue
    reported += 1
    tags = []
    for s, c, label in STRIDE_COUNT_PAIRS:
        if s in vals_present and c in vals_present:
            tags.append(label)
    if solo74:
        tags.append(SOLO_STRONG[74])
    print("\nFUNCTION %s @ %s  (%d total hits)  MATCH: %s" % (
        func.getName(), func.getEntryPoint(), len(hits), "; ".join(tags)))
    for addr, val, text in hits:
        marker = "**" if val in STRONG_VALUES else "  "
        print("  %s %s : %-30s  imm=%s" % (marker, addr, text, val))

print("\n" + "=" * 70)
print("TOTAL functions reported: %d (of %d functions with any candidate immediate via MOV/CMP/IMUL)" % (
    reported, len(hits_by_func)))
