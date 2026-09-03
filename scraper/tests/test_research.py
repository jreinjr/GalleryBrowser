"""crawl.py + research_venue.py: crawler scope/caps/sitemap on a local fixture
site, triage/compile schema round-trip with a stubbed client, rerun rule,
report + registry writes. Stdlib unittest, no network beyond localhost.

    PYTHONPATH=scraper/tests:scraper scraper/.venv/bin/python -m unittest tests.test_research
"""

from __future__ import annotations

import http.server
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crawl  # noqa: E402
import harness  # noqa: E402
import refresh  # noqa: E402
import research_venue as rv  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

SCRAPER = Path(__file__).resolve().parents[1]

PAGES = {
    "/": "<html><head><title>Fixture Gallery</title></head><body><main>"
         "<p>Fixture Gallery is a contemporary art gallery in Los Angeles founded in 2011 by Ada Lovelace. "
         "It shows emerging painters and runs a second space in Tokyo.</p>"
         "<a href='/represented'>Artists</a> <a href='/history?utm_source=x'>History</a> "
         "<a href='/archive/1'>Past</a> <a href='/photo.jpg'>img</a> <a href='mailto:a@b.c'>mail</a> "
         "<a href='https://other-site.example/x'>ext</a> <a href='/#top'>top</a></main></body></html>",
    "/represented": "<html><head><title>Represented artists</title></head><body><main>"
                    "<ul><li>Ada Painter</li><li>Bo Sculptor</li><li>Cy Estate (Estate)</li></ul>"
                    "<a href='/artist/ada'>Ada</a></main></body></html>",
    "/artist/ada": "<html><head><title>Ada Painter</title></head><body><main><p>Ada Painter (b. 1980) makes paintings. "
                   "Solo shows at the gallery in 2015 and 2019.</p></main></body></html>",
    "/history": "<html><head><title>History</title></head><body><main><p>Opened in 2011 on La Brea; moved to Chinatown in 2016. "
                "Hours: Wednesday to Saturday 11am to 6pm.</p></main></body></html>",
    "/archive/1": "<html><head><title>Past exhibitions</title></head><body><main>"
                  "<p>Ada Painter: Blue Rooms, March 3 - April 20, 2019</p>"
                  "<p>Group show: Summer Salon, July 1 - August 30, 2018</p>"
                  "<a href='/archive/2'>next</a></main></body></html>",
    "/archive/2": "<html><head><title>Past exhibitions 2</title></head><body><main>"
                  "<p>Bo Sculptor: Heavy, January 10 - February 28, 2017</p></main></body></html>",
    "/sitemap.xml": "<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                    "<url><loc>{root}/history</loc></url><url><loc>{root}/hidden</loc></url>"
                    "<url><loc>{root}/big.pdf</loc></url></urlset>",
    "/hidden": "<html><head><title>Hidden page</title></head><body><main><p>Only the sitemap links here. "
               "This page has enough words to count as real text for the crawler's threshold, so it is indexed.</p>"
               "</main></body></html>",
}


class _Handler(http.server.BaseHTTPRequestHandler):
    root = ""

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        body = PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        body = body.replace("{root}", self.root)
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml" if path.endswith(".xml") else "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # silence
        pass


