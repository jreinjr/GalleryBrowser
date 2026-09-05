"""Quick city: LLM-seeded, Places-verified galleries for many cities at once.

The galleries-first pipeline (docs/GALLERIES.md) finds galleries by sweeping Places,
crawls every site into a dossier and ranks on evidence. That is a week of work per
city. This module reaches a city in an afternoon and leaves a registry the full
pipeline can flesh out later:

    cities    both models' top-25 gallery cities -> content/expansion/cities.json
    seed      both models' top-100 galleries of a city, deduped, blended rank
    validate  each name -> Places text search + own-site checks + stale-site check;
              everything lands in the registry, only the VERIFIED ones will publish
    rank      content/curation/<city>/venue_order.json (verified only, blended order)
              -> rank_venues.py apply
    shows     run_deep.py --venue-ids <verified> --force-due (its verify stage promotes
              in-window shows to content/<city>.json; upcoming ones wait in pending)
    status    counts per stage + an audit of published shows at non-verified venues

    python quick_city.py cities [--refresh]
    python quick_city.py seed --city seoul [--refresh]
    python quick_city.py validate --city seoul [--max-places-requests 260] [--apply]
    python quick_city.py rank --city seoul [--tiers 20,50] [--force] [--apply]
    python quick_city.py shows --city seoul [--total-budget 70] [--workers 3]
    python quick_city.py status [--city seoul]

Models: claude-opus-5 and OpenAI gpt-5.6-sol (llm_clients.py). The blended rank of a
gallery is the mean of its two positions; a list that omits it contributes one past its
own length, so a gallery only one model names lands in the back half.

VERIFIED (the publish gate for galleries) needs all of: a name-matched Places listing
inside the metro with businessStatus OPERATIONAL, an address, coordinates and opening
hours; the venue's own site live and reading as an art venue with no closed notice; a
dated show on that site newer than ~2 years; and the pin inside the city's map span.

Los Angeles is NEVER_WRITE: nothing here touches its registry or order file (LA ranks by the
client's hand list). Tokyo was protected until 2026-09-04, when its ranking was switched to the
LLM consensus as well.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crosscheck  # noqa: E402
import curation_store  # noqa: E402
import llm_clients  # noqa: E402
import rank_venues  # noqa: E402
import refresh  # noqa: E402
import seed_venues  # noqa: E402
import tools  # noqa: E402
import validate_venues  # noqa: E402
import venues  # noqa: E402
import verify_order  # noqa: E402
from cities import CITIES  # noqa: E402
from run_scrape import load_env  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXPANSION_DIR = tools.CONTENT_DIR / "expansion"
NEVER_WRITE = frozenset({"los-angeles"})   # LA ranks by the client's hand list; Tokyo joined the LLM ranking 2026-09-04
CITY_LIST_N = 25
GALLERY_LIST_N = 100
DEFAULT_TIERS = {"1": 20, "2": 50}
PLACES_BUDGET = 400              # ~130 names at LOOKUP_REQUESTS (2) each, plus the name-only retries
VERIFIED_FRESH_DAYS = 30         # harness.VENUE_VERIFIED_FRESH_DAYS: older than this and the verify prompt re-checks venue facts
# an existing record keeps its name (a seed spelling becomes an alias), site, zone and schedule
PROTECT_EXISTING = seed_venues.NEW_ONLY_FIELDS + ("name", "website", "exhibitions_url", "address_detail")

CITY_QUESTION = ("What are the {n} cities with the most significant and influential art galleries "
                 "worldwide, ranked by the quantity, influence and activity of their galleries?")
GALLERY_QUESTION = ("What are the {n} most significant and influential galleries in {city}, ranked in "
                    "order? If less than {n} significant galleries exist, provide the complete list of "
                    "significant galleries.")

CITY_SYSTEM = """You are an art-world editor compiling a gallery guide. Answer with the JSON object the
schema describes and nothing else. Rank cities by the quantity, influence and activity of their
contemporary art galleries (commercial galleries, nonprofit and artist-run spaces), not by museums or
biennials. For every city give: the common English name; the country; a URL slug (lowercase, hyphens,
e.g. "mexico-city", "hong-kong"); the coordinates of the centre of its gallery activity; a map span in
degrees that frames the gallery districts (0.15 for a compact city, up to 0.6 for a sprawling one); the
IANA timezone; six to twelve gallery districts as short labels — combine adjacent districts with a
slash ("Chelsea/Tribeca") and never use a comma in a label — ordered from the busiest; two to four
metro_tokens: short strings that appear in a postal address inside the metro area (state or prefecture
abbreviation with surrounding punctuation like ", NY ", the city name, a postcode prefix); and one
sentence saying why the city ranks where it does."""

GALLERY_SYSTEM = """You are an art-world editor compiling a gallery guide for {city}, {country}. Answer with the
JSON object the schema describes and nothing else. Rank by significance and influence in the
contemporary art world: programme quality, the artists represented, art-fair presence, critical and
institutional attention, and longevity. Include commercial galleries and the nonprofit, artist-run and
project spaces that show contemporary art to the public in {city} itself (galleries whose home is
elsewhere count only through a permanent {city} space). Exclude museums, art fairs, auction houses,
framers, art schools, decor shops, online-only dealers and advisories. For every gallery give its usual
name; its official website when you are confident of it, else null; the district it is in, using one of
these labels when it fits ({districts}) else the district name you know; a street address hint when you
know it (street and number are enough), else null; its kind ({kinds}); and a short note on why it
matters. Every entry must be a distinct venue — list a gallery with several {city} spaces once."""

CITY_SCHEMA = {
    "type": "object",
    "properties": {"cities": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "rank": {"type": "integer"},
            "name": {"type": "string"},
            "country": {"type": "string"},
            "slug": {"type": "string"},
            "center": {"type": "object", "properties": {"lat": {"type": "number"}, "lng": {"type": "number"}}},
            "map_span_deg": {"type": "number"},
            "timezone": {"type": "string"},
            "neighborhoods": {"type": "array", "items": {"type": "string"}},
            "metro_tokens": {"type": "array", "items": {"type": "string"}},
            "why": {"type": "string"},
        }}}},
}

GALLERY_SCHEMA = {
    "type": "object",
    "properties": {"galleries": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "rank": {"type": "integer"},
            "name": {"type": "string"},
            "website": {"type": ["string", "null"]},
            "neighborhood": {"type": ["string", "null"]},
            "address_hint": {"type": ["string", "null"]},
            "kind": {"type": "string", "enum": list(venues.KINDS)},
            "note": {"type": "string"},
        }}}},
}


# --- files ------------------------------------------------------------------------------

def city_dir(city: str) -> Path:
    return EXPANSION_DIR / city


def cities_file() -> Path:
    return EXPANSION_DIR / "cities.json"


def _write_json(p: Path, obj) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n")
    return p


def _read_json(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


_AUGMENT_OK: set[str] = set()   # protected cities the `augment` command may ADD venues to (never rank)


def guard(city: str, ranking: bool = False) -> None:
    if city in NEVER_WRITE and (ranking or city not in _AUGMENT_OK):
        raise SystemExit(f"{city} is protected: quick_city never writes it (docs/GALLERIES.md)")


def _ts_label(stage: str, city: str) -> str:
    return f"quick-{stage}-{city}-{int(time.time())}"


# --- both models, one question -------------------------------------------------------------

def ask_both(system: str, user: str, schema: dict, schema_name: str, label: str,
             raw_dir: Path, refresh_: bool = False) -> dict[str, dict | None]:
    """Ask Claude and OpenAI the same question; raw answers are kept on disk so a
    re-run never pays twice. Returns {"anthropic": data|None, "openai": data|None}."""
    out: dict[str, dict | None] = {}
    for provider in ("anthropic", "openai"):
        p = raw_dir / f"{label}-{provider}.json"
        cached = _read_json(p) if not refresh_ else None
        if cached and cached.get("data"):
            out[provider] = cached["data"]
            print(f"  {provider}: cached ({p.name})")
            continue
        t0 = time.time()
        if provider == "anthropic":
            data, err, usage = llm_clients.anthropic_json(system, user, schema)
        else:
            data, err, usage = llm_clients.openai_json(system, user, schema_name, schema)
        _write_json(p, {"provider": provider, "model": usage.get("model"), "ts": int(time.time()),
                        "system": system, "user": user, "error": err, "usage": usage, "data": data})
        spend_label = _ts_label(label, provider)
        llm_clients.save_spend(spend_label, usage, {"stage": label})
        n = len(next(iter(data.values()))) if isinstance(data, dict) and data else 0
        print(f"  {provider} ({usage.get('model')}): {n} entries, ${usage.get('cost_usd', 0):.3f}, "
              f"{round(time.time() - t0)}s" + (f"  ERROR {err}" if err else ""))
        out[provider] = data
    return out


# --- pure helpers (unit-tested) -------------------------------------------------------------

def _norm_key(name: str) -> str:
    return venues.venue_id(name or "")


def _name_key_loose(name: str) -> str:
    """Letters only, corporate words dropped: 'Galerie Thaddaeus Ropac' == 'Thaddaeus Ropac Gallery'."""
    w = verify_order.words(name)
    return "".join(sorted(w)) if w else re.sub(r"[^a-z0-9]", "", tools._norm_venue(name or ""))


def merge_rankings(claude: list[dict], openai: list[dict], key_field: str = "name",
                   absent_penalty: int | None = None) -> list[dict]:
    """Union of two ranked lists. An entry is the same venue when its id matches,
    its site domain matches, or its distinctive name words match. Blended rank =
    mean of the two positions, a missing position = len(that list) + 1 (or
    ``absent_penalty``). Sorted by blended, then the better single rank, then name."""
    merged: list[dict] = []
    by_id: dict[str, dict] = {}
    by_dom: dict[str, dict] = {}
    by_loose: dict[str, dict] = {}

    def find(e: dict) -> dict | None:
        k = _norm_key(e.get(key_field) or "")
        d = venues.registrable_domain(e.get("website")) if e.get("website") else None
        lk = _name_key_loose(e.get(key_field) or "")
        return by_id.get(k) or (by_dom.get(d) if d else None) or (by_loose.get(lk) if lk else None)

    def index(m: dict) -> None:
        by_id[_norm_key(m["name"])] = m
        d = venues.registrable_domain(m.get("website")) if m.get("website") else None
        if d:
            by_dom.setdefault(d, m)
        lk = _name_key_loose(m["name"])
        if lk:
            by_loose.setdefault(lk, m)

    def add(entries: list[dict], provider: str) -> None:
        for i, e in enumerate(entries or [], 1):
            name = (e.get(key_field) or "").strip()
            if not name:
                continue
            rank = int(e.get("rank") or i)
            m = find(e)
            if m is None:
                m = {"name": name, "website": e.get("website"), "neighborhood": e.get("neighborhood"),
                     "address_hint": e.get("address_hint"), "kind": e.get("kind"),
                     "notes": {}, "ranks": {}, "names": {}}
                merged.append(m)
            else:
                for f in ("website", "neighborhood", "address_hint", "kind"):
                    if not m.get(f) and e.get(f):
                        m[f] = e[f]
            if provider not in m["ranks"]:      # a provider listing the same venue twice keeps its first rank
                m["ranks"][provider] = rank
                m["names"][provider] = name
                if e.get("note") or e.get("why"):
                    m["notes"][provider] = e.get("note") or e.get("why")
            index(m)

    add(claude, "anthropic")
    add(openai, "openai")
    n_c, n_o = len(claude or []), len(openai or [])
    for m in merged:
        rc = m["ranks"].get("anthropic", (absent_penalty or (n_c + 1)))
        ro = m["ranks"].get("openai", (absent_penalty or (n_o + 1)))
        m["rank_claude"] = m["ranks"].get("anthropic")
        m["rank_openai"] = m["ranks"].get("openai")
        m["blended"] = round((rc + ro) / 2, 1)
        m["best"] = min(rc, ro)
        m["both"] = len(m["ranks"]) == 2
    merged.sort(key=lambda m: (m["blended"], m["best"], m["name"].lower()))
    for i, m in enumerate(merged, 1):
        m["rank"] = i
    return merged


def in_span(lat: float | None, lng: float | None, cfg: dict, slack: float = 0.5) -> bool:
    """Inside the city's map box (center ± span/2, widened by ``slack`` × span each way)."""
    if lat is None or lng is None:
        return False
    c, sp = cfg["center"], cfg["span"]
    dlat = sp["latitudeDelta"] * (0.5 + slack)
    dlng = sp["longitudeDelta"] * (0.5 + slack)
    return abs(lat - c["latitude"]) <= dlat and abs(lng - c["longitude"]) <= dlng


