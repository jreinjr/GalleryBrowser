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
# one-show-per-venue rule against the live pools.
SANDBOX_DIR: Path | None = None


def set_sandbox(path: str | Path | None) -> None:
    global SANDBOX_DIR
    SANDBOX_DIR = Path(path).resolve() if path else None
    if SANDBOX_DIR:
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)


def _content_root() -> Path:
    return SANDBOX_DIR or CONTENT_DIR

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


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60] or "untitled"


_NORM_STRIP_SUFFIXES = (" gallery", " galleries", " fine art", " fine arts")


def _norm_venue(name: str) -> str:
    """Normalized venue-name key shared by the one-show-per-venue dedup, the
    venue directory, and TODO-list matching. Conservative on purpose: a false
    negative (two spellings of one venue) is advisory-list territory, a false
    positive would wrongly block a save."""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    for suf in _NORM_STRIP_SUFFIXES:
        if s.endswith(suf) and len(s) > len(suf) + 2:
            s = s[: -len(suf)]
            break
    return s.strip()


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
    note = "high-res" if width >= GOOD_WIDTH else "acceptable but below ideal resolution"
    return json.dumps(
        {"stored": rel, "original_px": [width, height], "quality": note, "images_for_show": idx}
    )


SAVE_SHOW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "city", "slug", "title", "artist", "start_date", "end_date", "description",
        "editors_pick", "featured", "reception", "images", "source_urls", "venue",
    ],
    "properties": {
        "city": {"type": "string", "description": "City key, e.g. 'seattle'"},
        "slug": {"type": "string", "description": "Short kebab-case id unique within the city; must match the show_slug used when downloading this show's images"},
        "title": {"type": "string", "description": "Exhibition title (shown in italics)"},
        "artist": {"type": ["string", "null"], "description": "Artist name(s), or null when the exhibition title stands alone (e.g. a themed group show)"},
        "start_date": {"type": "string", "description": "ISO date YYYY-MM-DD"},
        "end_date": {"type": "string", "description": "ISO date YYYY-MM-DD (last day on view)"},
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
        publish = args["status"] in ("verified", "corrected") and coords_ok and in_window
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
    entry = {"ts": int(_time.time()), "city": city_key, "slug": slug,
             "status": args["status"], "reason": args.get("reason"),
             "applied": applied, "placement": placement}
    with open(VERIFY_RESULTS, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    result = {"result": args["status"], "slug": slug,
              "corrections_applied": applied, "placement": placement}
    if args["status"] in ("verified", "corrected") and not coords_ok:
        result["note"] = ("kept in pending: coordinates could not be resolved from the "
                         "address — if the venue's site shows a more standard street "
                         "address, call confirm_show again with it as a correction")
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


def _parse_iso(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def show_expired(show: dict, today: date | None = None) -> bool:
    today = today or date.today()
    end = _parse_iso(show.get("end_date"))
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
                and show["venue"].get("longitude") is not None)


def pending_reason(city_key: str, show: dict, verdicts: dict | None = None) -> str:
    """One line on why a pending show isn't published (report/summary use)."""
    verdicts = verdicts if verdicts is not None else latest_verdicts()
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
                "appointment_only", "out_of_scope", "duplicate", "other"]


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
            "description": "no_image: no downloadable image at all; low_res_only: images exist but none reach 500px wide; unverifiable: venue/show could not be confirmed on a primary source; closed_or_between_shows: venue operating but nothing on view and no confirmed upcoming show; appointment_only: no public walk-in hours; out_of_scope: not an art-viewing venue (or outside this city's scope); duplicate: venue already has a saved show (possibly under another name); other: explain in detail",
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
    url = args.get("url")
    _append_jsonl(SKIPS_FILE, {
        "ts": int(time.time()), "session": session, "city": city_key,
        "venue": args["venue"], "neighborhood": args["neighborhood"],
        "reason": reason, "detail": detail,
        "url": url if isinstance(url, str) else None,
    })
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


def record_venue(args: dict, city_key: str, neighborhoods: list[str], session: str) -> str:
    """Append one venue to the city's durable directory (enumeration pass)."""
    if args["city"] != city_key:
        raise ValueError(f"city must be '{city_key}'")
    if args["neighborhood"] not in neighborhoods:
        raise ValueError(
            f"neighborhood '{args['neighborhood']}' is not one of {neighborhoods}")
    _append_jsonl(_directory_file(city_key), {
        "ts": int(time.time()), "session": session, "city": city_key,
        "name": args["name"], "neighborhood": args["neighborhood"],
        "kind": args["kind"], "address": args.get("address"),
        "website": args.get("website"), "note": args.get("note"),
    })
    zone_count = sum(1 for v in load_directory(city_key).values()
                     if v["neighborhood"] == args["neighborhood"])
    return json.dumps({"result": "recorded", "venue": args["name"], "zone_count": zone_count})


def load_directory(city_key: str) -> dict[str, dict]:
    """The city's venue directory: latest record per normalized venue name."""
    out: dict[str, dict] = {}
    p = _directory_file(city_key)
    if p.exists():
        for line in p.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            out[_norm_venue(e["name"])] = e
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
                if not show_in_window(s, today):
                    main["shows"].remove(s)
                    pending["shows"].append(s)
                    changed = True
                    actions["demoted"].append({
                        "city": c, "slug": s["slug"],
                        "reason": "ended" if show_expired(s, today)
                        else f"opens more than {OPEN_WINDOW_DAYS} days out"})
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
        if not (CONTENT_DIR / img).is_file():
            problems.append(f"image not found on disk: {img}")
    if show_expired(record):
        problems.append("show has already ended (end_date is in the past)")
    start = _parse_iso(record.get("start_date"))
    if start and start > date.today() + timedelta(days=FUTURE_SAVE_DAYS):
        problems.append(
            f"show opens more than {FUTURE_SAVE_DAYS} days out — too far ahead to save; "
            "log_skip it with reason 'closed_or_between_shows' instead")
    if record["venue"]["neighborhood"] not in neighborhoods:
        problems.append(
            f"neighborhood '{record['venue']['neighborhood']}' is not one of {neighborhoods}"
        )
    if len(record["description"].split()) < 60:
        problems.append("description is too short — write 2-4 substantial paragraphs")
    if problems:
        raise ValueError("Not saved. Fix and retry: " + "; ".join(problems))

    if SANDBOX_DIR is None:
        coord_note = resolve_venue_coords(record["venue"], city_key)
    else:
        record["venue"]["latitude"] = record["venue"]["longitude"] = None
        coord_note = "sandbox: coordinate resolution skipped"
    if coord_note is None:
        # Agent-supplied pins never ship; unresolved coordinates block promotion.
        record["venue"]["latitude"] = record["venue"]["longitude"] = None

    # New and re-saved shows always land in the pending pool; only a verify
    # pass promotes them to the published <city>.json that the apps display.
    # Exclusive lock: parallel neighborhood-shard sessions of one city share it.
    lock_path = CONTENT_DIR / f".{city_key}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main = _load_shows_file(_city_file(city_key))
        pending = _load_shows_file(_pending_file(city_key))
        # One show per venue, enforced (the ALREADY SAVED prompt list is
        # advisory only, and parallel shards never see each other's saves
        # mid-flight). Same-slug re-saves still upsert.
        new_key = _norm_venue(record["venue"]["name"])
        clash = next((s for s in main["shows"] + pending["shows"]
                      if s["slug"] != record["slug"]
                      and _norm_venue(s["venue"]["name"]) == new_key), None)
        if clash:
            raise ValueError(
                f"Not saved: venue '{record['venue']['name']}' already has saved show "
                f"'{clash['slug']}' — one show per venue. If this venue was on your TODO "
                "list, call log_skip with reason 'duplicate' and move on.")
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
    return json.dumps(result)
