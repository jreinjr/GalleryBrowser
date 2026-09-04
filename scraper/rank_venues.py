"""Gallery ranking engine + CLI (galleries-first plan, stage G4).

    python rank_venues.py score --city los-angeles [--params p.json] [--today YYYY-MM-DD] [--top 25]
    python rank_venues.py apply --city los-angeles [--params p.json] [--dry-run]

Deterministic and free: no LLM or network calls. Every feature is computed
from evidence already on disk (registry, signal store, cached Places sweep,
venue_judge.jsonl, venue_wiki.jsonl) and stored on the registry under
``features`` so re-ranking never re-fetches. The formula is mirrored one-to-one
in ``dashboard/venue_core.js`` (``computeFeatures`` / ``scoreVenue`` /
``applyGates`` / ``assignTiers``); keep it linear and keep every constant in params.

FEATURE FORMULA (raw evidence -> feature in [0, 1]) — params keys in ()

    sat(n, ref)          = min(1, log2(1 + n) / log2(1 + ref))
    hours_breadth        = hours.breadth_value(parse_hours(google hours | site hours))
    fairs                = sat(sum over distinct fairs of list_weights[fair] (default 1), refs.fairs)
    curated_lists        = sat(same over list_member sources, refs.lists)
    press                = sat(distinct outlets with pick|review|news at the venue, refs.press)
    directory            = sat(distinct directory seeds (gpla, carla, ... not places), refs.directory)
    longevity            = sat(years since founded_year | first_exhibition_year, refs.longevity_years)
    roster_size          = sat(facts.roster_count, refs.roster)
    roster_strength      = 0 until the artists dataset exists
    show_cadence         = sat(facts.exhibitions_per_year, refs.shows_per_year)
    multi_location       = min(1, n_locations_elsewhere / refs.locations)
    places_popularity    = min(1, log10(1 + user_ratings_total) / log10(1 + refs.ratings))
    web_presence         = 0.35 * has exhibitions_url + 0.35 * page has date strings
                           + 0.30 * has about text
    venue_judge          = overall / 10 for judge.{variant, model}; 0 when absent
    wiki                 = 0 if no article else 0.5 + 0.5 * sat(sitelinks, refs.wiki_sitelinks)
    kind_gallery|kind_nonprofit|kind_museum = one-hot over KIND_GROUP
    seesaw_presence      = 1 / 0.5 / 0 only when params.leak_seesaw (benchmark leakage)

    score                = sum over features of weights[f] * feature[f]

GATES (``gate`` names the first one a venue fails): status in gates.exclude_status
-> gates.require_verified (verification.status == "verified") -> gates.kinds
-> gates.min_score. Tiers: score >= tiers["1"] -> 1, >= tiers["2"] -> 2,
>= tiers["3"] -> 3, else null (ungated venues only).

MANUAL ORDER: ``params.manual_order = {"1": [venue_id, ...], "2": [...], "3": [...]}``
(hand-ordered on the curation site) freezes the ranked set: the listed venues take
ranks 1..n in exactly that order with the tier of the list they sit in (gates and
scores no longer decide membership or order); every other venue is scored and gated
as usual but gets tier null (below the hand-set list) and ranks after it. ``null``
(the default) = automatic ranking.

Report JSON: content/curation/<city>/venues_ranked.json (docs/GALLERIES.md).
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_store as store  # noqa: E402
import hours  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

HERE = Path(__file__).resolve().parent
CONTENT_DIR = tools.CONTENT_DIR
PARAMS_DIR = CONTENT_DIR / "curation" / "params"

FEATURES = ["hours_breadth", "fairs", "curated_lists", "press", "directory", "longevity",
            "roster_size", "roster_strength", "show_cadence", "multi_location",
            "places_popularity", "web_presence", "venue_judge", "wiki",
            "kind_gallery", "kind_nonprofit", "kind_museum", "seesaw_presence"]
PRESS_KINDS = {"pick", "review", "news"}
KIND_GROUP = {"gallery": "gallery", "nonprofit": "nonprofit", "project_space": "nonprofit",
              "university": "nonprofit", "museum": "museum", "other": None}
DIRECTORY_SEEDS_IGNORED = {"places", "llm"}

DEFAULT_PARAMS: dict = {
    "version": 1,
    "city": None,
    "today": None,
    "weights": {
        "hours_breadth": 0.10, "fairs": 0.18, "curated_lists": 0.12, "press": 0.10,
        "directory": 0.05, "longevity": 0.08, "roster_size": 0.06, "roster_strength": 0.0,
        "show_cadence": 0.06, "multi_location": 0.04, "places_popularity": 0.05,
        "web_presence": 0.04, "venue_judge": 0.12, "wiki": 0.05,
        "kind_gallery": 0.0, "kind_nonprofit": 0.0, "kind_museum": -0.15,
        "seesaw_presence": 0.0,
    },
    "refs": {"fairs": 3, "lists": 2, "press": 3, "directory": 2, "longevity_years": 20,
             "roster": 20, "shows_per_year": 6, "locations": 2, "ratings": 200,
             "wiki_sitelinks": 20},
    "list_weights": {},            # source id -> multiplier for fairs / curated lists
    "gates": {"require_verified": False, "kinds": [], "min_score": 0.0,
              "exclude_status": ["duplicate", "out_of_scope", "closed"]},
    "tiers": {"1": 0.55, "2": 0.30, "3": 0.10},
    "judge": {"variant": "venue_judge_v1", "model": "sonnet"},
    "leak_seesaw": False,
    "manual_order": None,          # {"1": [ids], "2": [ids], "3": [ids]} from the curation site, or None
}
_DEEP_MERGE_KEYS = ("weights", "refs", "list_weights", "gates", "tiers", "judge")
JUDGE_MODELS = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5"}


# --- params -------------------------------------------------------------------

def default_params_file() -> Path:
    return PARAMS_DIR / "venues-default.json"


def _merge_params(base: dict, override: dict) -> None:
    for k, v in override.items():
        if k.startswith("_"):
            continue
        if k in _DEEP_MERGE_KEYS and isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v


def load_params(path: str | Path | None = None, city: str | None = None) -> dict:
    """Defaults <- content/curation/params/venues-default.json <- ``path``."""
    params = json.loads(json.dumps(DEFAULT_PARAMS))
    for p in (default_params_file(), Path(path) if path else None):
        if p and p.exists():
            _merge_params(params, json.loads(p.read_text()))
    if city:
        params["city"] = city
    for f in FEATURES:
        params["weights"].setdefault(f, 0.0)
    return params


def params_hash(params: dict) -> str:
    return hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]


def resolve_today(params: dict, override: str | None = None) -> date:
    s = override or params.get("today")
    return date.fromisoformat(s) if s else date.today()


# --- context ------------------------------------------------------------------

def sat(n: float, ref: float) -> float:
    if ref is None or ref <= 0 or n is None or n <= 0:
        return 0.0
    return min(1.0, math.log2(1 + float(n)) / math.log2(1 + float(ref)))


def venue_norms(v: dict) -> set[str]:
    keys: set[str] = set()
    for name in [v.get("name")] + list(v.get("aliases") or []):
        keys |= store.venue_keys(name or "")
    return keys


def _places_files(city: str) -> list[Path]:
    return [Path(p) for p in sorted(glob.glob(str(HERE / ".cache" / "places" / city / "*.json")))]


def load_places_ratings(city: str) -> tuple[dict[str, dict], dict[str, dict]]:
    """(by_place_id, by_norm) of {name, rating, user_ratings_total} from the
    cached Places sweep. LA's legacy nearby field mask carried no ratings;
    Tokyo's does. Missing files / shapes are tolerated."""
    by_pid: dict[str, dict] = {}
    by_norm: dict[str, dict] = {}
    for f in _places_files(city):
        try:
            d = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(d, dict) or "query" not in d:
            continue
        data = d.get("data")
        results: list[dict] = []
        if isinstance(data, list):
            for p in data:
                if isinstance(p, dict):
                    results.extend(p.get("results") or [])
        elif isinstance(data, dict):
            results.extend(data.get("results") or [])
            if isinstance(data.get("result"), dict):
                results.append(data["result"])
        for r in results:
            if not isinstance(r, dict) or r.get("user_ratings_total") is None:
                continue
            row = {"name": r.get("name"), "rating": r.get("rating"),
                   "user_ratings_total": int(r.get("user_ratings_total") or 0)}
            pid = r.get("place_id")
            if pid and (pid not in by_pid or row["user_ratings_total"] > by_pid[pid]["user_ratings_total"]):
                by_pid[pid] = row
            for k in store.venue_keys(r.get("name") or ""):
                cur = by_norm.get(k)
                if cur is None or row["user_ratings_total"] > cur["user_ratings_total"]:
                    by_norm[k] = row
    return by_pid, by_norm


