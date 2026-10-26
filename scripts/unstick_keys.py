"""After loading a save state: if the game no longer reacts to keys, try the two likely causes, checking after each
whether keys work again.

"Keys work" is a memory check (no screenshots, WP-4): Insert cycles the keyboard sensitivity 1 -> 2 -> 3 -> 1
(old phys 0x417DA, f19.memory.addresses.SENS), so one Insert must change that byte; two more put it back.

  1. modifiers left "held" in the guest by the Ctrl+Alt host-key save/load:
     send key-up for Ctrl/Alt/Shift (both sides)
  2. keyboard interrupt stuck "in service" in the PIC: send EOI (OUT 20 20)

  unstick_keys.py            (needs F-19 in flight; sends Insert, modifier releases and an EOI)
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
from f19_state import read_sensitivity


def keys_work(db, settle=1.0):
    """Tap Insert and see the sensitivity byte move; if it did, tap twice more to restore it."""
    s0 = read_sensitivity(db)
    db.tap("ins", 0.2); time.sleep(settle)
    if read_sensitivity(db) == s0: return False
    for _ in range(2): db.tap("ins", 0.2); time.sleep(settle)
    if read_sensitivity(db) != s0: print(f"note: sensitivity is {read_sensitivity(db)}, was {s0}")
    return True


if __name__ == "__main__":
    with Dosbox() as db:
        if keys_work(db): print("keys already work"); sys.exit(0)
        for k in ("lctrl", "rctrl", "lalt", "ralt", "lshift", "rshift"):
            db.key_down(k); time.sleep(0.15); db.key_up(k); time.sleep(0.15)
        time.sleep(0.5)
        if keys_work(db): print("FIXED by releasing modifier keys"); sys.exit(0)
        db.out(0x20, 0x20); time.sleep(0.5)
        if keys_work(db): print("FIXED by EOI to the PIC (keyboard interrupt was stuck in service)"); sys.exit(0)
        print("still not responding - neither cause; see the DOSBox-X log (F19_DATA/logs/f19-dosbox.log)")
