"""Deterministic venue seeding for the registry (content/venues/<city>.json).

The LLM enumerator misses venues (1301 PE, Advocartsy, Monte Vista Projects)
and leaves whole zones empty (Pasadena/San Gabriel), so this module fills the
registry from sources that need no model at all:

  places    Google Places nearby sweeps (types art_gallery + museum) around the
            geocoded `areas` of every zone in cities.py; saturated circles are
            split into four half-radius circles (depth <= 2).
  gpla      galleryplatform.la/galleries — ~95 member galleries with address,
            website, phone, hours and a region pill; address -> geocode -> zone.
  carla     contemporaryartreview.la's site-wide "Distribution" list (~138 LA
            spaces, name + website only); zone via the registry / Places cache
            / optional --lookup (Places text search, ~$0.05 each).
  evidence  the local evidence cache (pages sessions already fetched): a page
            that looks like a venue's own site becomes a `candidate` venue via
            venues.on_fetch, or attaches a website to a venue that had none.

Zone assignment is nearest-labelled-point: registry venues with coordinates
plus geocoded area centres, both tagged with their zone. Nothing within 3 km
-> unresolved (neighborhood None, invisible to due_venues until
--resolve-zones); the nearest points of two zones within 300 m -> flagged
ambiguous. Anchors checked by --status come from cities.py guidance only —
never from See Saw data.

Registry rules: an existing venue only receives fields it lacks (plus the
`google` block and `sources.seed.<source>`); `status` and `neighborhood` are
never overwritten. New venues get status "unknown" and next_check today so
they are due immediately. Idempotent: a second --apply reports 0 new.

    python seed_venues.py --city los-angeles --sources gpla,carla --dry-run
    python seed_venues.py --city los-angeles --sources places            # plan + $ estimate, no paid calls
    python seed_venues.py --city los-angeles --sources places --zones "Pasadena/San Gabriel" \\
        --max-places-requests 6 --dry-run                                 # small paid smoke test
    python seed_venues.py --city los-angeles --sources places,gpla,carla,evidence --apply
    python seed_venues.py --city los-angeles --status
    python seed_venues.py --city los-angeles --resolve-zones --lookup --apply

Dry-run (the default) fetches the free/cheap inputs (HTML, geocoding at
$0.005/address, cached) and prints per-source tables; paid Places calls happen
only with --apply or an explicit --max-places-requests N. Every raw Google
response is cached 30 days under scraper/.cache/places/<city>/.
"""

from __future__ import annotations

import argparse
import hashlib
import html as _html
import json
import math
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crosscheck  # noqa: E402
import refresh  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

SCRAPER_DIR = Path(__file__).resolve().parent
PLACES_CACHE_DIR = SCRAPER_DIR / ".cache" / "places"   # tests redirect this
PLACES_CACHE_DAYS = 30
HTML_CACHE_DAYS = 1

GPLA_LIST_URL = "https://galleryplatform.la/galleries"
GPLA_DETAIL_URL = "https://galleryplatform.la/galleries/{slug}"
CARLA_URL = "https://contemporaryartreview.la/"

SOURCES = ("places", "gpla", "carla", "evidence")   # execution order when several are requested
PLACES_TYPES = ("art_gallery", "museum")
COST_USD = {"nearby_new": 0.035, "nearby_legacy": 0.032, "details_legacy": 0.020,
            "geocode": 0.005, "lookup": 0.05}
NEW_PAGE_CAP = 20            # searchNearby maxResultCount
LEGACY_PAGE_CAP = 20
LEGACY_MAX_PAGES = 3         # 60 results
RADIUS_MIN_M, RADIUS_MAX_M = 800, 2500
SPLIT_MAX_DEPTH = 2
ZONE_MAX_M = 3000            # farther than this from every labelled point -> unresolved
ZONE_AMBIGUOUS_M = 300       # two zones' nearest points this close -> ambiguous
PROXIMITY_MATCH_M = 60       # registry venue this close + a shared name word -> same venue

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
NEARBY_FIELDS = ",".join(f"places.{f}" for f in (
    "id", "displayName", "formattedAddress", "location", "types", "businessStatus",
    "websiteUri", "nationalPhoneNumber", "regularOpeningHours.weekdayDescriptions"))
LEGACY_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
LEGACY_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
LEGACY_DETAILS_FIELDS = ("name,website,formatted_phone_number,business_status,opening_hours,"
                         "formatted_address,geometry")
GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Places types that never mark an art venue (galleries often carry `store`,
# `point_of_interest`, `tourist_attraction` — those stay allowed).
PLACES_DENY_TYPES = frozenset({
    "furniture_store", "home_goods_store", "jewelry_store", "clothing_store", "hair_care",
    "beauty_salon", "restaurant", "bar", "cafe", "night_club", "lodging", "real_estate_agency",
    "florist", "book_store", "school", "spa", "gym", "car_dealer", "car_repair", "car_wash",
    "movie_theater", "amusement_park", "aquarium", "zoo", "casino", "cemetery",
    "hardware_store", "electronics_store", "convenience_store", "supermarket",
    "grocery_or_supermarket", "liquor_store", "pharmacy", "dentist", "doctor", "lawyer",
    "insurance_agency", "travel_agency", "tattoo_shop", "shopping_mall",
})
NAME_DENY_RE = re.compile(
    r"\b(framing|frames?|frame shop|tattoo|supplies|supply|art materials|print shop|print lab|"
    r"printing|auction|antiques?|rugs?|lighting|interiors?|furniture|design (?:studio|center|centre)|"
    r"salon|dispensary|blinds|flooring|tile|jewel(?:ry|ers?|lery)|escape room|wax museum|"
    r"natural history|science center|planetarium|zoo|observatory|hall of fame|grammy|petersen|"
    r"tar pits|children'?s museum|historical society|forest lawn|ripley|hair|barber|nails?|yoga|"
    r"cannabis|realty|real estate|dental|pawn|vape|smoke shop|car wash|holocaust|tolerance|"
    r"aviation|automotive|railway|railroad|madame tussauds|guinness|hollywood museum|mansion|"
    r"architects)\b", re.I)
# Carla's distribution list mixes in bookstores, framers, theatres, radio...
LIST_NAME_DENY_RE = re.compile(
    r"\b(\w*books?|bookstore|bookshop|framing|printing|coffee|cafe|theatre|theater|eyeworks|"
    r"radio|dublab|repertory|represents|literary)\b|press$", re.I)
BRANCH_TAG_RE = re.compile(r"\s*\([^)]*\)\s*$")
# Page names that are navigation labels or civic/press sites, never a venue.
EVIDENCE_NAME_DENY_RE = re.compile(
    r"^(news|home|homepage|visit|exhibitions|contact|about|menu|events|calendar)$|"
    r"\b(news|visit|chamber|tourism|city of|department of)\b", re.I)
NEW_ONLY_FIELDS = ("status", "neighborhood", "next_check")
ALWAYS_MERGE_FIELDS = ("sources", "google", "aliases", "exhibitions_url_candidates")


# --- small helpers ---------------------------------------------------------------

def _slugify(text: str) -> str:
    """Same rule as run_deep.slugify (kept local: run_deep imports the harness)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _addr_key(addr: str | None) -> str | None:
    """Street-line key for 'same building' checks: '1151 Oxford Road, San Marino' -> '1151 oxford road'."""
    if not addr:
        return None
    first = re.split(r",", addr.strip(), 1)[0].lower()
    first = re.sub(r"\b(suite|ste|unit|#|apt|apartment)\b.*$", "", first).strip()
    return re.sub(r"\s+", " ", first) or None


def _clean_address(addr: str | None) -> str | None:
    if not addr:
        return None
    return re.sub(r",\s*(USA|United States)\s*$", "", addr.strip()) or None


def _words(text: str | None) -> set[str]:
    return crosscheck._name_words(text or "") - crosscheck.GENERIC_NAME_WORDS


def _count(stats: dict | None, kind: str, n: int = 1) -> None:
    if stats is None:
        return
    stats.setdefault("requests", {})[kind] = stats["requests"].get(kind, 0) + n
    stats["cost_usd"] = round(stats.get("cost_usd", 0.0) + COST_USD[kind] * n, 4)


# --- caches (scraper/.cache/places/<city>/) ------------------------------------

def _cache_dir(city: str) -> Path:
    return PLACES_CACHE_DIR / city


def _cache_key(query: dict) -> str:
    return hashlib.sha1(json.dumps(query, sort_keys=True).encode()).hexdigest()


def _cache_get(city: str | None, key: str, max_age_days: float) -> dict | None:
    if not city:
        return None
    p = _cache_dir(city) / f"{key}.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if time.time() - obj.get("ts", 0) > max_age_days * 86400:
        return None
    return obj


def _cache_put(city: str | None, key: str, query: dict, data) -> None:
    if not city:
        return
    d = _cache_dir(city)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(
        {"ts": int(time.time()), "query": query, "data": data}, ensure_ascii=False))


def _store_load(city: str | None, name: str) -> dict:
    if not city:
        return {}
    p = _cache_dir(city) / f"{name}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _store_save(city: str | None, name: str, obj: dict) -> None:
    if not city:
        return
    d = _cache_dir(city)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False))


def _fresh(entry: dict | None) -> bool:
    return bool(entry) and time.time() - entry.get("ts", 0) <= PLACES_CACHE_DAYS * 86400


# --- geocoding -------------------------------------------------------------------

def _city_bounds(city: str | None) -> str | None:
    """Geocoding `bounds` bias (sw|ne) from the city's map centre/span: makes
    'Venice' or 'Jefferson Park' resolve inside the city instead of nowhere."""
    cfg = CITIES.get(city or "") or {}
    c, sp = cfg.get("center"), cfg.get("span")
    if not c or not sp:
        return None
    return (f"{c['latitude'] - sp['latitudeDelta'] / 2},{c['longitude'] - sp['longitudeDelta'] / 2}|"
            f"{c['latitude'] + sp['latitudeDelta'] / 2},{c['longitude'] + sp['longitudeDelta'] / 2}")


def geocode_area(key: str, name: str, city_name: str, city: str | None = None,
                 stats: dict | None = None) -> dict | None:
    """Centre + search radius for a sub-district / street corner. Unlike
    tools._geocode_address this accepts APPROXIMATE results (areas have no
    street number); radius = half the viewport diagonal clamped to 800-2500 m.
    Biased to the city's bounds. Cached in .cache/places/<city>/areas.json."""
    store = _store_load(city, "areas")
    k = f"{name}|{city_name}"
    hit = store.get(k)
    if _fresh(hit):
        return None if hit.get("none") else hit
    _count(stats, "geocode")
    params = {"address": f"{name}, {city_name}", "key": key}
    if _city_bounds(city):
        params["bounds"] = _city_bounds(city)
    try:
        resp = requests.get(GEOCODE_URL, params=params, timeout=20).json()
    except Exception:
        return None
    out = None
    if resp.get("status") == "OK" and resp.get("results"):
        res = resp["results"][0]
        geo = res.get("geometry") or {}
        loc = geo.get("location") or {}
        vp = geo.get("viewport") or {}
        ne, sw = vp.get("northeast"), vp.get("southwest")
        radius = RADIUS_MIN_M
        if ne and sw:
            radius = crosscheck.haversine_m(ne["lat"], ne["lng"], sw["lat"], sw["lng"]) / 2
        if loc.get("lat") is not None:
            out = {"lat": loc["lat"], "lng": loc["lng"],
                   "radius_m": int(max(RADIUS_MIN_M, min(RADIUS_MAX_M, radius))),
                   "formatted": res.get("formatted_address"),
                   "location_type": geo.get("location_type"), "ts": int(time.time())}
    store[k] = out or {"none": True, "ts": int(time.time())}
    _store_save(city, "areas", store)
    return out


def geocode_address(key: str, address: str, city_name: str, city: str | None = None,
                    stats: dict | None = None) -> dict | None:
    """Cached tools._geocode_address (precise results only, fails closed)."""
    store = _store_load(city, "geocode")
    hit = store.get(address)
    if _fresh(hit):
        return None if hit.get("none") else hit
    _count(stats, "geocode")
    try:
        out = tools._geocode_address(key, address, city_name)
    except Exception:
        return None
    store[address] = {**out, "ts": int(time.time())} if out else {"none": True, "ts": int(time.time())}
    _store_save(city, "geocode", store)
    return out


# --- Google Places client -------------------------------------------------------

_NEW_DISABLED = False   # flipped on a 403 from Places (New); mirrors crosscheck._V1_DISABLED


def _norm_new(p: dict) -> dict:
    loc = p.get("location") or {}
    return {"place_id": p.get("id"), "name": (p.get("displayName") or {}).get("text"),
            "address": p.get("formattedAddress"), "lat": loc.get("latitude"),
            "lng": loc.get("longitude"), "types": p.get("types") or [],
            "status": p.get("businessStatus"), "website": p.get("websiteUri"),
            "phone": p.get("nationalPhoneNumber"),
            "hours": (p.get("regularOpeningHours") or {}).get("weekdayDescriptions")}


def _norm_legacy(p: dict) -> dict:
    loc = (p.get("geometry") or {}).get("location") or {}
    return {"place_id": p.get("place_id"), "name": p.get("name"),
            "address": p.get("vicinity") or p.get("formatted_address"),
            "lat": loc.get("lat"), "lng": loc.get("lng"), "types": p.get("types") or [],
            "status": p.get("business_status"), "website": None, "phone": None, "hours": None}


def _nearby_query(api: str, lat: float, lng: float, radius_m: float, ptype: str) -> dict:
    return {"op": "nearby", "api": api, "lat": round(lat, 5), "lng": round(lng, 5),
            "radius_m": int(radius_m), "type": ptype}


def _resolve_api(api: str) -> str:
    if api == "auto":
        return "legacy" if _NEW_DISABLED else "new"
    return api


def _nearby_new_request(key: str, lat: float, lng: float, radius_m: float, ptype: str) -> dict:
    resp = requests.post(
        NEARBY_URL, timeout=20,
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": NEARBY_FIELDS,
                 "Content-Type": "application/json"},
        json={"includedTypes": [ptype], "maxResultCount": NEW_PAGE_CAP,
              "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lng},
                                                 "radius": float(radius_m)}}})
    resp.raise_for_status()
    return resp.json()


def _nearby_legacy_pages(key: str, lat: float, lng: float, radius_m: float, ptype: str,
                         stats: dict | None, max_pages: int = LEGACY_MAX_PAGES) -> list[dict]:
    pages: list[dict] = []
    token = None
    for _ in range(max(1, min(LEGACY_MAX_PAGES, max_pages))):
        params = {"key": key}
        if token:
            time.sleep(2.1)   # next_page_token is not valid immediately
            params["pagetoken"] = token
        else:
            params.update({"location": f"{lat},{lng}", "radius": int(radius_m), "type": ptype})
        page = requests.get(LEGACY_NEARBY_URL, params=params, timeout=20).json()
        _count(stats, "nearby_legacy")
        status = page.get("status")
        if status not in ("OK", "ZERO_RESULTS"):
            raise RuntimeError(f"legacy nearby {status}: {page.get('error_message', '')}"[:200])
        pages.append(page)
        token = page.get("next_page_token")
        if not token:
            break
    return pages


def places_nearby(key: str, lat: float, lng: float, radius_m: float, ptype: str,
                  api: str = "auto", city: str | None = None, stats: dict | None = None,
                  out: dict | None = None, max_requests: int | None = None) -> list[dict]:
    """Nearby search normalised to {place_id, name, address, lat, lng, types,
    status, website, phone, hours}. Places (New) first; a 403 (API not enabled
    on this key) switches the whole run to the legacy endpoint (which needs a
    Place Details call later for website/phone/hours). `out` receives
    {api, cached, saturated, n, paid} where paid = HTTP requests billed by this
    call (legacy pagination is up to 3; `max_requests` caps it). Raw responses
    are cached 30 days."""
    global _NEW_DISABLED
    use = _resolve_api(api)
    query = _nearby_query(use, lat, lng, radius_m, ptype)
    k = _cache_key(query)
    cached = _cache_get(city, k, PLACES_CACHE_DAYS)
    paid = 0
    if cached is not None:
        data = cached["data"]
    else:
        if use == "new":
            try:
                data = _nearby_new_request(key, lat, lng, radius_m, ptype)
                _count(stats, "nearby_new")
                paid = 1
            except requests.HTTPError as exc:
                if api == "auto" and exc.response is not None and exc.response.status_code == 403:
                    _NEW_DISABLED = True
                    return places_nearby(key, lat, lng, radius_m, ptype, api, city, stats, out, max_requests)
                raise
        else:
            data = _nearby_legacy_pages(key, lat, lng, radius_m, ptype, stats,
                                        LEGACY_MAX_PAGES if max_requests is None else max_requests)
            paid = len(data)
        _cache_put(city, k, query, data)
    if use == "new":
        raw = data.get("places") or []
        places = [_norm_new(p) for p in raw]
        saturated = len(raw) >= NEW_PAGE_CAP
    else:
        raw = [r for page in data for r in page.get("results") or []]
        places = [_norm_legacy(r) for r in raw]
        saturated = len(data) >= LEGACY_MAX_PAGES and len(raw) >= LEGACY_PAGE_CAP * LEGACY_MAX_PAGES
    places = [p for p in places if p.get("lat") is not None and p.get("name")]
    if out is not None:
        out.update({"api": use, "cached": cached is not None, "saturated": saturated, "n": len(places),
                    "paid": paid})
    return places


