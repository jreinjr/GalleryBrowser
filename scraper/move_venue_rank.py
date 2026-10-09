#!/usr/bin/env python3
"""Move one gallery to a new place in a city's hand-set order, then re-rank.

Used by the local preview server (webdemo/devserver.mjs, POST /api/local-rank)
when Carlton taps Change rank on a map card; never by the deployed site.

    move_venue_rank.py --city los-angeles --id matthew-marks --to 12

1. content/curation/<city>/venue_order.json: the gallery's entry moves to
   position --to (joining the list if it was not in it); every entry is
   renumbered 1..n. A --to past the end puts it last.
2. rank_venues.py apply with the city's params writes the registry ranks
   (content/venues/<city>.json), which the web build reads.
3. Prints JSON: {"ok": true, "id", "to", "rank": <registry rank>, "ranks": {id: rank}}.

Only cities listed in PARAMS can be moved this way; their params are the ones
the venue_order.json note says to rebuild with.
"""
from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PARAMS = {"los-angeles": ROOT / "content/curation/params/venues-la-ranked200.json"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--to", type=int, required=True)
    a = ap.parse_args()
    if a.city not in PARAMS:
        print(json.dumps({"ok": False, "error": f"rank moves are not set up for {a.city}"}))
        return 2

    reg_path = ROOT / "content/venues" / f"{a.city}.json"
    registry = {v["id"]: v for v in json.loads(reg_path.read_text())["venues"]}
    if a.id not in registry:
        print(json.dumps({"ok": False, "error": f"unknown gallery {a.id}"}))
        return 2

    order_path = ROOT / "content/curation" / a.city / "venue_order.json"
    order = json.loads(order_path.read_text())
    entries = sorted(order["entries"], key=lambda e: int(e.get("rank") or 0))
    old = next((e for e in entries if e.get("id") == a.id), None)
    entries = [e for e in entries if e is not old]
    entry = old or {"rank": None, "name": registry[a.id].get("name"), "neighborhood": None, "note": None, "id": a.id}
    entry["note"] = f"moved on the map {datetime.date.today().isoformat()}"
    pos = max(1, min(len(entries) + 1, a.to))
    entries.insert(pos - 1, entry)
    for i, e in enumerate(entries, 1):
        e["rank"] = i
    order["entries"] = entries
    order_path.write_text(json.dumps(order, indent=1, ensure_ascii=False) + "\n")

    run = subprocess.run([sys.executable, str(HERE / "rank_venues.py"), "apply", "--city", a.city,
                          "--params", str(PARAMS[a.city])], capture_output=True, text=True)
    if run.returncode != 0:
        print(json.dumps({"ok": False, "error": "rank_venues.py apply failed", "log": run.stderr[-2000:]}))
        return 1

    ranks = {v["id"]: v.get("rank") for v in json.loads(reg_path.read_text())["venues"]}
    print(json.dumps({"ok": True, "id": a.id, "to": pos, "rank": ranks.get(a.id), "ranks": ranks}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
