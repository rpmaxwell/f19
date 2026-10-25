# Ghidra headless post-script (Jython 2.7): find defined strings matching
# candidate HUD/telemetry-label keywords and print their addresses plus any
# code references to them (step 3 of FINDINGS.md's action plan -- the
# "string-anchor" xref walk). Run via analyzeHeadless's -postScript.
#
# Keyword list is broad on purpose since we don't know F-19's exact label
# vocabulary yet -- narrow it down after seeing what actually turns up.

ANCHOR_KEYWORDS = [
    "ALT", "HDG", "SPD", "FUEL", "MACH", "IAS", "AOA", "BRG", "RDR", "WPN",
    "TGT", "DIST", "HGT", "SPEED", "HEADING", "ALTITUDE", "DAMAGE",
    "THROTTLE", "WEAPON", "RADAR", "LOCK", "MISSILE", "CANNON", "GEAR",
    "FLAPS", "BRAKE", "AUTOPILOT", "BINGO", "MASTER", "WARN", "CAUTION",
]

listing = currentProgram.getListing()
data_iter = listing.getDefinedData(True)

found = 0
while data_iter.hasNext():
    data = data_iter.next()
    if not data.hasStringValue():
        continue
    val = data.getValue()
    if val is None:
        continue
    text = str(val)
    upper = text.upper()
    matched = [k for k in ANCHOR_KEYWORDS if k in upper]
    if not matched:
        continue

    addr = data.getAddress()
    refs = getReferencesTo(addr)
    print("STRING @ %s: %r  matched=%s  (%d refs)" % (addr, text, matched, len(refs)))
    for ref in refs:
        from_addr = ref.getFromAddress()
        print("    xref from %s  type=%s" % (from_addr, ref.getReferenceType()))
    found += 1

print("TOTAL matching strings: %d" % found)
