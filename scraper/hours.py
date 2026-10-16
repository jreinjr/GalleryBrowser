"""Opening-hours parsing for the gallery ranker (plan G4, feature `hours_breadth`).

Bigger galleries keep more public hours; project spaces are open one day a
week or by appointment. Two shapes of hours text exist in the registry:

    per-day (Google Places `weekday_text`): "Tuesday: 11:00 AM – 6:00 PM",
        "Monday: Closed" — with U+202F / U+2009 thin spaces and an en dash;
    range lines (agent-written): "Tue - Sat 10:00am to 6:00pm",
        "Fri 12pm to 5pm", "Sun 12pm to 5pm", "Other days by appointment",
        "or by appointment", "By appointment only".

``parse_hours`` is deterministic and never raises; a line it cannot read
counts as unparsed rather than as zero hours.
"""

from __future__ import annotations

import math
import re
import unicodedata

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_RE = r"(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?"
_DAY_TOKEN = re.compile(rf"\b({_DAY_RE})\b", re.I)
_RANGE = re.compile(rf"\b({_DAY_RE})\s*(?:-|–|—|to|through|thru)\s*({_DAY_RE})\b", re.I)
_TIME = re.compile(
    r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", re.I)
_TIME_SPAN = re.compile(
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)\s*(?:-|–|—|to|until|till|~)\s*"
    r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)?)", re.I)
_APPT = re.compile(r"\bappointment\b|\bby appt\b|\bappointments?\b", re.I)
_CLOSED = re.compile(r"\bclosed\b", re.I)
_NOON = re.compile(r"\bnoon\b", re.I)
_MIDNIGHT = re.compile(r"\bmidnight\b", re.I)

# saturation for hours_breadth: 6 days x 7 hours is "always open" territory
FULL_DAYS = 6.0
FULL_HOURS = 42.0
APPOINTMENT_ONLY_VALUE = 0.05


