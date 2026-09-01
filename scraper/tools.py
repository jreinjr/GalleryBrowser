"""Client-side tools for the gallery scraper agent: image URL extraction,
image download/normalization, and validated show persistence."""

from __future__ import annotations

import fcntl
import io
import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"
IMAGES_DIR = CONTENT_DIR / "images"

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

    show_dir = IMAGES_DIR / city / _slugify(show_slug)
    show_dir.mkdir(parents=True, exist_ok=True)
    idx = len(list(show_dir.glob("*.jpg"))) + 1
    path = show_dir / f"{idx:02d}.jpg"
    img.save(path, "JPEG", quality=88, optimize=True)

    rel = str(path.relative_to(CONTENT_DIR))
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
                          "hours", "phone", "website", "latitude", "longitude"],
            "properties": {
                "name": {"type": "string"},
                "is_museum": {"type": "boolean"},
                "address": {"type": "string", "description": "Street address, e.g. '212 Third Ave S'"},
                "address_detail": {"type": ["string", "null"], "description": "Suite/floor, or null"},
                "neighborhood": {"type": "string", "description": "One of the city's configured neighborhoods"},
                "hours": {"type": "array", "items": {"type": "string"}, "description": "Lines like 'Tue - Sat 10:30am to 5:30pm'"},
                "phone": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
                "latitude": {"type": "number"},
                "longitude": {"type": "number"},
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
                          "phone", "website", "latitude", "longitude", "reception"],
            "description": "Only for status=corrected: pass the corrected values, null for every field that is already right",
            "properties": {
                "start_date": {"type": ["string", "null"]},
                "end_date": {"type": ["string", "null"]},
                "hours": {"type": ["array", "null"], "items": {"type": "string"}},
                "address": {"type": ["string", "null"]},
                "address_detail": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
                "website": {"type": ["string", "null"]},
                "latitude": {"type": ["number", "null"]},
                "longitude": {"type": ["number", "null"]},
                "reception": {"type": ["string", "null"]},
            },
        },
    },
}

VERIFY_RESULTS = CONTENT_DIR / "spend" / "verify_results.jsonl"

CORRECTABLE_VENUE_FIELDS = ("hours", "address", "address_detail", "phone", "website",
                             "latitude", "longitude")
CORRECTABLE_SHOW_FIELDS = ("start_date", "end_date", "reception")


def confirm_show(args: dict, city_key: str) -> str:
    """Record a verification verdict; apply corrections to the saved record."""
    import time as _time
    if args["city"] != city_key:
        raise ValueError(f"city must be '{city_key}'")
    slug = args["slug"]
    out_path = CONTENT_DIR / f"{city_key}.json"
    lock_path = CONTENT_DIR / f".{city_key}.json.lock"
    applied = []
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(out_path.read_text())
        matching = [s for s in data["shows"] if s["slug"] == slug]
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
            # An agent-corrected address or pin is still model-memory geography;
            # re-anchor to the Google listing for the (possibly new) address.
            if {"address", "latitude", "longitude"} & set(applied):
                if _snap_venue_coords(show["venue"], city_key):
                    applied.append("coords_snapped_to_google")
            out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    entry = {"ts": int(_time.time()), "city": city_key, "slug": slug,
             "status": args["status"], "reason": args.get("reason"), "applied": applied}
    with open(VERIFY_RESULTS, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return json.dumps({"result": args["status"], "slug": slug, "corrections_applied": applied})


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


def _snap_venue_coords(venue: dict, city_key: str) -> str | None:
    """Replace agent-supplied lat/lng with the Google Places listing pin when
    the listing confidently matches the venue (crosscheck.listing_matches_venue).

    Agent coordinates come from model memory (venue sites don't publish them)
    and are wrong at block scale often enough that map pins can't ship without
    this. Never raises; on any failure the agent's coordinates stand.
    """
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        return None
    try:
        import crosscheck  # deferred: crosscheck imports tools at module load
        from cities import CITIES
        g = crosscheck.google_lookup(key, venue["name"], venue["address"],
                                     CITIES[city_key]["display_name"])
        if crosscheck.listing_matches_venue(venue, g):
            moved = round(crosscheck.haversine_m(
                venue["latitude"], venue["longitude"], g["lat"], g["lng"]))
            venue["latitude"], venue["longitude"] = g["lat"], g["lng"]
            if moved > 25:
                return f"venue pin snapped to Google Places listing (moved {moved}m)"
    except Exception:
        pass
    return None


def save_show(record: dict, city_key: str, neighborhoods: list[str]) -> str:
    """Validate and append a show record to content/<city>.json."""
    problems = []
    if record["city"] != city_key:
        problems.append(f"city must be '{city_key}'")
    for img in record["images"]:
        if not (CONTENT_DIR / img).is_file():
            problems.append(f"image not found on disk: {img}")
    if record["venue"]["neighborhood"] not in neighborhoods:
        problems.append(
            f"neighborhood '{record['venue']['neighborhood']}' is not one of {neighborhoods}"
        )
    if len(record["description"].split()) < 60:
        problems.append("description is too short — write 2-4 substantial paragraphs")
    if problems:
        raise ValueError("Not saved. Fix and retry: " + "; ".join(problems))

    snap_note = _snap_venue_coords(record["venue"], city_key)

    out_path = CONTENT_DIR / f"{city_key}.json"
    # Exclusive lock: parallel neighborhood-shard sessions of one city share this file.
    lock_path = CONTENT_DIR / f".{city_key}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = {"shows": []}
        if out_path.exists():
            data = json.loads(out_path.read_text())
        existing = [s for s in data["shows"] if s["slug"] == record["slug"]]
        if existing:
            data["shows"] = [record if s["slug"] == record["slug"] else s for s in data["shows"]]
            action = "updated"
        else:
            data["shows"].append(record)
            action = "saved"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    result = {"result": action, "shows_saved_for_city": len(data["shows"])}
    if snap_note:
        result["note"] = snap_note
    return json.dumps(result)
