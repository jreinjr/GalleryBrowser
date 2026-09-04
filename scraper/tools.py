"""Client-side tools for the gallery scraper agent: image URL extraction,
image download/normalization, and validated show persistence."""

from __future__ import annotations

import fcntl
import io
import json
import os
import re
import time
import unicodedata
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"
IMAGES_DIR = CONTENT_DIR / "images"
# Pending pool: scraped shows waiting for verification. Lives in a
# subdirectory so the web build's content/*.json glob and the iOS app's
# "<city>.json" lookup never see it — only verified shows are displayed.
PENDING_DIR = CONTENT_DIR / "pending"

# A/B sandbox: when set (via set_sandbox), show records and images are written
# under this directory instead of the real content tree, and coordinate
# resolution is skipped. Lets two model arms scrape the SAME venues for
# side-by-side comparison without touching real data or tripping the
# same-show dedup against the live pools.
SANDBOX_DIR: Path | None = None


def set_sandbox(path: str | Path | None) -> None:
    global SANDBOX_DIR
    SANDBOX_DIR = Path(path).resolve() if path else None
    if SANDBOX_DIR:
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)


def _content_root() -> Path:
    return SANDBOX_DIR or CONTENT_DIR


# Current agent session's trace (venues.SessionTrace), set by the harness.
# Registry hooks use it for URL attribution, per-venue cost slices and image
# provenance. None outside a session (CLI tools, tests).
SESSION = None


def set_session(trace) -> None:
    global SESSION
    SESSION = trace


def _note_resolution(name: str, bucket: str, reason: str | None = None,
                     venue_id: str | None = None) -> dict | None:
    """Record a save ('current' / 'upcoming') or a skip in the session's
    venue-resolution ledger (venues.SessionTrace.resolutions). Deliberately
    NOT behind _registry_hook: sandbox A/B sessions keep the contract too.
    No-op outside a session (CLI tools, tests)."""
    if SESSION is None or not hasattr(SESSION, "resolution"):
        return None
    r = SESSION.resolution(name, venue_id)
    if bucket == "skip":
        r["skip"] = reason
    else:
        r[bucket] += 1
    return r


def _registry_hook(fn_name: str, *args, **kwargs):
    """Call venues.<fn_name> without letting registry trouble break a save.
    No-op in A/B sandbox mode (registry is real-data only)."""
    if SANDBOX_DIR is not None:
        return None
    try:
        import venues
        return getattr(venues, fn_name)(*args, **kwargs)
    except Exception as exc:  # never fail the agent tool over bookkeeping
        try:
            log_event({"session": getattr(SESSION, "label", None), "kind": "registry_error",
                       "hook": fn_name, "error": str(exc)[:300]})
        except Exception:
            pass
        return None

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)

