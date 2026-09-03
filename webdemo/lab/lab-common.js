/* Design lab: helpers shared by every lab page (index.html, lists.html).
 * Real LA payload + the app's own stylesheet; every frame is a function that
 * returns a .phone element. Exposed as window.Lab. */
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
  ICONS.sliders = ICONS.sliders || `<svg ${S} ${stroke}><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="14.5" cy="7" r="2.2"/><circle cx="8.5" cy="17" r="2.2"/></svg>`;
  ICONS.trophy = `<svg ${S} fill="currentColor"><path d="M7 3h10v2h3v3a4 4 0 01-3.4 3.95A5.5 5.5 0 0113 15.9V18h3v2H8v-2h3v-2.1a5.5 5.5 0 01-3.6-3.95A4 4 0 014 8V5h3V3zm0 4H6v1a2 2 0 001 1.73V7zm10 0v2.73A2 2 0 0018 8V7h-1z"/></svg>`;
  ICONS.sparkle = `<svg ${S} fill="currentColor"><path d="M12 2.5l1.9 5.6 5.6 1.9-5.6 1.9L12 17.5l-1.9-5.6-5.6-1.9 5.6-1.9zM5 15l.9 2.6 2.6.9-2.6.9L5 22l-.9-2.6-2.6-.9 2.6-.9zM19 14l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z"/></svg>`;
  ICONS.share = `<svg ${S} ${stroke}><path d="M12 3v12M7.5 7.5L12 3l4.5 4.5M5 12v7a1 1 0 001 1h12a1 1 0 001-1v-7"/></svg>`;
  ICONS.plus = `<svg ${S} ${stroke}><path d="M12 5v14M5 12h14"/></svg>`;
  ICONS.minus = `<svg ${S} ${stroke}><path d="M5 12h14"/></svg>`;
  ICONS.grip = `<svg ${S} ${stroke}><path d="M5 9h14M5 15h14"/></svg>`;
  ICONS.link = `<svg ${S} ${stroke}><path d="M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7L11.5 6.8"/><path d="M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1.5-1.5"/></svg>`;
  ICONS.listBullet = `<svg ${S} fill="currentColor"><rect x="3" y="5" width="18" height="2.2" rx="1.1"/><rect x="3" y="10.9" width="18" height="2.2" rx="1.1"/><rect x="3" y="16.8" width="12" height="2.2" rx="1.1"/></svg>`;
  ICONS.pencil = `<svg ${S} ${stroke}><path d="M4 20h4l10.5-10.5a2.1 2.1 0 00-3-3L5 17v3z"/><path d="M13.5 6.5l3 3"/></svg>`;
  ICONS.send = `<svg ${S} ${stroke}><path d="M4 12l16-8-6 16-2.5-6.5z"/><path d="M11.5 13.5L20 4"/></svg>`;
  ICONS.message = `<svg ${S} ${stroke}><path d="M4 5.5A1.5 1.5 0 015.5 4h13A1.5 1.5 0 0120 5.5v9a1.5 1.5 0 01-1.5 1.5H9l-4.5 4v-4A1.5 1.5 0 014 14.5z"/></svg>`;
  ICONS.copy = `<svg ${S} ${stroke}><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5.5A1.5 1.5 0 0014.5 4h-9A1.5 1.5 0 004 5.5v9A1.5 1.5 0 005.5 16H8"/></svg>`;

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
  const fmtShort = d => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  const parseDate = str => { if (!str) return null; const [y, m, d] = str.split('-').map(Number); return new Date(y, m - 1, d); };
  const dateLine = (s, fmt) => { const e = parseDate(s.endDate); return e ? 'Through ' + (fmt || fmtLong)(e) : ''; };
  const fullAddress = v => v.addressDetail ? v.address + ', ' + v.addressDetail : v.address;

  // ---------- DOM ----------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k === 'style') node.style.cssText = v;
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    children.flat().forEach(c => { if (c == null || c === false) return; node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return node;
  }
  const icon = name => el('span', { class: 'ico', html: ICONS[name], style: 'display:inline-flex' });
  const svg = name => { const s = el('span', { html: ICONS[name] }); return s.firstElementChild; };

  const DEFAULT_TABS = [['featured', 'star', 'Featured'], ['list', 'list', 'List'], ['map', 'map', 'Map']];
  // phone(id, tab, [opts], ...content); opts.tabs overrides the tab bar
  // ([key, icon, label] triples) so a frame can add a fourth tab or relabel one.
  function phone(id, tab, ...content) {
    let opts = {};
    if (content.length && content[0] && !(content[0] instanceof Node) && typeof content[0] === 'object') opts = content.shift();
    const tabs = opts.tabs || DEFAULT_TABS;
    const bar = el('nav', { class: 'lab-tabbar' }, ...tabs.map(([k, ic, label]) =>
      el('button', { class: 'tab-btn' + (k === tab ? ' active' : '') }, svg(ic), el('span', null, label))));
    return el('div', { class: 'phone', id: 'frame-' + id, 'data-frame': id },
      el('div', { class: 'statusbar' }, el('span', null, '9:41'),
        el('span', { class: 'sb-right' }, el('span', null, '●●●●'), el('span', { class: 'sb-batt' }))),
      el('div', { class: 'app' }, el('div', { class: 'screen' }, ...content), bar, el('div', { class: 'home-bar' })));
  }
  const navrow = (left, right) => el('div', { class: 'navrow' }, left || el('span'), right || el('span'));
  const citiesBtn = () => el('button', { class: 'nav-textbtn' }, 'Cities');
  const title = t => el('div', { class: 'large-title' }, t || city.displayName);
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
  const bookmark = on => el('button', { class: 'bookmark-btn' + (on ? ' saved' : '') }, svg(on ? 'bookmarkFill' : 'bookmark'));
  // extra.lead: node before the text; extra.afterVenue: node after the venue
  // name; extra.trail: replaces the bookmark; extra.saved: filled bookmark.
  function showRow(s, extra) {
    const x = extra || {};
    return el('div', { class: 'show-row' + (x.cls ? ' ' + x.cls : '') },
      x.lead || null,
      el('div', { class: 'sr-text' },
        el('div', { class: 'sr-name' }, displayName(s)),
        el('div', { class: 'sr-venue' }, s.venue.name, x.afterVenue || null),
        x.sub !== undefined ? (x.sub ? el('div', { class: 'sr-addr' }, x.sub) : null)
          : el('div', { class: 'sr-addr' }, s.venue.neighborhood ? `${s.venue.neighborhood} · ${s.venue.address}` : s.venue.address)),
      x.trail !== undefined ? x.trail : bookmark(x.saved));
  }
  const rows = (shows, fn) => el('div', { class: 'group list-results' }, ...shows.map(s => showRow(s, fn && fn(s))));
  const chip = (label, on, opts) => el('button', { class: 'chip' + (on ? ' on' : '') + (opts && opts.cls ? ' ' + opts.cls : '') },
    opts && opts.lead ? opts.lead : null, el('span', null, label), opts && opts.n != null ? el('span', { class: 'n' }, String(opts.n)) : null,
    opts && opts.menu ? svg('chevronDown') : null);
  const chipRow = (...chips) => el('div', { class: 'chip-row' }, ...chips);
  const searchField = ph => el('div', { class: 'search-bar' }, el('div', { class: 'search-field' }, svg('search'),
    el('input', { type: 'search', placeholder: ph || 'Artist, gallery, or show', autocomplete: 'off' })));
  const status = (n, clear) => el('div', { class: 'list-status' }, el('span', { class: 'list-count' }, `${n} shows`), clear ? el('button', { class: 'list-clear' }, 'Clear') : el('span'));
  const tierDots = t => el('span', { class: 'tier-dots' }, ...[1, 2, 3].map(i => el('i', { class: i <= 4 - t ? 'on' : '' })));

  // ---------- map ----------
  const BLUE = 'rgba(97, 173, 242, 0.85)';
  const RING = 'rgba(255, 255, 255, 0.6)';
  const FONT_BOLD = ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular'];
  const T_COLOR = { 1: 'rgba(97, 173, 242, 0.95)', 2: 'rgba(255, 255, 255, 0.85)', 3: 'rgba(150, 150, 158, 0.55)' };
  const MAP_CENTER = [-118.325, 34.058], MAP_ZOOM = 11.55;
  const MAP_STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
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

  // Offline stand-in: a flat dark box with CSS pins from a linear projection,
  // used when MapLibre is missing or its style fails to load.
  function fallbackMap(container, features, center, zoom) {
    container.classList.add('map-fallback');
    container.innerHTML = '';
    const w = container.clientWidth || 393, h = container.clientHeight || 852;
    const scale = 256 * Math.pow(2, zoom) / 360;          // px per degree lng at this zoom
    const latScale = scale / Math.cos(center[1] * Math.PI / 180);
    (features || []).forEach(f => {
      const [lng, lat] = f.geometry.coordinates;
      const x = w / 2 + (lng - center[0]) * scale, y = h / 2 - (lat - center[1]) * latScale;
      if (x < -20 || y < -20 || x > w + 20 || y > h + 20) return;
      const p = f.properties || {};
      const pin = el('div', { class: 'fb-pin t' + (p.tier || 3) + (p.stop ? ' stop' : '') + (p.dim ? ' dim' : ''), style: `left:${x}px;top:${y}px` }, p.stop ? String(p.stop) : null);
      container.append(pin, p.tier === 3 || p.dim ? null : el('div', { class: 'fb-label', style: `left:${x}px;top:${y + 9}px` }, p.name));
    });
  }

  // mode: shows (today, clustered) | tier | size | stars | topn | custom
  // custom: opt.features (GeoJSON) + opt.layers(map) adds the layers.
  function makeMap(container, mode, opt) {
    const o = opt || {};
    const center = o.center || MAP_CENTER, zoom = o.zoom || MAP_ZOOM;
    const list = mode === 'shows' ? venues : galleries;
    const geo = mode === 'custom' ? o.features : venueGeo(list);
    if (!window.maplibregl) { fallbackMap(container, geo.features, center, zoom); return null; }
    const map = new maplibregl.Map({ container, style: MAP_STYLE, center, zoom, attributionControl: { compact: true }, interactive: true });
    window.__labMaps.push(map);
    let loaded = false;
    const fail = () => { if (loaded) return; loaded = true; try { map.remove(); } catch (e) { /* ignore */ } fallbackMap(container, geo.features, center, zoom); };
    map.on('error', e => { if (!loaded && e && e.error && /style|Failed to fetch|NetworkError/i.test(String(e.error.message || e.error))) fail(); });
    setTimeout(() => { if (!loaded) fail(); }, 8000);
    map.on('load', () => {
      loaded = true;
      const alwaysDraw = { 'text-allow-overlap': true, 'text-ignore-placement': true };
      if (mode === 'custom') { map.addSource('v', { type: 'geojson', data: geo }); o.layers(map, { label, alwaysDraw }); return; }
      if (mode === 'shows') {
        map.addSource('v', { type: 'geojson', data: geo, cluster: true, clusterRadius: 44, clusterMaxZoom: 16 });
        const single = ['!', ['has', 'point_count']], multi = ['all', single, ['>', ['get', 'count'], 1]];
        map.addLayer({ id: 'clusters', type: 'circle', source: 'v', filter: ['has', 'point_count'], paint: { 'circle-color': BLUE, 'circle-radius': ['step', ['get', 'point_count'], 16, 10, 20, 30, 24], 'circle-stroke-width': 2, 'circle-stroke-color': RING } });
        map.addLayer({ id: 'cluster-count', type: 'symbol', source: 'v', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': FONT_BOLD, 'text-size': 13, ...alwaysDraw }, paint: { 'text-color': '#fff' } });
        map.addLayer({ id: 'venue-dot', type: 'circle', source: 'v', filter: single, paint: { 'circle-color': BLUE, 'circle-radius': 10.5, 'circle-stroke-width': 1, 'circle-stroke-color': RING } });
        map.addLayer({ id: 'venue-badge', type: 'circle', source: 'v', filter: multi, paint: { 'circle-color': '#fff', 'circle-radius': 8.5, 'circle-translate': [10, -9] } });
        map.addLayer({ id: 'venue-badge-count', type: 'symbol', source: 'v', filter: multi, layout: { 'text-field': ['to-string', ['get', 'count']], 'text-font': FONT_BOLD, 'text-size': 10, ...alwaysDraw }, paint: { 'text-color': '#000', 'text-translate': [10, -9] } });
        const l = label(); l.filter = single; map.addLayer(l);
        return;
      }
      map.addSource('v', { type: 'geojson', data: geo });
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
    const ph = phone(id, 'map', opt && opt.tabs ? { tabs: opt.tabs } : {}, screen);
    requestAnimationFrame(() => makeMap(box, mode, opt));
    return ph;
  }

  // ---------- page ----------
  // sections: [{ id, title, blurb, frames: [[fn, id, head, body, opts?]] }]
  // opts.live marks a frame that reacts to input (caption badge).
  function mountSections(root, header, sections) {
    root.appendChild(el('div', { class: 'lab-header' }, el('h1', null, header.title), el('p', { html: header.blurb }), header.extra || null));
    sections.forEach(sec => {
      const grid = el('div', { class: 'lab-grid' });
      sec.frames.forEach(([fn, id, head, body, fo]) => {
        let ph;
        try { ph = fn(); } catch (e) { console.error(id, e); ph = el('div', { class: 'phone' }, el('pre', { style: 'padding:20px;color:#f66;white-space:pre-wrap' }, String(e.stack))); }
        if (fo && fo.live) ph.dataset.live = '1';
        grid.appendChild(el('div', { class: 'lab-item' }, ph, el('div', { class: 'lab-caption' },
          el('b', null, id + ' · '), fo && fo.live ? el('span', { class: 'lab-live' }, 'live') : null, head, ' ', el('span', null, body))));
      });
      root.appendChild(el('section', { class: 'lab-section', id: sec.id }, el('h2', null, sec.title), el('p', { html: sec.blurb }), grid));
    });
  }

  window.Lab = {
    DATA, ICONS, DIST, CITY_KEY, city, LA,
    showId, venueKey, TIER1, TIER2, tierOf, TIER_LABEL, TIER_SHORT, venues, galleries, PICKS_N, picks, pickVenues,
    displayName, img, fmtLong, fmtShort, parseDate, dateLine, fullAddress,
    el, icon, svg, phone, DEFAULT_TABS, navrow, citiesBtn, title, page, card, feed, bookmark, showRow, rows, chip, chipRow, searchField, status, tierDots,
    BLUE, RING, FONT_BOLD, T_COLOR, MAP_CENTER, MAP_ZOOM, MAP_STYLE, venueGeo, starImage, label, makeMap, fallbackMap, mapPill, legend, mapFrame,
    mountSections,
  };
})();
