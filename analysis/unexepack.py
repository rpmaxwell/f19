"""Unpack a Microsoft EXEPACK'd DOS .EXE into a flat load image (no relocations applied).

  unexepack.py <in.exe> <out.bin>

Prints the real CS:IP and SS:SP. The output is the decompressed load module as DOS
would have it in memory after the unpacker ran (paragraph 0 = load segment).
"""
import struct, sys

b = open(sys.argv[1], 'rb').read()
hdr = struct.unpack_from('<14H', b)
cparhdr, ip, cs = hdr[4], hdr[10], hdr[11]
img = bytearray(b[cparhdr * 16:(hdr[2] - 1) * 512 + (hdr[1] or 512)])
eh = cs * 16                                          # EXEPACK header at CS:0
real_ip, real_cs, _, exe_size, real_sp, real_ss, dest_len = struct.unpack_from('<7H', img, eh)
skip = struct.unpack_from('<H', img, eh + 14)[0] if img[eh + 16:eh + 18] == b'RB' else 1
sig_off = eh + 16 if skip != 1 or img[eh + 16:eh + 18] == b'RB' else eh + 14
assert b'RB' in img[eh + 12:eh + 20], "no EXEPACK signature"
packed = img[:eh]
out = bytearray(dest_len * 16)
out[:len(packed)] = packed
src = len(packed) - 1
while src >= 0 and packed[src] == 0xFF: src -= 1          # 0xFF padding at the end
dst = dest_len * 16 - 1 if False else None
# EXEPACK works in place, backwards: src from end of packed data, dst from end of dest.
buf = bytearray(max(len(out), len(packed)))
buf[:len(packed)] = packed
dst = len(packed) - 1 + (len(out) - len(packed))         # end of decompressed area
# standard algorithm: dest end = dest_len*16 relative to load start? use (exe_size area) - see below
s, d = src, len(out) - 1
while True:
    cmd = buf[s]; s -= 1
    cnt = buf[s] << 8 | buf[s - 1]; s -= 2
    if cmd & 0xFE == 0xB0:
        v = buf[s]; s -= 1
        for _ in range(cnt): out[d] = v; d -= 1
    elif cmd & 0xFE == 0xB2:
        for _ in range(cnt): out[d] = buf[s]; d -= 1; s -= 1
    else:
        raise SystemExit(f"bad command {cmd:#x} at {s + 1:#x}")
    if cmd & 1: break
# whatever lies below the last decompressed byte is unchanged (literal) data
lo = s + 1
out[:lo] = buf[:lo]
open(sys.argv[2], 'wb').write(out)
print(f"real CS:IP {real_cs:04x}:{real_ip:04x}  SS:SP {real_ss:04x}:{real_sp:04x}  image {len(out):#x} bytes (literal below {lo:#x}, decompressed region ends {d + 1:#x})")
