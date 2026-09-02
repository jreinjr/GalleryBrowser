"""The deep-session venue-resolution contract: save_show classifies current vs
upcoming saves, log_skip pushes back once on 'unverifiable' misuse, and
SessionTrace.unresolved() drives the harness's one-time nudge. Stdlib unittest.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402
import venues  # noqa: E402
import run_scrape  # noqa: E402
from test_dates import TempContent, show  # noqa: E402


def iso(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


class SessionTempContent(TempContent):
    """TempContent + an installed SessionTrace (sandbox mode: registry hooks
    are no-ops, the ledger is not)."""

    def setUp(self):
        super().setUp()
        self.trace = venues.SessionTrace("t", "los-angeles")
        tools.set_session(self.trace)

    def tearDown(self):
        tools.set_session(None)
        super().tearDown()

    def save(self, **kw):
        rec = show(**kw)
        rec["images"] = [self.image(rec["slug"])]
        return json.loads(tools.save_show(rec, "los-angeles", ["Hollywood"]))

    def skip(self, venue="Test Gallery", reason="unverifiable", detail="site unreachable"):
        return tools.log_skip({"city": "los-angeles", "venue": venue, "neighborhood": "Hollywood",
                               "reason": reason, "detail": detail, "url": None},
                              "los-angeles", "t")


class SaveShowClassificationTests(SessionTempContent):
    def test_current_upcoming_and_dateless(self):
        out = self.save(slug="a", start=iso(-10), end=iso(30))
        self.assertNotIn("venue_note", out)
        r = self.trace.resolutions["test"]           # _norm_venue("Test Gallery") == "test"
        self.assertEqual((r["current"], r["upcoming"]), (1, 0))

        out = self.save(slug="b", title="Later", start=iso(20), end=iso(60))
        self.assertIn("UPCOMING", out["venue_note"])
        self.assertEqual((r["current"], r["upcoming"]), (1, 1))

        self.save(slug="c", title="Now-ish", start=None, end=None, note="on view now per the page")
        self.assertEqual(r["current"], 2)             # null start counts as open


class LogSkipGuardTests(SessionTempContent):
    JS_DETAIL = ("Gallery's own site confirms the show is the current on-view exhibition but the "
                 "date badge renders via JavaScript; no exact close date")

    def test_rejects_confirmed_show_unverifiable_once_then_allows(self):
        with self.assertRaises(ValueError) as cm:
            self.skip(detail=self.JS_DETAIL)
        self.assertIn("end_date null", str(cm.exception))
        self.assertFalse(tools.SKIPS_FILE.exists())
        self.assertTrue(self.trace.resolutions["test"]["rejected_once"])
        out = json.loads(self.skip(detail=self.JS_DETAIL))    # second attempt goes through
        self.assertEqual(out["result"], "logged")
        self.assertEqual(self.trace.resolutions["test"]["skip"], "unverifiable")
        self.assertEqual(len(tools.SKIPS_FILE.read_text().splitlines()), 1)

    def test_plain_unverifiable_passes_first_time(self):
        out = json.loads(self.skip(detail="site unreachable, no listing anywhere"))
        self.assertEqual(out["result"], "logged")

    def test_guard_is_noop_without_session(self):
        tools.set_session(None)
        out = json.loads(self.skip(detail=self.JS_DETAIL))
        self.assertEqual(out["result"], "logged")

    def test_confirmed_show_skip_helper(self):
        self.assertTrue(tools.confirmed_show_skip(self.JS_DETAIL))
        self.assertFalse(tools.confirmed_show_skip("site unreachable"))
        self.assertFalse(tools.confirmed_show_skip(None))


class UnresolvedTests(SessionTempContent):
    def test_upcoming_only_and_nothing_then_cleared(self):
        self.save(slug="u", start=iso(20), end=iso(60))
        todo = [{"name": "Test Gallery", "key": "test", "venue_id": None},
                {"name": "Other", "key": "other", "venue_id": None}]
        self.assertEqual([(v["key"], st) for v, st in self.trace.unresolved(todo)],
                         [("test", "upcoming_only"), ("other", "nothing")])
        self.skip(venue="Test Gallery", reason="closed_or_between_shows",
                  detail="between shows; next opens in three weeks")
        self.assertEqual([v["key"] for v, _ in self.trace.unresolved(todo)], ["other"])
        self.save(slug="o", venue="Other", start=iso(-5), end=iso(20))
        self.assertEqual(self.trace.unresolved(todo), [])

    def test_matches_by_venue_id_when_spelling_differs(self):
        self.trace.resolution("1301 PE", venue_id="1301-pe")["current"] += 1
        todo = [{"name": "1301PE", "key": tools._norm_venue("1301PE"), "venue_id": "1301-pe"}]
        self.assertEqual(self.trace.unresolved(todo), [])


class HarnessTextTests(unittest.TestCase):
    def test_resolution_nudge_text(self):
        import harness
        txt = harness._resolution_nudge_text([({"name": "1301PE"}, "upcoming_only"),
                                              ({"name": "Fernberger"}, "nothing")])
        self.assertIn("1301PE (only an upcoming show saved", txt)
        self.assertIn("Fernberger (nothing saved or skipped)", txt)

    def test_todo_venue_keys(self):
        self.assertEqual(run_scrape.todo_venue_keys([{"name": "Karma Gallery", "venue_id": "karma"}]),
                         [{"name": "Karma Gallery", "key": "karma", "venue_id": "karma"}])


if __name__ == "__main__":
    unittest.main()
