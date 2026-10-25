"""Shim: unpack a Microsoft EXEPACK'd DOS .EXE into a flat load image (no relocations applied); the code is
src/f19/gamedata/exe.py (unexepack(bytes) -> (image, info)).

  unexepack.py <in.exe> <out.bin>

Prints the real CS:IP and SS:SP. The output is the decompressed load module as DOS
would have it in memory after the unpacker ran (paragraph 0 = load segment).
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
import sys  # noqa: E402
from f19.gamedata.exe import main, unexepack  # noqa: E402,F401

if __name__ == "__main__":
    main(sys.argv[1:])
