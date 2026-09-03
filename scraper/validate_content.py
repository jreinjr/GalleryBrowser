"""Validate scraped content: every city JSON decodes, matches the schema the
app expects, and every referenced image exists and is reasonably sized."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

import seed_venues
import tools
import venues
from cities import CITIES

REQUIRED = set(tools.SAVE_SHOW_SCHEMA["required"])
VENUE_REQUIRED = set(tools.SAVE_SHOW_SCHEMA["properties"]["venue"]["required"])


def check_registry(city: str, problems: list[str], review: list[str]) -> str:
    """Registry invariants. `kind` is the source of truth for museum-ness and
    `is_museum` is a derived mirror (venues.sync_museum_flag), so the two must
    agree — they silently drifted for four LA venues when a Google Places sweep
    ORed the boolean to true without touching `kind`. The name check is the
    second half of that guard: whatever Google says, a venue calling itself a
    gallery is not a museum."""
    path = venues._registry_file(city)
    if not path.exists():
        return f"{city}: no registry"
    reg = json.loads(path.read_text())["venues"]
    for v in reg:
        vid = f"{city}/{v.get('id', '?')}"
        kind = v.get("kind")
        if kind is not None and kind not in venues.KINDS:
            problems.append(f"{vid}: kind {kind!r} not one of {venues.KINDS}")
        if bool(v.get("is_museum")) != venues.is_museum(v):
            problems.append(f"{vid}: is_museum {v.get('is_museum')!r} disagrees with "
                            f"kind {kind!r} ({v.get('name')})")
        # Not an error: Whitechapel, Serpentine and Henry Art Gallery are real
        # museums named Gallery. It is a review queue — the same shape caught
        # Leica Gallery and Musichead Gallery, which were not.
        if venues.is_museum(v) and seed_venues.GALLERY_NAME_RE.search(v.get("name") or ""):
            review.append(f"{vid}: typed museum but named a gallery ({v.get('name')})")
    museums = sum(1 for v in reg if venues.is_museum(v))
    return f"{city}: {len(reg)} venues, {museums} museum"


def main() -> int:
    problems: list[str] = []
    review: list[str] = []
    summary: list[str] = []
    for city in CITIES:
        summary.append(check_registry(city, problems, review))
    for city in CITIES:
        path = tools.CONTENT_DIR / f"{city}.json"
        if not path.exists():
            summary.append(f"{city}: NO CONTENT FILE")
            continue
        data = json.loads(path.read_text())
        shows = data.get("shows", [])
        n_images = 0
        for show in shows:
            sid = f"{city}/{show.get('slug', '?')}"
            missing = REQUIRED - set(show) - {"dates_note"}   # legacy records: note = null
            if missing:
                problems.append(f"{sid}: missing keys {sorted(missing)}")
                continue
            vmissing = VENUE_REQUIRED - set(show["venue"])
            if vmissing:
                problems.append(f"{sid}: venue missing keys {sorted(vmissing)}")
            for key in ("start_date", "end_date"):
                if show.get(key) is None:
                    problems.append(f"{sid}: null {key} in a PUBLISHED file (must stay pending)")
                    continue
                try:
                    date.fromisoformat(show[key])
                except (ValueError, TypeError):
                    problems.append(f"{sid}: bad {key} {show[key]!r}")
            end = tools._parse_iso(show.get("end_date"))
            if end is not None and end < date.today():
                problems.append(f"{sid}: already closed ({show['end_date']})")
            for img in show["images"]:
                full = tools.CONTENT_DIR / img
                if not full.is_file():
                    problems.append(f"{sid}: image missing {img}")
                    continue
                n_images += 1
                with Image.open(full) as im:
                    if im.size[0] < 500:
                        problems.append(f"{sid}: image {img} only {im.size[0]}px wide")
            words = len(show["description"].split())
            if words < 60:
                problems.append(f"{sid}: description only {words} words")
        museums = sum(1 for s in shows if s["venue"]["is_museum"])
        picks = sum(1 for s in shows if s["editors_pick"])
        summary.append(
            f"{city}: {len(shows)} shows, {n_images} images, {museums} museum, {picks} picks"
        )

    print("\n".join(summary))
    if review:
        print(f"\n{len(review)} to review (not failures):")
        print("\n".join(f"  - {r}" for r in review))
    if problems:
        print(f"\n{len(problems)} PROBLEMS:")
        print("\n".join(f"  - {p}" for p in problems))
        return 1
    print("\nAll content valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