def _clean(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    s = _NOON.sub("12:00pm", s)
    s = _MIDNIGHT.sub("11:59pm", s)
    return re.sub(r"\s+", " ", s).strip()


def _day_index(tok: str) -> int | None:
    t = tok.lower().rstrip(".")[:3]
    return DAYS.index(t) if t in DAYS else None


def _to_minutes(tok: str, default_pm: bool = False) -> int | None:
    m = _TIME.fullmatch(tok.strip())
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if h > 24 or mi > 59:
        return None
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and default_pm and h < 12:
        # "10 to 6" / "12 - 5": the closing time is the afternoon one
        h += 12
    return h * 60 + mi


def _span_hours(text: str) -> float | None:
    """Sum of the time spans in a line, in hours; None when none parse."""
    total = 0.0
    found = False
    for m in _TIME_SPAN.finditer(text):
        se = _span_minutes(m.group(1), m.group(2))
        if se is None:
            continue
        start, end = se
        if end <= start:
            end += 24 * 60
        span = (end - start) / 60.0
        if 0 < span <= 24:
            total += span
            found = True
    return total if found else None


def _days_in(text: str) -> list[int]:
    """Day indices a line names: ranges expand, lists ('Fri, Sat') enumerate."""
    days: set[int] = set()
    rest = text
    for m in _RANGE.finditer(text):
        a, b = _day_index(m.group(1)), _day_index(m.group(2))
        if a is None or b is None:
            continue
        i = a
        while True:
            days.add(i)
            if i == b:
                break
            i = (i + 1) % 7
        rest = rest.replace(m.group(0), " ")
    for m in _DAY_TOKEN.finditer(rest):
        d = _day_index(m.group(1))
        if d is not None:
            days.add(d)
    return sorted(days)


def parse_hours(lines) -> dict:
    """{days_open, hours_per_week, by_appointment, parsed} from a list of
    hours lines (a single string is split on newlines / semicolons).

    days_open counts distinct weekdays with public hours; hours_per_week sums
    them. by_appointment is True when ANY line mentions appointments;
    appointment-only venues have days_open == 0 and by_appointment True.
    parsed is False when no line yielded either days or an appointment note."""
    if isinstance(lines, str):
        lines = re.split(r"[\n;]+", lines)
    per_day: dict[int, float] = {}
    by_appt = False
    parsed = False
    segs = []
    for raw in lines or []:
        segs.extend(split_segments(_clean(str(raw))))
    for line in segs:
        line = line.strip()
        if not line:
            continue
        if _APPT.search(line):
            by_appt = True
            parsed = True
            # "Sat 12-5 or by appointment" still carries public hours
            if not _TIME_SPAN.search(line):
                continue
        days = list(range(7)) if _DAILY.search(line) else _days_in(line)
        if not days:
            if _TIME_SPAN.search(line) and not per_day:
                # "10am-6pm" with no day named: assume a weekday-ish 5-day week
                hrs = _span_hours(line)
                if hrs:
                    for d in range(1, 6):
                        per_day[d] = max(per_day.get(d, 0.0), hrs)
                    parsed = True
            continue
        if _CLOSED.search(line) and not _TIME_SPAN.search(line):
            parsed = True
            for d in days:
                per_day.setdefault(d, 0.0)
            continue
        hrs = _span_hours(line)
        if hrs is None:
            # a day named with no readable time: count it as open ~5h
            hrs = 5.0
        parsed = True
        for d in days:
            per_day[d] = max(per_day.get(d, 0.0), hrs)
    days_open = sum(1 for h in per_day.values() if h > 0)
    return {"days_open": days_open,
            "hours_per_week": round(sum(per_day.values()), 2),
            "by_appointment": by_appt,
            "parsed": parsed}


def breadth_value(parsed: dict) -> float:
    """0..1 from a parse_hours() result: geometric blend of days/6 and
    hours/42, so 1 day/week scores ~0.17 and Tue-Sat 10-6 (5 x 8h) ~0.9."""
    if not parsed.get("parsed"):
        return 0.0
    days, hrs = parsed["days_open"], parsed["hours_per_week"]
    if days == 0:
        return APPOINTMENT_ONLY_VALUE if parsed.get("by_appointment") else 0.0
    d = min(1.0, days / FULL_DAYS)
    h = min(1.0, hrs / FULL_HOURS)
    value = math.sqrt(d * h) if h > 0 else d * 0.5
    if parsed.get("by_appointment") and days <= 2:
        value = max(value, APPOINTMENT_ONLY_VALUE)
    return round(min(1.0, value), 4)


def hours_breadth(v: dict) -> tuple[float, str]:
    """(value, basis) for a registry venue: Google's weekday_text when the
    crosscheck stored it, else the agent-written hours, else nothing."""
    g = (v.get("google") or {}).get("hours") or []
    own = v.get("hours") or []
    for lines, src in ((g, "google"), (own, "site")):
        if not lines:
            continue
        p = parse_hours(lines)
        if p["parsed"]:
            val = breadth_value(p)
            if p["days_open"] == 0 and p["by_appointment"]:
                return val, f"{src}: by appointment only"
            return val, (f"{src}: {p['days_open']} day(s)/wk, {p['hours_per_week']:g} h/wk"
                         + (", +appointment" if p["by_appointment"] else ""))
    if v.get("status") == "appointment_only":
        return APPOINTMENT_ONLY_VALUE, "status: appointment_only"
    return 0.0, "no hours on record"


# ---------------------------------------------------------------------------
# Grouped display form (Carlton, 2026-10-16): hours are never listed day by
# day. Every city's stored and shown hours read like
#
#     "Tue–Sat 11–6"                 (closed days are never mentioned)
#     "Wed–Sat 12–6, Sun 12–5, Mon–Tue by appointment"
#     "Daily 10–5"
#     "By appointment only"
#
# group_hours() turns any mix of Google per-day lines ("Tuesday: 11:00 AM –
# 6:00 PM") and range lines ("Tue - Sat 10am to 6pm", "Sun Closed", "and by
# appointment") into that one line, plus any free-text note lines it cannot
# read ("Closed through end of August"), kept as they are. It is idempotent:
# a grouped line groups to itself. The raw Google weekday_text stays under a
# venue's `google.hours` as evidence; only `hours` is regrouped.
# ---------------------------------------------------------------------------

DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_FULL_DAY = re.compile(r"^(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\s*:\s*(.*)$", re.I)
_DAILY = re.compile(r"\b(?:daily|every ?day|7 days(?: a week)?)\b", re.I)
_PAREN = re.compile(r"\(([^)]*)\)")
# A grouped line's segments are separated by ", " after a time, "closed …",
# or "appointment" — never inside a day list like "Tue, Thu, Sat 10–5".
_SEG_SPLIT = re.compile(r"\s*[,;]\s+(?=(?:closed\b|(?:mon|tue|wed|thu|fri|sat|sun|daily)))", re.I)
_SEG_END = re.compile(r"\d|\bclosed\b|appointment|appt|\bhours\b|\)", re.I)
# one-off or labelled lines stay as notes ("First Thursday 5pm to 8pm",
# "During exhibitions: …", "Tokyo: …", "holidays")
_SPECIAL = re.compile(r"\b(?:first|last|second|third|every other|holidays?|during|summer|winter|"
                      r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*[-–]\s*"
                      r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))\b|^[^:]{3,40}:\s", re.I)
