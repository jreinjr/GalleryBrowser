"""Build the client-facing curation site (plan: Part C).

    python curation_site.py build  --city los-angeles [--out DIR] [--contact-email E]
                                   [--site-url URL] [--thumb-width 320] [--force]
    python curation_site.py decode '<shared link or #p= token>' [--out params.json]

``build`` reads the score report (``content/spend/reports/curation-<city>.json``,
written by ``curate.py score``), the live feed record
(``content/curation/<city>/curated.json`` — its ``params`` block is what actually
ships, so it is the page's default), ``sources.json`` and the pool files
(published + pending) for gallery links and a thumbnail per show. It writes a
self-contained ``index.html`` (data inlined, ``noindex``), ``thumbs/`` and a
``vercel.json`` into ``webdemo/dist/gallery-browser-curation/``.

Deploy:  cd webdemo/dist/gallery-browser-curation && vercel deploy --prod --yes

The page shares settings as ``<site>/#p=<base64url JSON diff vs live>``;
``decode`` turns such a link back into a full params file that ``curate.py
apply --params`` accepts. Refuses to build when the report is older than
``curated.json`` or was scored with different params (re-run
``curate.py score --params <live preset>``), unless ``--force``.
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
                   "strength_weights", "sources", "venue_tier_map", "judge", "overrides"]

SIGNAL_KEYS = ("kind", "strength", "date", "snippet", "url", "n_rows", "match_confidence", "match_method")
SOURCE_KEYS = ("id", "name", "kind", "weight", "active")
JUDGE_KEYS = ("model", "overall", "confidence", "scores", "rationale")
WIKI_KEYS = ("title", "url", "pageviews_90d")
SHOW_KEYS = ("slug", "title", "artist", "pool", "in_window", "dates", "days_since_open", "days_to_close",
             "n_images", "desc_words", "features", "contrib", "score", "rank", "reasons", "keyword_hits")
VENUE_KEYS = ("name", "id", "neighborhood", "is_museum", "tier", "n_fair", "norm")


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


def token_from_link(link: str) -> str:
    """Accept a full URL (``…/#p=TOKEN``), a bare ``#p=TOKEN`` / ``p=TOKEN`` or the token."""
    s = link.strip()
    frag = urlparse(s).fragment if "://" in s else s.lstrip("#")
    frag = unquote(frag)
    if frag.startswith("p="):
        frag = frag[2:]
    if not frag:
        raise ValueError("no #p= token in link")
    return frag.split("&", 1)[0]


def merge_params(base: dict, override: dict) -> dict:
    """curate._merge_params semantics: dict-valued deep keys merged per key."""
    for k, v in (override or {}).items():
        if k in DEEP_MERGE_KEYS and isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


def ordered_params(p: dict) -> dict:
    out = {k: p[k] for k in PARAM_KEY_ORDER if k in p}
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
    """Shared link -> full params (live params with the shared diff merged)."""
    full = merge_params(json.loads(json.dumps(live_params)), decode_token(token_from_link(link)))
    full.pop("_note", None)
    return ordered_params(full)


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


def build_payload(city: str, report: dict, curated: dict, pool: dict[str, dict], thumbs: dict[str, str],
                  site_url: str, contact_email: str) -> dict:
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


def render(payload: dict) -> str:
    page = curation_dashboard.inline_core(TEMPLATE.read_text())
    if curation_dashboard.MARKER not in page:
        raise SystemExit(f"marker {curation_dashboard.MARKER!r} missing from {TEMPLATE}")
    return page.replace(curation_dashboard.MARKER,
                        f"<script>window.CURATION = {curation_dashboard.embed_json(payload)};</script>", 1)


def build(city: str, out: Path = DEFAULT_OUT, contact_email: str = DEFAULT_CONTACT, site_url: str = DEFAULT_SITE_URL,
          thumb_width: int = 320, force: bool = False, thumbs: bool = True) -> Path:
    report_file = CONTENT_DIR / "spend" / "reports" / f"curation-{city}.json"
    curated_file = CONTENT_DIR / "curation" / city / "curated.json"
    for f in (report_file, curated_file):
        if not f.exists():
            raise SystemExit(f"missing {f} — run curate.py score / apply first")
    report = json.loads(report_file.read_text())
    curated = json.loads(curated_file.read_text())
    problems = check_freshness(report_file, curated_file, report, curated, force)
    for p in problems:
        print(f"  warning (--force): {p}")
    pool = load_pool(city)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    thumb_map = make_thumbs(pool, out, thumb_width) if thumbs else {}
    payload = build_payload(city, report, curated, pool, thumb_map, site_url, contact_email)
    (out / "index.html").write_text(render(payload), encoding="utf-8")
    (out / "vercel.json").write_text(json.dumps({"trailingSlash": False}) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="render the site into a deployable directory")
    b.add_argument("--city", required=True)
    b.add_argument("--out", default=str(DEFAULT_OUT))
    b.add_argument("--contact-email", default=DEFAULT_CONTACT, help="recipient of the page's Email-link button")
    b.add_argument("--site-url", default=DEFAULT_SITE_URL, help="public URL used when composing share links")
    b.add_argument("--thumb-width", type=int, default=320)
    b.add_argument("--no-thumbs", action="store_true")
    b.add_argument("--force", action="store_true", help="build even if the report is stale vs curated.json")
    d = sub.add_parser("decode", help="turn a shared link into a full params JSON")
    d.add_argument("link")
    d.add_argument("--city", default=None, help="city whose live params to merge onto (default: inferred from the link's city field, else los-angeles)")
    d.add_argument("--out", help="write the params JSON here instead of stdout")
    args = ap.parse_args(argv)

    if args.cmd == "build":
        out = build(args.city, Path(args.out), args.contact_email, args.site_url, args.thumb_width, args.force,
                    thumbs=not args.no_thumbs)
        index = out / "index.html"
        n_thumbs = len(list((out / "thumbs").glob("*.webp"))) if (out / "thumbs").exists() else 0
        total = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
        print(f"wrote {index} ({index.stat().st_size:,} bytes; {n_thumbs} thumbs; {total / 1e6:.1f} MB total)")
        print(f"deploy: cd {out} && vercel deploy --prod --yes")
        return 0

    diff = decode_token(token_from_link(args.link))
    city = args.city or diff.get("city") or "los-angeles"
    curated = json.loads((CONTENT_DIR / "curation" / city / "curated.json").read_text())
    live = {k: v for k, v in curated["params"].items() if not k.startswith("_")}
    full = ordered_params(merge_params(live, {k: v for k, v in diff.items() if k != "city"}))
    full["_note"] = (f"Decoded from a curation-site share link on {datetime.now().date().isoformat()}; "
                     f"live params {curated.get('params_hash')} + changes {json.dumps(diff, sort_keys=True)}. "
                     f"Apply with: curate.py apply --city {city} --params <this file> --reorder")
    text = json.dumps(full, indent=1, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    print(f"changes vs live: {json.dumps(diff, sort_keys=True)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
