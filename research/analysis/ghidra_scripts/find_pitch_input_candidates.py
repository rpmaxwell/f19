# Ghidra headless script (PyGhidra / real Python 3): locate F-19's player
# attitude (heading/pitch/roll) representation and its keyboard-input feed,
# by CODE SHAPE, cross-referenced against f15se2-re's confirmed analog:
#
#   - g_ourHead/g_ourPitch/g_ourRoll (egdata.h): individual int16 scalars,
#     NOT a table, full-range fixed-point angle (boundary checks near
#     0x38e3/0x4001/0xc71d).
#   - g_pitchInput/g_rollInput: small-range (~-16..32) scalars recomputed
#     every frame from raw control input, integrated into g_our* via
#     `pitchAngle = (g_pitchInput << 7) / g_frameRateScaling`.
#   - eginput.c's kbdInt9Handler: F-15 SE2's keyboard-as-virtual-joystick is
#     a custom INT 09h handler that reads the raw scancode straight off
#     I/O port 0x60 (`IN AL,60h`), checks the BIOS NumLock/Shift flag byte
#     at 0040:0017, looks up a direction mask in a small table indexed by
#     (scancode & 0x7f) - 0x29 (covers the 0x29-0x51 numpad/arrow range),
#     and on press/release writes a small raw deflection byte (0x80 +/-
#     delta) into 1-2 scalars -- entirely separate from the BIOS-buffer
#     `_bios_keybrd()` polling used for ordinary command keys (throttle,
#     pause, etc, dispatched via a big switch on the polled scancode word).
#
# F-19 is keyboard-only (no joystick), so it may use EITHER shape: a direct
# case in the polled-scancode switch (simpler, matches F15SE2's throttle
# key handling), or its own INT9 hook (matches F15SE2's pitch/roll keys) --
# candidate_addresses.md's earlier live breakpoint test already caught
# CS:IP inside a tight loop at F000:D10B described as "plausibly the tail
# of a BIOS INT9 keyboard-handler chain the game's own hook jumps into",
# i.e. there's already live evidence F-19 does install a custom INT9
# handler, so search for both shapes.
#
# Search strategy:
#   1. Find every instruction reading I/O port 0x60 (`IN AL,60h` / `IN AX,60h`
#      / register-DX form) -- INT9 handlers reading the keyboard controller
#      directly are rare, this should shortlist very few functions.
#   2. Independently, scan every function for CMP/MOV instructions with an
#      immediate operand equal to 0x48, 0x50, 0x4800, or 0x5000 (kp8/kp2
#      scancode, alone or as the high byte of a combined scancode:ascii
#      word matching F15SE2's ScanCode enum encoding) -- catches a direct
#      switch-case dispatch shape instead of/in addition to an INT9 hook.
#   3. For every function flagged by either search, print its entry point
#      and full disassembly so a human/decompile pass can trace forward
#      from the hit to whatever memory location gets written.

from ghidra.program.model.symbol import RefType

listing = currentProgram.getListing()
fm = currentProgram.getFunctionManager()

PORT_60 = 0x60
SCANCODE_IMMS = {0x48, 0x50, 0x4800, 0x5000}

print("=" * 70)
print("PASS 1: instructions touching I/O port 0x60 (IN AL/AX,60h)")
print("=" * 70)

port60_funcs = {}
for instr in listing.getInstructions(True):
    mnem = instr.getMnemonicString().upper()
    if mnem not in ("IN", "OUT"):
        continue
    # operand text will show the port either as an immediate (60h) or via DX
    text = str(instr)
    if "60h" not in text and "0x60" not in text and ", 60" not in text:
        # also match plain hex "60" token bounded
        import re
        if not re.search(r"\b60h\b|\b0x60\b", text, re.IGNORECASE):
            continue
    func = fm.getFunctionContaining(instr.getAddress())
    fname = func.getName() if func else "NO_FUNC"
    fentry = str(func.getEntryPoint()) if func else "?"
    port60_funcs.setdefault((fname, fentry), []).append((instr.getAddress(), text))
    print("  %s  %-30s  in func %s @ %s" % (instr.getAddress(), text, fname, fentry))

print("\nFunctions containing port-0x60 access: %d" % len(port60_funcs))
for (fname, fentry) in port60_funcs:
    print("  %s @ %s" % (fname, fentry))

print("\n" + "=" * 70)
print("PASS 2: CMP/MOV against scancode immediates %s" % sorted(SCANCODE_IMMS))
print("=" * 70)

scancode_hits = {}
for func in fm.getFunctions(True):
    body = func.getBody()
    for instr in listing.getInstructions(body, True):
        mnem = instr.getMnemonicString().upper()
        if mnem not in ("CMP", "MOV", "SUB"):
            continue
        for op_idx in range(instr.getNumOperands()):
            scalar = instr.getScalar(op_idx)
            if scalar is None:
                continue
            val = scalar.getValue()
            if val in SCANCODE_IMMS:
                scancode_hits.setdefault(func, []).append((instr.getAddress(), val, str(instr)))

print("Functions with scancode-immediate hits: %d" % len(scancode_hits))
for func, hits in scancode_hits.items():
    print("\nFUNCTION %s @ %s  (%d hits)" % (func.getName(), func.getEntryPoint(), len(hits)))
    for addr, val, text in hits:
        print("    %s : %-30s imm=0x%x" % (addr, text, val))

print("\n" + "=" * 70)
print("DONE")