def place_details_legacy(key: str, place_id: str, city: str | None = None,
                         stats: dict | None = None) -> dict | None:
    """Website / phone / hours for one legacy-nearby result (cached 30 days)."""
    query = {"op": "details", "place_id": place_id}
    k = _cache_key(query)
    cached = _cache_get(city, k, PLACES_CACHE_DAYS)
    if cached is not None:
        data = cached["data"]
    else:
        data = requests.get(LEGACY_DETAILS_URL, timeout=20,
                            params={"place_id": place_id, "fields": LEGACY_DETAILS_FIELDS,
                                    "key": key}).json()
        _count(stats, "details_legacy")
        _cache_put(city, k, query, data)
    r = data.get("result") or {}
    if not r:
        return None
    loc = (r.get("geometry") or {}).get("location") or {}
    return {"website": r.get("website"), "phone": r.get("formatted_phone_number"),
            "status": r.get("business_status"),
            "hours": (r.get("opening_hours") or {}).get("weekday_text"),
            "address": r.get("formatted_address"), "lat": loc.get("lat"), "lng": loc.get("lng")}


def place_skip_reason(place: dict) -> str | None:
    """Why a Places result must not become a venue (None = keep)."""
    if place.get("status") == "CLOSED_PERMANENTLY":
        return "closed_permanently"
    bad = sorted(set(place.get("types") or []) & PLACES_DENY_TYPES)
    if bad:
        return f"type:{bad[0]}"
    m = NAME_DENY_RE.search(place.get("name") or "")
    if m:
        return f"name:{m.group(0).lower()}"
    return None


def place_kind(place: dict) -> str:
    return "museum" if "museum" in (place.get("types") or []) else "gallery"


def _split_circle(item: dict) -> list[dict]:
    """Four half-radius circles offset diagonally (denser coverage where a
    circle saturated; exact disk coverage is not the goal)."""
    r = item["radius_m"] / 2
    off = r * 0.75
    dlat = off / 111_320
    dlng = off / (111_320 * max(0.2, math.cos(math.radians(item["lat"]))))
    subs = []
    for sy, sx in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
        subs.append({**item, "lat": item["lat"] + sy * dlat, "lng": item["lng"] + sx * dlng,
                     "radius_m": int(r), "depth": item["depth"] + 1, "cached": False,
                     "parent": item.get("area")})
    return subs


def _places_cache_index(city: str) -> dict:
    """Every place in the local nearby-search cache, indexed by website domain
    and normalised name (Carla zone resolution without a paid call)."""
    by_domain: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    d = _cache_dir(city)
    if not d.exists():
        return {"domain": by_domain, "name": by_name}
    for f in d.glob("*.json"):
        try:
            obj = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        q = obj.get("query") if isinstance(obj, dict) else None
        if not q or q.get("op") != "nearby":
            continue
        data = obj.get("data")
        if q.get("api") == "new":
            places = [_norm_new(p) for p in (data or {}).get("places") or []]
        else:
            places = [_norm_legacy(r) for page in (data or []) for r in page.get("results") or []]
        for p in places:
            if p.get("lat") is None or not p.get("name"):
                continue
            dom = venues.registrable_domain(p.get("website"))
            if dom:
                by_domain.setdefault(dom, p)
            by_name.setdefault(tools._norm_venue(p["name"]), p)
    return {"domain": by_domain, "name": by_name}


# --- zone assignment ---------------------------------------------------------------

def labeled_points(reg: dict, areas_geo: dict | None = None) -> list[dict]:
    """Registry venues with coordinates (skipping out_of_scope/duplicate) plus
    geocoded area centres, each tagged {lat, lng, zone, via}."""
    pts = []
    for v in reg.get("venues", []):
        if v.get("latitude") is None or v.get("longitude") is None or not v.get("neighborhood"):
            continue
        if v.get("status") in ("out_of_scope", "duplicate"):
            continue
        pts.append({"lat": v["latitude"], "lng": v["longitude"], "zone": v["neighborhood"],
                    "via": f"venue:{v['id']}"})
    for zone, areas in (areas_geo or {}).items():
        for area, g in (areas or {}).items():
            if g:
                pts.append({"lat": g["lat"], "lng": g["lng"], "zone": zone, "via": f"area:{area}"})
    return pts


def assign_zone(lat: float, lng: float, labeled: list[dict]
                ) -> tuple[str | None, str | None, int | None, bool]:
    """(zone, via, dist_m, ambiguous): the zone of the nearest labelled point;
    None beyond ZONE_MAX_M; ambiguous when another zone's nearest point is
    within ZONE_AMBIGUOUS_M of the winner's distance."""
    if not labeled:
        return None, None, None, False
    best: dict[str, tuple[float, dict]] = {}
    for p in labeled:
        d = crosscheck.haversine_m(lat, lng, p["lat"], p["lng"])
        if p["zone"] not in best or d < best[p["zone"]][0]:
            best[p["zone"]] = (d, p)
    ranked = sorted(best.values(), key=lambda t: t[0])
    d1, p1 = ranked[0]
    if d1 > ZONE_MAX_M:
        return None, p1["via"], int(round(d1)), False
    ambiguous = len(ranked) > 1 and (ranked[1][0] - d1) <= ZONE_AMBIGUOUS_M
    return p1["zone"], p1["via"], int(round(d1)), ambiguous


# --- registry matching + patch shaping -------------------------------------------

def match_registry(reg: dict, name: str, website: str | None = None,
                   lat: float | None = None, lng: float | None = None
                   ) -> tuple[dict | None, str | None]:
    """venues.find_venue (id -> alias -> domain), then a registry venue within
    PROXIMITY_MATCH_M that shares a non-generic name word."""
    v = venues.find_venue(reg, name, website)
    if v is not None:
        return v, "registry"
    if lat is None or lng is None:
        return None, None
    words = _words(name)
    if not words:
        return None, None
    for v in reg.get("venues", []):
        if v.get("latitude") is None or v.get("longitude") is None:
            continue
        if crosscheck.haversine_m(lat, lng, v["latitude"], v["longitude"]) > PROXIMITY_MATCH_M:
            continue
        vw = set()
        for n in [v["name"]] + list(v.get("aliases") or []):
            vw |= _words(n)
        if words & vw:
            return v, "proximity"
    return None, None


def _containment_match(reg: dict, name: str) -> dict | None:
    """'Various Small Fires' -> 'Various Small Fires (VSF)': word-boundary
    containment (venues._anchor_hit), accepted only when the key is multi-word
    or the registry name starts with it (keeps 'Karma' from hitting
    'Karma International')."""
    v = venues._anchor_hit(reg, name)
    if v is None:
        return None
    key = tools._norm_venue(name)
    if len(key.split()) >= 2:
        return v
    for n in [v["name"]] + list(v.get("aliases") or []):
        if tools._norm_venue(n).startswith(key):
            return v
    return None


def id_collision(v: dict | None, name: str, website: str | None) -> str | None:
    """migrate_venues pattern: same id, different registrable domains."""
    if v is None or not website:
        return None
    d_new, d_old = venues.registrable_domain(website), venues.registrable_domain(v.get("website"))
    if d_new and d_old and d_new != d_old and venues.venue_id(name) == v["id"]:
        return f"{v['id']}: {v['website']} vs {website} ({name!r})"
    return None


