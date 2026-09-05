"""gallery_photos: venue selection, site-page choice, HTML candidate parsing and
ranking, Google metadata ranking, place acceptance, duplicate hashing, budget,
verdict parsing and the apply step. No network.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import io
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gallery_photos as gp  # noqa: E402
import tools  # noqa: E402


def venue(vid, rank=None, **kw):
    v = {"id": vid, "name": vid.replace("-", " ").title(), "rank": rank, "website": None,
         "address": None, "latitude": None, "longitude": None, "google": None, "sources": {}}
    v.update(kw)
    return v


def msg(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, stop_details=None,
                           content=[SimpleNamespace(type="text", text=text)])


class SelectVenues(unittest.TestCase):
    def test_rank_order_and_top(self):
        reg = {"venues": [venue("c", 3), venue("a", 1), venue("x"), venue("b", 2)]}
        self.assertEqual([v["id"] for v in gp.select_venues(reg, top=2)], ["a", "b"])
        self.assertEqual([v["id"] for v in gp.select_venues(reg)], ["a", "b", "c"])

    def test_explicit_ids_keep_order(self):
        reg = {"venues": [venue("c", 3), venue("a", 1)]}
        self.assertEqual([v["id"] for v in gp.select_venues(reg, top=1, ids=["c", "zz", "a"])],
                         ["c", "a"])


class ReportPages(unittest.TestCase):
    def test_about_and_contact_english_first_capped(self):
        labels = {
            "https://x.com/ja/about": "about",
            "https://x.com/en/exhibitions": "exhibitions_current",
            "https://x.com/en/access": "contact_hours",
            "https://x.com/en/about": "about",
            "https://x.com/en/about/history": "about",
            "https://x.com/ja/access": "contact_hours",
        }
        out = gp.report_pages({"triage": {"labels": labels}})
        self.assertEqual(out, ["https://x.com/en/about", "https://x.com/en/access",
                               "https://x.com/en/about/history"])
        self.assertEqual(gp.report_pages(None), [])


HTML = """<html><head><meta property="og:image" content="/img/og-space.jpg"></head><body>
<img src="/img/logo.png"><img src="/wp-content/uploads/space-1024x683.jpg">
<img src="/wp-content/uploads/space.jpg" width="2000" alt="Main room">
<img srcset="/a-400.jpg 400w, /a-1600.jpg 1600w">
<img src="/wp-content/uploads/only-800x600.jpg"><img src="/wp-content/uploads/only-400x300.jpg">
<img src="/icons/wordmark.svg"><a href="/files/facade.jpg">download</a>
</body></html>"""


class ParseSite(unittest.TestCase):
    def test_parse_drops_logos_and_collapses_variants(self):
        cands = gp.parse_site_html(HTML, "https://x.com/about")
        urls = [c["url"] for c in cands]
        self.assertIn("https://x.com/img/og-space.jpg", urls)
        self.assertNotIn("https://x.com/img/logo.png", urls)              # tools.SKIP_HINTS
        self.assertNotIn("https://x.com/icons/wordmark.svg", urls)         # EXTRA_SKIP
        self.assertNotIn("https://x.com/wp-content/uploads/space-1024x683.jpg", urls)  # original present
        self.assertIn("https://x.com/wp-content/uploads/space.jpg", urls)
        self.assertIn("https://x.com/a-1600.jpg", urls)                    # largest srcset entry
        self.assertIn("https://x.com/wp-content/uploads/only-800x600.jpg", urls)  # best variant kept
        self.assertNotIn("https://x.com/wp-content/uploads/only-400x300.jpg", urls)
        self.assertIn("https://x.com/files/facade.jpg", urls)
        only = next(c for c in cands if c["url"].endswith("only-800x600.jpg"))
        self.assertEqual(only["size_hint"], 800)

    def test_rank_site_og_first_then_size(self):
        cands = gp.parse_site_html(HTML, "https://x.com/about")
        ranked = gp.rank_site(cands + cands)                               # same URLs twice = once
        self.assertEqual(ranked[0]["kind"], "og")
        self.assertEqual(ranked[1]["url"], "https://x.com/wp-content/uploads/space.jpg")
        self.assertEqual(len(ranked), len(cands))
        self.assertEqual(len(gp.rank_site(cands, cap=2)), 2)


class RankGoogle(unittest.TestCase):
    def test_owner_then_landscape_then_area_and_min_width(self):
        photos = [
            {"name": "p/visitor-big", "widthPx": 4000, "heightPx": 3000,
             "authorAttributions": [{"displayName": "A Visitor", "uri": "u1"}]},
            {"name": "p/owner-small", "widthPx": 1200, "heightPx": 1600,
             "authorAttributions": [{"displayName": "SCAI The Bathhouse", "uri": "u2"}]},
            {"name": "p/tiny", "widthPx": 300, "heightPx": 200, "authorAttributions": []},
            {"name": "p/visitor-portrait", "widthPx": 3000, "heightPx": 4000, "authorAttributions": []},
        ]
        out = gp.rank_google(photos, "SCAI The Bathhouse")
        self.assertEqual([c["name"] for c in out], ["p/owner-small", "p/visitor-big", "p/visitor-portrait"])
        self.assertTrue(out[0]["owner"])
        self.assertEqual(out[0]["attribution"], {"name": "SCAI The Bathhouse", "uri": "u2"})
        self.assertEqual(len(gp.rank_google(photos, "SCAI The Bathhouse", cap=1)), 1)


class LegacyPhotos(unittest.TestCase):
    def test_normalize_legacy_rows(self):
        rows = [{"height": 622, "width": 1024, "photo_reference": "REF1",
                 "html_attributions": ['<a href="https://maps.google.com/maps/contrib/1">Regen &amp; Projects</a>']},
                {"height": 100, "width": 100, "html_attributions": []},           # no reference: dropped
                {"height": 3000, "width": 4000, "photo_reference": "REF2", "html_attributions": []}]
        out = gp.normalize_legacy_photos(rows)
        self.assertEqual([p["name"] for p in out], ["REF1", "REF2"])
        self.assertEqual(out[0]["authorAttributions"][0],
                         {"displayName": "Regen &amp; Projects", "uri": "https://maps.google.com/maps/contrib/1"})
        self.assertEqual(out[1]["authorAttributions"][0], {"displayName": None, "uri": None})
        ranked = gp.rank_google(out, "Regen Projects")
        self.assertEqual(ranked[0]["name"], "REF1")                          # owner beats 12 MP visitor shot
        self.assertTrue(ranked[0]["legacy"])
        self.assertEqual(ranked[0]["attribution"]["name"], "Regen & Projects")   # unescaped for display
        self.assertIsNone(ranked[1]["attribution"]["name"])


class AcceptPlace(unittest.TestCase):
    def test_name_and_distance(self):
        v = venue("scai-the-bathhouse", name="SCAI The Bathhouse",
                  google={"lat": 35.72178, "lng": 139.77049})
        near = {"name": "SCAI THE BATHHOUSE", "lat": 35.72178 + 0.0009, "lng": 139.77049}   # ~100 m
        far = {"name": "SCAI THE BATHHOUSE", "lat": 35.72178 + 0.0020, "lng": 139.77049}    # ~220 m
        other = {"name": "Taka Ishii Gallery Tokyo", "lat": 35.72178, "lng": 139.77049}
        self.assertTrue(gp.accept_place(near, v, "tokyo"))
        self.assertFalse(gp.accept_place(far, v, "tokyo"))
        self.assertFalse(gp.accept_place(other, v, "tokyo"))

    def test_loose_radius_without_places_coords(self):
        v = venue("scai-the-bathhouse", name="SCAI The Bathhouse", latitude=35.72178, longitude=139.77049)
        hit = {"name": "SCAI The Bathhouse", "lat": 35.72178 + 0.0020, "lng": 139.77049}     # ~220 m
        self.assertTrue(gp.accept_place(hit, v, "tokyo"))
        self.assertTrue(gp.accept_place({"name": "SCAI The Bathhouse"}, venue("x", name="SCAI The Bathhouse"), "tokyo"))


def picture(seed: int, size=(640, 480)):
    img = Image.new("RGB", size, (seed * 40 % 255, 120, 200 - seed * 30 % 200))
    d = ImageDraw.Draw(img)
    d.rectangle([seed * 20, 40, seed * 20 + 200, 300], fill=(255, 255, 255))
    d.ellipse([300, seed * 25, 500, seed * 25 + 150], fill=(0, 0, 0))
    return img


def blocks(seed: int, size=(640, 480)) -> Image.Image:
    """An 8x8 mosaic of random greys scaled up: a hash-friendly, high-entropy photo stand-in."""
    rnd = random.Random(seed)
    img = Image.new("L", (8, 8))
    img.putdata([rnd.randrange(256) for _ in range(64)])
    return img.resize(size, Image.NEAREST).convert("RGB")


def jpeg_bytes(img: Image.Image, quality=88) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class Hashing(unittest.TestCase):
    def test_near_duplicates_and_distinct(self):
        a = blocks(1)
        a_small = a.resize((320, 240), Image.LANCZOS)
        b = blocks(2)
        ha, hs, hb = gp.dhash(a), gp.dhash(a_small), gp.dhash(b)
        self.assertTrue(gp.is_duplicate(hs, [ha]))
        self.assertFalse(gp.is_duplicate(hb, [ha]))

    def test_flat_images_never_collapse(self):
        white1 = Image.new("RGB", (640, 480), (250, 250, 250))
        white2 = Image.new("RGB", (640, 480), (244, 244, 244))
        self.assertFalse(gp.is_duplicate(gp.dhash(white2), [gp.dhash(white1)]))

    def test_store_candidate_rejects_small_and_banners(self):
        with tempfile.TemporaryDirectory() as tmp:
            info, err = gp.store_candidate(jpeg_bytes(picture(1, (300, 200))), Path(tmp) / "c.jpg")
            self.assertIsNone(info)
            self.assertIn("too small", err)
            info, err = gp.store_candidate(jpeg_bytes(picture(1, (1200, 200))), Path(tmp) / "c.jpg")
            self.assertIsNone(info)
            self.assertIn("banner", err)
            info, err = gp.store_candidate(jpeg_bytes(picture(1)), Path(tmp) / "c.jpg")
            self.assertIsNone(err)
            self.assertEqual(info["px"], [640, 480])
            self.assertTrue((Path(tmp) / "c.jpg").exists())


class BudgetTest(unittest.TestCase):
    def test_google_cap_and_usd(self):
        b = gp.Budget(usd_cap=1.0, google_cap=2)
        b.take_google()
        b.take_google()
        with self.assertRaises(gp.BudgetExceeded):
            b.take_google()
        b.check_usd(0.5, 0.4)
        with self.assertRaises(gp.BudgetExceeded):
            b.check_usd(0.5, 0.6)
        gp.Budget().check_usd(99.0)                       # no cap = never raises


class ParseVerdict(unittest.TestCase):
    def test_valid_sorted_filtered_capped(self):
        picks = [{"id": f"C{i}", "rank": 9 - i, "subject": "interior", "caption": "x"} for i in range(1, 9)]
        picks.append({"id": "C42", "rank": 0, "subject": "exterior", "caption": "ghost"})
        text = json.dumps({"picks": picks, "rejects": [{"id": "C9", "reason": "logo"}], "confidence": 1.7})
        v, err = gp.parse_verdict(msg(text), {f"C{i}" for i in range(1, 10)})
        self.assertIsNone(err)
        self.assertEqual([p["id"] for p in v["picks"]], ["C8", "C7", "C6", "C5", "C4"])
        self.assertEqual([p["rank"] for p in v["picks"]], [1, 2, 3, 4, 5])
        self.assertEqual(v["rejects"], [{"id": "C9", "reason": "logo"}])
        self.assertEqual(v["confidence"], 1.0)

    def test_errors(self):
        self.assertEqual(gp.parse_verdict(msg("", "refusal"), {"C1"})[1], "refusal (None)")
        self.assertIn("max_tokens", gp.parse_verdict(msg("{", "max_tokens"), {"C1"})[1])
        self.assertIn("validation", gp.parse_verdict(msg('{"picks": "no"}'), {"C1"})[1])

    def test_request_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            picture(2, (1600, 1200)).save(Path(tmp) / "cand-01.jpg")
            fetched = [{"id": "C1", "provider": "site", "file": "cand-01.jpg", "px": [1600, 1200],
                        "source_page": "https://x.com/", "alt": "Main room"}]
            req, est = gp.build_judge_request("claude-sonnet-5", venue("x", name="X Gallery"), "tokyo",
                                              fetched, Path(tmp))
            content = req["messages"][0]["content"]
            self.assertEqual([c["type"] for c in content], ["text", "text", "image"])
            self.assertIn("[C1] provider=site", content[1]["text"])
            self.assertEqual(req["output_config"]["format"]["schema"], gp.VERDICT_SCHEMA)
            self.assertEqual(req["output_config"]["effort"], "low")
            self.assertGreater(est, gp.JUDGE_OVERHEAD_TOKENS + 500)       # 768x576 / 750 = 589
            self.assertLess(est, gp.JUDGE_OVERHEAD_TOKENS + 700)


class ApplyVenue(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tools.set_sandbox(self.tmp.name)

    def tearDown(self):
        tools.set_sandbox(None)
        self.tmp.cleanup()

    def test_site_takes_01_over_google_on_tie_and_block_shape(self):
        v = venue("x-gallery")
        vdir = gp.venue_dir("tokyo", "x-gallery")
        cdir = gp.cand_dir("tokyo", "x-gallery")
        vdir.mkdir(parents=True)
        cdir.mkdir(parents=True)
        for i in (1, 2, 3):
            picture(i).save(cdir / f"cand-{i:02d}.jpg")
        (vdir / "01.jpg").write_bytes(b"stale")
        work = {
            "candidates": [{"id": "C1"}, {"id": "C2"}, {"id": "C3"}, {"id": "C4"}],
            "google_events": 2,
            "fetched": [
                {"id": "C1", "provider": "google", "file": "cand-01.jpg", "px": [4000, 3000],
                 "name": "places/p/photos/q", "attribution": {"name": "Someone", "uri": "u"}, "fetched_ts": 1},
                {"id": "C2", "provider": "site", "file": "cand-02.jpg", "px": [2000, 1500],
                 "url": "https://x.com/space.jpg", "source_page": "https://x.com/", "fetched_ts": 2},
                {"id": "C3", "provider": "site", "file": "cand-03.jpg", "px": [1000, 800],
                 "url": "https://x.com/door.jpg", "source_page": "https://x.com/about", "fetched_ts": 3},
            ],
            "verdict": {"model": "claude-sonnet-5", "confidence": 0.9, "picks": [
                {"id": "C1", "rank": 1, "subject": "exterior", "caption": "Facade"},
                {"id": "C2", "rank": 2, "subject": "interior", "caption": "Main room"},
                {"id": "C3", "rank": 3, "subject": "entrance", "caption": "Door"},
                {"id": "C9", "rank": 4, "subject": "other", "caption": "never fetched"},
            ], "rejects": []},
        }
        photos = gp.apply_venue("tokyo", v, work, keep_candidates=True)
        self.assertEqual(photos["status"], "done")
        self.assertEqual([f["provider"] for f in photos["files"]], ["site", "google", "site"])
        self.assertEqual(len(list(cdir.glob("cand-*.jpg"))), 3)          # kept for a free re-judge
        self.assertEqual(photos["files"][0]["path"], "images/venues/tokyo/x-gallery/01.jpg")
        self.assertEqual(photos["files"][0]["url"], "https://x.com/space.jpg")
        self.assertEqual(photos["files"][1]["attribution"]["name"], "Someone")
        self.assertEqual(photos["candidates_seen"], 4)
        self.assertEqual(photos["google_events"], 2)
        self.assertEqual(sorted(p.name for p in vdir.glob("*.jpg")), ["01.jpg", "02.jpg", "03.jpg"])
        self.assertGreater((vdir / "01.jpg").stat().st_size, 100)        # stale file replaced

    def test_reapply_after_candidates_were_removed(self):
        v = venue("z")
        vdir = gp.venue_dir("tokyo", "z")
        cdir = gp.cand_dir("tokyo", "z")
        cdir.mkdir(parents=True)
        picture(1).save(cdir / "cand-01.jpg")
        picture(2).save(cdir / "cand-02.jpg")
        work = {"candidates": [{"id": "C1"}, {"id": "C2"}],
                "fetched": [{"id": "C1", "provider": "site", "file": "cand-01.jpg", "px": [640, 480]},
                            {"id": "C2", "provider": "site", "file": "cand-02.jpg", "px": [640, 480]}],
                "verdict": {"picks": [{"id": "C2", "rank": 1, "subject": "exterior", "caption": "a"},
                                      {"id": "C1", "rank": 2, "subject": "interior", "caption": "b"}],
                            "rejects": [], "confidence": 0.9}}
        first = gp.apply_venue("tokyo", v, work, keep_candidates=False)   # candidates removed
        self.assertEqual(list(cdir.glob("cand-*.jpg")), [])
        self.assertEqual(sorted(p.name for p in vdir.glob("*.jpg")), ["01.jpg", "02.jpg"])
        self.assertEqual([f["file"] for f in work["fetched"]], ["02.jpg", "01.jpg"])
        again = gp.apply_venue("tokyo", v, work, keep_candidates=False)   # re-apply from the final files
        self.assertEqual([f["path"] for f in again["files"]], [f["path"] for f in first["files"]])
        self.assertEqual(sorted(p.name for p in vdir.glob("*.jpg")), ["01.jpg", "02.jpg"])
        self.assertGreater((vdir / "01.jpg").stat().st_size, 100)
        (vdir / "02.jpg").unlink()                                    # a source vanished: skipped, noted
        third = gp.apply_venue("tokyo", v, work, keep_candidates=False)
        self.assertEqual(len(third["files"]), 1)
        self.assertIn("apply:missing_source:C1", work["notes"])

    def test_legacy_work_file_migrates_to_cache(self):
        vdir = gp.venue_dir("tokyo", "w")
        vdir.mkdir(parents=True)
        (vdir / "_work.json").write_text(json.dumps({"venue_id": "w", "candidates": [{"id": "C1"}],
                                                     "fetched": [], "verdict": None, "notes": []}))
        picture(1).save(vdir / "cand-01.jpg")
        work = gp.load_work("tokyo", "w")
        self.assertEqual(work["candidates"], [{"id": "C1"}])
        self.assertFalse((vdir / "_work.json").exists())
        self.assertTrue((gp.cand_dir("tokyo", "w") / "_work.json").exists())
        self.assertTrue((gp.cand_dir("tokyo", "w") / "cand-01.jpg").exists())

    def test_all_rejected(self):
        v = venue("y")
        vdir = gp.cand_dir("tokyo", "y")
        vdir.mkdir(parents=True)
        picture(1).save(vdir / "cand-01.jpg")
        work = {"candidates": [{"id": "C1"}], "fetched": [{"id": "C1", "provider": "site", "file": "cand-01.jpg"}],
                "verdict": {"picks": [], "rejects": [{"id": "C1", "reason": "logo"}], "confidence": 0.8}}
        photos = gp.apply_venue("tokyo", v, work, keep_candidates=True)
        self.assertEqual((photos["status"], photos["note"], photos["files"]), ("none", "all_rejected", []))
        self.assertTrue((vdir / "cand-01.jpg").exists())


class MonthTally(unittest.TestCase):
    def test_free_tier_then_billing(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = gp.EVENTS_PATH
            gp.EVENTS_PATH = Path(tmp) / "events.json"
            try:
                self.assertEqual(gp.add_month_events(900), (900, 0.0))
                total, usd = gp.add_month_events(200)
                self.assertEqual(total, 1100)
                self.assertAlmostEqual(usd, 0.7, places=4)
                self.assertEqual(gp.month_events(), 1100)
                self.assertEqual(gp.add_month_events(10), (1110, 0.07))
            finally:
                gp.EVENTS_PATH = orig


if __name__ == "__main__":
    unittest.main()
