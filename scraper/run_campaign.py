"""Multi-session campaign orchestrator: scrape a large city in parallel
neighborhood shards, each shard running sequential run_scrape.py sessions
until the city hits --max-shows, the shard saturates, or budget runs out.

Usage:
    python run_campaign.py --city los-angeles --max-shows 50 \
        --session-target 7 --session-budget 4 --total-budget 30
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import SPEND_DIR  # noqa: E402

HERE = Path(__file__).resolve().parent
LOG_DIR = tools.CONTENT_DIR / "spend" / "logs"


def show_count(city: str) -> int:
    path = tools.CONTENT_DIR / f"{city}.json"
    if not path.exists():
        return 0
    return len(json.loads(path.read_text()).get("shows", []))


def campaign_spend(city: str, since_ts: int) -> float:
    """Sum session ledgers for this city written since the campaign started."""
    total = 0.0
    if not SPEND_DIR.exists():
        return total
    for f in SPEND_DIR.glob(f"{city}-*.json"):
        m = re.match(rf"{re.escape(city)}-(\d+)\.json$", f.name)
        if not m or int(m.group(1)) < since_ts:
            continue
        try:
            total += json.loads(f.read_text()).get("cost_usd", 0.0)
        except (json.JSONDecodeError, OSError):
            pass  # mid-write; it'll count next check
    return total


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True, choices=sorted(CITIES))
    parser.add_argument("--max-shows", type=int, default=50)
    parser.add_argument("--session-target", type=int, default=7)
    parser.add_argument("--session-budget", type=float, default=4.0)
    parser.add_argument("--total-budget", type=float, default=30.0)
    parser.add_argument("--max-sessions-per-shard", type=int, default=4)
    parser.add_argument("--shards", type=int, default=None,
                        help="number of parallel shards (default: one per neighborhood)")
    parser.add_argument("--stagger", type=float, default=30.0,
                        help="seconds between shard starts")
    args = parser.parse_args()

    hoods = CITIES[args.city]["neighborhoods"]
    n_shards = min(args.shards or len(hoods), len(hoods))
    # round-robin neighborhoods into shards so counts stay balanced
    shards: list[list[str]] = [[] for _ in range(n_shards)]
    for i, hood in enumerate(hoods):
        shards[i % n_shards].append(hood)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    start_ts = int(time.time())
    state_lock = threading.Lock()
    stop = threading.Event()
    log: list[dict] = []

    def worker(shard_idx: int, shard_hoods: list[str]) -> None:
        time.sleep(shard_idx * args.stagger)
        shard_slug = slugify(shard_hoods[0])
        for session_n in range(1, args.max_sessions_per_shard + 1):
            with state_lock:
                count = show_count(args.city)
                spent = campaign_spend(args.city, start_ts)
                if stop.is_set() or count >= args.max_shows:
                    reason = "max shows reached"
                elif spent >= args.total_budget:
                    reason = f"campaign budget spent (${spent:.2f})"
                else:
                    reason = None
                if reason:
                    print(f"[shard {shard_slug}] stopping before session {session_n}: {reason}",
                          flush=True)
                    if count >= args.max_shows or spent >= args.total_budget:
                        stop.set()
                    return
                target = min(args.session_target, args.max_shows - count)

            before = show_count(args.city)
            log_path = LOG_DIR / f"{args.city}-{shard_slug}-{session_n}.log"
            cmd = [
                sys.executable, str(HERE / "run_scrape.py"),
                "--city", args.city,
                "--neighborhoods", ",".join(shard_hoods),
                "--target", str(target),
                "--budget", str(args.session_budget),
                "--campaign",
            ]
            print(f"[shard {shard_slug}] session {session_n}: target {target} -> {log_path.name}",
                  flush=True)
            with open(log_path, "w") as lf:
                proc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT)
            added = show_count(args.city) - before
            with state_lock:
                log.append({"shard": shard_slug, "session": session_n,
                            "added": added, "exit": proc.returncode})
            print(f"[shard {shard_slug}] session {session_n} done: +{added} shows "
                  f"(exit {proc.returncode})", flush=True)
            if added < 2:
                print(f"[shard {shard_slug}] saturated (+{added}); stopping shard", flush=True)
                return

    threads = [threading.Thread(target=worker, args=(i, s), daemon=True)
               for i, s in enumerate(shards)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = {
        "city": args.city,
        "final_show_count": show_count(args.city),
        "campaign_spend_usd": round(campaign_spend(args.city, start_ts), 4),
        "sessions": log,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
