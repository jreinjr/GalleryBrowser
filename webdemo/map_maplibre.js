/* Map tab: MapLibre GL with CARTO dark style; pins as HTML markers. */
(function () {
  'use strict';

  window.DemoMap = function ({ getCity, getShows, onPinTap }) {
    let map = null;
    let markers = [];

    function cityBounds(c) {
      const half = c.span / 2;
      return [
        [c.center.lng - half, c.center.lat - half],
        [c.center.lng + half, c.center.lat + half],
      ];
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
      map.on('load', renderPins);
      window.__demoMap = map;
    }

    function renderPins() {
      if (!map) return;
      markers.forEach(m => m.remove());
      markers = [];
      getShows().forEach(s => {
        const v = s.venue;
        const pin = document.createElement('div');
        const dot = document.createElement('div');
        dot.className = 'map-pin';
        const label = document.createElement('div');
        label.className = 'map-pin-label';
        label.textContent = v.name;
        pin.append(dot, label);
        pin.addEventListener('click', e => { e.stopPropagation(); onPinTap(s); });
        markers.push(new maplibregl.Marker({ element: pin }).setLngLat([v.lng, v.lat]).addTo(map));
      });
    }

    function cityChanged() {
      if (!map) return;
      map.fitBounds(cityBounds(getCity()), { padding: 30, duration: 700 });
      renderPins();
    }

    return { ensureInit, cityChanged, applyFilter: renderPins };
  };
})();
