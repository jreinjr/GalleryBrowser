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


# --- galleries mode ------------------------------------------------------------------

import re
import shutil
import subprocess

import venue_dashboard  # noqa: E402

VDEFAULT = json.loads((HERE.parent.parent / "content" / "curation" / "params" / "venues-default.json").read_text())
VPARAMS = {k: v for k, v in VDEFAULT.items() if not k.startswith("_")}


def _venue(vid, kind, score, rank, tier, gate=None, **extra):
    raw = {"hours": {"days_open": 5, "hours_per_week": 40.0, "by_appointment": False, "parsed": True}, "hours_status": "active",
           "fairs": ["frieze-la"] if kind == "gallery" else [], "lists": [], "press": [], "directories": ["gpla"],
           "years": 10, "founded_basis": "founded_year", "roster_count": 12, "exhibitions_per_year": 6.0, "n_locations": 0,
           "ratings": 40, "rating": 4.5, "web": {"exhibitions_url": True, "dates": True, "about": False, "fetch_mode": "static"},
           "judge": {}, "wiki": None, "kind": kind, "seesaw": None}
    feats = {f: 0.0 for f in site.G_FEATURES}
    feats.update({"hours_breadth": 0.89, "directory": 0.63, "longevity": 0.79, "roster_size": 0.84, "show_cadence": 1.0,
                  "places_popularity": 0.7, "web_presence": 0.7, "fairs": 0.5 if kind == "gallery" else 0.0,
                  "kind_gallery": 1.0 if kind == "gallery" else 0.0, "kind_museum": 1.0 if kind == "museum" else 0.0})
    v = {"id": vid, "name": vid.title(), "kind": kind, "is_museum": kind == "museum", "neighborhood": "Hollywood",
         "status": "active", "verification": "verified", "website": f"https://{vid}.example", "report_path": f"content/venues/reports/x/{vid}.json",
         "about": "A gallery. " * 100, "features": feats, "basis": {f: "b" for f in site.G_FEATURES}, "raw": raw,
         "score": score, "contrib": {}, "gate": gate, "rank": rank, "tier": tier}
    v.update(extra)
    return v


VREPORT = {"city": "los-angeles", "generated_at": 1788495195, "today": "2026-09-03", "params_default": dict(VPARAMS, city="los-angeles", today="2026-09-03"),
           "params_hash": "abc123def456", "feature_names": site.G_FEATURES,
           "venues": [_venue("alpha", "gallery", 0.62, 1, 1), _venue("beta", "museum", 0.21, 2, 3),
                      _venue("gone", "gallery", 0.4, None, None, gate="status:closed", status="closed")],
           "benchmark": {"seesaw_venue_ids": ["alpha"], "n_seesaw": 1, "auc": 1.0}, "presets": {}, "counts": {}}


