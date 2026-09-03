"""Description relocation lint (galleries-first plan, docs/GALLERIES.md).

Show descriptions must be about the SHOW; text about the gallery itself
(history, program identity, roster, founders, space, hours) belongs on the
venue record. This lint finds such spans with one offline Sonnet call per
show, rewrites flagged descriptions without them, and (with --apply) writes
the rewrite back, banking the lifted text as `about.hints` on the registry
venue for research_venue.py / describe passes to use as secondary hints.

    python lint_descriptions.py run --city los-angeles [--limit N] [--slugs a,b]
                                    [--pending] [--apply] [--workers 8]
                                    [--model claude-sonnet-5] [--force]

Offline, no web tools. Cache key sha256(slug|prompt_hash|model|sha256(description))
is stored per row in content/curation/<city>/lint.jsonl, so an unchanged show
is never re-paid. Cost is metered to content/spend/lint-<city>-<ts>.json.
--apply backs originals up to content/spend/relocated-descriptions-<city>-<ts>.json
before touching the show files (published + pending, under the city flock).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_store as store  # noqa: E402
import harness  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402
from run_scrape import load_env  # noqa: E402

DEFAULT_MODEL = "claude-sonnet-5"
MAX_TOKENS = 3000
MIN_WORDS = 60            # tools.save_show floor for a description
CONFIDENCE_FLOOR = 0.6    # below this a span is reported but not rewritten
SPAN_KINDS = ["history", "program", "space", "roster", "founder", "hours", "other"]

LINT_SYSTEM = """You audit exhibition descriptions for a gallery guide. Each description should be about ONE SHOW: what it is, what is in it, the artist's context, why it is worth seeing.

Find sentences (or self-contained clauses) that are ONLY about the gallery/venue rather than this show — text that would still be true if the show were replaced by another. Kinds:
- history: founding year, how long open, previous names/locations, founder or director biography ("founded in 1986", "for two decades", "recently rebranded from")
- program: the gallery's general identity/mission/reputation ("known for platforming...", "has built its program around...", "one of the city's steadiest small spaces")
- space: description of the building/rooms/storefront unrelated to how this show is installed
- roster: lists of the artists the gallery represents in general
- founder: who runs/founded the gallery, their background
- hours: opening hours, appointment policy, admission
- other: anything else venue-only

Do NOT flag:
- venue mentions welded to a claim about this show ("presented in the gallery's intimate back room, the paintings reward close viewing")
- concurrency/scheduling context for this show ("on view alongside a solo survey in the third room")
- a venue's name used as a location
- the artist's own history, previous shows, or biography

