/* curation_core.js — shared by the dev dashboard (curation_template.html) and the
 * client site (client_template.html). Inlined by curation_dashboard.inline_core() at the
 * CURATION_CORE marker, inside each page's main <script>. Expects window.CURATION.
 * Sections 1 (data access + helpers) and 2 (scoring mirror, one-to-one with curate.py)
 * plus the params normalize/merge/order helpers. No DOM access here. */

/* =====================================================================
 * 1. DATA ACCESS
 * ===================================================================*/
const R = window.CURATION || {};
const SHOWS = R.shows || [];
const SOURCES = R.sources || [];
const SOURCE_BY_ID = Object.fromEntries(SOURCES.filter(s => s && s.id).map(s => [s.id, s]));
const CANDIDATES = R.candidates || [];
const SEESAW = R.seesaw || { snapshot_id: null, entries: [], all_snapshots: [] };
const RUNS = R.runs || [];
const CITY = R.city || '?';

// feature order = curate.FEATURES (also the sum order for score, so JS and Python sum identically)
const FEATURES = ['press', 'judge', 'venue', 'artist_heat', 'museum', 'keyword', 'wiki_heat', 'opening_recency', 'closing_soon', 'quality'];
const FEATURE_LABEL = { press: 'press', judge: 'judge', venue: 'venue tier', artist_heat: 'artist heat', museum: 'museum', keyword: 'keywords', wiki_heat: 'wiki heat', opening_recency: 'opening recency', closing_soon: 'closing soon', quality: 'quality' };
// stacked-bar order + palette slot (hue × texture: two related pairs share a hue, the proxy/secondary member is hatched)
const STACK_ORDER = ['press', 'judge', 'venue', 'artist_heat', 'wiki_heat', 'museum', 'keyword', 'quality', 'opening_recency', 'closing_soon'];
const FEATURE_SLOT = { press: [1, false], judge: [2, false], venue: [3, false], artist_heat: [4, false], wiki_heat: [4, true], museum: [5, false], keyword: [6, false], quality: [7, false], opening_recency: [8, false], closing_soon: [8, true] };
const FEATURE_CAP = { press: 1.25, artist_heat: 1.25 };   // log-saturation cap; every other feature tops out at 1
const PRESS_KINDS = new Set(['pick', 'review', 'news', 'listing', 'press_release_claim']);
const ARTIST_KINDS = new Set(['artist_activity', 'award']);
const CLOSING_SOON_DAYS = 14;
const JUDGE_MODELS = [['sonnet', 'claude-sonnet-5'], ['opus', 'claude-opus-5'], ['mean', 'mean of both'], ['none', 'none (judge off)']];

function parseISO(s) {
  if (!s || typeof s !== 'string') return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s.trim());
  if (!m) return null;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  if (isNaN(d) || d.getUTCMonth() !== +m[2] - 1 || d.getUTCDate() !== +m[3]) return null;
  return d;
}
const TODAY = parseISO(R.today) || new Date(Date.UTC(new Date().getFullYear(), new Date().getMonth(), new Date().getDate()));
const daysBetween = (a, b) => Math.round((a - b) / 86400000);

const $ = (sel, root) => (root || document).querySelector(sel);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (x, d) => (x == null || Number.isNaN(+x)) ? '–' : (+x).toFixed(d == null ? 3 : d);
const pct = x => x == null ? '–' : (100 * x).toFixed(0) + '%';
const clone = o => JSON.parse(JSON.stringify(o));
const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? undefined : a[k]), o);
function setPath(o, p, v) {
  const ks = p.split('.'); let cur = o;
  for (const k of ks.slice(0, -1)) { if (cur[k] == null || typeof cur[k] !== 'object') cur[k] = {}; cur = cur[k]; }
  cur[ks[ks.length - 1]] = v;
}
const pyRepr = obj => '{' + Object.entries(obj || {}).map(([k, v]) => `'${k}': ${v}`).join(', ') + '}';

/* =====================================================================
 * 2. SCORING MIRROR — one-to-one with scraper/curate.py
 *    (compute_features / score_show / apply_gates / rank / seesaw_metrics /
 *     seesaw_commonality). Every constant comes from params; the comment on
 *     each function lists the params keys it reads.
 * ===================================================================*/

