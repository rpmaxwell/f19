# No instruction immediates and no auto-xrefs pointed at the "Roster.Fil"
# strings (2000:2c2d / 2000:2c8f) -- next hypothesis: they're referenced
# indirectly through a pointer table (array of near-offset words) that
# Ghidra hasn't typed as pointers, so it never built a reference. Scan raw
# memory bytes for the two little-endian 16-bit values directly, anywhere
# in the program (code or data), and print what's defined at each hit plus
# a short hex context window.

import sys
import traceback
from ghidra.util.task import ConsoleTaskMonitor

TARGET_OFFSETS = [0x2c2d, 0x2c8f]

try:
    mem = currentProgram.getMemory()
    listing = currentProgram.getListing()
    monitor = ConsoleTaskMonitor()

    for off in TARGET_OFFSETS:
        needle_bytes = bytes([off & 0xff, (off >> 8) & 0xff])
        print("\n=== searching for bytes matching offset 0x%04x (LE: %02x %02x) ===" % (
            off, off & 0xff, (off >> 8) & 0xff))
        sys.stdout.flush()
        found = mem.findBytes(currentProgram.getMinAddress(), needle_bytes, None, True, monitor)
        count = 0
        while found is not None and count < 200:
            count += 1
            try:
                start = found.subtract(4)
            except Exception:
                start = found
            ctx = []
            a = start
            for _ in range(12):
                try:
                    b = mem.getByte(a) & 0xff
                    ctx.append("%02x" % b)
                except Exception:
                    ctx.append("??")
                a = a.add(1)
            cu = listing.getCodeUnitAt(found)
            cu_desc = str(cu) if cu else "(no code unit boundary here)"
            print("  hit @ %s  ctx=%s  codeunit=%s" % (found, " ".join(ctx), cu_desc))
            sys.stdout.flush()
            try:
                found = mem.findBytes(found.add(1), needle_bytes, None, True, monitor)
            except Exception:
                break
        print("  total hits: %d%s" % (count, " (capped at 200)" if count >= 200 else ""))
        sys.stdout.flush()
except Exception:
    print("SCRIPT ERROR:")
    traceback.print_exc()
    sys.stdout.flush()
