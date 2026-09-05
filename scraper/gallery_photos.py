"""Gallery photos: up to five photographs of each gallery *as a place* (facade,
entrance, interior, installation views where the room is the subject), the
first one the most representative. Two sources, cheapest first:

  site    the venue's own website: og:image plus hero images from the homepage
          and its about / contact pages (the dossier's ``triage.labels`` when a
          research report exists, else /about, /contact, ... probes). $0, and
          durable: these ship in the app the way show images do.
  google  Google Places (New) photos. Photo *metadata* (field mask ``id,photos``)
          is free; each media download is one "Place Details Photos" event
          ($7 / 1,000 after 1,000 free per month). Google's terms cap caching at
          30 days and require attribution, so these are fetched, judged and kept
          flagged ``provider: google`` for a later opt-in; the web build ships
          site photos only by default (build.py --venue-photos).

One Claude call per venue (every candidate downscaled to 768 px, JSON verdict)
picks and orders the photos. Stages, each resumable from the venue's
``_work.json``:

    python gallery_photos.py candidates --city tokyo --top 50 [--no-google] [--venue-ids a b]
    python gallery_photos.py fetch      --city tokyo --top 50 [--google-per-venue 5] [--google-cap 400] [--dry-run]
    python gallery_photos.py judge      --city tokyo --top 50 [--model claude-sonnet-5] [--budget 3] [--force]
    python gallery_photos.py apply      --city tokyo --top 50 [--keep-candidates]
    python gallery_photos.py run        --city tokyo --top 50 --budget 3 --google-cap 400

Writes ``content/images/venues/<city>/<venue_id>/NN.jpg`` (the picks only;
candidates and the per-venue ``_work.json`` live in the gitignored
``scraper/.cache/venue-photos/<city>/<venue_id>/`` so a ``judge --force`` never
re-fetches), ``venue.photos`` in the registry (the only registry key this script
touches, so it may run on Los Angeles, which quick_city.py never writes), a spend ledger
``content/spend/gallery-photos-<city>-<ts>.json`` and the monthly Google photo
event tally ``content/spend/google-photo-events.json``. Runs inside WSL on
Windows (tools.py needs fcntl).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crosscheck  # noqa: E402
import harness  # noqa: E402
import refresh  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
import verify_order  # noqa: E402
from cities import CITIES  # noqa: E402
from run_scrape import load_env  # noqa: E402

# --- Google Places (New) -----------------------------------------------------------
PLACES_BASE = "https://places.googleapis.com/v1"
# id + displayName + location: the Pro SKU (5,000 free / month) — one call resolves
# and verifies a place, no Details round-trip
TEXT_SEARCH_FIELDS = "places.id,places.displayName,places.location"
PHOTOS_FIELDS = "id,photos"                      # "Essentials IDs Only": free, unlimited
GOOGLE_MAX_PX = 4800                             # media endpoint max; returns the original when smaller
PHOTO_PRICE_USD = 7.00 / 1000.0                  # "Place Details Photos" SKU, 0-100K tier
PHOTO_FREE_PER_MONTH = 1000
SEARCH_BIAS_M = 500.0
MATCH_RADIUS_M = 150.0                           # Places-sourced coordinates on file
MATCH_RADIUS_LOOSE_M = 300.0                     # coordinates from a show page / geocoder only
PHOTO_META_TTL_S = 30 * 86400                    # Google: cache non-id content <= 30 days
# Legacy Places endpoints, used when the key's API restrictions block Places API
# (New) (error reason API_KEY_SERVICE_BLOCKED) — the state of this project's key
# in 2026-09; crosscheck.py falls back the same way. Legacy Place Photo caps
# maxwidth at 1600 px and legacy Details bills the request itself.
LEGACY_BASE = "https://maps.googleapis.com/maps/api/place"
LEGACY_MAX_PX = 1600
ATTRIB_RE = re.compile(r'<a href="([^"]*)">([^<]*)</a>')
_LEGACY = False                                  # flipped for the rest of the run on the first block

# --- site ------------------------------------------------------------------------
SITE_PAGE_PATHS = ("/about", "/about-us", "/contact", "/visit", "/info", "/gallery", "/space")
SITE_PAGE_MAX = 3                                # pages after the homepage
SITE_CANDIDATE_CAP = 12
IMAGE_BODY_CAP = 12_000_000
EXTRA_SKIP = re.compile(r"wordmark|signature|emoji|tracking|gravatar|spacer|/flags?/"
                        r"|\.svg(?:[?#]|$)|\.gif(?:[?#]|$)", re.I)
WP_VARIANT = re.compile(r"-(\d{2,4})x(\d{2,4})(\.(?:jpe?g|png|webp))(?=[?#]|$)", re.I)
LANG_PATH = re.compile(r"/(?!en\b)[a-z]{2}(?:-[a-z]{2})?/", re.I)

# --- judge -----------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-5"
GOOGLE_PER_VENUE = 5
GOOGLE_META_KEEP = 10
MAX_PICKS = 5
JUDGE_MAX_SIDE = 768
JUDGE_MAX_TOKENS = 1500
JUDGE_OVERHEAD_TOKENS = 400
DUP_HAMMING = 4                                  # dhash bits; resizes/re-encodes land within 1-2
BANNER_ASPECT = 3.5                              # wider/taller than this = logo strip, not a photo

JUDGE_SYSTEM = """You select photographs of an art gallery as a PLACE for a city guide's venue page.