// decay(age, hl) = 0.5 ** (age / hl); hl <= 0 or null -> 1
function decay(ageDays, halfLife) {
  if (halfLife == null || +halfLife <= 0) return 1.0;
  return Math.pow(0.5, Math.max(0, +ageDays) / +halfLife);
}

// curate.signal_age_days: published_at else ts date; the report's signal view carries that as `date`
function signalAgeDays(sig) {
  const d = parseISO(sig.date) || parseISO(sig.published_at);
  return d ? daysBetween(TODAY, d) : 0;
}

// reads: params.sources[id].{enabled,weight}, sources.json weight/active, params.default_source_weight
function sourceWeight(sig, params) {
  const sid = (sig.source && sig.source.id) || 'unknown';
  const p = (params.sources || {})[sid];
  if (p && Object.keys(p).length) {
    if (p.enabled === false) return 0.0;
    if (p.weight != null) return +p.weight;
  }
  const dflt = +(params.default_source_weight ?? 0.3);
  const src = SOURCE_BY_ID[sid];
  if (src) {
    if (src.active === false) return 0.0;
    return src.weight != null ? +src.weight : dflt;
  }
  return dflt;
}

// reads: params.kind_weights[kind], params.strength_weights[strength] (+ sourceWeight)
function signalWeight(sig, params, halfLife) {
  const kw = +((params.kind_weights || {})[sig.kind] ?? 0);
  const sw = +((params.strength_weights || {})[sig.strength] ?? 0);
  return sourceWeight(sig, params) * kw * sw * decay(signalAgeDays(sig), halfLife);
}

function logSaturate(raw, ref, cap) {
  cap = cap == null ? 1.25 : cap;
  if (ref == null || +ref <= 0) return 0.0;
  return Math.min(cap, Math.log2(1 + Math.max(0, raw)) / Math.log2(1 + +ref));
}

// reads: params.cap_per_source (+ signalWeight keys). Returns {feature, raw, perSource}
function aggregateSignals(signals, kinds, params, halfLife, ref) {
  const per = {};
  for (const s of signals) {
    if (!kinds.has(s.kind)) continue;
    const sid = (s.source && s.source.id) || 'unknown';
    per[sid] = (per[sid] || 0) + signalWeight(s, params, halfLife);
  }
  const cap = +(params.cap_per_source ?? 1.5);
  let raw = 0;
  for (const v of Object.values(per)) raw += Math.min(cap, v);
  return { feature: logSaturate(raw, ref), raw, perSource: per };
}

// reads: params.venue_tier_map[tier|"unknown"]. Tier comes resolved from the registry in the report.
function venueFeature(show, params) {
  const v = show.venue || {};
  const tier = v.tier;
  const map = params.venue_tier_map || {};
  // Python counts fair listings per VENUE from every fair signal (ctx.fair_sources), not just the
  // signals matched to this show, so the report carries the count as venue.n_fair; older reports
  // without it fall back to the show's own fair_exhibitor signals.
  const nFair = v.n_fair != null ? +v.n_fair
    : new Set((show.signals || []).filter(s => s.kind === 'fair_exhibitor').map(s => (s.source && s.source.id) || 'unknown')).size;
  const isMuseum = !!v.is_museum;
  let value, basis;
  if (tier != null && Object.prototype.hasOwnProperty.call(map, String(tier))) {
    value = +map[String(tier)];
    basis = `tier ${tier}`;
  } else {
    const fallback = 0.4 * nFair + (isMuseum ? 0.3 : 0.0);
    value = Math.min(1.0, Math.max(+(map.unknown ?? 0.2), fallback));
    basis = 'no tier' + (nFair ? `, ${nFair} fair listing(s)` : '') + (isMuseum ? ', museum' : '');
  }
  return [value, { tier, n_fair: nFair, basis, venue_norm: v.norm }];
}