def load_venue_judge(city: str) -> dict[str, dict[str, dict[str, dict]]]:
    """venue_id -> variant -> model -> latest row (content/curation/<city>/venue_judge.jsonl)."""
    out: dict[str, dict[str, dict[str, dict]]] = {}
    for r in store.read_jsonl(store.city_dir(city) / "venue_judge.jsonl"):
        vid, variant, model = r.get("venue_id"), r.get("variant"), r.get("model")
        if vid and variant and model:
            out.setdefault(vid, {}).setdefault(variant, {})[model] = r
    return out


def load_venue_wiki(city: str) -> dict[str, dict]:
    """venue_id -> latest row (content/curation/<city>/venue_wiki.jsonl)."""
    out: dict[str, dict] = {}
    for r in store.read_jsonl(store.city_dir(city) / "venue_wiki.jsonl"):
        if r.get("venue_id"):
            out[r["venue_id"]] = r
    return out


class Context:
    def __init__(self, city: str):
        self.city = city
        self.registry = venues.load_registry(city)
        self.sources = store.sources_by_id(city)
        self.fairs: dict[str, set[str]] = {}       # venue norm -> fair source ids
        self.lists: dict[str, set[str]] = {}       # venue norm -> curated-list source ids
        self.press: dict[str, set[str]] = {}       # venue norm -> outlet source ids
        matches = store.load_matches(city)
        for s in store.collapse_signals(store.load_signals(city)):
            m = matches.get(s["id"]) or {}
            norm = m.get("venue_norm") or store.norm_venue((s.get("show_ref") or {}).get("venue"))
            if not norm:
                continue
            sid = (s.get("source") or {}).get("id") or "unknown"
            kind = s.get("kind")
            if kind == "fair_exhibitor":
                self.fairs.setdefault(norm, set()).add(sid)
            elif kind == "list_member":
                self.lists.setdefault(norm, set()).add(sid)
            elif kind in PRESS_KINDS:
                self.press.setdefault(norm, set()).add(sid)
        self.places_by_pid, self.places_by_norm = load_places_ratings(city)
        self.judge = load_venue_judge(city)
        self.wiki = load_venue_wiki(city)
        self._seesaw: tuple[set[str], set[str]] | None = None

    def seesaw(self) -> tuple[set[str], set[str]]:
        if self._seesaw is None:
            import venue_tiers
            self._seesaw = venue_tiers.seesaw_venue_norms(self.city)
        return self._seesaw