def nearest_zone(lat: float | None, lng: float | None, centroids: dict[str, dict]) -> tuple[str | None, int | None]:
    """(zone, metres) of the nearest geocoded neighborhood centroid; (None, None) without input."""
    if lat is None or lng is None or not centroids:
        return None, None
    best, dist = None, None
    for zone, g in centroids.items():
        if not g or g.get("lat") is None:
            continue
        d = crosscheck.haversine_m(lat, lng, g["lat"], g["lng"])
        if dist is None or d < dist:
            best, dist = zone, d
    return best, (int(round(dist)) if dist is not None else None)


BLOCKED_RE = re.compile(r"http_(?:403|429|503)|robots_disallow")


def quick_verdict(row: dict, in_footprint: bool) -> tuple[str, list[str]]:
    """verified | unverified with reason codes, from a verify_order check row."""
    pl, site = row.get("places") or {}, row.get("site") or {}
    dated = site.get("dated") or {}
    reasons: list[str] = []
    if row.get("verdict") == "not_a_venue":
        reasons.append("not_a_venue")
    if not pl.get("matched"):
        reasons.append("places_missing" if not pl.get("found") else "places_mismatch")
    else:
        if pl.get("status") != "OPERATIONAL":
            reasons.append("places_closed" if str(pl.get("status") or "").startswith("CLOSED") else "places_status")
        if not pl.get("address") or pl.get("lat") is None or pl.get("lng") is None:
            reasons.append("no_location")
        if not pl.get("hours"):
            reasons.append("no_hours")
    places_ok = not reasons and bool(pl.get("matched"))
    blocked = BLOCKED_RE.search(str(site.get("error") or ""))
    if not site.get("website"):
        reasons.append("no_website")
    elif site.get("website_live") is not True:
        # a site that refuses bots (429 / 403 / robots) is not a dead site: when Places
        # fully attests the venue AND names the same site, the listing carries the verdict
        same = venues.same_site(pl.get("website"), (row.get("hints") or {}).get("website") or site.get("website"))
        if blocked and places_ok and pl.get("website") and same is not False:
            row.setdefault("notes", []).append(f"site_blocked:{site.get('error')}")
        else:
            reasons.append("site_blocked" if blocked else "site_dead")
    else:
        same = venues.same_site(pl.get("website"), (row.get("hints") or {}).get("website") or site.get("website"))
        # Places attests hours / location / status; its website (when it lists one) must
        # not contradict the site we checked
        carried = places_ok and same is not False
        if not site.get("site_vouched"):
            # a JS-rendered site shows the crawler a menu and nothing else (Perrotin): when
            # Places fully attests the venue AND names this site, the vouch is not required
            if carried:
                row.setdefault("notes", []).append("site_unvouched")
            else:
                reasons.append("site_not_vouched")
        if site.get("closed_notice"):
            reasons.append("closed_notice")
        if dated.get("stale"):
            # stale = the newest dated show is years old (fatal). "No dated show found at
            # all" is usually a JS-rendered listing (Gladstone): Places' current hours and
            # OPERATIONAL status are the evidence of an open programme
            if dated.get("latest_year") is not None or not places_ok:
                reasons.append("stale")
            else:
                row.setdefault("notes", []).append("no_dated_show")
    if not in_footprint and (row.get("geo") or pl.get("lat") is not None):
        reasons.append("out_of_footprint")
    return ("verified" if not reasons else "unverified"), reasons


