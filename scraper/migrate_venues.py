"""One-shot seed of the venue registry (content/venues/<city>.json) from
everything already on disk, so the first refresh-aware run starts with a
full picture instead of an empty registry.

Seed order (earlier sources win on conflict, later ones fill gaps):
  1. published + pending show records (venue facts, coords, listing URL from
     source_urls, last_known_shows; also backfills `venue_id` on the record)
  2. content/spend/venue_directory-<city>.jsonl (venues never scraped)
  3. content/spend/crosscheck.json (Google listing block; CLOSED_PERMANENTLY)
  4. content/spend/skips.jsonl (latest skip per venue -> status / next_check)

Usage:
    python migrate_venues.py --city los-angeles          # dry run: prints the diff
    python migrate_venues.py --all --apply               # write registries + venue_id backfill
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

CROSSCHECK = tools.CONTENT_DIR / "spend" / "crosscheck.json"


def _jsonl(path: Path) -> list[dict]:
    out = []
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _get_or_create(reg: dict, name: str, website: str | None,
                   collisions: list[str]) -> dict:
    v = venues.find_venue(reg, name, website)
    if v is not None:
        d_new, d_old = venues.registrable_domain(website), venues.registrable_domain(v.get("website"))
        if d_new and d_old and d_new != d_old and venues.venue_id(name) == v["id"]:
            collisions.append(f"{v['id']}: {v['website']} vs {website} ({name!r})")
        return v
    v = venues.empty_venue(venues.venue_id(name), name)
    reg["venues"].append(v)
    return v


def seed_city(city: str, today: date) -> tuple[dict, dict, list[str]]:
    """Build the registry for one city in memory. Returns (registry,
    {slug: venue_id} backfill map, collision notes)."""
    reg = venues.load_registry(city)
    collisions: list[str] = []
    verdicts = tools.latest_verdicts()
    backfill: dict[str, str] = {}

    # 1. shows -----------------------------------------------------------------
    for placement, path in (("published", tools._city_file(city)),
                            ("pending", tools._pending_file(city))):
        for s in tools._load_shows_file(path)["shows"]:
            vd = s["venue"]
            v = _get_or_create(reg, vd["name"], vd.get("website"), collisions)
            venues.merge_patch(v, venues._venue_patch_from_show(s, placement))
            shows = v.setdefault("sources", {}).setdefault("shows", [])
            if s["slug"] not in shows:
                shows.append(s["slug"])
            verdict = verdicts.get((city, s["slug"]))
            if verdict and verdict.get("ts"):
                v["last_scraped"] = max(v.get("last_scraped") or 0, verdict["ts"])
                v["last_outcome"] = "saved"
            if not v.get("exhibitions_url"):
                best, cands = venues.attribute_urls(
                    v, [(0, u, "source") for u in s.get("source_urls") or []])
                if best:
                    v["exhibitions_url"], v["exhibitions_url_source"] = best, "migration"
                venues.merge_patch(v, {"exhibitions_url_candidates": cands})
            backfill[s["slug"]] = v["id"]

    # 2. directory ledger -------------------------------------------------------
    directory: dict[str, dict] = {}
    for e in _jsonl(tools._directory_file(city)):
        directory[tools._norm_venue(e["name"])] = e   # latest wins, as load_directory does
    for e in directory.values():
        v = _get_or_create(reg, e["name"], e.get("website"), collisions)
        venues.merge_patch(v, {
            "neighborhood": e.get("neighborhood"), "kind": e.get("kind"),
            "is_museum": True if e.get("kind") == "museum" else None,
            "address": e.get("address"), "website": e.get("website"), "notes": e.get("note"),
            "sources": {"directory_ts": e.get("ts"), "directory_session": e.get("session")}})

    # 3. crosscheck -----------------------------------------------------------
    if CROSSCHECK.exists():
        cc = json.loads(CROSSCHECK.read_text())
        slug_to_venue = {}
        for v in reg["venues"]:
            for slug in v.get("sources", {}).get("shows", []):
                slug_to_venue[slug] = v
        for key, entry in cc.items():
            c, _, slug = key.partition("/")
            if c != city or slug not in slug_to_venue:
                continue
            g = entry.get("google") or {}
            if not g.get("found"):
                continue
            v = slug_to_venue[slug]
            v["google"] = {"status": g.get("status"), "address": g.get("address"),
                           "hours": g.get("hours"), "phone": g.get("phone"),
                           "website": g.get("website"), "lat": g.get("lat"),
                           "lng": g.get("lng"), "ts": int(CROSSCHECK.stat().st_mtime)}
            v.setdefault("sources", {})["crosscheck_ts"] = v["google"]["ts"]
            if g.get("status") == "CLOSED_PERMANENTLY":
                v["status"] = "closed"

    # 4. skips ----------------------------------------------------------------
    latest_skip: dict[str, dict] = {}
    for e in tools.load_skips(city):
        latest_skip[tools._norm_venue(e["venue"])] = e   # oldest first -> latest wins
    for e in latest_skip.values():
        v = _get_or_create(reg, e["venue"], e.get("url"), collisions)
        if not v.get("neighborhood"):
            v["neighborhood"] = e.get("neighborhood")
        if venues.active_show(v, today):
            continue   # a saved live show outranks an older skip
        skip_day = date.fromtimestamp(e.get("ts") or time.time())
        venues.apply_skip(v, e, skip_day)
        v["last_scraped"] = max(v.get("last_scraped") or 0, e.get("ts") or 0) or None
        if e.get("reason") == "duplicate" and e.get("url"):
            dom = venues.registrable_domain(e["url"])
            for other in reg["venues"]:
                if other is not v and dom and venues.registrable_domain(other.get("website")) == dom:
                    if e["venue"] not in other["aliases"]:
                        other["aliases"].append(e["venue"])

    # finalize ----------------------------------------------------------------
    for v in reg["venues"]:
        if v.get("status") == "unknown" and v.get("last_known_shows"):
            v["status"] = "active"
        if not v.get("next_check"):
            v["next_check"] = venues._iso(venues.compute_next_check(v, today))
    return reg, backfill, collisions


def summarize(city: str, reg: dict) -> str:
    vs = reg["venues"]
    by_status: dict[str, int] = {}
    for v in vs:
        by_status[v.get("status") or "unknown"] = by_status.get(v.get("status") or "unknown", 0) + 1
    with_url = sum(1 for v in vs if v.get("exhibitions_url"))
    with_shows = sum(1 for v in vs if v.get("last_known_shows"))
    due = sum(1 for v in vs if v.get("next_check") and v["next_check"] <= date.today().isoformat()
              and v.get("status") in ("active", "unknown") and not venues.active_show(v))
    lines = [f"{city}: {len(vs)} venues; status {by_status}; exhibitions_url {with_url}/{len(vs)}; "
             f"with saved shows {with_shows}; due now {due}"]
    for v in sorted(vs, key=lambda x: x["id"]):
        show = venues.active_show(v)
        lines.append(f"  {v['id']:<34} {str(v.get('status')):<16} {str(v.get('neighborhood')):<26} "
                     f"next={v.get('next_check')}  url={'Y' if v.get('exhibitions_url') else '-'}  "
                     f"{('show:' + show['slug']) if show else (('skip:' + v['last_skip']['reason']) if v.get('last_skip') else '')}")
    return "\n".join(lines)


def backfill_venue_ids(city: str, mapping: dict[str, str]) -> int:
    """Add top-level venue_id to existing show records (both pools)."""
    n = 0
    lock_path = tools.CONTENT_DIR / f".{city}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in (tools._city_file(city), tools._pending_file(city)):
            if not path.exists():
                continue
            data = tools._load_shows_file(path)
            changed = False
            for s in data["shows"]:
                vid = mapping.get(s["slug"])
                if vid and s.get("venue_id") != vid:
                    s["venue_id"] = vid
                    changed = True
                    n += 1
            if changed:
                tools._write_shows_file(path, data)
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", choices=sorted(CITIES))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true", help="write registries (default: dry run)")
    ap.add_argument("--no-backfill", action="store_true",
                    help="do not add venue_id to existing show records")
    ap.add_argument("--force", action="store_true", help="proceed despite id collisions")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    if not args.city and not args.all:
        ap.error("--city or --all")
    cities = [args.city] if args.city else [
        c for c in CITIES if tools._city_file(c).exists() or tools._pending_file(c).exists()
        or tools._directory_file(c).exists()]
    today = date.today()
    for city in cities:
        reg, backfill, collisions = seed_city(city, today)
        if not args.quiet:
            print(summarize(city, reg))
        if collisions:
            print(f"  !! id collisions in {city} (different domains, same id):")
            for c in collisions:
                print("     " + c)
            if not args.force:
                print("  refusing to write; resolve with aliases or pass --force")
                continue
        if args.apply:
            venues.save_registry(city, reg)
            n = 0 if args.no_backfill else backfill_venue_ids(city, backfill)
            print(f"  wrote {venues._registry_file(city)} ({len(reg['venues'])} venues); "
                  f"venue_id backfilled on {n} show record(s)")
        else:
            print(f"  dry run — pass --apply to write {venues._registry_file(city)}")


if __name__ == "__main__":
    main()