// reads: params.judge.{variant, model in sonnet|opus|mean|none}; show.judge = {variant: {model_id: row}}
function judgeFeature(show, params) {
  const cfg = params.judge || {};
  const variant = cfg.variant, model = cfg.model || 'none';
  if (model === 'none') return [0.0, { model: 'none', rows: {} }];
  const rows = ((show.judge || {})[variant]) || {};
  const picked = {};
  for (const [mid, row] of Object.entries(rows)) {
    if (model === 'mean' || ((model === 'sonnet' || model === 'opus') && mid.toLowerCase().includes(model))) picked[mid] = row;
  }
  const ids = Object.keys(picked);
  if (!ids.length) return [0.0, { model, rows: {}, missing: true }];
  let sum = 0; for (const id of ids) sum += +(picked[id].overall ?? 0);
  const overall = sum / ids.length;
  return [Math.min(1.0, overall / 10.0), { model, rows: Object.fromEntries(ids.map(id => [id, picked[id].overall])) }];
}

// reads: params.wiki_ref; show.wiki = the best-matching wiki row picked in Python
function wikiFeature(show, params) {
  const w = show.wiki || {};
  if (!show.artist || !w.title) return [0.0, { title: null }];
  const ref = +(params.wiki_ref ?? 20000);
  const pv = +(w.pageviews_90d || 0);
  const val = ref > 0 ? 0.3 + 0.7 * Math.min(1.0, Math.log10(1 + pv) / Math.log10(1 + ref)) : 0.3;
  return [val, { title: w.title, pageviews_90d: w.pageviews_90d, url: w.url }];
}

// curation_keywords.keyword_feature: min(1, sum of class weights over distinct classes)
function keywordFeature(hits) {
  const cls = {};
  for (const h of hits || []) cls[h.class] = +h.weight;
  let s = 0; for (const v of Object.values(cls)) s += v;
  return Math.min(1.0, s);
}

// reads: params.half_life_days.{press,artist,opening}, press_ref, artist_ref (+ the helpers above)
function computeFeatures(show, params) {
  const hl = params.half_life_days || {};
  const signals = show.signals || [];
  const press = aggregateSignals(signals, PRESS_KINDS, params, +(hl.press ?? 21), +(params.press_ref ?? 4));
  const heat = aggregateSignals(signals, ARTIST_KINDS, params, +(hl.artist ?? 180), +(params.artist_ref ?? 3));
  const [venue, venueDetail] = venueFeature(show, params);
  const [judge, judgeDetail] = judgeFeature(show, params);
  const [wiki, wikiDetail] = wikiFeature(show, params);
  const hits = show.keyword_hits || [];
  const dso = show.days_since_open, dtc = show.days_to_close;
  let opening;
  if (dso == null) opening = 0.0;
  else if (dso < 0) opening = 1.0;
  else opening = decay(dso, +(hl.opening ?? 30));
  const closing = (dtc != null && dtc >= 0 && dtc <= CLOSING_SOON_DAYS) ? 1.0 : 0.0;
  const nImages = +(show.n_images || 0), descWords = +(show.desc_words || 0);
  const quality = 0.5 * Math.min(1.0, nImages / 5) + 0.5 * Math.min(1.0, descWords / 250);
  const features = {
    press: press.feature, judge, venue, artist_heat: heat.feature,
    museum: (show.venue && show.venue.is_museum) ? 1.0 : 0.0,
    keyword: keywordFeature(hits), wiki_heat: wiki,
    opening_recency: opening, closing_soon: closing, quality,
  };
  const detail = {
    signals, press_raw: press.raw, press_sources: press.perSource, heat_raw: heat.raw, heat_sources: heat.perSource,
    venue: venueDetail, judge: judgeDetail, wiki: wikiDetail, keyword_hits: hits,
    days_since_open: dso, days_to_close: dtc, n_images: nImages, desc_words: descWords,
  };
  return { features, detail };
}

// reads: params.weights[f]. score = Σ weights[f]·feature[f] in FEATURES order; contrib = each term
function scoreShow(features, params) {
  const w = params.weights || {};
  const contrib = {};
  let score = 0;
  for (const f of FEATURES) { contrib[f] = +(w[f] ?? 0) * +(features[f] ?? 0); score += contrib[f]; }
  return { score, contrib };
}