_24H = re.compile(r"\bopen 24 hours\b|\b24 hours\b", re.I)


def split_segments(line: str) -> list[str]:
    """'Tue–Sat 11–6, closed Sun–Mon' -> ['Tue–Sat 11–6', 'closed Sun–Mon'];
    'Tue, Thu, Sat 10–5' stays whole."""
    out: list[str] = []
    pending: list[str] = []
    for piece in _SEG_SPLIT.split(line):
        if not piece.strip():
            continue
        # bare day names ("Tue" in "Tue, Thu, Sat 10–5") join the next piece
        # that carries a time, "closed" or appointment; trailing ones the last
        if _SEG_END.search(piece) or _DAILY.search(piece):
            out.append(", ".join(pending + [piece]))
            pending = []
        else:
            pending.append(piece)
    if pending:
        if out:
            out[-1] += ", " + ", ".join(pending)
        else:
            out.append(", ".join(pending))
    return out


def _span_minutes(a: str, b: str) -> tuple[int, int] | None:
    """Start/end minutes for one span; am/pm inferred the gallery way
    ("11–6" is 11am–6pm, "1–6" is 1pm–6pm, "12:00 – 6:00 PM" is noon–6pm)."""
    a_ap = re.search(r"am|pm|a\.m|p\.m", a, re.I)
    b_ap = re.search(r"am|pm|a\.m|p\.m", b, re.I)
    end = _to_minutes(b, default_pm=not b_ap)
    start = _to_minutes(a)
    if start is None or end is None:
        return None
    if not a_ap:
        if b_ap and b_ap.group(0).lower().startswith("p") and start < 12 * 60 and start + 12 * 60 < end:
            start += 12 * 60          # "1 – 6 PM"
        elif not b_ap and start < 7 * 60:
            start += 12 * 60          # "1–6"
    return start, end


def _fmt_time(m: int, suffix: bool) -> str:
    h, mi = divmod(m % (24 * 60), 60)
    h12 = h % 12 or 12
    s = f"{h12}:{mi:02d}" if mi else f"{h12}"
    return s + (("am" if h < 12 else "pm") if suffix else "")


def _fmt_span(start: int, end: int) -> str:
    """'11–6' when it reads unambiguously as gallery hours (start 7am–6:59pm,
    end noon–11:59pm, after the start), else explicit: '6–9pm', '9am–12pm'."""
    if 7 * 60 <= start < 18 * 60 and 12 * 60 <= end < 24 * 60 and end > start:
        # the bare start reads as am for 7–11 and pm for 12–5
        return f"{_fmt_time(start, False)}–{_fmt_time(end, False)}"
    if start >= 12 * 60 and end >= 12 * 60 and end > start:
        return f"{_fmt_time(start, False)}–{_fmt_time(end, True)}"
    return f"{_fmt_time(start, True)}–{_fmt_time(end, True)}"


def _times_of(text: str) -> str | None:
    """'10:00 AM – 1:00 PM, 2:00 – 6:00 PM' -> '10–1 & 2–6'; None when no span."""
    spans = []
    for m in _TIME_SPAN.finditer(text):
        se = _span_minutes(m.group(1), m.group(2))
        if se:
            spans.append(_fmt_span(*se))
    if not spans:
        return None
    return " & ".join(dict.fromkeys(spans))


