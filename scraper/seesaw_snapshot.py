"""See Saw benchmark snapshot capture (plan 2.7).

The See Saw app's Featured feed for a city is transcribed (by hand or via a
computer-use session) into a text file, one card per line, then turned into
an immutable dated snapshot::

    python seesaw_snapshot.py --city los-angeles --date 2026-09-01 \
        --from-text feed.txt --notes "captured from iOS app, Featured tab" --match

Accepted line forms (blank lines and ``#`` comments are ignored)::

    Artist — Title @ Venue        (em/en dash or ' - ' between artist and title)
    Title @ Venue                 (no artist; the matcher also tries it as an artist)
    Venue: Artist                 (optionally 'Venue: Artist — Title')
    Venue                         (venue only)

``--from-json file`` accepts ``{"entries": [{venue, artist, title}]}`` or a
bare list of such objects. Output: ``content/curation/<city>/seesaw/<city>-<date>.json``
``{city, captured_at, tab: "featured", capture_method, notes, entries[{position,
venue, artist|null, title|null, raw}]}``. Snapshots are benchmark truth: the
tool refuses to overwrite an existing file unless ``--force``. ``--match``
prints how each entry resolves against the current pool. ``--out`` writes the
file elsewhere (scratch runs) and skips the runs.jsonl row.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_store as store  # noqa: E402
import tools  # noqa: E402

DASH_RE = re.compile(r"\s+(?:—|–|-)\s+")   # ' — ', ' – ', ' - ' (spaced hyphen only)


def parse_line(line: str) -> dict | None:
    """One transcribed card -> ``{venue, artist, title, raw}`` or None."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    body = re.sub(r"^\s*\d+[.)]\s+", "", raw)   # optional leading '1. ' numbering
    venue = artist = title = None
    if "@" in body:
        left, venue = body.rsplit("@", 1)
        left, venue = left.strip(), venue.strip()
        parts = DASH_RE.split(left, maxsplit=1)
        if len(parts) == 2:
            artist, title = parts[0].strip(), parts[1].strip()
        elif ":" in left and not left.endswith(":"):
            artist, title = (p.strip() for p in left.split(":", 1))
        else:
            title = left or None
    elif ":" in body:
        venue, rest = (p.strip() for p in body.split(":", 1))
        parts = DASH_RE.split(rest, maxsplit=1)
        if len(parts) == 2:
            artist, title = parts[0].strip(), parts[1].strip()
        else:
            artist = rest or None
    else:
        venue = body
    return {"venue": venue or None, "artist": artist or None, "title": title or None, "raw": raw}


def parse_text(text: str) -> list[dict]:
    entries = []
    for line in text.splitlines():
        e = parse_line(line)
        if e:
            e["position"] = len(entries) + 1
            entries.append(e)
    return entries


def parse_json(text: str) -> list[dict]:
    data = json.loads(text)
    items = data.get("entries", []) if isinstance(data, dict) else data
    entries = []
    for i, it in enumerate(items, 1):
        entries.append({"position": it.get("position", i), "venue": it.get("venue"),
                        "artist": it.get("artist"), "title": it.get("title"),
                        "raw": it.get("raw") or " / ".join(x for x in (it.get("artist"), it.get("title"), it.get("venue")) if x)})
    return entries


def build_snapshot(city: str, day: str, entries: list[dict], notes: str | None,
                   capture_method: str, tab: str = "featured") -> dict:
    return {"city": city, "captured_at": day, "tab": tab,
            "capture_method": capture_method, "notes": notes,
            "entries": [{"position": e["position"], "venue": e.get("venue"),
                         "artist": e.get("artist"), "title": e.get("title"), "raw": e.get("raw")}
                        for e in entries]}


