# Last check before concluding static analysis has hit a wall: find every
# DOS "open existing file" (INT 21h, AH=3Dh) and "create file" (AH=3Ch)
# call site in START.EXE, and print the few instructions right before each
# one (looking for what gets loaded into DX -- the pointer to the filename
# -- immediately beforehand). If DX is loaded via a register-indirect/
# computed addressing mode rather than a plain immediate at every site,
# that corroborates the overlay-loaded-code hypothesis (the actual roster
# file I/O isn't in code Ghidra's static analysis can see/resolve).

import sys

listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()

instr_iter = listing.getInstructions(True)
count = 0
for instr in instr_iter:
    mnem = instr.getMnemonicString()
    if mnem != "INT":
        continue
    ops = instr.getDefaultOperandRepresentation(0)
    if ops != "0x21":
        continue
    # walk backward up to 8 instructions collecting context, looking for AH set
    ctx = []
    cur = instr.getPrevious()
    for _ in range(8):
        if cur is None:
            break
        ctx.insert(0, str(cur))
        cur = cur.getPrevious()
    ctx_text = " | ".join(ctx)
    if "0x3d" in ctx_text.lower() or "0x3c" in ctx_text.lower() or " AH," in ctx_text.upper():
        func = fm.getFunctionContaining(instr.getAddress())
        fname = func.getName() if func else "???"
        count += 1
        print("\nINT21 @ %s in %s" % (instr.getAddress(), fname))
        print("  context: %s" % ctx_text)
        sys.stdout.flush()

print("\nTOTAL int21 sites with AH=3C/3D nearby: %d" % count)
