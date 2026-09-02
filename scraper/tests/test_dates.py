"""Nullable-date handling: helpers, placement gates, save/confirm handlers,
validate_content, render_fetch date scanning (stdlib unittest).

    scraper/.venv/bin/python -m unittest discover -s scraper/tests

Everything runs against a temp content tree (tools.CONTENT_DIR + sandbox);
nothing touches content/.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools  # noqa: E402

TODAY = date(2026, 9, 1)


def show(slug="s1", start="2026-08-15", end="2026-10-15", museum=False, note=None,
         venue="Test Gallery", title="Untitled", coords=True, **extra):
    rec = {
        "city": "los-angeles", "slug": slug, "title": title, "artist": None,
        "start_date": start, "end_date": end, "dates_note": note,
        "description": " ".join(["word"] * 70),
        "editors_pick": False, "featured": False, "reception": None,
        "images": [], "source_urls": ["https://example.org/x"],
        "venue": {"name": venue, "is_museum": museum, "address": "1 Main St",
                  "address_detail": None, "neighborhood": "Hollywood", "hours": [],
                  "phone": None, "website": "https://example.org",
                  "latitude": 34.0 if coords else None, "longitude": -118.0 if coords else None},
    }
    rec.update(extra)
    return rec


class TempContent(unittest.TestCase):
    """Redirect every content path tools.py writes to a temp dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {k: getattr(tools, k) for k in
                       ("CONTENT_DIR", "SKIPS_FILE", "EVENTS_FILE", "VERIFY_RESULTS", "SANDBOX_DIR")}
        tools.CONTENT_DIR = self.tmp
        tools.SKIPS_FILE = self.tmp / "spend" / "skips.jsonl"
        tools.EVENTS_FILE = self.tmp / "spend" / "session_events.jsonl"
        tools.VERIFY_RESULTS = self.tmp / "spend" / "verify_results.jsonl"
        tools.set_sandbox(self.tmp / "sandbox")   # no coords/registry side effects

    def tearDown(self):
        for k, v in self._saved.items():
            setattr(tools, k, v)

    def image(self, slug, width=600):
        from PIL import Image
        d = self.tmp / "sandbox" / "images" / "los-angeles" / slug
        d.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, 400), "gray").save(d / "01.jpg", "JPEG")
        return f"images/los-angeles/{slug}/01.jpg"

    def verdict(self, slug, status="verified", age_days=0):
        tools.VERIFY_RESULTS.parent.mkdir(parents=True, exist_ok=True)
        with open(tools.VERIFY_RESULTS, "a") as f:
            f.write(json.dumps({"ts": int(time.time()) - age_days * 86400, "city": "los-angeles",
                                "slug": slug, "status": status, "reason": None,
                                "applied": [], "placement": "pending"}) + "\n")


class HelperTests(unittest.TestCase):
    def test_dates_ok(self):
        self.assertTrue(tools.dates_ok(show()))
        self.assertFalse(tools.dates_ok(show(end=None)))
        self.assertFalse(tools.dates_ok(show(start=None, end=None)))
        self.assertFalse(tools.dates_ok(show(end="2026-08")))

    def test_effective_end_gallery_museum_and_cap(self):
        ts = int(time.mktime(TODAY.timetuple()))
        g = show(end=None, start="2026-08-15", dates_confirmed_ts=ts)
        self.assertEqual(tools.effective_end(g, TODAY), TODAY + timedelta(days=45))
        m = show(end=None, start="2026-08-15", museum=True, dates_confirmed_ts=ts)
        self.assertEqual(tools.effective_end(m, TODAY), min(TODAY + timedelta(days=120),
                                                            date(2026, 8, 15) + timedelta(days=365)))
        capped = show(end=None, start="2026-05-01", dates_confirmed_ts=ts)   # start + 90 < confirmed + 45
        self.assertEqual(tools.effective_end(capped, TODAY), date(2026, 5, 1) + timedelta(days=90))
        self.assertEqual(tools.effective_end(show(end="2026-10-15"), TODAY), date(2026, 10, 15))

    def test_show_expired_null_end_retires_after_confirmation_age(self):
        fresh = show(end=None, dates_confirmed_ts=int(time.mktime(TODAY.timetuple())))
        self.assertFalse(tools.show_expired(fresh, TODAY))
        old = show(end=None, dates_confirmed_ts=int(time.mktime((TODAY - timedelta(days=50)).timetuple())))
        self.assertTrue(tools.show_expired(old, TODAY))
        self.assertFalse(tools.show_expired(show(end=None), TODAY))   # no ts: measured from today

    def test_show_in_window_null_start_is_on_view(self):
        self.assertTrue(tools.show_in_window(show(start=None, end=None), TODAY))
        self.assertFalse(tools.show_in_window(show(start="2026-12-01", end="2027-01-01"), TODAY))

    def test_set_dates_meta_confidence(self):
        for kw, conf in ((dict(), "exact"), (dict(note="approx"), "approximate"),
                         (dict(end=None, note="no close"), "unknown_end"),
                         (dict(start=None, end=None, note="on view now"), "unknown")):
            s = show(**kw)
            tools._set_dates_meta(s, 123)
            self.assertEqual(s["dates_confidence"], conf, kw)
            self.assertEqual(s["dates_confirmed_ts"], 123)

    def test_scan_dates_finds_script_payload_dates(self):
        html = '<script>{"timing":{"begins":"2026-07-17","ends":"2026-09-13"}}</script>'
        found = tools._scan_dates("Dorothy Hood on view", html)
        self.assertTrue(any(d.startswith("2026-07-17") for d in found))
        self.assertTrue(any(d.startswith("2026-09-13") for d in found))
        self.assertTrue(all("[html:" in d for d in found))


