"""Whole-site crawl of one venue's website, $0 (stage G3.1 of the galleries-first
pipeline; see docs/GALLERIES.md).

    index = crawl.crawl_site(city, venue, max_pages=300)
    python crawl.py --city los-angeles --venue-id regen-projects [--max-pages 300] [--no-render]

Politeness and limits come from refresh.Fetcher (robots.txt honoured, one request
per domain every 2 s, 1.5 MB body cap). Discovery: robots.txt ``Sitemap:`` lines,
``/sitemap.xml`` and ``/sitemap_index.xml`` (sitemap indexes recursed), then a
breadth-first walk of in-domain links from the homepage — sitemap URLs seed the
queue but never replace the walk, since many gallery sitemaps are partial.
JS-only pages (refresh.looks_js_rendered, or a 200 whose extracted text is under
200 chars) are rendered with render_fetch.js (Playwright, Chrome channel) so the
walk also sees links the server never sends.

NO path heuristic decides what a page *is*: every fetched page lands in the index
(url, title, text_chars, dates, depth, evidence_path ...) and the LLM triage in
research_venue.py reads that inventory. The only filters are mechanical: same
registrable domain, not a media/binary extension, not mailto:/tel:.

Storage: page text -> venues.write_evidence(kind="crawl") (gitignored evidence
cache, indexed by venue_id); the page inventory -> ``scraper/.cache/crawl/<city>/
<venue_id>.jsonl`` (one row per page) with ``<venue_id>.meta.json`` beside it.
Re-running reuses any page fetched under 30 days ago whose evidence file still
exists, so an interrupted crawl resumes instead of restarting.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import refresh  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

SCRAPER_DIR = Path(__file__).resolve().parent
CRAWL_DIR = SCRAPER_DIR / ".cache" / "crawl"
FRESH_DAYS = 30
MIN_TEXT_CHARS = 200          # a 200 with less text than this is re-tried through the renderer
RENDER_MAX_CHARS = 120_000
RENDER_TIMEOUT_S = 60
RENDER_BUDGET = 60                    # Playwright renders per venue crawl
TRANSIENT_ERRORS = ("ConnectTimeout", "ReadTimeout", "Timeout", "ConnectionError",
                    "ChunkedEncodingError", "TooManyRedirects")
RETRY_PAUSE_S = 3.0
MAX_SITEMAPS = 8
SKIP_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".bmp", ".tif", ".tiff", ".ico",
    ".pdf", ".zip", ".gz", ".tar", ".rar", ".dmg", ".exe",
    ".mp4", ".mov", ".m4v", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".ogg",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom", ".txt",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
}
SKIP_SCHEMES = ("mailto:", "tel:", "javascript:", "sms:", "data:", "ftp:")
DROP_QUERY_RE = re.compile(r"^(utm_|fbclid|gclid|mc_cid|mc_eid|ref$|share$)", re.I)


# --- URL helpers -------------------------------------------------------------------

def normalize_url(url: str, base: str | None = None) -> str | None:
    """Absolute, fragment-free, tracking-param-free URL; None when unusable."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if url.lower().startswith(SKIP_SCHEMES):
        return None
    try:
        if base:
            url = urljoin(base, url)
        p = urlparse(url)
    except ValueError:   # e.g. href "http://リンク：https://…" (fullwidth colon in the host)
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = p.netloc.lower()
    path = p.path or "/"
    path = re.sub(r"/{2,}", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not DROP_QUERY_RE.match(k)]
    return urlunparse((p.scheme.lower(), host, path, "", urlencode(query), ""))


def site_root(website: str) -> str | None:
    if not website:
        return None
    w = website.strip()
    if "://" not in w:
        w = "https://" + w
    n = normalize_url(w)
    if not n:
        return None
    p = urlparse(n)
    return urlunparse((p.scheme, p.netloc, "/", "", "", ""))


def same_site(url: str, domain: str) -> bool:
    d = venues.registrable_domain(url)
    return bool(d) and (d == domain or d.endswith("." + domain) or domain.endswith("." + d))