def registry_patch(entry: dict, row: dict, verdict: str, reasons: list[str], zone: str | None,
                   existing: dict | None, session: str, ts: int | None = None) -> dict:
    """The registry write for one checked gallery (verify_order.seed shape). Places is
    the source of address / hours / coordinates; the site checks feed verification."""
    ts = ts or int(time.time())
    pl, site, geo = row.get("places") or {}, row.get("site") or {}, row.get("geo") or {}
    matched = bool(pl.get("matched"))
    hint_mismatch = None
    if matched and entry.get("address_hint") and pl.get("address"):
        hint_mismatch = validate_venues.address_match(entry["address_hint"], pl["address"]) is False
    patch = {
        "name": entry["name"],
        "kind": entry.get("kind") or "gallery", "kind_source": "agent",
        "status": "active" if verdict == "verified" else "unknown",
        "neighborhood": zone,
        "address": pl.get("address") if matched else None,
        "website": site.get("website") or entry.get("website"),
        "exhibitions_url": site.get("exhibitions_url"),
        "phone": pl.get("phone") if matched else None,
        "hours": (pl.get("hours") or []) if matched else None,
        "latitude": geo.get("lat"), "longitude": geo.get("lng"),
        "coords_source": f"{geo.get('source')}:quick" if geo else None,
        "google": ({"status": pl.get("status"), "address": pl.get("address"), "hours": pl.get("hours"),
                    "phone": pl.get("phone"), "website": pl.get("website"), "lat": pl.get("lat"),
                    "lng": pl.get("lng"), "ts": ts} if matched else None),
        "sources": seed_venues.seed_block(existing, "quick", {
            "ts": ts, "session": session, "rank_claude": entry.get("rank_claude"),
            "rank_openai": entry.get("rank_openai"), "blended": entry.get("blended"),
            "rank": entry.get("rank")}),
        "verification": {"status": verdict, "ts": ts, "checks": {
            "places_status": pl.get("status"), "places_matched": matched,
            "website_live": site.get("website_live"), "site_vouched": site.get("site_vouched"),
            "closed_notice": site.get("closed_notice"), "exhibitions_page": site.get("exhibitions_page"),
            "stale": (site.get("dated") or {}).get("stale"),
            "latest_show_year": (site.get("dated") or {}).get("latest_year"),
            "moved_hint": hint_mismatch, "verdict": row.get("verdict"), "reasons": reasons,
            "source": "quick_city"}, "reasons": reasons},
        "notes": f"quick city seed #{entry.get('rank')} (claude #{entry.get('rank_claude') or '-'} / "
                 f"openai #{entry.get('rank_openai') or '-'})",
    }
    return {k: v for k, v in patch.items() if v is not None}


def blended_cities(claude: list[dict], openai: list[dict]) -> list[dict]:
    """Union of the two city lists keyed by slug (existing CITIES keys win), with the
    blended rank; Claude's fields are kept when both name a city."""
    def canonical(e: dict) -> str:
        """An existing CITIES key when the city is already configured (by key, display
        name or the name's slug), else the slug of the name itself — never the model's
        own slug, which differs between models ("seoul" / "seoul-kr")."""
        n = (e.get("name") or "").strip().lower()
        s = (e.get("slug") or "").strip().lower()
        nid = venues.venue_id(e.get("name") or "")
        for key, cfg in CITIES.items():
            if key in (s, nid) or cfg["display_name"].lower() == n:
                return key
        return nid or s
    rows = [dict(e, name=e.get("name"), slug=canonical(e)) for e in claude or []]
    rows_o = [dict(e, name=e.get("name"), slug=canonical(e)) for e in openai or []]
    merged = merge_rankings(rows, rows_o, key_field="slug")
    out = []
    for m in merged:
        src = next((e for e in rows if e["slug"] == m["name"]), None) or next((e for e in rows_o if e["slug"] == m["name"]), None) or {}
        out.append({"slug": m["name"], "name": src.get("name"), "country": src.get("country"),
                    "rank": m["rank"], "blended": m["blended"], "rank_claude": m["rank_claude"],
                    "rank_openai": m["rank_openai"], "existing": m["name"] in CITIES,
                    "center": src.get("center"), "map_span_deg": src.get("map_span_deg"),
                    "timezone": src.get("timezone"), "neighborhoods": src.get("neighborhoods") or [],
                    "metro_tokens": src.get("metro_tokens") or [], "why": m["notes"]})
    return out


