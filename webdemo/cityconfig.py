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

DEFAULT_CITY = "seattle"

# Placeholder cities the demo shows but the scraper doesn't know about: same
# shape as a scraper CITIES entry, listed after the scraped cities.
DEMO_ONLY_CITIES = {
    "tucson": {
        "display_name": "Tucson",
        "center": {"latitude": 32.235, "longitude": -110.955},
        "span": {"latitudeDelta": 0.25, "longitudeDelta": 0.25},
        "neighborhoods": ["Downtown/Congress Street", "Warehouse Arts District", "4th Avenue",
                          "University/Main Gate", "Barrio Viejo/Armory Park", "Catalina Foothills"],
    },
}
ALL_CITIES = {**CITIES, **DEMO_ONLY_CITIES}

# IANA zone per city: the Discover function computes "today", "this weekend"
# and opening hours in the city's own time, not the visitor's device time.
CITY_TZ = {
    "seattle": "America/Los_Angeles", "new-york": "America/New_York",
    "los-angeles": "America/Los_Angeles", "tokyo": "Asia/Tokyo",
    "berlin": "Europe/Berlin", "london": "Europe/London", "paris": "Europe/Paris",
    "venice": "Europe/Rome",
    # quick-city expansion (scraper/quick_city.py)
    "hong-kong": "Asia/Hong_Kong", "seoul": "Asia/Seoul", "mexico-city": "America/Mexico_City",
    "shanghai": "Asia/Shanghai", "brussels": "Europe/Brussels", "milan": "Europe/Rome",
    "chicago": "America/Chicago", "san-francisco": "America/Los_Angeles", "miami": "America/New_York",
    # demo-only placeholder cities
    "tucson": "America/Phoenix",
}


def discover() -> list[dict]:
    """Cities with content, in CITIES declaration order (demo-only cities last);
    warn on orphans."""
    out = []
    for key, cfg in ALL_CITIES.items():
        if not (CONTENT_DIR / f"{key}.json").exists():
            print(f"  note: no content for configured city '{key}' — skipped")
            continue
        out.append({
            "key": key,
            "displayName": cfg["display_name"],
            "neighborhoods": list(cfg["neighborhoods"]),
            "center": {"lat": cfg["center"]["latitude"], "lng": cfg["center"]["longitude"]},
            "span": max(cfg["span"]["latitudeDelta"], cfg["span"]["longitudeDelta"]),
        })
    for f in sorted(CONTENT_DIR.glob("*.json")):
        if f.stem not in ALL_CITIES:
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


def published_photos(v: dict) -> list[dict]:
    """Gallery photos (scraper/gallery_photos.py) in hero order, as
    [{path, provider, attribution}]; [] when the venue has none. build.py decides
    which providers ship (--venue-photos)."""
    p = v.get("photos") or {}
    if p.get("status") != "done":
        return []
    return [{"path": f["path"], "provider": f.get("provider") or "site",
             "attribution": (f.get("attribution") or {}).get("name")}
            for f in p.get("files") or [] if f.get("path")]


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
            "verification": (v.get("verification") or {}).get("status"),
            "status": v.get("status"),
            "website": v.get("website"), "phone": v.get("phone"), "hours": v.get("hours") or [],
            "address": v.get("address"), "addressDetail": v.get("address_detail"),
            "neighborhood": v.get("neighborhood"),
            "lat": v.get("latitude"), "lng": v.get("longitude"),
            "photos": published_photos(v),
        }
    return out


def load_seesaw(city_key: str) -> dict[str, str]:
    """venue_id -> "pick" (on See Saw's latest Editor's Picks) or "listed" (on its
    latest All / Featured capture), from content/curation/<city>/seesaw/; {} when
    the city has no snapshots. Matched on the registry name and aliases with the
    pipeline's venue-name key (tools._norm_venue)."""
    d = CONTENT_DIR / "curation" / city_key / "seesaw"
    if not d.is_dir():
        return {}
    from tools import _norm_venue  # scraper/, on sys.path above
    latest: dict[str, dict] = {}   # tab -> newest snapshot (file names sort by date)
    for f in sorted(d.glob(f"{city_key}-*.json")):
        try:
            snap = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        latest[snap.get("tab") or "featured"] = snap
    norms = lambda tabs: {_norm_venue(e.get("venue") or "") for t in tabs
                          for e in latest.get(t, {}).get("entries", [])} - {""}
    picks, listed = norms(["editors_picks"]), norms(["all", "featured", "editors_picks"])
    if not listed:
        return {}
    reg = json.loads((CONTENT_DIR / "venues" / f"{city_key}.json").read_text()) \
        if (CONTENT_DIR / "venues" / f"{city_key}.json").exists() else {}
    city_tail = " " + city_key.replace("-", " ")
    out: dict[str, str] = {}
    for v in reg.get("venues", []):
        if not v.get("id") or v.get("status") in HIDDEN_STATUSES:
            continue
        keys = {_norm_venue(n) for n in [v.get("name") or "", *(v.get("aliases") or [])]} - {""}
        # "Vielmetter Los Angeles" is "Vielmetter" on See Saw
        keys |= {k[: -len(city_tail)] for k in keys if k.endswith(city_tail) and len(k) > len(city_tail) + 2}
        if keys & picks:
            out[v["id"]] = "pick"
        elif keys & listed:
            out[v["id"]] = "listed"
    return out


# Registry statuses that mean "not a venue to show anyone" (rank_venues.py gates
# on the same set).
HIDDEN_STATUSES = {"closed", "duplicate", "out_of_scope"}


def mappable(v: dict) -> bool:
    """A registry venue the app may show on its own, with or without a show.
    Pinned, not closed / a duplicate / out of scope, not flagged closed by
    validate_venues.py, and vouched for by at least one pipeline: verified
    (quick cities), status active (the older LA/Tokyo pipeline, which never
    wrote verification for most of its records), or ranked into a tier (the
    market order / hand list). Venues with a published show are always emitted."""
    if v.get("lat") is None or v.get("lng") is None:
        return False
    if v.get("status") in HIDDEN_STATUSES or v.get("verification") == "flagged":
        return False
    return bool(v.get("verified")) or v.get("status") == "active" or v.get("tier") is not None


def load_venue_kinds(city_key: str) -> dict[str, str]:
    """venue_id -> kind (gallery / museum / nonprofit / project_space / ...) from the
    venue registry; {} when the city has no registry."""
    return {vid: v["kind"] for vid, v in load_venues(city_key).items() if v.get("kind")}


def load_lists(city_key: str, published_slugs: set[str]) -> list[dict]:
    """Curated lists authored in content/lists/<city>.json, as the app's Lists
    tab shows them: {id, name, desc, kind: 'list'|'route', entries: [{slug, note}]}.
    Entries whose show is not published are dropped; a list left empty is
    dropped too (the app hides lists whose shows have all closed at runtime)."""
    f = CONTENT_DIR / "lists" / f"{city_key}.json"
    if not f.exists():
        return []
    data = json.loads(f.read_text())
    out = []
    for l in data.get("lists", []):
        entries = [{"slug": e["slug"], "note": e.get("note")}
                   for e in l.get("entries", []) if e.get("slug") in published_slugs]
        if not entries or not l.get("id") or not l.get("name"):
            continue
        out.append({"id": l["id"], "name": l["name"], "desc": l.get("desc"),
                    "kind": "route" if l.get("kind") == "route" else "list", "entries": entries})
    return out
