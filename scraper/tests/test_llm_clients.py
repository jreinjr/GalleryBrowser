"""llm_clients: schema normalisation, both providers with fake clients, cost math.

    scraper/.venv/bin/python -m unittest discover -s scraper/tests
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_clients as lc  # noqa: E402

SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {
    "type": "object", "properties": {"name": {"type": "string"}, "n": {"type": "integer"}}}}}}


class StrictSchema(unittest.TestCase):
    def test_every_object_is_closed_and_fully_required(self):
        s = lc.strict_schema(SCHEMA)
        self.assertFalse(s["additionalProperties"])
        self.assertEqual(s["required"], ["items"])
        item = s["properties"]["items"]["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["required"], ["name", "n"])
        self.assertNotIn("required", SCHEMA)   # input untouched


class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


def fake_anthropic(text, stop="end_turn", inp=1000, out=2000, cached=500):
    msg = NS(stop_reason=stop, content=[NS(type="text", text=text)],
             usage=NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cached,
                      cache_creation_input_tokens=0))
    calls = []

    def stream(**req):
        calls.append(req)
        return _Stream(msg)
    return NS(messages=NS(stream=stream)), calls


class AnthropicJson(unittest.TestCase):
    def test_parses_and_prices(self):
        client, calls = fake_anthropic(json.dumps({"items": [{"name": "a", "n": 1}]}))
        data, err, usage = lc.anthropic_json("sys", "user", SCHEMA, client=client)
        self.assertIsNone(err)
        self.assertEqual(data["items"][0]["name"], "a")
        req = calls[0]
        self.assertEqual(req["model"], "claude-opus-5")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["output_config"]["effort"], "high")
        self.assertFalse(req["output_config"]["format"]["schema"]["additionalProperties"])
        # 1000 in * 5 + 2000 out * 25 + 500 cache read * 0.5 per MTok
        self.assertAlmostEqual(usage["cost_usd"], (1000 * 5 + 2000 * 25 + 500 * 0.5) / 1e6, places=6)
        self.assertEqual(usage["provider"], "anthropic")

    def test_refusal_and_truncation(self):
        client, _ = fake_anthropic("", stop="refusal")
        data, err, _u = lc.anthropic_json("s", "u", SCHEMA, client=client)
        self.assertIsNone(data)
        self.assertEqual(err, "refusal")
        client, _ = fake_anthropic("{\"items\": [", stop="max_tokens")
        data, err, _u = lc.anthropic_json("s", "u", SCHEMA, client=client)
        self.assertEqual(err, "max_tokens")

    def test_api_error_is_returned_not_raised(self):
        def boom(**req):
            raise RuntimeError("down")
        client = NS(messages=NS(stream=boom))
        data, err, usage = lc.anthropic_json("s", "u", SCHEMA, client=client)
        self.assertIsNone(data)
        self.assertTrue(err.startswith("api RuntimeError"))
        self.assertEqual(usage["cost_usd"], 0.0)


def fake_openai(text, status="completed", inp=1000, out=2000, cached=200, refusal=None):
    parts = [NS(type="refusal", refusal=refusal)] if refusal else [NS(type="output_text", text=text)]
    resp = NS(output_text=text, status=status, output=[NS(type="message", content=parts)],
              usage=NS(input_tokens=inp, output_tokens=out, input_tokens_details=NS(cached_tokens=cached)),
              incomplete_details=None)
    calls = []

    def create(**req):
        calls.append(req)
        return resp
    return NS(responses=NS(create=create)), calls


class OpenAIJson(unittest.TestCase):
    def test_parses_and_prices(self):
        client, calls = fake_openai(json.dumps({"items": []}))
        data, err, usage = lc.openai_json("sys", "user", "items", SCHEMA, client=client)
        self.assertIsNone(err)
        self.assertEqual(data, {"items": []})
        req = calls[0]
        self.assertEqual(req["model"], "gpt-5.6-sol")
        self.assertEqual(req["reasoning"], {"effort": "high"})
        fmt = req["text"]["format"]
        self.assertEqual((fmt["type"], fmt["name"], fmt["strict"]), ("json_schema", "items", True))
        self.assertEqual(fmt["schema"]["required"], ["items"])
        # (1000-200) in * 4 + 200 cached * 0.4 + 2000 out * 20 per MTok
        self.assertAlmostEqual(usage["cost_usd"], (800 * 4 + 200 * 0.4 + 2000 * 20) / 1e6, places=6)

    def test_refusal_and_incomplete(self):
        client, _ = fake_openai("", refusal="no")
        data, err, _u = lc.openai_json("s", "u", "x", SCHEMA, client=client)
        self.assertIsNone(data)
        self.assertTrue(err.startswith("refusal"))
        client, _ = fake_openai("{", status="incomplete")
        data, err, _u = lc.openai_json("s", "u", "x", SCHEMA, client=client)
        self.assertTrue(err.startswith("incomplete"))


class Spend(unittest.TestCase):
    def test_ledger_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = lc.SPEND_DIR
            lc.SPEND_DIR = Path(tmp)
            try:
                p = lc.save_spend("quick-seed-x-1", {"provider": "openai", "model": "gpt-5.6-sol", "requests": 1,
                                                     "input_tokens": 10, "output_tokens": 20,
                                                     "cache_read_tokens": 0, "cost_usd": 0.12345, "seconds": 3},
                                  {"stage": "seed"})
                rec = json.loads(p.read_text())
            finally:
                lc.SPEND_DIR = saved
        self.assertEqual(rec["session"], "quick-seed-x-1")
        self.assertEqual(rec["cost_usd"], 0.1235)
        self.assertEqual(rec["stage"], "seed")
        self.assertIn("ts", rec)


if __name__ == "__main__":
    unittest.main()
