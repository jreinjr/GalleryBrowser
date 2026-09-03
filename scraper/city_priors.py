"""Stage G0 of the galleries-first pipeline: per-city prestige priors.

    python city_priors.py propose    --city tokyo [--apply] [--model claude-sonnet-5]
    python city_priors.py sweep      --city tokyo --kind fairs|lists [--budget 1.5] [--dry-run]
                                     [--sources a,b] [--include-swept]
    python city_priors.py candidates --city tokyo [--apply]

propose     one offline Sonnet call (json_schema output, no web tools) proposes the
            major art fairs a gallery in this city would exhibit at, the curated lists /
            dealers' associations / editorial gallery directories for the market, and —
            for a city with no sources.json yet — the publications a later pubsweep should
            read. --apply merges them into content/curation/<city>/sources.json as entries of
            kind "fair" (shape of the existing LA fair entries), "curated_list" (new; carries
            `weight`, `entry_urls`, `list_kind`, optional `parser`) and publication kinds.
            Existing ids are never duplicated or overwritten: hand edits win.
sweep       fairs: the existing fairs_v1 signal sessions (fair_exhibitor signals).
            lists: server-rendered lists with a known `parser` (adaa, nada) are parsed with
            the stdlib and written straight into the signal store for $0; every other active
            curated_list source goes to a lists_v1 agent session (list_member signals).
            Sources that already produced signals are skipped unless --include-swept.
candidates  venue names seen by fair_exhibitor / list_member signals and by
            candidates.jsonl (press mentions of shows at venues we do not track) that resolve
            to no registry venue. --apply creates them as status "candidate" with
            sources.seed.priors = [source ids]; existing venues are only tagged.

Costs: propose ~$0.03-0.06; a lists/fairs session ~$0.7-2 (budget-capped); parsers $0.
"""

from __future__ import annotations

import argparse
import html as htmllib
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_prompts as cp  # noqa: E402
import curation_store as store  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

PRIORS_MODEL = "claude-sonnet-5"
MAX_TOKENS = 8000
PRIOR_KINDS = ("fair_exhibitor", "list_member")

# LA-county / Tokyo-ward vocab used by the stdlib parsers to decide "has a location here".
CITY_PLACE_WORDS = {
    "los-angeles": ["los angeles", "beverly hills", "santa monica", "west hollywood", "culver city",
                    "pasadena", "venice, ca", "glendale", "hollywood", "long beach", "inglewood"],
    "tokyo": ["tokyo", "東京"],
}


# --- propose -------------------------------------------------------------------

PROPOSE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["fairs", "lists", "publications"],
    "properties": {
        "fairs": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "name", "url", "exhibitor_list_url", "prestige", "region",
                         "query_hints", "notes"],
            "properties": {
                "id": {"type": "string", "description": "kebab-case, e.g. art-basel-hk-exhibitors"},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "exhibitor_list_url": {"type": ["string", "null"],
                                       "description": "best guess for the exhibitor list page"},
                "prestige": {"type": "integer", "description": "3 = top-tier global fair, 2 = major regional, 1 = local / emerging"},
                "region": {"type": "string", "description": "local | regional | global"},
                "query_hints": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": ["string", "null"]},
            }}},
        "lists": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "name", "url", "entry_urls", "list_kind", "weight", "query_hints", "notes"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "entry_urls": {"type": "array", "items": {"type": "string"},
                               "description": "pages that list the members / galleries"},
                "list_kind": {"type": "string", "description": "association | editorial | directory"},
                "weight": {"type": "number", "description": "0-1: how selective / prestigious membership is (association of vetted dealers ~0.9, editorial venue list ~0.7, open directory ~0.2)"},
                "query_hints": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": ["string", "null"]},
            }}},
        "publications": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "name", "url", "kind", "weight", "query_hints", "notes"],
            "properties": {
                "id": {"type": "string"},
                "name": {"type": "string"},
                "url": {"type": "string"},
                "kind": {"type": "string", "description": "publication_picks | review_outlet | news | listing | culture"},
                "weight": {"type": "number"},
                "query_hints": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": ["string", "null"]},
            }}},
    },
}

