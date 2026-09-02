"""Run curation signal-collection sessions (see curation_prompts.VARIANTS).

Each session gets a group of units (sources for pubsweep/fairs, pooled shows
for artist_heat), one strict record_signal tool, and a small budget. Every
signal row carries the session label as run_id, and a runs.jsonl row records
the variant, prompt hash, model, cost and counts.

Usage:
    python run_curate.py --city los-angeles --variant pubsweep_v1 --sources carla,hyperallergic-la --budget 3
    python run_curate.py --city los-angeles --variant pubsweep_v1              # every active source
    python run_curate.py --city los-angeles --variant artist_heat_v1 --slugs deitch-urs-fischer
    python run_curate.py --city los-angeles --variant fairs_v1
    python run_curate.py --city los-angeles --variant pubsweep_v1 --dry-run   # print the groups + prompts
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_prompts as cp  # noqa: E402
import curation_store as store  # noqa: E402
import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import DEFAULT_MODEL, MODELS, run_city  # noqa: E402
from run_scrape import load_env  # noqa: E402

FAIR_KINDS = ("fair",)
SWEEP_KINDS = ("publication_picks", "review_outlet", "news", "listing", "culture")


def select_units(city: str, variant: cp.Variant, args) -> list[dict]:
    if variant.unit == "show":
        pool = [s for s in tools.all_city_shows(city) if not tools.show_expired(s)]
        if args.slugs:
            want = set(args.slugs.split(","))
            pool = [s for s in pool if s["slug"] in want]
        if not args.include_group_shows:
            pool = [s for s in pool if s.get("artist")]
        return pool
    sources = [s for s in store.load_sources(city) if s.get("active", True)]
    kinds = FAIR_KINDS if variant.unit == "fair" else SWEEP_KINDS
    sources = [s for s in sources if s.get("kind") in kinds]
    if args.sources:
        want = set(args.sources.split(","))
        sources = [s for s in sources if s["id"] in want]
    return sources


def count_rows(city: str, run_id: str) -> dict:
    sig = [r for r in store.load_signals(city) if r.get("run_id") == run_id]
    cand = [r for r in store.load_candidate_events(city)
            if r.get("run_id") == run_id and r.get("event") == "seen"]
    return {"signals_recorded": len(sig), "candidates_new": len({c["key"] for c in cand})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--variant", required=True, choices=sorted(cp.VARIANTS))
    ap.add_argument("--sources", default=None, help="comma-separated source ids (pubsweep/fairs)")
    ap.add_argument("--slugs", default=None, help="comma-separated pool slugs (artist_heat)")
    ap.add_argument("--include-group-shows", action="store_true",
                    help="artist_heat: include shows with no single artist")
    ap.add_argument("--group-size", type=int, default=None)
    ap.add_argument("--max-groups", type=int, default=None)
    ap.add_argument("--budget", type=float, default=None, help="per-session soft budget USD")
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    ap.add_argument("--effort", default="medium",
                    choices=["low", "medium", "high", "xhigh", "max"])
    ap.add_argument("--fetch-tokens", type=int, default=None)
    ap.add_argument("--max-searches", type=int, default=None)
    ap.add_argument("--max-fetches", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="print groups and prompts, no API")
    args = ap.parse_args()
    load_env()

    variant = cp.VARIANTS[args.variant]
    lim = dict(variant.limits)
    group_size = args.group_size or lim["group_size"]
    units = select_units(args.city, variant, args)
    if not units:
        sys.exit("nothing to do: no matching sources/shows")
    groups = [units[i:i + group_size] for i in range(0, len(units), group_size)]
    if args.max_groups:
        groups = groups[:args.max_groups]
    cfg = CITIES[args.city]
    last = [r for r in store.load_runs(args.city) if r.get("prompt_variant") == variant.name]
    if last and last[-1].get("prompt_hash") not in (None, variant.prompt_hash()):
        print(f"note: prompt text for {variant.name} changed since its last run "
              f"({last[-1]['prompt_hash']} -> {variant.prompt_hash()}); consider bumping the version")

    print(f"{variant.name}: {len(units)} {variant.unit}(s) in {len(groups)} session(s) "
          f"of <= {group_size}; model {args.model}; hash {variant.prompt_hash()}")
    total_cost = 0.0
    for gi, group in enumerate(groups, 1):
        ctx = {"items": group}
        label = f"curate-{args.city}-{variant.name}-g{gi}-{int(time.time())}"
        names = [u.get("id") or u.get("slug") for u in group]
        if args.dry_run:
            system, first = cp.render(variant.name, args.city, cfg, ctx)
            print(f"\n=== group {gi}: {names}\n--- system ({len(system)} chars) ---\n{system[:1500]}"
                  f"\n...\n--- first message ---\n{first[:1500]}")
            continue
        print(f"\n[{gi}/{len(groups)}] {label}: {names}", flush=True)
        t0 = time.time()
        res = run_city(
            args.city, target_shows=0,
            max_searches=args.max_searches or lim["max_searches"],
            max_fetches=args.max_fetches or lim["max_fetches"],
            max_iterations=lim["max_iterations"],
            budget_usd=args.budget or lim["budget_usd"],
            model=args.model, fetch_content_tokens=args.fetch_tokens or lim["fetch_tokens"],
            effort=args.effort, session_label=label,
            signal_variant=variant.name, signal_ctx=ctx,
            search_domains=variant.search_domains(ctx))
        counts = count_rows(args.city, label)
        row = {
            "ts": int(t0), "run_id": label, "city": args.city, "stage": variant.stage,
            "prompt_variant": variant.name, "prompt_hash": variant.prompt_hash(),
            "model": args.model, "effort": args.effort,
            "sources_planned": names if variant.unit != "show" else [],
            "sources_seen": sorted({r["source"]["id"] for r in store.load_signals(args.city)
                                    if r.get("run_id") == label and r.get("source")}),
            "slugs_planned": names if variant.unit == "show" else [],
            **counts, "duplicates_rejected": None,
            "requests": res.get("requests"), "web_searches": res.get("web_searches"),
            "cost_usd": res.get("cost_usd"), "stop_reason": res.get("stop_reason"),
            "duration_s": int(time.time() - t0), "notes": (res.get("final_message") or "")[:300],
        }
        store.append_run(row)
        total_cost += res.get("cost_usd") or 0.0
        print(f"   -> {counts['signals_recorded']} signals, {counts['candidates_new']} new candidates, "
              f"${res.get('cost_usd', 0):.2f}, {row['duration_s']}s, stop={res.get('stop_reason')}",
              flush=True)
    if not args.dry_run:
        print(f"\ntotal ${total_cost:.2f} across {len(groups)} session(s)")


if __name__ == "__main__":
    main()