MIN_ACCEPT_WIDTH = 500       # reject images narrower than this
GOOD_WIDTH = 1400            # what we consider "high-res enough"
MAX_STORED_WIDTH = 3200      # downscale anything wider to keep the bundle sane
IMG_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif|tiff?)([?#].*)?$", re.I)
SKIP_HINTS = re.compile(r"(logo|icon|sprite|favicon|avatar|badge|placeholder|pixel|blank)", re.I)


class _ImgParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base = base_url
        self.found: list[dict] = []

    def _add(self, url: str | None, alt: str = "", hint: int = 0):
        if not url:
            return
        url = urljoin(self.base, url.strip())
        if not url.startswith(("http://", "https://")):
            return
        if SKIP_HINTS.search(url):
            return
        self.found.append({"url": url, "alt": alt[:120], "size_hint": hint})

    @staticmethod
    def _largest_srcset(srcset: str) -> str | None:
        best, best_w = None, -1
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            w = 0
            if len(bits) > 1 and bits[1].endswith("w"):
                try:
                    w = int(bits[1][:-1])
                except ValueError:
                    w = 0
            if w > best_w:
                best, best_w = bits[0], w
        return best

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img":
            hint = 0
            for key in ("width", "data-width"):
                try:
                    hint = max(hint, int(re.sub(r"\D", "", a.get(key) or "0") or 0))
                except ValueError:
                    pass
            src = None
            if a.get("srcset"):
                src = self._largest_srcset(a["srcset"])
            src = src or a.get("data-src") or a.get("data-full") or a.get("data-image") or a.get("src")
            self._add(src, a.get("alt") or "", hint)
        elif tag == "source" and a.get("srcset"):
            self._add(self._largest_srcset(a["srcset"]))
        elif tag == "meta" and a.get("property") in ("og:image", "og:image:secure_url"):
            self._add(a.get("content"), alt="og:image", hint=1200)
        elif tag == "a" and a.get("href") and IMG_EXT_RE.search(a.get("href") or ""):
            self._add(a["href"], alt="linked image file")


def extract_image_urls(url: str) -> str:
    """Fetch a web page and return candidate image URLs found in its HTML."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    if "charset" not in resp.headers.get("Content-Type", "").lower():
        resp.encoding = resp.apparent_encoding
    if SESSION is not None:
        SESSION.add_url(resp.url, "client_fetch")
        _registry_hook("write_evidence", SESSION.city, resp.url, "client_html",
                       resp.text[:300_000], SESSION.label)
        try:
            from cities import CITIES as _CITIES
            _registry_hook("on_fetch", SESSION.city, resp.url, resp.text[:300_000], SESSION,
                           _CITIES.get(SESSION.city, {}))
        except Exception:
            pass
    parser = _ImgParser(resp.url)
    try:
        parser.parse_error = None
        parser.feed(resp.text)
    except Exception:
        pass
    seen, out = set(), []
    # og:image and explicit-size candidates first, then document order
    for item in sorted(parser.found, key=lambda i: -i["size_hint"]):
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        out.append(item)
        if len(out) >= 40:
            break
    if not out:
        return json.dumps({"page": resp.url, "candidates": [], "note": "No image URLs found in HTML (page may render images via JavaScript)."})
    return json.dumps({"page": resp.url, "candidates": out})


# Windows refuses to create a file whose stem is a DOS device name, so a venue
# like "CON_" would slug to a path no Windows clone can check out. Slugs become
# file names (venue reports, image dirs), so escape them here, at the source.
_WIN_RESERVED = {"con", "prn", "aux", "nul",
                 *(f"com{i}" for i in range(1, 10)),
                 *(f"lpt{i}" for i in range(1, 10))}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]
    if slug in _WIN_RESERVED:
        slug += "-"  # idempotent: the strip above runs before this check
    return slug or "untitled"


_NORM_STRIP_SUFFIXES = (" gallery", " galleries", " fine art", " fine arts")


def _norm_venue(name: str) -> str:
    """Normalized venue-name key shared by the one-show-per-venue dedup, the
    venue directory, and TODO-list matching. Conservative on purpose: a false
    negative (two spellings of one venue) is advisory-list territory, a false
    positive would wrongly block a save."""
    import html as _html
    s = unicodedata.normalize("NFKD", _html.unescape(name))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    # [\W_] and not [^a-z0-9]: on ASCII the two are identical (s is already
    # lowercased), but this keeps CJK and other non-Latin letters instead of
    # collapsing every Japanese venue name to the empty key.
    s = re.sub(r"[\W_]+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    for suf in _NORM_STRIP_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[: -len(suf)]
            break
    return s.strip()


def _norm_title(title: str | None) -> str:
    """Normalized show-title key for same-show dedup at one venue (a venue may
    hold several concurrent shows; the same show re-saved under a new slug
    must not)."""
    import html as _html
    s = unicodedata.normalize("NFKD", _html.unescape(title or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"^(the|a|an) ", "", s)
    return re.sub(r"\s+", " ", s)


def download_image(url: str, city: str, show_slug: str) -> str:
    """Download an image, verify resolution, normalize to JPEG, store it under
    content/images/<city>/<show_slug>/ and return the stored relative path."""
    resp = requests.get(url, headers={"User-Agent": UA, "Referer": url}, timeout=60)
    resp.raise_for_status()
    try:
        img = Image.open(io.BytesIO(resp.content))
        img.load()
    except Exception as exc:
        raise ValueError(f"URL did not decode as an image ({exc}). Try a different candidate.")

    width, height = img.size
    if width < MIN_ACCEPT_WIDTH:
        raise ValueError(
            f"Image is only {width}x{height}px — too small. Look for a higher-resolution "
            f"version (at least {GOOD_WIDTH}px wide is ideal)."
        )

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if width > MAX_STORED_WIDTH:
        img = img.resize((MAX_STORED_WIDTH, int(height * MAX_STORED_WIDTH / width)), Image.LANCZOS)

    show_dir = _content_root() / "images" / city / _slugify(show_slug)
    show_dir.mkdir(parents=True, exist_ok=True)
    idx = len(list(show_dir.glob("*.jpg"))) + 1
    path = show_dir / f"{idx:02d}.jpg"
    img.save(path, "JPEG", quality=88, optimize=True)

    rel = str(path.relative_to(_content_root()))
    if SESSION is not None:
        SESSION.image_sources[rel] = {"url": url, "px": [width, height]}
    note = "high-res" if width >= GOOD_WIDTH else "acceptable but below ideal resolution"
    return json.dumps(
        {"stored": rel, "original_px": [width, height], "quality": note, "images_for_show": idx}
    )


SAVE_SHOW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "city", "slug", "title", "artist", "start_date", "end_date", "dates_note", "description",
        "editors_pick", "featured", "reception", "images", "source_urls", "venue",
    ],
    "properties": {
        "city": {"type": "string", "description": "City key, e.g. 'seattle'"},
        "slug": {"type": "string", "description": "Short kebab-case id unique within the city; must match the show_slug used when downloading this show's images"},
        "title": {"type": "string", "description": "Exhibition title (shown in italics)"},
        "artist": {"type": ["string", "null"], "description": "Artist name(s), or null when the exhibition title stands alone (e.g. a themed group show)"},
        "start_date": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD (opening day). null ONLY when the venue's own page confirms the show is on view now but publishes no opening date."},
        "end_date": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD (last day on view). null when the venue publishes no closing date — never guess one; the show is held until a later pass fills it."},
        "dates_note": {"type": ["string", "null"], "description": "null when the venue publishes exact opening AND closing dates. Otherwise one line quoting what its page shows, e.g. \"page says 'on view now', no closing date\" or \"listed under 'August exhibitions' only (saved as Aug 1-31)\"."},
        "description": {
            "type": "string",
            "description": "2-4 paragraphs separated by blank lines. YOUR OWN original writing synthesizing what the show is, what's in it, and why it matters. Never paste or lightly rephrase the venue's press release.",
        },
        "editors_pick": {"type": "boolean"},
        "featured": {"type": "boolean", "description": "Whether the show appears in the Featured feed"},
        "reception": {"type": ["string", "null"], "description": "Opening reception info if any, e.g. 'Thursday, September 3, 6-8pm'"},
        "images": {
            "type": "array", "minItems": 1,
            "items": {"type": "string"},
            "description": "Stored relative paths returned by download_image, in display order",
        },
        "source_urls": {"type": "array", "items": {"type": "string"}},
        "venue": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "is_museum", "address", "address_detail", "neighborhood",
                          "hours", "phone", "website"],
            "properties": {
                "name": {"type": "string"},
                "is_museum": {"type": "boolean"},
                "address": {"type": "string", "description": "Street address, e.g. '212 Third Ave S'. Must match the venue's own site exactly — the map pin is geocoded from it."},
                "address_detail": {"type": ["string", "null"], "description": "Suite/floor, or null"},
                "neighborhood": {"type": "string", "description": "One of the city's configured neighborhoods"},
                "hours": {"type": "array", "items": {"type": "string"}, "description": "Lines like 'Tue - Sat 10:30am to 5:30pm'"},
                "phone": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
            },
        },
    },
}


CONFIRM_SHOW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city", "slug", "status", "reason", "corrections"],
    "properties": {
        "city": {"type": "string"},
        "slug": {"type": "string", "description": "Slug of the saved show being verified"},
        "status": {
            "type": "string", "enum": ["verified", "corrected", "unverified"],
            "description": "verified: everything confirmed on the venue's own site; corrected: show is real and current but you fixed fields; unverified: could not confirm show/venue, venue closed, or show already ended",
        },
        "reason": {"type": ["string", "null"], "description": "One line explaining an unverified status (or what was corrected); null for verified"},
        "corrections": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["start_date", "end_date", "hours", "address", "address_detail",
                          "phone", "website", "reception"],
            "description": "Only for status=corrected: pass the corrected values, null for every field that is already right. There is no coordinates field — pins are geocoded from the address, so correct the address instead.",
            "properties": {
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "hours": {"type": ["array", "null"], "items": {"type": "string"}},
                "address": {"type": ["string", "null"]},
                "address_detail": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
                "reception": {"type": ["string", "null"]},
            },
        },
    },
}

VERIFY_RESULTS = CONTENT_DIR / "spend" / "verify_results.jsonl"

CORRECTABLE_VENUE_FIELDS = ("hours", "address", "address_detail", "phone", "website")
CORRECTABLE_SHOW_FIELDS = ("start_date", "end_date", "reception")


def confirm_show(args: dict, city_key: str) -> str:
    """Record a verification verdict; apply corrections; move the record
    between the pending pool and the published city file.

    Promotion to the published (displayed) file requires BOTH a
    verified/corrected verdict AND deterministically resolved coordinates.
    An unverified verdict demotes a published show back to pending
    (non-destructive — nothing is deleted).
    """
    import time as _time
    if args["city"] != city_key:
        raise ValueError(f"city must be '{city_key}'")
    slug = args["slug"]
    lock_path = CONTENT_DIR / f".{city_key}.json.lock"
    applied = []
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main = _load_shows_file(_city_file(city_key))
        pending = _load_shows_file(_pending_file(city_key))
        matching = [s for s in main["shows"] + pending["shows"] if s["slug"] == slug]
        if not matching:
            raise ValueError(f"no saved show with slug '{slug}' in {city_key}")
        show = matching[0]
        if args["status"] == "corrected" and args.get("corrections"):
            for field, value in args["corrections"].items():
                if value is None:
                    continue
                if field in CORRECTABLE_SHOW_FIELDS:
                    show[field] = value
                    applied.append(field)
                elif field in CORRECTABLE_VENUE_FIELDS:
                    show["venue"][field] = value
                    applied.append(field)
            if "end_date" in applied and show.get("end_date"):
                show["dates_note"] = None   # the correction is authoritative
        show.setdefault("dates_note", None)
        _set_dates_meta(show, int(_time.time()) if args["status"] in ("verified", "corrected") else None)
        # Coordinates are deterministic, never agent memory: re-resolve when
        # the address changed or the pin is still unresolved from scrape time.
        v = show["venue"]
        if ("address" in applied
                or v.get("latitude") is None or v.get("longitude") is None):
            if resolve_venue_coords(v, city_key):
                applied.append("coords_resolved")
            elif "address" in applied:
                v["latitude"] = v["longitude"] = None

        coords_ok = v.get("latitude") is not None and v.get("longitude") is not None
        in_window = show_in_window(show)
        dates_known = dates_ok(show)
        publish = (args["status"] in ("verified", "corrected") and coords_ok and in_window
                   and dates_known)
        target = main if publish else pending
        other = pending if publish else main
        if any(s["slug"] == slug for s in target["shows"]):
            # staying in its pool: keep file position (feed order follows it)
            target["shows"] = [show if s["slug"] == slug else s for s in target["shows"]]
        else:
            other["shows"] = [s for s in other["shows"] if s["slug"] != slug]
            target["shows"].append(show)
        _write_shows_file(_city_file(city_key), main)
        _write_shows_file(_pending_file(city_key), pending)
    placement = "published" if publish else "pending"
    _registry_hook("on_confirm_show", show, city_key, placement)
    entry = {"ts": int(_time.time()), "city": city_key, "slug": slug,
             "status": args["status"], "reason": args.get("reason"),
             "applied": applied, "placement": placement}
    VERIFY_RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with open(VERIFY_RESULTS, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    result = {"result": args["status"], "slug": slug,
              "corrections_applied": applied, "placement": placement}
    if args["status"] in ("verified", "corrected") and not coords_ok:
        result["note"] = ("kept in pending: coordinates could not be resolved from the "
                         "address — if the venue's site shows a more standard street "
                         "address, call confirm_show again with it as a correction")
    elif args["status"] in ("verified", "corrected") and not dates_known:
        result["note"] = ("kept in pending: closing date unknown — pass corrections.end_date "
                         "(and start_date) once the venue's own site or two independent "
                         "listings publish it; never estimate")
    elif args["status"] in ("verified", "corrected") and not in_window:
        result["note"] = ("kept in pending: the show is verified but not yet within "
                         f"the {OPEN_WINDOW_DAYS}-day publication window (or it has "
                         "ended); placement is automatic — no action needed")
    return json.dumps(result)


def attach_images(city_key: str, slug: str) -> str:
    """Sync a saved show's images list with every image downloaded for its slug.
    Used by the enrichment pass to add newly downloaded images to a record."""
    out_path = CONTENT_DIR / f"{city_key}.json"
    if not out_path.exists():
        raise ValueError(f"no content file for city {city_key}")
    data = json.loads(out_path.read_text())
    matching = [s for s in data["shows"] if s["slug"] == slug]
    if not matching:
        raise ValueError(f"no saved show with slug '{slug}' in {city_key}")
    show = matching[0]
    show_dir = IMAGES_DIR / city_key / _slugify(slug)
    files = sorted(show_dir.glob("*.jpg")) if show_dir.is_dir() else []
    if not files:
        raise ValueError(f"no downloaded images on disk for {city_key}/{slug}")
    show["images"] = [str(p.relative_to(CONTENT_DIR)) for p in files]
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    return json.dumps({"result": "attached", "slug": slug, "image_count": len(show["images"])})


def _city_file(city_key: str) -> Path:
    return _content_root() / f"{city_key}.json"


def _pending_file(city_key: str) -> Path:
    return _content_root() / "pending" / f"{city_key}.json"


def _load_shows_file(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {"shows": []}


def _write_shows_file(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def all_city_shows(city_key: str) -> list[dict]:
    """Published + pending shows for a city (published first)."""
    return (_load_shows_file(_city_file(city_key))["shows"]
            + _load_shows_file(_pending_file(city_key))["shows"])


OPEN_WINDOW_DAYS = 7    # publish only shows on view now or opening within this
VERDICT_FRESH_DAYS = 14  # placement sweep trusts a verdict at most this old
FUTURE_SAVE_DAYS = 60   # deep runs may save confirmed shows opening up to this
                        # far out (they wait in pending; the sweep publishes
                        # them when the OPEN_WINDOW_DAYS window arrives)


# Shows saved without a closing date (the venue publishes none) are held in
# pending and re-checked weekly; for scheduling/retirement they get an
# estimated end measured from the last time a primary source confirmed them.
DATE_RETRY_DAYS = 7
NULL_END_GALLERY_DAYS, NULL_END_MUSEUM_DAYS = 45, 120    # from last confirmation
NULL_END_GALLERY_CAP, NULL_END_MUSEUM_CAP = 90, 365      # from start_date, when known


def _parse_iso(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except (ValueError, TypeError):
        return None


def dates_ok(show: dict) -> bool:
    """Both dates present and parseable — required for publication."""
    return _parse_iso(show.get("start_date")) is not None and \
        _parse_iso(show.get("end_date")) is not None


def effective_end(show: dict, today: date | None = None) -> date | None:
    """end_date, or an estimate for shows saved without one (scheduling and
    retirement only — never displayed)."""
    end = _parse_iso(show.get("end_date"))
    if end is not None:
        return end
    ts = show.get("dates_confirmed_ts")
    confirmed = date.fromtimestamp(ts) if ts else (today or date.today())
    museum = bool((show.get("venue") or {}).get("is_museum"))
    est = confirmed + timedelta(days=NULL_END_MUSEUM_DAYS if museum else NULL_END_GALLERY_DAYS)
    start = _parse_iso(show.get("start_date"))
    if start is not None:
        est = min(est, start + timedelta(days=NULL_END_MUSEUM_CAP if museum else NULL_END_GALLERY_CAP))
    return est


def _set_dates_meta(show: dict, confirmed_ts: int | None = None) -> None:
    """Handler-derived date metadata (never agent-set): dates_confidence in
    exact | approximate | unknown_end | unknown, and dates_confirmed_ts."""
    start, end = _parse_iso(show.get("start_date")), _parse_iso(show.get("end_date"))
    if start is None:
        conf = "unknown"
    elif end is None:
        conf = "unknown_end"
    elif show.get("dates_note"):
        conf = "approximate"
    else:
        conf = "exact"
    show["dates_confidence"] = conf
    if confirmed_ts:
        show["dates_confirmed_ts"] = int(confirmed_ts)
    elif not show.get("dates_confirmed_ts"):
        show["dates_confirmed_ts"] = int(time.time())


def show_expired(show: dict, today: date | None = None) -> bool:
    today = today or date.today()
    end = effective_end(show, today)
    return end is not None and end < today


def show_in_window(show: dict, today: date | None = None) -> bool:
    """The publication rule: on view now, or opening within OPEN_WINDOW_DAYS.
    Enforced in code, not prompts — agents have rationalized around it."""
    today = today or date.today()
    if show_expired(show, today):
        return False
    start = _parse_iso(show.get("start_date"))
    return start is None or start <= today + timedelta(days=OPEN_WINDOW_DAYS)


def latest_verdicts() -> dict[tuple[str, str], dict]:
    """Last confirm_show verdict per (city, slug) from the verify ledger."""
    verdicts: dict[tuple[str, str], dict] = {}
    if VERIFY_RESULTS.exists():
        for line in VERIFY_RESULTS.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            verdicts[(e["city"], e["slug"])] = e
    return verdicts


def awaiting_window_only(city_key: str, show: dict,
                         verdicts: dict | None = None) -> bool:
    """Pending show that holds a fresh positive verdict and resolved coords —
    nothing left to audit; only the publication window (time) holds it back,
    and the placement sweep will promote it. Skipped by pending-only verifies
    so cron runs don't re-audit (and re-pay for) the same show every pass."""
    v = (verdicts if verdicts is not None else latest_verdicts()).get(
        (city_key, show["slug"]))
    return bool(v and v["status"] in ("verified", "corrected")
                and time.time() - v.get("ts", 0) <= VERDICT_FRESH_DAYS * 86400
                and show["venue"].get("latitude") is not None
                and show["venue"].get("longitude") is not None
                and dates_ok(show))


def date_fill_cooldown(city_key: str, show: dict, verdicts: dict | None = None) -> bool:
    """A dateless show that was audited within DATE_RETRY_DAYS: don't re-pay
    to look for its closing date again this pass."""
    if dates_ok(show):
        return False
    v = (verdicts if verdicts is not None else latest_verdicts()).get(
        (city_key, show["slug"]))
    return bool(v and time.time() - v.get("ts", 0) <= DATE_RETRY_DAYS * 86400)


def verify_candidate(city_key: str, show: dict, verdicts: dict | None = None,
                     today: date | None = None) -> bool:
    """Should a pending-only verify pass audit this show now?"""
    verdicts = verdicts if verdicts is not None else latest_verdicts()
    return (not show_expired(show, today)
            and not awaiting_window_only(city_key, show, verdicts)
            and not date_fill_cooldown(city_key, show, verdicts))


def pending_reason(city_key: str, show: dict, verdicts: dict | None = None) -> str:
    """One line on why a pending show isn't published (report/summary use)."""
    verdicts = verdicts if verdicts is not None else latest_verdicts()
    if not dates_ok(show):
        v = verdicts.get((city_key, show["slug"]))
        ref = (v or {}).get("ts") or show.get("dates_confirmed_ts") or time.time()
        age = int((time.time() - ref) // 86400)
        return f"needs dates ({show.get('dates_confidence') or 'unknown'}; last check {age}d ago)"
    if awaiting_window_only(city_key, show, verdicts):
        return ("verified; publishes when its opening window arrives"
                if not show_expired(show) else "verified but ended")
    v = verdicts.get((city_key, show["slug"]))
    if v and v.get("reason"):
        return f"{v['status']}: {v['reason']}"
    if v:
        return v["status"]
    return "awaiting verification"


# --- deep-run ledgers: skips, session events, venue directory -----------------

SKIPS_FILE = CONTENT_DIR / "spend" / "skips.jsonl"
EVENTS_FILE = CONTENT_DIR / "spend" / "session_events.jsonl"

SKIP_REASONS = ["no_image", "low_res_only", "unverifiable", "closed_or_between_shows",
                "appointment_only", "out_of_scope", "duplicate", "unchanged", "other"]

# 'unverifiable' misuse: the venue's own page CONFIRMS a show but its dates
# are missing or JS-rendered. With nullable dates that is a save (end_date
# null + dates_note), not a skip — the handler pushes back once, and the
# scheduler retries such venues in a week instead of a month.
_UNVERIFIABLE_CLAIM_RE = re.compile(r"(confirm|current(ly)?|on view|on-view)", re.I)
_UNVERIFIABLE_DATES_RE = re.compile(r"(date|javascript|js\b|render|dynamic)", re.I)


def confirmed_show_skip(detail: str | None) -> bool:
    d = detail or ""
    return bool(_UNVERIFIABLE_CLAIM_RE.search(d) and _UNVERIFIABLE_DATES_RE.search(d))


def _append_jsonl(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_event(entry: dict) -> None:
    """Harness-side session event ledger (tool errors, refusals, budget stops,
    session ends) — the outlier report's safety net for everything the model
    never explicitly log_skip'ed."""
    _append_jsonl(EVENTS_FILE, {"ts": int(time.time()), **entry})


LOG_SKIP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city", "venue", "neighborhood", "reason", "detail", "url"],
    "properties": {
        "city": {"type": "string"},
        "venue": {"type": "string", "description": "Venue name as it appears on your TODO list (or as you found it)"},
        "neighborhood": {"type": "string", "description": "The zone you are working"},
        "reason": {
            "type": "string", "enum": SKIP_REASONS,
            "description": "no_image: no downloadable image at all; low_res_only: images exist but none reach 500px wide; unverifiable: venue/show could not be confirmed on any primary source even after render_fetch — NOT for shows the venue's own site confirms but without exact dates (save those with end_date null + dates_note); closed_or_between_shows: venue operating but nothing on view NOW (use it too after saving a venue's only UPCOMING show, noting the opening date); appointment_only: no public walk-in hours; out_of_scope: not an art-viewing venue (or outside this city's scope); duplicate: this TODO venue is the same physical space as a venue already on the list/saved under another name; unchanged: every current show at this venue is already saved with the same dates; other: explain in detail",
        },
        "detail": {"type": "string", "description": "One line of specifics: what you found and why it can't be saved (e.g. 'site shows Fall show opening Nov 14, beyond the 60-day horizon' or 'largest image on site is 400px')"},
        "url": {"type": ["string", "null"], "description": "Most relevant URL you checked, or null"},
    },
}


def log_skip(args: dict, city_key: str, session: str) -> str:
    """Record a venue the agent decided not to save, with the reason —
    feeds the post-run outlier report. The tool is declared non-strict
    (strict-schema complexity budget), so validate here."""
    missing = [k for k in ("venue", "neighborhood", "reason", "detail")
               if not (isinstance(args.get(k), str) and args[k].strip())]
    if missing:
        raise ValueError(f"log_skip needs non-empty: {', '.join(missing)}")
    if args.get("city", city_key) != city_key:
        raise ValueError(f"city must be '{city_key}'")
    reason, detail = args["reason"], args["detail"]
    if reason not in SKIP_REASONS:
        detail = f"[reason given: {reason}] {detail}"
        reason = "other"
    if reason == "unverifiable" and SESSION is not None and confirmed_show_skip(detail):
        r = SESSION.resolution(args["venue"])
        if not r["rejected_once"]:
            r["rejected_once"] = True   # push back once per venue; never loop
            raise ValueError(
                "Not logged: 'unverifiable' is not for a show the venue's own page confirms "
                "but whose dates are missing or JS-rendered. If the page confirms the show is "
                "on view now (or gives its opening date), save_show it with end_date null "
                "(start_date null if unknown) and a dates_note quoting the page — run "
                "render_fetch on that page first if you have not. Only if the show cannot be "
                "confirmed on ANY primary source, call log_skip again with the same reason "
                "and say so in detail.")
    url = args.get("url")
    entry = {
        "ts": int(time.time()), "session": session, "city": city_key,
        "venue": args["venue"], "neighborhood": args["neighborhood"],
        "reason": reason, "detail": detail,
        "url": url if isinstance(url, str) else None,
    }
    _append_jsonl(SKIPS_FILE, entry)
    vid = _registry_hook("on_log_skip", entry, city_key, SESSION)
    _note_resolution(args["venue"], "skip", reason, venue_id=vid)
    return json.dumps({"result": "logged", "venue": args["venue"], "reason": reason})


def _directory_file(city_key: str) -> Path:
    return CONTENT_DIR / "spend" / f"venue_directory-{city_key}.jsonl"


RECORD_VENUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city", "name", "neighborhood", "kind", "address", "website", "note"],
    "properties": {
        "city": {"type": "string"},
        "name": {"type": "string", "description": "Venue name in its standard form"},
        "neighborhood": {"type": "string", "description": "The zone being enumerated"},
        "kind": {"type": "string",
                 "enum": ["gallery", "museum", "nonprofit", "project_space", "university", "other"]},
        "address": {"type": ["string", "null"], "description": "Street address when the directory page shows one, else null"},
        "website": {"type": ["string", "null"], "description": "Venue website URL when shown, else null"},
        "note": {"type": ["string", "null"], "description": "Anything useful: district, focus, 'inside Bergamot Station', etc."},
    },
}


def zone_aliases(city_key: str | None) -> dict[str, str]:
    """Normalized {district -> zone} from the city's `zone_aliases` config.
    Cities whose district names do not nest inside the zone labels (Tokyo:
    "Shinagawa" is in Tennozu, "Harajuku" in Shibuya/Omotesando) declare them
    there so the agent does not have to guess our label."""
    from cities import CITIES   # local import: cities.py must stay dependency-free
    raw = (CITIES.get(city_key or "") or {}).get("zone_aliases") or {}
    return {_norm_venue(k): v for k, v in raw.items()}


def normalize_zone(name: str | None, neighborhoods: list[str],
                   city_key: str | None = None) -> str | None:
    """Map an agent-written zone name onto the city's zone list: exact match,
    then the city's configured aliases, else the single zone whose
    "/"-separated parts contain it (case- and punctuation-insensitive), e.g.
    'Pasadena' -> 'Pasadena/San Gabriel', 'Los Feliz' -> 'Los Feliz/NELA'.
    None when ambiguous or unknown."""
    if not isinstance(name, str):
        return None
    if name in neighborhoods:
        return name
    key = _norm_venue(name)
    parts_by_zone = {z: [_norm_venue(part) for part in z.split("/")] + [_norm_venue(z)]
                     for z in neighborhoods}
    exact = [z for z, parts in parts_by_zone.items() if key in parts]
    if len(exact) == 1:
        return exact[0]
    alias = zone_aliases(city_key).get(key)
    if alias in neighborhoods:
        return alias
    loose = [z for z, parts in parts_by_zone.items()
             if any(key and len(key) >= 4 and (key in part or part in key) for part in parts)]
    return loose[0] if len(loose) == 1 else None


def record_venue(args: dict, city_key: str, neighborhoods: list[str], session: str) -> str:
    """Append one venue to the city's durable directory (enumeration pass)."""
    if _norm_venue(str(args.get("city", ""))) not in (_norm_venue(city_key), _norm_venue(city_key.replace("-", " "))):
        raise ValueError(f"city must be '{city_key}'")
    zone = normalize_zone(args.get("neighborhood"), neighborhoods, city_key)
    if zone is None:
        raise ValueError(
            f"neighborhood '{args['neighborhood']}' is not one of {neighborhoods}")
    args = {**args, "neighborhood": zone}
    if SANDBOX_DIR is None:
        # Known venue in this zone: enforce the ALREADY KNOWN list instead of
        # trusting the prompt (repeat searches for known names wasted whole
        # enumeration budgets).
        try:
            import venues
            known = venues.find_venue(venues.load_registry(city_key), args["name"],
                                      args.get("website"), add_alias=False)
        except Exception:
            known = None
        given = venues.registrable_domain(args.get("website")) if known is not None else None
        same_site = (given is None
                     or given == venues.registrable_domain(known.get("website")))
        if (known is not None and known.get("neighborhood") == zone
                and known.get("website") and same_site):
            return json.dumps({"result": "already_known", "venue": known["name"],
                               "venue_id": known["id"],
                               "note": "already in the directory — do not search for it again"})
        # a different website under a colliding name: fall through and let the
        # registry merge the agent's facts (name-keyed ids can be shadowed by
        # a generic Places listing such as "Park view gallery")
    entry = {
        "ts": int(time.time()), "session": session, "city": city_key,
        "name": args["name"], "neighborhood": args["neighborhood"],
        "kind": args["kind"], "address": args.get("address"),
        "website": args.get("website"), "note": args.get("note"),
    }
    _append_jsonl(_directory_file(city_key), entry)
    _registry_hook("on_record_venue", entry, city_key)
    zone_count = sum(1 for v in load_directory(city_key).values()
                     if v["neighborhood"] == args["neighborhood"])
    return json.dumps({"result": "recorded", "venue": args["name"], "zone_count": zone_count})


def load_directory(city_key: str) -> dict[str, dict]:
    """The city's venue directory: latest record per normalized venue name.
    Once the venue registry exists (content/venues/<city>.json) it is the
    directory — same dict shape, plus venue_id/status/exhibitions_url."""
    out: dict[str, dict] = {}
    p = _directory_file(city_key)
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[_norm_venue(e["name"])] = e
    if SANDBOX_DIR is None:
        try:
            import venues
            if venues.registry_exists(city_key):
                out.update(venues.directory_view(city_key))
        except Exception:
            pass
    return out


def load_skips(city_key: str) -> list[dict]:
    """All log_skip entries for a city, oldest first."""
    out = []
    if SKIPS_FILE.exists():
        for line in SKIPS_FILE.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("city") == city_key:
                out.append(e)
    return out


def sweep_placements(cities: list[str], today: date | None = None) -> dict:
    """Deterministic, API-free publication sweep run before each verify pass:
    demote published shows now outside the window (ended, or opening too far
    out); promote pending shows that entered the window already holding a
    fresh verified verdict with resolved coordinates. This is what lets an
    unattended cron loop retire and release shows on time by itself."""
    today = today or date.today()
    verdicts = latest_verdicts()
    actions: dict = {"demoted": [], "promoted": []}
    for c in cities:
        lock_path = CONTENT_DIR / f".{c}.json.lock"
        with open(lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            main = _load_shows_file(_city_file(c))
            pending = _load_shows_file(_pending_file(c))
            changed = False
            for s in list(main["shows"]):
                if not show_in_window(s, today) or not dates_ok(s):
                    main["shows"].remove(s)
                    pending["shows"].append(s)
                    changed = True
                    reason = ("needs dates" if not dates_ok(s)
                              else "ended" if show_expired(s, today)
                              else f"opens more than {OPEN_WINDOW_DAYS} days out")
                    actions["demoted"].append({"city": c, "slug": s["slug"], "reason": reason})
                    _registry_hook("on_sweep_demoted", c, s["slug"], reason)
            for s in list(pending["shows"]):
                if (show_in_window(s, today)
                        and awaiting_window_only(c, s, verdicts)):
                    pending["shows"].remove(s)
                    main["shows"].append(s)
                    changed = True
                    actions["promoted"].append({"city": c, "slug": s["slug"]})
            if changed:
                _write_shows_file(_city_file(c), main)
                _write_shows_file(_pending_file(c), pending)
    return actions


GEOCODE_OK_TYPES = {"ROOFTOP", "RANGE_INTERPOLATED", "GEOMETRIC_CENTER"}


def _geocode_address(key: str, address: str, city_name: str) -> dict | None:
    """Google Geocoding API for a street address, failing closed.

    Accepts only a precise result (no APPROXIMATE locality fallbacks) whose
    formatted address still contains every digit group of the stored street
    line — the geocoder invents partial matches on other streets otherwise.
    partial_match alone is not a rejection: it also fires on unmatched
    building-name prefixes (e.g. "Koyanagi Bldg.") over a correct pin.
    """
    import crosscheck  # deferred: crosscheck imports tools at module load
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": f"{address}, {city_name}", "key": key},
        timeout=20).json()
    if resp.get("status") != "OK" or not resp.get("results"):
        return None
    res = resp["results"][0]
    geo = res.get("geometry", {})
    loc = geo.get("location", {})
    if geo.get("location_type") not in GEOCODE_OK_TYPES or loc.get("lat") is None:
        return None
    stored = crosscheck._digit_groups(address)
    found = crosscheck._digit_groups(res.get("formatted_address", ""))
    if not stored or not all(d in found for d in stored):
        return None
    return {"lat": loc["lat"], "lng": loc["lng"],
            "precision": geo["location_type"],
            "formatted": res.get("formatted_address")}


def resolve_venue_coords(venue: dict, city_key: str) -> str | None:
    """Deterministically resolve venue lat/lng; agent-supplied pins never ship.

    Authority rules (each validated against a real failure this pipeline hit):
    - Geocoded address and matching listing agreeing (<=250m): use the geocode
      (rooftop precision).
    - They disagree: use the LISTING. A geocoder only places an address
      string, and metro areas reuse street names/numbers (2525 Michigan Ave
      exists in Santa Monica and East LA; Ropac Pantin's street also exists in
      Paris 14e). The wrong-branch listing danger is fenced off separately:
      another branch's address fails listing_matches_venue's digit check, so
      a matching listing is the venue's own space.
    - Only one side resolves: use it.
    Returns a note on success, None when unresolved (the caller nulls the
    coordinates and the show stays in the pending pool). Never raises.
    """
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    try:
        import crosscheck
        from cities import CITIES
        city_name = CITIES[city_key]["display_name"]
        prior = (venue.get("latitude"), venue.get("longitude"))
        geo = _geocode_address(key, venue["address"], city_name)
        listing = crosscheck.google_lookup(key, venue["name"], venue["address"],
                                           city_name)
        listing_ok = crosscheck.listing_matches_venue(venue, listing)
        if geo and listing_ok:
            gap = round(crosscheck.haversine_m(
                geo["lat"], geo["lng"], listing["lat"], listing["lng"]))
            if gap <= 250:
                venue["latitude"], venue["longitude"] = geo["lat"], geo["lng"]
                note = f"coords geocoded from address ({geo['precision']}, listing agrees)"
            else:
                venue["latitude"], venue["longitude"] = listing["lat"], listing["lng"]
                note = (f"coords from the venue's Google listing; WARNING: geocoding the "
                        f"address alone landed {gap}m away ({geo['formatted']}) — "
                        "double-check the address names the right street and city")
        elif geo:
            # No listing to corroborate; ask OSM. Micro-addresses (Venice
            # sestiere numbering) can geocode ROOFTOP onto the wrong building,
            # so an uncorroborated geocode ships with a warning for the verify
            # pass — and the crosscheck flags it if a listing disagrees later.
            venue["latitude"], venue["longitude"] = geo["lat"], geo["lng"]
            note = f"coords geocoded from address ({geo['precision']}; no matching listing"
            osm = crosscheck.nominatim_geocode(venue["address"], city_name)
            if osm.get("found"):
                osm_gap = round(crosscheck.haversine_m(
                    geo["lat"], geo["lng"], osm["lat"], osm["lng"]))
                note += f"; OSM {'agrees' if osm_gap <= 500 else f'disagrees by {osm_gap}m — WARNING: verify the address is unambiguous'})"
            else:
                note += "; no OSM corroboration — WARNING: verify the address is unambiguous)"
        elif listing_ok:
            venue["latitude"], venue["longitude"] = listing["lat"], listing["lng"]
            note = "coords from the venue's Google listing (address did not geocode)"
        else:
            return None
        if prior[0] is not None and prior[1] is not None:
            moved = round(crosscheck.haversine_m(
                prior[0], prior[1], venue["latitude"], venue["longitude"]))
            if moved > 25:
                note += f" (moved {moved}m)"
        return note
    except Exception:
        return None


def save_show(record: dict, city_key: str, neighborhoods: list[str]) -> str:
    """Validate and append a show record to content/<city>.json."""
    problems = []
    if record["city"] != city_key:
        problems.append(f"city must be '{city_key}'")
    for img in record["images"]:
        if not (_content_root() / img).is_file():
            problems.append(f"image not found on disk: {img}")
    if show_expired(record):
        problems.append("show has already ended (end_date is in the past)")
    for k in ("start_date", "end_date"):
        val = record.get(k)
        if val is not None and _parse_iso(val) is None:
            problems.append(f"{k} must be ISO YYYY-MM-DD or null (got {val!r}); for a month-only "
                            "listing use the month's first/last day and explain in dates_note")
    note = record.get("dates_note")
    if record.get("end_date") is None and not (isinstance(note, str) and note.strip()):
        problems.append("end_date is null but dates_note is empty — quote what the venue's page "
                        "shows about dates (or pass the exact closing date)")
    start = _parse_iso(record.get("start_date"))
    if start and start > date.today() + timedelta(days=FUTURE_SAVE_DAYS):
        problems.append(
            f"show opens more than {FUTURE_SAVE_DAYS} days out — too far ahead to save; "
            "log_skip it with reason 'closed_or_between_shows' instead")
    zone = normalize_zone(record["venue"].get("neighborhood"), neighborhoods, city_key)
    if zone is None:
        problems.append(
            f"neighborhood '{record['venue']['neighborhood']}' is not one of {neighborhoods}"
        )
    else:
        record["venue"]["neighborhood"] = zone
    if len(record["description"].split()) < 60:
        problems.append("description is too short — write 2-4 substantial paragraphs")
    if problems:
        raise ValueError("Not saved. Fix and retry: " + "; ".join(problems))
    record.setdefault("dates_note", None)
    _set_dates_meta(record, int(time.time()))   # saving = the venue confirmed it today

    if SANDBOX_DIR is None:
        coord_note = resolve_venue_coords(record["venue"], city_key)
    else:
        record["venue"]["latitude"] = record["venue"]["longitude"] = None
        coord_note = "sandbox: coordinate resolution skipped"
    if coord_note is None:
        # Agent-supplied pins never ship; unresolved coordinates block promotion.
        record["venue"]["latitude"] = record["venue"]["longitude"] = None

    # Venue identity for the registry (harness-side; not part of the agent's
    # schema). The embedded venue object stays exactly as the apps expect.
    vid = _registry_hook("resolve_id", city_key, record["venue"]["name"],
                         record["venue"].get("website"))
    if vid:
        record["venue_id"] = vid

    # New and re-saved shows always land in the pending pool; only a verify
    # pass promotes them to the published <city>.json that the apps display.
    # Exclusive lock: parallel neighborhood-shard sessions of one city share it.
    lock_path = CONTENT_DIR / f".{city_key}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main = _load_shows_file(_city_file(city_key))
        pending = _load_shows_file(_pending_file(city_key))
        # A venue may hold any number of concurrent shows; only the SAME show
        # (same venue + same normalized title) under a different slug is a
        # duplicate (parallel shards never see each other's saves mid-flight).
        # Same-slug re-saves still upsert.
        new_key = _norm_venue(record["venue"]["name"])
        new_title = _norm_title(record.get("title"))
        clash = next((s for s in main["shows"] + pending["shows"]
                      if s["slug"] != record["slug"] and not show_expired(s)
                      and _norm_venue(s["venue"]["name"]) == new_key
                      and _norm_title(s.get("title")) == new_title), None)
        if clash:
            raise ValueError(
                f"Not saved: this show is already saved at '{record['venue']['name']}' as "
                f"slug '{clash['slug']}' — re-save under that slug to update it, or move on "
                "to the venue's OTHER current shows.")
        was_published = any(s["slug"] == record["slug"] for s in main["shows"])
        if was_published:
            main["shows"] = [s for s in main["shows"] if s["slug"] != record["slug"]]
            _write_shows_file(_city_file(city_key), main)
        if any(s["slug"] == record["slug"] for s in pending["shows"]):
            pending["shows"] = [record if s["slug"] == record["slug"] else s
                                for s in pending["shows"]]
            action = "updated"
        else:
            pending["shows"].append(record)
            action = "updated (moved back to pending)" if was_published else "saved"
        _write_shows_file(_pending_file(city_key), pending)
    _registry_hook("on_save_show", record, city_key,
                   getattr(SESSION, "label", None), SESSION, "pending")
    # Contract ledger: a save only resolves its TODO venue when the show is on
    # view NOW (null start counts as open); an upcoming show does not.
    upcoming = start is not None and start > date.today()
    _note_resolution(record["venue"]["name"], "upcoming" if upcoming else "current",
                     venue_id=record.get("venue_id"))
    result = {"result": action,
              "queue": "pending verification (displayed only after a verify pass confirms it)",
              "shows_saved_for_city": len(main["shows"]) + len(pending["shows"])}
    result["note"] = coord_note or (
        "coordinates could not be resolved from this address — the show is saved "
        "but cannot be published until the address geocodes; double-check it")
    if not show_in_window(record):
        result["window_note"] = (
            f"show opens more than {OPEN_WINDOW_DAYS} days out; it will be "
            "published automatically once within the window")
    if not dates_ok(record):
        result["dates_note"] = ("saved without exact dates; held in pending until a verify "
                                "pass fills them (re-checked weekly) — no action needed now")
    if upcoming:
        result["venue_note"] = ("this is an UPCOMING show; the venue still needs its CURRENT "
                                "show saved, or a log_skip closed_or_between_shows noting the "
                                "opening date if nothing is on view now")
    return json.dumps(result)


# --- rendered fetch (JS-only pages) --------------------------------------------

RENDER_SCRIPT = Path(__file__).resolve().parent / "render_fetch.js"
RENDER_MAX_CHARS = 12000
RENDER_TIMEOUT_S = 45
_ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _scan_dates(text: str, html_: str, limit: int = 40) -> list[str]:
    """Every date-looking string in the rendered text AND the raw HTML (script
    payloads such as Next.js RSC data carry exact ISO dates the visible page
    only renders client-side), each with a little context."""
    import refresh  # deferred: refresh imports tools at module load
    out: list[str] = []
    seen: set[str] = set()
    for src, label in ((text or "", "text"), (html_ or "", "html")):
        for m in list(_ISO_DATE_RE.finditer(src)) + list(refresh.DATE_RE.finditer(src)):
            key = m.group(0).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            ctx = re.sub(r"\s+", " ", src[max(0, m.start() - 60): m.end() + 60]).strip()
            out.append(f"{m.group(0).strip()}  [{label}: …{ctx}…]")
            if len(out) >= limit:
                return out
    return out


def render_fetch(url: str, max_chars: int = RENDER_MAX_CHARS) -> str:
    """Load a page in a headless browser (Playwright via node) and return its
    rendered text plus every date string found in text or raw HTML. Never
    raises: failures come back as an actionable {"error": ...}."""
    import subprocess
    decide = (" — decide from web_fetch instead; if the venue's own page confirms the show "
              "but shows no dates, save it with end_date null and a dates_note")
    if not RENDER_SCRIPT.exists():
        return json.dumps({"error": "render_fetch unavailable (render_fetch.js missing)" + decide})
    env = {**os.environ,
           "NODE_PATH": os.environ.get("GALLERY_NODE_PATH", "/opt/homebrew/lib/node_modules")}
    try:
        proc = subprocess.run(["node", str(RENDER_SCRIPT), url, str(max_chars)],
                              capture_output=True, text=True, timeout=RENDER_TIMEOUT_S, env=env)
    except FileNotFoundError:
        return json.dumps({"error": "render_fetch unavailable (node/Chrome missing)" + decide})
    except subprocess.TimeoutExpired:
        return json.dumps({"error": f"renderer timed out after {RENDER_TIMEOUT_S}s; treat the "
                                    "page as unreadable" + decide})
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    data = None
    if lines:
        try:
            data = json.loads(lines[-1])
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        tail = (proc.stderr or proc.stdout or "")[-300:].strip()
        return json.dumps({"error": f"renderer failed: {tail or 'no output'}" + decide})
    if data.get("error"):
        return json.dumps({"error": f"renderer error: {data['error']}" + decide})
    html_ = data.pop("html", "") or ""
    text = data.get("text") or ""
    result = {
        "final_url": data.get("final_url") or url,
        "renderer": data.get("renderer"),
        "title": data.get("title"),
        "text": text[:max_chars],
        "time_elements": (data.get("times") or [])[:40],
        "date_strings": _scan_dates(text, html_),
        "note": "date_strings scans the rendered text AND raw HTML/script data (JSON payloads "
                "often carry the exact opening/closing dates the page renders client-side)",
    }
    if SESSION is not None:
        SESSION.add_url(result["final_url"], "render_fetch")
        _registry_hook("write_evidence", SESSION.city, result["final_url"], "render",
                       json.dumps(result, ensure_ascii=False), SESSION.label)
    return json.dumps(result, ensure_ascii=False)
