"""Multiple shows per venue: same-show dedup in save_show, venue scheduling
in the registry, TODO batching and the first-message format (stdlib unittest).

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

import tools  # noqa: E402
import venues  # noqa: E402
import run_deep  # noqa: E402
import run_scrape  # noqa: E402
from test_dates import TempContent, show  # noqa: E402

TODAY = date(2026, 9, 1)


class NormTitleTests(unittest.TestCase):
    def test_norm_title(self):
        self.assertEqual(tools._norm_title("The Cars of Los Angeles"), "cars of los angeles")
        self.assertEqual(tools._norm_title("Fragmented Thought!"), tools._norm_title("fragmented thought"))
        self.assertEqual(tools._norm_title(None), "")


class SaveShowMultiTests(TempContent):
    def save(self, **kw):
        rec = show(**kw)
        rec["images"] = [self.image(rec["slug"])]
        return json.loads(tools.save_show(rec, "los-angeles", ["Hollywood"]))

    def test_second_show_at_same_venue_saves(self):
        self.save(slug="karma-a", venue="Karma", title="Fragmented Thought")
        out = self.save(slug="karma-b", venue="Karma", title="Dictation from the Other Side")
        self.assertEqual(out["result"], "saved")
        self.assertEqual(out["shows_saved_for_city"], 2)

    def test_same_show_new_slug_rejected(self):
        self.save(slug="karma-a", venue="Karma", title="Fragmented Thought")
        with self.assertRaises(ValueError) as cm:
            self.save(slug="karma-dupe", venue="Karma Gallery", title="Fragmented Thought")
        self.assertIn("karma-a", str(cm.exception))

    def test_same_slug_upserts(self):
        self.save(slug="karma-a", venue="Karma", title="Fragmented Thought")
        out = self.save(slug="karma-a", venue="Karma", title="Fragmented Thought")
        self.assertEqual(out["result"], "updated")
        self.assertEqual(out["shows_saved_for_city"], 1)


class RegistryScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._vd, self._cd = venues.VENUES_DIR, tools.CONTENT_DIR
        venues.VENUES_DIR = self.tmp / "venues"
        tools.CONTENT_DIR = self.tmp

    def tearDown(self):
        venues.VENUES_DIR, tools.CONTENT_DIR = self._vd, self._cd

    def venue(self, vid, shows=(), next_check=None, status="active", kind="gallery", scraped=True):
        v = venues.empty_venue(vid, vid.title())
        v.update({"status": status, "kind": kind, "neighborhood": "Hollywood",
                  "last_scraped": int(time.mktime(TODAY.timetuple())) - 3 * 86400 if scraped else None,
                  "next_check": next_check,
                  "last_known_shows": [{"slug": f"{vid}-{i}", "title": f"T{i}", "start": "2026-08-01",
                                        "end": end, "placement": "published"} for i, end in enumerate(shows)]})
        return v

    def write(self, *vs):
        with venues.locked_registry("los-angeles") as reg:
            reg["venues"] = list(vs)

    def test_active_show_is_earliest_ending(self):
        v = self.venue("multi", shows=("2026-10-15", "2026-09-10", "2026-08-01"))
        self.assertEqual(venues.active_show(v, TODAY)["end"], "2026-09-10")
        self.assertEqual([s["end"] for s in venues.active_shows(v, TODAY)], ["2026-09-10", "2026-10-15"])

    def test_venue_with_live_show_is_due_when_scheduled_or_ending_soon(self):
        far = self.venue("far", shows=("2026-10-15",), next_check=(TODAY + timedelta(days=10)).isoformat())
        due_check = self.venue("duecheck", shows=("2026-10-15",), next_check=TODAY.isoformat())
        ending = self.venue("ending", shows=("2026-09-04",), next_check=(TODAY + timedelta(days=10)).isoformat())
        ending["last_scraped"] = int(time.mktime((TODAY - timedelta(days=10)).timetuple()))   # not seen this week
        checked = self.venue("checked", shows=("2026-09-04",), next_check=(TODAY + timedelta(days=10)).isoformat())
        checked["last_scraped"] = int(time.mktime((TODAY - timedelta(days=1)).timetuple()))  # looked yesterday
        cand = self.venue("cand", status="candidate", scraped=False)
        self.write(far, due_check, ending, checked, cand)
        due = {r["id"]: r for r in venues.due_venues("los-angeles", "Hollywood", TODAY)}
        self.assertNotIn("far", due)
        self.assertIn("past_next_check", due["duecheck"]["_reasons"])
        self.assertIn("show_ending_soon", due["ending"]["_reasons"])
        self.assertNotIn("checked", due)
        self.assertIn("candidate", due["cand"]["_reasons"])
        self.assertGreaterEqual(due["cand"]["_priority"], 60)   # never_scraped 40 + candidate 20

    def test_compute_next_check_uses_earliest_end(self):
        v = self.venue("multi", shows=("2026-10-15", "2026-09-10"))
        self.assertEqual(venues.compute_next_check(v, TODAY), date(2026, 9, 8))

    def test_backfill_todo_and_museum_batching(self):
        one = self.venue("one", shows=("2026-10-15",))
        two = self.venue("two", shows=("2026-10-15", "2026-11-01"))
        done = self.venue("done", shows=("2026-10-15",))
        done["last_outcome"], done["last_scraped"] = "skipped:unchanged", int(time.time())
        m1 = self.venue("m1", shows=("2026-10-15",), kind="museum")
        m2 = self.venue("m2", shows=("2026-10-15",), kind="museum")
        m3 = self.venue("m3", shows=("2026-10-15",), kind="museum")
        self.write(one, two, m1, m2, m3, done)
        todo = run_deep.zone_todo("los-angeles", "Hollywood", only_with_saves=True)
        self.assertEqual([v["id"] for v in todo], ["m1", "m2", "m3", "one"])
        batch = run_deep.pick_batch(todo, 8)
        self.assertEqual([v["id"] for v in batch], ["m1", "m2", "one"])
        self.assertEqual(run_deep._already_saved(two)[0]["title"], "T0")

    def test_apply_skip_marks_js_only_when_detail_says_so(self):
        v = self.venue("js")
        venues.apply_skip(v, {"reason": "unverifiable", "detail": "dates render via JavaScript", "ts": 1}, TODAY)
        self.assertEqual(v["page"]["fetch_mode"], "js")
        w = self.venue("dead")
        venues.apply_skip(w, {"reason": "unverifiable", "detail": "site unreachable", "ts": 1}, TODAY)
        self.assertEqual(w["page"]["fetch_mode"], "static")


class ProgressAndMessageTests(TempContent):
    def test_zone_progress_counts_shows(self):
        a = show("a", venue="Karma"); b = show("b", venue="Karma"); c = show("c", venue="Other")
        tools._write_shows_file(tools._pending_file("los-angeles"), {"shows": [a, b, c]})
        tools.SANDBOX_DIR = None   # zone_progress_snapshot reads the real pools -> temp CONTENT_DIR
        tools._write_shows_file(tools.CONTENT_DIR / "pending" / "los-angeles.json", {"shows": [a, b, c]})
        saves, skips = run_deep.zone_progress_snapshot("los-angeles", "Hollywood")
        self.assertEqual((saves, skips), (3, 0))

    def test_todo_message_lists_saved_shows_and_tags(self):
        msg = run_scrape.format_todo_message("Los Angeles", "Hollywood", [
            {"name": "Karma", "website": "https://karma.org", "kind": "gallery",
             "already_saved": [{"title": "Fragmented Thought", "artist": "C. V.", "end": "2026-09-12"}]},
            {"name": "Mystery Space", "status": "candidate", "fetch_mode": "js"},
        ])
        self.assertIn('already saved here: "Fragmented Thought" (C. V.) through 2026-09-12', msg)
        self.assertIn("[CANDIDATE", msg)
        self.assertIn("render_fetch", msg)
        self.assertNotIn("SAVED VENUES", msg)
        self.assertIn("one save_show per show", msg)


if __name__ == "__main__":
    unittest.main()
