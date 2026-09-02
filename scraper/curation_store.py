"""Curation evidence store (plan Part 2, sections 2.1-2.3): paths, row
schemas, JSONL I/O, the ``record_signal`` tool handler and the deterministic
show matcher.

Layout — everything under ``content/curation/`` is git-tracked text. Like
``content/pending/``, the folder is invisible to the iOS bundle and to
``webdemo/build.py`` (both only look at ``content/*.json``)::

    content/curation/params/default.json         scoring params
    content/curation/<city>/sources.json         publication registry (hand-edited)
    content/curation/<city>/signals.jsonl        immutable: one row per source->show mention
    content/curation/<city>/matches.jsonl        append-only matcher output per signal id (latest wins)
    content/curation/<city>/candidates.jsonl     append-only: shows seen by signals but not in our pool
    content/curation/<city>/runs.jsonl           one row per signals/judge/score/apply/snapshot run
    content/curation/<city>/judge.jsonl          one row per (show, judge variant, model) verdict
    content/curation/<city>/wiki.jsonl           per-artist Wikipedia lookups (refresh > 30 d)
    content/curation/<city>/seesaw/<city>-<date>.json   benchmark snapshots (never edited)
    content/curation/<city>/overrides.json       {"pin": [], "exclude": []}
    content/curation/<city>/curated.json         last applied ranking
    content/curation/<city>/applied.jsonl        audit trail of apply runs

Row schemas (all timestamps are epoch seconds; dates are ISO ``YYYY-MM-DD``):

signal
    ``{id, dedupe_key, ts, city, run_id, prompt_variant, prompt_hash, model,
    source{id,kind,name,url}, published_at|null, kind, strength,
    show_ref{venue, artist|null, title|null}, snippet (<=300 chars),
    agent_pool_slug|null}``
    ``id = sha1(run_id|source_url|norm venue|norm artist-or-title|kind)[:12]``;
    ``dedupe_key`` is the same hash without ``run_id``. The handler rejects a
    repeat ``dedupe_key`` within one ``run_id``; across runs rows accumulate and
    the scorer collapses by ``dedupe_key`` keeping the strongest row.
match
    ``{ts, signal_id, slug|null, venue_id|null, venue_norm, method, confidence,
    matcher_version, candidate_key|null, venue_in_pool, venue_via}``
    ``venue_norm`` is the POOL venue's norm when a venue was found (so fair
    signals aggregate per pool venue), else the signal's own venue norm.
candidate
    ``{ts, city, key "<venue_norm>|<norm artist-or-title>", event seen|resolved,
    show_ref, signal_id, run_id, venue_in_pool, resolved_slug|null}``
run
    ``{ts, run_id, city, stage signals|judge|score|apply|snapshot, prompt_variant,
    prompt_hash, model, effort, sources_planned[], sources_seen[],
    signals_recorded, candidates_new, duplicates_rejected, requests,
    web_searches, cost_usd, stop_reason, duration_s, notes}``
judge
    ``{ts, city, slug, variant, prompt_hash, model, effort, evidence_hash,
    cache_key, run_id, scores{...}, overall, confidence, rationale,
    evidence_used[], red_flags[], usage{}, cost_usd, batch}`` (written by judge.py)
wiki
    ``{ts, artist, norm, title|null, pageviews_90d, url|null, description|null,
    error|null}`` (written by ``curate.py wiki``)

The matcher (``match_show_ref``) is pure: it never touches disk. The handler
is the only writer of signals/matches/candidates rows.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import tools
from crosscheck import GENERIC_NAME_WORDS, _name_words

MATCHER_VERSION = 1

# See Saw is the benchmark, never a signal source: signal sessions pass these
# as blocked_domains so the overlap metric stays honest.
BENCHMARK_DOMAINS = ["seesawmap.com", "seesaw.app"]

SIGNAL_KINDS = ["pick", "review", "news", "listing", "fair_exhibitor",
                "artist_activity", "award", "press_release_claim"]
SIGNAL_STRENGTHS = ["headline", "featured", "mentioned", "passing"]
SNIPPET_MAX = 300

# --- paths --------------------------------------------------------------------

CURATION_DIR = tools.CONTENT_DIR / "curation"
REPORTS_DIR = tools.CONTENT_DIR / "spend" / "reports"


def params_dir() -> Path:
    return CURATION_DIR / "params"


def default_params_file() -> Path:
    return params_dir() / "default.json"


def city_dir(city: str) -> Path:
    return CURATION_DIR / city


def sources_file(city: str) -> Path:
    return city_dir(city) / "sources.json"


def signals_file(city: str) -> Path:
    return city_dir(city) / "signals.jsonl"


def matches_file(city: str) -> Path:
    return city_dir(city) / "matches.jsonl"


def candidates_file(city: str) -> Path:
    return city_dir(city) / "candidates.jsonl"


def runs_file(city: str) -> Path:
    return city_dir(city) / "runs.jsonl"


def judge_file(city: str) -> Path:
    return city_dir(city) / "judge.jsonl"


def wiki_file(city: str) -> Path:
    return city_dir(city) / "wiki.jsonl"


def overrides_file(city: str) -> Path:
    return city_dir(city) / "overrides.json"


def curated_file(city: str) -> Path:
    return city_dir(city) / "curated.json"


def applied_file(city: str) -> Path:
    return city_dir(city) / "applied.jsonl"


def seesaw_dir(city: str) -> Path:
    return city_dir(city) / "seesaw"


def snapshot_file(city: str, day: str) -> Path:
    return seesaw_dir(city) / f"{city}-{day}.json"


def report_file(city: str, ext: str = "json") -> Path:
    return REPORTS_DIR / f"curation-{city}.{ext}"


def registry_file(city: str) -> Path:
    return tools.CONTENT_DIR / "venues" / f"{city}.json"


# --- generic JSONL / JSON I/O --------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    """All rows of a JSONL ledger, oldest first; malformed lines are skipped."""
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def append_row(path: Path, row: dict) -> dict:
    """Append one row (stamping ``ts`` when absent) under the shared flock."""
    if "ts" not in row:
        row = {"ts": int(time.time()), **row}
    tools._append_jsonl(path, row)
    return row


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except json.JSONDecodeError:
        return default


# --- tool schema ---------------------------------------------------------------

RECORD_SIGNAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["city", "source_id", "source_url", "published_at", "kind", "strength",
                 "venue", "artist", "title", "snippet", "agent_pool_slug"],
    "properties": {
        "city": {"type": "string", "description": "City key, e.g. 'los-angeles'"},
        "source_id": {"type": "string", "description": "id of the publication from the SOURCES list in your instructions (e.g. 'carla'); use the closest entry, or a short kebab-case name for an unlisted outlet"},
        "source_url": {"type": "string", "description": "URL of the exact page (article, picks list, exhibitor list) where the mention appears"},
        "published_at": {"type": ["string", "null"], "description": "ISO date YYYY-MM-DD the article/listing was published, or null when the page shows no date"},
        "kind": {
            "type": "string", "enum": SIGNAL_KINDS,
            "description": "pick: editorial 'shows to see' recommendation; review: a critic reviewed the show itself; news: news coverage of the show; listing: appears in a neutral listings roundup; fair_exhibitor: the VENUE is on an art-fair exhibitor list (leave artist/title null); artist_activity: the artist's recent museum/biennial show, acquisition or major profile elsewhere; award: the artist won a major prize; press_release_claim: a significance claim from the venue's own text (first museum solo, retrospective, commissioned...)",
        },
        "strength": {
            "type": "string", "enum": SIGNAL_STRENGTHS,
            "description": "headline: the piece is about this show/artist; featured: one of a few highlighted items; mentioned: one entry in a longer roundup; passing: a brief aside",
        },
        "venue": {"type": "string", "description": "Venue name as written in the source (the matcher normalizes it)"},
        "artist": {"type": ["string", "null"], "description": "Artist name(s) as written, or null (group show / venue-level signal)"},
        "title": {"type": ["string", "null"], "description": "Exhibition title as written, or null if the source gives none"},
        "snippet": {"type": "string", "description": "Up to 300 characters quoted or closely paraphrased from the source that justify kind and strength"},
        "agent_pool_slug": {"type": ["string", "null"], "description": "If you believe this mention is one of the shows in the POOL inventory, its slug; else null. Advisory only — the deterministic matcher decides"},
    },
}


# --- normalization + ids -------------------------------------------------------

def norm_text(s: str | None) -> str:
    """Accent-folded, lower-cased, alphanumeric-only key for artists/titles."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def norm_venue(name: str | None) -> str:
    return tools._norm_venue(name) if name and name.strip() else ""


