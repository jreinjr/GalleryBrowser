"""Venue registry: venues as first-class, durable records — independent of
any single show — so refreshes can be scheduled per venue, cheap page
change-detection can decide who needs an LLM session, and curation can rank
venues by notability over time.

Layout
    content/venues/<city>.json      {"schema": 1, "city", "updated", "venues": [...]}
    content/.venues-<city>.lock     flock for read-modify-write from parallel workers
    scraper/.cache/evidence/        raw fetched text (gitignored), see write_evidence()

The existing JSONL ledgers (venue_directory-<city>.jsonl, skips.jsonl,
verify_results.jsonl) stay as append-only audit trails; the registry is the
mutable *current state* they roll up into. Show records keep their embedded
`venue` object untouched (the apps decode it); only a top-level `venue_id`
is added to new saves.

Hooks (called lazily from tools.py so the two modules can import each other):
    on_save_show, on_log_skip, on_record_venue, on_confirm_show,
    on_sweep_demoted, on_crosscheck, on_fetch

A venue holds any number of concurrent shows (`last_known_shows`); scheduling
keys off the EARLIEST-ending live show so a venue is re-checked whenever one
of its shows turns over. `sources.seed.{places|gpla|carla}` records
deterministic seeding (seed_venues.py); `sources.candidate` records a venue
auto-discovered from a fetched domain (status "candidate" until a session
confirms it).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import tools

SCHEMA = 1
SCRAPER_DIR = Path(__file__).resolve().parent
VENUES_DIR = tools.CONTENT_DIR / "venues"
EVIDENCE_DIR = SCRAPER_DIR / ".cache" / "evidence"   # evidence paths are relative to SCRAPER_DIR
EVIDENCE_INDEX = EVIDENCE_DIR / "index.jsonl"
EVIDENCE_MAX_BYTES = 200_000
EVIDENCE_KEEP_PER_URL = 2
EVIDENCE_PRUNE_DAYS = 45

KINDS = ["gallery", "museum", "nonprofit", "project_space", "university", "other"]
STATUSES = ["active", "closed", "appointment_only", "out_of_scope", "duplicate", "unknown",
            "candidate"]   # candidate: domain seen in a session, not yet confirmed a venue
URL_SOURCES = ["harness", "migration", "discovered", "agent", "manual"]
CADENCE_BY_TIER = {1: 7, 2: 14, 3: 21}
DEFAULT_CADENCE = 21
MUSEUM_CADENCE = 14

# Shared hosting / social platforms: a URL on one of these never identifies
# a venue, so domain matching ignores them.
SHARED_PLATFORMS = (
    "squarespace.com", "wixsite.com", "wix.com", "instagram.com", "facebook.com",
    "artsy.net", "linktr.ee", "google.com", "cargo.site", "weebly.com",
    "wordpress.com", "tumblr.com", "bigcartel.com", "shopify.com", "youtube.com",
    "x.com", "twitter.com", "eventbrite.com", "artforum.com", "artnet.com",
    "hyperallergic.com", "latimes.com", "contemporaryartreview.la",
    "gallery-platform.la", "ocula.com", "artsy.com",
)

LISTING_RE = re.compile(
    r"/(exhibitions?|exhibits?|on-view|on_view|current|whats-on|what-s-on|now-showing|shows|"
    r"programs?|programming|current-exhibitions?|current-exhibits?|upcoming-exhibitions?|"
    r"exhibitions-and-events|exhibitions-events)"
    r"(/(current|on-view|now|upcoming|past))?/?$", re.I)
LISTING_WORDS = re.compile(r"(exhibit|on-view|on_view|current|whats-on|show|program)", re.I)

CLOSED_RE = re.compile(
    r"permanently closed|no longer (an active|operating|open)|ceased operations|"
    r"closed (its|the|their) (space|gallery|doors)|has closed|shut(ting)? down|"
    r"closed permanently", re.I)
MONTHS = ("january|february|march|april|may|june|july|august|september|october|"
          "november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec")
REOPEN_RE = re.compile(
    r"(open|opens|opening|reopen|reopens|reopening|next show|next exhibition|"
    r"upcoming)[^.;\n]{0,40}?\b(" + MONTHS + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:,?\s+(\d{4}))?", re.I)


# --- identity ------------------------------------------------------------------

def _slug(text: str) -> str:
    s = unicodedata.normalize("NFKD", text)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "venue"


def venue_id(name: str) -> str:
    """Stable id from the normalized venue name (shared with the one-show-per-
    venue key), e.g. 'Hammer Museum' -> 'hammer-museum'."""
    return _slug(tools._norm_venue(name))


def registrable_domain(url: str | None) -> str | None:
    """Host minus 'www.'; None for shared platforms that can't identify a venue."""
    if not url or not isinstance(url, str):
        return None
    if "://" not in url:
        url = "https://" + url
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    for plat in SHARED_PLATFORMS:
        if host == plat or host.endswith("." + plat):
            return None
    return host


# --- registry I/O --------------------------------------------------------------

def _registry_file(city: str) -> Path:
    return VENUES_DIR / f"{city}.json"


def _lock_file(city: str) -> Path:
    return tools.CONTENT_DIR / f".venues-{city}.lock"


