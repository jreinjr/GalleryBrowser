// POST /api/discover — the Discover chat endpoint. Streams server-sent events:
//   status {phase,label}   text {delta}   list {title,kind,entries,suggestions,dropped}
//   route {weekday,start_label,end_label,total_km,stops,skipped}   note {message}
//   done {usage,cost_usd,...}   error {code,message,retryable}
// The Anthropic key lives in the ANTHROPIC_API_KEY env var of the Vercel
// project; the browser never sees it. Body and guards: _lib/guards.js.

import Anthropic from '@anthropic-ai/sdk';
import { createHash } from 'node:crypto';
import { cities, getCorpus } from './_lib/corpus.js';
import { checkOrigin, clientIp, rateLimit, validateBody } from './_lib/guards.js';
import { runDiscover, config } from './_lib/agent.js';

const json = (status, obj, extra) => new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json', ...(extra || {}) } });

export function GET() {
  return json(200, { ok: true, cities: cities(), model: config().model });
}

export async function POST(request) {
  const originProblem = checkOrigin(request);
  if (originProblem) return json(403, { error: originProblem });
  let raw;
  try { raw = await request.json(); } catch (e) { return json(400, { error: 'invalid JSON' }); }
  const v = validateBody(raw, cities());
  if (v.error) return json(400, { error: v.error });
  const ip = clientIp(request);
  const rl = rateLimit(ip);
  if (!rl.ok) return json(429, { error: rl.reason }, { 'Retry-After': String(rl.retryAfter) });
  if (!process.env.ANTHROPIC_API_KEY) return json(503, { error: 'Discover is not configured (no API key)' });

  const corpus = getCorpus(v.body.city);
  const body = { ...v.body, userId: createHash('sha256').update(ip).digest('hex').slice(0, 32) };
  const todayOverride = process.env.DISCOVER_EVAL === '1' ? (request.headers.get('x-discover-today') || null) : null;

  const enc = new TextEncoder();
  let controller, closed = false, ping = null;
  const stream = new ReadableStream({
    start(ctl) { controller = ctl; },
    cancel() { closed = true; if (ping) clearInterval(ping); },
  });
  const write = s => { if (closed) return; try { controller.enqueue(enc.encode(s)); } catch (e) { closed = true; } };
  const send = (event, data) => write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const close = () => { if (closed) return; closed = true; if (ping) clearInterval(ping); try { controller.close(); } catch (e) { /* already closed */ } };
  ping = setInterval(() => write(': ping\n\n'), 10000);

  // Return the response first; the loop runs inside the stream.
  runDiscover({ corpus, body, send, signal: request.signal, todayOverride })
    .catch(e => {
      const retryable = e instanceof Anthropic.RateLimitError || e instanceof Anthropic.APIConnectionError || (e instanceof Anthropic.APIError && e.status >= 500);
      const code = e instanceof Anthropic.RateLimitError ? 'rate_limited' : e instanceof Anthropic.APIError && e.status >= 500 ? 'overloaded' : e instanceof Anthropic.APIError ? 'bad_request' : e && e.name === 'AbortError' ? 'aborted' : 'failed';
      console.error('discover failed', code, e && e.message);
      send('error', { code, message: retryable ? 'Discover is busy right now.' : "Couldn't answer that just now.", retryable, detail: process.env.DISCOVER_DEBUG === '1' ? String(e && e.message) : undefined });
    })
    .finally(close);

  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache, no-transform', 'X-Accel-Buffering': 'no', Connection: 'keep-alive' },
  });
}