# --- raw evidence per venue (what the JS mirror recomputes from) ---------------

def raw_evidence(v: dict, ctx: Context, today: date) -> dict:
    norms = venue_norms(v)
    facts = v.get("facts") or {}
    page = v.get("page") or {}
    about = v.get("about") or {}
    seed = ((v.get("sources") or {}).get("seed") or {})
    directories = sorted(k for k in seed if k not in DIRECTORY_SEEDS_IGNORED)
    if (v.get("sources") or {}).get("directory_session"):
        # named on a directory / listing page by a zone-enumeration session
        directories.append("enumerated")
    fairs = sorted(set().union(*(ctx.fairs.get(n, set()) for n in norms)) if norms else set())
    lists = sorted(set().union(*(ctx.lists.get(n, set()) for n in norms)) if norms else set())
    press = sorted(set().union(*(ctx.press.get(n, set()) for n in norms)) if norms else set())
    hp = None
    for lines in ((v.get("google") or {}).get("hours"), v.get("hours")):
        if lines:
            p = hours.parse_hours(lines)
            if p["parsed"]:
                hp = p
                break
    founded = facts.get("founded_year") or facts.get("first_exhibition_year")
    years = max(0, today.year - int(founded)) if founded else None
    pid = (seed.get("places") or {}).get("place_id")
    pl = ctx.places_by_pid.get(pid) if pid else None
    if pl is None:
        for n in norms:
            pl = ctx.places_by_norm.get(n)
            if pl:
                break
    judge_rows = ctx.judge.get(v["id"]) or {}
    judge = {variant: {model: {"overall": r.get("overall"), "confidence": r.get("confidence"),
                               "rationale": r.get("rationale")}
                       for model, r in models.items()}
             for variant, models in judge_rows.items()}
    wiki = ctx.wiki.get(v["id"]) or None
    return {
        "hours": hp, "hours_status": v.get("status"),
        "fairs": fairs, "lists": lists, "press": press, "directories": directories,
        "years": years, "founded_basis": ("founded_year" if facts.get("founded_year")
                                          else "first_exhibition_year" if facts.get("first_exhibition_year")
                                          else None),
        "roster_count": facts.get("roster_count"),
        "exhibitions_per_year": facts.get("exhibitions_per_year"),
        "n_locations": len(facts.get("locations_elsewhere") or []),
        "ratings": (pl or {}).get("user_ratings_total"), "rating": (pl or {}).get("rating"),
        "web": {"exhibitions_url": bool(v.get("exhibitions_url")),
                "dates": bool(page.get("date_strings")), "about": bool(about.get("text")),
                "fetch_mode": page.get("fetch_mode")},
        "judge": judge,
        "wiki": ({"title": wiki.get("title"), "sitelinks": wiki.get("sitelinks")} if wiki else None),
        "kind": v.get("kind"),
        "seesaw": None,   # filled by compute_features when leak_seesaw is on
    }


