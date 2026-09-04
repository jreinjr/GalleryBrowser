"""rank_venues: determinism, feature bounds, gates, tiers, benchmark AUC — on a
synthetic registry so no real content is read. Stdlib unittest, no network.

    PYTHONPATH=scraper/tests:scraper scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import unittest
from unittest import mock
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

    def test_manual_order_freezes_ranked_set(self):
        auto = rv.rank(self.ctx, self.params, TODAY)
        by = {r["id"]: r for r in auto}
        self.assertIsNone(by["big-gallery"]["gate"]); self.assertIsNone(by["small-space"]["gate"])
        self.assertEqual(by["the-museum"]["gate"], "below_min_score")
        # hand order: the weakest space first, then the (score-gated) museum — the list wins over score and gates
        params = dict(self.params, manual_order={"1": ["small-space"], "2": [], "3": ["the-museum"]})
        rows = rv.rank(self.ctx, params, TODAY)
        self.assertEqual([r["id"] for r in rows[:3]], ["small-space", "the-museum", "big-gallery"])
        self.assertEqual([(r["rank"], r["tier"], r["gate"]) for r in rows[:2]], [(1, 1, None), (2, 3, None)])
        self.assertEqual((rows[2]["rank"], rows[2]["tier"], rows[2]["gate"]), (3, None, None))   # outside the list = below tier 3
        self.assertTrue(all(r["gate"] and r["rank"] is None for r in rows[3:]))                    # status gates still apply
        # unknown ids are ignored, duplicates count once, a flat list is not a manual order
        params["manual_order"] = {"1": ["big-gallery", "big-gallery", "no-such-venue"], "2": [], "3": []}
        rows = rv.rank(self.ctx, params, TODAY)
        self.assertEqual([(r["id"], r["rank"]) for r in rows[:2]], [("big-gallery", 1), ("small-space", 2)])
        self.assertEqual(rv.manual_positions(self.params), {})
        self.assertEqual(rv.manual_positions({"manual_order": ["x"]}), {})

    def test_market_order_file_resolves_names_skips_gated_and_merges(self):
        order = {"city": "test", "tiers": {"1": 1, "2": 2},
                 "entries": [{"rank": 1, "name": "Small Space", "id": None},           # resolved by name
                             {"rank": 2, "name": "The Museum", "id": "the-museum"},
                             {"rank": 3, "name": "Gone", "id": "gone"},                 # status closed -> excluded
                             {"rank": 4, "name": "Nobody Here", "id": None},            # unresolved
                             {"rank": 5, "name": "Big Gallery", "id": "big-gallery"},
                             {"rank": 6, "name": "Big Gallery again", "id": "big-gallery"}]}   # merged into #5
        mo, res = rv.resolve_order(order, self.reg)
        self.assertEqual(mo, {"1": ["small-space"], "2": ["the-museum"], "3": ["big-gallery"]})
        self.assertEqual(res["ranked"], 3)
        self.assertEqual(res["unresolved"], [{"rank": 4, "name": "Nobody Here"}])
        self.assertEqual(res["excluded"], [{"rank": 3, "name": "Gone", "id": "gone", "status": "closed"}])
        self.assertEqual(res["merged"], [{"rank": 6, "name": "Big Gallery again", "id": "big-gallery", "same_as": 5}])
        # exclude_status from the params gates decides what is skipped
        mo2, res2 = rv.resolve_order(order, self.reg, exclude_status=[])
        self.assertIn("gone", mo2["3"])
        self.assertEqual(res2["excluded"], [])

    def test_market_order_applies_unless_params_set_manual_order(self):
        order = {"city": "test", "entries": [{"rank": 1, "name": "Small Space", "id": "small-space"}]}
        with mock.patch.object(rv, "load_order_file", return_value=order), \
             mock.patch.object(rv, "order_file", return_value=Path("/nonexistent/venue_order.json")):
            p = rv.load_params(None, "test")
            block = rv.apply_market_order(p, "test", self.reg)
            self.assertTrue(block["applied"])
            self.assertEqual(p["manual_order"], {"1": ["small-space"], "2": [], "3": []})
            self.assertEqual(p["manual_order_source"], "market")
            self.assertEqual((block["ranked"], block["n_entries"], block["tiers"]), (1, 1, {"1": 20, "2": 50}))
            rows = rv.rank(self.ctx, p, TODAY)
            self.assertEqual((rows[0]["id"], rows[0]["rank"], rows[0]["tier"]), ("small-space", 1, 1))
            self.assertEqual((rows[1]["id"], rows[1]["tier"]), ("big-gallery", None))
            # a params file that sets manual_order (even to null) wins over the market file
            p2 = rv.load_params(None, "test")
            rv._merge_params(p2, {"manual_order": None})
            self.assertEqual(p2["manual_order_source"], "params")
            self.assertIsNone(rv.apply_market_order(p2, "test", self.reg))
            self.assertIsNone(p2["manual_order"])
            # --no-order
            p3 = rv.load_params(None, "test")
            self.assertIsNone(rv.apply_market_order(p3, "test", self.reg, use_order=False))
            self.assertIsNone(p3["manual_order"])
        # no file at all
        with mock.patch.object(rv, "load_order_file", return_value=None):
            p4 = rv.load_params(None, "test")
            self.assertIsNone(rv.apply_market_order(p4, "test", self.reg))

    def test_parse_names_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "names.txt"
            f.write_text("# comment\nGagosian\n2|Meliksetian | Briggs|West Hollywood|design\n\n3|Pace|Mid-Wilshire|\n")
            rows = rv.parse_names_file(f)
        self.assertEqual(rows, [{"rank": 1, "name": "Gagosian", "neighborhood": None, "note": None},
                                {"rank": 2, "name": "Meliksetian | Briggs", "neighborhood": "West Hollywood", "note": "design"},
                                {"rank": 3, "name": "Pace", "neighborhood": "Mid-Wilshire", "note": None}])

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


class DuplicateRecords(unittest.TestCase):
    """venues.duplicate_pairs: same name merges, a branch never does."""

    def _reg(self, rows):
        return {"schema": 2, "city": "tokyo", "venues": rows}

    def _v(self, vid, name, site, rank=None):
        v = venues.empty_venue(vid, name)
        v["website"], v["rank"] = site, rank
        return v

    def test_same_name_spaced_differently_merges(self):
        rows = [self._v("shugoarts", "ShugoArts", "http://shugoarts.com/", 1),
                self._v("shugo-arts", "Shugo Arts", "http://shugoarts.com/", 50)]
        with mock.patch.object(venues, "load_registry", return_value=self._reg(rows)):
            merge, review = venues.duplicate_pairs("tokyo")
        self.assertEqual([(k["id"], d["id"]) for k, d in merge], [("shugoarts", "shugo-arts")])
        self.assertFalse(review)

    def test_branch_is_reported_not_merged(self):
        rows = [self._v("kotaro-nukaga", "KOTARO NUKAGA", "https://kotaronukaga.com", 1),
                self._v("kotaro-nukaga-tennoz", "KOTARO NUKAGA Tennoz", "https://kotaronukaga.com", 40)]
        with mock.patch.object(venues, "load_registry", return_value=self._reg(rows)):
            merge, review = venues.duplicate_pairs("tokyo")
        self.assertFalse(merge)
        self.assertEqual([(k["id"], d["id"]) for k, d in review],
                         [("kotaro-nukaga", "kotaro-nukaga-tennoz")])

    def test_acronym_merges(self):
        rows = [self._v("vincent-price-art-museum", "Vincent Price Art Museum", "https://vpam.org", 1),
                self._v("vpam", "VPAM", "https://vpam.org", 90)]
        with mock.patch.object(venues, "load_registry", return_value=self._reg(rows)):
            merge, _ = venues.duplicate_pairs("tokyo")
        self.assertEqual([d["id"] for _, d in merge], ["vpam"])
