# Locate the code in START.EXE that opens/reads ROSTER.FIL, to confirm
# the 80-byte record layout already recovered statically from the file
# itself (analysis/parse_wld_roster.py / candidate_addresses.md): 2-byte
# header + 10x80B records, name ASCIIZ at record offset 2, ~48 bytes of
# unparsed stats after the name. Finds the "ROSTER.FIL" string (or
# "ROSTER" substring, in case it's built from parts / has a different
# extension casing), walks xrefs to find the reading function, and
# decompiles it plus anything it calls that touches the buffer.

from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor

NEEDLE = "ROSTER"

listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()
ifc = DecompInterface()
ifc.openProgram(currentProgram)
monitor = ConsoleTaskMonitor()

data_iter = listing.getDefinedData(True)
hits = []
while data_iter.hasNext():
    data = data_iter.next()
    if not data.hasStringValue():
        continue
    val = data.getValue()
    if val is None:
        continue
    text = str(val)
    if NEEDLE in text.upper():
        hits.append((data.getAddress(), text))

print("TOTAL matching strings: %d" % len(hits))
for addr, text in hits:
    print("STRING @ %s: %r" % (addr, text))
    refs = getReferencesTo(addr)
    seen_funcs = set()
    for ref in refs:
        from_addr = ref.getFromAddress()
        func = fm.getFunctionContaining(from_addr)
        fname = func.getName() if func else "???"
        print("    xref from %s in function %s  type=%s" % (
            from_addr, fname, ref.getReferenceType()))
        if func is not None:
            seen_funcs.add(func)

    for func in seen_funcs:
        print("\n" + "=" * 70)
        print("DECOMPILE %s @ %s" % (func.getName(), func.getEntryPoint()))
        print("=" * 70)
        res = ifc.decompileFunction(func, 60, monitor)
        if res.decompileCompleted():
            print(res.getDecompiledFunction().getC())
        else:
            print("decompile failed: %s" % res.getErrorMessage())

if not hits:
    print("No strings containing %r found in defined data. Next step: " % NEEDLE +
          "search undefined/raw bytes too (the string may not be in a " +
          "Ghidra-defined Data unit yet), or check EGAME.EXE instead of " +
          "START.EXE.")
