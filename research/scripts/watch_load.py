"""Read-only recorder for what happens right after a save-state load: waits for sane flight data, then logs
alt / pitch / roll / throttle / flags / sensitivity at 4 Hz for 60 s and snapshots EGAME's data area at the
start and after 5 s (logs/probe/load_<t>.csv / .bin). Presses nothing."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sys as _sys; from pathlib import Path as _Path; _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "src"))   # the f19 package
from f19.emulator import Emulator as Dosbox
from f19.memory.reader import read_state, read_flight, read_sensitivity, _offset
from probe_controls import snap
ROOT = Path(__file__).resolve().parent.parent; t0 = int(time.time())
out = open(ROOT / f"logs/probe/load_{t0}.csv", "w", buffering=1); out.write("t,alt,spd,hdg,pitch,roll,thr,flags,sens\n")
with Dosbox() as db:
    start, t_arm, zero_since = None, time.time(), None
    while True:
        if start is None and time.time() - t_arm > 300: sys.exit("no flight data in 5 min - giving up (port released)")
        try:
            s = read_state(db, tries=1)
            if not (s["x"] and s["alt"] < 40000 and s["spd"] < 900): raise RuntimeError("not sane")
        except RuntimeError:
            _offset.clear(); time.sleep(0.5); continue
        now = time.time()
        if start is None and "--release" in sys.argv: db.release_arrows(); print("released arrows at first sane read", flush=True)
        start = start or now; f = read_flight(db)
        out.write(f"{now - start:.2f},{s['alt']},{s['spd']},{s['hdg']:.1f},{f['pitch']:.2f},{f['roll']:.2f},{f['throttle']},{f['flags']:#x},{read_sensitivity(db)}\n")
        if now - start < 0.3: (ROOT / f"logs/probe/load_{t0}_a.bin").write_bytes(snap(db))
        if 5 <= now - start < 5.3: (ROOT / f"logs/probe/load_{t0}_b.bin").write_bytes(snap(db))
        zero_since = (zero_since or now) if s["alt"] == 0 else None
        if now - start > 60 or (zero_since and now - zero_since > 2): break      # done, or crashed: let go of the port
        time.sleep(0.25)
print("recorded", out.name)
