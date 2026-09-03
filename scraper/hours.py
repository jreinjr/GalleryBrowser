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
        a, b = m.group(1), m.group(2)
        # "11:00 AM – 6:00 PM": explicit; "10am to 6": borrow the pm from context
        b_has_ap = bool(re.search(r"am|pm|a\.m|p\.m", b, re.I))
        a_has_ap = bool(re.search(r"am|pm|a\.m|p\.m", a, re.I))
        start = _to_minutes(a)
        end = _to_minutes(b, default_pm=not b_has_ap)
        if start is None or end is None:
            continue
        if not a_has_ap and b_has_ap and start > end:
            # "10 - 6pm" -> 10am
            pass
        if end <= start:
            if not a_has_ap and start >= 12 * 60:
                start -= 12 * 60
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
    for raw in lines or []:
        line = _clean(str(raw))
        if not line:
            continue
        if _APPT.search(line):
            by_appt = True
            parsed = True
            # "Sat 12-5 or by appointment" still carries public hours
            if not _TIME_SPAN.search(line):
                continue
        days = _days_in(line)
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