You receive candidate images labelled [C1]..[Cn]. Each label states the provider (site = the gallery's own website, google = a Google Maps photo), the page it came from, its alt text and its pixel size.

Pick up to 5 images that show the gallery itself: its exterior or facade, entrance or signage, interior architecture, or an installation view where the room is the subject rather than one artwork. Order them: rank 1 is the single most representative image - prefer a clear facade or a wide, well-lit interior of good photographic quality, with nobody in it. Ranks 2-5 add variety (exterior + interior beats two near-identical interiors).

Reject: logos and wordmarks, cropped or close-up artworks, any image where people are a subject (portraits, artist talks, panels, openings, crowds, staff at a desk - a distant passer-by in a wide architectural shot is fine), event photos and video stills, posters, flyers, screenshots, text-heavy graphics, images visibly of a different venue, low quality (blurry, tiny, heavy watermark, heavy filters), and near-duplicates (keep the larger one). When a site image and a google image are equally good, prefer the site image.

Answer with JSON only, matching the schema. Every candidate id appears in exactly one of picks or rejects."""

Subject = Literal["exterior", "entrance", "interior", "installation", "other"]
Reason = Literal["logo", "artwork_only", "person", "duplicate", "low_quality", "not_this_venue",
                 "text_heavy", "other"]


class Pick(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    rank: int = Field(description="1 = most representative")
    subject: Subject
    caption: str = Field(description="At most 12 words, e.g. 'Street facade with signage'")


class Reject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    reason: Reason


class PhotoVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    picks: list[Pick]
    rejects: list[Reject]
    confidence: float = Field(description="0-1: how sure you are these show this venue")


VERDICT_SCHEMA = PhotoVerdict.model_json_schema()


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    """Hard caps for one invocation: dollars (judge) and Google photo events."""
    usd_cap: float | None = None
    google_cap: int = 400
    google_events: int = 0
    notes: list[str] = field(default_factory=list)

    def take_google(self) -> None:
        if self.google_events >= self.google_cap:
            raise BudgetExceeded(f"google photo cap ({self.google_cap} events) reached")
        self.google_events += 1

    def check_usd(self, spent: float, next_estimate: float = 0.0) -> None:
        if self.usd_cap is not None and spent + next_estimate > self.usd_cap:
            raise BudgetExceeded(f"budget ${self.usd_cap:.2f} would be exceeded "
                                 f"(spent ${spent:.3f}, next ~${next_estimate:.3f})")


# --- paths + work state --------------------------------------------------------------

CACHE_DIR = tools.CONTENT_DIR.parent / "scraper" / ".cache" / "venue-photos"


def venue_dir(city: str, vid: str) -> Path:
    """Where the picks (NN.jpg) live: git-tracked content."""
    return tools._content_root() / "images" / "venues" / city / vid


def cand_dir(city: str, vid: str) -> Path:
    """Where candidates (cand-NN.jpg) and _work.json live: the gitignored cache
    (tests: under the sandbox root instead)."""
    if tools.SANDBOX_DIR is not None:
        return tools.SANDBOX_DIR / "venue-photos" / city / vid
    return CACHE_DIR / city / vid


def rel_path(path: Path) -> str:
    return path.relative_to(tools._content_root()).as_posix()


def load_work(city: str, vid: str) -> dict:
    p = cand_dir(city, vid) / "_work.json"
    legacy = venue_dir(city, vid) / "_work.json"          # first runs kept it next to the picks
    if not p.exists() and legacy.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(p)
        for c in venue_dir(city, vid).glob("cand-*.jpg"):
            c.replace(p.parent / c.name)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"venue_id": vid, "ts": None, "site_pages": [], "candidates": [], "fetched": [],
            "verdict": None, "google_events": 0, "notes": []}


def save_work(city: str, vid: str, work: dict) -> None:
    d = cand_dir(city, vid)
    d.mkdir(parents=True, exist_ok=True)
    work["ts"] = int(time.time())
    (d / "_work.json").write_text(json.dumps(work, indent=1, ensure_ascii=False), encoding="utf-8")


def report_for(city: str, vid: str) -> dict | None:
    p = tools.CONTENT_DIR / "venues" / "reports" / city / f"{vid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


# --- selection ---------------------------------------------------------------------

def select_venues(reg: dict, top: int | None = None, ids: list[str] | None = None) -> list[dict]:
    """Explicit ids in the order given, else the registry's rank order (the app's
    only ranking signal) cut at ``top``."""
    if ids:
        by = venues.index_by_id(reg)
        return [by[i] for i in ids if i in by]
    ranked = sorted((v for v in reg.get("venues", []) if v.get("rank")), key=lambda v: v["rank"])
    return ranked[:top] if top else ranked


# --- site candidates ---------------------------------------------------------------

def report_pages(report: dict | None) -> list[str]:
    """about / contact_hours URLs from the research dossier's triage, English
    (or unlabelled-language) paths first, shortest first, capped."""
    labels = ((report or {}).get("triage") or {}).get("labels") or {}
    wanted = [u for u, lab in labels.items() if lab in ("about", "contact_hours")]
    wanted.sort(key=lambda u: (bool(LANG_PATH.search(urlparse(u).path)), len(u)))
    return wanted[:SITE_PAGE_MAX]


BOT_WALL = ("http_403", "http_429", "http_503")


def get_page(fetcher: refresh.Fetcher, url: str) -> dict:
    """fetcher.get, retried once with a browser UA when the site walls the bot
    UA (crawl.py's experience: a real browser often gets through)."""
    r = fetcher.get(url)
    if r["status"] != 200 and r["error"] in BOT_WALL:
        r = fetcher.get(url, user_agent=tools.UA)
    return r


def probe_pages(fetcher: refresh.Fetcher, website: str) -> list[tuple[str, str]]:
    """(url, html) for up to SITE_PAGE_MAX common about/contact paths that answer 200."""
    out = []
    for path in SITE_PAGE_PATHS:
        url = urljoin(website, path)
        r = get_page(fetcher, url)
        if r["status"] == 200 and r["html"]:
            out.append((r["final_url"], r["html"]))
            if len(out) >= SITE_PAGE_MAX:
                break
    return out


def parse_site_html(html: str, page_url: str) -> list[dict]:
    """Image candidates in one page: og:image, <img>/<source> (largest srcset
    entry), linked image files — via tools._ImgParser (which already drops
    logo/icon/favicon URLs), minus vector/animated files and, when the original
    is present too, WordPress ``-WxH`` size variants."""
    parser = tools._ImgParser(page_url)
    try:
        parser.feed(html)
    except Exception:
        pass
    by_url: dict[str, dict] = {}
    for item in parser.found:
        url = item["url"]
        if url in by_url or EXTRA_SKIP.search(url):
            continue
        m = WP_VARIANT.search(url)
        base = WP_VARIANT.sub(r"\3", url) if m else url
        hint = item["size_hint"] or (int(m.group(1)) if m else 0)
        kind = "og" if item["alt"] == "og:image" else (
            "link" if item["alt"] == "linked image file" else "img")
        by_url[url] = {"provider": "site", "url": url, "base": base, "source_page": page_url,
                       "kind": kind, "alt": item["alt"] if kind == "img" else "", "size_hint": hint}
    # one entry per base: the original when present, else the largest variant
    best: dict[str, dict] = {}
    for c in by_url.values():
        cur = best.get(c["base"])
        if cur is None or (c["url"] == c["base"]) or (
                cur["url"] != cur["base"] and c["size_hint"] > cur["size_hint"]):
            best[c["base"]] = c
    return [c for c in by_url.values() if best[c["base"]] is c]


def rank_site(cands: list[dict], cap: int = SITE_CANDIDATE_CAP) -> list[dict]:
    """og:image first (the image the gallery chose to represent itself), then by
    declared size, then document order; the same URL seen on several pages counts once."""
    seen, uniq = set(), []
    for c in cands:
        if c["url"] not in seen:
            seen.add(c["url"])
            uniq.append(c)
    order = {c["url"]: i for i, c in enumerate(uniq)}
    uniq.sort(key=lambda c: (c["kind"] != "og", -c["size_hint"], order[c["url"]]))
    return uniq[:cap]


def site_candidates(fetcher: refresh.Fetcher, city: str, venue: dict, work: dict) -> list[dict]:
    website = venue.get("website")
    if not website:
        work["notes"].append("no_website")
        return []
    pages: list[tuple[str, str]] = []
    home = get_page(fetcher, website)
    if home["status"] == 200 and home["html"]:
        pages.append((home["final_url"], home["html"]))
    else:
        work["notes"].append(f"homepage:{home['error'] or home['status']}")
    extra = report_pages(report_for(city, venue["id"]))
    if extra:
        for url in extra:
            r = get_page(fetcher, url)
            if r["status"] == 200 and r["html"]:
                pages.append((r["final_url"], r["html"]))
            else:
                work["notes"].append(f"page:{r['error'] or r['status']}")
    elif pages:
        pages.extend(probe_pages(fetcher, pages[0][0]))
    work["site_pages"] = [u for u, _ in pages]
    cands: list[dict] = []
    for url, html in pages:
        cands.extend(parse_site_html(html, url))
    if pages and not cands:
        work["notes"].append("js_only_or_no_images")
    return rank_site(cands)


# --- Google candidates -------------------------------------------------------------

def stored_place_id(venue: dict) -> str | None:
    return (((venue.get("sources") or {}).get("seed") or {}).get("places") or {}).get("place_id")


def venue_coords(venue: dict) -> tuple[float, float, float] | None:
    """(lat, lng, accept radius): Places coordinates on file are trusted to 150 m,
    anything else (show page, geocoder) to 300 m."""
    g = venue.get("google") or {}
    if g.get("lat") is not None and g.get("lng") is not None:
        return float(g["lat"]), float(g["lng"]), MATCH_RADIUS_M
    if venue.get("latitude") is not None and venue.get("longitude") is not None:
        return float(venue["latitude"]), float(venue["longitude"]), MATCH_RADIUS_LOOSE_M
    return None


def accept_place(cand: dict, venue: dict, city: str) -> bool:
    """A text-search hit is our venue when the name matches (verify_order's rule,
    the city's own words ignored) and, when we hold coordinates, it sits within
    the accept radius."""
    ignore = verify_order.words(CITIES[city]["display_name"])
    if not verify_order.name_matches(venue["name"], cand.get("name"), ignore):
        return False
    here = venue_coords(venue)
    if here and cand.get("lat") is not None and cand.get("lng") is not None:
        return crosscheck.haversine_m(here[0], here[1], cand["lat"], cand["lng"]) <= here[2]
    return True


def places_cache_path(city: str, vid: str) -> Path:
    d = tools.CONTENT_DIR.parent / "scraper" / ".cache" / "places" / city
    d.mkdir(parents=True, exist_ok=True)
    return d / f"photos-{hashlib.sha1(vid.encode()).hexdigest()[:16]}.json"


def _blocked(resp: requests.Response) -> bool:
    """The key does not allow Places API (New): switch this run to legacy."""
    global _LEGACY
    if resp.status_code == 403 and "API_KEY_SERVICE_BLOCKED" in resp.text:
        _LEGACY = True
        return True
    return False


def text_search(key: str, venue: dict, city: str) -> list[dict]:
    """Up to 5 hits as {id, name, lat, lng}; Places (New) IDs+name+location
    (Pro SKU, 5,000 free / month) or the legacy textsearch endpoint."""
    parts = (venue["name"], venue.get("address") or (venue.get("google") or {}).get("address"),
             CITIES[city]["display_name"])
    query = ", ".join(p for p in parts if p)
    here = venue_coords(venue)
    if not _LEGACY:
        body: dict = {"textQuery": query, "languageCode": "en"}
        if here:
            body["locationBias"] = {"circle": {"center": {"latitude": here[0], "longitude": here[1]},
                                               "radius": SEARCH_BIAS_M}}
        resp = requests.post(f"{PLACES_BASE}/places:searchText", json=body, timeout=20,
                             headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": TEXT_SEARCH_FIELDS,
                                      "Content-Type": "application/json"})
        if not _blocked(resp):
            resp.raise_for_status()
            out = []
            for p in resp.json().get("places", [])[:5]:
                loc = p.get("location") or {}
                out.append({"id": p.get("id"), "name": (p.get("displayName") or {}).get("text"),
                            "lat": loc.get("latitude"), "lng": loc.get("longitude")})
            return out
    params = {"query": query, "key": key, "language": "en"}
    if here:
        params["location"] = f"{here[0]},{here[1]}"
        params["radius"] = int(SEARCH_BIAS_M)
    resp = requests.get(f"{LEGACY_BASE}/textsearch/json", params=params, timeout=20)
    resp.raise_for_status()
    out = []
    for p in resp.json().get("results", [])[:5]:
        loc = (p.get("geometry") or {}).get("location") or {}
        out.append({"id": p.get("place_id"), "name": p.get("name"),
                    "lat": loc.get("lat"), "lng": loc.get("lng")})
    return out


