/* Map tab: MapLibre GL with CARTO's Voyager street style; venues as a GeoJSON source.
 *
 * Galleries: one unclustered dot per venue, coloured and sized by the
 * gallery's city-wide rank band (top 25, 26–150, the rest). With the
 * Active-shows filter off the app hands over every gallery of the city, and
 * a venue with nothing on view is drawn faded. Name labels live in a symbol
 * layer, so MapLibre's collision engine guarantees they never overlap (a label
 * is hidden when there is no room; the dot itself is a circle layer and always
 * renders). Better-ranked venues sort first, so they win the label slots, and
 * an active venue beats a faded one of the same tier. */
(function () {
  'use strict';

  const SRC = 'venues';
  // CARTO's vector basemap: no API key, and the venue-page card reuses it.
  // Voyager is the light, street-map style with legible street and place names.
  const STYLE = 'https://basemaps.cartocdn.com/gl/voyager-gl-style/style.json';
  // Rank band -> dot colour: every dot is one size; the top 25 a deep blue,
  // 26–100 a lighter blue and the rest grey. Keep in step with the iOS MapTabView.
  const BAND_TOP = 25, BAND_RANKED = 100;
  const BAND_COLOR = ['rgba(21, 101, 214, 1)', 'rgba(160, 205, 248, 0.95)', 'rgba(130, 130, 138, 0.6)'];
  const BAND_RADIUS = [5.25, 5.25, 5.25];
  // A gallery with a saved show is drawn red, whatever its band; one the
  // person marked a gem is drawn as a gem instead of a dot (red too when it
  // has a saved show).
  const SAVED_COLOR = 'rgba(229, 57, 53, 1)';
  const IS_SAVED = ['==', ['get', 'saved'], 1];
  const IS_GEM = ['==', ['get', 'gem'], 1];
  const GEM_IMAGE = ['case', IS_SAVED, 'gem-red', ['match', ['get', 'band'], 0, 'gem-0', 1, 'gem-1', 'gem-2']];
  // Gem icons for the symbol layer, one per colour, drawn on a canvas at 3x.
  // With an emoji picked (`sym`) the icons are that emoji on a white disc with
  // a soft shadow, or on a red disc when the gallery has a saved show (an
  // emoji cannot be recoloured).
  function addGemImages(map, sym) {
    const S = sym ? 28 : 20, R = 3, P = S * R;   // an emoji gets a bigger, shadowed disc so it reads at a glance
    const colors = { 'gem-0': BAND_COLOR[0], 'gem-1': 'rgba(120, 180, 240, 1)', 'gem-2': 'rgba(130, 130, 138, 1)', 'gem-red': SAVED_COLOR };
    Object.entries(colors).forEach(([id, fill]) => {
      const c = document.createElement('canvas'); c.width = c.height = P;
      const g = c.getContext('2d');
      if (sym) {
        const r = P / 2 - 4 * R;
        g.save();
        g.shadowColor = 'rgba(0, 0, 0, 0.35)'; g.shadowBlur = 3 * R; g.shadowOffsetY = 1 * R;
        g.beginPath(); g.arc(P / 2, P / 2, r, 0, 2 * Math.PI);
        g.fillStyle = id === 'gem-red' ? fill : '#ffffff'; g.fill();
        g.restore();
        g.lineWidth = 1.5 * R; g.strokeStyle = id === 'gem-red' ? '#ffffff' : 'rgba(25, 75, 135, 0.35)'; g.stroke();
        g.font = `${Math.round(r * 1.3)}px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif`;
        g.textAlign = 'center'; g.textBaseline = 'middle';
        g.fillText(sym, P / 2, P / 2 + r * 0.08);
      } else {
        g.scale(P / 24, P / 24);
        const outline = () => { g.beginPath(); g.moveTo(6.5, 3.5); g.lineTo(17.5, 3.5); g.lineTo(21.5, 9); g.lineTo(12, 21); g.lineTo(2.5, 9); g.closePath(); };
        outline(); g.fillStyle = fill; g.fill();
        g.lineJoin = 'round'; g.strokeStyle = 'rgba(255, 255, 255, 0.7)'; g.lineWidth = 1;
        g.beginPath(); g.moveTo(2.5, 9); g.lineTo(21.5, 9); g.moveTo(9, 3.5); g.lineTo(7.5, 9); g.lineTo(12, 21); g.lineTo(16.5, 9); g.lineTo(15, 3.5); g.stroke();
        outline(); g.strokeStyle = RING; g.lineWidth = 1.2; g.stroke();
      }
      const img = g.getImageData(0, 0, P, P);
      // updateImage needs the same size; switching gem <-> emoji changes it
      if (map.hasImage(id)) map.removeImage(id);
      map.addImage(id, img, { pixelRatio: R });
    });
  }
  // An eye: the person's own "watch this one" mark, drawn as a small 👁️ badge
  // above the top-right of the gallery's pin (dot or gem).
  const IS_EYE = ['==', ['get', 'eye'], 1];
  function addEyeImage(map) {
    const S = 18, R = 3, P = S * R;
    const c = document.createElement('canvas'); c.width = c.height = P;
    const g = c.getContext('2d');
    const r = P / 2 - 2 * R;
    g.save();
    g.shadowColor = 'rgba(0, 0, 0, 0.35)'; g.shadowBlur = 2 * R; g.shadowOffsetY = 0.5 * R;
    g.beginPath(); g.arc(P / 2, P / 2, r, 0, 2 * Math.PI); g.fillStyle = '#ffffff'; g.fill();
    g.restore();
    g.lineWidth = 1 * R; g.strokeStyle = 'rgba(25, 75, 135, 0.45)'; g.stroke();
    g.font = `${Math.round(r * 1.25)}px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif`;
    g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText('👁️', P / 2, P / 2 + r * 0.08);
    if (map.hasImage('eye-badge')) map.removeImage('eye-badge');
    map.addImage('eye-badge', g.getImageData(0, 0, P, P), { pixelRatio: R });
  }
  // Several galleries in one building share a pin: larger, with the count inside.
  const IS_SHARED = ['>', ['coalesce', ['get', 'n'], 1], 1];
  const SHARED_RADIUS = 9;
  const bandForRank = r => r == null ? 2 : r <= BAND_TOP ? 0 : r <= BAND_RANKED ? 1 : 2;
  // A dark-blue ring keeps the pale dots readable on the light basemap.
  const RING = 'rgba(25, 75, 135, 0.75)';
  // A venue with nothing on view (only reachable with Active shows off) is semi-transparent.
  const IS_ACTIVE = ['==', ['get', 'active'], 1];
  const DOT_OPACITY = ['case', IS_ACTIVE, 1, 0.38];
  const LABEL_OPACITY = ['case', IS_ACTIVE, 1, 0.55];
  // Copy the CARTO style's own font stacks so the glyph request hits a
  // combination the tile server already serves.
  const FONT_BOLD = ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular',
    'HanWangHeiLight Regular', 'NanumBarunGothic Regular'];
  const CLICK_LAYERS = ['gal-dot', 'gal-gem', 'gal-eye', 'gal-count', 'gal-label', 'ctx-dot', 'ctx-label'];
  // A list as context: its venues in blue over a dimmed backdrop; a route
  // list also draws a dashed line through the stops in order.
  const CTX = 'ctx';
  const CTX_LAYERS = ['ctx-line', 'ctx-dim', 'ctx-dot', 'ctx-num', 'ctx-label'];
  const GAL_LAYERS = ['gal-dot', 'gal-gem', 'gal-eye', 'gal-count', 'gal-label'];

  // getVenues() -> [{ key, venue, shows, active, tier, rank }]: one entry per
  //   venue to draw (app.js mapVenues), tier 'top' | 'notable' | 'listed', rank
  //   the gallery's rank or null, active whether a show is on view there.
  // venueKey(venue) -> string groups shows that share a venue (the context layer).
  // onVenueTap(venue, shows, others) opens the venue card; others are the
  //   [{ venue, shows }] of further galleries sharing that pin (one building).
  // getGemSymbol() -> null (the built-in gem) or the emoji the person picked for gems.
  // getContext() -> null | { list: {kind}, shows: [ordered list shows], backdrop: [other shows] }
  // onViewChange() runs once the map is ready and after every pan or zoom (the show list under the map).
  // getBottomInset() -> px of the map hidden at the bottom (the show list and tab bar).
  window.DemoMap = function ({ getCity, getVenues, venueKey, onVenueTap, getContext, getGemSymbol, onViewChange, getBottomInset }) {
    let map = null;
    let ready = false;          // style loaded, source + layers added
    let groups = new Map();     // key -> { venue, shows }, refreshed on every render
    let gemSym = null;          // the symbol the gem images were last drawn with
    let stacks = new Map();     // lead key -> keys of every gallery on that pin, lead first
    let ctxKey = null;          // which list the context source currently shows
    let hlKey = null;           // the pin picked from the map, ringed until another is picked
    const inset = () => (getBottomInset ? getBottomInset() : 0);
    const fitPad = () => ({ top: 60, bottom: inset() + 20, left: 30, right: 30 });

    function cityBounds(c) {
      const half = c.span / 2;
      return [
        [c.center.lng - half, c.center.lat - half],
        [c.center.lng + half, c.center.lat + half],
      ];
    }

    // One feature per venue (the app has already grouped a venue's concurrent
    // shows). Galleries in one building share coordinates, so their dots would
    // stack and only the top one could be tapped: each keeps its own pin, the
    // best-ranked at the address and the others nudged onto a small ring
    // (~17 m) around it, so at street zoom every gallery is its own dot. Sort
    // keys are the gallery's own tier and rank, active ahead of faded.
    const SAME_SPOT = 0.00005;          // degrees, ~5 m
    const NUDGE = 0.00015;              // degrees of latitude, ~17 m
    const leadOrder = g => bandForRank(g.rank) * 1e6 + (g.active ? 0 : 5e5) + (g.rank == null ? 1e5 : g.rank);
    function buildGeoJSON() {
      groups = new Map();
      stacks = new Map();
      const spots = [];
      getVenues().forEach(g => {
        const v = g.venue;
        if (v.lat == null || v.lng == null) return;
        groups.set(g.key, { venue: v, shows: g.shows });
        const spot = spots.find(s => Math.abs(s.lat - v.lat) < SAME_SPOT && Math.abs(s.lng - v.lng) < SAME_SPOT);
        if (spot) spot.members.push(g); else spots.push({ lat: v.lat, lng: v.lng, members: [g] });
      });
      const features = spots.flatMap(({ lat, lng, members }) => {
        members.sort((a, b) => leadOrder(a) - leadOrder(b));
        const ring = members.length - 1;
        return members.map((g, i) => {
          const ang = ring ? (2 * Math.PI * (i - 1)) / ring : 0;   // first neighbour due east, clear of the label below
          const pos = i === 0 ? [lng, lat]
            : [lng + NUDGE * Math.cos(ang) / Math.cos(lat * Math.PI / 180), lat + NUDGE * Math.sin(ang)];
          stacks.set(g.key, [g.key]);
          return {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: pos },
            properties: { key: g.key, keys: g.key, name: g.venue.name, n: 1,
                          count: g.shows.length, active: g.active ? 1 : 0,
                          tier: g.tier || 'listed', band: bandForRank(g.rank), saved: g.saved ? 1 : 0,
                          gem: g.gem ? 1 : 0, eye: g.eye ? 1 : 0, rank: g.rank == null ? 1e5 : g.rank },
          };
        });
      });
      return { type: 'FeatureCollection', features };
    }
    const tierOrder = ['match', ['get', 'tier'], 'top', 0, 'notable', 1, 2];
    const activeOrder = ['case', IS_ACTIVE, 0, 1];

    // Context features: the list's venues as numbered stops (a route) or
    // plain highlighted dots, other venues of the filter as dim grey, and for
    // a route a LineString through the stops. Stops within ~60 m merge into
    // one pin labelled "1·2" so two galleries in one building read as one.
    function buildContextGeoJSON(ctx) {
      const stops = []; const keys = new Set();
      ctx.shows.forEach(s => {
        const v = s.venue, key = venueKey(v);
        const g = groups.get(key); if (g) { /* also in the filtered set */ }
        if (keys.has(key)) return;
        keys.add(key);
        const near = stops.find(f => Math.abs(f.geometry.coordinates[0] - v.lng) < 0.0006 && Math.abs(f.geometry.coordinates[1] - v.lat) < 0.0006);
        if (near) { near.properties.stop += '·' + (stops.length + near.properties.merged + 1); near.properties.merged += 1; near.properties.name += ' / ' + v.name; return; }
        stops.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [v.lng, v.lat] },
          properties: { key, name: v.name, stop: String(stops.length + 1 + stops.reduce((a, f) => a + f.properties.merged, 0)), merged: 0, on: 1 } });
      });
      const dimKeys = new Map();
      (ctx.backdrop || []).forEach(s => { const key = venueKey(s.venue); if (!keys.has(key) && !dimKeys.has(key)) dimKeys.set(key, s.venue); });
      const dim = [...dimKeys.entries()].map(([key, v]) => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [v.lng, v.lat] }, properties: { key, name: v.name, dim: 1 } }));
      const features = [...dim, ...stops];
      if (ctx.list && ctx.list.kind === 'route' && stops.length > 1) {
        features.unshift({ type: 'Feature', geometry: { type: 'LineString', coordinates: stops.map(f => f.geometry.coordinates) }, properties: { line: 1 } });
      }
      return { type: 'FeatureCollection', features, stops };
    }

    const LABEL_FILTER = ['!=', ['get', 'tier'], 'listed'];

    // CARTO hides street names until zoom 13–16 and draws them pale; bring
    // them in from city zoom, darker, so the map reads like a street map. Gallery labels are added
    // later, so they still win label collisions.
    const STREET_LABELS = { roadname_major: 10, roadname_pri: 11, roadname_sec: 12, roadname_minor: 13 };
    function showStreetNames() {
      Object.entries(STREET_LABELS).forEach(([id, minzoom]) => {
        if (!map.getLayer(id)) return;
        map.setLayerZoomRange(id, minzoom, 24);
        map.setLayoutProperty(id, 'text-size', ['interpolate', ['linear'], ['zoom'], 11, 10, 14, 11.5, 17, 13]);
        map.setPaintProperty(id, 'text-color', '#3a3a3c');
        map.setPaintProperty(id, 'text-halo-color', 'rgba(255, 255, 255, 0.9)');
        map.setPaintProperty(id, 'text-halo-width', 1.2);
      });
    }

    // Main roads drawn bolder and warmer, closer to Google Maps: motorways and
    // trunk roads orange, primary and secondary arterials yellow, each wider
    // with a darker casing, so the street grid reads at a glance.
    const ROAD_STYLE = [
      [/_(mot|trunk)_case/, 1.5, '#e8a33d'], [/_(mot|trunk)_fill/, 1.5, '#ffc95c'],
      [/_(pri|sec)_case/, 1.45, '#e3c06b'], [/_(pri|sec)_fill/, 1.45, '#ffe594'],
    ];
    function boldRoads() {
      const scale = (w, k) => w && w.stops ? { ...w, stops: w.stops.map(([z, v]) => [z, v * k]) }
        : typeof w === 'number' ? w * k : w;
      map.getStyle().layers.forEach(l => {
        if (l.type !== 'line' || !/^(road|bridge|tunnel)_/.test(l.id)) return;
        const hit = ROAD_STYLE.find(([re]) => re.test(l.id));
        if (!hit) return;
        const [, k, color] = hit;
        map.setPaintProperty(l.id, 'line-width', scale(map.getPaintProperty(l.id, 'line-width'), k));
        if (!l.id.startsWith('tunnel_')) map.setPaintProperty(l.id, 'line-color', color);
      });
    }

    function addLayers() {
      map.addSource(SRC, { type: 'geojson', data: buildGeoJSON() });
      map.addSource(CTX, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      // One dot per venue, by rank band; top-25 dots paint above 26–150 ones
      // and those above the rest, active ones above faded ones of the same band.
      gemSym = getGemSymbol ? getGemSymbol() : null;
      addGemImages(map, gemSym);
      addEyeImage(map);
      // The picked pin: an orange ring under its dot.
      map.addLayer({
        id: 'gal-hl', type: 'circle', source: SRC, filter: ['==', ['get', 'key'], hlKey || ''],
        paint: { 'circle-radius': 13, 'circle-color': 'rgba(255, 149, 0, 0.28)', 'circle-stroke-width': 2.5, 'circle-stroke-color': 'rgba(255, 149, 0, 1)' },
      });
      map.addLayer({
        id: 'gal-dot', type: 'circle', source: SRC, filter: ['!', IS_GEM],
        layout: { 'circle-sort-key': ['+', ['-', ['*', -1, ['get', 'band']], ['*', 0.5, activeOrder]], ['*', 5, ['get', 'saved']]] },
        paint: {
          'circle-color': ['case', IS_SAVED, SAVED_COLOR, ['match', ['get', 'band'], 0, BAND_COLOR[0], 1, BAND_COLOR[1], BAND_COLOR[2]]],
          'circle-radius': ['case', IS_SHARED, SHARED_RADIUS, ['match', ['get', 'band'], 0, BAND_RADIUS[0], 1, BAND_RADIUS[1], BAND_RADIUS[2]]],
          'circle-opacity': DOT_OPACITY,
          'circle-stroke-width': 1, 'circle-stroke-color': RING,
          'circle-stroke-opacity': DOT_OPACITY,
        },
      });
      // Gems: the person's marked galleries, drawn as a gem in place of the dot.
      map.addLayer({
        id: 'gal-gem', type: 'symbol', source: SRC, filter: IS_GEM,
        layout: { 'icon-image': GEM_IMAGE, 'icon-size': ['case', IS_SHARED, 1.3, 1], 'icon-allow-overlap': true, 'icon-ignore-placement': true },
        paint: { 'icon-opacity': DOT_OPACITY },
      });
      map.addLayer({
        id: 'gal-eye', type: 'symbol', source: SRC, filter: IS_EYE,
        layout: { 'icon-image': 'eye-badge', 'icon-anchor': 'bottom-left', 'icon-offset': ['case', IS_GEM, ['literal', [3, -3]], ['literal', [2, -2]]],
                  'icon-allow-overlap': true, 'icon-ignore-placement': true },
      });
      // The number of galleries on a shared pin, inside its dot.
      map.addLayer({
        id: 'gal-count', type: 'symbol', source: SRC, filter: ['all', IS_SHARED, ['!', IS_GEM]],   // a gem's names say who shares it
        layout: { 'text-field': ['to-string', ['get', 'n']], 'text-font': FONT_BOLD, 'text-size': 11,
                  'text-allow-overlap': true, 'text-ignore-placement': true },
        paint: { 'text-color': ['case', ['==', ['get', 'band'], 1], '#14365e', '#ffffff'], 'text-opacity': DOT_OPACITY },
      });
      // Name label under the dot; default collision => labels never overlap.
      map.addLayer({
        id: 'gal-label', type: 'symbol', source: SRC,
        filter: LABEL_FILTER,   // listed galleries stay unlabelled
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT_BOLD, 'text-size': 11,
          'text-anchor': 'top', 'text-offset': ['case', IS_SHARED, ['literal', [0, 1.5]], ['literal', [0, 1.2]]],
          'text-max-width': 12, 'text-padding': 4,
          // top tier first, active before faded, then the gallery's rank
          'symbol-sort-key': ['+', ['*', 1e6, tierOrder], ['*', 5e5, activeOrder], ['get', 'rank']],
        },
        paint: {
          'text-color': '#1c1c1e', 'text-opacity': LABEL_OPACITY,
          'text-halo-color': 'rgba(255, 255, 255, 0.95)', 'text-halo-width': 1.4, 'text-halo-blur': 0.4,
        },
      });
      // Context layers (hidden until a list is the context)
      map.addLayer({ id: 'ctx-line', type: 'line', source: CTX, filter: ['==', ['get', 'line'], 1], layout: { visibility: 'none' },
        paint: { 'line-color': '#61ADF2', 'line-width': 3, 'line-dasharray': [1.5, 1.2], 'line-opacity': 0.9 } });
      map.addLayer({ id: 'ctx-dim', type: 'circle', source: CTX, filter: ['==', ['get', 'dim'], 1], layout: { visibility: 'none' },
        paint: { 'circle-color': 'rgba(150, 150, 158, 0.32)', 'circle-radius': 4 } });
      map.addLayer({ id: 'ctx-dot', type: 'circle', source: CTX, filter: ['==', ['get', 'on'], 1], layout: { visibility: 'none' },
        paint: { 'circle-color': '#61ADF2', 'circle-radius': ['case', ['>', ['get', 'merged'], 0], 16, 12], 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } });
      map.addLayer({ id: 'ctx-num', type: 'symbol', source: CTX, filter: ['==', ['get', 'on'], 1],
        layout: { visibility: 'none', 'text-field': ['get', 'stop'], 'text-font': FONT_BOLD, 'text-size': 12, 'text-allow-overlap': true, 'text-ignore-placement': true },
        paint: { 'text-color': '#000' } });
      map.addLayer({ id: 'ctx-label', type: 'symbol', source: CTX, filter: ['==', ['get', 'on'], 1],
        layout: { visibility: 'none', 'text-field': ['get', 'name'], 'text-font': FONT_BOLD, 'text-size': 11, 'text-anchor': 'top', 'text-offset': [0, 1.5], 'text-max-width': 12, 'text-padding': 4 },
        paint: { 'text-color': '#1c1c1e', 'text-halo-color': 'rgba(255, 255, 255, 0.95)', 'text-halo-width': 1.4, 'text-halo-blur': 0.4 } });
    }

    function setVisible(ids, on) {
      ids.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none'); });
    }
    function renderContext() {
      const ctx = getContext ? getContext() : null;
      if (!ctx || !ctx.shows.length) {
        if (ctxKey !== null) { ctxKey = null; setVisible(CTX_LAYERS, false); setVisible(GAL_LAYERS, true); map.getSource(CTX).setData({ type: 'FeatureCollection', features: [] }); }
        return;
      }
      const geo = buildContextGeoJSON(ctx);
      map.getSource(CTX).setData({ type: 'FeatureCollection', features: geo.features });
      setVisible(GAL_LAYERS, false);
      setVisible(CTX_LAYERS, true);
      setVisible(['ctx-num'], ctx.list && ctx.list.kind === 'route');
      const key = ctx.list ? ctx.list.id : 'ctx';
      if (key !== ctxKey && geo.stops.length) {
        ctxKey = key;
        const lngs = geo.stops.map(f => f.geometry.coordinates[0]), lats = geo.stops.map(f => f.geometry.coordinates[1]);
        map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: { top: 100, bottom: inset() + 30, left: 70, right: 70 }, maxZoom: 15, duration: 600 });
      }
    }

    // One map-level click: a bbox query returns the features under the finger,
    // so a dot and its own label never fire two handlers. Two galleries in one
    // building stack their dots; the one painted on top (better tier, active
    // before faded, better rank) is the one the tap means.
    const TIER_ORDER = { top: 0, notable: 1, listed: 2 };
    const paintOrder = f => { const p = f.properties; return (p.band != null ? Number(p.band) : p.tier in TIER_ORDER ? TIER_ORDER[p.tier] : -1) * 1e6 + (p.active === 0 ? 5e5 : 0) + (Number(p.rank) || 0); };
    function onClick(e) {
      const pad = 6;
      const box = [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]];
      const hits = map.queryRenderedFeatures(box, { layers: CLICK_LAYERS.filter(id => map.getLayer(id)) });
      const f = hits.sort((a, b) => paintOrder(a) - paintOrder(b))[0];
      if (!f) return;
      const g = groups.get(f.properties.key);
      if (g) { onVenueTap(g.venue, g.shows, (stacks.get(f.properties.key) || []).slice(1).map(k => groups.get(k)), f.properties.key); return; }
      // a context stop whose shows are not in the filtered set
      const ctx = getContext ? getContext() : null;
      const shows = ctx ? ctx.shows.filter(s => venueKey(s.venue) === f.properties.key) : [];
      if (shows.length) onVenueTap(shows[0].venue, shows, [], f.properties.key);
    }

    function ensureInit() {
      if (map || !window.maplibregl) return;
      const c = getCity();
      map = new maplibregl.Map({
        container: 'maplibre',
        style: STYLE,
        bounds: cityBounds(c),
        fitBoundsOptions: { padding: fitPad() },
        attributionControl: { compact: true },
      });
      // The person's location: a blue dot that follows them, and a button that
      // recentres the map on it (asks for permission on first tap).
      map.addControl(new maplibregl.GeolocateControl({
        positionOptions: { enableHighAccuracy: true },
        trackUserLocation: true, showUserLocation: true, showAccuracyCircle: true,
      }), 'bottom-right');
      map.on('load', () => {
        showStreetNames();
        boldRoads();
        addLayers();
        ready = true;
        renderContext();
        map.on('click', onClick);
        if (onViewChange) { map.on('moveend', () => onViewChange()); onViewChange(); }
        if (window.matchMedia && window.matchMedia('(pointer: fine)').matches) {
          const canvas = map.getCanvas();
          CLICK_LAYERS.forEach(id => {
            map.on('mouseenter', id, () => { canvas.style.cursor = 'pointer'; });
            map.on('mouseleave', id, () => { canvas.style.cursor = ''; });
          });
        }
      });
      window.__demoMap = map;
    }

    function renderPins() {
      if (!ready) return;                 // the 'load' handler seeds the source itself
      const sym = getGemSymbol ? getGemSymbol() : null;
      if (sym !== gemSym) { gemSym = sym; addGemImages(map, sym); }
      map.getSource(SRC).setData(buildGeoJSON());
      renderContext();
    }

    function cityChanged() {
      if (!map) return;
      ctxKey = null;
      setHighlight(null);
      map.fitBounds(cityBounds(getCity()), { padding: fitPad(), duration: 700 });
      renderPins();
    }

    // Zoom back out to the whole city ("All shows" under the map).
    function showAll() {
      if (!map) return;
      map.fitBounds(cityBounds(getCity()), { padding: fitPad(), duration: 600 });
    }
    // Ring one pin (by its key), or none.
    function setHighlight(key) {
      hlKey = key || null;
      if (ready && map.getLayer('gal-hl')) map.setFilter('gal-hl', ['==', ['get', 'key'], hlKey || '']);
    }
    // Bring one gallery into view, zoomed in, and ring its pin (the Gallery
    // tab's map button). Before the basemap has loaded it waits for it.
    let pendingFocus = null;
    function focus(key, lng, lat) {
      if (lng == null || lat == null) return;
      if (!ready) {
        if (!pendingFocus && map) map.once('load', () => { const f = pendingFocus; pendingFocus = null; if (f) focus(...f); });
        pendingFocus = [key, lng, lat];
        return;
      }
      setHighlight(key);
      map.resize();
      map.flyTo({ center: [lng, lat], zoom: Math.max(map.getZoom(), 15), offset: [0, -inset() / 2], duration: 800 });
    }
    // Whether a point is on screen, above the show list. null until the map is ready.
    function inViewTest() {
      if (!ready) return null;
      const c = map.getContainer(), w = c.clientWidth, h = c.clientHeight - inset();
      if (w <= 0 || h <= 0) return null;   // not laid out (tab hidden): no view to narrow to
      return (lng, lat) => {
        if (lng == null || lat == null) return false;
        const p = map.project([lng, lat]);
        return p.x >= 0 && p.x <= w && p.y >= 0 && p.y <= h;
      };
    }
    // Centre a point in the part of the map above the list, without changing zoom.
    function reveal(lng, lat) {
      if (!ready || lng == null) return;
      const test = inViewTest();
      if (test && test(lng, lat)) return;
      map.easeTo({ center: [lng, lat], offset: [0, -inset() / 2], duration: 500 });
    }

    return { ensureInit, cityChanged, applyFilter: renderPins, showAll, setHighlight, focus, inViewTest, reveal, resize: () => map && map.resize() };
  };
  window.DemoMap.STYLE = STYLE;
})();
