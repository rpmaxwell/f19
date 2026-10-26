"""The text panes, ported 1:1 from scripts/nav_tui.py (_pane_*):

  nav       phase, banners, steering, leg, altitude / speed / throttle, territory
  radars    per site: alert level and detection margin against the plane right now
  aircraft  nearest aircraft: type, distance, radar (pulse / doppler), alert on us, detection margin
  fuel      gauge, burn (model and measured), endurance, fuel left at landing along the rest of the route, plan here
  score     the END.EXE rating so far from the game's own event list (rules.scoring.current_score)
  log       nav's mission log and the game's events (hits, IDs, photos, kills); the key help
  banner    only nav's banners (not in the default layout)
"""
import time

from f19.core.geometry import bearing_deg as bearing
from f19.core.geometry import game_len
from f19.core.units import GROUND_PER_KT, K
from f19.hud.screen import BLUE, CYAN, DIM, FG, GRN, ORG, RED, WHITE, YEL
from f19.hud.widget import TextWidget, register
from f19.rules.fuel import STEPS_PER_MIN, TANK_LB, burn_step
from f19.rules.scoring import OPPONENT_NAMES as OPP_NAMES
from f19.rules.scoring import current_score, next_sample_s

EXTRA_TANK_LB = int(TANK_LB)          # Extra Fuel tank (EGAME 0x7ae6)
CLUTTER = {0: (0.0, "mountains"), 1: (0.5, "rough"), 2: (1.0, "flat"), 3: (1.5, "sea")}   # territory grid bits 0x0C
HELP = ["keys: t territory  p radar power  d detection zones  c SAM rings  s SA-14 cells  a aircraft  g grid",
        "      + - zoom  f fit route / follow  Tab focus  z zoom pane  l layout  ? help off"]


def banner_lines(c):
    out = []
    for b in (c.get("banner") or "").split("\n"):
        if b.strip(): out.append([(b.strip(), RED if "!!!" in b or "GO-AROUND" in b else YEL)])
    return out


@register
class NavWidget(TextWidget):
    name = "nav"
    min_h, pref_h = 5, 9

    def title_extra(self, view): return f"{view.c['phase']} "

    def lines(self, view, w, h):
        c = view.c
        out = banner_lines(c)
        holds = (f"alt hold {c['hold_alt']:.0f}  " if c.get("hold_alt") is not None else "") + ("HDG HOLD  " if c.get("hdg_hold") else "")
        ts = c.get("ts", 1.0); tr = view.history.time_ratio()
        out.append([(holds, CYAN), (f"TIME x{ts:.0f}  " if ts > 1.3 else "", ORG), (f"game {tr:.1f}x real" if tr else "", DIM)])
        if c.get("wps"):
            turn = c.get("turn", 0.0)
            arrow = "<<< LEFT" if turn < -3 else "RIGHT >>>" if turn > 3 else "on course"
            out.append([("FLY ", FG), (f"{c.get('want', 0):03.0f}", WHITE), (f"  now {c['hdg']:03.0f}  ", FG), (f"{arrow} {abs(turn):.0f}", YEL if abs(turn) > 3 else GRN)])
            out.append(f"leg {c.get('leg')}/{len(c['wps']) - 1}  turn in {c.get('to_turn_km', 0):.1f} km  off route {abs(c.get('xtrack_km', 0)):.1f} km "
                       f"{'R' if c.get('xtrack_km', 0) > 0 else 'L'}")
            out.append([("next: ", DIM), (c.get("next_name") or "", WHITE)])
        st = c["state"]; vs = c.get("vs")
        out.append([(f"alt {c['alt']:5d} ft", WHITE), (f" vs {vs:+.0f}" if vs is not None else "", FG), (f"   {c['kt']:3d} kt", WHITE),
                    (f"   thr {st.systems.throttle}%" if st is not None and st.systems.throttle is not None else "", FG),
                    (f"   bank {st.att.roll:+.0f} pitch {st.att.pitch:+.0f}" if st is not None else "", DIM)])
        side = c.get("territory")
        if side: out.append([("territory ", DIM), ({"enemy": "ENEMY (scoring; ejecting = capture)"}.get(side, side), RED if side == "enemy" else BLUE if side == "friendly" else FG)])
        if c.get("rate") is not None: out.append([(f"turn rate {c['rate']:+.2f} (want {c['rate_cmd']:+.2f}) {c.get('rolling') or ''}", DIM)])
        if c.get("sens_msg"): out.append([(c["sens_msg"], ORG)])
        if c.get("gw"):
            out.append([(" ".join(f"{'>' if k == c.get('sel') else ' '}{k + 1} {game_len(g[0] - c['p'][0], g[1] - c['p'][1]) / K:.0f}km/{bearing(c['p'], g):03.0f}"
                                  for k, g in enumerate(c["gw"])), DIM)])
        return out


@register
class BannerWidget(TextWidget):
    name = "banner"
    min_h = 1

    def lines(self, view, w, h): return banner_lines(view.c)


