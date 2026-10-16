#!/usr/bin/env python3
"""Add a gallery to a city from its Google Maps listing.

Used by the local preview server (webdemo/devserver.mjs, /api/local-venue)
when Carlton taps Add gallery on the Gallery tab; never by the deployed site.

    add_venue.py --city seattle --name "Koplin Del Rio" --lookup   # what Google has, writes nothing
    add_venue.py --city seattle --name "Koplin Del Rio"            # add it

The Places text search (crosscheck.google_lookup, biased to the city centre)
gives the name, address, map location, hours, phone and website. Adding writes
a registry record (content/venues/<city>.json) with status active, so the build
pins it and lists it, its neighborhood from the nearest labelled galleries, and
the last rank: for a city with a hand-set order (move_venue_rank.PARAMS) it
joins the end of venue_order.json and the ranks are re-applied; elsewhere it
gets the rank after the city's last one.

Prints one JSON line: {"ok", "found", "place", "existing"?, "id"?, "rank"?}.
GOOGLE_MAPS_API_KEY comes from the environment or the repo's .env.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import cities  # noqa: E402
import crosscheck  # noqa: E402
import seed_venues  # noqa: E402
import venues  # noqa: E402
from hours import group_hours  # noqa: E402
sys.path.insert(0, str(ROOT / "webdemo"))
import cityconfig  # noqa: E402
from move_venue_rank import PARAMS as HAND_ORDER_CITIES  # noqa: E402


def load_key() -> str | None:
    if os.environ.get("GOOGLE_MAPS_API_KEY"):
        return os.environ["GOOGLE_MAPS_API_KEY"]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.strip().partition("=")
            if k == "GOOGLE_MAPS_API_KEY" and v:
                return v.strip().strip("'\"")
    return None


def out(**kw) -> int:
    print(json.dumps(kw, ensure_ascii=False))
    return 0 if kw.get("ok") else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--lookup", action="store_true", help="only report what Google has")
    a = ap.parse_args()
    cfg = cities.CITIES.get(a.city)
    if not cfg:
        return out(ok=False, error=f"unknown city {a.city}")
    name = a.name.strip()[:120]
    if not name:
        return out(ok=False, error="type a gallery name")
    key = load_key()
    if not key:
        return out(ok=False, error="no GOOGLE_MAPS_API_KEY in .env")

    c = cfg["center"]
    g = crosscheck.google_lookup(key, name, "", cfg["display_name"],
                                 bias={"lat": c["latitude"], "lng": c["longitude"], "radius_m": 40000})
    if g.get("error"):
        return out(ok=False, error="Google lookup failed: " + g["error"])
    if not g.get("found") or g.get("lat") is None:
        return out(ok=True, found=False)
    far = crosscheck.haversine_m(c["latitude"], c["longitude"], g["lat"], g["lng"])
    place = {k: g.get(k) for k in ("name", "address", "website", "phone", "hours", "lat", "lng", "status")}
    place["hours"] = group_hours(place.get("hours") or [])
    place["km_from_center"] = round(far / 1000, 1)

    reg = venues.load_registry(a.city)
    existing = seed_venues.match_registry(reg, g["name"] or name, g.get("website"), g["lat"], g["lng"], city=a.city)[0]
    # A registry record the app already shows is reported, never rewritten; one it
    # hides (unvouched, no pin) is filled in from Google and made active below.
    shown = bool(existing) and cityconfig.mappable(cityconfig.load_venues(a.city).get(existing["id"]) or {})
    ex = {"id": existing["id"], "name": existing["name"], "rank": existing.get("rank"),
          "shown": shown} if existing else None
    if a.lookup or shown:
        return out(ok=True, found=True, place=place, existing=ex)
    if existing and (existing.get("verification") or {}).get("status") == "flagged":
        return out(ok=False, error=f"{existing['name']} was flagged as closed by the gallery checks", place=place)
    if existing and existing.get("status") in cityconfig.HIDDEN_STATUSES:
        return out(ok=False, error=f"{existing['name']} is marked {existing['status'].replace('_', ' ')} in the gallery file", place=place)
    if far > 60000:
        return out(ok=False, error=f"Google's match is {place['km_from_center']} km from {cfg['display_name']}", place=place)
    if g.get("status") and g["status"] != "OPERATIONAL":
        return out(ok=False, error=f"Google lists it as {g['status'].lower().replace('_', ' ')}", place=place)

    now = int(time.time())
    with venues.locked_registry(a.city) as reg:
        zone, _, _, _ = seed_venues.assign_zone(g["lat"], g["lng"], seed_venues.labeled_points(reg))
        last = max((v.get("rank") or 0) for v in reg["venues"]) if reg["venues"] else 0
        v, created = venues._upsert_in(reg, g["name"] or name, {
            "address": g.get("address"), "latitude": g["lat"], "longitude": g["lng"],
            "coords_source": "places:manual", "website": g.get("website"), "phone": g.get("phone"),
            "hours": g.get("hours") or None,
            "google": {"status": g.get("status"), "address": g.get("address"), "hours": g.get("hours"),
                       "phone": g.get("phone"), "website": g.get("website"),
                       "lat": g["lat"], "lng": g["lng"], "ts": now},
        }, "manual-add", g.get("website"),
            protect_existing=("address", "latitude", "longitude", "coords_source", "website", "phone", "hours"))
        if v.get("status") in (None, "unknown") or created:
            v["status"] = "active"
        if not v.get("neighborhood") and zone:
            v["neighborhood"] = zone
        if v.get("rank") is None and a.city not in HAND_ORDER_CITIES:
            v["rank"] = last + 1
        v["notes"] = v.get("notes") or f"added from Google Maps on the Gallery tab {time.strftime('%Y-%m-%d')}"
        vid = v["id"]

    rank = None
    if a.city in HAND_ORDER_CITIES:
        mv = subprocess.run([sys.executable, str(HERE / "move_venue_rank.py"), "--city", a.city,
                             "--id", vid, "--to", "100000"], capture_output=True, text=True)
        try:
            rank = json.loads(mv.stdout.strip().splitlines()[-1]).get("rank")
        except (ValueError, IndexError):
            return out(ok=False, error="added, but re-ranking failed", id=vid, log=mv.stderr[-1000:])
    else:
        rank = {x["id"]: x.get("rank") for x in venues.load_registry(a.city)["venues"]}.get(vid)
    return out(ok=True, found=True, created=created, id=vid, rank=rank, place=place)


if __name__ == "__main__":
    sys.exit(main())
