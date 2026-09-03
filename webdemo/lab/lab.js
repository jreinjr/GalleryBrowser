/* Design lab: static mockups of filter / rank / map options, built from the
 * real LA payload and the app's own stylesheet. Nothing here ships; it is a
 * review surface. Every frame is a function that returns a .phone element. */
(function () {
  'use strict';

  const DATA = window.DEMO_DATA;
  const ICONS = window.ICONS;
  const DIST = '../dist/gallery-browser-demo/';
  const CITY_KEY = 'los-angeles';
  const city = DATA.cities.find(c => c.key === CITY_KEY);
  const LA = DATA.shows.filter(s => s.city === CITY_KEY).slice().sort((a, b) => a.rank - b.rank);

  // Icons the app does not have yet (would be added to icons.js).
  const S = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"';
  const stroke = 'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"';
  ICONS.sliders = `<svg ${S} ${stroke}><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="14.5" cy="7" r="2.2"/><circle cx="8.5" cy="17" r="2.2"/></svg>`;
  ICONS.trophy = `<svg ${S} fill="currentColor"><path d="M7 3h10v2h3v3a4 4 0 01-3.4 3.95A5.5 5.5 0 0113 15.9V18h3v2H8v-2h3v-2.1a5.5 5.5 0 01-3.6-3.95A4 4 0 014 8V5h3V3zm0 4H6v1a2 2 0 001 1.73V7zm10 0v2.73A2 2 0 0018 8V7h-1z"/></svg>`;

  // ---------- derived ranking ----------
  const showId = s => s.city + '/' + s.slug;
  const venueKey = v => {
    const name = (v.name || '').trim().toLowerCase().replace(/\s+/g, ' ');
    return name + '@' + Number(v.lat).toFixed(5) + ',' + Number(v.lng).toFixed(5);
  };
  const TIER1 = 0.35, TIER2 = 0.22;   // TIER2 = the live featured threshold
  const tierOf = score => score == null ? 3 : score >= TIER1 ? 1 : score >= TIER2 ? 2 : 3;
  const TIER_LABEL = { 1: 'Top gallery', 2: 'Notable', 3: 'Listed' };
  const TIER_SHORT = { 1: 'Top', 2: 'Notable', 3: 'Listed' };

  // One entry per venue: best show rank, best score, its shows.
  const venues = (() => {
    const m = new Map();
    LA.forEach(s => {
      const k = venueKey(s.venue);
      const g = m.get(k) || { key: k, venue: s.venue, shows: [], rank: Infinity, score: -Infinity };
      g.shows.push(s);
      g.rank = Math.min(g.rank, s.rank);
      g.score = Math.max(g.score, s.score ?? -1);
      m.set(k, g);
    });
    const arr = [...m.values()].sort((a, b) => a.rank - b.rank);
    arr.forEach((g, i) => { g.pos = i + 1; g.tier = tierOf(g.score); g.featured = g.shows.some(s => s.featured); });
    return arr;
  })();
  const galleries = venues.filter(v => !v.venue.isMuseum);
  galleries.forEach((g, i) => { g.gpos = i + 1; });
  const PICKS_N = 30;
  const picks = new Set(LA.filter(s => s.featured).slice(0, PICKS_N).map(showId));
  const pickVenues = new Set(venues.filter(v => v.shows.some(s => picks.has(showId(s)))).map(v => v.key));

  const displayName = s => s.artist || s.title;
  const img = s => DIST + (s.images[0] ? s.images[0].src : '');
  const fmtLong = d => d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const parseDate = str => { if (!str) return null; const [y, m, d] = str.split('-').map(Number); return new Date(y, m - 1, d); };
  const dateLine = s => { const e = parseDate(s.endDate); return e ? 'Through ' + fmtLong(e) : ''; };
  const fullAddress = v => v.addressDetail ? v.address + ', ' + v.addressDetail : v.address;

  // ---------- DOM ----------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k === 'style') node.style.cssText = v;
      else node.setAttribute(k, v);
    }
    children.flat().forEach(c => { if (c == null) return; node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return node;
  }
  const icon = name => el('span', { class: 'ico', html: ICONS[name], style: 'display:inline-flex' });
  const svg = name => { const s = el('span', { html: ICONS[name] }); return s.firstElementChild; };

  function phone(id, tab, ...content) {
    const tabs = [['featured', 'star', 'Featured'], ['list', 'list', 'List'], ['map', 'map', 'Map']];
    const bar = el('nav', { class: 'lab-tabbar' }, ...tabs.map(([k, ic, label]) =>
      el('button', { class: 'tab-btn' + (k === tab ? ' active' : '') }, svg(ic), el('span', null, label))));
    return el('div', { class: 'phone', id: 'frame-' + id, 'data-frame': id },
      el('div', { class: 'statusbar' }, el('span', null, '9:41'),
        el('span', { class: 'sb-right' }, el('span', null, '●●●●'), el('span', { class: 'sb-batt' }))),
      el('div', { class: 'app' }, el('div', { class: 'screen' }, ...content), bar, el('div', { class: 'home-bar' })));
  }
  const navrow = (left, right) => el('div', { class: 'navrow' }, left || el('span'), right || el('span'));
  const citiesBtn = () => el('button', { class: 'nav-textbtn' }, 'Cities');
  const title = () => el('div', { class: 'large-title' }, city.displayName);
  const page = (...kids) => el('div', { class: 'page' }, el('div', { class: 'page-scroll' }, ...kids));

  function card(s) {
    return el('div', { class: 'card' },
      el('img', { class: 'card-img', src: img(s), alt: '' }),
      s.images.length > 1 ? el('div', { class: 'carousel-dots top-left' }, ...s.images.slice(0, 5).map((_, i) => el('i', { class: i ? '' : 'on' }))) : null,
      el('div', { class: 'card-footer' },
        el('div', { class: 'name' }, displayName(s)),
        el('div', { class: 'venue' }, `${s.venue.name} • ${s.venue.address}`)));
  }
  const feed = shows => el('div', { class: 'feed' }, ...shows.map(card));
  const bookmark = () => el('button', { class: 'bookmark-btn' }, svg('bookmark'));
  function showRow(s, extra) {
    return el('div', { class: 'show-row' },
      extra && extra.lead ? extra.lead : null,
      el('div', { class: 'sr-text' },
        el('div', { class: 'sr-name' }, displayName(s)),
        el('div', { class: 'sr-venue' }, s.venue.name, extra && extra.afterVenue ? extra.afterVenue : null),
        el('div', { class: 'sr-addr' }, s.venue.neighborhood ? `${s.venue.neighborhood} · ${s.venue.address}` : s.venue.address)),
      bookmark());
  }
  const rows = (shows, fn) => el('div', { class: 'group list-results' }, ...shows.map(s => showRow(s, fn && fn(s))));
  const chip = (label, on, opts) => el('button', { class: 'chip' + (on ? ' on' : '') + (opts && opts.cls ? ' ' + opts.cls : '') },
    opts && opts.lead ? opts.lead : null, el('span', null, label), opts && opts.n != null ? el('span', { class: 'n' }, String(opts.n)) : null,
    opts && opts.menu ? svg('chevronDown') : null);
  const chipRow = (...chips) => el('div', { class: 'chip-row' }, ...chips);
  const searchField = () => el('div', { class: 'search-bar' }, el('div', { class: 'search-field' }, svg('search'),
    el('input', { type: 'search', placeholder: 'Artist, gallery, or show', autocomplete: 'off' })));
  const status = (n, clear) => el('div', { class: 'list-status' }, el('span', { class: 'list-count' }, `${n} shows`), clear ? el('button', { class: 'list-clear' }, 'Clear') : el('span'));

  // A filter state used across the Q1 frames: Galleries, 2 neighborhoods, ranking.
  const F = { kind: 'galleries', hoods: ['Hollywood', 'Los Feliz/NELA'], featured: true, receptions: false, saved: false, sort: 'Ranking' };
  const inHoods = s => F.hoods.includes(s.venue.neighborhood);
  const featuredLA = LA.filter(s => s.featured);
  const filtered = featuredLA.filter(s => !s.venue.isMuseum && inHoods(s));
  const activeCount = 2;   // Galleries (non-default? default is galleries) + 2 hoods -> count the hood group + featured

  /* The production sticky bar, verbatim structure. */
  function fullBar(opts) {
    const o = opts || {};
    return el('div', { class: 'list-filters' + (o.cls ? ' ' + o.cls : '') },
      o.head || null,
      searchField(),
      chipRow(chip('Featured', F.featured), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('2 neighborhoods', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(filtered.length, true));
  }

  // ================= Q1: filters =================
  const filterBtn = (on, n) => el('button', { class: 'icon-btn' + (on ? ' on' : ''), 'aria-label': 'Filters' }, svg('sliders'), n ? el('span', { class: 'badge' }, String(n)) : null);

  function F1a() {
    const summary = el('div', { class: 'filter-summary' },
      chip('Featured', true), chip('Galleries', true), chip('Hollywood', false, { cls: 'sub' }), chip('Los Feliz/NELA', false, { cls: 'sub' }),
      el('span', { class: 'count' }, `${filtered.length} shows`));
    return phone('F1a', 'featured', page(navrow(citiesBtn(), filterBtn(false, activeCount)), title(), summary, feed(filtered.slice(0, 3))));
  }
  function F1b() {
    const head = el('div', { class: 'panel-head' }, el('span', { class: 'ph-title' }, 'Filters'), el('button', { class: 'ph-done' }, 'Done', svg('chevronUp')));
    return phone('F1b', 'featured', page(navrow(citiesBtn(), filterBtn(true, activeCount)), title(), fullBar({ head, cls: 'expanded' }), feed(filtered.slice(0, 2))));
  }
  function F1c() {
    const summary = el('div', { class: 'filter-summary' },
      chip('Featured', true), chip('Galleries', true), chip('Hollywood', false, { cls: 'sub' }), chip('Los Feliz/NELA', false, { cls: 'sub' }),
      el('span', { class: 'count' }, `${filtered.length} shows`));
    return phone('F1c', 'list', page(navrow(citiesBtn(), filterBtn(false, activeCount)), title(), summary, rows(filtered)));
  }
  function F2a() {
    const search = el('button', { class: 'icon-btn', 'aria-label': 'Search' }, svg('search'));
    const r = el('div', { class: 'chip-row', style: 'padding-bottom:10px' },
      chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('2 neighborhoods', true, { menu: true, cls: 'chip-menu' }),
      chip('Featured', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' }));
    return phone('F2a', 'featured', page(navrow(citiesBtn(), search), title(), r, feed(filtered.slice(0, 3))));
  }
  function F2b() {
    const search = el('button', { class: 'icon-btn', 'aria-label': 'Search' }, svg('search'));
    const bar = el('div', { class: 'list-filters' },
      el('div', { class: 'chip-row' },
        chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('2 neighborhoods', true, { menu: true, cls: 'chip-menu' }),
        chip('Featured', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(filtered.length, true));
    return phone('F2b', 'list', page(navrow(citiesBtn(), search), title(), bar, rows(filtered)));
  }
  function F3a() {
    const pills = el('div', { class: 'pill-row' },
      chip('Filters', true, { lead: svg('sliders'), n: activeCount }),
      chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu outline' }),
      el('span', { class: 'list-count', style: 'margin-left:auto' }, `${filtered.length} shows`));
    return phone('F3a', 'featured', page(navrow(citiesBtn()), title(), pills, feed(filtered.slice(0, 3))));
  }
  function F3b() {
    const toggleRow = (label, on) => el('div', { class: 'row' }, el('span', { class: 'row-label' }, label), el('span', { class: 'switch' + (on ? ' on' : '') }));
    const radioRow = (label, on) => el('div', { class: 'row' }, el('span', { class: 'row-label' }, label), on ? el('span', { class: 'check', html: ICONS.check, style: 'width:18px;height:18px;color:var(--blue)' }) : el('span'));
    const sheet = el('div', { class: 'sheet-static' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Filters'), el('button', { class: 'sheet-close' }, svg('xmark'))),
      el('div', { class: 'sheet-scroll' },
        searchField(),
        el('div', { class: 'group-header' }, 'Show'),
        el('div', { class: 'group' }, toggleRow('Featured only', true), toggleRow('Saved only', false), toggleRow('Upcoming receptions', false)),
        el('div', { class: 'group-header' }, 'Venues'),
        el('div', { class: 'seg-row' }, el('button', null, 'All venues'), el('button', { class: 'on' }, 'Galleries'), el('button', null, 'Museums')),
        el('div', { class: 'group-header' }, 'Neighborhoods'),
        el('div', { class: 'chip-wrap' }, chip('All', false), ...city.neighborhoods.slice(0, 9).map(h => chip(h, F.hoods.includes(h))))),
      el('div', { class: 'sheet-foot' }, el('button', { class: 'ghost' }, 'Clear'), el('button', { class: 'capsule-btn' }, `Show ${filtered.length} shows`)));
    const behind = page(navrow(citiesBtn()), title(), el('div', { class: 'pill-row' }, chip('Filters', true, { lead: svg('sliders'), n: activeCount }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu outline' })), feed(filtered.slice(0, 2)));
    return phone('F3b', 'featured', behind, el('div', { class: 'sheet-backdrop open' }), sheet);
  }
  function F4() {
    const head = el('div', { class: 'panel-head' }, el('span', { class: 'ph-title' }, 'Filters'), el('button', { class: 'ph-done' }, 'Done', svg('chevronUp')));
    const location = el('div', null,
      el('div', { class: 'group-header', style: 'padding-left:16px' }, 'Location'),
      el('div', { class: 'group', style: 'margin-bottom:10px' },
        el('div', { class: 'row' }, el('span', { class: 'row-label' }, 'City'), el('span', { class: 'row-value' }, 'Los Angeles'), el('span', { class: 'chev', html: ICONS.chevronRight, style: 'display:flex' })),
        el('div', { class: 'row' }, el('span', { class: 'row-label' }, 'Neighborhoods'), el('span', { class: 'row-value' }, '2 selected'), el('span', { class: 'chev', html: ICONS.chevronRight, style: 'display:flex' }))));
    const bar = el('div', { class: 'list-filters expanded' }, head, location, searchField(),
      chipRow(chip('Featured', true), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(filtered.length, true));
    return phone('F4', 'featured', page(navrow(el('span'), filterBtn(true, 3)), title(), bar, feed(filtered.slice(0, 1))));
  }

  // ================= Q2: rank =================
  // Venue page subject: a top-ranked gallery with more than one show if possible.
  const subject = galleries.find(g => g.shows.length > 1 && g.tier === 1) || galleries[0];
  const subject2 = galleries.find(g => g.tier === 2) || galleries[5];

  function venuePage(g, afterTitle, opts) {
    const v = g.venue;
    const mapCard = el('div', { class: 'map-card' });
    // the shipped card runs a live MapLibre map; these static phones just stand it in
    mapCard.append(el('div', { class: 'pin-marker', html: ICONS.pin, style: 'position:absolute;left:50%;top:50%;transform:translate(-50%,-100%)' }));
    const actions = el('div', { class: 'venue-actions' },
      el('a', { class: 'capsule-btn' }, svg('walk'), el('span', null, 'Directions to venue')),
      el('a', { class: 'capsule-btn' }, svg('compass'), el('span', null, 'Open website')));
    const body = el('div', { class: 'venue-body' },
      el('div', { class: 'venue-title' }, v.name),
      afterTitle,
      el('div', { class: 'venue-lines' }, el('div', null, fullAddress(v)), ...v.hours.map(h => el('div', null, h))),
      opts && opts.card ? opts.card : null,
      el('div', { class: 'venue-shows' }, el('div', { class: 'group-header' }, 'Shows'), el('div', { class: 'group' }, ...g.shows.map(s => showRow(s)))),
      mapCard, actions);
    return page(navrow(el('button', { class: 'circle-btn', style: 'background:var(--field)' }, svg('chevronLeft'))), body);
  }
  const tierDots = t => el('span', { class: 'tier-dots' }, ...[1, 2, 3].map(i => el('i', { class: i <= 4 - t ? 'on' : '' })));

  function R1a() {
    const g = subject;
    const chipEl = el('div', { class: 'rank-chip outline' }, svg('trophy'), `#${g.gpos} gallery in Los Angeles`);
    return phone('R1a', 'featured', venuePage(g, chipEl));
  }
  function R1b() {
    const s = LA[2];
    const g = venues.find(v => v.key === venueKey(s.venue));
    const hero = el('div', { class: 'detail-hero' }, el('img', { src: img(s), alt: '', style: 'width:100%;height:340px;object-fit:cover;display:block' }),
      el('div', { class: 'detail-topbar' }, el('button', { class: 'circle-btn' }, svg('chevronLeft')),
        el('div', { class: 'stepper' }, el('button', null, svg('chevronUp')), el('button', null, svg('chevronDown')))));
    const body = el('div', { class: 'detail-body' },
      s.artist ? el('div', { class: 'detail-artist' }, s.artist) : null,
      el('div', { class: 'detail-title' }, s.title),
      el('div', { class: 'detail-meta-row' },
        el('span', { class: 'rank-chip soft', style: 'margin-top:0' }, svg('star'), `Editor's Pick`),
        el('span', { class: 'rank-chip outline', style: 'margin-top:0' }, `Show #${s.rank}`),
        el('span', { class: 'rank-chip outline', style: 'margin-top:0' }, `Gallery #${g.gpos}`)),
      el('div', { class: 'detail-dates' }, dateLine(s)),
      el('button', { class: 'capsule-btn detail-save' }, el('span', null, 'Add to My Shows')),
      el('button', { class: 'venue-block' }, el('div', { class: 'vb-text' }, el('div', { class: 'vb-name' }, s.venue.name), el('div', { class: 'vb-line' }, fullAddress(s.venue)), ...s.venue.hours.map(h => el('div', { class: 'vb-line' }, h))), svg('chevronRight')),
      el('div', { class: 'divider' }),
      el('div', { class: 'detail-desc' }, ...s.description.split(/\n\s*\n/).slice(0, 1).map(p => el('p', null, p))));
    const pg = el('div', { class: 'page' }, el('div', { class: 'page-scroll', style: 'padding-top:0' }, hero, body));
    return phone('R1b', 'featured', pg);
  }
  function R2a() {
    const g = subject2;
    const pill = el('div', { class: 'tier-pill t' + g.tier }, tierDots(g.tier), TIER_LABEL[g.tier]);
    return phone('R2a', 'featured', venuePage(g, pill));
  }
  function R2b() {
    const shows = featuredLA.filter(s => !s.venue.isMuseum).slice(0, 40);
    const bar = el('div', { class: 'list-filters' }, searchField(),
      chipRow(chip('Featured', true), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('Neighborhoods', false, { menu: true, cls: 'chip-menu' }), chip('Sort: Gallery rank', false, { menu: true, cls: 'chip-menu' })),
      status(shows.length, true));
    const mixed = [...shows.slice(0, 4), ...featuredLA.filter(s => tierOf(s.score) === 2).slice(0, 3), ...LA.filter(s => tierOf(s.score) === 3 && !s.venue.isMuseum).slice(0, 2)];
    const withTier = s => { const t = tierOf(s.score); return { afterVenue: el('span', { class: 'sr-tier t' + t }, tierDots(t), TIER_SHORT[t]) }; };
    return phone('R2b', 'list', page(navrow(citiesBtn()), title(), bar, rows(mixed, withTier)));
  }
  function R3() {
    const g = subject;
    const pct = Math.max(0, Math.min(1, (g.score + 0.05) / 0.6));
    const thr = (TIER2 + 0.05) / 0.6;
    const why = [['Press coverage', 0.18], ['Art fairs', 0.12], ['Artist notability', 0.10], ['Venue history', 0.06], ['Museum weighting', -0.02]];
    const card = el('div', { class: 'rank-card' },
      el('div', { class: 'rc-top' }, el('div', { class: 'rc-rank' }, `#${g.gpos}`, el('small', null, `of ${galleries.length} galleries`)), el('div', { class: 'rc-tier' }, TIER_LABEL[g.tier])),
      el('div', { class: 'score-bar' }, el('div', { class: 'fill', style: `width:${pct * 100}%` }), el('div', { class: 'thresh', style: `left:${thr * 100}%` }), el('div', { class: 'thresh-lbl', style: `left:${thr * 100}%` }, 'Featured')),
      el('div', { class: 'rc-axis' }, el('span', null, 'Listed'), el('span', null, 'Top')),
      el('div', { class: 'rc-why' }, el('span', null, 'Why this ranking'), svg('chevronUp')),
      ...why.map(([l, v]) => el('div', { class: 'why-row' }, el('span', { class: 'wl' }, l), el('span', { class: 'wb' }, el('i', { class: v < 0 ? 'neg' : '', style: `width:${Math.abs(v) / 0.2 * 100}%` })), el('span', { class: 'wv' }, (v > 0 ? '+' : '') + v.toFixed(2)))));
    return phone('R3', 'featured', venuePage(g, null, { card }));
  }
  function R4() {
    const shows = featuredLA.filter(s => !s.venue.isMuseum);
    const bar = el('div', { class: 'list-filters' }, searchField(),
      chipRow(chip('Featured', true), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('Neighborhoods', false, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(shows.length, true));
    let i = 0;
    const numbered = () => { i += 1; return { lead: el('div', { class: 'sr-num' + (i > 10 ? ' dim' : '') }, String(i)) }; };
    return phone('R4', 'list', page(navrow(citiesBtn()), title(), bar, rows(shows.slice(0, 14), numbered)));
  }

  // ================= Q3: map =================
  const BLUE = 'rgba(97, 173, 242, 0.85)';
  const RING = 'rgba(255, 255, 255, 0.6)';
  const FONT_BOLD = ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular'];
  const T_COLOR = { 1: 'rgba(97, 173, 242, 0.95)', 2: 'rgba(255, 255, 255, 0.85)', 3: 'rgba(150, 150, 158, 0.55)' };
  const MAP_CENTER = [-118.325, 34.058], MAP_ZOOM = 11.55;
  window.__labMaps = [];

  function venueGeo(list) {
    return { type: 'FeatureCollection', features: list.map(g => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [g.venue.lng, g.venue.lat] },
      properties: { key: g.key, name: g.venue.name, count: g.shows.length, museum: !!g.venue.isMuseum, tier: g.tier, score: g.score, pos: g.pos, gpos: g.gpos || 999, pick: pickVenues.has(g.key) ? 1 : 0 },
    })) };
  }
  function starImage(map) {
    const c = document.createElement('canvas'); c.width = c.height = 56;
    const x = c.getContext('2d'); x.translate(28, 28);
    x.beginPath();
    for (let i = 0; i < 10; i++) { const r = i % 2 ? 9 : 22; const a = -Math.PI / 2 + i * Math.PI / 5; x.lineTo(r * Math.cos(a), r * Math.sin(a)); }
    x.closePath(); x.fillStyle = '#61ADF2'; x.fill(); x.lineWidth = 3; x.strokeStyle = 'rgba(255,255,255,0.9)'; x.lineJoin = 'round'; x.stroke();
    map.addImage('star', x.getImageData(0, 0, 56, 56), { pixelRatio: 2 });
  }
  const label = extra => ({
    id: 'venue-label', type: 'symbol', source: 'v',
    layout: { 'text-field': ['get', 'name'], 'text-font': FONT_BOLD, 'text-size': 11, 'text-anchor': 'top', 'text-offset': [0, 1.2], 'text-max-width': 12, 'text-padding': 4, 'symbol-sort-key': ['get', 'pos'] },
    paint: { 'text-color': '#fff', 'text-halo-color': 'rgba(0,0,0,0.9)', 'text-halo-width': 1.2, 'text-halo-blur': 0.6, ...(extra || {}) },
  });

  // mode: shows (today, clustered) | tier | size | stars | topn
  function makeMap(container, mode, opt) {
    const o = opt || {};
    const map = new maplibregl.Map({ container, style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json', center: MAP_CENTER, zoom: o.zoom || MAP_ZOOM, attributionControl: { compact: true }, interactive: true });
    window.__labMaps.push(map);
    map.on('load', () => {
      const list = mode === 'shows' ? venues : galleries;
      const alwaysDraw = { 'text-allow-overlap': true, 'text-ignore-placement': true };
      if (mode === 'shows') {
        map.addSource('v', { type: 'geojson', data: venueGeo(list), cluster: true, clusterRadius: 44, clusterMaxZoom: 16 });
        const single = ['!', ['has', 'point_count']], multi = ['all', single, ['>', ['get', 'count'], 1]];
        map.addLayer({ id: 'clusters', type: 'circle', source: 'v', filter: ['has', 'point_count'], paint: { 'circle-color': BLUE, 'circle-radius': ['step', ['get', 'point_count'], 16, 10, 20, 30, 24], 'circle-stroke-width': 2, 'circle-stroke-color': RING } });
        map.addLayer({ id: 'cluster-count', type: 'symbol', source: 'v', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': FONT_BOLD, 'text-size': 13, ...alwaysDraw }, paint: { 'text-color': '#fff' } });
        map.addLayer({ id: 'venue-dot', type: 'circle', source: 'v', filter: single, paint: { 'circle-color': BLUE, 'circle-radius': 10.5, 'circle-stroke-width': 1, 'circle-stroke-color': RING } });
        map.addLayer({ id: 'venue-badge', type: 'circle', source: 'v', filter: multi, paint: { 'circle-color': '#fff', 'circle-radius': 8.5, 'circle-translate': [10, -9] } });
        map.addLayer({ id: 'venue-badge-count', type: 'symbol', source: 'v', filter: multi, layout: { 'text-field': ['to-string', ['get', 'count']], 'text-font': FONT_BOLD, 'text-size': 10, ...alwaysDraw }, paint: { 'text-color': '#000', 'text-translate': [10, -9] } });
        const l = label(); l.filter = single; map.addLayer(l);
        return;
      }
      map.addSource('v', { type: 'geojson', data: venueGeo(list) });
      if (mode === 'tier') {
        map.addLayer({ id: 'venue-dot', type: 'circle', source: 'v', paint: {
          'circle-color': ['match', ['get', 'tier'], 1, T_COLOR[1], 2, T_COLOR[2], T_COLOR[3]],
          'circle-radius': ['match', ['get', 'tier'], 1, 10.5, 2, 8, 5.5],
          'circle-stroke-width': ['match', ['get', 'tier'], 3, 0, 1], 'circle-stroke-color': RING } });
        const l = label({ 'text-opacity': ['match', ['get', 'tier'], 3, 0, 1] }); map.addLayer(l);
      } else if (mode === 'size') {
        const top = ['<=', ['get', 'gpos'], 10];
        map.addLayer({ id: 'venue-dot', type: 'circle', source: 'v', filter: ['!', top], paint: {
          'circle-color': ['interpolate', ['linear'], ['get', 'score'], 0.0, 'rgba(150,150,158,0.45)', 0.22, 'rgba(97,173,242,0.6)', 0.5, BLUE],
          'circle-radius': ['interpolate', ['linear'], ['get', 'score'], -0.05, 4, 0.5, 9], 'circle-stroke-width': 0 } });
        map.addLayer({ id: 'venue-top', type: 'circle', source: 'v', filter: top, paint: { 'circle-color': BLUE, 'circle-radius': 12, 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } });
        map.addLayer({ id: 'venue-top-num', type: 'symbol', source: 'v', filter: top, layout: { 'text-field': ['to-string', ['get', 'gpos']], 'text-font': FONT_BOLD, 'text-size': 12, ...alwaysDraw }, paint: { 'text-color': '#fff' } });
        const l = label({}); l.filter = top; map.addLayer(l);
      } else if (mode === 'stars') {
        starImage(map);
        map.addLayer({ id: 'venue-dot', type: 'circle', source: 'v', filter: ['==', ['get', 'pick'], 0], paint: { 'circle-color': 'rgba(150,150,158,0.35)', 'circle-radius': 5.5, 'circle-stroke-width': 0 } });
        map.addLayer({ id: 'venue-star', type: 'symbol', source: 'v', filter: ['==', ['get', 'pick'], 1], layout: { 'icon-image': 'star', 'icon-size': 1, 'icon-allow-overlap': true, 'icon-ignore-placement': true } });
        const l = label({}); l.filter = ['==', ['get', 'pick'], 1]; map.addLayer(l);
      } else if (mode === 'topn') {
        const n = o.topN || 30;
        const inTop = ['<=', ['get', 'gpos'], n];
        map.addLayer({ id: 'venue-dot', type: 'circle', source: 'v', paint: {
          'circle-color': ['case', inTop, BLUE, 'rgba(150,150,158,0.22)'],
          'circle-radius': ['case', inTop, 10.5, 4.5], 'circle-stroke-width': ['case', inTop, 1, 0], 'circle-stroke-color': RING } });
        const l = label({}); l.filter = inTop; map.addLayer(l);
      }
    });
    return map;
  }

  const mapPill = (text, opts) => el('button', { class: 'map-pill' + (opts && opts.on ? ' on' : '') }, opts && opts.lead ? opts.lead : null, text);
  const legend = (...rowsL) => el('div', { class: 'map-legend' }, ...rowsL.map(([style, text]) => el('div', null, el('i', { style }), el('span', null, text))));

  function mapFrame(id, mode, chrome, opt) {
    const box = el('div', { class: 'mapbox' });
    const screen = el('div', { class: 'screen' }, box, ...chrome);
    const ph = phone(id, 'map', screen);
    requestAnimationFrame(() => makeMap(box, mode, opt));
    return ph;
  }
  const seg = which => el('div', { class: 'seg-pill' }, el('button', { class: which === 'shows' ? 'on' : '' }, 'Shows'), el('button', { class: which === 'galleries' ? 'on' : '' }, 'Galleries'));

  function M1a() {
    return mapFrame('M1a', 'shows', [el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), seg('shows'), mapPill('Filter'))]);
  }
  function M1b() {
    return mapFrame('M1b', 'tier', [
      el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), seg('galleries'), mapPill('Filter')),
      legend([`background:${T_COLOR[1]}`, 'Top galleries'], [`background:${T_COLOR[2]}`, 'Notable'], [`background:${T_COLOR[3]};border:none;width:8px;height:8px;margin:2px`, 'Listed']),
      el('div', { class: 'map-count' }, `${galleries.length} galleries`)]);
  }
  function M2() {
    return mapFrame('M2', 'size', [el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), seg('galleries'), mapPill('Filter'))]);
  }
  function M3() {
    return mapFrame('M3', 'stars', [
      el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), mapPill(`Picks`, { on: true, lead: svg('star') }), mapPill('Filter')),
      el('div', { class: 'map-count' }, `Editor's Picks · top ${PICKS_N}`)]);
  }
  function M4() {
    const row = (t, on, sub) => el('button', { class: sub ? 'sub' : '' }, el('span', null, t), on ? svg('check') : el('span'));
    const head = t => el('div', { class: 'mm-head' }, t);
    const menu = el('div', { class: 'map-menu' },
      head('Show'), row('All shows', false), row('My Shows', false), row('Upcoming receptions', false),
      head('Rank'), row(`Editor's Picks (top ${PICKS_N})`, true), row('Top 100', false), row('Everything', false),
      head('Venues'), row('All venues', false), row('Galleries', true), row('Museums', false));
    return mapFrame('M4', 'topn', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter · 2', { on: true })), menu], { topN: PICKS_N });
  }
  function topnPill(n) {
    return el('div', { class: 'topn-pill' }, el('span', { class: 'lbl' }, 'Top'), ...[10, 30, 100].map(k => el('button', { class: k === n ? 'on' : '' }, String(k))), el('button', { class: n === Infinity ? 'on' : '' }, 'All'));
  }
  function M5a() { return mapFrame('M5a', 'topn', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter')), topnPill(10)], { topN: 10 }); }
  function M5b() { return mapFrame('M5b', 'topn', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter')), topnPill(100)], { topN: 100 }); }

  // ================= page =================
  const SECTIONS = [
    { id: 'q1', title: 'Q1 · Filters on Featured, collapsible on both tabs',
      blurb: `Filter state in every frame: Featured on, Galleries, Hollywood + Los Feliz/NELA, sorted by ranking (${filtered.length} shows). Cities stays a global control except in F4.`,
      frames: [
        [F1a, 'F1a', 'Filter button + collapsed summary (Featured).', 'Sliders icon in the navrow with an active-count badge. The one-line summary shows the live values; tapping any chip or the button expands.'],
        [F1b, 'F1b', 'Same, expanded in place.', 'The production List bar drops in under the title with a Filters/Done header. Nothing new to learn; List and Featured share one component.'],
        [F1c, 'F1c', 'F1 collapsed on List.', 'The List tab gets the same slim default. The tall bar becomes opt-in.'],
        [F2a, 'F2a', 'Picker chips only, search behind a button (Featured).', 'No toggles row and no search field. Featured/Saved/Receptions fold into one "Featured" picker. Always one row tall.'],
        [F2b, 'F2b', 'F2 on List.', 'Same row plus count/Clear. Cheapest change, but a fourth picker and hidden search cost discoverability.'],
        [F3a, 'F3a', 'Filters pill + Sort pill (Featured).', 'Two controls under the title, nothing else. Everything lives in a sheet.'],
        [F3b, 'F3b', 'The Filters sheet.', 'Grouped iOS-style form: search, toggles, venue segment, neighborhood chips, "Show N shows" apply button. Best for many filters, worst for quick toggling.'],
        [F4, 'F4', 'City inside the filter panel.', 'F1b with a Location group (City, Neighborhoods) at the top and no Cities button. Map would still need its own Cities pill, so the city control would exist in two shapes.'],
      ] },
    { id: 'q2', title: 'Q2 · Gallery rank visibility',
      blurb: `Ranking exists per show. Mockups derive a gallery rank as the best show rank at that venue (${galleries.length} ranked galleries in LA) and three tiers from score: Top ≥ 0.35, Notable ≥ 0.22 (the featured threshold), Listed below.`,
      frames: [
        [R1a, 'R1a', 'Rank chip on the venue page.', 'One outlined chip under the name: "#N gallery in Los Angeles". Honest and minimal; a number invites "why #7?".'],
        [R1b, 'R1b', 'Chips on the show detail.', 'Editor\'s Pick + show rank + gallery rank as a meta row under the title. Shows how much a full set of badges costs.'],
        [R2a, 'R2a', 'Tier label instead of a number.', 'Three-dot glyph + "Top gallery / Notable / Listed". Softer than a rank; no promise of precision; same glyph works in rows and on the map legend.'],
        [R2b, 'R2b', 'Tier glyph in list rows + "Sort: Gallery rank".', 'Rows are a mix of tiers to show the glyph at each level. A second sort entry separates gallery rank from show rank.'],
        [R3, 'R3', 'Score card with "Why this ranking".', 'Rank, score bar against the featured threshold, expandable factor breakdown (factor labels here are illustrative). Most transparent; also the busiest venue page.'],
        [R4, 'R4', 'Numbered list rows.', 'A chart-style ordinal column when sorted by ranking, dimmed past 10. Raw ranks are sparse (1, 2, 5, 6…) so this shows positions, not raw rank.'],
      ] },
    { id: 'q3', title: 'Q3 · Map: galleries vs shows, rank encoding, Top N',
      blurb: `Real MapLibre with the production layers. Frames are centered on Hollywood / Mid-Wilshire / Downtown. Editor's Picks here means the top ${PICKS_N} featured shows (the pipeline currently marks 5).`,
      frames: [
        [M1a, 'M1a', 'Shows | Galleries segmented control, Shows mode.', 'Today\'s clustered map with a mode switch in the top center. Shows mode is unchanged.'],
        [M1b, 'M1b', 'Galleries mode, colored by tier.', 'One dot per gallery, no clustering. Blue = Top, white = Notable, small gray = Listed (unlabeled). Legend bottom-left.'],
        [M2, 'M2', 'Galleries mode, size ramp + numbered top 10.', 'Top 10 get numbered pins; the rest scale and fade with score. No legend needed, and the numbers tie back to R4.'],
        [M3, 'M3', 'Editor\'s Picks as star markers.', 'A "Picks" toggle pill. Picks are stars, everything else dims to small gray dots. Reads instantly, but adds a third pill.'],
        [M4, 'M4', 'Sectioned Filter dropdown.', 'The existing dropdown grows Show / Rank / Venues sections. Rank = Editor\'s Picks, Top 100, Everything. No new chrome; one place for everything.'],
        [M5a, 'M5a', '"Top N" stepper, Top 10.', 'A bottom-floating pill cycling 10 / 30 / 100 / All. Below-cutoff galleries stay as faint dots for context.'],
        [M5b, 'M5b', '"Top N" stepper, Top 100.', 'Same control at 100, to show how the map fills in.'],
      ] },
  ];

  const root = document.getElementById('lab');
  root.appendChild(el('div', { class: 'lab-header' }, el('h1', null, 'Gallery Browser · design lab'),
    el('p', null, `Static mockups over the real Los Angeles payload (${LA.length} shows, ${venues.length} venues). Each frame is one option; IDs are what to reply with. Nothing here is wired into the app.`)));
  SECTIONS.forEach(sec => {
    const grid = el('div', { class: 'lab-grid' });
    sec.frames.forEach(([fn, id, head, body]) => {
      let ph;
      try { ph = fn(); } catch (e) { console.error(id, e); ph = el('div', { class: 'phone' }, el('pre', { style: 'padding:20px;color:#f66' }, String(e.stack))); }
      grid.appendChild(el('div', { class: 'lab-item' }, ph, el('div', { class: 'lab-caption' }, el('b', null, id + ' · '), head, ' ', el('span', null, body))));
    });
    root.appendChild(el('section', { class: 'lab-section', id: sec.id }, el('h2', null, sec.title), el('p', null, sec.blurb), grid));
  });
})();
