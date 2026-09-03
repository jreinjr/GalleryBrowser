"""lint_descriptions: schema handling with a stubbed client, rewrite floor,
apply = backup + write + venue hints. No network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lint_descriptions as lint  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402

DESC = ("Alice Smith fills the room with twelve new paintings of tide pools. "
        "The works are small and dense, made over two years on the Oregon coast.\n\n"
        "Founded in 1986, the gallery has been a home base for outsider art for decades. "
        "Smith's palette here is cooler than in her last show, and the hanging rewards slow looking, "
        "with every canvas given its own wall and a bench in the centre of the room for looking back.")


class Stub:
    """messages.create(**req) -> canned message per system prompt."""

    def __init__(self, lint_json, rewrite_json):
        self.lint_json, self.rewrite_json = lint_json, rewrite_json
        self.calls = []
        self.messages = self

    def create(self, **req):
        self.calls.append(req)
        body = self.lint_json if req["system"][0]["text"] == lint.LINT_SYSTEM else self.rewrite_json
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(body))],
            usage=SimpleNamespace(input_tokens=500, output_tokens=100,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0,
                                  server_tool_use=None))


class LintTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._content = tools.CONTENT_DIR
        self._venues = venues.VENUES_DIR
        self._store = lint.store.CURATION_DIR
        tools.CONTENT_DIR = root
        venues.VENUES_DIR = root / "venues"
        lint.store.CURATION_DIR = root / "curation"
        self.show = {"city": "los-angeles", "slug": "alice-tide", "title": "Tide", "artist": "Alice Smith",
                     "description": DESC, "venue": {"name": "Cactus Gallery", "neighborhood": "Eagle Rock"},
                     "venue_id": "cactus-gallery", "images": [], "source_urls": []}
        tools._write_shows_file(tools._city_file("los-angeles"), {"shows": [self.show]})
        tools._write_shows_file(tools._pending_file("los-angeles"), {"shows": []})
        with venues.locked_registry("los-angeles") as reg:
            reg["venues"].append(venues.empty_venue("cactus-gallery", "Cactus Gallery"))

    def tearDown(self):
        tools.CONTENT_DIR = self._content
        venues.VENUES_DIR = self._venues
        lint.store.CURATION_DIR = self._store
        self.tmp.cleanup()

    def _rewrite(self):
        return DESC.replace("Founded in 1986, the gallery has been a home base for outsider art for decades. ", "")

    def test_spans_must_be_verbatim_and_rewrite_kept(self):
        stub = Stub({"gallery_only_spans": [
                         {"text": "Founded in 1986, the gallery has been a home base for outsider art for decades.",
                          "kind": "history"},
                         {"text": "this sentence is not in the description", "kind": "other"}],
                     "show_tethered_mentions": 1, "confidence": 0.9},
                    {"description": self._rewrite()})
        rows, cached, meter = lint.run_lint("los-angeles", [self.show], "claude-sonnet-5", 2, False, client=stub)
        self.assertEqual(cached, 0)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual([s["kind"] for s in r["gallery_only_spans"]], ["history"])
        self.assertEqual(r["rewrite"], self._rewrite())
        self.assertEqual(len(stub.calls), 2)
        self.assertGreater(meter.dollars, 0)
        # second run is cached: no requests
        rows2, cached2, meter2 = lint.run_lint("los-angeles", [self.show], "claude-sonnet-5", 2, False, client=stub)
        self.assertEqual((cached2, meter2.requests, len(stub.calls)), (1, 0, 2))

    def test_low_confidence_skips_rewrite(self):
        stub = Stub({"gallery_only_spans": [{"text": "Founded in 1986, the gallery has been a home base for outsider art for decades.", "kind": "history"}],
                     "show_tethered_mentions": 0, "confidence": 0.3}, {"description": "x"})
        rows, _, _ = lint.run_lint("los-angeles", [self.show], "claude-sonnet-5", 1, False, client=stub)
        self.assertIsNone(rows[0]["rewrite"])
        self.assertEqual(len(stub.calls), 1)

    def test_rewrite_floor(self):
        self.assertFalse(lint.acceptable_rewrite("too short", DESC))
        self.assertFalse(lint.acceptable_rewrite(DESC + " longer", DESC))
        self.assertTrue(lint.acceptable_rewrite(self._rewrite(), DESC))

    def test_apply_backs_up_writes_and_banks_hints(self):
        span = "Founded in 1986, the gallery has been a home base for outsider art for decades."
        stub = Stub({"gallery_only_spans": [{"text": span, "kind": "history"}],
                     "show_tethered_mentions": 0, "confidence": 0.95}, {"description": self._rewrite()})
        rows, _, _ = lint.run_lint("los-angeles", [self.show], "claude-sonnet-5", 1, False, client=stub)
        res = lint.apply_rows("los-angeles", rows)
        self.assertEqual(res["applied"], 1)
        self.assertEqual(res["hints"], 1)
        backup = json.loads(Path(res["backup"]).read_text())
        self.assertEqual(backup["shows"][0]["description"], DESC)
        saved = tools._load_shows_file(tools._city_file("los-angeles"))["shows"][0]
        self.assertEqual(saved["description"], self._rewrite())
        v = venues.index_by_id(venues.load_registry("los-angeles"))["cactus-gallery"]
        self.assertEqual(v["about"]["hints"], [span])
        # idempotent
        self.assertEqual(lint.apply_rows("los-angeles", rows)["applied"], 0)


if __name__ == "__main__":
    unittest.main()
