"""Per-gallery research (stage G3 of the galleries-first pipeline, docs/GALLERIES.md):
crawl the venue's whole site, let the model triage the page inventory, compile a
GalleryReport from the pages it chose, and write the registry's about/facts/
research blocks. Once per venue: nothing here re-runs inside 365 days unless
``--force``.

    python research_venue.py run    --city X [--venue-ids a,b|@file] [--limit N] [--max-pages 300]
                                    [--max-fetch-more 40] [--gapfill] [--force] [--workers 4]
                                    [--budget USD] [--no-render] [--dry-run]
    python research_venue.py check  --city X [--venue-ids ...] [--limit N]     # QA the blurbs
    python research_venue.py judge  --city X [--venue-ids ...] [--limit N] [--workers 4] [--force]
    python research_venue.py report --city X

Stages per venue (``run``):
  1. crawl.crawl_site  -> page inventory + evidence text ($0; sitemap + BFS + renderer)
  2. TRIAGE  one offline Sonnet call over ``i | url | title | chars | dates`` lines ->
             a label per page (about, roster_index, artist_page, exhibitions_current,
             exhibitions_archive, exhibition_page, news, fairs, contact_hours, other)
             plus ``fetch_more`` (paginated archives, artist subpages) that the crawler
             then fetches under the same labels (cap --max-fetch-more, in-domain only)
  3. COMPILE profile call (about + contact + roster + fairs pages) and one exhibitions
             call per archive chunk (~45K tokens each; ~120K input tokens per venue cap),
             all offline, json_schema output, system prompt cached
  4. GAPFILL (--gapfill) when about/founded/roster are still missing: one agentic
             session with the server web_search + web_fetch tools (6 uses each; the
             container-id echo the 20260209 tools need), then a structured extraction
             of its findings -> source_kind "secondary"
  5. write content/venues/reports/<city>/<venue_id>.json + registry about/facts/research

``check`` verifies every sentence of about_text against the evidence pages the
triage labelled; unsupported sentences null the registry blurb (the report keeps
it, with ``claims_supported: false`` + ``unsupported_claims``).

``judge`` is the offline venue_judge (model knowledge only, no web): notability
0-10, confidence, rationale, flags -> content/curation/<city>/venue_judge.jsonl,
cache-keyed like judge.py so re-runs are free.

Spend: harness.CostMeter -> content/spend/research-<city>-<ts>.json (+ judge-
labelled files for ``judge``); each report carries its own cost_usd.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

import crawl  # noqa: E402
import curation_store as store  # noqa: E402
import harness  # noqa: E402
import refresh  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402
from run_scrape import load_env  # noqa: E402

REPORT_SCHEMA = 1
REPORTS_DIR = tools.CONTENT_DIR / "venues" / "reports"
MODEL = "claude-sonnet-5"
EFFORT = "medium"
MAX_TOKENS = 16000
RERUN_DAYS = 365
CHARS_PER_TOKEN = 4
VENUE_INPUT_TOKENS = 120_000          # per-venue cap on compile input
CHUNK_TOKENS = 45_000                 # per compile call
PROFILE_LABELS = ("about", "contact_hours", "roster_index", "fairs", "artist_page", "news")
ARCHIVE_LABELS = ("exhibitions_archive", "exhibitions_current", "exhibition_page")
LABELS = ["about", "roster_index", "artist_page", "exhibitions_current", "exhibitions_archive",
          "exhibition_page", "news", "fairs", "contact_hours", "other"]
JUDGE_VARIANT = "venue_judge_v1"

Label = Literal["about", "roster_index", "artist_page", "exhibitions_current", "exhibitions_archive",
                "exhibition_page", "news", "fairs", "contact_hours", "other"]


# --- structured outputs ---------------------------------------------------------------

try:
    from pydantic import BaseModel, ConfigDict, Field, ValidationError
except ImportError:  # pragma: no cover - the venv has pydantic (anthropic depends on it)
    raise


def _schema(model_cls) -> dict:
    schema = model_cls.model_json_schema()
    try:
        from anthropic.lib._parse._transform import transform_schema  # SDK-private helper
        return transform_schema(schema)
    except Exception:  # noqa: BLE001
        return schema


class PageLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    i: int = Field(description="Row index from the inventory")
    label: Label


class FetchMore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(description="Absolute in-domain URL not in the inventory (pagination, index subpages)")
    label: Label


class TriageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pages: list[PageLabel] = Field(description="Only rows that are NOT 'other'; unlabelled rows are 'other'")
    fetch_more: list[FetchMore] = Field(description="Pages the site clearly has but the inventory lacks")
    site_notes: str = Field(description="One or two sentences: how the site is organised, what is missing")


class Location(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str
    address: str | None
    since: int | None
    current: bool


class RosterEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    status: Literal["represented", "exhibited", "estate"]
    source_url: str


class PressItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outlet: str
    year: int | None
    url: str | None


class ProfileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    about_text: str | None = Field(description="60-140 words, your own words, gallery-guide tone; null when the pages say nothing about the gallery itself")
    about_source_url: str | None
    founded_year: int | None
    founders: list[str]
    directors: list[str]
    locations: list[Location]
    program_focus: list[str] = Field(description="2-6 short tags: media, generations, regions, e.g. 'emerging LA painters', 'postwar Japanese photography'")
    hours_text: str | None = Field(description="Opening hours exactly as posted, one line")
    roster: list[RosterEntry]
    fairs_self_reported: list[str]
    memberships_self_reported: list[str]
    press_self_reported: list[PressItem]
    notes: str | None


class ExhibitionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None
    artists: list[str]
    start: str | None = Field(description="YYYY-MM-DD or null")
    end: str | None = Field(description="YYYY-MM-DD or null")
    year: int | None
    kind: Literal["solo", "group", "fair", "other"]
    source_url: str


class ExhibitionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exhibitions: list[ExhibitionOut]
    complete: bool = Field(description="false when the pages clearly list more shows than you returned")


class SentenceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentence: str
    supported: bool
    why: str


class CheckOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sentences: list[SentenceCheck]


class VenueJudgeFlags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    international_program: bool
    museum_track_record: bool
    artist_run: bool
    blue_chip: bool


class VenueJudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notability: Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    confidence: float
    rationale: str = Field(description="At most 100 words; say what you actually know about this venue")
    flags: VenueJudgeFlags


TRIAGE_SCHEMA = _schema(TriageOut)
PROFILE_SCHEMA = _schema(ProfileOut)
EXHIBITIONS_SCHEMA = _schema(ExhibitionsOut)
CHECK_SCHEMA = _schema(CheckOut)
VENUE_JUDGE_SCHEMA = _schema(VenueJudgeOut)


# --- prompts ------------------------------------------------------------------------------

TRIAGE_SYSTEM = """You are triaging the crawled page inventory of one art venue's website for a gallery guide.
Each inventory row is: index | url | page title | text characters | count of date strings on the page.
Label every page that matters; anything you do not label is "other".

