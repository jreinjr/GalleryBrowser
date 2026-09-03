/* venue_core.js — scoring mirror for the gallery-ranking dashboard (venue_template.html).
 * Inlined by venue_dashboard.inline_core() at the VENUE_CORE marker, inside the page's main
 * <script>. Expects window.VENUES (content/curation/<city>/venues_ranked.json).
 * Section 1: data access + helpers. Section 2: one-to-one mirror of rank_venues.py
 * (computeFeatures / scoreVenue / gateFor / tierFor / rank / auc). No DOM access here. */

/* =====================================================================
 * 1. DATA ACCESS
 * ===================================================================*/
const R = window.VENUES || {};
const VENUES = R.venues || [];
const CITY = R.city || '?';
const BENCH = R.benchmark || { seesaw_venue_ids: [], auc: null };
const SEESAW_IDS = new Set(BENCH.seesaw_venue_ids || []);
const FILE_PRESETS = R.presets || {};

// feature order = rank_venues.FEATURES (also the sum order for score)
const FEATURES = ['hours_breadth', 'fairs', 'curated_lists', 'press', 'directory', 'longevity', 'roster_size', 'roster_strength',
  'show_cadence', 'multi_location', 'places_popularity', 'web_presence', 'venue_judge', 'wiki', 'kind_gallery', 'kind_nonprofit', 'kind_museum', 'seesaw_presence'];
const FEATURE_LABEL = { hours_breadth: 'hours breadth', fairs: 'fairs', curated_lists: 'curated lists', press: 'press', directory: 'directories',
  longevity: 'longevity', roster_size: 'roster size', roster_strength: 'roster strength', show_cadence: 'show cadence', multi_location: 'multi-location',
  places_popularity: 'Places popularity', web_presence: 'web presence', venue_judge: 'venue judge', wiki: 'Wikipedia',
  kind_gallery: 'kind: gallery', kind_nonprofit: 'kind: nonprofit', kind_museum: 'kind: museum', seesaw_presence: 'See Saw (leak)' };
// stacked-bar order + palette slot (hue, hatched)
const STACK_ORDER = ['fairs', 'curated_lists', 'press', 'directory', 'venue_judge', 'wiki', 'hours_breadth', 'longevity', 'roster_size', 'show_cadence',
  'multi_location', 'places_popularity', 'web_presence', 'roster_strength', 'kind_gallery', 'kind_nonprofit', 'kind_museum', 'seesaw_presence'];
const FEATURE_SLOT = { fairs: [1, false], curated_lists: [1, true], press: [2, false], directory: [2, true], venue_judge: [3, false], wiki: [3, true],
  hours_breadth: [4, false], longevity: [5, false], roster_size: [6, false], roster_strength: [6, true], show_cadence: [7, false], multi_location: [7, true],
  places_popularity: [8, false], web_presence: [8, true], kind_gallery: [5, true], kind_nonprofit: [5, true], kind_museum: [5, true], seesaw_presence: [2, true] };
const KIND_GROUP = { gallery: 'gallery', nonprofit: 'nonprofit', project_space: 'nonprofit', university: 'nonprofit', museum: 'museum', other: null };
const JUDGE_MODELS = [['sonnet', 'claude-sonnet-5'], ['opus', 'claude-opus-5'], ['mean', 'mean of both'], ['none', 'none (judge off)']];
const JUDGE_MODEL_ID = { sonnet: 'claude-sonnet-5', opus: 'claude-opus-5' };
const APPOINTMENT_ONLY_VALUE = 0.05, FULL_DAYS = 6.0, FULL_HOURS = 42.0;

const $ = (sel, root) => (root || document).querySelector(sel);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmt = (x, d) => (x == null || Number.isNaN(+x)) ? '–' : (+x).toFixed(d == null ? 3 : d);
const clone = o => JSON.parse(JSON.stringify(o));
const getPath = (o, p) => p.split('.').reduce((a, k) => (a == null ? undefined : a[k]), o);
function setPath(o, p, v) {
  const ks = p.split('.'); let cur = o;
  for (const k of ks.slice(0, -1)) { if (cur[k] == null || typeof cur[k] !== 'object') cur[k] = {}; cur = cur[k]; }
  cur[ks[ks.length - 1]] = v;
}