def _runs(days: set[int]) -> list[list[int]]:
    """Consecutive runs over the cyclic week (Sun–Mon is one run)."""
    if len(days) == 7:
        return [list(range(7))]
    start = next(d for d in range(7) if d not in days)
    runs, cur = [], []
    for k in range(1, 8):
        d = (start + k) % 7
        if d in days:
            cur.append(d)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return sorted(runs, key=lambda r: r[0])


def _days_label(days: set[int]) -> str:
    if len(days) == 7:
        return "Daily"
    parts = []
    for r in _runs(days):
        parts.append(DAY_ABBR[r[0]] if len(r) == 1 else f"{DAY_ABBR[r[0]]}–{DAY_ABBR[r[-1]]}")
    return ", ".join(parts)


def group_hours(lines) -> list[str]:
    """Any hours lines -> [one grouped line] + unreadable note lines.
    Returns [] for no hours; never raises."""
    raw = [str(x) for x in ([lines] if isinstance(lines, str) else (lines or [])) if str(x).strip()]
    try:
        return _drop_closed(_group(raw))
    except Exception:  # noqa: BLE001 - display helper, keep the input
        return raw


# a closing note is kept only when it says something besides closed days
# ("Closed through end of August", "closed for installation between shows")
_KEEP_CLOSED = re.compile(r"through|until|till|temporar|between|reopen|installation|break|summer|winter|august|january", re.I)
_CLOSED_CLAUSE = re.compile(r"\s*(?:[/;,.]\s*|\band\b\s*)?\(?\bclosed\b(?::)?[^()/;]*\)?", re.I)


def _drop_closed(lines: list[str]) -> list[str]:
    """Closed days and holidays are never mentioned (Carlton, 2026-10-16)."""
    out = []
    for line in lines:
        if not _CLOSED.search(line) or _KEEP_CLOSED.search(line) or line.strip().lower() == "closed":
            out.append(line)
            continue
        rest = _CLOSED_CLAUSE.sub("", line).strip(" ,;/.(")
        if rest and re.search(r"\d|appoint", rest, re.I):
            out.append(rest)
    return out


def _group(raw: list[str]) -> list[str]:
    out = _group_hours(raw)
    # a reading that loses open days or doesn't hold its shape keeps the
    # venue's own wording (only tidied), rather than a wrong summary
    if (out != _group_hours(out)
            or parse_hours(out)["days_open"] != parse_hours(raw)["days_open"]):
        return [_tidy(x) for x in raw if not _closed_days_only(x)] or [_tidy(x) for x in raw]
    return out


def _closed_days_only(line: str) -> bool:
    """'Sun - Mon closed', 'Closed Mondays': a line naming only closed days."""
    line = _clean(line)
    return bool(_CLOSED.search(line) and _days_in(line) and not _TIME_SPAN.search(line)
                and not re.search(r"\b(through|until|till|from|reopen)", line, re.I))


def _tidy(line: str) -> str:
    """'Tue - Sat 10:00am to 6:00pm' -> 'Tue–Sat 10–6' without regrouping."""
    line = _clean(line)
    line = re.sub(r"\s+-\s+(?=(?:mon|tue|wed|thu|fri|sat|sun))", "–", line, flags=re.I)
    def one(m):
        se = _span_minutes(m.group(1), m.group(2))
        return _fmt_span(*se) + re.search(r"\s*$", m.group(0)).group(0) if se else m.group(0)
    return _TIME_SPAN.sub(one, line)


