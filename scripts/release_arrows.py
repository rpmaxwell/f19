"""Run right after loading a save state made while nav was flying: delivers the arrow keys' release codes to the
game (a press stored in the state's keyboard buffer otherwise holds nose-down/roll forever). Presses nothing.
(f19.emulator.Emulator.release_arrows)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from dosbox_automation import Dosbox
with Dosbox() as db: db.release_arrows()
print("arrow releases sent")