def alert_bar(a, n=8):
    col = RED if a > 0xC0 else ORG if a > 0x7F else YEL if a else DIM
    k = round(a / 255 * n); return [("█" * k + "░" * (n - k), col), (f" {a:3d}", col)]


def margin_spans(m):
    if m <= -999: return [("   -  ", DIM)]
    return [(f"{m:+4d}", RED if m > 0 else ORG if m > -15 else YEL if m > -40 else GRN), (" SEEN" if m > 0 else "     ", RED)]


@register
class RadarsWidget(TextWidget):
    name = "radars"
    min_h = 3
    min_w = pref_w = 70

    def title_extra(self, view):
        c = view.c
        return f"EMV {c['emv']}  opp {OPP_NAMES[c['opp']] if c['opp'] < 4 else c['opp']} "

    def lines(self, view, w, h):
        c = view.c; st = c["state"]
        net = (st.net_alert if st is not None else None) or 0
        rids = (st.radar_ids if st is not None else None) or 0
        vids = (st.vis_ids if st is not None else None) or 0
        cf, cn = CLUTTER[(c["cell"] >> 2) & 3]
        hdr = [(f"cell {cn} x{cf:g}  ", RED if cf > 1 else FG), ("network ", DIM), (f"{net}", RED if net >= 0x80 else FG),
               (" ALERT" if net >= 0x80 else "", RED), (f"  IDs R{rids} V{vids}", RED if rids or vids else DIM)]
        out = [hdr, [("site          sys    P/D   km/rng  brg  alert         margin    on", DIM)]]
        live = [s for s in c["sites"] if not s["dead"]]
        live.sort(key=lambda s: (-(s["margin"] > 0), -s["alert"], s["dist"]))
        for s in live[:max(0, h - 2)]:
            rng = f"{s['range_km']}" if s.get("range_km") else "-"
            out.append([(f"{s['place'][:13]:<13} {s['system'][:6]:<6} {'D' if s['doppler'] else 'P'}  {s['dist']:6.1f}/{rng:<3} {bearing(c['p'], (s['x'], s['y'])):03.0f}  ", FG)]
                       + alert_bar(s["alert"]) + [("  ", FG)] + margin_spans(s["margin"]) + [("  *" if s["on"] else "  .", GRN if s["on"] else DIM)])
        if not live: out.append([("no radar sites read yet", DIM)])
        return out


@register
class AircraftWidget(TextWidget):
    name = "aircraft"
    min_h = 2
    min_w = pref_w = 70

    def title_extra(self, view): return f"{sum(1 for a in view.c['aircraft'] if not a['friendly'])} hostile "

    def lines(self, view, w, h):
        c = view.c
        out = [[("type        km  brg   alt  radar       alert         margin   ", DIM)]]
        acs = sorted(c["aircraft"], key=lambda a: (a["friendly"], a["dist"]))
        for a in acs[:max(0, h - 1)]:
            sp = a["spec"]
            radar = (f"{'doppler' if sp.get('doppler') else 'pulse'} {sp.get('radar_range', 0)}" if sp.get("radar_type") else "none")
            col = BLUE if a["friendly"] else FG
            out.append([(f"{a['name'][:8]:<8} {a['dist']:6.1f}  {bearing(c['p'], (a['x'], a['y'])):03.0f} {a['alt']:5d}  {radar:<11} ", col)]
                       + (alert_bar(a["alert"]) + [("  ", FG)] + margin_spans(a["margin"]) if not a["friendly"] else [("friendly", BLUE)])
                       + [(" VIS-ID" if a["flags"] & 0x800 else "", RED)])
        if not acs: out.append([("no aircraft in the sim slots", DIM)])
        return out


