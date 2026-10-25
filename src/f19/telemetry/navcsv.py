"""The nav CSV (logs/nav/<epoch>.csv) and events file (<epoch>.events.txt) formats, as scripts/nav.py writes them.

Rows (nav.py :747): one per control cycle, 32 columns (NAV_COLUMNS), written by csv.writer (CRLF line ends, minimal
quoting). Each column has a fixed formatting rule (KIND); e.g. t is f"{now:.2f}", x/y f"{p:.0f}", hdg/want ".1f", flags
f"{flags:#x}", ints as str(int), None as "". Short rows: the keyboard-sensitivity row (nav.py :658) has 11 values,
t and press = "sens <n>->ins".

Older logs have fewer columns (19 .. 29: the header grew by appending), so a file's own header is the authority when
reading; writers take a column list. EXTENDED_COLUMNS appends what WP-1 found missing (refactor plan section 7):
route_want (the route's heading BEFORE reactions override `want`), behavior (the active behavior), plan_version.
Readers that use csv.DictReader by name (memlog_load, missile_replay, the replay harness) are unaffected by them.

Events: one line per event, "<epoch:.0f> <message>" (nav.phase_event; every caller prefixes f"{now:.0f} ").
"""
from dataclasses import dataclass
import re

NAV_COLUMNS = ("t", "x", "y", "alt", "spd", "hdg", "leg", "want", "xtrack_km", "to_turn_km", "press", "rate", "rate_cmd",
               "roll", "tscale", "side", "phase", "vs", "cycle_s", "attack_state", "flags", "throttle", "attack_key",
               "locked", "fuel", "fuel_key", "escape", "fighter", "fighter_near", "missile", "m_worst", "m_action")
EXTRA_COLUMNS = ("route_want", "behavior", "plan_version")
EXTENDED_COLUMNS = NAV_COLUMNS + EXTRA_COLUMNS
SENS_ROW_WIDTH = 11                     # nav.py :658: t, 9 x "", press

# formatting kind per column: f<n> = f"{v:.<n>f}", int = str(int), hex = f"{v:#x}", str = as is
KIND = dict(t="f2", x="f0", y="f0", alt="int", spd="int", hdg="f1", leg="int", want="f1", xtrack_km="f2",
            to_turn_km="f2", press="str", rate="f2", rate_cmd="f2", roll="str", tscale="f2", side="str", phase="str",
            vs="f1", cycle_s="f2", attack_state="str", flags="hex", throttle="int", attack_key="str", locked="int",
            fuel="int", fuel_key="str", escape="str", fighter="str", fighter_near="str", missile="int", m_worst="str",
            m_action="str", route_want="f1", behavior="str", plan_version="int")


def format_value(col, v):
    """One CSV cell as nav formats it. None -> "". A str is written as is (whatever the column's kind)."""
    if v is None: return ""
    if isinstance(v, str): return v
    k = KIND.get(col, "str")
    if k[0] == "f": return f"{float(v):.{k[1:]}f}"
    if k == "int":
        if isinstance(v, float) and not v.is_integer(): return repr(v)
        return str(int(v))
    if k == "hex": return f"{int(v):#x}"
    return str(v)


def parse_value(col, s):
    """Inverse of format_value: "" -> None, numbers by the column kind; a cell that doesn't parse stays a str
    (lossless: format_value writes it back unchanged)."""
    if s == "": return None
    k = KIND.get(col, "str")
    try:
        if k[0] == "f": return float(s)
        if k == "int": return int(s)
        if k == "hex": return int(s, 16) if s.lower().startswith("0x") else s
    except ValueError:
        return s
    return s


def format_row(values, columns, width=None):
    """[str] for csv.writer: the first `width` columns (all if None) of `values` (dict column -> typed value)."""
    cols = columns if width is None else columns[:width]
    return [format_value(c, values.get(c)) for c in cols]


def parse_row(cells, columns):
    """(values dict, width or None if full). Cells beyond the header are kept as "_extra" (a list of str)."""
    vals = {c: parse_value(c, s) for c, s in zip(columns, cells)}
    if len(cells) > len(columns): vals["_extra"] = list(cells[len(columns):])
    return vals, (len(cells) if len(cells) < len(columns) else None)


# ------------------------------------------------------------------------------------------------------------- events
@dataclass(frozen=True, slots=True)
class Event:
    """One events-file line: t = the epoch prefix (nav writes it rounded to whole seconds), text = the message.
    t None: a line without the prefix (kept verbatim)."""
    t: float | None
    text: str

    def line(self):
        return self.text if self.t is None else f"{self.t:.0f} {self.text}"

    @classmethod
    def parse(cls, line):
        m = _EV.fullmatch(line)
        return cls(float(m[1]), m[2]) if m else cls(None, line)


_EV = re.compile(r"(0|[1-9]\d*) (.*)", re.S)     # no leading zeros: they would not round-trip
