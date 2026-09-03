"""LLM judge for curation (plan 2.5): one structured verdict per (show,
rubric variant, model), both ``claude-sonnet-5`` and ``claude-opus-5`` as A/B
arms, plus the compare report that surfaces the decision-relevant disagreements.

    python judge.py run     --city los-angeles --variant judge_v1
                            [--models claude-sonnet-5,claude-opus-5] [--limit N] [--slugs a,b]
                            [--batch] [--force] [--workers 4] [--effort high] [--dry-run]
    python judge.py compare --city los-angeles --variant judge_v1
                            [--params p.json] [--snapshot latest|<id>] [--top 10]

Offline, no web tools. Every request = cached system prompt (rubric + goal +
city-wide inventory) + one user message (title, artist, venue + registry tier,
dates, the agent-written description, matched signal snippets labelled
[S1]..[Sn], keyword hits). Structured output via ``output_config.format`` with
the ``JudgeVerdict`` pydantic schema; the same request dict feeds the sync path
(``messages.create`` in a thread pool) and ``--batch`` (Message Batches API).

Cache key ``sha256(slug|variant|prompt_hash|model|effort|evidence_hash)`` with
``evidence_hash = sha256(description|dates|sorted signal dedupe_keys|venue
tier)``: a show whose latest ``judge.jsonl`` row (per variant+model) carries the
same key is never re-judged unless ``--force``.

Writes: ``content/curation/<city>/judge.jsonl`` rows (plan 2.1 judge schema),
``content/spend/judge-<city>-<variant>-<model>-<ts>.json`` (CostMeter),
``runs.jsonl`` rows (stage ``judge``), and for ``compare``
``content/spend/reports/judge-compare-<city>.md`` + ``judge_compare`` JSON
(``content/curation/<city>/judge_compare.json`` and the copy
``content/spend/reports/judge-compare-<city>.json`` that ``curate.build_report``
embeds in the dashboard data).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curate  # noqa: E402
import curation_store as store  # noqa: E402
import harness  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402
from curation_keywords import merge_hits, scan_keywords  # noqa: E402
from run_scrape import load_env  # noqa: E402

DEFAULT_MODELS = ["claude-sonnet-5", "claude-opus-5"]
DEFAULT_EFFORT = "high"
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
MAX_TOKENS = 4000
BATCH_POLL_S = 15
RATIONALE_MAX_WORDS = 120
SUB_SCORES = ["artist_significance", "venue_significance", "critical_reception",
              "ambition_novelty", "timeliness"]
ARMS = ("sonnet", "opus", "mean")   # curate's params.judge.model tokens


# --- verdict schema -------------------------------------------------------------

Score = Literal[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


class SubScores(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artist_significance: Score
    venue_significance: Score
    critical_reception: Score
    ambition_novelty: Score
    timeliness: Score


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scores: SubScores
    overall: Score = Field(description="Holistic 0-10 call against the goal, not an average of the sub-scores")
    confidence: float = Field(description="0-1: how well the supplied evidence supports the scores")
    rationale: str = Field(description="At most 120 words; cite [S#] labels whenever signals were supplied")
    evidence_used: list[str] = Field(description="Inputs that drove the verdict, e.g. 'S1', 'description', 'venue tier', 'prior knowledge: artist'")
    red_flags: list[str] = Field(description="Concerns a human should check before featuring: hype without evidence, ended/ambiguous dates, unplaceable venue, unnamed group show")


def verdict_schema() -> dict:
    """The JSON schema sent as ``output_config.format``. Pydantic's output is
    already API-compliant (integer enums, $defs, additionalProperties:false);
    the SDK transform ``messages.parse`` applies is used when importable."""
    schema = JudgeVerdict.model_json_schema()
    try:
        from anthropic.lib._parse._transform import transform_schema  # SDK-private helper
        return transform_schema(schema)
    except Exception:  # noqa: BLE001 - fall back to the raw pydantic schema
        return schema


VERDICT_SCHEMA = verdict_schema()


# --- prompt variants -------------------------------------------------------------

JUDGE_V1_SYSTEM = """You are the exhibition judge for GalleryBrowser's {city_name} feed.

GOAL
We are choosing the handful of shows a well-informed {city_name} art-world insider would tell a visiting friend to see this week - the kind of list See Saw's Featured feed, Artforum's Must-See, or Carla would run. Your verdict is one input to a weighted ranking, so be discriminating rather than generous. Scores are RELATIVE WITHIN THIS CITY: across the inventory below only a few shows should reach 8 or more, and 9-10 is reserved for the canonical or the blockbuster.

