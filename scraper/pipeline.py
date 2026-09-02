"""One-command content pipeline: scrape -> verify -> build -> deploy.

The verification gate makes every stage safe to automate: scraped shows land
in content/pending/, and only shows a fact-check pass promoted to the
published <city>.json ever reach the built site — a failed or partial scrape
cannot publish bad data, so this can run unattended (cron/launchd).

Usage:
    python pipeline.py --city berlin --target 3        # scrape, verify, ship
    python pipeline.py --all-secondary                 # every non-Seattle city
    python pipeline.py --campaign --city los-angeles --max-shows 50
    python pipeline.py --verify-only                   # just promote pending + ship
    python pipeline.py --verify-only --full            # full re-audit of everything
Flags --no-build / --no-deploy stop the pipeline earlier.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from run_scrape import load_env, spend_report  # noqa: E402
from run_verify import verify_cities  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DIST = ROOT / "webdemo" / "dist" / "gallery-browser-demo"
PROD_URL = "https://gallery-browser-demo.vercel.app"


def sh(cmd: list, **kw) -> int:
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run([str(c) for c in cmd], **kw).returncode


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--city", choices=sorted(CITIES))
    p.add_argument("--target", type=int, default=3, help="shows to scrape")
    p.add_argument("--budget", type=float, default=None, help="scrape budget USD")
    p.add_argument("--neighborhoods", default=None)
    p.add_argument("--all-secondary", action="store_true",
                   help="scrape every non-Seattle city at 3 shows each")
    p.add_argument("--campaign", action="store_true",
                   help="big-city parallel campaign via run_campaign.py")
    p.add_argument("--max-shows", type=int, default=50)
    p.add_argument("--session-target", type=int, default=7)
    p.add_argument("--session-budget", type=float, default=4.0)
    p.add_argument("--total-budget", type=float, default=30.0)
    p.add_argument("--verify-only", action="store_true", help="skip the scrape stage")
    p.add_argument("--full", action="store_true",
                   help="verify published shows too, not just the pending pool")
    p.add_argument("--no-build", action="store_true", help="stop after verify")
    p.add_argument("--no-deploy", action="store_true", help="stop after build")
    args = p.parse_args()

    load_env()
    failures: list[str] = []

    # ---------- stage 1: scrape ----------
    scraped_cities: list[str] = []
    if args.verify_only:
        print("=== STAGE 1/4: scrape (skipped) ===", flush=True)
    else:
        if args.campaign:
            if not args.city:
                sys.exit("--campaign needs --city")
            cmd = [sys.executable, HERE / "run_campaign.py", "--city", args.city,
                   "--max-shows", args.max_shows, "--session-target", args.session_target,
                   "--session-budget", args.session_budget,
                   "--total-budget", args.total_budget, "--no-verify"]
            scraped_cities = [args.city]
        elif args.all_secondary:
            cmd = [sys.executable, HERE / "run_scrape.py", "--all-secondary", "--no-verify"]
            scraped_cities = [c for c in CITIES if c != "seattle"]
        elif args.city:
            cmd = [sys.executable, HERE / "run_scrape.py", "--city", args.city,
                   "--target", args.target, "--no-verify"]
            if args.budget is not None:
                cmd += ["--budget", args.budget]
            if args.neighborhoods:
                cmd += ["--neighborhoods", args.neighborhoods]
            scraped_cities = [args.city]
        else:
            sys.exit("pass --city, --all-secondary, or --verify-only")
        print("=== STAGE 1/4: scrape ===", flush=True)
        rc = sh(cmd)
        if rc != 0:
            failures.append(f"scrape exited {rc}")
            print(f"scrape exited {rc}; continuing — whatever was saved sits safely "
                  "in the pending pool", flush=True)

    # ---------- stage 2: verify (the publication gate) ----------
    print("=== STAGE 2/4: verify ===", flush=True)
    cities = scraped_cities or [c for c in sorted(CITIES) if tools.all_city_shows(c)]
    vs = verify_cities(cities, pending_only=not args.full)
    for job in vs["sessions"]:
        if "error" in job:
            failures.append(f"verify {job['job']}: {job['error']}")

    # ---------- stage 3: build ----------
    built = False
    if args.no_build:
        print("=== STAGE 3/4: build (skipped) ===", flush=True)
    else:
        print("=== STAGE 3/4: build ===", flush=True)
        rc = sh([sys.executable, ROOT / "webdemo" / "build.py"])
        built = rc == 0
        if rc != 0:
            failures.append(f"build exited {rc}")

    # ---------- stage 4: deploy ----------
    if args.no_deploy or args.no_build or not built:
        print("=== STAGE 4/4: deploy (skipped) ===", flush=True)
    else:
        print("=== STAGE 4/4: deploy ===", flush=True)
        rc = sh(["vercel", "deploy", "--prod", "--yes"], cwd=DIST)
        if rc != 0:
            failures.append(f"deploy exited {rc}")
        else:
            print(f"deployed: {PROD_URL}", flush=True)

    # ---------- summary ----------
    total = spend_report()
    print("\n=== PIPELINE SUMMARY ===")
    sweep = vs.get("sweep", {})
    for a in sweep.get("demoted", []):
        print(f"  unpublished {a['city']}/{a['slug']} ({a['reason']})")
    for a in sweep.get("promoted", []):
        print(f"  published {a['city']}/{a['slug']} (entered its window)")
    for c in cities:
        published = len(tools._load_shows_file(tools._city_file(c))["shows"])
        pending = tools._load_shows_file(tools._pending_file(c))["shows"]
        reasons = {x["slug"]: x.get("reason")
                   for x in vs["cities"].get(c, {}).get("pending", [])}
        verdicts_now = tools.latest_verdicts()

        def why(s: dict) -> str:
            if reasons.get(s["slug"]) and tools.dates_ok(s):
                return reasons[s["slug"]]
            return tools.pending_reason(c, s, verdicts_now)

        line = f"  {c}: {published} published, {len(pending)} pending"
        if pending:
            line += " (" + "; ".join(f"{s['slug']}: {why(s)}" for s in pending) + ")"
        print(line)
    print(f"  total spend to date: ${total['total_cost_usd']:.2f}")
    if failures:
        print("  FAILURES: " + "; ".join(failures))
        sys.exit(1)
    print("  all stages OK")


if __name__ == "__main__":
    main()