Labels:
  about               the gallery's own story: history, mission, founders, directors, spaces
  roster_index        the page listing the artists the gallery represents or has shown
  artist_page         one artist's page on the gallery site (label a FEW representative ones only if there is no roster_index)
  exhibitions_current what is on view now / upcoming
  exhibitions_archive past exhibitions list (all years, all pages)
  exhibition_page     one exhibition's own page (label these only when there is no archive list, or the archive omits dates/artists)
  news                news / press page
  fairs               art fairs the gallery has done
  contact_hours       address, hours, contact
  other               everything else (do not list)

Read URLs and titles for structure, not for fixed path names: rosters live under /artists, /represented, /gallery-artists, /program, a language prefix, or a CMS slug, and archives under /exhibitions/past, /archive, /past, ?year=, /page/2 ... Use the character and date counts: a roster is a long page with few dates; an archive has many dates.

fetch_more: list absolute in-domain URLs that clearly exist but are missing from the inventory - paginated archive pages (/exhibitions/past?page=2, /page/3), per-year archive pages, a roster index behind a link - each with its label. Top-level pages the site navigation clearly links to (about, exhibitions, artists, contact) may be listed when the inventory lacks them. Never invent paths you have no evidence for; an empty list is fine.
"""

PROFILE_SYSTEM = """You are compiling a factual profile of one art venue for a gallery guide, from pages of its own website (labelled below). Rules:
- Facts only from the supplied text. Unknown -> null / empty list. Never guess a founding year.
- about_text: 60-140 words in your own plain words, informed gallery-guide tone: what the gallery is, when and by whom it was founded, where it is, what its program is known for. No marketing copy, no superlatives the pages do not support, no opening hours, no current show. Null when the pages give nothing about the gallery itself.
- roster: every artist the site lists as represented (status represented) or as estate; artists merely mentioned in past shows are NOT roster unless the site presents them in an artists list (then status exhibited). Names exactly as printed; source_url = the page they came from.
- locations: every space the gallery says it runs, including other cities; current=false for closed spaces.
- hours_text: as posted, one line; program_focus: 2-6 tags.
- fairs_self_reported / memberships_self_reported / press_self_reported: only what the pages state.
"""

EXHIBITIONS_SYSTEM = """You are extracting the exhibition history of one art venue from pages of its own website (labelled below). Return EVERY exhibition the pages list, oldest to newest, one entry each:
- title (null for untitled), artists (names as printed; empty for unnamed group shows), start/end as YYYY-MM-DD when the page gives full dates else null, year (always fill when any date or year is visible), kind: solo (one artist), group, fair (a fair booth), other.
- Do not merge or summarise; do not invent dates; do not include exhibitions at other venues that are merely mentioned in news.
- complete=false only if the pages plainly list more exhibitions than you managed to return.
"""

CHECK_SYSTEM = """You are fact-checking a short gallery blurb against the gallery's own web pages (supplied). For EACH sentence of the blurb decide whether the pages support it (supported=true) or not (false: contradicted, or not stated anywhere). Paraphrase is fine; invented facts, years, names or claims of importance the pages do not make are unsupported. Return one entry per sentence, in order.
"""

VENUE_JUDGE_SYSTEM = """You rate the standing of art venues in {city_name} for a gallery guide, from your own knowledge plus the facts supplied. Scale (relative within {city_name}):
  0-2  unknown / retail / hobby space; nothing beyond its own site
  3-4  a small local gallery or project space with a modest, real program
  5-6  an established local gallery: regular program, some press, local fairs
  7-8  a leading gallery with an international program, major fairs (Basel, Frieze, Paris+, Gendai...), museum-level artists; or a serious kunsthalle / nonprofit
  9-10 blue-chip international gallery or a major museum
