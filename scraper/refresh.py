"""Deterministic, LLM-free refresh pass over the venue registry.

For every venue with an exhibitions page URL (or a website to probe), fetch
the page with plain `requests`, extract the main text, fingerprint it, and
compare with the fingerprint stored in the registry. Venues whose listing
changed — or whose saved show is ending, or that are past their cadence — are
written to a prioritized TODO file that refresh scrape sessions consume with
the page text pre-attached (so those sessions rarely need web_search).

    python refresh.py --city los-angeles                 # fetch + fingerprint + TODO
    python refresh.py --city los-angeles --dry-run       # no registry writes, no TODO
    python refresh.py --city los-angeles --discover      # also probe /exhibitions etc. for venues without a URL

Cost: $0. Politeness: one request per domain every 2s, robots.txt honoured,
1.5 MB body cap, conditional requests (ETag / Last-Modified -> 304 = free).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

UA = "GalleryBrowserRefresh/1.0 (+personal gallery-guide project; polite crawler, 1 req/2s/domain)"
TIMEOUT = 20
BODY_CAP = 1_500_000
DOMAIN_SPACING = 2.0
PROBE_PATHS = ("/exhibitions", "/exhibitions/current", "/current", "/on-view", "/whats-on",
               "/shows", "/exhibitions/on-view", "/current-exhibition", "/current-exhibitions",
               "/exhibition", "/program", "/programs")
DROP_TAGS = {"script", "style", "noscript", "svg", "iframe", "form", "nav", "header", "footer",
             "aside", "template", "button", "select", "option", "video", "audio", "canvas"}
DROP_CLASS_RE = re.compile(r"cookie|consent|gdpr|banner|newsletter|popup|modal|subscribe|"
                           r"announcement|mailchimp|signup|social|share", re.I)
MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
         r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
DATE_RE = re.compile(
    rf"(?:\b{MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    rf"(?:\s*(?:[-–—]|through|thru|to|until)\s*(?:{MONTH}\.?\s+)?\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?)?"
    rf"|\b\d{{1,2}}\s+{MONTH}\.?(?:\s+\d{{4}})?"
    rf"|\b\d{{4}}-\d{{2}}-\d{{2}}\b"
    rf"|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b)", re.I)
JS_HINT_RE = re.compile(r'id="__next"|data-reactroot|ng-app|id="root"|__NUXT__|enable javascript|'
                        r'requires javascript|please enable', re.I)


# --- text extraction -------------------------------------------------------------

class _TextParser(HTMLParser):
    """Main-content text: drops chrome (nav/header/footer/scripts) and elements
    whose id/class smell like cookie banners or newsletter modals; prefers
    <main>/<article>/role=main when present."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []   # (tag, dropped)
        self.drop_depth = 0
        self.main_depth = 0
        self.main_seen = False
        self.all_lines: list[str] = []
        self.main_lines: list[str] = []
        self.buf: list[str] = []
        self.title = ""
        self._in_title = False

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if text:
            self.all_lines.append(text)
            if self.main_depth:
                self.main_lines.append(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        dropped = tag in DROP_TAGS or bool(DROP_CLASS_RE.search(
            (a.get("id") or "") + " " + (a.get("class") or "")))
        if tag == "title":
            self._in_title = True
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "br", "tr", "section",
                   "article", "main", "figcaption", "dt", "dd"):
            self._flush()
        if not dropped and (tag in ("main", "article") or a.get("role") == "main"):
            self.main_depth += 1
            self.main_seen = True
            main_open = True
        else:
            main_open = False
        if dropped:
            self.drop_depth += 1
        self.stack.append((tag, dropped, main_open) if False else (tag, dropped))
        if main_open:
            self.stack[-1] = (tag + "\0main", dropped)

    def handle_endtag(self, tag):
        self._flush()
        # pop to the matching tag (HTML is sloppy)
        for i in range(len(self.stack) - 1, -1, -1):
            t, dropped = self.stack[i]
            if t.split("\0")[0] == tag:
                for _, d in self.stack[i:]:
                    if d:
                        self.drop_depth -= 1
                for t2, _ in self.stack[i:]:
                    if t2.endswith("\0main"):
                        self.main_depth -= 1
                del self.stack[i:]
                break
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.drop_depth == 0 and data.strip():
            self.buf.append(data)

    def text(self) -> str:
        self._flush()
        lines = self.main_lines if self.main_seen and len(" ".join(self.main_lines)) > 200 \
            else self.all_lines
        return "\n".join(lines)


