"""validate_venues (stage G2): status derivation, cached-nearby matching and the
per-venue check assembly with a stubbed fetcher. No network.

    PYTHONPATH=scraper/tests:scraper scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import seed_venues as sv  # noqa: E402
import validate_venues as vv  # noqa: E402
import venues  # noqa: E402


def checks(**kw):
    base = {"website": "https://x.example", "website_live": True, "site_vouched": True,
            "closed_notice": False, "places_status": "OPERATIONAL", "places_checked": True,
            "address_match": True}
    base.update(kw)
    return base


class DeriveStatus(unittest.TestCase):
    def test_operational_live_is_verified(self):
        self.assertEqual(vv.derive_status(checks())[0], "verified")

    def test_closed_places_is_flagged(self):
        for st in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            self.assertEqual(vv.derive_status(checks(places_status=st))[0], "flagged")

    def test_closed_temporarily_overridden_by_live_vouched_site(self):
        st, reasons = vv.derive_status({"places_status": "CLOSED_TEMPORARILY", "website": "https://x",
                                        "website_live": True, "site_vouched": True,
                                        "exhibitions_page": True, "address_match": True})
        self.assertEqual(st, "verified")
        self.assertIn("places_closed_temporarily_overridden", reasons)
        st, _ = vv.derive_status({"places_status": "CLOSED_TEMPORARILY", "website": "https://x",
                                  "website_live": True, "site_vouched": True,
                                  "exhibitions_page": False, "address_match": True})
        self.assertEqual(st, "flagged")

    def test_closed_notice_is_flagged(self):
        self.assertEqual(vv.derive_status(checks(closed_notice=True))[0], "flagged")

    def test_no_places_but_vouched_is_verified(self):
        st, why = vv.derive_status(checks(places_status=None, address_match=None))
        self.assertEqual(st, "verified")
        self.assertEqual(why, ["site_vouched"])

    def test_no_places_not_vouched_is_unverified(self):
        st, why = vv.derive_status(checks(places_status=None, site_vouched=False, address_match=None))
        self.assertEqual(st, "unverified")
        self.assertIn("no_places_row", why)

    def test_dead_site_is_unverified(self):
        st, why = vv.derive_status(checks(website_live=False, site_vouched=None))
        self.assertEqual(st, "unverified")
        self.assertIn("site_dead", why)

    def test_not_found_and_dead_is_flagged(self):
        st, _ = vv.derive_status(checks(places_status="NOT_FOUND", website_live=False,
                                        address_match=None))
        self.assertEqual(st, "flagged")

    def test_no_website_is_unverified(self):
        st, why = vv.derive_status(checks(website=None, website_live=None, site_vouched=None))
        self.assertEqual(st, "unverified")
        self.assertEqual(why, ["no_website"])

    def test_address_mismatch_blocks_verified(self):
        st, why = vv.derive_status(checks(address_match=False))
        self.assertEqual(st, "unverified")
        self.assertEqual(why, ["address_mismatch"])


class AddressMatch(unittest.TestCase):
    def test_digits(self):
        self.assertTrue(vv.address_match("6150 Wilshire Blvd", "6150 Wilshire Blvd, Los Angeles, CA 90048"))
        self.assertFalse(vv.address_match("427 N Camden Dr", "9953 S Santa Monica Blvd"))
        self.assertIsNone(vv.address_match("near Higashi-Ginza Station", "5-17-1 Roppongi"))
        self.assertIsNone(vv.address_match("1-7-2 Kyobashi", None))
        self.assertTrue(vv.address_match("１-７-２ Kyobashi", "1-7-2 Kyobashi, Chuo City"))
        # postal codes, floors and suites are not street digits
        self.assertTrue(vv.address_match("6F Toda Building, 1-7-1 Kyobashi, Chuo-ku, Tokyo 104-8388",
                                         "TODA BUILDING 6階, 1-chōme-7−１ Kyōbashi, Chuo City, 104-0031"))
        self.assertTrue(vv.address_match("Suite 210, 5900 Wilshire Blvd", "5900 Wilshire Blvd, Los Angeles, CA 90036"))
        self.assertIsNone(vv.address_match("7F Ginza Mitsukoshi department store", "4-chōme-6-16 Ginza"))


class ClosedNotice(unittest.TestCase):
    def test_commentary_about_galleries_is_not_a_notice(self):
        t = "Many galleries closed their doors in 2025. We will invite Tomio Koyama to discuss."
        self.assertFalse(vv.closed_notice(t, "Kaikai Kiki Gallery"))

    def test_first_person_or_named_closure_is(self):
        self.assertTrue(vv.closed_notice("After ten years, the gallery has closed permanently.", "Old Space"))
        self.assertTrue(vv.closed_notice("News. Old Space has closed its doors. Thank you.", "Old Space"))
        self.assertFalse(vv.closed_notice("The nearby Other Place has closed.", "Old Space"))


def _legacy(name, pid, lat, lng, status="OPERATIONAL", rating=4.5, n=12):
    return {"name": name, "place_id": pid, "business_status": status, "rating": rating,
            "user_ratings_total": n, "vicinity": "1 Test St",
            "geometry": {"location": {"lat": lat, "lng": lng}}}


class NearbyIndex(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = sv.PLACES_CACHE_DIR
        sv.PLACES_CACHE_DIR = Path(self.tmp.name)
        d = sv.PLACES_CACHE_DIR / "testcity"
        d.mkdir(parents=True)
        pages = [{"results": [_legacy("Bel Ami", "p1", 34.06, -118.24),
                              _legacy("Bel Ami", "p2", 34.50, -118.90, status="CLOSED_TEMPORARILY"),
                              _legacy("Unique Space", "p3", 34.07, -118.25)]}]
        (d / "a.json").write_text(json.dumps({"ts": 1, "query": {"op": "nearby"}, "data": pages}))
        (d / "areas.json").write_text("{}")   # store file without a query: ignored

    def tearDown(self):
        sv.PLACES_CACHE_DIR = self.old
        self.tmp.cleanup()

    def test_match_by_seed_place_id(self):
        idx = vv.load_nearby_index("testcity")
        self.assertEqual(len(idx["by_place_id"]), 3)
        v = {"name": "Something Else", "aliases": [], "sources": {"seed": {"places": {"place_id": "p2"}}}}
        self.assertEqual(vv.match_nearby(v, idx)["status"], "CLOSED_TEMPORARILY")

    def test_match_by_name_and_proximity(self):
        idx = vv.load_nearby_index("testcity")
        v = {"name": "Bel Ami", "aliases": [], "latitude": 34.0601, "longitude": -118.2401}
        self.assertEqual(vv.match_nearby(v, idx)["place_id"], "p1")
        far = {"name": "Bel Ami", "aliases": [], "latitude": 33.0, "longitude": -118.0}
        self.assertIsNone(vv.match_nearby(far, idx))
        ambiguous = {"name": "Bel Ami", "aliases": []}
        self.assertIsNone(vv.match_nearby(ambiguous, idx))
        unique = {"name": "unique space", "aliases": []}
        self.assertEqual(vv.match_nearby(unique, idx)["place_id"], "p3")


class StubFetcher:
    def __init__(self, pages: dict):
        self.pages = pages

    def get(self, url, etag=None, last_modified=None):
        page = self.pages.get(url)
        if page is None:
            return {"status": 404, "html": None, "error": "http_404", "final_url": url,
                    "etag": None, "last_modified": None}
        return {"status": 200, "html": page, "error": None, "final_url": url,
                "etag": None, "last_modified": None}


GALLERY_HTML = "<html><head><title>Bel Ami</title></head><body><main><p>Bel Ami is a gallery in " \
               "Chinatown. Current exhibitions open Tuesday–Saturday.</p></main></body></html>"
CLOSED_HTML = "<html><head><title>Old Space</title></head><body><main><p>The gallery has closed " \
              "permanently after ten years of exhibitions.</p></main></body></html>"


class ValidateOne(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_ev = (venues.EVIDENCE_DIR, venues.EVIDENCE_INDEX)
        venues.EVIDENCE_DIR = Path(self.tmp.name) / "evidence"
        venues.EVIDENCE_INDEX = venues.EVIDENCE_DIR / "index.jsonl"
        self.old_cache = sv.PLACES_CACHE_DIR
        sv.PLACES_CACHE_DIR = Path(self.tmp.name) / "places"

    def tearDown(self):
        venues.EVIDENCE_DIR, venues.EVIDENCE_INDEX = self.old_ev
        sv.PLACES_CACHE_DIR = self.old_cache
        self.tmp.cleanup()

    def ctx(self, pages):
        return vv.Ctx("los-angeles", StubFetcher(pages), key=None, max_places_requests=0,
                      network=True, discover=False, render=False, session="t")

    def test_registry_google_plus_live_site(self):
        v = {**venues.empty_venue("bel-ami", "Bel Ami"), "website": "https://belami.example",
             "address": "709 N Hill St", "google": {"status": "OPERATIONAL",
                                                    "address": "709 N Hill St, Los Angeles"}}
        r = vv.validate_one(v, self.ctx({"https://belami.example": GALLERY_HTML}))
        self.assertEqual(r["status"], "verified")
        self.assertTrue(r["checks"]["site_vouched"])
        self.assertTrue(r["checks"]["address_match"])
        self.assertEqual(r["checks"]["places_source"], "registry")

    def test_closed_notice_flags(self):
        v = {**venues.empty_venue("old", "Old Space"), "website": "https://old.example"}
        r = vv.validate_one(v, self.ctx({"https://old.example": CLOSED_HTML}))
        self.assertEqual(r["status"], "flagged")
        self.assertEqual(r["reasons"], ["closed_notice"])

    def test_dead_site_no_places_budget(self):
        v = {**venues.empty_venue("gone", "Gone Gallery"), "website": "https://gone.example",
             "address": "1 Nowhere Rd"}
        r = vv.validate_one(v, self.ctx({}))
        self.assertEqual(r["status"], "unverified")
        self.assertIn("site_dead", r["reasons"])
        self.assertFalse(r["checks"]["places_checked"])   # no key, no budget: no lookup

    def test_select_targets_skips_parked(self):
        parked = {**venues.empty_venue("frame-shop", "Frame Shop"),
                  "sources": {"seed": {"places": {"plausible": False}}}}
        active = {**venues.empty_venue("bel-ami", "Bel Ami"), "status": "active"}
        closed = {**venues.empty_venue("x", "X"), "status": "closed"}
        reg = {"venues": [parked, active, closed]}
        self.assertEqual([v["id"] for v in vv.select_targets(reg, None, None)], ["bel-ami"])
        self.assertEqual([v["id"] for v in vv.select_targets(reg, ["frame-shop"], None)], ["frame-shop"])


if __name__ == "__main__":
    unittest.main()