// curate._reasons
function reasonsFor(r, d) {
  const out = [];
  const f = r.features, c = r.contrib;
  const top = FEATURES.filter(k => c[k] > 0).sort((a, b) => c[b] - c[a]).slice(0, 4);
  for (const k of top) {
    if (k === 'press') {
      const sigs = d.signals.filter(s => PRESS_KINDS.has(s.kind));
      const names = [...new Set(sigs.map(s => (s.source && s.source.id) || '?'))].sort();
      out.push(`press ${f.press.toFixed(2)} from ${sigs.length} signal(s): ${names.slice(0, 4).join(', ')}`);
    } else if (k === 'judge') out.push(`judge ${f.judge.toFixed(2)} (${d.judge.model}: ${pyRepr(d.judge.rows)})`);
    else if (k === 'venue') out.push(`venue ${f.venue.toFixed(2)} (${d.venue.basis})`);
    else if (k === 'artist_heat') out.push(`artist heat ${f.artist_heat.toFixed(2)} from ${d.signals.filter(s => ARTIST_KINDS.has(s.kind)).length} signal(s)`);
    else if (k === 'museum') out.push('museum');
    else if (k === 'keyword') out.push('keywords: ' + d.keyword_hits.map(h => h.label).join(', '));
    else if (k === 'wiki_heat') out.push(`wikipedia: ${d.wiki.title} (${d.wiki.pageviews_90d} views/90d)`);
    else if (k === 'opening_recency') { const dd = d.days_since_open; out.push(dd != null && dd < 0 ? `opens in ${-dd} d` : `opened ${dd} d ago`); }
    else if (k === 'closing_soon') out.push(`closes in ${d.days_to_close} d`);
    else if (k === 'quality') out.push(`quality ${f.quality.toFixed(2)} (${d.n_images} images, ${d.desc_words} words)`);
  }
  if (d.judge.missing) out.push('no judge verdict');
  if (!d.signals.length) out.push('no signals');
  if (r.gate) out.push(`gated: ${r.gate_detail || r.gate}`);
  else if (r.pinned) out.push('pinned');
  return out;
}

// curate.apply_gates — reads: overrides.{exclude,pin}, threshold, max_per_venue, max_per_neighborhood,
// max_museum_share, max_n, include_pending, exclude_museums, editors_pick_top. Gate order:
// exclude -> in-window -> threshold -> max_per_venue -> exclude_museums -> max_per_neighborhood
// -> max_museum_share -> (pins first) -> max_n. Pinned rows skip threshold/venue/diversity gates.
function applyGates(rows, params) {
  const ov = params.overrides || {};
  const exclude = new Set(ov.exclude || []);
  const pins = ov.pin || [];
  const threshold = +(params.threshold || 0);
  const maxPerVenue = params.max_per_venue;
  const maxPerNb = params.max_per_neighborhood;
  const share = params.max_museum_share;
  const maxN = params.max_n;
  const nRef = maxN ? maxN : rows.length;
  const museumAllowed = (share != null) ? Math.floor(+share * nRef) : null;
  const seenVenue = {}, seenNb = {};
  let museums = 0;
  const survivors = [];
  for (const r of rows) {
    r.gate = null; r.gate_detail = null;
    r.featured = false; r.editors_pick = false; r.featured_rank = null;
    r.pinned = pins.includes(r.slug);
    const isMuseum = !!r.venue.is_museum;
    const nb = r.venue.neighborhood || '';
    const vn = r.venue.norm;
    if (exclude.has(r.slug)) { r.gate = 'excluded'; r.gate_detail = 'overrides.exclude'; continue; }
    if (!r.in_window && !params.include_pending) { r.gate = 'out_of_window'; r.gate_detail = 'not in the publication window'; continue; }
    if (params.published_only && r.pool !== 'published') { r.gate = 'not_published'; r.gate_detail = 'in the pending pool; the built site reads the published file only'; continue; }
    if (params.require_open) {
      const d = r.days_since_open;
      if (d == null) { r.gate = 'not_open'; r.gate_detail = 'no start date; cannot confirm it is open'; continue; }
      if (d < 0) { r.gate = 'not_open'; r.gate_detail = `opens in ${-d} d`; continue; }
    }
    if (!r.pinned) {
      if (r.score < threshold) { r.gate = 'below_threshold'; r.gate_detail = `score ${r.score.toFixed(3)} < threshold ${threshold.toFixed(2)}`; continue; }
      if (maxPerVenue && (seenVenue[vn] || 0) >= maxPerVenue) { r.gate = 'max_per_venue'; r.gate_detail = `venue already has ${maxPerVenue}`; continue; }
      if (params.exclude_museums && isMuseum) { r.gate = 'exclude_museums'; r.gate_detail = 'museums excluded'; continue; }
      if (maxPerNb && (seenNb[nb] || 0) >= maxPerNb) { r.gate = 'max_per_neighborhood'; r.gate_detail = `${nb} already has ${maxPerNb}`; continue; }
      if (museumAllowed != null && isMuseum && museums >= museumAllowed) { r.gate = 'max_museum_share'; r.gate_detail = `museum share cap ${share} of ${nRef} = ${museumAllowed}`; continue; }
    }
    seenVenue[vn] = (seenVenue[vn] || 0) + 1;
    seenNb[nb] = (seenNb[nb] || 0) + 1;
    museums += isMuseum ? 1 : 0;
    survivors.push(r);
  }
  let ordered = survivors.filter(r => r.pinned).concat(survivors.filter(r => !r.pinned));
  if (maxN) {
    for (const r of ordered.slice(maxN)) { r.gate = 'max_n'; r.gate_detail = `beyond max_n ${maxN}`; }
    ordered = ordered.slice(0, maxN);
  }
  const topPicks = parseInt(params.editors_pick_top || 0, 10) || 0;
  ordered.forEach((r, i) => { r.featured = true; r.featured_rank = i + 1; r.editors_pick = i < topPicks; });
  return ordered;
}