RUBRIC - score each of the five dimensions 0-10 against these anchors:
  0-2   unknown / minor: little trace beyond the venue's own page
  3-5   regional: known locally, a solid gallery show, modest coverage
  6-8   nationally notable: museum-level artist or institution, real press, a show people travel for
  9-10  canonical / blockbuster: once-a-decade survey, era-defining artist, citywide event

  artist_significance   career stature: museum solos, biennials, awards, canonical status. Group shows score on the strongest named artists; an unnamed group or collection show scores low here.
  venue_significance    the venue's standing: encyclopedic museum or major kunsthalle 8-10; leading commercial gallery with an international program 6-8; established local gallery 3-5; artist-run, pop-up, retail or unknown space 0-2.
  critical_reception    independent coverage of THIS show: reviews, picks, news, artist activity ([S#] signals). With no signals and no knowledge of coverage, score at most 3.
  ambition_novelty      scale and originality: retrospective or survey, first US / LA / museum solo, commissioned or site-specific work, a genuinely new body of work. A routine sales show of familiar work scores low.
  timeliness            why this week: recently opened, closing soon, tied to a current moment or event. A long-running collection show mid-run scores low.

overall: your holistic call against the GOAL (not an average of the five). Same anchors.
confidence: 0-1, how well the supplied evidence supports the scores; thin evidence means lower confidence.

EVIDENCE RULES
- Weigh evidence over the description's prose - the description is our own agent's writing, drafted from the venue's page and press release. Treat its superlatives as claims until a signal or your own knowledge confirms them.
- Signals labelled [S1]..[Sn] are independent mentions (press picks, reviews, news, artist activity, fair lists) matched to the show. When signals exist the rationale MUST cite the labels you relied on, e.g. "[S2] Carla pick". When none exist, say "no signals", lean on venue standing and what you know of the artist, keep confidence modest, and never invent coverage.
- Keyword hits are a mechanical scan of the description (retrospective, first solo, biennial, award, commissioned, new work). They tell you what is CLAIMED, not what is true.
- Registry tier (1 = top) and the museum flag come from our venue registry; tier "unknown" means we have not rated the venue yet - use your own knowledge of it.
- Dates matter: a show that has already ended scores 0 on timeliness and gets a red flag.
- red_flags: hype without evidence, mismatched or ended dates, an unnamed group show, a venue you cannot place, a description that reads as a press release - anything a human should check before featuring the show.
- evidence_used: the inputs that drove the verdict ("S1", "S3", "description", "venue tier", "prior knowledge: artist", ...).
- rationale: at most 120 words, concrete, no restating of the description.

CITY-WIDE INVENTORY (slug | artist | title | venue) - the pool you are scoring against:
{inventory}
"""

JUDGE_VARIANTS: dict[str, dict] = {
    "judge_v1": {
        "system": JUDGE_V1_SYSTEM,
        "notes": "five-part rubric with anchors, evidence-over-prose rule, city-wide inventory",
    },
    # "judge_v2": {"system": ..., "notes": ...}  -- next rubric revision goes here; keep
    # judge_v1 frozen once rows exist so its prompt_hash stays stable.
}


def prompt_hash(variant: str) -> str:
    """sha1 of the system TEMPLATE (placeholders unfilled) so inventory growth
    does not invalidate every cached verdict."""
    return hashlib.sha1(JUDGE_VARIANTS[variant]["system"].encode()).hexdigest()


def system_blocks(variant: str, city: str, pool: list[dict]) -> list[dict]:
    """The cached system prompt: template + compact city inventory (sorted by
    slug so the cached prefix is byte-stable between requests)."""
    inventory = "\n".join(
        f"{s['slug']} | {s.get('artist') or '-'} | {s.get('title') or '-'} | "
        f"{(s.get('venue') or {}).get('name') or '-'}"
        for s in sorted(pool, key=lambda s: s["slug"]))
    city_name = (CITIES.get(city) or {}).get("display_name", city)
    text = (JUDGE_VARIANTS[variant]["system"]
            .replace("{city_name}", city_name)
            .replace("{inventory}", inventory))
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


# --- per-show evidence + user message -------------------------------------------

def registry_venue(show: dict, ctx: curate.Context) -> dict | None:
    name = (show.get("venue") or {}).get("name") or ""
    for k in store.venue_keys(name):
        v = ctx.registry_by_norm.get(k)
        if v:
            return v
    return ctx.registry_by_id.get(show.get("venue_id") or "")


def show_evidence(show: dict, ctx: curate.Context) -> dict:
    """Signals (with their match rows), registry venue, keyword hits and the
    evidence hash for one show."""
    signals = sorted(ctx.signals.get(show["slug"], []),
                     key=lambda s: (s.get("published_at") or "", s.get("ts") or 0))
    reg = registry_venue(show, ctx)
    tier = reg.get("tier") if reg else None
    desc = show.get("description") or ""
    snippets = " ".join(s.get("snippet") or "" for s in signals if s.get("kind") in curate.PRESS_KINDS)
    hits = merge_hits(scan_keywords(desc, "description"), scan_keywords(snippets, "snippet"))
    dates = f"{show.get('start_date')}|{show.get('end_date')}"
    dkeys = ",".join(sorted({s.get("dedupe_key") or s.get("id") or "" for s in signals}))
    ehash = hashlib.sha256(f"{desc}|{dates}|{dkeys}|{tier}".encode()).hexdigest()
    return {"signals": signals, "registry": reg or {}, "tier": tier, "hits": hits, "evidence_hash": ehash}


def _date_notes(show: dict, today: date) -> str:
    start, end = tools._parse_iso(show.get("start_date")), tools._parse_iso(show.get("end_date"))
    notes = []
    if start:
        notes.append(f"opens in {(start - today).days} d" if start > today else f"opened {(today - start).days} d ago")
    if end:
        notes.append(f"closes in {(end - today).days} d" if end >= today else f"ENDED {(today - end).days} d ago")
    return "; ".join(notes) or "dates unknown"


def _signal_lines(signals: list[dict]) -> list[str]:
    if not signals:
        return ["SIGNALS: none matched to this show yet (no independent coverage on file)."]
    lines = [f"SIGNALS ({len(signals)} independent mention(s) matched to this show):"]
    for i, s in enumerate(signals, 1):
        src, m = s.get("source") or {}, s.get("match") or {}
        lines.append(f"[S{i}] {src.get('name') or src.get('id') or '?'} - {s.get('kind')}/{s.get('strength')}, "
                     f"{s.get('published_at') or 'undated'}; match {m.get('method')} {m.get('confidence')}")
        lines.append(f"     \"{(s.get('snippet') or '').strip()}\"")
        if src.get("url"):
            lines.append(f"     {src['url']}")
    return lines


def format_user_message(show: dict, ev: dict, today: date) -> str:
    v, reg = show.get("venue") or {}, ev["registry"]
    tier = "unknown" if ev["tier"] is None else ev["tier"]
    hits = ", ".join(f"{h['label']} (\"{h['term']}\" in {h['where']})" for h in ev["hits"]) or "none"
    lines = [
        f"SHOW {show['slug']}",
        f"Title: {show.get('title') or '-'}",
        f"Artist: {show.get('artist') or '- (no artist listed: group / collection show)'}",
        f"Venue: {v.get('name')} - registry tier: {tier}; "
        f"kind: {reg.get('kind') or 'unknown'}; museum: {'yes' if venues.is_museum(reg) else 'no'}; "
        f"neighborhood: {v.get('neighborhood') or reg.get('neighborhood') or '?'}",
        f"Dates: {show.get('start_date') or '?'} to {show.get('end_date') or '?'} "
        f"(today {today.isoformat()}; {_date_notes(show, today)})",
        f"Images on file: {len(show.get('images') or [])}",
        "",
        "DESCRIPTION (our agent's prose - a claim, not evidence):",
        (show.get("description") or "").strip() or "(none)",
        "",
        f"KEYWORD HITS (mechanical scan): {hits}",
        "",
        *_signal_lines(ev["signals"]),
        "",
        "Return the verdict JSON.",
    ]
    return "\n".join(lines)


# --- jobs, requests, parsing -------------------------------------------------------

@dataclass
class Job:
    slug: str
    variant: str
    model: str
    effort: str
    prompt_hash: str
    evidence_hash: str
    cache_key: str
    user_text: str
    n_signals: int
    custom_id: str = ""


def cache_key(slug: str, variant: str, phash: str, model: str, effort: str, ehash: str) -> str:
    return hashlib.sha256(f"{slug}|{variant}|{phash}|{model}|{effort}|{ehash}".encode()).hexdigest()


def select_jobs(city: str, variant: str, models: list[str], effort: str, shows: list[dict],
                ctx: curate.Context, today: date, force: bool) -> tuple[list[Job], int]:
    """One job per (show, model) whose latest verdict is missing or stale."""
    latest = store.load_judge(city)
    phash = prompt_hash(variant)
    jobs, skipped = [], 0
    for show in shows:
        ev = show_evidence(show, ctx)
        text = format_user_message(show, ev, today)
        for model in models:
            key = cache_key(show["slug"], variant, phash, model, effort, ev["evidence_hash"])
            prev = ((latest.get(show["slug"]) or {}).get(variant) or {}).get(model)
            if prev and prev.get("cache_key") == key and not force:
                skipped += 1
                continue
            jobs.append(Job(show["slug"], variant, model, effort, phash, ev["evidence_hash"],
                            key, text, len(ev["signals"])))
    return jobs, skipped


def build_request(job: Job, system: list[dict]) -> dict:
    """The one Messages request shape, used verbatim by the sync path
    (``messages.create(**req)``) and the batch path (``Request(params=req)``).
    No ``thinking`` (adaptive by default on both judge models), no temperature."""
    req = {
        "model": job.model,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": job.user_text}],
        "output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
    }
    if harness.MODELS.get(job.model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = job.effort
    return req


def parse_verdict(msg, n_signals: int) -> tuple[JudgeVerdict | None, str | None]:
    """(verdict, error). Refusals, truncation and schema violations become an
    error string instead of an exception."""
    if msg.stop_reason == "refusal":
        det = getattr(msg, "stop_details", None)
        return None, f"refusal ({getattr(det, 'category', None)}: {getattr(det, 'explanation', '')})"
    if msg.stop_reason == "max_tokens":
        return None, "max_tokens: verdict truncated"
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        v = JudgeVerdict.model_validate_json(text)
    except ValidationError as exc:
        return None, f"validation: {str(exc)[:200]}"
    v.confidence = max(0.0, min(1.0, float(v.confidence)))
    if n_signals and not re.search(r"\[S\d+\]", v.rationale):
        v.red_flags.append("rationale cites no [S#] label although signals were supplied")
    words = len(v.rationale.split())
    if words > RATIONALE_MAX_WORDS:
        v.red_flags.append(f"rationale is {words} words (limit {RATIONALE_MAX_WORDS})")
    return v, None


# --- metering + rows ---------------------------------------------------------------

def usage_dict(usage) -> dict:
    return {"input": usage.input_tokens or 0, "output": usage.output_tokens or 0,
            "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0}


class Meters:
    """One ``harness.CostMeter`` per model (label = run_id); per-response cost
    is the meter's dollar delta, taken under a lock for the thread pool."""

    def __init__(self, city: str, variant: str, models: list[str], batch: bool, ts: int):
        self.lock = threading.Lock()
        self.meters = {m: harness.CostMeter(label=f"judge-{city}-{variant}-{m}-{ts}", model=m, batch=batch)
                       for m in models}

    def record(self, model: str, usage) -> float:
        with self.lock:
            meter = self.meters[model]
            before = meter.dollars
            meter.record(usage)
            return meter.dollars - before

    def total(self) -> float:
        return sum(m.dollars for m in self.meters.values())


@dataclass
class Stats:
    ok: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_ok(self, row: dict) -> None:
        with self.lock:
            self.ok.append(row)

    def add_fail(self, job: Job, err: str) -> None:
        with self.lock:
            self.failed.append({"slug": job.slug, "model": job.model, "error": err})
        print(f"  {job.slug:<44} {short_model(job.model):<7} FAILED: {err}")


def short_model(model: str) -> str:
    return "sonnet" if "sonnet" in model else "opus" if "opus" in model else model[:7]


def verdict_row(job: Job, v: JudgeVerdict, usage, cost: float, run_id: str, city: str, batch: bool) -> dict:
    """Plan 2.1 judge row, key order as specified."""
    return {
        "ts": int(time.time()), "city": city, "slug": job.slug, "variant": job.variant,
        "prompt_hash": job.prompt_hash, "model": job.model, "effort": job.effort,
        "evidence_hash": job.evidence_hash, "cache_key": job.cache_key, "run_id": run_id,
        "scores": v.scores.model_dump(), "overall": v.overall,
        "confidence": round(v.confidence, 3), "rationale": v.rationale.strip(),
        "evidence_used": v.evidence_used, "red_flags": v.red_flags,
        "usage": usage_dict(usage), "cost_usd": round(cost, 5), "batch": batch,
    }


def handle_message(job: Job, msg, meters: Meters, city: str, batch: bool, stats: Stats) -> dict | None:
    """Meter (even refusals cost tokens), parse, append the row, log."""
    cost = meters.record(job.model, msg.usage)
    verdict, err = parse_verdict(msg, job.n_signals)
    if err:
        stats.add_fail(job, err)
        return None
    row = verdict_row(job, verdict, msg.usage, cost, meters.meters[job.model].label, city, batch)
    store.append_row(store.judge_file(city), row)
    stats.add_ok(row)
    print(f"  {job.slug:<44} {short_model(job.model):<7} overall {row['overall']:>2}  "
          f"conf {row['confidence']:.2f}  ${cost:.4f}  {row['rationale'][:72]}")
    return row


# --- sync + batch runners -----------------------------------------------------------

def run_sync(client: anthropic.Anthropic, jobs: list[Job], system: list[dict], meters: Meters,
             city: str, workers: int, stats: Stats) -> None:
    """Thread pool. The first job per model runs alone so it writes the prompt
    cache before the rest read it."""
    def one(job: Job) -> None:
        try:
            msg = client.messages.create(**build_request(job, system))
        except anthropic.APIError as exc:
            stats.add_fail(job, f"api {exc.__class__.__name__}: {str(exc)[:160]}")
            return
        handle_message(job, msg, meters, city, False, stats)

    by_model: dict[str, list[Job]] = {}
    for j in jobs:
        by_model.setdefault(j.model, []).append(j)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, [js[0] for js in by_model.values()]))
        list(pool.map(one, [j for js in by_model.values() for j in js[1:]]))


