"""Fast post-load fix for save states made while nav held a key: polls the plane's position at the known data offset
(no rescan) 20x a second; when a load changes it, at once releases the arrows in DOSBox (cancels a saved auto-repeat),
sends the arrow BREAK codes to the game (clears its held-key state), centres the stick (keypad 5), then logs attitude for
30 s to logs/probe/fix_<t>.csv. Waits up to 5 min.  fix_after_load.py [offset_hex, default 0x10420]"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as S  # noqa: E402
import struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
from f19.memory.addresses import POS, ALT, PITCH
OFF = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x10420
out = open(S.logs / f"probe/fix_{int(time.time())}.csv", "w", buffering=1)
out.write("t,alt,pitch,x,y\n")
def rd(db):
    p, a, pr = db.peek_many([(POS + OFF, 10), (ALT + OFF, 2), (PITCH + OFF, 2)])
    return struct.unpack_from("<I", p, 0)[0] >> 5, struct.unpack_from("<I", p, 6)[0] >> 5, struct.unpack("<H", a)[0], struct.unpack("<h", pr)[0] * 360 / 65536
with Dosbox() as db:
    last, t_arm, t_fix = rd(db), time.time(), None
    while True:
        cur = rd(db); now = time.time()
        if t_fix is None and (abs(cur[0] - last[0]) > 64 or abs(cur[1] - last[1]) > 64) and cur[0] and cur[2] < 40000:
            for k in ("up", "down", "left", "right"): db.key_up(k)
            db.release_arrows(); db.tap("kp5", 0.2); t_fix = now
            print(f"load seen at {cur}: released + centred in {time.time() - now:.2f} s", flush=True)
        if t_fix: out.write(f"{now - t_fix:.2f},{cur[2]},{cur[3]:.2f},{cur[0]},{cur[1]}\n")
        if (t_fix and now - t_fix > 30) or (not t_fix and now - t_arm > 300): break
        last = cur; time.sleep(0.05)
print("done")