def normalize_legacy_photos(photos: list[dict]) -> list[dict]:
    """Legacy Details ``photos`` rows -> the Places (New) shape rank_google reads
    ({name, widthPx, heightPx, authorAttributions}); name = photo_reference and
    legacy = True so fetch_bytes uses the legacy photo endpoint."""
    out = []
    for p in photos or []:
        if not p.get("photo_reference"):
            continue
        m = ATTRIB_RE.search((p.get("html_attributions") or [""])[0] or "")
        out.append({"name": p["photo_reference"], "widthPx": p.get("width"), "heightPx": p.get("height"),
                    "legacy": True,
                    "authorAttributions": [{"displayName": m.group(2) if m else None,
                                            "uri": m.group(1) if m else None}]})
    return out


def photo_metadata(key: str, place_id: str) -> list[dict]:
    """Photo metadata in the Places (New) shape: the free ``id,photos`` Details
    call, or the legacy Details ``photo`` field normalised into it."""
    if not _LEGACY:
        resp = requests.get(f"{PLACES_BASE}/places/{place_id}", timeout=20,
                            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PHOTOS_FIELDS})
        if not _blocked(resp):
            resp.raise_for_status()
            return resp.json().get("photos", []) or []
    resp = requests.get(f"{LEGACY_BASE}/details/json", timeout=20,
                        params={"place_id": place_id, "fields": "photo", "key": key})
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") not in ("OK", "ZERO_RESULTS", "NOT_FOUND"):
        raise requests.HTTPError(f"legacy details {data.get('status')}")
    return normalize_legacy_photos((data.get("result") or {}).get("photos") or [])