def _group_hours(lines) -> list[str]:
    if isinstance(lines, str):
        lines = re.split(r"[\n;]+", lines)
    per_day: dict[int, str] = {}       # day -> "11–6" | "closed" | "appt"
    appt_any = False                   # "or by appointment" with no days
    appt_only = False
    notes: list[str] = []
    segs: list[str] = []
    for raw in lines or []:
        line = _clean(str(raw))
        if line:
            segs.extend(s.strip() for s in split_segments(line))

    width: dict[int, int] = {}         # day -> how many days the setting line covered

    def put(days, val):
        # a narrower line overrides a wider one ("Tue–Sun 10–6" then "Fri
        # 10–8"); lines over the same days add up ("Tue–Sat 11–1", "Tue–Sat 2–7")
        n = len(days)
        for d in days:
            old = per_day.get(d)
            if old is None or n < width[d]:
                per_day[d], width[d] = val, n
            elif n == width[d] and old != val:
                if "closed" in (old, val) or "appt" in (old, val):
                    if old in ("closed", "appt"):
                        per_day[d] = val
                else:
                    per_day[d] = f"{old} & {val}"

    for seg in segs:
        g = _FULL_DAY.match(seg)
        if g:
            d = DAYS.index(g.group(1).lower()[:3])
            rest = g.group(2)
            if _CLOSED.search(rest):
                put([d], "closed")
            elif _24H.search(rest):
                put([d], "24 hours")
            else:
                t = _times_of(rest)
                put([d], t if t else rest)
            continue
        # "Monday - Thursday: 9 AM - 6 PM", "Tue: By Appointment"
        seg = re.sub(rf"\b({_DAY_RE})\s*:\s*", r"\1 ", seg, flags=re.I)
        if _SPECIAL.search(_PAREN.sub(" ", seg)):
            sp_days = _days_in(seg)
            if (sp_days and _CLOSED.search(seg) and not _TIME_SPAN.search(seg)
                    and re.search(r"holiday", seg, re.I) and not re.search(r"\b(first|last|every other)\b", seg, re.I)):
                # "Closed Sundays, Mondays and public holidays"
                put(sp_days, "closed")
            else:
                notes.append(_tidy(seg))
            continue
        paren = " ".join(f"({p})" for p in _PAREN.findall(seg))
        body = _PAREN.sub(" ", seg)
        days = list(range(7)) if _DAILY.search(body) else _days_in(body)
        has_appt = bool(_APPT.search(body))
        times = _times_of(body)
        if not days:
            if has_appt and not times:
                if re.search(r"\bonly\b", body, re.I) and not per_day:
                    appt_only = True
                elif re.search(r"\b(other|remaining|all other)\b", body, re.I):
                    appt_any = "other"
                else:
                    appt_any = appt_any or True
            else:
                notes.append(_tidy(seg))
            continue
        if times:
            put(days, times + (f" {paren}" if paren else ""))
            if has_appt:
                appt_any = appt_any or True
        elif _CLOSED.search(body):
            if re.search(r"\b(through|until|till|for|from|reopen)", body, re.I):
                notes.append(_tidy(seg))
            else:
                put(days, "closed")
        elif has_appt:
            put(days, "appt")
        elif _24H.search(body):
            put(days, "24 hours")
        else:
            notes.append(_tidy(seg))

    open_days = {d for d, v in per_day.items() if v not in ("closed", "appt")}
    if not per_day:
        if any(_TIME_SPAN.search(n) for n in notes):
            return [_tidy(x) for x in segs]
        if appt_only or appt_any:
            return ["By appointment only"] + notes
        return notes
    # days nobody mentioned: closed, or by appointment when the venue says so
    note_names_days = any(_days_in(n) for n in notes)
    if open_days and not note_names_days:
        fill = "appt" if appt_any == "other" else "closed"
        for d in range(7):
            per_day.setdefault(d, fill)
    groups: dict[str, set[int]] = {}
    for d, v in per_day.items():
        groups.setdefault(v, set()).add(d)
    order = lambda item: (item[0] in ("appt", "closed"), item[0] == "closed",
                          _runs(item[1])[0][0] if item[1] else 0)
    parts = []
    for val, days in sorted(groups.items(), key=order):
        label = _days_label(days)
        if val == "closed":
            # closed days aren't mentioned (Carlton, 2026-10-16): only open
            # days and times show; a venue closed every day still says so
            if len(groups) == 1:
                parts.append("Closed")
        elif val == "appt":
            parts.append(f"{label} by appointment" if len(days) < 7 else "By appointment only")
        else:
            parts.append(f"{label} {val}")
    if appt_any is True and open_days:
        # "or by appointment" rides on the last open-hours segment
        k = max(i for i, p in enumerate(parts) if not p.lower().startswith("closed") and "appointment" not in p)
        parts[k] += " or by appointment"
    line = ", ".join(parts)
    return [line[0].upper() + line[1:]] + notes