def draft_city_config(c: dict) -> dict:
    """Text blocks to paste into cities.py, cityconfig.CITY_TZ and Models.swift."""
    hoods = [h.replace(",", "/").strip() for h in (c.get("neighborhoods") or [])]
    span = float(c.get("map_span_deg") or 0.3)
    lat, lng = (c.get("center") or {}).get("lat"), (c.get("center") or {}).get("lng")
    guidance = (f"{c.get('name')}'s gallery districts, busiest first: {', '.join(hoods)}. "
                f"Verify which shows are actually on view right now on the venues' own websites.")
    py = (f'    "{c["slug"]}": {{\n'
          f'        "display_name": "{c.get("name")}",\n'
          f'        "center": {{"latitude": {lat}, "longitude": {lng}}},\n'
          f'        "span": {{"latitudeDelta": {span}, "longitudeDelta": {span}}},\n'
          f'        "neighborhoods": {json.dumps(hoods, ensure_ascii=False)},\n'
          f'        "metro_tokens": {json.dumps(c.get("metro_tokens") or [], ensure_ascii=False)},\n'
          f'        "guidance": {json.dumps(guidance, ensure_ascii=False)},\n'
          f'    }},')
    tz = f'    "{c["slug"]}": "{c.get("timezone")}",'
    swift = (f'        City(key: "{c["slug"]}", displayName: "{c.get("name")}",\n'
             f'             neighborhoods: {json.dumps(hoods, ensure_ascii=False)},\n'
             f'             center: CLLocationCoordinate2D(latitude: {lat}, longitude: {lng}),\n'
             f'             spanDegrees: {span}, availabilityNote: nil),')
    return {"cities_py": py, "city_tz": tz, "models_swift": swift}


# --- stage: cities ---------------------------------------------------------------------------

def cmd_cities(refresh_: bool = False) -> dict:
    user = CITY_QUESTION.format(n=CITY_LIST_N)
    ans = ask_both(CITY_SYSTEM, user, CITY_SCHEMA, "gallery_cities", "cities", EXPANSION_DIR, refresh_)
    claude = (ans.get("anthropic") or {}).get("cities") or []
    openai_ = (ans.get("openai") or {}).get("cities") or []
    union = blended_cities(claude, openai_)
    # every configured city joins the pass even when neither model listed it (Seattle, Venice)
    for key, cfg in CITIES.items():
        if key not in {c["slug"] for c in union}:
            union.append({"slug": key, "name": cfg["display_name"], "country": None, "rank": len(union) + 1,
                          "blended": None, "rank_claude": None, "rank_openai": None, "existing": True,
                          "center": None, "map_span_deg": None, "timezone": None,
                          "neighborhoods": list(cfg["neighborhoods"]), "metro_tokens": cfg.get("metro_tokens") or [],
                          "why": {"note": "configured city, not on either model's list"}})
    for c in union:
        c["config_draft"] = draft_city_config(c) if not c["existing"] else None
    ranked = [c["slug"] for c in union if c["slug"] not in NEVER_WRITE]
    pilot = ranked[:10] + [k for k in CITIES if k not in NEVER_WRITE and k not in ranked[:10]]
    out = {"generated": date.today().isoformat(), "question": user, "n_claude": len(claude),
           "n_openai": len(openai_), "union": len(union), "pilot": pilot, "cities": union}
    _write_json(cities_file(), out)
    print(f"\n{len(union)} cities in the union ({len(claude)} claude, {len(openai_)} openai):")
    for c in union:
        flag = "  (existing)" if c["existing"] else ("  (protected)" if c["slug"] in NEVER_WRITE else "")
        print(f"  {c['rank']:>2}. {c['name']:<18} blended {c['blended'] or '-':>5}  claude #{c['rank_claude'] or '-':<3} "
              f"openai #{c['rank_openai'] or '-':<3}{flag}")
    print(f"\npilot (top 10 + every configured city, LA/Tokyo excluded): {', '.join(pilot)}")
    missing = [c["slug"] for c in union if c["slug"] not in CITIES]
    if missing:
        print(f"not yet in scraper/cities.py: {', '.join(missing)} — paste config_draft blocks from {cities_file()}")
    return out


# --- stage: seed -----------------------------------------------------------------------------

def cmd_seed(city: str, refresh_: bool = False) -> dict:
    guard(city)
    cfg = CITIES[city]
    country = ""
    cj = _read_json(cities_file()) or {}
    for c in cj.get("cities") or []:
        if c.get("slug") == city:
            country = c.get("country") or ""
    system = GALLERY_SYSTEM.format(city=cfg["display_name"], country=country or "",
                                   districts=", ".join(cfg["neighborhoods"]),
                                   kinds=", ".join(venues.KINDS))
    user = GALLERY_QUESTION.format(n=GALLERY_LIST_N, city=cfg["display_name"])
    ans = ask_both(system, user, GALLERY_SCHEMA, "galleries", "seed", city_dir(city), refresh_)
    claude = (ans.get("anthropic") or {}).get("galleries") or []
    openai_ = (ans.get("openai") or {}).get("galleries") or []
    merged = merge_rankings(claude, openai_)
    out = {"city": city, "generated": date.today().isoformat(), "question": user,
           "n_claude": len(claude), "n_openai": len(openai_), "n_merged": len(merged),
           "n_both": sum(1 for m in merged if m["both"]), "galleries": merged}
    _write_json(city_dir(city) / "seed-merged.json", out)
    print(f"\n{city}: {len(claude)} claude + {len(openai_)} openai -> {len(merged)} galleries "
          f"({out['n_both']} on both lists)")
    for m in merged[:15]:
        print(f"  {m['rank']:>3}. {m['name']:<36} blended {m['blended']:>5}  c#{m['rank_claude'] or '-':<3} o#{m['rank_openai'] or '-':<3}")
    return out


# --- stage: validate ------------------------------------------------------------------------