def resolve_place(key: str, venue: dict, city: str, work: dict) -> tuple[str | None, list[dict]]:
    """(place_id, photo metadata). Registry place_id when present, else one free
    text search verified by name + distance; photo metadata cached 30 days."""
    cache_p = places_cache_path(city, venue["id"])
    cache = {}
    if cache_p.exists():
        try:
            cache = json.loads(cache_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache = {}
    pid = stored_place_id(venue) or cache.get("place_id")
    if not pid:
        try:
            hits = text_search(key, venue, city)
        except requests.RequestException as exc:
            work["notes"].append(f"places_search_error:{str(exc)[:80]}")
            return None, []
        pid = next((h["id"] for h in hits if accept_place(h, venue, city)), None)
        if not pid:
            work["notes"].append("no_place_match" if hits else "no_place_found")
            cache_p.write_text(json.dumps({"place_id": None, "ts": int(time.time()),
                                           "hits": hits}, ensure_ascii=False), encoding="utf-8")
            return None, []
        cache["place_id"] = pid
    if cache.get("photos") is not None and time.time() - (cache.get("photos_ts") or 0) < PHOTO_META_TTL_S:
        return pid, cache["photos"]
    try:
        photos = photo_metadata(key, pid)
    except requests.RequestException as exc:
        work["notes"].append(f"places_photos_error:{str(exc)[:80]}")
        return pid, []
    cache.update({"place_id": pid, "photos": photos, "photos_ts": int(time.time())})
    cache_p.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return pid, photos


def rank_google(photos: list[dict], venue_name: str, cap: int = GOOGLE_META_KEEP) -> list[dict]:
    """Free pre-filter on metadata: owner-uploaded (author shares a distinctive
    word with the venue name) beats visitor photos, landscape beats portrait,
    then pixel area; anything narrower than the store minimum is dropped."""
    key_words = venues._distinct_words(venue_name)
    scored = []
    for p in photos:
        w, h = int(p.get("widthPx") or 0), int(p.get("heightPx") or 0)
        if not p.get("name") or w < tools.MIN_ACCEPT_WIDTH:
            continue
        author = (p.get("authorAttributions") or [{}])[0] or {}
        author_name = html.unescape(author.get("displayName") or "") or None   # legacy anchors carry &amp;
        owner = bool(key_words and key_words & venues._distinct_words(author_name))
        landscape = w >= h * 1.2
        score = (2.0 if owner else 0.0) + (1.0 if landscape else 0.0) + min(w * h, 12e6) / 12e6
        scored.append((score, {"provider": "google", "name": p["name"], "px": [w, h],
                               "kind": "google", "alt": "", "source_page": "google",
                               "owner": owner, "legacy": bool(p.get("legacy")),
                               "attribution": {"name": author_name,
                                               "uri": author.get("uri")}}))
    scored.sort(key=lambda t: -t[0])
    return [c for _, c in scored[:cap]]


# --- fetch -------------------------------------------------------------------------

def dhash(img: Image.Image) -> int:
    """64-bit difference hash (9x8 grayscale; each bit = a pixel brighter than
    its right neighbour). Re-encodes and resizes of one photo (a site image
    mirrored on Google, a WordPress variant) land within a couple of bits."""
    g = img.convert("L").resize((9, 8), Image.LANCZOS)
    px = g.tobytes()                     # mode L: one byte per pixel
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def is_duplicate(h: int, seen: list[int], max_dist: int = DUP_HAMMING) -> bool:
    """Near-duplicate of an earlier candidate. Flat hashes (fewer than 8 or more
    than 56 set bits: white walls, blank frames) never match by hash, so two
    different white-cube interiors cannot collapse into one (the average hash
    did exactly that on Regen Projects)."""
    pop = bin(h).count("1")
    if pop < 8 or pop > 56:
        return False
    return any(bin(h ^ s).count("1") <= max_dist for s in seen)


def fetch_bytes(fetcher: refresh.Fetcher, cand: dict, key: str | None, budget: Budget) -> tuple[bytes | None, str | None]:
    """(bytes, error). Site images go through the polite fetcher (robots, 2 s per
    domain), retried once with a browser UA when bot-walled; Google media is one
    billed photo event, taken from the budget BEFORE the request."""
    if cand["provider"] == "google":
        if not key:
            return None, "no_google_key"
        budget.take_google()
        try:
            if cand.get("legacy"):
                r = requests.get(f"{LEGACY_BASE}/photo", timeout=60, allow_redirects=True,
                                 params={"photo_reference": cand["name"], "maxwidth": LEGACY_MAX_PX,
                                         "key": key})
            else:
                r = requests.get(f"{PLACES_BASE}/{cand['name']}/media", timeout=60, allow_redirects=True,
                                 params={"maxWidthPx": GOOGLE_MAX_PX, "maxHeightPx": GOOGLE_MAX_PX, "key": key})
            if r.status_code != 200:
                return None, f"http_{r.status_code}"
            return r.content, None
        except requests.RequestException as exc:
            return None, type(exc).__name__
    r = fetcher.get_bytes(cand["url"], referer=cand.get("source_page"), cap=IMAGE_BODY_CAP)
    if r["content"] is None and r["error"] in BOT_WALL:
        r = fetcher.get_bytes(cand["url"], referer=cand.get("source_page"), cap=IMAGE_BODY_CAP,
                              user_agent=tools.UA)
    return r["content"], r["error"]


def store_candidate(data: bytes, dest: Path) -> tuple[dict | None, str | None]:
    """Decode + normalise (tools.normalize_image_bytes), write JPEG q88. Returns
    ({px, stored_px, ahash}, error)."""
    try:
        img, (w, h) = tools.normalize_image_bytes(data)
    except ValueError as exc:
        return None, str(exc)[:120]
    if w > h * BANNER_ASPECT or h > w * BANNER_ASPECT:
        return None, f"banner aspect {w}x{h}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=88, optimize=True)
    return {"px": [w, h], "stored_px": list(img.size), "dhash": f"{dhash(img):016x}"}, None


# --- judge -------------------------------------------------------------------------

def judge_image_block(path: Path) -> tuple[dict, int]:
    """(image content block, ~input tokens) for one stored candidate, downscaled
    to JUDGE_MAX_SIDE so every candidate costs ~600 tokens instead of ~1,600."""
    with Image.open(path) as im:
        im.load()
        im.thumbnail((JUDGE_MAX_SIDE, JUDGE_MAX_SIDE), Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=80)
        tokens = (im.width * im.height) // 750
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.b64encode(buf.getvalue()).decode("ascii")}}, tokens


