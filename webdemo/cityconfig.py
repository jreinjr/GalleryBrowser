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
