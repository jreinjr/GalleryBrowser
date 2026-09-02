"""Curation scoring engine + CLI (plan 2.4, with the See Saw metrics of 2.7).

    python curate.py score      --city los-angeles [--params p.json] [--today YYYY-MM-DD]
                                [--snapshot latest|<id>|none] [--top 20] [--no-report]
    python curate.py match      --city los-angeles [--unmatched] [--force]
    python curate.py candidates --city los-angeles [--unresolved]
    python curate.py apply      --city los-angeles [--params p.json] [--reorder] [--dry-run]
    python curate.py wiki       --city los-angeles [--limit N] [--force]

Everything here is deterministic and free: no LLM calls. The formula below
is mirrored one-to-one in the dashboard's JS (``score_show`` /
``compute_features``), so keep it linear and keep every constant in params.

FEATURE FORMULA (raw evidence -> feature in [0, ~1.25]) — params keys in ()

    decay(age, hl)      = 0.5 ** (age / hl)
    sig_w(s)            = source_weight(s) * kind_weights[s.kind] * strength_weights[s.strength]
                          * decay(age(s), half_life_days.press)        # age from published_at, else ts
      source_weight(s)  = params.sources[id].weight if set (0 when enabled=false),
                          else sources.json weight, else default_source_weight
    press_raw           = sum over sources of min(cap_per_source, sum sig_w over kind in
                          {pick, review, news, listing, press_release_claim})
    press               = min(1.25, log2(1 + press_raw) / log2(1 + press_ref))
    artist_heat         = same shape over {artist_activity, award} with half_life_days.artist
                          and artist_ref
    venue               = venue_tier_map[registry tier] when the registry knows the tier, else
                          min(1, max(venue_tier_map.unknown,
                                     0.4 * n_distinct_fair_exhibitor_sources + 0.3 * is_museum))
    museum              = 1 if venue.is_museum else 0
    keyword             = min(1, sum of lexicon class weights over distinct classes hit in the
                          description + matched press snippets)             # curation_keywords
    judge               = overall / 10 for params.judge.{variant, model in sonnet|opus|mean|none};
                          0 + a "no verdict" reason when absent
    wiki_heat           = 0 if the artist has no Wikipedia article else
                          0.3 + 0.7 * min(1, log10(1 + pageviews_90d) / log10(1 + wiki_ref))
    opening_recency     = 1.0 while the show has not opened yet, else
                          decay(days_since_open, half_life_days.opening)
    closing_soon        = 1 if 0 <= days_to_close <= 14 else 0
    quality             = 0.5 * min(1, n_images / 5) + 0.5 * min(1, desc_words / 250)

    score               = sum over features f of weights[f] * feature[f]
    contrib[f]          = weights[f] * feature[f]                         # the per-show "why"

GATES, in order (a show stops at the first gate it fails; ``gate`` names it):
    overrides.exclude -> in-window (tools.show_in_window, skipped when include_pending)
    -> published_only (the pending pool never reaches the built site)
    -> require_open (on view TODAY; in-window also admits shows opening within
       tools.OPEN_WINDOW_DAYS, and a show with no start_date is not provably open)
    -> score >= threshold -> max_per_venue -> exclude_museums -> max_per_neighborhood
    -> max_museum_share (museum count <= floor(share * (max_n or pool size)))
    -> overrides.pin first -> max_n.
    Pinned shows skip threshold/venue/diversity gates (still must be in window).
    featured = survivors; editors_pick = the top ``editors_pick_top`` of them.

Report JSON (``content/spend/reports/curation-<city>.json``) top-level keys:
    city, generated_at, today, params_default, sources, venue_registry_present,
    shows[], candidates[], seesaw{}, runs[], judge_compare{}, commonality{}, wiki{}
    (``shows[].dates`` is ``{start, end}``.)
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_store as store  # noqa: E402
import tools  # noqa: E402
from curation_keywords import keyword_feature, merge_hits, scan_keywords  # noqa: E402

PRESS_KINDS = {"pick", "review", "news", "listing", "press_release_claim"}
ARTIST_KINDS = {"artist_activity", "award"}
FEATURES = ["press", "judge", "venue", "artist_heat", "museum", "keyword", "wiki_heat",
            "opening_recency", "closing_soon", "quality"]
CLOSING_SOON_DAYS = 14

DEFAULT_PARAMS: dict = {
    "version": 1,
    "city": None,
    "today": None,
    "max_n": 12,
    "threshold": 0.35,
    "max_per_venue": 1,
    "include_pending": False,
    "published_only": False,
    "require_open": False,
    "editors_pick_top": 5,
    "exclude_museums": False,
    "max_per_neighborhood": None,
    "max_museum_share": None,
    "weights": {
        "press": 0.35, "judge": 0.25, "venue": 0.15, "artist_heat": 0.10, "museum": 0.05,
        "keyword": 0.05, "wiki_heat": 0.05, "opening_recency": 0.03, "closing_soon": 0.02,
        "quality": 0,
    },
    "half_life_days": {"press": 21, "artist": 180, "opening": 30},
    "press_ref": 4,
    "artist_ref": 3,
    "cap_per_source": 1.5,
    "wiki_ref": 20000,
    "default_source_weight": 0.3,
    "kind_weights": {
        "pick": 1, "review": 0.9, "news": 0.6, "listing": 0.3, "press_release_claim": 0.3,
        "artist_activity": 1, "award": 1.2,
    },
    "strength_weights": {"headline": 1, "featured": 0.7, "mentioned": 0.4, "passing": 0.2},
    "sources": {},
    "venue_tier_map": {"1": 1, "2": 0.7, "3": 0.4, "4": 0.15, "unknown": 0.2},
    "judge": {"variant": "judge_v1", "model": "sonnet"},
    "overrides": {"pin": [], "exclude": []},
}

_DEEP_MERGE_KEYS = ("weights", "half_life_days", "kind_weights", "strength_weights",
                    "venue_tier_map", "judge", "overrides")


# --- params -------------------------------------------------------------------

def load_params(path: str | Path | None = None, city: str | None = None) -> dict:
    """Defaults <- content/curation/params/default.json <- ``path``; dict-valued
    keys are merged per key so a partial export still scores."""
    params = json.loads(json.dumps(DEFAULT_PARAMS))
    layers = [store.default_params_file()]
    if path:
        layers.append(Path(path))
    for p in layers:
        if p and p.exists():
            _merge_params(params, json.loads(p.read_text()))
    if city:
        params["city"] = city
        ov = store.load_overrides(city)
        for k in ("pin", "exclude"):
            merged = list(params["overrides"].get(k, [])) + [s for s in ov[k] if s not in params["overrides"].get(k, [])]
            params["overrides"][k] = merged
    return params


def _merge_params(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k in _DEEP_MERGE_KEYS and isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v


def params_hash(params: dict) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


def resolve_today(params: dict, override: str | None = None) -> date:
    s = override or params.get("today")
    return date.fromisoformat(s) if s else date.today()


# --- context (everything a score needs besides the show itself) ---------------

@dataclass
class Context:
    signals: dict[str, list[dict]] = field(default_factory=dict)   # slug -> signals (+ match)
    fair_sources: dict[str, set] = field(default_factory=dict)      # venue norm -> fair source ids
    judge: dict[str, dict] = field(default_factory=dict)            # slug -> variant -> model -> row
    wiki: dict[str, dict] = field(default_factory=dict)             # artist norm -> row
    registry_by_norm: dict[str, dict] = field(default_factory=dict)
    registry_by_id: dict[str, dict] = field(default_factory=dict)
    registry_present: bool = False
    sources: dict[str, dict] = field(default_factory=dict)          # source id -> sources.json row
    candidates: dict[str, dict] = field(default_factory=dict)       # key -> aggregated candidate
    all_signals: list[dict] = field(default_factory=list)           # collapsed signals (+ match)
    pool_index: store.PoolIndex | None = None


def empty_context() -> Context:
    return Context()


def build_context(city: str, pool: list[dict] | None = None) -> Context:
    ctx = Context()
    ctx.sources = store.sources_by_id(city)
    matches = store.load_matches(city)
    for sig in store.collapse_signals(store.load_signals(city)):
        m = matches.get(sig["id"]) or {}
        row = {**sig, "match": m}
        ctx.all_signals.append(row)
        if m.get("slug"):
            ctx.signals.setdefault(m["slug"], []).append(row)
        if sig.get("kind") == "fair_exhibitor":
            vn = m.get("venue_norm") or store.norm_venue(sig["show_ref"].get("venue"))
            ctx.fair_sources.setdefault(vn, set()).add(sig["source"]["id"])
    ctx.judge = store.load_judge(city)
    ctx.wiki = store.load_wiki(city)
    reg = store.load_registry_safe(city)
    ctx.registry_present = bool(reg)
    ctx.registry_by_id, ctx.registry_by_norm = store.registry_index(reg)
    ctx.candidates = store.load_candidates(city)
    ctx.pool_index = store.PoolIndex(pool) if pool is not None else None
    return ctx


# --- features -------------------------------------------------------------------

def decay(age_days: float, half_life: float) -> float:
    if half_life is None or half_life <= 0:
        return 1.0
    return 0.5 ** (max(0.0, float(age_days)) / float(half_life))


def signal_age_days(sig: dict, today: date) -> float:
    d = tools._parse_iso(sig.get("published_at"))
    if d is None:
        ts = sig.get("ts")
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date() if ts else today
    return (today - d).days


def source_weight(sig: dict, params: dict, ctx: Context) -> float:
    sid = (sig.get("source") or {}).get("id") or "unknown"
    p = (params.get("sources") or {}).get(sid)
    if p:
        if p.get("enabled") is False:
            return 0.0
        if p.get("weight") is not None:
            return float(p["weight"])
    src = ctx.sources.get(sid)
    if src is not None:
        if src.get("active") is False:
            return 0.0
        return float(src.get("weight", params.get("default_source_weight", 0.3)))
    return float(params.get("default_source_weight", 0.3))


def signal_weight(sig: dict, params: dict, ctx: Context, today: date, half_life: float) -> float:
    kw = float((params.get("kind_weights") or {}).get(sig.get("kind"), 0))
    sw = float((params.get("strength_weights") or {}).get(sig.get("strength"), 0))
    return source_weight(sig, params, ctx) * kw * sw * decay(signal_age_days(sig, today), half_life)


def log_saturate(raw: float, ref: float, cap: float = 1.25) -> float:
    if ref is None or ref <= 0:
        return 0.0
    return min(cap, math.log2(1 + max(0.0, raw)) / math.log2(1 + ref))


def aggregate_signals(signals: list[dict], kinds: set, params: dict, ctx: Context,
                      today: date, half_life: float, ref: float) -> tuple[float, float, dict]:
    """(feature, raw, per-source raw) for one signal family."""
    per_source: dict[str, float] = {}
    for s in signals:
        if s.get("kind") not in kinds:
            continue
        sid = (s.get("source") or {}).get("id") or "unknown"
        per_source[sid] = per_source.get(sid, 0.0) + signal_weight(s, params, ctx, today, half_life)
    cap = float(params.get("cap_per_source", 1.5))
    raw = sum(min(cap, v) for v in per_source.values())
    return log_saturate(raw, ref), raw, per_source


def venue_feature(show: dict, ctx: Context, params: dict) -> tuple[float, dict]:
    v = show.get("venue") or {}
    norm = store.norm_venue(v.get("name"))
    reg = None
    for k in store.venue_keys(v.get("name") or ""):
        reg = ctx.registry_by_norm.get(k)
        if reg:
            break
    if reg is None and show.get("venue_id"):
        reg = ctx.registry_by_id.get(show["venue_id"])
    tier = reg.get("tier") if reg else None
    tier_map = params.get("venue_tier_map") or {}
    n_fair = len(ctx.fair_sources.get(norm, ()))
    is_museum = bool(v.get("is_museum"))
    if tier is not None and str(tier) in tier_map:
        value = float(tier_map[str(tier)])
        basis = f"tier {tier}"
    else:
        fallback = 0.4 * n_fair + (0.3 if is_museum else 0.0)
        value = min(1.0, max(float(tier_map.get("unknown", 0.2)), fallback))
        basis = "no tier" + (f", {n_fair} fair listing(s)" if n_fair else "") + (", museum" if is_museum else "")
    return value, {"tier": tier, "n_fair": n_fair, "basis": basis,
                   "registry_id": reg.get("id") if reg else None, "venue_norm": norm}


def judge_feature(show: dict, ctx: Context, params: dict) -> tuple[float, dict]:
    cfg = params.get("judge") or {}
    variant, model = cfg.get("variant"), (cfg.get("model") or "none")
    if model == "none":
        return 0.0, {"model": "none", "rows": {}}
    rows = (ctx.judge.get(show["slug"], {}) or {}).get(variant, {}) or {}
    picked = {}
    for mid, row in rows.items():
        if model == "mean" or (model in ("sonnet", "opus") and model in mid.lower()):
            picked[mid] = row
    if not picked:
        return 0.0, {"model": model, "rows": {}, "missing": True}
    overall = statistics.fmean(float(r.get("overall", 0)) for r in picked.values())
    return min(1.0, overall / 10.0), {"model": model, "rows": {k: r.get("overall") for k, r in picked.items()}}


def wiki_feature(show: dict, ctx: Context, params: dict) -> tuple[float, dict]:
    artist = show.get("artist")
    if not artist:
        return 0.0, {"title": None}
    names = [artist] + store._split_names(artist)
    best, best_row = 0.0, None
    ref = float(params.get("wiki_ref", 20000))
    for n in names:
        row = ctx.wiki.get(store.norm_text(n))
        if not row or not row.get("title"):
            continue
        pv = float(row.get("pageviews_90d") or 0)
        val = 0.3 + 0.7 * min(1.0, math.log10(1 + pv) / math.log10(1 + ref)) if ref > 0 else 0.3
        if val > best:
            best, best_row = val, row
    return best, {"title": best_row.get("title") if best_row else None,
                  "pageviews_90d": best_row.get("pageviews_90d") if best_row else None,
                  "url": best_row.get("url") if best_row else None}


def compute_features(show: dict, ctx: Context, params: dict, today: date) -> tuple[dict, dict]:
    """(features, detail). ``features`` has exactly the FEATURES keys;
    ``detail`` carries the raw evidence behind them (signals, keyword hits,
    judge rows, venue basis, day counts) for reasons and the report."""
    hl = params.get("half_life_days") or {}
    signals = ctx.signals.get(show["slug"], [])
    press, press_raw, press_sources = aggregate_signals(
        signals, PRESS_KINDS, params, ctx, today, float(hl.get("press", 21)), float(params.get("press_ref", 4)))
    heat, heat_raw, heat_sources = aggregate_signals(
        signals, ARTIST_KINDS, params, ctx, today, float(hl.get("artist", 180)), float(params.get("artist_ref", 3)))
    venue, venue_detail = venue_feature(show, ctx, params)
    judge, judge_detail = judge_feature(show, ctx, params)
    wiki, wiki_detail = wiki_feature(show, ctx, params)

    desc = show.get("description") or ""
    snippets = " ".join(s.get("snippet") or "" for s in signals if s.get("kind") in PRESS_KINDS)
    hits = merge_hits(scan_keywords(desc, "description"), scan_keywords(snippets, "snippet"))

    start, end = tools._parse_iso(show.get("start_date")), tools._parse_iso(show.get("end_date"))
    days_since_open = (today - start).days if start else None
    days_to_close = (end - today).days if end else None
    if days_since_open is None:
        opening = 0.0
    elif days_since_open < 0:
        opening = 1.0
    else:
        opening = decay(days_since_open, float(hl.get("opening", 30)))
    closing = 1.0 if days_to_close is not None and 0 <= days_to_close <= CLOSING_SOON_DAYS else 0.0

    n_images = len(show.get("images") or [])
    desc_words = len(desc.split())
    quality = 0.5 * min(1.0, n_images / 5) + 0.5 * min(1.0, desc_words / 250)

    features = {
        "press": press, "judge": judge, "venue": venue, "artist_heat": heat,
        "museum": 1.0 if (show.get("venue") or {}).get("is_museum") else 0.0,
        "keyword": keyword_feature(hits), "wiki_heat": wiki,
        "opening_recency": opening, "closing_soon": closing, "quality": quality,
    }
    detail = {
        "signals": signals, "press_raw": press_raw, "press_sources": press_sources,
        "heat_raw": heat_raw, "heat_sources": heat_sources, "venue": venue_detail,
        "judge": judge_detail, "wiki": wiki_detail, "keyword_hits": hits,
        "days_since_open": days_since_open, "days_to_close": days_to_close,
        "n_images": n_images, "desc_words": desc_words,
    }
    return features, detail


def score_show(features: dict, params: dict) -> tuple[float, dict]:
    """score = sum(weights[f] * features[f]); contrib[f] = each term. Mirror
    this exactly in JS."""
    weights = params.get("weights") or {}
    contrib = {f: float(weights.get(f, 0)) * float(features.get(f, 0.0)) for f in FEATURES}
    return sum(contrib.values()), contrib


# --- ranking + gates ----------------------------------------------------------

def _reasons(row: dict, detail: dict, params: dict) -> list[str]:
    out = []
    f, c = row["features"], row["contrib"]
    top = sorted((k for k in FEATURES if c[k] > 0), key=lambda k: -c[k])[:4]
    for k in top:
        if k == "press":
            n = sum(1 for s in detail["signals"] if s.get("kind") in PRESS_KINDS)
            names = sorted({(s.get("source") or {}).get("id") or "?" for s in detail["signals"] if s.get("kind") in PRESS_KINDS})
            out.append(f"press {f['press']:.2f} from {n} signal(s): {', '.join(names[:4])}")
        elif k == "judge":
            out.append(f"judge {f['judge']:.2f} ({detail['judge'].get('model')}: {detail['judge'].get('rows')})")
        elif k == "venue":
            out.append(f"venue {f['venue']:.2f} ({detail['venue']['basis']})")
        elif k == "artist_heat":
            n = sum(1 for s in detail["signals"] if s.get("kind") in ARTIST_KINDS)
            out.append(f"artist heat {f['artist_heat']:.2f} from {n} signal(s)")
        elif k == "museum":
            out.append("museum")
        elif k == "keyword":
            out.append("keywords: " + ", ".join(h["label"] for h in detail["keyword_hits"]))
        elif k == "wiki_heat":
            out.append(f"wikipedia: {detail['wiki'].get('title')} ({detail['wiki'].get('pageviews_90d')} views/90d)")
        elif k == "opening_recency":
            d = detail["days_since_open"]
            out.append(f"opens in {-d} d" if d is not None and d < 0 else f"opened {d} d ago")
        elif k == "closing_soon":
            out.append(f"closes in {detail['days_to_close']} d")
        elif k == "quality":
            out.append(f"quality {f['quality']:.2f} ({detail['n_images']} images, {detail['desc_words']} words)")
    if detail["judge"].get("missing"):
        out.append("no judge verdict")
    if not detail["signals"]:
        out.append("no signals")
    if row.get("gate"):
        out.append(f"gated: {row['gate_detail'] or row['gate']}")
    elif row.get("pinned"):
        out.append("pinned")
    return out


def apply_gates(rows: list[dict], params: dict) -> list[dict]:
    """Walk the rank order applying the gate chain; sets ``gate``,
    ``gate_detail``, ``featured``, ``featured_rank``, ``editors_pick``.
    Returns the featured rows in feed order."""
    ov = params.get("overrides") or {}
    exclude = set(ov.get("exclude") or [])
    pins = list(ov.get("pin") or [])
    threshold = float(params.get("threshold") or 0)
    max_per_venue = params.get("max_per_venue")
    max_per_nb = params.get("max_per_neighborhood")
    share = params.get("max_museum_share")
    max_n = params.get("max_n")
    n_ref = max_n if max_n else len(rows)
    museum_allowed = math.floor(float(share) * n_ref) if share is not None else None

    seen_venue: Counter = Counter()
    seen_nb: Counter = Counter()
    museums = 0
    survivors: list[dict] = []
    for r in rows:
        r["gate"], r["gate_detail"] = None, None
        r["featured"], r["editors_pick"], r["featured_rank"] = False, False, None
        r["pinned"] = r["slug"] in pins
        is_museum = r["venue"]["is_museum"]
        nb = r["venue"].get("neighborhood") or ""
        vn = r["venue"]["norm"]
        if r["slug"] in exclude:
            r["gate"], r["gate_detail"] = "excluded", "overrides.exclude"
            continue
        if not r["in_window"] and not params.get("include_pending"):
            r["gate"], r["gate_detail"] = "out_of_window", "not in the publication window"
            continue
        if params.get("published_only") and r["pool"] != "published":
            r["gate"], r["gate_detail"] = "not_published", "in the pending pool; the built site reads the published file only"
            continue
        if params.get("require_open"):
            d = r.get("days_since_open")
            if d is None:
                r["gate"], r["gate_detail"] = "not_open", "no start date; cannot confirm it is open"
                continue
            if d < 0:
                r["gate"], r["gate_detail"] = "not_open", f"opens in {-d} d"
                continue
        if not r["pinned"]:
            if r["score"] < threshold:
                r["gate"], r["gate_detail"] = "below_threshold", f"score {r['score']:.3f} < threshold {threshold:.2f}"
                continue
            if max_per_venue and seen_venue[vn] >= max_per_venue:
                r["gate"], r["gate_detail"] = "max_per_venue", f"venue already has {max_per_venue}"
                continue
            if params.get("exclude_museums") and is_museum:
                r["gate"], r["gate_detail"] = "exclude_museums", "museums excluded"
                continue
            if max_per_nb and seen_nb[nb] >= max_per_nb:
                r["gate"], r["gate_detail"] = "max_per_neighborhood", f"{nb} already has {max_per_nb}"
                continue
            if museum_allowed is not None and is_museum and museums >= museum_allowed:
                r["gate"], r["gate_detail"] = "max_museum_share", f"museum share cap {share} of {n_ref} = {museum_allowed}"
                continue
        seen_venue[vn] += 1
        seen_nb[nb] += 1
        museums += 1 if is_museum else 0
        survivors.append(r)

    ordered = [r for r in survivors if r["pinned"]] + [r for r in survivors if not r["pinned"]]
    if max_n:
        for r in ordered[max_n:]:
            r["gate"], r["gate_detail"] = "max_n", f"beyond max_n {max_n}"
        ordered = ordered[:max_n]
    top_picks = int(params.get("editors_pick_top") or 0)
    for i, r in enumerate(ordered):
        r["featured"], r["featured_rank"] = True, i + 1
        r["editors_pick"] = i < top_picks
    return ordered


def _signal_view(s: dict) -> dict:
    m = s.get("match") or {}
    return {
        "id": s.get("id"), "dedupe_key": s.get("dedupe_key"),
        "source": s.get("source"), "kind": s.get("kind"), "strength": s.get("strength"),
        "date": s.get("published_at") or (datetime.fromtimestamp(s["ts"], tz=timezone.utc).date().isoformat() if s.get("ts") else None),
        "published_at": s.get("published_at"), "snippet": s.get("snippet"),
        "url": (s.get("source") or {}).get("url"), "run": s.get("run_id"),
        "variant": s.get("prompt_variant"), "model": s.get("model"),
        "match_confidence": m.get("confidence"), "match_method": m.get("method"),
        "n_rows": s.get("n_rows", 1), "show_ref": s.get("show_ref"),
    }


def rank(shows: list[dict], params: dict, today: date, ctx: Context | None = None,
         published_slugs: set | None = None) -> list[dict]:
    """Score every pool show, sort, gate. Returns report-shaped rows (plan
    2.4 ``shows[]``) in rank order."""
    ctx = ctx or empty_context()
    rows = []
    for show in shows:
        feats, detail = compute_features(show, ctx, params, today)
        score, contrib = score_show(feats, params)
        v = show.get("venue") or {}
        rows.append({
            "slug": show["slug"], "title": show.get("title"), "artist": show.get("artist"),
            "pool": ("published" if published_slugs is None or show["slug"] in published_slugs else "pending"),
            "in_window": tools.show_in_window(show, today),
            "dates": {"start": show.get("start_date"), "end": show.get("end_date")},
            "n_images": detail["n_images"], "desc_words": detail["desc_words"],
            "venue": {"name": v.get("name"), "id": show.get("venue_id") or detail["venue"]["registry_id"] or store.venue_id_for(v.get("name") or ""),
                      "tier": detail["venue"]["tier"], "n_fair": detail["venue"]["n_fair"], "is_museum": bool(v.get("is_museum")),
                      "neighborhood": v.get("neighborhood"), "norm": detail["venue"]["venue_norm"]},
            "signals": [_signal_view(s) for s in detail["signals"]],
            "judge": (ctx.judge.get(show["slug"]) or {}),
            "keyword_hits": detail["keyword_hits"],
            "wiki": detail["wiki"],
            "features": feats, "contrib": contrib, "score": score,
            "days_since_open": detail["days_since_open"], "days_to_close": detail["days_to_close"],
            "_detail": detail,
        })
    rows.sort(key=lambda r: (-r["score"], r["slug"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    apply_gates(rows, params)
    for r in rows:
        r["reasons"] = _reasons(r, r.pop("_detail"), params)
    return rows


# --- See Saw benchmark (plan 2.7) ---------------------------------------------

def _ndcg(top_slugs: list[str], rel: dict[str, float]) -> float | None:
    if not rel:
        return None
    dcg = sum(rel.get(s, 0.0) / math.log2(i + 2) for i, s in enumerate(top_slugs))
    ideal = sorted(rel.values(), reverse=True)[:max(1, len(top_slugs))]
    idcg = sum(v / math.log2(i + 2) for i, v in enumerate(ideal))
    return dcg / idcg if idcg else None


def seesaw_metrics(ranked: list[dict], snapshot: dict | None, matcher) -> dict:
    """Match every snapshot entry through ``matcher(ref) -> Match`` and
    compare with the featured list. Returns the report's ``seesaw`` block
    (minus ``all_snapshots``): ``{snapshot_id, entries[], metrics, misses}``."""
    if not snapshot:
        return {"snapshot_id": None, "entries": [], "metrics": None, "misses": None}
    by_slug = {r["slug"]: r for r in ranked}
    top = [r["slug"] for r in sorted((r for r in ranked if r["featured"]), key=lambda r: r["featured_rank"])]
    top_set = set(top)
    entries, tp, in_pool = [], 0, 0
    rel: dict[str, float] = {}
    n = len(snapshot.get("entries") or [])
    for e in snapshot.get("entries") or []:
        m = matcher({"venue": e.get("venue"), "artist": e.get("artist"), "title": e.get("title")})
        row = by_slug.get(m.slug) if m.slug else None
        match = {"slug": m.slug, "method": m.method, "confidence": m.confidence,
                 "venue_in_pool": m.venue_in_pool, "candidate_key": m.candidate_key,
                 "rank": row["rank"] if row else None, "score": row["score"] if row else None,
                 "featured": bool(row and row["featured"]), "gate": row["gate"] if row else None}
        if row:
            in_pool += 1
            rel[m.slug] = max(rel.get(m.slug, 0.0), float(n - int(e.get("position", n)) + 1))
            if m.slug in top_set:
                tp += 1
        entries.append({"position": e.get("position"), "venue": e.get("venue"),
                        "artist": e.get("artist"), "title": e.get("title"), "raw": e.get("raw"),
                        "match": match})
    n_top = len(top)
    metrics = {
        "n_snapshot": n, "n_top": n_top, "tp": tp,
        "precision": tp / n_top if n_top else None,
        "recall": tp / n if n else None,
        "jaccard": tp / (n_top + n - tp) if (n_top + n - tp) else None,
        "pool_coverage": in_pool / n if n else None,
        "ranking_recall": tp / in_pool if in_pool else None,
        "ndcg": _ndcg(top, rel),
    }
    misses = {
        "not_in_pool": [e for e in entries if e["match"]["slug"] is None],
        "in_pool_below_cutoff": [e for e in entries if e["match"]["slug"] and not e["match"]["featured"]],
        "our_picks_not_on_seesaw": [s for s in top if s not in rel],
    }
    return {"snapshot_id": snapshot.get("id"), "entries": entries, "metrics": metrics, "misses": misses}


def seesaw_commonality(ranked: list[dict], seesaw: dict | None) -> dict:
    """Feature means / shares for See Saw-matched pool shows vs the rest of
    the in-window pool, with a lift column (matched / rest). ``seesaw`` is
    the annotated block returned by ``seesaw_metrics``."""
    if not seesaw or not seesaw.get("entries"):
        return {"snapshot_id": None, "n_matched": 0, "n_rest": 0, "rows": []}
    matched = {e["match"]["slug"] for e in seesaw["entries"] if e["match"]["slug"]}
    pool = [r for r in ranked if r["in_window"]]
    a = [r for r in pool if r["slug"] in matched]
    b = [r for r in pool if r["slug"] not in matched]

    def mean(rows, fn):
        vals = [v for v in (fn(r) for r in rows) if v is not None]
        return statistics.fmean(vals) if vals else None

    def median(rows, fn):
        vals = [v for v in (fn(r) for r in rows) if v is not None]
        return statistics.median(vals) if vals else None

    def share(rows, fn):
        return (sum(1 for r in rows if fn(r)) / len(rows)) if rows else None

    specs = [
        ("museum share", "share", lambda r: r["venue"]["is_museum"], share),
        ("tier 1 share", "share", lambda r: r["venue"]["tier"] == 1, share),
        ("tier 2 share", "share", lambda r: r["venue"]["tier"] == 2, share),
        ("tier 3 share", "share", lambda r: r["venue"]["tier"] == 3, share),
        ("tier unknown share", "share", lambda r: r["venue"]["tier"] is None, share),
        ("has any signal", "share", lambda r: bool(r["signals"]), share),
        ("press signals per show", "mean", lambda r: sum(1 for s in r["signals"] if s["kind"] in PRESS_KINDS), mean),
        ("press feature", "mean", lambda r: r["features"]["press"], mean),
        ("artist heat feature", "mean", lambda r: r["features"]["artist_heat"], mean),
        ("wiki heat feature", "mean", lambda r: r["features"]["wiki_heat"], mean),
        ("has wikipedia article", "share", lambda r: bool(r["wiki"].get("title")), share),
        ("keyword feature", "mean", lambda r: r["features"]["keyword"], mean),
        ("judge feature", "mean", lambda r: r["features"]["judge"], mean),
        ("quality feature", "mean", lambda r: r["features"]["quality"], mean),
        ("days since opening", "median", lambda r: r["days_since_open"], median),
        ("days to close", "median", lambda r: r["days_to_close"], median),
        ("image count", "mean", lambda r: r["n_images"], mean),
        ("description words", "mean", lambda r: r["desc_words"], mean),
        ("score", "mean", lambda r: r["score"], mean),
    ]
    rows = []
    for name, kind, fn, agg in specs:
        va, vb = agg(a, fn), agg(b, fn)
        lift = (va / vb) if (va is not None and vb not in (None, 0)) else None
        rows.append({"name": name, "kind": kind, "seesaw": va, "rest": vb, "lift": lift})
    spread = {
        "name": "neighborhood spread (distinct / shows)", "kind": "ratio",
        "seesaw": (len({r["venue"]["neighborhood"] for r in a}) / len(a)) if a else None,
        "rest": (len({r["venue"]["neighborhood"] for r in b}) / len(b)) if b else None,
    }
    spread["lift"] = (spread["seesaw"] / spread["rest"]) if (spread["seesaw"] is not None and spread["rest"]) else None
    rows.append(spread)
    return {"snapshot_id": seesaw.get("snapshot_id"), "n_matched": len(a), "n_rest": len(b), "rows": rows}


# --- candidates ---------------------------------------------------------------

def candidate_rows(ctx: Context, params: dict, today: date, unresolved_only: bool = True) -> list[dict]:
    """Candidates (shows seen by signals but not in the pool), scored on
    press/artist only. Never counted in the ranking."""
    by_id = {s["id"]: s for s in ctx.all_signals}
    # signal ids in candidates.jsonl are run-scoped; rows collapsed by dedupe key may carry another id
    by_dkey: dict[str, dict] = {}
    for s in store.read_jsonl(store.signals_file(params.get("city") or "")) if params.get("city") else []:
        by_dkey.setdefault(s["dedupe_key"], []).append(s["id"])
    out = []
    hl = params.get("half_life_days") or {}
    for key, c in ctx.candidates.items():
        if unresolved_only and c.get("event") == "resolved":
            continue
        sigs = []
        for sid in c.get("signal_ids", []):
            s = by_id.get(sid)
            if s is None:
                # find the collapsed row through the dedupe key of this id
                for dk, ids in by_dkey.items():
                    if sid in ids:
                        s = next((x for x in ctx.all_signals if x.get("dedupe_key") == dk), None)
                        break
            if s and s not in sigs:
                sigs.append(s)
        press, press_raw, _ = aggregate_signals(sigs, PRESS_KINDS, params, ctx, today, float(hl.get("press", 21)), float(params.get("press_ref", 4)))
        heat, heat_raw, _ = aggregate_signals(sigs, ARTIST_KINDS, params, ctx, today, float(hl.get("artist", 180)), float(params.get("artist_ref", 3)))
        w = params.get("weights") or {}
        ref = c.get("show_ref") or {}
        urls = [(s.get("source") or {}).get("url") for s in sigs if (s.get("source") or {}).get("url")]
        out.append({
            "key": key, "venue": ref.get("venue"), "artist": ref.get("artist"), "title": ref.get("title"),
            "event": c.get("event"), "resolved_slug": c.get("resolved_slug"),
            "venue_in_pool": c.get("venue_in_pool"), "n_signals": len(sigs), "n_seen": c.get("n_seen"),
            "first_seen": c.get("first_seen"), "last_seen": c.get("last_seen"),
            "best_url": urls[-1] if urls else None,
            "features": {"press": press, "artist_heat": heat},
            "score": float(w.get("press", 0)) * press + float(w.get("artist_heat", 0)) * heat,
            "signals": [_signal_view(s) for s in sigs],
        })
    out.sort(key=lambda r: (-r["score"], -(r["n_signals"] or 0), r["key"]))
    return out


# --- report ------------------------------------------------------------------

def _pool(city: str) -> tuple[list[dict], set]:
    published = tools._load_shows_file(tools._city_file(city))["shows"]
    pending = tools._load_shows_file(tools._pending_file(city))["shows"]
    return published + pending, {s["slug"] for s in published}


def build_report(city: str, params: dict, today: date, snapshot_id: str | None = "latest") -> dict:
    shows, published = _pool(city)
    ctx = build_context(city, shows)
    ranked = rank(shows, params, today, ctx, published)
    snap = store.load_snapshot(city, snapshot_id)
    reg = store.load_registry_safe(city)
    matcher = lambda ref: store.match_show_ref(ref, ctx.pool_index, reg)  # noqa: E731
    seesaw = seesaw_metrics(ranked, snap, matcher)
    seesaw["all_snapshots"] = [p.stem for p in store.list_snapshots(city)]
    wiki_rows = []
    for r in ranked:
        if r["artist"]:
            for n in [r["artist"]] + store._split_names(r["artist"]):
                w = ctx.wiki.get(store.norm_text(n))
                if w and w not in wiki_rows:
                    wiki_rows.append(w)
    judge_compare = store.read_json(store.REPORTS_DIR / f"judge-compare-{city}.json", {})
    return {
        "city": city,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "today": today.isoformat(),
        "params_default": {**params, "city": city, "today": today.isoformat()},
        "sources": store.load_sources(city),
        "venue_registry_present": ctx.registry_present,
        "shows": ranked,
        "candidates": candidate_rows(ctx, {**params, "city": city}, today, unresolved_only=False),
        "seesaw": seesaw,
        "runs": store.load_runs(city),
        "judge_compare": judge_compare,
        "commonality": seesaw_commonality(ranked, seesaw),
        "wiki": {"n_artists": sum(1 for r in ranked if r["artist"]),
                 "n_with_article": sum(1 for r in ranked if r["wiki"].get("title")),
                 "rows": wiki_rows},
    }


def write_report(city: str, params: dict, today: date, snapshot_id: str | None = "latest") -> Path:
    report = build_report(city, params, today, snapshot_id)
    path = store.report_file(city)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return path


# --- wiki (Wikimedia REST) ----------------------------------------------------

WIKI_SEARCH_URL = "https://en.wikipedia.org/w/rest.php/v1/search/title"
WIKI_PAGEVIEWS_URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
                      "en.wikipedia/all-access/user/{title}/daily/{start}/{end}")
WIKI_CONTACT = os.environ.get("WIKI_CONTACT", "GalleryBrowser curation tool; set WIKI_CONTACT to your email")
WIKI_UA = f"GalleryBrowser-curation/1.0 ({WIKI_CONTACT}) python-requests/{requests.__version__}"
WIKI_FRESH_DAYS = 30
WIKI_ART_RE = re.compile(
    r"\b(artist|painter|sculptor|photographer|printmaker|ceramicist|ceramist|muralist|"
    r"illustrator|installation|video art|performance art|conceptual|visual art|collective|"
    r"designer|architect|filmmaker|animator|cartoonist|textile|fiber art|potter|weaver|"
    r"draughtsman|multimedia|multidisciplinary)\b", re.I)


def pick_wiki_page(artist: str, pages: list[dict]) -> dict | None:
    """Choose the search hit that is this artist: title equals the name (or
    'Name (artist)') AND the description/excerpt reads like an artist; else an
    exact-title stub with no description; else any art-related hit whose
    title contains the artist's last name."""
    an = store.norm_text(artist)
    if not an:
        return None
    last = an.split()[-1]

    def is_arty(p: dict) -> bool:
        blob = f"{p.get('description') or ''} {p.get('excerpt') or ''} {p.get('title') or ''}"
        return bool(WIKI_ART_RE.search(blob))

    def title_is_name(p: dict) -> bool:
        tn = store.norm_text(p.get("title"))
        return tn == an or tn.startswith(an + " ")

    for p in pages:
        if title_is_name(p) and is_arty(p):
            return p
    for p in pages:
        if store.norm_text(p.get("title")) == an and not (p.get("description") or "").strip():
            return p
    for p in pages:
        if len(last) >= 3 and last in store.norm_text(p.get("title")).split() and is_arty(p):
            return p
    return None


