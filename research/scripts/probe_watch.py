#!/usr/bin/env python3
"""
Screenshot-diff watcher for the keystroke-delivery test environment
(FINDINGS.md's "Stage 0/1/2" design, 2026-08-07). Replaces the old approach
of one 640KB debugger memdump captured around a manually-timed human/agent
handshake - that gave a single ambiguous snapshot and no way to tell "the
keystroke didn't register" from "it registered outside my capture window".

This instead polls the dosbox-x window with plain `screencapture` (no
debugger break-in, so emulation never pauses - nothing to desync) on a
tight loop for a generous duration, and diffs every frame against the
FIRST frame captured, not the previous one. That distinction matters: the
DOS text cursor blinks in hardware (~500ms), which oscillates the frame
back toward the baseline every other tick. A real change (a typed
character staying on screen) instead drifts away from the baseline and
stays there. So "first tick where changed-pixel-count clears the
threshold AND stays cleared for CONFIRM_TICKS in a row" is used as the
signal, which cursor blink alone cannot produce.

Removes timing pressure entirely: start a watch for a generous window
(the whole point is you don't have to precisely land your keystroke inside
a narrow capture) and read the result back afterward.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))
import memdump_loop as ml

PROBE_DIR = f"{ml.PROJECT_DIR}/dumps/probes"
PER_PIXEL_THRESHOLD = 40       # 0-255 grayscale delta to count one pixel as "changed"
# Calibrated against two live runs (2026-08-07): two screencaptures of
# *provably identical* on-screen content (nothing typed yet) differ by
# ~30-100 changed pixels tick to tick - PNG/rendering jitter, not signal.
# A single real typed character (confirmed independently via a debugger
# memory read-back showing DOS actually consumed it) showed up as only
# ~140-288 changed pixels - much smaller than a full-window occlusion event
# (~6300-1,097,000 px, seen when another app covered the window). 500 was
# calibrated off the occlusion case and was too high, producing a false
# FAIL on a real character. 130 sits above the noise floor with a smaller
# margin than ideal - if this starts flapping on noise, widen PER_PIXEL_
# THRESHOLD or add a second confirmation signal rather than raising this
# back toward the occlusion-sized range.
CHANGED_PIXEL_THRESHOLD = 130  # min changed-pixel count for a frame to count as "different"
CONFIRM_TICKS = 3              # consecutive above-threshold ticks required (filters cursor blink)


def _screen_scale_factor() -> float:
    """Pixels-per-point of the main display. `screencapture -x` writes native
    pixel resolution (2x on this Retina display, confirmed live: a 2940px-wide
    capture against a 1470pt-wide desktop from `Finder`'s `bounds of window of
    desktop`) while AppleScript's window-bounds calls report points - detected
    at runtime rather than hardcoded so this keeps working if that ever
    changes (external display, resolution change)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        subprocess.run(["screencapture", "-x", tmp_path], check=True)
        img_w = Image.open(tmp_path).width
    finally:
        os.remove(tmp_path)
    bounds = ml.run_osa('tell application "Finder" to get bounds of window of desktop')
    left, _, right, _ = (int(p.strip()) for p in bounds.split(","))
    return img_w / (right - left)


def _capture_crop(box_px) -> Image.Image:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        subprocess.run(["screencapture", "-x", tmp_path], check=True)
        img = Image.open(tmp_path).crop(box_px).convert("L")
        img.load()  # force read before the backing temp file is removed
        return img
    finally:
        os.remove(tmp_path)


def _frontmost_process() -> str:
    try:
        return ml.run_osa('tell application "System Events" to name of first process whose frontmost is true')
    except RuntimeError:
        return ""


def watch(duration_s: float, interval_s: float = 0.35, label: str = "watch") -> dict:
    """Polls the dosbox-x window for `duration_s` seconds. Returns a dict with
    the run's output directory and the first *persistent* change detected (or
    None). Every tick is logged to `<run_dir>/ticks.jsonl`; the baseline frame
    and the first over-threshold frame are saved as PNGs for visual sanity
    checking.

    `screencapture -x` grabs whatever is actually visible on screen at a set
    of pixel coordinates - it has no idea which application "owns" that
    region. `ml.get_dosbox_window_bounds()` only reports where DOSBox-X's
    window *thinks* it is, with no check for whether some other window (e.g.
    the terminal driving this very script) is sitting on top of it. A live
    run on 2026-08-07 measured a completely unrelated Claude Code/iTerm2
    session for 8 seconds this way and reported a false PASS - the "keystroke
    landing" it detected was actually that terminal's own UI redrawing.
    Every tick therefore explicitly checks (and the loop starts by forcing)
    dosbox-x as the frontmost process, and any tick where that check fails is
    excluded from "persistent change" detection rather than silently
    counted."""
    ml.focus_dosbox()  # raises RuntimeError if dosbox-x can't be made frontmost

    scale = _screen_scale_factor()
    x, y, w, h = ml.get_dosbox_window_bounds()
    box = (int(x * scale), int(y * scale), int((x + w) * scale), int((y + h) * scale))

    os.makedirs(PROBE_DIR, exist_ok=True)
    run_dir = f"{PROBE_DIR}/{int(time.time())}_{label}"
    os.makedirs(run_dir, exist_ok=True)
    jsonl_path = f"{run_dir}/ticks.jsonl"

    baseline_img = _capture_crop(box)
    baseline_arr = np.asarray(baseline_img, dtype=np.int16)
    baseline_img.save(f"{run_dir}/baseline.png")

    ticks = []
    saved_change_frame = False
    occlusion_warned = False
    t0 = time.time()
    with open(jsonl_path, "w") as jf:
        while time.time() - t0 < duration_s:
            tick_t = time.time()
            frontmost = _frontmost_process()
            img = _capture_crop(box)
            arr = np.asarray(img, dtype=np.int16)
            changed_px = int(np.sum(np.abs(arr - baseline_arr) > PER_PIXEL_THRESHOLD))
            rec = {
                "elapsed": round(tick_t - t0, 3),
                "unix_ts": tick_t,
                "changed_px": changed_px,
                "frontmost": frontmost,
                "dosbox_frontmost": frontmost == "dosbox-x",
            }
            ticks.append(rec)
            jf.write(json.dumps(rec) + "\n")
            jf.flush()
            if not rec["dosbox_frontmost"] and not occlusion_warned:
                print(f"  [warn] t+{rec['elapsed']:.2f}s: frontmost process is "
                      f"{frontmost!r}, not dosbox-x - captures from here on may not "
                      "be showing the dosbox-x window at all.")
                occlusion_warned = True
            if changed_px >= CHANGED_PIXEL_THRESHOLD and not saved_change_frame:
                img.save(f"{run_dir}/first_over_threshold_{rec['elapsed']:.2f}s.png")
                saved_change_frame = True
            time.sleep(max(0.0, interval_s - (time.time() - tick_t)))

    first_change = None
    for i in range(len(ticks) - CONFIRM_TICKS + 1):
        window = ticks[i:i + CONFIRM_TICKS]
        if all(t["changed_px"] >= CHANGED_PIXEL_THRESHOLD and t["dosbox_frontmost"] for t in window):
            first_change = window[0]
            break

    result = {
        "run_dir": run_dir,
        "n_ticks": len(ticks),
        "max_changed_px": max((t["changed_px"] for t in ticks), default=0),
        "first_persistent_change": first_change,
        "any_occlusion": any(not t["dosbox_frontmost"] for t in ticks),
    }
    with open(f"{run_dir}/result.json", "w") as f:
        json.dump(result, f, indent=2)
    return result


def main():
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    label = sys.argv[2] if len(sys.argv) > 2 else "manual"
    if not ml.dosbox_running():
        print("dosbox-x is not running.")
        sys.exit(1)
    print(f"Watching dosbox-x window for {duration:.1f}s (label={label})...")
    result = watch(duration, label=label)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
