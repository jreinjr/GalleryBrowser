"""Fact-check audit of saved shows: verify each against the venue's own site
and apply corrections. Verdicts drive placement (in tools.confirm_show):
verified/corrected shows with resolved coordinates are promoted to the
published <city>.json the apps display; everything else stays in — or is
demoted to — content/pending/<city>.json. Nothing is deleted.

Also importable: run_scrape/run_campaign/pipeline call verify_cities() to
verify automatically after scraping (pending pool only, the cheap default).

Usage:
    python run_verify.py --all              # full audit, published + pending
    python run_verify.py --all --pending    # only shows awaiting promotion
    python run_verify.py --city tokyo
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import run_city  # noqa: E402
from run_scrape import load_env, spend_report  # noqa: E402

SHARD_THRESHOLD = 12  # cities with more shows than this verify one neighborhood per session


def city_shows(city: str) -> list[dict]:
    return tools.all_city_shows(city)  # published + pending both need verdicts


def verify_cities(cities: list[str], pending_only: bool = False,
                  stagger: float = 15.0) -> dict:
    """Cross-check + agent-verify shows for the given cities; placement
    (promote/demote between pending and published) happens in confirm_show.

    pending_only audits just the pending pool — the automation default after
    a scrape, since that is what blocks publication. A full audit (False)
    re-verifies published shows too. Returns {"sessions": [...], "cities":
    {...}} and is a cheap no-op when there is nothing to verify.
    """
    import crosscheck

    # free deterministic pass first: retire ended shows, release window-blocked
    # ones that already hold a fresh verdict
    sweep = tools.sweep_placements(list(cities))
    for a in sweep["demoted"]:
        print(f"sweep: demoted {a['city']}/{a['slug']} ({a['reason']})", flush=True)
    for a in sweep["promoted"]:
        print(f"sweep: promoted {a['city']}/{a['slug']}", flush=True)

    verdicts_now = tools.latest_verdicts()

    def inventory(c: str) -> list[dict]:
        shows = (tools._load_shows_file(tools._pending_file(c))["shows"]
                 if pending_only else tools.all_city_shows(c))
        shows = [s for s in shows if not tools.show_expired(s)]
        if pending_only:
            # skip shows only waiting out the publication window, and dateless
            # shows audited within the last week — cron runs must not re-pay
            shows = [s for s in shows if tools.verify_candidate(c, s, verdicts_now)]
        return shows

    # build jobs: (city, neighborhoods-or-None, n_shows)
    jobs: list[tuple[str, list[str] | None, int]] = []
    audited: dict[str, list[str]] = {}
    for c in cities:
        shows = inventory(c)
        if not shows:
            continue
        audited[c] = [s["slug"] for s in shows]
        if len(shows) > SHARD_THRESHOLD:
            for hood in CITIES[c]["neighborhoods"]:
                n = sum(1 for s in shows if s["venue"]["neighborhood"] == hood)
                if n:
                    jobs.append((c, [hood], n))
        else:
            jobs.append((c, None, len(shows)))
    if not jobs:
        print("nothing to verify" + (" (pending pool empty or only awaiting "
                                     "its publication window)" if pending_only else ""),
              flush=True)
        return {"sessions": [], "cities": {}, "sweep": sweep}

    affected = sorted({c for c, _, _ in jobs})
    # deterministic cross-check first (Google Places + OSM); agent sessions read its output
    print("=== cross-check pass ===", flush=True)
    crosscheck.run(affected)
    print("=== agent verification pass ===", flush=True)

    start_ts = int(time.time())
    results_lock = threading.Lock()
    session_reports: list[dict] = []

    def worker(idx: int, city: str, hoods: list[str] | None, n: int) -> None:
        time.sleep(idx * stagger)
        label = f"{city}" + (f"/{hoods[0]}" if hoods else "")
        print(f"[verify {label}] {n} shows", flush=True)
        try:
            r = run_city(
                city_key=city, target_shows=0,
                max_searches=max(8, 2 * n), max_fetches=max(12, 4 * n),
                max_iterations=max(30, 8 * n),
                budget_usd=max(1.5, 0.30 * n),
                neighborhoods=hoods, verify=True,
                verify_pending_only=pending_only,
            )
            with results_lock:
                session_reports.append({"job": label, "cost": r["cost_usd"]})
            print(f"[verify {label}] done ${r['cost_usd']:.2f}", flush=True)
        except Exception as exc:
            with results_lock:
                session_reports.append({"job": label, "error": str(exc)})
            print(f"[verify {label}] FAILED: {exc}", flush=True)

    threads = [threading.Thread(target=worker, args=(i, c, h, n), daemon=True)
               for i, (c, h, n) in enumerate(jobs)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # collect verdicts written since start (last verdict per city/slug wins)
    verdicts: dict[tuple[str, str], dict] = {}
    if tools.VERIFY_RESULTS.exists():
        for line in tools.VERIFY_RESULTS.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("ts", 0) >= start_ts:
                verdicts[(e["city"], e["slug"])] = e

    # placement already happened inside confirm_show; report the outcome
    summary = {}
    for c in affected:
        published = tools._load_shows_file(tools._city_file(c))["shows"]
        pending = tools._load_shows_file(tools._pending_file(c))["shows"]
        counts = {"verified": 0, "corrected": 0, "unverified": 0}
        for (vc, _slug), v in verdicts.items():
            if vc == c and v["status"] in counts:
                counts[v["status"]] += 1
        summary[c] = {
            **counts,
            "unchecked": [slug for slug in audited.get(c, [])
                          if (c, slug) not in verdicts],
            "published": len(published),
            "pending": [{"slug": s["slug"], "venue": s["venue"]["name"],
                         "reason": (verdicts.get((c, s["slug"])) or {}).get("reason")}
                        for s in pending],
        }
    return {"sessions": session_reports, "cities": summary, "sweep": sweep}


def verify_by_rank(city: str, total_budget: float, batch: int = 8,
                   max_rank: int | None = None) -> dict:
    """Pending-pool audit in gallery-rank order (registry `rank`, unranked
    last), one sequential session per `batch` shows, stopping before
    `total_budget` — so a capped run spends on the galleries that matter most
    rather than on whichever neighborhood comes first. `max_rank` skips shows
    at venues ranked below it (and unranked ones)."""
    import crosscheck
    import venues

    sweep = tools.sweep_placements([city])
    rank = {v["id"]: v.get("rank") or 10**6 for v in venues.load_registry(city)["venues"]}
    verdicts_now = tools.latest_verdicts()
    shows = [s for s in tools._load_shows_file(tools._pending_file(city))["shows"]
             if not tools.show_expired(s) and tools.verify_candidate(city, s, verdicts_now)]
    if max_rank is not None:
        shows = [s for s in shows if rank.get(s.get("venue_id"), 10**6) <= max_rank]
    shows.sort(key=lambda s: (rank.get(s.get("venue_id"), 10**6), s.get("start_date") or ""))
    if not shows:
        print("nothing to verify", flush=True)
        return {"sessions": [], "spent": 0.0, "sweep": sweep}
    crosscheck.run([city])
    spent, reports = 0.0, []
    for i in range(0, len(shows), batch):
        chunk = shows[i:i + batch]
        n = len(chunk)
        # the harness can overrun its budget, so leave the per-session headroom
        budget = min(max(1.5, 0.30 * n), (total_budget - spent) / 1.5)
        if budget < 1.0:
            print(f"stop: ${spent:.2f} spent of ${total_budget:.2f}", flush=True)
            break
        names = sorted({s["venue"]["name"] for s in chunk})
        print(f"[verify rank batch {i // batch + 1}] {n} shows: {', '.join(names)}", flush=True)
        r = run_city(city_key=city, target_shows=0, max_searches=max(8, 2 * n),
                     max_fetches=max(12, 4 * n), max_iterations=max(30, 8 * n),
                     budget_usd=budget, verify=True, verify_pending_only=True,
                     verify_slugs=[s["slug"] for s in chunk])
        spent += r["cost_usd"]
        reports.append({"job": f"rank batch {i // batch + 1}", "cost": r["cost_usd"]})
        print(f"[verify rank batch {i // batch + 1}] ${r['cost_usd']:.2f} (total ${spent:.2f})", flush=True)
    return {"sessions": reports, "spent": round(spent, 2), "sweep": sweep}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=sorted(CITIES))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--pending", action="store_true",
                        help="audit only the pending pool (what blocks publication)")
    parser.add_argument("--stagger", type=float, default=15.0)
    parser.add_argument("--by-rank", type=float, metavar="BUDGET",
                        help="with --city: audit the pending pool in gallery-rank order, "
                             "highest-ranked first, stopping before BUDGET USD")
    parser.add_argument("--max-rank", type=int,
                        help="with --by-rank: skip venues ranked below N (and unranked ones)")
    args = parser.parse_args()

    load_env()
    if args.by_rank is not None:
        if not args.city:
            sys.exit("--by-rank needs --city")
        out = verify_by_rank(args.city, args.by_rank, max_rank=args.max_rank)
        spend_report()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return
    cities = [args.city] if args.city else \
        [c for c in CITIES if city_shows(c)] if args.all else \
        sys.exit("pass --city or --all")

    out = verify_cities(cities, pending_only=args.pending, stagger=args.stagger)
    spend_report()
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