def patch_for(v: dict | None, full: dict) -> dict:
    """New venue: the whole patch. Existing venue: only fields it lacks, plus
    the always-merged blocks; never status / neighborhood / next_check."""
    if v is None:
        return {k: val for k, val in full.items() if val is not None}
    out: dict = {}
    has_coords = v.get("latitude") is not None and v.get("longitude") is not None
    for k, val in full.items():
        if val is None or k in NEW_ONLY_FIELDS:
            continue
        if k in ALWAYS_MERGE_FIELDS:
            out[k] = val
        elif k in ("latitude", "longitude", "coords_source"):
            if not has_coords:
                out[k] = val
        elif k == "is_museum":
            if val and not v.get(k):
                out[k] = True
        elif not v.get(k):
            out[k] = val
    return out


def seed_block(v: dict | None, source: str, info: dict) -> dict:
    """sources.seed.<source> without clobbering other sources' seed blocks
    (merge_patch merges `sources` only one level deep)."""
    seed = dict(((v or {}).get("sources") or {}).get("seed") or {})
    seed[source] = info
    return {"seed": seed}


# --- run context + reports ----------------------------------------------------------

class Ctx:
    """One run: config, API key, a working copy of the registry (dry-runs
    mutate only this; --apply also writes through venues.bulk_upsert)."""

    def __init__(self, city: str, apply: bool = False, zones: list[str] | None = None,
                 places_api: str = "auto", lookup: bool = False,
                 max_places_requests: int | None = None, refetch: bool = False,
                 today: date | None = None, key: str | None = None, quiet: bool = False):
        self.city, self.cfg = city, CITIES[city]
        self.quiet = quiet
        self.city_name = self.cfg["display_name"]
        self.apply, self.zones = apply, (list(zones) if zones else None)
        self.places_api, self.lookup = places_api, lookup
        self.max_places_requests, self.refetch = max_places_requests, refetch
        self.today = today or date.today()
        self.key = key if key is not None else os.environ.get("GOOGLE_MAPS_API_KEY")
        self.reg = venues.load_registry(city)
        self.stats: dict = {"requests": {}, "cost_usd": 0.0}
        self.areas_geo: dict[str, dict[str, dict | None]] = {}
        self._fetcher = None

    @property
    def fetcher(self):
        if self._fetcher is None:
            self._fetcher = refresh.Fetcher()
        return self._fetcher

    def zone_ok(self, zone: str | None) -> bool:
        return not self.zones or zone in self.zones


def new_report(source: str) -> dict:
    return {"source": source, "new": [], "updated": [], "skipped": [], "zone_unresolved": [],
            "ambiguous": [], "collisions": [], "notes": []}


def _record_collision(rep: dict, v: dict | None, name: str, website: str | None) -> None:
    c = id_collision(v, name, website)
    if c and c not in rep["collisions"]:
        rep["collisions"].append(c)


def commit(ctx: Ctx, rep: dict, entries: list[dict], source_label: str) -> None:
    """Preview every entry on the working copy (so later sources in the same
    run see them); with --apply also write them under one lock."""
    rows = [{"name": e["name"], "patch": e["patch"], "source": source_label,
             "website": e.get("website")} for e in entries]
    for e, row in zip(entries, rows):
        if not e.get("existing"):
            # A row computed as "new" can still land on a venue created a few
            # rows earlier in this batch (same domain): then it is an update
            # and must not carry the new-only fields.
            prior = venues.find_venue(ctx.reg, row["name"], row["website"] or row["patch"].get("website"),
                                      add_alias=False)
            if prior is not None:
                for k in NEW_ONLY_FIELDS:
                    row["patch"].pop(k, None)
                e["existing"], e["how"] = True, f"batch:{prior['id']}"
        v, created = venues._upsert_in(ctx.reg, row["name"], row["patch"], row["source"], row["website"])
        e["id"], e["created"] = v["id"], created
    if ctx.apply and rows:
        res = venues.bulk_upsert(ctx.city, rows)
        remaining = list(res["new"])
        for e in entries:
            if e["id"] in remaining:
                remaining.remove(e["id"])
                e["created"] = True
            else:
                e["created"] = False
    for e in entries:
        (rep["new"] if e["created"] else rep["updated"]).append(e)
        if e["created"] and not e.get("zone"):
            rep["zone_unresolved"].append(e)
        if e.get("ambiguous"):
            rep["ambiguous"].append(e)


def ensure_areas(ctx: Ctx, zones: list[str]) -> None:
    for zone in zones:
        areas = ((ctx.cfg.get("zones") or {}).get(zone) or {}).get("areas") or []
        bucket = ctx.areas_geo.setdefault(zone, {})
        for a in areas:
            if a in bucket:
                continue
            bucket[a] = geocode_area(ctx.key, a, ctx.city_name, ctx.city, ctx.stats) if ctx.key else None


def fetch_html(ctx: Ctx, url: str) -> str | None:
    """Polite fetch through refresh.Fetcher, cached HTML_CACHE_DAYS."""
    query = {"op": "html", "url": url}
    k = _cache_key(query)
    if not ctx.refetch:
        c = _cache_get(ctx.city, k, HTML_CACHE_DAYS)
        if c is not None:
            return c["data"]
    r = ctx.fetcher.get(url)
    if r.get("status") == 200 and r.get("html"):
        _cache_put(ctx.city, k, query, r["html"])
        return r["html"]
    return None


# --- source: Google Places ---------------------------------------------------------

def plan_places(ctx: Ctx) -> list[dict]:
    zones = ctx.zones or list(ctx.cfg.get("zones") or {})
    ensure_areas(ctx, zones)
    use = _resolve_api(ctx.places_api)
    plan = []
    for zone in zones:
        for area, g in ctx.areas_geo.get(zone, {}).items():
            if not g:
                continue
            for t in PLACES_TYPES:
                q = _nearby_query(use, g["lat"], g["lng"], g["radius_m"], t)
                plan.append({"zone": zone, "area": area, "lat": g["lat"], "lng": g["lng"],
                             "radius_m": g["radius_m"], "type": t, "depth": 0,
                             "cached": _cache_get(ctx.city, _cache_key(q), PLACES_CACHE_DAYS) is not None})
    return plan


def _print_places_plan(ctx: Ctx, rep: dict) -> None:
    plan = rep["plan"]
    print(f"Places plan ({ctx.city}): {plan['requests']} nearby request(s) over "
          f"{plan['areas']} area(s) x {len(PLACES_TYPES)} types; {plan['cached']} cached")
    print(f"  est. cost of the {plan['uncached']} uncached: Places (New) ${plan['est_new_usd']:.2f} "
          f"({COST_USD['nearby_new']}/req) | legacy ${plan['est_legacy_usd']:.2f} "
          f"({COST_USD['nearby_legacy']}/req) + ${COST_USD['details_legacy']} details per new venue; "
          f"saturated circles add up to 4 sub-requests each")
    if plan["unresolved_areas"]:
        print(f"  areas that did not geocode: {', '.join(plan['unresolved_areas'])}")
    print(_table(plan["items"], [("zone", "zone"), ("area", "area"), ("type", "type"),
                                 ("lat", "lat"), ("lng", "lng"), ("r_m", "radius_m"),
                                 ("cached", "cached")], indent=2))


