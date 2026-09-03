/* Design lab: a tiny keyword retriever standing in for the future RAG layer.
 * Pure functions over the payload so the same answers render in the static
 * frames, the live Ask phone, and a node check:
 *   node -e "const A=require('./webdemo/lab/lab-ask.js'); ..."
 * No model call; the point is honest counts and honest grounding lines. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.LabAsk = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DAY = 86400e3;
  const STOP = new Set(('a an and are as at be by for from i in is it of on or the to what which who with show shows me some any ' +
    'current currently now there that this these those find give list like liked really last week want would could please about ' +
    'art artist artists gallery galleries exhibition exhibitions museum museums also else other similar new near around ' +
    'showing neighborhood neighbourhood area nearby close next best good great recommend recommendations suggestions something anything things see visit going right week').split(' '));

  // Payload neighborhood ← query aliases. Longer aliases are tried first so
  // "west hollywood" never reads as Hollywood.
  const HOODS = [
    ['West Hollywood/Fairfax', ['west hollywood', 'weho', 'fairfax', 'melrose']],
    ['Hollywood', ['hollywood']],
    ['Downtown/Arts District', ['arts district', 'downtown', 'dtla', 'little tokyo', 'bunker hill']],
    ['Chinatown/East LA', ['chinatown', 'east la', 'boyle heights', 'chung king']],
    ['Los Feliz/NELA', ['los feliz', 'silver lake', 'silverlake', 'highland park', 'eagle rock', 'nela', 'glassell park', 'frogtown']],
    ['Beverly Hills', ['beverly hills']],
    ['Mid-Wilshire/Koreatown', ['koreatown', 'mid-wilshire', 'mid wilshire', 'miracle mile', 'wilshire', 'hancock park']],
    ['Culver City/West Adams', ['culver city', 'west adams']],
    ['Santa Monica/Venice', ['santa monica', 'venice']],
    ['Westside/Brentwood', ['brentwood', 'westwood', 'westside', 'west la']],
    ['South LA/Inglewood', ['inglewood', 'south la', 'leimert park', 'south central']],
    ['Pasadena/San Gabriel', ['pasadena', 'san gabriel', 'altadena']],
  ];

  // Concept → terms searched in the corpus. Triggers → words in the query that
  // switch a concept on.
  const CONCEPTS = {
    queer: ['queer', 'lgbtq', 'transgender', 'trans ', 'nonbinary', 'non-binary', 'gay ', 'lesbian', 'drag ', 'gender identity', 'gender nonconforming', 'genderqueer', 'gay liberation'],
    video: ['video', 'media art', 'new media', 'television', ' tv', 'screens', 'screen-based', 'projection', 'broadcast', 'moving image', 'monitor'],
    interactive: ['interactive', 'electronic', 'sound art', 'sound installation', 'sound work', 'soundscape', 'audio', 'kinetic', 'generative', 'sensor ', 'sensors', 'software', 'neon', 'robot', 'machine learning', 'algorithm'],
    photography: ['photograph', 'photo', 'camera', 'darkroom', 'gelatin'],
    ceramics: ['ceramic', 'clay', 'porcelain', 'stoneware', 'glaze'],
    painting: ['painting', 'painter', 'canvas', 'oil on'],
    sculpture: ['sculpture', 'sculptural', 'bronze', 'carved', 'cast '],
    textile: ['textile', 'weav', 'quilt', 'fabric', 'embroider', 'tapestry'],
    abstract: ['abstract', 'abstraction', 'geometric'],
    figurative: ['figurative', 'figure', 'portrait'],
    latinx: ['latinx', 'latino', 'latina', 'chicano', 'chicana', 'mexican'],
    black: ['black artist', 'black american', 'african american', 'black californian', 'diaspora'],
    japanese: ['japanese', 'japan', 'tokyo'],
    korean: ['korean', 'korea'],
    performance: ['performance', 'performative'],
    installation: ['installation', 'immersive'],
    landscape: ['landscape', 'nature', 'desert', 'ocean'],
    fluxus: ['fluxus', 'happening', 'conceptual'],
    political: ['political', 'protest', 'activis', 'liberation'],
  };
  const TRIGGERS = [
    [/\bqueer|lgbtq|trans(gender)?\b|nonbinary|gay\b|lesbian/, 'queer'],
    [/\bvideo|media art|new media|television|\btv\b|moving image|film\b/, 'video'],
    [/interactive|electronic|kinetic|generative|sound art|digital/, 'interactive'],
    [/photograph|photo\b|photos\b|photographers?/, 'photography'],
    [/ceramic|clay|pottery|porcelain/, 'ceramics'],
    [/painting|paintings|painters?/, 'painting'],
    [/sculptur/, 'sculpture'],
    [/textile|weaving|quilt|fiber/, 'textile'],
    [/abstract/, 'abstract'],
    [/figurative|portrait/, 'figurative'],
    [/latinx|latino|latina|chican/, 'latinx'],
    [/black artists?|african american/, 'black'],
    [/japanese|japan\b/, 'japanese'],
    [/korean|korea\b/, 'korean'],
    [/performance/, 'performance'],
    [/installation|immersive/, 'installation'],
    [/landscape|nature/, 'landscape'],
    [/political|protest|activis/, 'political'],
  ];
  // "similar to <artist>" reads the artist as a bundle of concepts.
  const ARTIST_PROFILES = {
    'nam june paik': { concepts: ['video', 'fluxus'], reading: 'video, television and electronic media work' },
    'paik': { concepts: ['video', 'fluxus'], reading: 'video, television and electronic media work' },
    'james turrell': { concepts: ['interactive', 'installation'], reading: 'light and immersive installation' },
    'yayoi kusama': { concepts: ['installation', 'painting'], reading: 'immersive installation and pattern painting' },
    'cindy sherman': { concepts: ['photography', 'figurative'], reading: 'staged photography and portraiture' },
    'bruce nauman': { concepts: ['video', 'performance', 'fluxus'], reading: 'video, performance and conceptual work' },
    'ed ruscha': { concepts: ['painting', 'photography'], reading: 'text painting and deadpan photography' },
    'mark rothko': { concepts: ['abstract', 'painting'], reading: 'abstract painting' },
  };
  // Backend artists dataset (content/artists/los-angeles.json), the four LA
  // show artists it currently links to a representing gallery. A fixture.
  const ROSTER = {
    'evan whale': 'Represented by Tyler Park Presents since 2020; three solo shows there since 2020',
    'lee mullican': 'Represented by Marc Selwyn Fine Art (estate, since 2026)',
    'torbjørn rødland': 'Represented by David Kordansky Gallery (since 2026)',
    'william wegman': 'Represented by Marc Selwyn Fine Art (since 2026)',
  };
  const NOT_OPENING = /\b(talk|conversation|closing|panel|screening|performance|walkthrough|walk-through|tour|brunch)\b/i;

  const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
  const RECEPTION_RE = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?/i;
  const parseDate = str => { if (!str) return null; const [y, m, d] = str.split('-').map(Number); return new Date(y, m - 1, d); };
  // Same rule as app.js receptionDate: first "Month day[, year]"; a missing
  // year is the opening year, rolled forward when it lands before the opening.
  function receptionDate(s, today) {
    if (!s.reception) return null;
    const m = RECEPTION_RE.exec(s.reception);
    if (!m) return null;
    const month = MONTHS.findIndex(name => name.startsWith(m[1].slice(0, 3).toLowerCase()));
    const day = Number(m[2]);
    if (month < 0 || day < 1 || day > 31) return null;
    const start = parseDate(s.startDate);
    const year = m[3] ? Number(m[3]) : (start ? start.getFullYear() : today.getFullYear());
    let d = new Date(year, month, day);
    if (!m[3] && start && start - d > 60 * DAY) d = new Date(year + 1, month, day);
    return d;
  }
  const isActive = (s, today) => {
    const start = parseDate(s.startDate), end = parseDate(s.endDate);
    return (!start || start <= today) && (!end || end >= today);
  };
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
  const DOW = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const DOW3 = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

  // Is the venue open on weekday `dow` (0 = Sunday)? Reads the hours lines the
  // way a person would: "Tue - Sat 11am to 6pm", "Saturday: 11:00 AM – 4:00 PM",
  // "Daily", "Sat - Sun", "Check site". Returns { open, opens, closes, line }.
  function openOn(v, dow) {
    const lines = (v.hours || []).map(h => h.toLowerCase());
    const name = DOW[dow], short = DOW3[dow];
    const timeRange = line => {
      const t = [...line.matchAll(/(\d{1,2})(?::(\d{2}))?\s*(am|pm)/g)].map(m => (Number(m[1]) % 12) + (m[3] === 'pm' ? 12 : 0) + (m[2] ? Number(m[2]) / 60 : 0));
      return t.length >= 2 ? { opens: t[0], closes: t[1] } : {};
    };
    for (const line of lines) {
      if (line.startsWith(name) || line.startsWith(short + ' ') || line.startsWith(short + ':')) {
        if (/closed/.test(line)) return { open: false, line };
        return { open: true, line, ...timeRange(line) };
      }
      const span = /\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s*[-–]\s*(mon|tue|wed|thu|fri|sat|sun)/.exec(line);
      if (span) {
        const a = DOW3.indexOf(span[1]), b = DOW3.indexOf(span[2]);
        const inside = a <= b ? dow >= a && dow <= b : dow >= a || dow <= b;
        if (inside) return { open: true, line, ...timeRange(line) };
        if (/closed/.test(line) && !inside) continue;
        continue;
      }
      if (/daily|every day|7 days/.test(line)) return { open: true, line, ...timeRange(line) };
      if (/check site|by appointment/.test(line)) return { open: null, line };
    }
    if (!lines.length) return { open: null, line: '' };
    return { open: false, line: lines[0] };
  }
  const fmtHour = h => { const hh = Math.floor(h), mm = Math.round((h - hh) * 60); const ap = hh >= 12 ? 'pm' : 'am'; const h12 = ((hh + 11) % 12) + 1; return mm ? `${h12}:${String(mm).padStart(2, '0')}${ap}` : `${h12}${ap}`; };

  const km = (a, b) => {
    const R = 6371, dLat = (b.lat - a.lat) * Math.PI / 180, dLng = (b.lng - a.lng) * Math.PI / 180;
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * Math.PI / 180) * Math.cos(b.lat * Math.PI / 180) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(x));
  };
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // A leading/trailing space in a term means a word boundary ("trans " must
  // not read "Transportation").
  const termSrc = t => { let x = esc(t); if (t.startsWith(' ')) x = '\\b' + x.slice(1); if (t.endsWith(' ')) x = x.slice(0, -1) + '\\b'; return x; };
  const sentences = t => (t || '').split(/(?<=[.!?])\s+/);
  const clip = (t, n) => t.length <= n ? t : t.slice(0, n - 1).replace(/\s+\S*$/, '') + '…';

  const SOURCE_LABEL = { title: 'show title', artist: 'artist line', description: 'show description', about: 'gallery about', hood: 'venue neighborhood', roster: 'artist roster (fixture)' };
  const FIELD_W = { title: 3, artist: 3, venue: 2, hood: 2, description: 1, about: 1, roster: 2 };

  function makeAsk(shows, venuesById, opts) {
    const o = opts || {};
    const today = o.today || new Date();
    const aboutOf = s => (venuesById && venuesById[s.venueId] && venuesById[s.venueId].about) || s.venue.about || '';
    const rosterOf = s => (s.artist || '').split(/,|\band\b|&/).map(n => ROSTER[n.trim().toLowerCase()]).filter(Boolean).join(' ');
    const docs = shows.map(s => ({
      s,
      f: { title: s.title || '', artist: s.artist || '', venue: s.venue.name || '', hood: s.venue.neighborhood || '', description: s.description || '', about: aboutOf(s), roster: rosterOf(s) },
    }));

    function parse(query) {
      let q = ' ' + query.toLowerCase().replace(/[“”"’]/g, "'").replace(/\s+/g, ' ') + ' ';
      const c = { hoods: [], concepts: [], required: [], terms: [], active: false, window: null, receptions: false, kind: null, route: false, artist: null, reading: null };
      // similar to <artist>
      const sim = /(?:similar to|like|by|from|loved?|liked|enjoyed)\s+(?:a |an |the )?(?:show (?:by|from) |show )?([a-z][a-z .'-]{3,40}?)(?:\s+(?:show|exhibition|last|at|,|\.|what|and|;|—)|\s*$)/.exec(q.trim());
      const names = Object.keys(ARTIST_PROFILES).sort((a, b) => b.length - a.length);
      for (const n of names) if (new RegExp('\\b' + esc(n) + '\\b').test(q)) { c.artist = n; break; }
      if (!c.artist && sim && sim[1] && ARTIST_PROFILES[sim[1].trim()]) c.artist = sim[1].trim();
      if (c.artist) {
        const p = ARTIST_PROFILES[c.artist];
        c.concepts.push(...p.concepts); c.reading = p.reading;
        q = q.replace(c.artist, ' ');
      }
      // neighborhoods (longest alias first, consumed from the query)
      const aliasList = HOODS.flatMap(([h, al]) => al.map(a => [a, h])).sort((a, b) => b[0].length - a[0].length);
      for (const [a, h] of aliasList) if (q.includes(' ' + a + ' ') || q.includes(' ' + a + ',') || q.includes(' ' + a + '.') || q.includes(' ' + a + '?')) { if (!c.hoods.includes(h)) c.hoods.push(h); q = q.split(a).join(' '); }
      // time
      if (/\b(now|current|currently|on view|open now|today)\b/.test(q)) c.active = true;
      const dow = today.getDay();
      const toFri = ((5 - dow) + 7) % 7;
      if (/this weekend|the weekend|weekend/.test(q)) { const fri = addDays(today, toFri === 0 && dow === 5 ? 0 : toFri); c.window = { from: dow === 6 || dow === 0 ? today : fri, to: addDays(dow === 6 || dow === 0 ? today : fri, dow === 0 ? 0 : dow === 6 ? 1 : 2), label: 'this weekend' }; }
      else if (/\bsaturday\b/.test(q)) { const d = addDays(today, ((6 - dow) + 7) % 7); c.window = { from: d, to: d, label: 'Saturday' }; }
      else if (/\bsunday\b/.test(q)) { const d = addDays(today, ((0 - dow) + 7) % 7); c.window = { from: d, to: d, label: 'Sunday' }; }
      else if (/\btonight\b|\btoday\b/.test(q)) c.window = { from: today, to: today, label: 'today' };
      else if (/this week\b/.test(q)) c.window = { from: today, to: addDays(today, 6), label: 'this week' };
      else if (/this month\b/.test(q)) c.window = { from: today, to: addDays(today, 30), label: 'this month' };
      if (/\b(opening|openings|reception|receptions|vernissage)\b/.test(q)) c.receptions = true;
      if (/\bclosing (soon|this)|last chance|ends? (soon|this)/.test(q)) c.closing = true;
      if (/\bmuseums?\b/.test(q)) c.kind = 'museum';
      else if (/\bgaller(y|ies)\b/.test(q) && !/\bmuseums?\b/.test(q)) c.kind = 'gallery';
      if (/\b(plan|itinerary|route|tour|walk|day out|afternoon|morning|day in|a day)\b/.test(q)) c.route = true;
      // concepts
      for (const [re, k] of TRIGGERS) if (re.test(q)) { if (!c.concepts.includes(k)) c.concepts.push(k); c.required.push(k); }
      // leftover free terms (searched as-is)
      const consumed = new Set(TRIGGERS.flatMap(([re]) => q.match(new RegExp(re.source, 'g')) || []));
      q.replace(/[^a-z0-9' ]/g, ' ').split(' ').filter(w => w.length >= 3 && !STOP.has(w) && !consumed.has(w) && !DOW.includes(w) && !['weekend', 'plan', 'opening', 'openings', 'reception', 'receptions', 'saturday', 'district'].includes(w))
        .forEach(w => { if (!c.terms.includes(w)) c.terms.push(w); });
      return c;
    }

    function score(doc, terms, termConcept) {
      let total = 0; const hits = []; const covered = new Set();
      for (const t of terms) {
        const re = new RegExp(termSrc(t), 'gi');
        for (const [field, text] of Object.entries(doc.f)) {
          if (!text) continue;
          const n = (text.match(re) || []).length;
          if (!n) continue;
          total += FIELD_W[field] * Math.min(n, 3) + (field === 'title' || field === 'artist' ? 2 : 0);
          hits.push({ field, term: t, n });
          if (termConcept && termConcept[t]) covered.add(termConcept[t]);
        }
      }
      if (total > 0 && doc.s.featured) total += 0.5;
      return { total: total > 0 ? total - (doc.s.rank || 200) / 1000 : 0, hits, covered };
    }
    function grounding(doc, hits) {
      const out = []; const seen = new Set();
      const order = ['description', 'about', 'roster', 'title', 'artist', 'hood'];
      for (const field of order) {
        const hs = hits.filter(h => h.field === field);
        if (!hs.length || seen.has(field)) continue;
        seen.add(field);
        const text = doc.f[field];
        const re = new RegExp(hs.map(h => termSrc(h.term)).join('|'), 'i');
        const sent = sentences(text).find(x => re.test(x)) || text;
        out.push({ field, source: SOURCE_LABEL[field] || field, term: hs[0].term.trim(), snippet: clip(sent.trim(), 150) });
      }
      return out;
    }

    function passes(doc, c, relax) {
      const s = doc.s;
      if (c.hoods.length && !relax.hood && !c.hoods.includes(s.venue.neighborhood)) return false;
      if (c.kind === 'museum' && !s.venue.isMuseum) return false;
      if (c.kind === 'gallery' && s.venue.isMuseum) return false;
      if (c.active && !isActive(s, today)) return false;
      if (c.receptions) {
        const d = receptionDate(s, today);
        if (!d) return false;
        if (c.window && !relax.window) { if (d < c.window.from || d > c.window.to) return false; }
        else if (d < today) return false;
      } else if (c.window && !relax.window) {
        // a show is relevant to a day window if it is on view during it
        const start = parseDate(s.startDate), end = parseDate(s.endDate);
        if ((start && start > c.window.to) || (end && end < c.window.from)) return false;
      }
      if (c.closing) { const end = parseDate(s.endDate); if (!end || end - today > 14 * DAY) return false; }
      return true;
    }

    function ask(query) {
      const c = parse(query);
      const termConcept = {};
      c.concepts.forEach(k => CONCEPTS[k].forEach(t => { termConcept[t] = k; }));
      const terms = [...new Set(c.concepts.flatMap(k => CONCEPTS[k]).concat(c.terms))];
      const conceptual = terms.length > 0;
      const run = relax => {
        const hits = [];
        for (const doc of docs) {
          if (!passes(doc, c, relax)) continue;
          if (conceptual) {
            const sc = score(doc, terms, termConcept);
            if (sc.total <= 0) continue;
            // every concept in the question has to land somewhere in the record
            if (!relax.concepts && c.required.length > 1 && c.required.some(k => !sc.covered.has(k))) continue;
            hits.push({ show: doc.s, score: sc.total, grounding: grounding(doc, sc.hits) });
          } else {
            hits.push({ show: doc.s, score: 1000 - (doc.s.rank || 200), grounding: [] });
          }
        }
        if (c.receptions) hits.sort((a, b) => receptionDate(a.show, today) - receptionDate(b.show, today) || a.show.rank - b.show.rank);
        else hits.sort((a, b) => b.score - a.score);
        return hits;
      };
      let relaxed = null, hits = run({});
      const strictCount = hits.length;
      if (!hits.length && c.hoods.length) { hits = run({ hood: true }); if (hits.length) relaxed = 'neighborhood'; }
      if (!hits.length && c.window) { hits = run({ hood: true, window: true }); if (hits.length) relaxed = 'time'; }
      if (!hits.length && c.required.length > 1) { hits = run({ hood: true, window: true, concepts: true }); if (hits.length) relaxed = 'concepts'; }
      const res = { query, constraints: c, terms, conceptual, hits, strictCount, relaxed, interpretation: interpretation(c, terms, hits.length, relaxed, strictCount) };
      if (c.receptions) res.groups = groupByDay(hits);
      if (c.route) res.route = route(hits, c);
      res.listDraft = { name: listName(c, query), query, entries: (res.route ? res.route.stops.map(st => st.show) : hits).map(s => ({ type: 'show', id: s.city + '/' + s.slug })) };
      return res;
    }

    function interpretation(c, terms, n, relaxed, strict) {
      const bits = [];
      if (c.reading) bits.push(`Reading that as ${c.reading}`);
      else if (c.concepts.length) bits.push(`Looking for ${c.concepts.join(', ')} in show and gallery text`);
      if (c.receptions) bits.push(`receptions ${c.window ? c.window.label : 'coming up'}`);
      else if (c.window) bits.push(`on view ${c.window.label}`);
      if (c.hoods.length) bits.push(`in ${c.hoods.join(' or ')}`);
      if (c.kind) bits.push(c.kind === 'museum' ? 'museums only' : 'galleries only');
      if (c.active) bits.push('on view now');
      return { reading: bits.join(' · '), relaxed, strict, n };
    }
    function groupByDay(hits) {
      const m = new Map();
      hits.forEach(h => {
        const d = receptionDate(h.show, today);
        const key = d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });
        const g = m.get(key) || { day: key, date: d, items: [] };
        g.items.push({ ...h, flag: NOT_OPENING.test(h.show.reception) ? (h.show.reception.match(NOT_OPENING)[1].toLowerCase() + ', not an opening') : null });
        m.set(key, g);
      });
      return [...m.values()].sort((a, b) => a.date - b.date);
    }
    function route(hits, c) {
      const dow = c.window ? c.window.from.getDay() : 6;
      const byVenue = new Map();
      hits.forEach(h => {
        const key = h.show.venueId || h.show.venue.name;
        const cur = byVenue.get(key);
        if (!cur || (h.show.rank || 999) < (cur.show.rank || 999)) byVenue.set(key, h);
      });
      const open = [...byVenue.values()].map(h => ({ ...h, hours: openOn(h.show.venue, dow) })).filter(h => h.hours.open !== false);
      open.sort((a, b) => (a.show.rank || 999) - (b.show.rank || 999));
      const pool = open.slice(0, 6);
      if (!pool.length) return { stops: [], dow };
      const DWELL = 40 / 60;
      const stops = []; const skipped = [];
      let clock = 12;
      const first = pool.shift();
      clock = Math.max(12, first.hours.opens || 12);
      Object.assign(first, { walkMin: 0, walkKm: 0, arrive: fmtHour(clock) });
      stops.push(first); clock += DWELL;
      while (pool.length) {
        const last = stops[stops.length - 1].show.venue;
        // nearest venue that is still open when we would get there
        let bi = -1, bd = Infinity;
        pool.forEach((p, i) => {
          const d = km(last, p.show.venue), arrive = clock + d / 4.8;
          if (p.hours.closes != null && arrive + 0.5 > p.hours.closes) return;
          if (d < bd) { bd = d; bi = i; }
        });
        if (bi < 0) { skipped.push(...pool.splice(0)); break; }
        const st = pool.splice(bi, 1)[0];
        st.walkKm = bd; st.walkMin = Math.round(bd / 4.8 * 60);
        clock += st.walkMin / 60;
        if (st.hours.opens != null && clock < st.hours.opens) clock = st.hours.opens;
        st.arrive = fmtHour(clock);
        stops.push(st); clock += DWELL;
      }
      const dayName = DOW3[dow][0].toUpperCase() + DOW3[dow].slice(1);
      stops.forEach(st => { st.note = st.hours.opens != null ? `${dayName} ${fmtHour(st.hours.opens)}–${fmtHour(st.hours.closes)}` : (st.hours.open === null ? 'hours: check site' : ''); });
      skipped.forEach(st => { st.why = st.hours.closes != null ? `closes ${fmtHour(st.hours.closes)}` : 'no fit'; });
      return { stops, skipped, dow, start: '12pm', end: fmtHour(clock), totalKm: stops.reduce((a, s) => a + s.walkKm, 0) };
    }
    function listName(c, query) {
      if (c.route) return `${c.window ? c.window.label : 'A day'} in ${c.hoods[0] ? c.hoods[0].split('/').pop() : 'LA'}`;
      if (c.receptions) return `Receptions ${c.window ? c.window.label : 'coming up'}`;
      if (c.artist) return `Like ${c.artist.replace(/\b\w/g, m => m.toUpperCase())}`;
      const cap = query.trim().replace(/[?.!]+$/, '');
      return cap.charAt(0).toUpperCase() + cap.slice(1);
    }

    return { ask, parse, receptionDate, isActive, openOn, today, CONCEPTS, HOODS, ROSTER, ARTIST_PROFILES };
  }

  return { makeAsk, receptionDate, openOn, CONCEPTS, HOODS, ROSTER, ARTIST_PROFILES, SOURCE_LABEL };
});
