# Raw-byte scan for the "Roster.Fil" string offsets (0x2c2d / 0x2c8f) as
# literal immediates found nothing anywhere in memory -- so the reference is
# not a plain MOV/PUSH imm16. Try the decompiler instead: it does constant
# propagation/merging that can surface a value even when no single
# instruction's raw bytes contain it verbatim (e.g. built from a
# base-register + table-byte-offset addressing pattern). Decompile every
# function in the program and grep the resulting C text for the two hex
# constants.

import sys
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

NEEDLES = ["2c2d", "2c8f"]

fm = currentProgram.getFunctionManager()
ifc = DecompInterface()
ifc.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()

funcs = list(fm.getFunctions(True))
print("total functions: %d" % len(funcs))
sys.stdout.flush()

hits = 0
for i, func in enumerate(funcs):
    res = ifc.decompileFunction(func, 30, monitor)
    if not res.decompileCompleted():
        continue
    c = res.getDecompiledFunction().getC()
    low = c.lower()
    matched = [n for n in NEEDLES if n in low]
    if matched:
        hits += 1
        print("\n" + "=" * 70)
        print("MATCH %s in %s @ %s" % (matched, func.getName(), func.getEntryPoint()))
        print("=" * 70)
        print(c)
        sys.stdout.flush()
    if i % 200 == 0:
        print("...progress %d/%d" % (i, len(funcs)))
        sys.stdout.flush()

print("\nTOTAL functions with a matching constant: %d" % hits)
