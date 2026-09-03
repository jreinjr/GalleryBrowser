"""rank_venues: determinism, feature bounds, gates, tiers, benchmark AUC — on a
synthetic registry so no real content is read. Stdlib unittest, no network.

    PYTHONPATH=scraper/tests:scraper scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rank_venues as rv  # noqa: E402
import venues  # noqa: E402

TODAY = date(2026, 9, 3)


def venue(vid, **kw):
    v = venues.empty_venue(vid, kw.pop("name", vid.replace("-", " ").title()))
    v.update(kw)
    return v


class FakeContext:
    """rank_venues.Context without disk: only the attributes compute_features reads."""

    def __init__(self, reg, fairs=None, lists=None, press=None, places=None, judge=None, wiki=None, seesaw=None):
        self.city = "test"
        self.registry = reg
        self.sources = {}
        self.fairs = fairs or {}
        self.lists = lists or {}
        self.press = press or {}
        self.places_by_pid = {}
        self.places_by_norm = places or {}
        self.judge = judge or {}
        self.wiki = wiki or {}
        self._ss = seesaw or (set(), set())

    def seesaw(self):
        return self._ss


def registry():
    big = venue("big-gallery", kind="gallery", status="active", website="https://big.example",
                exhibitions_url="https://big.example/exhibitions",
                google={"hours": ["Monday: Closed"] + [f"{d}: 10:00 AM – 6:00 PM" for d in
                                                       ("Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")] + ["Sunday: Closed"]},
                page={"date_strings": ["Sep 1 – Oct 4"], "fetch_mode": "static"},
                sources={"seed": {"gpla": {}, "carla": {}}})
    big["facts"].update({"founded_year": 1996, "roster_count": 30, "exhibitions_per_year": 8,
                         "locations_elsewhere": ["New York", "London"]})
    big["about"]["text"] = "A gallery."
    big["verification"]["status"] = "verified"
    small = venue("small-space", kind="project_space", status="active", hours=["Sat 12pm to 5pm", "or by appointment"])
    museum = venue("the-museum", kind="museum", status="active",
                   google={"hours": [f"{d}: 11:00 AM – 5:00 PM" for d in
                                     ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")]})
    dup = venue("dupe", kind="gallery", status="duplicate")
    closed = venue("gone", kind="gallery", status="closed")
    reg = {"schema": 2, "city": "test", "updated": 0, "venues": [big, small, museum, dup, closed]}
    for v in reg["venues"]:
        venues.sync_museum_flag(v)
    return reg


def context(reg, **kw):
    n = lambda vid: rv.venue_norms(venues.index_by_id(reg)[vid])  # noqa: E731
    fairs = {k: {"frieze-la", "art-basel", "felix"} for k in n("big-gallery")}
    lists = {k: {"adaa"} for k in n("big-gallery")}
    press = {k: {"carla"} for k in n("small-space")}
    places = {k: {"name": "Big Gallery", "rating": 4.6, "user_ratings_total": 180} for k in n("big-gallery")}
    judge = {"big-gallery": {"venue_judge_v1": {"claude-sonnet-5": {"overall": 8, "confidence": 0.7, "rationale": "x"}}}}
    wiki = {"big-gallery": {"venue_id": "big-gallery", "title": "Big Gallery", "sitelinks": 4}}
    return FakeContext(reg, fairs=fairs, lists=lists, press=press, places=places, judge=judge, wiki=wiki, **kw)


class Features(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.ctx = context(self.reg)
        self.params = rv.load_params(None, "test")

    def test_bounds_and_basis(self):
        for v in self.reg["venues"]:
            f, b, raw = rv.compute_features(v, self.ctx, self.params, TODAY)
            self.assertEqual(set(f), set(rv.FEATURES))
            for k, val in f.items():
                self.assertTrue(0.0 <= val <= 1.0, (v["id"], k, val))
                self.assertIsInstance(b[k], str)

    def test_big_gallery_evidence(self):
        v = venues.index_by_id(self.reg)["big-gallery"]
        f, b, raw = rv.compute_features(v, self.ctx, self.params, TODAY)
        self.assertEqual(f["fairs"], 1.0)                     # 3 fairs = ref
        self.assertGreater(f["curated_lists"], 0.6)
        self.assertEqual(f["directory"], 1.0)                 # gpla + carla
        self.assertGreater(f["longevity"], 0.9)               # 30 years
        self.assertGreater(f["hours_breadth"], 0.8)
        self.assertEqual(f["multi_location"], 1.0)
        self.assertEqual(f["venue_judge"], 0.8)
        self.assertAlmostEqual(f["web_presence"], 1.0)
        self.assertGreater(f["places_popularity"], 0.9)
        self.assertGreater(f["wiki"], 0.5)
        self.assertEqual((f["kind_gallery"], f["kind_museum"]), (1.0, 0.0))
        self.assertIn("frieze-la", b["fairs"])

    def test_small_space_and_museum(self):
        by = venues.index_by_id(self.reg)
        fs, _, _ = rv.compute_features(by["small-space"], self.ctx, self.params, TODAY)
        self.assertLess(fs["hours_breadth"], 0.2)
        self.assertGreater(fs["press"], 0)
        self.assertEqual(fs["kind_nonprofit"], 1.0)
        self.assertEqual(fs["longevity"], 0.0)
        fm, bm, _ = rv.compute_features(by["the-museum"], self.ctx, self.params, TODAY)
        self.assertEqual(fm["kind_museum"], 1.0)
        self.assertEqual(fm["hours_breadth"], 1.0)

    def test_list_weights_scale_fairs(self):
        p = json.loads(json.dumps(self.params))
        p["list_weights"] = {"frieze-la": 0.0, "art-basel": 0.0, "felix": 0.0}
        v = venues.index_by_id(self.reg)["big-gallery"]
        f, _, _ = rv.compute_features(v, self.ctx, p, TODAY)
        self.assertEqual(f["fairs"], 0.0)

    def test_seesaw_only_when_leaking(self):
        v = venues.index_by_id(self.reg)["big-gallery"]
        ctx = context(self.reg, seesaw=({"big"}, {"big"}))
        f, _, _ = rv.compute_features(v, ctx, self.params, TODAY)
        self.assertEqual(f["seesaw_presence"], 0.0)
        p = dict(self.params, leak_seesaw=True)
        f, _, _ = rv.compute_features(v, ctx, p, TODAY)
        self.assertEqual(f["seesaw_presence"], 1.0)

    def test_judge_model_selection(self):
        v = venues.index_by_id(self.reg)["big-gallery"]
        for model, expect in (("sonnet", 0.8), ("opus", 0.0), ("mean", 0.8), ("none", 0.0)):
            p = json.loads(json.dumps(self.params))
            p["judge"]["model"] = model
            f, _, _ = rv.compute_features(v, self.ctx, p, TODAY)
            self.assertEqual(f["venue_judge"], expect, model)


class Ranking(unittest.TestCase):
    def setUp(self):
        self.reg = registry()
        self.ctx = context(self.reg)
        self.params = rv.load_params(None, "test")

    def test_deterministic_and_ordered(self):
        a = rv.rank(self.ctx, self.params, TODAY)
        b = rv.rank(self.ctx, self.params, TODAY)
        self.assertEqual([r["id"] for r in a], [r["id"] for r in b])
        self.assertEqual([r["score"] for r in a], [r["score"] for r in b])
        scores = [r["score"] for r in a]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(a[0]["id"], "big-gallery")
        ranks = [r["rank"] for r in a if not r["gate"]]
        self.assertEqual(ranks, list(range(1, len(ranks) + 1)))

    def test_score_is_linear_in_weights(self):
        rows = rv.rank(self.ctx, self.params, TODAY)
        for r in rows:
            self.assertAlmostEqual(r["score"], sum(r["contrib"].values()))
            for f in rv.FEATURES:
                self.assertAlmostEqual(r["contrib"][f], self.params["weights"][f] * r["features"][f])

    def test_status_gates(self):
        rows = {r["id"]: r for r in rv.rank(self.ctx, self.params, TODAY)}
        self.assertEqual(rows["dupe"]["gate"], "status:duplicate")
        self.assertEqual(rows["gone"]["gate"], "status:closed")
        self.assertIsNone(rows["dupe"]["rank"])
        self.assertIsNone(rows["dupe"]["tier"])
        self.assertIsNone(rows["big-gallery"]["gate"])

    def test_require_verified_and_kind_gates(self):
        p = json.loads(json.dumps(self.params))
        p["gates"]["require_verified"] = True
        rows = {r["id"]: r for r in rv.rank(self.ctx, p, TODAY)}
        self.assertIsNone(rows["big-gallery"]["gate"])
        self.assertEqual(rows["small-space"]["gate"], "unverified")
        p["gates"]["require_verified"] = False
        p["gates"]["kinds"] = ["gallery"]
        rows = {r["id"]: r for r in rv.rank(self.ctx, p, TODAY)}
        self.assertEqual(rows["the-museum"]["gate"], "kind:museum")
        p["gates"]["kinds"] = []
        p["gates"]["min_score"] = 0.9
        rows = {r["id"]: r for r in rv.rank(self.ctx, p, TODAY)}
        self.assertTrue(all(r["gate"] == "below_min_score" for r in rows.values() if r["status"] == "active"))

    def test_tier_thresholds(self):
        p = json.loads(json.dumps(self.params))
        p["tiers"] = {"1": 0.5, "2": 0.2, "3": 0.05}
        rows = {r["id"]: r for r in rv.rank(self.ctx, p, TODAY)}
        self.assertEqual(rows["big-gallery"]["tier"], 1)
        self.assertIn(rows["small-space"]["tier"], (2, 3))
        self.assertEqual(rv.tier_for(0.049, p), None)
        self.assertEqual(rv.tier_for(0.5, p), 1)

    def test_museum_weight_negative_sinks_museum(self):
        rows = {r["id"]: r for r in rv.rank(self.ctx, self.params, TODAY)}
        self.assertLess(rows["the-museum"]["contrib"]["kind_museum"], 0)
        p = json.loads(json.dumps(self.params))
        p["weights"]["kind_museum"] = 0.0
        rows2 = {r["id"]: r for r in rv.rank(self.ctx, p, TODAY)}
        self.assertGreater(rows2["the-museum"]["score"], rows["the-museum"]["score"])

    def test_benchmark_auc(self):
        self.assertIsNone(rv.auc([], [1.0]))
        self.assertEqual(rv.auc([1.0, 0.9], [0.1, 0.2]), 1.0)
        self.assertEqual(rv.auc([0.5], [0.5]), 0.5)
        ctx = context(self.reg, seesaw=(set(), {"big"}))
        rows = rv.rank(ctx, self.params, TODAY)
        bm = rv.benchmark(rows, ctx)
        self.assertEqual(bm["seesaw_venue_ids"], ["big-gallery"])
        self.assertEqual(bm["auc"], 1.0)
        self.assertEqual(bm["seesaw_ranks"], [1])


class Params(unittest.TestCase):
    def test_defaults_cover_every_feature(self):
        p = rv.load_params(None, "test")
        self.assertEqual(set(p["weights"]), set(rv.FEATURES))
        self.assertEqual(p["city"], "test")
        self.assertFalse(p["leak_seesaw"])

    def test_layered_params_merge_per_key(self):
        base = rv.load_params(None, "test")
        override = {"weights": {"fairs": 0.9}, "tiers": {"1": 0.7}, "_note": "ignored"}
        merged = json.loads(json.dumps(base))
        rv._merge_params(merged, override)
        self.assertEqual(merged["weights"]["fairs"], 0.9)
        self.assertEqual(merged["weights"]["press"], base["weights"]["press"])
        self.assertEqual(merged["tiers"]["1"], 0.7)
        self.assertEqual(merged["tiers"]["2"], base["tiers"]["2"])
        self.assertNotIn("_note", merged)
        self.assertNotEqual(rv.params_hash(merged), rv.params_hash(base))


if __name__ == "__main__":
    unittest.main()
