"""Check every show's closing (and opening) date against the gallery's own page.

    scraper/.venv/bin/python scraper/check_show_dates.py --city los-angeles [--slug SLUG ...] [--all]

The app hides a show the day after its end_date, so a wrong or missing date
sends people to a closed show (Carlton, 2026-10-29). For each show still on the
list (end_date today or later, or none) this renders the show's own pages in
headless Chrome and looks for the saved dates written the ways gallery sites
write them ("November 22", "Nov 22, 2026", "22 November", "11/22/26",
"11.22.2026"). A show is flagged when its page is readable but shows neither
saved date (`FLAG`), and when it has no end_date at a gallery (`NO END`).
`unread` means no page could be fetched. Museums' long-term installations
legitimately run with no end date and are reported as `open-ended`.
Run it after adding or refreshing shows; exits 1 when anything is flagged.
--all also checks shows that have already closed.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]


def page_text(url: str) -> str:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--virtual-time-budget=8000",
                                  f"--user-data-dir={tmp}", "--dump-dom", url],
                                 capture_output=True, text=True, timeout=90).stdout
    except subprocess.TimeoutExpired:
        return ""
    out = re.sub(r"<(script|style|noscript)\b.*?</\1>", " ", out, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", out))).lower()


def date_patterns(iso: str) -> list[str]:
    """Regexes for one date the way gallery pages write it."""
    d = date.fromisoformat(iso)
    full, abbr = MONTHS[d.month - 1], MONTHS[d.month - 1][:3]
    mon = rf"(?:{full}|{abbr}\.?|{abbr[:3]}t\.?)" if d.month == 9 else rf"(?:{full}|{abbr}\.?)"
    day = rf"0?{d.day}(?:st|nd|rd|th)?"
    m, yy = d.month, d.year % 100
    return [
        rf"\b{mon}\s+{day}\b",                        # November 22 / Nov. 22nd
        rf"\b{day}\s+{mon}\b",                        # 22 November
        rf"\b0?{m}\s*[/.\-]\s*0?{d.day}\s*[/.\-]\s*(?:20)?{yy}\b",   # 11/22/26, 11.22.2026
        rf"\b{d.year}\s*[\-/.]\s*0?{m}\s*[\-/.]\s*0?{d.day}\b",      # 2026-11-22
    ]


def shows_date(text: str, iso: str | None) -> bool:
    return bool(iso) and any(re.search(p, text) for p in date_patterns(iso))


def show_urls(show: dict) -> list[str]:
    # the show's own page first; a gallery's exhibitions list as the fallback
    urls = show.get("source_urls") or []
    own = [u for u in urls if not re.search(r"/(exhibitions|current|upcoming)/?$", u)]
    return own + [u for u in urls if u not in own]


def check(show: dict) -> tuple[str, str]:
    start, end = show.get("start_date"), show.get("end_date")
    if not end:
        museum = show["venue"].get("is_museum")
        return ("open-ended" if museum else "NO END"), ""
    read = False
    for url in show_urls(show)[:3]:
        text = page_text(url)
        if len(text) < 200:
            continue
        read = True
        if shows_date(text, end):
            return "ok", url
    if not read:
        return "unread", (show_urls(show) or [""])[0]
    return "FLAG", (show_urls(show) or [""])[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--slug", nargs="*")
    ap.add_argument("--all", action="store_true", help="also check shows that have closed")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()
    shows = json.loads((CONTENT / f"{args.city}.json").read_text())["shows"]
    today = date.today().isoformat()
    if args.slug:
        shows = [s for s in shows if s["slug"] in args.slug]
    elif not args.all:
        shows = [s for s in shows if not s.get("end_date") or s["end_date"] >= today]
    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(check, shows))
    bad = 0
    for s, (verdict, url) in sorted(zip(shows, results), key=lambda x: x[1][0]):
        if verdict in ("FLAG", "NO END"):
            bad += 1
        if verdict != "ok":
            print(f"{verdict:10} {s['slug']}  {s.get('start_date')} → {s.get('end_date')}  "
                  f"{s['venue']['name']} | {s['title']}  {url}")
    print(f"\n{len(shows)} checked, {sum(r[0] == 'ok' for r in results)} ok, {bad} flagged")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