def run_batch(client: anthropic.Anthropic, jobs: list[Job], system: list[dict], meters: Meters,
              city: str, stats: Stats) -> str:
    """Message Batches API: submit, poll until ended, key results by custom_id."""
    from anthropic.types.messages.batch_create_params import Request

    for i, j in enumerate(jobs):
        j.custom_id = f"j{i:04d}-{short_model(j.model)}"
    by_id = {j.custom_id: j for j in jobs}
    batch = client.messages.batches.create(
        requests=[Request(custom_id=j.custom_id, params=build_request(j, system)) for j in jobs])
    print(f"batch {batch.id}: {len(jobs)} request(s), status {batch.processing_status}")
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        rc = batch.request_counts
        print(f"  ... {batch.processing_status}: processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored}")
        time.sleep(BATCH_POLL_S)
    for res in client.messages.batches.results(batch.id):
        job = by_id.get(res.custom_id)
        if job is None:
            continue
        if res.result.type == "succeeded":
            handle_message(job, res.result.message, meters, city, True, stats)
        else:
            err = getattr(getattr(res.result, "error", None), "type", None)
            stats.add_fail(job, f"batch {res.result.type}" + (f": {err}" if err else ""))
    return batch.id


def finish_run(city: str, variant: str, meters: Meters, stats: Stats, skipped: int,
               batch_id: str | None, t0: float) -> None:
    """Save each model's spend file and append one runs.jsonl row per model
    that actually sent requests."""
    for model, meter in meters.meters.items():
        if meter.requests == 0:
            continue
        meter.save()
        n_ok = sum(1 for r in stats.ok if r["model"] == model)
        n_fail = sum(1 for f in stats.failed if f["model"] == model)
        store.append_run({
            "run_id": meter.label, "city": city, "stage": "judge",
            "prompt_variant": variant, "prompt_hash": prompt_hash(variant), "model": model,
            "effort": next((r["effort"] for r in stats.ok if r["model"] == model), None),
            "sources_planned": [], "sources_seen": [], "signals_recorded": 0, "candidates_new": 0,
            "duplicates_rejected": 0, "requests": meter.requests, "web_searches": 0,
            "cost_usd": round(meter.dollars, 4), "stop_reason": None,
            "duration_s": round(time.time() - t0, 1),
            "notes": f"verdicts={n_ok} failed={n_fail} skipped_cached={skipped} batch={meter.batch}"
                     + (f" batch_id={batch_id}" if batch_id else ""),
        })
        print(f"  {model}: {meter.requests} request(s), ${meter.dollars:.4f} "
              f"(in {meter.input_tokens}, out {meter.output_tokens}, cache w {meter.cache_write_tokens} / r {meter.cache_read_tokens})")
    print(f"total: {len(stats.ok)} verdict(s), {len(stats.failed)} failed, {skipped} cached; ${meters.total():.4f}")