class GateTests(TempContent):
    def test_awaiting_window_only_requires_dates(self):
        s = show(end=None, note="no close")
        self.verdict("s1")
        self.assertFalse(tools.awaiting_window_only("los-angeles", s))
        self.assertTrue(tools.awaiting_window_only("los-angeles", show()))

    def test_date_fill_cooldown(self):
        s = show(end=None, note="no close")
        self.assertFalse(tools.date_fill_cooldown("los-angeles", s))   # never audited
        self.verdict("s1", age_days=6)
        self.assertTrue(tools.date_fill_cooldown("los-angeles", s))
        self.assertFalse(tools.verify_candidate("los-angeles", s))
        tools.VERIFY_RESULTS.unlink()
        self.verdict("s1", age_days=8)
        self.assertFalse(tools.date_fill_cooldown("los-angeles", s))
        self.assertTrue(tools.verify_candidate("los-angeles", s))
        self.assertFalse(tools.date_fill_cooldown("los-angeles", show()))   # dated: n/a

    def test_pending_reason_needs_dates(self):
        s = show(end=None, note="no close", dates_confidence="unknown_end",
                 dates_confirmed_ts=int(time.time()) - 3 * 86400)
        self.assertEqual(tools.pending_reason("los-angeles", s), "needs dates (unknown_end; last check 3d ago)")

    def test_sweep_demotes_dateless_published_and_never_promotes_dateless_pending(self):
        pub = show("pub-null", end=None, note="n", dates_confirmed_ts=int(time.time()))
        pend_null = show("pend-null", end=None, note="n", dates_confirmed_ts=int(time.time()))
        pend_ok = show("pend-ok")
        tools._write_shows_file(tools._city_file("los-angeles"), {"shows": [pub]})
        tools._write_shows_file(tools._pending_file("los-angeles"), {"shows": [pend_null, pend_ok]})
        self.verdict("pend-null"); self.verdict("pend-ok")
        actions = tools.sweep_placements(["los-angeles"])
        self.assertEqual([a["slug"] for a in actions["demoted"]], ["pub-null"])
        self.assertEqual(actions["demoted"][0]["reason"], "needs dates")
        self.assertEqual([a["slug"] for a in actions["promoted"]], ["pend-ok"])
        published = {s["slug"] for s in tools._load_shows_file(tools._city_file("los-angeles"))["shows"]}
        self.assertEqual(published, {"pend-ok"})


class SaveConfirmTests(TempContent):
    def save(self, **kw):
        rec = show(**kw)
        rec["images"] = [self.image(rec["slug"])]
        return json.loads(tools.save_show(rec, "los-angeles", ["Hollywood"]))

    def with_coords(self, slug):
        # sandbox saves skip coordinate resolution; give the record a pin so
        # confirm_show's placement is decided by dates alone
        pend = tools._load_shows_file(tools._pending_file("los-angeles"))
        for s in pend["shows"]:
            if s["slug"] == slug:
                s["venue"]["latitude"], s["venue"]["longitude"] = 34.0, -118.0
        tools._write_shows_file(tools._pending_file("los-angeles"), pend)

    def test_null_end_with_note_saves_and_is_held(self):
        out = self.save(slug="a", end=None, note="page says 'on view now', no closing date")
        self.assertEqual(out["result"], "saved")
        self.assertIn("dates_note", out)
        rec = tools._load_shows_file(tools._pending_file("los-angeles"))["shows"][0]
        self.assertEqual(rec["dates_confidence"], "unknown_end")
        self.assertIsNone(rec["end_date"])
        self.assertTrue(rec["dates_confirmed_ts"] > 0)

    def test_null_end_without_note_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.save(slug="b", end=None, note=None)
        self.assertIn("dates_note", str(cm.exception))

    def test_month_granularity_rejected(self):
        with self.assertRaises(ValueError) as cm:
            self.save(slug="c", end="2026-08", note="august")
        self.assertIn("ISO", str(cm.exception))

    def test_confirm_with_end_date_publishes_and_clears_note(self):
        self.save(slug="d", end=None, note="no close")
        self.with_coords("d")
        out = json.loads(tools.confirm_show({
            "city": "los-angeles", "slug": "d", "status": "corrected", "reason": "found on Artsy",
            "corrections": {"start_date": None, "end_date": "2026-10-01", "hours": None, "address": None,
                            "address_detail": None, "phone": None, "website": None, "reception": None}},
            "los-angeles"))
        self.assertEqual(out["placement"], "published")
        rec = tools._load_shows_file(tools._city_file("los-angeles"))["shows"][0]
        self.assertIsNone(rec["dates_note"])
        self.assertEqual(rec["dates_confidence"], "exact")

    def test_verified_but_dateless_stays_pending_with_note(self):
        self.save(slug="e", end=None, note="no close")
        self.with_coords("e")
        out = json.loads(tools.confirm_show({"city": "los-angeles", "slug": "e", "status": "verified",
                                             "reason": None, "corrections": None}, "los-angeles"))
        self.assertEqual(out["placement"], "pending")
        self.assertIn("closing date unknown", out["note"])


class ValidateContentTests(TempContent):
    def test_null_date_in_published_file_is_reported_not_raised(self):
        import validate_content
        rec = show("v1", end=None, note="n")
        rec["images"] = []
        (self.tmp / "los-angeles.json").write_text(json.dumps({"shows": [rec]}))
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = validate_content.main()
        finally:
            sys.stdout = old
        self.assertEqual(rc, 1)
        self.assertIn("null end_date in a PUBLISHED file", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