class GalleryTrimTests(unittest.TestCase):
    def test_trim_venue_keeps_evidence_adds_live_and_drops_internals(self):
        out = site.trim_venue(VREPORT["venues"][0])
        for k in site.G_VENUE_KEYS:
            self.assertIn(k, out)
        for k in ("features", "basis", "contrib", "report_path", "score", "rank", "tier", "gate"):
            self.assertNotIn(k, out)                       # recomputed in the page from raw
        self.assertEqual((out["rank_live"], out["score_live"], out["tier_live"], out["gate_live"]), (1, 0.62, 1, None))
        self.assertLessEqual(len(out["about"]), site.ABOUT_MAX + 1)
        self.assertTrue(out["about"].endswith("…"))
        for k in site.G_EMPTY_RAW_KEYS:
            self.assertNotIn(k, out["raw"])                # {} / None dropped
        self.assertEqual(out["raw"]["fairs"], ["frieze-la"])
        self.assertIsNone(site.trim_venue(dict(VREPORT["venues"][0], about=None))["about"])

    def test_build_galleries_drops_status_gated_and_counts_coverage(self):
        g = site.build_galleries("los-angeles", VREPORT)
        self.assertEqual([v["id"] for v in g["venues"]], ["alpha", "beta"])
        self.assertEqual(g["counts"]["status_gated"], 1)
        self.assertEqual(g["counts"]["listed"], 2)
        self.assertEqual(g["counts"]["tiers"], {"1": 1, "3": 1})
        self.assertEqual(g["coverage"]["fairs"], 1)
        self.assertEqual(g["coverage"]["venue_judge"], 0)
        self.assertEqual(g["client_paths"], site.G_CLIENT_PATHS)
        self.assertEqual(g["params_default"]["city"], "los-angeles")
        self.assertEqual(g["params_hash"], "abc123def456")
        self.assertNotIn("benchmark", g)
        self.assertNotIn("presets", g)

    def test_g_constants_match_venue_core(self):
        core = venue_dashboard.CORE.read_text()
        m = re.search(r"const FEATURES = \[(.*?)\];", core, re.S)
        self.assertEqual(re.findall(r"'([a-z_]+)'", m.group(1)), site.G_FEATURES)
        m = re.search(r"const PARAM_KEY_ORDER = \[(.*?)\];", core)
        self.assertEqual(re.findall(r"'([a-z_]+)'", m.group(1)), site.G_PARAM_KEY_ORDER)
        m = re.search(r"const DEEP_KEYS = new Set\(\[(.*?)\]\)", core)
        self.assertEqual(tuple(re.findall(r"'([a-z_]+)'", m.group(1))), site.G_DEEP_MERGE_KEYS)
        for name in site.G_EXPORTS:
            self.assertRegex(core, rf"(const|function) {name}\b", name)
        self.assertEqual(len(site.G_CLIENT_PATHS), 5 + 18 - len(site.G_HIDDEN_WEIGHTS) + 1)   # + manual_order

    def test_payload_carries_galleries_and_default_mode(self):
        report = {"today": "2026-09-03", "generated_at": "g", "sources": [], "shows": [REPORT_ROW], "params_default": LIVE}
        curated = {"params_hash": "abc", "ts": 1, "params": LIVE, "featured": []}
        g = site.build_galleries("los-angeles", VREPORT)
        payload = site.build_payload("los-angeles", report, curated, {}, {}, "https://s", "me@x", galleries=g)
        self.assertEqual(payload["default_mode"], "galleries")
        self.assertEqual(len(payload["galleries"]["venues"]), 2)
        payload = site.build_payload("los-angeles", report, curated, {}, {}, "https://s", "me@x")
        self.assertEqual(payload["default_mode"], "shows")
        self.assertEqual(payload["galleries"], {})


class GalleryLinkTests(unittest.TestCase):
    def test_parse_fragment(self):
        self.assertEqual(site.parse_fragment("https://x.test/#p=AAA&g=BBB&v=shows"), {"p": "AAA", "g": "BBB", "v": "shows"})
        self.assertEqual(site.parse_fragment("#g=BBB"), {"g": "BBB"})
        self.assertEqual(site.parse_fragment("#v=shows"), {"v": "shows"})
        self.assertEqual(site.parse_fragment("https://x.test/?q=1#g=BBB&other=2"), {"g": "BBB"})
        self.assertEqual(site.parse_fragment("AAA"), {"p": "AAA"})          # legacy bare token
        self.assertEqual(site.parse_fragment("p=AAA"), {"p": "AAA"})
        self.assertEqual(site.parse_fragment("https://x.test/"), {})
        self.assertEqual(site.token_from_link("#p=AAA&g=BBB"), "AAA")
        with self.assertRaises(ValueError):
            site.token_from_link("#g=BBB")

    def test_gallery_diff_merge_round_trip(self):
        kinds = ["gallery", "nonprofit", "project_space", "university", "other"]
        diff = {"gates": {"kinds": kinds}, "tiers": {"1": 0.6}, "weights": {"kind_museum": -0.3}}
        tok = site.encode_diff(diff)
        full = site.decode_gallery_link(tok, VREPORT["params_default"])
        self.assertEqual(full["gates"]["kinds"], kinds)
        self.assertEqual(full["gates"]["exclude_status"], VPARAMS["gates"]["exclude_status"])   # untouched gate keys survive
        self.assertEqual(full["gates"]["require_verified"], False)
        self.assertEqual(full["tiers"], {"1": 0.6, "2": 0.30, "3": 0.10})
        self.assertEqual(full["weights"]["kind_museum"], -0.3)
        self.assertEqual(full["weights"]["fairs"], 0.18)
        self.assertEqual(full["refs"], VPARAMS["refs"])
        self.assertEqual(list(full)[:4], ["version", "city", "today", "weights"])
        self.assertEqual(site.diff_params(VREPORT["params_default"], full, site.G_CLIENT_PATHS), diff)
        empty = site.decode_gallery_link(site.encode_diff({"gates": {"kinds": []}}), dict(VREPORT["params_default"], gates=dict(VPARAMS["gates"], kinds=kinds)))
        self.assertEqual(empty["gates"]["kinds"], [])

    def test_gallery_manual_order_round_trip(self):
        order = {"1": ["pace", "gagosian-beverly-hills"], "2": [], "3": ["box"]}
        tok = site.encode_diff({"manual_order": order})
        full = site.decode_gallery_link(tok, VREPORT["params_default"])
        self.assertEqual(full["manual_order"], order)
        self.assertEqual(list(full)[-1], "manual_order")
        self.assertEqual(site.diff_params(VREPORT["params_default"], full, site.G_CLIENT_PATHS), {"manual_order": order})
        self.assertIn("manual_order", site.G_CLIENT_PATHS)

    def test_gallery_freshness(self):
        reg = {"venues": [{"id": "a", "notability_breakdown": {"params_hash": "abc123def456"}}, {"id": "b", "notability_breakdown": {"params_hash": "abc123def456"}}]}
        self.assertEqual(site.check_gallery_freshness(VREPORT, reg, False), [])
        stale = {"venues": [{"id": "a", "notability_breakdown": {"params_hash": "other"}}]}
        with self.assertRaises(SystemExit):
            site.check_gallery_freshness(VREPORT, stale, False)
        self.assertEqual(len(site.check_gallery_freshness(VREPORT, stale, True)), 1)
        with self.assertRaises(SystemExit):
            site.check_gallery_freshness(VREPORT, {"venues": [{"id": "a"}]}, False)

    def test_render_inlines_both_cores(self):
        g = site.build_galleries("los-angeles", VREPORT)
        page = site.render({"city": "x", "shows": [], "sources": [], "params_default": LIVE, "live": {}, "galleries": g})
        for marker in (curation_dashboard.CORE_MARKER, site.GALLERY_CORE_MARKER, curation_dashboard.MARKER):
            self.assertNotIn(marker, page)
        self.assertIn("const G = (function () {", page)
        self.assertIn("window.VENUES = (window.CURATION || {}).galleries", page)
        self.assertEqual(page.count("function computeFeatures("), 2)
        self.assertEqual(page.count("function applyGates("), 1)
        self.assertEqual(page.count("function gateFor("), 1)
        self.assertIn('"galleries":{', page)
        # the dev gallery dashboard still splices the bare core
        dev = venue_dashboard.inline_core(venue_dashboard.TEMPLATE.read_text())
        self.assertNotIn(venue_dashboard.CORE_MARKER, dev)
        self.assertNotIn("const G = (function", dev)


