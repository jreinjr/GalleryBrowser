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
When a show's own page lacks the date it also reads the gallery's exhibitions
listings and home page, where the date must sit next to the show's title.
Results go to content/spend/dates/<city>.json; refresh.py sends galleries
ranked 1-150 whose dates are unconfirmed back to research at top priority
(Carlton, 2026-10-29: "push really hard until the top 150 have very accurate
closing dates"). Run it after adding or refreshing shows; exits 1 when anything
is flagged or a top-150 gallery's show is unconfirmed (unread counts there).
--all also checks shows that have already closed. Galleries ranked 1-150 need three
distinct pages carrying both the opening and closing dates ("always triple check the top 150");
`--confirm SLUG URL [URL ...]` adds pages read by hand in a browser (bot walls),
which hold until the end_date changes.
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
    fr = FR_MONTHS[d.month - 1]
    fr_day = rf"(?:0?{d.day}|1er)" if d.day == 1 else rf"0?{d.day}"
    return [
        rf"\b{mon}\s+{day}\b",                        # November 22 / Nov. 22nd
        rf"\b{day}\s+{mon}\b",                        # 22 November
        rf"\b0?{m}\s*[/.\-]\s*0?{d.day}\s*[/.\-]\s*(?:20)?{yy}\b",   # 11/22/26, 11.22.2026
        rf"\b{d.year}\s*[\-/.]\s*0?{m}\s*[\-/.]\s*0?{d.day}\b",      # 2026-11-22
        # European pages (Paris): 22 novembre, 1er nov., 22.11.2026, 22/11/26
        rf"(?<!\w){fr_day}\s+{fr}(?!\w)",
        rf"\b0?{d.day}\s*[/.\-]\s*0?{m}\s*[/.\-]\s*(?:20)?{yy}\b",
    ]


# French month names as Paris galleries write them, full or abbreviated
FR_MONTHS = [r"janv(?:ier|\.)?", r"f[ée]vr(?:ier|\.)?", r"mars", r"avr(?:il|\.)?", r"mai",
             r"juin", r"juil(?:let|\.)?", r"ao[ûu]t", r"sept(?:embre|\.)?", r"oct(?:obre|\.)?",
             r"nov(?:embre|\.)?", r"d[ée]c(?:embre|\.)?"]


def shows_date(text: str, iso: str | None) -> bool:
    return bool(iso) and any(re.search(p, text) for p in date_patterns(iso))


def show_urls(show: dict) -> list[str]:
    # the show's own page first; a gallery's exhibitions list as the fallback
    urls = show.get("source_urls") or []
    own = [u for u in urls if not re.search(r"/(exhibitions|current|upcoming)/?$", u)]
    return own + [u for u in urls if u not in own]


def listing_urls(show: dict, reg: dict) -> list[str]:
    """The gallery's own listings, where many sites carry the dates their show
    pages leave out (Kohn's home page, Hoffman Donahue's /upcoming)."""
    v = reg.get(show.get("venue_id")) or {}
    out = []
    for u in (v.get("exhibitions_url"), v.get("website"), show["venue"].get("website")):
        if not u:
            continue
        out.append(u)
        base = u.rstrip("/")
        if not re.search(r"/(exhibitions|current|upcoming)$", base):
            out += [base + "/exhibitions", base + "/exhibitions/upcoming", base + "/upcoming"]
    return list(dict.fromkeys(out))


def carries(text: str, show: dict, both: bool) -> bool:
    # the closing date; for a top-150 gallery the opening date too
    return shows_date(text, show["end_date"]) and \
        (not both or not show.get("start_date") or shows_date(text, show["start_date"]))


def near_title(text: str, show: dict, both: bool) -> bool:
    # on a listing page the date must sit next to this show's title or artist
    for name in (show.get("title"), show.get("artist")):
        name = (name or "").lower().strip()[:30]
        if len(name) < 4:
            continue
        for m in re.finditer(re.escape(name), text):
            if carries(text[max(0, m.start() - 200):m.end() + 300], show, both):
                return True
    return False


NEED_TOP = 3   # pages that must agree on a top-ranked gallery's closing date


def confirmations(show: dict, reg: dict, need: int, both: bool) -> tuple[list[str], bool]:
    """Distinct pages carrying the saved dates (the show's own pages, then
    the gallery's listings and home page), up to `need`; and whether any page
    could be read at all."""
    found, read = [], False
    for url in show_urls(show):
        text = page_text(url)
        if len(text) < 200:
            continue
        read = True
        if carries(text, show, both):
            found.append(url)
            if len(found) >= need:
                return found, read
    for url in listing_urls(show, reg)[:6]:
        if any(url.rstrip("/") == f.rstrip("/") for f in found):
            continue
        text = page_text(url)
        if len(text) < 200:
            continue
        read = True
        if near_title(text, show, both):
            found.append(url)
            if len(found) >= need:
                break
    return found, read


def check(show: dict, reg: dict | None = None, need: int = 1, hand: list[str] | None = None) -> tuple[str, list[str]]:
    """ok when `need` distinct pages carry the saved end_date. Galleries ranked
    1-150 need three, each carrying the opening date too (Carlton, 2026-10-29:
    "always triple check the top 150", "for opening and closing");
    `hand` are pages someone read the date on in a browser, which count too."""
    if not show.get("end_date"):
        museum = show["venue"].get("is_museum")
        return ("open-ended" if museum else "NO END"), []
    hand = hand or []
    # the triple check covers opening and closing; a single check, closing only
    auto, read = confirmations(show, reg or {}, need, need > 1)
    seen, found = set(), []
    for u in auto + hand:   # one page however its URL is spelled
        k = re.sub(r"^https?://(www\.)?|/+$", "", u.lower())
        if k not in seen:
            seen.add(k)
            found.append(u)
    if len(found) >= need:
        return "ok", found
    if found:
        return f"CHECK {len(found)}/{need}", found
    return ("FLAG" if read or hand else "unread"), show_urls(show)[:1]


def ledger_path(city: str) -> Path:
    return CONTENT / "spend" / "dates" / f"{city}.json"


def load_ledger(city: str) -> dict:
    """slug -> {verdict, url, end_date, checked}: the last check of each show.
    refresh.py reads it to send top-ranked galleries with unconfirmed dates
    back to research."""
    try:
        return json.loads(ledger_path(city).read_text())
    except (OSError, ValueError):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--slug", nargs="*")
    ap.add_argument("--all", action="store_true", help="also check shows that have closed")
    ap.add_argument("--top", type=int, default=150,
                    help="galleries ranked this high must have every date confirmed (exit 1 otherwise)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--confirm", nargs="+", metavar="SLUG URL",
                    help="record pages a closing date was read on by hand (bot walls, Instagram-only galleries)")
    args = ap.parse_args()
    shows = json.loads((CONTENT / f"{args.city}.json").read_text())["shows"]
    try:
        reg = {v["id"]: v for v in json.loads((CONTENT / "venues" / f"{args.city}.json").read_text())["venues"]}
    except (OSError, ValueError, KeyError):
        reg = {}
    today = date.today().isoformat()
    if args.confirm:
        pass
    elif args.slug:
        shows = [s for s in shows if s["slug"] in args.slug]
    elif not args.all:
        shows = [s for s in shows if not s.get("end_date") or s["end_date"] >= today]
    ledger = load_ledger(args.city)

    def rank(s: dict) -> int | None:
        return (reg.get(s.get("venue_id")) or {}).get("rank")

    def is_top(s: dict) -> bool:
        r = rank(s)
        return r is not None and r <= args.top and not s["venue"].get("is_museum") \
            and (reg.get(s.get("venue_id")) or {}).get("kind") not in ("museum", "nonprofit")

    def hand_pages(s: dict) -> list[str]:
        # pages read by hand hold until the saved end_date changes
        e = ledger.get(s["slug"]) or {}
        return e.get("hand", []) if e.get("end_date") == s.get("end_date") else []

    if args.confirm:
        slug, urls = args.confirm[0], args.confirm[1:]
        show = next(s for s in shows if s["slug"] == slug)
        e = ledger.get(slug) or {}
        hand = list(dict.fromkeys(hand_pages(show) + urls))
        need = NEED_TOP if is_top(show) else 1
        verdict = "ok" if len(set(hand + e.get("pages", []))) >= need else f"CHECK {len(set(hand + e.get('pages', [])))}/{need}"
        ledger[slug] = {**e, "end_date": show.get("end_date"), "hand": hand, "verdict": verdict, "checked": today}
        ledger_path(args.city).parent.mkdir(parents=True, exist_ok=True)
        ledger_path(args.city).write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")
        print(f"{slug} closes {show.get('end_date')}: {verdict}")
        return

    with ThreadPoolExecutor(args.workers) as pool:
        results = list(pool.map(lambda s: check(s, reg, NEED_TOP if is_top(s) else 1, hand_pages(s)), shows))

    bad = top_bad = 0
    for s, (verdict, pages) in sorted(zip(shows, results), key=lambda x: (x[1][0], rank(x[0]) or 9999)):
        ledger[s["slug"]] = {"verdict": verdict, "end_date": s.get("end_date"), "checked": today,
                             "pages": [u for u in pages if u not in hand_pages(s)], "hand": hand_pages(s)}
        r = rank(s)
        if verdict in ("FLAG", "NO END"):
            bad += 1
        if is_top(s) and verdict != "ok":
            top_bad += 1
        if verdict != "ok":
            print(f"{verdict:10} {'#' + str(r) if r else '-':>5}  {s['slug']}  {s.get('start_date')} → {s.get('end_date')}  "
                  f"{s['venue']['name']} | {s['title']}  {' '.join(pages)}")
    ledger_path(args.city).parent.mkdir(parents=True, exist_ok=True)
    ledger_path(args.city).write_text(json.dumps(ledger, indent=1, ensure_ascii=False) + "\n")
    print(f"\n{len(shows)} checked, {sum(r[0] == 'ok' for r in results)} ok, {bad} flagged; "
          f"{top_bad} at galleries ranked 1-{args.top} without {NEED_TOP} agreeing pages")
    sys.exit(1 if bad or top_bad else 0)


if __name__ == "__main__":
    main()