def cmd_run(args: argparse.Namespace) -> int:
    city, variant = args.city, args.variant
    if variant not in JUDGE_VARIANTS:
        print(f"unknown variant {variant!r}; have {sorted(JUDGE_VARIANTS)}")
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    bad = [m for m in models if m not in harness.MODELS]
    if bad:
        print(f"unknown model(s) {bad}; have {sorted(harness.MODELS)}")
        return 2
    pool = tools.all_city_shows(city)
    shows = pool
    if args.slugs:
        keep = {s.strip() for s in args.slugs.split(",") if s.strip()}
        shows = [s for s in shows if s["slug"] in keep]
    if args.limit:
        shows = shows[: args.limit]
    ctx = curate.build_context(city, pool)
    today = date.today()
    jobs, skipped = select_jobs(city, variant, models, args.effort, shows, ctx, today, args.force)
    system = system_blocks(variant, city, pool)
    print(f"{city}/{variant}: {len(shows)} show(s) x {len(models)} model(s) -> {len(jobs)} request(s), "
          f"{skipped} cached; prompt_hash {prompt_hash(variant)[:12]}; system {len(system[0]['text']):,} chars (cached); "
          f"effort {args.effort}; {'batch' if args.batch else f'{args.workers} workers'}")
    if args.dry_run:
        print("\n--- system ---\n" + system[0]["text"])
        if jobs:
            print("\n--- first user message ---\n" + jobs[0].user_text)
        print("\n--- jobs ---")
        for j in jobs:
            print(f"  {j.slug:<44} {j.model:<16} signals={j.n_signals} key={j.cache_key[:12]}")
        return 0
    if not jobs:
        print("nothing to do: every verdict is cached (use --force to re-judge); 0 API calls")
        return 0
    load_env()
    client = anthropic.Anthropic(max_retries=3)
    t0 = time.time()
    meters = Meters(city, variant, models, args.batch, int(t0))
    stats = Stats()
    batch_id = None
    if args.batch:
        batch_id = run_batch(client, jobs, system, meters, city, stats)
    else:
        run_sync(client, jobs, system, meters, city, args.workers, stats)
    finish_run(city, variant, meters, stats, skipped, batch_id, t0)
    return 0