# --- features ---------------------------------------------------------------------

def compute_features(v: dict, ctx: Context, params: dict, today: date,
                     raw: dict | None = None) -> tuple[dict, dict, dict]:
    """(features, basis, raw)."""
    raw = raw or raw_evidence(v, ctx, today)
    refs = params.get("refs") or {}
    lw = params.get("list_weights") or {}
    f: dict[str, float] = {}
    b: dict[str, str] = {}

    if raw["hours"]:
        f["hours_breadth"] = hours.breadth_value(raw["hours"])
        hp = raw["hours"]
        b["hours_breadth"] = ("by appointment only" if hp["days_open"] == 0 and hp["by_appointment"]
                              else f"{hp['days_open']} day(s)/wk, {hp['hours_per_week']:g} h/wk"
                              + (", +appointment" if hp["by_appointment"] else ""))
    elif raw.get("hours_status") == "appointment_only":
        f["hours_breadth"], b["hours_breadth"] = hours.APPOINTMENT_ONLY_VALUE, "status: appointment_only"
    else:
        f["hours_breadth"], b["hours_breadth"] = 0.0, "no hours on record"

    fw = sum(float(lw.get(s, 1.0)) for s in raw["fairs"])
    f["fairs"] = sat(fw, refs.get("fairs", 3))
    b["fairs"] = ", ".join(raw["fairs"]) if raw["fairs"] else "no fair listings"
    lsw = sum(float(lw.get(s, 1.0)) for s in raw["lists"])
    f["curated_lists"] = sat(lsw, refs.get("lists", 2))
    b["curated_lists"] = ", ".join(raw["lists"]) if raw["lists"] else "on no curated list"
    f["press"] = sat(len(raw["press"]), refs.get("press", 3))
    b["press"] = ", ".join(raw["press"]) if raw["press"] else "no press"
    f["directory"] = sat(len(raw["directories"]), refs.get("directory", 2))
    b["directory"] = ", ".join(raw["directories"]) if raw["directories"] else "no directory seed"

    if raw["years"] is not None:
        f["longevity"] = sat(raw["years"], refs.get("longevity_years", 20))
        b["longevity"] = f"{raw['years']} yr(s) ({raw['founded_basis']})"
    else:
        f["longevity"], b["longevity"] = 0.0, "no report"
    rc = raw.get("roster_count")
    f["roster_size"] = sat(rc or 0, refs.get("roster", 20))
    b["roster_size"] = f"{rc} artists" if rc is not None else "no report"
    f["roster_strength"], b["roster_strength"] = 0.0, "artists dataset not built"
    epy = raw.get("exhibitions_per_year")
    f["show_cadence"] = sat(epy or 0, refs.get("shows_per_year", 6))
    b["show_cadence"] = f"{epy:g} shows/yr" if epy is not None else "no report"
    nl = raw.get("n_locations") or 0
    f["multi_location"] = min(1.0, nl / float(refs.get("locations", 2) or 1))
    b["multi_location"] = f"{nl} other location(s)" if nl else "single location / no report"

    ratings = raw.get("ratings")
    if ratings is not None:
        ref = float(refs.get("ratings", 200))
        f["places_popularity"] = min(1.0, math.log10(1 + max(0, ratings)) / math.log10(1 + ref))
        b["places_popularity"] = f"{ratings} Google reviews" + (f", {raw['rating']}★" if raw.get("rating") else "")
    else:
        f["places_popularity"], b["places_popularity"] = 0.0, "no Places ratings cached"

    w = raw["web"]
    f["web_presence"] = 0.35 * w["exhibitions_url"] + 0.35 * w["dates"] + 0.30 * w["about"]
    parts = [k for k in ("exhibitions_url", "dates", "about") if w[k]]
    b["web_presence"] = (", ".join(parts) if parts else "nothing fetched") + (f" [{w['fetch_mode']}]" if w.get("fetch_mode") and w["fetch_mode"] != "static" else "")

    jcfg = params.get("judge") or {}
    variant, model = jcfg.get("variant"), jcfg.get("model") or "none"
    jv = (raw.get("judge") or {}).get(variant) or {}
    vals = []
    if model == "mean":
        vals = [r["overall"] for r in jv.values() if r.get("overall") is not None]
    elif model != "none":
        r = jv.get(JUDGE_MODELS.get(model, model))
        if r and r.get("overall") is not None:
            vals = [r["overall"]]
    if vals:
        f["venue_judge"] = min(1.0, max(0.0, sum(vals) / len(vals) / 10.0))
        b["venue_judge"] = f"{sum(vals) / len(vals):g}/10 ({variant}/{model})"
    else:
        f["venue_judge"], b["venue_judge"] = 0.0, "no verdict"

    wk = raw.get("wiki")
    if wk and wk.get("title"):
        f["wiki"] = 0.5 + 0.5 * sat(wk.get("sitelinks") or 0, refs.get("wiki_sitelinks", 20))
        b["wiki"] = f"{wk['title']} ({wk.get('sitelinks') or 0} sitelinks)"
    else:
        f["wiki"], b["wiki"] = 0.0, "no article"

    grp = KIND_GROUP.get(raw.get("kind") or "")
    for g in ("gallery", "nonprofit", "museum"):
        f[f"kind_{g}"] = 1.0 if grp == g else 0.0
        b[f"kind_{g}"] = raw.get("kind") or "?"

    if params.get("leak_seesaw"):
        feat, allv = ctx.seesaw()
        norms = venue_norms(v)
        ss = 1.0 if norms & feat else (0.5 if norms & allv else 0.0)
        raw["seesaw"] = ss
        f["seesaw_presence"], b["seesaw_presence"] = ss, "LEAKAGE: See Saw presence"
    else:
        f["seesaw_presence"], b["seesaw_presence"] = 0.0, "off"
    return f, b, raw


