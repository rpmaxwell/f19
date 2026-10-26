#!/bin/sh
# Launch F-19 in the custom DOSBox-X build (automation socket; port from the config, default 6000).
# Paths as f19.core.settings: F19_DOSBOX_BIN, F19_DOSBOX_CONF (same as python -m f19.emulator.launch).
exec "${F19_DOSBOX_BIN:-$HOME/projects/dosbox-x/src/dosbox-x}" -conf "${F19_DOSBOX_CONF:-$HOME/Library/Preferences/f19-dosbox.conf}" "$@"