# --- compare ------------------------------------------------------------------------

def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rho with average ranks for ties; None when undefined."""
    n = len(xs)
    if n < 2:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx)
    sy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(sx * sy) if sx and sy else None


def arm_rows(judge_rows: dict) -> tuple[dict | None, dict | None]:
    """(sonnet row, opus row) among a show's per-model verdict rows."""
    son = next((r for m, r in judge_rows.items() if "sonnet" in m.lower()), None)
    opu = next((r for m, r in judge_rows.items() if "opus" in m.lower()), None)
    return son, opu


def rank_arms(shows: list[dict], params: dict, today: date, ctx: curate.Context, published: set,
              variant: str, snapshot: dict | None, matcher) -> dict[str, dict]:
    """curate's ranking + See Saw metrics under params.judge.model = sonnet / opus / mean."""
    arms = {}
    for arm in ARMS:
        p = json.loads(json.dumps(params))
        p["judge"] = {"variant": variant, "model": arm}
        ranked = curate.rank(shows, p, today, ctx, published)
        ss = curate.seesaw_metrics(ranked, snapshot, matcher)
        arms[arm] = {
            "rank": {r["slug"]: r["rank"] for r in ranked},
            "featured": {r["slug"]: bool(r["featured"]) for r in ranked},
            "metrics": ss["metrics"],
            "matched": {e["match"]["slug"] for e in ss["entries"] if e["match"]["slug"]},
        }
    return arms


