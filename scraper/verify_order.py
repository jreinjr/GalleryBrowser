"""Verification pass for market-order entries the registry does not know.

    python verify_order.py --city los-angeles [--apply] [--max-places-requests 60]
                           [--no-network] [--refetch]

For every entry of ``content/curation/<city>/venue_order.json`` (docs/GALLERIES.md)
that neither carries a registry id nor resolves by name, find out whether it is a
real, open venue the pipeline missed, and if so why. Per entry:

    hints       an entry may carry ``address`` / ``website`` (added by hand after a first
                pass) — the address sharpens the Places query, the website gets checked
    registry    near-miss names (word overlap) — a rename or a mis-typed alias
    places      one Places text search + details (crosscheck.google_lookup, cached
                under scraper/.cache/places/<city>/ like every other lookup; legacy
                key = 2 requests, $0.052) -> business_status, address, website,
                coordinates, and whether the result's name actually matches
    site        validate_venues.site_checks on the website Places (or the entry)
                gives: live, vouched as an art venue, closed notice, exhibitions page
    dated       newest dated show on the exhibitions/current page: a live, venue-looking
                site whose newest show is years old (or that only describes online
                exhibitions) is NOT proof of an open programme -> unverified
    zone        seed_venues.assign_zone -> inside the city's zone footprint or not
    coverage    was the name on Gallery Platform LA / Carla (the two directory
                seeds), in the cached Places nearby sweep (and if so why the sweep
                dropped it: seed_venues.place_skip_reason), or in an enumeration log

verdict  closed          Places CLOSED_PERMANENTLY or a closed notice on the site
         not_found       no Places row and no live website
         out_of_footprint the venue is beyond every zone (assign_zone None)
         not_a_venue     auction house / bookstore / advisory / private dealing / co-op
                         shop etc. — the pipeline never targets these
         miss            a real, open venue inside the footprint: the pipeline should
                         have had it; ``why`` explains which nets it fell through
         unverified      found but not confirmable (no site, site dead, name mismatch)

``--apply`` seeds every ``miss`` (and ``unverified`` venues with a confirmed Places
row) into the registry (status active when verified, else unknown; sources.seed.order
carries the list rank) and writes the new ids back into venue_order.json, so the next
``rank_venues.py apply`` ranks them. Closed / not-a-venue entries are left as they
are (the order file keeps them with id null and the report says why).

Writes content/spend/reports/verify-order-<city>-<ts>.json and a spend record.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crosscheck  # noqa: E402
import curation_store as store  # noqa: E402
import rank_venues  # noqa: E402
import refresh  # noqa: E402
import seed_venues  # noqa: E402
import tools  # noqa: E402
import validate_venues  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOOKUP_CACHE_DAYS = 30
NOT_A_VENUE_RE = re.compile(
    r"\b(auction|auctions|bookstore|books on|book shop|advisory|private dealing|dealer|"
    r"appraisal|framing|framer|co-?op|society of artists|sculpture garden)\b", re.I)
GENERIC = crosscheck.GENERIC_NAME_WORDS | {"la", "los", "angeles", "l", "a", "ltd", "inc", "llc"}


def _load_env() -> None:
    """GOOGLE_MAPS_API_KEY from the repo's .env when the shell did not export it."""
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        return
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def words(text: str | None) -> set[str]:
    return crosscheck._name_words(text) - GENERIC


MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
SHOW_DATE_RE = re.compile(rf"\b(?:{MONTHS})[a-z]*\.?\s+(?:\d{{1,2}},?\s+)?(20\d\d)\b|\b\d{{1,2}}\s+(?:{MONTHS})[a-z]*\.?\s+(20\d\d)\b|\b(20\d\d)-\d\d-\d\d\b", re.I)
STALE_DAYS = 540          # no dated show in ~18 months -> the site is not evidence of an open programme
ONLINE_RE = re.compile(r"\bonline(?:-| )(?:only|exhibition|viewing room)\b", re.I)


