"""Snapshot a window of guest memory (default phys 0x30000-0x60000, where
EGAME's live data sits) about once a second via automation-socket PEEKs.

PEEKs are sent in small batches, waiting for each batch's replies, so the
emulator never services thousands in one frame - a big batch stalls the frame
and makes injected key DOWN/UP pairs land together (the game never sees the
key held). Writes logs/mem/<start>/snap_<t>.bin until logs/mem/STOP exists.

  mem_sampler.py [period_s] [start_hex] [end_hex]
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
START = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x30000
END = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x60000
BATCH = 64
OUT = Path(__file__).resolve().parent.parent / "logs" / "mem" / f"{int(time.time())}_{START:05X}"
OUT.mkdir(parents=True, exist_ok=True)
STOP = OUT.parent / "STOP"; STOP.unlink(missing_ok=True)

with Dosbox() as db:
    while not STOP.exists():
        t0 = time.time(); buf = bytearray()
        for a in range(START, END, 64 * BATCH):
            n = min(BATCH, (END - a) // 64)
            for i in range(n): db._send(f"PEEK {(a + i * 64) >> 4:X}:0 64")
            for i in range(n): buf += bytes.fromhex(db._recv_line().split(" ", 1)[1])
        t1 = time.time()
        (OUT / f"snap_{(t0 + t1) / 2:.2f}.bin").write_bytes(buf)
        time.sleep(max(0, PERIOD - (time.time() - t0)))
print("stopped", OUT)