def empty_venue(vid: str, name: str) -> dict:
    return {
        "id": vid, "name": name, "aliases": [], "kind": "gallery", "is_museum": False,
        "status": "unknown", "tier": None, "notability": None,
        "neighborhood": None, "address": None, "address_detail": None,
        "latitude": None, "longitude": None, "coords_source": None,
        "website": None, "exhibitions_url": None, "exhibitions_url_source": None,
        "exhibitions_url_candidates": [], "hours": [], "phone": None,
        "google": None,
        "last_scraped": None, "last_outcome": None, "last_checked": None,
        "next_check": None, "check_cadence_days": None,
        "last_known_shows": [], "last_skip": None,
        "page": {"fetched_ts": None, "http_status": None, "etag": None,
                 "last_modified": None, "text_hash": None, "date_sig": None,
                 "date_strings": [], "text_chars": 0, "fetch_mode": "static",
                 "changed_ts": None, "evidence_path": None},
        "scrape_history": [], "sources": {"directory_ts": None, "directory_session": None,
                                          "shows": [], "crosscheck_ts": None},
        "notes": None,
    }


def registry_exists(city: str) -> bool:
    return _registry_file(city).exists()


def load_registry(city: str) -> dict:
    p = _registry_file(city)
    if p.exists():
        try:
            reg = json.loads(p.read_text())
            if isinstance(reg, dict) and isinstance(reg.get("venues"), list):
                return reg
        except json.JSONDecodeError:
            pass
    return {"schema": SCHEMA, "city": city, "updated": None, "venues": []}


def save_registry(city: str, reg: dict) -> None:
    """Atomic write; callers that mutate should hold locked_registry()."""
    VENUES_DIR.mkdir(parents=True, exist_ok=True)
    reg["schema"] = SCHEMA
    reg["city"] = city
    reg["updated"] = int(time.time())
    reg["venues"] = sorted(reg["venues"], key=lambda v: v["id"])
    p = _registry_file(city)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=1, ensure_ascii=False))
    os.replace(tmp, p)


