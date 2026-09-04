"""verify_order: verdict rules and name matching, no network."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_order as vo  # noqa: E402

OPEN = {"found": True, "name": "Tanya Leighton Gallery", "status": "OPERATIONAL", "address": "4654 W Washington Blvd, Los Angeles, CA 90016, USA", "lat": 34.04, "lng": -118.34, "metro": True, "matched": True}
LIVE = {"website": "https://x", "website_live": True, "site_vouched": True, "closed_notice": False, "exhibitions_page": True}
COV = {"gpla": None, "carla": None, "sweep": None, "enumeration_logs": [], "circle": {"dist_m": 100, "radius_m": 600, "type": "art_gallery", "inside": True}}


class NameMatch(unittest.TestCase):
    def test_shared_word_or_squashed_letters(self):
        self.assertTrue(vo.name_matches("Tanya Leighton", "Tanya Leighton Gallery"))
        self.assertTrue(vo.name_matches("ArtPic", "Art Pic"))
        self.assertFalse(vo.name_matches("Lowell Ryan Projects", "Royale Projects"))   # 'projects' is generic
        self.assertFalse(vo.name_matches("Kylin Gallery", None))

    def test_metro(self):
        self.assertTrue(vo.in_metro({"address": "1 Main St, Los Angeles, CA 90013, USA"}, "los-angeles"))
        self.assertFalse(vo.in_metro({"address": "165 E Broadway, New York, NY 10002, USA"}, "los-angeles"))
        self.assertIsNone(vo.in_metro({}, "los-angeles"))


class Verdicts(unittest.TestCase):
    def v(self, entry, pl=OPEN, site=LIVE, zone="Culver City/West Adams", cov=COV, near=()):
        return vo.verdict_for(dict({"name": "Tanya Leighton", "note": None, "neighborhood": "West Adams"}, **entry), list(near), pl, site, zone, cov)

    def test_miss_names_the_nets(self):
        verdict, why = self.v({})
        self.assertEqual(verdict, "miss")
        self.assertTrue(any("Gallery Platform LA" in w for w in why) and any("Carla" in w for w in why))
        self.assertTrue(any("swept Places circle" in w for w in why))
        verdict, why = self.v({}, cov=dict(COV, gpla="Tanya Leighton"))
        self.assertTrue(any(w.startswith("IS on Gallery Platform LA") for w in why))
        verdict, why = self.v({}, cov=dict(COV, sweep={"name": "Liz's", "status": "OPERATIONAL", "skip": "name:antique", "query": None}))
        self.assertTrue(any("dropped: name:antique" in w for w in why))

    def test_closed_and_closed_but_live(self):
        closed = dict(OPEN, status="CLOSED_PERMANENTLY")
        self.assertEqual(self.v({}, pl=closed, site={"website": "https://x", "website_live": False})[0], "closed")
        self.assertEqual(self.v({}, pl=closed)[0], "unverified")                       # live site + exhibitions page: check by hand
        self.assertEqual(self.v({}, site=dict(LIVE, closed_notice=True))[0], "closed")

    def test_not_found_out_of_metro_and_footprint(self):
        verdict, why = self.v({}, pl={"found": True, "matched": False, "metro": False, "address_seen": "NY"}, site={"website": None})
        self.assertEqual(verdict, "not_found"); self.assertIn("outside the metro", why[-1])
        self.assertEqual(self.v({}, pl={"found": False}, site={"website": None})[0], "not_found")
        verdict, why = self.v({}, pl={"found": True, "matched": False, "mismatch": "Royale Projects", "metro": True}, site={"website": None})
        self.assertEqual(verdict, "not_found"); self.assertIn("different business: 'Royale Projects'", why[-1])
        # a hinted website that is live and vouched confirms the venue even when Places knows nothing
        verdict, why = self.v({"address": "1 Main St"}, pl={"found": False}, site=LIVE, zone="Hollywood")
        self.assertEqual(verdict, "miss")
        self.assertEqual(self.v({"_geo": {"lat": 1, "lng": 2}}, zone=None)[0], "out_of_footprint")
        self.assertEqual(self.v({"address": "1 Main St"}, zone=None)[0], "miss")     # no coordinates at all: not a footprint call

    def test_not_a_venue_from_name_or_note(self):
        self.assertEqual(self.v({"name": "Los Angeles Modern Auctions"})[0], "not_a_venue")
        self.assertEqual(self.v({"name": "Skidmore Contemporary Art", "note": "private dealing"})[0], "not_a_venue")

    def test_unverified_when_not_confirmable(self):
        verdict, why = self.v({}, pl=dict(OPEN, status="CLOSED_TEMPORARILY"), site=dict(LIVE, site_vouched=False))
        self.assertEqual(verdict, "unverified")
        self.assertIn("Places: closed_temporarily", why)


if __name__ == "__main__":
    unittest.main()