class FixtureSite:
    def __enter__(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.srv.server_address[1]
        _Handler.root = f"http://127.0.0.1:{self.port}"
        self.t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.t.start()
        return self

    @property
    def root(self):
        return _Handler.root

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


class TempCaches(unittest.TestCase):
    """Redirect the evidence + crawl caches to throwaway dirs under scraper/
    (write_evidence stores paths relative to SCRAPER_DIR)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="test-research-", dir=SCRAPER / ".cache"))
        self._saved = (venues.EVIDENCE_DIR, venues.EVIDENCE_INDEX, crawl.CRAWL_DIR, rv.REPORTS_DIR,
                       venues.VENUES_DIR)
        venues.EVIDENCE_DIR = self.tmp / "evidence"
        venues.EVIDENCE_INDEX = venues.EVIDENCE_DIR / "index.jsonl"
        crawl.CRAWL_DIR = self.tmp / "crawl"
        rv.REPORTS_DIR = self.tmp / "reports"
        venues.VENUES_DIR = self.tmp / "venues"
        self._lock_saved = venues._lock_file
        venues._lock_file = lambda city: self.tmp / f".venues-{city}.lock"
        self._spend_saved = harness.SPEND_DIR
        harness.SPEND_DIR = self.tmp / "spend"

    def tearDown(self):
        (venues.EVIDENCE_DIR, venues.EVIDENCE_INDEX, crawl.CRAWL_DIR, rv.REPORTS_DIR,
         venues.VENUES_DIR) = self._saved
        venues._lock_file = self._lock_saved
        harness.SPEND_DIR = self._spend_saved
        shutil.rmtree(self.tmp, ignore_errors=True)


def _venue(root: str, vid: str = "fixture-gallery") -> dict:
    v = venues.empty_venue(vid, "Fixture Gallery")
    v.update({"website": root, "status": "active", "kind": "gallery", "neighborhood": "Chinatown/East LA"})
    return v


class CrawlerTests(TempCaches):
    def test_scope_caps_sitemap_and_resume(self):
        with FixtureSite() as site:
            v = _venue(site.root)
            idx = crawl.crawl_site("los-angeles", v, max_pages=50, fetcher=refresh.Fetcher(spacing=0),
                                   render=False, session="t")
            urls = {p.url for p in idx.pages}
            self.assertTrue(idx.sitemap)
            self.assertIn(f"{site.root}/hidden", urls, "sitemap-only page must be crawled")
            self.assertIn(f"{site.root}/archive/2", urls, "walk must follow in-domain links")
            self.assertIn(f"{site.root}/history", urls)
            self.assertNotIn(f"{site.root}/history?utm_source=x", urls, "tracking params stripped")
            self.assertFalse(any("other-site.example" in u for u in urls), "off-domain links skipped")
            self.assertFalse(any(u.endswith((".jpg", ".pdf")) for u in urls), "media skipped")
            self.assertFalse(any("#" in u for u in urls))
            ok = idx.ok_pages()
            self.assertEqual(len(ok), 7)
            self.assertTrue(all(p.evidence_path and (SCRAPER / p.evidence_path).exists() for p in ok))
            self.assertIn("founded in 2011", idx.text(idx.by_url()[site.root + "/"]))
            self.assertTrue(idx.path.exists() and crawl.CrawlIndex.meta_path("los-angeles", v["id"]).exists())
            # cap
            idx2 = crawl.crawl_site("los-angeles", {**v, "id": "capped"}, max_pages=3,
                                    fetcher=refresh.Fetcher(spacing=0), render=False)
            self.assertEqual(len(idx2.ok_pages()), 3)
            self.assertTrue(idx2.truncated)
            # resume: fresh pages are not refetched
            calls = []
            f = refresh.Fetcher(spacing=0)
            orig = f.get
            f.get = lambda url, **kw: (calls.append(url), orig(url, **kw))[1]
            idx3 = crawl.crawl_site("los-angeles", v, max_pages=50, fetcher=f, render=False)
            self.assertEqual(len(idx3.ok_pages()), 7)
            self.assertFalse(any(u.endswith("/represented") for u in calls), "fresh page refetched")
            # extra_urls (triage fetch_more) are fetched even when not linked
            idx4 = crawl.crawl_site("los-angeles", v, max_pages=50, fetcher=refresh.Fetcher(spacing=0),
                                    render=False, extra_urls=[f"{site.root}/artist/ada"])
            self.assertIn(f"{site.root}/artist/ada", {p.url for p in idx4.pages})
            lines = crawl.inventory_lines(idx4)
            self.assertTrue(lines[0].startswith("0 | "))
            self.assertEqual(len(lines), len(idx4.ok_pages()))

    def test_url_helpers(self):
        self.assertIsNone(crawl.normalize_url("mailto:x@y.z"))
        self.assertIsNone(crawl.normalize_url("javascript:void(0)"))
        self.assertEqual(crawl.normalize_url("/a/?utm_medium=m&page=2", "https://Ex.com/b/"),
                         "https://ex.com/a?page=2")
        self.assertEqual(crawl.site_root("regenprojects.com/exhibitions"), "https://regenprojects.com/")
        self.assertTrue(crawl.same_site("https://www.foo.com/x", "foo.com"))
        self.assertTrue(crawl.same_site("https://shop.foo.com/x", "foo.com"))
        self.assertFalse(crawl.same_site("https://foo.co/x", "foo.com"))
        self.assertTrue(crawl.skippable("https://a.b/c/d.PDF"))
        pages, nested = crawl._sitemap_locs(
            "<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><sitemap><loc>https://a/b.xml</loc></sitemap></sitemapindex>")
        self.assertEqual((pages, nested), ([], ["https://a/b.xml"]))


class _StubClient:
    """messages.create returns canned structured outputs keyed by the system prompt."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **req):
        self.calls.append(req)
        system = req["system"][0]["text"]
        user = req["messages"][0]["content"]
        if system == rv.TRIAGE_SYSTEM:
            lines = [ln for ln in user.splitlines() if " | " in ln and ln.split(" | ")[0].isdigit()]
            pages = []
            for ln in lines:
                i, url = ln.split(" | ")[0], ln.split(" | ")[1]
                lab = ("about" if url.endswith("/history") else "roster_index" if url.endswith("/represented")
                       else "exhibitions_archive" if "/archive/" in url else "artist_page" if "/artist/" in url
                       else "about" if url.rstrip("/").count("/") == 2 else None)
                if lab:
                    pages.append({"i": int(i), "label": lab})
            root = next(ln.split(" | ")[1] for ln in lines if ln.split(" | ")[1].rstrip("/").count("/") == 2)
            out = {"pages": pages, "fetch_more": [{"url": root.rstrip("/") + "/artist/ada", "label": "artist_page"},
                                                  {"url": "https://elsewhere.example/x", "label": "about"}],
                   "site_notes": "small squarespace-like site"}
        elif system == rv.PROFILE_SYSTEM:
            out = {"about_text": "Fixture Gallery opened in 2011 in Los Angeles. It shows emerging painters.",
                   "about_source_url": None, "founded_year": 2011, "founders": ["Ada Lovelace"], "directors": [],
                   "locations": [{"city": "Los Angeles", "address": None, "since": 2011, "current": True},
                                 {"city": "Tokyo", "address": None, "since": None, "current": True}],
                   "program_focus": ["emerging painters"], "hours_text": "Wednesday to Saturday 11am to 6pm",
                   "roster": [{"name": "Ada Painter", "status": "represented", "source_url": "u"},
                              {"name": "Bo Sculptor", "status": "represented", "source_url": "u"},
                              {"name": "Cy Estate", "status": "estate", "source_url": "u"}],
                   "fairs_self_reported": [], "memberships_self_reported": [], "press_self_reported": [], "notes": None}
        elif system == rv.EXHIBITIONS_SYSTEM:
            ex = []
            if "Heavy" in user:
                ex += [{"title": "Heavy", "artists": ["Bo Sculptor"], "start": "2017-01-10", "end": "2017-02-28",
                        "year": 2017, "kind": "solo", "source_url": "u"}]
            if "Blue Rooms" in user:
                ex += [{"title": "Blue Rooms", "artists": ["Ada Painter"], "start": "2019-03-03", "end": "2019-04-20",
                        "year": 2019, "kind": "solo", "source_url": "u"},
                       {"title": "Summer Salon", "artists": [], "start": None, "end": None, "year": 2018,
                        "kind": "group", "source_url": "u"},
                       {"title": "Blue Rooms", "artists": ["Ada Painter"], "start": None, "end": None,
                        "year": 2019, "kind": "solo", "source_url": "u2"}]
            out = {"exhibitions": ex, "complete": True}
        elif system == rv.CHECK_SYSTEM:
            out = {"sentences": [{"sentence": "Fixture Gallery opened in 2011 in Los Angeles.", "supported": True, "why": "history page"},
                                 {"sentence": "It shows emerging painters.", "supported": False, "why": "not stated"}]}
        elif system.startswith("You rate the standing"):
            out = {"notability": 4, "confidence": 0.4, "rationale": "small local gallery",
                   "flags": {"international_program": True, "museum_track_record": False, "artist_run": False, "blue_chip": False}}
        else:
            raise AssertionError("unexpected system prompt")
        usage = SimpleNamespace(input_tokens=500, output_tokens=100, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0, server_tool_use=None)
        return SimpleNamespace(stop_reason="end_turn", usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(out))])