# CMS plumbing that never carries gallery prose but floods sitemaps (WordPress
# media "attachment" pages were 169 of Regen Projects' 300-page budget).
JUNK_PATH_RE = re.compile(r"/(attachment|wp-content|wp-json|wp-includes|feed|xmlrpc\.php|trackback|"
                          r"cdn-cgi|_next/|static/|assets/)(/|$)|[?&]attachment_id=", re.I)


def skippable(url: str) -> bool:
    path = urlparse(url).path.lower()
    ext = os.path.splitext(path)[1]
    return ext in SKIP_EXT or bool(JUNK_PATH_RE.search(url))


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.base: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "base" and a.get("href"):
            self.base = a["href"]
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "link" and a.get("rel") and "next" in a["rel"].lower() and a.get("href"):
            self.links.append(a["href"])


def extract_links(html: str, page_url: str) -> list[str]:
    p = _LinkParser()
    try:
        p.feed(html or "")
    except Exception:  # noqa: BLE001 - sloppy HTML never aborts a crawl
        pass
    base = page_url
    if p.base:
        try:
            base = urljoin(page_url, p.base)
        except ValueError:
            pass
    out, seen = [], set()
    for href in p.links:
        n = normalize_url(href, base)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# --- sitemaps ------------------------------------------------------------------------

def _sitemap_locs(xml_text: str) -> tuple[list[str], list[str]]:
    """(page urls, nested sitemap urls) from a urlset or sitemapindex."""
    pages, nested = [], []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return pages, nested
    tag = root.tag.lower()
    for el in root.iter():
        if el.tag.lower().endswith("loc") and el.text:
            (nested if tag.endswith("sitemapindex") else pages).append(el.text.strip())
    return pages, nested


def discover_sitemap_urls(fetcher: refresh.Fetcher, root: str, domain: str) -> tuple[list[str], bool]:
    """Every in-domain page URL the site's sitemaps list; (urls, found_any)."""
    cands: list[str] = []
    rb = fetcher.get(urljoin(root, "/robots.txt"))
    if rb.get("status") == 200 and rb.get("html"):
        for m in re.finditer(r"(?im)^\s*sitemap:\s*(\S+)", rb["html"]):
            cands.append(m.group(1))
    cands += [urljoin(root, "/sitemap.xml"), urljoin(root, "/sitemap_index.xml"),
              urljoin(root, "/sitemap-index.xml")]
    seen_maps: set[str] = set()
    pages: list[str] = []
    found = False
    queue = list(dict.fromkeys(cands))
    while queue and len(seen_maps) < MAX_SITEMAPS:
        sm = queue.pop(0)
        if sm in seen_maps:
            continue
        seen_maps.add(sm)
        r = fetcher.get(sm)
        if r.get("status") != 200 or not r.get("html") or "<" not in r["html"][:200]:
            continue
        ps, nested = _sitemap_locs(r["html"])
        if ps or nested:
            found = True
        pages.extend(ps)
        queue.extend(n for n in nested if n not in seen_maps)
    out, seen = [], set()
    for u in pages:
        n = normalize_url(u)
        if n and same_site(n, domain) and not skippable(n) and n not in seen:
            seen.add(n)
            out.append(n)
    return out, found


# --- rendering -----------------------------------------------------------------------