def _arm_view(row: dict) -> dict:
    return {"model": row.get("model"), "scores": row.get("scores"), "overall": row.get("overall"),
            "confidence": row.get("confidence"), "rationale": row.get("rationale"),
            "red_flags": row.get("red_flags") or [], "cost_usd": row.get("cost_usd"), "ts": row.get("ts")}


def compare_rows(shows: list[dict], ctx: curate.Context, variant: str, arms: dict, n_top: int) -> list[dict]:
    """One row per show with both verdicts, sorted by decision relevance."""
    by_slug = {s["slug"]: s for s in shows}
    rows = []
    for slug, variants in ctx.judge.items():
        son, opu = arm_rows(variants.get(variant) or {})
        if not (son and opu) or slug not in by_slug:
            continue
        show = by_slug[slug]
        delta = int(opu["overall"]) - int(son["overall"])
        max_sub = max(abs(int(opu["scores"][k]) - int(son["scores"][k])) for k in SUB_SCORES)
        membership = {arm: arms[arm]["featured"].get(slug, False) for arm in ARMS}
        flip = membership["sonnet"] != membership["opus"]
        on_seesaw = slug in arms["mean"]["matched"]
        seesaw_flip = on_seesaw and flip
        rank_mean = arms["mean"]["rank"].get(slug) or len(shows)
        proximity = 1.0 / (1.0 + abs(rank_mean - n_top))
        rows.append({
            "slug": slug, "title": show.get("title"), "artist": show.get("artist"),
            "venue": (show.get("venue") or {}).get("name"),
            "sonnet": _arm_view(son), "opus": _arm_view(opu),
            "delta": delta, "max_sub_delta": max_sub,
            "membership": membership, "membership_flip": flip,
            "on_seesaw": on_seesaw, "seesaw_flip": seesaw_flip,
            "rank": {arm: arms[arm]["rank"].get(slug) for arm in ARMS},
            "cutoff_proximity": round(proximity, 4),
            "relevance": round(abs(delta) + 3 * flip + 4 * seesaw_flip + 0.5 * max_sub + 0.5 * proximity, 4),
        })
    rows.sort(key=lambda r: (-r["relevance"], r["slug"]))
    return rows