def extract_main_text(html: str) -> tuple[str, str]:
    p = _TextParser()
    try:
        p.feed(html)
    except Exception:
        pass
    return p.text(), re.sub(r"\s+", " ", p.title).strip()


def fingerprint(text: str) -> dict:
    norm = re.sub(r"\s+", " ", text.lower()).strip()
    dates = sorted({m.group(0).strip() for m in DATE_RE.finditer(text)})
    date_lines = sorted({ln.strip() for ln in text.splitlines() if DATE_RE.search(ln)})
    return {
        "text_hash": hashlib.sha1(norm.encode("utf-8", "ignore")).hexdigest(),
        "date_sig": hashlib.sha1("\n".join(date_lines).lower().encode()).hexdigest() if date_lines else None,
        "date_strings": dates[:60],
        "text_chars": len(norm),
    }


def looks_js_rendered(html: str, text: str) -> bool:
    return len(text) < 300 and bool(JS_HINT_RE.search(html) or len(html) > 20_000)


# --- polite fetching ---------------------------------------------------------------

class Fetcher:
    def __init__(self, spacing: float = DOMAIN_SPACING):
        self.spacing = spacing
        self.last: dict[str, float] = {}
        self.robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA

    def _wait(self, host: str):
        while True:
            with self.lock:
                t = self.last.get(host, 0.0)
                now = time.time()
                if now - t >= self.spacing:
                    self.last[host] = now
                    return
                wait = self.spacing - (now - t)
            time.sleep(min(wait, self.spacing))

    def _allowed(self, url: str) -> bool:
        host = urlparse(url).netloc
        with self.lock:
            rp = self.robots.get(host, "unset")
        if rp == "unset":
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._wait(host)
                r = self.session.get(f"{urlparse(url).scheme}://{host}/robots.txt", timeout=10)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                else:
                    rp = None
            except Exception:
                rp = None
            with self.lock:
                self.robots[host] = rp
        return True if rp is None else rp.can_fetch(UA.split("/")[0], url)

    def get(self, url: str, etag: str | None = None, last_modified: str | None = None) -> dict:
        """{status, html|None, etag, last_modified, error, final_url}; status 304 = unchanged."""
        out = {"status": None, "html": None, "etag": None, "last_modified": None,
               "error": None, "final_url": url}
        if not self._allowed(url):
            out["error"] = "robots_disallow"
            return out
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        host = urlparse(url).netloc
        self._wait(host)
        try:
            r = self.session.get(url, headers=headers, timeout=TIMEOUT, stream=True,
                                 allow_redirects=True)
            out["status"] = r.status_code
            out["final_url"] = r.url
            out["etag"] = r.headers.get("ETag")
            out["last_modified"] = r.headers.get("Last-Modified")
            if r.status_code == 304:
                return out
            if r.status_code != 200:
                out["error"] = f"http_{r.status_code}"
                return out
            chunks, size = [], 0
            for chunk in r.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size >= BODY_CAP:
                    break
            raw = b"".join(chunks)
            # r.apparent_encoding reads r.content, which raises "content already
            # consumed" after iter_content — sniff the bytes we hold instead.
            enc = r.encoding if r.encoding and "charset" in r.headers.get("Content-Type", "").lower() \
                else None
            if not enc:
                m = re.search(rb'<meta[^>]+charset=["\']?([A-Za-z0-9_-]+)', raw[:4096], re.I)
                enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
            try:
                out["html"] = raw.decode(enc or "utf-8", "replace")
            except LookupError:
                out["html"] = raw.decode("utf-8", "replace")
        except requests.RequestException as exc:
            out["error"] = type(exc).__name__
        return out


# --- discovery -----------------------------------------------------------------