def _args(**kw):
    base = dict(max_pages=50, max_fetch_more=10, gapfill=False, no_render=True, force=False,
                workers=1, budget=None, limit=None, venue_ids=None, dry_run=False)
    base.update(kw)
    return SimpleNamespace(**base)


class ResearchTests(TempCaches):
    def _seed_registry(self, root: str) -> dict:
        v = _venue(root)
        with venues.locked_registry("los-angeles") as reg:
            reg["venues"].append(v)
        return v

    def test_end_to_end_with_stub(self):
        with FixtureSite() as site:
            v = self._seed_registry(site.root)
            client = _StubClient()
            meter = rv.Meter("research-test")
            rep = rv.research_one(client, "los-angeles", v, meter, _args(), "s", fetcher=refresh.Fetcher(spacing=0),
                                  log=lambda *_: None)
            self.assertEqual(rep["founded_year"], 2011)
            self.assertEqual(rep["source_kind"], "official")
            self.assertEqual(len(rep["roster"]), 3)
            # merge: duplicate Blue Rooms collapsed, dates kept, chronological
            titles = [e["title"] for e in rep["exhibitions"]]
            self.assertEqual(titles, ["Heavy", "Summer Salon", "Blue Rooms"])
            self.assertEqual(rep["exhibitions"][2]["start"], "2019-03-03")
            # fetch_more: in-domain honoured, off-domain dropped, page labelled
            self.assertEqual(rep["triage"]["fetch_more"], [f"{site.root}/artist/ada"])
            self.assertEqual(rep["triage"]["labels"].get(f"{site.root}/artist/ada"), "artist_page")
            self.assertEqual(rep["triage"]["labels"].get(f"{site.root}/represented"), "roster_index")
            self.assertGreater(rep["cost_usd"], 0)
            # report on disk, registry patched
            p = rv.report_path("los-angeles", v["id"])
            self.assertTrue(p.exists())
            reg = venues.load_registry("los-angeles")
            rv2 = venues.index_by_id(reg)[v["id"]]
            self.assertEqual(rv2["facts"]["founded_year"], 2011)
            self.assertEqual(rv2["facts"]["roster_count"], 3)
            self.assertEqual(rv2["facts"]["exhibitions_total"], 3)
            self.assertEqual(rv2["facts"]["first_exhibition_year"], 2017)
            self.assertEqual(rv2["facts"]["locations_elsewhere"], ["Tokyo"])
            self.assertAlmostEqual(rv2["facts"]["solo_share"], 0.67)
            self.assertEqual(rv2["about"]["source_kind"], "official")
            self.assertTrue(rv2["about"]["text"].startswith("Fixture Gallery opened"))
            self.assertIsNotNone(rv2["research"]["ts"])
            self.assertEqual(rv2["hours"], ["Wednesday to Saturday 11am to 6pm"])
            self.assertIsNone(rv2["verification"]["status"], "research must not touch verification")
            # rerun rule
            picked, skipped = rv.select_venues("los-angeles", None, None, False)
            self.assertEqual(picked, [])
            self.assertTrue(any("researched" in why for _, why in skipped))
            picked, _ = rv.select_venues("los-angeles", None, None, True)
            self.assertEqual([x["id"] for x in picked], [v["id"]])
            # check: unsupported sentence nulls the registry blurb, report keeps it
            out, err, _ = rv.structured_call(client, rv.CHECK_SYSTEM, "x", rv.CHECK_SCHEMA, rv.CheckOut, meter)
            self.assertIsNone(err)
            self.assertEqual([s.supported for s in out.sentences], [True, False])

    def test_check_and_judge_paths(self):
        with FixtureSite() as site:
            v = self._seed_registry(site.root)
            client = _StubClient()
            meter = rv.Meter("research-test")
            rv.research_one(client, "los-angeles", v, meter, _args(), "s", fetcher=refresh.Fetcher(spacing=0),
                            log=lambda *_: None)
        # monkeypatch the client factory used by the commands
        import anthropic
        saved = anthropic.Anthropic
        anthropic.Anthropic = lambda **kw: client
        try:
            rv.cmd_check(SimpleNamespace(city="los-angeles", venue_ids=None, limit=None))
            rep = rv.load_report("los-angeles", v["id"])
            self.assertFalse(rep["claims_supported"])
            self.assertEqual(rep["unsupported_claims"], ["It shows emerging painters."])
            self.assertIsNotNone(rep["about_text"])
            self.assertIsNone(venues.index_by_id(venues.load_registry("los-angeles"))[v["id"]]["about"]["text"])
            saved_store = rv.store.city_dir
            rv.store.city_dir = lambda city: self.tmp / "curation" / city
            try:
                rv.cmd_judge(SimpleNamespace(city="los-angeles", venue_ids=None, limit=None, workers=1, force=False))
                rows = rv.load_venue_judge("los-angeles")
                self.assertEqual(rows[v["id"]]["notability"], 4)
                self.assertTrue(rows[v["id"]]["flags"]["international_program"])
                n = len(client.calls)
                rv.cmd_judge(SimpleNamespace(city="los-angeles", venue_ids=None, limit=None, workers=1, force=False))
                self.assertEqual(len(client.calls), n, "cached verdict must not re-call")
            finally:
                rv.store.city_dir = saved_store
        finally:
            anthropic.Anthropic = saved

    def test_merge_and_facts_helpers(self):
        merged = rv.merge_exhibitions([[{"title": "A", "artists": ["X"], "year": 2020, "kind": "solo", "source_url": "u"}],
                                       [{"title": "a", "artists": ["x"], "year": 2020, "start": "2020-05-01", "kind": "solo", "source_url": "u"}]])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start"], "2020-05-01")
        facts = rv.facts_from_report({"city": "los-angeles", "exhibitions": [], "roster": None, "locations": []})
        self.assertIsNone(facts["exhibitions_total"])
        self.assertIsNone(facts["roster_count"])


if __name__ == "__main__":
    unittest.main()