@register
class FuelWidget(TextWidget):
    name = "fuel"
    min_h, pref_h = 3, 5

    def __init__(self, ctx):
        super().__init__(ctx)
        self.capacity, self.reserve = ctx.capacity, ctx.reserve

    def lines(self, view, w, h):
        c = view.c; st = c["state"]; hist = view.history
        fuel = st.systems.fuel_lb if st is not None else None
        if fuel is None: return [[("fuel not read yet", DIM)]]
        self.capacity = max(self.capacity, fuel)
        tanks = sum(n for wpn, n in st.systems.loadout if wpn == 17)
        total = fuel + tanks * EXTRA_TANK_LB
        n = max(8, min(30, w - 34)); k = round(fuel / max(self.capacity, 1) * n)
        col = RED if fuel < 1500 else YEL if fuel < 3000 else GRN
        out = [[("[", DIM), ("█" * k, col), ("░" * (n - k), DIM), ("] ", DIM), (f"{fuel:5d} lb", WHITE),
                (f"  +{tanks} tank{'s' if tanks != 1 else ''} ({tanks * EXTRA_TANK_LB})" if tanks else "", CYAN)]]
        thr = st.systems.throttle
        leak = (st.systems.dmg_count or 0) if st.systems.dmg_bits & 0x20 else 0
        model = burn_step(thr, leak) * STEPS_PER_MIN if thr is not None else None
        meas = hist.burn_measured()
        burn = meas if meas and meas > 0 else model
        out.append([(f"thr {thr}%  " if thr is not None else "", FG), ("burn ", DIM), (f"{burn:.0f} lb/min" if burn else "-", WHITE),
                    (f"  (model {model}{', measured' if meas else ''})" if model is not None else "", DIM), ("  LEAK" if leak else "", RED)])
        if burn:
            mins = total / burn; gkm = c["kt"] * 1.852 / 60 * GROUND_PER_KT; tr = hist.time_ratio()
            out.append([("endurance ", DIM), (f"{mins:.0f} game min", WHITE), (f" ({mins / tr:.0f} real)" if tr else "", DIM),
                        ("  range ", DIM), (f"{mins * gkm:.0f} km", WHITE), (f" at {c['kt']} kt", DIM)])
            left = c.get("route_left_km")
            if left is not None and gkm > 0:
                eta = left / gkm; at = total - eta * burn
                col = GRN if at >= self.reserve else YEL if at >= 0 else RED
                out.append([("route left ", DIM), (f"{left:.0f} km", WHITE), (f"  ETA {eta:.0f} min  ", FG), ("at landing ", DIM),
                            (f"{at:.0f} lb", col), (" OK" if at >= self.reserve else " BELOW RESERVE" if at >= 0 else " SHORT", col)])
        at = c.get("attrs") or []; leg = c.get("leg", 1); wps = c.get("wps") or []
        if 0 < leg < min(len(at), len(wps)) and "fuel_lb" in at[leg] and "fuel_lb" in at[leg - 1]:
            (ax, ay), (bx, by) = wps[leg - 1][0], wps[leg][0]; L2 = (bx - ax) ** 2 + (by - ay) ** 2 or 1e-9
            f_ = max(0.0, min(1.0, ((c["p"][0] - ax) * (bx - ax) + (c["p"][1] - ay) * (by - ay)) / L2))
            plan = at[leg - 1]["fuel_lb"] + (at[leg]["fuel_lb"] - at[leg - 1]["fuel_lb"]) * f_
            d_ = fuel - plan
            out.append([("plan here ", DIM), (f"{plan:.0f} lb", FG), (f" (you {d_:+.0f})", GRN if d_ >= -200 else YEL if d_ >= -800 else RED),
                        ("  plan landing ", DIM), (f"{at[-1].get('fuel_lb', 0):.0f}", FG), (" (internal)", DIM)])
        return out


@register
class ScoreWidget(TextWidget):
    name = "score"
    min_h, pref_h = 3, 4

    def title_extra(self, view): return f"{self.ctx.theater or ''} "

    def lines(self, view, w, h):
        hist, c = view.history, view.c
        ev = hist.events
        if not ev and not hist.have_status: return [[("event list not read yet", DIM)]]
        sc = current_score(ev, view.hud.grid, self.ctx.theater, self.ctx.settings, c["opp"])
        enemy, samples = sc["enemy"], sc["samples"]
        pri, sec, rad, vis = sc["primary"], sc["secondary"], sc["radar_ids"], sc["visual_ids"]
        out = [[("enemy samples ", DIM), (f"{enemy}", WHITE), (f" of {samples}", DIM)]]
        clk, last9 = c.get("clock"), sc["last_sample_clock"]
        if clk is not None and last9 is not None:
            nxt = next_sample_s(last9, clk)
            here = c.get("territory") == "enemy"
            out[0] += [("   next in ", DIM), (f"{nxt:2d} s", FG), (" +1" if here else " (not enemy: 0)", GRN if here else DIM)]
        out.append([("primary ", DIM), ("DONE" if pri else "--", GRN if pri else DIM), ("  secondary ", DIM), ("DONE" if sec else "--", GRN if sec else DIM),
                    ("  IDs ", DIM), (f"radar {rad} visual {vis} ({sc['id_base']:+d})", RED if rad or vis else DIM)])
        if "error" in sc: out.append([(f"score: {sc['error']}", DIM)])
        elif "score" in sc:
            s0, s1 = sc["score"]
            out.append([("base ", DIM), (f"{sc['base']}", WHITE), ("  score ", DIM), (f"{s0}" if s0 == s1 else f"{s0}-{s1}", GRN),
                        (f"   +{sc['per_sample']:.1f}/sample", DIM)])
            if sc["assumed"]: out.append([("? " + "; ".join(a.split(":")[0] for a in sc["assumed"]), DIM)])
        return out


@register
class LogWidget(TextWidget):
    name = "log"
    min_h, pref_h = 2, 7

    def clip(self, lines, h): return lines[-h:][:h]

    def lines(self, view, w, h):
        hud = view.hud
        out = [[(time.strftime("%H:%M:%S ", time.localtime(t_)), DIM), (m, col)] for t_, m, col in hud.history.log]
        if hud.help: out += [[(l, CYAN)] for l in HELP]
        return out or [[("? for keys", DIM)]]