/* =====================================================================
 * 2. SCORING MIRROR — one-to-one with scraper/rank_venues.py
 * ===================================================================*/
function sat(n, ref) {
  if (ref == null || +ref <= 0 || n == null || +n <= 0) return 0.0;
  return Math.min(1.0, Math.log2(1 + +n) / Math.log2(1 + +ref));
}

// hours.breadth_value over a parse_hours() result carried in raw.hours
function breadthValue(p) {
  if (!p || !p.parsed) return 0.0;
  const days = p.days_open, hrs = p.hours_per_week;
  if (days === 0) return p.by_appointment ? APPOINTMENT_ONLY_VALUE : 0.0;
  const d = Math.min(1.0, days / FULL_DAYS), h = Math.min(1.0, hrs / FULL_HOURS);
  let value = h > 0 ? Math.sqrt(d * h) : d * 0.5;
  if (p.by_appointment && days <= 2) value = Math.max(value, APPOINTMENT_ONLY_VALUE);
  return Math.round(Math.min(1.0, value) * 10000) / 10000;
}

// rank_venues.compute_features from the embedded raw evidence
function computeFeatures(v, params) {
  const raw = v.raw || {}, refs = params.refs || {}, lw = params.list_weights || {};
  const f = {}, b = {};
  if (raw.hours) {
    f.hours_breadth = breadthValue(raw.hours);
    const hp = raw.hours;
    b.hours_breadth = (hp.days_open === 0 && hp.by_appointment) ? 'by appointment only' : `${hp.days_open} day(s)/wk, ${+hp.hours_per_week} h/wk${hp.by_appointment ? ', +appointment' : ''}`;
  } else if (raw.hours_status === 'appointment_only') { f.hours_breadth = APPOINTMENT_ONLY_VALUE; b.hours_breadth = 'status: appointment_only'; }
  else { f.hours_breadth = 0.0; b.hours_breadth = 'no hours on record'; }
  const fw = (raw.fairs || []).reduce((s, id) => s + (lw[id] != null ? +lw[id] : 1.0), 0);
  f.fairs = sat(fw, refs.fairs ?? 3); b.fairs = (raw.fairs || []).length ? raw.fairs.join(', ') : 'no fair listings';
  const lsw = (raw.lists || []).reduce((s, id) => s + (lw[id] != null ? +lw[id] : 1.0), 0);
  f.curated_lists = sat(lsw, refs.lists ?? 2); b.curated_lists = (raw.lists || []).length ? raw.lists.join(', ') : 'on no curated list';
  f.press = sat((raw.press || []).length, refs.press ?? 3); b.press = (raw.press || []).length ? raw.press.join(', ') : 'no press';
  f.directory = sat((raw.directories || []).length, refs.directory ?? 2); b.directory = (raw.directories || []).length ? raw.directories.join(', ') : 'no directory seed';
  if (raw.years != null) { f.longevity = sat(raw.years, refs.longevity_years ?? 20); b.longevity = `${raw.years} yr(s) (${raw.founded_basis})`; }
  else { f.longevity = 0.0; b.longevity = 'no report'; }
  f.roster_size = sat(raw.roster_count || 0, refs.roster ?? 20); b.roster_size = raw.roster_count != null ? `${raw.roster_count} artists` : 'no report';
  f.roster_strength = 0.0; b.roster_strength = 'artists dataset not built';
  f.show_cadence = sat(raw.exhibitions_per_year || 0, refs.shows_per_year ?? 6); b.show_cadence = raw.exhibitions_per_year != null ? `${+raw.exhibitions_per_year} shows/yr` : 'no report';
  const nl = raw.n_locations || 0;
  f.multi_location = Math.min(1.0, nl / ((+refs.locations || 2) || 1)); b.multi_location = nl ? `${nl} other location(s)` : 'single location / no report';
  if (raw.ratings != null) { const ref = +(refs.ratings ?? 200); f.places_popularity = Math.min(1.0, Math.log10(1 + Math.max(0, raw.ratings)) / Math.log10(1 + ref)); b.places_popularity = `${raw.ratings} Google reviews${raw.rating ? `, ${raw.rating}★` : ''}`; }
  else { f.places_popularity = 0.0; b.places_popularity = 'no Places ratings cached'; }
  const w = raw.web || {};
  f.web_presence = 0.35 * (w.exhibitions_url ? 1 : 0) + 0.35 * (w.dates ? 1 : 0) + 0.30 * (w.about ? 1 : 0);
  const parts = ['exhibitions_url', 'dates', 'about'].filter(k => w[k]);
  b.web_presence = (parts.length ? parts.join(', ') : 'nothing fetched') + (w.fetch_mode && w.fetch_mode !== 'static' ? ` [${w.fetch_mode}]` : '');
  const jcfg = params.judge || {}, variant = jcfg.variant, model = jcfg.model || 'none';
  const jv = ((raw.judge || {})[variant]) || {};
  let vals = [];
  if (model === 'mean') vals = Object.values(jv).map(r => r.overall).filter(x => x != null);
  else if (model !== 'none') { const r = jv[JUDGE_MODEL_ID[model] || model]; if (r && r.overall != null) vals = [r.overall]; }
  if (vals.length) { const m = vals.reduce((a, c) => a + c, 0) / vals.length; f.venue_judge = Math.min(1.0, Math.max(0.0, m / 10.0)); b.venue_judge = `${+m.toFixed(4)}/10 (${variant}/${model})`; }
  else { f.venue_judge = 0.0; b.venue_judge = 'no verdict'; }
  const wk = raw.wiki;
  if (wk && wk.title) { f.wiki = 0.5 + 0.5 * sat(wk.sitelinks || 0, refs.wiki_sitelinks ?? 20); b.wiki = `${wk.title} (${wk.sitelinks || 0} sitelinks)`; }
  else { f.wiki = 0.0; b.wiki = 'no article'; }
  const grp = KIND_GROUP[raw.kind || ''] ?? null;
  for (const g of ['gallery', 'nonprofit', 'museum']) { f['kind_' + g] = grp === g ? 1.0 : 0.0; b['kind_' + g] = raw.kind || '?'; }
  if (params.leak_seesaw) { const ss = raw.seesaw != null ? +raw.seesaw : (SEESAW_IDS.has(v.id) ? 0.5 : 0.0); f.seesaw_presence = ss; b.seesaw_presence = 'LEAKAGE: See Saw presence'; }
  else { f.seesaw_presence = 0.0; b.seesaw_presence = 'off'; }
  return { features: f, basis: b };
}