def build_judge_request(model: str, venue: dict, city: str, fetched: list[dict], vdir: Path,
                        effort: str = "low") -> tuple[dict, int]:
    """One Messages request (all candidates, one verdict) and its input-token estimate."""
    header = (f"Venue: {venue['name']}\nAddress: {venue.get('address') or 'unknown'}\n"
              f"City: {CITIES[city]['display_name']}\nCandidates: {len(fetched)}")
    content: list[dict] = [{"type": "text", "text": header}]
    est = JUDGE_OVERHEAD_TOKENS
    for f in fetched:
        page = f.get("source_page") or "google"
        w, h = f.get("px") or [0, 0]
        content.append({"type": "text",
                        "text": f"[{f['id']}] provider={f['provider']} page={page} "
                                f"alt={(f.get('alt') or '')[:80]!r} {w}x{h}"})
        block, tok = judge_image_block(vdir / f["file"])
        content.append(block)
        est += tok + 20
    req = {
        "model": model,
        "max_tokens": JUDGE_MAX_TOKENS,
        "system": [{"type": "text", "text": JUDGE_SYSTEM}],
        "messages": [{"role": "user", "content": content}],
        "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
    }
    if harness.MODELS.get(model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = effort
    return req, est


def parse_verdict(msg, known_ids: set[str]) -> tuple[dict | None, str | None]:
    """(verdict dict, error). Refusal, truncation and schema violations become an
    error string; picks naming unknown ids are dropped, order = rank, cap MAX_PICKS."""
    if msg.stop_reason == "refusal":
        det = getattr(msg, "stop_details", None)
        return None, f"refusal ({getattr(det, 'category', None)})"
    if msg.stop_reason == "max_tokens":
        return None, "max_tokens: verdict truncated"
    text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), "")
    try:
        v = PhotoVerdict.model_validate_json(text)
    except ValidationError as exc:
        return None, f"validation: {str(exc)[:200]}"
    picks, seen = [], set()
    for p in sorted(v.picks, key=lambda p: p.rank):
        if p.id in known_ids and p.id not in seen:
            seen.add(p.id)
            picks.append({"id": p.id, "rank": len(picks) + 1, "subject": p.subject,
                          "caption": p.caption.strip()[:120]})
        if len(picks) >= MAX_PICKS:
            break
    rejects = [{"id": r.id, "reason": r.reason} for r in v.rejects if r.id in known_ids and r.id not in seen]
    return {"picks": picks, "rejects": rejects,
            "confidence": max(0.0, min(1.0, float(v.confidence)))}, None


