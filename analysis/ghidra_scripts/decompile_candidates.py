# Decompile the handful of functions find_entity_loop_candidates.py
# flagged as strong entity-table candidates, for a quick readable look
# instead of raw disassembly.
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

TARGETS = ["1000:4ede", "1000:7a59", "1000:3c2c", "1000:6ae8", "1000:ab20"]

fm = currentProgram.getFunctionManager()
addr_factory = currentProgram.getAddressFactory()
ifc = DecompInterface()
ifc.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()

for t in TARGETS:
    seg, off = t.split(":")
    addr = addr_factory.getAddress(seg + ":" + off)
    func = fm.getFunctionContaining(addr)
    if func is None:
        print("NO FUNCTION at %s" % t)
        continue
    print("\n" + "=" * 70)
    print("DECOMPILE %s @ %s" % (func.getName(), func.getEntryPoint()))
    print("=" * 70)
    res = ifc.decompileFunction(func, 60, monitor)
    if res.decompileCompleted():
        print(res.getDecompiledFunction().getC())
    else:
        print("decompile failed: %s" % res.getErrorMessage())
