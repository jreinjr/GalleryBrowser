"""curation_site.py — client site builder: trimming, share links, defaults, core inlining."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import curation_dashboard  # noqa: E402
import curation_site as site  # noqa: E402

REPORT_ROW = {
    "slug": "s1", "title": "T", "artist": "A", "pool": "published", "in_window": True,
    "dates": {"start": "2026-08-01", "end": "2026-09-30"}, "days_since_open": 32, "days_to_close": 28,
    "n_images": 4, "desc_words": 200, "features": {"judge": 0.9}, "contrib": {"judge": 0.396}, "score": 0.396,
    "rank": 3, "reasons": ["judge 0.90"], "featured": True, "featured_rank": 2, "gate": None, "pinned": False,
    "venue": {"name": "V", "id": "v", "tier": None, "n_fair": 2, "is_museum": False, "neighborhood": "Hollywood", "norm": "v"},
    "signals": [{"id": "x", "dedupe_key": "y", "source": {"id": "carla", "kind": "news", "name": "Carla", "url": "u"},
                 "kind": "review", "strength": "featured", "date": "2026-08-20", "published_at": "2026-08-20",
                 "snippet": "great", "url": "https://carla.example/a", "run": "r1", "variant": "v1", "model": "m",
                 "match_confidence": 0.9, "match_method": "exact", "n_rows": 1, "show_ref": {"venue": "V"}}],
    "judge": {"judge_v1": {
        "claude-sonnet-5": {"ts": 1, "prompt_hash": "p", "evidence_hash": "e", "cache_key": "c", "usage": {"input": 1},
                            "cost_usd": 0.01, "model": "claude-sonnet-5", "overall": 9, "confidence": 0.8,
                            "scores": {"artist_significance": 9}, "rationale": "because"},
        "claude-opus-5": {"overall": 7, "scores": {}, "rationale": "meh", "usage": {}}}},
    "wiki": {"title": "A", "url": "https://w", "pageviews_90d": 100, "description": "d", "considered": ["A"]},
    "keyword_hits": [{"class": "survey", "label": "survey", "weight": 0.5, "term": "survey", "where": "title", "extra": 1}],
}
LIVE = {"version": 1, "threshold": 0.22, "max_per_venue": 2, "exclude_museums": False, "editors_pick_top": 5,
        "weights": {"judge": 0.44, "quality": 0.08, "venue": 0.14, "press": 0.04, "closing_soon": 0.04, "museum": -0.2},
        "judge": {"variant": "judge_v1", "model": "sonnet"}, "overrides": {"pin": [], "exclude": []}, "_note": "live"}


class TrimTests(unittest.TestCase):
    def test_trim_show_keeps_evidence_and_drops_internals(self):
        out = site.trim_show(REPORT_ROW, LIVE["judge"], {"source_urls": ["https://v.example/show"], "venue": {"website": "https://v.example"}},
                             "thumbs/s1.webp", {"s1": {"featured_rank": 2, "editors_pick": True}})
        for k in site.SHOW_KEYS:
            self.assertIn(k, out)
        for k in ("featured", "gate", "pinned", "featured_rank"):
            self.assertNotIn(k, out)             # recomputed in the page
        self.assertEqual(set(out["venue"]), set(site.VENUE_KEYS))
        sig = out["signals"][0]
        self.assertEqual(sig["source"], {"id": "carla", "kind": "news", "name": "Carla"})
        for k in ("id", "dedupe_key", "run", "variant", "model", "show_ref", "published_at"):
            self.assertNotIn(k, sig)
        self.assertEqual(list(out["judge"]), ["judge_v1"])
        self.assertEqual(list(out["judge"]["judge_v1"]), ["claude-sonnet-5"])   # live model only
        verdict = out["judge"]["judge_v1"]["claude-sonnet-5"]
        self.assertEqual(set(verdict), set(site.JUDGE_KEYS))
        self.assertEqual(out["wiki"], {"title": "A", "url": "https://w", "pageviews_90d": 100})
        self.assertNotIn("extra", out["keyword_hits"][0])
        self.assertEqual(out["source_urls"], ["https://v.example/show"])
        self.assertEqual(out["venue_website"], "https://v.example")
        self.assertEqual(out["thumb"], "thumbs/s1.webp")
        self.assertTrue(out["featured_live"] and out["editors_pick_live"])
        self.assertEqual(out["featured_rank_live"], 2)
        self.assertNotIn("usage", json.dumps(out))
        self.assertNotIn("hash", json.dumps(out))

    def test_trim_judge_mean_keeps_all_models_and_none_drops(self):
        both = site.trim_judge(REPORT_ROW["judge"], {"variant": "judge_v1", "model": "mean"})
        self.assertEqual(set(both["judge_v1"]), {"claude-sonnet-5", "claude-opus-5"})
        self.assertEqual(site.trim_judge(REPORT_ROW["judge"], {"variant": "judge_v1", "model": "none"}), {})
        self.assertEqual(site.trim_judge(REPORT_ROW["judge"], {"variant": "judge_v9", "model": "sonnet"}), {})


class ShareLinkTests(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        diff = {"threshold": 0.3, "weights": {"museum": -0.35, "judge": 0.5}, "exclude_museums": True, "max_per_venue": None}
        tok = site.encode_diff(diff)
        self.assertNotIn("=", tok)
        self.assertRegex(tok, r"^[A-Za-z0-9_-]+$")
        self.assertEqual(site.decode_token(tok), diff)
        for link in (f"https://gallery-browser-curation.vercel.app/#p={tok}", f"#p={tok}", f"p={tok}", tok,
                     f"https://x.test/path?x=1#p={tok}&other=2"):
            self.assertEqual(site.token_from_link(link), tok, link)

    def test_diff_and_merge_are_inverse(self):
        current = json.loads(json.dumps(LIVE))
        current["threshold"] = 0.3
        current["weights"]["museum"] = -0.35
        current["max_per_venue"] = None
        d = site.diff_params(LIVE, current)
        self.assertEqual(d, {"threshold": 0.3, "max_per_venue": None, "weights": {"museum": -0.35}})
        full = site.decode_link("#p=" + site.encode_diff(d), LIVE)
        self.assertEqual(full["threshold"], 0.3)
        self.assertIsNone(full["max_per_venue"])
        self.assertEqual(full["weights"]["museum"], -0.35)
        self.assertEqual(full["weights"]["judge"], 0.44)          # untouched deep keys survive the merge
        self.assertEqual(full["judge"], LIVE["judge"])
        self.assertNotIn("_note", full)
        self.assertEqual(list(full)[:2], ["version", "threshold"])  # curate.py key order

    def test_js_encoder_compat(self):
        # what the page's b64url(JSON.stringify(diff)) produces for a known diff
        self.assertEqual(site.decode_token("eyJ0aHJlc2hvbGQiOjAuMywid2VpZ2h0cyI6eyJtdXNldW0iOi0wLjM1fX0"),
                         {"threshold": 0.3, "weights": {"museum": -0.35}})

    def test_decode_rejects_non_object(self):
        with self.assertRaises(ValueError):
            site.decode_token(site.encode_diff([1, 2]) if False else "WzEsMl0")
        with self.assertRaises(ValueError):
            site.token_from_link("https://x.test/")


class PayloadTests(unittest.TestCase):
    def test_default_params_are_the_live_params(self):
        report = {"today": "2026-09-02", "generated_at": "g", "sources": [{"id": "carla", "name": "Carla", "kind": "news",
                  "weight": 1, "active": True, "urls": ["x"], "notes": "n"}], "shows": [REPORT_ROW], "params_default": LIVE}
        curated = {"params_hash": "abc", "ts": 1788382496, "params": LIVE,
                   "featured": [{"slug": "s1", "rank": 3, "featured_rank": 1, "score": 0.396, "editors_pick": True}]}
        payload = site.build_payload("los-angeles", report, curated, {"s1": {"source_urls": [], "venue": {}}}, {}, "https://s", "me@x")
        expect = {k: v for k, v in LIVE.items() if not k.startswith("_")}
        expect.update(city="los-angeles", today="2026-09-02")
        self.assertEqual(payload["params_default"], expect)
        self.assertEqual(payload["live"]["featured"], ["s1"])
        self.assertEqual(payload["live"]["editors_pick"], ["s1"])
        self.assertEqual(payload["live"]["params_hash"], "abc")
        self.assertEqual(payload["sources"], [{"id": "carla", "name": "Carla", "kind": "news", "weight": 1, "active": True}])
        self.assertEqual(payload["client_paths"], site.CLIENT_PATHS)
        self.assertEqual(payload["contact_email"], "me@x")
        self.assertEqual(payload["city_name"], "Los Angeles")
        self.assertEqual(payload["candidates"], [])   # the core expects these keys to exist

    def test_freshness_check(self):
        with tempfile.TemporaryDirectory() as d:
            rep, cur = Path(d) / "r.json", Path(d) / "c.json"
            cur.write_text("{}"); rep.write_text("{}")
            os.utime(cur, (2_000_000_000, 2_000_000_000)); os.utime(rep, (2_000_000_100, 2_000_000_100))
            self.assertEqual(site.check_freshness(rep, cur, {"params_default": LIVE}, {"params": LIVE}, False), [])
            stale = {"params_default": dict(LIVE, threshold=0.9)}
            with self.assertRaises(SystemExit):
                site.check_freshness(rep, cur, stale, {"params": LIVE}, False)
            self.assertEqual(len(site.check_freshness(rep, cur, stale, {"params": LIVE}, True)), 1)
            os.utime(rep, (1_999_999_000, 1_999_999_000))
            with self.assertRaises(SystemExit):
                site.check_freshness(rep, cur, {"params_default": LIVE}, {"params": LIVE}, False)

    def test_render_inlines_core_and_data(self):
        core = curation_dashboard.CORE.read_text()
        self.assertIn("function applyGates", core)
        self.assertIn("function rank(", core)
        page = site.render({"city": "x", "shows": [], "sources": [], "params_default": LIVE, "live": {}})
        self.assertNotIn(curation_dashboard.CORE_MARKER, page)
        self.assertNotIn(curation_dashboard.MARKER, page)
        self.assertIn("function applyGates", page)
        self.assertIn("window.CURATION = {", page)
        self.assertIn('name="robots" content="noindex', page)
        # the dev dashboard goes through the same splice
        dev = curation_dashboard.inline_core(curation_dashboard.TEMPLATE.read_text())
        self.assertIn("function applyGates", dev)
        self.assertNotIn(curation_dashboard.CORE_MARKER, dev)
        self.assertEqual(dev.count("function applyGates"), 1)


if __name__ == "__main__":
    unittest.main()
