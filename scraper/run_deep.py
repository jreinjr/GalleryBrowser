"""Exhaustive-coverage orchestrator: enumerate a city's venues zone by zone
into a durable directory, then scrape every directory venue not yet saved or
recently skipped, then verify, then write the coverage/outlier report.

Stages:
  1. enumerate  — optional deterministic seeding (seed_venues.py: Google Places,
                  Gallery Platform LA, Carla), then venues.zone_coverage decides which
                  zones need an LLM enumeration session (missing anchors, too few
                  venues, weak overlap with Places); up to --max-enum-rounds rounds
  2. scrape     — workers pull zones breadth-first from a shared queue (the zone with
                  the fewest sessions so far goes next, so no zone starves behind a big
                  one); each session works the zone's TODO (venues.due_venues: never
                  scraped, past next_check, page changed, a show ending soon, only an
                  upcoming show saved, requeued) until the TODO is empty, progress
                  stalls, or the budget headroom is gone. A venue may yield several shows.
  3. verify     — one pending-pool verification pass (promotes publishable shows)
  4. report     — scraper/report.py coverage + outlier report

Resumable by construction: re-running recomputes every TODO from the ledgers.

Usage:
    python run_deep.py --city los-angeles --total-budget 300 --workers 4
    python run_deep.py --city los-angeles --zones "Chinatown/East LA" \
        --session-budget 3 --total-budget 8          # single-zone dry run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import DEFAULT_MODEL, MODELS, SPEND_DIR  # noqa: E402

HERE = Path(__file__).resolve().parent
LOG_DIR = tools.CONTENT_DIR / "spend" / "logs"
TODO_DIR = tools.CONTENT_DIR / "spend" / "todo"

SKIP_FRESH_DAYS = 10  # a logged skip suppresses re-attempts for this many days


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_venue_ids(spec: str | None) -> set[str] | None:
    """--venue-ids 'a,b' or '@file' (one per line or comma-separated, '#'
    comments). Tokens go through venues.venue_id, so registry ids and names
    that normalize to them both work ('Hammer Museum' -> 'hammer-museum');
    unknown ids are reported at startup."""
    if not spec:
        return None
    text = Path(spec[1:]).read_text() if spec.startswith("@") else spec
    import venues
    toks = [t.strip() for line in text.splitlines() for t in line.split("#")[0].split(",")]
    return {venues.venue_id(t) for t in toks if t}


def zone_todo(city: str, zone: str, only_with_saves: bool = False,
              venue_ids: set[str] | None = None, force_due: bool = False,
              rank_kw: dict | None = None) -> list[dict]:
    """Venues due for a scrape session in this zone (venues.due_venues: status,
    next_check, page-change signals, shows ending soon, upcoming-only saves,
    requeues). `only_with_saves` is the multi-show backfill: every active venue
    of ANY kind (galleries, museums, nonprofits, project spaces, universities)
    holding exactly one show on view now, regardless of schedule. `venue_ids`
    restricts the result to those registry ids; with `force_due` they are
    included regardless of schedule or status (priority 100, reason 'forced').
    `rank_kw` (galleries-first) is passed to venues.due_venues: require_verified,
    min_tier, min_score, rank_weight — see docs/GALLERIES.md."""
    import venues
    if venue_ids and force_due:
        out = []
        for v in venues.load_registry(city).get("venues", []):
            if v.get("neighborhood") == zone and v.get("id") in venue_ids:
                rec = dict(v)
                rec["_priority"], rec["_reasons"] = 100, ["forced"]
                out.append(rec)
        return sorted(out, key=lambda r: r["id"])
    if only_with_saves:
        out = []
        for v in venues.load_registry(city).get("venues", []):
            if v.get("neighborhood") != zone or v.get("status") not in ("active", None):
                continue
            if (v.get("last_outcome") == "skipped:unchanged"
                    and (v.get("last_scraped") or 0) > time.time() - 86400):
                continue   # a multi-show session already confirmed nothing else is on: no re-pay
            if len(venues.current_shows(v)) == 1:
                rec = dict(v)
                rec["_priority"], rec["_reasons"] = 50, ["multishow_backfill"]
                out.append(rec)
        todo = sorted(out, key=lambda r: r["id"])
    else:
        todo = venues.due_venues(city, zone, include_places_only=not EXCLUDE_PLACES_ONLY,
                                 **(rank_kw or {}))
    if venue_ids:
        todo = [v for v in todo if v.get("id") in venue_ids]
    return todo


EXCLUDE_PLACES_ONLY = False   # set from --exclude-places-only


def ranked_file(city: str) -> Path:
    return tools.CONTENT_DIR / "curation" / city / "venues_ranked.json"


def load_ranked(city: str) -> dict:
    """The gallery ranking (rank_venues.py score/apply) a galleries-first run
    starts from. Exits with instructions when it is missing."""
    p = ranked_file(city)
    if not p.exists():
        sys.exit(f"--galleries-first needs {p} — run\n"
                 f"  python rank_venues.py score --city {city}\n"
                 f"  python rank_venues.py apply --city {city}\n"
                 "then retry (see docs/GALLERIES.md).")
    return json.loads(p.read_text())


def venue_roster(city: str, venue_id: str | None, limit: int = 8) -> list[str]:
    """Represented artists from the venue's GalleryReport
    (content/venues/reports/<city>/<id>.json), [] when there is none."""
    if not venue_id:
        return []
    p = tools.CONTENT_DIR / "venues" / "reports" / city / f"{venue_id}.json"
    if not p.exists():
        return []
    try:
        rep = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    names = [r.get("name") for r in rep.get("roster") or []
             if r.get("name") and r.get("status") in (None, "represented", "estate")]
    return names[:limit]


def galleries_first_kw(args) -> dict:
    """due_venues kwargs for a galleries-first run."""
    return {"require_verified": True, "min_tier": args.min_tier,
            "min_score": args.min_score, "rank_weight": args.rank_weight}


def pick_batch(todo: list[dict], n: int, max_museums: int = 2,
               max_places_only: int = 2) -> list[dict]:
    """Next session's venues in priority order, but at most `max_museums`
    museums per batch (a museum can run 8+ concurrent exhibitions and would
    otherwise eat a whole session's budget) and at most `max_places_only`
    Google-Places-only venues (framers and decor shops share Places'
    art_gallery type; they must never fill a batch)."""
    import venues

    batch, museums, places = [], 0, 0
    for v in todo:
        if len(batch) >= n:
            break
        is_museum = venues.is_museum(v)
        is_places = "places_only" in (v.get("_reasons") or [])
        if is_museum and museums >= max_museums:
            continue
        if is_places and places >= max_places_only:
            continue
        museums += is_museum
        places += is_places
        batch.append(v)
    return batch


class ZoneQueue:
    """Breadth-first zone scheduler shared by the scrape workers: the next
    session goes to the zone with the FEWEST sessions so far (ties: most due
    venues, then config order), so every zone gets its first session before
    any zone gets its third. One in-flight session per zone — parallel sessions
    on one zone would batch overlapping TODOs (due_venues only sees a session's
    saves/skips once they land)."""

    def __init__(self, zones: list[str], max_sessions: int,
                 due_counts: dict[str, int] | None = None):
        self.lock = threading.Lock()
        self.max_sessions = max_sessions
        self.zones: dict[str, dict] = {
            z: {"zone": z, "order": i, "due": (due_counts or {}).get(z, 0),
                "sessions_run": 0, "zero_progress": 0, "exhausted": False,
                "active": False, "why": None}
            for i, z in enumerate(zones)}

    def claim(self) -> dict | None:
        """Snapshot of the zone to run next (marked in flight), or None when
        every non-exhausted zone is already in flight / nothing is left."""
        with self.lock:
            free = [s for s in self.zones.values() if not s["exhausted"] and not s["active"]]
            if not free:
                return None
            s = min(free, key=lambda s: (s["sessions_run"], -s["due"], s["order"]))
            s["active"] = True
            return dict(s)

    def release(self, zone: str, progress: int, todo_empty: bool,
                due_left: int | None = None) -> dict:
        with self.lock:
            s = self.zones[zone]
            s["active"] = False
            if todo_empty:
                s["exhausted"], s["why"] = True, "todo_empty"
                return dict(s)
            s["sessions_run"] += 1
            if due_left is not None:
                s["due"] = due_left
            s["zero_progress"] = s["zero_progress"] + 1 if progress == 0 else 0
            if s["zero_progress"] >= 2:
                s["exhausted"], s["why"] = True, "zero_progress"
            elif s["sessions_run"] >= self.max_sessions:
                s["exhausted"], s["why"] = True, "max_sessions"
            return dict(s)

    def any_active(self) -> bool:
        with self.lock:
            return any(s["active"] for s in self.zones.values())

    def snapshot(self) -> dict[str, dict]:
        with self.lock:
            return {z: dict(s) for z, s in self.zones.items()}


def zone_progress_snapshot(city: str, zone: str) -> tuple[int, int]:
    """(saved shows in zone, logged skips in zone) — per-zone session
    attribution. Shows, not venues: a second show at an already-saved venue
    is progress too."""
    saves = sum(1 for s in tools.all_city_shows(city)
                if s["venue"]["neighborhood"] == zone)
    skips = sum(1 for e in tools.load_skips(city) if e.get("neighborhood") == zone)
    return saves, skips


def _already_saved(v: dict) -> list[dict]:
    import venues
    return [{"title": x.get("title"), "artist": x.get("artist"),
             "start": x.get("start"), "end": x.get("end")}
            for x in venues.active_shows(v)]


def run_spend(city: str, start_ts: int) -> float:
    """Sum enum-/deep-labeled session ledgers written since the run started."""
    total = 0.0
    if not SPEND_DIR.exists():
        return total
    for f in SPEND_DIR.glob(f"*deep-{city}-*.json"):
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if e.get("ts", _label_ts(e.get("session", ""))) >= start_ts:
            total += e.get("cost_usd", 0.0)
    for f in SPEND_DIR.glob(f"enum-{city}-*.json"):
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if _label_ts(e.get("session", "")) >= start_ts:
            total += e.get("cost_usd", 0.0)
    return total


def _label_ts(label: str) -> int:
    m = re.search(r"-(\d{9,})$", label or "")
    return int(m.group(1)) if m else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True, choices=sorted(CITIES))
    parser.add_argument("--zones", default=None,
                        help="comma-separated subset of zones (default: all)")
    parser.add_argument("--session-todo", type=int, default=8,
                        help="TODO venues per scrape session")
    parser.add_argument("--session-budget", type=float, default=4.5)
    parser.add_argument("--enum-budget", type=float, default=1.5,
                        help="budget per enumeration session")
    parser.add_argument("--max-sessions-per-zone", type=int, default=10)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--stagger", type=float, default=20.0)
    parser.add_argument("--total-budget", type=float, required=True,
                        help="hard-ish cap on enumerate+scrape spend (checked "
                             "between sessions)")
    parser.add_argument("--max-shows", type=int, default=600,
                        help="city-wide safety cap on SHOWS (published + pending), not venues")
    parser.add_argument("--min-zone-venues", type=int, default=4,
                        help="zones with fewer enumerated+seeded venues get enumerated")
    parser.add_argument("--seed-sources", default=None,
                        help="comma list for seed_venues.py before enumeration: "
                             "places,gpla,carla,evidence (or 'none')")
    parser.add_argument("--max-enum-rounds", type=int, default=2,
                        help="enumeration rounds while zone coverage still fails")
    parser.add_argument("--exclude-places-only", action="store_true",
                        help="TODO never includes venues whose only provenance is Google Places")
    parser.add_argument("--only-venues-with-saves", action="store_true",
                        help="multi-show backfill: TODO = active venues of any kind holding "
                             "exactly one show on view now (ignores the schedule)")
    parser.add_argument("--venue-ids", default=None,
                        help="restrict the TODO to these registry ids/names (comma list or "
                             "@file); zones default to the ones holding them; implies "
                             "--skip-enumeration")
    parser.add_argument("--force-due", action="store_true",
                        help="with --venue-ids: include them regardless of schedule AND status "
                             "(priority 100, reason 'forced'); each is batched at most once")
    parser.add_argument("--budget-reserve", type=float, default=None,
                        help="do not start a session unless spent + reserve <= --total-budget "
                             "(default 1.5 x --session-budget, the harness hard-stop)")
    parser.add_argument("--max-places-per-batch", type=int, default=2,
                        help="cap on Google-Places-only venues per session batch")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    parser.add_argument("--enum-model", default=None, choices=sorted(MODELS),
                        help="model for enumeration sessions (default: --model)")
    parser.add_argument("--fetch-tokens", type=int, default=20000)
    parser.add_argument("--effort", default=None,
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--context-editing", action="store_true")
    parser.add_argument("--arm-label", default=None,
                        help="A/B arm tag prefixed onto session labels")
    parser.add_argument("--keyword-signals", action="store_true",
                        help="scrape sessions also record significance claims they read "
                             "on venue pages (curation evidence; non-strict record_signal)")
    parser.add_argument("--galleries-first", action="store_true",
                        help="galleries-first pipeline (docs/GALLERIES.md): no enumeration; scrape "
                             "only VERIFIED venues from content/curation/<city>/venues_ranked.json, "
                             "highest rank first")
    parser.add_argument("--min-tier", type=int, default=None,
                        help="galleries-first: only venues with tier <= N (1 = top)")
    parser.add_argument("--min-score", type=float, default=None,
                        help="galleries-first: only venues with rank score >= F")
    parser.add_argument("--rank-weight", type=float, default=30.0,
                        help="galleries-first: priority += rank_weight * score (default 30)")
    parser.add_argument("--skip-enumeration", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    from run_scrape import load_env
    load_env()
    global EXCLUDE_PLACES_ONLY
    EXCLUDE_PLACES_ONLY = bool(args.exclude_places_only)

    all_zones = CITIES[args.city]["neighborhoods"]
    if args.zones:
        zones = [z.strip() for z in args.zones.split(",")]
        bad = [z for z in zones if z not in all_zones]
        if bad:
            sys.exit(f"unknown zones: {bad} (valid: {all_zones})")
    else:
        zones = list(all_zones)

    venue_ids = parse_venue_ids(args.venue_ids)
    if args.force_due and not venue_ids:
        sys.exit("--force-due requires --venue-ids")
    if venue_ids:
        import venues
        by_id = venues.index_by_id(venues.load_registry(args.city))
        missing = sorted(venue_ids - set(by_id))
        if missing:
            print(f"warning: --venue-ids not in registry: {missing}", flush=True)
        if not args.zones:   # only the zones holding a requested venue
            hit = {by_id[i].get("neighborhood") for i in venue_ids if i in by_id}
            zones = [z for z in all_zones if z in hit]
        args.skip_enumeration = True   # a targeted re-scrape never enumerates

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TODO_DIR.mkdir(parents=True, exist_ok=True)
    start_ts = int(time.time())
    arm = f"{args.arm_label}-" if args.arm_label else ""
    manifest = SPEND_DIR / f"deep_run-{args.city}-{start_ts}.json"
    manifest.write_text(json.dumps(
        {"city": args.city, "start_ts": start_ts, "zones": zones,
         "args": {k: v for k, v in vars(args).items()}}, indent=2))

    stop = threading.Event()
    state_lock = threading.Lock()
    session_log: list[dict] = []

    # Sessions are metered per API response, so run_spend sees in-flight ones;
    # the reserve covers the session about to start (harness hard-stops a
    # session at 1.5x its budget). Worst case with W workers still overshoots
    # by up to (W-1) x 0.5 x session_budget — raise --budget-reserve when tight.
    reserve = (args.budget_reserve if args.budget_reserve is not None
               else args.session_budget * 1.5)

    def over_limits() -> str | None:
        spent = run_spend(args.city, start_ts)
        if spent + reserve > args.total_budget:
            return (f"budget headroom gone (${spent:.2f} spent + ${reserve:.2f} reserve "
                    f"> ${args.total_budget:.2f})")
        if len(tools.all_city_shows(args.city)) >= args.max_shows:
            return f"max shows reached ({args.max_shows})"
        return None

    def run_session(cmd: list[str], log_name: str) -> int:
        log_path = LOG_DIR / log_name
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}  # logs tail-able live
        with open(log_path, "w") as lf:
            proc = subprocess.run([str(c) for c in cmd], stdout=lf,
                                  stderr=subprocess.STDOUT, env=env)
        return proc.returncode

    rank_kw: dict | None = None
    if args.galleries_first:
        ranked = load_ranked(args.city)
        rank_kw = galleries_first_kw(args)
        n_ranked = sum(1 for v in ranked.get("venues", []) if v.get("rank") is not None)
        print(f"=== STAGE 1: galleries-first — {n_ranked} ranked venue(s) from "
              f"{ranked_file(args.city).name} (generated {ranked.get('generated_at')}); "
              f"cutoffs tier<={args.min_tier} score>={args.min_score}, rank weight {args.rank_weight}; "
              "enumeration skipped ===", flush=True)
        args.skip_enumeration = True

    # ---------- stage 1: seed + enumerate ----------
    if args.skip_enumeration:
        print("=== STAGE 1: enumerate (skipped) ===", flush=True)
    else:
        import venues
        cfg = CITIES[args.city]
        if args.seed_sources and args.seed_sources.lower() != "none":
            import seed_venues
            srcs = [x.strip() for x in args.seed_sources.split(",") if x.strip()]
            print(f"=== STAGE 1a: seed registry from {srcs} ===", flush=True)
            seeded = seed_venues.run(args.city, srcs, zones=zones, apply=True)
            with state_lock:
                session_log.append({"stage": "seed", "sources": srcs,
                                    "summary": {k: v for k, v in (seeded or {}).items()
                                                if not isinstance(v, (list, dict))}})

        def enum_worker(idx: int, zone: str, missing: list[str]) -> None:
            time.sleep((idx % args.workers) * args.stagger)
            if stop.is_set():
                return
            label = f"enum-{args.city}-{slugify(zone)}-{int(time.time())}"
            cmd = [sys.executable, HERE / "run_scrape.py",
                   "--city", args.city, "--enumerate-zone", zone,
                   "--budget", args.enum_budget,
                   "--model", args.enum_model or args.model,
                   "--fetch-tokens", min(args.fetch_tokens, 8000),
                   "--session-label", label, "--no-verify"]
            if missing:
                cmd += ["--missing-anchors", "; ".join(missing)]
            if args.effort:
                cmd += ["--effort", args.effort]
            print(f"[enum {zone}] -> {label}.log" + (f" (missing: {missing})" if missing else ""),
                  flush=True)
            rc = run_session(cmd, f"{label}.log")
            n = sum(1 for v in tools.load_directory(args.city).values()
                    if v["neighborhood"] == zone)
            with state_lock:
                session_log.append({"stage": "enumerate", "zone": zone,
                                    "exit": rc, "zone_directory_size": n})
            print(f"[enum {zone}] done (exit {rc}); zone directory: {n} venues",
                  flush=True)

        swept_weak: set[str] = set()
        for round_n in range(1, args.max_enum_rounds + 1):
            cov = {z: venues.zone_coverage(args.city, z, cfg, args.min_zone_venues)
                   for z in zones}
            need = []
            for z in zones:
                reasons = list(cov[z]["reasons"])
                if "weak_enumeration" in reasons and z in swept_weak:
                    reasons.remove("weak_enumeration")   # one re-sweep per run
                if reasons:
                    need.append(z)
            print(f"=== STAGE 1: enumerate round {round_n}: {len(need)} zone(s) ===", flush=True)
            for z in need:
                c = cov[z]
                print(f"[enum {z}] registry={c['registry']} enumerated={c['enumerated']} "
                      f"seeded={c['seeded']} reasons={c['reasons']} "
                      f"missing={c['missing_anchors']}", flush=True)
                if "weak_enumeration" in c["reasons"]:
                    swept_weak.add(z)
            if not need:
                break
            threads = []
            for i, z in enumerate(need):
                t = threading.Thread(target=enum_worker, args=(i, z, cov[z]["missing_anchors"]),
                                     daemon=True)
                threads.append(t)
                t.start()
                while sum(t.is_alive() for t in threads) >= args.workers:
                    time.sleep(2)
            for t in threads:
                t.join()
        final = {z: venues.zone_coverage(args.city, z, cfg, args.min_zone_venues) for z in zones}
        for z, c in final.items():
            if c["missing_anchors"]:
                print(f"[enum {z}] unresolvable anchors after enumeration: {c['missing_anchors']}",
                      flush=True)
                with state_lock:
                    session_log.append({"stage": "enumerate", "zone": z,
                                        "unresolvable_anchors": c["missing_anchors"]})

    # ---------- stage 2: scrape ----------
    print("=== STAGE 2: scrape ===", flush=True)
    todo_kw = dict(only_with_saves=args.only_venues_with_saves,
                   venue_ids=venue_ids, force_due=args.force_due, rank_kw=rank_kw)
    once_per_run = args.only_venues_with_saves or args.force_due   # one batch per venue per run
    queue = ZoneQueue(zones, args.max_sessions_per_zone,
                      {z: len(zone_todo(args.city, z, **todo_kw)) for z in zones})
    print("[queue] due venues per zone: " + ", ".join(
        f"{z}={s['due']}" for z, s in queue.snapshot().items()), flush=True)

    def write_manifest() -> None:   # caller holds state_lock
        manifest.write_text(json.dumps(
            {"city": args.city, "start_ts": start_ts, "zones": zones,
             "args": {k: v for k, v in vars(args).items()}, "updated_ts": int(time.time()),
             "queue": queue.snapshot(), "sessions": session_log},
            indent=2, default=str))

    def scrape_one(zone: str, session_n: int) -> tuple[int, bool, int]:
        """One deep session for `zone`: (progress, todo_was_empty, due_left)."""
        zslug = slugify(zone)
        with state_lock:
            todo = zone_todo(args.city, zone, **todo_kw)
            if once_per_run:
                # a backfill/forced session per venue at most: drop ones this run already visited
                done_ids = {x.get("venue_id") for x in session_log if x.get("stage") == "backfill"}
                todo = [v for v in todo if v.get("id") not in done_ids]
            if not todo:
                print(f"[{zslug}] TODO empty after {session_n - 1} session(s)", flush=True)
                return 0, True, 0
            batch = pick_batch(todo, args.session_todo,
                               max_places_only=args.max_places_per_batch)
            if once_per_run:
                session_log.extend({"stage": "backfill", "venue_id": v.get("id")}
                                   for v in batch)
        label = f"{arm}deep-{args.city}-{zslug}-{int(time.time())}"
        todo_path = TODO_DIR / f"{label}.json"
        todo_path.write_text(json.dumps({
            "zone": zone, "session_label": label,
            "venues": [{"name": v["name"], "address": v.get("address"),
                        "website": v.get("website"), "kind": v.get("kind"),
                        "exhibitions_url": v.get("exhibitions_url"),
                        "venue_id": v.get("id") or v.get("venue_id"),
                        "status": v.get("status"),
                        "fetch_mode": (v.get("page") or {}).get("fetch_mode"),
                        "already_saved": _already_saved(v),
                        "represents": venue_roster(args.city, v.get("id") or v.get("venue_id")),
                        "tier": v.get("tier"), "score": v.get("score"),
                        "priority": v.get("_priority"),
                        "reasons": v.get("_reasons")}
                       for v in batch],
        }, indent=2, ensure_ascii=False))
        ids = [v.get("id") or v.get("venue_id") for v in batch]
        print(f"[{zslug}] session {session_n}: {len(batch)} TODO venues -> {label}.log\n"
              f"[{zslug}]   {ids}", flush=True)
        before = zone_progress_snapshot(args.city, zone)
        cmd = [sys.executable, HERE / "run_scrape.py",
               "--city", args.city, "--deep", "--todo-file", todo_path,
               "--budget", args.session_budget,
               "--model", args.model,
               "--fetch-tokens", args.fetch_tokens,
               "--no-verify"]
        if args.effort:
            cmd += ["--effort", args.effort]
        if args.context_editing:
            cmd += ["--context-editing"]
        if args.keyword_signals:
            cmd += ["--keyword-signals"]
        t0 = int(time.time())
        rc = run_session(cmd, f"{label}.log")
        after = zone_progress_snapshot(args.city, zone)
        saved, skipped = after[0] - before[0], after[1] - before[1]
        with state_lock:
            session_log.append({
                "stage": "scrape", "zone": zone, "session": session_n,
                "todo": len(batch), "saved": saved, "skipped": skipped, "exit": rc,
                # diagnosable when the run is killed: which venues each session held
                "label": label, "venue_ids": ids, "started_ts": t0, "ended_ts": int(time.time())})
            write_manifest()
        print(f"[{zslug}] session {session_n} done: +{saved} saved, +{skipped} skipped "
              f"(exit {rc})", flush=True)
        return saved + skipped, False, len(todo) - len(batch)

    def scrape_worker(idx: int) -> None:
        time.sleep(idx * args.stagger)
        while not stop.is_set():
            with state_lock:
                limit = over_limits()
            if limit:
                print(f"[worker {idx}] stopping: {limit}", flush=True)
                stop.set()
                return
            st = queue.claim()
            if st is None:
                if queue.any_active():
                    time.sleep(5)      # a zone may free up with venues left
                    continue
                return                 # every zone exhausted
            progress, empty, due_left = 0, False, None
            try:
                progress, empty, due_left = scrape_one(st["zone"], st["sessions_run"] + 1)
            finally:
                s = queue.release(st["zone"], progress, empty, due_left)
                if s["exhausted"]:
                    print(f"[{slugify(st['zone'])}] zone done: {s['why']} after "
                          f"{s['sessions_run']} session(s)", flush=True)

    threads = [threading.Thread(target=scrape_worker, args=(i,), daemon=True)
               for i in range(min(args.workers, len(zones)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # ---------- stage 3: verify ----------
    verify_summary = None
    if args.no_verify:
        print("=== STAGE 3: verify (skipped) ===", flush=True)
    else:
        from run_verify import verify_cities
        print("=== STAGE 3: verify (pending pool) ===", flush=True)
        verify_summary = verify_cities([args.city], pending_only=True)

    # ---------- stage 4: report ----------
    if not args.no_report:
        import report
        print("=== STAGE 4: report ===", flush=True)
        out = report.generate_report(args.city, start_ts)
        print(f"report: {out}", flush=True)

    summary = {
        "city": args.city,
        "zones": zones,
        "run_spend_usd": round(run_spend(args.city, start_ts), 4),
        "shows_total": len(tools.all_city_shows(args.city)),
        "remaining_todo": {z: len(zone_todo(args.city, z, venue_ids=venue_ids, rank_kw=rank_kw))
                           for z in zones},
        "queue": queue.snapshot(),
        "sessions": session_log,
        "verification": (verify_summary or {}).get("cities"),
        "manifest": str(manifest),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
