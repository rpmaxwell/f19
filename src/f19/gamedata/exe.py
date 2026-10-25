"""The EGAME.EXE / END.EXE load images, loaded once (lazily, on first use).

The images are the EXEPACK'd .EXEs unpacked into flat load images (no relocations applied): settings.egame_bin /
settings.end_bin (F19_DATA/game/EGAME.bin, END.bin). When one is missing it is unpacked from the game install
(F19_GAME_DIR/EGAME.EXE, END.EXE) and written there. EGAME's data segment (DGROUP) starts at image offset DS_FILE,
so ds:X is image[DS_FILE + X]; ds_base() finds it from the runtime library's copyright string (ds:0008).

  python -m f19.gamedata.exe <in.exe> <out.bin>     unpack one .EXE (as analysis/unexepack.py)
"""
import struct
import sys
from functools import lru_cache

from f19.core.settings import settings as S

DS_FILE = 0x1ECF0                  # EGAME DGROUP in the load image (= ds_base(egame()))


def unexepack(b):
    """Unpack a Microsoft EXEPACK'd DOS .EXE (bytes) -> (load image bytes, info dict).

    The output is the decompressed load module as DOS would have it in memory after the unpacker ran (paragraph 0 =
    load segment). info: real_cs, real_ip, real_ss, real_sp, size, lo (literal below), end (decompressed region
    ends)."""
    hdr = struct.unpack_from('<14H', b)
    cparhdr, ip, cs = hdr[4], hdr[10], hdr[11]
    img = bytearray(b[cparhdr * 16:(hdr[2] - 1) * 512 + (hdr[1] or 512)])
    eh = cs * 16                                          # EXEPACK header at CS:0
    real_ip, real_cs, _, exe_size, real_sp, real_ss, dest_len = struct.unpack_from('<7H', img, eh)
    if b'RB' not in img[eh + 12:eh + 20]: raise ValueError("no EXEPACK signature")
    packed = img[:eh]
    out = bytearray(dest_len * 16)
    out[:len(packed)] = packed
    src = len(packed) - 1
    while src >= 0 and packed[src] == 0xFF: src -= 1      # 0xFF padding at the end
    # EXEPACK works in place, backwards: src from end of packed data, dst from end of dest.
    buf = bytearray(max(len(out), len(packed)))
    buf[:len(packed)] = packed
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
            raise ValueError(f"bad command {cmd:#x} at {s + 1:#x}")
        if cmd & 1: break
    # whatever lies below the last decompressed byte is unchanged (literal) data
    lo = s + 1
    out[:lo] = buf[:lo]
    return bytes(out), dict(real_cs=real_cs, real_ip=real_ip, real_ss=real_ss, real_sp=real_sp, size=len(out), lo=lo,
                            end=d + 1)


def _image(path, exe):
    """The load image at `path`; unpacked from the game's `exe` (and written to `path`) when it doesn't exist yet."""
    if not path.exists():
        img, _ = unexepack((S.game_dir / exe).read_bytes())
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img)
    return path.read_bytes()


@lru_cache(None)
def egame():
    """EGAME.EXE load image (bytes)."""
    return _image(S.egame_bin, "EGAME.EXE")


@lru_cache(None)
def end():
    """END.EXE load image (bytes)."""
    return _image(S.end_bin, "END.EXE")


def ds_base(img):
    """Image offset of DGROUP: the 'MS Run-Time Library' string sits at ds:0008."""
    return img.index(b"MS Run-Time Library") - 8


def egame_ds(off, n):
    """n bytes of EGAME at ds:off."""
    return egame()[DS_FILE + off:DS_FILE + off + n]


def main(argv):
    """unexepack.py <in.exe> <out.bin>: prints the real CS:IP and SS:SP."""
    img, i = unexepack(open(argv[0], 'rb').read())
    open(argv[1], 'wb').write(img)
    print(f"real CS:IP {i['real_cs']:04x}:{i['real_ip']:04x}  SS:SP {i['real_ss']:04x}:{i['real_sp']:04x}  image "
          f"{i['size']:#x} bytes (literal below {i['lo']:#x}, decompressed region ends {i['end']:#x})")


if __name__ == "__main__":
    main(sys.argv[1:])