def wiki_lookup(artist: str, today: date, session: requests.Session | None = None) -> dict:
    """One artist -> wiki row. Never raises: network failures land in
    ``error`` with ``title: null`` (and are treated as stale by the cache)."""
    sess = session or requests.Session()
    headers = {"User-Agent": WIKI_UA, "Accept": "application/json"}
    row = {"ts": int(time.time()), "artist": artist, "norm": store.norm_text(artist),
           "title": None, "pageviews_90d": 0, "url": None, "description": None, "error": None}
    try:
        r = sess.get(WIKI_SEARCH_URL, params={"q": artist, "limit": 3}, headers=headers, timeout=15)
        r.raise_for_status()
        pages = r.json().get("pages", []) or []
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"search: {str(exc)[:160]}"
        return row
    row["considered"] = [p.get("title") for p in pages]
    page = pick_wiki_page(artist, pages)
    if not page:
        return row
    key = page.get("key") or (page.get("title") or "").replace(" ", "_")
    row["title"] = page.get("title")
    row["description"] = page.get("description")
    row["url"] = f"https://en.wikipedia.org/wiki/{key}"
    start = (today - timedelta(days=91)).strftime("%Y%m%d")
    end = (today - timedelta(days=1)).strftime("%Y%m%d")
    try:
        r = sess.get(WIKI_PAGEVIEWS_URL.format(title=quote(key, safe=""), start=start, end=end),
                     headers=headers, timeout=15)
        if r.status_code == 404:
            row["pageviews_90d"] = 0
        else:
            r.raise_for_status()
            row["pageviews_90d"] = int(sum(int(i.get("views", 0)) for i in r.json().get("items", [])))
    except Exception as exc:  # noqa: BLE001
        row["error"] = f"pageviews: {str(exc)[:160]}"
    return row