def compare_header(rows: list[dict], arms: dict, variant: str, city: str) -> dict:
    son = [r["sonnet"]["overall"] for r in rows]
    opu = [r["opus"]["overall"] for r in rows]
    rho = spearman(son, opu) if len(rows) >= 3 else None
    tp = {arm: (arms[arm]["metrics"] or {}).get("tp") for arm in ARMS}
    within_one = (tp["sonnet"] is not None and tp["opus"] is not None and abs(tp["sonnet"] - tp["opus"]) <= 1)
    if rho is not None and rho >= 0.85 and within_one:
        suggestion, basis = "claude-sonnet-5", f"rho {rho:.2f} >= 0.85 and See Saw overlap within 1 ({tp['sonnet']} vs {tp['opus']})"
    else:
        why = []
        if rho is None:
            why.append("rho undefined (fewer than 3 shows or all ties)")
        elif rho < 0.85:
            why.append(f"rho {rho:.2f} < 0.85")
        if not within_one:
            why.append(f"See Saw overlap differs by more than 1 ({tp['sonnet']} vs {tp['opus']})" if tp["sonnet"] is not None else "no See Saw snapshot")
        suggestion, basis = "claude-opus-5", "; ".join(why)
    cost = {arm: statistics.fmean(r[arm]["cost_usd"] or 0 for r in rows) if rows else None for arm in ("sonnet", "opus")}
    all_rows = [r for r in store.read_jsonl(store.judge_file(city)) if r.get("variant") == variant]
    return {
        "variant": variant, "city": city,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "n": len(rows),
        "spearman": None if rho is None else round(rho, 4),
        "mean_abs_delta": round(statistics.fmean(abs(r["delta"]) for r in rows), 3) if rows else None,
        "precision_at_n": {arm: (arms[arm]["metrics"] or {}).get("precision") for arm in ARMS},
        "overlap": tp,
        "cost_per_verdict": cost,
        "total_cost_compared": round(sum((r["sonnet"]["cost_usd"] or 0) + (r["opus"]["cost_usd"] or 0) for r in rows), 4),
        "total_cost_variant": round(sum(float(r.get("cost_usd") or 0) for r in all_rows), 4),
        "suggestion": suggestion, "suggestion_basis": basis,
    }


def _vec(row: dict) -> str:
    s = row["scores"]
    return (f"A{s['artist_significance']} V{s['venue_significance']} C{s['critical_reception']} "
            f"N{s['ambition_novelty']} T{s['timeliness']} -> **{row['overall']}**")


def _md(s) -> str:
    return str(s if s is not None else "-").replace("|", "/").replace("\n", " ").strip()


def _yn(membership: dict) -> str:
    return "/".join("Y" if membership[a] else "N" for a in ARMS)


def _money(x) -> str:
    return "-" if x is None else f"${x:.4f}"


def _pct(x) -> str:
    return "-" if x is None else f"{x:.2f}"


