// The Discover agent loop: one streamed Messages request per iteration,
// custom tools executed here, server tools (web search / fetch) run by the
// API, present_list as the terminal tool. Text deltas and tool status are
// forwarded to the client as they happen through `send(event, data)`.
//
// Why a manual loop rather than the SDK tool runner: the SSE relay, the
// container-id echo the 20260209 web tools need, pause_turn, and stopping the
// loop on present_list without another API call.

import Anthropic from '@anthropic-ai/sdk';
import { toolDefs, runTool, statusLabel } from './tools.js';
import { systemText, contextMessage } from './prompt.js';
import { nowIn } from './corpus.js';

export const DEFAULT_MODEL = 'claude-opus-5';
const MAX_ITERATIONS = 8;
const MAX_TOKENS = 16000;
// $/MTok: input, output, cache read, cache write (5m = 1.25x, 1h = 2x input)
const PRICES = {
  'claude-opus-5': [5, 25, 0.5], 'claude-opus-4-8': [5, 25, 0.5], 'claude-sonnet-5': [2, 10, 0.2],
  'claude-fable-5-1': [10, 50, 0.25],
};
const TIME_RE = /\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b(mon|tues|wednes|thurs|fri|satur|sun)day\b|\b\d{1,2}:\d{2}\b/i;

let client = null;
function getClient() {
  if (!client) client = new Anthropic({ maxRetries: 2, timeout: 110_000 });
  return client;
}

// Cache TTL: the system prefix (prompt + catalog + tools) is ~20k tokens. A
// 5-minute entry costs 1.25x to write and every question within five minutes
// refreshes it; the 1-hour entry costs 2x and only pays off when questions
// arrive 5-60 minutes apart. DISCOVER_CACHE_TTL=1h switches.
export function config() {
  return {
    model: process.env.DISCOVER_MODEL || DEFAULT_MODEL,
    effort: process.env.DISCOVER_EFFORT || 'medium',
    cacheTtl: process.env.DISCOVER_CACHE_TTL === '1h' ? '1h' : '5m',
  };
}

function historyMessages(history) {
  return history.map(t => {
    if (t.role === 'user') return { role: 'user', content: t.text };
    let text = t.text;
    if (t.presented && t.presented.ids.length) {
      text += `\n\n[presented list "${t.presented.title}" (${t.presented.kind}): ${t.presented.ids.join(', ')}; dropped: ${t.presented.dropped.length ? t.presented.dropped.join(', ') : 'none'}]`;
    }
    return { role: 'assistant', content: text };
  });
}

