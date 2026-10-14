"""Catch shows saved to a city that actually hang at the gallery's space in
another city.

    scraper/.venv/bin/python scraper/check_show_city.py --city los-angeles [--slug SLUG ...]

A show record carries the gallery's local address whichever of its spaces the
show is in, so nothing downstream notices when a scrape picks up, say, the New
York show from a gallery's shared exhibitions list (Carroll Dunham at Matthew
Brown, 390 Broadway, sat on the LA list for weeks in 2026). For every show at a
gallery whose research report lists spaces in other cities, this renders the
show's own exhibition page in headless Chrome and reads the lines right after
the title, where gallery sites state the location. A show is flagged when that
stretch names one of the other-city spaces (its street address or its city) and
none of the gallery's local spaces. Prints FLAG / unchecked / ok lines; exits 1
when anything is flagged. Run it after adding or refreshing shows.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WINDOW = 400          # characters after the title where the location line sits

# words that place a show in the home city; other-city spaces are the report
# locations whose address is outside it
HOME = {
    "los-angeles": {"state": ("CA", "California"),
                    "words": ["Los Angeles", "Beverly Hills", "West Hollywood", "Hollywood",
                              "Santa Monica", "Culver City", "Pasadena", "Venice"]},
}


def street(address: str) -> str:
    """'390 Broadway, New York, NY 10013' -> '390 Broadway'."""
    return address.split(",")[0].strip()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def page_text(url: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=8000",
                              f"--user-data-dir={tmp}", "--dump-dom", url],
                             capture_output=True, text=True, timeout=90).stdout
    out = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", out, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", out))


def exhibition_url(show: dict) -> str | None:
    # the show's own page, not the gallery's exhibitions list
    urls = show.get("source_urls") or []
    for u in urls:
        if not re.search(r"/(exhibitions|current|upcoming)/?$", u):
            return u
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, choices=sorted(HOME))
    ap.add_argument("--slug", nargs="*", default=[])
    args = ap.parse_args()
    home = HOME[args.city]

    registry = {v["id"]: v for v in json.loads((CONTENT / "venues" / f"{args.city}.json").read_text())["venues"]}
    shows = json.loads((CONTENT / f"{args.city}.json").read_text())["shows"]
    flagged = 0
    for s in shows:
        if args.slug and s["slug"] not in args.slug:
            continue
        rep_path = CONTENT / "venues" / "reports" / args.city / f"{s.get('venue_id')}.json"
        if not rep_path.exists():
            continue
        locs = json.loads(rep_path.read_text()).get("locations") or []
        away = [l for l in locs if l.get("address")
                and not any(f", {st}" in l["address"] or f" {st} " in l["address"] for st in home["state"])]
        if not away:
            continue
        rv = registry.get(s["venue_id"], {})
        local = [street(a) for a in [rv.get("address") or s["venue"]["address"]]
                 + [l["address"] for l in rv.get("locations") or []] if a]
        url = exhibition_url(s)
        if not url:
            print(f"unchecked {s['slug']}: no exhibition page in source_urls")
            continue
        try:
            text = norm(page_text(url))
        except subprocess.TimeoutExpired:
            print(f"unchecked {s['slug']}: {url} timed out")
            continue
        if re.search(r"security checkpoint|verifying your browser|just a moment|cloudflare", text[:600]):
            print(f"unchecked {s['slug']}: the site blocked the check; open {url} and read its location by hand")
            continue
        at = text.find(norm(s["title"])) if s.get("title") else -1
        if at < 0:
            print(f"unchecked {s['slug']}: title not found on {url}")
            continue
        near = text[at:at + len(s["title"]) + WINDOW]
        here = [w for w in local + home["words"] if norm(w) in near]
        there = [x for l in away for x in (street(l["address"]), l.get("city") or "") if x and norm(x) in near]
        if there and not here:
            flagged += 1
            print(f"FLAG {s['slug']}: page names {', '.join(there)} and no {args.city} space ({url})")
        else:
            print(f"ok {s['slug']}")
    sys.exit(1 if flagged else 0)


if __name__ == "__main__":
    main()
