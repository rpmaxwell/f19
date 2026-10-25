"""The DOSBox-X side: the automation socket client (PEEK/POKE/keys), save-state files, launching the binary.
No screenshots: screen state comes from memory (f19.memory)."""
from f19.emulator.automation import (AutomationError, DesyncError, Emulator, PokeError, connect)  # noqa: F401
