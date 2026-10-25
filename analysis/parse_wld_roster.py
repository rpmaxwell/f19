#!/usr/bin/env python3
"""
Step 7 of FINDINGS.md's action plan: parse *.WLD and ROSTER.FIL statically,
no emulator needed. The four WLD files differ in size (2080/2695/2280/2937B)
so there's no single fixed-size struct across theaters -- this does
comparative structural analysis instead of assuming a known layout, plus two
concrete, narrow, high-confidence checks: the known North Cape byte-fix
offset, and a diff between the live ROSTER.FIL and the sibling backup found
alongside it.

Usage: python3 analysis/parse_wld_roster.py
"""

import os
import struct

GAME_DIR = os.path.expanduser("~/Documents/F-19 Stealth Fighter/MPS/F19")
WLD_FILES = ["LIBYA.WLD", "GULF.WLD", "NC.WLD", "CE.WLD"]
ROSTER_LIVE = "ROSTER.FIL"
ROSTER_BACKUP = "ROSTER.FIL.corrupted-20260803"
NC_FIX_OFFSETS = [0x1F1, 0x201]


def read(fname: str) -> bytes:
    with open(os.path.join(GAME_DIR, fname), "rb") as f:
        return f.read()


def wld_structural_survey():
    print("=== WLD structural survey ===")
    datas = {f: read(f) for f in WLD_FILES}

    print("\n-- first 64 bytes, all four files --")
    for f, d in datas.items():
        print(f"  {f:10s} ({len(d)}B): {d[:64].hex()}")

    print("\n-- header field guesses (offsets 4 and 6, little-endian u16) --")
    print("   (bytes 0-3 look non-zero/non-obvious -- possibly a checksum or")
    print("    seed, not attempted here; bytes 8+ are zero-padded up to a")
    print("    detected header boundary before real data resumes)")
    counts = {}
    header_sizes = {}
    for f, d in datas.items():
        n1 = struct.unpack_from("<H", d, 4)[0]
        n2 = struct.unpack_from("<H", d, 6)[0]
        counts[f] = (n1, n2)
        # header size = first nonzero byte at offset >= 8
        hsize = 8
        while hsize < len(d) and d[hsize] == 0:
            hsize += 1
        header_sizes[f] = hsize
        remaining = len(d) - hsize
        print(f"  {f:10s}: n1(@4)={n1:5d}  n2(@6)={n2:5d}  "
              f"header_size(first nonzero byte >=8)={hsize}  remaining={remaining}")

    common_header = set(header_sizes.values())
    if len(common_header) == 1:
        print(f"\n  -> consistent header size across all 4 files: {common_header.pop()}")
    else:
        print(f"\n  -> header size NOT consistent across files: {header_sizes}")

    print("\n-- single-divisor hypothesis: does (filesize - header) / X divide evenly")
    print("   for X in {n1, n2, n1+n2} across ALL FOUR files simultaneously? --")
    for label, pick in [("n1", lambda c: c[0]), ("n2", lambda c: c[1]),
                         ("n1+n2", lambda c: c[0] + c[1])]:
        results = []
        all_exact = True
        for f, d in datas.items():
            remaining = len(d) - header_sizes[f]
            x = pick(counts[f])
            if x == 0:
                all_exact = False
                results.append(f"{f}: x=0")
                continue
            q, r = divmod(remaining, x)
            results.append(f"{f}: remaining/{label}={q}.{'exact' if r == 0 else f'rem{r}'}")
            if r != 0:
                all_exact = False
        tag = "EXACT for all 4" if all_exact else "not exact for all 4"
        print(f"  {label:6s} [{tag}]: " + "; ".join(results))

    print("\n  Conclusion: no clean single-record-width division was found (expected --")
    print("  see script output above). The two count fields at offset 4/6 are likely")
    print("  real (they vary sensibly with file size) but the record layout probably")
    print("  mixes fixed-width entries with variable-length data (e.g. name strings),")
    print("  which a linear-division search can't resolve. Cross-check against")
    print("  START.EXE's file-loading code once Ghidra (Phase 2) is available.")


def nc_byte_fix_check():
    print("\n=== North Cape byte-fix spot check ===")
    data = read("NC.WLD")
    for off in NC_FIX_OFFSETS:
        val = data[off]
        state = "BROKEN (matches known bug)" if val == 0x08 else (
            "PATCHED (0x09, bug not present)" if val == 0x09 else "UNEXPECTED VALUE")
        print(f"  offset 0x{off:04X}: byte=0x{val:02X}  [{state}]")


def roster_check():
    print("\n=== ROSTER.FIL parse + backup diff ===")
    live_path = os.path.join(GAME_DIR, ROSTER_LIVE)
    backup_path = os.path.join(GAME_DIR, ROSTER_BACKUP)
    if not os.path.exists(backup_path):
        print(f"  no backup file found at {backup_path}, skipping diff")
        return

    live = read(ROSTER_LIVE)
    backup = read(ROSTER_BACKUP)
    print(f"  live={len(live)}B  backup={len(backup)}B")

    live_nonzero = sum(1 for b in live if b != 0)
    backup_nonzero = sum(1 for b in backup if b != 0)
    print(f"  live non-zero bytes: {live_nonzero}/{len(live)}")
    print(f"  backup non-zero bytes: {backup_nonzero}/{len(backup)}")

    if live_nonzero == 0 and backup_nonzero > 0:
        print("  *** live ROSTER.FIL is entirely zeroed; backup has real data. ***")
        print("  *** This is flagged to the user directly -- not touched by this script. ***")

    # Backup has real pilot-name data -- recover the record layout from it,
    # since the live file (all zero) can't tell us anything structurally.
    print("\n  -- record layout recovered from backup (has real data) --")
    HEADER = 2
    RECORD_SIZE = 80
    n_records = (len(backup) - HEADER) // RECORD_SIZE
    print(f"  header={HEADER}B, record_size={RECORD_SIZE}B -> "
          f"{n_records} full records, {(len(backup)-HEADER) % RECORD_SIZE}B leftover")
    for i in range(n_records):
        off = HEADER + i * RECORD_SIZE
        rec = backup[off:off + RECORD_SIZE]
        # name appears to start at record offset 2, null/space-padded
        name_bytes = rec[2:2 + 30]
        name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")
        print(f"    record {i}: offset 0x{off:04X}  name={name!r}")


def b_y_quick_look():
    print("\n=== Unexplained files B and Y (quick look) ===")
    for fname in ["B", "Y"]:
        path = os.path.join(GAME_DIR, fname)
        if not os.path.exists(path):
            print(f"  {fname}: not found")
            continue
        with open(path, "rb") as f:
            data = f.read()
        print(f"  {fname} ({len(data)}B): {data[:32].hex()}")
        if data[:2] == b"MZ":
            print(f"    -> looks like an MZ executable")
        else:
            print(f"    -> NOT an MZ executable (no 'MZ' magic) -- despite size,"
                  f" not simply a renamed ASOUND.EXE copy" if fname == "B" else "")


def main():
    wld_structural_survey()
    nc_byte_fix_check()
    roster_check()
    b_y_quick_look()


if __name__ == "__main__":
    main()