PROPOSE_SYSTEM = """You are helping build a notability index of contemporary art galleries in {city_name}. Today is {today}.
Propose, from your own knowledge (no web access):
1. fairs: the art fairs where a serious {city_name} gallery would exhibit — the city's own fairs, the region's major fairs, and the global fairs that admit galleries from this market (e.g. Art Basel editions, Frieze editions, regional fairs). 6-12 entries. Give the most likely exhibitor-list URL when you know the site's structure, else null.
2. lists: dealers' associations, vetted membership bodies and curated / editorial gallery lists or directories relevant to this market (e.g. for Los Angeles: ADAA, NADA, Contemporary Art Daily, Gallery Platform LA, Carla's venue list; for Tokyo: CADAN — Contemporary Art Dealers Association Nippon, the Art Dealers Association of Japan / 全国美術商連合会, Tokyo Art Beat's venue list, ART iT, Contemporary Art Daily, Ocula, Artsy's gallery pages, the Frieze and Art Basel gallery directories). 5-12 entries; weight = how selective membership is.
3. publications: {pub_instruction}
Use kebab-case ids that would not collide with these existing ids: {existing_ids}. Prefer official URLs. Be concrete and conservative: only real organisations and sites."""


def _client():
    import anthropic
    from run_scrape import load_env
    load_env()
    return anthropic.Anthropic(max_retries=3)


