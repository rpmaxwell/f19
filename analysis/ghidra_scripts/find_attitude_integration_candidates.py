# Ghidra headless script (PyGhidra): step 3 of the pitch/roll code-shape
# search -- find functions that combine a SHIFT (SHR/SAR/SHL) with a
# DIVIDE (DIV/IDIV), the arithmetic shape already confirmed elsewhere in
# this binary for per-frame angle integration (FUN_1000_5828's
# `heading += (turnRate>>3)/frameDivisor`, matching f15se2-re's
# `pitchAngle = (g_pitchInput << 7) / g_frameRateScaling`). Reports which
# of those functions ALSO write to 2-3 small, mutually-adjacent (<=8 bytes
# apart) fixed absolute addresses -- the shape of a small scalar cluster
# like g_ourHead/g_ourPitch/g_ourRoll, as opposed to a large-stride
# table-indexed write (which the already-identified entity tables use).

from collections import defaultdict
from ghidra.program.model.lang import Register
from ghidra.program.model.address import Address

listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()

SHIFT_MNEMONICS = {"SHR", "SAR", "SHL"}
DIV_MNEMONICS = {"DIV", "IDIV"}

shift_div_funcs = {}
for func in fm.getFunctions(True):
    body = func.getBody()
    has_shift = False
    has_div = False
    for instr in listing.getInstructions(body, True):
        mnem = instr.getMnemonicString().upper()
        if mnem in SHIFT_MNEMONICS:
            has_shift = True
        elif mnem in DIV_MNEMONICS:
            has_div = True
        if has_shift and has_div:
            break
    if has_shift and has_div:
        shift_div_funcs[func] = body

print("Functions with both a shift and a divide instruction: %d" % len(shift_div_funcs))

# Now, within each such function, collect all fixed-absolute-address MOV
# destinations (MOV [addr], ...) -- i.e. instructions whose first operand
# is a plain memory reference to a literal address (not [reg+disp] with a
# register present), and look for clusters of 2-3 such addresses within 8
# bytes of each other.
for func in shift_div_funcs:
    body = shift_div_funcs[func]
    abs_write_addrs = set()
    for instr in listing.getInstructions(body, True):
        mnem = instr.getMnemonicString().upper()
        if mnem not in ("MOV", "ADD", "SUB", "INC", "DEC"):
            continue
        if instr.getNumOperands() < 1:
            continue
        # operand 0 should be a pure absolute memory ref: no register
        # component anywhere in the operand's object list, but an Address
        # object present (matches the raw-disasm shape seen elsewhere in
        # this binary, e.g. "MOV word ptr [0x855a],0x50").
        objs = instr.getOpObjects(0)
        has_register = any(isinstance(o, Register) for o in objs)
        addr_obj = None
        for o in objs:
            if isinstance(o, Address):
                addr_obj = o
        if has_register or addr_obj is None:
            continue
        abs_write_addrs.add(addr_obj.getOffset())
    if len(abs_write_addrs) < 2:
        continue
    sorted_addrs = sorted(abs_write_addrs)
    # find clusters of 2-3 addresses within 8 bytes span
    clusters = []
    i = 0
    while i < len(sorted_addrs):
        cluster = [sorted_addrs[i]]
        j = i + 1
        while j < len(sorted_addrs) and sorted_addrs[j] - cluster[0] <= 8:
            cluster.append(sorted_addrs[j])
            j += 1
        if 2 <= len(cluster) <= 3:
            clusters.append(cluster)
        i = j if j > i + 1 else i + 1
    if clusters:
        print("\nFUNCTION %s @ %s" % (func.getName(), func.getEntryPoint()))
        print("  all abs-write addrs: %s" % [hex(a) for a in sorted_addrs])
        print("  candidate small clusters (2-3 addrs, <=8 bytes span): %s" % (
            [[hex(a) for a in c] for c in clusters]))

print("\nDONE")
