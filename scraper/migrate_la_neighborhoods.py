"""One-shot migration: re-label the venue.neighborhood of every saved LA show
from the old 4-hood taxonomy to the 12-zone taxonomy in cities.py.

The mapping is a literal, venue-by-venue table (addresses verified against the
stored records) — it also FIXES the labels that were wrong under the old
taxonomy (shards used to force their own label regardless of address).

Hard-fails if any saved venue is unmapped or maps to an unknown zone, and
writes nothing in that case. Run once, then update the LA neighborhood list in
GalleryBrowser/Models.swift to match cities.py.

Usage:
    python migrate_la_neighborhoods.py           # apply
    python migrate_la_neighborhoods.py --dry-run # print the diff only
"""

from __future__ import annotations

import argparse
import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402

CITY = "los-angeles"

# venue name -> new zone (12-zone taxonomy)
MAPPING = {
    "Hammer Museum": "Westside/Brentwood",
    "Karma": "West Hollywood/Fairfax",
    "François Ghebaly": "Downtown/Arts District",
    "Craft Contemporary": "Mid-Wilshire/Koreatown",
    "Morán Morán": "Hollywood",
    "MOCA Grand Avenue": "Downtown/Arts District",
    "The Broad": "Downtown/Arts District",
    "Vielmetter Los Angeles": "Downtown/Arts District",
    "Peter Fetterman Gallery": "Santa Monica/Venice",
    "Marc Selwyn Fine Art": "Beverly Hills",
    "Academy Museum of Motion Pictures": "Mid-Wilshire/Koreatown",
    "LACMA": "Mid-Wilshire/Koreatown",
    "Los Angeles County Museum of Art (LACMA)": "Mid-Wilshire/Koreatown",
    "Louis Stern Fine Arts": "West Hollywood/Fairfax",
    "Michael Kohn Gallery": "Hollywood",
    "M+B": "West Hollywood/Fairfax",
    "Getty Center": "Westside/Brentwood",
    "California African American Museum": "South LA/Inglewood",
    "Autry Museum of the American West": "Los Feliz/NELA",
    "Skirball Cultural Center": "Westside/Brentwood",
    "Corey Helford Gallery": "Chinatown/East LA",
    "Thinkspace Projects": "Culver City/West Adams",
    "Jeffrey Deitch": "Hollywood",
    "Fahey/Klein Gallery": "Hollywood",
    "Track 16": "Downtown/Arts District",
    "induction gallery": "Mid-Wilshire/Koreatown",
    "Musichead Gallery": "Hollywood",
    "NOON Projects": "Chinatown/East LA",
    "LA Artcore Union Center for the Arts": "Downtown/Arts District",
    "Diane Rosenstein Gallery": "Hollywood",
    "Timothy Hawkinson Gallery": "West Hollywood/Fairfax",
    "Reisig and Taylor Contemporary": "Hollywood",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    zones = set(CITIES[CITY]["neighborhoods"])
    bad_targets = {v for v in MAPPING.values() if v not in zones}
    if bad_targets:
        sys.exit(f"MAPPING targets not in cities.py zones: {sorted(bad_targets)}")

    norm_map = {tools._norm_venue(k): v for k, v in MAPPING.items()}

    lock_path = tools.CONTENT_DIR / f".{CITY}.json.lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        pools = {
            "published": (tools._city_file(CITY), tools._load_shows_file(tools._city_file(CITY))),
            "pending": (tools._pending_file(CITY), tools._load_shows_file(tools._pending_file(CITY))),
        }
        unmapped, changes = [], []
        for pool_name, (_path, data) in pools.items():
            for s in data["shows"]:
                name = s["venue"]["name"]
                new = norm_map.get(tools._norm_venue(name))
                if new is None:
                    unmapped.append(f"{pool_name}: {name}")
                    continue
                old = s["venue"]["neighborhood"]
                if old != new:
                    changes.append(f"  {name}: {old} -> {new}")
                s["venue"]["neighborhood"] = new
        if unmapped:
            sys.exit("UNMAPPED venues (add them to MAPPING, nothing written):\n  "
                     + "\n  ".join(unmapped))
        print(f"{len(changes)} label changes:")
        print("\n".join(changes) or "  (none)")
        if args.dry_run:
            print("dry run — nothing written")
            return
        for _pool_name, (path, data) in pools.items():
            if data["shows"] or path.exists():
                tools._write_shows_file(path, data)
        print("written. Remember: GalleryBrowser/Models.swift still lists the old "
              "4 LA neighborhoods — update it to match cities.py.")


if __name__ == "__main__":
    main()