def pool_artists(shows: list[dict], max_names: int = 4) -> list[str]:
    """Distinct individual artist names in the pool (group shows with more
    than ``max_names`` names are skipped)."""
    seen, out = set(), []
    for s in shows:
        a = s.get("artist")
        if not a:
            continue
        parts = store._split_names(a) or [a]
        if len(parts) > max_names:
            continue
        for n in parts:
            k = store.norm_text(n)
            if k and k not in seen:
                seen.add(k)
                out.append(n.strip())
    return out


def cmd_wiki(args: argparse.Namespace) -> int:
    city = args.city
    today = date.today()
    shows, _ = _pool(city)
    cached = store.load_wiki(city)
    todo = []
    for name in pool_artists(shows):
        row = cached.get(store.norm_text(name))
        fresh = (row and not row.get("error")
                 and time.time() - row.get("ts", 0) < WIKI_FRESH_DAYS * 86400)
        if fresh and not args.force:
            continue
        todo.append(name)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} artist(s) to look up ({len(cached)} cached)")
    sess = requests.Session()
    for name in todo:
        row = wiki_lookup(name, today, sess)
        tools._append_jsonl(store.wiki_file(city), row)
        status = row["title"] or "-"
        err = f"  ERROR {row['error']}" if row.get("error") else ""
        print(f"  {name:<32} -> {status:<40} {row['pageviews_90d']:>8} views/90d{err}")
        time.sleep(0.25)
    return 0


