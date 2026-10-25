"""DOSBox-X save-state files: named checkpoints and the slot files, under Settings.saves (F19_DATA/saves).

DOSBox-X save slot N is the file `<N>.sav` in its -savedir (src/misc/savestates.cpp: `slotname << slot+1`). A
checkpoint is a copy kept as `checkpoint_<name>.sav`, so a later save to the slot can't overwrite it.

What this module can do without a GUI: list checkpoints, copy a checkpoint into a slot (install) or a slot out to a
checkpoint (keep), and give the launch arguments that point DOSBox-X at this directory. Triggering the save or load
itself needs the host key or the Capture menu; the AppleScript menu clicker for that stays in
research/scripts/savestate.py. A LOADSTATE/SAVESTATE verb on the automation socket would make it scriptable (not
part of the refactor).

After loading a state made while nav was flying, run scripts/release_arrows.py (or Emulator.release_arrows()).
"""
import shutil

from f19.core.settings import settings as _settings

ACTIVE_SLOT = 1            # the slot this project has always used (saves/1.sav)


def savedir(settings=None):
    return (settings or _settings).saves


def _safe(name):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def checkpoint_path(name, settings=None):
    return savedir(settings) / f"checkpoint_{_safe(name)}.sav"


def slot_path(slot=ACTIVE_SLOT, settings=None):
    return savedir(settings) / f"{int(slot)}.sav"


def checkpoints(settings=None):
    """Names of the saved checkpoints, sorted."""
    d = savedir(settings)
    if not d.is_dir(): return []
    return sorted(p.name[len("checkpoint_"):-len(".sav")] for p in d.glob("checkpoint_*.sav"))


def install(name, slot=ACTIVE_SLOT, settings=None):
    """Copy checkpoint `name` over slot file `slot` (then load that slot in DOSBox-X). Returns the slot path."""
    src = checkpoint_path(name, settings)
    if not src.exists(): raise FileNotFoundError(f"no checkpoint {name!r} ({src})")
    dst = slot_path(slot, settings)
    shutil.copyfile(src, dst)
    return dst


def keep(name, slot=ACTIVE_SLOT, settings=None):
    """Copy slot file `slot` (just saved in DOSBox-X) to checkpoint `name`. Returns the checkpoint path."""
    src = slot_path(slot, settings)
    if not src.exists(): raise FileNotFoundError(f"no slot file {src}")
    dst = checkpoint_path(name, settings)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def launch_args(settings=None):
    """Arguments for f19.emulator.launch(extra=...): use this directory for the save slots."""
    return ["-savedir", str(savedir(settings))]