// present_list validation: ids must exist, notes carry no times, cap 40.
export function validateList(c, input) {
  const seen = new Set(); const entries = []; const dropped = [];
  for (const e of Array.isArray(input.entries) ? input.entries : []) {
    const raw = String(e && e.show_id || '').trim();
    const id = raw.replace(/^[a-z-]+\//, '');
    if (!c.showsById.has(id)) { if (raw) dropped.push(raw); continue; }
    if (seen.has(id)) continue;
    seen.add(id);
    let note = typeof e.note === 'string' ? e.note.trim() : null;
    if (note && (TIME_RE.test(note) || note.length > 140)) note = note.length > 140 ? note.slice(0, 137) + '…' : null;
    entries.push({ id: `${c.city}/${id}`, note: note || null });
    if (entries.length >= 40) break;
  }
  const suggestions = (Array.isArray(input.suggestions) ? input.suggestions : []).filter(s => typeof s === 'string' && s.trim()).map(s => s.trim().slice(0, 80)).slice(0, 3);
  return { title: String(input.title || 'Shows').slice(0, 120), kind: input.kind === 'route' ? 'route' : 'list', entries, suggestions, dropped };
}

function cost(model, u, cacheTtl) {
  const p = PRICES[model] || PRICES[DEFAULT_MODEL];
  const write = p[0] * (cacheTtl === '1h' ? 2 : 1.25);
  return ((u.input_tokens || 0) * p[0] + (u.output_tokens || 0) * p[1] + (u.cache_read_input_tokens || 0) * p[2] + (u.cache_creation_input_tokens || 0) * write) / 1e6;
}

export async function runDiscover({ corpus: c, body, send, signal, now = new Date(), todayOverride = null }) {
  const { model, effort, cacheTtl } = config();
  const t = nowIn(c.tz, now);
  const today = todayOverride || t.date;
  const tools = toolDefs(c);
  const system = [{ type: 'text', text: systemText(c, today), cache_control: cacheTtl === '1h' ? { type: 'ephemeral', ttl: '1h' } : { type: 'ephemeral' } }];
  const ctxText = contextMessage(c, { ...body.ctx, today }, now);
  let messages = [...historyMessages(body.history), { role: 'user', content: body.question }, { role: 'system', content: ctxText }];
  let systemAsUserBlock = false;   // fallback when the model rejects role:system
  const usage = { input_tokens: 0, output_tokens: 0, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 };
  let containerId = null;
  let iterations = 0, nudged = false, sawHits = false, webUses = 0;
  const routeResults = [];
  const client = getClient();

  const request = async (quiet) => {
    const params = {
      model, max_tokens: MAX_TOKENS, system, tools, messages,
      output_config: { effort },
      metadata: body.userId ? { user_id: body.userId } : undefined,
    };
    if (/^claude-(opus-5|fable)/.test(model)) { params.betas = ['server-side-fallback-2026-07-01']; params.fallbacks = 'default'; }
    if (containerId) params.container = containerId;
    const stream = client.beta.messages.stream(params, { signal });
    for await (const ev of stream) {
      if (ev.type === 'content_block_start' && ev.content_block && ev.content_block.type === 'server_tool_use') {
        webUses += 1;
        send('status', { phase: 'web', label: statusLabel(ev.content_block.name, {}) });
      } else if (ev.type === 'content_block_delta' && ev.delta && ev.delta.type === 'text_delta' && !quiet) {
        send('text', { delta: ev.delta.text });
      }
    }
    return stream.finalMessage();
  };

  for (;;) {
    iterations += 1;
    if (iterations > MAX_ITERATIONS) { send('error', { code: 'timeout', message: 'That took too many steps. Try a narrower question.', retryable: true }); break; }
    send('status', { phase: 'think', label: iterations === 1 ? 'Thinking' : 'Reading results' });
    let final;
    try {
      final = await request(nudged);
    } catch (e) {
      if (e instanceof Anthropic.BadRequestError && /container/i.test(e.message) && containerId) { containerId = null; iterations -= 1; continue; }
      if (e instanceof Anthropic.BadRequestError && /role 'system'|role "system"|system.*not supported/i.test(e.message) && !systemAsUserBlock) {
        systemAsUserBlock = true; iterations -= 1;
        messages = messages.filter(m => m.role !== 'system');
        const last = messages.findLastIndex(m => m.role === 'user');
        const u = messages[last];
        messages[last] = { role: 'user', content: [{ type: 'text', text: typeof u.content === 'string' ? u.content : body.question }, { type: 'text', text: `<context>\n${ctxText}\n</context>` }] };
        continue;
      }
      throw e;
    }
    for (const k of Object.keys(usage)) usage[k] += final.usage && final.usage[k] || 0;
    if (final.container && final.container.id) containerId = final.container.id;

    if (final.stop_reason === 'refusal') {
      send('error', { code: 'refusal', message: "I can't help with that one.", retryable: false });
      break;
    }
    if (final.stop_reason === 'pause_turn') {
      messages.push({ role: 'assistant', content: final.content });
      continue;
    }
    const toolUses = final.content.filter(b => b.type === 'tool_use');
    const present = [...toolUses].reverse().find(b => b.name === 'present_list');
    if (present) {
      const list = validateList(c, present.input || {});
      if (list.entries.length) {
        send('list', list);
        if (list.kind === 'route') {
          const ids = list.entries.map(e => e.id.replace(/^[a-z-]+\//, ''));
          const rt = [...routeResults].reverse().find(r => r.stops && ids.every(id => r.stops.some(s => s.id === id)));
          if (rt) send('route', { ...rt, stops: ids.map(id => rt.stops.find(s => s.id === id)) });
        }
      } else if (list.dropped.length) {
        send('note', { message: "I couldn't match those shows to the data." });
      }
      break;
    }
    if (final.stop_reason !== 'tool_use' || !toolUses.length) {
      // end_turn (or max_tokens) without a list; nudge once when a search found shows
      if (!nudged && sawHits && final.stop_reason === 'end_turn') {
        nudged = true;
        messages.push({ role: 'assistant', content: final.content });
        messages.push({ role: 'user', content: '[harness] If your reply recommends specific shows, call present_list with their ids now. Otherwise reply with a single period.' });
        continue;
      }
      if (final.stop_reason === 'max_tokens') send('note', { message: 'Cut short.' });
      break;
    }
    messages.push({ role: 'assistant', content: final.content });
    const results = [];
    for (const tu of toolUses) {
      send('status', { phase: 'tool', name: tu.name, label: statusLabel(tu.name, tu.input) });
      const r = runTool(c, tu.name, tu.input, today);
      if (tu.name === 'find_shows' && !r.isError) { try { if (JSON.parse(r.content).count > 0) sawHits = true; } catch (e) { /* ignore */ } }
      if (tu.name === 'plan_route' && !r.isError) { try { routeResults.push(JSON.parse(r.content)); } catch (e) { /* ignore */ } }
      results.push({ type: 'tool_result', tool_use_id: tu.id, content: r.content, is_error: r.isError || undefined });
    }
    messages.push({ role: 'user', content: results });
  }
  send('done', { iterations, web_uses: webUses, nudged, model, effort, cache_ttl: cacheTtl, usage, cost_usd: Math.round(cost(model, usage, cacheTtl) * 10000) / 10000 });
}