# --- CLI commands -------------------------------------------------------------

def _fmt_table(rows: list[dict], top: int | None) -> str:
    lines = [f"{'#':>3} {'score':>6} {'F':>1} {'EP':>2} {'slug':<42} {'venue':<30} {'gate':<18} reasons"]
    for r in rows[: top or len(rows)]:
        lines.append(
            f"{r['rank']:>3} {r['score']:>6.3f} {'*' if r['featured'] else ' ':>1} "
            f"{'*' if r['editors_pick'] else ' ':>2} {r['slug'][:42]:<42} "
            f"{(r['venue']['name'] or '')[:30]:<30} {(r['gate'] or '-')[:18]:<18} "
            f"{'; '.join(r['reasons'])[:110]}")
    return "\n".join(lines)


def cmd_score(args: argparse.Namespace) -> int:
    city = args.city
    params = load_params(args.params, city)
    today = resolve_today(params, args.today)
    params["today"] = today.isoformat()
    t0 = time.time()
    report = build_report(city, params, today, args.snapshot)
    ranked = report["shows"]
    print(f"{city}: {len(ranked)} shows, today={today}, params={params_hash(params)} "
          f"({args.params or 'default'}), featured={sum(1 for r in ranked if r['featured'])}, "
          f"registry={'yes' if report['venue_registry_present'] else 'no'}")
    print(_fmt_table(ranked, args.top))
    ss = report["seesaw"]
    if ss.get("metrics"):
        m = ss["metrics"]
        print(f"\nSee Saw {ss['snapshot_id']}: n={m['n_snapshot']} top={m['n_top']} tp={m['tp']} "
              f"precision={_f(m['precision'])} recall={_f(m['recall'])} jaccard={_f(m['jaccard'])} "
              f"pool_coverage={_f(m['pool_coverage'])} ranking_recall={_f(m['ranking_recall'])} "
              f"ndcg={_f(m['ndcg'])}")
        for e in ss["misses"]["not_in_pool"]:
            print(f"  miss/not in pool: #{e['position']} {e['venue']} — {e['artist'] or e['title']}")
        for e in ss["misses"]["in_pool_below_cutoff"]:
            mm = e["match"]
            print(f"  miss/below cutoff: #{e['position']} {mm['slug']} rank {mm['rank']} score {_f(mm['score'])} gate {mm['gate']}")
        for s in ss["misses"]["our_picks_not_on_seesaw"]:
            print(f"  ours not on See Saw: {s}")
    elif args.snapshot not in (None, "none"):
        print("\n(no See Saw snapshot on disk)")
    if report["candidates"]:
        print(f"\n{len(report['candidates'])} candidate(s) (not in pool) — see `curate.py candidates`")
    if not args.no_report:
        run_row = store.append_run({
            "run_id": f"score-{city}-{int(t0)}", "city": city, "stage": "score",
            "prompt_variant": None, "prompt_hash": None, "model": None, "effort": None,
            "sources_planned": [], "sources_seen": [], "signals_recorded": 0, "candidates_new": 0,
            "duplicates_rejected": 0, "requests": 0, "web_searches": 0, "cost_usd": 0.0,
            "stop_reason": None, "duration_s": round(time.time() - t0, 2),
            "notes": f"n_shows={len(ranked)} featured={sum(1 for r in ranked if r['featured'])} "
                     f"snapshot={ss.get('snapshot_id')} params={params_hash(params)}",
            "params_hash": params_hash(params), "params_path": args.params,
        })
        report["runs"].append(run_row)   # the report lists its own run
        path = store.report_file(city)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(f"\nreport: {path} ({path.stat().st_size:,} bytes)")
    return 0