def norm_ref_key(ref: dict) -> str:
    """The 'norm artist-or-title' half of ids and candidate keys."""
    return norm_text(ref.get("artist")) or norm_text(ref.get("title"))


def signal_ids(run_id: str, source_url: str, venue_norm: str, ref_key: str,
               kind: str) -> tuple[str, str]:
    """(id, dedupe_key) — id is run-scoped, dedupe_key is not."""
    base = f"{source_url}|{venue_norm}|{ref_key}|{kind}"
    sid = hashlib.sha1(f"{run_id}|{base}".encode()).hexdigest()[:12]
    dkey = hashlib.sha1(base.encode()).hexdigest()[:12]
    return sid, dkey


def candidate_key(venue_norm: str, ref_key: str) -> str:
    return f"{venue_norm}|{ref_key}"


def venue_id_for(name: str) -> str:
    """Registry-style venue id: slugified ``_norm_venue`` (plan 1.1)."""
    return tools._slugify(norm_venue(name))


# --- loaders (latest-wins where the plan says so) ------------------------------

def load_sources(city: str) -> list[dict]:
    data = read_json(sources_file(city), {"sources": []})
    return data.get("sources", []) if isinstance(data, dict) else data


def sources_by_id(city: str) -> dict[str, dict]:
    return {s["id"]: s for s in load_sources(city) if s.get("id")}