def discover_exhibitions_url(fetcher: Fetcher, v: dict) -> tuple[str | None, dict | None]:
    """Probe common listing paths on the venue's site; pick the 200 with the
    most date strings (bonus when it names a known show). Returns (url, page)."""
    site = v.get("website")
    if not site:
        return None, None
    base = site if site.startswith("http") else "https://" + site
    known_titles = [s.get("title") or "" for s in v.get("last_known_shows", [])]
    best, best_score, best_page = None, -1, None
    cands = list(dict.fromkeys(v.get("exhibitions_url_candidates", []) + [urljoin(base, p) for p in PROBE_PATHS]))
    for url in cands[:10]:
        r = fetcher.get(url)
        if r["status"] != 200 or not r["html"]:
            continue
        text, _ = extract_main_text(r["html"])
        fp = fingerprint(text)
        score = len(fp["date_strings"]) + sum(3 for t in known_titles if t and t.lower() in text.lower())
        if venues.NON_CURRENT_PATH_RE.search(urlparse(r["final_url"]).path or ""):
            score -= 5   # /upcoming, /past, /future-exhibitions: never the "on now" page
        if score > best_score:
            best, best_score, best_page = r["final_url"], score, {"r": r, "text": text, "fp": fp}
    return (best if best_score > 0 else None), best_page


# --- per-venue refresh ------------------------------------------------------------

def refresh_venue(fetcher: Fetcher, v: dict, today: date, discover: bool, force: bool) -> dict:
    """Fetch + fingerprint one venue; returns the registry patch + change info."""
    page = dict(v.get("page") or {})
    result = {"id": v["id"], "name": v["name"], "changed": False, "reasons": [],
              "patch": {"page": page}, "text": None, "fetched": False}
    url = v.get("exhibitions_url")
    fetched_page = None
    if not url and discover:
        url, fetched_page = discover_exhibitions_url(fetcher, v)
        if url:
            result["patch"]["exhibitions_url"] = url
            result["patch"]["exhibitions_url_source"] = "discovered"
    if not url:
        result["reasons"].append("no_exhibitions_url")
        return result
    if fetched_page is None:
        r = fetcher.get(url, None if force else page.get("etag"),
                        None if force else page.get("last_modified"))
    else:
        r = fetched_page["r"]
    now = int(time.time())
    page["fetched_ts"] = now
    page["http_status"] = r["status"]
    result["fetched"] = True
    if r["error"]:
        page["fetch_mode"] = "blocked" if r["error"] == "robots_disallow" else "error"
        result["reasons"].append(r["error"])
        return result
    if r["status"] == 304:
        result["reasons"].append("not_modified")
        return result
    page["etag"], page["last_modified"] = r["etag"], r["last_modified"]
    if fetched_page is not None:
        text, fp = fetched_page["text"], fetched_page["fp"]
    else:
        text, _title = extract_main_text(r["html"] or "")
        fp = fingerprint(text)
    if looks_js_rendered(r["html"] or "", text):
        page["fetch_mode"] = "js"
        result["reasons"].append("js_rendered")
    else:
        page["fetch_mode"] = "static"
    old_text_hash, old_date_sig = page.get("text_hash"), page.get("date_sig")
    old_dates = set(page.get("date_strings") or [])
    page.update({k: fp[k] for k in ("text_hash", "date_sig", "date_strings", "text_chars")})
    if old_text_hash and fp["text_hash"] != old_text_hash:
        result["changed"] = True
        if old_date_sig != fp["date_sig"]:
            result["reasons"].append("dates_changed")
            result["date_strings_new"] = sorted(set(fp["date_strings"]) - old_dates)[:10]
            result["date_strings_gone"] = sorted(old_dates - set(fp["date_strings"]))[:10]
        else:
            result["reasons"].append("text_changed")
        page["changed_ts"] = now
    elif not old_text_hash:
        result["reasons"].append("first_fingerprint")
    result["text"] = text
    if text:
        ep = venues.write_evidence(v.get("_city", ""), url, "refresh", text, f"refresh-{now}", v["id"])
        page["evidence_path"] = ep
    return result


