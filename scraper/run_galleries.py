"""Galleries-first stage driver (docs/GALLERIES.md): run the gallery stages for
a city in order, each as a logged subprocess, with one manifest and one spend
ledger per run. Show scraping is deliberately NOT here — after `rank`, run

    python run_deep.py --city X --galleries-first --total-budget ... [--min-tier 2]

Stages (``--stages`` picks a subset, default all, always in this order):

    priors    city_priors.py propose --apply ; sweep fairs ; sweep lists ; candidates --apply
    find      seed_venues (places[,gpla,carla]) ; LLM knowledge seed (candidates only) ;
              enumerate zones whose registry coverage is below --min-zone-venues
    validate  validate_venues.py run --apply
    research  research_venue.py run [--limit] [--gapfill] ; check ; judge
    rank      rank_venues.py score + apply ; venue_dashboard.py

Every stage is resumable/idempotent on its own (cache keys, rerun rules), so
re-running the driver only pays for what changed. Budget: ``--total-budget`` is
checked between stages against this run's spend files (label prefixes
priors-/curate-/enum-/validate-/research-/lint-/judge- since start_ts).

    python run_galleries.py --city tokyo --total-budget 60 --research-limit 100
    python run_galleries.py --city los-angeles --stages validate,research,rank --research-limit 150
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import DEFAULT_MODEL, MODELS, SPEND_DIR  # noqa: E402

HERE = Path(__file__).resolve().parent
LOG_DIR = tools.CONTENT_DIR / "spend" / "logs"
STAGES = ("priors", "find", "validate", "research", "rank")
SPEND_PREFIXES = ("priors-", "curate-", "enum-", "validate-", "research-", "judge-", "seed-")
LLM_SEED_MIN_CONF = 0.5
LLM_SEED_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["galleries"],
    "properties": {"galleries": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "website", "district", "kind", "confidence"],
        "properties": {
            "name": {"type": "string"},
            "website": {"type": ["string", "null"]},
            "district": {"type": ["string", "null"]},
            "kind": {"type": "string", "enum": ["gallery", "museum", "nonprofit",
                                                  "project_space", "university", "other"]},
            "confidence": {"type": "number", "description": "0..1"},
        }}}},
}


def run_spend(city: str, start_ts: int) -> float:
    total = 0.0
    if not SPEND_DIR.exists():
        return total
    for f in SPEND_DIR.glob("*.json"):
        if not any(f.name.startswith(p) for p in SPEND_PREFIXES) or city not in f.name:
            continue
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ts = e.get("ts") or e.get("start_ts") or _label_ts(e.get("session") or f.stem)
        if ts and ts >= start_ts:
            total += float(e.get("cost_usd") or 0.0)
    return total


def _label_ts(label: str) -> int:
    import re
    m = re.search(r"-(\d{9,})", label or "")
    return int(m.group(1)) if m else 0


class Run:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.city = args.city
        self.start_ts = int(time.time())
        self.manifest = SPEND_DIR / f"galleries_run-{self.city}-{self.start_ts}.json"
        self.log: list[dict] = []
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        SPEND_DIR.mkdir(parents=True, exist_ok=True)
        self.save()

    def save(self) -> None:
        self.manifest.write_text(json.dumps(
            {"city": self.city, "start_ts": self.start_ts, "updated_ts": int(time.time()),
             "args": vars(self.args), "spend_usd": round(run_spend(self.city, self.start_ts), 4),
             "stages": self.log}, indent=2, ensure_ascii=False))

    def over_budget(self) -> str | None:
        spent = run_spend(self.city, self.start_ts)
        if spent >= self.args.total_budget:
            return f"budget reached (${spent:.2f} >= ${self.args.total_budget:.2f})"
        return None

    def sh(self, stage: str, name: str, cmd: list, optional: bool = False) -> int:
        """Run a stage command with its stdout in content/spend/logs/."""
        log_path = LOG_DIR / f"galleries-{self.city}-{name}-{self.start_ts}.log"
        cmd = [str(c) for c in cmd]
        print(f"[{stage}] {name}: {' '.join(cmd[1:])}\n         -> {log_path.name}", flush=True)
        t0 = time.time()
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        with open(log_path, "w") as lf:
            rc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, env=env).returncode
        tail = ""
        try:
            tail = "\n".join(log_path.read_text().splitlines()[-3:])
        except OSError:
            pass
        self.log.append({"stage": stage, "step": name, "rc": rc, "seconds": round(time.time() - t0),
                         "log": str(log_path.relative_to(tools.CONTENT_DIR.parent)),
                         "spend_after": round(run_spend(self.city, self.start_ts), 4)})
        self.save()
        print(f"         rc={rc} ({round(time.time() - t0)}s)" + (f"\n{tail}" if tail else ""), flush=True)
        if rc != 0 and not optional:
            print(f"!! {name} failed (rc={rc}); see {log_path}", flush=True)
        return rc


# --- find: LLM knowledge seed (candidates only) ----------------------------------

def llm_seed(city: str, apply: bool, model: str = DEFAULT_MODEL) -> dict:
    """One offline call: galleries the model knows in this city. Everything it
    returns is a CANDIDATE (status "candidate", sources.seed.llm) — nothing
    publishes on model memory; validation and research decide."""
    import anthropic
    import venues
    from run_scrape import load_env
    load_env()
    cfg = CITIES[city]
    zones = ", ".join(cfg["neighborhoods"])
    prompt = (f"List the contemporary art galleries, nonprofit art spaces and project spaces you know "
              f"of in {cfg.get('display_name', city)} that show contemporary art to the public. Include the "
              f"official website when you are confident of it (else null) and the district/neighborhood "
              f"(pick from: {zones}; else the district name you know). Set confidence to how sure you "
              f"are that the space exists and is still open. Do not include art fairs, auction houses, "
              f"framers, art schools, or commercial decor shops. Aim for completeness over polish; "
              f"200 entries is fine.")
    client = anthropic.Anthropic(max_retries=3)
    t0 = time.time()
    resp = client.messages.create(
        model=model, max_tokens=16000,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": LLM_SEED_SCHEMA}},
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = json.loads(text)
    u = resp.usage
    price = MODELS[model]
    cost = (u.input_tokens * price["in"] + u.output_tokens * price["out"]
            + getattr(u, "cache_read_input_tokens", 0) * price["cache_read"]) / 1e6
    ts = int(time.time())
    (SPEND_DIR / f"seed-llm-{city}-{ts}.json").write_text(json.dumps(
        {"session": f"seed-llm-{city}-{ts}", "model": model, "requests": 1,
         "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
         "cost_usd": round(cost, 4), "seconds": round(time.time() - t0)}, indent=2))
    reg = venues.load_registry(city)
    new, known = [], []
    for g in data["galleries"]:
        if g["confidence"] < LLM_SEED_MIN_CONF and not g.get("website"):
            continue   # a low-confidence memory with nothing to check is noise
        hit = venues.find_venue(reg, g["name"], g.get("website"), add_alias=False)
        (known if hit else new).append(g)
    print(f"llm seed: {len(data['galleries'])} names, {len(known)} already in registry, "
          f"{len(new)} new candidates, ${cost:.3f}")
    for g in new:
        print(f"  + {g['name']}  {g.get('district') or '-'}  {g.get('website') or ''}  "
              f"conf {g['confidence']:.2f}")
    if apply:
        for g in new:
            zone = (tools.normalize_zone(g.get("district"), cfg["neighborhoods"], city)
                    if g.get("district") else None)
            patch = {"kind": g["kind"], "status": "candidate", "website": g.get("website"),
                     "neighborhood": zone,
                     "sources": {"seed": {"llm": {"ts": ts, "confidence": g["confidence"],
                                                  "district": g.get("district")}}},
                     "notes": f"LLM knowledge seed ({g.get('district') or 'district unknown'})"}
            venues.upsert(city, g["name"], patch, "seed-llm", g.get("website"))
        for g in known:
            hit = venues.find_venue(reg, g["name"], g.get("website"), add_alias=False)
            if hit:
                venues.upsert(city, hit["name"],
                              {"sources": {"seed": {"llm": {"ts": ts, "confidence": g["confidence"]}}}},
                              "seed-llm")
        print(f"registry: {len(new)} candidates created, {len(known)} tagged sources.seed.llm")
    return {"total": len(data["galleries"]), "new": len(new), "known": len(known), "cost_usd": cost}


def zones_needing_enumeration(city: str, min_venues: int) -> list[str]:
    import venues
    reg = venues.load_registry(city)
    out = []
    for z in CITIES[city]["neighborhoods"]:
        n = sum(1 for v in reg["venues"] if v.get("neighborhood") == z
                and (v.get("sources") or {}).get("directory_session"))
        if n < min_venues:
            out.append(z)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--stages", default=",".join(STAGES))
    ap.add_argument("--total-budget", type=float, required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL, choices=sorted(MODELS))
    ap.add_argument("--effort", default="medium")
    # priors
    ap.add_argument("--priors-budget", type=float, default=1.5, help="per signal session")
    # find
    ap.add_argument("--seed-sources", default="places",
                    help="seed_venues sources before enumeration (places,gpla,carla,evidence or none)")
    ap.add_argument("--max-places-requests", type=int, default=400)
    ap.add_argument("--min-zone-venues", type=int, default=8)
    ap.add_argument("--enum-budget", type=float, default=1.5)
    ap.add_argument("--no-llm-seed", action="store_true")
    # validate
    ap.add_argument("--validate-places-requests", type=int, default=150)
    # research
    ap.add_argument("--research-limit", type=int, default=None,
                    help="fixed N (default: auto selection from the prior score distribution)")
    ap.add_argument("--research-pct", type=float, default=10.0,
                    help="auto selection: at least this %% of eligible venues by prior score")
    ap.add_argument("--research-cap", type=int, default=250, help="auto selection hard cap")
    ap.add_argument("--research-budget", type=float, default=None,
                    help="cap for the research stage (default: what is left of --total-budget)")
    ap.add_argument("--research-workers", type=int, default=4)
    ap.add_argument("--gapfill", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    # rank
    ap.add_argument("--params", default=None, help="rank_venues params file")
    ap.add_argument("--no-apply-rank", action="store_true")
    args = ap.parse_args()

    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    bad = [s for s in stages if s not in STAGES]
    if bad:
        sys.exit(f"unknown stages {bad}; valid: {STAGES}")
    stages = [s for s in STAGES if s in stages]
    run = Run(args)
    py = sys.executable
    print(f"=== galleries-first run: {args.city} stages={stages} budget=${args.total_budget:.2f} "
          f"manifest={run.manifest.name} ===", flush=True)

    for stage in stages:
        stop = run.over_budget()
        if stop:
            print(f"!! stopping before {stage}: {stop}", flush=True)
            break
        print(f"\n=== STAGE {stage} ===", flush=True)
        if stage == "priors":
            run.sh(stage, "priors-propose", [py, HERE / "city_priors.py", "propose", "--city", args.city, "--apply"])
            run.sh(stage, "priors-fairs", [py, HERE / "city_priors.py", "sweep", "--city", args.city,
                                           "--kind", "fairs", "--budget", args.priors_budget], optional=True)
            run.sh(stage, "priors-lists", [py, HERE / "city_priors.py", "sweep", "--city", args.city,
                                           "--kind", "lists", "--budget", args.priors_budget], optional=True)
            run.sh(stage, "priors-candidates", [py, HERE / "city_priors.py", "candidates", "--city", args.city, "--apply"])
        elif stage == "find":
            if args.seed_sources and args.seed_sources.lower() != "none":
                run.sh(stage, "seed", [py, HERE / "seed_venues.py", "--city", args.city, "--sources",
                                       args.seed_sources, "--apply", "--max-places-requests",
                                       args.max_places_requests])
            if not args.no_llm_seed:
                t0 = time.time()
                try:
                    rep = llm_seed(args.city, apply=True, model=args.model)
                    run.log.append({"stage": stage, "step": "llm-seed", "rc": 0, **rep,
                                    "seconds": round(time.time() - t0)})
                except Exception as e:  # noqa: BLE001 — a seed failure must not stop the run
                    run.log.append({"stage": stage, "step": "llm-seed", "rc": 1, "error": str(e)})
                    print(f"!! llm seed failed: {e}", flush=True)
                run.save()
            for zone in zones_needing_enumeration(args.city, args.min_zone_venues):
                if run.over_budget():
                    break
                zslug = tools._slugify(zone)
                label = f"enum-{args.city}-{zslug}-{int(time.time())}"
                run.sh(stage, f"enum-{zslug}",
                       [py, HERE / "run_scrape.py", "--city", args.city, "--enumerate-zone", zone,
                        "--budget", args.enum_budget, "--model", args.model, "--fetch-tokens", 8000,
                        "--session-label", label, "--no-verify", "--effort", args.effort], optional=True)
        elif stage == "validate":
            run.sh(stage, "validate", [py, HERE / "validate_venues.py", "run", "--city", args.city, "--apply",
                                       "--max-places-requests", args.validate_places_requests])
        elif stage == "research":
            # prior ranking first: research order and the auto-selected set come from it
            cmd = [py, HERE / "rank_venues.py", "apply", "--city", args.city]
            if args.params:
                cmd += ["--params", args.params]
            run.sh(stage, "rank-prior", cmd)
            spent = run_spend(args.city, run.start_ts)
            budget = args.research_budget if args.research_budget is not None else max(0.0, args.total_budget - spent)
            cmd = [py, HERE / "research_venue.py", "run", "--city", args.city,
                   "--budget", round(budget, 2), "--workers", args.research_workers]
            if args.research_limit:
                cmd += ["--limit", args.research_limit]
            else:
                cmd += ["--select", "auto", "--select-pct", args.research_pct, "--select-cap", args.research_cap]
            if args.gapfill:
                cmd.append("--gapfill")
            run.sh(stage, "research", cmd)
            run.sh(stage, "research-check", [py, HERE / "research_venue.py", "check", "--city", args.city], optional=True)
            if not args.no_judge:
                run.sh(stage, "venue-judge", [py, HERE / "research_venue.py", "judge", "--city", args.city], optional=True)
        elif stage == "rank":
            cmd = [py, HERE / "rank_venues.py", "score", "--city", args.city]
            if args.params:
                cmd += ["--params", args.params]
            run.sh(stage, "rank-score", cmd)
            if not args.no_apply_rank:
                cmd = [py, HERE / "rank_venues.py", "apply", "--city", args.city]
                if args.params:
                    cmd += ["--params", args.params]
                run.sh(stage, "rank-apply", cmd)
            run.sh(stage, "dashboard", [py, HERE / "venue_dashboard.py", "--city", args.city], optional=True)

    run.save()
    spent = run_spend(args.city, run.start_ts)
    print(f"\n=== done: ${spent:.2f} this run; manifest {run.manifest} ===", flush=True)
    print("next: python run_deep.py --city", args.city, "--galleries-first --total-budget <usd> [--min-tier 2]")


if __name__ == "__main__":
    main()