// curate.rank: score every pool show, sort by (-score, slug), 1-based rank, gate, reasons
function rank(shows, params) {
  const rows = shows.map(show => {
    const { features, detail } = computeFeatures(show, params);
    const { score, contrib } = scoreShow(features, params);
    return Object.assign({}, show, { features, contrib, score, _detail: detail });
  });
  rows.sort((a, b) => (b.score - a.score) || (a.slug < b.slug ? -1 : a.slug > b.slug ? 1 : 0));
  rows.forEach((r, i) => { r.rank = i + 1; });
  applyGates(rows, params);
  for (const r of rows) r.reasons = reasonsFor(r, r._detail);
  return rows;
}

function ndcg(topSlugs, rel) {
  if (!rel.size) return null;
  let dcg = 0; topSlugs.forEach((s, i) => { dcg += (rel.get(s) || 0) / Math.log2(i + 2); });
  const ideal = [...rel.values()].sort((a, b) => b - a).slice(0, Math.max(1, topSlugs.length));
  let idcg = 0; ideal.forEach((v, i) => { idcg += v / Math.log2(i + 2); });
  return idcg ? dcg / idcg : null;
}

// curate.seesaw_metrics over the snapshot entries (matching was done in Python: entries[].match.slug)
function seesawMetrics(rows, entries) {
  if (!entries || !entries.length) return { entries: [], metrics: null, misses: null, top: [] };
  const bySlug = new Map(rows.map(r => [r.slug, r]));
  const top = rows.filter(r => r.featured).sort((a, b) => a.featured_rank - b.featured_rank).map(r => r.slug);
  const topSet = new Set(top);
  const n = entries.length;
  let tp = 0, inPool = 0;
  const rel = new Map();
  const annotated = entries.map(e => {
    const m = e.match || {};
    const row = m.slug ? bySlug.get(m.slug) : null;
    const match = Object.assign({}, m, { rank: row ? row.rank : null, score: row ? row.score : null, featured: !!(row && row.featured), gate: row ? row.gate : null, slug: row ? m.slug : (m.slug || null) });
    if (row) {
      inPool++;
      const pos = (e.position == null) ? n : parseInt(e.position, 10);
      rel.set(m.slug, Math.max(rel.get(m.slug) || 0, n - pos + 1));
      if (topSet.has(m.slug)) tp++;
    }
    return Object.assign({}, e, { match, in_pool: !!row });
  });
  const nTop = top.length;
  const metrics = {
    n_snapshot: n, n_top: nTop, tp,
    precision: nTop ? tp / nTop : null,
    recall: n ? tp / n : null,
    jaccard: (nTop + n - tp) ? tp / (nTop + n - tp) : null,
    pool_coverage: n ? inPool / n : null,
    ranking_recall: inPool ? tp / inPool : null,
    ndcg: ndcg(top, rel),
  };
  const misses = {
    not_in_pool: annotated.filter(e => !e.in_pool),
    in_pool_below_cutoff: annotated.filter(e => e.in_pool && !e.match.featured),
    our_picks_not_on_seesaw: top.filter(s => !rel.has(s)),
  };
  return { entries: annotated, metrics, misses, top, rel };
}