def estimate_usd(model: str, in_tokens: int, out_tokens: int = 200) -> float:
    p = harness.MODELS[model]
    return in_tokens / 1e6 * p["in"] + out_tokens / 1e6 * p["out"]


# --- apply -------------------------------------------------------------------------

def apply_venue(city: str, venue: dict, work: dict, keep_candidates: bool = False) -> dict:
    """Copy the picks to NN.jpg in verdict order (a site photo takes 01 over a
    Google photo on a rank-1/rank-2 tie, since only site photos ship) and build
    the venue.photos block. Idempotent: sources (candidates in the cache dir)
    are read before the old NN.jpg files are cleared; candidates are kept so a
    later ``judge --force`` re-judges without fetching (keep_candidates=False
    deletes them and re-points picked entries at their final file)."""
    vdir = venue_dir(city, venue["id"])
    cdir = cand_dir(city, venue["id"])
    verdict = work.get("verdict") or {}
    by_id = {f["id"]: f for f in work.get("fetched", [])}

    def source(f: dict) -> Path | None:
        name = f.get("file")
        if not name:
            return None
        for base in (cdir, vdir):
            if (base / name).exists():
                return base / name
        return None

    picks = [p for p in verdict.get("picks", []) if p["id"] in by_id and source(by_id[p["id"]])]
    missing = [p["id"] for p in verdict.get("picks", []) if p["id"] in by_id and p not in picks]
    if missing:
        work.setdefault("notes", []).append(f"apply:missing_source:{','.join(missing)}")
    if len(picks) > 1 and by_id[picks[0]["id"]]["provider"] == "google" \
            and by_id[picks[1]["id"]]["provider"] == "site":
        picks[0], picks[1] = picks[1], picks[0]
    payload = [(p, by_id[p["id"]], source(by_id[p["id"]]).read_bytes()) for p in picks]
    for old in vdir.glob("[0-9][0-9].jpg"):
        old.unlink()
    vdir.mkdir(parents=True, exist_ok=True)
    files = []
    for i, (p, f, data) in enumerate(payload, 1):
        dest = vdir / f"{i:02d}.jpg"
        dest.write_bytes(data)
        if not keep_candidates:
            f["file"] = dest.name
        entry = {"path": rel_path(dest), "provider": f["provider"], "px": f.get("px"),
                 "fetched_ts": f.get("fetched_ts"), "subject": p["subject"], "caption": p["caption"]}
        if f["provider"] == "site":
            entry["url"] = f.get("url")
            entry["source_page"] = f.get("source_page")
        else:
            entry["google_photo"] = f.get("name")
            entry["attribution"] = f.get("attribution")
        files.append(entry)
    if not keep_candidates:
        for c in list(cdir.glob("cand-*.jpg")) + list(vdir.glob("cand-*.jpg")):
            c.unlink()
        for f in work.get("fetched", []):
            if (f.get("file") or "").startswith("cand-"):
                f["file"] = None                      # gone; the verdict keeps its id
    return {
        "status": "done" if files else "none",
        "ts": int(time.time()),
        "model": verdict.get("model"),
        "confidence": verdict.get("confidence"),
        "candidates_seen": len(work.get("candidates", [])),
        "google_events": work.get("google_events", 0),
        "note": None if files else ("all_rejected" if by_id else "no_candidates"),
        "files": files,
    }


# --- monthly Google tally + ledger ------------------------------------------------------

EVENTS_PATH = harness.SPEND_DIR / "google-photo-events.json"


def month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def month_events() -> int:
    if EVENTS_PATH.exists():
        try:
            return int(json.loads(EVENTS_PATH.read_text()).get(month_key(), 0))
        except (json.JSONDecodeError, ValueError):
            pass
    return 0


def add_month_events(n: int) -> tuple[int, float]:
    """Record n photo events this month; returns (month total, USD this run cost)."""
    data = {}
    if EVENTS_PATH.exists():
        try:
            data = json.loads(EVENTS_PATH.read_text())
        except json.JSONDecodeError:
            data = {}
    before = int(data.get(month_key(), 0))
    after = before + n
    data[month_key()] = after
    harness.SPEND_DIR.mkdir(parents=True, exist_ok=True)
    EVENTS_PATH.write_text(json.dumps(data, indent=2))
    billable = max(0, after - max(before, PHOTO_FREE_PER_MONTH))
    return after, round(billable * PHOTO_PRICE_USD, 4)


