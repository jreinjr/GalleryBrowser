"""Deterministic venue cross-check against Google Places (New) and OSM/Nominatim.

Writes content/spend/crosscheck.json keyed "city/slug"; the verify agent
receives this data in its inventory and reconciles it with the venue's site.

Usage:
    python crosscheck.py --all            # every city with content
    python crosscheck.py --city tokyo
Requires GOOGLE_MAPS_API_KEY in .env (or env) for the Google half; the OSM
half always runs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
from cities import CITIES  # noqa: E402

CROSSCHECK_PATH = tools.CONTENT_DIR / "spend" / "crosscheck.json"
UA = "GalleryBrowser-crosscheck/1.0 (personal art-guide project)"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELDS = ",".join(f"places.{f}" for f in (
    "displayName", "businessStatus", "formattedAddress", "location",
    "regularOpeningHours.weekdayDescriptions", "internationalPhoneNumber", "websiteUri"))


def haversine_m(lat1, lng1, lat2, lng2) -> float:
    r = math.radians
    a = (math.sin(r(lat2 - lat1) / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(r(lng2 - lng1) / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


_V1_DISABLED = False


def _google_lookup_legacy(key: str, query: str) -> dict:
    """Fallback for keys with only the legacy Places API enabled (2 calls/venue)."""
    ts = requests.get("https://maps.googleapis.com/maps/api/place/textsearch/json",
                      params={"query": query, "key": key}, timeout=20).json()
    if ts.get("status") != "OK" or not ts.get("results"):
        return {"found": False} if ts.get("status") == "ZERO_RESULTS" else \
            {"found": False, "error": ts.get("status")}
    det = requests.get(
        "https://maps.googleapis.com/maps/api/place/details/json",
        params={"place_id": ts["results"][0]["place_id"], "key": key,
                "fields": "name,business_status,formatted_address,geometry,"
                          "opening_hours,formatted_phone_number,website"},
        timeout=20).json()
    r = det.get("result", {})
    loc = r.get("geometry", {}).get("location", {})
    return {
        "found": True,
        "name": r.get("name"),
        "status": r.get("business_status"),
        "address": r.get("formatted_address"),
        "hours": r.get("opening_hours", {}).get("weekday_text"),
        "phone": r.get("formatted_phone_number"),
        "website": r.get("website"),
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
    }


def google_lookup(key: str, name: str, address: str, city_name: str) -> dict:
    global _V1_DISABLED
    if _V1_DISABLED:
        try:
            return _google_lookup_legacy(key, f"{name}, {address}, {city_name}")
        except Exception as exc:
            return {"error": str(exc)[:200]}
    try:
        resp = requests.post(
            PLACES_URL, timeout=20,
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELDS,
                     "Content-Type": "application/json"},
            json={"textQuery": f"{name}, {address}, {city_name}"},
        )
        resp.raise_for_status()
        places = resp.json().get("places", [])
        if not places:
            return {"found": False}
        p = places[0]
        return {
            "found": True,
            "name": p.get("displayName", {}).get("text"),
            "status": p.get("businessStatus"),
            "address": p.get("formattedAddress"),
            "hours": p.get("regularOpeningHours", {}).get("weekdayDescriptions"),
            "phone": p.get("internationalPhoneNumber"),
            "website": p.get("websiteUri"),
            "lat": p.get("location", {}).get("latitude"),
            "lng": p.get("location", {}).get("longitude"),
        }
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 403:
            _V1_DISABLED = True  # Places API (New) not enabled — use legacy from now on
            return google_lookup(key, name, address, city_name)
        return {"error": str(exc)[:200]}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def nominatim_geocode(address: str, city_name: str) -> dict:
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search", timeout=20,
            headers={"User-Agent": UA},
            params={"q": f"{address}, {city_name}", "format": "json", "limit": 1},
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return {"found": False}
        return {"found": True, "lat": float(rows[0]["lat"]), "lng": float(rows[0]["lon"])}
    except Exception as exc:
        return {"error": str(exc)[:200]}
    finally:
        time.sleep(1.1)  # Nominatim rate limit


def run(city_keys: list[str]) -> dict:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        print("  note: GOOGLE_MAPS_API_KEY not set — Google Places half skipped")
    out: dict[str, dict] = {}
    flags_count: dict[str, int] = {}
    for city in city_keys:
        path = tools.CONTENT_DIR / f"{city}.json"
        if not path.exists():
            continue
        city_name = CITIES[city]["display_name"]
        for s in json.loads(path.read_text())["shows"]:
            v = s["venue"]
            entry: dict = {"flags": []}
            if key:
                g = google_lookup(key, v["name"], v["address"], city_name)
                if g.get("found") and g.get("lat") is not None:
                    g["distance_m"] = round(haversine_m(v["latitude"], v["longitude"],
                                                        g["lat"], g["lng"]))
                entry["google"] = g
                status = g.get("status")
                if status and status != "OPERATIONAL":
                    entry["flags"].append(f"GOOGLE_{status}")
                if g.get("found") is False:
                    entry["flags"].append("GOOGLE_NOT_FOUND")
                if g.get("distance_m", 0) > 250:
                    entry["flags"].append(f"COORDS_OFF_GOOGLE_{g['distance_m']}m")
            o = nominatim_geocode(v["address"], city_name)
            if o.get("found"):
                o["distance_m"] = round(haversine_m(v["latitude"], v["longitude"],
                                                    o["lat"], o["lng"]))
                if o["distance_m"] > 500:
                    entry["flags"].append(f"COORDS_OFF_OSM_{o['distance_m']}m")
            entry["osm"] = o
            out[f"{city}/{s['slug']}"] = entry
            for f in entry["flags"]:
                flags_count[f.split("_m")[0] if "COORDS" in f else f] = \
                    flags_count.get(f.split("_m")[0] if "COORDS" in f else f, 0) + 1
            print(f"  {city}/{s['slug']}: {', '.join(entry['flags']) or 'ok'}", flush=True)
    CROSSCHECK_PATH.parent.mkdir(parents=True, exist_ok=True)
    CROSSCHECK_PATH.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"\nwrote {CROSSCHECK_PATH} ({len(out)} venues); flags: {flags_count or 'none'}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", choices=sorted(CITIES))
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    from run_scrape import load_env
    load_env()
    cities = [args.city] if args.city else \
        [c for c in CITIES if (tools.CONTENT_DIR / f"{c}.json").exists()]
    run(cities)


if __name__ == "__main__":
    main()
