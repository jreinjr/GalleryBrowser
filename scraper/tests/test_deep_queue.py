"""run_deep stage-2 scheduling: the breadth-first ZoneQueue, pick_batch caps,
and the targeted zone_todo filters (--venue-ids / --force-due). Stdlib unittest,
no network, no subprocess.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import run_deep  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

TODAY = date(2026, 9, 1)


class ZoneQueueTests(unittest.TestCase):
    def test_claim_breadth_first_then_most_due_then_order(self):
        q = run_deep.ZoneQueue(["A", "B", "C"], max_sessions=10,
                               due_counts={"A": 10, "B": 3, "C": 5})
        # first three claims: every zone before any repeats, most-due first
        self.assertEqual(q.claim()["zone"], "A")
        self.assertEqual(q.claim()["zone"], "C")
        self.assertEqual(q.claim()["zone"], "B")
        self.assertIsNone(q.claim())          # all in flight
        self.assertTrue(q.any_active())
        q.release("A", progress=1, todo_empty=False)   # A back to 1 session
        self.assertEqual(q.claim()["zone"], "A")        # only free one
        q.release("B", progress=1, todo_empty=False)
        q.release("C", progress=1, todo_empty=False)
        # A=1, B=1, C=1 sessions; tie broken by due desc -> C (5) before B (3)
        self.assertEqual(q.claim()["zone"], "C")

    def test_exhaustion_rules(self):
        q = run_deep.ZoneQueue(["Z"], max_sessions=10)
        q.claim()
        q.release("Z", progress=0, todo_empty=False)
        q.claim()
        s = q.release("Z", progress=0, todo_empty=False)
        self.assertTrue(s["exhausted"]); self.assertEqual(s["why"], "zero_progress")
        self.assertIsNone(q.claim())

        q2 = run_deep.ZoneQueue(["Z"], max_sessions=10)
        q2.claim()
        s = q2.release("Z", progress=0, todo_empty=True)
        self.assertTrue(s["exhausted"]); self.assertEqual(s["why"], "todo_empty")

        q3 = run_deep.ZoneQueue(["Z"], max_sessions=1)
        q3.claim()
        s = q3.release("Z", progress=5, todo_empty=False)
        self.assertTrue(s["exhausted"]); self.assertEqual(s["why"], "max_sessions")

    def test_one_inflight_session_per_zone(self):
        q = run_deep.ZoneQueue(["Z"], max_sessions=10)
        self.assertEqual(q.claim()["zone"], "Z")
        self.assertIsNone(q.claim())          # already active
        self.assertTrue(q.any_active())
        q.release("Z", progress=1, todo_empty=False)
        self.assertEqual(q.claim()["zone"], "Z")


class PickBatchTests(unittest.TestCase):
    def v(self, vid, kind="gallery", reasons=("never_scraped",)):
        return {"id": vid, "name": vid, "kind": kind, "_reasons": list(reasons)}

    def test_caps_places_only_and_museums(self):
        todo = [self.v("n1"), self.v("n2"),
                self.v("p1", reasons=["places_only"]),
                self.v("p2", reasons=["places_only"]),
                self.v("p3", reasons=["places_only"])]
        batch = run_deep.pick_batch(todo, n=4, max_places_only=1)
        self.assertEqual([v["id"] for v in batch], ["n1", "n2", "p1"])

    def test_museum_cap_still_holds(self):
        todo = [self.v("m1", kind="museum"), self.v("m2", kind="museum"),
                self.v("m3", kind="museum"), self.v("g1")]
        batch = run_deep.pick_batch(todo, n=8)
        self.assertEqual([v["id"] for v in batch], ["m1", "m2", "g1"])


class ZoneTodoTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._vd, self._cd = venues.VENUES_DIR, tools.CONTENT_DIR
        venues.VENUES_DIR = self.tmp / "venues"
        tools.CONTENT_DIR = self.tmp

    def tearDown(self):
        venues.VENUES_DIR, tools.CONTENT_DIR = self._vd, self._cd

    def venue(self, vid, next_check=None, status="active", scraped=True):
        v = venues.empty_venue(vid, vid.title())
        v.update({"status": status, "kind": "gallery", "neighborhood": "Hollywood",
                  "last_scraped": int(time.mktime(TODAY.timetuple())) - 3 * 86400 if scraped else None,
                  "next_check": next_check, "last_known_shows": []})
        return v

    def write(self, *vs):
        with venues.locked_registry("los-angeles") as reg:
            reg["venues"] = list(vs)

    def test_venue_ids_restrict_and_force_due(self):
        far = self.venue("far", next_check=(TODAY + timedelta(days=30)).isoformat())  # not due
        due = self.venue("duecheck", next_check=TODAY.isoformat())                    # due
        self.write(far, due)
        # plain restriction keeps only the intersection of due and the id set
        todo = run_deep.zone_todo("los-angeles", "Hollywood", venue_ids={"duecheck", "far"})
        self.assertEqual([v["id"] for v in todo], ["duecheck"])
        # a non-due id yields nothing without force
        self.assertEqual(run_deep.zone_todo("los-angeles", "Hollywood", venue_ids={"far"}), [])
        # force_due includes it regardless, at priority 100
        forced = run_deep.zone_todo("los-angeles", "Hollywood", venue_ids={"far"}, force_due=True)
        self.assertEqual([v["id"] for v in forced], ["far"])
        self.assertEqual(forced[0]["_priority"], 100)
        self.assertEqual(forced[0]["_reasons"], ["forced"])

    def test_force_due_ignores_status(self):
        closed = self.venue("dead", status="closed", next_check=None)
        self.write(closed)
        self.assertEqual(run_deep.zone_todo("los-angeles", "Hollywood", venue_ids={"dead"}), [])
        forced = run_deep.zone_todo("los-angeles", "Hollywood", venue_ids={"dead"}, force_due=True)
        self.assertEqual([v["id"] for v in forced], ["dead"])


class ParseVenueIdsTests(unittest.TestCase):
    def test_names_ids_and_file(self):
        self.assertEqual(run_deep.parse_venue_ids("Hammer Museum, 1301-pe"),
                         {"hammer-museum", "1301-pe"})
        self.assertIsNone(run_deep.parse_venue_ids(None))
        d = Path(tempfile.mkdtemp())
        f = d / "ids.txt"
        f.write_text("1301-pe  # a note\nHammer Museum,karma\n")
        self.assertEqual(run_deep.parse_venue_ids(f"@{f}"),
                         {"1301-pe", "hammer-museum", "karma"})


if __name__ == "__main__":
    unittest.main()


class GalleriesFirstTests(unittest.TestCase):
    """run_deep --galleries-first: ranked-file loader + roster on the TODO line."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._cd = tools.CONTENT_DIR
        tools.CONTENT_DIR = self.tmp

    def tearDown(self):
        tools.CONTENT_DIR = self._cd

    def test_missing_ranked_file_exits_with_instructions(self):
        with self.assertRaises(SystemExit) as cm:
            run_deep.load_ranked("tokyo")
        self.assertIn("rank_venues.py score --city tokyo", str(cm.exception))

    def test_roster_from_report_on_todo_line(self):
        rep = self.tmp / "venues" / "reports" / "tokyo" / "taka-ishii.json"
        rep.parent.mkdir(parents=True)
        rep.write_text(json.dumps({"roster": [{"name": f"Artist {i}", "status": "represented"}
                                              for i in range(12)]
                                   + [{"name": "Past Person", "status": "exhibited"}]}))
        names = run_deep.venue_roster("tokyo", "taka-ishii")
        self.assertEqual(len(names), 8)
        self.assertNotIn("Past Person", names)
        self.assertEqual(run_deep.venue_roster("tokyo", "nope"), [])
        import run_scrape
        msg = run_scrape.format_todo_message("Tokyo", "Roppongi", [
            {"name": "Taka Ishii Gallery", "website": "https://takaishiigallery.com",
             "represents": names[:2]}])
        self.assertIn("represents: Artist 0, Artist 1", msg)


class AttemptedOncePerRun(unittest.TestCase):
    """A venue batched in this run is not batched again, however due it looks."""

    def test_batch_excludes_already_attempted(self):
        todo = [{"id": "mizuma", "name": "Mizuma"}, {"id": "maki", "name": "MAKI"}]
        attempted = {"mizuma"}
        left = [v for v in todo if v["id"] not in attempted]
        self.assertEqual([v["id"] for v in run_deep.pick_batch(left, 6)], ["maki"])
        attempted.update(v["id"] for v in left)
        self.assertEqual([v for v in todo if v["id"] not in attempted], [])
