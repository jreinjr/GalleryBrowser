"""Build the client-facing curation site (plan: Part C; Galleries mode added 2026-09-03).

    python curation_site.py build  --city los-angeles [--out DIR] [--contact-email E]
                                   [--site-url URL] [--thumb-width 320] [--force]
    python curation_site.py decode '<shared link or #p= token>' [--out params.json]
                                   [--out-galleries venues.json] [--city C]

``build`` reads the show score report (``content/spend/reports/curation-<city>.json``,
written by ``curate.py score``), the live feed record
(``content/curation/<city>/curated.json`` — its ``params`` block is what actually
ships, so it is the page's default), ``sources.json``, the pool files (published +
pending) for gallery links and a thumbnail per show, AND the gallery ranking report
(``content/curation/<city>/venues_ranked.json``, written by ``rank_venues.py
score``/``apply``) plus the registry (``content/venues/<city>.json``) to confirm the
report is the ranking that was applied. It writes a self-contained ``index.html``
(data inlined, ``noindex``), ``thumbs/`` and a ``vercel.json`` into
``webdemo/dist/gallery-browser-curation/``.

The page has two modes. **Galleries** (default) ranks every listed venue with the
gallery core (``dashboard/venue_core.js``, wrapped in an IIFE as ``G`` so its
names never collide with the shows core); **Shows** is the original featured-feed
ranking. Deploy:  cd webdemo/dist/gallery-browser-curation && vercel deploy --prod --yes

The page shares settings as ``<site>/#p=<shows diff>&g=<galleries diff>&v=<mode>``
(each diff a base64url JSON diff vs live; parts omitted when empty). ``decode``
turns such a link back into params files that ``curate.py apply --params`` and
``rank_venues.py apply --params`` accept. Refuses to build when the show report is
older than ``curated.json`` or was scored with different params, or when the
gallery report's params hash is not the one applied to the registry, unless
``--force``.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONTENT_DIR = ROOT / "content"
TEMPLATE = HERE / "dashboard" / "client_template.html"
DEFAULT_OUT = ROOT / "webdemo" / "dist" / "gallery-browser-curation"
DEFAULT_SITE_URL = "https://gallery-browser-curation.vercel.app/"
DEFAULT_CONTACT = "jreinjr555@hotmail.com"

sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "webdemo"))
import curation_dashboard  # noqa: E402
import venue_dashboard  # noqa: E402

# Parameter keys the client page may change; everything else stays at the live value.
CLIENT_PATHS = ["threshold", "max_per_venue", "exclude_museums",
                "weights.judge", "weights.quality", "weights.venue", "weights.press",
                "weights.closing_soon", "weights.museum"]
DEEP_MERGE_KEYS = ("weights", "half_life_days", "kind_weights", "strength_weights",
                   "venue_tier_map", "judge", "overrides")   # = curate._DEEP_MERGE_KEYS
PARAM_KEY_ORDER = ["version", "city", "today", "max_n", "threshold", "max_per_venue", "include_pending",
                   "published_only", "require_open", "editors_pick_top", "exclude_museums",
                   "max_per_neighborhood", "max_museum_share", "weights", "half_life_days", "press_ref",
                   "artist_ref", "cap_per_source", "wiki_ref", "default_source_weight", "kind_weights",
                   "strength_weights", "sources", "venue_tier_map", "venue_rank_ref", "judge", "overrides"]

SIGNAL_KEYS = ("kind", "strength", "date", "snippet", "url", "n_rows", "match_confidence", "match_method")
SOURCE_KEYS = ("id", "name", "kind", "weight", "active")
JUDGE_KEYS = ("model", "overall", "confidence", "scores", "rationale")
WIKI_KEYS = ("title", "url", "pageviews_90d")
SHOW_KEYS = ("slug", "title", "artist", "pool", "in_window", "dates", "days_since_open", "days_to_close",
             "n_images", "desc_words", "features", "contrib", "score", "rank", "reasons", "keyword_hits")
VENUE_KEYS = ("name", "id", "neighborhood", "is_museum", "tier", "n_fair", "norm")

# --- galleries mode (rank_venues.py / dashboard/venue_core.js) -----------------------
# These mirror venue_core.js; test_curation_site asserts they stay in sync (rank_venues is
# not imported here because it drags in the registry/hours/tools modules).
GALLERY_CORE_MARKER = "<!-- GALLERY_CORE -->"
G_VENUE_KEYS = ("id", "name", "kind", "is_museum", "neighborhood", "status", "verification", "website", "about", "raw")
G_FEATURES = ["hours_breadth", "fairs", "curated_lists", "press", "directory", "longevity", "roster_size",
              "roster_strength", "show_cadence", "multi_location", "places_popularity", "web_presence",
              "venue_judge", "wiki", "kind_gallery", "kind_nonprofit", "kind_museum", "seesaw_presence"]
G_HIDDEN_WEIGHTS = ("roster_strength", "kind_gallery", "kind_nonprofit", "seesaw_presence")
G_CLIENT_PATHS = ["gates.require_verified", "gates.kinds", "tiers.1", "tiers.2", "tiers.3"] + \
                 [f"weights.{f}" for f in G_FEATURES if f not in G_HIDDEN_WEIGHTS] + ["manual_order"]
G_DEEP_MERGE_KEYS = ("weights", "refs", "list_weights", "gates", "tiers", "judge")   # = rank_venues._DEEP_MERGE_KEYS
G_PARAM_KEY_ORDER = ["version", "city", "today", "weights", "refs", "list_weights", "gates", "tiers", "judge", "leak_seesaw", "manual_order"]
G_EXPORTS = ["R", "VENUES", "FEATURES", "FEATURE_LABEL", "STACK_ORDER", "FEATURE_SLOT", "KIND_GROUP",
             "computeFeatures", "scoreVenue", "gateFor", "tierFor", "rank", "manualPositions", "normalizeParams", "mergeParams",
             "orderedParams", "diffParams", "PARAM_KEY_ORDER", "DEEP_KEYS", "parityCheck"]
G_EMPTY_RAW_KEYS = ("judge", "wiki", "seesaw")   # dropped when {} / None; computeFeatures treats missing the same
ABOUT_MAX = 600


# --- share links ----------------------------------------------------------------

def encode_diff(diff: dict) -> str:
    """dict -> base64url token (no padding), matching the page's JS encoder."""
    raw = json.dumps(diff, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_token(token: str) -> dict:
    token = token.strip()
    pad = "=" * (-len(token) % 4)
    obj = json.loads(base64.urlsafe_b64decode(token + pad).decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("share token does not hold a JSON object")
    return obj


FRAGMENT_KEYS = ("p", "g", "v")


def parse_fragment(link: str) -> dict[str, str]:
    """Accept a full URL (``…/#p=TOKEN&g=TOKEN&v=shows``), a bare ``#p=TOKEN`` /
    ``p=TOKEN`` or a bare token (treated as ``p``). Returns the present keys among
    ``p`` (shows diff), ``g`` (galleries diff) and ``v`` (mode)."""
    s = link.strip()
    frag = urlparse(s).fragment if "://" in s else s.lstrip("#")
    frag = unquote(frag).strip()
    out: dict[str, str] = {}
    if not frag:
        return out
    for part in frag.split("&"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            if k in FRAGMENT_KEYS and v:
                out[k] = v
        elif not out:
            out["p"] = part          # legacy bare token
    return out


def token_from_link(link: str) -> str:
    """Shows token (``p``) of a link; raises when absent (legacy API)."""
    tok = parse_fragment(link).get("p")
    if not tok:
        raise ValueError("no #p= token in link")
    return tok


def merge_params(base: dict, override: dict, deep_keys=DEEP_MERGE_KEYS) -> dict:
    """curate._merge_params semantics: dict-valued deep keys merged per key."""
    for k, v in (override or {}).items():
        if k in deep_keys and isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def ordered_params(p: dict, order=PARAM_KEY_ORDER) -> dict:
    out = {k: p[k] for k in order if k in p}
    out.update({k: v for k, v in p.items() if k not in out and not k.startswith("_")})
    return out


def diff_params(live: dict, current: dict, paths: list[str] = CLIENT_PATHS) -> dict:
    """Nested dict of the client-editable values that differ from ``live``."""
    def get(o, path):
        for k in path.split("."):
            o = (o or {}).get(k) if isinstance(o, dict) else None
        return o
    out: dict = {}
    for path in paths:
        a, b = get(live, path), get(current, path)
        if a != b:
            head, *rest = path.split(".")
            if rest:
                out.setdefault(head, {})[rest[0]] = b
            else:
                out[head] = b
    return out


def decode_link(link: str, live_params: dict) -> dict:
    """Shared link -> full show params (live params with the shared diff merged)."""
    full = merge_params(json.loads(json.dumps(live_params)), decode_token(token_from_link(link)))
    full.pop("_note", None)
    return ordered_params(full)


def merge_gallery_params(base: dict, override: dict) -> dict:
    return merge_params(base, override, G_DEEP_MERGE_KEYS)


def ordered_gallery_params(p: dict) -> dict:
    return ordered_params(p, G_PARAM_KEY_ORDER)


def decode_gallery_link(token: str, params_default: dict) -> dict:
    """``g`` token -> full rank_venues params (report defaults with the diff merged)."""
    full = merge_gallery_params(json.loads(json.dumps(params_default)), decode_token(token))
    full.pop("_note", None)
    return ordered_gallery_params(full)


# --- report trimming --------------------------------------------------------------

def _pick(d: dict, keys) -> dict:
    return {k: d[k] for k in keys if k in (d or {})}


def trim_judge(judge: dict, cfg: dict) -> dict:
    """Keep only the verdicts the live params can read: the configured variant,
    and within it the model(s) ``judgeFeature`` would pick (``mean`` keeps all)."""
    variant, model = (cfg or {}).get("variant"), (cfg or {}).get("model") or "none"
    if model == "none" or not variant or variant not in (judge or {}):
        return {}
    kept = {}
    for mid, row in (judge[variant] or {}).items():
        if model == "mean" or model in mid.lower():
            kept[mid] = _pick(row, JUDGE_KEYS)
    return {variant: kept} if kept else {}


def trim_show(row: dict, judge_cfg: dict, pool_show: dict | None, thumb: str | None,
              live_featured: dict) -> dict:
    out = _pick(row, SHOW_KEYS)
    out["venue"] = _pick(row.get("venue") or {}, VENUE_KEYS)
    out["signals"] = [dict(_pick(s, SIGNAL_KEYS), source=_pick(s.get("source") or {}, ("id", "name", "kind")))
                      for s in row.get("signals") or []]
    out["judge"] = trim_judge(row.get("judge") or {}, judge_cfg)
    out["wiki"] = _pick(row.get("wiki") or {}, WIKI_KEYS)
    out["keyword_hits"] = [_pick(h, ("class", "label", "weight", "term", "where")) for h in row.get("keyword_hits") or []]
    ps = pool_show or {}
    out["source_urls"] = list(ps.get("source_urls") or [])
    out["venue_website"] = (ps.get("venue") or {}).get("website")
    out["reception"] = ps.get("reception")
    out["thumb"] = thumb
    lf = live_featured.get(row["slug"])
    out["featured_live"] = lf is not None
    out["featured_rank_live"] = lf["featured_rank"] if lf else None
    out["editors_pick_live"] = bool(lf and lf.get("editors_pick"))
    return out


def _truncate(text: str | None, limit: int = ABOUT_MAX) -> str | None:
    if not text or len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def _status_gated(v: dict) -> bool:
    return str(v.get("gate") or "").startswith("status:")


def trim_venue(v: dict) -> dict:
    """One ranked venue -> what the Galleries mode needs: identity, ``raw`` evidence
    (the JS core recomputes every feature from it) and the live rank/tier for the
    "vs live" chips. ``features``/``basis``/``contrib`` are recomputed in the page."""
    out = _pick(v, G_VENUE_KEYS)
    out["about"] = _truncate(v.get("about"))
    raw = dict(v.get("raw") or {})
    for k in G_EMPTY_RAW_KEYS:
        if k in raw and not raw[k]:
            del raw[k]
    out["raw"] = raw
    out["rank_live"] = v.get("rank")
    out["score_live"] = round(v["score"], 6) if v.get("score") is not None else None
    out["tier_live"] = v.get("tier")
    out["gate_live"] = v.get("gate")
    return out


def feature_coverage(rows: list[dict], feature_names=G_FEATURES) -> dict[str, int]:
    """Per feature: how many (untrimmed) venues have a non-zero value. The page hides
    the slider of a feature nobody in the city scores on ("no data yet")."""
    return {f: sum(1 for r in rows if (r.get("features") or {}).get(f)) for f in feature_names}


def build_galleries(city: str, vreport: dict) -> dict:
    rows = [v for v in vreport.get("venues") or [] if not _status_gated(v)]
    params = {k: v for k, v in (vreport.get("params_default") or {}).items() if not k.startswith("_")}
    params["city"] = city
    params["today"] = vreport.get("today")
    tiers: dict[str, int] = {}
    for v in rows:
        if not v.get("gate"):
            tiers[str(v.get("tier"))] = tiers.get(str(v.get("tier")), 0) + 1
    return {
        "city": city,
        "today": vreport.get("today"),
        "generated_at": vreport.get("generated_at"),
        "params_default": params,
        "params_hash": vreport.get("params_hash"),
        "order": vreport.get("order"),          # market order file block (rank_venues.apply_market_order) or None
        "client_paths": G_CLIENT_PATHS,
        "hidden_weights": list(G_HIDDEN_WEIGHTS),
        "coverage": feature_coverage(rows),
        "counts": {"venues": len(vreport.get("venues") or []), "listed": len(rows),
                   "status_gated": len(vreport.get("venues") or []) - len(rows),
                   "verified": sum(1 for v in rows if v.get("verification") == "verified"),
                   "researched": sum(1 for v in rows if v.get("report_path")),
                   "tiers": tiers},
        "venues": [trim_venue(v) for v in rows],
    }


def load_pool(city: str) -> dict[str, dict]:
    pool: dict[str, dict] = {}
    for path in (CONTENT_DIR / f"{city}.json", CONTENT_DIR / "pending" / f"{city}.json"):
        if path.exists():
            for s in json.loads(path.read_text()).get("shows", []):
                pool.setdefault(s["slug"], s)
    return pool


def city_display_name(city: str) -> str:
    try:
        from cities import CITIES  # scraper/cities.py
        return CITIES[city]["display_name"]
    except Exception:  # noqa: BLE001 - cosmetic fallback
        return city.replace("-", " ").title()


def _params_core(p: dict) -> dict:
    return {k: v for k, v in (p or {}).items() if k not in ("city", "today") and not k.startswith("_")}


def check_freshness(report_file: Path, curated_file: Path, report: dict, curated: dict, force: bool) -> list[str]:
    problems = []
    if report_file.stat().st_mtime < curated_file.stat().st_mtime:
        problems.append(f"{report_file.name} is older than {curated_file.name}")
    if _params_core(report.get("params_default")) != _params_core(curated.get("params")):
        problems.append("the report was scored with different params than the live feed")
    if problems and not force:
        raise SystemExit("refusing to build: " + "; ".join(problems) +
                         ". Re-run  curate.py score --city <city> --params <live preset>  (or pass --force).")
    return problems


def check_gallery_freshness(vreport: dict, registry: dict, force: bool) -> list[str]:
    """The gallery report must be the ranking ``rank_venues.py apply`` wrote to the
    registry (every venue carries ``notability_breakdown.params_hash``)."""
    applied = {(v.get("notability_breakdown") or {}).get("params_hash") for v in registry.get("venues") or []}
    applied.discard(None)
    problems = []
    if not applied:
        problems.append("the registry has no applied gallery ranking")
    elif len(applied) > 1 or next(iter(applied)) != vreport.get("params_hash"):
        problems.append(f"venues_ranked.json was scored under params {vreport.get('params_hash')} but the registry "
                        f"has {', '.join(sorted(applied))} applied")
    if problems and not force:
        raise SystemExit("refusing to build: " + "; ".join(problems) +
                         ". Run  rank_venues.py apply --city <city>  (or pass --force).")
    return problems


def build_payload(city: str, report: dict, curated: dict, pool: dict[str, dict], thumbs: dict[str, str],
                  site_url: str, contact_email: str, galleries: dict | None = None) -> dict:
    live_params = {k: v for k, v in curated["params"].items() if not k.startswith("_")}
    live_params["city"] = city
    live_params["today"] = report.get("today") or curated.get("today")
    live_featured = {f["slug"]: f for f in curated.get("featured") or []}
    shows = [trim_show(r, live_params.get("judge") or {}, pool.get(r["slug"]), thumbs.get(r["slug"]), live_featured)
             for r in report.get("shows") or []]
    return {
        "city": city,
        "city_name": city_display_name(city),
        "today": report.get("today"),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "report_generated_at": report.get("generated_at"),
        "site_url": site_url,
        "contact_email": contact_email,
        "client_paths": CLIENT_PATHS,
        "params_default": live_params,
        "live": {"params_hash": curated.get("params_hash"), "applied_ts": curated.get("ts"),
                 "featured": [f["slug"] for f in sorted(curated.get("featured") or [], key=lambda f: f["featured_rank"])],
                 "editors_pick": [f["slug"] for f in curated.get("featured") or [] if f.get("editors_pick")]},
        "sources": [_pick(s, SOURCE_KEYS) for s in report.get("sources") or []],
        "shows": shows,
        "candidates": [],
        "seesaw": {"snapshot_id": None, "entries": [], "all_snapshots": []},
        "runs": [],
        "galleries": galleries or {},
        "default_mode": "galleries" if galleries and galleries.get("venues") else "shows",
    }


def make_thumbs(pool: dict[str, dict], out: Path, width: int) -> dict[str, str]:
    import images as image_pipe  # webdemo/images.py (sys.path above)
    thumbs: dict[str, str] = {}
    (out / "thumbs").mkdir(parents=True, exist_ok=True)
    for slug, s in pool.items():
        rels = s.get("images") or []
        if not rels:
            continue
        src = CONTENT_DIR / rels[0]
        if not src.is_file():
            continue
        rel = f"thumbs/{slug}.webp"
        image_pipe.process(src, out / rel, width, 70)
        thumbs[slug] = rel
    return thumbs


def gallery_core_js() -> str:
    """venue_core.js wrapped in an IIFE: its top-level names (R, CITY, FEATURES, $, esc,
    rank, computeFeatures, normalizeParams …) would otherwise collide with the shows
    core that shares the page's <script>. The core reads window.VENUES, so that global
    is set from the payload first (nothing else on the page reads it)."""
    core = venue_dashboard.CORE.read_text()
    exports = ", ".join(G_EXPORTS)
    return ("const G = (function () {\n"
            "window.VENUES = (window.CURATION || {}).galleries || {};\n"
            f"{core}\n"
            f"return {{ {exports} }};\n"
            "})();")


def render(payload: dict) -> str:
    page = curation_dashboard.inline_core(TEMPLATE.read_text())
    if GALLERY_CORE_MARKER not in page:
        raise SystemExit(f"marker {GALLERY_CORE_MARKER!r} missing from {TEMPLATE}")
    page = page.replace(GALLERY_CORE_MARKER, gallery_core_js(), 1)
    if curation_dashboard.MARKER not in page:
        raise SystemExit(f"marker {curation_dashboard.MARKER!r} missing from {TEMPLATE}")
    return page.replace(curation_dashboard.MARKER,
                        f"<script>window.CURATION = {curation_dashboard.embed_json(payload)};</script>", 1)


def gallery_report_file(city: str) -> Path:
    return CONTENT_DIR / "curation" / city / "venues_ranked.json"


def registry_file(city: str) -> Path:
    return CONTENT_DIR / "venues" / f"{city}.json"


def build(city: str, out: Path = DEFAULT_OUT, contact_email: str = DEFAULT_CONTACT, site_url: str = DEFAULT_SITE_URL,
          thumb_width: int = 320, force: bool = False, thumbs: bool = True,
          cities: list[dict] | None = None) -> Path:
    """One city's page into ``out`` (index.html + thumbs/). ``cities`` = the switcher menu
    ``[{key, name, url}]`` when the page is part of a multi-city site (build_all)."""
    report_file = CONTENT_DIR / "spend" / "reports" / f"curation-{city}.json"
    curated_file = CONTENT_DIR / "curation" / city / "curated.json"
    for f in (report_file, curated_file):
        if not f.exists():
            raise SystemExit(f"missing {f} — run curate.py score / apply first")
    vr_file, reg_file = gallery_report_file(city), registry_file(city)
    for f in (vr_file, reg_file):
        if not f.exists():
            raise SystemExit(f"missing {f} — run rank_venues.py apply --city {city} first")
    report = json.loads(report_file.read_text())
    curated = json.loads(curated_file.read_text())
    vreport = json.loads(vr_file.read_text())
    registry = json.loads(reg_file.read_text())
    problems = check_freshness(report_file, curated_file, report, curated, force)
    problems += check_gallery_freshness(vreport, registry, force)
    for p in problems:
        print(f"  warning (--force): {p}")
    pool = load_pool(city)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    thumb_map = make_thumbs(pool, out, thumb_width) if thumbs else {}
    payload = build_payload(city, report, curated, pool, thumb_map, site_url, contact_email,
                            galleries=build_galleries(city, vreport))
    payload["cities"] = cities or []
    (out / "index.html").write_text(render(payload), encoding="utf-8")
    if not cities:
        (out / "vercel.json").write_text(json.dumps({"trailingSlash": False}) + "\n")
    return out


def city_inputs_present(city: str) -> bool:
    return all(f.exists() for f in (CONTENT_DIR / "spend" / "reports" / f"curation-{city}.json",
                                    CONTENT_DIR / "curation" / city / "curated.json",
                                    gallery_report_file(city), registry_file(city)))


ROOT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Gallery Browser · rankings</title>
<style>body{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;background:#f4f4f1;color:#1b1b1b}
main{max-width:720px;margin:48px auto;padding:0 20px}h1{font-size:22px;margin:0 0 6px}p{color:#666;margin:0 0 20px}
ul{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
a{display:block;padding:14px 16px;background:#fff;border-radius:10px;text-decoration:none;color:inherit;box-shadow:0 1px 3px rgba(0,0,0,.08)}
a b{display:block}a span{color:#666;font-size:13px}</style></head><body><main>
<h1>Gallery Browser · rankings</h1><p>Gallery ranking and show ranking per city. Pick a city; every page has a city menu in its header.</p>
<ul>__LINKS__</ul></main>
<script>try{var k=localStorage.getItem('gb-curation-city');if(k&&!location.hash&&document.querySelector('a[data-key="'+k+'"]'))location.replace(k+'/');}catch(e){}</script>
</body></html>
"""


def build_all(cities: list[str], out: Path = DEFAULT_OUT, contact_email: str = DEFAULT_CONTACT,
              site_url: str = DEFAULT_SITE_URL, thumb_width: int = 320, force: bool = False,
              thumbs: bool = True) -> Path:
    """The whole site: ``out/<city>/`` per city plus a root page listing them. Cities
    whose inputs are missing (no curation report / curated.json / venues_ranked.json /
    registry) are skipped with a note."""
    base = site_url if site_url.endswith("/") else site_url + "/"
    ready = [c for c in cities if city_inputs_present(c)]
    for c in cities:
        if c not in ready:
            print(f"  note: {c} skipped — run curate.py score/apply and rank_venues.py apply first")
    menu = [{"key": c, "name": city_display_name(c), "url": f"{base}{c}/"} for c in ready]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for c in ready:
        build(c, out / c, contact_email, f"{base}{c}/", thumb_width, force, thumbs, cities=menu)
        print(f"  built {c}")
    links = "".join(f'<li><a href="{m["key"]}/" data-key="{m["key"]}"><b>{m["name"]}</b><span>gallery ranking · show ranking</span></a></li>'
                    for m in menu)
    (out / "index.html").write_text(ROOT_PAGE.replace("__LINKS__", links), encoding="utf-8")
    (out / "vercel.json").write_text(json.dumps({"trailingSlash": True}) + "\n")
    return out


def _decode(args: argparse.Namespace) -> int:
    parts = parse_fragment(args.link)
    if not parts.get("p") and not parts.get("g"):
        print("no changes in link" + (f" (mode {parts['v']})" if parts.get("v") else ""))
        return 0
    city = args.city or "los-angeles"
    today = datetime.now().date().isoformat()

    def emit(text: str, out: str | None, label: str) -> None:
        if out:
            Path(out).write_text(text + "\n")
            print(f"wrote {out}  ({label})")
        else:
            print(text)

    if parts.get("p"):
        diff = decode_token(parts["p"])
        curated = json.loads((CONTENT_DIR / "curation" / city / "curated.json").read_text())
        live = {k: v for k, v in curated["params"].items() if not k.startswith("_")}
        full = ordered_params(merge_params(live, {k: v for k, v in diff.items() if k != "city"}))
        full["_note"] = (f"Decoded from a curation-site share link on {today}; live show params "
                         f"{curated.get('params_hash')} + changes {json.dumps(diff, sort_keys=True)}. "
                         f"Apply with: curate.py apply --city {city} --params <this file> --reorder")
        emit(json.dumps(full, indent=1, ensure_ascii=False), args.out, "show params")
        print(f"shows: changes vs live: {json.dumps(diff, sort_keys=True)}", file=sys.stderr)
        print(f"apply shows:     scraper/curate.py apply --city {city} --params {args.out or '<file>'} --reorder")
    if parts.get("g"):
        gdiff = decode_token(parts["g"])
        vreport = json.loads(gallery_report_file(city).read_text())
        gfull = decode_gallery_link(parts["g"], build_galleries(city, vreport)["params_default"])
        gfull["_note"] = (f"Decoded from a curation-site share link on {today}; live gallery params "
                          f"{vreport.get('params_hash')} + changes {json.dumps(gdiff, sort_keys=True)}. "
                          f"Apply with: rank_venues.py apply --city {city} --params <this file>")
        emit(json.dumps(gfull, indent=1, ensure_ascii=False), args.out_galleries, "gallery params")
        print(f"galleries: changes vs live: {json.dumps(gdiff, sort_keys=True)}", file=sys.stderr)
        print(f"apply galleries: scraper/rank_venues.py apply --city {city} --params {args.out_galleries or '<file>'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="render the site into a deployable directory")
    b.add_argument("--city", default=None, help="one city (single-page site); see --all")
    b.add_argument("--all", action="store_true",
                   help="every city with inputs (or --cities): a page per city under <out>/<city>/ plus a root menu")
    b.add_argument("--cities", default=None, help="comma list for --all (default: scraper/cities.py order)")
    b.add_argument("--out", default=str(DEFAULT_OUT))
    b.add_argument("--contact-email", default=DEFAULT_CONTACT, help="recipient of the page's Email-link button")
    b.add_argument("--site-url", default=DEFAULT_SITE_URL, help="public URL used when composing share links")
    b.add_argument("--thumb-width", type=int, default=320)
    b.add_argument("--no-thumbs", action="store_true")
    b.add_argument("--force", action="store_true", help="build even if a report is stale vs what is live/applied")
    d = sub.add_parser("decode", help="turn a shared link into params JSON files (shows and/or galleries)")
    d.add_argument("link")
    d.add_argument("--city", default=None, help="city whose live params to merge onto (default: los-angeles)")
    d.add_argument("--out", help="write the show params JSON here instead of stdout")
    d.add_argument("--out-galleries", help="write the gallery params JSON here instead of stdout")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        if args.all or args.cities:
            from cities import CITIES
            cities = [c.strip() for c in args.cities.split(",")] if args.cities else list(CITIES)
            out = build_all(cities, Path(args.out), args.contact_email, args.site_url, args.thumb_width, args.force,
                            thumbs=not args.no_thumbs)
        elif args.city:
            out = build(args.city, Path(args.out), args.contact_email, args.site_url, args.thumb_width, args.force,
                        thumbs=not args.no_thumbs)
        else:
            raise SystemExit("pass --city C or --all")
        index = out / "index.html"
        n_thumbs = len(list(out.rglob("thumbs/*.webp")))
        total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
        print(f"wrote {index} ({index.stat().st_size:,} bytes; {n_thumbs} thumbs; {total / 1e6:.1f} MB total)")
        print(f"deploy: cd {out} && vercel deploy --prod --yes")
        return 0
    return _decode(args)


if __name__ == "__main__":
    sys.exit(main())
