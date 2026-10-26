"""`f19 fly --plain`: nav's one-column text display (the `out` block of scripts/nav.py's loop) as a telemetry sink,
and the fallback when the HUD fails. Reads only the Frame's nav view (the Runner's `_view`), never memory."""
import sys

from f19.core.geometry import bearing_deg, dist, game_dist_km
from f19.core.units import K

GAME_WP_NAMES = ["WP1 (midpoint)", "WP2 primary", "WP3 secondary", "WP4 landing"]
TERRITORY = {"enemy": "ENEMY - behind the lines (scoring; ejecting here = capture)", "friendly": "friendly", "neutral": "neutral"}


def plain_text(v, radars=(), clear=True):
    """nav's text frame from the view dict `v` (Runner._view); radars: mission_mem 'radars' rows with a system."""
    p, ts = v["p"], v.get("ts") or 1.0
    turn, want = v["turn"], v["want"]
    arrow = "<<< LEFT" if turn < -3 else ("RIGHT >>>" if turn > 3 else "on course")
    hold_alt = v.get("hold_alt")
    head = ("\033[H\033[2J" if clear else "") + "  F-19 NAV" + (f"   [alt hold {hold_alt:.0f} ft]" if hold_alt is not None else "") \
        + ("   [HDG HOLD]" if v.get("hdg_hold") else "")
    leg, wps = v["leg"], v["wps"]
    out = [head, v.get("banner", ""),
           f"  leg {leg}/{len(wps) - 1}   next turn in {v['to_turn_km']:5.1f} km",
           f"  FLY HEADING  {want:4.0f}    now {v['hdg']:4.0f}   {arrow} {abs(turn):3.0f}",
           f"  off route    {abs(v['xtrack_km']):5.1f} km {'right' if v['xtrack_km'] > 0 else 'left'}", "",
           f"  next on route: {v['next_name']}",
           "  game waypoints (exact):"]
    gw = v.get("gw") or []
    for k in range(min(4, len(gw))):
        out.append(f"   {'>' if k == v.get('sel') else ' '} {GAME_WP_NAMES[k]:<16} {game_dist_km(p, gw[k]):6.1f} km  brg {bearing_deg(p, gw[k]):4.0f}")
    out += ["", f"  alt {v['alt']:5d} ft   spd {v['kt']:3d} kt" + (f"   TIME x{ts:.1f}" if ts > 1.3 else "")
            + ("" if not v.get("sens_msg") else "   " + v["sens_msg"])]
    if v.get("territory"):
        out += ["", "  territory: " + TERRITORY.get(v["territory"], v["territory"])]
    if radars:
        near = sorted(radars, key=lambda r: dist(p, (r["x"], r["y"])))[:4]
        out += ["", "  nearest radars:"] + [f"    {r['place']:<13} {r['system']:<6} {r['type']} {dist(p, (r['x'], r['y'])) / K:6.1f} km"
                                           f" / {r['range_km']:3d}  brg {bearing_deg(p, (r['x'], r['y'])):4.0f}" for r in near]
    if v.get("phase_log"): out += ["", "  mission log:"] + [f"    {m}" for m in v["phase_log"][-6:]]
    if v.get("hdg_hold") and v.get("rate") is not None:
        out.append(f"  turn rate {v['rate']:+5.2f} deg/s  (want {v['rate_cmd']:+5.2f})  {v.get('rolling', '')}")
    return "\n".join(out)


class PlainSink:
    """A telemetry sink printing plain_text() for every frame that carries the nav view."""
    def __init__(self, out=None, radars=(), clear=None):
        self.out = out or (lambda s: print(s, flush=True))
        self.radars = [r for r in radars if r.get("system")]
        self.clear = sys.stdout.isatty() if clear is None else clear
        from f19.telemetry import SinkStats
        self.stats = SinkStats()

    def write(self, tf):
        if tf.frame is None or "p" not in tf.nav: return
        self.out(plain_text(tf.nav, self.radars, self.clear))

    def flush(self, timeout=None): return True
    def close(self, timeout=10.0): return True


class FallbackSink:
    """The HUD with nav's rule: if the display fails, say so once and carry on with the plain text."""
    def __init__(self, primary, fallback, say=print):
        self.primary, self.fallback, self.say, self.failed = primary, fallback, say, None
        self.stats = getattr(primary, "stats", None) or fallback.stats

    def write(self, tf):
        if self.failed is None:
            try: self.primary.write(tf); return
            except Exception as e:                  # noqa: BLE001
                self.failed = e
                try: self.primary.close()
                except Exception: pass              # noqa: BLE001
                self.say(f"fly: display failed ({e!r}) - plain text from here")
        self.fallback.write(tf)

    def flush(self, timeout=None): return (self.primary if self.failed is None else self.fallback).flush(timeout)
    def close(self, timeout=10.0):
        ok = True
        for s in (self.primary, self.fallback):
            try: ok = bool(s.close(timeout)) and ok
            except Exception: pass                  # noqa: BLE001
        return ok
