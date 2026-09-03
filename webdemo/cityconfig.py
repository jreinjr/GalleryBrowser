"""City configuration for the web demo build.

Reads the scraper's CITIES dict so newly scraped cities appear in the demo
automatically, and derives the buildable city list from which content JSON
files actually exist.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT_DIR = ROOT / "content"

sys.path.insert(0, str(ROOT / "scraper"))
from cities import CITIES  # noqa: E402

# App-side extras not present in the scraper config (mirrors Models.swift).
AVAILABILITY_NOTES = {"venice": "Available through Sunday, November 22"}

DEFAULT_CITY = "seattle"


def discover() -> list[dict]:
    """Cities with content, in CITIES declaration order; warn on orphans."""
    out = []
    for key, cfg in CITIES.items():
        if not (CONTENT_DIR / f"{key}.json").exists():
            print(f"  note: no content for configured city '{key}' — skipped")
            continue
        out.append({
            "key": key,
            "displayName": cfg["display_name"],
            "neighborhoods": list(cfg["neighborhoods"]),
            "availabilityNote": AVAILABILITY_NOTES.get(key),
            "center": {"lat": cfg["center"]["latitude"], "lng": cfg["center"]["longitude"]},
            "span": max(cfg["span"]["latitudeDelta"], cfg["span"]["longitudeDelta"]),
        })
    for f in sorted(CONTENT_DIR.glob("*.json")):
        if f.stem not in CITIES:
            print(f"  warning: content file {f.name} has no city config — not included")
    return out


def load_shows(city_key: str) -> list[dict]:
    data = json.loads((CONTENT_DIR / f"{city_key}.json").read_text())
    return data.get("shows", [])


PUBLISHABLE_ABOUT = ("official", "secondary")


def published_about(v: dict) -> str | None:
    """The venue blurb the apps may show: `about.text` only when the research
    pass marked its provenance official/secondary (a QA-failed blurb keeps
    source_kind but has text None; an unreviewed one has no source_kind)."""
    about = v.get("about") or {}
    text = about.get("text")
    if text and about.get("source_kind") in PUBLISHABLE_ABOUT:
        return text.strip() or None
    return None


def load_venues(city_key: str) -> dict[str, dict]:
    """venue_id -> public venue view from the registry (content/venues/<city>.json,
    see docs/GALLERIES.md); {} when the city has no registry. This is the only
    place the web build reads registry fields, so new venue-level data (blurb,
    rank, verification) is added here once."""
    f = CONTENT_DIR / "venues" / f"{city_key}.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text())
    out: dict[str, dict] = {}
    for v in data.get("venues", []):
        vid = v.get("id")
        if not vid:
            continue
        kind = v.get("kind") or ("museum" if v.get("is_museum") else "gallery")
        out[vid] = {
            "id": vid, "name": v.get("name"), "kind": kind, "isMuseum": kind == "museum",
            "about": published_about(v),
            "tier": v.get("tier"), "rank": v.get("rank"), "score": v.get("score"),
            "verified": (v.get("verification") or {}).get("status") == "verified",
            "website": v.get("website"), "hours": v.get("hours") or [],
            "address": v.get("address"), "addressDetail": v.get("address_detail"),
            "neighborhood": v.get("neighborhood"),
            "lat": v.get("latitude"), "lng": v.get("longitude"),
        }
    return out


def load_venue_kinds(city_key: str) -> dict[str, str]:
    """venue_id -> kind (gallery / museum / nonprofit / project_space / ...) from the
    venue registry; {} when the city has no registry."""
    return {vid: v["kind"] for vid, v in load_venues(city_key).items() if v.get("kind")}


def load_ranking(city_key: str) -> dict[str, dict]:
    """slug -> {rank, score} for every published show, from the last
    ``curate.py apply`` (content/curation/<city>/curated.json); {} when absent."""
    f = CONTENT_DIR / "curation" / city_key / "curated.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text())
    rows = data.get("ranked") or data.get("featured") or []
    return {r["slug"]: {"rank": r["rank"], "score": r.get("score")} for r in rows if r.get("slug")}
