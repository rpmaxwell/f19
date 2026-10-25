#!/usr/bin/env python3
"""
Step 5 of FINDINGS.md's action plan: binary-diff ASOUND.EXE against
ASOUND_FROM_F15STRIKEEAGLE2.EXE. One is confirmed sourced from F-15 Strike
Eagle II (literal proof MicroProse reused compiled modules across titles,
per the two files sitting side-by-side in the install dir) -- the diff
tells us how much MicroProse modified shared modules for F-19. Near-
identical calibrates the F-15 SE2 decompiled C source (neuviemeporte/f15-se2)
as a strong analog for other shared subsystems (physics, rendering, input);
heavily patched means treat it as a weak signal only.

The two files differ in raw size (10024 vs 9192 bytes), so a naive
fixed-offset byte compare is wrong. This instead:
  (a) parses and diffs the two 28-byte MZ headers field-by-field,
  (b) parses and diffs the relocation tables (count + values),
  (c) diffs the content body with difflib.SequenceMatcher (autojunk off --
      binary data has too many repeated bytes, e.g. 0x00 padding, for the
      default junk heuristic to behave sensibly) to find best alignment and
      report an overall similarity percentage plus the diverging regions.

Usage: python3 analysis/diff_asound.py
"""

import difflib
import os
import struct

GAME_DIR = os.path.expanduser("~/Documents/F-19 Stealth Fighter/MPS/F19")
FILE_A = "ASOUND.EXE"
FILE_B = "ASOUND_FROM_F15STRIKEEAGLE2.EXE"

MZ_HEADER_FIELDS = [
    ("e_magic", "2s", 0x00),
    ("e_cblp", "<H", 0x02),
    ("e_cp", "<H", 0x04),
    ("e_crlc", "<H", 0x06),
    ("e_cparhdr", "<H", 0x08),
    ("e_minalloc", "<H", 0x0A),
    ("e_maxalloc", "<H", 0x0C),
    ("e_ss", "<H", 0x0E),
    ("e_sp", "<H", 0x10),
    ("e_csum", "<H", 0x12),
    ("e_ip", "<H", 0x14),
    ("e_cs", "<H", 0x16),
    ("e_lfarlc", "<H", 0x18),
    ("e_ovno", "<H", 0x1A),
]


def parse_mz_header(data: bytes) -> dict:
    header = {}
    for name, fmt, off in MZ_HEADER_FIELDS:
        size = struct.calcsize(fmt)
        (val,) = struct.unpack_from(fmt, data, off)
        header[name] = val
    return header


def parse_relocations(data: bytes, header: dict) -> list[tuple[int, int]]:
    table_off = header["e_lfarlc"]
    count = header["e_crlc"]
    relocs = []
    for i in range(count):
        off, seg = struct.unpack_from("<HH", data, table_off + i * 4)
        relocs.append((seg, off))
    return relocs


def diff_headers(ha: dict, hb: dict):
    print("--- MZ header comparison ---")
    for name, _fmt, _off in MZ_HEADER_FIELDS:
        va, vb = ha[name], hb[name]
        mark = "  " if va == vb else "**"
        print(f"  {mark} {name:12s} A={va!r:>10}  B={vb!r:>10}")
    header_bytes_a = ha["e_cparhdr"] * 16
    header_bytes_b = hb["e_cparhdr"] * 16
    print(f"  header size: A={header_bytes_a} bytes, B={header_bytes_b} bytes")
    return header_bytes_a, header_bytes_b


def diff_relocations(relocs_a, relocs_b):
    print("\n--- Relocation table comparison ---")
    print(f"  count: A={len(relocs_a)}  B={len(relocs_b)}")
    set_a, set_b = set(relocs_a), set(relocs_b)
    only_a = sorted(set_a - set_b)
    only_b = sorted(set_b - set_a)
    shared = set_a & set_b
    print(f"  shared entries: {len(shared)}")
    print(f"  only in A: {len(only_a)}  (first 10: {only_a[:10]})")
    print(f"  only in B: {len(only_b)}  (first 10: {only_b[:10]})")


def diff_bodies(body_a: bytes, body_b: bytes):
    print("\n--- Content body diff (difflib.SequenceMatcher, autojunk=False) ---")
    print(f"  body sizes: A={len(body_a)} bytes, B={len(body_b)} bytes")
    sm = difflib.SequenceMatcher(None, body_a, body_b, autojunk=False)
    ratio = sm.ratio()
    print(f"  similarity ratio: {ratio:.4f}  ({ratio*100:.1f}%)")

    opcodes = sm.get_opcodes()
    tag_bytes = {"equal": 0, "replace": 0, "insert": 0, "delete": 0}
    for tag, i1, i2, j1, j2 in opcodes:
        tag_bytes[tag] += max(i2 - i1, j2 - j1)
    print(f"  opcode byte totals: {tag_bytes}")

    non_equal = [op for op in opcodes if op[0] != "equal"]
    print(f"  non-equal regions: {len(non_equal)} (showing up to 30)")
    for tag, i1, i2, j1, j2 in non_equal[:30]:
        print(f"    {tag:8s} A[0x{i1:04X}:0x{i2:04X}] ({i2-i1}B)  "
              f"B[0x{j1:04X}:0x{j2:04X}] ({j2-j1}B)")
    if len(non_equal) > 30:
        print(f"    ...and {len(non_equal)-30} more")


def main():
    path_a = os.path.join(GAME_DIR, FILE_A)
    path_b = os.path.join(GAME_DIR, FILE_B)
    with open(path_a, "rb") as f:
        data_a = f.read()
    with open(path_b, "rb") as f:
        data_b = f.read()

    print(f"A = {FILE_A} ({len(data_a)} bytes)")
    print(f"B = {FILE_B} ({len(data_b)} bytes)\n")

    header_a = parse_mz_header(data_a)
    header_b = parse_mz_header(data_b)
    header_bytes_a, header_bytes_b = diff_headers(header_a, header_b)

    relocs_a = parse_relocations(data_a, header_a)
    relocs_b = parse_relocations(data_b, header_b)
    diff_relocations(relocs_a, relocs_b)

    body_a = data_a[header_bytes_a:]
    body_b = data_b[header_bytes_b:]
    diff_bodies(body_a, body_b)


if __name__ == "__main__":
    main()
