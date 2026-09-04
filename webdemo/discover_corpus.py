"""Per-city corpus for the Discover function (webdemo/api/discover.js).

build.py calls ``build(city, shows, ranking, images)`` once per city and writes
the result to dist/api/_data/<city>.json. Everything fragile is parsed here,
in Python, at build time: opening hours become a per-weekday table (Google's
weekday_text first, the agent-written lines second, via scraper/hours.py),
reception lines become an ISO date plus a kind (opening / talk / closing /
event), and the artists dataset and gallery reports are trimmed to what the
model can use. The JS side only indexes and filters.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import cityconfig
from cityconfig import CONTENT_DIR, ROOT

sys.path.insert(0, str(ROOT / "scraper"))
import hours as hours_mod  # noqa: E402

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# mirrors GALLERY_TIER_CUTOFF in app.js
GALLERY_TIER_CUTOFF = {"top": 25, "notable": 100}

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
# same rule as app.js receptionDate: first "Month day[, year]"
RECEPTION_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
    re.I)
KIND_RES = [
    ("closing", re.compile(r"\bclosing\b", re.I)),
    ("talk", re.compile(r"\b(talk|conversation|panel|lecture|discussion|q&a)\b", re.I)),
    ("event", re.compile(r"\b(screening|performance|walkthrough|walk-through|tour|brunch|party|workshop)\b", re.I)),
]
OPENING_RE = re.compile(r"\bopening\b", re.I)
DAILY_RE = re.compile(r"\b(daily|every day|7 days|open every)\b", re.I)
AP_RE = re.compile(r"am|pm|a\.m|p\.m", re.I)


def gallery_tier(rank) -> str:
    if rank is None:
        return "listed"
    return "top" if rank <= GALLERY_TIER_CUTOFF["top"] else "notable" if rank <= GALLERY_TIER_CUTOFF["notable"] else "listed"


def parse_iso(s: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(s) if s else None
    except ValueError:
        return None


def reception_info(show: dict) -> tuple[str | None, str | None]:
    """(iso date, kind) from the free-text reception line; (None, None) when
    there is none. kind: opening | talk | closing | event."""
    text = show.get("reception")
    if not text:
        return None, None
    kind = "opening"
    if not OPENING_RE.search(text):
        for k, rx in KIND_RES:
            if rx.search(text):
                kind = k
                break
    elif re.search(r"\bclosing\b", text, re.I) and not re.search(r"\bopening reception\b", text, re.I):
        kind = "closing"
    m = RECEPTION_RE.search(text)
    if not m:
        return None, kind
    month = next((i for i, name in enumerate(MONTHS) if name.startswith(m.group(1)[:3].lower())), -1)
    day = int(m.group(2))
    start = parse_iso(show.get("start_date"))
    year = int(m.group(3)) if m.group(3) else (start.year if start else None)
    if month < 0 or year is None:
        return None, kind
    try:
        d = dt.date(year, month + 1, day)
    except ValueError:
        return None, kind
    if not m.group(3) and start and (start - d).days > 60:
        try:
            d = dt.date(year + 1, month + 1, day)
        except ValueError:
            return None, kind
    return d.isoformat(), kind


def _hours_table(lines) -> dict | None:
    """{byDay: {mon: [openMin, closeMin] | None}, byAppointment} or None when no
    line parsed. Per-day lines ("Tuesday: 11:00 AM – 6:00 PM", "Monday: Closed")
    and range lines ("Tue - Sat 10am to 6pm") both work; a day never named is
    closed once anything parsed."""
    per: dict[int, list | None] = {}
    parsed = False
    appt = False
    for raw in lines or []:
        line = hours_mod._clean(str(raw))
        if not line:
            continue
        if hours_mod._APPT.search(line):
            appt = True
        days = list(range(7)) if DAILY_RE.search(line) else hours_mod._days_in(line)
        if not days:
            continue
        span = hours_mod._TIME_SPAN.search(line)
        if not span:
            if hours_mod._CLOSED.search(line) or hours_mod._APPT.search(line):
                for d in days:
                    per.setdefault(d, None)
                parsed = True
            continue
        a, b = span.group(1), span.group(2)
        start = hours_mod._to_minutes(a)
        end = hours_mod._to_minutes(b, default_pm=not AP_RE.search(b))
        if start is None or end is None:
            continue
        # "1 - 5pm": a bare opening hour before 8 with a pm closing is afternoon
        if not AP_RE.search(a) and re.search(r"pm|p\.m", b, re.I) and start < 8 * 60:
            start += 12 * 60
        if end <= start and not AP_RE.search(a) and start >= 12 * 60:
            start -= 12 * 60
        if end <= start:
            continue
        parsed = True
        for d in days:
            cur = per.get(d)
            per[d] = [start, end] if not cur else [min(cur[0], start), max(cur[1], end)]
    if not parsed:
        return None
    return {"byDay": {DAYS[i]: per.get(i) for i in range(7)}, "byAppointment": appt}


def hours_by_day(v: dict) -> dict:
    google = (v.get("google") or {}).get("hours") or []
    own = v.get("hours") or []
    for lines, src in ((google, "google"), (own, "site")):
        t = _hours_table(lines)
        if t:
            t["source"] = src
            return t
    return {"byDay": {d: None for d in DAYS}, "byAppointment": bool(hours_mod._APPT.search(" ".join(own))), "source": None}


def split_artists(text: str | None) -> list[str]:
    if not text:
        return []
    t = re.sub(r"\([^)]*\)", " ", text)
    parts = re.split(r",|;|\band\b|&|\bwith\b", t)
    out = []
    for p in parts:
        p = re.sub(r"\s+", " ", p).strip(" .")
        if len(p) >= 2 and p.lower() not in ("others", "more", "et al"):
            out.append(p)
    return out


def load_registry(city_key: str) -> dict[str, dict]:
    f = CONTENT_DIR / "venues" / f"{city_key}.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text())
    return {v["id"]: v for v in data.get("venues", []) if v.get("id")}


def load_judge(city_key: str) -> dict[str, dict]:
    """slug -> {score, rationale} from the latest judge verdict per slug."""
    f = CONTENT_DIR / "curation" / city_key / "judge.jsonl"
    out: dict[str, dict] = {}
    if not f.exists():
        return out
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        slug = r.get("slug")
        if not slug or r.get("overall") is None:
            continue
        if slug not in out or (r.get("ts") or 0) >= out[slug]["_ts"]:
            out[slug] = {"score": r.get("overall"), "rationale": (r.get("rationale") or "").strip() or None, "_ts": r.get("ts") or 0}
    for v in out.values():
        v.pop("_ts", None)
    return out


def load_reports(city_key: str) -> dict[str, dict]:
    """venue_id -> {roster: [names], programFocus: [...]} from GalleryReports."""
    out: dict[str, dict] = {}
    d = CONTENT_DIR / "venues" / "reports" / city_key
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.json")):
        try:
            r = json.loads(f.read_text())
        except ValueError:
            continue
        vid = r.get("venue_id") or f.stem
        roster = r.get("roster") or []
        names = [x.get("name") for x in roster if isinstance(x, dict) and x.get("status") == "represented" and x.get("name")]
        names += [x.get("name") for x in roster if isinstance(x, dict) and x.get("status") != "represented" and x.get("name")]
        out[vid] = {"roster": names[:40], "programFocus": [p for p in (r.get("program_focus") or []) if isinstance(p, str)][:8]}
    return out


def load_artists(city_key: str) -> list[dict]:
    f = CONTENT_DIR / "artists" / f"{city_key}.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text())
    out = []
    for a in data.get("artists", []):
        if not a.get("name"):
            continue
        out.append({
            "name": a["name"],
            "aliases": [x for x in (a.get("aliases") or []) if isinstance(x, str)][:6],
            "galleries": [{"venueId": g.get("venue_id"), "relation": g.get("relation"), "since": g.get("since")}
                          for g in (a.get("galleries") or []) if g.get("venue_id")][:12],
            "showsCurrent": [s.get("slug") for s in (a.get("shows_current") or []) if s.get("slug")],
            "showsHistory": [{"venueId": s.get("venue_id"), "title": s.get("title"), "year": s.get("year")}
                             for s in (a.get("shows_history") or [])][:10],
        })
    return out


def build(city: dict, shows: list[dict], ranking: dict[str, dict], images: dict[str, str]) -> dict:
    """The corpus for one city. `shows` are the raw published records that made
    it into data.js (build.py skips image-less shows), `images` maps slug ->
    the first proxy image path."""
    key = city["key"]
    registry = load_registry(key)
    judge = load_judge(key)
    reports = load_reports(key)

    out_shows = []
    used: dict[str, dict] = {}   # venueId -> embedded venue (fallback when the registry lacks it)
    for s in shows:
        v = s["venue"]
        vid = s.get("venue_id") or "v-" + re.sub(r"[^a-z0-9]+", "-", v["name"].lower()).strip("-")
        used.setdefault(vid, v)
        rv = registry.get(vid) or {}
        rec_date, rec_kind = reception_info(s)
        rk = ranking.get(s["slug"]) or {}
        j = judge.get(s["slug"]) or {}
        kind = rv.get("kind") or ("museum" if v.get("is_museum") else "gallery")
        out_shows.append({
            "id": s["slug"], "title": s["title"], "artist": s.get("artist"),
            "artists": split_artists(s.get("artist")),
            "venueId": vid, "venueName": v["name"], "venueKind": kind,
            "neighborhood": v.get("neighborhood"),
            "startDate": s.get("start_date"), "endDate": s.get("end_date"),
            "datesNote": s.get("dates_note"), "datesConfidence": s.get("dates_confidence"),
            "receptionText": s.get("reception"), "receptionDate": rec_date, "receptionKind": rec_kind,
            "featured": bool(s.get("featured")), "editorsPick": bool(s.get("editors_pick")),
            "rank": rk.get("rank"), "galleryTier": gallery_tier(rv.get("rank")),
            "description": s.get("description") or "",
            "sourceUrls": s.get("source_urls") or [],
            "image": images.get(s["slug"]),
            "judgeScore": j.get("score"), "judgeRationale": j.get("rationale"),
        })

    venues: dict[str, dict] = {}
    ids = set(used) | {vid for vid, rv in registry.items() if rv.get("status") == "active"}
    for vid in sorted(ids):
        rv = registry.get(vid) or {}
        ev = used.get(vid) or {}
        lat = rv.get("latitude", ev.get("latitude"))
        lng = rv.get("longitude", ev.get("longitude"))
        if lat is None or lng is None:
            continue
        kind = rv.get("kind") or ("museum" if (ev.get("is_museum") or rv.get("is_museum")) else "gallery")
        rep = reports.get(vid) or {}
        hours_src = rv if (rv.get("hours") or (rv.get("google") or {}).get("hours")) else {"hours": ev.get("hours")}
        venues[vid] = {
            "id": vid, "name": rv.get("name") or ev.get("name"),
            "aliases": [a for a in (rv.get("aliases") or []) if isinstance(a, str)],
            "kind": kind, "isMuseum": kind == "museum",
            "neighborhood": rv.get("neighborhood") or ev.get("neighborhood"),
            "address": rv.get("address") or ev.get("address"),
            "addressDetail": rv.get("address_detail") or ev.get("address_detail"),
            "website": rv.get("website") or ev.get("website"),
            "phone": rv.get("phone") or ev.get("phone"),
            "lat": lat, "lng": lng,
            "rank": rv.get("rank"), "tier": gallery_tier(rv.get("rank")),
            "about": cityconfig.published_about(rv) if rv else None,
            "hoursText": rv.get("hours") or ev.get("hours") or [],
            "hours": hours_by_day(hours_src),
            "roster": rep.get("roster") or [],
            "programFocus": rep.get("programFocus") or [],
            "hasShows": vid in used,
        }

    return {
        "city": key, "displayName": city["displayName"],
        "tz": cityconfig.CITY_TZ.get(key, "UTC"),
        "neighborhoods": list(city["neighborhoods"]),
        "generatedAt": dt.date.today().isoformat(),
        "shows": out_shows, "venues": venues, "artists": load_artists(key),
    }


def dumps(corpus: dict) -> str:
    return json.dumps(corpus, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
