"""Artists dataset: name normalization, dedup, collisions, merges, build + report."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import artists  # noqa: E402

REG = {"venues": [{"id": "gal-a", "name": "Gallery A", "verification": {"status": "verified"}},
                  {"id": "gal-b", "name": "Gallery B", "verification": {"status": None}}]}
TODAY = date(2026, 9, 3)


def report(vid, roster=(), exhibitions=()):
    return {"schema": 1, "city": "x", "venue_id": vid, "roster": list(roster),
            "exhibitions": list(exhibitions)}


class Names(unittest.TestCase):
    def test_diacritics_and_punctuation(self):
        self.assertEqual(artists.artist_id("Léon  Ferrari"), artists.artist_id("Leon Ferrari"))
        self.assertEqual(artists.artist_id("Judith F. Baca"), "judith-f-baca")
        self.assertEqual(artists.artist_id("Joel-Peter Witkin"), "joel-peter-witkin")

    def test_cjk_keeps_script(self):
        self.assertTrue(artists.artist_id("草間彌生").startswith("a-"))

    def test_split(self):
        self.assertEqual(artists.split_artists("Dan Mitchell, Richard Sides"), ["Dan Mitchell", "Richard Sides"])
        self.assertEqual(artists.split_artists("Leslie Foster and Whisper"), ["Leslie Foster", "Whisper"])
        self.assertEqual(artists.split_artists("Judith F. Baca (with SPARC)"), ["Judith F. Baca"])
        self.assertEqual(artists.split_artists("Various Artists"), [])
        self.assertEqual(artists.split_artists("Group Show"), [])
        self.assertEqual(artists.split_artists(None), [])

    def test_swapped(self):
        self.assertEqual(artists.swapped("Moriyama Daido"), "Daido Moriyama")
        self.assertIsNone(artists.swapped("Judith F. Baca"))


class Build(unittest.TestCase):
    def _build(self, reports=(), shows=(), signals=(), merges=None):
        return artists.build("x", TODAY, reports=list(reports), shows=list(shows),
                             signals=list(signals), merges=merges or {}, registry=REG)

    def test_name_order_merges(self):
        d = self._build(reports=[report("gal-a", roster=[{"name": "Daido Moriyama", "status": "represented",
                                                            "source_url": "u"}])],
                        shows=[{"slug": "s1", "artist": "Moriyama Daido", "venue_id": "gal-b",
                                "start_date": "2026-08-01", "end_date": "2026-09-20", "title": "T"}])
        self.assertEqual(len(d["artists"]), 1)
        a = d["artists"][0]
        self.assertIn("Moriyama Daido", a["aliases"])
        self.assertEqual({g["venue_id"]: g["relation"] for g in a["galleries"]},
                         {"gal-a": "represented", "gal-b": "showed"})
        self.assertEqual(a["galleries"][0]["venue_verification"], "verified")

    def test_buckets_and_history(self):
        shows = [{"slug": "cur", "artist": "A One", "venue_id": "gal-a", "start_date": "2026-08-01", "end_date": "2026-09-20"},
                 {"slug": "up", "artist": "A One", "venue_id": "gal-a", "start_date": "2026-10-01", "end_date": "2026-11-01"},
                 {"slug": "old", "artist": "A One", "venue_id": "gal-b", "start_date": "2025-01-01", "end_date": "2025-02-01"}]
        d = self._build(reports=[report("gal-a", exhibitions=[{"title": "Old show", "artists": ["A One"],
                                                                 "year": 2019, "kind": "solo", "source_url": "u"}])],
                        shows=shows)
        a = d["artists"][0]
        self.assertEqual([s["slug"] for s in a["shows_current"]], ["cur"])
        self.assertEqual([s["slug"] for s in a["shows_upcoming"]], ["up"])
        self.assertEqual({h["year"] for h in a["shows_history"]}, {2019, 2025})
        self.assertEqual(next(g for g in a["galleries"] if g["venue_id"] == "gal-a")["since"], 2019)

    def test_collision_goes_to_review(self):
        a = {"artist_id": "x-y", "name": "X Y", "aliases": ["X Y"], "_raw": ["X Y"], "_cities": ["la"],
             "galleries": [{"venue_id": "g1", "relation": "showed"}]}
        b = {"artist_id": "x-y", "name": "Y X", "aliases": ["Zed Y"], "_raw": ["Y X", "Zed Y"], "_cities": ["tokyo"],
             "galleries": [{"venue_id": "g2", "relation": "showed"}]}
        self.assertTrue(artists.looks_like_collision(a, b))
        b2 = {**b, "_cities": ["la"]}
        self.assertFalse(artists.looks_like_collision(a, b2))

    def test_merges_override(self):
        shows = [{"slug": "s1", "artist": "Bob Ross", "venue_id": "gal-a", "start_date": "2026-08-01", "end_date": "2026-09-20"},
                 {"slug": "s2", "artist": "Robert Ross", "venue_id": "gal-b", "start_date": "2026-08-01", "end_date": "2026-09-20"}]
        d = self._build(shows=shows)
        self.assertEqual(len(d["artists"]), 2)
        d = self._build(shows=shows, merges={"merge": [["bob-ross", "robert-ross"]]})
        self.assertEqual(len(d["artists"]), 1)
        self.assertEqual(len(d["artists"][0]["galleries"]), 2)
        self.assertIn("Robert Ross", d["artists"][0]["aliases"])

    def test_signals_add_activity_not_artists(self):
        sig = [{"kind": "award", "show_ref": {"artist": "A One"}, "source": {"id": "s"}, "source_url": "u", "snippet": "won"},
               {"kind": "award", "show_ref": {"artist": "Nobody Here"}, "source": {"id": "s"}, "source_url": "u"}]
        d = self._build(shows=[{"slug": "s", "artist": "A One", "venue_id": "gal-a", "start_date": "2026-08-01", "end_date": "2026-09-20"}],
                        signals=sig)
        self.assertEqual(len(d["artists"]), 1)
        self.assertEqual(d["artists"][0]["activity"][0]["kind"], "award")

    def test_report_renders(self):
        d = self._build(shows=[{"slug": "s", "artist": "A One", "venue_id": "gal-a", "title": "Now",
                                "start_date": "2026-08-01", "end_date": "2026-09-20"}])
        md, js = artists.render_report(d, {v["id"]: v for v in REG["venues"]})
        self.assertIn("| A One | Gallery A (showed) | Now @ Gallery A", md)
        self.assertEqual(js["artists"][0]["galleries"][0]["venue"], "Gallery A")


if __name__ == "__main__":
    unittest.main()