def seed_places(ctx: Ctx, rep: dict) -> None:
    if not ctx.key:
        rep["notes"].append("GOOGLE_MAPS_API_KEY not set - places skipped")
        return
    plan = plan_places(ctx)
    n_uncached = sum(1 for p in plan if not p["cached"])
    unresolved_areas = [f"{z}/{a}" for z, areas in ctx.areas_geo.items() for a, g in areas.items()
                        if not g and (not ctx.zones or z in ctx.zones)]
    rep["plan"] = {"requests": len(plan), "cached": len(plan) - n_uncached, "uncached": n_uncached,
                   "areas": len({(p["zone"], p["area"]) for p in plan}),
                   "est_new_usd": round(n_uncached * COST_USD["nearby_new"], 2),
                   "est_legacy_usd": round(n_uncached * COST_USD["nearby_legacy"], 2),
                   "unresolved_areas": unresolved_areas, "items": plan}
    if not ctx.quiet:
        _print_places_plan(ctx, rep)   # always BEFORE any paid Places call
    limit = ctx.max_places_requests
    if limit is None and not ctx.apply:
        rep["notes"].append("dry-run without --max-places-requests: plan only, no paid Places calls made")
        return
    queue = list(plan)
    found: dict[str, dict] = {}
    made, skipped_budget, executed = 0, 0, []
    while queue:
        item = queue.pop(0)
        if not item["cached"] and limit is not None and made >= limit:
            skipped_budget += 1
            continue
        info: dict = {}
        try:
            places = places_nearby(ctx.key, item["lat"], item["lng"], item["radius_m"], item["type"],
                                   ctx.places_api, ctx.city, ctx.stats, info,
                                   None if limit is None else max(1, limit - made))
        except Exception as exc:
            rep["notes"].append(f"nearby failed ({item['area']}/{item['type']}): {str(exc)[:160]}")
            continue
        made += info.get("paid", 0)
        executed.append({**item, **info})
        for p in places:
            found.setdefault(p["place_id"], {**p, "area": item["area"], "zone_hint": item["zone"],
                                             "qtype": item["type"]})
        if info.get("saturated") and item["depth"] < SPLIT_MAX_DEPTH:
            for sub in _split_circle(item):
                q = _nearby_query(info["api"], sub["lat"], sub["lng"], sub["radius_m"], sub["type"])
                sub["cached"] = _cache_get(ctx.city, _cache_key(q), PLACES_CACHE_DAYS) is not None
                queue.append(sub)
    rep["executed"] = executed
    if skipped_budget:
        rep["notes"].append(f"--max-places-requests {limit} reached: {skipped_budget} planned "
                            f"request(s) not made")
    api_used = executed[0]["api"] if executed else _resolve_api(ctx.places_api)
    rep["notes"].append(f"{len(executed)} nearby search(es) via Places ({api_used}), "
                        f"{made} paid HTTP request(s); {len(found)} distinct places")
    labeled = labeled_points(ctx.reg, ctx.areas_geo)
    entries: list[dict] = []
    now = int(time.time())
    museum_at = {_addr_key(p.get("address")): p["name"] for p in found.values()
                 if place_kind(p) == "museum" and not place_skip_reason(p) and _addr_key(p.get("address"))}
    for p in found.values():
        reason = place_skip_reason(p)
        if reason:
            rep["skipped"].append({"name": p["name"], "reason": reason, "area": p["area"]})
            continue
        host = museum_at.get(_addr_key(p.get("address")))
        if host and place_kind(p) == "gallery" and host != p["name"]:
            # a wing / named gallery inside a museum (same street address)
            rep["skipped"].append({"name": p["name"], "reason": f"inside museum: {host}", "area": p["area"]})
            continue
        zone, via, dist, amb = assign_zone(p["lat"], p["lng"], labeled)
        if not ctx.zone_ok(zone):
            rep["skipped"].append({"name": p["name"], "reason": f"zone {zone} not requested",
                                   "area": p["area"]})
            continue
        v, how = match_registry(ctx.reg, p["name"], p.get("website"), p["lat"], p["lng"])
        if v is None and api_used == "legacy" and ctx.apply:
            det = None
            try:
                det = place_details_legacy(ctx.key, p["place_id"], ctx.city, ctx.stats)
            except Exception as exc:
                rep["notes"].append(f"details failed ({p['name']}): {str(exc)[:120]}")
            if det:
                p.update({k: det[k] for k in ("website", "phone", "hours", "status") if det.get(k)})
                if det.get("address"):
                    p["address"] = det["address"]
                if place_skip_reason(p):
                    rep["skipped"].append({"name": p["name"], "reason": place_skip_reason(p),
                                           "area": p["area"]})
                    continue
                v, how = match_registry(ctx.reg, p["name"], p.get("website"), p["lat"], p["lng"])
        _record_collision(rep, v, p["name"], p.get("website"))
        kind = place_kind(p)
        full = {
            "kind": kind, "is_museum": kind == "museum", "status": "unknown",
            "neighborhood": zone, "address": _clean_address(p.get("address")),
            "latitude": p["lat"], "longitude": p["lng"], "coords_source": "places",
            "website": p.get("website"), "phone": p.get("phone"), "hours": p.get("hours") or None,
            "google": {"status": p.get("status"), "address": p.get("address"),
                       "hours": p.get("hours"), "phone": p.get("phone"), "website": p.get("website"),
                       "lat": p["lat"], "lng": p["lng"], "ts": now},
            "notes": f"seeded from Google Places ({p['area']}/{p['qtype']})",
            "next_check": ctx.today.isoformat(),
            "sources": seed_block(v, "places", {"ts": now, "place_id": p["place_id"], "area": p["area"],
                                                "type": p["qtype"], "zone_by": via, "ambiguous": amb}),
        }
        patch = patch_for(v, full)
        name = p["name"]
        if v is not None and how == "proximity":
            name = v["name"]
            patch["aliases"] = [p["name"]]
        entries.append({"name": name, "source_name": p["name"], "website": p.get("website"),
                        "patch": patch, "zone": zone, "via": via, "dist_m": dist, "ambiguous": amb,
                        "existing": v is not None, "how": how, "area": p["area"], "kind": kind,
                        "address": _clean_address(p.get("address"))})
    commit(ctx, rep, entries, "seed-places")


# --- source: Gallery Platform LA ----------------------------------------------------

GPLA_LINK_RE = re.compile(r'<a href="https://galleryplatform\.la/galleries/([a-z0-9-]+)"[^>]*>\s*([^<]+?)\s*</a>')
GPLA_PILL_RE = re.compile(r'ui-pill">\s*([^<]+?)\s*</span>')
GPLA_ROW_RE = re.compile(r'details-table--row-title">\s*([A-Za-z ]+?)\s*</div>\s*<div[^>]*>(.*?)</div>\s*</div>',
                         re.S)


def parse_gpla_list(html: str) -> list[dict]:
    """[{slug, name, region}] from galleryplatform.la/galleries (server-rendered)."""
    out, seen = [], set()
    for m in GPLA_LINK_RE.finditer(html or ""):
        slug, name = m.group(1), re.sub(r"\s+", " ", _html.unescape(m.group(2))).strip()
        if slug in seen or not name:
            continue
        seen.add(slug)
        tail = html[m.end(): m.end() + 800]
        nxt = tail.find('href="https://galleryplatform.la/galleries/')
        if nxt >= 0:
            tail = tail[:nxt]
        pm = GPLA_PILL_RE.search(tail)
        out.append({"slug": slug, "name": name, "region": pm.group(1).strip() if pm else None})
    return out


def parse_gpla_detail(html: str) -> dict:
    """{website, phone, address, hours} from a /galleries/<slug> details-table."""
    out: dict = {"website": None, "phone": None, "address": None, "hours": []}
    for title, body in GPLA_ROW_RE.findall(html or ""):
        t = title.strip().lower()
        if t == "links":
            for href in re.findall(r'href="(https?://[^"]+)"', body):
                if venues.registrable_domain(href):     # skips instagram/facebook
                    out["website"] = _html.unescape(href)
                    break
        elif t == "contact":
            m = re.search(r'href="tel:[^"]*"[^>]*>\s*([^<]+?)\s*<', body)
            if m:
                out["phone"] = _strip_tags(m.group(1))
        elif t == "address":
            out["address"] = _strip_tags(body) or None
        elif t == "hours":
            lines = [_strip_tags(x) for x in re.findall(r'<div class="space-x-5">(.*?)(?:</div>|$)', body, re.S)]
            lines = [ln for ln in lines if ln]
            out["hours"] = lines or ([_strip_tags(body)] if _strip_tags(body) else [])
    return out


