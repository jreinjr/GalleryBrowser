// The Discover corpus: one city's shows, venues and artists (built by
// webdemo/discover_corpus.py into api/_data/<city>.json) plus the indexes and
// query functions the tools run over. Loaded once per function instance.
//
// Dates are ISO strings compared lexically, in the city's own timezone; a
// missing start or end date means the show runs open-ended (the app's rule).

import DATA from '../_data/index.js';
import { NEIGHBORHOOD_ALIASES, CONCEPTS, TRIGGERS, ARTIST_PROFILES, STOPWORDS } from './tables.js';

export const GALLERY_TIER_CUTOFF = { top: 25, notable: 100 };
export const DAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];
const DAY_LONG = { sun: 'Sunday', mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday' };

// ---------- dates in the city's timezone ----------
export function nowIn(tz, now = new Date()) {
  const f = new Intl.DateTimeFormat('en-US', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', weekday: 'short', hour12: false });
  const p = Object.fromEntries(f.formatToParts(now).map(x => [x.type, x.value]));
  const hour = p.hour === '24' ? '00' : p.hour;
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${hour}:${p.minute}`, weekday: p.weekday.toLowerCase().slice(0, 3) };
}
export function addDays(iso, n) {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
export function weekdayOf(iso) { return DAYS[new Date(iso + 'T00:00:00Z').getUTCDay()]; }
export function longDate(iso) {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}
export function shortDate(iso) {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric' });
}
// Fri..Sun containing or following `today`; on a weekend it starts today.
export function weekendOf(today) {
  const wd = weekdayOf(today);
  if (wd === 'sat') return { from: today, to: addDays(today, 1) };
  if (wd === 'sun') return { from: today, to: today };
  const toFri = (5 - DAYS.indexOf(wd) + 7) % 7;
  const fri = addDays(today, toFri);
  return { from: fri, to: addDays(fri, 2) };
}
export function fmtMinutes(min) {
  const h = Math.floor(min / 60), m = min % 60;
  const ap = h >= 12 ? 'pm' : 'am', h12 = ((h + 11) % 12) + 1;
  return m ? `${h12}:${String(m).padStart(2, '0')}${ap}` : `${h12}${ap}`;
}
export function hoursOn(venue, wd) {
  const h = venue.hours || {};
  if (!h.source) return { known: false, open: null, closes: null, text: h.byAppointment ? 'by appointment' : 'hours unknown' };
  const span = h.byDay && h.byDay[wd];
  if (!span) return { known: true, open: false, opens: null, closes: null, text: `closed ${DAY_LONG[wd]}` };
  return { known: true, open: true, opens: span[0], closes: span[1], text: `${DAY_LONG[wd].slice(0, 3)} ${fmtMinutes(span[0])}–${fmtMinutes(span[1])}` };
}

// ---------- text helpers ----------
export const fold = s => (s || '').normalize('NFKD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const stem = w => w.length > 4 ? w.replace(/(ies)$/, 'y').replace(/(sses|ches|shes|xes)$/, m => m.slice(0, -2)).replace(/([^s])s$/, '$1').replace(/ing$/, '') : w;
export function tokens(text) {
  return fold(text).replace(/[^a-z0-9' ]+/g, ' ').split(' ').map(w => w.replace(/^'+|'+$/g, '')).filter(w => w.length >= 2 && !STOPWORDS.has(w)).map(stem);
}
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
// A leading/trailing space in a concept term means a word boundary.
const termRe = t => { let x = esc(t.trim()); if (t.startsWith(' ')) x = '\\b' + x; if (t.endsWith(' ')) x += '\\b'; return x; };
const sentences = t => (t || '').split(/(?<=[.!?])\s+/);
export const clip = (t, n) => t.length <= n ? t : t.slice(0, n - 1).replace(/\s+\S*$/, '') + '…';

export function showStatus(s, today) {
  if (s.endDate && s.endDate < today) return 'past';
  if (s.startDate && s.startDate > today) return 'upcoming';
  return 'on_view';
}
export const displayName = s => s.artist || s.title;
export const datesText = s => `${s.startDate || '?'}..${s.endDate || 'open-ended'}`;

// ---------- the loaded corpus ----------
const cache = new Map();
export function cities() { return Object.keys(DATA); }
export function getCorpus(city) {
  if (cache.has(city)) return cache.get(city);
  const raw = DATA[city];
  if (!raw) return null;
  const c = buildIndex(raw);
  cache.set(city, c);
  return c;
}

const FIELD_W = { title: 3, artist: 3, venue: 2, hood: 1.5, description: 1, about: 1, roster: 1.5, judge: 0.8, focus: 1 };

function buildIndex(raw) {
  const venues = raw.venues;
  const showsById = new Map(raw.shows.map(s => [s.id, s]));
  const docs = raw.shows.map(s => {
    const v = venues[s.venueId] || {};
    const f = {
      title: s.title || '', artist: s.artist || '', venue: s.venueName || '', hood: s.neighborhood || '',
      description: s.description || '', about: v.about || '', roster: (v.roster || []).join(', '),
      judge: s.judgeRationale || '', focus: (v.programFocus || []).join(', '),
    };
    const tf = new Map();
    let len = 0;
    for (const [field, text] of Object.entries(f)) {
      for (const t of tokens(text)) { tf.set(t, (tf.get(t) || 0) + FIELD_W[field]); len += 1; }
    }
    return { s, f, tf, len };
  });
  const df = new Map();
  docs.forEach(d => d.tf.forEach((_, t) => df.set(t, (df.get(t) || 0) + 1)));
  const avgLen = docs.reduce((a, d) => a + d.len, 0) / Math.max(1, docs.length);

  const venueByName = new Map();
  Object.values(venues).forEach(v => {
    venueByName.set(fold(v.name), v.id);
    (v.aliases || []).forEach(a => venueByName.set(fold(a), v.id));
  });
  const artistByName = new Map();
  raw.artists.forEach(a => {
    artistByName.set(fold(a.name), a);
    (a.aliases || []).forEach(x => artistByName.set(fold(x), a));
  });
  const showsByVenue = new Map();
  raw.shows.forEach(s => { const l = showsByVenue.get(s.venueId) || []; l.push(s); showsByVenue.set(s.venueId, l); });
  const artistsByVenue = new Map();
  raw.artists.forEach(a => a.galleries.forEach(g => { const l = artistsByVenue.get(g.venueId) || []; l.push({ name: a.name, relation: g.relation }); artistsByVenue.set(g.venueId, l); }));
  const aliases = (NEIGHBORHOOD_ALIASES[raw.city] || []).flatMap(([h, al]) => al.map(a => [a, h])).sort((a, b) => b[0].length - a[0].length);
  return { ...raw, docs, df, avgLen, showsById, venueByName, artistByName, showsByVenue, artistsByVenue, hoodAliases: aliases };
}

// ---------- resolution ----------
export function resolveNeighborhoods(c, list) {
  const out = []; const unknown = [];
  for (const raw of list || []) {
    const exact = c.neighborhoods.find(h => fold(h) === fold(raw));
    if (exact) { out.push(exact); continue; }
    const q = ' ' + fold(raw) + ' ';
    const hit = c.hoodAliases.find(([a]) => q.includes(' ' + a + ' '));
    const part = hit ? hit[1] : c.neighborhoods.find(h => fold(h).split('/').some(p => p === fold(raw) || p.startsWith(fold(raw))));
    if (part) out.push(part); else unknown.push(raw);
  }
  return { neighborhoods: [...new Set(out)], unknown };
}
export function resolveVenue(c, idOrName) {
  if (!idOrName) return null;
  const key = fold(idOrName);
  if (c.venues[idOrName]) return c.venues[idOrName];
  if (c.venueByName.has(key)) return c.venues[c.venueByName.get(key)];
  const cands = Object.values(c.venues).filter(v => fold(v.name).includes(key) || key.includes(fold(v.name)));
  if (cands.length === 1) return cands[0];
  // distinctive-word match ("Vielmetter" ~ "Vielmetter Los Angeles")
  const words = tokens(idOrName).filter(w => w.length >= 4);
  const scored = Object.values(c.venues).map(v => ({ v, n: words.filter(w => tokens(v.name).includes(w)).length })).filter(x => x.n > 0).sort((a, b) => b.n - a.n);
  return scored.length && (scored.length === 1 || scored[0].n > scored[1].n) ? scored[0].v : null;
}
export function resolveArtist(c, name) {
  const key = fold(name || '');
  if (c.artistByName.has(key)) return { artist: c.artistByName.get(key), candidates: [] };
  const words = tokens(name).filter(w => w.length >= 3);
  const scored = c.artists.map(a => ({ a, n: words.filter(w => tokens(a.name).includes(w)).length })).filter(x => x.n > 0).sort((a, b) => b.n - a.n);
  if (scored.length && scored[0].n === words.length && (scored.length === 1 || scored[1].n < scored[0].n)) return { artist: scored[0].a, candidates: [] };
  return { artist: null, candidates: scored.slice(0, 6).map(x => x.a.name) };
}

// ---------- search ----------
// Concepts a query switches on. `required` are the ones the person named
// (each has to land somewhere in a record); an artist profile ("similar to
// Nam June Paik") adds optional concepts and strips the name from the query.
function conceptsFor(query) {
  let q = ' ' + fold(query) + ' ';
  const required = new Set();
  for (const [re, k] of TRIGGERS) if (re.test(q)) required.add(k);
  for (const [k, terms] of Object.entries(CONCEPTS)) if (terms.some(t => t.trim().length >= 5 && q.includes(t.trim()))) required.add(k);
  const optional = new Set();
  let reading = null;
  for (const [name, p] of Object.entries(ARTIST_PROFILES)) if (q.includes(name)) {
    p.concepts.forEach(k => optional.add(k));
    reading = `${name.replace(/\b\w/g, m => m.toUpperCase())}: ${p.reading}`;
    q = q.split(name).join(' ').replace(/\b(similar to|like|by|from|show|shows|artist|last week)\b/g, ' ');
  }
  return { concepts: [...new Set([...required, ...optional])], required: [...required], reading, query: q.trim() };
}
// Concept-term weights: descriptive prose counts most; a name in a title
// ("Hibiscus TV") is weak evidence of a medium.
const CONCEPT_W = { description: 1, judge: 0.8, about: 0.8, focus: 0.8, roster: 0.5, title: 0.6, artist: 0.4, venue: 0.3, hood: 0 };
function scoreDoc(c, doc, qTokens, conceptTerms) {
  let total = 0; const matched = [];
  const N = c.docs.length, k1 = 1.2, b = 0.75;
  for (const t of qTokens) {
    const tf = doc.tf.get(t);
    if (!tf) continue;
    const idf = Math.log(1 + (N - c.df.get(t) + 0.5) / (c.df.get(t) + 0.5));
    total += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * doc.len / c.avgLen));
    matched.push(t);
  }
  const hitTerms = [];
  for (const term of conceptTerms) {
    const re = new RegExp(termRe(term), 'gi');
    let weight = 0;
    for (const [field, text] of Object.entries(doc.f)) {
      if (!text || !CONCEPT_W[field]) continue;
      const n = (text.match(re) || []).length;
      if (n) weight += Math.min(n, 3) * CONCEPT_W[field];
    }
    if (weight > 0) { total += Math.min(weight, 3); hitTerms.push(term); }
  }
  if (total > 0 && doc.s.featured) total += 0.3;
  return { total, matched, hitTerms };
}
function grounding(doc, matched, hitTerms) {
  const pats = [...hitTerms.map(termRe), ...matched.map(t => '\\b' + esc(t))];
  if (!pats.length) return null;
  const re = new RegExp(pats.join('|'), 'i');
  for (const field of ['description', 'judge', 'about', 'roster', 'focus', 'title', 'artist']) {
    const text = doc.f[field];
    if (!text || !re.test(text)) continue;
    const sent = sentences(text).find(x => re.test(x)) || text;
    return { field: field === 'judge' ? 'curation note' : field === 'about' ? 'gallery about' : field === 'focus' ? 'gallery program' : field === 'roster' ? 'gallery roster' : field, snippet: clip(sent.trim(), 150) };
  }
  return null;
}

// Strict kinds (2026-09-03): gallery is the commercial-gallery kind only, museum the
// museum kind only; nonprofits, university galleries, project spaces and other venues
// are reachable with 'any' and carry their kind as a flag / catalog tag.
const KIND_OK = { gallery: v => v.kind === 'gallery', museum: v => v.kind === 'museum', any: () => true };
const kindTag = k => (k && k !== 'gallery' ? k : null);

export function findShows(c, input, today) {
  const inp = input || {};
  const errors = [];
  const hoods = resolveNeighborhoods(c, inp.neighborhoods);
  if (hoods.unknown.length) errors.push(`unknown neighborhoods: ${hoods.unknown.join(', ')} (use: ${c.neighborhoods.join(' | ')})`);
  const venue = inp.venue ? resolveVenue(c, inp.venue) : null;
  if (inp.venue && !venue) errors.push(`no venue matches "${inp.venue}"`);
  const limit = Math.max(1, Math.min(40, Number(inp.limit) || 12));
  const status = inp.status || 'on_view';
  const query = (inp.query || '').trim();
  const cf = query ? conceptsFor(query) : { concepts: [], required: [], reading: null, query: '' };
  const { concepts, required, reading } = cf;
  const conceptTerms = [...new Set(concepts.flatMap(k => CONCEPTS[k]))];
  const qTokens = [...new Set(tokens(cf.query))];
  const artistKey = inp.artist ? fold(inp.artist) : null;

  const passes = (s, relax) => {
    const v = c.venues[s.venueId] || {};
    const st = showStatus(s, today);
    if (status === 'on_view' && st !== 'on_view') return false;
    if (status === 'upcoming' && st !== 'upcoming') return false;
    if (status === 'any' && st === 'past') return false;
    if (!relax.hood && hoods.neighborhoods.length && !hoods.neighborhoods.includes(s.neighborhood)) return false;
    if (inp.venue_kind && inp.venue_kind !== 'any' && !(KIND_OK[inp.venue_kind] || KIND_OK.any)({ kind: s.venueKind })) return false;
    if (inp.gallery_tier === 'top' && s.galleryTier !== 'top') return false;
    if (inp.gallery_tier === 'notable' && s.galleryTier === 'listed') return false;
    if (inp.show_tier === 'picks' && !s.editorsPick) return false;
    if (inp.show_tier === 'featured' && !s.featured) return false;
    if (!relax.dates) {
      if (inp.closing_by && !(s.endDate && s.endDate <= inp.closing_by && s.endDate >= today)) return false;
      if (inp.opening_from && !(s.startDate && s.startDate >= inp.opening_from)) return false;
      if (inp.reception_from || inp.reception_to) {
        if (!s.receptionDate) return false;
        if (inp.reception_from && s.receptionDate < inp.reception_from) return false;
        if (inp.reception_to && s.receptionDate > inp.reception_to) return false;
      }
      if (inp.open_on) { const h = hoursOn(v, inp.open_on); if (h.known && !h.open) return false; }
    }
    if (venue && s.venueId !== venue.id) return false;
    if (artistKey && !fold(s.artist || '').includes(artistKey) && !(s.artists || []).some(a => fold(a).includes(artistKey))) return false;
    return true;
  };
  const run = relax => {
    const hits = [];
    for (const doc of c.docs) {
      if (!passes(doc.s, relax)) continue;
      if (query) {
        const sc = scoreDoc(c, doc, qTokens, conceptTerms);
        if (sc.total <= 0) continue;
        // with several concepts asked for, each has to land unless relaxed
        if (!relax.concepts && required.length > 1 && required.some(k => !CONCEPTS[k].some(t => sc.hitTerms.includes(t)))) continue;
        hits.push({ doc, score: sc.total, why: grounding(doc, sc.matched, sc.hitTerms) });
      } else hits.push({ doc, score: 0, why: null });
    }
    if (inp.reception_from || inp.reception_to) hits.sort((a, b) => (a.doc.s.receptionDate || '').localeCompare(b.doc.s.receptionDate || '') || (a.doc.s.rank || 999) - (b.doc.s.rank || 999));
    else if (inp.closing_by) hits.sort((a, b) => (a.doc.s.endDate || '').localeCompare(b.doc.s.endDate || '') || (a.doc.s.rank || 999) - (b.doc.s.rank || 999));
    else if (query) hits.sort((a, b) => b.score - a.score || (a.doc.s.rank || 999) - (b.doc.s.rank || 999));
    else hits.sort((a, b) => (a.doc.s.rank || 999) - (b.doc.s.rank || 999));
    return hits;
  };
  let hits = run({});
  const strictCount = hits.length;
  let relaxed = null;
  if (!hits.length && hoods.neighborhoods.length) { hits = run({ hood: true }); if (hits.length) relaxed = 'neighborhood'; }
  if (!hits.length && (inp.closing_by || inp.opening_from || inp.reception_from || inp.reception_to || inp.open_on)) { hits = run({ hood: true, dates: true }); if (hits.length) relaxed = 'dates'; }
  if (!hits.length && required.length > 1) { hits = run({ hood: true, dates: true, concepts: true }); if (hits.length) relaxed = 'concepts'; }
  return {
    count: hits.length, strict_count: strictCount, relaxed,
    applied: { query: query || null, concepts, reading, neighborhoods: hoods.neighborhoods, status, venue: venue ? venue.id : null,
      venue_kind: inp.venue_kind || 'any', gallery_tier: inp.gallery_tier || 'any', show_tier: inp.show_tier || 'any',
      closing_by: inp.closing_by || null, opening_from: inp.opening_from || null, reception_from: inp.reception_from || null, reception_to: inp.reception_to || null, open_on: inp.open_on || null, artist: inp.artist || null },
    errors: errors.length ? errors : undefined,
    hits: hits.slice(0, limit).map(h => hitView(c, h.doc.s, today, h.why)),
  };
}
export function hitView(c, s, today, why) {
  const v = c.venues[s.venueId] || {};
  const flags = [];
  if (s.editorsPick) flags.push('editors_pick'); else if (s.featured) flags.push('featured');
  if (kindTag(s.venueKind)) flags.push(kindTag(s.venueKind));
  if (s.galleryTier === 'top') flags.push('top_gallery'); else if (s.galleryTier === 'notable') flags.push('notable_gallery');
  if (s.datesNote || (s.datesConfidence && s.datesConfidence !== 'high')) flags.push('dates_approximate');
  const out = { id: s.id, artist: s.artist || null, title: s.title, venue: s.venueName, venue_id: s.venueId, neighborhood: s.neighborhood,
    dates: datesText(s), status: showStatus(s, today), flags };
  if (s.receptionDate) { out.reception = s.receptionDate; out.reception_kind = s.receptionKind; out.reception_text = s.receptionText; }
  if (why) out.why = why;
  return out;
}

export function findVenues(c, input, today) {
  const inp = input || {};
  const hoods = resolveNeighborhoods(c, inp.neighborhoods);
  const q = inp.query ? fold(inp.query) : null;
  const out = [];
  for (const v of Object.values(c.venues)) {
    if (hoods.neighborhoods.length && !hoods.neighborhoods.includes(v.neighborhood)) continue;
    if (inp.kind && inp.kind !== 'any' && !(KIND_OK[inp.kind] || KIND_OK.any)(v)) continue;
    if (inp.tier === 'top' && v.tier !== 'top') continue;
    if (inp.tier === 'notable' && v.tier === 'listed') continue;
    const onView = (c.showsByVenue.get(v.id) || []).filter(s => showStatus(s, today) === 'on_view');
    if (inp.with_shows_only && !onView.length) continue;
    if (q) {
      const hay = fold([v.name, v.about, (v.programFocus || []).join(' '), (v.roster || []).join(' ')].join(' '));
      if (!tokens(q).some(t => hay.includes(t))) continue;
    }
    out.push({ id: v.id, name: v.name, kind: v.kind, neighborhood: v.neighborhood, tier: v.tier, rank: v.rank,
      shows_on_view: onView.map(s => ({ id: s.id, name: displayName(s) })), program: (v.programFocus || []).slice(0, 4) });
  }
  out.sort((a, b) => (a.rank || 9999) - (b.rank || 9999));
  const limit = Math.max(1, Math.min(40, Number(inp.limit) || 20));
  return { count: out.length, neighborhoods: hoods.neighborhoods, unknown_neighborhoods: hoods.unknown, venues: out.slice(0, limit) };
}

export function getDetails(c, input, today) {
  const kind = input && input.kind, key = input && String(input.id_or_name || '').trim();
  if (!key) return { error: 'id_or_name is required' };
  if (kind === 'show') {
    let s = c.showsById.get(key) || c.showsById.get(key.replace(/^[a-z-]+\//, ''));
    if (!s) {
      const k = fold(key);
      const cands = c.shows.filter(x => fold(x.title).includes(k) || fold(x.artist || '').includes(k));
      if (cands.length === 1) s = cands[0];
      else return { error: `no show with id "${key}"`, candidates: cands.slice(0, 6).map(x => ({ id: x.id, name: displayName(x), venue: x.venueName })) };
    }
    const v = c.venues[s.venueId] || {};
    return { ...hitView(c, s, today, null), description: s.description, dates_note: s.datesNote || null, source_urls: s.sourceUrls,
      curation_rank: s.rank, curation_note: s.judgeRationale || null,
      venue_details: { id: v.id, address: [v.address, v.addressDetail].filter(Boolean).join(', '), hours: v.hoursText, hours_today: hoursOn(v, weekdayOf(today)).text, website: v.website, about: v.about || null } };
  }
  if (kind === 'venue') {
    const v = resolveVenue(c, key);
    if (!v) return { error: `no venue matches "${key}"`, candidates: findVenues(c, { query: key }, today).venues.slice(0, 6).map(x => ({ id: x.id, name: x.name })) };
    const shows = (c.showsByVenue.get(v.id) || []).filter(s => showStatus(s, today) !== 'past');
    const byDay = {}; DAYS.forEach(d => { byDay[d] = hoursOn(v, d).text; });
    return { id: v.id, name: v.name, kind: v.kind, neighborhood: v.neighborhood, tier: v.tier, rank: v.rank,
      address: [v.address, v.addressDetail].filter(Boolean).join(', '), website: v.website, phone: v.phone,
      hours: v.hoursText, hours_by_day: byDay, hours_source: v.hours && v.hours.source, about: v.about || null,
      program_focus: v.programFocus || [], roster: v.roster || [],
      artists_on_file: (c.artistsByVenue.get(v.id) || []).slice(0, 30),
      shows: shows.map(s => hitView(c, s, today, null)) };
  }
  if (kind === 'artist') {
    const { artist, candidates } = resolveArtist(c, key);
    if (!artist) {
      // artists on show records but not in the dataset still answer
      const k = fold(key);
      const onShows = c.shows.filter(s => fold(s.artist || '').includes(k) && showStatus(s, today) !== 'past');
      if (onShows.length) return { name: key, note: 'not in the artists dataset; from show records only', shows: onShows.map(s => hitView(c, s, today, null)) };
      return { error: `no artist matches "${key}"`, candidates };
    }
    return { name: artist.name, aliases: artist.aliases,
      galleries: artist.galleries.map(g => ({ venue_id: g.venueId, venue: (c.venues[g.venueId] || {}).name || g.venueId, relation: g.relation, since: g.since })),
      shows_on_view: artist.showsCurrent.map(id => c.showsById.get(id)).filter(Boolean).map(s => hitView(c, s, today, null)),
      show_history: artist.showsHistory.map(h => ({ venue: (c.venues[h.venueId] || {}).name || h.venueId, title: h.title, year: h.year })) };
  }
  return { error: 'kind must be show, venue or artist' };
}

// ---------- the catalog (system prompt) ----------
export function catalog(c, today) {
  const line = s => {
    const v = c.venues[s.venueId] || {};
    const names = s.artists && s.artists.length ? s.artists.slice(0, 3).join(', ') + (s.artists.length > 3 ? ` +${s.artists.length - 3}` : '') : (s.artist || '-');
    const tags = [s.neighborhood, kindTag(v.kind), s.galleryTier === 'top' ? 'T' : s.galleryTier === 'notable' ? 'N' : null].filter(Boolean).join(', ');
    const approx = s.datesNote || (s.datesConfidence && s.datesConfidence !== 'high') ? ' ~' : '';
    const parts = [s.id, names, clip(s.title, 70), `${s.venueName} (${tags})`, `${s.startDate || '?'}..${s.endDate || 'open'}${approx}`];
    if (s.receptionDate && s.receptionDate >= today) parts.push(`${s.receptionKind === 'opening' ? 'rcpt' : s.receptionKind} ${s.receptionDate}`);
    if (s.editorsPick) parts.push('pick'); else if (s.featured) parts.push('feat');
    return parts.join(' | ');
  };
  const byRank = (a, b) => (a.rank || 999) - (b.rank || 999);
  const onView = c.shows.filter(s => showStatus(s, today) === 'on_view').sort(byRank);
  const soon = addDays(today, 30);
  const upcoming = c.shows.filter(s => showStatus(s, today) === 'upcoming' && s.startDate <= soon).sort((a, b) => a.startDate.localeCompare(b.startDate));
  return [
    '<catalog>',
    '# One line per show: id | artists | title | venue (neighborhood[, museum|nonprofit|university|project_space|other — absent means a commercial gallery][, T=top gallery|N=notable]) | start..end (~ = dates approximate) | rcpt/talk/closing DATE | pick|feat',
    `## ON VIEW (${onView.length})`, ...onView.map(line),
    `## UPCOMING within 30 days (${upcoming.length})`, ...upcoming.map(line),
    '</catalog>',
  ].join('\n');
}