def load_signals(city: str) -> list[dict]:
    """Every signal row, oldest first (immutable ledger)."""
    return read_jsonl(signals_file(city))


STRENGTH_RANK = {s: i for i, s in enumerate(reversed(SIGNAL_STRENGTHS))}  # passing=0 .. headline=3


def collapse_signals(rows: list[dict]) -> list[dict]:
    """Collapse repeats across runs by ``dedupe_key``: keep the strongest
    row, ties broken by the latest ``ts``. Adds ``n_rows`` (how many runs saw
    it) so the dashboard can show corroboration."""
    best: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for r in rows:
        k = r.get("dedupe_key") or r.get("id")
        counts[k] = counts.get(k, 0) + 1
        cur = best.get(k)
        rank = (STRENGTH_RANK.get(r.get("strength"), -1), r.get("ts", 0))
        if cur is None or rank > (STRENGTH_RANK.get(cur.get("strength"), -1), cur.get("ts", 0)):
            best[k] = r
    out = []
    for k, r in best.items():
        out.append({**r, "n_rows": counts[k]})
    out.sort(key=lambda r: r.get("ts", 0))
    return out


def load_matches(city: str) -> dict[str, dict]:
    """Latest match row per signal id."""
    out: dict[str, dict] = {}
    for r in read_jsonl(matches_file(city)):
        if r.get("signal_id"):
            out[r["signal_id"]] = r
    return out


def load_candidate_events(city: str) -> list[dict]:
    return read_jsonl(candidates_file(city))


def load_candidates(city: str) -> dict[str, dict]:
    """Aggregated candidate state per key: latest event wins for
    ``event``/``resolved_slug``/``show_ref``; ``signal_ids``/``run_ids`` union;
    ``first_seen``/``last_seen``/``n_seen`` counters."""
    out: dict[str, dict] = {}
    for r in load_candidate_events(city):
        k = r.get("key")
        if not k:
            continue
        agg = out.get(k)
        if agg is None:
            agg = {"key": k, "city": r.get("city"), "show_ref": r.get("show_ref"),
                   "event": r.get("event"), "venue_in_pool": bool(r.get("venue_in_pool")),
                   "resolved_slug": r.get("resolved_slug"), "signal_ids": [],
                   "run_ids": [], "first_seen": r.get("ts"), "last_seen": r.get("ts"),
                   "n_seen": 0}
            out[k] = agg
        agg["event"] = r.get("event", agg["event"])
        agg["resolved_slug"] = r.get("resolved_slug") if r.get("event") == "resolved" else agg["resolved_slug"]
        if r.get("show_ref"):
            agg["show_ref"] = r["show_ref"]
        agg["venue_in_pool"] = bool(r.get("venue_in_pool", agg["venue_in_pool"]))
        if r.get("signal_id") and r["signal_id"] not in agg["signal_ids"]:
            agg["signal_ids"].append(r["signal_id"])
        if r.get("run_id") and r["run_id"] not in agg["run_ids"]:
            agg["run_ids"].append(r["run_id"])
        if r.get("event") == "seen":
            agg["n_seen"] += 1
            agg["last_seen"] = r.get("ts", agg["last_seen"])
    return out


def load_runs(city: str) -> list[dict]:
    return read_jsonl(runs_file(city))


def append_run(row: dict) -> dict:
    """Append one run row to ``runs.jsonl`` of ``row['city']``."""
    if not row.get("city"):
        raise ValueError("run row needs a city")
    return append_row(runs_file(row["city"]), row)


def load_judge(city: str) -> dict[str, dict[str, dict[str, dict]]]:
    """slug -> variant -> model -> latest verdict row."""
    out: dict[str, dict[str, dict[str, dict]]] = {}
    for r in read_jsonl(judge_file(city)):
        slug, variant, model = r.get("slug"), r.get("variant"), r.get("model")
        if not (slug and variant and model):
            continue
        out.setdefault(slug, {}).setdefault(variant, {})[model] = r
    return out