def write_ledger(meter: harness.CostMeter, city: str, stages: list[str], n_venues: int,
                 budget: Budget) -> dict | None:
    if not meter.requests and not budget.google_events:
        return None
    total, google_usd = add_month_events(budget.google_events)
    s = meter.summary()
    s.update({"city": city, "stage": "gallery-photos", "stages": stages, "venues": n_venues,
              "google_photo_events": budget.google_events, "google_photo_events_month": total,
              "google_cost_usd": google_usd, "cost_usd": round(s["cost_usd"] + google_usd, 4)})
    harness.SPEND_DIR.mkdir(parents=True, exist_ok=True)
    (harness.SPEND_DIR / f"{meter.label}.json").write_text(json.dumps(s, indent=2))
    return s


# --- stages ------------------------------------------------------------------------------

def stage_candidates(city: str, targets: list[dict], key: str | None, fetcher: refresh.Fetcher,
                     force: bool, no_google: bool) -> None:
    for v in targets:
        work = load_work(city, v["id"])
        if work["candidates"] and not force:
            print(f"  {v['id']}: {len(work['candidates'])} candidates (cached)")
            continue
        work.update({"candidates": [], "fetched": [], "verdict": None, "notes": [], "google_events": 0})
        site = site_candidates(fetcher, city, v, work)
        google: list[dict] = []
        if not no_google and key:
            pid, photos = resolve_place(key, v, city, work)
            work["place_id"] = pid
            work["google_api"] = "legacy" if _LEGACY else "new"
            google = rank_google(photos, v["name"])
        cands = site + google
        for i, c in enumerate(cands, 1):
            c["id"] = f"C{i}"
        work["candidates"] = cands
        save_work(city, v["id"], work)
        print(f"  {v['id']}: site {len(site)} google {len(google)}"
              + (f"  [{', '.join(work['notes'])}]" if work["notes"] else ""))


def stage_fetch(city: str, targets: list[dict], key: str | None, fetcher: refresh.Fetcher,
                budget: Budget, google_per_venue: int, dry_run: bool) -> None:
    planned_google = 0
    for v in targets:
        work = load_work(city, v["id"])
        vdir = cand_dir(city, v["id"])
        done = {f["id"] for f in work["fetched"]}
        google_taken = sum(1 for f in work["fetched"] if f["provider"] == "google")
        todo = []
        for c in work["candidates"]:
            if c["id"] in done:
                continue
            if c["provider"] == "google":
                if google_taken >= google_per_venue:
                    continue
                google_taken += 1
            todo.append(c)
        n_google = sum(1 for c in todo if c["provider"] == "google")
        if dry_run:
            planned_google += n_google
            print(f"  {v['id']}: would fetch {len(todo)} ({n_google} google), "
                  f"{len(work['fetched'])} already stored")
            continue
        hashes = [int(f["dhash"], 16) for f in work["fetched"] if f.get("dhash")]
        n_ok = 0
        for c in todo:
            try:
                data, err = fetch_bytes(fetcher, c, key, budget)
            except BudgetExceeded as exc:
                print(f"  stop: {exc}")
                save_work(city, v["id"], work)
                return
            if c["provider"] == "google":
                work["google_events"] = work.get("google_events", 0) + 1
            if data is None:
                work["notes"].append(f"{c['id']}:{err}")
                continue
            idx = int(c["id"][1:])
            info, err = store_candidate(data, vdir / f"cand-{idx:02d}.jpg")
            if info is None:
                work["notes"].append(f"{c['id']}:{err}")
                continue
            h = int(info["dhash"], 16)
            if is_duplicate(h, hashes):
                (vdir / f"cand-{idx:02d}.jpg").unlink(missing_ok=True)
                work["notes"].append(f"{c['id']}:duplicate")
                continue
            hashes.append(h)
            work["fetched"].append({**c, "file": f"cand-{idx:02d}.jpg", **info,
                                    "fetched_ts": int(time.time())})
            n_ok += 1
        save_work(city, v["id"], work)
        print(f"  {v['id']}: stored {n_ok} new, {len(work['fetched'])} total "
              f"(google events so far {budget.google_events})")
    if dry_run:
        print(f"  dry run: {planned_google} google photo events planned "
              f"(cap {budget.google_cap}; {month_events()} used this month, {PHOTO_FREE_PER_MONTH} free)")


def stage_judge(city: str, targets: list[dict], model: str, effort: str, meter: harness.CostMeter,
                budget: Budget, force: bool, dry_run: bool) -> None:
    import anthropic
    client = anthropic.Anthropic(max_retries=3) if not dry_run else None
    est_total = 0.0
    for v in targets:
        work = load_work(city, v["id"])
        if not work["fetched"]:
            continue
        if work.get("verdict") and not force:
            continue
        vdir = cand_dir(city, v["id"])
        fetched = [f for f in work["fetched"] if f.get("file") and (vdir / f["file"]).exists()]
        if not fetched:
            continue
        req, est_in = build_judge_request(model, v, city, fetched, vdir, effort)
        est = estimate_usd(model, est_in)
        if dry_run:
            est_total += est
            print(f"  {v['id']}: {len(fetched)} candidates ~{est_in} tokens ~${est:.4f}")
            continue
        try:
            budget.check_usd(meter.dollars, est)
        except BudgetExceeded as exc:
            print(f"  stop: {exc}")
            return
        before = meter.dollars
        try:
            msg = client.messages.create(**req)
        except anthropic.APIError as exc:
            work["notes"].append(f"judge_api_error:{str(exc)[:100]}")
            save_work(city, v["id"], work)
            print(f"  {v['id']}: API error {str(exc)[:80]}")
            continue
        meter.record(msg.usage)
        verdict, err = parse_verdict(msg, {f["id"] for f in fetched})
        if err:
            work["notes"].append(f"judge:{err}")
            save_work(city, v["id"], work)
            print(f"  {v['id']}: {err}")
            continue
        verdict.update({"model": model, "effort": effort, "ts": int(time.time()),
                        "cost_usd": round(meter.dollars - before, 5)})
        work["verdict"] = verdict
        save_work(city, v["id"], work)
        picks = ", ".join(f"{p['id']}:{p['subject']}" for p in verdict["picks"])
        print(f"  {v['id']}: {len(verdict['picks'])} picks [{picks}] "
              f"conf {verdict['confidence']:.2f} ${verdict['cost_usd']:.4f}")
    if dry_run:
        print(f"  dry run: judge ~${est_total:.2f} total")