Quote each span EXACTLY as it appears (verbatim substring of the description). Count show-tethered venue mentions you deliberately did not flag as show_tethered_mentions. confidence (0-1) is your confidence that the flagged spans are venue-only and removable without breaking the description."""

LINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gallery_only_spans", "show_tethered_mentions", "confidence"],
    "properties": {
        "gallery_only_spans": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "kind"],
                "properties": {
                    "text": {"type": "string", "description": "verbatim substring of the description"},
                    "kind": {"type": "string", "enum": SPAN_KINDS},
                },
            },
        },
        "show_tethered_mentions": {"type": "integer"},
        "confidence": {"type": "number"},
    },
}

REWRITE_SYSTEM = """You edit an exhibition description for a gallery guide. Remove the listed venue-only spans and return the description with the same paragraph structure (paragraphs separated by blank lines), the same voice, and every remaining fact intact. Do not add new claims. Smooth any sentence you cut into so the prose still reads naturally. Keep at least 60 words; if removal would drop below that, keep the show-relevant half of a span rather than inventing text."""

REWRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["description"],
    "properties": {"description": {"type": "string"}},
}


def prompt_hash() -> str:
    return hashlib.sha1((LINT_SYSTEM + "\n" + REWRITE_SYSTEM).encode()).hexdigest()[:16]


def cache_key(slug: str, model: str, description: str) -> str:
    dh = hashlib.sha256((description or "").encode()).hexdigest()
    return hashlib.sha256(f"{slug}|{prompt_hash()}|{model}|{dh}".encode()).hexdigest()


def lint_file(city: str) -> Path:
    return store.city_dir(city) / "lint.jsonl"


def latest_rows(city: str) -> dict[str, dict]:
    """slug -> latest lint row."""
    out: dict[str, dict] = {}
    for r in store.read_jsonl(lint_file(city)):
        if r.get("slug"):
            out[r["slug"]] = r
    return out


# --- requests ---------------------------------------------------------------------

def lint_request(show: dict, model: str) -> dict:
    v = show.get("venue") or {}
    user = (f"SHOW {show['slug']}\nTitle: {show.get('title')}\nArtist: {show.get('artist') or '-'}\n"
            f"Venue: {v.get('name')} ({v.get('neighborhood') or '?'})\n\nDESCRIPTION:\n"
            f"{(show.get('description') or '').strip()}\n\nReturn the JSON.")
    req = {
        "model": model, "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": LINT_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": LINT_SCHEMA}},
    }
    if harness.MODELS.get(model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = "medium"
    return req


def rewrite_request(show: dict, spans: list[dict], model: str) -> dict:
    spans_txt = "\n".join(f"- [{s['kind']}] \"{s['text']}\"" for s in spans)
    user = (f"SHOW {show['slug']} — {show.get('artist') or ''} \"{show.get('title')}\"\n\n"
            f"SPANS TO REMOVE:\n{spans_txt}\n\nDESCRIPTION:\n{(show.get('description') or '').strip()}"
            "\n\nReturn the JSON.")
    req = {
        "model": model, "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": REWRITE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": REWRITE_SCHEMA}},
    }
    if harness.MODELS.get(model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = "medium"
    return req


def parse_json(msg) -> tuple[dict | None, str | None]:
    if msg.stop_reason == "refusal":
        return None, "refusal"
    if msg.stop_reason == "max_tokens":
        return None, "max_tokens"
    text = next((b.text for b in msg.content if b.type == "text"), "")
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"json: {exc}"


def normalize_verdict(raw: dict, description: str) -> dict:
    """Keep only spans that are verbatim substrings (whitespace-normalized
    fallback); clamp confidence."""
    desc_norm = " ".join((description or "").split())
    spans = []
    for s in raw.get("gallery_only_spans") or []:
        text = " ".join(str(s.get("text") or "").split())
        kind = s.get("kind") if s.get("kind") in SPAN_KINDS else "other"
        if text and text in desc_norm:
            spans.append({"text": text, "kind": kind})
    conf = float(raw.get("confidence") or 0.0)
    return {"gallery_only_spans": spans,
            "show_tethered_mentions": int(raw.get("show_tethered_mentions") or 0),
            "confidence": max(0.0, min(1.0, conf))}


def should_rewrite(verdict: dict) -> bool:
    return bool(verdict["gallery_only_spans"]) and verdict["confidence"] >= CONFIDENCE_FLOOR


def acceptable_rewrite(new: str, old: str) -> bool:
    """The rewrite must still clear the save_show word floor, be shorter, and
    keep the paragraph count within one of the original."""
    if not new or len(new.split()) < MIN_WORDS:
        return False
    if len(new) >= len(old):
        return False
    p_old = len([p for p in old.split("\n\n") if p.strip()])
    p_new = len([p for p in new.split("\n\n") if p.strip()])
    return abs(p_old - p_new) <= 1


# --- run ------------------------------------------------------------------------------

@dataclass
class Job:
    show: dict
    key: str


def select_shows(city: str, limit: int | None, slugs: set[str] | None, pending: bool) -> list[dict]:
    published = tools._load_shows_file(tools._city_file(city))["shows"]
    pool = published + (tools._load_shows_file(tools._pending_file(city))["shows"] if pending else [])
    if slugs:
        pool = [s for s in pool if s["slug"] in slugs]
    if limit:
        pool = pool[:limit]
    return pool


def run_lint(city: str, shows: list[dict], model: str, workers: int, force: bool,
             client=None) -> tuple[list[dict], int, harness.CostMeter]:
    """Lint (and rewrite where warranted) each show. Returns (rows, n_cached, meter).
    `client` may be a stub exposing messages.create(**req) for tests."""
    ts = int(time.time())
    meter = harness.CostMeter(label=f"lint-{city}-{ts}", model=model)
    prev = latest_rows(city)
    jobs, cached = [], []
    for s in shows:
        key = cache_key(s["slug"], model, s.get("description") or "")
        row = prev.get(s["slug"])
        if row and row.get("cache_key") == key and not force:
            cached.append(row)
        else:
            jobs.append(Job(s, key))
    if jobs and client is None:
        import anthropic
        client = anthropic.Anthropic()
    lock = threading.Lock()
    rows: list[dict] = []

    def meter_record(usage) -> float:
        with lock:
            before = meter.dollars
            meter.record(usage)
            return meter.dollars - before

    def one(job: Job) -> None:
        s = job.show
        try:
            msg = client.messages.create(**lint_request(s, model))
        except Exception as exc:  # noqa: BLE001 - one failure must not sink the run
            print(f"  {s['slug']:<44} FAILED: {str(exc)[:120]}")
            return
        cost = meter_record(msg.usage)
        raw, err = parse_json(msg)
        if err:
            print(f"  {s['slug']:<44} FAILED: {err}")
            return
        verdict = normalize_verdict(raw, s.get("description") or "")
        rewrite, rewrite_err = None, None
        if should_rewrite(verdict):
            try:
                msg2 = client.messages.create(**rewrite_request(s, verdict["gallery_only_spans"], model))
                cost += meter_record(msg2.usage)
                raw2, err2 = parse_json(msg2)
                if err2:
                    rewrite_err = err2
                else:
                    cand = (raw2.get("description") or "").strip()
                    if acceptable_rewrite(cand, s.get("description") or ""):
                        rewrite = cand
                    else:
                        rewrite_err = "rewrite rejected (word floor / length / paragraphs)"
            except Exception as exc:  # noqa: BLE001
                rewrite_err = f"rewrite failed: {str(exc)[:120]}"
        row = {"ts": int(time.time()), "city": city, "slug": s["slug"],
               "venue_id": s.get("venue_id"), "venue": (s.get("venue") or {}).get("name"),
               "model": model, "prompt_hash": prompt_hash(), "cache_key": job.key,
               **verdict, "rewrite": rewrite, "rewrite_error": rewrite_err,
               "cost_usd": round(cost, 5)}
        store.append_row(lint_file(city), row)
        with lock:
            rows.append(row)
        n = len(verdict["gallery_only_spans"])
        print(f"  {s['slug']:<44} spans {n}  conf {verdict['confidence']:.2f}  "
              f"${cost:.4f}" + ("  REWRITE" if rewrite else ""))

    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, jobs[:1]))        # warm the prompt cache
            list(pool.map(one, jobs[1:]))
    return rows + cached, len(cached), meter


def print_table(rows: list[dict]) -> None:
    flagged = [r for r in rows if r.get("gallery_only_spans")]
    print(f"\n{len(flagged)} of {len(rows)} show(s) carry gallery-only text; "
          f"{sum(1 for r in flagged if r.get('rewrite'))} rewrite(s) ready")
    print(f"{'slug':<40} {'venue':<28} {'n':>2} {'conf':>4}  kinds / first span")
    for r in sorted(flagged, key=lambda r: -len(r["gallery_only_spans"])):
        kinds = ",".join(sorted({s["kind"] for s in r["gallery_only_spans"]}))
        first = r["gallery_only_spans"][0]["text"]
        first = first[:117] + "..." if len(first) > 120 else first
        print(f"{r['slug'][:40]:<40} {(r.get('venue') or '')[:28]:<28} "
              f"{len(r['gallery_only_spans']):>2} {r['confidence']:>4.2f}  {kinds}: \"{first}\"")


# --- apply ----------------------------------------------------------------------------

def apply_rows(city: str, rows: list[dict]) -> dict:
    """Write rewrites into the show files (published + pending) under the city
    flock, after backing the originals up; bank spans as venue about.hints."""
    todo = {r["slug"]: r for r in rows if r.get("rewrite")}
    if not todo:
        return {"applied": 0, "backup": None, "hints": 0}
    ts = int(time.time())
    backup = tools.CONTENT_DIR / "spend" / f"relocated-descriptions-{city}-{ts}.json"
    applied, originals, hints_by_venue = [], [], {}
    lock_path = tools.CONTENT_DIR / f".{city}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        files = [(tools._city_file(city), tools._load_shows_file(tools._city_file(city))),
                 (tools._pending_file(city), tools._load_shows_file(tools._pending_file(city)))]
        for path, data in files:
            changed = False
            for s in data["shows"]:
                r = todo.get(s["slug"])
                if not r or s.get("description") == r["rewrite"]:
                    continue
                originals.append({"slug": s["slug"], "file": path.name,
                                  "description": s.get("description"),
                                  "spans": r["gallery_only_spans"]})
                s["description"] = r["rewrite"]
                applied.append(s["slug"])
                changed = True
                vid = s.get("venue_id") or r.get("venue_id")
                if vid:
                    hints_by_venue.setdefault(vid, []).extend(
                        x["text"] for x in r["gallery_only_spans"])
            if changed:
                backup.parent.mkdir(parents=True, exist_ok=True)
                if not backup.exists():
                    backup.write_text(json.dumps({"city": city, "ts": ts, "shows": []}, indent=1))
                tools._write_shows_file(path, data)
        if originals:
            backup.write_text(json.dumps({"city": city, "ts": ts, "shows": originals},
                                         indent=1, ensure_ascii=False))
    n_hints = 0
    if hints_by_venue:
        with venues.locked_registry(city) as reg:
            by_id = venues.index_by_id(reg)
            for vid, texts in hints_by_venue.items():
                v = by_id.get(vid)
                if not v:
                    continue
                venues.ensure_v2(v)
                cur = v["about"].setdefault("hints", [])
                for t in texts:
                    if t not in cur:
                        cur.append(t)
                        n_hints += 1
    return {"applied": len(applied), "backup": str(backup) if originals else None,
            "hints": n_hints, "slugs": applied}


# --- CLI ------------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    load_env()
    slugs = {s.strip() for s in args.slugs.split(",") if s.strip()} if args.slugs else None
    shows = select_shows(args.city, args.limit, slugs, args.pending or bool(slugs))
    if not shows:
        print("no shows selected")
        return 1
    print(f"{args.city}: linting {len(shows)} show(s) with {args.model}")
    rows, n_cached, meter = run_lint(args.city, shows, args.model, args.workers, args.force)
    if meter.requests:
        meter.save()
    print_table(rows)
    print(f"\n{meter.requests} request(s), ${meter.dollars:.4f}; {n_cached} cached")
    if args.apply:
        skip = {k.strip() for k in (args.skip_kinds or "").split(",") if k.strip()}
        keep = []
        for r in rows:
            kinds = {sp["kind"] for sp in r.get("gallery_only_spans") or []}
            if r.get("confidence", 0) < args.min_confidence or not (kinds - skip):
                continue
            keep.append(r)
        print(f"apply filter: confidence >= {args.min_confidence}, skipping kinds {sorted(skip) or '-'} "
              f"-> {len(keep)} of {len(rows)} row(s)")
        res = apply_rows(args.city, keep)
        print(f"applied {res['applied']} rewrite(s); {res['hints']} hint(s) banked on venues; "
              f"backup {res['backup']}")
    else:
        n = sum(1 for r in rows if r.get("rewrite"))
        if n:
            print(f"dry run — {n} rewrite(s) pending; pass --apply to write them")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--city", required=True, choices=sorted(CITIES))
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--slugs", default=None, help="comma list; searches pending too")
    r.add_argument("--pending", action="store_true", help="include the pending pool")
    r.add_argument("--apply", action="store_true")
    r.add_argument("--workers", type=int, default=8)
    r.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(harness.MODELS))
    r.add_argument("--force", action="store_true", help="ignore the lint.jsonl cache")
    r.add_argument("--min-confidence", type=float, default=0.8,
                   help="--apply only rows at/above this confidence (dry run shows all)")
    r.add_argument("--skip-kinds", default="hours",
                   help="--apply skips rows whose spans are all of these kinds (visitor-practical text)")
    args = ap.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