def load_wiki(city: str) -> dict[str, dict]:
    """artist norm -> latest lookup row (rows with ``error`` are kept but the
    wiki fetcher treats them as stale)."""
    out: dict[str, dict] = {}
    for r in read_jsonl(wiki_file(city)):
        k = r.get("norm") or norm_text(r.get("artist"))
        if k:
            out[k] = r
    return out


def load_overrides(city: str) -> dict:
    data = read_json(overrides_file(city), {})
    return {"pin": list(data.get("pin", []) or []), "exclude": list(data.get("exclude", []) or [])}


def list_snapshots(city: str) -> list[Path]:
    d = seesaw_dir(city)
    return sorted(d.glob(f"{city}-*.json")) if d.is_dir() else []


def load_snapshot(city: str, snapshot_id: str | None = "latest") -> dict | None:
    """A See Saw snapshot by id (file stem, e.g. ``los-angeles-2026-09-01``),
    ``latest``, or ``None``/``none`` for no snapshot. Adds ``id``."""
    if snapshot_id in (None, "none", ""):
        return None
    paths = list_snapshots(city)
    if not paths:
        return None
    path = paths[-1] if snapshot_id == "latest" else next(
        (p for p in paths if p.stem == snapshot_id), None)
    if path is None:
        return None
    snap = read_json(path, None)
    if not isinstance(snap, dict):
        return None
    snap["id"] = path.stem
    return snap


# --- venue registry (Part 1 module, written by someone else) ------------------

def load_registry_safe(city: str) -> dict:
    """The Part-1 venue registry, or ``{}`` when it does not exist yet.
    Prefers ``venues.load_registry`` when that module is importable, else
    reads ``content/venues/<city>.json`` directly."""
    try:
        import venues  # type: ignore  # noqa: F401
        reg = venues.load_registry(city)  # type: ignore[attr-defined]
        if isinstance(reg, dict) and reg.get("venues"):
            return reg
    except Exception:
        pass
    data = read_json(registry_file(city), {})
    return data if isinstance(data, dict) and data.get("venues") else {}