def stage_apply(city: str, targets: list[dict], keep_candidates: bool) -> dict:
    stats = {"done": 0, "none": 0, "skipped": 0}
    blocks: dict[str, dict] = {}
    for v in targets:
        work = load_work(city, v["id"])
        if not work.get("verdict"):
            stats["skipped"] += 1
            continue
        try:
            photos = apply_venue(city, v, work, keep_candidates)
        except OSError as exc:
            work.setdefault("notes", []).append(f"apply_error:{str(exc)[:100]}")
            save_work(city, v["id"], work)
            stats["skipped"] += 1
            print(f"  {v['id']}: apply error {str(exc)[:80]}")
            continue
        save_work(city, v["id"], work)
        blocks[v["id"]] = photos
        stats[photos["status"]] += 1
        print(f"  {v['id']}: {photos['status']} {len(photos['files'])} photos"
              + (f" ({photos['note']})" if photos["note"] else ""))
    if blocks:
        with venues.locked_registry(city) as reg:
            by = venues.index_by_id(reg)
            for vid, photos in blocks.items():
                if vid in by:
                    by[vid]["photos"] = photos
    return stats


# --- CLI ---------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--city", required=True, choices=sorted(CITIES))
        p.add_argument("--top", type=int, help="the N best-ranked venues")
        p.add_argument("--venue-ids", nargs="*", help="explicit registry ids instead of --top")
        p.add_argument("--force", action="store_true", help="redo cached stage output")
        p.add_argument("--no-google", action="store_true", help="site images only, no Places calls")

    def fetch_flags(p):
        p.add_argument("--google-per-venue", type=int, default=GOOGLE_PER_VENUE)
        p.add_argument("--google-cap", type=int, default=400,
                       help="max Google photo events this invocation (1,000 free per month)")

    def judge_flags(p):
        p.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(harness.MODELS))
        p.add_argument("--effort", default="low", choices=("low", "medium", "high"))
        p.add_argument("--budget", type=float, help="USD cap for judge calls this invocation")

    for name in ("candidates", "fetch", "judge", "apply", "run"):
        p = sub.add_parser(name)
        common(p)
        if name in ("fetch", "run"):
            fetch_flags(p)
        if name in ("judge", "run"):
            judge_flags(p)
        if name in ("fetch", "judge", "run"):
            p.add_argument("--dry-run", action="store_true")
        if name in ("apply", "run"):
            p.add_argument("--drop-candidates", action="store_true",
                           help="delete the cached candidates after apply (default keeps them "
                                "in scraper/.cache so judge --force can re-judge for free)")
    args = ap.parse_args()
    if not args.top and not args.venue_ids:
        ap.error("--top N or --venue-ids required")

    load_env()
    import os
    key = None if args.no_google else os.environ.get("GOOGLE_MAPS_API_KEY")
    if not args.no_google and not key:
        print("note: GOOGLE_MAPS_API_KEY unset — site images only")
    reg = venues.load_registry(args.city)
    targets = select_venues(reg, args.top, args.venue_ids)
    print(f"{args.city}: {len(targets)} venues, stage {args.cmd}")
    fetcher = refresh.Fetcher()
    budget = Budget(usd_cap=getattr(args, "budget", None), google_cap=getattr(args, "google_cap", 400))
    model = getattr(args, "model", DEFAULT_MODEL)
    meter = harness.CostMeter(f"gallery-photos-{args.city}-{int(time.time())}", model)
    stages = [args.cmd] if args.cmd != "run" else ["candidates", "fetch", "judge", "apply"]
    dry = getattr(args, "dry_run", False)

    try:
        if "candidates" in stages:
            print("candidates:")
            stage_candidates(args.city, targets, key, fetcher, args.force, args.no_google)
        if "fetch" in stages:
            print("fetch:")
            stage_fetch(args.city, targets, key, fetcher, budget, args.google_per_venue, dry)
        if "judge" in stages:
            print("judge:")
            stage_judge(args.city, targets, model, args.effort, meter, budget, args.force, dry)
        if "apply" in stages and not dry:
            print("apply:")
            st = stage_apply(args.city, targets, keep_candidates=not args.drop_candidates)
            print(f"  registry: {st}")
    finally:
        # billable work happened before any crash: the ledger and the monthly
        # Google tally must not depend on the run finishing
        ledger = write_ledger(meter, args.city, stages, len(targets), budget)
    if ledger:
        print(f"spend: ${ledger['cost_usd']:.4f} ({meter.requests} judge calls, "
              f"{budget.google_events} google photo events, {ledger['google_photo_events_month']} this month)"
              f" -> {meter.label}.json")


if __name__ == "__main__":
    main()