def propose(city: str, model: str = PRIORS_MODEL) -> tuple[dict, dict]:
    """(proposal, usage/cost) from one structured Messages call."""
    from harness import MODELS
    cfg = CITIES[city]
    existing = [s["id"] for s in store.load_sources(city)]
    has_pubs = any(s.get("kind") in ("publication_picks", "review_outlet", "news", "listing", "culture")
                   for s in store.load_sources(city))
    pub_instruction = ("this city already has a publication registry; return an empty array."
                       if has_pubs else
                       "the 6-10 publications / picks columns / listings sites whose critics and "
                       "editors recommend or review exhibitions in this city (local art press first, "
                       "then international outlets with a local column). kind: publication_picks | "
                       "review_outlet | news | listing | culture.")
    system = PROPOSE_SYSTEM.format(city_name=cfg["display_name"], today=date.today().isoformat(),
                                   existing_ids=", ".join(existing) or "(none)",
                                   pub_instruction=pub_instruction)
    client = _client()
    req = {
        "model": model, "max_tokens": MAX_TOKENS, "system": system,
        "messages": [{"role": "user", "content": f"City: {cfg['display_name']} ({city})."}],
        "output_config": {"format": {"type": "json_schema", "schema": PROPOSE_SCHEMA}},
    }
    if MODELS.get(model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = "medium"
    resp = client.messages.create(**req)
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = json.loads(text)
    u = resp.usage
    prices = MODELS[model]
    cost = (u.input_tokens / 1e6 * prices["in"] + u.output_tokens / 1e6 * prices["out"]
            + (getattr(u, "cache_read_input_tokens", 0) or 0) / 1e6 * prices["cache_read"]
            + (getattr(u, "cache_creation_input_tokens", 0) or 0) / 1e6 * prices["cache_write"])
    usage = {"model": model, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cost_usd": round(cost, 4)}
    _save_spend(f"priors-{city}-{int(time.time())}", usage)
    return data, usage


def _save_spend(label: str, usage: dict) -> None:
    p = tools.CONTENT_DIR / "spend" / f"{label}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"session": label, **usage}, indent=1))


def _domain(url: str | None) -> str | None:
    return venues.registrable_domain(url) if url else None


def _slug_id(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s or "source"


def entries_from_proposal(city: str, data: dict, existing: list[dict],
                          today: str | None = None) -> list[dict]:
    """sources.json entries for every proposed fair/list/publication that is not
    already registered: same id, or same domain under the same kind (the LA file
    already has frieze-la-exhibitors; a proposed "frieze-los-angeles" on
    frieze.com is the same fair). Pure."""
    today = today or date.today().isoformat()
    existing_ids = {s["id"] for s in existing if s.get("id")}
    existing_dom_kind = {(_domain(u), s.get("kind")) for s in existing
                         for u in (s.get("urls") or []) + (s.get("entry_urls") or []) if _domain(u)}
    out: list[dict] = []
    seen = set(existing_ids)

    def keep(sid: str, urls: list[str], kind: str) -> bool:
        if sid in seen:
            return False
        if any((_domain(u), kind) in existing_dom_kind for u in urls):
            return False
        seen.add(sid)
        existing_dom_kind.update((_domain(u), kind) for u in urls if _domain(u))
        return True

    for f in data.get("fairs", []):
        sid = _slug_id(f["id"])
        urls = [u for u in [f.get("exhibitor_list_url"), f.get("url")] if u]
        if not keep(sid, urls, "fair"):
            continue
        out.append({
            "id": sid, "name": f["name"], "kind": "fair", "urls": urls,
            "domains": sorted({d for d in map(_domain, urls) if d}),
            "weight": 0, "cadence": "annual", "active": True, "added": today,
            "query_hints": list(f.get("query_hints") or [])[:4],
            "prestige": int(f.get("prestige") or 1), "region": f.get("region"),
            "notes": (f.get("notes") or "Venue-tier signal (fair_exhibitor); no show weight.")
                     + " [proposed by city_priors]",
        })
    for l in data.get("lists", []):
        sid = _slug_id(l["id"])
        urls = [l["url"]] + [u for u in (l.get("entry_urls") or []) if u and u != l["url"]]
        if not keep(sid, urls, "curated_list"):
            continue
        out.append({
            "id": sid, "name": l["name"], "kind": "curated_list", "urls": urls[:1],
            "entry_urls": urls, "domains": sorted({d for d in map(_domain, urls) if d}),
            "weight": max(0.0, min(1.0, float(l.get("weight") or 0.5))),
            "list_kind": l.get("list_kind") or "editorial",
            "parser": KNOWN_PARSERS.get(_domain(l["url"]) or ""),
            "cadence": "annual", "active": True, "added": today,
            "query_hints": list(l.get("query_hints") or [])[:4],
            "notes": (l.get("notes") or "Venue-tier signal (list_member).") + " [proposed by city_priors]",
        })
    for p in data.get("publications", []):
        sid = _slug_id(p["id"])
        kind0 = p.get("kind") if p.get("kind") in ("publication_picks", "review_outlet", "news",
                                                   "listing", "culture") else "publication_picks"
        if not keep(sid, [p["url"]], kind0):
            continue
        kind = p.get("kind") if p.get("kind") in ("publication_picks", "review_outlet", "news",
                                                  "listing", "culture") else "publication_picks"
        out.append({
            "id": sid, "name": p["name"], "kind": kind, "urls": [p["url"]],
            "domains": [d for d in [_domain(p["url"])] if d],
            "weight": max(0.0, min(1.5, float(p.get("weight") or 0.7))),
            "cadence": "weekly", "active": True, "added": today,
            "query_hints": list(p.get("query_hints") or [])[:4],
            "notes": (p.get("notes") or "") + " [proposed by city_priors]",
        })
    return out


def merge_sources(city: str, entries: list[dict]) -> tuple[int, int]:
    """Append entries whose id is new; returns (added, skipped)."""
    f = store.sources_file(city)
    data = store.read_json(f, None)
    if not isinstance(data, dict):
        data = {"schema": 1, "city": city,
                "notes": ("Publication + prior registry for curation signals. weight = per-signal "
                          "multiplier in the press features (0 = venue-tier only: fairs, lists, "
                          "institutions). curated_list.weight = how selective membership is. "
                          "Hand-edit freely; signals keep their own provenance."),
                "sources": []}
    have = {s["id"] for s in data["sources"] if s.get("id")}
    added = 0
    for e in entries:
        if e["id"] in have:
            continue
        data["sources"].append(e)
        have.add(e["id"])
        added += 1
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    return added, len(entries) - added


# --- stdlib parsers for server-rendered lists ($0) -----------------------------------

KNOWN_PARSERS = {"artdealers.org": "adaa", "newartdealers.org": "nada"}

_TAG_RE = re.compile(r"<[^>]+>")


def _text(s: str) -> str:
    return htmllib.unescape(_TAG_RE.sub(" ", s)).replace("\xa0", " ")


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def parse_adaa(html: str, city: str) -> list[dict]:
    """artdealers.org/member-galleries: <li class="SearchResultContainer"> blocks with
    a GalleryResultTitle h5 and a GRL address block."""
    words = CITY_PLACE_WORDS.get(city, [])
    out = []
    for block in re.split(r'<li class="SearchResultContainer', html)[1:]:
        m = re.search(r'GalleryResultTitle[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>\s*<h5[^>]*>(.*?)</h5>', block, re.S)
        if not m:
            continue
        name = _clean(_text(m.group(2)))
        addr_m = re.search(r'class="GRL">(.*?)</div>', block, re.S)
        addr = _clean(_text(addr_m.group(1))) if addr_m else ""
        site_m = re.search(r'href="(https?://(?!artdealers\.org)[^"]+)"', block)
        low = addr.lower()
        if name and any(w in low for w in words):
            out.append({"name": name, "location": addr, "website": site_m.group(1) if site_m else None,
                        "page": "https://artdealers.org/member-galleries"})
    return out


def parse_nada(html: str, city: str) -> list[dict]:
    """newartdealers.org/members: <li><a href=site>Name, City</a></li>."""
    words = CITY_PLACE_WORDS.get(city, [])
    out = []
    for href, body in re.findall(r'<li>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        label = _clean(_text(body))
        if "," not in label:
            continue
        # "Anat Ebgi, Los Angeles & New York" / "Long Story Short, Los Angeles, New York":
        # the name is everything before the first comma, the tail is the city list.
        name, _, tail = label.partition(",")
        if any(w in tail.lower() for w in words):
            out.append({"name": _clean(name), "location": _clean(tail),
                        "website": href if href.startswith("http") else None,
                        "page": "https://www.newartdealers.org/members"})
    return out


PARSERS = {"adaa": parse_adaa, "nada": parse_nada}


def fetch_html(url: str) -> str | None:
    import refresh
    r = refresh.Fetcher().get(url)
    if r.get("status") == 200 and r.get("html"):
        return r["html"]
    return None


def parse_list_source(src: dict, city: str, html_by_url: dict[str, str] | None = None) -> list[dict]:
    fn = PARSERS.get(src.get("parser") or "")
    if not fn:
        return []
    rows: list[dict] = []
    for url in src.get("entry_urls") or src.get("urls") or []:
        html = (html_by_url or {}).get(url) if html_by_url is not None else fetch_html(url)
        if not html:
            continue
        for r in fn(html, city):
            r["page"] = url
            rows.append(r)
    # dedupe by name
    seen, out = set(), []
    for r in rows:
        k = store.norm_venue(r["name"])
        if k and k not in seen:
            seen.add(k); out.append(r)
    return out


def record_members(city: str, src: dict, rows: list[dict], run_id: str) -> int:
    n = 0
    year = date.today().year
    for r in rows:
        snippet = f"Member of {src['name']} ({year}); {r.get('location') or ''}"
        if r.get("website"):
            snippet += f"; site: {r['website']}"
        res = json.loads(store.record_signal({
            "city": city, "source_id": src["id"], "source_url": r["page"], "published_at": None,
            "kind": "list_member", "strength": "featured", "venue": r["name"], "artist": None,
            "title": None, "snippet": snippet[:store.SNIPPET_MAX], "agent_pool_slug": None,
        }, city, run_id, variant="lists_parser", model=None))
        n += 1 if res.get("recorded") else 0
    return n


# --- sweep ----------------------------------------------------------------------

def swept_source_ids(city: str) -> set[str]:
    return {r["source"]["id"] for r in store.load_signals(city)
            if r.get("kind") in PRIOR_KINDS and r.get("source", {}).get("id")}


def sweep(city: str, kind: str, budget: float | None, dry_run: bool, only: set[str] | None,
          include_swept: bool, model: str, effort: str) -> dict:
    from harness import run_city
    from run_scrape import load_env
    load_env()
    want_kind = "fair" if kind == "fairs" else "curated_list"
    variant = cp.VARIANTS["fairs_v1" if kind == "fairs" else "lists_v1"]
    srcs = [s for s in store.load_sources(city) if s.get("kind") == want_kind and s.get("active", True)]
    if only:
        srcs = [s for s in srcs if s["id"] in only]
    if not include_swept:
        done = swept_source_ids(city)
        srcs = [s for s in srcs if s["id"] not in done]
    summary = {"parsed": {}, "sessions": [], "cost_usd": 0.0}
    llm_srcs = []
    for s in srcs:
        if s.get("parser") in PARSERS and kind == "lists":
            if dry_run:
                summary["parsed"][s["id"]] = "would parse"
                continue
            run_id = f"priors-{city}-{s['id']}-{int(time.time())}"
            rows = parse_list_source(s, city)
            n = record_members(city, s, rows, run_id) if rows else 0
            summary["parsed"][s["id"]] = n
            print(f"[parser {s.get('parser')}] {s['id']}: {len(rows)} entries, {n} signals", flush=True)
            if not rows:
                llm_srcs.append(s)   # parser found nothing: let the agent try
        else:
            llm_srcs.append(s)
    lim = dict(variant.limits)
    groups = [llm_srcs[i:i + lim["group_size"]] for i in range(0, len(llm_srcs), lim["group_size"])]
    cfg = CITIES[city]
    for gi, group in enumerate(groups, 1):
        ctx = {"items": group}
        label = f"curate-{city}-{variant.name}-g{gi}-{int(time.time())}"
        names = [s["id"] for s in group]
        if dry_run:
            system, first = cp.render(variant.name, city, cfg, ctx)
            print(f"\n=== {label}: {names}\n{first[:1200]}")
            summary["sessions"].append({"label": label, "sources": names, "dry_run": True})
            continue
        print(f"\n[{gi}/{len(groups)}] {label}: {names}", flush=True)
        t0 = time.time()
        res = run_city(city, target_shows=0, max_searches=lim["max_searches"],
                       max_fetches=lim["max_fetches"], max_iterations=lim["max_iterations"],
                       budget_usd=budget or lim["budget_usd"], model=model,
                       fetch_content_tokens=lim["fetch_tokens"], effort=effort,
                       session_label=label, signal_variant=variant.name, signal_ctx=ctx,
                       search_domains=variant.search_domains(ctx))
        sig = [r for r in store.load_signals(city) if r.get("run_id") == label]
        row = {"ts": int(t0), "run_id": label, "city": city, "stage": "signals",
               "prompt_variant": variant.name, "prompt_hash": variant.prompt_hash(),
               "model": model, "effort": effort, "sources_planned": names,
               "sources_seen": sorted({r["source"]["id"] for r in sig if r.get("source")}),
               "slugs_planned": [], "signals_recorded": len(sig), "candidates_new": None,
               "duplicates_rejected": None, "requests": res.get("requests"),
               "web_searches": res.get("web_searches"), "cost_usd": res.get("cost_usd"),
               "stop_reason": res.get("stop_reason"), "duration_s": int(time.time() - t0),
               "notes": (res.get("final_message") or "")[:300]}
        store.append_run(row)
        summary["cost_usd"] += res.get("cost_usd") or 0.0
        summary["sessions"].append({"label": label, "sources": names, "signals": len(sig),
                                    "cost_usd": res.get("cost_usd")})
        print(f"   -> {len(sig)} signals, ${res.get('cost_usd', 0):.2f}, stop={res.get('stop_reason')}",
              flush=True)
    return summary


# --- candidates -----------------------------------------------------------------

def _website_from_snippet(snippet: str | None) -> str | None:
    m = re.search(r"site:\s*(\S+)", snippet or "")
    if not m:
        return None
    u = m.group(1).rstrip(".,;)")
    return u if "://" in u else "https://" + u


def resolve_registry_venue(name: str, reg: dict, by_norm: dict[str, dict],
                           website: str | None = None) -> dict | None:
    """Registry venue for a name seen in a signal: exact/alias/parenthetical key,
    then venues.find_venue (id + domain), then the matcher's distinctive-words
    rule against every registry name."""
    for k in store.venue_keys(name):
        if k in by_norm:
            return by_norm[k]
    v = venues.find_venue(reg, name, website, add_alias=False)
    if v:
        return v
    words = _sig_words(name)
    if words:
        best = None
        for k, cand in by_norm.items():
            kw = _sig_words(k)
            if kw == words:
                return cand
            # "Taka Ishii Gallery" vs "Taka Ishii Gallery Roppongi", "Perrotin" vs
            # "Perrotin Tokyo", "Nanzuka" vs "NANZUKA UNDERGROUND": the signal's
            # distinctive words all appear in the registry name with at most one
            # extra word (a branch / district label). Prefer the shortest hit.
            if words <= kw and len(kw) - len(words) <= 1 and (best is None or len(k) < len(best[0])):
                best = (k, cand)
        if best:
            return best[1]
    return None


# Branch / district labels that a registry name may carry beyond the gallery's name.
BRANCH_WORDS = {"tokyo", "roppongi", "kyobashi", "ginza", "piramide", "tennoz", "tennozu",
                "shibuya", "omotesando", "annex", "kiyosumi", "shirakawa", "ebisu", "meguro",
                "nihonbashi", "bakurocho", "shinjuku", "kagurazaka", "yanaka", "ueno", "underground"}


def _sig_words(name_or_norm: str) -> set[str]:
    norm = store.norm_venue(name_or_norm)
    # "ShugoArts" -> also try the camel-case split so it matches "Shugo Arts"
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name_or_norm)
    words = store._distinctive_words(norm) | store._distinctive_words(store.norm_venue(spaced))
    return {w for w in words if w not in BRANCH_WORDS}


def unresolved_candidates(city: str, reg: dict | None = None,
                          signals: list[dict] | None = None,
                          candidate_events: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """(new, tagged): `new` = names with no registry venue, one row per norm with
    the source ids and best website; `tagged` = registry venues that prior signals
    name (so sources.seed.priors can be recorded)."""
    reg = reg if reg is not None else venues.load_registry(city)
    _, by_norm = store.registry_index(reg)
    signals = signals if signals is not None else store.collapse_signals(store.load_signals(city))
    events = candidate_events if candidate_events is not None else store.load_candidate_events(city)
    seen: dict[str, dict] = {}
    tagged: dict[str, dict] = {}

    def add(name: str, source_id: str, website: str | None, kind: str):
        norm = store.norm_venue(name)
        if not norm:
            return
        v = resolve_registry_venue(name, reg, by_norm, website)
        if v:
            t = tagged.setdefault(v["id"], {"venue": v, "sources": set()})
            if kind in PRIOR_KINDS:
                t["sources"].add(source_id)
            return
        row = seen.setdefault(norm, {"name": name, "norm": norm, "sources": set(),
                                     "website": None, "kinds": set(), "n": 0})
        row["n"] += 1
        row["sources"].add(source_id)
        row["kinds"].add(kind)
        if website and not row["website"]:
            row["website"] = website

    for s in signals:
        if s.get("kind") not in PRIOR_KINDS:
            continue
        add(s["show_ref"]["venue"], s["source"]["id"], _website_from_snippet(s.get("snippet")), s["kind"])
    prior_sig_ids = {s.get("id") for s in signals if s.get("kind") in PRIOR_KINDS}
    for e in events:
        if e.get("event") != "seen" or e.get("venue_in_pool"):
            continue
        if e.get("signal_id") in prior_sig_ids:
            continue   # a venue-level signal's own candidate row, already counted above
        ref = e.get("show_ref") or {}
        if ref.get("venue"):
            add(ref["venue"], f"press:{e.get('run_id') or '?'}", None, "press")
    new = sorted(seen.values(), key=lambda r: (-len(r["sources"]), -r["n"], r["norm"]))
    for r in new:
        r["sources"] = sorted(r["sources"]); r["kinds"] = sorted(r["kinds"])
    return new, [{"id": k, "sources": sorted(t["sources"])} for k, t in tagged.items() if t["sources"]]


def apply_candidates(city: str, new: list[dict], tagged: list[dict],
                     prior_only: bool = True) -> tuple[int, int]:
    """Create candidate venues (prior_only: skip press-only names — those stay in
    candidates.jsonl for the S3 cleanup pass) and tag existing ones."""
    created = 0
    now = int(time.time())
    with venues.locked_registry(city) as reg:
        by_id = venues.index_by_id(reg)
        for t in tagged:
            v = by_id.get(t["id"])
            if not v:
                continue
            seed = v.setdefault("sources", {}).setdefault("seed", {})
            cur = seed.get("priors") or {"ts": now, "lists": []}
            cur["lists"] = sorted(set(cur.get("lists") or []) | set(t["sources"]))
            seed["priors"] = cur
        for r in new:
            prior_srcs = [s for s in r["sources"] if not s.startswith("press:")]
            if prior_only and not prior_srcs:
                continue
            patch = {"kind": "gallery", "status": "candidate", "website": r.get("website"),
                     "sources": {"seed": {"priors": {"ts": now, "lists": prior_srcs}},
                                 "candidate": {"ts": now, "why": "city_priors",
                                               "sources": r["sources"]}},
                     "notes": f"candidate from priors: {', '.join(r['sources'])}"}
            v, was_new = venues._upsert_in(reg, r["name"], patch, "city_priors", r.get("website"),
                                            protect_existing=("status", "kind", "notes"))
            created += 1 if was_new else 0
    return created, len(tagged)


# --- CLI ------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--city", required=True, choices=sorted(CITIES))
    p.add_argument("--apply", action="store_true")
    p.add_argument("--model", default=PRIORS_MODEL)
    s = sub.add_parser("sweep")
    s.add_argument("--city", required=True, choices=sorted(CITIES))
    s.add_argument("--kind", required=True, choices=["fairs", "lists"])
    s.add_argument("--budget", type=float, default=None)
    s.add_argument("--sources", default=None, help="comma-separated source ids")
    s.add_argument("--include-swept", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--model", default=PRIORS_MODEL)
    s.add_argument("--effort", default="medium")
    c = sub.add_parser("candidates")
    c.add_argument("--city", required=True, choices=sorted(CITIES))
    c.add_argument("--apply", action="store_true")
    c.add_argument("--include-press", action="store_true",
                   help="also create venues seen only by press signals (default: priors only)")
    args = ap.parse_args(argv)

    if args.cmd == "propose":
        existing = store.load_sources(args.city)
        data, usage = propose(args.city, args.model)
        entries = entries_from_proposal(args.city, data, existing)
        for e in entries:
            extra = (f" prestige={e.get('prestige')}" if e["kind"] == "fair"
                     else f" weight={e.get('weight')} parser={e.get('parser')}" if e["kind"] == "curated_list"
                     else "")
            print(f"  {e['kind']:<17} {e['id']:<34} {e['name']}{extra}\n{'':21}{e['urls'][0]}")
        print(f"proposed {len(data.get('fairs', []))} fairs, {len(data.get('lists', []))} lists, "
              f"{len(data.get('publications', []))} publications -> {len(entries)} new entries; "
              f"${usage['cost_usd']:.3f}")
        if args.apply:
            added, skipped = merge_sources(args.city, entries)
            print(f"sources.json: +{added} (skipped {skipped} existing)")
        return 0
    if args.cmd == "sweep":
        only = {x.strip() for x in args.sources.split(",")} if args.sources else None
        summ = sweep(args.city, args.kind, args.budget, args.dry_run, only, args.include_swept,
                     args.model, args.effort)
        print(json.dumps(summ, indent=1))
        return 0
    if args.cmd == "candidates":
        new, tagged = unresolved_candidates(args.city)
        prior_new = [r for r in new if any(not s.startswith("press:") for s in r["sources"])]
        press_new = [r for r in new if r not in prior_new]
        print(f"{args.city}: {len(tagged)} registry venues named by prior signals; "
              f"{len(prior_new)} unresolved from priors; {len(press_new)} press-only (candidates.jsonl)")
        for r in prior_new:
            print(f"  + {r['name']:<40} {', '.join(r['sources'])}  {r.get('website') or ''}")
        for r in press_new[:40]:
            print(f"  ~ {r['name']:<40} press x{r['n']}")
        if args.apply:
            created, ntag = apply_candidates(args.city, new, tagged, prior_only=not args.include_press)
            print(f"registry: {created} candidate venue(s) created, {ntag} tagged with sources.seed.priors")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
