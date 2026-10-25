# Locate strings related to pilot/mission selection and .WLD map loading in
# START.EXE, with xrefs, to hand the user concrete navigation targets for
# interactive GUI exploration (Window > Defined Strings won't group these
# usefully, and blind browsing is slow).

KEYWORDS = [
    "WLD", ".WLD", "LIBYA", "GULF", "NORTH CAPE", "N CAPE", "NC.WLD",
    "CENTRAL EUROPE", "CE.WLD", "PILOT", "MISSION", "THEATER", "THEATRE",
    "RANK", "SELECT", "ROOKIE", "VETERAN", "ACE", "PROMOT",
]

listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()

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
    upper = text.upper()
    matched = [k for k in KEYWORDS if k in upper]
    if matched:
        hits.append((data.getAddress(), text, matched))

print("TOTAL matching strings: %d" % len(hits))
for addr, text, matched in hits:
    refs = getReferencesTo(addr)
    ref_descs = []
    for ref in refs:
        from_addr = ref.getFromAddress()
        func = fm.getFunctionContaining(from_addr)
        fname = func.getName() if func else "???"
        ref_descs.append("%s(in %s)" % (from_addr, fname))
    print("STRING @ %s: %r  matched=%s  refs=%s" % (addr, text, matched, ref_descs))