def zone_centroids(city: str, key: str | None) -> dict[str, dict]:
    """Geocoded centre per configured neighborhood (label split on '/' -> first part
    that geocodes), persisted in content/expansion/<city>/zones.json."""
    p = city_dir(city) / "zones.json"
    have = _read_json(p) or {}
    cfg = CITIES[city]
    out: dict[str, dict] = {}
    for hood in cfg["neighborhoods"]:
        if have.get(hood) and have[hood].get("lat") is not None:
            out[hood] = have[hood]
            continue
        g = None
        if key:
            for part in [hood] + hood.split("/"):
                g = seed_venues.geocode_area(key, part.strip(), cfg["display_name"], city)
                if g and in_span(g.get("lat"), g.get("lng"), cfg):
                    break
                g = None
        out[hood] = {"lat": g["lat"], "lng": g["lng"], "formatted": g.get("formatted")} if g else {}
    _write_json(p, out)
    return out


def vouch_via_root(row: dict, vctx, name: str) -> None:
    """A live page that does not read as an art venue is often a localised branch page
    (davidzwirner.com/hongkong is Chinese-only): try the site root once before
    giving up on the vouch; the row records `vouched_via_root`."""
    site = row.get("site") or {}
    url = site.get("final_url") or site.get("website")
    if not url or site.get("website_live") is not True or site.get("site_vouched"):
        return
    try:
        parts = urlsplit(url if url.startswith("http") else "https://" + url)
    except ValueError:
        return
    if not parts.path.strip("/") and not parts.query:
        return
    root = f"{parts.scheme}://{parts.netloc}/"
    try:
        r2 = validate_venues.site_checks({"id": venues.venue_id(name), "name": name, "website": root,
                                          "exhibitions_url": site.get("exhibitions_url")}, vctx)
    except Exception:  # noqa: BLE001 - a bonus check, never a crash
        return
    if r2.get("website_live") and r2.get("site_vouched"):
        site["site_vouched"] = True
        site["vouched_via_root"] = root
        if r2.get("closed_notice"):
            site["closed_notice"] = True
        if not site.get("exhibitions_url") and r2.get("exhibitions_url"):
            site["exhibitions_url"], site["exhibitions_page"] = r2["exhibitions_url"], True


def validate_city(city: str, entries: list[dict], *, key: str | None, max_places_requests: int,
                  network: bool = True, render: bool = True, refetch: bool = False,
                  session: str | None = None, limit: int | None = None, workers: int = 6) -> dict:
    cfg = CITIES[city]
    session = session or _ts_label("validate", city)
    reg = venues.load_registry(city)
    centroids = zone_centroids(city, key) if network else {}
    labeled = seed_venues.labeled_points(reg, {h: {h: g} for h, g in centroids.items() if g})
    fetcher = refresh.Fetcher()
    vctx = validate_venues.Ctx(city, fetcher, key, 0, network, True, render, session)
    # the Places budget is shared across worker threads (verify_order.places_lookup takes the lock)
    budget = {"requests": 0, "cost_usd": 0.0, "max_requests": max_places_requests, "refetch": refetch,
              "lock": threading.Lock()}

    def check(e: dict) -> dict:
        entry = {"rank": e.get("rank"), "name": e["name"], "neighborhood": e.get("neighborhood"),
                 "address": e.get("address_hint"), "website": e.get("website"), "note": None}
        row = verify_order.check_entry(entry, city=city, reg=reg, key=key, network=network, fetcher=fetcher,
                                       vctx=vctx, labeled=labeled, budget=budget, render=render, cov=None,
                                       retry_without_hint=True)
        vouch_via_root(row, vctx, e["name"])
        geo = row.get("geo") or {}
        footprint = in_span(geo.get("lat"), geo.get("lng"), cfg) if geo else False
        zone = row.get("zone")
        if zone is None and geo:
            zone, _d = nearest_zone(geo.get("lat"), geo.get("lng"), centroids)
        if zone is None and e.get("neighborhood"):
            zone = tools.normalize_zone(e["neighborhood"], cfg["neighborhoods"], city)
        verdict, reasons = quick_verdict(row, footprint)
        if verdict == "verified" and zone is None:
            verdict, reasons = "unverified", ["no_zone"]
        row.update({"quick_verdict": verdict, "reasons": reasons, "zone": zone, "in_footprint": footprint,
                    "entry": e})
        print(f"  {e.get('rank', 0):>3}. {e['name']:<36} {verdict:<10} {','.join(reasons)[:60]:<60} {zone or '-'}", flush=True)
        return row

    todo = entries[:limit] if limit else entries
    if workers > 1 and len(todo) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(check, todo))
    else:
        rows = [check(e) for e in todo]
    counts = {"verified": sum(1 for r in rows if r["quick_verdict"] == "verified"),
              "unverified": sum(1 for r in rows if r["quick_verdict"] != "verified")}
    by_reason: dict[str, int] = {}
    for r in rows:
        for x in r["reasons"]:
            by_reason[x] = by_reason.get(x, 0) + 1
    rep = {"session": session, "city": city, "ts": int(time.time()), "n": len(rows), "counts": counts,
           "reasons": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])),
           "spend": {"places_requests": budget["requests"], "cost_usd": round(budget["cost_usd"], 4),
                     "fetches": vctx.stats["fetches"], "renders": vctx.stats["renders"]},
           "rows": rows}
    _write_json(city_dir(city) / f"validate-{rep['ts']}.json", rep)
    (harness_spend_dir() / f"{session}.json").write_text(json.dumps(
        {"session": session, "city": city, "stage": "validate", "ts": rep["ts"], **rep["spend"]}, indent=1))
    return rep


def harness_spend_dir() -> Path:
    d = tools.CONTENT_DIR / "spend"
    d.mkdir(parents=True, exist_ok=True)
    return d


def apply_validation(city: str, rep: dict) -> dict:
    """Write every checked gallery to the registry (verified -> active, else unknown)."""
    guard(city)
    reg = venues.load_registry(city)
    rows_out = []
    for r in rep["rows"]:
        e = r["entry"]
        existing = venues.find_venue(reg, e["name"], e.get("website"), add_alias=False, city=city)
        patch = registry_patch(e, r, r["quick_verdict"], r["reasons"], r.get("zone"), existing, rep["session"])
        # match existing records on the MODEL's website, never the Places one: a hallucinated
        # gallery whose lookup lands on a neighbour would otherwise merge into that neighbour
        rows_out.append({"name": e["name"], "patch": patch, "source": "seed-quick", "website": e.get("website")})
    out = venues.bulk_upsert(city, rows_out, protect_existing=PROTECT_EXISTING)
    ids = out["new"] + out["updated"]
    for r, vid in zip(rep["rows"], ids):
        r["venue_id"] = vid
    for r in rep["rows"]:
        r["venue_id"] = venues.resolve_id(city, r["entry"]["name"], r["entry"].get("website"))
    _write_json(city_dir(city) / f"validate-{rep['ts']}.json", rep)
    print(f"registry: {len(out['new'])} created, {len(out['updated'])} updated")
    try:
        d = venues.cmd_dedupe(city, apply=False)
        if d.get("review"):
            print(f"  {d['review']} same-domain pair(s) to review by hand (venues.py dedupe --city {city})")
    except Exception as exc:  # noqa: BLE001 - a report, not a gate
        print(f"  dedupe report skipped: {exc}")
    return out