// curate.seesaw_commonality: See Saw-matched in-window shows vs the rest, lift = matched / rest
function commonality(rows, ss) {
  if (!ss || !ss.entries.length) return { n_matched: 0, n_rest: 0, rows: [] };
  const matched = new Set(ss.entries.filter(e => e.in_pool).map(e => e.match.slug));
  const pool = rows.filter(r => r.in_window);
  const a = pool.filter(r => matched.has(r.slug)), b = pool.filter(r => !matched.has(r.slug));
  const vals = (rs, fn) => rs.map(fn).filter(v => v != null);
  const mean = (rs, fn) => { const v = vals(rs, fn); return v.length ? v.reduce((x, y) => x + y, 0) / v.length : null; };
  const median = (rs, fn) => { const v = vals(rs, fn).sort((x, y) => x - y); if (!v.length) return null; const m = v.length >> 1; return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2; };
  const share = (rs, fn) => rs.length ? rs.filter(fn).length / rs.length : null;
  const specs = [
    ['museum share', 'share', r => r.venue.is_museum, share],
    ['tier 1 share', 'share', r => r.venue.tier === 1, share],
    ['tier 2 share', 'share', r => r.venue.tier === 2, share],
    ['tier 3 share', 'share', r => r.venue.tier === 3, share],
    ['tier unknown share', 'share', r => r.venue.tier == null, share],
    ['has any signal', 'share', r => (r.signals || []).length > 0, share],
    ['press signals per show', 'mean', r => (r.signals || []).filter(s => PRESS_KINDS.has(s.kind)).length, mean],
    ['press feature', 'mean', r => r.features.press, mean],
    ['artist heat feature', 'mean', r => r.features.artist_heat, mean],
    ['wiki heat feature', 'mean', r => r.features.wiki_heat, mean],
    ['has wikipedia article', 'share', r => !!(r.wiki && r.wiki.title), share],
    ['keyword feature', 'mean', r => r.features.keyword, mean],
    ['judge feature', 'mean', r => r.features.judge, mean],
    ['quality feature', 'mean', r => r.features.quality, mean],
    ['days since opening', 'median', r => r.days_since_open, median],
    ['days to close', 'median', r => r.days_to_close, median],
    ['image count', 'mean', r => r.n_images, mean],
    ['description words', 'mean', r => r.desc_words, mean],
    ['score', 'mean', r => r.score, mean],
  ];
  const out = specs.map(([name, kind, fn, agg]) => {
    const va = agg(a, fn), vb = agg(b, fn);
    return { name, kind, seesaw: va, rest: vb, lift: (va != null && vb != null && vb !== 0) ? va / vb : null };
  });
  const spreadA = a.length ? new Set(a.map(r => r.venue.neighborhood)).size / a.length : null;
  const spreadB = b.length ? new Set(b.map(r => r.venue.neighborhood)).size / b.length : null;
  out.push({ name: 'neighborhood spread (distinct / shows)', kind: 'ratio', seesaw: spreadA, rest: spreadB, lift: (spreadA != null && spreadB) ? spreadA / spreadB : null });
  return { n_matched: a.length, n_rest: b.length, rows: out };
}

// candidates (not in pool): press/artist only, never counted — curate.candidate_rows
function candidateScores(params) {
  const hl = params.half_life_days || {}, w = params.weights || {};
  return CANDIDATES.map(c => {
    const sigs = c.signals || [];
    const press = aggregateSignals(sigs, PRESS_KINDS, params, +(hl.press ?? 21), +(params.press_ref ?? 4)).feature;
    const heat = aggregateSignals(sigs, ARTIST_KINDS, params, +(hl.artist ?? 180), +(params.artist_ref ?? 3)).feature;
    return Object.assign({}, c, { features: { press, artist_heat: heat }, score: +(w.press ?? 0) * press + +(w.artist_heat ?? 0) * heat });
  }).sort((x, y) => (y.score - x.score) || ((y.n_signals || 0) - (x.n_signals || 0)) || (x.key < y.key ? -1 : 1));
}

