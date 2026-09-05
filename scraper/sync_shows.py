"""Copy venue-level data from the registry onto the venue object embedded in
every show record, so consumers that read content/<city>.json directly (the
iOS bundle) see it without a join.

    python sync_shows.py --city los-angeles [--apply]

Today that is `about` (the published blurb: research_venue.py's about.text
when about.source_kind is official/secondary — see docs/GALLERIES.md) and
`photos` (site-sourced gallery photo paths, hero first, from
gallery_photos.py's photos block). Idempotent; --apply writes, otherwise
counts only.
Locks the city JSON the way tools.confirm_show does.
"""

from __future__ import annotations

import argparse
import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

PUBLISHABLE_ABOUT = ("official", "secondary")


def published_about(v: dict) -> str | None:
    about = v.get("about") or {}
    text = about.get("text")
    if text and about.get("source_kind") in PUBLISHABLE_ABOUT:
        return text.strip() or None
    return None


def published_photos(v: dict) -> list[str] | None:
    """Site-sourced gallery photo paths in hero order; Google-sourced photos stay
    out of the app bundle (Google's 30-day cache + attribution terms)."""
    p = v.get("photos") or {}
    if p.get("status") != "done":
        return None
    paths = [f["path"] for f in p.get("files") or []
             if f.get("provider") == "site" and f.get("path")]
    return paths or None


def sync_city(city: str, apply: bool = False) -> dict:
    reg = venues.index_by_id(venues.load_registry(city))
    stats = {"shows": 0, "changed": 0, "with_about": 0, "with_photos": 0, "no_venue_id": 0}
    lock_path = tools.CONTENT_DIR / f".{city}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for path in (tools._city_file(city), tools._pending_file(city)):
            if not path.exists():
                continue
            data = tools._load_shows_file(path)
            dirty = False
            for show in data.get("shows", []):
                stats["shows"] += 1
                vid = show.get("venue_id")
                if not vid:
                    stats["no_venue_id"] += 1
                    continue
                about = published_about(reg.get(vid) or {})
                if about:
                    stats["with_about"] += 1
                cur = show["venue"].get("about")
                if cur != about:
                    stats["changed"] += 1
                    dirty = True
                    if about is None:
                        show["venue"].pop("about", None)
                    else:
                        show["venue"]["about"] = about
                photos = published_photos(reg.get(vid) or {})
                if photos:
                    stats["with_photos"] += 1
                if show["venue"].get("photos") != photos:
                    stats["changed"] += 1
                    dirty = True
                    if photos is None:
                        show["venue"].pop("photos", None)
                    else:
                        show["venue"]["photos"] = photos
            if dirty and apply:
                tools._write_shows_file(path, data)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--apply", action="store_true", help="write the city + pending JSON")
    args = ap.parse_args()
    st = sync_city(args.city, apply=args.apply)
    print(f"{args.city}: {st['shows']} shows, {st['with_about']} with a published blurb, "
          f"{st['with_photos']} with gallery photos, "
          f"{st['changed']} venue objects {'updated' if args.apply else 'would change'}, "
          f"{st['no_venue_id']} without venue_id")
    if not args.apply:
        print("dry run — pass --apply to write")


if __name__ == "__main__":
    main()
