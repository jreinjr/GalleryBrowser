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
  // A gallery marked "See" on its map card is drawn red, whatever its band.
  const SEE_COLOR = 'rgba(229, 57, 53, 1)';
  const IS_SEE = ['==', ['get', 'see'], 1];
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
  const CLICK_LAYERS = ['gal-dot', 'gal-label', 'ctx-dot', 'ctx-label'];
  // A list as context: its venues in blue over a dimmed backdrop; a route
  // list also draws a dashed line through the stops in order.
  const CTX = 'ctx';
  const CTX_LAYERS = ['ctx-line', 'ctx-dim', 'ctx-dot', 'ctx-num', 'ctx-label'];
  const GAL_LAYERS = ['gal-dot', 'gal-label'];

  // getVenues() -> [{ key, venue, shows, active, tier, rank }]: one entry per
  //   venue to draw (app.js mapVenues), tier 'top' | 'notable' | 'listed', rank
  //   the gallery's rank or null, active whether a show is on view there.
  // venueKey(venue) -> string groups shows that share a venue (the context layer).
  // onVenueTap(venue, shows) opens the venue page.
  // getContext() -> null | { list: {kind}, shows: [ordered list shows], backdrop: [other shows] }
  window.DemoMap = function ({ getCity, getVenues, venueKey, onVenueTap, getContext }) {
    let map = null;
    let ready = false;          // style loaded, source + layers added
    let groups = new Map();     // key -> { venue, shows }, refreshed on every render
    let ctxKey = null;          // which list the context source currently shows

    function cityBounds(c) {
      const half = c.span / 2;
      return [
        [c.center.lng - half, c.center.lat - half],
        [c.center.lng + half, c.center.lat + half],
      ];
    }

    // One feature per venue (the app has already grouped a venue's concurrent
    // shows). Sort keys are the gallery's own tier and rank — a show has no
    // rank of its own — with an active venue ahead of a faded one.
    function buildGeoJSON() {
      groups = new Map();
      const features = [];
      getVenues().forEach(g => {
        const v = g.venue;
        if (v.lat == null || v.lng == null) return;
        groups.set(g.key, { venue: v, shows: g.shows });
        features.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [v.lng, v.lat] },
          properties: { key: g.key, name: v.name, count: g.shows.length, active: g.active ? 1 : 0,
                        tier: g.tier || 'listed', band: bandForRank(g.rank), see: g.see ? 1 : 0, rank: g.rank == null ? 1e5 : g.rank },
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

    function addLayers() {
      map.addSource(SRC, { type: 'geojson', data: buildGeoJSON() });
      map.addSource(CTX, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      // One dot per venue, by rank band; top-25 dots paint above 26–150 ones
      // and those above the rest, active ones above faded ones of the same band.
      map.addLayer({
        id: 'gal-dot', type: 'circle', source: SRC,
        layout: { 'circle-sort-key': ['+', ['-', ['*', -1, ['get', 'band']], ['*', 0.5, activeOrder]], ['*', 5, ['get', 'see']]] },
        paint: {
          'circle-color': ['case', IS_SEE, SEE_COLOR, ['match', ['get', 'band'], 0, BAND_COLOR[0], 1, BAND_COLOR[1], BAND_COLOR[2]]],
          'circle-radius': ['match', ['get', 'band'], 0, BAND_RADIUS[0], 1, BAND_RADIUS[1], BAND_RADIUS[2]],
          'circle-opacity': DOT_OPACITY,
          'circle-stroke-width': 1, 'circle-stroke-color': RING,
          'circle-stroke-opacity': DOT_OPACITY,
        },
      });
      // Name label under the dot; default collision => labels never overlap.
      map.addLayer({
        id: 'gal-label', type: 'symbol', source: SRC,
        filter: LABEL_FILTER,   // listed galleries stay unlabelled
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT_BOLD, 'text-size': 11,
          'text-anchor': 'top', 'text-offset': [0, 1.2],
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
        map.fitBounds([[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]], { padding: { top: 100, bottom: 210, left: 70, right: 70 }, maxZoom: 15, duration: 600 });
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
      if (g) { onVenueTap(g.venue, g.shows); return; }
      // a context stop whose shows are not in the filtered set
      const ctx = getContext ? getContext() : null;
      const shows = ctx ? ctx.shows.filter(s => venueKey(s.venue) === f.properties.key) : [];
      if (shows.length) onVenueTap(shows[0].venue, shows);
    }

    function ensureInit() {
      if (map || !window.maplibregl) return;
      const c = getCity();
      map = new maplibregl.Map({
        container: 'maplibre',
        style: STYLE,
        bounds: cityBounds(c),
        fitBoundsOptions: { padding: 30 },
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
        addLayers();
        ready = true;
        renderContext();
        map.on('click', onClick);
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
      map.getSource(SRC).setData(buildGeoJSON());
      renderContext();
    }

    function cityChanged() {
      if (!map) return;
      ctxKey = null;
      map.fitBounds(cityBounds(getCity()), { padding: 30, duration: 700 });
      renderPins();
    }

    return { ensureInit, cityChanged, applyFilter: renderPins };
  };
  window.DemoMap.STYLE = STYLE;
})();
