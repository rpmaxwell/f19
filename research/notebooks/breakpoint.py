#!/usr/bin/env python3
"""
breakpoint.py

Command-line harness for investigating F19 Strike Fighter's DOS memory
allocation / EXEC-transition behavior via the DOSBox-X debugger.

Usage:
    python breakpoint.py --breakpoint "BPINT 21 48"
    python breakpoint.py --breakpoint "BP 1CE8:68A0" --max-hits 50
    python breakpoint.py --breakpoint "BPINT 21 4B" --regs CS IP --output exec_calls.json
"""

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

from debugbox import DOSBoxDebugger

# --- register-read parsing --------------------------------------------

# Strips ncurses/VT100 escape sequences (cursor moves, color codes, etc.)
# that get interleaved into dbg.ev()'s output as the debugger console
# redraws the typed command character by character.
ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
EV_RE = re.compile(r"LOG: EV of '([^']*)' is:\s*\n(?:LOG: )?([0-9a-fA-F]+(?:\s+[0-9a-fA-F]+)*)")


def get_registers(dbg, reg_names=("BX", "CS", "IP")):
    """
    Sends an EV command for the requested registers and parses the
    result out of the debugger console output. Returns
    {reg_name: int_value}, or None if no parseable EV result was found
    (e.g. because the emulator hasn't actually paused yet).
    """
    new_text = dbg.ev(" ".join(reg_names))
    cleaned = ANSI_RE.sub('', new_text)
    m = EV_RE.search(cleaned)
    if not m:
        return None
    names = m.group(1).split()
    values = [int(v, 16) for v in m.group(2).split()]
    return dict(zip(names, values))


def read_cstring(dbg, seg, off, max_len=64):
    """
    Reads bytes at seg:off via debugbox's dump_memory() (DOSBox-X's
    MEMDUMPBIN under the hood) and decodes up to the first null byte.
    DOS EXEC filenames (DS:DX for INT 21h AH=4Bh) are ASCIZ strings.
    """
    data = dbg.dump_memory(seg, offset=off, length=max_len)
    nul = np.where(data == 0)[0]
    end = int(nul[0]) if len(nul) else len(data)
    return bytes(data[:end]).decode("ascii", errors="replace")


def run_until_break(dbg, reg_names=("BX", "CS", "IP"), max_wait=5.0, poll_interval=0.03):
    """
    Resumes execution and polls for the breakpoint to actually land,
    since dbg.run() returns before the emulator has necessarily paused.
    Returns the register dict on success, or None on timeout.
    """
    dbg.run()
    start = time.time()
    while time.time() - start < max_wait:
        regs = get_registers(dbg, reg_names)
        if regs is not None:
            return regs
        time.sleep(poll_interval)
    return None


def breakpoint_run(dbg, breakpoint, reg_names=("BX", "CS", "IP"), max_hits=200, max_wait=5.0):
    """
    Clears existing breakpoints, sets the given one, and captures
    registers at up to max_hits consecutive hits.
    """
    allocs = []
    dbg.send("BPDEL *")
    dbg.send(breakpoint)

    for i in range(max_hits):
        try:
            regs = run_until_break(dbg, reg_names, max_wait=max_wait)
        except (OSError, EOFError, BrokenPipeError) as e:
            # Quitting DOSBox-X out from under the debugger connection
            # raises here (e.g. OSError: [Errno 5] Input/output error).
            # Treat it as "the run is over" rather than a crash, and keep
            # whatever hits we already captured.
            print(f"[{i}] lost connection to DOSBox-X ({e!r}) -- stopping, keeping {len(allocs)} hits")
            break
        except KeyboardInterrupt:
            print(f"\n[{i}] interrupted -- keeping {len(allocs)} hits")
            break

        if regs:
            # For INT 21h AH=4Bh (EXEC), DS:DX points at the ASCIZ
            # filename being loaded. Resolve it live, while the
            # debugger is still paused at this exact hit -- the string
            # can't be recovered later from a saved JSON, since memory
            # keeps changing after we resume.
            if "DX" in regs and "DS" in regs:
                try:
                    regs["filename"] = read_cstring(dbg, regs["DS"], regs["DX"])
                except (OSError, EOFError, BrokenPipeError, TimeoutError) as e:
                    regs["filename"] = f"<read failed: {e!r}>"
            print(f"[{i}] {regs}")
            allocs.append(regs)
        else:
            print(f"[{i}] timed out waiting for break")
    return allocs


# --- LOGC-based instruction trace parsing (not used by breakpoint_run,
# kept here for scripts that want to combine this with the LOGC/MCB loop
# from earlier in the investigation) ------------------------------------

def get_logcpu_file(path):
    return Path(path).read_text(errors="replace")


def get_pairs(text):
    pairs = [line.strip() for line in text.splitlines() if ":" in line]
    return Counter(pairs)


def get_transitions(text):
    pairs = [line.strip() for line in text.splitlines() if ":" in line]
    return Counter(zip(pairs[:-1], pairs[1:]))


# --- main ---------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--breakpoint", required=True,
        help='Debugger breakpoint command, e.g. "BPINT 21 48" or "BP 1CE8:68A0"',
    )
    parser.add_argument(
        "--regs", nargs="+", default=["BX", "CS", "IP"],
        help="Registers to capture at each hit (default: BX CS IP)",
    )
    parser.add_argument(
        "--max-hits", type=int, default=200,
        help="Stop after this many successful hits (default: 200)",
    )
    parser.add_argument(
        "--max-wait", type=float, default=5.0,
        help="Seconds to wait for each breakpoint to actually land before giving up (default: 5.0)",
    )
    parser.add_argument(
        "--conf-path", default="/Users/rmaxwell/Downloads/f19_2.conf",
        help="Path to the DOSBox-X conf file",
    )
    parser.add_argument(
        "--dump-path", default="/Users/rmaxwell/Documents/personal/MEMDUMP.BIN",
        help="Path DOSBoxDebugger uses for memory dumps",
    )
    parser.add_argument(
        "--output", default="breakpoint_results.json",
        help="Path to save captured hits as JSON (default: breakpoint_results.json)",
    )
    args = parser.parse_args()

    dbg = DOSBoxDebugger(conf_path=args.conf_path, dump_path=args.dump_path)
    dbg.start()

    # breakpoint_run() catches the connection-loss / interrupt cases itself
    # and returns whatever it captured rather than raising, so this should
    # always come back with real (possibly partial) data -- but keep the
    # try/except as a backstop in case dbg.start() or something outside the
    # loop misbehaves too.
    allocs = []
    try:
        allocs = breakpoint_run(
            dbg,
            args.breakpoint,
            reg_names=tuple(args.regs),
            max_hits=args.max_hits,
            max_wait=args.max_wait,
        )
    except (OSError, EOFError, BrokenPipeError, KeyboardInterrupt) as e:
        print(f"\nStopped ({e!r}) -- keeping {len(allocs)} hits captured so far.")

    # Write results before attempting any further dbg calls, so a second
    # connection error (e.g. from dbg.stop() below) can't cost you the data.
    Path(args.output).write_text(json.dumps(allocs, indent=2))
    print(f"Saved {len(allocs)} hits to {args.output}")

    # If DOSBox-X was already quit manually this may raise/no-op depending
    # on internals -- that's fine, the data is already on disk at this point.
    try:
        dbg.close()
    except (OSError, EOFError, BrokenPipeError):
        pass

    if not allocs:
        print("No hits captured.")
        sys.exit(1)


if __name__ == "__main__":
    main()