def score_venue(features: dict, params: dict) -> tuple[float, dict]:
    weights = params.get("weights") or {}
    contrib = {f: float(weights.get(f, 0)) * float(features.get(f, 0.0)) for f in FEATURES}
    return sum(contrib.values()), contrib


def gate_for(v: dict, score: float, params: dict) -> str | None:
    g = params.get("gates") or {}
    if v.get("status") in set(g.get("exclude_status") or []):
        return f"status:{v.get('status')}"
    if g.get("require_verified") and ((v.get("verification") or {}).get("status") != "verified"):
        return "unverified"
    kinds = g.get("kinds") or []
    if kinds and v.get("kind") not in kinds:
        return f"kind:{v.get('kind')}"
    if score < float(g.get("min_score") or 0.0):
        return "below_min_score"
    return None


def manual_positions(params: dict) -> dict[str, tuple[int, int]]:
    """venue_id -> (position, tier) from params.manual_order; {} when automatic."""
    mo = params.get("manual_order")
    if not isinstance(mo, dict):
        return {}
    out: dict[str, tuple[int, int]] = {}
    pos = 0
    for t in ("1", "2", "3"):
        for vid in mo.get(t) or []:
            if vid not in out:
                pos += 1
                out[vid] = (pos, int(t))
    return out