def seed_gpla(ctx: Ctx, rep: dict) -> None:
    html = fetch_html(ctx, GPLA_LIST_URL)
    if not html:
        rep["notes"].append("GPLA list fetch failed")
        return
    rows = parse_gpla_list(html)
    rep["notes"].append(f"GPLA list: {len(rows)} galleries")
    ensure_areas(ctx, list(ctx.cfg.get("zones") or {}))
    labeled = labeled_points(ctx.reg, ctx.areas_geo)
    entries: list[dict] = []
    for r in rows:
        dh = fetch_html(ctx, GPLA_DETAIL_URL.format(slug=r["slug"]))
        if not dh:
            rep["notes"].append(f"detail fetch failed: {r['slug']}")
        d = parse_gpla_detail(dh) if dh else {"website": None, "phone": None, "address": None, "hours": []}
        geo = None
        if ctx.key and d.get("address"):
            geo = geocode_address(ctx.key, d["address"], ctx.city_name, ctx.city, ctx.stats)
        zone = via = dist = None
        amb = False
        if geo:
            zone, via, dist, amb = assign_zone(geo["lat"], geo["lng"], labeled)
        v, how = match_registry(ctx.reg, r["name"], d.get("website"),
                                geo["lat"] if geo else None, geo["lng"] if geo else None)
        _record_collision(rep, v, r["name"], d.get("website"))
        if not ctx.zone_ok(zone) and not (v is not None and ctx.zone_ok(v.get("neighborhood"))):
            shown = (v.get("neighborhood") if v is not None else None) or zone
            rep["skipped"].append({"name": r["name"], "reason": f"zone {shown} not requested"})
            continue
        now = int(time.time())
        full = {
            "kind": "gallery", "status": "unknown", "neighborhood": zone,
            "address": d.get("address"),
            "latitude": geo["lat"] if geo else None, "longitude": geo["lng"] if geo else None,
            "coords_source": "geocode" if geo else None,
            "website": d.get("website"), "phone": d.get("phone"), "hours": d.get("hours") or None,
            "notes": f"GPLA region: {r['region']}" if r.get("region") else None,
            "next_check": ctx.today.isoformat(),
            "sources": seed_block(v, "gpla", {"ts": now, "slug": r["slug"], "region": r.get("region")}),
        }
        patch = patch_for(v, full)
        name = r["name"]
        if v is not None and how == "proximity":
            name = v["name"]
            patch["aliases"] = [r["name"]]
        entries.append({"name": name, "source_name": r["name"], "website": d.get("website"),
                        "patch": patch, "zone": zone, "via": via, "dist_m": dist, "ambiguous": amb,
                        "existing": v is not None, "how": how, "address": d.get("address"),
                        "region": r.get("region"), "geocoded": bool(geo)})
    commit(ctx, rep, entries, "seed-gpla")


# --- source: Carla distribution list ----------------------------------------------

CARLA_GROUP_RE = re.compile(r'<div class="sub-title[^"]*">\s*([^<]+?)\s*</div>')
CARLA_LINK_RE = re.compile(r'<a\s+href\s*=\s*[“"\']?\s*([^"”\'>\s]+)[”"\']?\s*>\s*(.*?)\s*</a>', re.S)


def carla_deny_reason(row: dict) -> str | None:
    m = LIST_NAME_DENY_RE.search(row.get("name") or "")
    if m:
        return f"name:{m.group(0).lower()}"
    dom = venues.registrable_domain(row.get("website")) or ""
    label = dom.split(".")[0] if dom else ""
    m = LIST_NAME_DENY_RE.search(label)
    if m and label:
        return f"domain:{m.group(0).lower()}"
    return None


def parse_carla(html: str, deny: bool = True) -> list[dict]:
    """[{name, website, group}] from <section id="distribution">; 'Outside
    L.A.' dropped, trailing branch tags like '(Wilshire)' stripped, and (by
    default) bookstores / framers / theatres etc. removed."""
    i = (html or "").find('id="distribution"')
    if i < 0:
        return []
    sec = html[i:]
    j = sec.find("</section>")
    if j > 0:
        sec = sec[:j]
    parts = CARLA_GROUP_RE.split(sec)
    out, seen = [], set()
    for g in range(1, len(parts) - 1, 2):
        group = _html.unescape(parts[g]).strip()
        if group.lower().startswith("outside"):
            continue
        for m in CARLA_LINK_RE.finditer(parts[g + 1]):
            url = m.group(1).strip()
            name = BRANCH_TAG_RE.sub("", _strip_tags(m.group(2))).strip()
            if not name or not url.startswith("http"):
                continue
            row = {"name": name, "website": url, "group": group}
            if deny and carla_deny_reason(row):
                continue
            key = (tools._norm_venue(name), venues.registrable_domain(url))
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def seed_carla(ctx: Ctx, rep: dict) -> None:
    html = fetch_html(ctx, CARLA_URL)
    if not html:
        rep["notes"].append("Carla fetch failed")
        return
    rows = parse_carla(html, deny=False)
    rep["notes"].append(f"Carla distribution: {len(rows)} LA entries")
    idx = _places_cache_index(ctx.city)
    ensure_areas(ctx, list(ctx.cfg.get("zones") or {}))
    labeled = labeled_points(ctx.reg, ctx.areas_geo)
    entries: list[dict] = []
    for r in rows:
        reason = carla_deny_reason(r)
        if reason:
            rep["skipped"].append({"name": r["name"], "reason": reason, "group": r["group"]})
            continue
        v, how = match_registry(ctx.reg, r["name"], r["website"])
        _record_collision(rep, v, r["name"], r["website"])
        zone = via = dist = None
        amb = False
        coords = None
        zone_by = None
        google = None
        address = None
        if v is None:
            dom = venues.registrable_domain(r["website"])
            p = (idx["domain"].get(dom) if dom else None) or idx["name"].get(tools._norm_venue(r["name"]))
            if p:
                coords, zone_by = (p["lat"], p["lng"]), "places_cache"
                address = _clean_address(p.get("address"))
            elif ctx.lookup and ctx.key:
                g = crosscheck.google_lookup(ctx.key, r["name"], "", ctx.city_name)
                _count(ctx.stats, "lookup")
                if g.get("found") and g.get("lat") is not None and (_words(r["name"]) & _words(g.get("name"))):
                    coords, zone_by = (g["lat"], g["lng"]), "lookup"
                    address = _clean_address(g.get("address"))
                    google = {"status": g.get("status"), "address": g.get("address"),
                              "hours": g.get("hours"), "phone": g.get("phone"),
                              "website": g.get("website"), "lat": g["lat"], "lng": g["lng"],
                              "ts": int(time.time())}
            if coords:
                zone, via, dist, amb = assign_zone(coords[0], coords[1], labeled)
        if not ctx.zone_ok(zone) and not (v is not None and ctx.zone_ok(v.get("neighborhood"))):
            shown = (v.get("neighborhood") if v is not None else None) or zone
            rep["skipped"].append({"name": r["name"], "reason": f"zone {shown} not requested",
                                   "group": r["group"]})
            continue
        now = int(time.time())
        full = {
            "kind": "gallery", "status": "unknown", "neighborhood": zone, "website": r["website"],
            "address": address,
            "latitude": coords[0] if coords else None, "longitude": coords[1] if coords else None,
            "coords_source": ("places" if zone_by == "places_cache" else "lookup") if coords else None,
            "google": google, "notes": f"Carla distribution: {r['group']}",
            "next_check": ctx.today.isoformat(),
            "sources": seed_block(v, "carla", {"ts": now, "group": r["group"], "zone_by": zone_by}),
        }
        entries.append({"name": r["name"], "source_name": r["name"], "website": r["website"],
                        "patch": patch_for(v, full), "zone": zone, "via": via, "dist_m": dist,
                        "ambiguous": amb, "existing": v is not None, "how": how,
                        "group": r["group"], "zone_by": zone_by,
                        "existing_zone": v.get("neighborhood") if v else None})
    commit(ctx, rep, entries, "seed-carla")


# --- source: evidence cache backfill ---------------------------------------------

class _Trace:
    """The three attributes venues.on_fetch reads from a SessionTrace."""

    def __init__(self, label: str | None, zone: str | None):
        self.label, self.zone = label, zone
        self.seen_domains: set[str] = set()


def session_zone(session: str | None, cfg: dict) -> str | None:
    """'deep-los-angeles-pasadena-san-gabriel-1788...' -> 'Pasadena/San Gabriel'."""
    if not session:
        return None
    base = "-" + re.sub(r"-\d+$", "", session) + "-"   # also 'deep-<city>-<zone>-<tag>-<ts>'
    best = None
    for zone in cfg.get("neighborhoods", []):
        slug = _slugify(zone)
        if ("-" + slug + "-") in base and (best is None or len(slug) > len(_slugify(best))):
            best = zone       # longest slug wins: 'west-hollywood-fairfax' over 'hollywood'
    return best


def _evidence_file(path: str) -> Path | None:
    p = Path(path)
    if p.is_absolute():
        return p if p.exists() else None
    for base in (venues.SCRAPER_DIR, venues.SCRAPER_DIR / ".cache",
                 venues.EVIDENCE_DIR.parent.parent, venues.EVIDENCE_DIR.parent):
        c = base / p
        if c.exists():
            return c
    return None


