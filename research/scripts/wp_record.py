"""Ground recording for finding waypoint storage: every ~0.5 s save a 320 KB (0x00000-0x50000)
memory snapshot (batched PEEKs) plus the waypoint-panel crop, until
logs/wp_rec/STOP exists. Output: logs/wp_rec/<start>/{snap_<t>.bin, panel_<t>.png}"""
import subprocess, sys, time
from pathlib import Path
from PIL import Image

def _full(path):
    """Load a window capture at Retina (1280x856) scale even from a 1x display."""
    im = Image.open(path).convert("RGB")
    return im.resize((1280, 856), Image.NEAREST) if im.width == 640 else im
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
WIN = sys.argv[1]
OUT = Path(__file__).resolve().parent.parent / "logs" / "wp_rec" / str(int(time.time())); OUT.mkdir(parents=True)
STOP = OUT.parent / "STOP"; STOP.unlink(missing_ok=True)
with Dosbox() as db:
    while not STOP.exists():
        t0 = time.time(); buf = bytearray()
        for a in range(0, 0x50000, 64 * 64):          # skip video buffers above 0x50000
            for i in range(64): db._send(f"PEEK {(a + i * 64) >> 4:X}:0 64")
            for i in range(64): buf += bytes.fromhex(db._recv_line().split(" ", 1)[1])
        t = (t0 + time.time()) / 2
        (OUT / f"snap_{t:.2f}.bin").write_bytes(buf)
        subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(OUT / "_f.png")])
        _full(OUT / "_f.png").crop((700, 545, 1130, 795)).save(OUT / f"panel_{t:.2f}.png")
        time.sleep(max(0, 0.4 - (time.time() - t0)))
print("stopped", OUT)