@contextmanager
def locked_registry(city: str):
    """Exclusive flock around a read-modify-write of the city registry."""
    tools.CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    with open(_lock_file(city), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reg = load_registry(city)
        yield reg
        save_registry(city, reg)


def index_by_id(reg: dict) -> dict[str, dict]:
    return {v["id"]: v for v in reg.get("venues", [])}


def find_venue(reg: dict, name: str | None, website: str | None = None,
               add_alias: bool = True) -> dict | None:
    """id -> alias -> domain lookup. A hit through an alias/domain records the
    new spelling so future lookups are direct."""
    venues = reg.get("venues", [])
    vid = venue_id(name) if name else None
    if vid:
        for v in venues:
            if v["id"] == vid:
                return v
        for v in venues:
            if vid in {venue_id(a) for a in v.get("aliases", [])}:
                return v
    dom = registrable_domain(website)
    if dom:
        for v in venues:
            if registrable_domain(v.get("website")) == dom:
                if name and add_alias and name != v["name"] and name not in v["aliases"]:
                    v["aliases"].append(name)
                return v
    return None


_LIST_UNION = ("aliases", "exhibitions_url_candidates")
_LIST_APPEND = ("scrape_history",)
_PROTECTED = ("id",)


def merge_patch(v: dict, patch: dict) -> dict:
    """Apply a patch: None never overwrites a value; list fields union/append;
    `last_known_shows` upserts by slug; nested `page`/`sources` merge shallowly."""
    for k, val in patch.items():
        if k in _PROTECTED or val is None:
            continue
        if k in _LIST_UNION:
            cur = v.setdefault(k, [])
            for item in val:
                if item and item not in cur:
                    cur.append(item)
            if k == "exhibitions_url_candidates":
                del cur[5:]
        elif k in _LIST_APPEND:
            v.setdefault(k, []).extend(val)
            del v[k][:-40]  # keep the last 40 sessions
        elif k == "last_known_shows":
            cur = {s["slug"]: s for s in v.setdefault(k, [])}
            for s in val:
                cur[s["slug"]] = {**cur.get(s["slug"], {}), **s}
            v[k] = sorted(cur.values(), key=lambda s: s.get("start") or "")
        elif k in ("page", "sources", "google") and isinstance(val, dict):
            base = v.get(k) or {}
            base.update({kk: vv for kk, vv in val.items() if vv is not None or kk in ("etag", "last_modified")})
            v[k] = base
        else:
            v[k] = val
    return v


def _upsert_in(reg: dict, name: str, patch: dict, source: str,
               website: str | None = None) -> tuple[dict, bool]:
    """Find (id/alias/domain) or create inside an already-loaded registry,
    merge the patch, record the source. Returns (venue, created)."""
    v = find_venue(reg, name, website or patch.get("website"))
    created = v is None
    if created:
        v = empty_venue(venue_id(name), name)
        reg["venues"].append(v)
    elif name != v["name"] and name not in v["aliases"]:
        v["aliases"].append(name)
    merge_patch(v, patch)
    tb = v.setdefault("sources", {}).setdefault("touched_by", [])
    if source not in tb:
        tb.append(source)
    return v, created


def upsert(city: str, name: str, patch: dict, source: str,
           website: str | None = None) -> dict:
    """Locked read-modify-write: find (id/alias/domain) or create, then merge."""
    with locked_registry(city) as reg:
        v, _ = _upsert_in(reg, name, patch, source, website)
        return json.loads(json.dumps(v))


def bulk_upsert(city: str, rows: list[dict]) -> dict:
    """Upsert many venues under ONE lock. rows: [{"name", "patch", "source",
    "website"?}]. Returns {"new": [ids], "updated": [ids]} in row order."""
    out: dict[str, list[str]] = {"new": [], "updated": []}
    with locked_registry(city) as reg:
        for r in rows:
            v, created = _upsert_in(reg, r["name"], r.get("patch") or {},
                                    r.get("source", "seed"), r.get("website"))
            out["new" if created else "updated"].append(v["id"])
    return out


def resolve_id(city: str, name: str, website: str | None = None) -> str:
    """Canonical venue id for a name (alias/domain aware; no lock, read-only)."""
    v = find_venue(load_registry(city), name, website, add_alias=False)
    return v["id"] if v else venue_id(name)


def by_zone(reg: dict, zone: str) -> list[dict]:
    return [v for v in reg.get("venues", []) if v.get("neighborhood") == zone]


def directory_view(city: str) -> dict[str, dict]:
    """Registry venues in the shape tools.load_directory() has always returned
    ({norm_name: {name, neighborhood, kind, address, website, note, ...}})."""
    out: dict[str, dict] = {}
    for v in load_registry(city).get("venues", []):
        out[tools._norm_venue(v["name"])] = {
            "ts": v.get("sources", {}).get("directory_ts") or v.get("last_scraped") or 0,
            "session": v.get("sources", {}).get("directory_session"),
            "city": city, "name": v["name"], "neighborhood": v.get("neighborhood"),
            "kind": v.get("kind"), "address": v.get("address"),
            "website": v.get("website"), "note": v.get("notes"),
            "venue_id": v["id"], "status": v.get("status"),
            "exhibitions_url": v.get("exhibitions_url"), "tier": v.get("tier"),
            "next_check": v.get("next_check"), "last_scraped": v.get("last_scraped"),
            "seeded": bool((v.get("sources") or {}).get("seed")),
            "candidate": v.get("status") == "candidate",
        }
    return out


# --- scheduling ----------------------------------------------------------------

def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _parse_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def cadence_days(v: dict) -> int:
    if v.get("check_cadence_days"):
        return int(v["check_cadence_days"])
    tier = v.get("tier")
    if tier in CADENCE_BY_TIER:
        return CADENCE_BY_TIER[tier]
    return MUSEUM_CADENCE if v.get("is_museum") else DEFAULT_CADENCE


def active_shows(v: dict, today: date | None = None) -> list[dict]:
    """Every saved show at this venue that has not ended, earliest end first."""
    today = today or date.today()
    live = [s for s in v.get("last_known_shows", [])
            if (_parse_date(s.get("end")) or today) >= today]
    return sorted(live, key=lambda s: s.get("end") or "9999")


def active_show(v: dict, today: date | None = None) -> dict | None:
    """The venue's EARLIEST-ending live show — the one whose turnover should
    trigger the next check. None when nothing is on view."""
    live = active_shows(v, today)
    return live[0] if live else None


def compute_next_check(v: dict, today: date | None = None) -> date | None:
    """When this venue should next be looked at (None = never)."""
    today = today or date.today()
    status = v.get("status") or "unknown"
    scraped = v.get("last_scraped")
    base = date.fromtimestamp(scraped) if scraped else today
    if status == "out_of_scope":
        return None
    if status == "closed":
        return base + timedelta(days=90)
    if status == "appointment_only":
        return base + timedelta(days=120)
    show = active_show(v, today)
    if show:
        end = _parse_date(show.get("end"))
        by_cadence = base + timedelta(days=cadence_days(v))
        return min(end - timedelta(days=2), by_cadence) if end else by_cadence
    if not scraped:
        return today
    return base + timedelta(days=cadence_days(v))


def parse_reopen_date(detail: str | None, today: date | None = None) -> date | None:
    """'next show opens Sept 12' -> 2026-09-12 (year rolls forward if past)."""
    if not detail:
        return None
    today = today or date.today()
    m = REOPEN_RE.search(detail)
    if not m:
        return None
    mon_txt, day_txt, year_txt = m.group(2).lower(), m.group(3), m.group(4)
    try:
        mon = datetime.strptime(mon_txt[:3], "%b").month
        day = int(day_txt)
        year = int(year_txt) if year_txt else today.year
        d = date(year, mon, day)
    except ValueError:
        return None
    if not year_txt and d < today - timedelta(days=14):
        d = date(year + 1, mon, day)
    return d


def apply_skip(v: dict, skip: dict, today: date | None = None) -> None:
    """Fold a log_skip entry into status/next_check per the scheduling rules."""
    today = today or date.today()
    reason, detail = skip.get("reason"), skip.get("detail") or ""
    v["last_skip"] = {k: skip.get(k) for k in ("ts", "reason", "detail", "url", "session")}
    v["last_outcome"] = f"skipped:{reason}"
    if reason == "closed_or_between_shows":
        if CLOSED_RE.search(detail):
            v["status"] = "closed"
            v["next_check"] = _iso(today + timedelta(days=90))
        else:
            v["status"] = "active"
            reopen = parse_reopen_date(detail, today)
            v["next_check"] = _iso(reopen - timedelta(days=3)) if reopen \
                else _iso(today + timedelta(days=21))
    elif reason == "appointment_only":
        v["status"] = "appointment_only"
        v["next_check"] = _iso(today + timedelta(days=120))
    elif reason == "out_of_scope":
        v["status"] = "out_of_scope"
        v["next_check"] = None
    elif reason == "unverifiable":
        v["status"] = v.get("status") if v.get("status") not in (None, "unknown") else "unknown"
        v["next_check"] = _iso(today + timedelta(days=30))
        if re.search(r"javascript|js[- ]rendered|rendered|dynamic", detail, re.I):
            v.setdefault("page", {})["fetch_mode"] = "js"
    elif reason in ("no_image", "low_res_only"):
        v["status"] = "active"
        v["next_check"] = _iso(today + timedelta(days=30))
    elif reason == "duplicate":
        # Another registry entry owns this space; never schedule it on its own.
        v["status"] = "duplicate"
        v["next_check"] = None
    elif reason == "unchanged":
        show = active_show(v, today)
        end = _parse_date(show.get("end")) if show else None
        v["status"] = "active"
        v["next_check"] = _iso(end - timedelta(days=2)) if end else _iso(today + timedelta(days=14))
    else:  # other
        v["next_check"] = _iso(today + timedelta(days=14))
    if skip.get("url") and registrable_domain(skip["url"]) and \
            registrable_domain(skip["url"]) == registrable_domain(v.get("website")):
        merge_patch(v, {"exhibitions_url_candidates": [skip["url"]]})


PLAUSIBLE_VENUE_RE = re.compile(
    r"\b(galler(y|ies|ia|ie)|galerie|museum|museo|projects?|project space|art ?space|"
    r"arts? cent(er|re)|contemporary|kunst|foundation|institute|collection|artspace|"
    r"studio gallery|art gallery|fine art|art center|nonprofit|non-profit)\b", re.I)


def seed_only_places(v: dict) -> bool:
    """True when Google Places is the venue's ONLY provenance — no curated
    list, enumeration session, saved show or candidate hit backs it."""
    src = v.get("sources") or {}
    seed = src.get("seed") or {}
    return bool(seed.get("places")) and not (
        seed.get("gpla") or seed.get("carla") or src.get("directory_session")
        or src.get("shows") or src.get("candidate"))


def places_plausible(v: dict) -> bool:
    """A Places-only venue worth a scrape session: venue-like name, not on the
    deny list, and a website of its own."""
    name = v.get("name") or ""
    return bool(PLAUSIBLE_VENUE_RE.search(name) and not NOT_VENUE_RE.search(name)
                and v.get("website") and registrable_domain(v.get("website")))


def triage_places_only(city: str) -> dict:
    """One-off after a Places seed: implausible Places-only venues are parked
    (next_check None, never scheduled) until another source vouches for them."""
    n = {"parked": 0, "kept": 0}
    with locked_registry(city) as reg:
        for v in reg["venues"]:
            if not seed_only_places(v):
                continue
            if places_plausible(v):
                n["kept"] += 1
            else:
                v["next_check"] = None
                v["sources"]["seed"]["places"]["plausible"] = False
                n["parked"] += 1
    return n


def due_venues(city: str, zone: str | None = None, today: date | None = None,
               saved_keys: set[str] | None = None, include_places_only: bool = True) -> list[dict]:
    """Venues that need an LLM scrape session, highest priority first. Each
    returned record carries `_priority` and `_reasons`.

    A venue with live saved shows is still due when its next_check passes,
    its page changed, or one of its shows ends within a week — the session
    then saves the venue's OTHER current shows (multiple shows per venue).
    `saved_keys` is accepted for backward compatibility and ignored."""
    today = today or date.today()
    reg = load_registry(city)
    out = []
    for v in reg.get("venues", []):
        if zone and v.get("neighborhood") != zone:
            continue
        if v.get("status") not in ("active", "unknown", "candidate", None):
            continue
        scraped = v.get("last_scraped")
        nxt = _parse_date(v.get("next_check"))
        page = v.get("page") or {}
        changed = page.get("changed_ts") and (not scraped or page["changed_ts"] > scraped)
        past_due = nxt is not None and nxt <= today
        never = not scraped
        live = active_shows(v, today)
        ending_soon = any((_parse_date(x.get("end")) or today) <= today + timedelta(days=7)
                          for x in live)
        if not (never or nxt is None or past_due or changed or ending_soon):
            continue
        pr, reasons = 0, []
        places_only = seed_only_places(v)
        if places_only and not include_places_only:
            continue
        if never and places_only:
            # Google Places alone is a weak signal (framers, studios, decor
            # shops share its "art_gallery" type): scrape these last.
            if not places_plausible(v):
                continue
            pr += 10; reasons.append("places_only")
        elif never:
            pr += 40; reasons.append("never_scraped")
        if changed:
            if page.get("date_sig"):
                pr += 30; reasons.append("dates_changed")
            else:
                pr += 10; reasons.append("page_changed")
        if not live:
            pr += 25; reasons.append("no_active_show")
        if ending_soon:
            pr += 15; reasons.append("show_ending_soon")
        if past_due:
            pr += 15; reasons.append("past_next_check")
        if v.get("status") == "candidate":
            pr += 20; reasons.append("candidate")
        if v.get("curation_flag"):
            pr += 15; reasons.append("curation_flag")
        if v.get("tier") == 1:
            pr += 10
        elif v.get("tier") == 2:
            pr += 5
        rec = dict(v)
        rec["_priority"], rec["_reasons"] = pr, reasons
        out.append(rec)
    out.sort(key=lambda r: (-r["_priority"], r["id"]))
    return out


# --- URL attribution (learn each venue's exhibitions page for free) -------------

def _path_score(url: str) -> tuple[int, str | None]:
    """(score, parent_candidate). 3 listing page, 2 exhibition-ish shallow page,
    1 homepage, 0 detail page (its listing-like parent becomes a candidate)."""
    path = (urlparse(url).path or "/").rstrip("/") or "/"
    if LISTING_RE.search(path):
        return 3, None
    depth = path.count("/")
    if path != "/" and "exhibit" in path.lower() and depth == 1:
        return 2, None   # e.g. /upcoming-exhibition, /current-exhibit (top-level page)
    if path == "/":
        return 1, None
    parent = path.rsplit("/", 1)[0]
    if parent and LISTING_WORDS.search(parent):
        base = url.split(urlparse(url).path)[0] if urlparse(url).path else url
        return 0, base + parent + "/"
    return 0, None


def attribute_urls(venue: dict, urls: list, extra_domains: list[str] | None = None
                   ) -> tuple[str | None, list[str]]:
    """From the URLs a session touched for this venue, pick its exhibitions
    page. Only URLs on the venue's own domain count."""
    # The venue's own domain; fall back to the extra domains (a show's
    # source_urls) only when the registry has no website for it yet.
    doms = {d for d in [registrable_domain(venue.get("website"))] if d}
    if not doms:
        doms = {d for d in (registrable_domain(x) for x in (extra_domains or [])) if d}
    if not doms:
        return None, []
    scored: list[tuple[int, int, str]] = []
    for i, entry in enumerate(urls):
        url = entry[1] if isinstance(entry, (tuple, list)) else entry
        if registrable_domain(url) not in doms:
            continue
        score, parent = _path_score(url)
        scored.append((score, i, url.split("#")[0]))
        if parent:
            scored.append((2, i, parent))
    if not scored:
        return None, []
    scored.sort(key=lambda t: (-t[0], t[1]))
    cands: list[str] = []
    for _, _, u in scored:
        if u not in cands:
            cands.append(u)
    best = scored[0][2] if scored[0][0] >= 2 else None
    return best, cands[:3]


# --- session trace (what the harness observed this session) --------------------

class SessionTrace:
    """Per-session observations the harness feeds to the registry: URLs the
    agent fetched (attributed per venue between save/skip boundaries), cost and
    search/fetch marks for per-venue accounting, and image provenance."""

    def __init__(self, label: str, city: str):
        self.label, self.city = label, city
        self.urls: list[tuple[int, str, str]] = []
        self.queries: list[str] = []
        self.mark_idx = 0
        self.iteration = 0
        self.cost_now = self.mark_cost = 0.0
        self.searches_now = self.mark_searches = 0
        self.fetches_now = self.mark_fetches = 0
        self.image_sources: dict[str, dict] = {}
        self.zone: str | None = None          # single-zone sessions: candidate zone hint
        self.seen_domains: set[str] = set()   # domains already considered as candidates
        self.current_key: str | None = None   # venue key of the last save (multi-show runs)

    def add_url(self, url: str, kind: str) -> None:
        if isinstance(url, str) and url.startswith("http"):
            self.urls.append((self.iteration, url, kind))
            if kind == "web_fetch":
                self.fetches_now += 1

    def since_mark(self) -> list[tuple[int, str, str]]:
        return self.urls[self.mark_idx:]

    def advance(self) -> dict:
        """Close the current venue's window: return its deltas, move the marks."""
        deltas = {"cost_usd": round(max(0.0, self.cost_now - self.mark_cost), 4),
                  "searches": max(0, self.searches_now - self.mark_searches),
                  "fetches": max(0, self.fetches_now - self.mark_fetches)}
        self.mark_idx = len(self.urls)
        self.mark_cost, self.mark_searches = self.cost_now, self.searches_now
        self.mark_fetches = self.fetches_now
        return deltas


# --- evidence cache ------------------------------------------------------------

def write_evidence(city: str, url: str, kind: str, text: str, session: str | None,
                   venue_id_: str | None = None) -> str | None:
    """Persist fetched text (already paid for) so later passes can reuse it.
    Returns the file path (relative to the scraper dir) or None when empty."""
    if not text:
        return None
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text[:EVIDENCE_MAX_BYTES]
    d = EVIDENCE_DIR / city
    d.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8", "ignore")).hexdigest()[:16]
    ts = int(time.time())
    path = d / f"{key}-{ts}.txt"
    path.write_text(text, encoding="utf-8")
    older = sorted(d.glob(f"{key}-*.txt"))
    for old in older[:-EVIDENCE_KEEP_PER_URL]:
        try:
            old.unlink()
        except OSError:
            pass
    rel = str(path.relative_to(SCRAPER_DIR))
    tools._append_jsonl(EVIDENCE_INDEX, {
        "ts": ts, "session": session, "city": city, "url": url, "kind": kind,
        "bytes": len(text.encode("utf-8")), "path": rel, "venue_id": venue_id_})
    return rel


def prune_evidence(days: int = EVIDENCE_PRUNE_DAYS) -> int:
    """Delete evidence older than `days` unless a registry page points at it."""
    if not EVIDENCE_DIR.exists():
        return 0
    keep = set()
    if VENUES_DIR.exists():
        for f in VENUES_DIR.glob("*.json"):
            for v in load_registry(f.stem).get("venues", []):
                ep = (v.get("page") or {}).get("evidence_path")
                if ep:
                    keep.add(ep)
    cutoff = time.time() - days * 86400
    n = 0
    for f in EVIDENCE_DIR.glob("*/*.txt"):
        rel = str(f.relative_to(SCRAPER_DIR))
        if rel not in keep and f.stat().st_mtime < cutoff:
            f.unlink()
            n += 1
    return n


# --- auto-candidates (venues discovered from fetched domains) -------------------

# Domains that are never a venue's own site: shared platforms, publications,
# listings, city guides. Extended per city with content/curation/<city>/sources.json.
CANDIDATE_DENY = SHARED_PLATFORMS + (
    "yelp.com", "wikipedia.org", "wikimedia.org", "timeout.com", "tripadvisor.com",
    "laist.com", "lamag.com", "kcet.org", "kcrw.com", "downtownla.com", "wanderlog.com",
    "artrabbit.com", "art-collecting.com", "medium.com", "substack.com", "apple.com",
    "nytimes.com", "lovebeverlyhills.com", "losangelesartgallerytours.com",
    "melroseartsdistrict.com", "artsdistrictla.org", "galleryplatform.la",
    "artillerymag.com", "contemporaryartdaily.com", "diversionsla.com", "laweekly.com",
    "curbed.com", "secretlosangeles.com", "discoverlosangeles.com", "visitcalifornia.com",
    "culturela.org", "mutualart.com", "artfacts.net", "artland.com", "artsper.com",
    "artguide.artforum.com", "frieze.com", "artbasel.com", "felixfair.com",
    "untitledartfairs.com", "artdealers.org", "artnews.com", "artinamericamagazine.com",
    "cultured.com", "flaunt.com", "x-traonline.org", "newyorker.com", "wsj.com",
    "theguardian.com", "bloomberg.com", "forbes.com", "vogue.com", "wallpaper.com",
    "archive.org", "bing.com", "duckduckgo.com", "reddit.com", "pinterest.com",
    "vimeo.com", "soundcloud.com", "spotify.com", "amazon.com", "etsy.com",
    "squarespace-cdn.com", "cloudfront.net", "wixstatic.com", "thelalocal.org",
    "thegramercyla.com", "losangeles.com", "la.curbed.com", "hollywoodreporter.com",
)
VENUE_RE = re.compile(r"\b(exhibitions?|gallery|galerie|museum|art space|project space|"
                      r"artist-run|on view|opening reception|kunsthalle)\b", re.I)
ADDR_RE = re.compile(r"\b\d{2,5}\s+(?:[NSEW]\.?\s+)?[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}\s+"
                     r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Rd|Road|Dr|Drive|Way|Pl|Place|"
                     r"Ln|Lane|Hwy|Highway|Ct|Court|Pkwy|Parkway)\b\.?")
NOT_VENUE_RE = re.compile(r"\b(magazine|review|journal|podcast|art fair|festival|newspaper|"
                          r"press release distribution|auction house|framing|frame shop|"
                          r"art supplies|tattoo|nerd|guide|blog|things to do|best of|top \d+|"
                          r"art and culture|art scene|neighborhood|district council|chamber of|"
                          r"local|news|daily|weekly|times|tribune|observer|patch)\b", re.I)
GENERIC_NAME_RE = re.compile(r"^(home|welcome|exhibitions?|current|about|contact|news|"
                             r"gallery|galleries|art|events?)$", re.I)


def _candidate_deny(city: str) -> set[str]:
    deny = set(CANDIDATE_DENY)
    p = tools.CONTENT_DIR / "curation" / city / "sources.json"
    if p.exists():
        try:
            for src in json.loads(p.read_text()).get("sources", []):
                for u in (src.get("urls") or []):
                    d = registrable_domain(u)
                    if d:
                        deny.add(d)
                for d in (src.get("domains") or []):
                    deny.add(d.lower().removeprefix("www."))
        except (json.JSONDecodeError, OSError):
            pass
    return deny


def _denied_domain(dom: str, deny: set[str]) -> bool:
    return any(dom == d or dom.endswith("." + d) for d in deny)


def _page_name(url: str, text: str) -> str | None:
    """Venue name from evidence front-matter (web_fetch text) or HTML meta."""
    fm = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        fm = text[3:end] if end > 0 else text[3:3000]
    site = (re.search(r"^meta-og:site_name:\s*(.+)$", fm, re.M)
            or re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', text[:20000], re.I))
    title = (re.search(r"^title:\s*(.+)$", fm, re.M)
             or re.search(r"<title[^>]*>([^<]{2,200})</title>", text[:20000], re.I))
    raw = (site.group(1) if site else (title.group(1) if title else "")).strip()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return None
    dom_word = (registrable_domain(url) or "").split(".")[0].lower()
    parts = [x.strip(" &,") for x in re.split(r"\s+[|–—\-:]\s+", raw)]
    parts = [x for x in parts if len(x) >= 3 and not GENERIC_NAME_RE.match(x)]
    if not parts:
        return None
    for part in parts:   # prefer the segment that shares letters with the domain
        if dom_word and len(dom_word) >= 4 and dom_word in re.sub(r"[^a-z0-9]", "", part.lower()):
            return part[:80]
    # otherwise the shortest segment, unless it reads like an article title
    cand = min(parts, key=len)
    if len(cand.split()) > 6 or re.search(r"\b(in|at|of)\s+[A-Z]", cand) and len(cand.split()) > 4:
        return None
    return cand[:80]


def candidate_from_page(url: str, text: str, cfg: dict, deny: set[str] | None = None) -> dict | None:
    """Decide whether a fetched page looks like an art venue's OWN site in this
    city. Returns {"name", "website", "url", "domain"} or None. Pure function
    (no registry access) so it can be unit-tested and run over the evidence
    cache for backfills."""
    dom = registrable_domain(url)
    if not dom or not isinstance(text, str) or len(text) < 200:
        return None
    if _denied_domain(dom, deny if deny is not None else set(CANDIDATE_DENY)):
        return None
    head = text[:8000]
    if len(VENUE_RE.findall(head)) < 2:
        return None
    postal = cfg.get("postal_re")
    if not (ADDR_RE.search(head) or (postal and re.search(postal, head))):
        return None
    hints = [cfg.get("display_name", "")] + [part for z in cfg.get("neighborhoods", [])
                                              for part in z.split("/")]
    low = head.lower()
    if not any(h and h.lower() in low for h in hints):
        return None
    name = _page_name(url, text)
    if not name:
        return None
    if NOT_VENUE_RE.search(name) or NOT_VENUE_RE.search(dom):
        return None
    scheme = "https" if url.startswith("https") else "http"
    return {"name": name, "website": f"{scheme}://{dom}/", "url": url, "domain": dom}


def on_fetch(city: str, url: str, text: str, trace: SessionTrace | None, cfg: dict) -> str | None:
    """Harness hook for every fetched page: a domain not yet in the registry
    that looks like a venue site becomes a `candidate` venue (or attaches a
    website to a registry venue that had none). One decision per domain per
    session. Returns "attached:<id>", "candidate:<id>" or None."""
    dom = registrable_domain(url)
    if not dom:
        return None
    if trace is not None:
        if dom in trace.seen_domains:
            return None
        trace.seen_domains.add(dom)
    cand = candidate_from_page(url, text, cfg, _candidate_deny(city))
    if not cand:
        return None
    now = int(time.time())
    with locked_registry(city) as reg:
        v = find_venue(reg, cand["name"], url)
        if v is not None:
            changed = False
            if not v.get("website"):
                v["website"] = cand["website"]; changed = True
            if _path_score(url)[0] >= 2:
                merge_patch(v, {"exhibitions_url_candidates": [url.split("#")[0]]}); changed = True
            return f"attached:{v['id']}" if changed else None
        # a same-domain venue with a different name: attach as alias instead
        for other in reg["venues"]:
            if registrable_domain(other.get("website")) == dom:
                if cand["name"] not in other["aliases"] and cand["name"] != other["name"]:
                    other["aliases"].append(cand["name"])
                return None
        v = empty_venue(venue_id(cand["name"]), cand["name"])
        v.update({"status": "candidate", "kind": "other", "website": cand["website"],
                  "neighborhood": getattr(trace, "zone", None),
                  "next_check": date.today().isoformat(),
                  "notes": f"auto-candidate from {getattr(trace, 'label', None) or 'backfill'}"})
        merge_patch(v, {"exhibitions_url_candidates": [url.split("#")[0]]})
        v["sources"]["candidate"] = {"session": getattr(trace, "label", None), "first_url": url,
                                     "ts": now, "zone_source": "session" if getattr(trace, "zone", None) else None}
        v["sources"]["touched_by"] = ["candidate"]
        reg["venues"].append(v)
        return f"candidate:{v['id']}"


# --- zone coverage (our own anchors; never See Saw) ---------------------------

def _anchor_hit(reg: dict, anchor: str) -> dict | None:
    v = find_venue(reg, anchor, None, add_alias=False)
    if v is not None:
        return v
    key = tools._norm_venue(anchor)
    if len(key) < 3:
        return None
    pat = re.compile(r"\b" + re.escape(key) + r"\b")
    for v in reg.get("venues", []):
        for n in [v["name"]] + list(v.get("aliases", [])):
            if pat.search(tools._norm_venue(n)):
                return v
    return None


def zone_coverage(city: str, zone: str, cfg: dict, min_enumerated: int = 4,
                  reg: dict | None = None) -> dict:
    """Did enumeration + seeding cover this zone? Anchors come from
    cfg["zones"][zone]["anchors"] (cities.py — our own config)."""
    reg = reg if reg is not None else load_registry(city)
    zv = [v for v in reg.get("venues", []) if v.get("neighborhood") == zone]
    enumerated = [v for v in zv if (v.get("sources") or {}).get("directory_session")
                  or (v.get("sources") or {}).get("shows")]
    seeded = [v for v in zv if (v.get("sources") or {}).get("seed")]
    # Curated lists (GPLA, Carla) are the yardstick for enumeration quality;
    # Places alone sweeps in framers and studios, so it never counts here.
    curated = [v for v in seeded if (v["sources"]["seed"] or {}).get("gpla")
               or (v["sources"]["seed"] or {}).get("carla")]
    candidates = [v for v in zv if v.get("status") == "candidate"]
    enum_ids = {v["id"] for v in enumerated}
    overlap = (sum(1 for v in curated if v["id"] in enum_ids) / len(curated)) if curated else None
    anchors = (cfg.get("zones") or {}).get(zone, {}).get("anchors", [])
    missing = [a for a in anchors if _anchor_hit(reg, a) is None]
    reasons = []
    if missing:
        reasons.append("anchors_missing")
    if len(enumerated) + len(curated) < min_enumerated:
        reasons.append("below_min")
    if len(curated) >= 5 and overlap is not None and overlap < 0.3:
        reasons.append("weak_enumeration")
    return {"zone": zone, "ok": not reasons, "registry": len(zv),
            "enumerated": len(enumerated), "seeded": len(seeded), "curated": len(curated),
            "candidates": len(candidates),
            "missing_anchors": missing, "overlap": None if overlap is None else round(overlap, 2),
            "reasons": reasons}


# --- hooks from tools.py -------------------------------------------------------

def _venue_patch_from_show(record: dict, placement: str) -> dict:
    v = record["venue"]
    return {
        "name": v["name"], "kind": "museum" if v.get("is_museum") else None,
        "is_museum": bool(v.get("is_museum")), "status": "active",
        "neighborhood": v.get("neighborhood"), "address": v.get("address"),
        "address_detail": v.get("address_detail"),
        "latitude": v.get("latitude"), "longitude": v.get("longitude"),
        "coords_source": f"show:{record['slug']}" if v.get("latitude") is not None else None,
        "website": v.get("website"), "hours": v.get("hours") or None, "phone": v.get("phone"),
        "last_known_shows": [{"slug": record["slug"], "title": record.get("title"),
                              "artist": record.get("artist"),
                              "start": record.get("start_date"),
                              # scheduling needs an end even when the venue
                              # publishes none: estimate (never displayed)
                              "end": record.get("end_date") or _iso(tools.effective_end(record)),
                              "end_estimated": record.get("end_date") is None,
                              "placement": placement}],
        "sources": {"shows": None},
    }


def on_save_show(record: dict, city: str, session: str | None,
                 trace: SessionTrace | None, placement: str = "pending") -> str:
    """Registry side of save_show: upsert venue facts, attribute this venue's
    fetched URLs, log the per-venue cost slice. Returns the venue id."""
    patch = _venue_patch_from_show(record, placement)
    urls = list(trace.since_mark()) if trace else []
    deltas = trace.advance() if trace else {}
    if trace:
        trace.current_key = tools._norm_venue(record["venue"]["name"])
    today = date.today()
    with locked_registry(city) as reg:
        v = find_venue(reg, record["venue"]["name"], record["venue"].get("website"))
        if v is None:
            v = empty_venue(venue_id(record["venue"]["name"]), record["venue"]["name"])
            reg["venues"].append(v)
        merge_patch(v, patch)
        shows = v.setdefault("sources", {}).setdefault("shows", [])
        if record["slug"] not in shows:
            shows.append(record["slug"])
        if trace:
            best, cands = attribute_urls(v, urls,
                                         extra_domains=record.get("source_urls") or [])
            if best and v.get("exhibitions_url_source") not in ("agent", "manual"):
                v["exhibitions_url"], v["exhibitions_url_source"] = best, "harness"
            merge_patch(v, {"exhibitions_url_candidates": cands})
            if trace.image_sources:
                for s in v["last_known_shows"]:
                    if s["slug"] == record["slug"]:
                        s["image_sources"] = {img: trace.image_sources[img]
                                              for img in record.get("images", [])
                                              if img in trace.image_sources}
        if not v.get("exhibitions_url"):
            best, cands = attribute_urls(v, [(0, u, "source") for u in record.get("source_urls") or []],
                                         extra_domains=record.get("source_urls") or [])
            if best:
                v["exhibitions_url"], v["exhibitions_url_source"] = best, "harness"
            merge_patch(v, {"exhibitions_url_candidates": cands})
        v["last_scraped"] = int(time.time())
        v["last_outcome"] = "saved"
        v["scrape_history"].append({"ts": v["last_scraped"], "session": session,
                                    "outcome": "saved", "slug": record["slug"], **deltas})
        del v["scrape_history"][:-40]
        v["next_check"] = _iso(compute_next_check(v, today))
        return v["id"]


def on_log_skip(entry: dict, city: str, trace: SessionTrace | None) -> str | None:
    urls = list(trace.since_mark()) if trace else []
    deltas = trace.advance() if trace else {}
    with locked_registry(city) as reg:
        v = find_venue(reg, entry["venue"], entry.get("url"))
        if v is None:
            v = empty_venue(venue_id(entry["venue"]), entry["venue"])
            v["neighborhood"] = entry.get("neighborhood")
            reg["venues"].append(v)
        apply_skip(v, entry)
        if entry.get("reason") == "duplicate" and entry.get("url"):
            dom = registrable_domain(entry["url"])
            for other in reg["venues"]:
                if other is not v and dom and registrable_domain(other.get("website")) == dom:
                    if entry["venue"] not in other["aliases"]:
                        other["aliases"].append(entry["venue"])
                    break
        if v.get("website") and trace:
            best, cands = attribute_urls(v, urls)
            if best and not v.get("exhibitions_url"):
                v["exhibitions_url"], v["exhibitions_url_source"] = best, "harness"
            merge_patch(v, {"exhibitions_url_candidates": cands})
        v["last_scraped"] = int(time.time())
        v["scrape_history"].append({"ts": v["last_scraped"], "session": entry.get("session"),
                                    "outcome": f"skipped:{entry.get('reason')}",
                                    "slug": None, **deltas})
        del v["scrape_history"][:-40]
        return v["id"]


def on_record_venue(entry: dict, city: str) -> str:
    with locked_registry(city) as reg:
        v = find_venue(reg, entry["name"], entry.get("website"))
        if v is None:
            v = empty_venue(venue_id(entry["name"]), entry["name"])
            reg["venues"].append(v)
            v["next_check"] = date.today().isoformat()
        merge_patch(v, {"neighborhood": entry.get("neighborhood"), "kind": entry.get("kind"),
                        "is_museum": True if entry.get("kind") == "museum" else None,
                        "address": entry.get("address"), "website": entry.get("website"),
                        "notes": entry.get("note"),
                        "sources": {"directory_ts": entry.get("ts"),
                                    "directory_session": entry.get("session")}})
        return v["id"]


def on_confirm_show(show: dict, city: str, placement: str) -> None:
    patch = _venue_patch_from_show(show, placement)
    patch.pop("status", None)
    with locked_registry(city) as reg:
        v = find_venue(reg, show["venue"]["name"], show["venue"].get("website"))
        if v is None:
            v = empty_venue(venue_id(show["venue"]["name"]), show["venue"]["name"])
            reg["venues"].append(v)
        merge_patch(v, patch)
        v["next_check"] = _iso(compute_next_check(v))


def on_sweep_demoted(city: str, slug: str, reason: str) -> None:
    with locked_registry(city) as reg:
        for v in reg["venues"]:
            for s in v.get("last_known_shows", []):
                if s["slug"] == slug:
                    s["placement"] = "pending"
                    if reason == "ended":
                        v["next_check"] = date.today().isoformat()
                    return


def on_crosscheck(city: str, venue_name: str, google: dict | None) -> None:
    if not google or not google.get("found"):
        return
    with locked_registry(city) as reg:
        v = find_venue(reg, venue_name, google.get("website"), add_alias=False)
        if v is None:
            return
        v["google"] = {"status": google.get("status"), "address": google.get("address"),
                       "hours": google.get("hours"), "phone": google.get("phone"),
                       "website": google.get("website"), "lat": google.get("lat"),
                       "lng": google.get("lng"), "ts": int(time.time())}
        v.setdefault("sources", {})["crosscheck_ts"] = v["google"]["ts"]
        if google.get("status") == "CLOSED_PERMANENTLY":
            v["status"] = "closed"
            v["next_check"] = _iso(compute_next_check(v))