def tier_for(score: float, params: dict) -> int | None:
    t = params.get("tiers") or {}
    for k in ("1", "2", "3"):
        thr = t.get(k)
        if thr is not None and score >= float(thr):
            return int(k)
    return None


def rank(ctx: Context, params: dict, today: date) -> list[dict]:
    rows = []
    for v in ctx.registry.get("venues", []):
        feats, basis, raw = compute_features(v, ctx, params, today)
        score, contrib = score_venue(feats, params)
        rows.append({
            "id": v["id"], "name": v.get("name"), "kind": v.get("kind"),
            "is_museum": venues.is_museum(v), "neighborhood": v.get("neighborhood"),
            "status": v.get("status"),
            "verification": (v.get("verification") or {}).get("status"),
            "website": v.get("website"),
            "report_path": (v.get("research") or {}).get("report_path"),
            "about": ((v.get("about") or {}).get("text") or None),
            "features": feats, "basis": basis, "raw": raw,
            "score": score, "contrib": contrib,
            "gate": gate_for(v, score, params),
        })
    manual = manual_positions(params)
    rows.sort(key=lambda r: ((0, manual[r["id"]][0], "") if r["id"] in manual
                             else (1, -r["score"], r["id"])))
    n = 0
    for r in rows:
        if r["id"] in manual:
            n += 1
            r["rank"], r["tier"], r["gate"] = n, manual[r["id"]][1], None
        elif r["gate"]:
            r["tier"], r["rank"] = None, None
        else:
            n += 1
            r["rank"] = n
            r["tier"] = None if manual else tier_for(r["score"], params)
    return rows


# --- benchmark (reported, never fitted) ----------------------------------------

def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for q in neg:
            wins += 1.0 if p > q else 0.5 if p == q else 0.0
    return wins / (len(pos) * len(neg))


def benchmark(rows: list[dict], ctx: Context) -> dict:
    try:
        feat, allv = ctx.seesaw()
    except Exception:
        feat, allv = set(), set()
    by_id = venues.index_by_id(ctx.registry)
    ss_ids = [r["id"] for r in rows if venue_norms(by_id[r["id"]]) & (allv | feat)]
    gated = [r for r in rows if not r["gate"]]
    pos = [r["score"] for r in gated if r["id"] in set(ss_ids)]
    neg = [r["score"] for r in gated if r["id"] not in set(ss_ids)]
    tiers = {}
    for r in gated:
        if r["id"] in set(ss_ids):
            tiers[str(r["tier"])] = tiers.get(str(r["tier"]), 0) + 1
    return {"seesaw_venue_ids": ss_ids, "n_seesaw": len(ss_ids), "auc": auc(pos, neg),
            "seesaw_tiers": tiers,
            "seesaw_ranks": sorted(r["rank"] for r in gated if r["id"] in set(ss_ids))}


# --- report / CLI -------------------------------------------------------------

def report_path(city: str) -> Path:
    return store.city_dir(city) / "venues_ranked.json"