def cmd_validate(city: str, apply: bool, max_places_requests: int, network: bool, render: bool,
                 refetch: bool, limit: int | None, workers: int = 6) -> dict:
    guard(city)
    load_env()
    key = os.environ.get("GOOGLE_MAPS_API_KEY") if network else None
    if network and not key:
        raise SystemExit("GOOGLE_MAPS_API_KEY missing (.env)")
    seed = _read_json(city_dir(city) / "seed-merged.json")
    if not seed:
        raise SystemExit(f"no seed for {city}: run quick_city.py seed --city {city} first")
    entries = seed["galleries"]
    print(f"{city}: validating {len(entries[:limit] if limit else entries)} galleries "
          f"(places budget {max_places_requests} requests)")
    rep = validate_city(city, entries, key=key, max_places_requests=max_places_requests, network=network,
                        render=render, refetch=refetch, limit=limit, workers=workers)
    print(f"\n{city}: {rep['counts']}  reasons {rep['reasons']}  places {rep['spend']['places_requests']} "
          f"requests (${rep['spend']['cost_usd']:.2f})")
    if apply:
        apply_validation(city, rep)
    else:
        print("dry run — pass --apply to write the registry")
    return rep


def latest_validation(city: str) -> dict | None:
    files = sorted(city_dir(city).glob("validate-*.json"))
    return _read_json(files[-1]) if files else None


# --- stage: rank ----------------------------------------------------------------------------

def write_order_file(city: str, verified: list[dict], tiers: dict, source: str, force: bool = False) -> Path:
    guard(city, ranking=True)
    p = rank_venues.order_file(city)
    if p.exists() and not force:
        raise SystemExit(f"{p} exists — a hand ranking? pass --force to replace it")
    entries = [{"rank": i, "name": r["name"], "id": r["id"], "neighborhood": r.get("neighborhood"),
                "note": r.get("note")} for i, r in enumerate(verified, 1)]
    _write_json(p, {"city": city, "source": source, "generated": date.today().isoformat(),
                    "tiers": tiers, "entries": entries})
    return p


def cmd_rank(city: str, apply: bool, tiers: dict, force: bool) -> dict:
    guard(city, ranking=True)
    rep = latest_validation(city)
    if not rep:
        raise SystemExit(f"no validation report for {city}: run quick_city.py validate --city {city} --apply")
    reg = venues.load_registry(city)
    by_id = venues.index_by_id(reg)
    by_name = {r["entry"]["name"]: r for r in rep["rows"]}
    seed = _read_json(city_dir(city) / "seed-merged.json") or {"galleries": []}
    verified = []
    for e in seed["galleries"]:
        # a quick city: every seed name has a validation row; an augmented city (Tokyo): the
        # rows cover only the names the registry lacked, the rest resolve by name / site
        r = by_name.get(e["name"])
        if r is not None:
            vid = r.get("venue_id") if r.get("quick_verdict") == "verified" else None
            # the row's id must still answer to this name (a repaired mis-merge no longer does)
            hit = known_in_registry(reg, e["name"], e.get("website")) if vid else None
            if not hit or hit["id"] != vid:
                vid = None
        else:
            hit = known_in_registry(reg, e["name"], e.get("website"))
            vid = hit["id"] if hit else None
        v = by_id.get(vid or "")
        if v and (v.get("verification") or {}).get("status") != "verified":
            # the name landed on an unverified branch / old-address record: a verified
            # sibling on the same site domain is the venue the list means (NANZUKA
            # UNDERGROUND -> the verified NANZUKA record)
            dom = venues.registrable_domain(v.get("website"))
            sib = [w for w in reg.get("venues", []) if dom and venues.registrable_domain(w.get("website")) == dom
                   and (w.get("verification") or {}).get("status") == "verified"
                   and w.get("status") not in ("closed", "duplicate", "out_of_scope")]
            if sib:
                v = sorted(sib, key=lambda w: (w.get("rank") is None, w.get("rank") or 0))[0]
                vid = v["id"]
        if not v or (v.get("verification") or {}).get("status") != "verified":
            continue
        verified.append({"name": v["name"], "id": vid, "neighborhood": v.get("neighborhood"),
                         "note": f"claude #{e.get('rank_claude') or '-'} / openai #{e.get('rank_openai') or '-'}",
                         "blended": e.get("blended"), "best": e.get("best"), "rank": e.get("rank")})
    verified.sort(key=lambda r: (r["blended"] or 999, r["best"] or 999, r["rank"] or 999))
    seen: set[str] = set()
    verified = [r for r in verified if not (r["id"] in seen or seen.add(r["id"]))]
    source = f"LLM consensus ({llm_clients.ANTHROPIC_MODEL} + {llm_clients.OPENAI_MODEL}), {date.today().isoformat()}"
    print(f"{city}: {len(verified)} verified galleries in blended order")
    for r in verified[:10]:
        print(f"  {r['name']:<36} {r['id']:<34} {r['note']}")
    if not apply:
        print("dry run — pass --apply to write venue_order.json and rank")
        return {"verified": len(verified)}
    p = write_order_file(city, verified, tiers, source, force)
    ids_file = city_dir(city) / "verified-ids.txt"
    ids_file.write_text("\n".join(r["id"] for r in verified) + "\n")
    print(f"wrote {p} and {ids_file}")
    rc = subprocess.run([sys.executable, str(HERE / "rank_venues.py"), "apply", "--city", city]).returncode
    if rc != 0:
        raise SystemExit(f"rank_venues.py apply failed (rc={rc})")
    return {"verified": len(verified), "order_file": str(p)}


# --- stage: shows ---------------------------------------------------------------------------

RESCRAPE_DAYS = 7