def print_match_table(city: str, entries: list[dict]) -> dict:
    pool = tools.all_city_shows(city)
    reg = store.load_registry_safe(city)
    index = store.PoolIndex(pool)
    counts = {"matched": 0, "venue_in_pool": 0, "not_in_pool": 0}
    print(f"{'#':>3} {'venue':<32} {'artist / title':<36} {'-> slug':<40} {'method':<13} conf")
    for e in entries:
        m = store.match_show_ref({"venue": e.get("venue"), "artist": e.get("artist"), "title": e.get("title")}, index, reg)
        who = e.get("artist") or e.get("title") or ""
        if m.is_match:
            counts["matched"] += 1
        elif m.venue_in_pool:
            counts["venue_in_pool"] += 1
        else:
            counts["not_in_pool"] += 1
        print(f"{e['position']:>3} {(e.get('venue') or '')[:32]:<32} {who[:36]:<36} "
              f"{(m.slug or ('(venue in pool)' if m.venue_in_pool else '(not in pool)'))[:40]:<40} "
              f"{m.method:<13} {m.confidence:.2f}")
    n = len(entries) or 1
    print(f"matched {counts['matched']}/{len(entries)} ({counts['matched']/n:.0%}); "
          f"venue in pool but different show {counts['venue_in_pool']}; not in pool {counts['not_in_pool']}")
    return counts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--city", required=True)
    ap.add_argument("--date", default=date.today().isoformat(), help="capture date YYYY-MM-DD")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-text", help="transcribed feed, one card per line")
    src.add_argument("--from-json", help="JSON with entries [{venue, artist, title}]")
    ap.add_argument("--tab", default="featured", choices=["featured", "all", "editors_picks"],
                    help="which See Saw list this is; non-featured tabs are stored as "
                         "<city>-<date>-<tab>.json (the Featured feed is the benchmark)")
    ap.add_argument("--notes", default=None)
    ap.add_argument("--capture-method", default="computer_use_transcription")
    ap.add_argument("--match", action="store_true", help="print how entries resolve against the pool")
    ap.add_argument("--out", help="write the snapshot here instead of the curation store")
    ap.add_argument("--force", action="store_true", help="overwrite an existing snapshot file")
    ap.add_argument("--no-write", action="store_true", help="parse (+match) only")
    args = ap.parse_args(argv)

    date.fromisoformat(args.date)
    if args.from_text:
        entries = parse_text(Path(args.from_text).read_text())
    else:
        entries = parse_json(Path(args.from_json).read_text())
    if not entries:
        print("no entries parsed", file=sys.stderr)
        return 1
    snap = build_snapshot(args.city, args.date, entries, args.notes, args.capture_method,
                          tab=args.tab)
    print(f"{len(entries)} entries parsed for {args.city} {args.date}")
    for e in entries:
        print(f"  {e['position']:>2}. venue={e.get('venue')!r} artist={e.get('artist')!r} title={e.get('title')!r}")

    if args.match:
        print()
        print_match_table(args.city, entries)

    if args.no_write:
        return 0
    out = Path(args.out) if args.out else store.snapshot_file(args.city, args.date)
    if not args.out and args.tab != "featured":
        out = out.with_name(f"{args.city}-{args.date}-{args.tab}.json")
    if out.exists() and not args.force:
        print(f"refusing to overwrite {out} (use --force)", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=1, ensure_ascii=False))
    print(f"\nwrote {out}")
    if not args.out:
        store.append_run({
            "run_id": f"snapshot-{args.city}-{int(time.time())}", "city": args.city,
            "stage": "snapshot", "prompt_variant": None, "prompt_hash": None, "model": None,
            "effort": None, "sources_planned": [], "sources_seen": [], "signals_recorded": 0,
            "candidates_new": 0, "duplicates_rejected": 0, "requests": 0, "web_searches": 0,
            "cost_usd": 0.0, "stop_reason": None, "duration_s": 0,
            "notes": f"snapshot {out.stem}: {len(entries)} entries via {args.capture_method}",
        })
    return 0


if __name__ == "__main__":
    sys.exit(main())
