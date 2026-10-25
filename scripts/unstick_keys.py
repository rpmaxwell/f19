"""After loading a save state: if the game no longer reacts to keys, try the
two likely causes, checking after each whether keys work again (F2 then F1
should change the view).

  1. modifiers left "held" in the guest by the Ctrl+Alt host-key save/load:
     send key-up for Ctrl/Alt/Shift (both sides)
  2. keyboard interrupt stuck "in service" in the PIC: send EOI (OUT 20 20)

  unstick_keys.py <window_id>
"""
import subprocess, sys, time
from pathlib import Path
from PIL import Image, ImageChops
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox

WIN = sys.argv[1]; TMP = Path("/tmp") / "unstick.png"
def view():
    subprocess.run(["screencapture", "-x", "-o", "-l", WIN, str(TMP)], check=False)
    im = Image.open(TMP).convert("RGB"); w, h = im.size
    return im.crop((0, int(h * 0.07), w, int(h * 0.56)))
def keys_work(db):
    a = view(); db.tap("f2", 0.6); time.sleep(1.5); b = view(); db.tap("f1", 0.6); time.sleep(1.5)
    return ImageChops.difference(a, b).getbbox() is not None

with Dosbox() as db:
    if keys_work(db): print("keys already work"); sys.exit(0)
    for k in ("lctrl", "rctrl", "lalt", "ralt", "lshift", "rshift"):
        db.key_down(k); time.sleep(0.15); db.key_up(k); time.sleep(0.15)
    time.sleep(0.5)
    if keys_work(db): print("FIXED by releasing modifier keys"); sys.exit(0)
    db.out(0x20, 0x20); time.sleep(0.5)
    if keys_work(db): print("FIXED by EOI to the PIC (keyboard interrupt was stuck in service)"); sys.exit(0)
    print("still not responding - neither cause; see logs/f19-dosbox.log")
