/* Discover eval: real API calls through the function handler, checked
 * against the corpus the same tools use. Costs money (about $1.50 for the
 * full set on Opus 5). Run after build.py:
 *   node webdemo/tests/discover.eval.mjs [--only 1,3] [--effort low] [--model claude-sonnet-5] [--today 2026-09-03]
 * Today is pinned (default 2026-09-03) through the x-discover-today header,
 * which the function honors only with DISCOVER_EVAL=1.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadEnv, parseSSE } from './lib/env.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const envFile = loadEnv(path.join(HERE, '..', '..'));
process.env.DISCOVER_EVAL = '1';
process.env.DISCOVER_ALLOW_NO_ORIGIN = '1';
process.env.DISCOVER_DEBUG = '1';
const args = process.argv.slice(2);
const opt = k => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : null; };
if (opt('--effort')) process.env.DISCOVER_EFFORT = opt('--effort');
if (opt('--model')) process.env.DISCOVER_MODEL = opt('--model');
const TODAY = opt('--today') || '2026-09-03';
const ONLY = opt('--only') ? new Set(opt('--only').split(',').map(Number)) : null;

const { POST } = await import('../api/discover.js');
const { getCorpus, findShows, weekendOf, addDays, showStatus, hoursOn } = await import('../api/_lib/corpus.js');
const c = getCorpus('los-angeles');
const wk = weekendOf(TODAY);
const ids = r => (r.list ? r.list.entries.map(e => e.id.replace(/^[a-z-]+\//, '')) : []);
const subset = (a, b) => a.every(x => b.includes(x));
const TIME_RE = /\b\d{1,2}(:\d{2})?\s*(am|pm)\b/i;

const CASES = [
  { n: 1, q: "What's closing this week?", check: r => {
    const exp = findShows(c, { closing_by: addDays(TODAY, 7), limit: 40 }, TODAY).hits.map(h => h.id);
    return [r.list && ids(r).length >= 5 && subset(ids(r), exp) && /clos/i.test(r.text), `${ids(r).length} entries ⊆ ${exp.length} closing`]; } },
  { n: 2, q: 'Opening receptions this weekend', check: r => {
    const exp = findShows(c, { reception_from: wk.from, reception_to: wk.to, status: 'any', limit: 40 }, TODAY).hits;
    const expIds = exp.map(h => h.id);
    const odd = exp.filter(h => h.reception_kind !== 'opening' && ids(r).includes(h.id));
    const named = !odd.length || /talk|conversation|closing|not an opening|event/i.test(r.text);
    return [r.list && ids(r).length >= 3 && subset(ids(r), expIds) && named, `${ids(r).length} ⊆ ${expIds.length}; non-openings ${odd.length} named=${named}`]; } },
  { n: 3, q: 'Plan a Saturday in the Arts District', check: r => {
    const inHood = ids(r).every(id => (c.showsById.get(id) || {}).neighborhood === 'Downtown/Arts District');
    const openSat = ids(r).every(id => { const s = c.showsById.get(id); const h = s && hoursOn(c.venues[s.venueId] || {}, 'sat'); return h && (!h.known || h.open); });
    const routeOk = r.route && r.route.stops.map(s => s.id).join() === ids(r).join();
    const noTimes = r.list && r.list.entries.every(e => !e.note || !TIME_RE.test(e.note));
    return [r.list && r.list.kind === 'route' && ids(r).length >= 3 && ids(r).length <= 6 && inHood && openSat && routeOk && noTimes,
      `${ids(r).length} stops, kind ${r.list && r.list.kind}, inHood ${inHood}, openSat ${openSat}, routeEvent ${!!r.route} matches ${routeOk}, noTimes ${noTimes}`]; } },
  { n: 4, q: 'Current shows by queer artists in the Hollywood neighborhood', check: r => {
    const allHolly = ids(r).length > 0 && ids(r).every(id => (c.showsById.get(id) || {}).neighborhood === 'Hollywood');
    const honest = /Hollywood/.test(r.text) && /\bno\b|nothing|none|widen|across|elsewhere|all of|outside/i.test(r.text);
    return [allHolly || honest, `allHollywood ${allHolly}, honest ${honest}`]; } },
  { n: 5, q: 'Ceramics in Chinatown', check: r => {
    const exp = findShows(c, { query: 'ceramics', neighborhoods: ['Chinatown/East LA'], limit: 40 }, TODAY);
    const ok = (r.list && subset(ids(r), exp.hits.map(h => h.id))) || /Chinatown/.test(r.text) && /\bno\b|nothing|none|widen|across|elsewhere|only/i.test(r.text);
    return [ok, `${ids(r).length} entries; strict ${exp.strict_count}`]; } },
  { n: 6, q: "What's on at Jeffrey Deitch?", check: r => [ids(r).length >= 1 && ids(r).every(id => (c.showsById.get(id) || {}).venueId === 'jeffrey-deitch') && ids(r).includes('deitch-urs-fischer') && r.done.iterations <= 2, `${ids(r).join(',')} in ${r.done.iterations} iterations`] },
  { n: 7, q: 'Anything with Ed Ruscha?', check: r => [ids(r).includes('bel-ami-cars-of-los-angeles') || /Cars of Los Angeles|Bel Ami/.test(r.text), ids(r).join(',')] },
  { n: 8, q: 'Museum shows on view now', check: r => [ids(r).length >= 5 && ids(r).every(id => { const s = c.showsById.get(id); return s && s.venueKind === 'museum' && showStatus(s, TODAY) === 'on_view'; }), `${ids(r).length} entries`] },
  { n: 9, q: 'Tell me about Urs Fischer', check: r => [(!r.list || ids(r).every(id => /fischer/i.test((c.showsById.get(id) || {}).artist || ''))) && r.text.split(/\s+/).length <= 160, `${r.text.split(/\s+/).length} words, list ${!!r.list}, web ${r.done.web_uses}`] },
  // an honest "nothing" may still offer the nearest thing as a list
  { n: 10, q: 'Shows about submarines', check: r => [/\bno\b|nothing|none|couldn't|not/i.test(r.text) && (!r.list || ids(r).length <= 3) && r.done.iterations <= 3, `list ${!!r.list} (${ids(r).length}), ${r.done.iterations} iterations`] },
  { n: 11, q: 'Top galleries with shows opening this month', check: r => {
    const ok = ids(r).length > 0 && ids(r).every(id => { const s = c.showsById.get(id); return s && s.galleryTier === 'top'; });
    return [ok, `${ids(r).length} entries, all top ${ok}`]; } },
  { n: 12, q: 'What are the hours at Vielmetter on Saturday?', check: r => [/10\s*(am|:00)/i.test(r.text) && /6\s*(pm|:00)/i.test(r.text) && !r.list, `text: ${r.text.slice(0, 120)}`] },
];

async function ask(question, history) {
  const req = new Request('http://localhost/api/discover', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'x-discover-today': TODAY },
    body: JSON.stringify({ city: 'los-angeles', question, history: history || [], filterSummary: 'Galleries · Featured · Active' }),
  });
  const t0 = Date.now();
  const res = await POST(req);
  const text = await res.text();
  const events = parseSSE(text);
  const r = { status: res.status, text: '', list: null, route: null, done: null, error: null, statuses: [], ms: Date.now() - t0 };
  for (const e of events) {
    if (e.event === 'text') r.text += e.data.delta;
    else if (e.event === 'list') r.list = e.data;
    else if (e.event === 'route') r.route = e.data;
    else if (e.event === 'done') r.done = e.data;
    else if (e.event === 'error') r.error = e.data;
    else if (e.event === 'status') r.statuses.push(e.data.label);
  }
  return r;
}

console.log(`env ${envFile || 'none'} · model ${process.env.DISCOVER_MODEL || 'claude-opus-5'} · effort ${process.env.DISCOVER_EFFORT || 'medium'} · today ${TODAY}`);
let pass = 0, total = 0, cost = 0, cacheReads = 0, nudges = 0;
for (const cs of CASES) {
  if (ONLY && !ONLY.has(cs.n)) continue;
  total += 1;
  let r;
  try { r = await ask(cs.q); } catch (e) { console.log(`FAIL  #${cs.n} ${cs.q}  — threw ${e.message}`); continue; }
  if (r.error || !r.done) { console.log(`FAIL  #${cs.n} ${cs.q}  — ${r.error ? r.error.code + ': ' + (r.error.detail || r.error.message) : 'no done event'} (status ${r.status})`); continue; }
  const global = [];
  if (r.list && r.list.dropped.length) global.push(`dropped ${r.list.dropped.join(',')}`);
  if (r.done.iterations > 5) global.push(`${r.done.iterations} iterations`);
  if (r.done.web_uses > 3) global.push(`${r.done.web_uses} web uses`);
  if (r.ms > 60000) global.push(`${Math.round(r.ms / 1000)}s`);
  let ok = false, detail = '';
  try { [ok, detail] = cs.check(r); } catch (e) { detail = 'check threw ' + e.message; }
  ok = ok && !global.length;
  pass += ok ? 1 : 0;
  cost += r.done.cost_usd || 0; cacheReads += r.done.usage.cache_read_input_tokens || 0; nudges += r.done.nudged ? 1 : 0;
  console.log(`${ok ? 'PASS' : 'FAIL'}  #${cs.n} ${cs.q}  — ${detail}${global.length ? ' | ' + global.join(', ') : ''} | ${Math.round(r.ms / 1000)}s, $${(r.done.cost_usd || 0).toFixed(3)}, cache ${r.done.usage.cache_read_input_tokens}/${r.done.usage.cache_creation_input_tokens}`);
  console.log(`      ${r.text.replace(/\s+/g, ' ').slice(0, 220)}`);
  if (r.list) console.log(`      list "${r.list.title}" (${r.list.kind}): ${ids(r).slice(0, 6).join(', ')}${ids(r).length > 6 ? ' …' : ''}${r.list.suggestions.length ? ' | chips: ' + r.list.suggestions.join(' / ') : ''}`);
}
console.log(`\n${pass}/${total} passed · $${cost.toFixed(3)} · nudges ${nudges} · cache reads ${cacheReads}`);
process.exit(pass === total ? 0 : 1);