@unittest.skipUnless(shutil.which("node"), "node not installed")
class JsParityTests(unittest.TestCase):
    """The gallery core exactly as shipped (IIFE-wrapped, fed the trimmed payload) must
    reproduce rank_venues.py's scores/ranks/tiers for every listed venue."""

    REPORT = HERE.parent.parent / "content" / "curation" / "los-angeles" / "venues_ranked.json"

    def _run(self, vreport):
        g = site.build_galleries(vreport["city"], vreport)
        js = ("globalThis.window = {CURATION: {galleries: " + json.dumps(g) + "}};\n" + site.gallery_core_js() +
              "\nconsole.log(JSON.stringify(G.rank(G.VENUES, G.R.params_default).map(r => [r.id, r.score, r.rank, r.tier, r.gate])));\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "parity.js"
            p.write_text(js)
            out = subprocess.run(["node", str(p)], capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return json.loads(out.stdout)

    def test_fixture_parity(self):
        rows = self._run(VREPORT)
        self.assertEqual([r[0] for r in rows], ["alpha", "beta"])
        # alpha: hours .8909*.10 + fairs sat(1,3)*.18 + directory sat(1,2)*.05 + longevity sat(10,20)*.08
        #        + roster sat(12,20)*.06 + cadence 1*.06 + places .7002*.05 + web .7*.04 = 0.4472 -> tier 2
        self.assertAlmostEqual(rows[0][1], 0.4472, places=3)
        self.assertEqual(rows[0][2:], [1, 2, None])
        # beta: same minus fairs, plus kind_museum -0.15 = 0.2072 -> tier 3
        self.assertAlmostEqual(rows[1][1], 0.2072, places=3)
        self.assertEqual(rows[1][2:], [2, 3, None])

    @unittest.skipUnless(REPORT.exists(), "no LA venues_ranked.json")
    def test_gallery_core_matches_python_report(self):
        vreport = json.loads(self.REPORT.read_text())
        rows = self._run(vreport)
        py = {v["id"]: v for v in vreport["venues"]}
        self.assertEqual(len(rows), sum(1 for v in vreport["venues"] if not str(v.get("gate") or "").startswith("status:")))
        worst = 0.0
        for vid, score, rank, tier, gate in rows:
            v = py[vid]
            worst = max(worst, abs(score - v["score"]))
            self.assertEqual((rank, tier, gate), (v["rank"], v["tier"], v["gate"]), vid)
        self.assertLess(worst, 1e-9)


if __name__ == "__main__":
    unittest.main()
