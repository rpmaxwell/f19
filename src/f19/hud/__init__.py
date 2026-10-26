"""The HUD: a tiled terminal display of telemetry frames. It never reads memory (an adapter makes the frames:
f19.cli.hud for memlog replays) and imports only core, rules and telemetry.

  data.py      HudFrame (per frame), HudContext (per flight), History (kept across frames)
  view.py      one frame's derived values (detection margins, time of day, ...), shared by the widgets
  widget.py    the Widget interface and registry;  widgets/  map, nav, radars, aircraft, fuel, score, log, banner, status
  layout.py    the layout engine (TOML files in layouts/, default.toml = the old nav_tui arrangement)
  screen.py    the in-memory cell buffer and colours;  terminal.py  the terminal (alt screen, keys, size)
  app.py       Hud (update / handle / render) and HudSink (a TelemetrySink driving a terminal)
"""
from f19.hud.app import Hud, HudSink
from f19.hud.data import History, HudContext, HudFrame
from f19.hud.layout import Box, LayoutSet
from f19.hud.layout import load as load_layout
from f19.hud.screen import Screen
from f19.hud.widget import REGISTRY, TextWidget, Widget, register

__all__ = ["Hud", "HudSink", "History", "HudContext", "HudFrame", "Box", "LayoutSet", "load_layout", "Screen",
           "REGISTRY", "TextWidget", "Widget", "register"]
