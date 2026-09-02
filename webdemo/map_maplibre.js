/* Map tab: MapLibre GL with CARTO dark style; pins as HTML markers. */
(function () {
  'use strict';

  // venueKey(venue) -> string groups shows that share a venue (one pin per venue).
  // onPinTap(show) opens a single show; onVenueTap(venue, shows) opens a venue
  // that has several concurrent shows.
  window.DemoMap = function ({ getCity, getShows, venueKey, onPinTap, onVenueTap }) {
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
      // Several shows can run at one venue: group them so each venue gets a
      // single pin instead of a stack of identically labelled markers.
      const groups = new Map();
      getShows().forEach(s => {
        const key = venueKey(s.venue);
        const g = groups.get(key);
        if (g) g.shows.push(s);
        else groups.set(key, { venue: s.venue, shows: [s] });
      });
      groups.forEach(({ venue: v, shows }, key) => {
        const pin = document.createElement('div');
        pin.dataset.venueKey = key;
        pin.dataset.count = String(shows.length);
        const dot = document.createElement('div');
        dot.className = 'map-pin';
        if (shows.length > 1) {
          const count = document.createElement('div');
          count.className = 'map-pin-count';
          count.textContent = String(shows.length);
          dot.appendChild(count);
        }
        const label = document.createElement('div');
        label.className = 'map-pin-label';
        label.textContent = v.name;
        pin.append(dot, label);
        pin.addEventListener('click', e => {
          e.stopPropagation();
          if (shows.length > 1) onVenueTap(v, shows);
          else onPinTap(shows[0]);
        });
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