def load_evidence_rows(city: str, kinds: tuple[str, ...] = ("web_fetch", "client_html")) -> dict[str, dict]:
    """Latest index row per URL for this city (kinds with page text)."""
    latest: dict[str, dict] = {}
    idx = venues.EVIDENCE_INDEX
    if not idx.exists():
        return latest
    for line in idx.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("city") != city or r.get("kind") not in kinds or not r.get("url", "").startswith("http"):
            continue
        if r["url"] not in latest or (r.get("ts") or 0) > (latest[r["url"]].get("ts") or 0):
            latest[r["url"]] = r
    return latest


def seed_evidence(ctx: Ctx, rep: dict) -> None:
    rows = load_evidence_rows(ctx.city)
    rep["notes"].append(f"evidence index: {len(rows)} fetched URL(s) for {ctx.city}")
    deny = venues._candidate_deny(ctx.city)
    decided: set[str] = set()
    trace = _Trace(None, None)
    by_dom: dict[str, list[dict]] = {}
    for url, row in rows.items():
        dom = venues.registrable_domain(url)
        if dom:
            by_dom.setdefault(dom, []).append(row)
    ordered: list[tuple[str, dict]] = []
    for dom in sorted(by_dom):
        # listing-like pages first (their URL becomes the exhibitions candidate), then newest
        group = sorted(by_dom[dom], key=lambda r: (-venues._path_score(r["url"])[0], -(r.get("ts") or 0)))
        ordered.extend((r["url"], r) for r in group)
    for url, row in ordered:
        dom = venues.registrable_domain(url)
        if not dom or dom in decided:
            continue
        f = _evidence_file(row.get("path") or "")
        if f is None:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        zone = session_zone(row.get("session"), ctx.cfg)
        cand = venues.candidate_from_page(url, text, ctx.cfg, deny)
        if cand is None:
            # Weaker, registry-backed rule: a page whose site name is a venue
            # we already list (without a website) still attaches the website.
            name = venues._page_name(url, text) if venues._denied_domain(dom, deny) is False else None
            v = _containment_match(ctx.reg, name) if name and len(text) >= 200 else None
            if v is None or v.get("website") or venues.NOT_VENUE_RE.search(name):
                continue
            scheme = "https" if url.startswith("https") else "http"
            cand = {"name": name, "website": f"{scheme}://{dom}/", "url": url, "domain": dom,
                    "weak": True}
        decided.add(dom)
        cand["name"] = _html.unescape(cand["name"]).strip()
        if not ctx.zone_ok(zone):
            rep["skipped"].append({"name": cand["name"], "reason": f"zone {zone} not requested", "url": url})
            continue
        v = venues.find_venue(ctx.reg, cand["name"], url, add_alias=False)
        matched_via = "registry" if v is not None else None
        if v is None:
            v = _containment_match(ctx.reg, cand["name"])
            matched_via = "containment" if v is not None else None
        same_domain = next((o for o in ctx.reg["venues"]
                            if venues.registrable_domain(o.get("website")) == dom), None)
        if v is not None:
            action = "attach_website" if not v.get("website") else "known"
        elif same_domain is not None:
            action, v = "alias", same_domain
        else:
            action = "candidate"
        entry = {"name": cand["name"], "website": cand["website"], "url": url, "zone": zone,
                 "session": row.get("session"), "action": action, "id": v["id"] if v else None,
                 "matched_via": matched_via, "weak": bool(cand.get("weak"))}
        if action == "known":
            rep["skipped"].append({"name": cand["name"], "reason": f"already in registry ({v['id']})",
                                   "url": url})
            continue
        if action == "candidate" and (EVIDENCE_NAME_DENY_RE.search(cand["name"]) or dom.endswith(".gov")):
            rep["skipped"].append({"name": cand["name"], "reason": "generic page name / civic site", "url": url})
            continue
        if ctx.apply:
            if action == "attach_website" and matched_via == "containment":
                # on_fetch matches by id/alias/domain only: teach it the alias
                # + website first, then let it attach the listing URL.
                venues.bulk_upsert(ctx.city, [{"name": v["name"], "source": "seed-evidence",
                                               "patch": {"website": cand["website"],
                                                         "aliases": [cand["name"]]}}])
                venues.merge_patch(v, {"website": cand["website"], "aliases": [cand["name"]]})
            if cand.get("weak") and action == "attach_website":
                if matched_via != "containment":
                    venues.bulk_upsert(ctx.city, [{"name": v["name"], "source": "seed-evidence",
                                                   "patch": {"website": cand["website"]}}])
                    venues.merge_patch(v, {"website": cand["website"]})
                entry["result"] = f"attached:{v['id']}"
            else:
                trace.label, trace.zone = row.get("session"), zone
                trace.seen_domains.discard(dom)
                entry["result"] = venues.on_fetch(ctx.city, url, text, trace, ctx.cfg)
                if entry["result"] is None and action == "attach_website":
                    entry["result"] = f"attached:{v['id']}"
        else:
            # mirror on the working copy so --status and later logic see it
            if action == "attach_website":
                venues.merge_patch(v, {"website": cand["website"], "aliases": [cand["name"]]})
            elif action == "alias":
                venues.merge_patch(v, {"aliases": [cand["name"]]})
            elif action == "candidate":
                nv = venues.empty_venue(venues.venue_id(cand["name"]), cand["name"])
                nv.update({"status": "candidate", "kind": "other", "website": cand["website"],
                           "neighborhood": zone, "next_check": ctx.today.isoformat(),
                           "notes": f"auto-candidate from {row.get('session') or 'backfill'}"})
                nv["sources"]["candidate"] = {"session": row.get("session"), "first_url": url,
                                              "ts": int(time.time()),
                                              "zone_source": "session" if zone else None}
                ctx.reg["venues"].append(nv)
                entry["id"] = nv["id"]
        if action == "candidate":
            entry["created"] = True
            rep["new"].append(entry)
            if not zone:
                rep["zone_unresolved"].append(entry)
        else:
            entry["created"] = False
            rep["updated"].append(entry)


# --- --resolve-zones ---------------------------------------------------------------

def resolve_zones(ctx: Ctx, rep: dict) -> None:
    """Give a zone to registry venues that have none: coordinates -> google
    block -> geocoded address -> (--lookup) Places text search."""
    ensure_areas(ctx, list(ctx.cfg.get("zones") or {}))
    labeled = labeled_points(ctx.reg, ctx.areas_geo)
    entries: list[dict] = []
    for v in list(ctx.reg["venues"]):
        if v.get("neighborhood") or v.get("status") in ("out_of_scope", "duplicate"):
            continue
        lat, lng, src, google = v.get("latitude"), v.get("longitude"), "coords", None
        if lat is None and (v.get("google") or {}).get("lat") is not None:
            lat, lng, src = v["google"]["lat"], v["google"]["lng"], "google"
        if lat is None and v.get("address") and ctx.key:
            geo = geocode_address(ctx.key, v["address"], ctx.city_name, ctx.city, ctx.stats)
            if geo:
                lat, lng, src = geo["lat"], geo["lng"], "geocode"
        if lat is None and ctx.lookup and ctx.key:
            g = crosscheck.google_lookup(ctx.key, v["name"], v.get("address") or "", ctx.city_name)
            _count(ctx.stats, "lookup")
            if g.get("found") and g.get("lat") is not None and (_words(v["name"]) & _words(g.get("name"))):
                lat, lng, src = g["lat"], g["lng"], "lookup"
                google = {"status": g.get("status"), "address": g.get("address"), "hours": g.get("hours"),
                          "phone": g.get("phone"), "website": g.get("website"), "lat": g["lat"],
                          "lng": g["lng"], "ts": int(time.time())}
        if lat is None:
            rep["zone_unresolved"].append({"name": v["name"], "id": v["id"], "reason": "no coordinates"})
            continue
        zone, via, dist, amb = assign_zone(lat, lng, labeled)
        if zone is None:
            rep["zone_unresolved"].append({"name": v["name"], "id": v["id"],
                                           "reason": f"{dist} m from nearest labelled point"})
            continue
        if not ctx.zone_ok(zone):
            rep["skipped"].append({"name": v["name"], "reason": f"zone {zone} not requested"})
            continue
        patch = {"neighborhood": zone, "google": google,
                 "sources": {"zone_resolved": {"ts": int(time.time()), "via": via, "from": src,
                                               "dist_m": dist, "ambiguous": amb}}}
        if v.get("latitude") is None:
            patch.update({"latitude": lat, "longitude": lng, "coords_source": src})
        entries.append({"name": v["name"], "website": v.get("website"), "patch": patch, "zone": zone,
                        "via": via, "dist_m": dist, "ambiguous": amb, "existing": True, "how": src})
    commit(ctx, rep, entries, "seed-resolve")