confidence 0-1 reflects how well you actually know this venue; unknown to you and thin facts -> low confidence and a score near the facts.
flags: international_program (spaces or a program abroad), museum_track_record (artists with museum shows / the venue itself a museum), artist_run, blue_chip.
rationale: at most 100 words, concrete, no restating of the supplied facts.
"""

GAPFILL_SYSTEM = """You research one art venue for a gallery guide. Its own website gave us too little. Use web_search and web_fetch (at most 6 each) on independent sources - Artsy, Ocula, Tokyo Art Beat, Contemporary Art Daily, press, Wikipedia, art-fair exhibitor pages - and then answer in plain text with, in this order, each on its own line prefixed by the field name:
ABOUT: 60-140 words in your own words about the gallery (founding, founders, location, program). Write "ABOUT: unknown" if you found nothing reliable.
FOUNDED: year or unknown
FOUNDERS: names or unknown
LOCATIONS: city - address (since year) ; ...
ROSTER: represented artists, comma-separated, or unknown
FAIRS: fairs the gallery has exhibited at, or unknown
MEMBERSHIPS: associations / curated lists, or unknown
SOURCES: the URLs you relied on
Only state what a source you read says. Do not pad. Stop when done."""


# --- helpers --------------------------------------------------------------------------------

class Meter:
    """harness.CostMeter behind a lock; ``record`` returns the dollar delta."""

    def __init__(self, label: str, model: str = MODEL):
        self.m = harness.CostMeter(label, model=model)
        self.lock = threading.Lock()

    def record(self, usage) -> float:
        with self.lock:
            before = self.m.dollars
            self.m.record(usage)
            self.m.save()
            return self.m.dollars - before

    @property
    def dollars(self) -> float:
        return self.m.dollars


def _usage_dict(usage) -> dict:
    return {"input": usage.input_tokens or 0, "output": usage.output_tokens or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0}


def structured_call(client, system: str, user: str, schema: dict, model_cls, meter: Meter,
                    model: str = MODEL, effort: str = EFFORT, max_tokens: int = MAX_TOKENS):
    """One offline Messages call with a cached system block and json_schema output.
    Returns (parsed model | None, error | None, cost)."""
    req = {
        "model": model, "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }
    if harness.MODELS.get(model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = effort
    try:
        msg = client.messages.create(**req)
    except Exception as exc:  # noqa: BLE001 - api errors become a per-venue failure, not a crash
        return None, f"api {type(exc).__name__}: {str(exc)[:200]}", 0.0
    cost = meter.record(msg.usage)
    if msg.stop_reason == "refusal":
        return None, "refusal", cost
    if msg.stop_reason == "max_tokens":
        return None, "max_tokens", cost
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return model_cls.model_validate_json(text), None, cost
    except ValidationError as exc:
        return None, f"validation: {str(exc)[:200]}", cost


def report_path(city: str, vid: str) -> Path:
    return REPORTS_DIR / city / f"{vid}.json"


def load_report(city: str, vid: str) -> dict | None:
    p = report_path(city, vid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def save_report(rep: dict) -> Path:
    p = report_path(rep["city"], rep["venue_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    return p


def _venue_ids_arg(spec: str | None) -> list[str] | None:
    if not spec:
        return None
    if spec.startswith("@"):
        return [ln.strip() for ln in Path(spec[1:]).read_text().splitlines() if ln.strip()]
    return [x.strip() for x in spec.split(",") if x.strip()]


def eligible(v: dict) -> bool:
    if not v.get("website"):
        return False
    if v.get("status") in ("closed", "duplicate", "out_of_scope"):
        return False
    ver = (v.get("verification") or {}).get("status")
    if ver == "flagged":
        return False
    return ver == "verified" or v.get("status") in ("active", "appointment_only")


def order_key(v: dict):
    rank = v.get("rank")
    tier = v.get("tier")
    return (0 if rank is not None else 1, rank if rank is not None else 0,
            0 if tier is not None else 1, tier or 0, v["id"])


# prestige evidence only: a directory/enumeration mention is listing evidence and
# earns a venue its place through the score percentile, not by itself
EVIDENCE_FEATURES = ("fairs", "curated_lists", "press", "venue_judge", "wiki")
SELECT_PCT = 10.0      # --select auto: at least this share of ungated venues, by prior score
SELECT_CAP = 250       # --select auto: never more than this many per pass


def auto_select(reg: dict, pct: float = SELECT_PCT, cap: int = SELECT_CAP, log=print) -> set[str]:
    """Research set from the PRIOR ranking (rank_venues apply must have run):
    every eligible venue with any prestige evidence (a fair, a curated list,
    press, a judge verdict, a Wikipedia page) UNION the top `pct` percent of eligible venues by score, capped at
    `cap`. A fixed N is brittle across markets: LA's evidence-bearing set is
    150 venues, Tokyo's 76, and in both the score histogram has a lump at
    0.10-0.15 that is nothing but Google opening hours — the cut sits above it."""
    el = [v for v in reg["venues"] if eligible(v) and v.get("score") is not None]
    if not el:
        return set()
    scores = sorted((v["score"] for v in el), reverse=True)
    bins = [0, 0.02, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 1.01]
    log(f"prior score distribution over {len(el)} eligible venue(s) (max {scores[0]:.3f}):")
    for a, b in zip(bins, bins[1:]):
        c = sum(1 for x in scores if a <= x < b)
        log(f"  [{a:.2f},{b:.2f}) {c:4d} {'#' * min(c // 5, 60)}")
    ev = {v["id"] for v in el if any(((v.get("features") or {}).get(f) or {}).get("value", 0) > 0
                                     for f in EVIDENCE_FEATURES)}
    n_pct = max(1, int(round(len(el) * pct / 100.0)))
    by_score = sorted(el, key=lambda v: (-(v["score"] or 0), v["id"]))
    top = {v["id"] for v in by_score[:n_pct]}
    chosen = ev | top
    floor = by_score[min(n_pct, len(by_score)) - 1]["score"]
    log(f"evidence-bearing {len(ev)} | top {pct:g}% = {n_pct} (score >= {floor:.3f}) | union {len(chosen)}"
        + (f" -> capped at {cap}" if len(chosen) > cap else ""))
    if len(chosen) > cap:
        chosen = {v["id"] for v in by_score if v["id"] in chosen}
        chosen = set(list(v["id"] for v in by_score if v["id"] in chosen)[:cap])
    return chosen


def select_venues(city: str, ids: list[str] | None, limit: int | None, force: bool,
                  today_ts: int | None = None, select: str | None = None,
                  select_pct: float = SELECT_PCT, select_cap: int = SELECT_CAP,
                  log=print) -> tuple[list[dict], list[tuple[str, str]]]:
    """(venues to research, [(id, why skipped)])."""
    reg = venues.load_registry(city)
    by_id = venues.index_by_id(reg)
    now = today_ts or int(time.time())
    picked, skipped = [], []
    if select == "auto" and not ids:
        keep = auto_select(reg, select_pct, select_cap, log=log)
        cands = [v for v in sorted(reg["venues"], key=order_key) if v["id"] in keep]
    else:
        cands = [by_id[i] for i in ids if i in by_id] if ids else sorted(reg["venues"], key=order_key)
    for i in (ids or []):
        if i not in by_id:
            skipped.append((i, "unknown id"))
    for v in cands:
        if not ids and not eligible(v):
            continue
        if ids and not v.get("website"):
            skipped.append((v["id"], "no website"))
            continue
        ts = (v.get("research") or {}).get("ts")
        if ts and now - ts < RERUN_DAYS * 86400 and not force:
            skipped.append((v["id"], f"researched {(now - ts) // 86400}d ago"))
            continue
        picked.append(v)
        if limit and len(picked) >= limit:
            break
    return picked, skipped


# --- triage + compile -------------------------------------------------------------------------

TRIAGE_BATCH = 120     # inventory rows per triage call
MIN_USABLE_PAGES = 2   # fewer readable pages than this = transport failure, retry later


def triage(client, idx: crawl.CrawlIndex, venue: dict, meter: Meter, max_rows: int = 400) -> tuple[dict[int, str], list[FetchMore], str, float, str | None]:
    """labels by inventory index, fetch_more, notes, cost, error.

    The inventory is labelled in batches: one label per page means a 300-page
    site overruns the output cap in a single call (`triage: max_tokens` left
    Mizuma with an empty report), and a batch that still overruns is halved."""
    lines = crawl.inventory_lines(idx, limit=max_rows)
    n = len(idx.ok_pages())
    labels: dict[int, str] = {}
    more: list[FetchMore] = []
    notes: list[str] = []
    cost = 0.0
    errs: list[str] = []

    def run(batch: list[str], depth: int = 0) -> None:
        nonlocal cost
        user = (f"VENUE: {venue['name']} ({venue.get('kind') or 'gallery'}), site {idx.site}\n"
                f"INVENTORY ({len(batch)} of {len(lines)} pages; index | url | title | chars | dates):\n"
                + "\n".join(batch) + "\n\nLabel these pages and list fetch_more.")
        out, err, c = structured_call(client, TRIAGE_SYSTEM, user, TRIAGE_SCHEMA, TriageOut, meter,
                                      max_tokens=8000)
        cost += c
        if err == "max_tokens" and len(batch) > 10 and depth < 5:
            mid = len(batch) // 2
            run(batch[:mid], depth + 1)
            run(batch[mid:], depth + 1)
            return
        if err or out is None:
            errs.append(err or "no output")
            return
        for p in out.pages:
            if 0 <= p.i < n and p.label != "other":
                labels[p.i] = p.label
        more.extend(out.fetch_more)
        if out.site_notes:
            notes.append(out.site_notes)

    for i in range(0, len(lines), TRIAGE_BATCH):
        run(lines[i:i + TRIAGE_BATCH])
    if not labels and errs:
        return {}, [], "", cost, errs[0]
    return labels, more, " ".join(notes)[:1200], cost, (errs[0] if errs else None)


def _labelled_pages(idx: crawl.CrawlIndex, labels: dict[int, str], extra: dict[str, str]) -> list[tuple[str, crawl.CrawlPage]]:
    """[(label, page)] for every labelled page (inventory index labels + URL labels
    for pages fetched on request)."""
    pages = idx.ok_pages()
    out = []
    seen = set()
    for i, lab in labels.items():
        if i < len(pages):
            out.append((lab, pages[i]))
            seen.add(pages[i].url)
    for p in pages:
        lab = extra.get(p.url)
        if lab and p.url not in seen:
            out.append((lab, p))
    return out


def _page_block(label: str, page: crawl.CrawlPage, text: str, cap: int) -> str:
    body = text[:cap]
    return f"=== [{label}] {page.url}\nTITLE: {page.title or '-'}\n{body}\n"


def build_profile_input(idx: crawl.CrawlIndex, pages: list[tuple[str, crawl.CrawlPage]], budget_chars: int) -> str:
    order = {lab: i for i, lab in enumerate(PROFILE_LABELS)}
    chosen = sorted([lp for lp in pages if lp[0] in order], key=lambda lp: (order[lp[0]], -lp[1].text_chars))
    per_label_cap = {"artist_page": 6, "news": 2}
    counts: dict[str, int] = {}
    blocks, used = [], 0
    for lab, p in chosen:
        if counts.get(lab, 0) >= per_label_cap.get(lab, 99):
            continue
        text = idx.text(p)
        if not text.strip():
            continue
        cap = min(len(text), max(2000, budget_chars - used))
        if used >= budget_chars:
            break
        blocks.append(_page_block(lab, p, text, cap))
        used += min(len(text), cap)
        counts[lab] = counts.get(lab, 0) + 1
    if not blocks:   # nothing labelled: fall back to the homepage + the longest few pages
        pages_all = sorted(idx.ok_pages(), key=lambda p: (p.depth, -p.text_chars))[:4]
        for p in pages_all:
            blocks.append(_page_block("unlabelled", p, idx.text(p), min(12000, budget_chars // 4)))
    return "\n".join(blocks)


def build_archive_chunks(idx: crawl.CrawlIndex, pages: list[tuple[str, crawl.CrawlPage]], chunk_chars: int,
                         budget_chars: int) -> list[str]:
    order = {lab: i for i, lab in enumerate(ARCHIVE_LABELS)}
    chosen = sorted([lp for lp in pages if lp[0] in order], key=lambda lp: (order[lp[0]], lp[1].url))
    archive_chars = sum(p.text_chars for lab, p in chosen if lab == "exhibitions_archive")
    chunks, cur, cur_len, used = [], [], 0, 0
    for lab, p in chosen:
        # single exhibition pages only matter when the archive itself is thin
        if lab == "exhibition_page" and archive_chars > 20_000:
            continue
        text = idx.text(p)
        if not text.strip():
            continue
        cap = 8000 if lab == "exhibition_page" else 200_000
        block = _page_block(lab, p, text, cap)
        if used + len(block) > budget_chars:
            break
        if cur and cur_len + len(block) > chunk_chars:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(block)
        cur_len += len(block)
        used += len(block)
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def _ex_key(e: dict) -> str:
    t = re.sub(r"\W+", " ", (e.get("title") or "").lower()).strip()
    a = re.sub(r"\W+", " ", (e.get("artists") or [""])[0].lower()).strip() if e.get("artists") else ""
    y = e.get("year") or (e.get("start") or "")[:4]
    return f"{t}|{a}|{y}"


def merge_exhibitions(lists: list[list[dict]]) -> list[dict]:
    out: dict[str, dict] = {}
    for lst in lists:
        for e in lst:
            k = _ex_key(e)
            if k in out:
                cur = out[k]
                for f in ("title", "start", "end", "year"):
                    if not cur.get(f) and e.get(f):
                        cur[f] = e[f]
                if len(e.get("artists") or []) > len(cur.get("artists") or []):
                    cur["artists"] = e["artists"]
            else:
                out[k] = dict(e)
    def sk(e):
        return (e.get("start") or (f"{e['year']}-00-00" if e.get("year") else "9999"), e.get("title") or "")
    return sorted(out.values(), key=sk)


def facts_from_report(rep: dict) -> dict:
    ex = rep.get("exhibitions") or []
    years = sorted({int(e["year"]) for e in ex if e.get("year")} |
                   {int(e["start"][:4]) for e in ex if e.get("start") and e["start"][:4].isdigit()})
    first_year = years[0] if years else None
    span = (max(years) - first_year + 1) if years else None
    solo = sum(1 for e in ex if e.get("kind") == "solo")
    roster = [r for r in rep.get("roster") or [] if r.get("status") in ("represented", "estate")]
    return {
        "founded_year": rep.get("founded_year"),
        "founders": rep.get("founders") or [],
        "locations_elsewhere": [l["city"] for l in rep.get("locations") or []
                                if l.get("city") and l.get("current", True)
                                and tools._norm_venue(l["city"]) != tools._norm_venue(_city_name(rep["city"]))],
        "program_focus": rep.get("program_focus") or [],
        "roster_count": len(roster) if (rep.get("roster") is not None) else None,
        "exhibitions_total": len(ex) if ex else None,
        "exhibitions_per_year": round(len(ex) / span, 2) if ex and span else None,
        "first_exhibition_year": first_year,
        "solo_share": round(solo / len(ex), 2) if ex else None,
    }


def _city_name(city: str) -> str:
    return (CITIES.get(city) or {}).get("display_name", city)


# --- gap-fill (agentic) ------------------------------------------------------------------------

def gapfill(client, venue: dict, city: str, meter: Meter, max_uses: int = 6, max_iter: int = 12) -> tuple[str, list[str], float]:
    """Web-search session -> (final text, urls fetched, cost). Container-id echo
    per the 20260209 tools (see harness.run_city)."""
    import anthropic
    mcfg = harness.MODELS[MODEL]
    server_tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": max_uses,
         "max_content_tokens": 8000},
    ]
    facts = f"VENUE: {venue['name']}\nCITY: {_city_name(city)}\nADDRESS: {venue.get('address') or '?'}\nWEBSITE: {venue.get('website')}\n"
    messages = [{"role": "user", "content": facts + "\nResearch this venue and answer in the fixed format."}]
    container_id = None
    final_text, urls, cost = "", [], 0.0
    for _ in range(max_iter):
        kwargs: dict = {"model": MODEL, "max_tokens": 6000,
                        "system": [{"type": "text", "text": GAPFILL_SYSTEM}],
                        "messages": messages, "tools": server_tools}
        if container_id:
            kwargs["container"] = container_id
        if mcfg.get("supports_effort"):
            kwargs["output_config"] = {"effort": EFFORT}
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.BadRequestError as exc:
            if container_id and "container" in str(exc).lower():
                container_id = None
                kwargs.pop("container", None)
                resp = client.messages.create(**kwargs)
            else:
                return final_text, urls, cost
        except Exception:  # noqa: BLE001
            return final_text, urls, cost
        cost += meter.record(resp.usage)
        container = getattr(resp, "container", None)
        if container is not None and getattr(container, "id", None):
            container_id = container.id
        for block in resp.content:
            if block.type == "text" and block.text.strip():
                final_text = block.text.strip()
            elif block.type == "server_tool_use" and block.name == "web_fetch":
                inp = block.input if isinstance(block.input, dict) else {}
                if inp.get("url"):
                    urls.append(inp["url"])
        if resp.stop_reason in ("end_turn", "stop_sequence", "refusal", "max_tokens"):
            break
        # pause_turn: continue with the assistant turn appended
        messages.append({"role": "assistant", "content": resp.content})
    return final_text, urls, cost


def structure_gapfill(client, venue: dict, text: str, meter: Meter) -> tuple[ProfileOut | None, str | None, float]:
    user = (f"VENUE: {venue['name']}\nThe following are research notes gathered from independent web "
            f"sources (not the gallery's own site). Turn them into the profile; leave anything the "
            f"notes do not state as null/empty.\n\n{text}")
    return structured_call(client, PROFILE_SYSTEM, user, PROFILE_SCHEMA, ProfileOut, meter, max_tokens=6000)


# --- one venue ----------------------------------------------------------------------------------

def research_one(client, city: str, venue: dict, meter: Meter, args, session: str,
                 fetcher: refresh.Fetcher | None = None, log=print) -> dict:
    vid = venue["id"]
    t0 = time.time()
    rep: dict = {"schema": REPORT_SCHEMA, "city": city, "venue_id": vid, "name": venue["name"],
                 "website": venue.get("website"), "generated_ts": int(t0), "model": MODEL,
                 "cost_usd": 0.0, "crawl": {}, "about_text": None, "source_kind": None,
                 "source_urls": [], "founded_year": None, "founders": [], "directors": [],
                 "locations": [], "program_focus": [], "hours_text": None, "roster": [],
                 "exhibitions": [], "fairs_self_reported": [], "memberships_self_reported": [],
                 "press_self_reported": [], "claims_supported": True, "unsupported_claims": [],
                 "notes": None, "triage": {"labels": {}, "site_notes": "", "fetch_more": []},
                 "errors": []}
    cost = 0.0
    # 1. crawl
    idx = crawl.crawl_site(city, venue, max_pages=args.max_pages, fetcher=fetcher,
                           render=not args.no_render, session=session)
    rep["crawl"] = {"pages": len(idx.ok_pages()), "sitemap": idx.sitemap, "rendered": idx.rendered,
                    "truncated": idx.truncated, "index_path": str(idx.path.relative_to(crawl.SCRAPER_DIR))}
    log(f"  [{vid}] crawl: {len(idx.ok_pages())} pages (sitemap={idx.sitemap} rendered={idx.rendered})")
    labels: dict[int, str] = {}
    extra_labels: dict[str, str] = {}
    if idx.ok_pages():
        # 2. triage
        labels, more, notes, c, err = triage(client, idx, venue, meter)
        cost += c
        if err:
            rep["errors"].append(f"triage: {err}")
        rep["triage"]["site_notes"] = notes
        wanted = []
        domain = venues.registrable_domain(idx.site) or ""
        for fm in more[: args.max_fetch_more]:
            n = crawl.normalize_url(fm.url, idx.site)
            if n and crawl.same_site(n, domain):
                wanted.append(n)
                extra_labels[n] = fm.label
        rep["triage"]["fetch_more"] = wanted
        if wanted:
            idx = crawl.crawl_site(city, venue, max_pages=args.max_pages + len(wanted), fetcher=fetcher,
                                   render=not args.no_render, session=session, extra_urls=wanted)
            # the inventory grew: re-map index labels through URLs
        pages = idx.ok_pages()
        url_labels = {pages[i].url: lab for i, lab in labels.items() if i < len(pages)}
        url_labels.update(extra_labels)
        rep["triage"]["labels"] = url_labels
        labelled = [(url_labels[p.url], p) for p in pages if p.url in url_labels]
        log(f"  [{vid}] triage: {len(labelled)} labelled, {len(wanted)} requested; {notes[:100]}")
        # 3. compile: profile
        budget = VENUE_INPUT_TOKENS * CHARS_PER_TOKEN
        prof_in = build_profile_input(idx, labelled, min(budget // 2, CHUNK_TOKENS * CHARS_PER_TOKEN))
        prof, err, c = structured_call(client, PROFILE_SYSTEM,
                                       f"VENUE: {venue['name']} - {_city_name(city)}\nSITE: {idx.site}\n\n{prof_in}",
                                       PROFILE_SCHEMA, ProfileOut, meter)
        cost += c
        if err or prof is None:
            rep["errors"].append(f"profile: {err}")
        else:
            d = prof.model_dump()
            rep.update({k: d[k] for k in ("about_text", "founded_year", "founders", "directors", "locations",
                                          "program_focus", "hours_text", "roster", "fairs_self_reported",
                                          "memberships_self_reported", "press_self_reported", "notes")})
            if d.get("about_text"):
                rep["source_kind"] = "official"
                rep["source_urls"] = [u for u in [d.get("about_source_url")] if u] or \
                    [p.url for lab, p in labelled if lab == "about"][:1] or [idx.site]
        # 3b. exhibitions
        used = len(prof_in)
        chunks = build_archive_chunks(idx, labelled, CHUNK_TOKENS * CHARS_PER_TOKEN, budget - used)
        lists = []

        def extract(ch: str, depth: int = 0) -> float:
            """One structured call per chunk; a chunk whose exhibition list
            overruns max_tokens (Regen Projects' 30-year archive) is split at
            its middle line and both halves retried, down to ~4K chars."""
            ex, err, c = structured_call(client, EXHIBITIONS_SYSTEM,
                                         f"VENUE: {venue['name']}\n\n{ch}", EXHIBITIONS_SCHEMA, ExhibitionsOut, meter)
            if err == "max_tokens" and len(ch) > 4000 and depth < 5:
                lines = ch.splitlines()
                mid = len(lines) // 2
                return c + extract("\n".join(lines[:mid]), depth + 1) + extract("\n".join(lines[mid:]), depth + 1)
            if err or ex is None:
                rep["errors"].append(f"exhibitions: {err}")
                return c
            lists.append([e.model_dump() for e in ex.exhibitions])
            return c

        for ch in chunks:
            cost += extract(ch)
        rep["exhibitions"] = merge_exhibitions(lists)
        log(f"  [{vid}] compile: roster {len(rep['roster'])}, exhibitions {len(rep['exhibitions'])} "
            f"from {len(chunks)} chunk(s), founded {rep['founded_year']}")
    else:
        rep["errors"].append("crawl: no readable pages")
    # 4. gap-fill
    gap = bool(args.gapfill) and (not rep["about_text"] or rep["founded_year"] is None or not rep["roster"])
    if gap:
        text, urls, c = gapfill(client, venue, city, meter)
        cost += c
        if text:
            prof, err, c2 = structure_gapfill(client, venue, text, meter)
            cost += c2
            if prof is not None:
                d = prof.model_dump()
                if not rep["about_text"] and d.get("about_text"):
                    rep["about_text"], rep["source_kind"] = d["about_text"], "secondary"
                    rep["source_urls"] = urls[:6]
                for k in ("founded_year",):
                    if rep.get(k) is None and d.get(k) is not None:
                        rep[k] = d[k]
                for k in ("founders", "roster", "fairs_self_reported", "memberships_self_reported", "locations"):
                    if not rep.get(k) and d.get(k):
                        rep[k] = d[k]
                rep["gapfill"] = {"urls": urls, "notes": text[:4000]}
            else:
                rep["errors"].append(f"gapfill-structure: {err}")
        log(f"  [{vid}] gapfill: {len(urls)} fetch(es), ${c:.3f}")
    rep["cost_usd"] = round(cost, 4)
    rep["duration_s"] = round(time.time() - t0, 1)
    p = save_report(rep)
    # 5. registry
    facts = facts_from_report(rep)
    with venues.locked_registry(city) as reg:
        v = venues.index_by_id(reg).get(vid)
        if v is not None:
            venues.ensure_v2(v)
            v["about"].update({
                "text": rep["about_text"], "source_kind": rep["source_kind"],
                "source_url": (rep["source_urls"] or [None])[0],
                "evidence_path": next((pg.evidence_path for pg in idx.ok_pages()
                                       if rep["triage"]["labels"].get(pg.url) == "about"), None),
                "written_ts": int(time.time()) if rep["about_text"] else None,
                "model": MODEL if rep["about_text"] else None,
            })
            v["facts"].update(facts)
            # A crawl that read almost nothing is a transport failure, not a
            # finding about the gallery (five venues were banked for a year on
            # ConnectTimeouts during a loaded run). Leave `ts` null so the next
            # pass retries; the report and its errors are still written.
            usable = len(idx.ok_pages()) >= MIN_USABLE_PAGES and (
                rep["about_text"] or rep["roster"] or rep["exhibitions"])
            v["research"].update({"ts": int(time.time()) if usable else None,
                                  "crawl_pages": rep["crawl"].get("pages"),
                                  "triaged": len(rep["triage"]["labels"]),
                                  "report_path": str(p.relative_to(tools.CONTENT_DIR.parent)),
                                  "cost_usd": rep["cost_usd"], "gapfill": gap,
                                  "incomplete": not usable})
            if not usable:
                rep["errors"].append("not banked: too little readable content, will retry")
            if rep.get("hours_text") and not v.get("hours"):
                v["hours"] = [rep["hours_text"]]
    log(f"  [{vid}] done ${cost:.3f} in {rep['duration_s']}s -> {p}")
    return rep


# --- commands -------------------------------------------------------------------------------------

def cmd_run(args) -> int:
    city = args.city
    ids = _venue_ids_arg(args.venue_ids)
    picked, skipped = select_venues(city, ids, args.limit, args.force, select=args.select,
                                    select_pct=args.select_pct, select_cap=args.select_cap, log=print)
    for vid, why in skipped:
        print(f"  skip {vid}: {why}")
    print(f"{city}: {len(picked)} venue(s) to research" + (f" (budget ${args.budget:.2f})" if args.budget else ""))
    if args.dry_run or not picked:
        for v in picked:
            print(f"  {v['id']:<36} {v.get('website')}")
        return 0
    load_env()
    import anthropic
    client = anthropic.Anthropic(max_retries=3)
    ts = int(time.time())
    session = f"research-{city}-{ts}"
    meter = Meter(session)
    fetcher = refresh.Fetcher()
    stop = threading.Event()
    lock = threading.Lock()
    done: list[dict] = []

    def log(msg: str) -> None:
        with lock:
            print(msg, flush=True)

    def one(v: dict) -> None:
        if stop.is_set():
            return
        if args.budget and meter.dollars >= args.budget:
            stop.set()
            log(f"  budget ${args.budget:.2f} reached; not starting {v['id']}")
            return
        try:
            rep = research_one(client, city, v, meter, args, session, fetcher=fetcher, log=log)
            done.append(rep)
        except Exception as exc:  # noqa: BLE001
            log(f"  [{v['id']}] FAILED: {type(exc).__name__}: {str(exc)[:200]}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(one, picked))
    meter.m.save()
    print(f"researched {len(done)}/{len(picked)}; ${meter.dollars:.3f} -> content/spend/{session}.json")
    return 0


def _evidence_for_check(city: str, vid: str, rep: dict) -> str:
    idx = crawl.CrawlIndex.load(city, vid)
    if not idx:
        return ""
    labels = (rep.get("triage") or {}).get("labels") or {}
    want = {u for u, l in labels.items() if l in ("about", "contact_hours", "news", "fairs")} | set(rep.get("source_urls") or [])
    parts = []
    for p in idx.ok_pages():
        if p.url in want or p.depth == 0:
            parts.append(f"=== {p.url}\n{idx.text(p)[:30000]}")
    return "\n".join(parts)[:200_000]


def cmd_check(args) -> int:
    city = args.city
    ids = _venue_ids_arg(args.venue_ids)
    reg = venues.load_registry(city)
    todo = []
    for v in reg["venues"]:
        if ids and v["id"] not in ids:
            continue
        rep = load_report(city, v["id"])
        if rep and rep.get("about_text") and rep.get("source_kind") == "official":
            todo.append((v, rep))
    if args.limit:
        todo = todo[: args.limit]
    print(f"{city}: checking {len(todo)} blurb(s)")
    if not todo:
        return 0
    load_env()
    import anthropic
    client = anthropic.Anthropic(max_retries=3)
    meter = Meter(f"research-check-{city}-{int(time.time())}")
    failed = 0
    for v, rep in todo:
        ev = _evidence_for_check(city, v["id"], rep)
        if not ev:
            print(f"  {v['id']}: no evidence on disk, skipped")
            continue
        user = f"BLURB:\n{rep['about_text']}\n\nPAGES:\n{ev}"
        out, err, _ = structured_call(client, CHECK_SYSTEM, user, CHECK_SCHEMA, CheckOut, meter, max_tokens=4000)
        if err or out is None:
            print(f"  {v['id']}: check failed ({err})")
            continue
        bad = [s.sentence for s in out.sentences if not s.supported]
        rep["claims_supported"] = not bad
        rep["unsupported_claims"] = bad
        rep["checked_ts"] = int(time.time())
        save_report(rep)
        with venues.locked_registry(city) as r2:
            vv = venues.index_by_id(r2).get(v["id"])
            if vv is not None:
                venues.ensure_v2(vv)
                vv["about"]["text"] = None if bad else rep["about_text"]
                vv["about"]["checked_ts"] = rep["checked_ts"]
        failed += bool(bad)
        print(f"  {v['id']:<36} {'OK' if not bad else 'UNSUPPORTED ' + str(len(bad))}"
              + (f"  e.g. {bad[0][:90]}" if bad else ""))
    meter.m.save()
    print(f"checked {len(todo)}, {failed} with unsupported claims; ${meter.dollars:.3f}")
    return 0


# --- venue judge -------------------------------------------------------------------------------------

def venue_judge_file(city: str) -> Path:
    return store.city_dir(city) / "venue_judge.jsonl"


def load_venue_judge(city: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in store.read_jsonl(venue_judge_file(city)):
        if r.get("venue_id"):
            out[r["venue_id"]] = r
    return out


def judge_prompt_hash() -> str:
    return hashlib.sha1(VENUE_JUDGE_SYSTEM.encode()).hexdigest()


def judge_user_message(v: dict, rep: dict | None) -> str:
    facts = v.get("facts") or {}
    about = (v.get("about") or {}).get("text") or (rep or {}).get("about_text") or "(none)"
    roster = [r["name"] for r in (rep or {}).get("roster") or []][:25]
    lines = [
        f"VENUE {v['id']}: {v['name']}",
        f"kind: {v.get('kind')}; neighborhood: {v.get('neighborhood') or '?'}; address: {v.get('address') or '?'}",
        f"website: {v.get('website') or '?'}; status: {v.get('status')}",
        f"founded: {facts.get('founded_year') or '?'}; founders: {', '.join(facts.get('founders') or []) or '?'}",
        f"other locations: {', '.join(facts.get('locations_elsewhere') or []) or 'none known'}",
        f"program: {', '.join(facts.get('program_focus') or []) or '?'}",
        f"roster size: {facts.get('roster_count') if facts.get('roster_count') is not None else '?'}; "
        f"exhibitions on record: {facts.get('exhibitions_total') or '?'} ({facts.get('exhibitions_per_year') or '?'}/yr since {facts.get('first_exhibition_year') or '?'})",
        f"fairs (self-reported): {', '.join((rep or {}).get('fairs_self_reported') or []) or 'none stated'}",
        f"hours: {'; '.join(v.get('hours') or []) or '?'}",
        "",
        "ABOUT (from its own site or secondary sources - a claim, not evidence):",
        about,
        "",
        f"ROSTER SAMPLE: {', '.join(roster) or '(none)'}",
        "",
        "Return the verdict JSON.",
    ]
    return "\n".join(lines)


def judge_evidence_hash(v: dict, rep: dict | None) -> str:
    return hashlib.sha256(judge_user_message(v, rep).encode()).hexdigest()


def cmd_judge(args) -> int:
    city = args.city
    ids = _venue_ids_arg(args.venue_ids)
    reg = venues.load_registry(city)
    latest = load_venue_judge(city)
    phash = judge_prompt_hash()
    jobs, skipped = [], 0
    cands = [v for v in sorted(reg["venues"], key=order_key)
             if (ids and v["id"] in ids) or (not ids and eligible(v))]
    for v in cands:
        rep = load_report(city, v["id"])
        ehash = judge_evidence_hash(v, rep)
        key = hashlib.sha256(f"{v['id']}|{JUDGE_VARIANT}|{phash}|{MODEL}|{EFFORT}|{ehash}".encode()).hexdigest()
        prev = latest.get(v["id"])
        if prev and prev.get("cache_key") == key and not args.force:
            skipped += 1
            continue
        jobs.append((v, rep, key, ehash))
        if args.limit and len(jobs) >= args.limit:
            break
    print(f"{city}: {len(jobs)} venue(s) to judge, {skipped} cached")
    if not jobs:
        return 0
    load_env()
    import anthropic
    client = anthropic.Anthropic(max_retries=3)
    ts = int(time.time())
    meter = Meter(f"judge-{city}-{JUDGE_VARIANT}-{MODEL}-{ts}")
    system = VENUE_JUDGE_SYSTEM.replace("{city_name}", _city_name(city))
    lock = threading.Lock()

    def one(job):
        v, rep, key, ehash = job
        out, err, cost = structured_call(client, system, judge_user_message(v, rep), VENUE_JUDGE_SCHEMA,
                                         VenueJudgeOut, meter, max_tokens=2000)
        if err or out is None:
            with lock:
                print(f"  {v['id']:<36} FAILED {err}")
            return
        row = {"ts": int(time.time()), "city": city, "venue_id": v["id"], "variant": JUDGE_VARIANT,
               "prompt_hash": phash, "model": MODEL, "effort": EFFORT, "evidence_hash": ehash,
               "cache_key": key, "run_id": meter.m.label, "notability": out.notability,
               "confidence": round(max(0.0, min(1.0, out.confidence)), 3),
               "rationale": out.rationale.strip(), "flags": out.flags.model_dump(),
               "cost_usd": round(cost, 5)}
        store.append_row(venue_judge_file(city), row)
        with lock:
            print(f"  {v['id']:<36} notability {out.notability:>2}  conf {row['confidence']:.2f}  ${cost:.4f}  {out.rationale[:70]}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(one, jobs[:1]))
        list(pool.map(one, jobs[1:]))
    meter.m.save()
    print(f"judged {len(jobs)}; ${meter.dollars:.3f}")
    return 0


def cmd_report(args) -> int:
    city = args.city
    d = REPORTS_DIR / city
    reps = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            reps.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    print(f"{city}: {len(reps)} report(s)")
    print(f"{'venue':<34} {'pages':>5} {'lab':>4} {'roster':>6} {'exhib':>5} {'founded':>7} {'src':<9} {'cost':>6}  errors")
    total = 0.0
    for r in reps:
        total += r.get("cost_usd") or 0
        print(f"{r['venue_id'][:34]:<34} {(r.get('crawl') or {}).get('pages', 0):>5} "
              f"{len((r.get('triage') or {}).get('labels') or {}):>4} {len(r.get('roster') or []):>6} "
              f"{len(r.get('exhibitions') or []):>5} {str(r.get('founded_year') or '-'):>7} "
              f"{str(r.get('source_kind') or '-'):<9} {r.get('cost_usd', 0):>6.3f}  {'; '.join(r.get('errors') or [])[:60]}")
    print(f"total ${total:.3f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--city", required=True, choices=sorted(CITIES))
        p.add_argument("--venue-ids", help="a,b or @file")
        p.add_argument("--limit", type=int)
        p.add_argument("--select", choices=["auto"], default=None,
                       help="auto: evidence-bearing venues + top --select-pct%% by prior score, capped")
        p.add_argument("--select-pct", type=float, default=SELECT_PCT)
        p.add_argument("--select-cap", type=int, default=SELECT_CAP)

    r = sub.add_parser("run"); common(r)
    r.add_argument("--max-pages", type=int, default=300)
    r.add_argument("--max-fetch-more", type=int, default=40)
    r.add_argument("--gapfill", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--budget", type=float)
    r.add_argument("--no-render", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    c = sub.add_parser("check"); common(c)
    j = sub.add_parser("judge"); common(j)
    j.add_argument("--workers", type=int, default=4)
    j.add_argument("--force", action="store_true")
    rp = sub.add_parser("report"); rp.add_argument("--city", required=True, choices=sorted(CITIES))
    args = ap.parse_args(argv)
    return {"run": cmd_run, "check": cmd_check, "judge": cmd_judge, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