def registry_index(registry: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """(by_id, by_norm) where by_norm covers name + aliases."""
    by_id: dict[str, dict] = {}
    by_norm: dict[str, dict] = {}
    for v in (registry or {}).get("venues", []) or []:
        if v.get("id"):
            by_id[v["id"]] = v
        for name in [v.get("name")] + list(v.get("aliases") or []):
            for k in venue_keys(name or ""):
                by_norm.setdefault(k, v)
    return by_id, by_norm


# --- matcher (plan 2.3) --------------------------------------------------------

VENUE_RATIO_MIN = 0.88
SHARED_WORDS_MIN = 2
ARTIST_RATIO_MIN = 0.80
TITLE_RATIO_MIN = 0.75
WEAK_MAX = 0.5            # artist AND title both below this -> different show at a pool venue
ARTIST_TITLE_ARTIST_MIN = 0.90
ARTIST_TITLE_TITLE_MIN = 0.80

# Words that two different LA venues commonly share and must not count as
# "distinctive" in the shared-words rule (crosscheck's set + place words).
MATCH_GENERIC_WORDS = set(GENERIC_NAME_WORDS) | {
    "los", "angeles", "la", "new", "york", "city", "inc", "llc", "ca", "california",
    "street", "st", "avenue", "ave", "blvd", "boulevard", "road", "rd", "west", "east",
    "north", "south", "hollywood", "santa", "monica", "downtown", "house", "institute",
    "project", "exhibitions", "artists", "gallerie", "galeria", "at", "in", "on",
}


@dataclass
class Match:
    """Result of ``match_show_ref``. ``slug`` set => matched a pool show;
    otherwise ``candidate_key`` is set (``venue_in_pool`` says whether the
    venue exists in the pool with a different show)."""
    slug: str | None
    venue_id: str | None
    venue_norm: str
    method: str
    confidence: float
    candidate_key: str | None
    venue_in_pool: bool = False
    venue_via: str | None = None       # exact | alias | fuzzy_ratio | shared_words | registry | hint
    hint_rejected: str | None = None   # agent_pool_slug that was rejected (venue mismatch / disagreement)
    detail: str = ""
    scores: dict = field(default_factory=dict)

    @property
    def is_match(self) -> bool:
        return self.slug is not None

    def to_row(self, signal_id: str, ts: int | None = None) -> dict:
        return {
            "ts": ts if ts is not None else int(time.time()),
            "signal_id": signal_id,
            "slug": self.slug,
            "venue_id": self.venue_id,
            "venue_norm": self.venue_norm,
            "method": self.method,
            "confidence": round(self.confidence, 3),
            "matcher_version": MATCHER_VERSION,
            "candidate_key": self.candidate_key,
            "venue_in_pool": self.venue_in_pool,
            "venue_via": self.venue_via,
        }

    def as_dict(self) -> dict:
        return asdict(self)


def venue_keys(name: str) -> set[str]:
    """Norm keys a venue answers to: the full norm, the name without any
    parenthetical, and each parenthetical on its own (so 'Los Angeles County
    Museum of Art (LACMA)' also answers to 'lacma')."""
    keys = {norm_venue(name)}
    base = re.sub(r"\([^)]*\)", " ", name or "")
    if base.strip():
        keys.add(norm_venue(base))
    for inner in re.findall(r"\(([^)]+)\)", name or ""):
        keys.add(norm_venue(inner))
    return {k for k in keys if k}


class PoolIndex:
    """Venue-keyed view of the pool (published + pending show records)."""

    def __init__(self, pool: list[dict]):
        self.pool = list(pool or [])
        self.by_key: dict[str, list[dict]] = {}
        self.by_slug: dict[str, dict] = {}
        self.primary_norm: dict[str, str] = {}   # slug -> full venue norm
        for show in self.pool:
            name = (show.get("venue") or {}).get("name") or ""
            self.by_slug[show["slug"]] = show
            self.primary_norm[show["slug"]] = norm_venue(name)
            for k in venue_keys(name):
                self.by_key.setdefault(k, []).append(show)

    def shows_for_norm(self, norm: str) -> list[dict]:
        return list(self.by_key.get(norm, []))

    def venue_norms(self) -> list[str]:
        return sorted({n for n in self.primary_norm.values() if n})


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _distinctive_words(norm: str) -> set[str]:
    return _name_words(norm) - MATCH_GENERIC_WORDS


def _split_names(s: str) -> list[str]:
    parts = re.split(r",|&|/|\+|;|\bwith\b|\band\b", s or "", flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def find_pool_venue(venue_norm: str, index: PoolIndex,
                    registry: dict | None = None) -> tuple[list[dict], str | None, str]:
    """(shows at the venue, how it was found, pool venue norm). Empty list
    when the venue is not in the pool. Order of attempts (plan 2.3 step 1):
    exact key -> fuzzy ratio >= 0.88 -> >= 2 shared distinctive words ->
    registry alias/domain."""
    if not venue_norm:
        return [], None, venue_norm
    exact = index.shows_for_norm(venue_norm)
    if exact:
        via = "exact" if index.primary_norm[exact[0]["slug"]] == venue_norm else "alias"
        return exact, via, index.primary_norm[exact[0]["slug"]]

    best: tuple[float, str, str] | None = None   # (ratio, pool norm, via)
    ref_words = _distinctive_words(venue_norm)
    for pool_norm in index.venue_norms():
        r = _ratio(venue_norm, pool_norm)
        via = None
        if r >= VENUE_RATIO_MIN:
            via = "fuzzy_ratio"
        elif len(ref_words & _distinctive_words(pool_norm)) >= SHARED_WORDS_MIN:
            via = "shared_words"
        if via and (best is None or r > best[0]):
            best = (r, pool_norm, via)
    if best:
        return index.shows_for_norm(best[1]), best[2], best[1]

    if registry:
        by_id, by_norm = registry_index(registry)
        v = by_norm.get(venue_norm)
        if v is None:
            try:
                import venues  # type: ignore
                v = venues.find_venue(registry, venue_norm)  # type: ignore[attr-defined]
            except Exception:
                v = None
        if v:
            keys = set()
            for name in [v.get("name")] + list(v.get("aliases") or []):
                keys |= venue_keys(name or "")
            hits = [s for s in index.pool
                    if s.get("venue_id") == v.get("id")
                    or index.primary_norm.get(s["slug"]) in keys]
            if hits:
                return hits, "registry", index.primary_norm[hits[0]["slug"]]
    return [], None, venue_norm


def artist_similarity(ref_artist: str | None, show: dict) -> float | None:
    """Best of: difflib ratio between the (split) names, or last-name
    containment in the pool artist or title (=> 0.85, i.e. above the 0.80
    bar). ``None`` when the reference has no artist."""
    ra = norm_text(ref_artist)
    if not ra:
        return None
    show_artist = norm_text(show.get("artist"))
    show_title = norm_text(show.get("title"))
    cands = [c for c in [show_artist] + [norm_text(p) for p in _split_names(show.get("artist") or "")] if c]
    refs = [c for c in [ra] + [norm_text(p) for p in _split_names(ref_artist or "")] if c]
    best = 0.0
    for r in refs:
        for c in cands:
            best = max(best, _ratio(r, c))
    for r in refs:
        toks = r.split()
        last = toks[-1] if toks else ""
        if len(last) >= 3 and (last in show_artist.split() or last in show_title.split()):
            best = max(best, 0.85)
    return best


def title_similarity(ref_title: str | None, show: dict, ref_has_artist: bool = True) -> float | None:
    """difflib ratio against the pool title (containment of a >= 6-char
    reference title counts as 0.9). When the reference carries a title but no
    artist ("Title @ Venue" lines), the title is also compared to the pool
    artist so 'Charles Dickson @ Parrasch Heijnen' still resolves."""
    rt = norm_text(ref_title)
    if not rt:
        return None
    st = norm_text(show.get("title"))
    best = _ratio(rt, st)
    if len(rt) >= 6 and st and (rt in st or st in rt):
        best = max(best, 0.9)
    if not ref_has_artist:
        sa = norm_text(show.get("artist"))
        if sa:
            best = max(best, _ratio(rt, sa))
            last = rt.split()[-1]
            if len(last) >= 3 and last in sa.split():
                best = max(best, 0.85)
    return best


def _judge_within_venue(ref: dict, show: dict) -> tuple[str, float, dict]:
    """Plan 2.3 step 2. Returns (method, confidence, scores); method
    'candidate' means the venue is in the pool with a different show."""
    a = artist_similarity(ref.get("artist"), show)
    t = title_similarity(ref.get("title"), show, ref_has_artist=bool(norm_text(ref.get("artist"))))
    scores = {"artist": None if a is None else round(a, 3),
              "title": None if t is None else round(t, 3)}
    if a is not None and a >= ARTIST_RATIO_MIN:
        return "venue+artist", 0.95, scores
    if t is not None and t >= TITLE_RATIO_MIN:
        return "venue+title", 0.90, scores
    if a is None and t is None:
        return "venue_only", 0.70, scores
    provided = [x for x in (a, t) if x is not None]
    if all(x < WEAK_MAX for x in provided):
        return "candidate", 0.0, scores
    return "venue_only", 0.60, scores   # ambiguous band: same venue, weak artist/title evidence


def match_show_ref(ref: dict, pool: list[dict] | PoolIndex,
                   registry: dict | None = None) -> Match:
    """Deterministic matcher (plan 2.3). ``ref`` = ``{venue, artist|null,
    title|null, agent_pool_slug|null}``; ``pool`` = show records (published +
    pending) or a prebuilt ``PoolIndex``; ``registry`` = the Part-1 venue
    registry dict (or ``{}``)."""
    index = pool if isinstance(pool, PoolIndex) else PoolIndex(pool)
    venue_norm = norm_venue(ref.get("venue"))
    ref_key = norm_ref_key(ref)
    ckey = candidate_key(venue_norm, ref_key)
    _, reg_by_norm = registry_index(registry) if registry else ({}, {})

    def vid(show: dict | None, norm: str) -> str | None:
        if show and show.get("venue_id"):
            return show["venue_id"]
        rv = reg_by_norm.get(norm)
        if rv and rv.get("id"):
            return rv["id"]
        return venue_id_for(norm) if norm else None

    shows, via, pool_norm = find_pool_venue(venue_norm, index, registry)
    result: Match
    if shows:
        best: tuple[float, str, dict, dict] | None = None
        for show in shows:
            method, conf, scores = _judge_within_venue(ref, show)
            key = (conf, 1 if tools.show_in_window(show) else 0)
            if best is None or key > (best[0], 1 if tools.show_in_window(best[3]) else 0):
                best = (conf, method, scores, show)
        conf, method, scores, show = best
        if method == "venue_only" and len(shows) > 1 and conf >= 0.70:
            # A bare venue mention at a venue holding several live shows is a
            # venue-level signal: don't pin it to whichever show sorts first.
            live = [x for x in shows if tools.show_in_window(x)]
            if len(live) != 1:
                result = Match(None, vid(show, pool_norm), pool_norm, "venue_only", 0.5, None,
                               venue_in_pool=True, venue_via=via, scores=scores,
                               detail=f"venue in pool with {len(shows)} shows; no artist/title to pick one")
                return result
            show = live[0]
        if method == "candidate":
            result = Match(None, vid(show, pool_norm), pool_norm, "candidate", 0.0, ckey,
                           venue_in_pool=True, venue_via=via, scores=scores,
                           detail=f"venue in pool ({show['slug']}) but artist/title differ")
        else:
            result = Match(show["slug"], vid(show, pool_norm), pool_norm, method, conf, None,
                           venue_in_pool=True, venue_via=via, scores=scores,
                           detail=f"venue via {via}")
    else:
        best = None
        for show in index.pool:
            a = artist_similarity(ref.get("artist"), show)
            t = title_similarity(ref.get("title"), show, ref_has_artist=bool(norm_text(ref.get("artist"))))
            if a is not None and t is not None and a >= ARTIST_TITLE_ARTIST_MIN and t >= ARTIST_TITLE_TITLE_MIN:
                if best is None or a + t > best[0]:
                    best = (a + t, show, {"artist": round(a, 3), "title": round(t, 3)})
        if best:
            _, show, scores = best
            sn = index.primary_norm[show["slug"]]
            result = Match(show["slug"], vid(show, sn), sn, "artist_title", 0.80, None,
                           venue_in_pool=False, venue_via=None, scores=scores,
                           detail="venue not in pool; artist+title agree")
        else:
            result = Match(None, vid(None, venue_norm), venue_norm, "candidate", 0.0, ckey,
                           venue_in_pool=False, venue_via=None,
                           detail="no venue hit" if venue_norm else "no venue in reference")

    # Step 4: the agent's pool-slug hint is advisory. Accept only when its
    # venue equals the signal's venue; a rejection is surfaced via
    # ``hint_rejected`` so record_signal can log_event it.
    hint = ref.get("agent_pool_slug")
    if hint:
        hs = index.by_slug.get(hint)
        hint_keys = venue_keys((hs.get("venue") or {}).get("name") or "") if hs else set()
        same_venue = bool(hs) and (venue_norm in hint_keys or (pool_norm and pool_norm in hint_keys))
        if not same_venue:
            result.hint_rejected = hint
        elif result.slug is None:
            sn = index.primary_norm[hs["slug"]]
            result = Match(hs["slug"], vid(hs, sn), sn, "agent_hint", 0.65, None,
                           venue_in_pool=True, venue_via=result.venue_via or "hint",
                           scores=result.scores, detail="deterministic match failed; agent hint at same venue accepted")
        elif result.slug != hint:
            result.hint_rejected = hint
    return result


# --- record_signal tool handler (plan 2.2) ------------------------------------

def _iso_or_none(s) -> str | None:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return date.fromisoformat(s.strip()[:10]).isoformat()
    except ValueError:
        return None


def record_signal(args: dict, city_key: str, run_id: str, variant: str | None,
                  model: str | None, prompt_hash: str | None = None,
                  pool: list[dict] | None = None, registry: dict | None = None) -> str:
    """Tool handler: validate, dedupe within the run, match, append the
    signal + match (+ candidate) rows, and return JSON the model can learn
    from: ``{recorded, signal_id, match|candidate, recorded_this_session}``.

    ``pool``/``registry`` default to the city's live pool and registry; tests
    inject them."""
    if args.get("city", city_key) != city_key:
        raise ValueError(f"city must be '{city_key}'")
    kind = args.get("kind")
    if kind not in SIGNAL_KINDS:
        raise ValueError(f"kind must be one of {SIGNAL_KINDS}")
    strength = args.get("strength")
    if strength not in SIGNAL_STRENGTHS:
        raise ValueError(f"strength must be one of {SIGNAL_STRENGTHS}")
    venue = args.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        raise ValueError("venue is required (the venue name as written in the source)")
    source_url = args.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("source_url is required (the page where the mention appears)")
    source_id = (args.get("source_id") or "unknown").strip()
    notes = []
    snippet = args.get("snippet") or ""
    if not isinstance(snippet, str):
        snippet = str(snippet)
    if len(snippet) > SNIPPET_MAX:
        snippet = snippet[:SNIPPET_MAX]
        notes.append(f"snippet truncated to {SNIPPET_MAX} chars")
    published_at = _iso_or_none(args.get("published_at"))
    if args.get("published_at") and published_at is None:
        notes.append("published_at was not ISO YYYY-MM-DD; stored as null")
    artist = (args.get("artist") or "").strip() or None
    title = (args.get("title") or "").strip() or None
    ref = {"venue": venue.strip(), "artist": artist, "title": title}

    venue_n = norm_venue(venue)
    ref_key = norm_ref_key(ref)
    sid, dkey = signal_ids(run_id, source_url.strip(), venue_n, ref_key, kind)

    this_run = [r for r in load_signals(city_key) if r.get("run_id") == run_id]
    prior = next((r for r in this_run if r.get("dedupe_key") == dkey), None)
    if prior:
        return json.dumps({
            "recorded": False,
            "reason": "duplicate: this source/venue/show/kind was already recorded in this session",
            "signal_id": prior["id"],
            "recorded_this_session": len(this_run),
        })

    src = sources_by_id(city_key).get(source_id)
    source = {"id": source_id, "kind": src.get("kind") if src else None,
              "name": src.get("name") if src else None, "url": source_url.strip()}
    if src is None:
        notes.append(f"source_id '{source_id}' is not in sources.json (kept; weight falls back to default)")

    ts = int(time.time())
    row = {
        "id": sid, "dedupe_key": dkey, "ts": ts, "city": city_key, "run_id": run_id,
        "prompt_variant": variant, "prompt_hash": prompt_hash, "model": model,
        "source": source, "published_at": published_at, "kind": kind, "strength": strength,
        "show_ref": ref, "snippet": snippet,
        "agent_pool_slug": (args.get("agent_pool_slug") or None),
    }
    pool = pool if pool is not None else tools.all_city_shows(city_key)
    registry = registry if registry is not None else load_registry_safe(city_key)
    m = match_show_ref({**ref, "agent_pool_slug": row["agent_pool_slug"]}, pool, registry)

    tools._append_jsonl(signals_file(city_key), row)
    tools._append_jsonl(matches_file(city_key), m.to_row(sid, ts))
    if m.hint_rejected:
        tools.log_event({"kind": "match_hint_rejected", "session": run_id, "city": city_key,
                         "signal_id": sid, "hint": m.hint_rejected, "venue": venue,
                         "matched_slug": m.slug})

    result: dict = {"recorded": True, "signal_id": sid,
                    "recorded_this_session": len(this_run) + 1}
    if m.is_match:
        show = next((s for s in pool if s["slug"] == m.slug), None)
        result["match"] = {"slug": m.slug, "method": m.method, "confidence": m.confidence,
                           "title": show.get("title") if show else None}
    else:
        tools._append_jsonl(candidates_file(city_key), {
            "ts": ts, "city": city_key, "key": m.candidate_key, "event": "seen",
            "show_ref": ref, "signal_id": sid, "run_id": run_id,
            "venue_in_pool": m.venue_in_pool, "resolved_slug": None,
        })
        result["candidate"] = {
            "key": m.candidate_key, "venue_in_pool": m.venue_in_pool,
            "note": ("this venue is in our pool with a different show — recorded as a "
                     "refresh hint" if m.venue_in_pool else
                     "not in our pool — recorded as a scrape-gap candidate; keep recording "
                     "shows like this, they are valuable"),
        }
    if m.hint_rejected:
        notes.append(f"agent_pool_slug '{m.hint_rejected}' ignored (different venue)")
    if notes:
        result["note"] = "; ".join(notes)
    return json.dumps(result, ensure_ascii=False)


# --- offline re-matching (curate.py match) ------------------------------------

def rematch(city: str, pool: list[dict] | None = None, registry: dict | None = None,
            unmatched_only: bool = False, force: bool = False) -> dict:
    """Re-run the matcher over every signal (or only currently unmatched
    ones), appending a new match row whenever the outcome changed (or
    ``force``), then resolve candidates whose show has since been scraped."""
    pool = pool if pool is not None else tools.all_city_shows(city)
    registry = registry if registry is not None else load_registry_safe(city)
    index = PoolIndex(pool)
    latest = load_matches(city)
    stats = {"signals": 0, "considered": 0, "changed": 0, "matched": 0,
             "candidates_resolved": 0}
    ts = int(time.time())
    for sig in load_signals(city):
        stats["signals"] += 1
        prev = latest.get(sig["id"])
        if unmatched_only and prev and prev.get("slug"):
            continue
        stats["considered"] += 1
        m = match_show_ref({**sig["show_ref"], "agent_pool_slug": sig.get("agent_pool_slug")},
                           index, registry)
        row = m.to_row(sig["id"], ts)
        if m.is_match:
            stats["matched"] += 1
        changed = (prev is None or force or any(
            prev.get(k) != row.get(k) for k in ("slug", "method", "candidate_key", "matcher_version")))
        if changed:
            tools._append_jsonl(matches_file(city), row)
            stats["changed"] += 1
    # candidates: resolve any whose reference now matches a pool show
    for key, cand in load_candidates(city).items():
        if cand.get("event") == "resolved":
            continue
        ref = cand.get("show_ref") or {}
        m = match_show_ref(ref, index, registry)
        if m.is_match:
            tools._append_jsonl(candidates_file(city), {
                "ts": ts, "city": city, "key": key, "event": "resolved", "show_ref": ref,
                "signal_id": (cand.get("signal_ids") or [None])[-1],
                "run_id": f"match-{city}-{ts}", "venue_in_pool": m.venue_in_pool,
                "resolved_slug": m.slug,
            })
            stats["candidates_resolved"] += 1
    return stats
