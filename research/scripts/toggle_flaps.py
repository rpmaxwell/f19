#!/usr/bin/env python3
"""
Smoke test for the automation-socket layer (dosbox_automation.py): toggles
F19's flaps switch ("9" per the Quick Reference Card - Toggles row: gear=6,
autopilot=7, bay doors=8, flaps=9, brakes=0) 20 times at a 2-second cadence.

Deliberately small and single-purpose - this is acceptance-criteria test 3
from ~/projects/dosbox-x/prd.md (Python -> DOWN/UP over the socket, guest
receives it) run against a real, visible toggle instead of an arbitrary key,
so a human watching the cockpit panel can confirm each tap actually flips
the flap indicator. Not a memory-mapping tool - see FINDINGS.md and the
other scripts in this directory for that.

Prerequisite: DOSBox-X running with F19 already loaded (any screen where
the flaps toggle is live, e.g. in-flight or on the runway) and
`automation port = 6000` set in dosbox-x.conf (already true in
~/Library/Preferences/dosbox-x.conf).

Usage: python3 scripts/toggle_flaps.py [host] [port]
"""
import sys
import time

from dosbox_automation import Dosbox, DEFAULT_HOST, DEFAULT_PORT

FLAPS_KEY = "9"
REPEATS = 20
INTERVAL_SECS = 2.0


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_HOST
    port = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_PORT

    with Dosbox(host, port) as dos:
        print(f"connected to {host}:{port} - toggling flaps ({FLAPS_KEY}) "
              f"{REPEATS}x at {INTERVAL_SECS}s intervals")
        for i in range(1, REPEATS + 1):
            dos.tap(FLAPS_KEY)
            print(f"  [{i}/{REPEATS}] flaps toggled")
            if i < REPEATS:
                time.sleep(INTERVAL_SECS)
    print("done")


if __name__ == "__main__":
    main()
