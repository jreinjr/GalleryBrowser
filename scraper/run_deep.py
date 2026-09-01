"""Exhaustive-coverage orchestrator: enumerate a city's venues zone by zone
into a durable directory, then scrape every directory venue not yet saved or
recently skipped, then verify, then write the coverage/outlier report.

Stages:
  1. enumerate  — search-only sessions build content/spend/venue_directory-<city>.jsonl
                  (skipped for zones that already have >= --min-zone-venues entries)
  2. scrape     — workers round-robin zones; each zone loops deep sessions over its
                  TODO list (directory minus saved minus skips fresher than 10 days)
                  until the TODO is empty, progress stalls, or budget runs out
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


def zone_todo(city: str, zone: str) -> list[dict]:
    """Directory venues in a zone minus saved venues minus fresh skips."""
    directory = tools.load_directory(city)
    saved = {tools._norm_venue(s["venue"]["name"]) for s in tools.all_city_shows(city)}
    cutoff = time.time() - SKIP_FRESH_DAYS * 86400
    skipped = {tools._norm_venue(e["venue"]) for e in tools.load_skips(city)
               if e.get("ts", 0) >= cutoff}
    return [v for key, v in sorted(directory.items())
            if v["neighborhood"] == zone and key not in saved | skipped]


def saved_venue_lines(city: str) -> list[str]:
    return sorted(f"{s['venue']['name']} ({s['venue']['neighborhood']})"
                  for s in tools.all_city_shows(city))


def zone_progress_snapshot(city: str, zone: str) -> tuple[int, int]:
    """(saves in zone, logged skips in zone) — per-zone session attribution."""
    saves = sum(1 for s in tools.all_city_shows(city)
                if s["venue"]["neighborhood"] == zone)
    skips = sum(1 for e in tools.load_skips(city) if e.get("neighborhood") == zone)
    return saves, skips


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
    parser.add_argument("--max-shows", type=int, default=300,
                        help="city-wide safety cap (published + pending)")
    parser.add_argument("--min-zone-venues", type=int, default=4,
                        help="zones with fewer directory entries get enumerated")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    parser.add_argument("--enum-model", default=None, choices=sorted(MODELS),
                        help="model for enumeration sessions (default: --model)")
    parser.add_argument("--fetch-tokens", type=int, default=20000)
    parser.add_argument("--effort", default=None,
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--context-editing", action="store_true")
    parser.add_argument("--arm-label", default=None,
                        help="A/B arm tag prefixed onto session labels")
    parser.add_argument("--skip-enumeration", action="store_true")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    from run_scrape import load_env
    load_env()

    all_zones = CITIES[args.city]["neighborhoods"]
    if args.zones:
        zones = [z.strip() for z in args.zones.split(",")]
        bad = [z for z in zones if z not in all_zones]
        if bad:
            sys.exit(f"unknown zones: {bad} (valid: {all_zones})")
    else:
        zones = list(all_zones)

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

    def over_limits() -> str | None:
        spent = run_spend(args.city, start_ts)
        if spent >= args.total_budget:
            return f"total budget spent (${spent:.2f})"
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

    # ---------- stage 1: enumerate ----------
    if args.skip_enumeration:
        print("=== STAGE 1: enumerate (skipped) ===", flush=True)
    else:
        directory = tools.load_directory(args.city)
        need = [z for z in zones
                if sum(1 for v in directory.values() if v["neighborhood"] == z)
                < args.min_zone_venues]
        print(f"=== STAGE 1: enumerate {len(need)} zone(s) ===", flush=True)

        def enum_worker(idx: int, zone: str) -> None:
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
            if args.effort:
                cmd += ["--effort", args.effort]
            print(f"[enum {zone}] -> {label}.log", flush=True)
            rc = run_session(cmd, f"{label}.log")
            n = sum(1 for v in tools.load_directory(args.city).values()
                    if v["neighborhood"] == zone)
            with state_lock:
                session_log.append({"stage": "enumerate", "zone": zone,
                                    "exit": rc, "zone_directory_size": n})
            print(f"[enum {zone}] done (exit {rc}); zone directory: {n} venues",
                  flush=True)

        threads = []
        for i, z in enumerate(need):
            t = threading.Thread(target=enum_worker, args=(i, z), daemon=True)
            threads.append(t)
            t.start()
            # cap concurrency at --workers
            while sum(t.is_alive() for t in threads) >= args.workers:
                time.sleep(2)
        for t in threads:
            t.join()

    # ---------- stage 2: scrape ----------
    print("=== STAGE 2: scrape ===", flush=True)
    shards: list[list[str]] = [[] for _ in range(min(args.workers, len(zones)))]
    for i, z in enumerate(zones):
        shards[i % len(shards)].append(z)

    def scrape_worker(shard_idx: int, shard_zones: list[str]) -> None:
        time.sleep(shard_idx * args.stagger)
        for zone in shard_zones:
            zslug = slugify(zone)
            zero_progress = 0
            for session_n in range(1, args.max_sessions_per_zone + 1):
                with state_lock:
                    limit = over_limits()
                    if stop.is_set() or limit:
                        if limit:
                            stop.set()
                            print(f"[{zslug}] stopping: {limit}", flush=True)
                        return
                todo = zone_todo(args.city, zone)
                if not todo:
                    print(f"[{zslug}] TODO empty after {session_n - 1} session(s)",
                          flush=True)
                    break
                batch = todo[:args.session_todo]
                label = f"{arm}deep-{args.city}-{zslug}-{int(time.time())}"
                todo_path = TODO_DIR / f"{label}.json"
                todo_path.write_text(json.dumps({
                    "zone": zone, "session_label": label,
                    "venues": [{"name": v["name"], "address": v.get("address"),
                                "website": v.get("website"), "kind": v.get("kind")}
                               for v in batch],
                    "saved_venues": saved_venue_lines(args.city),
                }, indent=2, ensure_ascii=False))
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
                print(f"[{zslug}] session {session_n}: {len(batch)} TODO venues "
                      f"-> {label}.log", flush=True)
                rc = run_session(cmd, f"{label}.log")
                after = zone_progress_snapshot(args.city, zone)
                progress = (after[0] - before[0]) + (after[1] - before[1])
                with state_lock:
                    session_log.append({
                        "stage": "scrape", "zone": zone, "session": session_n,
                        "todo": len(batch), "saved": after[0] - before[0],
                        "skipped": after[1] - before[1], "exit": rc})
                print(f"[{zslug}] session {session_n} done: +{after[0] - before[0]} "
                      f"saved, +{after[1] - before[1]} skipped (exit {rc})", flush=True)
                zero_progress = zero_progress + 1 if progress == 0 else 0
                if zero_progress >= 2:
                    print(f"[{zslug}] two zero-progress sessions; stopping zone",
                          flush=True)
                    break

    threads = [threading.Thread(target=scrape_worker, args=(i, s), daemon=True)
               for i, s in enumerate(shards) if s]
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
        "remaining_todo": {z: len(zone_todo(args.city, z)) for z in zones},
        "sessions": session_log,
        "verification": (verify_summary or {}).get("cities"),
        "manifest": str(manifest),
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
