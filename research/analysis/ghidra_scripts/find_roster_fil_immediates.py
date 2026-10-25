# Ghidra's auto-analysis found the "Roster.Fil" strings (2000:2c2d and
# 2000:2c8f) but no xrefs to them -- typical for segmented 16-bit DOS code
# where a string offset is loaded as a bare immediate (e.g. MOV DX, 2C2Dh)
# rather than a resolved reference. Scan every instruction's scalar operands
# for those two offset values directly.

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

TARGET_OFFSETS = [0x2c2d, 0x2c8f]

listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()
ifc = DecompInterface()
ifc.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()

instr_iter = listing.getInstructions(True)
hit_funcs = {}
n_checked = 0
while instr_iter.hasNext():
    instr = instr_iter.next()
    n_checked += 1
    for i in range(instr.getNumOperands()):
        for obj in instr.getOpObjects(i):
            val = None
            if hasattr(obj, "getValue"):
                try:
                    val = obj.getValue()
                except Exception:
                    val = None
            if val is None:
                continue
            try:
                ival = int(val)
            except Exception:
                continue
            if ival in TARGET_OFFSETS:
                addr = instr.getAddress()
                func = fm.getFunctionContaining(addr)
                fname = func.getName() if func else "???"
                print("MATCH 0x%04x @ %s in function %s: %s" % (
                    ival, addr, fname, instr))
                if func is not None:
                    hit_funcs.setdefault(func, []).append(addr)

print("\nchecked %d instructions" % n_checked)
print("functions containing a matching immediate: %d" % len(hit_funcs))

for func, addrs in hit_funcs.items():
    print("\n" + "=" * 70)
    print("DECOMPILE %s @ %s (immediate hits at %s)" % (
        func.getName(), func.getEntryPoint(), addrs))
    print("=" * 70)
    res = ifc.decompileFunction(func, 60, monitor)
    if res.decompileCompleted():
        print(res.getDecompiledFunction().getC())
    else:
        print("decompile failed: %s" % res.getErrorMessage())