def refresh_city(city: str, zones: list[str] | None = None, workers: int = 8,
                 discover: bool = False, force: bool = False, dry_run: bool = False,
                 limit: int | None = None, today: date | None = None) -> list[dict]:
    """Run the pass; write registry page fingerprints (unless dry_run); return
    the prioritized TODO items (venues that need an LLM session)."""
    today = today or date.today()
    reg = venues.load_registry(city)
    # Parked Places-only venues (implausible name, never vouched) are not
    # fingerprinted here: seed_venues.py --vouch-parked probes them instead.
    targets = [v for v in reg["venues"]
               if v.get("status") in ("active", "unknown", None)
               and (not zones or v.get("neighborhood") in zones)
               and (v.get("exhibitions_url") or (discover and v.get("website")))
               and not (venues.seed_only_places(v) and not venues.places_plausible(v))]
    if limit:
        targets = targets[:limit]
    for v in targets:
        v["_city"] = city
    fetcher = Fetcher()
    results: list[dict] = []

    def _safe(v: dict) -> dict:
        # One misbehaving site must never abort a city-wide pass (a decode
        # error once killed the whole run before anything was written).
        try:
            return refresh_venue(fetcher, v, today, discover, force)
        except Exception as exc:
            return {"id": v["id"], "name": v["name"], "changed": False,
                    "reasons": [f"error:{type(exc).__name__}"], "patch": {},
                    "text": None, "fetched": False}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(_safe, targets):
            results.append(res)
    by_id = {r["id"]: r for r in results}
    if not dry_run:
        with venues.locked_registry(city) as reg2:
            idx = venues.index_by_id(reg2)
            for r in results:
                v = idx.get(r["id"])
                if not v:
                    continue
                venues.merge_patch(v, r["patch"])
                v["last_checked"] = int(time.time())
    # TODO items: due venues (registry scheduling) + anything whose dates changed
    due = venues.due_venues(city, None, today)
    due_ids = {d["id"] for d in due}
    items = []
    for v in reg["venues"]:
        r = by_id.get(v["id"])
        changed = r and "dates_changed" in r["reasons"]
        if v["id"] not in due_ids and not changed:
            continue
        pr = next((d["_priority"] for d in due if d["id"] == v["id"]), 0)
        reasons = next((d["_reasons"] for d in due if d["id"] == v["id"]), [])
        if changed:
            pr += 30
            reasons = list(reasons) + ["dates_changed"]
        show = venues.active_show(v, today)
        items.append({
            "venue_id": v["id"], "name": v["name"], "zone": v.get("neighborhood"),
            "website": v.get("website"), "exhibitions_url": v.get("exhibitions_url"),
            "kind": v.get("kind"), "address": v.get("address"),
            "priority": pr, "reasons": reasons,
            "last_known_show": show,
            "existing_images": [],
            "page_text_path": (r or {}).get("patch", {}).get("page", {}).get("evidence_path"),
            "page_text_chars": (r or {}).get("patch", {}).get("page", {}).get("text_chars", 0),
            "date_strings_new": (r or {}).get("date_strings_new", []),
            "date_strings_gone": (r or {}).get("date_strings_gone", []),
        })
    items.sort(key=lambda i: (-i["priority"], i["venue_id"]))
    if not dry_run:
        todo_dir = tools.CONTENT_DIR / "spend" / "todo"
        todo_dir.mkdir(parents=True, exist_ok=True)
        out = todo_dir / f"refresh-{city}-{int(time.time())}.json"
        out.write_text(json.dumps({"city": city, "generated": int(time.time()), "items": items},
                                  indent=1, ensure_ascii=False))
        print(f"wrote {out} ({len(items)} items)")
    return items


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--zones", default=None, help="comma-separated zones")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--discover", action="store_true",
                    help="probe common listing paths for venues without exhibitions_url")
    ap.add_argument("--force", action="store_true", help="ignore ETag/Last-Modified")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    zones = [z.strip() for z in args.zones.split(",")] if args.zones else None
    t0 = time.time()
    items = refresh_city(args.city, zones, args.workers, args.discover, args.force,
                         args.dry_run, args.limit)
    reg = venues.load_registry(args.city)
    fetched = [v for v in reg["venues"] if ((v.get("page") or {}).get("fetched_ts") or 0) >= int(t0)]
    modes: dict[str, int] = {}
    for v in fetched:
        m = v["page"].get("fetch_mode") or "?"
        modes[m] = modes.get(m, 0) + 1
    print(f"{args.city}: fetched {len(fetched)} venue pages in {time.time() - t0:.0f}s; modes {modes}")
    for it in items[:40]:
        print(f"  {it['priority']:>3} {it['venue_id']:<34} {', '.join(it['reasons'])}"
              + (f"  new dates: {it['date_strings_new'][:3]}" if it.get("date_strings_new") else ""))


if __name__ == "__main__":
    main()
