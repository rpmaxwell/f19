#!/usr/bin/env python3
"""Shim: the automation socket client now lives in f19.emulator.automation (WP-4).

`Dosbox` is `f19.emulator.Emulator`: same constructor (host, port), same methods (peek(seg, off, n) still works next
to peek(phys, n), peek_many, poke, key_down/key_up/tap/scan_tap/release_arrows/out, close), and the old private
names `_send`, `_recv_line`, `_held` stay available for nav.py and the research scripts until they move.
Host and port come from f19.core.settings (F19_AUTOMATION_HOST / F19_AUTOMATION_PORT, default 127.0.0.1:6000).
"""
import sys as _sys; from pathlib import Path as _P
_sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "src"))  # until the package move
from f19.core.settings import settings as _S  # noqa: E402
from f19.emulator.automation import (AutomationError, DesyncError, Emulator, PokeError, connect)  # noqa: E402,F401

DEFAULT_HOST = _S.host
DEFAULT_PORT = _S.port
Dosbox = Emulator