// precision/recall vs threshold at the current weights (scores fixed; only the gates move)
function sweepData(rows, params, entries) {
  const out = [];
  for (let i = 0; i <= 100; i++) {
    const t = i / 100;
    const rr = rows.map(r => Object.assign({}, r));
    applyGates(rr, Object.assign({}, params, { threshold: t }));
    const m = seesawMetrics(rr, entries).metrics || {};
    out.push({ t, precision: m.precision ?? null, recall: m.recall ?? null, jaccard: m.jaccard ?? null, n_top: rr.filter(r => r.featured).length });
  }
  return out;
}

// Python/JS parity: re-score every show under params_default and compare with the report's numbers
function parityCheck() {
  const p = R.params_default || {};
  let maxScore = 0, maxFeat = 0, worst = null;
  const rows = [];
  for (const s of SHOWS) {
    const { features } = computeFeatures(s, p);
    const { score } = scoreShow(features, p);
    const ds = Math.abs(score - (s.score || 0));
    let df = 0, dfName = '';
    for (const f of FEATURES) { const d = Math.abs(features[f] - ((s.features || {})[f] || 0)); if (d > df) { df = d; dfName = f; } }
    rows.push({ slug: s.slug, js: score, py: s.score, diff: ds, feat_diff: df, feat: dfName });
    if (ds > maxScore) { maxScore = ds; worst = s.slug; }
    if (df > maxFeat) maxFeat = df;
  }
  rows.sort((a, b) => b.diff - a.diff);
  return { max_score_diff: maxScore, max_feature_diff: maxFeat, worst, ok: maxScore < 1e-9 && maxFeat < 1e-9, rows: rows.slice(0, 5), n: SHOWS.length };
}

/* =====================================================================
 * 3. STATE + RENDERING
 * ===================================================================*/
const PARAM_KEY_ORDER = ['version', 'city', 'today', 'max_n', 'threshold', 'max_per_venue', 'include_pending', 'published_only', 'require_open', 'editors_pick_top',
  'exclude_museums', 'max_per_neighborhood', 'max_museum_share', 'weights', 'half_life_days', 'press_ref', 'artist_ref',
  'cap_per_source', 'wiki_ref', 'default_source_weight', 'kind_weights', 'strength_weights', 'sources', 'venue_tier_map', 'judge', 'overrides'];

function normalizeParams(p) {
  p.version = p.version ?? 1; p.city = CITY; p.today = R.today || null;
  for (const k of ['weights', 'half_life_days', 'kind_weights', 'strength_weights', 'venue_tier_map', 'judge', 'sources']) if (!p[k] || typeof p[k] !== 'object') p[k] = {};
  if (!p.overrides || typeof p.overrides !== 'object') p.overrides = {};
  p.overrides.pin = Array.isArray(p.overrides.pin) ? p.overrides.pin : [];
  p.overrides.exclude = Array.isArray(p.overrides.exclude) ? p.overrides.exclude : [];
  for (const f of FEATURES) if (p.weights[f] == null) p.weights[f] = 0;
  return p;
}
function mergeParams(base, over) {   // curate._merge_params: dict-valued keys merged per key
  const deep = new Set(['weights', 'half_life_days', 'kind_weights', 'strength_weights', 'venue_tier_map', 'judge', 'overrides']);
  for (const [k, v] of Object.entries(over || {})) {
    if (deep.has(k) && v && typeof v === 'object' && !Array.isArray(v) && base[k] && typeof base[k] === 'object') Object.assign(base[k], v);
    else base[k] = v;
  }
  return base;
}
function orderedParams(p) {
  const out = {};
  for (const k of PARAM_KEY_ORDER) if (k in p) out[k] = p[k];
  for (const k of Object.keys(p)) if (!(k in out) && !k.startsWith('_')) out[k] = p[k];
  return out;
}