def render_page(url: str, max_chars: int = RENDER_MAX_CHARS) -> dict | None:
    """{final_url, title, text, html} via render_fetch.js, or None on failure."""
    script = tools.RENDER_SCRIPT
    if not script.exists():
        return None
    env = {**os.environ,
           "NODE_PATH": os.environ.get("GALLERY_NODE_PATH", "/opt/homebrew/lib/node_modules")}
    try:
        proc = subprocess.run(["node", str(script), url, str(max_chars)], capture_output=True,
                              text=True, timeout=RENDER_TIMEOUT_S, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        data = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("error"):
        return None
    return {"final_url": data.get("final_url") or url, "title": data.get("title") or "",
            "text": data.get("text") or "", "html": data.get("html") or ""}


# --- index ---------------------------------------------------------------------------

@dataclass
class CrawlPage:
    url: str
    title: str
    text_chars: int
    dates: int
    depth: int
    evidence_path: str | None
    fetched_ts: int
    http_status: int | None
    rendered: bool
    final_url: str | None = None
    error: str | None = None
    source: str = "walk"          # walk | sitemap | requested


@dataclass
class CrawlIndex:
    city: str
    venue_id: str
    site: str
    pages: list[CrawlPage] = field(default_factory=list)
    sitemap: bool = False
    rendered: bool = False
    started_ts: int = 0
    finished_ts: int = 0
    truncated: bool = False

    # -- paths
    @staticmethod
    def index_path(city: str, venue_id: str) -> Path:
        return CRAWL_DIR / city / f"{venue_id}.jsonl"

    @staticmethod
    def meta_path(city: str, venue_id: str) -> Path:
        return CRAWL_DIR / city / f"{venue_id}.meta.json"

    @property
    def path(self) -> Path:
        return self.index_path(self.city, self.venue_id)

    def by_url(self) -> dict[str, CrawlPage]:
        return {p.url: p for p in self.pages}

    def ok_pages(self) -> list[CrawlPage]:
        return [p for p in self.pages if p.evidence_path and p.text_chars > 0]

    def text(self, page: CrawlPage) -> str:
        if not page.evidence_path:
            return ""
        f = SCRAPER_DIR / page.evidence_path
        try:
            return f.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            for p in self.pages:
                fh.write(json.dumps(asdict(p), ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)
        meta = {k: v for k, v in asdict(self).items() if k != "pages"}
        meta["n_pages"] = len(self.pages)
        self.meta_path(self.city, self.venue_id).write_text(json.dumps(meta, indent=1))
        return self.path

    @classmethod
    def load(cls, city: str, venue_id: str) -> "CrawlIndex | None":
        ip, mp = cls.index_path(city, venue_id), cls.meta_path(city, venue_id)
        if not ip.exists():
            return None
        meta = {}
        if mp.exists():
            try:
                meta = json.loads(mp.read_text())
            except json.JSONDecodeError:
                meta = {}
        idx = cls(city=city, venue_id=venue_id, site=meta.get("site") or "",
                  sitemap=bool(meta.get("sitemap")), rendered=bool(meta.get("rendered")),
                  started_ts=meta.get("started_ts") or 0, finished_ts=meta.get("finished_ts") or 0,
                  truncated=bool(meta.get("truncated")))
        for line in ip.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                idx.pages.append(CrawlPage(**{k: row.get(k) for k in CrawlPage.__dataclass_fields__}))
            except (json.JSONDecodeError, TypeError):
                continue
        return idx


# --- the crawl -------------------------------------------------------------------------

def _fresh(page: CrawlPage, now: int) -> bool:
    if not page.evidence_path or page.error:
        return False
    if now - (page.fetched_ts or 0) > FRESH_DAYS * 86400:
        return False
    return (SCRAPER_DIR / page.evidence_path).exists()


class RenderBudget:
    """Rendering is the crawl's real cost: a JS-heavy site (75% of Tokyo gallery
    sites) spawns one Playwright process per page at ~8s, so a 300-page crawl
    burns 40 minutes of CPU while the model only ever reads a few sections.
    The budget renders the first `n` pages that ask for it — section-fair queue
    ordering means those are the about/exhibitions/roster pages, not the 200th
    per-artist subpage — and leaves the rest their static text."""

    def __init__(self, n: int = RENDER_BUDGET):
        self.left = n
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            if self.left <= 0:
                return False
            self.left -= 1
            return True


def fetch_page(fetcher: refresh.Fetcher, url: str, render: bool,
               budget: "RenderBudget | None" = None) -> dict:
    """{status, final_url, title, text, html, rendered, error} for one URL."""
    r = fetcher.get(url)
    out = {"status": r.get("status"), "final_url": r.get("final_url") or url, "title": "",
           "text": "", "html": r.get("html") or "", "rendered": False, "error": r.get("error")}
    if r.get("status") == 200 and r.get("html"):
        text, title = refresh.extract_main_text(r["html"])
        out["text"], out["title"], out["error"] = text, title, None
        if (render and (refresh.looks_js_rendered(r["html"], text) or len(text) < MIN_TEXT_CHARS)
                and (budget is None or budget.take())):
            rd = render_page(url)
            if rd and len(rd["text"]) > len(text):
                out.update({"text": rd["text"], "title": rd["title"] or title,
                            "html": rd["html"] or r["html"], "final_url": rd["final_url"],
                            "rendered": True})
    elif (render and r.get("status") in (403, 429, 503, None) and r.get("error") != "robots_disallow"
            and (budget is None or budget.take())):
        # bot-walled or connection-refused: a real browser often gets through
        rd = render_page(url)
        if rd and rd["text"]:
            out.update({"status": 200, "text": rd["text"], "title": rd["title"], "html": rd["html"],
                        "final_url": rd["final_url"], "rendered": True, "error": None})
    return out


def crawl_site(city: str, venue: dict, max_pages: int = 300, fetcher: refresh.Fetcher | None = None,
               render: bool = True, session: str | None = None, extra_urls: list[str] | None = None,
               progress=None, render_budget: int = RENDER_BUDGET) -> CrawlIndex:
    """Crawl ``venue['website']`` (plus ``extra_urls``, e.g. pages the triage asked
    for) and return the CrawlIndex; pages fetched under FRESH_DAYS ago are reused."""
    fetcher = fetcher or refresh.Fetcher()
    root = site_root(venue.get("website") or "")
    vid = venue["id"]
    now = int(time.time())
    idx = CrawlIndex.load(city, vid) or CrawlIndex(city=city, venue_id=vid, site=root or "")
    # a rule added after an earlier crawl drops the pages it would now skip
    idx.pages = [p for p in idx.pages if not skippable(p.url)]
    idx.site = idx.site or (root or "")
    idx.started_ts = idx.started_ts or now
    idx.truncated = False
    if not root:
        idx.finished_ts = now
        idx.save()
        return idx
    domain = venues.registrable_domain(root) or urlparse(root).netloc.lower()
    known = idx.by_url()
    seen: set[str] = set(known)
    queue: list[tuple[str, int, str]] = []   # (url, depth, source)

    def enqueue(url: str | None, depth: int, source: str) -> None:
        if not url or url in seen or skippable(url) or not same_site(url, domain):
            return
        seen.add(url)
        queue.append((url, depth, source))

    home = normalize_url(root)
    seen.discard(home)
    enqueue(home, 0, "walk")
    for u in extra_urls or []:
        n = normalize_url(u, root)
        if n in known and _fresh(known[n], now):
            continue
        seen.discard(n)
        enqueue(n, 1, "requested")
    sm_urls, found = discover_sitemap_urls(fetcher, root, domain)
    idx.sitemap = idx.sitemap or found
    for u in sm_urls:
        enqueue(u, 1, "sitemap")
    # previously indexed pages re-enter the queue only when stale
    for u, p in known.items():
        if not _fresh(p, now):
            seen.discard(u)
            enqueue(u, p.depth or 1, p.source or "walk")

    # Queue order decides what a capped crawl actually sees. FIFO with a
    # sitemap seeded before the walk let 300 per-artist pages crowd out the
    # about/exhibitions pages on a roster-heavy site (Regen Projects, 2026-09-03),
    # so pages are picked by: requested > homepage nav links (walk depth 1) >
    # sitemap > deeper walk; within a tier, the SECTION (first path segment)
    # with the fewest fetched pages goes first, then shallower paths, then FIFO.
    section_counts: dict[str, int] = {}

    def section(u: str) -> str:
        parts = [x for x in urlparse(u).path.split("/") if x]
        return parts[0].lower() if parts else ""

    for p in known.values():
        if p.evidence_path:
            section_counts[section(p.url)] = section_counts.get(section(p.url), 0) + 1

    def pick() -> tuple[str, int, str]:
        tier = {"requested": 0, "walk": 1, "sitemap": 2}
        best_i, best_key = 0, None
        for i, (u, d, src) in enumerate(queue):
            t = tier.get(src, 3)
            if src == "walk" and d > 1:
                t = 3
            key = (t, section_counts.get(section(u), 0), u.count("/"), i)
            if best_key is None or key < best_key:
                best_i, best_key = i, key
        return queue.pop(best_i)

    budget = RenderBudget(render_budget)
    fetched_now = 0
    while queue:
        n_ok = len([p for p in idx.pages if p.evidence_path])
        if n_ok >= max_pages:
            idx.truncated = bool(queue)
            break
        url, depth, source = pick()
        res = fetch_page(fetcher, url, render, budget)
        if res["status"] is None and res["error"] in TRANSIENT_ERRORS:
            # a timeout under load is not evidence about the site: one retry,
            # after the politeness gap, before the page is banked as unreadable
            time.sleep(RETRY_PAUSE_S)
            res = fetch_page(fetcher, url, render, budget)
        text = res["text"] or ""
        ev = None
        if res["status"] == 200 and text.strip():
            ev = venues.write_evidence(city, res["final_url"] or url, "crawl", text, session, vid)
            section_counts[section(url)] = section_counts.get(section(url), 0) + 1
        page = CrawlPage(url=url, title=(res["title"] or "")[:200], text_chars=len(text),
                         dates=sum(1 for _ in refresh.DATE_RE.finditer(text)), depth=depth,
                         evidence_path=ev, fetched_ts=int(time.time()), http_status=res["status"],
                         rendered=bool(res["rendered"]), final_url=res["final_url"],
                         error=res["error"], source=source)
        idx.rendered = idx.rendered or page.rendered
        if url in known:
            idx.pages[idx.pages.index(known[url])] = page
        else:
            idx.pages.append(page)
        known[url] = page
        fetched_now += 1
        if progress:
            progress(page, len(queue))
        if res["status"] == 200 and res["html"]:
            final = normalize_url(res["final_url"] or url)
            if final and final != url:
                seen.add(final)
            for link in extract_links(res["html"], res["final_url"] or url):
                enqueue(link, depth + 1, "walk")
        if fetched_now % 10 == 0:
            idx.save()
    idx.finished_ts = int(time.time())
    idx.save()
    return idx


def inventory_lines(idx: CrawlIndex, limit: int | None = None) -> list[str]:
    """``i | url | title | chars | dates`` rows for the triage prompt, index i
    being the position in ``idx.ok_pages()``."""
    rows = []
    for i, p in enumerate(idx.ok_pages()):
        if limit and i >= limit:
            break
        rows.append(f"{i} | {p.url} | {p.title or '-'} | {p.text_chars} | {p.dates}")
    return rows


def main() -> None:
    import argparse
    from cities import CITIES
    ap = argparse.ArgumentParser(description="crawl one venue's website into the crawl index ($0)")
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--venue-id", required=True)
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--no-render", action="store_true")
    args = ap.parse_args()
    reg = venues.load_registry(args.city)
    v = venues.index_by_id(reg).get(args.venue_id)
    if not v:
        raise SystemExit(f"unknown venue {args.venue_id}")
    t0 = time.time()

    def prog(page: CrawlPage, left: int) -> None:
        print(f"  {page.http_status or '-':>4} {'R' if page.rendered else ' '} {page.text_chars:>7} "
              f"{page.dates:>3}d  {page.url}  (queue {left})", flush=True)

    idx = crawl_site(args.city, v, max_pages=args.max_pages, render=not args.no_render,
                     session=f"crawl-{args.city}-{int(t0)}", progress=prog)
    ok = idx.ok_pages()
    print(f"{args.venue_id}: {len(idx.pages)} page(s), {len(ok)} with text, sitemap={idx.sitemap}, "
          f"rendered={idx.rendered}, truncated={idx.truncated}, {time.time() - t0:.0f}s -> {idx.path}")


if __name__ == "__main__":
    main()