function scoreVenue(features, params) {
  const weights = params.weights || {};
  const contrib = {}; let score = 0;
  for (const f of FEATURES) { contrib[f] = (+weights[f] || 0) * (+features[f] || 0); score += contrib[f]; }
  return { score, contrib };
}

function gateFor(v, score, params) {
  const g = params.gates || {};
  if ((g.exclude_status || []).includes(v.status)) return `status:${v.status}`;
  if (g.require_verified && v.verification !== 'verified') return 'unverified';
  const kinds = g.kinds || [];
  if (kinds.length && !kinds.includes(v.kind)) return `kind:${v.kind}`;
  if (score < (+g.min_score || 0)) return 'below_min_score';
  return null;
}

function tierFor(score, params) {
  const t = params.tiers || {};
  for (const k of ['1', '2', '3']) { const thr = t[k]; if (thr != null && score >= +thr) return +k; }
  return null;
}

function rank(venuesIn, params) {
  const rows = venuesIn.map(v => {
    const { features, basis } = computeFeatures(v, params);
    const { score, contrib } = scoreVenue(features, params);
    return Object.assign({}, v, { features, basis, score, contrib, gate: gateFor(v, score, params) });
  });
  rows.sort((a, b) => (b.score - a.score) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  let n = 0;
  for (const r of rows) { if (r.gate) { r.tier = null; r.rank = null; } else { n += 1; r.rank = n; r.tier = tierFor(r.score, params); } }
  return rows;
}

function auc(pos, neg) {
  if (!pos.length || !neg.length) return null;
  let wins = 0;
  for (const p of pos) for (const q of neg) wins += p > q ? 1 : p === q ? 0.5 : 0;
  return wins / (pos.length * neg.length);
}

function benchmark(rows) {
  const gated = rows.filter(r => !r.gate);
  const pos = gated.filter(r => SEESAW_IDS.has(r.id)).map(r => r.score);
  const neg = gated.filter(r => !SEESAW_IDS.has(r.id)).map(r => r.score);
  const tiers = {};
  for (const r of gated) if (SEESAW_IDS.has(r.id)) { const k = String(r.tier); tiers[k] = (tiers[k] || 0) + 1; }
  return { n_seesaw: SEESAW_IDS.size, auc: auc(pos, neg), seesaw_tiers: tiers, seesaw_ranks: gated.filter(r => SEESAW_IDS.has(r.id)).map(r => r.rank).sort((a, b) => a - b), pos: pos.length };
}

// Python/JS parity: re-score every venue under params_default and compare with the report
function parityCheck() {
  const p = R.params_default || {};
  let maxScore = 0, maxFeat = 0, worst = null; const rows = [];
  for (const v of VENUES) {
    const { features } = computeFeatures(v, p);
    const { score } = scoreVenue(features, p);
    const ds = Math.abs(score - (v.score || 0));
    let df = 0, dfName = '';
    for (const f of FEATURES) { const d = Math.abs((features[f] || 0) - ((v.features || {})[f] || 0)); if (d > df) { df = d; dfName = f; } }
    rows.push({ id: v.id, js: score, py: v.score, diff: ds, feat_diff: df, feat: dfName });
    if (ds > maxScore) { maxScore = ds; worst = v.id; }
    if (df > maxFeat) maxFeat = df;
  }
  rows.sort((a, b) => b.diff - a.diff);
  return { max_score_diff: maxScore, max_feature_diff: maxFeat, worst, ok: maxScore < 1e-9 && maxFeat < 1e-9, rows: rows.slice(0, 5), n: VENUES.length };
}

/* ---- params helpers (rank_venues.load_params / _merge_params) ---- */
const PARAM_KEY_ORDER = ['version', 'city', 'today', 'weights', 'refs', 'list_weights', 'gates', 'tiers', 'judge', 'leak_seesaw'];
const DEEP_KEYS = new Set(['weights', 'refs', 'list_weights', 'gates', 'tiers', 'judge']);
function normalizeParams(p) {
  p.version = p.version ?? 1; p.city = CITY; p.today = R.today || null;
  for (const k of DEEP_KEYS) if (!p[k] || typeof p[k] !== 'object') p[k] = {};
  for (const f of FEATURES) if (p.weights[f] == null) p.weights[f] = 0;
  if (!Array.isArray(p.gates.kinds)) p.gates.kinds = [];
  if (!Array.isArray(p.gates.exclude_status)) p.gates.exclude_status = [];
  p.leak_seesaw = !!p.leak_seesaw;
  return p;
}
function mergeParams(base, over) {
  for (const [k, v] of Object.entries(over || {})) {
    if (k.startsWith('_')) continue;
    if (DEEP_KEYS.has(k) && v && typeof v === 'object' && !Array.isArray(v) && base[k] && typeof base[k] === 'object') Object.assign(base[k], v);
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
// share links: #p=<base64url(JSON diff vs params_default)>
function diffParams(p, base) {
  const out = {};
  for (const k of Object.keys(p)) {
    if (k === 'city' || k === 'today' || k === 'version') continue;
    if (DEEP_KEYS.has(k) && typeof p[k] === 'object' && !Array.isArray(p[k])) {
      const d = {};
      for (const kk of new Set([...Object.keys(p[k]), ...Object.keys((base || {})[k] || {})])) if (JSON.stringify(p[k][kk]) !== JSON.stringify(((base || {})[k] || {})[kk])) d[kk] = p[k][kk];
      if (Object.keys(d).length) out[k] = d;
    } else if (JSON.stringify(p[k]) !== JSON.stringify((base || {})[k])) out[k] = p[k];
  }
  return out;
}
function b64urlEncode(s) { return btoa(unescape(encodeURIComponent(s))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
function b64urlDecode(s) { s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length % 4) s += '='; return decodeURIComponent(escape(atob(s))); }
