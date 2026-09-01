"""Fact-check audit of saved shows: verify each against the venue's own site
and apply corrections. Verdicts drive placement (in tools.confirm_show):
verified/corrected shows with resolved coordinates are promoted to the
published <city>.json the apps display; everything else stays in — or is
demoted to — content/pending/<city>.json. Nothing is deleted.

Usage:
    python run_verify.py --all
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=sorted(CITIES))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--stagger", type=float, default=15.0)
    args = parser.parse_args()

    load_env()
    cities = [args.city] if args.city else \
        [c for c in CITIES if city_shows(c)] if args.all else \
        sys.exit("pass --city or --all")

    # deterministic cross-check first (Google Places + OSM); agent sessions read its output
    import crosscheck
    print("=== cross-check pass ===", flush=True)
    crosscheck.run(cities)
    print("=== agent verification pass ===", flush=True)

    # build jobs: (city, neighborhoods-or-None, n_shows)
    jobs: list[tuple[str, list[str] | None, int]] = []
    for c in cities:
        shows = city_shows(c)
        if not shows:
            continue
        if len(shows) > SHARD_THRESHOLD:
            for hood in CITIES[c]["neighborhoods"]:
                n = sum(1 for s in shows if s["venue"]["neighborhood"] == hood)
                if n:
                    jobs.append((c, [hood], n))
        else:
            jobs.append((c, None, len(shows)))

    start_ts = int(time.time())
    results_lock = threading.Lock()
    session_reports = []

    def worker(idx: int, city: str, hoods: list[str] | None, n: int) -> None:
        time.sleep(idx * args.stagger)
        label = f"{city}" + (f"/{hoods[0]}" if hoods else "")
        print(f"[verify {label}] {n} shows", flush=True)
        try:
            r = run_city(
                city_key=city, target_shows=0,
                max_searches=max(8, 2 * n), max_fetches=max(12, 4 * n),
                max_iterations=max(30, 8 * n),
                budget_usd=max(1.5, 0.30 * n),
                neighborhoods=hoods, verify=True,
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
    for c in cities:
        published = tools._load_shows_file(tools._city_file(c))["shows"]
        pending = tools._load_shows_file(tools._pending_file(c))["shows"]
        if not published and not pending:
            continue
        counts = {"verified": 0, "corrected": 0, "unverified": 0}
        unchecked = []
        for s in published + pending:
            v = verdicts.get((c, s["slug"]))
            if v is None:
                unchecked.append(s["slug"])
            else:
                counts[v["status"]] += 1
        summary[c] = {
            **counts, "unchecked": unchecked, "published": len(published),
            "pending": [{"slug": s["slug"], "venue": s["venue"]["name"],
                         "reason": (verdicts.get((c, s["slug"])) or {}).get("reason")}
                        for s in pending],
        }

    spend_report()
    print(json.dumps({"sessions": session_reports, "cities": summary}, indent=2,
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
