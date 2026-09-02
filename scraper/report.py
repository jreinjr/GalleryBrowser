"""Post-run coverage + outlier report for a deep scrape.

Assembles, per city:
  - run stats (spend split enumerate / scrape / verify, sessions, searches)
  - published shows by zone (marking ones verified during this run)
  - pending shows with the reason they aren't published
  - OUTLIERS: every venue discovered but not published, with the reason —
    merged from log_skip entries, harness session events (validator fights,
    refusals, budget stops), unverified verdicts, and directory venues never
    attempted at all
  - per-zone coverage accounting (directory vs saved vs skipped vs remaining)
  - orphan image directories

Usage:
    python report.py --city los-angeles --since auto   # latest run_deep manifest
    python report.py --city los-angeles --since 1788300000
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402
from harness import SPEND_DIR  # noqa: E402

REPORT_DIR = tools.CONTENT_DIR / "spend" / "reports"


def _label_ts(label: str) -> int:
    m = re.search(r"-(\d{9,})$", label or "")
    return int(m.group(1)) if m else 0


def _ledgers(city: str, since_ts: int) -> dict[str, list[dict]]:
    """Session ledgers since the run start, bucketed enumerate/scrape/verify."""
    buckets: dict[str, list[dict]] = {"enumerate": [], "scrape": [], "verify": []}
    if not SPEND_DIR.exists():
        return buckets
    for f in SPEND_DIR.glob(f"*{city}-*.json"):
        try:
            e = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        label = e.get("session", "")
        if not isinstance(e, dict) or "cost_usd" not in e or _label_ts(label) < since_ts:
            continue
        if label.startswith("enum-"):
            buckets["enumerate"].append(e)
        elif f"deep-{city}" in label:
            buckets["scrape"].append(e)
        elif label.startswith("verify-"):
            buckets["verify"].append(e)
        elif label.startswith(city):
            buckets["scrape"].append(e)  # plain run_scrape sessions in-window
    return buckets


def _events(city: str, since_ts: int) -> list[dict]:
    out = []
    if tools.EVENTS_FILE.exists():
        for line in tools.EVENTS_FILE.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("city") == city and e.get("ts", 0) >= since_ts:
                out.append(e)
    return out


def _verdicts_since(city: str, since_ts: int) -> dict[str, dict]:
    """Latest in-window verdict per slug."""
    out: dict[str, dict] = {}
    if tools.VERIFY_RESULTS.exists():
        for line in tools.VERIFY_RESULTS.read_text().splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("city") == city and e.get("ts", 0) >= since_ts:
                out[e["slug"]] = e
    return out


def _md_escape(s: str | None) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def generate_report(city: str, since_ts: int, out: Path | None = None) -> Path:
    cfg = CITIES[city]
    zones = cfg["neighborhoods"]
    now = int(time.time())
    published = tools._load_shows_file(tools._city_file(city))["shows"]
    pending = tools._load_shows_file(tools._pending_file(city))["shows"]
    directory = tools.load_directory(city)
    skips_all = tools.load_skips(city)
    skips = [s for s in skips_all if s.get("ts", 0) >= since_ts]
    events = _events(city, since_ts)
    verdicts_since = _verdicts_since(city, since_ts)
    verdicts_all = tools.latest_verdicts()
    ledgers = _ledgers(city, since_ts)

    saved_keys = {tools._norm_venue(s["venue"]["name"]) for s in published + pending}
    skip_keys = {tools._norm_venue(s["venue"]) for s in skips_all
                 if s.get("ts", 0) >= since_ts}

    lines: list[str] = []
    w = lines.append
    w(f"# {cfg['display_name']} deep run report")
    w("")
    w(f"Run window: {datetime.fromtimestamp(since_ts):%Y-%m-%d %H:%M} -> "
      f"{datetime.fromtimestamp(now):%Y-%m-%d %H:%M}")
    w("")

    # ---- run stats ----
    w("## Run stats")
    w("")
    w("| stage | sessions | requests | searches | cost |")
    w("|---|---|---|---|---|")
    total_cost = 0.0
    for stage in ("enumerate", "scrape", "verify"):
        ss = ledgers[stage]
        cost = sum(s["cost_usd"] for s in ss)
        total_cost += cost
        w(f"| {stage} | {len(ss)} | {sum(s['requests'] for s in ss)} | "
          f"{sum(s['web_searches'] for s in ss)} | ${cost:.2f} |")
    w(f"| **total** | | | | **${total_cost:.2f}** |")
    w("")

    # ---- published by zone ----
    new_slugs = {slug for slug, v in verdicts_since.items()
                 if v["status"] in ("verified", "corrected")}
    w(f"## Published ({len(published)} shows)")
    w("")
    by_zone: dict[str, list[dict]] = defaultdict(list)
    for s in published:
        by_zone[s["venue"]["neighborhood"]].append(s)
    for zone in zones:
        shows = by_zone.get(zone, [])
        if not shows:
            continue
        w(f"**{zone}** ({len(shows)}):")
        for s in sorted(shows, key=lambda x: x["venue"]["name"].lower()):
            mark = " *(new this run)*" if s["slug"] in new_slugs else ""
            w(f"- {s['venue']['name']} — \"{s['title']}\" "
              f"({s.get('start_date') or '?'} to {s.get('end_date') or '?'}){mark}")
        w("")

    # ---- pending with reasons ----
    w(f"## Pending ({len(pending)} shows)")
    w("")
    if pending:
        w("| venue | zone | show | why not published |")
        w("|---|---|---|---|")
        for s in sorted(pending, key=lambda x: x["venue"]["name"].lower()):
            w(f"| {_md_escape(s['venue']['name'])} | {s['venue']['neighborhood']} "
              f"| {_md_escape(s['title'])} "
              f"| {_md_escape(tools.pending_reason(city, s, verdicts_all))} |")
    else:
        w("(none)")
    w("")

    # ---- outliers ----
    w("## Outliers — discovered but not published")
    w("")
    rows: dict[str, dict] = {}

    def add_row(venue: str, zone: str, reason: str, detail: str, source: str):
        key = tools._norm_venue(venue) if venue else f"?{len(rows)}"
        if key in saved_keys:
            return  # it did get saved (pending shows are listed above)
        if key not in rows:
            rows[key] = {"venue": venue, "zone": zone, "reason": reason,
                         "detail": detail, "source": source}

    for s in skips:
        add_row(s["venue"], s.get("neighborhood", "?"), s["reason"],
                s.get("detail", ""), s.get("url") or "log_skip")
    for slug, v in verdicts_since.items():
        if v["status"] != "unverified":
            continue
        show = next((s for s in pending + published if s["slug"] == slug), None)
        if show:
            add_row(show["venue"]["name"], show["venue"]["neighborhood"],
                    "unverified", v.get("reason") or "", "verify pass")
    ev_by_venue: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("kind") == "tool_error":
            ev_by_venue[e.get("venue") or e.get("slug") or e.get("show_slug") or ""].append(e)
    for venue, evs in ev_by_venue.items():
        if not venue:
            continue
        last = evs[-1]
        add_row(venue, "?", "validator_rejection",
                f"{len(evs)} tool error(s); last: {last.get('error', '')[:160]}",
                last.get("session", "events"))
    for key, v in sorted(directory.items()):
        if key not in saved_keys and key not in skip_keys:
            add_row(v["name"], v["neighborhood"], "never_attempted",
                    "still on the TODO list (budget/sessions ended first)",
                    "directory")

    if rows:
        w(f"{len(rows)} outlier venue(s):")
        w("")
        w("| venue | zone | reason | detail | source |")
        w("|---|---|---|---|---|")
        order = {"unverified": 0, "no_image": 1, "low_res_only": 1,
                 "validator_rejection": 2, "closed_or_between_shows": 3,
                 "appointment_only": 4, "duplicate": 5, "out_of_scope": 6,
                 "other": 7, "never_attempted": 8}
        for r in sorted(rows.values(),
                        key=lambda r: (order.get(r["reason"], 9), r["venue"].lower())):
            w(f"| {_md_escape(r['venue'])} | {r['zone']} | {r['reason']} "
              f"| {_md_escape(r['detail'])} | {_md_escape(r['source'])} |")
    else:
        w("(none recorded)")
    w("")

    # ---- session-level incidents ----
    incidents = [e for e in events
                 if e.get("kind") in ("refusal", "budget_hard_stop")]
    if incidents:
        w("## Session incidents")
        w("")
        for e in incidents:
            w(f"- {e['kind']} in `{e.get('session', '?')}`: "
              f"{_md_escape(str(e.get('detail') or e.get('spent') or ''))}")
        w("")

    # ---- coverage accounting ----
    w("## Coverage by zone")
    w("")
    w("| zone | directory | venues saved (all-time) | shows saved | skipped this run | remaining TODO |")
    w("|---|---|---|---|---|---|")
    for zone in zones:
        dir_n = sum(1 for v in directory.values() if v["neighborhood"] == zone)
        zone_shows = [s for s in published + pending if s["venue"]["neighborhood"] == zone]
        venues_n = len({tools._norm_venue(s["venue"]["name"]) for s in zone_shows})
        skip_n = sum(1 for s in skips if s.get("neighborhood") == zone)
        remaining = sum(1 for key, v in directory.items()
                        if v["neighborhood"] == zone
                        and key not in saved_keys and key not in skip_keys)
        w(f"| {zone} | {dir_n} | {venues_n} | {len(zone_shows)} | {skip_n} | {remaining} |")
    w("")
    reason_counts: dict[str, int] = defaultdict(int)
    for s in skips:
        reason_counts[s["reason"]] += 1
    if reason_counts:
        w("Skips this run by reason: "
          + ", ".join(f"{k} {v}" for k, v in sorted(reason_counts.items(),
                                                    key=lambda kv: -kv[1])))
        w("")

    # ---- orphan image dirs ----
    img_root = tools.IMAGES_DIR / city
    if img_root.is_dir():
        referenced = {Path(img).parts[2] for s in published + pending
                      for img in s["images"] if len(Path(img).parts) > 2}
        orphans = sorted(d.name for d in img_root.iterdir()
                         if d.is_dir() and d.name not in referenced)
        if orphans:
            w("## Orphan image directories (downloaded, never saved)")
            w("")
            for o in orphans:
                w(f"- content/images/{city}/{o}/")
            w("")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = out or REPORT_DIR / f"{city}-deep-{now}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True, choices=sorted(CITIES))
    parser.add_argument("--since", default="auto",
                        help="'auto' (latest deep_run manifest) or a unix timestamp")
    args = parser.parse_args()

    if args.since == "auto":
        manifests = sorted(SPEND_DIR.glob(f"deep_run-{args.city}-*.json"))
        if not manifests:
            sys.exit(f"no deep_run manifest for {args.city}; pass --since <ts>")
        since_ts = json.loads(manifests[-1].read_text())["start_ts"]
    else:
        since_ts = int(args.since)

    path = generate_report(args.city, since_ts)
    print(path)
    print(path.read_text())


if __name__ == "__main__":
    main()