def check_show_preconditions(city: str, rescrape: bool = False, ids_file: Path | None = None) -> list[str]:
    """Verified, fresh, zoned ids from verified-ids.txt (or ``ids_file``); venues the deep
    scrape visited in the last RESCRAPE_DAYS are skipped unless ``rescrape`` (a
    re-validation that adds a few venues must not re-pay for the whole city)."""
    ids_file = ids_file or city_dir(city) / "verified-ids.txt"
    if not ids_file.exists():
        raise SystemExit(f"no {ids_file}: run quick_city.py rank --city {city} --apply first")
    ids = [x.strip() for x in ids_file.read_text().splitlines() if x.strip()]
    by_id = venues.index_by_id(venues.load_registry(city))
    hoods = set(CITIES[city]["neighborhoods"])
    now = int(time.time())
    ok = []
    for vid in ids:
        v = by_id.get(vid)
        ver = (v or {}).get("verification") or {}
        why = None
        if not v:
            why = "not in registry"
        elif ver.get("status") != "verified":
            why = f"verification {ver.get('status')}"
        elif not ver.get("ts") or now - int(ver["ts"]) > VERIFIED_FRESH_DAYS * 86400:
            why = "verification older than 30 days (re-run validate)"
        elif v.get("neighborhood") not in hoods:
            why = f"neighborhood {v.get('neighborhood')!r} not a configured zone"
        elif not rescrape and v.get("last_scraped") and now - int(v["last_scraped"]) < RESCRAPE_DAYS * 86400:
            why = "scraped in the last week (pass --rescrape)"
        if why:
            print(f"  skip {vid}: {why}")
        else:
            ok.append(vid)
    return ok


def cmd_shows(city: str, total_budget: float, workers: int, session_budget: float, session_todo: int,
              no_verify: bool, dry_run: bool, rescrape: bool = False, ids_file: Path | None = None,
              label: str = "shows") -> int:
    guard(city)
    load_env()
    for k in ("ANTHROPIC_API_KEY", "GOOGLE_MAPS_API_KEY"):
        if not os.environ.get(k):
            raise SystemExit(f"{k} missing (.env)")
    ids = check_show_preconditions(city, rescrape, ids_file)
    if not ids:
        print("nothing to scrape (every verified venue was visited this week)")
        return 0
    run_file = city_dir(city) / f"{label}-ids.txt"
    run_file.write_text("\n".join(ids) + "\n")
    cmd = [sys.executable, str(HERE / "run_deep.py"), "--city", city, "--venue-ids", f"@{run_file}", "--force-due",
           "--total-budget", str(total_budget), "--session-budget", str(session_budget),
           "--session-todo", str(session_todo), "--workers", str(workers), "--no-report"]
    if no_verify:
        cmd.append("--no-verify")
    print(f"{city}: {len(ids)} verified venues -> {' '.join(cmd[1:])}")
    if dry_run:
        return 0
    rc = subprocess.run(cmd).returncode
    if rc == 0 and not no_verify:
        subprocess.run([sys.executable, str(HERE / "sync_shows.py"), "--city", city, "--apply"])
    return rc


# --- augment: add the galleries a city's registry is missing ---------------------------------

def known_in_registry(reg: dict, name: str, website: str | None) -> dict | None:
    """The registry venue an LLM entry already refers to: id / alias / site domain
    (venues.find_venue) or the same normalised name keys (rank_venues' order resolution)."""
    hit = venues.find_venue(reg, name, website, add_alias=False)
    if hit:
        return hit
    idx = rank_venues._name_index(reg)
    cands: set[str] = set()
    for k in curation_store.venue_keys(name):
        cands |= idx.get(k, set())
    if len(cands) == 1:
        return venues.index_by_id(reg)[next(iter(cands))]
    return None


def cmd_augment(city: str, apply: bool, total_budget: float, workers: int, max_places_requests: int,
                refresh_: bool = False, no_shows: bool = False) -> dict:
    """Seed the city, keep only the galleries the registry does not know, validate those,
    add them (verified -> active, else unknown), scrape the verified ones' shows. The
    city's ranking / order file is never touched — new venues rank automatically when
    rank_venues.py apply next runs. Works on protected cities (LA, Tokyo) because it only ADDS."""
    _AUGMENT_OK.add(city)
    load_env()
    seed = cmd_seed(city, refresh_)
    reg = venues.load_registry(city)
    new, known = [], []
    for g in seed["galleries"]:
        hit = known_in_registry(reg, g["name"], g.get("website"))
        (known if hit else new).append(dict(g, registry_id=hit["id"] if hit else None))
    print(f"\n{city}: {len(seed['galleries'])} names from the two models, {len(known)} already in the registry, "
          f"{len(new)} new")
    for g in new:
        print(f"  + {g['rank']:>3}. {g['name']:<36} c#{g['rank_claude'] or '-':<3} o#{g['rank_openai'] or '-':<3} {g.get('website') or ''}")
    report = {"city": city, "generated": date.today().isoformat(), "seeded": len(seed["galleries"]),
              "already_known": len(known), "new": len(new), "new_names": [g["name"] for g in new],
              "known_matches": [{"name": g["name"], "registry_id": g["registry_id"]} for g in known]}
    _write_json(city_dir(city) / "augment-report.json", report)
    if not new:
        print("nothing new to validate")
        return report
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        raise SystemExit("GOOGLE_MAPS_API_KEY missing (.env)")
    rep = validate_city(city, new, key=key, max_places_requests=max_places_requests, workers=workers,
                        session=_ts_label("augment", city))
    report.update({"validated": rep["n"], "verified": rep["counts"]["verified"],
                   "unverified": rep["counts"]["unverified"], "unverified_reasons": rep["reasons"],
                   "verified_names": [r["name"] for r in rep["rows"] if r["quick_verdict"] == "verified"],
                   "unverified_detail": [{"name": r["name"], "reasons": r["reasons"]} for r in rep["rows"]
                                         if r["quick_verdict"] != "verified"],
                   "places_cost_usd": rep["spend"]["cost_usd"]})
    _write_json(city_dir(city) / "augment-report.json", report)
    if not apply:
        print("dry run — pass --apply to add the new galleries to the registry and scrape their shows")
        return report
    out = apply_validation(city, rep)
    report["registry_created"] = len(out["new"])
    report["registry_updated"] = len(out["updated"])
    verified_ids = [r["venue_id"] for r in rep["rows"] if r["quick_verdict"] == "verified" and r.get("venue_id")]
    ids_file = city_dir(city) / "augment-verified-ids.txt"
    ids_file.write_text("\n".join(dict.fromkeys(verified_ids)) + "\n")
    _write_json(city_dir(city) / "augment-report.json", report)
    if no_shows or not verified_ids:
        return report
    before = {s["slug"] for s in tools.all_city_shows(city)}
    rc = cmd_shows(city, total_budget, 3, 4.5, 8, False, False, rescrape=True, ids_file=ids_file, label="augment-shows")
    pub = {s["slug"]: s for s in tools._load_shows_file(tools._city_file(city))["shows"]}
    pend = {s["slug"]: s for s in tools._load_shows_file(tools._pending_file(city))["shows"]}
    vids = set(verified_ids)
    report.update({
        "shows_rc": rc,
        "shows_published_new": sorted(k for k, s in pub.items() if k not in before and s.get("venue_id") in vids),
        "shows_pending_new": sorted(k for k, s in pend.items() if k not in before and s.get("venue_id") in vids),
        "venues_with_active_show": sorted({s["venue_id"] for s in pub.values() if s.get("venue_id") in vids}),
    })
    _write_json(city_dir(city) / "augment-report.json", report)
    return report


