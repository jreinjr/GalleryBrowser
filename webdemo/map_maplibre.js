/* Map tab: MapLibre GL with CARTO dark style; venues as a clustered GeoJSON source.
 *
 * Venues cluster into numbered bubbles when zoomed out and split apart as the
 * user zooms in. Name labels live in a symbol layer, so MapLibre's collision
 * engine guarantees they never overlap (a label is hidden when there is no
 * room; the dot itself is a circle layer and always renders). */
(function () {
  'use strict';

  const SRC = 'venues';
  const BLUE = 'rgba(97, 173, 242, 0.85)';
  const MUSEUM_TINT = null;            // e.g. 'rgba(242,176,97,0.85)' to tint museum pins; null = all blue
  const RING = 'rgba(255, 255, 255, 0.6)';
  // Copy the CARTO style's own font stacks so the glyph request hits a
  // combination the tile server already serves.
  const FONT_BOLD = ['Montserrat Medium', 'Open Sans Bold', 'Noto Sans Regular',
    'HanWangHeiLight Regular', 'NanumBarunGothic Regular'];
  const CLICK_LAYERS = ['clusters', 'venue-dot', 'venue-label'];

  // venueKey(venue) -> string groups shows that share a venue (one pin per venue).
  // onPinTap(show) opens a single show; onVenueTap(venue, shows) opens a venue
  // that has several concurrent shows.
  window.DemoMap = function ({ getCity, getShows, venueKey, onPinTap, onVenueTap }) {
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
    // feature (with a count) instead of a stack of identically labelled points.
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
        properties: { key, name: v.name, count: shows.length, museum: !!v.isMuseum },
      }));
      return { type: 'FeatureCollection', features };
    }

    function addLayers() {
      map.addSource(SRC, {
        type: 'geojson',
        data: buildGeoJSON(),
        cluster: true,
        clusterRadius: 44,      // px: a 22px dot plus its label fits without touching
        clusterMaxZoom: 16,     // beyond z16 every venue stands alone
      });
      const single = ['!', ['has', 'point_count']];
      const multi = ['all', single, ['>', ['get', 'count'], 1]];
      const alwaysDraw = { 'text-allow-overlap': true, 'text-ignore-placement': true };

      // Cluster bubble: same blue, larger, number inside.
      map.addLayer({
        id: 'clusters', type: 'circle', source: SRC, filter: ['has', 'point_count'],
        paint: {
          'circle-color': BLUE,
          'circle-radius': ['step', ['get', 'point_count'], 16, 10, 20, 30, 24],
          'circle-stroke-width': 2, 'circle-stroke-color': RING,
        },
      });
      map.addLayer({
        id: 'cluster-count', type: 'symbol', source: SRC, filter: ['has', 'point_count'],
        layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-font': FONT_BOLD, 'text-size': 13, ...alwaysDraw },
        paint: { 'text-color': '#fff' },
      });
      // Venue dot: 22px = radius 10.5 + 1px ring; circle layers never collide.
      map.addLayer({
        id: 'venue-dot', type: 'circle', source: SRC, filter: single,
        paint: {
          'circle-color': MUSEUM_TINT ? ['case', ['get', 'museum'], MUSEUM_TINT, BLUE] : BLUE,
          'circle-radius': 10.5,
          'circle-stroke-width': 1, 'circle-stroke-color': RING,
        },
      });
      // Count badge for venues with several concurrent shows (top-right of the dot).
      map.addLayer({
        id: 'venue-badge', type: 'circle', source: SRC, filter: multi,
        paint: { 'circle-color': '#fff', 'circle-radius': 8.5, 'circle-translate': [10, -9] },
      });
      map.addLayer({
        id: 'venue-badge-count', type: 'symbol', source: SRC, filter: multi,
        layout: { 'text-field': ['to-string', ['get', 'count']], 'text-font': FONT_BOLD, 'text-size': 10, ...alwaysDraw },
        paint: { 'text-color': '#000', 'text-translate': [10, -9] },
      });
      // Name label under the dot; default collision => labels never overlap.
      map.addLayer({
        id: 'venue-label', type: 'symbol', source: SRC, filter: single,
        layout: {
          'text-field': ['get', 'name'],
          'text-font': FONT_BOLD, 'text-size': 11,
          'text-anchor': 'top', 'text-offset': [0, 1.2],
          'text-max-width': 12, 'text-padding': 4,
          'symbol-sort-key': ['*', -1, ['get', 'count']],   // busier venues win label slots
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
      if (f.properties.cluster) {
        map.getSource(SRC).getClusterExpansionZoom(f.properties.cluster_id).then(zoom => {
          map.easeTo({ center: f.geometry.coordinates, zoom: zoom + 0.5, duration: 500 });
        });
        return;
      }
      const g = groups.get(f.properties.key);
      if (!g) return;
      if (g.shows.length > 1) onVenueTap(g.venue, g.shows);
      else onPinTap(g.shows[0]);
    }

    function ensureInit() {
      if (map || !window.maplibregl) return;
      const c = getCity();
      map = new maplibregl.Map({
        container: 'maplibre',
        style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
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
})();