def render_markdown(hdr: dict, rows: list[dict], top: int, n_top: int, snapshot: dict | None, params_hash: str) -> str:
    snap_line = (f"{snapshot['id']} ({len(snapshot.get('entries') or [])} entries)" if snapshot else "none")
    p, tp, c = hdr["precision_at_n"], hdr["overlap"], hdr["cost_per_verdict"]
    out = [
        f"# Judge compare - {hdr['city']} / {hdr['variant']}", "",
        f"generated {hdr['generated_at']}; params {params_hash}; N = {n_top} (params.max_n); See Saw snapshot {snap_line}", "",
        "| metric | value |", "|---|---|",
        f"| shows with both verdicts | {hdr['n']} |",
        f"| Spearman rho (overall, opus vs sonnet) | {_pct(hdr['spearman'])} |",
        f"| mean abs delta (opus - sonnet) | {_pct(hdr['mean_abs_delta'])} |",
        f"| precision@{n_top} vs See Saw - sonnet / opus / mean | {_pct(p['sonnet'])} / {_pct(p['opus'])} / {_pct(p['mean'])} |",
        f"| See Saw overlap (tp) - sonnet / opus / mean | {tp['sonnet']} / {tp['opus']} / {tp['mean']} |",
        f"| $/verdict - sonnet / opus | {_money(c['sonnet'])} / {_money(c['opus'])} |",
        f"| total cost - compared rows / all judge rows for this variant | {_money(hdr['total_cost_compared'])} / {_money(hdr['total_cost_variant'])} |",
        f"| auto-suggestion | **{hdr['suggestion']}** - {hdr['suggestion_basis']} |",
        "", "Auto-suggestion rule: rho >= 0.85 and See Saw overlap within 1 -> Sonnet, else Opus. "
        "Read the rationales below before deciding.", "",
        f"## Top {min(top, len(rows))} decision-relevant comparisons", "",
        "relevance = |delta| + 3*membership_flip + 4*seesaw_flip + 0.5*max_sub_delta + 0.5*cutoff_proximity; "
        "member = featured under params with judge = sonnet / opus / mean.", "",
        "| # | show | venue | sonnet A V C N T -> overall | opus A V C N T -> overall | delta | member S/O/M | rank S/O/M | See Saw | relevance | $ S / O |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows[:top], 1):
        rk = r["rank"]
        out.append(f"| {i} | {_md(r['title'])} | {_md(r['venue'])} | {_vec(r['sonnet'])} | {_vec(r['opus'])} | "
                   f"{r['delta']:+d} | {_yn(r['membership'])} | {rk['sonnet']}/{rk['opus']}/{rk['mean']} | "
                   f"{'Y' if r['on_seesaw'] else 'N'} | {r['relevance']:.2f} | "
                   f"{_money(r['sonnet']['cost_usd'])} / {_money(r['opus']['cost_usd'])} |")
    out.append("")
    for i, r in enumerate(rows[:top], 1):
        s, o = r["sonnet"], r["opus"]
        out += [
            f"### {i}. {_md(r['title'])} - {_md(r['venue'])} (`{r['slug']}`)", "",
            f"| Sonnet (overall {s['overall']}, conf {s['confidence']}, {_money(s['cost_usd'])}) | "
            f"Opus (overall {o['overall']}, conf {o['confidence']}, {_money(o['cost_usd'])}) |",
            "|---|---|",
            f"| {_md(s['rationale'])} | {_md(o['rationale'])} |",
            f"| red flags: {_md('; '.join(s['red_flags']) or 'none')} | red flags: {_md('; '.join(o['red_flags']) or 'none')} |",
            "",
        ]
    out += [
        "## Full agreement table", "",
        "| slug | venue | sonnet | opus | delta | max sub delta | member S/O/M | rank S/O/M | See Saw | flip | relevance |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rows, key=lambda r: r["slug"]):
        rk = r["rank"]
        out.append(f"| `{r['slug']}` | {_md(r['venue'])} | {r['sonnet']['overall']} | {r['opus']['overall']} | "
                   f"{r['delta']:+d} | {r['max_sub_delta']} | {_yn(r['membership'])} | "
                   f"{rk['sonnet']}/{rk['opus']}/{rk['mean']} | {'Y' if r['on_seesaw'] else 'N'} | "
                   f"{'seesaw' if r['seesaw_flip'] else 'member' if r['membership_flip'] else '-'} | {r['relevance']:.2f} |")
    out.append("")
    return "\n".join(out)


def cmd_compare(args: argparse.Namespace) -> int:
    city, variant = args.city, args.variant
    params = curate.load_params(args.params, city)
    today = curate.resolve_today(params)
    params["today"] = today.isoformat()
    shows, published = curate._pool(city)
    ctx = curate.build_context(city, shows)
    snapshot = store.load_snapshot(city, args.snapshot)
    registry = store.load_registry_safe(city)
    matcher = lambda ref: store.match_show_ref(ref, ctx.pool_index, registry)  # noqa: E731
    n_top = int(params.get("max_n") or 12)
    arms = rank_arms(shows, params, today, ctx, published, variant, snapshot, matcher)
    rows = compare_rows(shows, ctx, variant, arms, n_top)
    if not rows:
        print(f"no show has both a sonnet and an opus verdict for {variant}; run `judge.py run` first")
        return 1
    hdr = compare_header(rows, arms, variant, city)
    hdr.update({"n_top": n_top, "snapshot_id": snapshot.get("id") if snapshot else None,
                "params_hash": curate.params_hash(params), "top": rows[: args.top], "all": rows})

    md = render_markdown(hdr, rows, args.top, n_top, snapshot, hdr["params_hash"])
    md_path = store.REPORTS_DIR / f"judge-compare-{city}.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md)
    payload = json.dumps(hdr, indent=1, ensure_ascii=False)
    json_path = store.city_dir(city) / "judge_compare.json"
    json_path.write_text(payload)
    (store.REPORTS_DIR / f"judge-compare-{city}.json").write_text(payload)   # what curate.build_report embeds

    top_end = md.index("### 1.") if "### 1." in md else len(md)
    print(md[:top_end].rstrip())
    print(f"\nwrote {md_path}\nwrote {json_path} (+ {store.REPORTS_DIR / f'judge-compare-{city}.json'})")
    return 0


# --- CLI -------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="judge pool shows with one or more models")
    p.add_argument("--city", required=True)
    p.add_argument("--variant", default="judge_v1", help=f"rubric variant: {sorted(JUDGE_VARIANTS)}")
    p.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-separated model ids")
    p.add_argument("--limit", type=int, default=None, help="first N pool shows (published first)")
    p.add_argument("--slugs", default=None, help="comma-separated slugs to judge")
    p.add_argument("--batch", action="store_true", help="Message Batches API (50%% off, async)")
    p.add_argument("--force", action="store_true", help="re-judge even when the cache key matches")
    p.add_argument("--workers", type=int, default=4, help="threads for the sync path")
    p.add_argument("--effort", default=DEFAULT_EFFORT, choices=EFFORTS)
    p.add_argument("--dry-run", action="store_true", help="print prompts + jobs; no API calls")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("compare", help="sonnet vs opus report for one variant")
    p.add_argument("--city", required=True)
    p.add_argument("--variant", default="judge_v1")
    p.add_argument("--params", default=None, help="params JSON layered over params/default.json")
    p.add_argument("--snapshot", default="latest", help="See Saw snapshot id, 'latest' or 'none'")
    p.add_argument("--top", type=int, default=10, help="rows in the decision-relevant table")
    p.set_defaults(fn=cmd_compare)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
