// Walking route over a set of shows: one stop per venue, greedy nearest
// neighbour from the top-ranked stop, checked against each venue's hours for
// the weekday (precomputed by discover_corpus.py). Best-effort by design: 4.8
// km/h, 40 minutes a stop, a stop is skipped when it would close within 30
// minutes of arrival. Times live in this result only; a saved route list keeps
// just the order.

import { DAYS, hoursOn, fmtMinutes, weekdayOf, displayName, galleryRank } from './corpus.js';

const WALK_KMH = 4.8, DWELL = 40, MIN_VISIT = 30;

function km(a, b) {
  const R = 6371, r = x => x * Math.PI / 180;
  const x = Math.sin(r(b.lat - a.lat) / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(r(b.lng - a.lng) / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}
const parseHHMM = t => { const m = /^(\d{1,2}):(\d{2})$/.exec(t || ''); return m ? Number(m[1]) * 60 + Number(m[2]) : null; };
const hhmm = min => `${String(Math.floor(min / 60) % 24).padStart(2, '0')}:${String(min % 60).padStart(2, '0')}`;

export function planRoute(c, input, today) {
  const inp = input || {};
  const ids = (Array.isArray(inp.show_ids) ? inp.show_ids : []).map(x => String(x).replace(/^[a-z-]+\//, ''));
  const weekday = DAYS.includes(inp.weekday) ? inp.weekday : weekdayOf(today);
  const startMin = parseHHMM(inp.start_time) ?? 12 * 60;
  const maxStops = Math.max(2, Math.min(8, Number(inp.max_stops) || 6));
  const unknown = ids.filter(id => !c.showsById.has(id));
  const byVenue = new Map();
  for (const id of ids) {
    const s = c.showsById.get(id);
    if (!s) continue;
    // one stop per venue: the first of its shows the model listed
    if (!byVenue.has(s.venueId)) byVenue.set(s.venueId, s);
  }
  const cands = [...byVenue.values()].map(s => {
    const v = c.venues[s.venueId] || {};
    return { s, v, hours: hoursOn(v, weekday) };
  }).filter(x => x.v.lat != null);
  const skipped = [];
  const pool = cands.filter(x => {
    if (x.hours.known && !x.hours.open) { skipped.push({ id: x.s.id, venue: x.v.name, reason: x.hours.text }); return false; }
    return true;
  }).sort((a, b) => galleryRank(c, a.s) - galleryRank(c, b.s));
  if (!pool.length) return { weekday, stops: [], skipped, unknown_ids: unknown, note: 'no candidate is open that day' };

  const stops = [];
  const first = pool.shift();
  let clock = Math.max(startMin, first.hours.opens ?? startMin);
  stops.push({ ...first, arrive: clock, walkKm: 0, walkMin: 0 });
  clock += DWELL;
  while (pool.length && stops.length < maxStops) {
    const last = stops[stops.length - 1].v;
    let bi = -1, bd = Infinity;
    pool.forEach((p, i) => {
      const d = km(last, p.v), arrive = clock + d / WALK_KMH * 60;
      if (p.hours.closes != null && arrive + MIN_VISIT > p.hours.closes) return;
      if (d < bd) { bd = d; bi = i; }
    });
    if (bi < 0) break;
    const st = pool.splice(bi, 1)[0];
    const walkMin = Math.round(bd / WALK_KMH * 60);
    clock += walkMin;
    if (st.hours.opens != null && clock < st.hours.opens) clock = st.hours.opens;
    stops.push({ ...st, arrive: clock, walkKm: bd, walkMin });
    clock += DWELL;
  }
  pool.forEach(p => skipped.push({ id: p.s.id, venue: p.v.name, reason: p.hours.closes != null ? `closes ${fmtMinutes(p.hours.closes)}` : 'out of time' }));
  const totalKm = stops.reduce((a, s) => a + s.walkKm, 0);
  return {
    weekday, start: hhmm(stops[0].arrive), start_label: fmtMinutes(stops[0].arrive), end: hhmm(clock), end_label: fmtMinutes(clock),
    total_km: Math.round(totalKm * 10) / 10,
    stops: stops.map(st => ({ id: st.s.id, name: displayName(st.s), venue: st.v.name, venue_id: st.v.id, neighborhood: st.v.neighborhood,
      arrive: hhmm(st.arrive), arrive_label: fmtMinutes(st.arrive), walk_min: st.walkMin, walk_km: Math.round(st.walkKm * 10) / 10,
      hours: st.hours.text, hours_known: st.hours.known })),
    skipped, unknown_ids: unknown,
  };
}