def build_report(city: str, params: dict, today: date) -> dict:
    ctx = Context(city)
    rows = rank(ctx, params, today)
    try:
        import curation_dashboard
        presets = {k: v for k, v in curation_dashboard.params_presets().items() if k.startswith("venues")}
    except Exception:
        presets = {}
    return {
        "city": city, "generated_at": int(time.time()), "today": today.isoformat(),
        "params_default": params, "params_hash": params_hash(params),
        "feature_names": FEATURES,
        "venues": rows,
        "benchmark": benchmark(rows, ctx),
        "presets": presets,
        "counts": {"venues": len(rows), "gated": sum(1 for r in rows if r["gate"]),
                   "tiers": _tier_counts(rows)},
    }


def _tier_counts(rows: list[dict]) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        if r["gate"]:
            continue
        k = str(r["tier"])
        out[k] = out.get(k, 0) + 1
    return out


def write_report(city: str, params: dict, today: date) -> tuple[Path, dict]:
    rep = build_report(city, params, today)
    p = report_path(city)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    return p, rep


def _print_summary(rep: dict, top: int) -> None:
    rows = rep["venues"]
    print(f"{rep['city']}: {len(rows)} venues, {rep['counts']['gated']} gated, tiers {rep['counts']['tiers']}")
    bm = rep["benchmark"]
    if bm.get("n_seesaw"):
        print(f"See Saw venues in registry: {bm['n_seesaw']}, AUC {bm['auc']:.3f} "
              f"(reported, not fitted); their tiers {bm['seesaw_tiers']}; ranks {bm['seesaw_ranks']}")
    print(f"{'rank':>4} {'score':>6} {'tier':>4} {'id':<34} {'kind':<13} top contributions")
    for r in [x for x in rows if not x["gate"]][:top]:
        c = sorted(r["contrib"].items(), key=lambda kv: -abs(kv[1]))[:4]
        print(f"{r['rank']:>4} {r['score']:6.3f} {str(r['tier'] or '-'):>4} {r['id'][:34]:<34} "
              f"{(r['kind'] or '?')[:13]:<13} " + ", ".join(f"{k} {v:+.2f}" for k, v in c if v))


def cmd_score(args: argparse.Namespace) -> int:
    params = load_params(args.params, args.city)
    today = resolve_today(params, args.today)
    p, rep = write_report(args.city, params, today)
    _print_summary(rep, args.top)
    print(f"wrote {p}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    params = load_params(args.params, args.city)
    today = resolve_today(params, args.today)
    p, rep = write_report(args.city, params, today)
    _print_summary(rep, args.top)
    if args.dry_run:
        print("dry run — registry untouched")
        return 0
    by_id = {r["id"]: r for r in rep["venues"]}
    ts = int(time.time())
    with venues.locked_registry(args.city) as reg:
        for v in reg["venues"]:
            r = by_id.get(v["id"])
            if not r:
                continue
            v["features"] = {f: {"value": round(r["features"][f], 4), "basis": r["basis"].get(f), "ts": ts}
                             for f in FEATURES}
            v["rank"], v["score"] = r["rank"], round(r["score"], 4)
            v["score_breakdown"] = {k: round(c, 4) for k, c in r["contrib"].items() if c}
            v["tier"] = r["tier"]
            v["notability"] = round(min(1.0, max(0.0, r["score"])), 3)
            v["notability_breakdown"] = {"params_hash": rep["params_hash"], "gate": r["gate"]}
            if venues.active_show(v) or not v.get("last_skip"):
                v["next_check"] = venues._iso(venues.compute_next_check(v))
    print(f"registry updated ({len(by_id)} venues, params {rep['params_hash']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    from cities import CITIES
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("score", cmd_score), ("apply", cmd_apply)):
        sp = sub.add_parser(name)
        sp.add_argument("--city", required=True, choices=sorted(CITIES))
        sp.add_argument("--params", help="params JSON layered over venues-default.json")
        sp.add_argument("--today", help="YYYY-MM-DD (default: params.today or real today)")
        sp.add_argument("--top", type=int, default=25)
        if name == "apply":
            sp.add_argument("--dry-run", action="store_true")
        sp.set_defaults(fn=fn)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