# --- status -----------------------------------------------------------------------------------

def city_status(city: str) -> dict:
    seed = _read_json(city_dir(city) / "seed-merged.json") or {}
    rep = latest_validation(city) or {}
    reg = venues.load_registry(city)
    by_id = venues.index_by_id(reg)
    verified_reg = sum(1 for v in reg.get("venues", []) if (v.get("verification") or {}).get("status") == "verified")
    order = rank_venues.load_order_file(city) if rank_venues.order_file(city).exists() else None
    published = tools._load_shows_file(tools._city_file(city))["shows"]
    pending = tools._load_shows_file(tools._pending_file(city))["shows"]
    bad = [s["slug"] for s in published
           if (by_id.get(s.get("venue_id") or "", {}).get("verification") or {}).get("status") != "verified"]
    spend = 0.0
    for f in harness_spend_dir().glob("*.json"):
        if city in f.name and (f.name.startswith("quick-") or f.name.startswith("deep-") or f.name.startswith("verify-")):
            try:
                spend += float((json.loads(f.read_text()) or {}).get("cost_usd") or 0)
            except (json.JSONDecodeError, OSError, ValueError):
                pass
    return {"city": city, "seeded": seed.get("n_merged", 0), "validated": rep.get("n", 0),
            "verified": (rep.get("counts") or {}).get("verified", 0), "registry_verified": verified_reg,
            "order_entries": len(order["entries"]) if order else 0, "published": len(published),
            "pending": len(pending), "published_at_unverified": bad, "spend_usd": round(spend, 2)}


def cmd_status(city: str | None) -> None:
    cities = [city] if city else [c for c in CITIES if city_dir(c).exists()]
    print(f"{'city':<16}{'seed':>6}{'valid':>7}{'verif':>7}{'reg✓':>6}{'order':>7}{'pub':>5}{'pend':>6}{'$':>8}  audit")
    for c in cities:
        s = city_status(c)
        audit = "ok" if not s["published_at_unverified"] else f"{len(s['published_at_unverified'])} published at non-verified venues: {', '.join(s['published_at_unverified'][:3])}"
        print(f"{c:<16}{s['seeded']:>6}{s['validated']:>7}{s['verified']:>7}{s['registry_verified']:>6}"
              f"{s['order_entries']:>7}{s['published']:>5}{s['pending']:>6}{s['spend_usd']:>8.2f}  {audit}")


# --- CLI ---------------------------------------------------------------------------------------

def _tiers(s: str | None) -> dict:
    if not s:
        return dict(DEFAULT_TIERS)
    a, b = (int(x) for x in s.split(","))
    return {"1": a, "2": b}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("cities"); s.add_argument("--refresh", action="store_true")
    s = sub.add_parser("seed"); s.add_argument("--city", required=True, choices=sorted(CITIES)); s.add_argument("--refresh", action="store_true")
    s = sub.add_parser("validate"); s.add_argument("--city", required=True, choices=sorted(CITIES))
    s.add_argument("--apply", action="store_true"); s.add_argument("--max-places-requests", type=int, default=PLACES_BUDGET)
    s.add_argument("--no-network", action="store_true"); s.add_argument("--no-render", action="store_true")
    s.add_argument("--refetch", action="store_true"); s.add_argument("--limit", type=int, default=None)
    s.add_argument("--workers", type=int, default=6, help="parallel site checks (Places budget is shared)")
    s = sub.add_parser("rank"); s.add_argument("--city", required=True, choices=sorted(CITIES))
    s.add_argument("--apply", action="store_true"); s.add_argument("--tiers", default=None, help="e.g. 20,50")
    s.add_argument("--force", action="store_true", help="replace an existing venue_order.json")
    s = sub.add_parser("shows"); s.add_argument("--city", required=True, choices=sorted(CITIES))
    s.add_argument("--total-budget", type=float, default=70.0); s.add_argument("--workers", type=int, default=3)
    s.add_argument("--session-budget", type=float, default=4.5); s.add_argument("--session-todo", type=int, default=8)
    s.add_argument("--no-verify", action="store_true"); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--rescrape", action="store_true", help="include venues scraped in the last week")
    s = sub.add_parser("augment", help="add the galleries a registry is missing (allowed on LA/Tokyo: adds only, never ranks)")
    s.add_argument("--city", required=True, choices=sorted(CITIES)); s.add_argument("--apply", action="store_true")
    s.add_argument("--total-budget", type=float, default=40.0); s.add_argument("--workers", type=int, default=6)
    s.add_argument("--max-places-requests", type=int, default=PLACES_BUDGET); s.add_argument("--refresh", action="store_true")
    s.add_argument("--no-shows", action="store_true")
    s = sub.add_parser("status"); s.add_argument("--city", default=None, choices=sorted(CITIES))
    a = ap.parse_args(argv)
    if a.cmd == "cities":
        cmd_cities(a.refresh)
    elif a.cmd == "seed":
        cmd_seed(a.city, a.refresh)
    elif a.cmd == "validate":
        cmd_validate(a.city, a.apply, a.max_places_requests, not a.no_network, not a.no_render, a.refetch, a.limit,
                     a.workers)
    elif a.cmd == "rank":
        cmd_rank(a.city, a.apply, _tiers(a.tiers), a.force)
    elif a.cmd == "shows":
        return cmd_shows(a.city, a.total_budget, a.workers, a.session_budget, a.session_todo, a.no_verify, a.dry_run,
                         a.rescrape)
    elif a.cmd == "augment":
        cmd_augment(a.city, a.apply, a.total_budget, a.workers, a.max_places_requests, a.refresh, a.no_shows)
    elif a.cmd == "status":
        cmd_status(a.city)
    return 0


if __name__ == "__main__":
    sys.exit(main())