# --- --status ----------------------------------------------------------------------

def status_rows(ctx: Ctx) -> list[dict]:
    return [venues.zone_coverage(ctx.city, z, ctx.cfg, reg=ctx.reg)
            for z in ctx.cfg.get("neighborhoods", [])]


# --- printing ----------------------------------------------------------------------

def _table(rows: list[dict], cols: list[tuple[str, str]], indent: int = 2, max_w: int = 44,
           limit: int | None = None) -> str:
    if not rows:
        return " " * indent + "(none)"
    shown = rows if limit is None else rows[:limit]
    cells = []
    for r in shown:
        line = []
        for _, key in cols:
            val = r.get(key)
            if isinstance(val, float):
                val = f"{val:.5f}"
            elif isinstance(val, bool):
                val = "yes" if val else "no"
            elif val is None:
                val = "-"
            s = str(val).replace("\n", " ")
            line.append(s if len(s) <= max_w else s[:max_w - 1] + "…")
        cells.append(line)
    widths = [max(len(h), *(len(c[i]) for c in cells)) for i, (h, _) in enumerate(cols)]
    pad = " " * indent
    out = [pad + "  ".join(h.ljust(widths[i]) for i, (h, _) in enumerate(cols))]
    for line in cells:
        out.append(pad + "  ".join(c.ljust(widths[i]) for i, c in enumerate(line)))
    if limit is not None and len(rows) > limit:
        out.append(pad + f"... {len(rows) - limit} more")
    return "\n".join(out)


def print_report(rep: dict, ctx: Ctx) -> None:
    mode = "apply" if ctx.apply else "dry-run"
    print(f"\n=== {rep['source']} ({mode}) ===")
    for n in rep["notes"]:
        print(f"  note: {n}")
    base_cols = [("id", "id"), ("name", "name"), ("zone", "zone"), ("via", "via"), ("m", "dist_m")]
    if rep["source"] == "evidence":
        cols = [("id", "id"), ("name", "name"), ("zone", "zone"), ("action", "action"),
                ("matched", "matched_via"), ("website", "website")]
    elif rep["source"] == "carla":
        cols = base_cols + [("group", "group"), ("zone_by", "zone_by"), ("website", "website")]
    elif rep["source"] == "gpla":
        cols = base_cols + [("region", "region"), ("address", "address"), ("website", "website")]
    elif rep["source"] == "places":
        cols = base_cols + [("kind", "kind"), ("area", "area"), ("address", "address"), ("website", "website")]
    else:
        cols = base_cols + [("from", "how")]
    print(f"new ({len(rep['new'])}):")
    print(_table(rep["new"], cols))
    if rep["source"] == "evidence":
        upd_cols = cols
    elif rep["source"] == "resolve_zones":
        upd_cols = cols
    else:
        upd_cols = [("id", "id"), ("name", "name"), ("as", "source_name"), ("how", "how"),
                    ("existing zone", "existing_zone"), ("fields", "fields")]
        for e in rep["updated"]:
            e.setdefault("existing_zone", None)
            e["fields"] = ",".join(k for k in e["patch"] if k not in ("sources",)) or "-"
    print(f"updated ({len(rep['updated'])}):")
    print(_table(rep["updated"], upd_cols, limit=80))
    print(f"skipped ({len(rep['skipped'])}):")
    print(_table(rep["skipped"], [("name", "name"), ("reason", "reason")], limit=60))
    print(f"zone_unresolved ({len(rep['zone_unresolved'])}):")
    print(_table(rep["zone_unresolved"], [("id", "id"), ("name", "name"), ("website", "website"),
                                          ("reason", "reason")]))
    print(f"ambiguous ({len(rep['ambiguous'])}):")
    print(_table(rep["ambiguous"], [("id", "id"), ("name", "name"), ("zone", "zone"), ("via", "via"),
                                    ("m", "dist_m")]))
    if rep["collisions"]:
        print(f"id collisions ({len(rep['collisions'])}) - same id, different domain; website NOT overwritten:")
        for c in rep["collisions"]:
            print(f"  {c}")


def print_status(rows: list[dict]) -> None:
    print("\n=== zone coverage (venues.zone_coverage; anchors from cities.py) ===")
    for r in rows:
        r["missing"] = ", ".join(r["missing_anchors"]) or "-"
        r["why"] = ",".join(r["reasons"]) or "-"
    print(_table(rows, [("zone", "zone"), ("ok", "ok"), ("reg", "registry"), ("enum", "enumerated"),
                        ("seed", "seeded"), ("cand", "candidates"), ("overlap", "overlap"),
                        ("reasons", "why"), ("missing anchors", "missing")], max_w=120))


# --- entry points ------------------------------------------------------------------

def run(city: str, sources: list[str] | tuple[str, ...] = (), zones: list[str] | None = None,
        apply: bool = False, places_api: str = "auto", lookup: bool = False,
        resolve: bool = False, max_places_requests: int | None = None, refetch: bool = False,
        status: bool = False, quiet: bool = False, today: date | None = None) -> dict:
    """In-process entry point (run_deep can call this before enumeration).
    Returns {"city", "apply", "sources": {name: report}, "stats", "status"?}."""
    cfg = CITIES[city]
    bad = [s for s in sources if s not in SOURCES]
    if bad:
        raise ValueError(f"unknown sources {bad}; valid: {SOURCES}")
    if zones:
        bad = [z for z in zones if z not in cfg["neighborhoods"]]
        if bad:
            raise ValueError(f"unknown zones {bad}; valid: {cfg['neighborhoods']}")
    ctx = Ctx(city, apply=apply, zones=zones, places_api=places_api, lookup=lookup,
              max_places_requests=max_places_requests, refetch=refetch, today=today, quiet=quiet)
    out: dict = {"city": city, "apply": apply, "sources": {}, "stats": ctx.stats}
    runners = {"places": seed_places, "gpla": seed_gpla, "carla": seed_carla, "evidence": seed_evidence}
    for s in [s for s in SOURCES if s in sources]:
        rep = new_report(s)
        runners[s](ctx, rep)
        out["sources"][s] = rep
        if not quiet:
            print_report(rep, ctx)
    if resolve:
        rep = new_report("resolve_zones")
        resolve_zones(ctx, rep)
        out["sources"]["resolve_zones"] = rep
        if not quiet:
            print_report(rep, ctx)
    if status:
        out["status"] = status_rows(ctx)
        if not quiet:
            print_status(out["status"])
    if not quiet:
        print(f"\npaid Google requests this run: {ctx.stats['requests'] or 'none'}; "
              f"est. ${ctx.stats['cost_usd']:.2f}")
        if not apply and out["sources"]:
            print(f"dry run - nothing written to {venues._registry_file(city)}; pass --apply to write")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--zones", default=None, help='comma-separated zone subset, e.g. "Hollywood,Beverly Hills"')
    ap.add_argument("--sources", default="", help=f"comma-separated subset of {','.join(SOURCES)}")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(default) report only, write nothing")
    mode.add_argument("--apply", action="store_true", help="write the registry")
    ap.add_argument("--places-api", choices=("auto", "new", "legacy"), default="auto",
                    help="auto = Places (New), falling back to legacy on 403")
    ap.add_argument("--lookup", action="store_true",
                    help="Carla / --resolve-zones: Places text search (~$0.05) for venues with no zone")
    ap.add_argument("--resolve-zones", action="store_true",
                    help="assign a zone to registry venues that have none (coords/google/address/--lookup)")
    ap.add_argument("--status", action="store_true", help="print venues.zone_coverage per zone")
    ap.add_argument("--max-places-requests", type=int, default=None,
                    help="cap on paid nearby requests; in dry-run this is the ONLY way paid Places calls happen")
    ap.add_argument("--refetch", action="store_true", help="ignore the 1-day HTML cache (GPLA/Carla)")
    args = ap.parse_args()
    from run_scrape import load_env
    load_env()
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    zones = [z.strip() for z in args.zones.split(",")] if args.zones else None
    if not sources and not args.status and not args.resolve_zones:
        ap.error("nothing to do: pass --sources, --status and/or --resolve-zones")
    try:
        run(args.city, sources, zones, apply=args.apply, places_api=args.places_api,
            lookup=args.lookup, resolve=args.resolve_zones,
            max_places_requests=args.max_places_requests, refetch=args.refetch, status=args.status)
    except ValueError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