def _f(x) -> str:
    return "-" if x is None else f"{x:.3f}"


def cmd_match(args: argparse.Namespace) -> int:
    stats = store.rematch(args.city, unmatched_only=args.unmatched, force=args.force)
    print(json.dumps(stats))
    return 0


def cmd_candidates(args: argparse.Namespace) -> int:
    city = args.city
    params = load_params(None, city)
    today = resolve_today(params, None)
    shows, _ = _pool(city)
    ctx = build_context(city, shows)
    rows = candidate_rows(ctx, params, today, unresolved_only=args.unresolved)
    if not rows:
        print("no candidates")
        return 0
    print(f"{'score':>6} {'n':>2} {'pool?':<5} {'venue':<32} {'artist / title':<40} url")
    for r in rows:
        who = r["artist"] or r["title"] or "-"
        print(f"{r['score']:>6.3f} {r['n_signals']:>2} {'venue' if r['venue_in_pool'] else '-':<5} "
              f"{(r['venue'] or '')[:32]:<32} {who[:40]:<40} {r['best_url'] or ''}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    city = args.city
    params = load_params(args.params, city)
    today = resolve_today(params, args.today)
    params["today"] = today.isoformat()
    shows, published = _pool(city)
    ctx = build_context(city, shows)
    ranked = rank(shows, params, today, ctx, published)
    flags = {r["slug"]: (r["featured"], r["editors_pick"], r["featured_rank"]) for r in ranked}

    lock_path = tools.CONTENT_DIR / f".{city}.json.lock"
    changes: list[dict] = []
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        main = tools._load_shows_file(tools._city_file(city))
        pending = tools._load_shows_file(tools._pending_file(city))
        for pool_name, data in (("published", main), ("pending", pending)):
            for s in data["shows"]:
                feat, pick, _ = flags.get(s["slug"], (False, False, None))
                for field_, new in (("featured", feat), ("editors_pick", pick)):
                    old = bool(s.get(field_))
                    if old != new:
                        changes.append({"slug": s["slug"], "pool": pool_name, "field": field_,
                                        "old": old, "new": new})
                        s[field_] = new
        old_order = [s["slug"] for s in main["shows"]]
        new_order = old_order
        if args.reorder:
            feat_sorted = sorted((s for s in main["shows"] if flags.get(s["slug"], (False,))[0]),
                                 key=lambda s: flags[s["slug"]][2])
            rest = [s for s in main["shows"] if not flags.get(s["slug"], (False,))[0]]
            main["shows"] = feat_sorted + rest
            new_order = [s["slug"] for s in main["shows"]]
        if not args.dry_run:
            tools._write_shows_file(tools._city_file(city), main)
            tools._write_shows_file(tools._pending_file(city), pending)

    print(f"{'DRY RUN — ' if args.dry_run else ''}{len(changes)} flag change(s), "
          f"featured={sum(1 for r in ranked if r['featured'])}, "
          f"editors_pick={sum(1 for r in ranked if r['editors_pick'])}, params={params_hash(params)}")
    for c in changes:
        print(f"  {c['slug']:<44} {c['pool']:<9} {c['field']:<12} {c['old']} -> {c['new']}")
    if args.reorder and new_order != old_order:
        print("  published order changes:")
        for i, (a, b) in enumerate(zip(old_order, new_order)):
            if a != b:
                print(f"    [{i}] {a} -> {b}")
    elif args.reorder:
        print("  published order unchanged")
    if args.dry_run:
        return 0

    ts = int(time.time())
    store.curated_file(city).parent.mkdir(parents=True, exist_ok=True)
    store.curated_file(city).write_text(json.dumps({
        "ts": ts, "city": city, "today": today.isoformat(), "params_hash": params_hash(params),
        "params": params, "reorder": bool(args.reorder),
        "featured": [{"slug": r["slug"], "rank": r["rank"], "featured_rank": r["featured_rank"],
                      "score": r["score"], "editors_pick": r["editors_pick"]}
                     for r in sorted((r for r in ranked if r["featured"]), key=lambda r: r["featured_rank"])],
        # Every published show's rank/score, so the web app can sort non-featured
        # shows by ranking too (webdemo/build.py reads this).
        "ranked": [{"slug": r["slug"], "rank": r["rank"], "score": r["score"], "gate": r["gate"]}
                   for r in ranked if r["pool"] == "published"],
    }, indent=1, ensure_ascii=False))
    store.append_row(store.applied_file(city), {
        "ts": ts, "city": city, "params_hash": params_hash(params), "params_path": args.params,
        "reorder": bool(args.reorder), "dry_run": False, "changes": changes,
        "n_featured": sum(1 for r in ranked if r["featured"]),
        "n_editors_pick": sum(1 for r in ranked if r["editors_pick"]),
    })
    store.append_run({
        "run_id": f"apply-{city}-{ts}", "city": city, "stage": "apply",
        "prompt_variant": None, "prompt_hash": None, "model": None, "effort": None,
        "sources_planned": [], "sources_seen": [], "signals_recorded": 0, "candidates_new": 0,
        "duplicates_rejected": 0, "requests": 0, "web_searches": 0, "cost_usd": 0.0,
        "stop_reason": None, "duration_s": 0,
        "notes": f"changes={len(changes)} reorder={bool(args.reorder)} params={params_hash(params)}",
        "params_hash": params_hash(params),
    })
    print(f"wrote {store.curated_file(city)}; appended applied.jsonl")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("score", help="rank the pool, print the table, write the report JSON")
    p.add_argument("--city", required=True)
    p.add_argument("--params", help="params JSON (dashboard export); layered over params/default.json")
    p.add_argument("--today", help="YYYY-MM-DD (default: params.today or the real date)")
    p.add_argument("--snapshot", default="latest", help="See Saw snapshot id, 'latest' (default) or 'none'")
    p.add_argument("--top", type=int, default=None, help="rows to print (default: all)")
    p.add_argument("--no-report", action="store_true", help="print only; do not write the report/run row")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("match", help="re-run the matcher over stored signals and candidates")
    p.add_argument("--city", required=True)
    p.add_argument("--unmatched", action="store_true", help="only signals whose latest match has no slug")
    p.add_argument("--force", action="store_true", help="append a match row even when unchanged")
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("candidates", help="list shows seen by signals but missing from the pool")
    p.add_argument("--city", required=True)
    p.add_argument("--unresolved", action="store_true", help="hide candidates that were later scraped")
    p.set_defaults(fn=cmd_candidates)

    p = sub.add_parser("apply", help="write featured/editors_pick into the city files")
    p.add_argument("--city", required=True)
    p.add_argument("--params")
    p.add_argument("--today")
    p.add_argument("--reorder", action="store_true", help="also reorder published shows: featured by rank, then the rest")
    p.add_argument("--dry-run", action="store_true", help="print the diff; write nothing")
    p.set_defaults(fn=cmd_apply)

    p = sub.add_parser("wiki", help="look up pooled artists on Wikipedia (search + 90-day pageviews)")
    p.add_argument("--city", required=True)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--force", action="store_true", help="ignore the 30-day cache")
    p.set_defaults(fn=cmd_wiki)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
