"""CLI entry point for the gallery scraper agent.

Usage:
    python run_scrape.py --city seattle --target 9
    python run_scrape.py --all-secondary          # 1-3 shows for every non-Seattle city
    python run_scrape.py --report                 # print accumulated spend
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import DEFAULT_MODEL, MODELS, SPEND_DIR, run_city  # noqa: E402


def load_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def spend_report() -> dict:
    sessions = []
    if SPEND_DIR.exists():
        for f in sorted(SPEND_DIR.glob("*.json")):
            if f.name == "TOTAL.json":
                continue
            entry = json.loads(f.read_text())
            if isinstance(entry, dict) and "cost_usd" in entry:  # skip non-ledger files
                sessions.append(entry)
    total = {
        "total_cost_usd": round(sum(s["cost_usd"] for s in sessions), 4),
        "total_requests": sum(s["requests"] for s in sessions),
        "total_web_searches": sum(s["web_searches"] for s in sessions),
        "total_input_tokens": sum(s["input_tokens"] for s in sessions),
        "total_output_tokens": sum(s["output_tokens"] for s in sessions),
        "total_cache_write_tokens": sum(s["cache_write_tokens"] for s in sessions),
        "total_cache_read_tokens": sum(s["cache_read_tokens"] for s in sessions),
        "sessions": sessions,
    }
    SPEND_DIR.mkdir(parents=True, exist_ok=True)
    (SPEND_DIR / "TOTAL.json").write_text(json.dumps(total, indent=2))
    return total


def format_todo_message(city_display: str, zone: str, venues: list[dict],
                        saved_venues: list[str]) -> str:
    """First user message for a deep session: the zone's TODO batch plus a
    compact city-wide saved-venues exclusion list."""
    lines = []
    for i, v in enumerate(venues, 1):
        bits = [v["name"]]
        if v.get("address"):
            bits.append(v["address"])
        if v.get("website"):
            bits.append(v["website"])
        kind = f" ({v['kind']})" if v.get("kind") else ""
        lines.append(f"{i}. {' — '.join(bits)}{kind}")
    msg = (f"Work your TODO list for the {zone} zone of {city_display}.\n\n"
           "TODO — attempt each of these venues this session, in order:\n"
           + "\n".join(lines)
           + "\n\nEvery TODO venue must end in exactly one save_show or log_skip.")
    if saved_venues:
        msg += ("\n\nSAVED VENUES city-wide — never save a show at any of these:\n"
                + "; ".join(saved_venues))
    return msg


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=sorted(CITIES))
    parser.add_argument("--target", type=int, default=3, help="shows to save")
    parser.add_argument("--all-secondary", action="store_true",
                        help="run every non-Seattle city at 3 shows each")
    parser.add_argument("--enrich", type=int, metavar="MIN_IMAGES", default=None,
                        help="enrich imagery for already-saved shows in --city "
                             "up to at least MIN_IMAGES images each")
    parser.add_argument("--report", action="store_true", help="print spend report only")
    parser.add_argument("--budget", type=float, default=None,
                        help="soft per-city budget in USD")
    parser.add_argument("--neighborhoods", default=None,
                        help="comma-separated subset of the city's neighborhoods to "
                             "restrict this session to (shard for parallel campaigns)")
    parser.add_argument("--campaign", action="store_true",
                        help="campaign mode: prioritize notable venues, curate featured, "
                             "skip image-poor galleries")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the automatic post-scrape verification pass "
                             "(scraped shows then stay in the pending pool)")
    parser.add_argument("--deep", action="store_true",
                        help="exhaustive mode: work a venue TODO list (requires --todo-file); "
                             "every TODO venue ends in save_show or log_skip")
    parser.add_argument("--todo-file", default=None,
                        help="JSON file with {zone, venues, saved_venues, session_label?} "
                             "for a --deep session (written by run_deep.py)")
    parser.add_argument("--enumerate-zone", default=None, metavar="ZONE",
                        help="enumeration session: build the venue directory for one zone "
                             "instead of scraping shows")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS),
                        help="model for this session")
    parser.add_argument("--fetch-tokens", type=int, default=20000,
                        help="max_content_tokens per web_fetch (context-cost lever)")
    parser.add_argument("--effort", default=None,
                        choices=["low", "medium", "high", "xhigh", "max"],
                        help="output_config.effort (models that support it)")
    parser.add_argument("--context-editing", action="store_true",
                        help="enable clear_tool_uses context editing (beta)")
    parser.add_argument("--session-label", default=None,
                        help="explicit spend-ledger label (e.g. deep-los-angeles-hollywood-3)")
    parser.add_argument("--ab-sandbox", default=None, metavar="DIR",
                        help="A/B sandbox: write shows/images under DIR instead of content/ "
                             "and skip coordinate resolution")
    args = parser.parse_args()

    if args.report:
        print(json.dumps(spend_report(), indent=2))
        return

    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (add it to .env or the environment)")

    if args.ab_sandbox:
        tools.set_sandbox(args.ab_sandbox)

    todo = None
    if args.deep:
        if not args.todo_file:
            sys.exit("--deep requires --todo-file")
        todo = json.loads(Path(args.todo_file).read_text())

    runs: list[tuple[str, int]] = []
    if args.all_secondary:
        runs = [(c, 3) for c in CITIES if c != "seattle"]
    elif args.city:
        runs = [(args.city, args.target)]
    else:
        sys.exit("pass --city or --all-secondary")

    results = []
    for city, target in runs:
        common = dict(
            model=args.model,
            fetch_content_tokens=args.fetch_tokens,
            effort=args.effort,
            context_editing=args.context_editing,
            session_label=args.session_label or (todo or {}).get("session_label"),
        )
        if args.enumerate_zone:
            budget = args.budget if args.budget is not None else 1.5
            print(f"=== {city} (enumerate {args.enumerate_zone}, budget ${budget:.2f}) ===")
            result = run_city(
                city_key=city, target_shows=0,
                max_searches=14, max_fetches=10, max_iterations=25,
                budget_usd=budget,
                neighborhoods=[args.enumerate_zone],
                enumerate_zone=args.enumerate_zone,
                **common,
            )
        elif args.deep:
            venues = todo["venues"]
            target = len(venues)
            budget = args.budget if args.budget is not None else 4.5
            msg = format_todo_message(CITIES[city]["display_name"], todo["zone"],
                                      venues, todo.get("saved_venues", []))
            print(f"=== {city} (deep {todo['zone']}: {target} TODO venues, "
                  f"budget ${budget:.2f}, model {args.model}) ===")
            result = run_city(
                city_key=city, target_shows=target,
                max_searches=max(12, 3 * target),
                max_fetches=max(18, 5 * target),
                max_iterations=max(45, 9 * target),
                budget_usd=budget,
                neighborhoods=[todo["zone"]],
                deep=True, first_user_message=msg,
                **common,
            )
        else:
            big = target >= 6 or (args.enrich is not None and city == "seattle")
            budget = args.budget if args.budget is not None else (8.0 if big else 3.0)
            mode = f"enrich to {args.enrich}+ images" if args.enrich else f"target {target} shows"
            print(f"=== {city} ({mode}, budget ${budget:.2f}) ===")
            result = run_city(
                city_key=city,
                target_shows=target,
                max_searches=max(12, 4 * target),
                max_fetches=max(18, 6 * target),
                max_iterations=max(45, 10 * target),
                budget_usd=budget,
                enrich_min_images=args.enrich,
                neighborhoods=[n.strip() for n in args.neighborhoods.split(",")]
                              if args.neighborhoods else None,
                campaign=args.campaign,
                **common,
            )
        results.append(result)
        print(json.dumps(result, indent=2))

    # Scraped shows land in content/pending/ and are displayed only once a
    # verification pass promotes them, so verifying is part of scraping.
    if (not args.no_verify and args.enrich is None
            and not args.deep and not args.enumerate_zone):
        from run_verify import verify_cities
        print("\n=== POST-SCRAPE VERIFICATION (pending pool) ===", flush=True)
        verify_summary = verify_cities(sorted({c for c, _ in runs}), pending_only=True)
        print(json.dumps(verify_summary["cities"], indent=2, ensure_ascii=False))

    total = spend_report()
    print(f"\n=== RUN COMPLETE — total spend so far: ${total['total_cost_usd']:.2f} "
          f"({total['total_web_searches']} web searches) ===")
    for r in results:
        print(f"  {r['city']}: {r['shows_saved']} shows, ${r['cost_usd']:.2f}")


if __name__ == "__main__":
    main()
