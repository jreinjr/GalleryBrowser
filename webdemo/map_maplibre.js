/* Map tab: MapLibre GL with CARTO dark style; venues as a GeoJSON source.
 *
 * Galleries over the filtered show set: one unclustered dot per venue,
 * coloured and sized by gallery tier. Name labels live in a symbol layer, so
 * MapLibre's collision engine guarantees they never overlap (a label is hidden
 * when there is no room; the dot itself is a circle layer and always renders).
 * Better-ranked venues sort first, so they win the label slots. */
(function () {
  'use strict';

  const SRC = 'venues';
  // CARTO's vector basemap: no API key, and the venue-page card reuses it.
  const STYLE = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';
  // tier -> dot colour / radius. Keep in step with .map-legend in styles.css.
  const TIER_COLOR = { top: 'rgba(97, 173, 242, 0.95)', notable: 'rgba(255, 255, 255, 0.85)', listed: 'rgba(150, 150, 158, 0.55)' };
  const TIER_RADIUS = { top: 10.5, notable: 8, listed: 5.5 };
  const RING = 'rgba(255, 255, 255, 0.6)';
  // Copy the CARTO style's own font stacks so the glyph request hits a
  // combination the tile server already serves.
  const FONT_BOLD = ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular',
    'HanWangHeiLight Regular', 'NanumBarunGothic Regular'];
  const CLICK_LAYERS = ['gal-dot', 'gal-label'];

  // venueKey(venue) -> string groups shows that share a venue (one dot per venue).
  // venueTier(venue) -> 'top' | 'notable' | 'listed'.
  // onVenueTap(venue, shows) opens the venue page.
  window.DemoMap = function ({ getCity, getShows, venueKey, venueTier, onVenueTap }) {
    let map = null;
    let ready = false;          // style loaded, source + layers added
    let groups = new Map();     // key -> { venue, shows }, refreshed on every render

    function cityBounds(c) {
      const half = c.span / 2;
      return [
        [c.center.lng - half, c.center.lat - half],
        [c.center.lng + half, c.center.lat + half],
      ];
    }

    // Several shows can run at one venue: group them so each venue is a single
    // feature instead of a stack of identically labelled points.
    function buildGeoJSON() {
      groups = new Map();
      getShows().forEach(s => {
        const key = venueKey(s.venue);
        const g = groups.get(key);
        if (g) g.shows.push(s);
        else groups.set(key, { venue: s.venue, shows: [s] });
      });
      const features = [];
      groups.forEach(({ venue: v, shows }, key) => features.push({
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [v.lng, v.lat] },
        properties: { key, name: v.name, count: shows.length,
                      // sort keys: best show rank at the venue, then the venue's own tier
                      rank: Math.min(...shows.map(s => s.rank ?? 1e9)),
                      tier: venueTier ? venueTier(v) : 'listed' },
      }));
      return { type: 'FeatureCollection', features };
    }
    const tierOrder = ['match', ['get', 'tier'], 'top', 0, 'notable', 1, 2];

    function addLayers() {
      map.addSource(SRC, { type: 'geojson', data: buildGeoJSON() });
      // One dot per venue, by tier; top dots paint above listed ones.
      map.addLayer({
        id: 'gal-dot', type: 'circle', source: SRC,
        layout: { 'circle-sort-key': ['*', -1, tierOrder] },
        paint: {
          'circle-color': ['match', ['get', 'tier'], 'top', TIER_COLOR.top, 'notable', TIER_COLOR.notable, TIER_COLOR.listed],
          'circle-radius': ['match', ['get', 'tier'], 'top', TIER_RADIUS.top, 'notable', TIER_RADIUS.notable, TIER_RADIUS.listed],
          'circle-stroke-width': ['match', ['get', 'tier'], 'listed', 0, 1], 'circle-stroke-color': RING,
        },
      });
      // Name label under the dot; default collision => labels never overlap.
      map.addLayer({
        id: 'gal-label', type: 'symbol', source: SRC,
        filter: ['!=', ['get', 'tier'], 'listed'],   // listed galleries stay unlabelled
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT_BOLD, 'text-size': 11,
          'text-anchor': 'top', 'text-offset': [0, 1.2],
          'text-max-width': 12, 'text-padding': 4,
          'symbol-sort-key': ['+', ['*', 1e6, tierOrder], ['get', 'rank']],   // top tier first, then show rank
        },
        paint: {
          'text-color': '#fff',
          'text-halo-color': 'rgba(0, 0, 0, 0.9)', 'text-halo-width': 1.2, 'text-halo-blur': 0.6,
        },
      });
    }

    // One map-level click: a bbox query returns the top-most feature under the
    // finger, so a dot and its own label never fire two handlers.
    function onClick(e) {
      const pad = 6;
      const box = [[e.point.x - pad, e.point.y - pad], [e.point.x + pad, e.point.y + pad]];
      const f = map.queryRenderedFeatures(box, { layers: CLICK_LAYERS })[0];
      if (!f) return;
      const g = groups.get(f.properties.key);
      if (g) onVenueTap(g.venue, g.shows);
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
      map.on('load', () => {
        addLayers();
        ready = true;
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
    }

    function cityChanged() {
      if (!map) return;
      map.fitBounds(cityBounds(getCity()), { padding: 30, duration: 700 });
      renderPins();
    }

    return { ensureInit, cityChanged, applyFilter: renderPins };
  };
  window.DemoMap.STYLE = STYLE;
})();
