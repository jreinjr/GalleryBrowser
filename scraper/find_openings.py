"""Find upcoming openings and receptions on the galleries' own sites, for any city.

Carlton (2026-10-31): openings and reception times must come from each
gallery's own website, found by us, not from See Saw (which covers few cities).
This sweep renders each top-N gallery's home and exhibitions pages in headless
Chrome (no Playwright needed), follows their upcoming / current / events / news
/ press links, and lists

  * every date range starting in the window that the city file doesn't carry
    for that gallery (a show the app is missing), and
  * every "reception / opening / join us" line near a date in the window
    (a reception time to add).

    python find_openings.py --city los-angeles [--top 150] [--from 2026-11-01] [--days 45]

$0: no LLM, no paid scraper. Output is a worklist to read by hand: confirm each
show on its own page (dates, reception, pictures), add it, then run
check_show_dates.py and check_show_city.py as usual. Watch for year-less
listings from past seasons (a "Nov 15" post can be last year's).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.4 Safari/605.1.15")
CACHE = Path("/tmp/find_openings_cache")
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
FOLLOW = re.compile(r"(upcoming|forthcoming|future|current|events?|news|press|exhibitions?)", re.I)
SKIP = re.compile(r"(past|archive|\?page|20(0\d|1\d|2[0-5]))", re.I)
OLD_YEAR = re.compile(r"\b(19\d\d|20(0\d|1\d|2[0-5]))\b")
REC = re.compile(r"(?i)(reception|vernissage|opening[: ]|join us|celebrat)")


def render(url: str) -> str:
    CACHE.mkdir(exist_ok=True)
    f = CACHE / (hashlib.md5(url.encode()).hexdigest() + ".html")
    if f.exists() and f.stat().st_size > 500:
        return f.read_text(errors="ignore")
    try:
        out = subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--dump-dom",
                              "--virtual-time-budget=9000", f"--user-agent={UA}",
                              f"--user-data-dir={CACHE / ('p' + hashlib.md5(url.encode()).hexdigest()[:6])}", url],
                             capture_output=True, timeout=70).stdout.decode(errors="ignore")
    except Exception:
        out = ""
    f.write_text(out)
    return out


def text(h: str) -> str:
    h = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", h)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h)))


def links(base: str, h: str) -> list[str]:
    host = urllib.parse.urlparse(base).netloc.replace("www.", "")
    out = []
    for href, label in re.findall(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', h):
        full = urllib.parse.urljoin(base, html.unescape(href))
        if urllib.parse.urlparse(full).netloc.replace("www.", "") != host:
            continue
        if FOLLOW.search(full + " " + text(label)) and not SKIP.search(full):
            out.append(full)
    return list(dict.fromkeys(out))[:8]


def start_dates(t: str, window: tuple[date, date]):
    """(date, context) for every 'Month D – ...' / 'D Mon – ...' range starting in the window."""
    lo, hi = window
    pat = re.compile(r"(?i)\b(?:(?P<m1>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?P<d1>\d{1,2})"
                     r"|(?P<d2>\d{1,2})\s+(?P<m2>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)"
                     r"(?:st|nd|rd|th)?,?\s*(?:\d{4})?\s*(?:–|-|—|to|through)\s*")
    for m in pat.finditer(t):
        mon = (m.group("m1") or m.group("m2")).lower()[:3]
        day = int(m.group("d1") or m.group("d2"))
        ctx = t[max(0, m.start() - 120): m.end() + 60]
        if OLD_YEAR.search(ctx):
            continue
        for year in (lo.year, lo.year + 1):
            try:
                d = date(year, MONTHS.index(mon) + 1, day)
            except ValueError:
                continue
            if lo <= d <= hi:
                yield d, ctx


def sweep(v: dict, window) -> dict:
    pages = [u for u in [v.get("website"), v.get("exhibitions_url")] if u]
    pages = list(dict.fromkeys(pages))
    for u in list(pages):
        pages += [l for l in links(u, render(u)) if l not in pages]
    pages = pages[:10]
    starts, recs = {}, {}
    for u in pages:
        t = text(render(u))
        for d, ctx in start_dates(t, window):
            starts.setdefault((d, re.sub(r"\W", "", ctx[60:140]).lower()), (u, ctx))
        for m in REC.finditer(t):
            ctx = t[max(0, m.start() - 140): m.end() + 140]
            if any(True for _ in start_dates(ctx.replace(" at ", " – "), window)) or re.search(
                    r"(?i)(nov|dec|jan)[a-z]*\.?\s+\d", ctx):
                if not OLD_YEAR.search(ctx):
                    recs.setdefault(re.sub(r"\W", "", ctx[100:200]).lower(), (u, ctx))
    # The gallery's own newsletter archive: a Mailchimp signup form on its site
    # leads to the public campaign archive, where the opening emails often carry
    # the reception time the website leaves out (Ghebaly, Parker, 2026-10-31).
    home = render(pages[0]) if pages else ""
    mc = re.search(r"https?://[a-z0-9-]+\.(us\d+)\.list-manage\.com/subscribe/post\?u=([0-9a-f]+)&(?:amp;)?id=([0-9a-f]+)", home)
    if mc:
        archive = f"https://{mc.group(1)}.campaign-archive.com/home/?u={mc.group(2)}&id={mc.group(3)}"
        for _, url, _ in re.findall(r'(\d\d/\d\d/\d{4})\s*-\s*<a href="([^"]+)"[^>]*>(.*?)</a>', render(archive))[:6]:
            t = text(render(url))
            for m in REC.finditer(t):
                ctx = t[max(0, m.start() - 140): m.end() + 140]
                if re.search(r"(?i)(nov|dec|jan)[a-z]*\.?\s+\d", ctx) and not OLD_YEAR.search(ctx):
                    recs.setdefault(re.sub(r"\W", "", ctx[100:200]).lower(), (url, ctx))
    return {"id": v["id"], "name": v["name"], "rank": v.get("rank"), "pages": pages,
            "starts": sorted(starts.items()), "receptions": list(recs.values()),
            "rendered": sum(len(render(u)) for u in pages)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True)
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--from", dest="start", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    lo = date.fromisoformat(a.start)
    window = (lo, lo + timedelta(days=a.days))
    venues = json.loads((ROOT / f"content/venues/{a.city}.json").read_text())["venues"]
    venues = sorted([v for v in venues if v.get("rank") and v["rank"] <= a.top and not v.get("is_museum")],
                    key=lambda v: v["rank"])
    shows = json.loads((ROOT / f"content/{a.city}.json").read_text())["shows"]
    have = {}
    for s in shows:
        have.setdefault(s.get("venue_id"), set()).add(s.get("start_date"))
    with ThreadPoolExecutor(a.workers) as ex:
        results = list(ex.map(lambda v: sweep(v, window), venues))
    for r in results:
        known = have.get(r["id"], set())
        missing = [(k, v) for k, v in r["starts"] if k[0].isoformat() not in known]
        flag = " (pages did not load: check by hand)" if r["rendered"] < 3000 else ""
        print(f"## {r['rank']:>3} {r['name']}: {len(r['starts'])} openings in window, "
              f"{len(missing)} not in the app{flag}")
        for (d, _), (u, ctx) in missing:
            print(f"   MISSING? {d}  {ctx.strip()[:220]}\n            {u}")
        for u, ctx in r["receptions"][:4]:
            print(f"   RECEPTION  {ctx.strip()[:260]}\n            {u}")


if __name__ == "__main__":
    main()
