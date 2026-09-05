"""quick_city: blended merge, the verified rule, footprint/zone helpers, registry
patch shape and the LA/Tokyo guard. No network.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import quick_city as qc  # noqa: E402
import rank_venues  # noqa: E402
import curation_store  # noqa: E402

CFG = {"center": {"latitude": 37.55, "longitude": 126.98}, "span": {"latitudeDelta": 0.3, "longitudeDelta": 0.3}}


class MergeRankings(unittest.TestCase):
    def test_same_venue_three_ways(self):
        claude = [{"rank": 1, "name": "Kukje Gallery", "website": "https://www.kukjegallery.com"},
                  {"rank": 2, "name": "Galerie Thaddaeus Ropac", "website": None},
                  {"rank": 3, "name": "PKM Gallery", "website": "https://pkmgallery.com"}]
        openai = [{"rank": 1, "name": "Thaddaeus Ropac Gallery", "website": "https://ropac.net"},
                  {"rank": 2, "name": "Kukje", "website": "http://kukjegallery.com/"},
                  {"rank": 3, "name": "Arario Gallery", "website": None}]
        out = qc.merge_rankings(claude, openai)
        names = [m["name"] for m in out]
        self.assertEqual(len(out), 4, names)
        kukje = next(m for m in out if m["name"] == "Kukje Gallery")       # domain match
        self.assertEqual((kukje["rank_claude"], kukje["rank_openai"]), (1, 2))
        ropac = next(m for m in out if "Ropac" in m["name"])               # distinctive-words match
        self.assertEqual((ropac["rank_claude"], ropac["rank_openai"]), (2, 1))
        self.assertEqual(ropac["website"], "https://ropac.net")            # filled from the other list
        self.assertEqual(kukje["blended"], 1.5)
        self.assertEqual(ropac["blended"], 1.5)
        pkm = next(m for m in out if m["name"] == "PKM Gallery")           # only on claude: absent = 4
        self.assertEqual(pkm["blended"], 3.5)
        self.assertFalse(pkm["both"])
        self.assertEqual([m["rank"] for m in out], [1, 2, 3, 4])
        # tie at 1.5 (both hold a #1) broken by name
        self.assertEqual(names[:2], ["Galerie Thaddaeus Ropac", "Kukje Gallery"])

    def test_duplicate_within_one_list_keeps_first_rank(self):
        claude = [{"rank": 1, "name": "X Gallery"}, {"rank": 5, "name": "X Gallery"}]
        out = qc.merge_rankings(claude, [])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["rank_claude"], 1)
        self.assertEqual(out[0]["blended"], 1.0)   # absent openai list -> penalty 1


class Footprint(unittest.TestCase):
    def test_in_span_with_slack(self):
        self.assertTrue(qc.in_span(37.55, 126.98, CFG))
        self.assertTrue(qc.in_span(37.55 + 0.25, 126.98, CFG))     # within the 0.5-span slack
        self.assertFalse(qc.in_span(37.55 + 0.35, 126.98, CFG))
        self.assertFalse(qc.in_span(None, 126.98, CFG))

    def test_nearest_zone(self):
        cents = {"Samcheong": {"lat": 37.58, "lng": 126.98}, "Hannam": {"lat": 37.535, "lng": 127.0}, "Empty": {}}
        zone, d = qc.nearest_zone(37.581, 126.981, cents)
        self.assertEqual(zone, "Samcheong")
        self.assertLess(d, 300)
        self.assertEqual(qc.nearest_zone(None, None, cents), (None, None))


OPEN = {"found": True, "matched": True, "status": "OPERATIONAL", "address": "54 Samcheong-ro, Seoul",
        "hours": ["Monday: Closed", "Tuesday: 10:00 AM – 6:00 PM"], "lat": 37.58, "lng": 126.98, "phone": "+82"}
LIVE = {"website": "https://kukjegallery.com", "website_live": True, "site_vouched": True, "closed_notice": False,
        "exhibitions_page": True, "exhibitions_url": "https://kukjegallery.com/exhibitions",
        "dated": {"latest_year": 2026, "stale": False}}


class QuickVerdict(unittest.TestCase):
    def v(self, pl=None, site=None, verdict="miss", fp=True):
        return qc.quick_verdict({"places": dict(OPEN, **(pl or {})), "site": dict(LIVE, **(site or {})),
                                 "verdict": verdict}, fp)

    def test_verified_needs_everything(self):
        self.assertEqual(self.v(), ("verified", []))

    def test_each_missing_piece(self):
        self.assertEqual(self.v(pl={"matched": False, "found": False})[1], ["places_missing"])
        self.assertEqual(self.v(pl={"matched": False, "found": True})[1], ["places_mismatch"])
        self.assertEqual(self.v(pl={"status": "CLOSED_TEMPORARILY"})[1], ["places_closed"])
        self.assertEqual(self.v(pl={"hours": []})[1], ["no_hours"])
        self.assertEqual(self.v(pl={"lat": None})[1], ["no_location"])
        self.assertEqual(self.v(site={"website_live": False})[1], ["site_dead"])
        self.assertEqual(self.v(site={"website": None})[1], ["no_website"])
        self.assertEqual(self.v(site={"site_vouched": False}, pl={"website": "https://other.example"})[1], ["site_not_vouched"])
        self.assertEqual(self.v(site={"closed_notice": True})[1], ["closed_notice"])
        self.assertEqual(self.v(site={"dated": {"latest_year": 2021, "stale": True}})[1], ["stale"])
        # no dated show at all: fatal only without a complete Places listing
        self.assertEqual(self.v(site={"dated": {"latest_year": None, "stale": True}})[1], [])
        self.assertEqual(self.v(site={"dated": {"latest_year": None, "stale": True}}, pl={"hours": []})[1], ["no_hours", "stale"])
        self.assertEqual(self.v(fp=False)[1], ["out_of_footprint"])
        self.assertEqual(self.v(pl={"matched": False, "found": False, "lat": None}, fp=False)[1], ["places_missing"])
        self.assertEqual(self.v(verdict="not_a_venue")[1], ["not_a_venue"])
        self.assertEqual(self.v(pl={"hours": None}, site={"website_live": False})[0], "unverified")

    def test_unvouched_js_site_is_carried_by_places(self):
        row = {"places": dict(OPEN, website="https://www.perrotin.com/"), "site": dict(LIVE, site_vouched=False, js_only=True),
               "hints": {"website": "https://perrotin.com"}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True), ("verified", []))
        self.assertIn("site_unvouched", row["notes"])
        row = {"places": dict(OPEN, website="https://other.example/"), "site": dict(LIVE, site_vouched=False),
               "hints": {"website": "https://perrotin.com"}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True)[1], ["site_not_vouched"])
        row = {"places": dict(OPEN, website=None), "site": dict(LIVE, site_vouched=False),   # Places lists no site: no contradiction
               "hints": {"website": "https://perrotin.com"}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True)[0], "verified")

    def test_blocked_site_is_carried_by_places(self):
        """429 / robots on the venue's own site: verified only when Places fully attests
        the venue and names the same site."""
        blocked = {"website_live": False, "site_vouched": None, "error": "http_429", "dated": None}
        row = {"places": dict(OPEN, website="https://www.hauserwirth.com/"), "site": dict(LIVE, **blocked),
               "hints": {"website": "https://hauserwirth.com"}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True), ("verified", []))
        self.assertTrue(row["notes"][0].startswith("site_blocked"))
        row = {"places": dict(OPEN, website="https://other.example"), "site": dict(LIVE, **blocked),
               "hints": {"website": "https://hauserwirth.com"}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True)[1], ["site_blocked"])
        row = {"places": dict(OPEN, hours=None, website="https://www.hauserwirth.com/"), "site": dict(LIVE, **blocked),
               "hints": {"website": "https://hauserwirth.com"}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True)[1], ["no_hours", "site_blocked"])
        row = {"places": OPEN, "site": dict(LIVE, website_live=False, error="http_404"), "hints": {}, "verdict": "miss"}
        self.assertEqual(qc.quick_verdict(row, True)[1], ["site_dead"])


class RegistryPatch(unittest.TestCase):
    def test_shape(self):
        entry = {"rank": 3, "name": "Kukje Gallery", "website": "https://kukjegallery.com", "kind": "gallery",
                 "address_hint": "54 Samcheong-ro", "rank_claude": 1, "rank_openai": 5, "blended": 3.0}
        row = {"places": OPEN, "site": LIVE, "geo": {"lat": 37.58, "lng": 126.98, "source": "places"}, "verdict": "miss"}
        existing = {"sources": {"seed": {"places": {"ts": 1}}}}
        p = qc.registry_patch(entry, row, "verified", [], "Samcheong", existing, "quick-validate-seoul-1", ts=99)
        self.assertEqual(p["status"], "active")
        self.assertEqual(p["hours"], OPEN["hours"])
        self.assertEqual(p["google"]["hours"], OPEN["hours"])
        self.assertEqual((p["latitude"], p["longitude"], p["coords_source"]), (37.58, 126.98, "places:quick"))
        self.assertEqual(p["exhibitions_url"], LIVE["exhibitions_url"])
        self.assertEqual(p["verification"]["status"], "verified")
        self.assertEqual(p["verification"]["ts"], 99)
        self.assertEqual(p["verification"]["checks"]["source"], "quick_city")
        self.assertFalse(p["verification"]["checks"]["moved_hint"])
        self.assertEqual(p["sources"]["seed"]["places"], {"ts": 1})        # sibling seed block kept
        self.assertEqual(p["sources"]["seed"]["quick"]["rank_claude"], 1)
        self.assertEqual((p["kind"], p["kind_source"]), ("gallery", "agent"))

    def test_unverified_keeps_only_what_is_known(self):
        entry = {"rank": 9, "name": "Ghost Space", "website": None, "kind": "project_space"}
        row = {"places": {"found": False, "matched": False}, "site": {"website": None}, "geo": None, "verdict": "not_found"}
        p = qc.registry_patch(entry, row, "unverified", ["places_missing", "no_website"], None, None, "s")
        self.assertEqual(p["status"], "unknown")
        self.assertNotIn("hours", p)
        self.assertNotIn("google", p)
        self.assertNotIn("latitude", p)
        self.assertEqual(p["verification"]["reasons"], ["places_missing", "no_website"])

    def test_moved_hint(self):
        entry = {"rank": 1, "name": "A", "address_hint": "12 Old St", "rank_claude": 1}
        row = {"places": dict(OPEN, address="99 New St, Seoul"), "site": LIVE, "geo": {"lat": 1, "lng": 2, "source": "places"}}
        p = qc.registry_patch(entry, row, "verified", [], "Z", None, "s")
        self.assertTrue(p["verification"]["checks"]["moved_hint"])


class Guard(unittest.TestCase):
    def test_never_write(self):
        with self.assertRaises(SystemExit):
            qc.guard("los-angeles")
        qc.guard("seattle")
        qc.guard("tokyo")   # LLM-ranked since 2026-09-04

    def test_augment_may_add_but_never_rank(self):
        qc._AUGMENT_OK.add("los-angeles")
        try:
            qc.guard("los-angeles")                       # additive writes allowed
            with self.assertRaises(SystemExit):
                qc.guard("los-angeles", ranking=True)     # the hand ranking stays untouched
        finally:
            qc._AUGMENT_OK.discard("los-angeles")

    def test_order_file_refuses_to_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = curation_store.CURATION_DIR
            curation_store.CURATION_DIR = Path(tmp)
            try:
                p = qc.write_order_file("seattle", [{"name": "A", "id": "a", "neighborhood": "Downtown", "note": "c#1 / o#2"}],
                                        {"1": 20, "2": 50}, "test")
                self.assertTrue(p.exists())
                self.assertEqual(rank_venues.load_order_file("seattle")["entries"][0]["rank"], 1)
                with self.assertRaises(SystemExit):
                    qc.write_order_file("seattle", [], {"1": 20, "2": 50}, "test")
                qc.write_order_file("seattle", [], {"1": 20, "2": 50}, "test", force=True)
            finally:
                curation_store.CURATION_DIR = saved


class Cities(unittest.TestCase):
    def test_blend_keeps_existing_keys_and_drafts_config(self):
        claude = [{"rank": 1, "name": "New York", "slug": "nyc", "country": "USA"},
                  {"rank": 2, "name": "Lisbon", "slug": "lisbon", "country": "Portugal",
                   "center": {"lat": 38.72, "lng": -9.14}, "map_span_deg": 0.2, "timezone": "Europe/Lisbon",
                   "neighborhoods": ["Chiado/Baixa", "Marvila, Beato"], "metro_tokens": ["Lisboa"], "why": "w"}]
        openai = [{"rank": 1, "name": "Lisbon", "slug": "lisbon-pt"}, {"rank": 2, "name": "New York City", "slug": "new-york"}]
        out = qc.blended_cities(claude, openai)
        slugs = [c["slug"] for c in out]
        self.assertEqual(sorted(slugs), ["lisbon", "new-york"])
        seoul = next(c for c in out if c["slug"] == "lisbon")
        self.assertEqual((seoul["rank_claude"], seoul["rank_openai"], seoul["blended"]), (2, 1, 1.5))
        self.assertFalse(seoul["existing"])
        ny = next(c for c in out if c["slug"] == "new-york")
        self.assertTrue(ny["existing"])
        self.assertEqual((ny["rank_claude"], ny["rank_openai"]), (1, 2))
        d = qc.draft_city_config(seoul)
        self.assertIn('"lisbon": {', d["cities_py"])
        self.assertIn('"Marvila/ Beato"', d["cities_py"])      # commas never survive into a label
        self.assertIn('"lisbon": "Europe/Lisbon"', d["city_tz"])
        self.assertIn('City(key: "lisbon"', d["models_swift"])


if __name__ == "__main__":
    unittest.main()