def latest_dated_year(text: str, today_year: int | None = None) -> int | None:
    """Newest plausible show year on a page (next year at most — a '2030' is a typo
    or an artist's dates, not a show)."""
    top = (today_year or time.gmtime().tm_year) + 1
    years = [int(next(g for g in m.groups() if g)) for m in SHOW_DATE_RE.finditer(text or "")]
    years = [y for y in years if 1990 <= y <= top]
    return max(years) if years else None


SHOW_PAGES = ("/exhibitions", "/current", "/past", "/exhibitions/past", "/current-exhibitions")


def _page_text(fetcher, url: str, render: bool) -> str:
    r = fetcher.get(url)
    if r["status"] != 200 or not r["html"]:
        return ""
    text, _title = refresh.extract_main_text(r["html"])
    if render and (refresh.looks_js_rendered(r["html"], text) or len(text) < 300):
        try:
            text = json.loads(tools.render_fetch(r["final_url"], 20000)).get("text") or text
        except (json.JSONDecodeError, TypeError, OSError):
            pass
    return text or ""


def stale_check(fetcher, website: str | None, exhibitions_url: str | None, render: bool, today_year: int) -> dict:
    """{latest_year, stale, online, pages} across the exhibitions page and the usual
    show-list paths: the newest dated show anywhere on them, and whether the newest
    ones are labelled online exhibitions."""
    out = {"latest_year": None, "stale": None, "online": False, "pages": []}
    if not website:
        return out
    base = website if website.startswith("http") else "https://" + website
    base = base.rstrip("/")
    # the homepage counts too: a site with no dated show anywhere is not evidence of a programme
    urls = [u for u in dict.fromkeys([exhibitions_url] + [base + p for p in SHOW_PAGES] + [base]) if u]
    chars = 0
    for u in urls:
        text = _page_text(fetcher, u, render)
        if len(text) < 200:
            continue
        chars += len(text)
        y = latest_dated_year(text, today_year)
        out["pages"].append({"url": u, "latest_year": y, "online": bool(ONLINE_RE.search(text))})
        if y and (out["latest_year"] is None or y > out["latest_year"]):
            out["latest_year"], out["online"] = y, bool(ONLINE_RE.search(text))
    # a whole year's grace on top of STALE_DAYS/365 (dates are read at year precision)
    out["stale"] = (out["latest_year"] is not None and today_year - out["latest_year"] > (STALE_DAYS // 365) + 1) \
        or (out["latest_year"] is None and chars > 300)
    return out


def name_matches(name: str, found: str | None, ignore: set[str] | frozenset[str] = frozenset()) -> bool:
    """The Places result is the venue we asked for: a shared distinctive word, or the
    same letters once spaces go ('ArtPic' vs 'Art Pic'). ``ignore`` = words that are
    never distinctive here (the city's own name: "Gagosian Tokyo" must not match
    "Taka Ishii Gallery Tokyo")."""
    if not found:
        return False
    if (words(name) - ignore) & (words(found) - ignore):
        return True
    a = re.sub(r"[^a-z0-9]", "", tools._norm_venue(name))
    b = re.sub(r"[^a-z0-9]", "", tools._norm_venue(found))
    return bool(a) and (a in b or b in a)


def city_words(city: str) -> frozenset[str]:
    """Words of the city's display name and metro tokens — never distinctive in a venue name."""
    cfg = CITIES.get(city) or {}
    toks = [cfg.get("display_name") or ""] + list(cfg.get("metro_tokens") or [])
    return frozenset(w for t in toks for w in crosscheck._name_words(t))


def in_metro(pl: dict, city: str) -> bool | None:
    """Does the Places address sit in this city's metro (state / prefecture token from
    cities.py `metro_tokens`, default: the display name or ', CA')? None = unknown."""
    addr = pl.get("address") or ""
    if not addr:
        return None
    toks = CITIES[city].get("metro_tokens") or [CITIES[city]["display_name"], ", CA "]
    return any(t.lower() in (addr + " ").lower() for t in toks)


# --- coverage: what the pipeline's nets saw ---------------------------------------

class Coverage:
    """Directory seeds, the cached Places sweep and the enumeration logs, indexed by
    name so a missing entry can be asked 'which net should have caught you?'."""

    def __init__(self, city: str, sctx: seed_venues.Ctx | None):
        self.gpla: list[dict] = []
        self.carla: list[dict] = []
        if sctx is not None:
            html = seed_venues.fetch_html(sctx, seed_venues.GPLA_LIST_URL)
            self.gpla = seed_venues.parse_gpla_list(html) if html else []
            html = seed_venues.fetch_html(sctx, seed_venues.CARLA_URL)
            self.carla = seed_venues.parse_carla(html, deny=False) if html else []
        self.sweep: list[dict] = []
        for f in glob.glob(str(seed_venues._cache_dir(city) / "*.json")):
            try:
                d = json.loads(Path(f).read_text())
            except (OSError, json.JSONDecodeError):
                continue
            q = d.get("query") or {}
            if q.get("op") not in (None, "nearby") and "lat" not in q:
                continue
            data = d.get("data")
            pages = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
            for pg in pages:
                for r in (pg or {}).get("results") or []:
                    if isinstance(r, dict) and r.get("name"):
                        self.sweep.append({"name": r["name"], "types": r.get("types") or [],
                                           "status": r.get("business_status"), "place_id": r.get("place_id"),
                                           "query": q})
        self.circles: list[dict] = []
        for f in glob.glob(str(seed_venues._cache_dir(city) / "*.json")):
            try:
                q = (json.loads(Path(f).read_text()) or {}).get("query") or {}
            except (OSError, json.JSONDecodeError):
                continue
            if q.get("op") == "nearby" and q.get("lat") is not None:
                self.circles.append(q)
        self.logs: list[tuple[str, str]] = []
        for f in glob.glob(str(tools.CONTENT_DIR / "spend" / "logs" / f"*{city}*.log")):
            try:
                self.logs.append((Path(f).name, Path(f).read_text(errors="ignore").lower()))
            except OSError:
                pass

    @staticmethod
    def _hit(name: str, rows: list[dict]) -> dict | None:
        keys = store.venue_keys(name)
        w = words(name)
        for r in rows:
            if store.venue_keys(r["name"]) & keys:
                return r
        if len(w) >= 2:
            for r in rows:
                if w <= words(r["name"]):
                    return r
        return None

    def nearest_circle(self, lat: float | None, lng: float | None) -> dict | None:
        if lat is None or lng is None or not self.circles:
            return None
        best = min(self.circles, key=lambda q: crosscheck.haversine_m(lat, lng, q["lat"], q["lng"]))
        d = crosscheck.haversine_m(lat, lng, best["lat"], best["lng"])
        return {"dist_m": int(d), "radius_m": best.get("radius_m"), "type": best.get("type"),
                "inside": d <= float(best.get("radius_m") or 0)}

    def check(self, name: str, lat: float | None = None, lng: float | None = None) -> dict:
        g = self._hit(name, self.gpla)
        c = self._hit(name, self.carla)
        s = self._hit(name, self.sweep)
        distinct = sorted(words(name))
        needle = " ".join(distinct[:2]) if distinct else name.lower()
        in_logs = [fn for fn, txt in self.logs if name.lower() in txt or (len(needle) > 6 and needle in txt)]
        return {"gpla": g["name"] if g else None, "carla": c["name"] if c else None,
                "sweep": ({"name": s["name"], "status": s["status"], "skip": seed_venues.place_skip_reason(s),
                           "query": s["query"].get("area") or s["query"].get("ptype")} if s else None),
                "enumeration_logs": in_logs[:4], "circle": self.nearest_circle(lat, lng)}


# --- per-entry checks ----------------------------------------------------------------

def registry_near_misses(reg: dict, name: str, limit: int = 3) -> list[dict]:
    w = words(name)
    if not w:
        return []
    out = []
    for v in reg.get("venues", []):
        vw = words(v.get("name")) | set().union(*(words(a) for a in (v.get("aliases") or [])), set())
        j = len(w & vw) / max(1, len(w | vw))
        if j >= 0.34 or (len(w) >= 2 and w <= vw):
            out.append((j, {"id": v["id"], "name": v.get("name"), "status": v.get("status")}))
    return [o for _, o in sorted(out, key=lambda t: -t[0])[:limit]]


def places_lookup(key: str | None, city: str, name: str, hint: str | None, budget: dict) -> dict:
    """crosscheck.google_lookup, cached like the seed lookups; never raises."""
    bias = city_bias(city)
    query = {"op": "order_lookup", "name": name, "hint": hint or ""}
    if bias:
        query["bias"] = "city"
    k = seed_venues._cache_key(query)
    c = seed_venues._cache_get(city, k, LOOKUP_CACHE_DAYS) if not budget.get("refetch") else None
    if c is not None:
        return dict(c["data"], cached=True)
    with (budget.get("lock") or contextlib.nullcontext()):   # quick_city checks venues in threads
        if not key or budget["requests"] + validate_venues.LOOKUP_REQUESTS > budget["max_requests"]:
            return {"found": None, "skipped": "no_key" if not key else "budget"}
        budget["requests"] += validate_venues.LOOKUP_REQUESTS
        budget["cost_usd"] += validate_venues.LOOKUP_COST
    res = crosscheck.google_lookup(key, name, hint or "", CITIES[city]["display_name"], bias)
    for pause in (2, 5, 10):
        # Places (New) answers DEADLINE_EXCEEDED / INTERNAL / UNAVAILABLE under load: back off and retry
        if not TRANSIENT_RE.search(str(res.get("error") or "")):
            break
        time.sleep(pause)
        res = crosscheck.google_lookup(key, name, hint or "", CITIES[city]["display_name"], bias)
    if "error" not in res:
        seed_venues._cache_put(city, k, query, res)
    return res


TRANSIENT_RE = re.compile(r"DEADLINE_EXCEEDED|UNAVAILABLE|INTERNAL|timed? ?out|Timeout|50\d\b|RESOURCE_EXHAUSTED|Connection", re.I)


def city_bias(city: str) -> dict | None:
    """Places locationBias circle from the city's map centre/span: a chain's outpost in
    this city, not its home branch ('Pace Gallery, Hong Kong' -> 540 W 25th St otherwise)."""
    cfg = CITIES.get(city) or {}
    c, sp = cfg.get("center"), cfg.get("span")
    if not c or not sp:
        return None
    radius = max(sp["latitudeDelta"], sp["longitudeDelta"]) * 111_000 / 2
    return {"lat": c["latitude"], "lng": c["longitude"], "radius_m": int(min(50000, max(5000, radius)))}


def verdict_for(entry: dict, near: list[dict], pl: dict, site: dict, zone: str | None,
                cov: dict) -> tuple[str, list[str]]:
    name = entry.get("name") or ""
    note = f"{entry.get('note') or ''} {entry.get('neighborhood') or ''}"
    why: list[str] = []
    name_ok = bool(pl.get("matched"))
    if pl.get("found") and not name_ok:
        why.append(f"Places only knows a listing outside the metro: {pl.get('address_seen')}" if pl.get("metro") is False
                   else f"places returned a different business: {pl.get('mismatch')!r}")
    if site.get("closed_notice"):
        return "closed", ["closed notice on its own site"]
    if name_ok and pl.get("status") == "CLOSED_PERMANENTLY":
        if site.get("website_live") and site.get("site_vouched") and site.get("exhibitions_page"):
            return "unverified", ["Places: permanently closed, but its own site is live with an exhibitions page — check by hand (moved?)"]
        return "closed", ["Places: permanently closed" + (", website dead" if site.get("website_live") is False else "")]
    if NOT_A_VENUE_RE.search(name) or NOT_A_VENUE_RE.search(note) or venues.NOT_VENUE_RE.search(name):
        return "not_a_venue", [f"name/note reads as a non-exhibition business ({(NOT_A_VENUE_RE.search(name + ' ' + note) or venues.NOT_VENUE_RE.search(name)).group(0).lower()}); the pipeline never targets these"]
    if not name_ok and site.get("website_live") is not True:
        return "not_found", ["no matching Places listing" if pl.get("found") is not None else "Places lookup skipped",
                             "no live website" if site.get("website") else "no website known"] + why
    if zone is None and entry.get("_geo") is not None:
        return "out_of_footprint", [f"{pl.get('address') or entry.get('address')} is beyond every configured zone (>{seed_venues.ZONE_MAX_M} m)"]
    confirmed = (name_ok and pl.get("status") == "OPERATIONAL") or (site.get("website_live") and site.get("site_vouched"))
    dated = site.get("dated") or {}
    if confirmed and dated.get("stale"):
        # a live, venue-looking site whose newest dated show is years old is not proof of an open programme
        return "unverified", [f"site's newest dated show is from {dated['latest_year']}" if dated.get("latest_year")
                              else "no dated show on the site at all"] + why
    if confirmed and dated.get("online") and not (name_ok and pl.get("status") == "OPERATIONAL"):
        return "unverified", ["site describes online exhibitions — physical space unconfirmed"] + why
    if not confirmed:
        r = []
        if name_ok and pl.get("status") not in (None, "OPERATIONAL"):
            r.append(f"Places: {str(pl.get('status')).lower()}")
        if site.get("website") and site.get("website_live") is False:
            r.append("website dead")
        if site.get("website_live") and not site.get("site_vouched"):
            r.append("site does not read as an art venue")
        if not site.get("website"):
            r.append("no website")
        return "unverified", (r or ["not confirmable"]) + why
    # a real, open venue inside the footprint — which nets missed it?
    if cov.get("gpla"):
        why.append(f"IS on Gallery Platform LA as {cov['gpla']!r} — seed_gpla did not create it (name/id clash?)")
    else:
        why.append("not a Gallery Platform LA member")
    if cov.get("carla"):
        why.append(f"IS on Carla's distribution list as {cov['carla']!r} — seed_carla did not create it")
    else:
        why.append("not on Carla's distribution list")
    sw, circ = cov.get("sweep"), cov.get("circle")
    if sw:
        why.append(f"seen by the Places nearby sweep but dropped: {sw['skip'] or 'details not bought'}")
    elif circ and circ["inside"]:
        why.append(f"sits inside a swept Places circle ({circ['type']}, r {circ['radius_m']} m, {circ['dist_m']} m from centre) yet was not returned — not typed {circ['type']} on Google, or beyond the per-circle result cap")
    elif circ:
        why.append(f"outside every swept Places circle (nearest {circ['dist_m']} m away, r {circ['radius_m']} m)")
    else:
        why.append("never returned by the Places nearby sweep")
    if site.get("website_live") and not site.get("site_vouched"):
        why.append("caveat: its site does not read as an exhibition venue")
    if cov.get("enumeration_logs"):
        why.append(f"named in enumeration/scrape logs ({', '.join(cov['enumeration_logs'][:2])}) but no record_venue landed")
    else:
        why.append("no zone-enumeration session named it")
    if near:
        why.append("registry near-miss: " + ", ".join(f"{n['id']} ({n['status']})" for n in near))
    return "miss", why


def check_entry(e: dict, *, city: str, reg: dict, key: str | None, network: bool, fetcher, vctx,
                labeled: list[dict], budget: dict, render: bool, cov: "Coverage | None" = None,
                today_year: int | None = None, retry_without_hint: bool = False) -> dict:
    """Every check for one order entry ``{name, neighborhood?, address?, website?, note?}``
    -> the report row (places / site / dated / geo / zone / coverage / verdict). The
    coverage probe is skipped when ``cov`` is None (quick_city reuses the checks
    without the LA directory nets); ``verdict_for`` then answers ``miss`` for a
    confirmed venue. ``retry_without_hint``: when an address hint steered Places to
    the building instead of the tenant ("Pace Gallery, H Queen's" -> "H Queen's"),
    ask once more by name and district only."""
    name = e.get("name") or ""
    near = registry_near_misses(reg, name)
    cw = city_words(city)

    def nm(found):
        return name_matches(name, found, cw)
    # an entry may carry `address` / `website` hints (hand-added after a first pass)
    hint = e.get("address") or e.get("neighborhood")
    pl = places_lookup(key, city, name, hint, budget) if network else {"found": None, "skipped": "no_network"}
    if network and not (pl.get("found") and nm(pl.get("name"))) and not re.search(r"gallery|galerie", name, re.I):
        pl2 = places_lookup(key, city, name + " gallery", e.get("neighborhood"), budget)
        if pl2.get("found") and nm(pl2.get("name")):
            pl = pl2
    moved = bool(pl.get("found")) and nm(pl.get("name")) and pl.get("status") == "CLOSED_PERMANENTLY"
    if network and retry_without_hint and e.get("address") and (moved or not (pl.get("found") and nm(pl.get("name")))):
        # name + city only (a district label such as "Central/Soho" pulls in New York's SoHo);
        # also when the address hint found the OLD listing of a gallery that moved
        pl3 = places_lookup(key, city, name, None, budget)
        if pl3.get("found") and nm(pl3.get("name")) and (not moved or pl3.get("status") == "OPERATIONAL"):
            pl = pl3
    pl["metro"] = in_metro(pl, city) if pl.get("found") else None
    pl["matched"] = bool(pl.get("found")) and nm(pl.get("name")) and pl["metro"] is not False
    if pl.get("found") and not pl["matched"]:
        # another business (or the same name in another city): keep only the note, never its data
        pl = {"found": True, "matched": False, "mismatch": pl.get("name"), "address_seen": pl.get("address"),
              "metro": pl.get("metro"), "cached": pl.get("cached")}
    website = (pl.get("website") if pl["matched"] else None) or e.get("website")
    fake = {"id": venues.venue_id(name), "name": name, "website": website, "exhibitions_url": None}
    site = validate_venues.site_checks(fake, vctx) if website else {"website": None}
    if site.get("website_live"):
        site["dated"] = stale_check(fetcher, site.get("final_url") or website, site.get("exhibitions_url"), render,
                                    today_year or time.gmtime().tm_year)
    geo = {"lat": pl.get("lat"), "lng": pl.get("lng"), "source": "places"} if pl["matched"] and pl.get("lat") is not None else None
    if geo is None and e.get("address") and key:
        # precise geocode of the hint; a second try drops the unit/zip tail ("4619 W Washington Blvd, Los Angeles")
        tries = [e["address"], ", ".join(e["address"].split(",")[:2])]
        for addr in dict.fromkeys(tries):
            g = seed_venues.geocode_address(key, addr, CITIES[city]["display_name"], city) or {}
            lat, lng = g.get("lat", g.get("latitude")), g.get("lng", g.get("longitude"))
            if lat is not None and lng is not None:
                geo = {"lat": lat, "lng": lng, "source": "geocode:address_hint"}
                break
    zone = seed_venues.assign_zone(geo["lat"], geo["lng"], labeled)[0] if geo else None
    if zone is None and geo is None and e.get("neighborhood"):
        # no coordinates at all: the list's own neighbourhood label, mapped onto the city's zones
        zone = tools.normalize_zone(e["neighborhood"], CITIES[city]["neighborhoods"], city)
    c = cov.check(name, (geo or {}).get("lat"), (geo or {}).get("lng")) if cov is not None else {}
    verdict, why = verdict_for(dict(e, _geo=geo), near, pl, site, zone, c)
    return {"rank": e.get("rank"), "name": name, "neighborhood": e.get("neighborhood"), "note": e.get("note"),
            "hints": {k: e.get(k) for k in ("address", "website") if e.get(k)},
            "verdict": verdict, "why": why, "zone": zone, "geo": geo, "near_misses": near,
            "places": {k: pl.get(k) for k in ("found", "matched", "mismatch", "address_seen", "name", "status", "address", "website", "phone", "hours", "lat", "lng", "metro", "cached", "skipped", "error")},
            "site": {k: site.get(k) for k in ("website", "website_live", "site_vouched", "closed_notice", "exhibitions_page", "exhibitions_url", "js_only", "final_url", "dated", "error")},
            "coverage": c}


def run(city: str, apply: bool, max_places_requests: int, network: bool, refetch: bool,
        render: bool = True, ranks: list[int] | None = None) -> dict:
    _load_env()
    key = os.environ.get("GOOGLE_MAPS_API_KEY") if network else None
    ts = int(time.time())
    session = f"verify-order-{city}-{ts}"
    reg = venues.load_registry(city)
    order = rank_venues.load_order_file(city)
    if not order:
        raise SystemExit(f"no {rank_venues.order_file(city)}")
    _mo, res = rank_venues.resolve_order(order, reg)
    todo_ranks = {u["rank"] for u in res["unresolved"]}
    if ranks:
        todo_ranks = set(ranks)
    by_id = venues.index_by_id(reg)
    entries = []
    for e in order["entries"]:
        if e.get("rank") not in todo_ranks:
            continue
        v = by_id.get(e.get("id") or "")
        if v:   # re-check of a seeded/known venue: its registry website and address are the hints
            e = dict(e, website=e.get("website") or v.get("website"), address=e.get("address") or v.get("address"))
        entries.append(e)
    fetcher = refresh.Fetcher()
    vctx = validate_venues.Ctx(city, fetcher, key, 0, network, True, render, session)
    sctx = seed_venues.Ctx(city, refetch=refetch, key=key or "") if network else None
    cov = Coverage(city, sctx)
    labeled = seed_venues.labeled_points(reg)
    budget = {"requests": 0, "cost_usd": 0.0, "max_requests": max_places_requests, "refetch": refetch}
    print(f"{city}: {len(entries)} unresolved order entries; GPLA {len(cov.gpla)} / Carla {len(cov.carla)} names, "
          f"sweep rows {len(cov.sweep)}, logs {len(cov.logs)}; places budget {max_places_requests} requests", flush=True)
    rows = []
    for e in entries:
        row = check_entry(e, city=city, reg=reg, key=key, network=network, fetcher=fetcher, vctx=vctx,
                          labeled=labeled, budget=budget, render=render, cov=cov)
        rows.append(row)
        print(f"  #{row['rank']:<4} {row['name']:<34} {row['verdict']:<16} {'; '.join(row['why'])[:150]}", flush=True)
    rep = {"session": session, "city": city, "ts": ts, "n": len(rows), "apply": apply,
           "counts": {k: sum(1 for r in rows if r["verdict"] == k) for k in ("miss", "unverified", "closed", "not_found", "out_of_footprint", "not_a_venue")},
           "spend": {"places_requests": budget["requests"], "cost_usd": round(budget["cost_usd"], 4),
                     "fetches": vctx.stats["fetches"], "renders": vctx.stats["renders"]},
           "rows": rows}
    out = tools.CONTENT_DIR / "spend" / "reports" / f"{session}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    (tools.CONTENT_DIR / "spend" / f"{session}.json").write_text(json.dumps({"session": session, "city": city, **rep["spend"]}, indent=1))
    print(f"verdicts {rep['counts']}; places {budget['requests']} requests (${budget['cost_usd']:.2f}); report {out}")
    if apply:
        seeded = seed(city, order, rows, session)
        print(f"seeded {len(seeded)} venue(s): {', '.join(seeded)}" if seeded else "nothing to seed")
        print(f"next: python rank_venues.py apply --city {city} --params <live preset>")
    return rep


def seed(city: str, order: dict, rows: list[dict], session: str) -> list[str]:
    """Create registry records for the misses (and confirmed-but-unverified rows),
    then pin their ids in the order file."""
    known = {e.get("rank") for e in order["entries"] if e.get("id")}
    picked = [r for r in rows if r["rank"] not in known and (r["verdict"] == "miss"
              or (r["verdict"] == "unverified" and r["places"].get("matched") and r["places"].get("status") == "OPERATIONAL"))]
    if not picked:
        return []
    upserts = []
    for r in picked:
        pl, site, geo = r["places"], r["site"], r.get("geo") or {}
        matched = bool(pl.get("matched"))
        verified = r["verdict"] == "miss" and ((matched and pl.get("status") == "OPERATIONAL") or site.get("site_vouched"))
        patch = {
            "name": r["name"], "kind": "gallery", "status": "active" if verified else "unknown",
            "neighborhood": r["zone"], "address": (pl.get("address") if matched else None) or r["hints"].get("address"),
            "website": site.get("website") or r["hints"].get("website"),
            "exhibitions_url": site.get("exhibitions_url"), "phone": pl.get("phone") if matched else None,
            "hours": (pl.get("hours") or []) if matched else [],
            "latitude": geo.get("lat"), "longitude": geo.get("lng"),
            "coords_source": f"{geo.get('source')}:order_lookup" if geo else None,
            "google": ({"status": pl.get("status"), "address": pl.get("address"), "hours": pl.get("hours"),
                        "phone": pl.get("phone"), "website": pl.get("website"), "lat": pl.get("lat"),
                        "lng": pl.get("lng"), "ts": int(time.time())} if matched else None),
            "sources": {"seed": {"order": {"ts": int(time.time()), "rank": r["rank"], "session": session,
                                           "source": order.get("source")}}},
            "verification": {"status": "verified" if verified else "unverified", "ts": int(time.time()),
                             "checks": {"places_status": pl.get("status"), "website_live": site.get("website_live"),
                                        "site_vouched": site.get("site_vouched"), "closed_notice": site.get("closed_notice"),
                                        "exhibitions_page": site.get("exhibitions_page"), "source": "verify_order"}},
        }
        upserts.append({"name": r["name"], "patch": {k: v for k, v in patch.items() if v is not None},
                        "source": "seed-order", "website": patch.get("website")})
    out = venues.bulk_upsert(city, upserts)
    ids = out["new"] + out["updated"]
    by_rank = {r["rank"]: vid for r, vid in zip(picked, ids)}
    for e in order["entries"]:
        if e.get("rank") in by_rank:
            e["id"] = by_rank[e["rank"]]
    p = rank_venues.order_file(city)
    p.write_text(json.dumps(order, indent=1, ensure_ascii=False) + "\n")
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--apply", action="store_true", help="seed misses into the registry and pin ids in venue_order.json")
    ap.add_argument("--max-places-requests", type=int, default=60)
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--refetch", action="store_true", help="ignore cached lookups / directory pages")
    ap.add_argument("--ranks", help="re-check these list ranks (comma-separated) even when they already resolve; never re-seeds them")
    a = ap.parse_args(argv)
    ranks = [int(x) for x in a.ranks.split(",")] if a.ranks else None
    run(a.city, a.apply, a.max_places_requests, not a.no_network, a.refetch, render=not a.no_render, ranks=ranks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
