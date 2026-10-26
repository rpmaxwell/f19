"""One frame's derived values, computed once and shared by every widget (nav_tui.UI._context): the plane's position
and flight values, the opponent level and EMV, its territory cell, the radar sites and aircraft with their detection
margin (the game's return + EMV - 100, rules.radar), the time of day.

View.c is a dict with nav_tui's context keys (widgets were ported from nav_tui 1:1); View.hud is the Hud (layout,
focus, history, log, context)."""
import math
import re

from f19.core.geometry import game_len
from f19.core.units import K
from f19.core.types import ThreatKind
from f19.rules import radar as rg
from f19.rules.scoring import OPPONENT_NAMES as OPP_NAMES


class View:
    __slots__ = ("c", "hud")

    def __init__(self, c, hud): self.c, self.hud = c, hud

    @property
    def ctx(self): return self.hud.ctx

    @property
    def history(self): return self.hud.history


def _sites(state):
    return [dict(record=t.id, x=t.x, y=t.y, type=t.type, flags=t.flags, alert=t.alert, dead=t.dead, on=t.on)
            for t in state.threats if t.kind is ThreatKind.SITE] if state else []


def _aircraft(state):
    return [dict(slot=t.id, x=t.x, y=t.y, alt=int(t.alt_ft), type=t.type, name=t.name, flags=t.flags, alert=t.alert)
            for t in state.threats if t.kind is ThreatKind.AIRCRAFT] if state else []


def build(hud, fr):
    """The context dict for HudFrame fr (the latest one)."""
    ctx, st, v = hud.ctx, fr.state, fr.view
    p = v.get("p") or (st.xy if st else (0.0, 0.0))
    opp = st.opp if st is not None and st.opp is not None else 4
    if not 0 <= opp <= 3: opp = OPP_NAMES.index(ctx.settings["opponent"]) if ctx.settings.get("opponent") in OPP_NAMES else 2
    alt = int(v.get("alt", st.alt_ft if st else 0)); kt = int(v.get("kt", st.speed_kt if st else 0))
    hdg = float(v.get("hdg", st.heading if st else 0.0))
    emv = st.emv if st is not None else None
    if emv is None or emv > 400: emv = rg.emv(alt, kt, opp, int(abs(st.att.roll if st else 0) / 360 * 65536))
    grid = hud.grid
    gb = int(grid[min(15, int(p[1]) >> 11), min(15, int(p[0]) >> 11)]) if grid is not None else 8
    h65 = int(hdg / 360 * 65536) & 0xFFFF
    names, systems = hud.names, ctx.radar_systems
    sites = []
    for s in _sites(st):
        R, P, dop = rg.radar_spec(s["type"]) if s["type"] < 23 else (0, 0, False)
        m = rg.site_return(int(p[0]), int(p[1]), alt, h65, kt, opp, gb, s["x"], s["y"], s["type"]) + emv - 100 if R else -999
        meta = names.get(s["record"], {})
        sy = systems.get(s["type"], (f"type {s['type']}", None, None))
        sites.append(dict(s, margin=m, doppler=dop, power=P, place=meta.get("place") or ctx.places.get((s["x"], s["y"])) or f"record {s['record']}",
                          system=sy[0], range_km=meta.get("range_km") or sy[2], dist=game_len(s["x"] - p[0], s["y"] - p[1]) / K))
    acs = []
    for a in _aircraft(st):
        sp = hud.specs.get(a["type"], {})
        m = -999
        if sp.get("radar_type"):
            m = rg.site_return(int(p[0]), int(p[1]), alt, h65, kt, opp, gb, a["x"], a["y"], sp["radar_type"], a["alt"]) + emv - 100
        acs.append(dict(a, margin=m, spec=sp, friendly=a["type"] in rg.FRIENDLY_TYPES, dist=math.dist(p, (a["x"], a["y"])) / K))
    start = (ctx.mission.get("times") or {}).get("start")
    tod, clock = "", None
    if st is not None and st.t is not None:
        clock = int(st.t) // 2
        sec = clock * 2
        if start and re.match(r"\d+:\d+", start): sec += int(start.split(":")[0]) * 3600 + int(start.split(":")[1]) * 60
        tod = f"{sec // 3600 % 24:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}"
    gw, sel = v.get("gw"), v.get("sel")
    if gw is None and st is not None and st.waypoints: gw, sel = list(st.waypoints), st.wp_index
    return dict(v, p=p, alt=alt, kt=kt, hdg=hdg, opp=opp, emv=emv, cell=gb, sites=sites, aircraft=acs, state=st,
                clock=clock, tod=tod, gw=gw, sel=sel, now=fr.t, wps=v.get("wps") or ctx.wps,
                targets=ctx.mission.get("targets", []), phase=v.get("phase", ""))
