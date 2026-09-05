"""Structured-output calls to Claude and OpenAI for the quick-city pipeline.

Both helpers take a system prompt, a user prompt and a JSON schema, and return
``(data, error, usage)``: the parsed object (or None), a short error string
(or None) and a usage dict ``{provider, model, requests, input_tokens,
output_tokens, cache_read_tokens, cost_usd, seconds}`` shaped like every other
ledger under content/spend/ (``save_spend`` writes it there).

    anthropic_json(system, user, schema)          # claude-opus-5, adaptive thinking, effort high
    openai_json(system, user, "name", schema)     # gpt-5.6-sol, Responses API, strict json_schema

The schema is normalised by ``strict_schema`` so the same dict works for both
providers: every object gets ``additionalProperties: false`` and lists all of
its properties in ``required`` (OpenAI strict mode demands it; Claude accepts it).
Keys come from the repo's .env via run_scrape.load_env().
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import harness
from run_scrape import load_env

ANTHROPIC_MODEL = "claude-opus-5"
OPENAI_MODEL = "gpt-5.6-sol"
# USD per million tokens (OpenAI promotional pricing, 2026-07).
OPENAI_MODELS = {
    "gpt-5.6-sol": {"in": 4.00, "out": 20.00, "cache_read": 0.40},
}
SPEND_DIR = harness.SPEND_DIR


def strict_schema(schema: dict) -> dict:
    """Deep copy with every object closed (additionalProperties false, all
    properties required) — the shape OpenAI strict mode insists on."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    out = copy.deepcopy(schema)
    walk(out)
    return out


def _usage(provider: str, model: str, inp: int, out: int, cached: int, cost: float, t0: float) -> dict:
    return {"provider": provider, "model": model, "requests": 1,
            "input_tokens": int(inp or 0), "output_tokens": int(out or 0),
            "cache_read_tokens": int(cached or 0), "cost_usd": round(cost, 6),
            "seconds": round(time.time() - t0, 1)}


def _empty_usage(provider: str, model: str, t0: float) -> dict:
    return _usage(provider, model, 0, 0, 0, 0.0, t0)


# --- Claude ----------------------------------------------------------------------

def anthropic_json(system: str, user: str, schema: dict, *, model: str = ANTHROPIC_MODEL,
                   effort: str = "high", max_tokens: int = 64000, client=None
                   ) -> tuple[dict | None, str | None, dict]:
    """One Messages call with json_schema output, streamed (long lists)."""
    t0 = time.time()
    if client is None:
        import anthropic
        load_env()
        client = anthropic.Anthropic(max_retries=3)
    req = {
        "model": model, "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": user}],
        "output_config": {"format": {"type": "json_schema", "schema": strict_schema(schema)}},
    }
    if harness.MODELS.get(model, {}).get("supports_effort", True):
        req["output_config"]["effort"] = effort
    try:
        with client.messages.stream(**req) as stream:
            msg = stream.get_final_message()
    except Exception as exc:  # noqa: BLE001 - the caller records the failure and moves on
        return None, f"api {type(exc).__name__}: {str(exc)[:200]}", _empty_usage("anthropic", model, t0)
    u = msg.usage
    price = harness.MODELS.get(model) or harness.MODELS[harness.DEFAULT_MODEL]
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    written = getattr(u, "cache_creation_input_tokens", 0) or 0
    cost = ((u.input_tokens or 0) * price["in"] + (u.output_tokens or 0) * price["out"]
            + cached * price["cache_read"] + written * price["cache_write"]) / 1e6
    usage = _usage("anthropic", model, u.input_tokens, u.output_tokens, cached, cost, t0)
    if msg.stop_reason == "refusal":
        return None, "refusal", usage
    if msg.stop_reason == "max_tokens":
        return None, "max_tokens", usage
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        return json.loads(text), None, usage
    except json.JSONDecodeError as exc:
        return None, f"json: {str(exc)[:120]}", usage


# --- OpenAI ------------------------------------------------------------------------

def openai_json(system: str, user: str, schema_name: str, schema: dict, *,
                model: str = OPENAI_MODEL, effort: str = "high", max_output_tokens: int = 64000,
                client=None) -> tuple[dict | None, str | None, dict]:
    """One Responses API call with a strict json_schema text format."""
    t0 = time.time()
    if client is None:
        import openai
        load_env()
        client = openai.OpenAI(max_retries=3)
    try:
        resp = client.responses.create(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            reasoning={"effort": effort},
            text={"format": {"type": "json_schema", "name": schema_name,
                             "schema": strict_schema(schema), "strict": True}},
            max_output_tokens=max_output_tokens,
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"api {type(exc).__name__}: {str(exc)[:200]}", _empty_usage("openai", model, t0)
    u = getattr(resp, "usage", None)
    inp = getattr(u, "input_tokens", 0) or 0
    out = getattr(u, "output_tokens", 0) or 0
    details = getattr(u, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    price = OPENAI_MODELS.get(model) or next(iter(OPENAI_MODELS.values()))
    cost = ((inp - cached) * price["in"] + cached * price["cache_read"] + out * price["out"]) / 1e6
    usage = _usage("openai", model, inp, out, cached, cost, t0)
    # a refusal arrives as an output item of type "refusal" inside the message
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") == "refusal":
                return None, f"refusal: {str(getattr(part, 'refusal', ''))[:120]}", usage
    if getattr(resp, "status", "completed") == "incomplete":
        reason = getattr(getattr(resp, "incomplete_details", None), "reason", None)
        return None, f"incomplete: {reason or 'unknown'}", usage
    text = getattr(resp, "output_text", "") or ""
    try:
        return json.loads(text), None, usage
    except json.JSONDecodeError as exc:
        return None, f"json: {str(exc)[:120]}", usage


# --- spend ---------------------------------------------------------------------------

def save_spend(label: str, usage: dict, extra: dict | None = None) -> Path:
    """content/spend/<label>.json in the shared ledger shape (run_scrape.spend_report sums it)."""
    SPEND_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"session": label, "model": usage.get("model"), "provider": usage.get("provider"),
           "requests": usage.get("requests", 1), "input_tokens": usage.get("input_tokens", 0),
           "output_tokens": usage.get("output_tokens", 0),
           "cache_read_tokens": usage.get("cache_read_tokens", 0),
           "cost_usd": round(float(usage.get("cost_usd") or 0.0), 4),
           "seconds": usage.get("seconds", 0), "ts": int(time.time()), **(extra or {})}
    p = SPEND_DIR / f"{label}.json"
    p.write_text(json.dumps(rec, indent=2))
    return p
