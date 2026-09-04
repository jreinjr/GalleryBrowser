"""Stage G2 of the galleries-first pipeline: deterministic venue validation.

    python validate_venues.py run --city los-angeles [--apply] [--max-places-requests N]
                                  [--venue-ids a,b|@file] [--limit N] [--workers 8]
                                  [--no-network] [--no-discover] [--no-render]

No LLM. For every venue with status active / unknown / appointment_only /
candidate (parked Places-only venues are skipped unless named with
--venue-ids) it gathers `verification.checks` and derives `verification.status`
per docs/GALLERIES.md:

    places_status   registry google block -> cached legacy nearby sweep
                    (scraper/.cache/places/<city>/, matched by seed place_id, else
                    normalized name + proximity) -> one live Places lookup only
                    while --max-places-requests allows (legacy = 2 HTTP requests,
                    $0.052; result also stored through venues.on_crosscheck)
    places_ratings  {rating, user_ratings_total} from the cached sweep (ranker input)
    address_match   every digit group of the stored street line appears in the
                    Places address (None when either side has no digits)
    website_live    homepage GET returns 200 (refresh.Fetcher: robots, 2 s/domain)
    js_only         page reads as a JS shell; text then comes from tools.render_fetch
    site_vouched    homepage title+text reads like an art venue (venues.VENUE_RE,
                    the seed_venues --vouch-parked test)
    closed_notice   venues.CLOSED_RE on the homepage text
    exhibitions_page registry exhibitions_url, else refresh.discover_exhibitions_url
                    (skipped with --no-discover)

    verified   = (OPERATIONAL, or no Places row but site vouched) and website_live
                 and (address_match is not False) and not closed_notice
    flagged    = CLOSED_PERMANENTLY / CLOSED_TEMPORARILY / (NOT_FOUND and site dead)
                 / closed_notice
    unverified = everything else (no website, dead site, address mismatch, ...)

Only `verified` venues enter research (G3), ranking gates (G4) and show scraping
(S1). Writes the registry only with --apply; always writes an audit JSONL to
content/spend/validate-<city>-<ts>.jsonl and a spend record next to it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crosscheck  # noqa: E402
import refresh  # noqa: E402
import seed_venues  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

TARGET_STATUSES = ("active", "unknown", "appointment_only", "candidate", None)
NEARBY_MATCH_M = 300          # cached nearby row this close + same normalized name -> same venue
LOOKUP_REQUESTS = 2           # legacy textsearch + details
LOOKUP_COST = seed_venues.COST_USD["nearby_legacy"] + seed_venues.COST_USD["details_legacy"]
FLAGGED_PLACES = ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")
RENDER_MAX_CHARS = 6000


# --- status derivation (pure) ---------------------------------------------------

def derive_status(checks: dict) -> tuple[str, list[str]]:
    """(status, reasons) from a checks dict; the only place the rule lives."""
    ps = checks.get("places_status")
    live = checks.get("website_live")
    reasons: list[str] = []
    if checks.get("closed_notice"):
        return "flagged", ["closed_notice"]
    if ps == "CLOSED_TEMPORARILY" and live and checks.get("site_vouched") \
            and checks.get("exhibitions_page") and checks.get("address_match") is not False:
        # Google's "temporarily closed" is stale for a quarter of LA galleries
        # (2026-09-03: 23 of 47 had a live, vouched site with a dated exhibitions
        # page). The gallery's own site outranks the listing; the reason keeps
        # the disagreement visible for the ranker/dashboard.
        return "verified", ["site_vouched", "places_closed_temporarily_overridden"]
    if ps in FLAGGED_PLACES:
        return "flagged", [f"places_{ps.lower()}"]
    if ps == "NOT_FOUND" and live is False:
        return "flagged", ["places_not_found", "site_dead"]
    if checks.get("website") is None:
        reasons.append("no_website")
    elif live is not True:
        reasons.append("site_dead" if live is False else "site_unchecked")
    if checks.get("address_match") is False:
        reasons.append("address_mismatch")
    if ps is None and not checks.get("site_vouched"):
        reasons.append("no_places_row" if checks.get("places_checked") else "places_unchecked")
        if checks.get("website_live"):
            reasons.append("site_not_vouched")
    if ps not in (None, "OPERATIONAL"):
        reasons.append(f"places_{str(ps).lower()}")
    if not reasons:
        return "verified", ["operational" if ps == "OPERATIONAL" else "site_vouched"]
    return "unverified", reasons


POSTAL_RE = re.compile(r"〒?\b\d{3}-\d{4}\b|\b\d{5}(?:-\d{4})?\b")
FLOOR_RE = re.compile(r"\b(?:B\d+F?|\d+F|\d+(?:st|nd|rd|th) floor|\d+階)\b|\bsuite\s*#?\d+\b|#\d+\b", re.I)


def street_digits(address: str | None) -> list[str]:
    """Digit groups of the street line only: postal codes, floors and suite
    numbers are dropped (they differ between a venue's listing and Google's
    formatted address without meaning a different place)."""
    a = unicodedata.normalize("NFKC", address or "")
    a = POSTAL_RE.sub(" ", a)
    a = FLOOR_RE.sub(" ", a)
    return crosscheck._digit_groups(a)


def address_match(venue_address: str | None, places_address: str | None) -> bool | None:
    stored = street_digits(venue_address)
    if not stored or not places_address:
        return None
    listing = crosscheck._digit_groups(unicodedata.normalize("NFKC", places_address))
    return all(d in listing for d in stored)


CLOSED_SELF_RE = re.compile(r"\b(we|our|this (gallery|space)|the gallery|the space)\b", re.I)


def closed_notice(text: str, name: str | None) -> bool:
    """A closure notice ABOUT THIS VENUE: venues.CLOSED_RE within a sentence
    that names the venue or speaks in the first person, and that is not about
    'galleries' in general ("Many galleries closed their doors" is commentary)."""
    if not text:
        return False
    words = {w for w in crosscheck._name_words(name or "") if w not in crosscheck.GENERIC_NAME_WORDS}
    for m in venues.CLOSED_RE.finditer(text):
        start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
        end_candidates = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i != -1]
        end = min(end_candidates) if end_candidates else len(text)
        sent = text[start:end]
        if re.search(r"\bgalleries\b", sent, re.I):
            continue
        if CLOSED_SELF_RE.search(sent) or (words & crosscheck._name_words(sent)):
            return True
    return False


# --- cached Places sweep index --------------------------------------------------

def load_nearby_index(city: str) -> dict:
    """{by_place_id: {pid: row}, by_norm: {norm: [row]}} from the cached legacy
    nearby responses (30-day cache written by seed_venues.places_nearby)."""
    by_pid: dict[str, dict] = {}
    by_norm: dict[str, list[dict]] = {}
    d = seed_venues._cache_dir(city)
    if not d.exists():
        return {"by_place_id": by_pid, "by_norm": by_norm}
    for f in d.glob("*.json"):
        try:
            obj = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        q = obj.get("query") if isinstance(obj, dict) else None
        if not isinstance(q, dict) or q.get("op") != "nearby":
            continue
        data = obj.get("data")
        pages = data if isinstance(data, list) else [data]
        for page in pages:
            rows = (page or {}).get("results") if isinstance(page, dict) else None
            for r in rows or []:
                row = _nearby_row(r)
                if not row or not row["place_id"]:
                    continue
                if row["place_id"] not in by_pid:
                    by_pid[row["place_id"]] = row
                    by_norm.setdefault(row["norm"], []).append(row)
    return {"by_place_id": by_pid, "by_norm": by_norm}


def _nearby_row(r: dict) -> dict | None:
    if not isinstance(r, dict) or not r.get("name"):
        return None
    if "place_id" in r and "geometry" in r:      # legacy shape
        loc = (r.get("geometry") or {}).get("location") or {}
        return {"place_id": r.get("place_id"), "name": r["name"],
                "norm": tools._norm_venue(r["name"]), "lat": loc.get("lat"), "lng": loc.get("lng"),
                "status": r.get("business_status"), "rating": r.get("rating"),
                "user_ratings_total": r.get("user_ratings_total"),
                "address": r.get("vicinity") or r.get("formatted_address")}
    return None


def match_nearby(v: dict, index: dict) -> dict | None:
    pid = (((v.get("sources") or {}).get("seed") or {}).get("places") or {}).get("place_id")
    if pid and pid in index["by_place_id"]:
        return index["by_place_id"][pid]
    norms = {tools._norm_venue(v["name"])} | {tools._norm_venue(a) for a in v.get("aliases") or []}
    cands = [r for n in norms for r in index["by_norm"].get(n, [])]
    if not cands:
        return None
    lat, lng = v.get("latitude"), v.get("longitude")
    if lat is not None and lng is not None:
        near = [r for r in cands if r["lat"] is not None
                and crosscheck.haversine_m(lat, lng, r["lat"], r["lng"]) <= NEARBY_MATCH_M]
        return near[0] if near else None
    return cands[0] if len(cands) == 1 else None


# --- one venue --------------------------------------------------------------------

class Ctx:
    def __init__(self, city: str, fetcher, key: str | None, max_places_requests: int,
                 network: bool, discover: bool, render: bool, session: str):
        self.city, self.fetcher, self.key = city, fetcher, key
        self.max_places_requests = max_places_requests
        self.network, self.discover, self.render = network, discover, render
        self.session = session
        self.lock = threading.Lock()
        self.stats = {"places_requests": 0, "cost_usd": 0.0, "lookups": 0, "fetches": 0,
                      "renders": 0}
        self.index = load_nearby_index(city)
        self.city_name = CITIES[city]["display_name"]

    def take_lookup(self) -> bool:
        with self.lock:
            if self.stats["places_requests"] + LOOKUP_REQUESTS > self.max_places_requests:
                return False
            self.stats["places_requests"] += LOOKUP_REQUESTS
            self.stats["cost_usd"] += LOOKUP_COST
            self.stats["lookups"] += 1
            return True


def places_checks(v: dict, ctx: Ctx) -> dict:
    out: dict = {"places_status": None, "places_source": None, "places_address": None,
                 "places_name": None, "places_ratings": None, "places_checked": False}
    g = v.get("google") or {}
    row = match_nearby(v, ctx.index) if ctx.index["by_place_id"] else None
    if row:
        out["places_ratings"] = {"rating": row.get("rating"),
                                 "user_ratings_total": row.get("user_ratings_total")}
    if g.get("status") or g.get("address"):
        out.update(places_status=g.get("status"), places_source="registry",
                   places_address=g.get("address"), places_name=g.get("name"),
                   places_checked=True)
        return out
    if row:
        out.update(places_status=row.get("status"), places_source="nearby_cache",
                   places_address=row.get("address"), places_name=row.get("name"),
                   places_checked=True)
        return out
    if ctx.network and ctx.key and v.get("address") and ctx.take_lookup():
        res = crosscheck.google_lookup(ctx.key, v["name"], v["address"], ctx.city_name)
        out["places_checked"] = True
        out["places_source"] = "lookup"
        if res.get("found"):
            out.update(places_status=res.get("status"), places_address=res.get("address"),
                       places_name=res.get("name"), places_lookup=res)
        elif res.get("found") is False:
            out["places_status"] = "NOT_FOUND"
        else:
            out["places_error"] = res.get("error")
    return out


def site_checks(v: dict, ctx: Ctx) -> dict:
    out: dict = {"website": v.get("website"), "website_live": None, "js_only": False,
                 "site_vouched": None, "closed_notice": None, "exhibitions_page": None,
                 "final_url": None, "evidence_path": None}
    site = v.get("website")
    if not site:
        return out
    out["exhibitions_page"] = bool(v.get("exhibitions_url")) or None
    if not ctx.network:
        return out
    url = site if site.startswith("http") else "https://" + site
    r = ctx.fetcher.get(url)
    with ctx.lock:
        ctx.stats["fetches"] += 1
    if r["status"] != 200 or not r["html"]:
        out["website_live"] = False
        out["error"] = r.get("error") or f"http_{r['status']}"
        return out
    out["website_live"] = True
    out["final_url"] = r["final_url"]
    text, title = refresh.extract_main_text(r["html"])
    if refresh.looks_js_rendered(r["html"], text):
        out["js_only"] = True
        if ctx.render:
            with ctx.lock:
                ctx.stats["renders"] += 1
            try:
                rendered = json.loads(tools.render_fetch(r["final_url"], RENDER_MAX_CHARS))
            except (json.JSONDecodeError, TypeError):
                rendered = {}
            if rendered.get("text"):
                text, title = rendered["text"], rendered.get("title") or title
    blob = f"{title or ''}\n{text}"
    out["site_vouched"] = bool(venues.VENUE_RE.search(blob)
                               and not venues.NOT_VENUE_RE.search(v["name"] or "")
                               and not venues.NOT_VENUE_RE.search(title or ""))
    out["closed_notice"] = closed_notice(text, v.get("name"))
    if text:
        try:
            out["evidence_path"] = venues.write_evidence(ctx.city, r["final_url"], "homepage",
                                                         text, ctx.session, v["id"])
        except (OSError, ValueError):   # evidence is a bonus, never a blocker
            out["evidence_path"] = None
    if out["exhibitions_page"] is None and ctx.discover:
        try:
            exh, _page = refresh.discover_exhibitions_url(ctx.fetcher, v)
        except Exception:
            exh = None
        out["exhibitions_page"] = bool(exh)
        if exh:
            out["exhibitions_url"] = exh
    return out


def validate_one(v: dict, ctx: Ctx) -> dict:
    checks: dict = {}
    try:
        checks.update(places_checks(v, ctx))
    except Exception as exc:   # one bad venue never stops the sweep
        checks["places_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        checks.setdefault("places_status", None)
    try:
        checks.update(site_checks(v, ctx))
    except Exception as exc:
        checks.setdefault("website", v.get("website"))
        checks["site_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        checks.setdefault("website_live", None)
    checks["address_match"] = address_match(v.get("address"), checks.get("places_address"))
    status, reasons = derive_status(checks)
    return {"id": v["id"], "name": v["name"], "status": status, "reasons": reasons,
            "checks": checks, "ts": int(time.time())}


# --- registry write ----------------------------------------------------------------

def apply_results(city: str, results: list[dict]) -> int:
    by_id = {r["id"]: r for r in results}
    n = 0
    with venues.locked_registry(city) as reg:
        for v in reg["venues"]:
            r = by_id.get(v["id"])
            if not r:
                continue
            venues.ensure_v2(v)
            checks = dict(r["checks"])
            lookup = checks.pop("places_lookup", None)
            v["verification"] = {"status": r["status"], "ts": r["ts"], "checks": checks,
                                 "reasons": r["reasons"]}
            if lookup and lookup.get("found"):
                if venues.listing_matches(v, lookup):
                    v["google"] = venues.google_block(lookup, r["ts"])
                    v.setdefault("sources", {})["crosscheck_ts"] = r["ts"]
                    v["sources"].pop("crosscheck_mismatch", None)
                else:
                    # somebody else's listing: keep it off the record (see
                    # venues.on_crosscheck)
                    v.setdefault("sources", {})["crosscheck_mismatch"] = {
                        "name": lookup.get("name"), "address": lookup.get("address"), "ts": r["ts"]}
                    lookup = None
            if lookup and lookup.get("status") == "CLOSED_PERMANENTLY":
                v["status"] = "closed"
            if checks.get("exhibitions_url") and not v.get("exhibitions_url"):
                v["exhibitions_url"] = checks["exhibitions_url"]
                v["exhibitions_url_source"] = "discovered"
            if checks.get("site_vouched") and checks.get("website_live"):
                site = v.setdefault("sources", {}).get("site") or {}
                if not site:
                    v["sources"]["site"] = {"ts": r["ts"], "url": checks.get("final_url"),
                                            "why": "validate_homepage"}
            n += 1
    return n


# --- CLI ----------------------------------------------------------------------------

def select_targets(reg: dict, venue_ids: list[str] | None, limit: int | None) -> list[dict]:
    out = []
    wanted = set(venue_ids or [])
    for v in reg["venues"]:
        if wanted:
            if v["id"] in wanted:
                out.append(v)
            continue
        if v.get("status") not in TARGET_STATUSES:
            continue
        if venues.seed_only_places(v) and not venues.places_plausible(v):
            continue   # parked: never spend a fetch on it unless named
        out.append(v)
    if limit:
        out = out[:limit]
    return out


def _parse_ids(spec: str | None) -> list[str] | None:
    if not spec:
        return None
    if spec.startswith("@"):
        return [ln.strip() for ln in Path(spec[1:]).read_text().splitlines() if ln.strip()]
    return [x.strip() for x in spec.split(",") if x.strip()]


def summarize(results: list[dict]) -> str:
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if r["status"] != "verified":
            for x in r["reasons"]:
                reasons[x] = reasons.get(x, 0) + 1
    lines = ["status counts: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))]
    lines.append("non-verified reasons: " + ", ".join(
        f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])))
    flagged = [r for r in results if r["status"] == "flagged"]
    if flagged:
        lines.append(f"flagged ({len(flagged)}):")
        for r in flagged[:60]:
            lines.append(f"  {r['id']:<40} {', '.join(r['reasons'])}")
    return "\n".join(lines)


def run(city: str, apply: bool = False, venue_ids: list[str] | None = None,
        limit: int | None = None, max_places_requests: int = 0, workers: int = 8,
        network: bool = True, discover: bool = True, render: bool = True,
        quiet: bool = False) -> dict:
    key = os.environ.get("GOOGLE_MAPS_API_KEY") if network else None
    ts = int(time.time())
    session = f"validate-{city}-{ts}"
    reg = venues.load_registry(city)
    targets = select_targets(reg, venue_ids, limit)
    ctx = Ctx(city, refresh.Fetcher(), key, max_places_requests, network, discover, render, session)
    if not quiet:
        print(f"{city}: validating {len(targets)} venue(s); nearby cache rows "
              f"{len(ctx.index['by_place_id'])}; places budget {max_places_requests} requests",
              flush=True)
    results: list[dict] = []
    audit = tools.CONTENT_DIR / "spend" / f"{session}.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)

    def work(v: dict) -> dict:
        r = validate_one(v, ctx)
        with ctx.lock:
            tools._append_jsonl(audit, r)
            if not quiet:
                print(f"  {r['status']:<10} {r['id']:<40} {', '.join(r['reasons'])}", flush=True)
        return r

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        results = list(ex.map(work, targets))
    spend = {"session": session, "city": city, "n": len(results), "apply": apply, **ctx.stats,
             "cost_usd": round(ctx.stats["cost_usd"], 4)}
    (tools.CONTENT_DIR / "spend" / f"{session}.json").write_text(json.dumps(spend, indent=1))
    if not quiet:
        print(summarize(results))
        print(f"places lookups {ctx.stats['lookups']} ({ctx.stats['places_requests']} requests, "
              f"${ctx.stats['cost_usd']:.2f}); fetches {ctx.stats['fetches']}; renders "
              f"{ctx.stats['renders']}; audit {audit}")
    if apply:
        n = apply_results(city, results)
        if not quiet:
            print(f"registry updated: {n} venue(s)")
    elif not quiet:
        print("dry run — pass --apply to write verification blocks")
    return {"results": results, "spend": spend}


def rederive(city: str, audit: Path, apply: bool = False, quiet: bool = False) -> list[dict]:
    """Re-run the pure parts (address_match, closed_notice from the saved
    homepage evidence, derive_status) over an audit JSONL — for rule fixes
    without refetching. Keeps the latest row per venue id."""
    rows: dict[str, dict] = {}
    for line in audit.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    reg = venues.index_by_id(venues.load_registry(city))
    out = []
    for r in rows.values():
        v = reg.get(r["id"]) or {}
        c = r["checks"]
        c["address_match"] = address_match(v.get("address"), c.get("places_address"))
        ep = c.get("evidence_path")
        if ep and (venues.SCRAPER_DIR / ep).exists():
            c["closed_notice"] = closed_notice((venues.SCRAPER_DIR / ep).read_text(errors="replace"),
                                               v.get("name") or r["name"])
        r["status"], r["reasons"] = derive_status(c)
        out.append(r)
    if not quiet:
        print(summarize(out))
    if apply:
        n = apply_results(city, out)
        if not quiet:
            print(f"registry updated: {n} venue(s)")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--city", required=True, choices=sorted(CITIES))
    r.add_argument("--apply", action="store_true")
    r.add_argument("--venue-ids", help="comma list or @file (one id per line)")
    r.add_argument("--limit", type=int)
    r.add_argument("--max-places-requests", type=int, default=0,
                   help="paid Places HTTP requests allowed (legacy lookup = 2, $0.052)")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--no-network", action="store_true", help="registry + cache only")
    r.add_argument("--no-discover", action="store_true",
                   help="don't probe listing paths for venues lacking exhibitions_url")
    r.add_argument("--no-render", action="store_true", help="never launch the headless renderer")
    rd = sub.add_parser("rederive", help="recompute statuses from an audit JSONL (no fetch)")
    rd.add_argument("--city", required=True, choices=sorted(CITIES))
    rd.add_argument("--audit", required=True)
    rd.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    from run_scrape import load_env
    load_env()
    if args.cmd == "rederive":
        rederive(args.city, Path(args.audit), apply=args.apply)
        return 0
    run(args.city, apply=args.apply, venue_ids=_parse_ids(args.venue_ids), limit=args.limit,
        max_places_requests=args.max_places_requests, workers=args.workers,
        network=not args.no_network, discover=not args.no_discover, render=not args.no_render)
    return 0


if __name__ == "__main__":
    sys.exit(main())
