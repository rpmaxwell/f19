#!/bin/sh
# Launch F-19 in the custom DOSBox-X build (automation socket on port 6000).
exec "$HOME/projects/dosbox-x/src/dosbox-x" -conf "$HOME/Library/Preferences/f19-dosbox.conf" "$@"
