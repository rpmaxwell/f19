"""Replay fixtures and helpers: the recorded flights (memlog snapshots + nav CSV + events + plan) that the pilot tests feed through
the reactions, phases and the arbiter (tests/unit/pilot/test_pilot_*.py).

Sources (fixtures/replay, built by make_fixtures.py):
  memlog snapshots   game memory, one every ~1-1.3 s of real time (MemLog spreads a 49 KB read over several nav cycles,
                     so a snapshot is not atomic and is named by the MIDPOINT of its read window)
  nav CSV            one row per nav cycle (~0.2-0.3 s with --memlog): inputs (x, y, alt, spd, hdg, tscale) and the
                     decisions (want, phase, attack_state, escape, fighter, ...) AFTER every override of that cycle
  events.txt         phase events with epoch times ("takeoff done", "terrain escape: ...", "... attack start")

Until 2026-10-30 this module also replayed the OLD phases / missile_watch over the flights and froze their agreement with the
nav log in baseline.json; that went with the old code (the new code's replays are pinned by digests in the pilot tests).

Known limits of comparing a replay with the nav log (by design of the data, not a bug):
  - nav's route want before a reaction's override isn't logged: route_want = the logged want of the last row where no
    reaction was active (stale by the length of the reaction, seconds to minutes for FighterWatch).
  - snapshots are sparser than nav cycles: state machines with timers (CLEAR_S, next_ok, settle) step at ~1 Hz.
  - the player state in a snapshot is up to ~0.5 s off the nav row it is compared with.
"""
import bisect, csv, json, math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

import product

S = product.settings()
FIX = S.data / "fixtures" / "replay"
K = 64.0


def wrap(d): return (d + 180) % 360 - 180


def have_fixtures() -> bool:
    return (FIX / "manifest.json").exists()


def manifest() -> dict:
    return json.loads((FIX / "manifest.json").read_text())


# ---------------------------------------------------------------------------------------------------- fake connection
class SnapDB:
    """A stand-in for the automation connection that answers PEEKs from one memlog snapshot (as nav_tui.SnapDB) and
    records the keys a phase presses instead of sending them."""
    def __init__(self, windows):
        self.windows, self.raw, self.q, self.keys, self.now = windows, b"", [], [], 0.0
    def load(self, raw, now=0.0): self.raw, self.now = raw, now
    def _read(self, phys, n):
        out, pos = bytearray(n), 0
        for _, lo, hi in self.windows:
            if lo <= phys < hi or lo < phys + n <= hi:
                a, b = max(lo, phys), min(hi, phys + n)
                out[a - phys:b - phys] = self.raw[pos + a - lo:pos + b - lo]
            pos += hi - lo
        return bytes(out)
    def _send(self, cmd):
        _, addr, n = cmd.split(); seg, off = addr.split(":")
        self.q.append("PEEK-REPLY " + self._read(int(seg, 16) * 16 + int(off, 16), int(n)).hex())
    def _recv_line(self): return self.q.pop(0)
    def peek(self, seg, off, n): return self._read(int(seg, 16) * 16 + int(off, 16), n)
    # keys: recorded with the replay time
    def tap(self, key, hold_secs=0.05): self.keys.append((self.now, key))
    def scan_tap(self, code, hold_secs=0.5, shift=False): self.keys.append((self.now, f"scan{code:#x}{'+shift' if shift else ''}"))
    def key_down(self, key): self.keys.append((self.now, key + "+"))
    def key_up(self, key): self.keys.append((self.now, key + "-"))


