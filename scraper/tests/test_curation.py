"""Unit tests for the curation layer (stdlib unittest).

    scraper/.venv/bin/python -m unittest discover -s scraper/tests

Nothing here touches content/: the store is redirected to a temp directory
for the record_signal test, and every other test works on in-memory pools.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import curate  # noqa: E402
import curation_store as store  # noqa: E402
import seesaw_snapshot  # noqa: E402
import tools  # noqa: E402
from curation_keywords import keyword_feature, scan_keywords  # noqa: E402

TODAY = date(2026, 9, 1)


def show(slug, venue, artist=None, title="Untitled", start="2026-08-15", end="2026-10-15",
         museum=False, neighborhood="Downtown/Arts District", desc_words=100, n_images=3):
    return {
        "city": "los-angeles", "slug": slug, "title": title, "artist": artist,
        "start_date": start, "end_date": end,
        "description": " ".join(["word"] * desc_words),
        "editors_pick": False, "featured": False, "reception": None,
        "images": [f"images/los-angeles/{slug}/{i:02d}.jpg" for i in range(1, n_images + 1)],
        "source_urls": [],
        "venue": {"name": venue, "is_museum": museum, "address": "1 Main St",
                  "address_detail": None, "neighborhood": neighborhood, "hours": [],
                  "phone": None, "website": None, "latitude": 34.0, "longitude": -118.0},
    }


POOL = [
    show("wonzimer-jane-doe", "Wönzimer", artist="Jane Doe", title="Night Fields"),
    show("parrasch-heijnen-charles-dickson", "Parrasch Heijnen", artist="Charles Dickson",
         title="Charles Dickson: Select Works, 1966-2026"),
    show("lacma-group", "Los Angeles County Museum of Art (LACMA)", artist=None,
         title="Imagining Black Diasporas", museum=True, neighborhood="Mid-Wilshire/Koreatown"),
    show("charlie-james-perez-bros", "Charlie James Gallery", artist="The Perez Bros",
         title="Lookout Weekend", neighborhood="Chinatown/East LA"),
    show("hammer-collection", "Hammer Museum", artist=None,
         title="SPACE IS THE PLACE: Selections from the Hammer Contemporary Collection",
         museum=True, neighborhood="Westside/Brentwood"),
]


class MatcherTests(unittest.TestCase):
    def match(self, venue, artist=None, title=None, **kw):
        return store.match_show_ref({"venue": venue, "artist": artist, "title": title, **kw}, POOL, {})

    def test_exact_venue_only(self):
        m = self.match("Parrasch Heijnen")
        self.assertEqual(m.slug, "parrasch-heijnen-charles-dickson")
        self.assertEqual((m.method, m.confidence, m.venue_via), ("venue_only", 0.70, "exact"))

    def test_diacritic_variant_resolves_at_exact_stage(self):
        m = self.match("Wonzimer Gallery", artist="Jane Doe")
        self.assertEqual(m.slug, "wonzimer-jane-doe")
        self.assertEqual(m.method, "venue+artist")
        self.assertEqual(m.confidence, 0.95)

    def test_same_distinctive_words_after_place_words(self):
        pool = POOL + [show("vielmetter-ongoingness", "Vielmetter Los Angeles", title="Ongoingness")]
        m = store.match_show_ref({"venue": "Vielmetter", "artist": None, "title": "Ongoingness"}, pool, {})
        self.assertEqual(m.slug, "vielmetter-ongoingness")
        self.assertEqual(m.venue_via, "distinctive_words")
        # one shared word is NOT enough when the other side has more distinctive words
        m2 = store.match_show_ref({"venue": "Charlie", "artist": None, "title": None}, pool, {})
        self.assertIsNone(m2.slug)

    def test_branch_label_does_not_hide_the_main_space(self):
        # See Saw labels the venue with its annex; the main space's show must
        # still be found (and a title holding the artist's name still matches).
        pool = POOL + [show("marc-selwyn-wegman", "Marc Selwyn Fine Art", artist="William Wegman",
                            title="Private Show"),
                       show("lee-mullican-silent-shades", "Marc Selwyn Fine Art (Camden Downstairs Annex)",
                            artist="Lee Mullican", title="Silent Shades")]
        m = store.match_show_ref({"venue": "Marc Selwyn Fine Art | Camden Annex", "artist": None,
                                  "title": "William Wegman"}, pool, {})
        self.assertEqual(m.slug, "marc-selwyn-wegman")
        m1 = store.match_show_ref({"venue": "Marc Selwyn Fine Art | Camden Annex", "artist": None,
                                   "title": "Lee Mullican"}, pool, {})
        self.assertEqual(m1.slug, "lee-mullican-silent-shades")
        # an unrelated title at the same venue is still a different show
        m2 = store.match_show_ref({"venue": "Marc Selwyn Fine Art", "artist": None,
                                   "title": "Sculptures of the Desert"}, pool, {})
        self.assertIsNone(m2.slug)

    def test_fuzzy_venue_ratio(self):
        m = self.match("Parrasch Heijen", artist="Charles Dickson")   # typo
        self.assertEqual(m.slug, "parrasch-heijnen-charles-dickson")
        self.assertEqual(m.venue_via, "fuzzy_ratio")

    def test_shared_distinctive_words(self):
        m = self.match("Charlie James Gallery in Chinatown", artist="Perez Bros")
        self.assertEqual(m.slug, "charlie-james-perez-bros")
        self.assertEqual(m.venue_via, "shared_words")
        self.assertEqual(m.method, "venue+artist")

    def test_parenthetical_alias(self):
        m = self.match("LACMA", title="Imagining Black Diasporas")
        self.assertEqual(m.slug, "lacma-group")
        self.assertEqual(m.method, "venue+title")
        self.assertEqual(m.confidence, 0.90)

    def test_venue_plus_title(self):
        m = self.match("Wönzimer", title="Night Fields")
        self.assertEqual((m.slug, m.method), ("wonzimer-jane-doe", "venue+title"))

    def test_title_only_line_tries_artist(self):
        m = self.match("Parrasch Heijnen", title="Charles Dickson")
        self.assertEqual(m.slug, "parrasch-heijnen-charles-dickson")

    def test_venue_in_pool_different_show_is_candidate(self):
        m = self.match("Wönzimer", artist="Someone Else", title="Completely Other")
        self.assertIsNone(m.slug)
        self.assertTrue(m.venue_in_pool)
        self.assertEqual(m.method, "candidate")
        self.assertEqual(m.candidate_key, "wonzimer|someone else")

    def test_no_venue_hit_candidate_key(self):
        m = self.match("Nonexistent Space", artist="Ana Lopez")
        self.assertIsNone(m.slug)
        self.assertFalse(m.venue_in_pool)
        self.assertEqual(m.candidate_key, "nonexistent space|ana lopez")

    def test_no_venue_at_all(self):
        m = self.match("", artist="Ana Lopez", title="Vestiges")
        self.assertIsNone(m.slug)
        self.assertEqual(m.candidate_key, "|ana lopez")

    def test_artist_title_without_venue_hit(self):
        m = self.match("Some Other Name", artist="Jane Doe", title="Night Fields")
        self.assertEqual((m.slug, m.method, m.confidence), ("wonzimer-jane-doe", "artist_title", 0.80))

    def test_agent_hint_rejected_on_venue_mismatch(self):
        m = self.match("Nonexistent Space", artist="Ana Lopez", agent_pool_slug="wonzimer-jane-doe")
        self.assertIsNone(m.slug)
        self.assertEqual(m.hint_rejected, "wonzimer-jane-doe")

    def test_agent_hint_accepted_same_venue(self):
        m = self.match("Wönzimer", artist="Someone Else", title="Other", agent_pool_slug="wonzimer-jane-doe")
        self.assertEqual((m.slug, m.method), ("wonzimer-jane-doe", "agent_hint"))
        self.assertIsNone(m.hint_rejected)


class ScoringTests(unittest.TestCase):
    def setUp(self):
        self.params = json.loads(json.dumps(curate.DEFAULT_PARAMS))

    def test_zero_signal_features(self):
        s = show("x", "Some Gallery", start="2026-08-25", end="2026-09-10", desc_words=250, n_images=5)
        feats, detail = curate.compute_features(s, curate.empty_context(), self.params, TODAY)
        self.assertEqual(set(feats), set(curate.FEATURES))
        self.assertEqual(feats["press"], 0.0)
        self.assertEqual(feats["artist_heat"], 0.0)
        self.assertEqual(feats["judge"], 0.0)
        self.assertEqual(feats["wiki_heat"], 0.0)
        self.assertEqual(feats["venue"], 0.2)         # tier unknown floor
        self.assertEqual(feats["museum"], 0.0)
        self.assertEqual(feats["closing_soon"], 1.0)  # closes in 9 days
        self.assertAlmostEqual(feats["opening_recency"], 0.5 ** (7 / 30))
        self.assertEqual(feats["quality"], 1.0)
        self.assertTrue(detail["judge"]["missing"])
        score, contrib = curate.score_show(feats, self.params)
        self.assertAlmostEqual(score, sum(contrib.values()))
        self.assertAlmostEqual(contrib["venue"], 0.15 * 0.2)

    def test_museum_outranks_gallery_at_zero_signals(self):
        m = show("m", "Big Museum", museum=True)
        g = show("g", "Small Gallery")
        fm, _ = curate.compute_features(m, curate.empty_context(), self.params, TODAY)
        fg, _ = curate.compute_features(g, curate.empty_context(), self.params, TODAY)
        self.assertEqual(fm["venue"], 0.3)   # museum fallback beats the unknown floor
        self.assertGreater(curate.score_show(fm, self.params)[0], curate.score_show(fg, self.params)[0])

    def test_decay_and_cap(self):
        self.assertEqual(curate.decay(0, 21), 1.0)
        self.assertAlmostEqual(curate.decay(21, 21), 0.5)
        self.assertAlmostEqual(curate.decay(42, 21), 0.25)
        ctx = curate.empty_context()
        ctx.sources = {"carla": {"id": "carla", "weight": 1.0}}
        sig = lambda i, days: {"id": f"s{i}", "kind": "pick", "strength": "headline",  # noqa: E731
                               "source": {"id": "carla", "url": "u"}, "published_at": (TODAY.toordinal() - days) and date.fromordinal(TODAY.toordinal() - days).isoformat()}
        fresh = [sig(1, 0)]
        self.assertAlmostEqual(curate.signal_weight(fresh[0], self.params, ctx, TODAY, 21), 1.0)
        old = sig(2, 21)
        self.assertAlmostEqual(curate.signal_weight(old, self.params, ctx, TODAY, 21), 0.5)
        # cap_per_source: five headline picks from one source cap at 1.5 raw
        many = [sig(i, 0) for i in range(5)]
        feat, raw, per = curate.aggregate_signals(many, curate.PRESS_KINDS, self.params, ctx, TODAY, 21, 4)
        self.assertAlmostEqual(raw, 1.5)
        self.assertAlmostEqual(feat, curate.math.log2(2.5) / curate.math.log2(5))
        # saturation cap 1.25 with many sources
        ctx.sources = {f"s{i}": {"id": f"s{i}", "weight": 1.0} for i in range(20)}
        lots = [{"id": f"x{i}", "kind": "pick", "strength": "headline", "source": {"id": f"s{i}", "url": "u"},
                 "published_at": TODAY.isoformat()} for i in range(20)]
        feat, raw, _ = curate.aggregate_signals(lots, curate.PRESS_KINDS, self.params, ctx, TODAY, 21, 4)
        self.assertEqual(feat, 1.25)

    def test_press_feature_via_context(self):
        ctx = curate.empty_context()
        ctx.sources = {"carla": {"id": "carla", "weight": 1.0}}
        ctx.signals["x"] = [{"id": "a", "kind": "review", "strength": "headline", "snippet": "a retrospective",
                             "source": {"id": "carla", "url": "u"}, "published_at": TODAY.isoformat(), "match": {}}]
        s = show("x", "Some Gallery")
        feats, detail = curate.compute_features(s, ctx, self.params, TODAY)
        self.assertAlmostEqual(feats["press"], curate.math.log2(1.9) / curate.math.log2(5))
        self.assertEqual(feats["keyword"], 1.0)   # snippet keyword hit
        self.assertEqual(detail["keyword_hits"][0]["class"], "retrospective_survey")

    def test_keyword_lexicon(self):
        hits = scan_keywords("Her first museum solo exhibition and a newly commissioned site-specific work; a retrospective. retrospective again.")
        classes = {h["class"] for h in hits}
        self.assertIn("first_solo", classes)
        self.assertIn("commissioned", classes)
        self.assertIn("retrospective_survey", classes)
        self.assertEqual(keyword_feature(hits), 1.0)
        self.assertEqual(keyword_feature(scan_keywords("new works on paper")), 0.3)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.params = json.loads(json.dumps(curate.DEFAULT_PARAMS))
        self.params["threshold"] = 0.0
        self.params["max_n"] = 10
        self.params["editors_pick_top"] = 2
        self.pool = [
            show("m1", "Museum One", museum=True, neighborhood="A"),
            show("m2", "Museum Two", museum=True, neighborhood="A"),
            show("g1", "Gallery One", neighborhood="A"),
            show("g2", "Gallery Two", neighborhood="B"),
            show("g3", "Gallery Three", neighborhood="B"),
            show("pending", "Gallery Four", start="2026-10-20", end="2026-12-01", neighborhood="C"),
            show("ended", "Gallery Five", start="2026-06-01", end="2026-08-20", neighborhood="C"),
        ]

    def featured(self, params):
        ranked = curate.rank(self.pool, params, TODAY)
        return [r["slug"] for r in sorted((r for r in ranked if r["featured"]), key=lambda r: r["featured_rank"])], ranked

    def test_window_gate_and_rank_order(self):
        feat, ranked = self.featured(self.params)
        gates = {r["slug"]: r["gate"] for r in ranked}
        self.assertEqual(gates["pending"], "out_of_window")
        self.assertEqual(gates["ended"], "out_of_window")
        self.assertEqual(feat[:2], ["m1", "m2"])     # museums on top at zero signals
        self.assertEqual([r["rank"] for r in ranked], list(range(1, 8)))
        picks = [r["slug"] for r in ranked if r["editors_pick"]]
        self.assertEqual(picks, ["m1", "m2"])
        self.assertTrue(all(r["reasons"] for r in ranked))

    def test_include_pending(self):
        p = {**self.params, "include_pending": True}
        feat, ranked = self.featured(p)
        self.assertIn("pending", feat)

    def test_published_only_gate(self):
        """The built site reads the published file only, so a pending-pool show
        must not win a slot the Featured tab can never render."""
        published = {"g1", "g2", "g3", "m1", "m2"}          # 'soon' is pending-pool
        pool = self.pool + [show("soon", "Gallery Six", neighborhood="D")]
        ranked = curate.rank(pool, {**self.params, "published_only": True}, TODAY, None, published)
        gates = {r["slug"]: r["gate"] for r in ranked}
        self.assertEqual(gates["soon"], "not_published")
        self.assertIsNone(gates["g1"])
        # off by default: the same pool with the gate down features it
        ranked = curate.rank(pool, self.params, TODAY, None, published)
        self.assertIn("soon", [r["slug"] for r in ranked if r["featured"]])

    def test_require_open_gate(self):
        """in_window admits shows opening within OPEN_WINDOW_DAYS; require_open
        keeps the feed to what a visitor can actually walk into today."""
        opens_soon = show("opens_soon", "Gallery Six", start="2026-09-05",
                          end="2026-11-01", neighborhood="D")
        undated = show("undated", "Gallery Seven", neighborhood="E")
        undated["start_date"] = None
        pool = self.pool + [opens_soon, undated]
        ranked = curate.rank(pool, self.params, TODAY)
        self.assertIsNone({r["slug"]: r["gate"] for r in ranked}["opens_soon"])   # off by default
        ranked = curate.rank(pool, {**self.params, "require_open": True}, TODAY)
        rows = {r["slug"]: r for r in ranked}
        self.assertEqual(rows["opens_soon"]["gate"], "not_open")
        days = (date.fromisoformat("2026-09-05") - TODAY).days
        self.assertIn(f"opens in {days} d", rows["opens_soon"]["gate_detail"])
        self.assertEqual(rows["undated"]["gate"], "not_open")
        self.assertIsNone(rows["g1"]["gate"])

    def test_threshold_gate(self):
        p = {**self.params, "threshold": 0.9}
        feat, ranked = self.featured(p)
        self.assertEqual(feat, [])
        self.assertTrue(all(r["gate"] in ("below_threshold", "out_of_window") for r in ranked))

    def test_exclude_museums(self):
        p = {**self.params, "exclude_museums": True}
        feat, ranked = self.featured(p)
        self.assertNotIn("m1", feat)
        self.assertEqual(next(r["gate"] for r in ranked if r["slug"] == "m1"), "exclude_museums")
        self.assertEqual(feat, ["g1", "g2", "g3"])

    def test_max_per_neighborhood(self):
        p = {**self.params, "max_per_neighborhood": 2}
        feat, ranked = self.featured(p)
        # A holds m1, m2, g1 -> g1 gated; B holds g2, g3 -> both survive
        self.assertEqual(feat, ["m1", "m2", "g2", "g3"])
        self.assertEqual(next(r["gate"] for r in ranked if r["slug"] == "g1"), "max_per_neighborhood")

    def test_max_museum_share(self):
        p = {**self.params, "max_museum_share": 0.25}   # floor(0.25 * 10) = 2 museums allowed
        feat, _ = self.featured(p)
        self.assertEqual(feat[:2], ["m1", "m2"])
        p["max_museum_share"] = 0.1                       # floor(1) = 1
        feat, ranked = self.featured(p)
        self.assertEqual(feat[0], "m1")
        self.assertNotIn("m2", feat)
        self.assertEqual(next(r["gate"] for r in ranked if r["slug"] == "m2"), "max_museum_share")

    def test_gate_order_per_venue_before_museum(self):
        # two shows at one venue: the second must be gated by max_per_venue, not by the museum gates
        self.pool.append(show("m1b", "Museum One", museum=True, neighborhood="A", desc_words=10, n_images=1))
        p = {**self.params, "exclude_museums": False, "max_per_neighborhood": None}
        feat, ranked = self.featured(p)
        self.assertEqual(next(r["gate"] for r in ranked if r["slug"] == "m1b"), "max_per_venue")

    def test_pin_and_exclude_and_max_n(self):
        p = json.loads(json.dumps(self.params))
        p["overrides"] = {"pin": ["g3"], "exclude": ["m1"]}
        p["max_n"] = 2
        feat, ranked = self.featured(p)
        self.assertEqual(feat, ["g3", "m2"])
        self.assertEqual(next(r["gate"] for r in ranked if r["slug"] == "m1"), "excluded")
        self.assertEqual(next(r["gate"] for r in ranked if r["slug"] == "g1"), "max_n")


class SeesawTests(unittest.TestCase):
    def test_metrics_and_commonality(self):
        params = json.loads(json.dumps(curate.DEFAULT_PARAMS))
        params["threshold"] = 0.0
        params["max_n"] = 2
        ranked = curate.rank(POOL, params, TODAY)
        snap = {"id": "t", "entries": [
            {"position": 1, "venue": "Hammer Museum", "artist": None, "title": None},
            {"position": 2, "venue": "Wönzimer", "artist": "Jane Doe", "title": None},
            {"position": 3, "venue": "Unknown Space", "artist": "Nobody", "title": None},
        ]}
        index = store.PoolIndex(POOL)
        ss = curate.seesaw_metrics(ranked, snap, lambda ref: store.match_show_ref(ref, index, {}))
        m = ss["metrics"]
        self.assertEqual(m["n_snapshot"], 3)
        self.assertEqual(m["n_top"], 2)
        self.assertAlmostEqual(m["pool_coverage"], 2 / 3)
        self.assertEqual(len(ss["misses"]["not_in_pool"]), 1)
        self.assertEqual(m["tp"] + len(ss["misses"]["in_pool_below_cutoff"]), 2)
        com = curate.seesaw_commonality(ranked, ss)
        self.assertEqual(com["n_matched"], 2)
        names = {r["name"] for r in com["rows"]}
        self.assertIn("museum share", names)


class TextParserTests(unittest.TestCase):
    def test_forms(self):
        p = seesaw_snapshot.parse_line
        self.assertEqual(p("Jane Doe — Night Fields @ Wönzimer"),
                         {"venue": "Wönzimer", "artist": "Jane Doe", "title": "Night Fields", "raw": "Jane Doe — Night Fields @ Wönzimer"})
        self.assertEqual(p("Jane Doe - Night Fields @ Wönzimer")["artist"], "Jane Doe")
        e = p("Night Fields @ Wönzimer")
        self.assertEqual((e["venue"], e["artist"], e["title"]), ("Wönzimer", None, "Night Fields"))
        e = p("Hammer Museum: Sun Ra")
        self.assertEqual((e["venue"], e["artist"], e["title"]), ("Hammer Museum", "Sun Ra", None))
        e = p("Hammer Museum: Sun Ra — Space Is the Place")
        self.assertEqual((e["venue"], e["artist"], e["title"]), ("Hammer Museum", "Sun Ra", "Space Is the Place"))
        e = p("3. The Broad")
        self.assertEqual((e["venue"], e["artist"], e["title"]), ("The Broad", None, None))
        self.assertIsNone(p("# comment"))
        self.assertIsNone(p("   "))

    def test_parse_text_positions(self):
        entries = seesaw_snapshot.parse_text("A @ V1\n\n# c\nV2: B\n")
        self.assertEqual([e["position"] for e in entries], [1, 2])


class RecordSignalTests(unittest.TestCase):
    def test_record_dedupe_and_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir, old_events = store.CURATION_DIR, tools.EVENTS_FILE
            store.CURATION_DIR = Path(tmp) / "curation"
            tools.EVENTS_FILE = Path(tmp) / "events.jsonl"
            try:
                args = {"city": "los-angeles", "source_id": "carla", "source_url": "https://x/1",
                        "published_at": "2026-08-30", "kind": "review", "strength": "headline",
                        "venue": "Wönzimer", "artist": "Jane Doe", "title": None,
                        "snippet": "s" * 400, "agent_pool_slug": None}
                r1 = json.loads(store.record_signal(args, "los-angeles", "run-1", "pubsweep_v1", "m", pool=POOL, registry={}))
                self.assertTrue(r1["recorded"])
                self.assertEqual(r1["match"]["slug"], "wonzimer-jane-doe")
                self.assertEqual(r1["recorded_this_session"], 1)
                self.assertIn("truncated", r1["note"])
                r2 = json.loads(store.record_signal(args, "los-angeles", "run-1", "pubsweep_v1", "m", pool=POOL, registry={}))
                self.assertFalse(r2["recorded"])
                self.assertEqual(r2["signal_id"], r1["signal_id"])
                r3 = json.loads(store.record_signal(args, "los-angeles", "run-2", "pubsweep_v1", "m", pool=POOL, registry={}))
                self.assertTrue(r3["recorded"])          # other run: accumulates
                rows = store.load_signals("los-angeles")
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["dedupe_key"], rows[1]["dedupe_key"])
                self.assertNotEqual(rows[0]["id"], rows[1]["id"])
                self.assertEqual(len(rows[0]["snippet"]), 300)
                self.assertEqual(len(store.collapse_signals(rows)), 1)
                cand = json.loads(store.record_signal({**args, "venue": "Nowhere", "artist": "Nobody", "snippet": "x"},
                                                      "los-angeles", "run-2", "pubsweep_v1", "m", pool=POOL, registry={}))
                self.assertIn("candidate", cand)
                self.assertEqual(cand["candidate"]["key"], "nowhere|nobody")
                self.assertEqual(list(store.load_candidates("los-angeles")), ["nowhere|nobody"])
                self.assertEqual(len(store.load_matches("los-angeles")), 3)
                with self.assertRaises(ValueError):
                    store.record_signal({**args, "kind": "bogus"}, "los-angeles", "run-3", None, None, pool=POOL, registry={})
                with self.assertRaises(ValueError):
                    store.record_signal({**args, "strength": "loud"}, "los-angeles", "run-3", None, None, pool=POOL, registry={})
                # hint rejection is logged as an event, not raised
                rej = json.loads(store.record_signal({**args, "venue": "Nowhere Else", "artist": "Q", "agent_pool_slug": "wonzimer-jane-doe"},
                                                     "los-angeles", "run-3", None, None, pool=POOL, registry={}))
                self.assertIn("ignored", rej["note"])
                self.assertTrue(tools.EVENTS_FILE.exists())
            finally:
                store.CURATION_DIR, tools.EVENTS_FILE = old_dir, old_events


if __name__ == "__main__":
    unittest.main()