# ---------------------------------------------------------------------------------------------------- one flight
@dataclass
class Run:
    run: str
    nav: str
    plan: str
    windows_cover: list
    dir: Path = field(init=False)
    def __post_init__(self):
        self.dir = FIX / "logs" / "memlog" / self.run
        w = json.loads((self.dir / "windows.json").read_text())
        self.offset, self.windows = w["offset"], [tuple(x) for x in w["windows"]]
        self.snap_files = sorted(self.dir.glob("snap_*.bin"), key=lambda f: float(f.stem[5:]))
        self.rows = [r for r in csv.DictReader(open(FIX / "logs" / "nav" / f"{self.nav}.csv")) if r.get("x")]
        self.row_t = [float(r["t"]) for r in self.rows]
        self.events = []
        ev = FIX / "logs" / "nav" / f"{self.nav}.events.txt"
        for line in (ev.read_text().splitlines() if ev.exists() else []):
            t, _, text = line.partition(" ")
            try: self.events.append((float(t), text))
            except ValueError: pass
        self.plan_dir = FIX / "plan" / self.plan
        self.route = json.loads((self.plan_dir / "route.json").read_text())
        self.mission = json.loads((self.plan_dir / "mission_mem.json").read_text())

    @classmethod
    def load(cls, run):
        m = manifest()[run]
        return cls(run, m["nav"], m["plan"], m["windows"])

    def row_at(self, t):
        """The nav row nearest in time."""
        i = bisect.bisect_left(self.row_t, t)
        if i >= len(self.rows) or (i > 0 and t - self.row_t[i - 1] < self.row_t[i] - t): i -= 1
        return self.rows[max(0, i)]

    def db(self):
        db = SnapDB(self.windows)
        from f19.memory import reader as R
        R._offset[id(db)] = self.offset              # find_offset would scan 0x10000-0xA0000 through the fake
        return db

    def snaps(self, t0=None, t1=None):
        for f in self.snap_files:
            t = float(f.stem[5:])
            if (t0 is None or t >= t0) and (t1 is None or t <= t1): yield t, f.read_bytes()

    def event_times(self, prefix):
        return [t for t, e in self.events if e.startswith(prefix)]


def theater_grid(theater):
    """nav's _gbyte (territory grid byte at a game position) and FighterWatch's terrain_type."""
    from f19.gamedata import wld
    g = wld.grid(theater)
    gbyte = lambda x, y: int(g[min(15, max(0, int(y) >> 11)), min(15, max(0, int(x) >> 11))])
    return gbyte, (lambda x, y: (gbyte(x, y) >> 2) & 3)


# ---------------------------------------------------------------------------------------------------- terrain probe
def make_terrain_ahead(npz_path):
    """scripts/nav.py terrain_ahead (the height_game branch), copied: nav is module-level code and can't be imported.
    When the probe moves into the package, call it from there instead (and the baseline must not change)."""
    t = np.load(npz_path); H, step = t["height_game"], int(t["height_step"])
    def terrain_ahead(p, hdg, alt, max_km=6.0, half_width_km=0.5, margin_ft=100):
        hr = math.radians(hdg); sx, cy = math.sin(hr), -math.cos(hr)
        for d in np.arange(0.25, max_km + 0.01, 0.25):
            tops = []
            for w in (-half_width_km, 0.0, half_width_km):
                gx, gy = p[0] + (sx * d - cy * w) * K, p[1] + (cy * d + sx * w) * K
                i, j = int(gx // step), int(gy // step)
                if 0 <= j < H.shape[0] and 0 <= i < H.shape[1]: tops.append(int(H[j, i]))
            if tops and max(tops) > 0 and max(tops) >= alt - margin_ft: return float(d), max(tops)
        return None
    return terrain_ahead


# ---------------------------------------------------------------------------------------------------- attacks
def build_attacks(route, mission):
    """scripts/nav.py build_attacks, copied (module-level in nav): {role: dict(mode, T, record, launch_km, photo_km)}."""
    from f19.core.geometry import game_dist_units
    pts = route["points"]
    out = {}
    for t in mission["targets"]:
        role = t["role"].rstrip("?"); T = (float(t["x"]), float(t["y"]))
        kinds = {a.get("kind"): (a["x"], a["y"]) for a in pts if a.get("role") == role}
        mode = "maverick" if "launch" in kinds else "photo" if "photo" in kinds else "overfly"
        out[role] = dict(mode=mode, T=T, record=t.get("record"),
                         launch_km=game_dist_units(kinds["launch"], T) / K if "launch" in kinds else None,
                         photo_km=game_dist_units(kinds["photo"], T) / K if "photo" in kinds else None)
    return out
