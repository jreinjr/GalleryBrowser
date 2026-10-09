/* Gallery Browser web demo — vanilla JS port of the SwiftUI app. */
(function () {
  'use strict';

  const DATA = window.DEMO_DATA;
  const ICONS = window.ICONS;

  // ---------------- state ----------------
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : v; }
      catch (e) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* private mode */ } },
  };

  const readSet = key => { try { return new Set(JSON.parse(store.get(key, '[]')) || []); } catch (e) { return new Set(); } };
  const state = {
    cityKey: store.get('selectedCityKey', DATA.defaultCity),
    saved: readSet('savedShowIDs'),            // My Shows: "<city>/<slug>"
    favorites: readSet('favoriteVenueIDs'),    // favorite galleries: "<city>/<venueId>"
    toSee: readSet('seeVenueIDs'),             // galleries marked "See" on the Map: "<city>/<venueId>" (red dots)
    tab: 'featured',
  };
  if (!DATA.cities.some(c => c.key === state.cityKey)) state.cityKey = DATA.defaultCity;

  // ---------------- city list ----------------
  // The Cities sheet lists the cities in the person's order and only the ones
  // they show; both live in localStorage['cityOrder'] (versioned). Out of the
  // box the seven founding cities are shown, in the build's order; every other
  // city is there behind Edit. A city the payload no longer has drops out; a
  // new one joins at the end, hidden until shown.
  const CITY_ORDER_VERSION = 1;
  const DEFAULT_SHOWN_CITIES = ['seattle', 'new-york', 'los-angeles', 'tokyo', 'berlin', 'london', 'paris'];
  const cityPrefs = (() => {
    let data = null;    // { v, order: [key], shown: [key] } once saved
    try { const o = JSON.parse(store.get('cityOrder', 'null')); if (o && o.v === CITY_ORDER_VERSION && Array.isArray(o.order) && Array.isArray(o.shown)) data = o; } catch (e) { /* defaults */ }
    const keys = () => DATA.cities.map(c => c.key);
    const api = {
      isPersonal: () => !!data,
      // Every city, in the order in force: the saved one (unknown keys dropped,
      // new cities appended in build order), else the build's.
      order() {
        const all = keys();
        if (!data) return all.slice();
        const known = new Set(all);
        const out = data.order.filter(k => known.has(k));
        const seen = new Set(out);
        all.forEach(k => { if (!seen.has(k)) out.push(k); });
        return out;
      },
      shown() {
        const s = new Set(data ? data.shown : DEFAULT_SHOWN_CITIES);
        return new Set(keys().filter(k => s.has(k)));
      },
      isShown: key => api.shown().has(key),
      // The cities the sheet lists outside Edit, as city records.
      visible: () => { const s = api.shown(); return api.order().filter(k => s.has(k)).map(k => cityByKey[k]); },
      save(order, shown) { data = { v: CITY_ORDER_VERSION, order: order.slice(), shown: [...shown] }; store.set('cityOrder', JSON.stringify(data)); },
      reset() { data = null; try { localStorage.removeItem('cityOrder'); } catch (e) { /* private mode */ } },
    };
    return api;
  })();

  const persistSaved = () => store.set('savedShowIDs', JSON.stringify([...state.saved]));
  const persistFavorites = () => store.set('favoriteVenueIDs', JSON.stringify([...state.favorites]));
  const persistToSee = () => store.set('seeVenueIDs', JSON.stringify([...state.toSee]));
  const persistCity = () => store.set('selectedCityKey', state.cityKey);
  const cityByKey = Object.fromEntries(DATA.cities.map(c => [c.key, c]));

  // ---------------- data helpers ----------------
  const showsByCity = {};
  DATA.shows.forEach(s => { (showsByCity[s.city] = showsByCity[s.city] || []).push(s); });

  const city = () => cityByKey[state.cityKey];
  const cityShows = () => showsByCity[state.cityKey] || [];
  const showId = s => s.city + '/' + s.slug;
  // Shows embed their own venue copy; this key identifies "the same venue"
  // across shows, so several concurrent shows collapse into one map pin and one
  // venue page. The registry's venueId is that identity whenever a show carries
  // one: shows at a single gallery are geocoded per show and drift by a couple
  // of metres, which a coordinate key splits into two venues (Thinkspace
  // Projects dealt 3 shows and 1). Normalized name + coordinate to ~1 m stays
  // the fallback for a show with no registry id.
  const venueKey = v => {
    const id = v && (v.venueId || v.id);
    if (id) return 'id:' + id;
    const name = (v.name || '').trim().toLowerCase().replace(/\s+/g, ' ');
    const pos = Number(v.lat).toFixed(5) + ',' + Number(v.lng).toFixed(5);
    return name ? name + '@' + pos : pos;
  };
  const venueShows = v => { const k = venueKey(v); return cityShows().filter(s => venueKey(s.venue) === k); };
  // Venue records live in DATA.venues, keyed by city then venueId (registry ids
  // repeat across cities): every vouched-for venue of the city, with or
  // without a show (build.py), carrying what a venue page and a map dot need.
  // A show's embedded venue copy carries the same fields as a fallback for a
  // venue the registry lacks.
  const VENUES = DATA.venues || {};
  const cityVenueMap = () => VENUES[state.cityKey] || {};
  const venueId = v => (v && (v.venueId || v.id)) || null;
  const venueRecord = v => { const id = venueId(v); return (id && cityVenueMap()[id]) || null; };
  const venueAbout = v => { const r = venueRecord(v); return (r && r.about) || (v && v.about) || null; };
  // The registry's own rank (rank_venues.py, city-wide); null when unranked.
  const appRank = v => { const r = venueRecord(v); const n = r && r.rank != null ? r.rank : (v && v.rank); return n == null ? null : +n; };
  // The rank the app uses everywhere: the person's saved order for this city
  // when there is one (see `ranking` below), else the registry rank.
  const venueRank = v => ranking.rankOf(venueId(v), v);
  // Every venue of the current city the payload knows, shows or not.
  const cityVenues = () => Object.values(cityVenueMap());
  // A show's embedded venue with the registry record filling whatever the copy lacks.
  const fullVenue = v => {
    const r = venueRecord(v);
    if (!r) return v;
    const out = { ...v };
    Object.entries(r).forEach(([k, val]) => { if (out[k] == null) out[k] = val; });
    return out;
  };
  const KIND_LABEL = { museum: 'Museum', nonprofit: 'Nonprofit', project_space: 'Project space', university: 'University', other: 'Other' };
  const displayName = s => s.artist || s.title;
  const listLine = v => v.name;
  const fullAddress = v => v.addressDetail ? v.address + ', ' + v.addressDetail : v.address;

  function parseDate(str) {
    if (!str) return null;
    const [y, m, d] = str.split('-').map(Number);
    return new Date(y, m - 1, d);
  }
  const fmtLong = d => d.toLocaleDateString('en-US',
    { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
  const fmtShort = d => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  function dateLine(s, fmt) {
    const f = fmt || fmtLong;
    const start = parseDate(s.startDate), end = parseDate(s.endDate);
    if (!end) return '';
    if (start && start > new Date()) return 'Opens ' + f(start);
    return 'Through ' + f(end);
  }

  const DAY = 86400e3;
  const savedShows = () => cityShows().filter(s => state.saved.has(showId(s)));
  // Reception lines are free text from the gallery's page ("Saturday, July 18,
  // 6-9pm", "Thursday, September 24, 2026, 7:00pm - 9:00pm"). Take the first
  // "Month day[, year]"; a missing year is the show's opening year, rolled
  // forward when that would land well before the opening.
  const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
  const RECEPTION_RE = /\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?/i;
  function receptionDate(s) {
    if (!s.reception) return null;
    const m = RECEPTION_RE.exec(s.reception);
    if (!m) return null;
    const month = MONTHS.findIndex(name => name.startsWith(m[1].slice(0, 3).toLowerCase()));
    const day = Number(m[2]);
    if (month < 0 || day < 1 || day > 31) return null;
    const start = parseDate(s.startDate);
    let year = m[3] ? Number(m[3]) : (start ? start.getFullYear() : new Date().getFullYear());
    let d = new Date(year, month, day);
    if (!m[3] && start && start - d > 60 * DAY) d = new Date(year + 1, month, day);
    return d;
  }
  const startOfToday = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
  const hasUpcomingReception = s => { const d = receptionDate(s); return !!d && d >= startOfToday(); };
  // "Active shows": running today. Dates are nullable in the registry, and a
  // missing one is no reason to hide a show, so an open-ended run counts.
  const isActiveShow = s => {
    const today = startOfToday();
    const start = parseDate(s.startDate), end = parseDate(s.endDate);
    return (!start || start <= today) && (!end || end >= today);
  };
  const isOpeningThisWeek = s => {
    const d = parseDate(s.startDate); return !!d && Math.abs(d - Date.now()) <= 7 * DAY;
  };
  const isClosingThisWeek = s => {
    const d = parseDate(s.endDate); if (!d) return false;
    const diff = d - Date.now(); return diff >= 0 && diff <= 7 * DAY;
  };
  function haversine(lat1, lng1, lat2, lng2) {
    const r = x => x * Math.PI / 180, R = 6371;
    const a = Math.sin(r(lat2 - lat1) / 2) ** 2 +
      Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lng2 - lng1) / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const directionsUrl = v => isIOS
    ? `https://maps.apple.com/?daddr=${v.lat},${v.lng}&dirflg=w`
    : `https://www.google.com/maps/dir/?api=1&destination=${v.lat},${v.lng}&travelmode=walking`;

  // ---------------- DOM helpers ----------------
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined) node.setAttribute(k, v);
    }
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return node;
  }
  const icon = name => { const s = el('span'); s.innerHTML = ICONS[name]; return s.firstChild; };

  // ---------------- bookmarks ----------------
  // The bookmark is the one-tap save (My Shows); a toast offers the second tap
  // into a list (L4). quiet: no toast (the add-to-list sheet toggles it itself).
  function toggleSaved(id, quiet) {
    const nowSaved = !state.saved.has(id);
    if (nowSaved) state.saved.add(id); else state.saved.delete(id);
    persistSaved();
    refreshBookmarkUI();
    if (state.filter.list === 'saved') refreshAll();   // the row set itself changes
    else { MapTab.applyFilter(); refreshLibrary(); }
    const s = showById(id);
    if (nowSaved && !quiet && s) toast('Saved to My Shows', 'Add to list…', () => addToListSheet(s));
  }
  // Re-render the Lists tab's pages (library, list detail) in place.
  function refreshLibrary() {
    [...pagesRoot.list.children].forEach(p => { if (p.refresh) p.refresh(); });
  }
  let toastEl = null, toastTimer = null;
  function toast(text, actionLabel, onAction) {
    if (toastEl) { toastEl.remove(); clearTimeout(toastTimer); }
    const t = el('div', { class: 'toast' }, icon('bookmarkFill'), el('span', null, text),
      actionLabel ? el('button', { onclick: () => { hide(); onAction && onAction(); } }, actionLabel) : null);
    document.getElementById('app').appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    const hide = () => { t.classList.remove('show'); setTimeout(() => t.remove(), 220); if (toastEl === t) toastEl = null; };
    toastEl = t;
    toastTimer = setTimeout(hide, 3800);
  }
  function refreshBookmarkUI() {
    document.querySelectorAll('[data-bm]').forEach(btn => {
      const saved = state.saved.has(btn.dataset.bm);
      btn.classList.toggle('saved', saved);
      btn.innerHTML = saved ? ICONS.bookmarkFill : ICONS.bookmark;
    });
    document.querySelectorAll('[data-save-capsule]').forEach(btn => {
      const saved = state.saved.has(btn.dataset.saveCapsule);
      btn.querySelector('span').textContent = saved ? 'Added to My Shows' : 'Add to My Shows';
    });
  }
  const bookmarkBtn = s => el('button', {
    class: 'bookmark-btn' + (state.saved.has(showId(s)) ? ' saved' : ''),
    'data-bm': showId(s),
    'aria-label': 'Save show',
    html: state.saved.has(showId(s)) ? ICONS.bookmarkFill : ICONS.bookmark,
    onclick: e => { e.stopPropagation(); toggleSaved(showId(s)); },
  });

  // ---------------- lists ----------------
  // User lists live in localStorage['lists'] (versioned like `filter`); My
  // Shows stays savedShowIDs and is presented as a list, not stored twice.
  // Drafts are answers from Discover that are not saved yet; they live in
  // sessionStorage so a draft can still be the filter/map context.
  const LISTS_VERSION = 1;
  const lists = (() => {
    let data = { v: LISTS_VERSION, lists: [] };
    try { const o = JSON.parse(store.get('lists', '{}')); if (o && o.v === LISTS_VERSION && Array.isArray(o.lists)) data = o; } catch (e) { /* defaults */ }
    let drafts = [];
    try { drafts = JSON.parse(sessionStorage.getItem('discover.drafts') || '[]') || []; } catch (e) { drafts = []; }
    const subs = new Set();
    const notify = () => subs.forEach(fn => { try { fn(); } catch (e) { /* a listener failed */ } });
    const write = () => { store.set('lists', JSON.stringify(data)); notify(); };
    const writeDrafts = () => { try { sessionStorage.setItem('discover.drafts', JSON.stringify(drafts.slice(-12))); } catch (e) { /* private mode */ } };
    const uid = () => 'l-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    const own = id => data.lists.find(l => l.id === id) || null;
    const api = {
      get: id => own(id) || drafts.find(l => l.id === id) || null,
      all: () => data.lists.filter(l => l.city === state.cityKey),
      subscribe: fn => { subs.add(fn); return () => subs.delete(fn); },
      create(name, entries, extra) {
        const x = extra || {}; const now = Date.now();
        const l = { id: uid(), name: (name || 'New list').trim().slice(0, 80) || 'New list', desc: (x.desc || '').slice(0, 200), kind: x.kind || 'user', city: state.cityKey,
          createdAt: now, updatedAt: now, cover: x.cover || null, entries: (entries || []).map(e => ({ id: e.id, note: e.note || null })), source: x.source || null };
        data.lists.unshift(l); write(); return l;
      },
      update(id, patch) { const l = own(id); if (!l) return null; Object.assign(l, patch, { updatedAt: Date.now() }); write(); return l; },
      remove(id) { data.lists = data.lists.filter(l => l.id !== id); write(); },
      has: (id, sid) => { const l = api.get(id); return !!l && l.entries.some(e => e.id === sid); },
      addEntry(id, entry) { const l = own(id); if (!l || l.entries.some(e => e.id === entry.id)) return; l.entries.push({ id: entry.id, note: entry.note || null }); l.updatedAt = Date.now(); write(); },
      removeEntry(id, sid) { const l = own(id); if (!l) return; l.entries = l.entries.filter(e => e.id !== sid); l.updatedAt = Date.now(); write(); },
      toggleEntry(id, sid) { if (api.has(id, sid)) api.removeEntry(id, sid); else api.addEntry(id, { id: sid }); },
      // A copy of a curated list or a Discover draft into the user's lists.
      copyFrom(l, extra) {
        const x = extra || {};
        return api.create(l.name, l.entries, { desc: l.desc, kind: l.kind === 'route' ? 'route' : (x.kind || 'user'), source: x.source || null, cover: l.cover || null });
      },
      draft(l) { const d = { ...l, id: 'draft-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), city: state.cityKey, draft: true }; drafts.push(d); writeDrafts(); return d; },
      markDraftSaved(id, savedAs) { const d = drafts.find(l => l.id === id); if (d) { d.savedAs = savedAs; writeDrafts(); } },
      isDraft: id => /^draft-/.test(id || ''),
    };
    return api;
  })();
  const showById = id => cityShows().find(s => showId(s) === id) || DATA.shows.find(s => showId(s) === id) || null;
  // Entries -> { show, note } in list order; ids the payload no longer has drop out.
  const listShows = l => l.entries.map(e => { const s = showById(e.id); return s ? { show: s, note: e.note || null } : null; }).filter(Boolean);
  const listRunning = l => listShows(l).filter(x => isActiveShow(x.show));
  // A list hides once none of its shows is still running; an empty list of
  // the user's own stays visible so it can be filled from Edit.
  const listVisible = l => l.entries.length ? listRunning(l).length > 0 : l.kind === 'user';
  const savedAsList = () => ({ id: 'saved', name: 'My Shows', kind: 'saved', desc: '', city: state.cityKey, cover: null,
    entries: [...state.saved].filter(id => id.startsWith(state.cityKey + '/')).map(id => ({ id, note: null })) });
  const venueCount = xs => new Set(xs.map(x => venueKey(x.show.venue))).size;
  const plural = (n, one, many) => `${n} ${n === 1 ? one : (many || one + 's')}`;
  const listMeta = xs => `${plural(xs.length, 'show')} · ${plural(venueCount(xs), 'venue')}`;
  const listBy = l => l.curated ? 'Gallery Browser' : (l.draft || l.kind === 'answer' || (l.source && l.source.query)) ? 'Discover' : 'You';
  const firstSentence = t => { const m = /^(.+?[.!?])(\s|$)/.exec((t || '').trim()); return m ? m[1] : (t || '').slice(0, 140); };
  const NOT_OPENING = /\b(talk|conversation|closing|panel|screening|walkthrough|walk-through|tour|brunch)\b/i;

  // ---------------- gallery ranking ----------------
  // The registry rank (rank_venues.py, city-wide) is the app's only ranking
  // signal: it orders the feed and the list, colours and sizes the map dots and
  // sets the tiers (top 20 / top 50). Shows carry no rank of their own. A person
  // can save their own order of the city's galleries (Settings → Gallery
  // ranking); it then replaces the app's rank everywhere. Stored per city in
  // localStorage['galleryOrder'] (versioned).
  const RANKING_VERSION = 1;
  const ranking = (() => {
    let data = { v: RANKING_VERSION, cities: {} };
    try { const o = JSON.parse(store.get('galleryOrder', '{}')); if (o && o.v === RANKING_VERSION && o.cities) data = o; } catch (e) { /* defaults */ }
    const maps = new Map();     // city -> Map(venueId -> rank) under the saved order; cleared on a write
    const write = () => { store.set('galleryOrder', JSON.stringify(data)); maps.clear(); };
    const byApp = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9) || (a.name || '').localeCompare(b.name || '');
    const venuesOf = city => Object.values(VENUES[city] || {});
    const api = {
      // The city's venues in the app's own order: registry rank, unranked last, A–Z.
      appOrder: city => venuesOf(city).sort(byApp),
      personal: city => (data.cities[city] && Array.isArray(data.cities[city].order) && data.cities[city].order) || null,
      savedAt: city => (data.cities[city] && data.cities[city].updatedAt) || null,
      isPersonal: city => !!api.personal(city),
      // The order in force: the saved one (venues the payload no longer has drop
      // out; new ones join at the end in app order), else the app's.
      order(city) {
        const app = api.appOrder(city);
        const mine = api.personal(city);
        if (!mine) return app;
        const byId = new Map(app.map(v => [v.id, v]));
        const out = mine.map(id => byId.get(id)).filter(Boolean);
        const seen = new Set(out.map(v => v.id));
        app.forEach(v => { if (!seen.has(v.id)) out.push(v); });
        return out;
      },
      rankMap(city) {
        let m = maps.get(city);
        if (!m) { m = new Map(); if (api.personal(city)) api.order(city).forEach((v, i) => m.set(v.id, i + 1)); maps.set(city, m); }
        return m;
      },
      // Effective rank of a venue of the current city: its place in the saved
      // order, else the registry rank; null when unranked (or unknown to a saved order).
      rankOf(id, v) {
        if (!id) return null;
        const m = api.rankMap(state.cityKey);
        if (m.has(id)) return m.get(id);
        return api.personal(state.cityKey) ? null : appRank(v);
      },
      save(city, ids) { data.cities[city] = { order: ids.slice(), updatedAt: Date.now() }; write(); },
      reset(city) { delete data.cities[city]; write(); },
    };
    return api;
  })();

  // ---------------- favorite galleries ----------------
  // A heart on a gallery (its page, the ranking page) follows it; "Favorite
  // galleries" is then a default list — their shows — and a filter context.
  // Stored as "<city>/<venueId>" in localStorage['favoriteVenueIDs'].
  const favKey = v => state.cityKey + '/' + venueId(v);
  // "See": a gallery marked on the Map as one to visit; its dot turns red.
  const isToSee = v => !!venueId(v) && state.toSee.has(favKey(v));
  function toggleToSee(v) {
    if (!venueId(v)) return;
    const k = favKey(v);
    if (state.toSee.has(k)) state.toSee.delete(k); else state.toSee.add(k);
    persistToSee();
    MapTab.applyFilter();
  }
  const isFavorite = v => !!venueId(v) && state.favorites.has(favKey(v));
  const favoriteVenues = () => cityVenues().filter(isFavorite)
    .sort((a, b) => (venueRank(a) ?? 1e9) - (venueRank(b) ?? 1e9) || (a.name || '').localeCompare(b.name || ''));
  function toggleFavorite(v) {
    if (!venueId(v)) return;
    const k = favKey(v);
    if (state.favorites.has(k)) state.favorites.delete(k); else state.favorites.add(k);
    persistFavorites();
    refreshFavoriteUI();
    if (state.filter.list === 'favorites') refreshAll();   // the context itself changed
    else refreshLibrary();
  }
  function refreshFavoriteUI() {
    document.querySelectorAll('[data-fav]').forEach(btn => {
      const on = state.favorites.has(btn.dataset.fav);
      btn.classList.toggle('on', on);
      btn.innerHTML = on ? ICONS.heartFill : ICONS.heart;
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  const favoriteBtn = (v, cls) => {
    if (!venueId(v)) return el('span');
    const on = isFavorite(v);
    return el('button', {
      class: 'fav-btn' + (cls ? ' ' + cls : '') + (on ? ' on' : ''), 'data-fav': favKey(v),
      'aria-label': 'Favorite gallery', 'aria-pressed': on ? 'true' : 'false',
      html: on ? ICONS.heartFill : ICONS.heart,
      onclick: e => { e.stopPropagation(); toggleFavorite(v); },
    });
  };
  // Shows at the favorite galleries, best-ranked gallery first, as a list.
  const favoritesAsList = () => {
    const ids = new Set(favoriteVenues().map(v => v.id));
    return { id: 'favorites', name: 'Favorite galleries', kind: 'favorites', desc: '', city: state.cityKey, cover: null,
      entries: sortShows(cityShows().filter(s => ids.has(venueId(s.venue))), 'rank').map(s => ({ id: showId(s), note: null })) };
  };

  // Curated lists: Top 20 galleries and Openings this weekend are rules over the
  // data; the rest are authored in content/lists/<city>.json (DATA.lists).
  function weekendWindow() {
    const today = startOfToday(); const dow = today.getDay();
    const plus = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
    if (dow === 6) return [today, plus(today, 1)];
    if (dow === 0) return [today, today];
    const fri = plus(today, ((5 - dow) + 7) % 7);
    return [fri, plus(fri, 2)];
  }
  function curatedLists() {
    const ck = state.cityKey;
    const byRank = (a, b) => (venueRank(a.venue) ?? 1e9) - (venueRank(b.venue) ?? 1e9);
    const top = sortShows(cityShows().filter(s => isActiveShow(s) && galleryTier(s.venue) === 'top'), 'rank');
    const [from, to] = weekendWindow();
    const openings = cityShows().filter(s => {
      const d = receptionDate(s);
      return d && d >= from && d <= to && !(NOT_OPENING.test(s.reception) && !/\bopening\b/i.test(s.reception));
    }).sort((a, b) => receptionDate(a) - receptionDate(b) || byRank(a, b));
    const authored = (DATA.lists && DATA.lists[ck]) || [];
    const mk = (id, name, desc, kind, entries) => ({ id, name, desc, kind, city: ck, curated: true, cover: null, entries });
    const fromAuthored = l => mk(l.id, l.name, l.desc, l.kind, l.entries.map(e => ({ id: ck + '/' + e.slug, note: e.note || null })));
    const walks = authored.filter(l => l.kind === 'route'), rest = authored.filter(l => l.kind !== 'route');
    const out = [
      mk('c-top', 'Top 20 galleries', "What the city's best-ranked galleries have on view.", 'list', top.map(s => ({ id: showId(s), note: null }))),
      ...walks.map(fromAuthored),
      mk('c-openings-weekend', 'Openings this weekend', "Receptions Friday to Sunday, from each venue's own listing.", 'list', openings.map(s => ({ id: showId(s), note: s.reception }))),
      ...rest.map(fromAuthored),
    ];
    return out.filter(listVisible);
  }
  const curatedById = id => curatedLists().find(l => l.id === id) || null;
  const savedCopyOf = curatedId => lists.all().find(l => l.source && l.source.curated === curatedId) || null;
  // Any list id -> the list: 'saved', 'favorites', a user list, a draft, or a curated id.
  const listById = id => !id ? null : id === 'saved' ? savedAsList() : id === 'favorites' ? favoritesAsList() : (lists.get(id) || curatedById(id));

  // ---------------- navigation ----------------
  const pagesRoot = {
    featured: document.getElementById('pages-featured'),
    list: document.getElementById('pages-list'),
    discover: document.getElementById('pages-discover'),
  };
  function push(tab, page) {
    if (pagesRoot[tab].children.length) {
      page.classList.add('page-push');
      // The slide-in is a CSS animation, and animations restart whenever a
      // display:none tab is shown again; drop the class once it has played.
      page.addEventListener('animationend', () => page.classList.remove('page-push'), { once: true });
    }
    pagesRoot[tab].appendChild(page);
  }
  function pop(tab, animate) {
    const root = pagesRoot[tab];
    if (root.children.length <= 1) return;
    const page = root.lastElementChild;
    if (page.dataset.popping) return;
    if (animate === false) { page.remove(); return; }
    page.dataset.popping = '1';
    page.style.transition = 'transform 0.25s cubic-bezier(0.32, 0.72, 0, 1)';
    page.style.transform = 'translateX(100%)';
    setTimeout(() => page.remove(), 260);
  }
  const backBtn = tab => {
    // plain onclick property so sheet contexts can replace the handler
    const b = el('button', { class: 'circle-btn', html: ICONS.chevronLeft, 'aria-label': 'Back' });
    b.onclick = () => pop(tab);
    return b;
  };

  function largeTitleScroll(scrollEl, inlineTitle) {
    scrollEl.addEventListener('scroll', () => {
      inlineTitle.classList.toggle('visible', scrollEl.scrollTop > 48);
    }, { passive: true });
  }

  // ---------------- sheets ----------------
  const sheetRoot = document.getElementById('sheet-root');
  const sheetStack = [];
  function openSheet(contentEl) {
    const backdrop = el('div', { class: 'sheet-backdrop' });
    const sheet = el('div', { class: 'sheet' }, contentEl);
    const entry = { backdrop, sheet };
    sheetStack.push(entry);
    sheetRoot.append(backdrop, sheet);
    requestAnimationFrame(() => { backdrop.classList.add('open'); sheet.classList.add('open'); });
    backdrop.addEventListener('click', () => closeSheet(entry));
    wireSheetDrag(entry);
    return () => closeSheet(entry);
  }

  // iOS-style drag-down to dismiss (only when the sheet's scroller is at the top)
  function wireSheetDrag(entry) {
    const { sheet } = entry;
    let start = null, dragging = false;
    const scrollerFor = node => {
      while (node && node !== sheet) {
        if (node.classList && node.classList.contains('page-scroll')) return node;
        node = node.parentElement;
      }
      return null;
    };
    sheet.addEventListener('touchstart', e => {
      if (e.touches.length !== 1) { start = null; return; }
      const sc = scrollerFor(e.target);
      if (sc && sc.scrollTop > 2) { start = null; return; }
      start = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      dragging = false;
    }, { passive: true });
    sheet.addEventListener('touchmove', e => {
      if (!start) return;
      const dy = e.touches[0].clientY - start.y;
      const dx = e.touches[0].clientX - start.x;
      if (!dragging) {
        if (dy > 10 && dy > Math.abs(dx) * 1.2) { dragging = true; sheet.style.transition = 'none'; }
        else if (Math.abs(dx) > 14 || dy < -14) { start = null; return; }
      }
      if (dragging && dy > 0) {
        e.preventDefault();
        sheet.style.transform = `translateY(${dy}px)`;
      }
    }, { passive: false });
    const end = e => {
      if (!dragging) { start = null; return; }
      const dy = (e.changedTouches ? e.changedTouches[0].clientY : 0) - start.y;
      sheet.style.transition = '';
      sheet.style.transform = '';
      if (dy > 140) closeSheet(entry);
      start = null; dragging = false;
    };
    sheet.addEventListener('touchend', end);
    sheet.addEventListener('touchcancel', end);
  }

  // photo pinch/pan gestures must win over the edge-swipe-back recognizer
  let activePinchSessions = 0;
  const viewerOpen = () => document.getElementById('viewer-root').children.length > 0;

  // iOS-style edge-swipe back for pushed pages
  function wireEdgeSwipeBack() {
    const app = document.getElementById('app');
    let track = null;
    app.addEventListener('touchstart', e => {
      if (sheetStack.length || e.touches.length !== 1 || state.tab === 'map' ||
          viewerOpen() || activePinchSessions) return;
      const root = pagesRoot[state.tab];
      if (!root || root.children.length < 2) return;
      const t = e.touches[0];
      if (t.clientX > 28) return;
      track = { page: root.lastElementChild, startX: t.clientX, startY: t.clientY,
                width: root.clientWidth || 393, active: false };
    }, { passive: true });
    app.addEventListener('touchmove', e => {
      if (!track) return;
      if (viewerOpen() || activePinchSessions || e.touches.length !== 1) {
        if (track.active) {                    // don't leave the page frozen mid-drag
          const p = track.page;
          p.style.transition = 'transform 0.22s';
          p.style.transform = '';
          setTimeout(() => { p.style.transition = ''; p.style.boxShadow = ''; }, 240);
        }
        track = null;
        return;
      }
      const t = e.touches[0];
      const dx = t.clientX - track.startX, dy = t.clientY - track.startY;
      if (!track.active) {
        if (dx > 12 && dx > Math.abs(dy) * 1.2) { track.active = true; track.page.style.transition = 'none'; }
        else if (Math.abs(dy) > 18) { track = null; return; }
      }
      if (track && track.active && dx > 0) {
        e.preventDefault();
        track.page.style.transform = `translateX(${dx}px)`;
        track.page.style.boxShadow = '-12px 0 30px rgba(0,0,0,0.5)';
      }
    }, { passive: false });
    const finish = e => {
      if (!track) return;
      const { page, startX, width, active } = track;
      if (active) {
        const dx = (e.changedTouches ? e.changedTouches[0].clientX : 0) - startX;
        if (dx > width * 0.33) {
          page.dataset.popping = '1';
          page.style.transition = 'transform 0.2s ease-out';
          page.style.transform = `translateX(${width}px)`;
          setTimeout(() => page.remove(), 210);
        } else {
          page.style.transition = 'transform 0.22s';
          page.style.transform = '';
          setTimeout(() => { page.style.transition = ''; page.style.boxShadow = ''; }, 240);
        }
      }
      track = null;
    };
    app.addEventListener('touchend', finish);
    app.addEventListener('touchcancel', finish);
  }
  wireEdgeSwipeBack();
  function closeSheet(entry) {
    const e = entry || sheetStack[sheetStack.length - 1];
    if (!e) return;
    sheetStack.splice(sheetStack.indexOf(e), 1);
    e.backdrop.classList.remove('open');
    e.sheet.classList.remove('open');
    setTimeout(() => { e.backdrop.remove(); e.sheet.remove(); }, 300);
  }
  function closeAllSheets() { while (sheetStack.length) closeSheet(); }
  const sheetCloseBtn = () => el('button', {
    class: 'sheet-close', html: ICONS.xmark, 'aria-label': 'Close',
    onclick: () => closeSheet(),
  });

  // ---------------- carousel ----------------
  // Image entries are {src, full?}: src is the 1080px proxy every surface loads
  // first; full (present when the source out-resolves the proxy) is the
  // near-4K variant swapped in once someone zooms.
  function upgradeToFull(img, full) {
    if (!full || img.dataset.res === 'full') return;
    img.dataset.res = 'full';                        // claim before the async load
    const hi = new Image();
    hi.decoding = 'async';
    hi.src = full;
    const swap = () => { img.src = full; };
    if (hi.decode) hi.decode().then(swap, () => { delete img.dataset.res; });
    else hi.onload = swap;
  }

  function makeCarousel(images, opts) {
    const { height, aspect, dots = 'top-left', onTap, expand } = opts;
    const car = el('div', { class: 'carousel', style: aspect ? `aspect-ratio:${aspect}` : `height:${height}px` });
    const track = el('div', { class: 'carousel-track' });
    images.forEach((entry, i) => {
      const img = el('img', {
        src: entry.src, 'data-full': entry.full || null,
        loading: 'lazy', decoding: 'async', alt: '',
      });
      const slide = el('div', { class: 'carousel-slide' }, img);
      if (onTap) slide.addEventListener('click', () => onTap(i));
      track.appendChild(slide);
    });
    car.appendChild(track);

    let index = 0;
    let dotEls = [];
    if (images.length > 1) {
      const dotsEl = el('div', { class: 'carousel-dots ' + ({ bottom: 'bottom-center', 'above-footer': 'bottom-center above-footer' }[dots] || 'top-left') });
      dotEls = images.map((_, i) => el('i', i === 0 ? { class: 'on' } : null));
      dotEls.forEach(d => dotsEl.appendChild(d));
      car.appendChild(dotsEl);

      const prev = el('button', { class: 'carousel-arrow prev', html: ICONS.chevronLeft });
      const next = el('button', { class: 'carousel-arrow next', html: ICONS.chevronRight });
      prev.addEventListener('click', e => { e.stopPropagation(); track.scrollBy({ left: -track.clientWidth, behavior: 'smooth' }); });
      next.addEventListener('click', e => { e.stopPropagation(); track.scrollBy({ left: track.clientWidth, behavior: 'smooth' }); });
      car.append(prev, next);

      const syncArrows = () => {
        prev.classList.toggle('hidden', index === 0);
        next.classList.toggle('hidden', index === images.length - 1);
      };
      syncArrows();
      track.addEventListener('scroll', () => {
        const i = Math.round(track.scrollLeft / track.clientWidth);
        if (i !== index && i >= 0 && i < images.length) {
          index = i;
          dotEls.forEach((d, j) => d.classList.toggle('on', j === i));
          syncArrows();
        }
      }, { passive: true });
    }
    if (expand) {
      car.appendChild(el('button', {
        class: 'circle-btn expand-btn', html: ICONS.expand, 'aria-label': 'View full screen',
        onclick: e => { e.stopPropagation(); openViewer(images, index); },
      }));
    }
    wireCarouselPinch(track);
    return car;
  }

  // Instagram-style pinch on in-page photos: the image lifts into a fixed
  // overlay clone (so it can escape the card's rounded-corner clipping) and
  // follows the fingers — focal zoom while two are down, 1:1 pan with the
  // survivor when one lifts, seamless re-pinch when it returns. It springs
  // back only when the last finger leaves.
  function wireCarouselPinch(track) {
    const pts = new Map();  // touches that began on this track; active pair = first two
    let sess = null;        // {img, clone, backdrop, cx, cy, scale, tx, ty, base}
    let pinchEndAt = 0;

    const rebaselinePinch = () => {
      const [a, b] = [...pts.values()];
      const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      sess.base = {
        kind: 'pinch',
        dist0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        s0: sess.scale,
        fx: (mx - sess.cx - sess.tx) / sess.scale,
        fy: (my - sess.cy - sess.ty) / sess.scale,
      };
    };
    const rebaselinePan = () => {
      const p = [...pts.values()][0];
      sess.base = { kind: 'pan', x0: p.x, y0: p.y, tx0: sess.tx, ty0: sess.ty };
    };
    const render = () => {
      sess.clone.style.transform = `translate3d(${sess.tx}px, ${sess.ty}px, 0) scale(${sess.scale})`;
      sess.backdrop.style.opacity = String(Math.min(0.7, (sess.scale - 1) * 0.9));
    };
    const teardown = () => {
      const { img, clone, backdrop } = sess;
      sess = null;
      activePinchSessions--;
      pinchEndAt = Date.now();
      clone.style.transition = 'transform 0.25s cubic-bezier(0.22, 0.9, 0.3, 1)';
      backdrop.style.transition = 'opacity 0.25s';
      clone.style.transform = 'none';
      backdrop.style.opacity = '0';
      setTimeout(() => {
        clone.remove(); backdrop.remove();
        // a rapid re-pinch may own this img again before the timer fires
        if (!(sess && sess.img === img)) img.style.visibility = '';
      }, 260);
    };

    track.addEventListener('touchstart', e => {
      for (const t of e.changedTouches) pts.set(t.identifier, { x: t.clientX, y: t.clientY });
      if (sess) {
        e.preventDefault();
        if (sess.base.kind === 'pan' && pts.size >= 2) rebaselinePinch();
        return;
      }
      // a lone first finger must stay native: it may be a scroll, a swipe, or a tap
      if (pts.size !== 2) return;
      const slide = e.target.closest ? e.target.closest('.carousel-slide') : null;
      const img = slide && slide.querySelector('img');
      if (!img) return;
      e.preventDefault();                            // keep the browser out of it
      const rect = img.getBoundingClientRect();
      const card = track.closest('.card');
      const clone = el('img', { class: 'pinch-img', src: img.currentSrc || img.src });
      clone.style.left = rect.left + 'px';
      clone.style.top = rect.top + 'px';
      clone.style.width = rect.width + 'px';
      clone.style.height = rect.height + 'px';
      if (card) clone.style.borderRadius = getComputedStyle(card).borderRadius;
      const backdrop = el('div', { class: 'pinch-backdrop' });
      document.body.append(backdrop, clone);
      img.style.visibility = 'hidden';
      sess = {
        img, clone, backdrop,
        cx: rect.left + rect.width / 2, cy: rect.top + rect.height / 2,
        scale: 1, tx: 0, ty: 0, base: null,
      };
      activePinchSessions++;
      rebaselinePinch();
      upgradeToFull(clone, img.dataset.full);        // sharpen mid-pinch
    }, { passive: false });

    track.addEventListener('touchmove', e => {
      for (const t of e.changedTouches) {
        if (pts.has(t.identifier)) pts.set(t.identifier, { x: t.clientX, y: t.clientY });
      }
      if (!sess) return;
      e.preventDefault();
      if (sess.base.kind === 'pinch' && pts.size >= 2) {
        const [a, b] = [...pts.values()];
        const { dist0, s0, fx, fy } = sess.base;
        sess.scale = Math.max(1, Math.min(4, s0 * Math.hypot(a.x - b.x, a.y - b.y) / dist0));
        sess.tx = (a.x + b.x) / 2 - sess.cx - fx * sess.scale;
        sess.ty = (a.y + b.y) / 2 - sess.cy - fy * sess.scale;
      } else if (sess.base.kind === 'pan' && pts.size >= 1) {
        const p = [...pts.values()][0];
        sess.tx = sess.base.tx0 + (p.x - sess.base.x0);
        sess.ty = sess.base.ty0 + (p.y - sess.base.y0);
      }
      render();
    }, { passive: false });

    const end = e => {
      if (!sess) {
        for (const t of e.changedTouches) pts.delete(t.identifier);
        return;
      }
      if (e.type === 'touchcancel') {                // the system took the gesture
        pts.clear();
        teardown();
        return;
      }
      for (const t of e.changedTouches) pts.delete(t.identifier);
      e.preventDefault();                            // a pan can end off-track, beyond the
                                                     // reach of the click swallow below
      if (pts.size >= 2) rebaselinePinch();          // active pair may have changed
      else if (pts.size === 1) rebaselinePan();      // keep the zoom; survivor pans
      else teardown();
    };
    track.addEventListener('touchend', end, { passive: false });
    track.addEventListener('touchcancel', end, { passive: false });

    // swallow the tap a pinch can synthesize so it doesn't open the detail page
    track.addEventListener('click', e => {
      if (Date.now() - pinchEndAt < 400) { e.stopPropagation(); e.preventDefault(); }
    }, true);
  }

  // ---------------- full-screen viewer ----------------
  // Fully JS-driven gestures (touch-action: none on the track) so pinch/pan never
  // race the browser's native scrolling or page zoom — the old scroll-snap +
  // touch-event hybrid lost that race on iOS Safari.
  function openViewer(images, startIndex) {
    const viewer = el('div', { class: 'viewer' });
    const track = el('div', { class: 'viewer-track' });
    const slides = images.map(entry => {
      const img = el('img', { src: entry.src, decoding: 'async', alt: '' });
      const slide = el('div', { class: 'viewer-slide' }, img);
      track.appendChild(slide);
      return { slide, img, full: entry.full, scale: 1, tx: 0, ty: 0 };
    });
    // full-screen earns full-res: upgrade the current slide and its neighbors
    const warm = () => {
      for (const s of [slides[index], slides[index - 1], slides[index + 1]]) {
        if (s) upgradeToFull(s.img, s.full);
      }
    };
    const counter = el('div', { class: 'viewer-counter' }, `${startIndex + 1} / ${images.length}`);
    const closeBtn = el('button', { class: 'circle-btn viewer-close', html: ICONS.xmark, 'aria-label': 'Close' });
    viewer.append(track, closeBtn, counter);
    document.getElementById('viewer-root').appendChild(viewer);

    let index = startIndex;
    const width = () => viewer.clientWidth || 393;
    const setTrack = (x, animate) => {
      track.style.transition = animate ? 'transform 0.3s cubic-bezier(0.22, 0.9, 0.3, 1)' : 'none';
      track.style.transform = `translate3d(${x}px, 0, 0)`;
    };
    const apply = (s, animate) => {
      s.img.style.transition = animate ? 'transform 0.25s cubic-bezier(0.22, 0.9, 0.3, 1)' : 'none';
      s.img.style.transform = `translate3d(${s.tx}px, ${s.ty}px, 0) scale(${s.scale})`;
      if (s === slides[index]) track.classList.toggle('is-zoomed', s.scale > 1.01);
    };
    const clampPan = s => {
      const maxX = Math.max(0, (s.scale - 1) * s.img.clientWidth / 2);
      const maxY = Math.max(0, (s.scale - 1) * s.img.clientHeight / 2);
      s.tx = Math.max(-maxX, Math.min(maxX, s.tx));
      s.ty = Math.max(-maxY, Math.min(maxY, s.ty));
    };
    const slideCenter = () => {
      const r = slides[index].slide.getBoundingClientRect();
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
    };

    function goTo(i, animate) {
      i = Math.max(0, Math.min(images.length - 1, i));
      if (i !== index) {
        const prev = slides[index];
        prev.scale = 1; prev.tx = 0; prev.ty = 0; apply(prev, false);
        index = i;
        counter.textContent = `${i + 1} / ${images.length}`;
      }
      setTrack(-index * width(), animate);
      warm();
    }
    goTo(startIndex, false);

    function toggleZoom(px, py) {
      const s = slides[index];
      if (s.scale > 1.01) { s.scale = 1; s.tx = 0; s.ty = 0; }
      else {
        const c = slideCenter();
        s.scale = 2.5;
        s.tx = (px - c.x) * (1 - s.scale);
        s.ty = (py - c.y) * (1 - s.scale);
        clampPan(s);
      }
      apply(s, true);
    }

    function close() {
      viewer.remove();
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
    }
    closeBtn.addEventListener('click', close);
    function onKey(e) {
      if (e.key === 'Escape') close();
      if (e.key === 'ArrowRight') goTo(index + 1, true);
      if (e.key === 'ArrowLeft') goTo(index - 1, true);
    }
    document.addEventListener('keydown', onKey);
    const onResize = () => setTrack(-index * width(), false);
    window.addEventListener('resize', onResize);

    // gestures: pinch (focal zoom + two-finger pan), pan-when-zoomed,
    // swipe between slides, pull-down dismiss, double-tap zoom, pinch-to-close
    const pts = new Map();
    let mode = null;      // 'pinch' | 'pan' | 'swipe' | 'pull' | 'tap' | null
    let pinch = null;     // {dist0, s0, vfx, vfy} — vf = focal point in image coords
    let origin = null;    // single-finger start {x, y, tx, ty, t}
    let lastTap = { t: 0, x: 0, y: 0 };
    let pinched = false;  // current touch sequence included a two-finger pinch

    track.addEventListener('touchstart', e => {
      e.preventDefault();
      const s = slides[index];
      if (pts.size === 0) pinched = false;           // a fresh touch sequence begins
      for (const t of e.changedTouches) pts.set(t.identifier, { x: t.clientX, y: t.clientY });
      if (pts.size >= 2) pinched = true;
      if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        const c = slideCenter();
        pinch = {
          dist0: Math.hypot(a.x - b.x, a.y - b.y),
          s0: s.scale,
          vfx: ((a.x + b.x) / 2 - c.x - s.tx) / s.scale,
          vfy: ((a.y + b.y) / 2 - c.y - s.ty) / s.scale,
        };
        mode = 'pinch';
        setTrack(-index * width(), true);           // abort any half swipe
        viewer.style.transform = ''; viewer.style.opacity = '';
      } else if (pts.size === 1) {
        const t = e.touches[0];
        origin = { x: t.clientX, y: t.clientY, tx: s.tx, ty: s.ty, t: Date.now() };
        mode = s.scale > 1.01 ? 'pan' : 'tap';
      }
    }, { passive: false });

    track.addEventListener('touchmove', e => {
      e.preventDefault();
      const s = slides[index];
      for (const t of e.changedTouches) {
        if (pts.has(t.identifier)) pts.set(t.identifier, { x: t.clientX, y: t.clientY });
      }
      if (mode === 'pinch' && pts.size >= 2) {
        const [a, b] = [...pts.values()];
        const c = slideCenter();
        const sc = Math.max(0.5, Math.min(5, pinch.s0 * Math.hypot(a.x - b.x, a.y - b.y) / pinch.dist0));
        s.scale = sc;
        s.tx = (a.x + b.x) / 2 - c.x - pinch.vfx * sc;
        s.ty = (a.y + b.y) / 2 - c.y - pinch.vfy * sc;
        if (sc > 1) clampPan(s);
        apply(s, false);
        return;
      }
      if (!origin || pts.size !== 1) return;
      const t = e.touches[0];
      const dx = t.clientX - origin.x, dy = t.clientY - origin.y;
      if (mode === 'tap' && (Math.abs(dx) > 8 || Math.abs(dy) > 8)) {
        mode = Math.abs(dx) > Math.abs(dy) ? 'swipe' : (dy > 0 ? 'pull' : 'swipe');
      }
      if (mode === 'pan') {
        s.tx = origin.tx + dx; s.ty = origin.ty + dy;
        clampPan(s); apply(s, false);
      } else if (mode === 'swipe') {
        const resist = (index === 0 && dx > 0) || (index === images.length - 1 && dx < 0);
        setTrack(-index * width() + (resist ? dx * 0.3 : dx), false);
      } else if (mode === 'pull') {
        const y = Math.max(0, dy);
        viewer.style.transform = `translateY(${y}px)`;
        viewer.style.opacity = String(Math.max(0.4, 1 - y / 600));
      }
    }, { passive: false });

    track.addEventListener('touchend', e => {
      const s = slides[index];
      for (const t of e.changedTouches) pts.delete(t.identifier);
      if (mode === 'pinch') {
        if (pts.size >= 2) {                        // active pair changed: re-baseline
          const [a, b] = [...pts.values()];
          const c = slideCenter();
          pinch = {
            dist0: Math.hypot(a.x - b.x, a.y - b.y) || 1, s0: s.scale,
            vfx: ((a.x + b.x) / 2 - c.x - s.tx) / s.scale,
            vfy: ((a.y + b.y) / 2 - c.y - s.ty) / s.scale,
          };
          return;
        }
        if (s.scale < 0.75) {                       // pinched shut → dismiss
          viewer.style.transition = 'opacity 0.18s';
          viewer.style.opacity = '0';
          setTimeout(close, 180);
          mode = null; pinch = null; return;
        }
        if (s.scale <= 1.01) { s.scale = 1; s.tx = 0; s.ty = 0; }
        else clampPan(s);
        apply(s, true);
        pinch = null;
        if (pts.size === 1) {                       // hand off to remaining finger
          const p = [...pts.values()][0];
          origin = { x: p.x, y: p.y, tx: s.tx, ty: s.ty, t: Date.now() };
          // 'tap' = undecided: it can still promote to swipe/pull; the
          // `pinched` flag keeps it from ever counting as an actual tap
          mode = s.scale > 1.01 ? 'pan' : 'tap';
        } else mode = null;
        return;
      }
      if (pts.size) return;
      const t = e.changedTouches[0];
      if (mode === 'swipe') {
        const dx = t.clientX - origin.x;
        const dt = Date.now() - origin.t;
        let next = index;
        if (Math.abs(dx) > width() * 0.3 || (Math.abs(dx) > 30 && dt < 250)) next += dx < 0 ? 1 : -1;
        goTo(next, true);
      } else if (mode === 'pull') {
        if (t.clientY - origin.y > 110) { close(); return; }
        viewer.style.transition = 'transform 0.22s, opacity 0.22s';
        viewer.style.transform = ''; viewer.style.opacity = '';
        setTimeout(() => { viewer.style.transition = ''; }, 240);
      } else if (!pinched && (mode === 'tap' || mode === 'pan') && Date.now() - origin.t < 300 &&
                 Math.hypot(t.clientX - origin.x, t.clientY - origin.y) < 10) {
        const now = Date.now();
        if (now - lastTap.t < 300 && Math.hypot(t.clientX - lastTap.x, t.clientY - lastTap.y) < 40) {
          toggleZoom(t.clientX, t.clientY);
          lastTap.t = 0;
        } else lastTap = { t: now, x: t.clientX, y: t.clientY };
      }
      mode = null; origin = null;
    });

    track.addEventListener('touchcancel', () => {
      pts.clear(); pinch = null; origin = null; mode = null; pinched = false;
      const s = slides[index];
      if (s.scale < 1) { s.scale = 1; s.tx = 0; s.ty = 0; }
      apply(s, true);
      setTrack(-index * width(), true);
      viewer.style.transform = ''; viewer.style.opacity = '';
    });

    // desktop: double-click zoom, trackpad pinch (ctrl+wheel), two-finger
    // scroll pan while zoomed, and mouse drag (pan when zoomed, swipe at 1x)
    let dragEndAt = 0;
    track.addEventListener('dblclick', e => {
      if (Date.now() - dragEndAt < 400) return;     // a real drag just ended, not a tap-tap
      toggleZoom(e.clientX, e.clientY);
    });
    track.addEventListener('wheel', e => {
      const s = slides[index];
      if (e.ctrlKey) {
        e.preventDefault();
        const c = slideCenter();
        const vx = (e.clientX - c.x - s.tx) / s.scale;
        const vy = (e.clientY - c.y - s.ty) / s.scale;
        s.scale = Math.max(1, Math.min(5, s.scale * Math.exp(-e.deltaY / 100)));
        s.tx = e.clientX - c.x - vx * s.scale;
        s.ty = e.clientY - c.y - vy * s.scale;
        clampPan(s); apply(s, false);
      } else if (s.scale > 1.01) {
        e.preventDefault();
        s.tx -= e.deltaX; s.ty -= e.deltaY;
        clampPan(s); apply(s, false);
      }
    }, { passive: false });

    let mdrag = null;     // mouse drag: {x0, y0, tx0, ty0, t0, moved, panning}
    track.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'mouse' || e.button !== 0) return;
      e.preventDefault();
      const s = slides[index];
      mdrag = { x0: e.clientX, y0: e.clientY, tx0: s.tx, ty0: s.ty,
                t0: Date.now(), moved: false, panning: s.scale > 1.01 };
      track.setPointerCapture(e.pointerId);
    });
    track.addEventListener('pointermove', e => {
      if (!mdrag || e.pointerType !== 'mouse') return;
      const s = slides[index];
      const dx = e.clientX - mdrag.x0, dy = e.clientY - mdrag.y0;
      if (!mdrag.moved && Math.hypot(dx, dy) > 8) mdrag.moved = true;
      if (!mdrag.moved) return;
      if (mdrag.panning) {
        s.tx = mdrag.tx0 + dx; s.ty = mdrag.ty0 + dy;
        clampPan(s); apply(s, false);
      } else {
        const resist = (index === 0 && dx > 0) || (index === images.length - 1 && dx < 0);
        setTrack(-index * width() + (resist ? dx * 0.3 : dx), false);
      }
    });
    track.addEventListener('pointerup', e => {
      if (!mdrag || e.pointerType !== 'mouse') return;
      const d = mdrag;
      mdrag = null;
      if (!d.moved) return;                         // plain click: taps belong to dblclick
      dragEndAt = Date.now();
      if (d.panning) return;
      const dx = e.clientX - d.x0;
      const dt = Date.now() - d.t0;
      let next = index;
      if (Math.abs(dx) > width() * 0.3 || (Math.abs(dx) > 30 && dt < 250)) next += dx < 0 ? 1 : -1;
      goTo(next, true);
    });
    track.addEventListener('pointercancel', e => {
      if (!mdrag || e.pointerType !== 'mouse') return;
      const d = mdrag;
      mdrag = null;
      if (!d.panning && d.moved) goTo(index, true); // settle a half-finished swipe
    });
  }

  // ---------------- screens ----------------
  // A Featured row is one venue, not one show: concurrent shows at the same
  // gallery deal through a single card rather than repeating the venue down the
  // feed. `all` is the whole filtered set, so opening a show still hands the
  // detail page a stepper over every card in the feed.
  function showCard(deck, all) {
    return showDeck(deck, {
      cls: 'feed-card',
      carousel: { aspect: '1 / 1', dots: 'above-footer' },
      sub: s => `${listLine(s.venue)} • ${s.venue.address}`,
      onOpen: s => push(state.tab, showDetailPage(all, all.findIndex(x => showId(x) === showId(s)))),
    });
  }

  // Group the filtered set by venue, keeping feed order: a venue takes the slot
  // of its best-placed show.
  function byVenue(shows) {
    const decks = new Map();
    shows.forEach(s => {
      const k = venueKey(s.venue);
      const d = decks.get(k);
      if (d) d.push(s); else decks.set(k, [s]);
    });
    return [...decks.values()];
  }

  // Featured and List are two renderings of the same filtered set (state.filter);
  // each root page exposes refresh() so refreshAll() can re-render it in place.
  function featuredRoot() {
    const feed = el('div', { class: 'feed' });
    const empty = el('div', { class: 'empty-plain', hidden: '' }, 'No shows match these filters.');
    const ctxSlot = el('div');
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' },
        el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'),
        filterButton()),
      el('div', { class: 'large-title' }, city().displayName),
      ctxSlot, feed, empty);
    const inline = el('div', { class: 'inline-title' }, city().displayName);
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page' }, inline, scroll);
    page.refresh = () => {
      const shows = filteredShows();
      const l = currentList();
      ctxSlot.innerHTML = '';
      if (l) ctxSlot.appendChild(ctxBar(l));
      feed.innerHTML = '';
      byVenue(shows).forEach(deck => feed.appendChild(showCard(deck, shows)));
      feed.hidden = !shows.length;
      empty.hidden = !!shows.length;
    };
    page.refresh();
    return page;
  }

  function showDetailPage(shows, index, opts) {
    const asSheet = opts && opts.asSheet;
    const page = el('div', { class: 'page' });
    const scroll = el('div', { class: 'page-scroll' });
    page.appendChild(scroll);

    function render(i) {
      index = i;
      const s = shows[i];
      scroll.innerHTML = '';
      scroll.scrollTop = 0;

      const car = makeCarousel(s.images, {
        height: 340, dots: 'bottom', expand: true,
        onTap: i => openViewer(s.images, i),
      });
      // opts.subPage: pushed on top of another page inside a sheet, so the
      // leading button reads as "back" rather than "close".
      const leading = asSheet
        ? el('button', {
            class: 'circle-btn',
            html: opts.subPage ? ICONS.chevronLeft : ICONS.xmark,
            'aria-label': opts.subPage ? 'Back' : 'Close',
            onclick: () => (opts.onClose ? opts.onClose() : closeSheet()),
          })
        : backBtn(state.tab);
      const topbar = el('div', { class: 'detail-topbar' }, leading);
      if (shows.length > 1) {
        const up = el('button', { html: ICONS.chevronUp, 'aria-label': 'Previous show' });
        const down = el('button', { html: ICONS.chevronDown, 'aria-label': 'Next show' });
        up.disabled = i === 0; down.disabled = i === shows.length - 1;
        up.addEventListener('click', () => render(i - 1));
        down.addEventListener('click', () => render(i + 1));
        topbar.appendChild(el('div', { class: 'stepper' }, up, down));
      }
      const hero = el('div', { class: 'detail-hero' }, car, topbar);

      const saveBtn = el('button', {
        class: 'capsule-btn detail-save', 'data-save-capsule': showId(s),
        onclick: () => toggleSaved(showId(s)),
      }, el('span', null, state.saved.has(showId(s)) ? 'Added to My Shows' : 'Add to My Shows'));
      const addListBtn = el('button', { class: 'detail-addlist', onclick: () => addToListSheet(s) }, 'Add to list…');

      const venueBlock = el('button', {
        class: 'venue-block',
        onclick: () => pushOrSheet(venuePage(fullVenue(s.venue), asSheet ? { inSheet: true } : undefined)),
      },
        el('div', { class: 'vb-text' },
          el('div', { class: 'vb-name' }, tierStar(galleryTier(s.venue)), listLine(s.venue)),
          el('div', { class: 'vb-line' }, fullAddress(s.venue)),
          ...s.venue.hours.map(h => el('div', { class: 'vb-line' }, h))),
        icon('chevronRight'));

      const body = el('div', { class: 'detail-body' },
        s.artist ? el('div', { class: 'detail-artist' }, s.artist) : null,
        el('div', { class: 'detail-title' }, s.title),
        el('div', { class: 'detail-dates' }, dateLine(s)),
        s.reception ? el('div', { class: 'detail-reception' }, 'Reception: ' + s.reception) : null,
        saveBtn,
        addListBtn,
        venueBlock,
        el('div', { class: 'divider' }),
        el('div', { class: 'detail-desc' },
          ...s.description.split(/\n\s*\n/).map(p => el('p', null, p))));

      scroll.append(hero, body);
    }
    function pushOrSheet(p) {
      if (asSheet) { page.parentElement.appendChild(p); p.dataset.sheetSub = '1'; addSubBack(p); }
      else push(state.tab, p);
    }
    function addSubBack(p) {
      const nav = p.querySelector('.navrow .circle-btn');
      if (nav) nav.onclick = () => p.remove();
    }
    render(index);
    return page;
  }

  // Opens a show detail as a sub-page inside an already-open sheet (the map
  // tab has no page stack of its own, so sheet pages layer instead of pushing).
  function pushShowInSheet(sheetEl, s) {
    const p = showDetailPage([s], 0, { asSheet: true, subPage: true, onClose: () => p.remove() });
    p.dataset.sheetSub = '1';
    sheetEl.appendChild(p);
  }

  // opts.asSheet: this page is the root of a sheet (X closes the sheet).
  // opts.inSheet: pushed inside a sheet (back button is rewired by the caller).
  // In either sheet case, show rows layer a show detail inside the same sheet.
  function venuePage(v, opts) {
    const asSheet = !!(opts && opts.asSheet);
    const inSheet = asSheet || !!(opts && opts.inSheet);
    const mapCard = venueMapCard(v);

    // Gallery photos (registry `photos` via build.py): the hero above the title,
    // the rest as a thumbnail strip; both open the zoom viewer. Entries are
    // {src, full?} like show images; a Google-sourced one names its author.
    const photos = v.photos || [];
    const hero = photos.length
      ? el('div', { class: 'venue-hero', onclick: () => openViewer(photos, 0) },
          el('img', { src: photos[0].src, alt: '', decoding: 'async' }),
          photos[0].attribution
            ? el('div', { class: 'venue-credit' }, 'Photo: ' + photos[0].attribution) : null)
      : null;
    const strip = photos.length > 1
      ? el('div', { class: 'venue-strip' },
          ...photos.slice(1).map((p, i) => el('img', {
            src: p.src, alt: '', loading: 'lazy', decoding: 'async',
            onclick: () => openViewer(photos, i + 1),
          })))
      : null;

    const actions = el('div', { class: 'venue-actions' },
      el('a', { class: 'capsule-btn', href: directionsUrl(v), target: '_blank', rel: 'noopener' },
        icon('walk'), el('span', null, 'Directions to venue')),
      v.website ? el('a', { class: 'capsule-btn', href: v.website, target: '_blank', rel: 'noopener' },
        icon('compass'), el('span', null, 'Open website')) : null,
      v.phone ? el('a', { class: 'capsule-btn', href: 'tel:' + v.phone.replace(/[^\d+]/g, '') },
        icon('phone'), el('span', null, 'Call venue')) : null);

    const page = el('div', { class: 'page venue-page' });
    // A venue with nothing on view (reached from the Map with Active shows off,
    // or from the ranking page) still gets its full page.
    const shows = sortShows(venueShows(v), 'rank');
    const openShow = inSheet ? s => pushShowInSheet(page.parentElement, s) : pushDetailFromRow;
    const showsSection = shows.length
      ? el('div', { class: 'venue-shows' },
          el('div', { class: 'group-header' }, 'Shows'),
          el('div', { class: 'venue-show-list' },
            ...shows.map(s => venueShowCard(s, openShow))))
      : el('div', { class: 'venue-none' }, 'Nothing on view right now.');

    const leading = asSheet
      ? el('button', { class: 'circle-btn', html: ICONS.xmark, 'aria-label': 'Close' })
      : backBtn(state.tab);
    if (asSheet) leading.onclick = () => closeSheet();

    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, leading, favoriteBtn(v)),
      hero,
      el('div', { class: 'venue-body' },
        el('div', { class: 'venue-title' }, v.name),
        rankPill(v),
        showsSection,
        venueAbout(v) ? el('p', { class: 'venue-about' }, venueAbout(v)) : null,
        el('div', { class: 'venue-lines' },
          el('div', null, fullAddress(v)),
          ...(v.hours || []).map(h => el('div', null, h))),
        strip,
        mapCard,
        actions));
    page.appendChild(scroll);
    return page;
  }

  // The Map's tap card: the images of what is on view (the gallery's own
  // photos when nothing is), then only the gallery's name, address, hours and
  // a Directions link; no show text. The name opens the full gallery page.
  function mapVenueCard(v, shows) {
    const on = shows.filter(isActiveShow);
    const images = (on.length ? on : shows).flatMap(s => s.images || []);
    const pics = images.length ? images : (v.photos || []);
    const page = el('div', { class: 'page map-card-sheet' });
    const close = el('button', { class: 'circle-btn', html: ICONS.xmark, 'aria-label': 'Close', onclick: () => closeSheet() });
    const hero = pics.length
      ? el('div', { class: 'detail-hero' },
          makeCarousel(pics, { height: 300, dots: 'bottom', expand: true, onTap: i => openViewer(pics, i) }),
          el('div', { class: 'detail-topbar' }, close))
      : el('div', { class: 'navrow' }, close);
    const openVenue = () => {
      const p = venuePage(v, { inSheet: true });
      p.dataset.sheetSub = '1';
      const back = p.querySelector('.navrow .circle-btn');
      if (back) back.onclick = () => p.remove();
      page.parentElement.appendChild(p);
    };
    const seeBtn = el('button', { class: 'capsule-btn detail-save see-btn', 'data-see': '' });
    const paintSee = () => {
      const on = isToSee(v);
      seeBtn.classList.toggle('on', on);
      seeBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
      seeBtn.replaceChildren(icon(on ? 'check' : 'eye'), el('span', null, on ? 'Marked to see' : 'See'));
    };
    seeBtn.onclick = () => { toggleToSee(v); paintSee(); };
    paintSee();
    const body = el('div', { class: 'detail-body' },
      el('button', { class: 'venue-block', style: 'margin-top:0', onclick: openVenue },
        el('div', { class: 'vb-text' },
          el('div', { class: 'vb-name' }, tierStar(galleryTier(v)), listLine(v)),
          el('div', { class: 'vb-line' }, fullAddress(v)),
          ...(v.hours || []).map(h => el('div', { class: 'vb-line' }, h))),
        icon('chevronRight')),
      seeBtn,
      el('a', { class: 'capsule-btn detail-save', href: directionsUrl(v), target: '_blank', rel: 'noopener' },
        icon('walk'), el('span', null, 'Directions')));
    page.appendChild(el('div', { class: 'page-scroll' }, hero, body));
    return page;
  }

  // The gallery detail page lists its shows one card each — the Featured card in
  // miniature at 248px, with the run dates on the second line because the venue
  // is already the subject of the page.
  const SHOW_CARD_H = 248;
  function venueShowCard(s, onOpen) {
    return showDeck([s], {
      cls: 'venue-show-card',
      carousel: { height: SHOW_CARD_H, dots: 'above-footer' },
      sub: x => dateLine(x, fmtShort),
      onOpen,
    });
  }

  // One card: photo carousel under a frosted footer. When `shows` holds more
  // than one they are the same venue's concurrent shows, so sheets peek out
  // behind the card, swiping the footer deals the next one, and a tap opens
  // whichever show is face up. A single show returns the bare card — no sheets,
  // no counter, nothing to swipe.
  const STACK_MAX = 2;            // sheets drawn behind the card, however deep the deck
  function showDeck(shows, opts) {
    const { cls, carousel, sub, onOpen } = opts;
    let idx = 0;
    let swipedAt = 0;             // a swipe ends over the card: don't let it open a show too
    const card = el('div', { class: 'card ' + cls });
    const deep = shows.length > 1;
    let root = card;
    if (deep) {
      root = el('div', { class: 'show-stack', 'data-shows': shows.length });
      for (let d = Math.min(shows.length - 1, STACK_MAX); d >= 1; d--)
        root.appendChild(el('i', { class: 'stack-sheet d' + d }));
      root.appendChild(card);
    }

    function deal(step) {
      idx = (idx + step + shows.length) % shows.length;
      swipedAt = Date.now();
      paint(step);
    }
    // one step per gesture, once the drag reads as horizontal
    function wireSwipe(node) {
      let x0 = null, y0 = null;
      node.addEventListener('pointerdown', e => { x0 = e.clientX; y0 = e.clientY; });
      node.addEventListener('pointermove', e => {
        if (x0 == null) return;
        const dx = e.clientX - x0, dy = e.clientY - y0;
        if (Math.abs(dx) < 14 || Math.abs(dx) <= Math.abs(dy)) return;
        x0 = null;
        deal(dx < 0 ? 1 : -1);
      });
      const end = () => { x0 = null; };
      node.addEventListener('pointerup', end);
      node.addEventListener('pointercancel', end);
    }

    function paint(step) {
      const s = shows[idx];
      const open = () => { if (Date.now() - swipedAt > 350) (onOpen || pushDetailFromRow)(s); };
      const text = el('div', { class: 'cf-text' },
        el('div', { class: 'name' }, displayName(s)),
        el('div', { class: 'sub' }, sub(s)));
      text.addEventListener('click', open);
      const footer = el('div', { class: 'card-footer' }, text,
        deep ? el('div', { class: 'cf-count' }, `${idx + 1} / ${shows.length}`) : null,
        bookmarkBtn(s));
      if (deep) wireSwipe(footer);
      card.innerHTML = '';
      card.append(makeCarousel(s.images, { ...carousel, onTap: open }), footer);
      card.classList.remove('deal-next', 'deal-prev');
      if (!step) return;
      void card.offsetWidth;                  // restart the animation on a re-deal
      card.classList.add(step > 0 ? 'deal-next' : 'deal-prev');
    }
    paint(0);
    return root;
  }

  // The venue card runs the same MapLibre vector style as the Map tab. It used
  // to crop a pre-stitched raster basemap, but CARTO's raster tiles now demand
  // an API key and stamp every one of them; vector tiles are crisp at any zoom,
  // carry their own attribution, and let the card pan.
  // cooperativeGestures keeps one-finger drags scrolling the page, so the map
  // can be interactive without trapping the scroll on touch.
  const venueMaps = new Set();   // { node, map } — swept when the page is gone
  let mapSweeper = null;
  function trackMap(node, map) {
    venueMaps.add({ node, map });
    if (mapSweeper) return;
    mapSweeper = new MutationObserver(() => {
      venueMaps.forEach(e => {
        if (e.node.isConnected) return;
        e.map.remove();                       // frees the WebGL context
        venueMaps.delete(e);
      });
      if (venueMaps.size) return;
      mapSweeper.disconnect();
      mapSweeper = null;
    });
    mapSweeper.observe(document.getElementById('app'), { childList: true, subtree: true });
  }
  function venueMapCard(v) {
    const card = el('div', { class: 'map-card' });
    if (!window.maplibregl) return card;
    // the card has no size until it is on screen, so build the map a frame later
    requestAnimationFrame(() => {
      if (!card.isConnected) return;
      const map = new maplibregl.Map({
        container: card, style: DemoMap.STYLE,
        center: [v.lng, v.lat], zoom: 15.5,
        attributionControl: { compact: true },
        cooperativeGestures: true,
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
      new maplibregl.Marker({ element: el('div', { class: 'pin-marker', html: ICONS.pin }), anchor: 'bottom' })
        .setLngLat([v.lng, v.lat]).addTo(map);
      trackMap(card, map);
    });
    return card;
  }

  // onOpen(show) overrides the default push-to-detail (used inside sheets).
  function showRow(s, onOpen) {
    return el('div', { class: 'show-row' },
      el('button', { class: 'sr-text', onclick: () => (onOpen || pushDetailFromRow)(s) },
        el('div', { class: 'sr-name' }, el('span', { class: 'sr-txt' }, displayName(s))),
        el('div', { class: 'sr-venue' }, tierStar(galleryTier(s.venue)), el('span', { class: 'sr-txt' }, listLine(s.venue))),
        el('div', { class: 'sr-addr' }, s.venue.neighborhood ? `${s.venue.neighborhood} · ${s.venue.address}` : s.venue.address)),
      bookmarkBtn(s));
  }
  function pushDetailFromRow(s) {
    const list = cityShows();
    push(state.tab, showDetailPage(list, list.findIndex(x => showId(x) === showId(s))));
  }

  // ---------------- filters: one state shared by Featured, List and Map ----------------
  // Defaults: gallery shows that are running now, best-ranked gallery first
  // (museums, the long tail and shows that have closed or not yet opened are
  // opt-in). kind: 'all' | 'galleries' | 'museums'; galleryRank: '50' | '100' |
  // '150', show the top N galleries; below100: also show ranks 101–200 (galleries
  // past 200 or unranked never show). Both apply to the Map only; active / receptions are toggles; list: null | 'saved' |
  // 'favorites' | a list id (the chosen list is the context every tab shows,
  // L5); sort: SORTS key (List order only). There is no show-level rank: the
  // gallery's rank is the only ranking signal (v7 dropped the Show Rank group).
  const FILTER_VERSION = 7;
  const FILTER_DEFAULT = { v: FILTER_VERSION, q: '', hoods: [], kind: 'galleries', galleryRank: '100',
    below100: true, active: true, list: null, receptions: false, sort: 'rank' };
  const KINDS = [['all', 'All venues'], ['galleries', 'Galleries'], ['museums', 'Museums']];
  // Gallery rank: buttons for the top 50 / 100 / 150, and a separate Below 100
  // toggle that adds ranks 101–200 (with Top 150 the two simply combine to the
  // top 200). Galleries past 200 or unranked are not shown. Map only: the
  // Shows and Lists tabs keep every gallery.
  const GALLERY_RANKS = [['50', 'Top 50'], ['100', 'Top 100'], ['150', 'Top 150']];
  const BELOW_100_MAX = 200;
  const withinRank = (v, f) => {
    const r = venueRank(v);
    if (r == null) return false;
    return r <= Number(f.galleryRank) || (!!f.below100 && r > 100 && r <= BELOW_100_MAX);
  };
  const SORTS = [
    ['rank', 'Gallery rank'], ['closing', 'Closing soon'], ['opened', 'Recently opened'],
    ['reception', 'Reception soon'], ['venue', 'Venue A–Z'], ['nearby', 'Nearby'],
  ];
  // Gallery tiers come from the gallery rank (the registry's, or the person's
  // own order once saved): Top = the 20 best, Notable = the 50 best, Listed =
  // everything else or unranked. Mirrored in discover_corpus.py / api/_lib/corpus.js.
  const GALLERY_TIER_CUTOFF = { top: 20, notable: 50 };
  const tierForRank = r => r == null ? 'listed' : r <= GALLERY_TIER_CUTOFF.top ? 'top' : r <= GALLERY_TIER_CUTOFF.notable ? 'notable' : 'listed';
  const galleryTier = v => tierForRank(venueRank(v));

  function loadFilter() {
    let o = {};
    try { o = JSON.parse(store.get('filter', '{}')) || {}; } catch (e) { /* ignore */ }
    if (o.v !== FILTER_VERSION) o = {};   // older filter shape: start from the defaults
    const f = { ...FILTER_DEFAULT, ...o };
    f.hoods = Array.isArray(o.hoods) ? [...o.hoods] : [];
    if (!SORTS.some(([k]) => k === f.sort)) f.sort = 'rank';
    if (!KINDS.some(([k]) => k === f.kind)) f.kind = 'galleries';
    if (!GALLERY_RANKS.some(([k]) => k === f.galleryRank)) f.galleryRank = FILTER_DEFAULT.galleryRank;
    f.below100 = typeof f.below100 === 'boolean' ? f.below100 : FILTER_DEFAULT.below100;
    if (typeof f.list !== 'string') f.list = null;
    return f;
  }
  state.filter = loadFilter();
  const persistFilter = () => store.set('filter', JSON.stringify(state.filter));
  // Clear from a sheet without the Gallery rank group leaves the Map's rank choice alone.
  const resetFilter = keepRank => { const f = state.filter; Object.assign(f, { ...FILTER_DEFAULT, hoods: [], sort: f.sort,
    ...(keepRank ? { galleryRank: f.galleryRank, below100: f.below100 } : {}) }); };
  // A list that no longer exists (a draft from an earlier session) is no context.
  const currentList = () => { const l = listById(state.filter.list); if (state.filter.list && !l) state.filter.list = null; return l; };
  // Number of filter groups off their default: the badge on the filter button.
  // Gallery rank only counts on the Map, the one tab it applies to.
  const filterActiveCount = (f, onMap) => [f.q.trim(), f.hoods.length, f.kind !== FILTER_DEFAULT.kind,
    onMap && (f.galleryRank !== FILTER_DEFAULT.galleryRank || f.below100 !== FILTER_DEFAULT.below100),
    f.active !== FILTER_DEFAULT.active, f.list, f.receptions]
    .filter(Boolean).length;
  // One line for the Discover context message.
  function filterSummary() {
    const f = state.filter, label = (opts, k) => (opts.find(([key]) => key === k) || [])[1];
    const bits = [label(KINDS, f.kind)];
    bits.push(f.active ? 'Active' : 'All dates');
    if (f.receptions) bits.push('Upcoming receptions');
    if (f.hoods.length) bits.push(f.hoods.join(', '));
    if (f.q.trim()) bits.push(`search "${f.q.trim()}"`);
    return bits.join(' · ');
  }

  const matchesQuery = (s, t) => !t ||
    s.title.toLowerCase().includes(t) ||
    (s.artist || '').toLowerCase().includes(t) ||
    s.venue.name.toLowerCase().includes(t);

  // The venue-kind split. `kind` is the registry's source of truth ('gallery',
  // 'museum', 'nonprofit', 'project_space', 'university', 'other'); isMuseum is
  // the derived mirror and only the fallback for records built before kind was
  // carried through. The filter is strict (2026-09-03): Galleries is the
  // gallery kind only, Museums the museum kind only; nonprofits, university
  // galleries, project spaces and other venues appear under All venues alone.
  const venueKind = s => s.venue.kind || (s.venue.isMuseum ? 'museum' : 'gallery');

  // Pure: shows -> shows passing every active filter.
  function filterShows(shows, f) {
    const t = f.q.trim().toLowerCase();
    const hoods = new Set(f.hoods);
    const ctxList = f.list ? listById(f.list) : null;
    const inList = ctxList ? new Set(ctxList.entries.map(e => e.id)) : null;
    return shows.filter(s => {
      if (inList && !inList.has(showId(s))) return false;
      if (!matchesQuery(s, t)) return false;
      if (hoods.size && !hoods.has(s.venue.neighborhood)) return false;
      if (f.kind === 'museums' && venueKind(s) !== 'museum') return false;
      if (f.kind === 'galleries' && venueKind(s) !== 'gallery') return false;
      if (f.active && !isActiveShow(s)) return false;
      if (f.receptions && !hasUpcomingReception(s)) return false;
      return true;
    });
  }
  // The shows the Map dims behind a list context: everything else the filter admits.
  const backdropShows = () => filterShows(cityShows(), { ...state.filter, list: null });
  // Venue-level filter: what a gallery with nothing on view has to pass to
  // reach the Map (name search, neighborhood, venue type, gallery tier).
  function venueMatches(v, f) {
    const t = f.q.trim().toLowerCase();
    if (t && !(v.name || '').toLowerCase().includes(t)) return false;
    if (f.hoods.length && !f.hoods.includes(v.neighborhood)) return false;
    const kind = v.kind || (v.isMuseum ? 'museum' : 'gallery');
    if (f.kind === 'museums' && kind !== 'museum') return false;
    if (f.kind === 'galleries' && kind !== 'gallery') return false;
    return true;
  }
  // The Map's venues: one entry per venue of the filtered shows, plus — when
  // Active shows is off and nothing else narrows to shows (upcoming receptions,
  // a list as context) — every other venue of the city the filter admits, with
  // no shows. `active` says whether something is on view there; the map fades
  // the rest. Tier and rank are the gallery's own (a show has no rank).
  function mapVenues() {
    const f = state.filter;
    const out = new Map();
    // The Gallery rank buttons narrow the Map only; galleries marked See stay
    // on it whatever they say.
    const keep = v => withinRank(v, f) || isToSee(v);
    filteredShows().filter(s => keep(s.venue)).forEach(s => {
      const k = venueKey(s.venue);
      const g = out.get(k);
      if (g) g.shows.push(s); else out.set(k, { key: k, venue: fullVenue(s.venue), shows: [s] });
    });
    if (!f.active && !f.receptions && !f.list) {
      cityVenues().forEach(v => {
        const k = venueKey(v);
        if (!out.has(k) && venueMatches(v, f) && keep(v)) out.set(k, { key: k, venue: v, shows: [] });
      });
    }
    return [...out.values()].map(g => ({ ...g, active: g.shows.some(isActiveShow), tier: galleryTier(g.venue), rank: venueRank(g.venue), see: isToSee(g.venue) }));
  }
  // Pure: stable sort by the chosen key; ties fall back to the gallery rank,
  // then, within one gallery, to the show closing soonest.
  function sortShows(shows, sort, origin) {
    const time = str => { const d = parseDate(str); return d ? d.getTime() : null; };
    const vr = s => venueRank(s.venue) ?? 1e9;
    const byRank = (a, b) => vr(a) - vr(b) || (a.venue.name || '').localeCompare(b.venue.name || '')
      || (time(a.endDate) ?? Infinity) - (time(b.endDate) ?? Infinity) || a.title.localeCompare(b.title);
    const now = Date.now();
    const arr = [...shows];
    if (sort === 'closing') {
      arr.sort((a, b) => (time(a.endDate) ?? Infinity) - (time(b.endDate) ?? Infinity) || byRank(a, b));
    } else if (sort === 'opened') {
      // most recently opened first; not-yet-open shows after, soonest first
      const key = s => { const t = time(s.startDate); return t == null ? Infinity : (t <= now ? now - t : 1e15 + (t - now)); };
      arr.sort((a, b) => key(a) - key(b) || byRank(a, b));
    } else if (sort === 'reception') {
      // soonest upcoming reception first; shows without one keep rank order after
      const key = s => { const d = receptionDate(s); return d && d >= startOfToday() ? d.getTime() : Infinity; };
      arr.sort((a, b) => key(a) - key(b) || byRank(a, b));
    } else if (sort === 'venue') {
      arr.sort((a, b) => a.venue.name.localeCompare(b.venue.name) || byRank(a, b));
    } else if (sort === 'nearby' && origin) {
      const dist = s => haversine(origin.lat, origin.lng, s.venue.lat, s.venue.lng);
      arr.sort((a, b) => dist(a) - dist(b) || byRank(a, b));
    } else {
      arr.sort(byRank);
    }
    return arr;
  }

  // Browser position once granted (Nearby sort); asked for on first use.
  let geo = null, geoAsked = false;
  function filteredShows() {
    const f = state.filter;
    currentList();
    if (f.sort === 'nearby' && !geo && !geoAsked && navigator.geolocation) {
      geoAsked = true;
      navigator.geolocation.getCurrentPosition(pos => {
        geo = { lat: pos.coords.latitude, lng: pos.coords.longitude };
        if (state.filter.sort === 'nearby') refreshAll();
      }, () => { /* keep city-center order */ }, { timeout: 5000 });
    }
    return sortShows(filterShows(cityShows(), f), f.sort, geo || city().center);
  }
  // Re-render every surface that shows the filtered set.
  function refreshAll() {
    currentList();
    persistFilter();
    Object.values(pagesRoot).forEach(root => {
      [...root.children].forEach(page => { if (page.refresh) page.refresh(); });
    });
    document.querySelectorAll('[data-filter-btn]').forEach(updateFilterBadge);
    renderMapContext();
    renderMapLegend();
    MapTab.applyFilter();
  }
  // The legend's "Nothing on view" row only means something when faded venues can appear.
  function renderMapLegend() {
    const off = document.getElementById('map-legend-off');
    if (!off) return;
    const f = state.filter;
    off.hidden = !!(f.active || f.receptions || f.list);
  }

  // ---- a list as context (L5b/L5c): bar under the Featured title, pill on the Map ----
  const ctxIcon = l => l.kind === 'favorites' ? 'heartFill' : (l.draft || l.kind === 'answer' || l.kind === 'route' || (l.source && l.source.query)) ? 'sparkle' : 'listBullet';
  function ctxBar(l, opts) {
    const running = listRunning(l);
    const saveAct = l.draft && !(l.savedAs && lists.get(l.savedAs))
      ? el('button', { class: 'ctx-act', onclick: () => { const c = lists.copyFrom(l, { kind: 'answer', source: l.source || null }); lists.markDraftSaved(l.id, c.id); refreshAll(); } }, 'Save')
      : null;
    return el('div', { class: 'ctx-bar' + (opts && opts.pill ? ' ctx-pill' : ''), 'data-ctx': l.id },
      el('span', { class: 'ctx-ico' }, icon(ctxIcon(l))),
      el('button', { class: 'ctx-text', onclick: () => { setTab('list'); push('list', listPage(l)); } }, l.name),
      el('span', { class: 'ctx-n' }, opts && opts.pill ? plural(venueCount(running), 'venue') : String(running.length)),
      saveAct,
      el('button', { class: 'ctx-x', 'aria-label': 'Clear list', onclick: () => { state.filter.list = null; refreshAll(); } }, icon('xmark')));
  }
  function renderMapContext() {
    const host = document.getElementById('map-ctx');
    if (!host) return;
    const l = currentList();
    host.innerHTML = '';
    host.hidden = !l;
    if (!l) return;
    const bar = ctxBar(l, { pill: true });
    host.className = 'ctx-pill';
    [...bar.children].forEach(ch => host.appendChild(ch));
  }

  // ---------------- rank glyphs ----------------
  // Only the top tier is marked, with a filled blue star, and only the gallery
  // is ever rated: the star leads the gallery's name on a show row and in the
  // show detail's venue block. Shows carry no mark of their own.
  const TIER_GLYPH = { top: { icon: 'star', label: 'Top 20' } };
  // The star alone, as a prefix to the gallery it rates.
  function tierStar(tier) {
    const g = TIER_GLYPH[tier];
    if (!g) return null;
    return el('span', { class: 'tier-star t-' + tier, 'data-tier': tier }, icon(g.icon));
  }
  // The gallery page's pill: "#4 · Top 20", "#33 · Top 50", "#120"; nothing when unranked.
  function rankPill(v) {
    const r = venueRank(v);
    if (r == null) return null;
    const tier = galleryTier(v);
    const label = tier === 'top' ? 'Top 20' : tier === 'notable' ? 'Top 50' : null;
    return el('div', { class: 'tier-pill t-' + tier, 'data-tier': tier, 'data-rank': String(r) },
      tier === 'top' ? icon('star') : null,
      el('span', null, `#${r}${label ? ' · ' + label : ''}${ranking.isPersonal(state.cityKey) ? ' · your ranking' : ''}`));
  }

  // ---------------- filter button + sheet ----------------
  function updateFilterBadge(btn) {
    const n = filterActiveCount(state.filter, btn.id === 'map-filter-btn');
    let badge = btn.querySelector('.badge');
    if (!n) { if (badge) badge.remove(); return; }
    if (!badge) { badge = el('span', { class: 'badge' }); btn.appendChild(badge); }
    badge.textContent = String(n);
  }
  function filterButton() {
    const b = el('button', { class: 'icon-btn', 'data-filter-btn': '', 'aria-label': 'Filters', onclick: openFilterSheet }, icon('sliders'));
    updateFilterBadge(b);
    return b;
  }

  // Grouped form; every control applies immediately, the footer just closes.
  // { sort: false } drops the Sort group — it only orders the List.
  function openFilterSheet(opts) {
    const withSort = !(opts && opts.sort === false);
    const onMap = !!(opts && opts.map);   // Gallery rank is a Map-only group
    const f = state.filter;
    const hoodList = city().neighborhoods;

    const input = el('input', { type: 'search', placeholder: 'Artist, gallery, or show', autocomplete: 'off', value: f.q });
    const clearQ = el('button', { class: 'search-clear', html: ICONS.xmark, 'aria-label': 'Clear search', hidden: f.q ? null : '' });
    input.addEventListener('input', () => { f.q = input.value; clearQ.hidden = !f.q; update(); });
    clearQ.addEventListener('click', () => { input.value = ''; f.q = ''; clearQ.hidden = true; update(); input.focus(); });

    const header = t => el('div', { class: 'group-header' }, t);
    const switchRow = (label, key) => el('button', {
      class: 'row', role: 'switch', 'data-switch': key,
      onclick: () => { f[key] = !f[key]; update(); },
    }, el('span', { class: 'row-label' }, label), el('span', { class: 'switch' }));
    const seg = (key, options) => el('div', { class: 'seg-row', 'data-seg': key },
      ...options.map(([k, label]) => el('button', { 'data-value': k, onclick: () => { f[key] = k; update(); } }, label)));
    // Gallery rank: Top 50 / 100 / 150, and a separate Below 100 toggle (ranks 101–200).
    const below = el('button', { class: 'rank-all', 'data-below100': '', onclick: () => { f.below100 = !f.below100; update(); } }, 'Below 100');
    const rankRow = el('div', { class: 'rank-buttons' }, seg('galleryRank', GALLERY_RANKS), below);
    const hoodWrap = el('div', { class: 'chip-wrap', 'data-hoods': '' });
    const sortGroup = el('div', { class: 'group', 'data-sort': '' });
    const listGroup = el('div', { class: 'group', 'data-list-group': '' });

    const body = el('div', { class: 'page-scroll' },
      el('div', { class: 'search-bar' }, el('div', { class: 'search-field' }, icon('search'), input, clearQ)),
      header('List'), listGroup,
      header('Show'),
      el('div', { class: 'group' }, switchRow('Active shows', 'active'), switchRow('Upcoming receptions', 'receptions')),
      ...(onMap ? [header('Gallery rank'), rankRow] : []),
      header('Venue Type'), seg('kind', KINDS),
      header('Neighborhoods'), hoodWrap,
      ...(withSort ? [header('Sort'), sortGroup] : []));
    const countEl = el('span');
    const clearBtn = el('button', { class: 'ghost', onclick: () => {
      resetFilter(!onMap); input.value = ''; clearQ.hidden = true; update();
    } }, 'Clear');
    const doneBtn = el('button', { class: 'capsule-btn', onclick: () => closeSheet() }, countEl);
    const sheetPage = el('div', { class: 'page filter-sheet' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Filters'), sheetCloseBtn()),
      body,
      el('div', { class: 'sheet-foot' }, clearBtn, doneBtn));

    function update(apply) {
      if (apply !== false) refreshAll();
      // List group (L5a): All shows, My Shows when something is saved, Favorite
      // galleries once a gallery is followed, the user's lists (saved copies
      // of curated lists included), and the current draft from Discover when
      // it is the context.
      listGroup.innerHTML = '';
      const options = [{ id: null, name: 'All shows', n: null }];
      const savedRunning = listRunning(savedAsList());
      if (savedRunning.length) options.push({ id: 'saved', name: 'My Shows', n: savedRunning.length });
      if (favoriteVenues().length) options.push({ id: 'favorites', name: 'Favorite galleries', n: listRunning(favoritesAsList()).length });
      lists.all().filter(listVisible).forEach(l => options.push({ id: l.id, name: l.name, n: listRunning(l).length }));
      const cur = currentList();
      if (cur && !options.some(o => o.id === cur.id)) options.push({ id: cur.id, name: cur.name, n: listRunning(cur).length });
      options.forEach(o => listGroup.appendChild(el('button', {
        class: 'row city-row', 'data-list-option': o.id || 'all', onclick: () => { f.list = o.id; update(); },
      }, el('span', { style: 'flex:1;min-width:0' }, el('div', { class: 'cr-name' }, o.name), o.n != null ? el('div', { class: 'cr-note' }, plural(o.n, 'show')) : null),
        (f.list || null) === o.id ? el('span', { class: 'check', html: ICONS.check }) : el('span'))));
      sheetPage.querySelectorAll('[data-switch]').forEach(r => {
        const on = !!f[r.dataset.switch];
        r.querySelector('.switch').classList.toggle('on', on);
        r.setAttribute('aria-checked', on ? 'true' : 'false');
      });
      sheetPage.querySelectorAll('[data-seg]').forEach(sg => sg.querySelectorAll('button').forEach(b => {
        const on = f[sg.dataset.seg] === b.dataset.value;
        b.classList.toggle('on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
      }));
      below.classList.toggle('on', !!f.below100);
      below.setAttribute('aria-pressed', f.below100 ? 'true' : 'false');
      hoodWrap.innerHTML = '';
      hoodWrap.append(
        el('button', { class: 'chip' + (f.hoods.length ? '' : ' on'), onclick: () => { f.hoods = []; update(); } }, 'All'),
        ...hoodList.map(h => el('button', {
          class: 'chip' + (f.hoods.includes(h) ? ' on' : ''),
          onclick: () => { const i = f.hoods.indexOf(h); if (i >= 0) f.hoods.splice(i, 1); else f.hoods.push(h); update(); },
        }, h)));
      sortGroup.innerHTML = '';
      if (withSort) SORTS.forEach(([k, label]) => sortGroup.appendChild(el('button', {
        class: 'row city-row', onclick: () => { f.sort = k; update(); },
      }, el('span', { class: 'cr-name' }, label),
        f.sort === k ? el('span', { class: 'check', html: ICONS.check }) : el('span'))));
      const n = onMap ? mapVenues().reduce((a, g) => a + g.shows.length, 0) : filteredShows().length;
      countEl.textContent = `Show ${n} show${n === 1 ? '' : 's'}`;
      clearBtn.hidden = !filterActiveCount(f, onMap);
    }
    update(false);
    openSheet(sheetPage);
  }

  // ---------------- lists tab ----------------
  // The tab is a library of lists (L1a): today's filtered list is the pinned
  // "All shows in <City>", My Shows appears once something is saved, the
  // user's lists when they have one, and a curated shelf with a See-all page.
  const showImg = s => s.images && s.images[0] ? s.images[0].src : null;
  function collage(shows, cls) {
    const srcs = shows.map(showImg).filter(Boolean).slice(0, 4);
    return el('div', { class: 'collage' + (srcs.length && srcs.length < 4 ? ' n' + srcs.length : '') + (cls ? ' ' + cls : '') },
      ...(srcs.length ? srcs.map(src => el('img', { src, alt: '', loading: 'lazy' })) : [el('div', { class: 'collage-empty' }, icon('listBullet'))]));
  }
  // The four cover images: the chosen cover first, then the list's running shows.
  function coverShows(l, xs) {
    const shows = xs.map(x => x.show);
    if (!l.cover) return shows;
    const c = shows.find(s => showId(s) === l.cover);
    return c ? [c, ...shows.filter(s => s !== c)] : shows;
  }
  const libRow = (o, opts) => el('button', { class: 'lib-row' + (opts && opts.cls ? ' ' + opts.cls : ''), onclick: opts && opts.onclick },
    collage(o.shows, 'sm'),
    el('span', { class: 'lib-text' }, el('span', { class: 'lib-name' }, o.name), el('span', { class: 'lib-sub' }, o.sub)),
    icon('chevronRight'));
  function savePill(l) {
    const copy = savedCopyOf(l.id);
    return el('button', { class: 'tile-save' + (copy ? ' on' : ''), 'data-save-list': l.id, onclick: e => {
      e.stopPropagation();
      if (savedCopyOf(l.id)) return;
      lists.copyFrom(l, { source: { curated: l.id } });
    } }, icon(copy ? 'check' : 'plus'), copy ? 'Saved' : 'Save');
  }
  function libTile(l, opts) {
    const o = opts || {};
    const xs = listRunning(l);
    const tile = el('div', { class: 'lib-tile' + (o.cls ? ' ' + o.cls : '') },
      el('button', { class: 'lib-tile-hit', onclick: o.onclick },
        collage(coverShows(l, xs)), el('span', { class: 'lib-name' }, l.name),
        el('span', { class: 'lib-sub' }, l.curated ? `Gallery Browser · ${xs.length}` : listMeta(xs))),
      o.save ? savePill(l) : null);
    return tile;
  }
  const libHeader = (t, trail) => el('div', { class: 'lib-header' }, el('span', null, t), trail || null);

  function listRoot() {
    const pinned = el('div', { class: 'lib-pinned' });
    const yourHead = libHeader('Your lists', el('button', { class: 'lib-new', onclick: () => newListSheet() }, icon('plus'), 'New'));
    const grid = el('div', { class: 'lib-grid' });
    const curHead = libHeader('Curated', el('button', { class: 'lib-new', 'data-see-all': '', onclick: () => push('list', curatedPage()) }, 'See all', icon('chevronRight')));
    const shelf = el('div', { class: 'lib-shelf' });
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' },
        el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'),
        el('div', { class: 'nav-btns' }, settingsButton(), filterButton())),
      el('div', { class: 'large-title' }, city().displayName),
      pinned, yourHead, grid, curHead, shelf, el('div', { class: 'lib-tail' }));
    const inline = el('div', { class: 'inline-title' }, city().displayName);
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page' }, inline, scroll);
    page.refresh = () => {
      const all = filteredShows();
      pinned.innerHTML = '';
      pinned.appendChild(libRow({
        name: `All shows in ${city().displayName}`, shows: all.slice(0, 4),
        sub: `${plural(all.length, 'show')} · ${plural(new Set(all.map(s => venueKey(s.venue))).size, 'venue')} · filters apply`,
      }, { cls: 'all', onclick: () => push('list', allShowsPage()) }));
      const saved = savedAsList(), savedRunning = listRunning(saved);
      if (savedRunning.length) {
        pinned.appendChild(libRow({ name: 'My Shows', shows: savedRunning.map(x => x.show), sub: `${plural(savedRunning.length, 'show')} · saved by you` },
          { cls: 'mine', onclick: () => push('list', listPage(saved)) }));
      }
      // Favorite galleries is a default list: what the galleries the person
      // follows have on view. Empty, it leads to the ranking page to pick some.
      const favs = favoriteVenues(), favList = favoritesAsList(), favRunning = listRunning(favList);
      pinned.appendChild(libRow({
        name: 'Favorite galleries', shows: favRunning.map(x => x.show),
        sub: favs.length
          ? `${plural(favRunning.length, 'show')} on view · ${plural(favs.length, 'gallery', 'galleries')}`
          : 'Follow galleries with the heart · browse the ranking',
      }, { cls: 'favorites', onclick: () => push('list', favs.length ? listPage(favList) : galleriesPage()) }));
      const mine = lists.all().filter(listVisible);
      grid.innerHTML = '';
      mine.forEach(l => grid.appendChild(libTile(l, { onclick: () => push('list', listPage(l)) })));
      yourHead.hidden = !mine.length; grid.hidden = !mine.length;
      shelf.innerHTML = '';
      const cur = curatedLists();
      cur.forEach(l => shelf.appendChild(libTile(l, { cls: 'small', save: true, onclick: () => push('list', listPage(l)) })));
      curHead.hidden = !cur.length; shelf.hidden = !cur.length;
    };
    page.refresh();
    const unsub = lists.subscribe(() => { if (page.isConnected) page.refresh(); else unsub(); });
    return page;
  }

  // The flat filtered list, now one list among many (L2).
  function allShowsPage() {
    const group = el('div', { class: 'group list-results' });
    const empty = el('div', { class: 'empty-plain', hidden: '' }, 'No shows match these filters.');
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, backBtn('list'), filterButton()),
      el('div', { class: 'large-title' }, 'All shows'),
      el('div', { style: 'padding-bottom:96px' }, group, empty));
    const inline = el('div', { class: 'inline-title' }, 'All shows');
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page' }, inline, scroll);
    page.refresh = () => {
      const shows = filteredShows();
      group.innerHTML = '';
      shows.forEach((s, i) => group.appendChild(showRow(s, () => push('list', showDetailPage(shows, i)))));
      group.hidden = !shows.length;
      empty.hidden = !!shows.length;
    };
    page.refresh();
    return page;
  }

  // ---- list detail (L3a; non-owned lists get Save instead of Edit) ----
  const rowSub = s => `${dateLine(s, fmtShort)}${s.venue.neighborhood ? ' · ' + s.venue.neighborhood : ''}`;
  const heroAct = (ic, label, onclick, on) => el('button', { class: 'act' + (on ? ' on' : ''), 'data-act': label, onclick }, el('span', { class: 'act-ico' }, icon(ic)), el('span', null, label));
  function listRow(x, i, l, shows, expanded, tab) {
    const s = x.show, id = showId(s), gone = !isActiveShow(s);
    const row = el('div', { class: 'list-row' + (gone ? ' gone' : '') + (expanded.has(id) ? ' expanded' : ''), 'data-id': id });
    const main = el('button', { class: 'lr-main', onclick: () => push(tab, showDetailPage(shows, i)) },
      el('div', { class: 'sr-name' }, el('span', { class: 'sr-txt' }, displayName(s))),
      el('div', { class: 'sr-venue' }, tierStar(galleryTier(s.venue)), el('span', { class: 'sr-txt' }, listLine(s.venue))),
      el('div', { class: 'sr-sub' }, rowSub(s)),
      el('div', { class: 'lr-byline' }, x.note || firstSentence(s.description)));
    const caret = el('button', { class: 'lr-caret', 'aria-label': 'Why this show', 'aria-expanded': expanded.has(id) ? 'true' : 'false', onclick: () => {
      const on = row.classList.toggle('expanded');
      caret.setAttribute('aria-expanded', on ? 'true' : 'false');
      if (on) expanded.add(id); else expanded.delete(id);
    } }, icon('chevronDown'));
    // Element.append() would render a null child as the text "null"
    [l.kind === 'route' ? el('span', { class: 'stop-n' }, String(i + 1)) : null, main, gone ? el('span', { class: 'gone-tag' }, 'closed') : null, caret]
      .filter(Boolean).forEach(n => row.appendChild(n));
    return row;
  }
  // Remove a user list (from its detail page or its edit page); `depth` pages
  // are popped so the person lands back on the library.
  function removeList(l, tab, depth) {
    if (!confirm(`Remove "${l.name}"?`)) return false;
    if (state.filter.list === l.id) state.filter.list = null;
    lists.remove(l.id);
    for (let i = 0; i < (depth || 1); i += 1) pop(tab, i < (depth || 1) - 1 ? false : undefined);
    refreshAll();
    return true;
  }
  // A page re-renders whenever lists change, possibly while another tab is
  // showing, so it keeps the tab it was pushed on rather than reading state.tab.
  function listPage(list) {
    const tab = state.tab;
    const page = el('div', { class: 'page list-page' });
    const scroll = el('div', { class: 'page-scroll' });
    page.appendChild(scroll);
    const expanded = new Set();
    const builtIn = list.id === 'saved' || list.id === 'favorites';
    const current = () => builtIn ? listById(list.id) : (lists.get(list.id) || curatedById(list.id) || list);
    function render() {
      const l = current();
      const xs = listShows(l), running = xs.filter(x => isActiveShow(x.show)), shows = xs.map(x => x.show);
      const owned = builtIn || (!l.curated && !l.draft && !!lists.get(l.id));
      const savedAs = l.draft ? (l.savedAs && lists.get(l.savedAs)) : l.curated ? savedCopyOf(l.id) : null;
      scroll.innerHTML = '';
      const acts = [heroAct('map', 'Map', () => { state.filter.list = l.id; refreshAll(); setTab('map'); })];
      if (l.kind === 'favorites') {
        acts.push(heroAct('heart', 'Galleries', () => push(tab, galleriesPage())));
      } else if (owned && l.kind !== 'saved') {
        acts.push(heroAct('pencil', 'Edit', () => push(tab, listEditPage(l, tab))));
        acts.push(heroAct('trash', 'Remove', () => removeList(l, tab, 1)));
      } else if (!owned) {
        if (savedAs) acts.push(heroAct('check', 'Saved', () => push(tab, listPage(savedAs)), true));
        else acts.push(heroAct('plus', 'Save', () => {
          const c = lists.copyFrom(l, { source: l.curated ? { curated: l.id } : (l.source || null), kind: l.draft ? 'answer' : 'user' });
          if (l.draft) lists.markDraftSaved(l.id, c.id);
          render();
        }));
      }
      const hero = el('div', { class: 'list-hero' }, collage(coverShows(l, running.length ? running : xs), 'hero'),
        el('div', { class: 'lh-name' }, l.name),
        el('div', { class: 'lh-meta' }, `${listBy(l) === 'You' ? 'by You' : listBy(l)} · ${listMeta(running)}`),
        l.desc ? el('div', { class: 'lh-desc' }, l.desc) : null,
        el('div', { class: 'lh-actions' }, ...acts));
      const rows = el('div', { class: 'list-rows' });
      xs.forEach((x, i) => rows.appendChild(listRow(x, i, l, shows, expanded, tab)));
      const emptyText = xs.length ? 'Nothing on this list is still on view.'
        : l.kind === 'favorites' ? (favoriteVenues().length ? 'Nothing on view at your favorite galleries right now.' : 'Follow galleries with the heart to see their shows here.')
        : 'This list is empty.';
      [el('div', { class: 'navrow' }, backBtn(tab), el('span')), hero,
        !running.length ? el('div', { class: 'list-empty' }, emptyText) : null,
        xs.length ? rows : null].filter(Boolean).forEach(n => scroll.appendChild(n));
    }
    page.refresh = render;
    render();
    const unsub = lists.subscribe(() => { if (page.isConnected) render(); else unsub(); });
    return page;
  }

  // ---- edit (L3d): name, description, cover, order, membership ----
  // Drag a row by its grip to reorder it among its siblings (`rowSel`, default
  // .list-row); onReorder gets the ids in their new order. Dragging near the
  // top or bottom of the enclosing scroller scrolls it, so a long list (the
  // gallery ranking) can be reordered end to end.
  function wireDrag(row, container, onReorder, rowSel) {
    const sel = rowSel || '.list-row';
    const grip = row.querySelector('.grip');
    let active = false, target = null, before = false, px = 0, py = 0, raf = 0;
    const clear = () => container.querySelectorAll('.drop-before, .drop-after').forEach(r => r.classList.remove('drop-before', 'drop-after'));
    const scroller = () => { let n = container; while (n && !(n.classList && n.classList.contains('page-scroll'))) n = n.parentElement; return n; };
    const hover = () => {
      const under = document.elementFromPoint(px, py);
      const r = under && under.closest ? under.closest(sel) : null;
      clear();
      if (!r || r === row || r.parentElement !== container) { target = null; return; }
      const box = r.getBoundingClientRect();
      before = py < box.top + box.height / 2;
      target = r; r.classList.add(before ? 'drop-before' : 'drop-after');
    };
    const autoscroll = () => {
      raf = 0;
      if (!active) return;
      const sc = scroller();
      if (sc) {
        const b = sc.getBoundingClientRect(), edge = 60;
        const step = py < b.top + edge ? -Math.min(24, (b.top + edge - py) / 2) : py > b.bottom - edge ? Math.min(24, (py - (b.bottom - edge)) / 2) : 0;
        if (step) { sc.scrollTop += step; hover(); }
      }
      raf = requestAnimationFrame(autoscroll);
    };
    // the sheet's drag-to-dismiss and the edge-swipe listen on ancestors
    grip.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
    grip.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });
    grip.addEventListener('pointerdown', e => {
      e.preventDefault(); active = true; grip.setPointerCapture(e.pointerId); row.classList.add('dragging');
      px = e.clientX; py = e.clientY;
      if (!raf) raf = requestAnimationFrame(autoscroll);
    });
    grip.addEventListener('pointermove', e => {
      if (!active) return;
      px = e.clientX; py = e.clientY;
      hover();
    });
    const finish = () => {
      if (!active) return;
      active = false; row.classList.remove('dragging'); clear();
      if (raf) { cancelAnimationFrame(raf); raf = 0; }
      if (target) { container.insertBefore(row, before ? target : target.nextSibling); onReorder([...container.querySelectorAll(sel)].map(r => r.dataset.id)); }
      target = null;
    };
    grip.addEventListener('pointerup', finish);
    grip.addEventListener('pointercancel', finish);
  }
  function listEditPage(list, tabArg) {
    const tab = tabArg || state.tab;
    const l0 = lists.get(list.id);
    if (!l0) return listPage(list);
    const snapshot = JSON.parse(JSON.stringify(l0));
    const page = el('div', { class: 'page' });
    const scroll = el('div', { class: 'page-scroll' });
    page.appendChild(scroll);
    const nameIn = el('input', { class: 'edit-name', type: 'text', value: l0.name, maxlength: '80', 'aria-label': 'List name' });
    const descIn = el('input', { class: 'edit-desc', type: 'text', value: l0.desc || '', placeholder: 'Add a description', maxlength: '200', 'aria-label': 'Description' });
    const coverWrap = el('div');
    const rows = el('div', { class: 'list-rows' });
    const renderCover = () => { const l = lists.get(list.id); coverWrap.innerHTML = ''; coverWrap.appendChild(collage(coverShows(l, listShows(l)), 'hero')); };
    function renderRows() {
      const l = lists.get(list.id);
      rows.innerHTML = '';
      listShows(l).forEach(x => {
        const s = x.show;
        const row = el('div', { class: 'list-row', 'data-id': showId(s) },
          el('span', { class: 'grip', 'aria-label': 'Reorder' }, icon('grip')),
          el('div', { class: 'lr-main' },
            el('div', { class: 'sr-name' }, el('span', { class: 'sr-txt' }, displayName(s))),
            el('div', { class: 'sr-venue' }, el('span', { class: 'sr-txt' }, listLine(s.venue))),
            el('div', { class: 'sr-sub' }, rowSub(s))),
          el('button', { class: 'minus', 'aria-label': 'Remove from list', onclick: () => { lists.removeEntry(list.id, showId(s)); renderRows(); renderCover(); } }, icon('minus')));
        wireDrag(row, rows, order => {
          const cur = lists.get(list.id);
          lists.update(list.id, { entries: order.map(id => cur.entries.find(e => e.id === id)).filter(Boolean) });
          renderCover();
        });
        rows.appendChild(row);
      });
      rows.appendChild(el('button', { class: 'row add-row', onclick: () => addShowsSheet(list.id, () => { renderRows(); renderCover(); }) },
        el('span', { class: 'row-icon', html: ICONS.plus }), el('span', { class: 'row-label' }, 'Add shows')));
    }
    const cancel = () => { lists.update(list.id, { name: snapshot.name, desc: snapshot.desc, cover: snapshot.cover, entries: snapshot.entries }); pop(tab); };
    const done = () => { lists.update(list.id, { name: nameIn.value.trim() || snapshot.name, desc: descIn.value.trim() }); pop(tab); };
    scroll.append(
      el('div', { class: 'navrow' }, el('button', { class: 'nav-textbtn', onclick: cancel }, 'Cancel'), el('button', { class: 'nav-textbtn bold', onclick: done }, 'Done')),
      el('div', { class: 'list-hero edit' }, coverWrap,
        el('button', { class: 'change-cover', onclick: () => coverSheet(list.id, renderCover) }, 'Change cover'), nameIn, descIn),
      rows,
      el('button', { class: 'delete-list', onclick: () => removeList(lists.get(list.id), tab, 2) }, 'Remove list'));
    renderCover(); renderRows();
    return page;
  }
  function coverSheet(listId, onDone) {
    const l = lists.get(listId);
    const grid = el('div', { class: 'cover-grid' });
    let close;
    listShows(l).forEach(x => {
      const s = x.show, src = showImg(s);
      if (!src) return;
      grid.appendChild(el('button', { class: showId(s) === l.cover ? 'on' : '', 'aria-label': displayName(s), onclick: () => { lists.update(listId, { cover: showId(s) }); onDone(); close(); } }, el('img', { src, alt: '' })));
    });
    close = openSheet(el('div', { class: 'page' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Cover'), sheetCloseBtn()),
      el('div', { class: 'page-scroll' }, grid)));
  }
  // Pick shows for a list from everything running in the city (search narrows).
  function addShowsSheet(listId, onDone) {
    const input = el('input', { type: 'search', placeholder: 'Artist, gallery, or show', autocomplete: 'off' });
    const group = el('div', { class: 'group picker-list' });
    const pool = sortShows(cityShows().filter(isActiveShow), 'rank');
    const render = () => {
      const t = input.value.trim().toLowerCase();
      group.innerHTML = '';
      pool.filter(s => matchesQuery(s, t)).slice(0, 60).forEach(s => {
        const on = lists.has(listId, showId(s));
        const ring = el('span', { class: 'check-ring' + (on ? ' on' : '') }, icon('check'));
        group.appendChild(el('button', { class: 'row', 'data-pick': showId(s), onclick: () => { lists.toggleEntry(listId, showId(s)); ring.classList.toggle('on'); } },
          el('span', { class: 'row-text' }, el('span', { class: 'row-label' }, displayName(s)), el('span', { class: 'row-sub' }, listLine(s.venue))), ring));
      });
    };
    input.addEventListener('input', render);
    render();
    const close = openSheet(el('div', { class: 'page' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Add shows'), sheetCloseBtn()),
      el('div', { class: 'page-scroll' }, el('div', { class: 'search-bar' }, el('div', { class: 'search-field' }, icon('search'), input)), group),
      el('div', { class: 'sheet-foot' }, el('button', { class: 'capsule-btn', onclick: () => { close(); onDone(); } }, 'Done'))));
  }
  function newListSheet() {
    const input = el('input', { class: 'new-name', type: 'text', placeholder: 'List name', autocomplete: 'off', maxlength: '80' });
    let close;
    const create = () => { const name = input.value.trim(); if (!name) { input.focus(); return; } const l = lists.create(name, []); close(); push('list', listPage(l)); push('list', listEditPage(l)); };
    input.addEventListener('keydown', e => { if (e.key === 'Enter') create(); });
    close = openSheet(el('div', { class: 'page' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'New list'), sheetCloseBtn()),
      el('div', { class: 'page-scroll' }, el('div', { class: 'group', style: 'margin-top:12px' }, el('div', { class: 'row new-list' }, el('span', { class: 'row-icon', html: ICONS.plus }), input, el('button', { class: 'create', onclick: create }, 'Create'))))));
    setTimeout(() => input.focus(), 320);
  }

  // ---- add-to-list from the bookmark (L4) ----
  function addToListSheet(s) {
    const id = showId(s);
    const group = el('div', { class: 'group' });
    let close;
    const render = () => {
      group.innerHTML = '';
      const savedRing = el('span', { class: 'check-ring' + (state.saved.has(id) ? ' on' : '') }, icon('check'));
      group.appendChild(el('button', { class: 'row', 'data-list': 'saved', onclick: () => { toggleSaved(id, true); savedRing.classList.toggle('on', state.saved.has(id)); } },
        el('span', { class: 'lib-ico blue' }, icon('bookmarkFill')),
        el('span', { class: 'row-text' }, el('span', { class: 'row-label' }, 'My Shows'), el('span', { class: 'row-sub' }, 'always')), savedRing));
      lists.all().forEach(l => {
        const ring = el('span', { class: 'check-ring' + (lists.has(l.id, id) ? ' on' : '') }, icon('check'));
        group.appendChild(el('button', { class: 'row', 'data-list': l.id, onclick: () => { lists.toggleEntry(l.id, id); ring.classList.toggle('on', lists.has(l.id, id)); } },
          collage(listShows(l).map(x => x.show), 'xs'),
          el('span', { class: 'row-text' }, el('span', { class: 'row-label' }, l.name), el('span', { class: 'row-sub' }, plural(l.entries.length, 'show'))), ring));
      });
      const input = el('input', { class: 'new-name', type: 'text', placeholder: 'New list…', autocomplete: 'off', maxlength: '80' });
      const create = () => { const name = input.value.trim(); if (!name) { input.focus(); return; } lists.create(name, [{ id }]); render(); };
      input.addEventListener('keydown', e => { if (e.key === 'Enter') create(); });
      group.appendChild(el('div', { class: 'row new-list' }, el('span', { class: 'row-icon', html: ICONS.plus }), input, el('button', { class: 'create', onclick: create }, 'Create')));
    };
    render();
    close = openSheet(el('div', { class: 'page add-sheet' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Add to list'), sheetCloseBtn()),
      el('div', { class: 'page-scroll' },
        el('div', { class: 'add-subject' }, showImg(s) ? el('img', { src: showImg(s), alt: '' }) : null, el('span', null, el('b', null, displayName(s)), el('br'), listLine(s.venue))),
        group),
      el('div', { class: 'sheet-foot' }, el('button', { class: 'capsule-btn', onclick: () => close() }, 'Done'))));
  }

  // ---- curated page (L7): the See-all behind the shelf ----
  function curatedPage() {
    const cur = curatedLists();
    const picks = cur.find(l => l.id === 'c-top');
    const walks = cur.filter(l => l.kind === 'route'), weekend = cur.filter(l => l.id === 'c-openings-weekend');
    const medium = cur.filter(l => l.id !== 'c-top' && l.kind !== 'route' && l.id !== 'c-openings-weekend');
    const shelfOf = ls => el('div', { class: 'lib-shelf' }, ...ls.map(l => libTile(l, { cls: 'small', save: true, onclick: () => push('list', listPage(l)) })));
    const section = (t, ls) => ls.length ? [libHeader(t), shelfOf(ls)] : [];
    const hero = picks ? el('button', { class: 'guide-hero', onclick: () => push('list', listPage(picks)) },
      collage(listRunning(picks).map(x => x.show), 'wide'),
      el('div', { class: 'gh-text' }, el('div', { class: 'gh-kicker' }, 'This week'), el('div', { class: 'gh-name' }, picks.name), el('div', { class: 'gh-sub' }, picks.desc))) : null;
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, backBtn('list'), el('span')),
      el('div', { class: 'large-title' }, `Curated for ${city().displayName}`),
      hero, ...section('Walks', walks), ...section('This weekend', weekend), ...section('By medium', medium), el('div', { class: 'lib-tail' }));
    const inline = el('div', { class: 'inline-title' }, 'Curated');
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page' }, inline, scroll);
    const unsub = lists.subscribe(() => { if (!page.isConnected) { unsub(); return; } page.querySelectorAll('[data-save-list]').forEach(b => { const l = curatedById(b.dataset.saveList); if (l) b.replaceWith(savePill(l)); }); });
    return page;
  }

  // ---------------- settings ----------------
  // A gear on the Lists tab. Settings holds what is personal and city-wide:
  // the gallery ranking (the app's, or the person's own) and the favorites.
  const settingsButton = () => el('button', { class: 'icon-btn', 'data-settings-btn': '', 'aria-label': 'Settings', onclick: () => push(state.tab, settingsPage()) }, icon('gear'));
  const settingRow = (label, sub, onclick, key) => el('button', { class: 'row city-row', 'data-setting': key, onclick },
    el('span', { style: 'flex:1;min-width:0' }, el('div', { class: 'cr-name' }, label), el('div', { class: 'cr-note' }, sub)),
    el('span', { class: 'chev' }, icon('chevronRight')));
  function settingsPage() {
    const tab = state.tab;
    const group = el('div', { class: 'group settings-group' });
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, backBtn(tab), el('span')),
      el('div', { class: 'large-title' }, 'Settings'),
      el('div', { class: 'group-header' }, city().displayName),
      group,
      el('p', { class: 'settings-note' }, 'Galleries are ranked city-wide. The rank orders the feed and the list, colours the map, and sets the tiers: the top 20 and the top 50. Shows themselves are not ranked. Save your own ranking and the app uses it instead.'),
      el('div', { class: 'lib-tail' }));
    const inline = el('div', { class: 'inline-title' }, 'Settings');
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page settings-page' }, inline, scroll);
    page.refresh = () => {
      const n = cityVenues().length, favs = favoriteVenues().length;
      group.innerHTML = '';
      group.append(
        settingRow('Gallery ranking',
          `${ranking.isPersonal(state.cityKey) ? 'Your ranking' : 'App ranking'} · ${plural(n, 'gallery', 'galleries')}`,
          () => push(tab, galleriesPage()), 'ranking'),
        settingRow('Favorite galleries', favs ? plural(favs, 'gallery', 'galleries') : 'None yet',
          () => push(tab, favs ? listPage(favoritesAsList()) : galleriesPage()), 'favorites'));
    };
    page.refresh();
    return page;
  }

  // ---------------- gallery ranking page ----------------
  // Every venue of the city in the order the app uses, with the tier dot, the
  // rank and a heart. Edit reorders (drag a row, or tap its number to move it
  // to a rank) and Save keeps the order as the person's own ranking; Reset
  // returns to the app's. In a sheet (from the Map) venue pages layer inside it.
  function galleriesPage(opts) {
    const asSheet = !!(opts && opts.asSheet);
    const tab = state.tab;
    const cityKey = state.cityKey;
    const page = el('div', { class: 'page galleries-page' });
    const scroll = el('div', { class: 'page-scroll' });
    const rows = el('div', { class: 'rank-rows' });
    const sub = el('div', { class: 'rank-sub' });
    const input = el('input', { type: 'search', placeholder: 'Find a gallery', autocomplete: 'off', 'aria-label': 'Find a gallery' });
    const clearQ = el('button', { class: 'search-clear', html: ICONS.xmark, 'aria-label': 'Clear search', hidden: '' });
    input.addEventListener('input', () => { clearQ.hidden = !input.value; render(); });
    clearQ.addEventListener('click', () => { input.value = ''; clearQ.hidden = true; render(); input.focus(); });
    let editing = false;
    let order = null;               // working copy while editing: venue ids

    const openVenue = v => {
      if (!asSheet) { push(tab, venuePage(v)); return; }
      const p = venuePage(v, { inSheet: true });
      page.parentElement.appendChild(p);
      p.dataset.sheetSub = '1';
      const nav = p.querySelector('.navrow .circle-btn');
      if (nav) nav.onclick = () => p.remove();
    };
    // A drag reorders the rows on screen; with a search narrowing them, the
    // visible ids take their new relative order and hidden ones keep their slots.
    const reorderVisible = visible => {
      const vis = new Set(visible); let j = 0;
      order = order.map(id => (vis.has(id) ? visible[j++] : id));
    };
    function moveSheet(v) {
      const n = order.length, cur = order.indexOf(v.id) + 1;
      const num = el('input', { class: 'move-num', type: 'number', min: '1', max: String(n), value: String(cur), inputmode: 'numeric', 'aria-label': 'Rank' });
      let close;
      const move = to => {
        const i = order.indexOf(v.id);
        if (i < 0) return;
        order.splice(i, 1);
        order.splice(Math.max(0, Math.min(n - 1, (Number(to) || cur) - 1)), 0, v.id);
        close(); render();
        const r = rows.querySelector(`[data-id="${CSS.escape(v.id)}"]`);
        if (r) r.scrollIntoView({ block: 'center' });
      };
      num.addEventListener('keydown', e => { if (e.key === 'Enter') move(num.value); });
      close = openSheet(el('div', { class: 'page move-sheet' },
        el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Move gallery'), sheetCloseBtn()),
        el('div', { class: 'page-scroll' },
          el('div', { class: 'move-name' }, v.name),
          el('div', { class: 'group', style: 'margin-top:12px' },
            el('div', { class: 'row' }, el('span', { class: 'row-label' }, `Rank (1–${n})`), num),
            el('button', { class: 'row', 'data-move': 'top', onclick: () => move(1) }, el('span', { class: 'row-icon', html: ICONS.arrowUp }), el('span', { class: 'row-label' }, 'Move to top')))),
        el('div', { class: 'sheet-foot' }, el('button', { class: 'capsule-btn', 'data-move': 'go', onclick: () => move(num.value) }, 'Move'))));
      setTimeout(() => { num.focus(); if (num.select) num.select(); }, 320);
    }
    function rankRow(v, n, onView) {
      const r = editing ? n : venueRank(v);
      const tier = tierForRank(r);
      const kind = v.kind || 'gallery';
      const row = el('div', { class: 'rank-row t-' + tier, 'data-id': v.id },
        editing ? el('span', { class: 'grip', 'aria-label': 'Reorder' }, icon('grip')) : null,
        el('button', { class: 'rank-n', 'data-rank': r == null ? '' : String(r), disabled: editing ? null : '',
          'aria-label': editing ? 'Move to a rank' : null, onclick: editing ? () => moveSheet(v) : null },
          el('i', { class: 'dot t-' + tier }), r == null ? '–' : String(r)),
        el('button', { class: 'rr-main', onclick: editing ? null : () => openVenue(v) },
          el('div', { class: 'rr-name' }, tierStar(tier), el('span', { class: 'sr-txt' }, v.name)),
          el('div', { class: 'rr-sub' }, [v.neighborhood, kind !== 'gallery' ? (KIND_LABEL[kind] || kind) : null,
            onView ? `${plural(onView, 'show')} on view` : null].filter(Boolean).join(' · '))),
        favoriteBtn(v));
      if (editing) wireDrag(row, rows, ids => { reorderVisible(ids); render(); }, '.rank-row');
      return row;
    }
    function render() {
      const byId = VENUES[cityKey] || {};
      const list = editing ? order.map(id => byId[id]).filter(Boolean) : ranking.order(cityKey);
      const t = input.value.trim().toLowerCase();
      const onView = new Map();
      cityShows().forEach(s => { if (isActiveShow(s)) { const id = venueId(s.venue); if (id) onView.set(id, (onView.get(id) || 0) + 1); } });
      const frag = document.createDocumentFragment();
      list.forEach((v, i) => { if (!t || (v.name || '').toLowerCase().includes(t)) frag.appendChild(rankRow(v, i + 1, onView.get(v.id) || 0)); });
      rows.innerHTML = '';
      rows.appendChild(frag);
      sub.textContent = editing ? 'Drag a gallery, or tap its number to move it.'
        : `${ranking.isPersonal(cityKey) ? 'Your ranking' : 'App ranking'} · ${plural(list.length, 'gallery', 'galleries')}`;
      page.classList.toggle('editing', editing);
      nav.innerHTML = '';
      nav.append(editing ? cancelBtn : leading, editing ? saveBtn : editBtn);
      resetBtn.hidden = editing || !ranking.isPersonal(cityKey);
    }
    const leading = asSheet
      ? el('button', { class: 'circle-btn', html: ICONS.xmark, 'aria-label': 'Close', onclick: () => closeSheet() })
      : backBtn(tab);
    const editBtn = el('button', { class: 'nav-textbtn', 'data-rank-edit': '', onclick: () => { editing = true; order = ranking.order(cityKey).map(v => v.id); render(); } }, 'Edit');
    const cancelBtn = el('button', { class: 'nav-textbtn', 'data-rank-cancel': '', onclick: () => { editing = false; order = null; render(); } }, 'Cancel');
    const saveBtn = el('button', { class: 'nav-textbtn bold', 'data-rank-save': '', onclick: () => {
      ranking.save(cityKey, order); editing = false; order = null; refreshAll(); render();
    } }, 'Save');
    const resetBtn = el('button', { class: 'delete-list rank-reset', 'data-rank-reset': '', onclick: () => {
      if (!confirm('Go back to the app’s ranking? Your order will be discarded.')) return;
      ranking.reset(cityKey); refreshAll(); render();
    } }, 'Reset to app ranking');
    const nav = el('div', { class: 'navrow' });
    scroll.append(nav, el('div', { class: 'large-title' }, 'Galleries'), sub,
      el('div', { class: 'search-bar' }, el('div', { class: 'search-field' }, icon('search'), input, clearQ)),
      rows, resetBtn, el('div', { class: 'lib-tail' }));
    const inline = el('div', { class: 'inline-title' }, 'Galleries');
    largeTitleScroll(scroll, inline);
    page.append(inline, scroll);
    render();
    return page;
  }

  // ---------------- city sheet ----------------
  // The cities the person shows, in their order; a tap switches. Edit lists
  // every city with a grip to reorder and an eye to show or hide it (same UX
  // as the gallery ranking); Save keeps both, Cancel discards, and Reset goes
  // back to the seven default cities in the build's order.
  function openCitySheet() {
    const page = el('div', { class: 'page cities-page' });
    const group = el('div', { class: 'group city-rows', style: 'margin-top:12px' });
    const header = el('div', { class: 'sheet-header' });
    const sub = el('div', { class: 'rank-sub' });
    let editing = false;
    let order = null, shown = null;     // working copies while editing
    const cityRow = c => {
      if (!editing) {
        return el('button', { class: 'row city-row', 'data-city': c.key, onclick: () => { setCity(c.key); } },
          el('span', { style: 'flex:1;min-width:0' }, el('div', { class: 'cr-name' }, c.displayName)),
          c.key === state.cityKey ? el('span', { class: 'check', html: ICONS.check }) : el('span'));
      }
      const on = shown.has(c.key);
      const row = el('div', { class: 'row city-row' + (on ? '' : ' hidden-city'), 'data-city': c.key, 'data-id': c.key },
        el('span', { class: 'grip', 'aria-label': 'Reorder' }, icon('grip')),
        el('span', { style: 'flex:1;min-width:0' }, el('div', { class: 'cr-name' }, c.displayName)),
        el('button', { class: 'eye-btn' + (on ? ' on' : ''), 'data-city-eye': '', 'aria-label': on ? 'Hide city' : 'Show city', 'aria-pressed': on ? 'true' : 'false',
          onclick: () => { if (shown.has(c.key)) shown.delete(c.key); else shown.add(c.key); render(); } },
          icon(on ? 'eye' : 'eyeSlash')));
      wireDrag(row, group, keys => { order = keys.slice(); render(); }, '.city-row');
      return row;
    };
    const editBtn = el('button', { class: 'nav-textbtn', 'data-city-edit': '', onclick: () => { editing = true; order = cityPrefs.order(); shown = cityPrefs.shown(); render(); } }, 'Edit');
    const cancelBtn = el('button', { class: 'nav-textbtn', 'data-city-cancel': '', onclick: () => { editing = false; order = shown = null; render(); } }, 'Cancel');
    const saveBtn = el('button', { class: 'nav-textbtn bold', 'data-city-save': '', onclick: () => {
      cityPrefs.save(order, shown); editing = false; order = shown = null; render();
    } }, 'Save');
    const resetBtn = el('button', { class: 'delete-list rank-reset', 'data-city-reset': '', onclick: () => {
      if (!confirm('Go back to the default cities? Your order will be discarded.')) return;
      cityPrefs.reset(); render();
    } }, 'Reset to default cities');
    function render() {
      const list = editing ? order.map(k => cityByKey[k]).filter(Boolean) : cityPrefs.visible();
      group.innerHTML = '';
      list.forEach(c => group.appendChild(cityRow(c)));
      header.innerHTML = '';
      header.append(editing ? cancelBtn : editBtn, el('div', { class: 'sheet-title' }, 'Cities'), editing ? saveBtn : sheetCloseBtn());
      sub.textContent = editing ? 'Drag a city to reorder it; the eye shows or hides it.' : '';
      sub.hidden = !editing;
      page.classList.toggle('editing', editing);
      resetBtn.hidden = editing || !cityPrefs.isPersonal();
    }
    render();
    page.append(header, el('div', { class: 'page-scroll' }, sub, group, resetBtn));
    openSheet(page);
  }

  // ---------------- map tab ----------------
  window.__venueKey = venueKey;          // test hook, alongside map_maplibre's window.__demoMap
  const MapTab = window.DemoMap({
    getCity: city,
    getVenues: mapVenues,
    venueKey,
    // A tap opens the compact gallery card: images, hours, Directions.
    onVenueTap: (v, shows) => openSheet(mapVenueCard(fullVenue(v), shows || [])),
    // The chosen list as context: its venues highlighted (a route also drawn
    // as a line through them in order), the rest of the filter dimmed.
    getContext: () => {
      const l = currentList();
      if (!l) return null;
      return { list: l, shows: listRunning(l).map(x => x.show), backdrop: backdropShows() };
    },
  });

  const mapFilterBtn = document.getElementById('map-filter-btn');
  mapFilterBtn.append(icon('sliders'), el('span', null, 'Filter'));
  mapFilterBtn.dataset.filterBtn = '';
  updateFilterBadge(mapFilterBtn);
  mapFilterBtn.addEventListener('click', () => openFilterSheet({ sort: false, map: true }));   // Sort orders the List only; Gallery rank is Map-only
  document.getElementById('map-cities-btn').addEventListener('click', openCitySheet);

  // ---------------- discover tab ----------------
  // A chat over the city's data, answered by /api/discover (webdemo/api). Every
  // answer that is a set of shows arrives as a list: an unsaved draft the
  // person can open, save, or put on the Map. The transcript lives per city
  // in sessionStorage; the server is stateless and gets a compact history.
  const PROMPTS = ['Opening receptions this weekend', 'What is closing this week?', 'Plan a Saturday in the Arts District', 'Video art on view now'];
  const chatKey = () => 'discover.' + state.cityKey;
  const loadChat = () => { try { const c = JSON.parse(sessionStorage.getItem(chatKey()) || 'null'); return c && Array.isArray(c.turns) ? c : { turns: [] }; } catch (e) { return { turns: [] }; } };
  const saveChat = chat => { try { sessionStorage.setItem(chatKey(), JSON.stringify({ turns: chat.turns.slice(-24) })); } catch (e) { /* private mode */ } };

  // Reads a fetch body as server-sent events; onEvent(name, data) per event.
  async function readSSE(body, onEvent) {
    const reader = body.getReader(), dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n\n')) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        let event = 'message', data = '';
        block.split('\n').forEach(line => {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          else if (line.startsWith('data:')) data += line.slice(5).trim();
        });
        if (!data) continue;
        let parsed; try { parsed = JSON.parse(data); } catch (e) { continue; }
        onEvent(event, parsed);
      }
    }
  }

  function discoverRoot() {
    let chat = loadChat();
    let busy = false;
    const body = el('div', { class: 'chat-body' });
    const newBtn = el('button', { class: 'nav-textbtn', 'data-new-chat': '', onclick: () => { if (busy) return; chat = { turns: [] }; saveChat(chat); render(); } }, 'New');
    const input = el('input', { type: 'text', placeholder: 'Ask about shows, galleries, artists…', autocomplete: 'off', enterkeyhint: 'send', 'aria-label': 'Ask Discover' });
    const sendBtn = el('button', { class: 'ask-send', 'aria-label': 'Send', onclick: () => ask(input.value) }, icon('send'));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') ask(input.value); });
    const bar = el('div', { class: 'ask-bar' }, el('div', { class: 'ask-field' }, input, sendBtn));
    const scroll = el('div', { class: 'page-scroll chat' },
      el('div', { class: 'navrow' }, el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'), newBtn),
      el('div', { class: 'large-title' }, city().displayName),
      body);
    const inline = el('div', { class: 'inline-title' }, city().displayName);
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page discover-page' }, inline, scroll, bar);

    const idle = () => el('div', { class: 'ask-idle' },
      el('div', { class: 'group-header' }, 'Try'),
      el('div', { class: 'group prompts' }, ...PROMPTS.map(p => el('button', { class: 'row prompt-row', onclick: () => ask(p) }, el('span', { class: 'row-label' }, p), icon('chevronRight')))));
    const userMsg = t => el('div', { class: 'msg user' }, t);

    // The answer card: a draft list rendered as entries with a why-line, plus
    // the route stops with times when the answer was an itinerary.
    function answerCard(turn) {
      const draft = turn.draftId && lists.get(turn.draftId);
      if (!draft) return null;
      const xs = listShows(draft), shows = xs.map(x => x.show);
      const savedAs = draft.savedAs && lists.get(draft.savedAs);
      const openList = () => push('discover', listPage(draft));
      const saveBtn = savedAs
        ? el('button', { class: 'ans-act done', onclick: () => push('discover', listPage(savedAs)) }, icon('check'), 'Saved')
        : el('button', { class: 'ans-act primary', 'data-save-draft': draft.id, onclick: () => { const c = lists.copyFrom(draft, { kind: 'answer', source: draft.source || null }); lists.markDraftSaved(draft.id, c.id); render(); } }, icon('bookmark'), 'Save as list');
      const mapBtn = el('button', { class: 'ans-act', 'data-map-draft': draft.id, onclick: () => { state.filter.list = draft.id; refreshAll(); setTab('map'); } }, icon('map'), draft.kind === 'route' ? 'Show route on Map' : 'Show on Map');
      const kids = [];
      if (draft.kind === 'route' && turn.route && turn.route.stops) {
        kids.push(el('div', { class: 'ans-head' }, el('span', { class: 'ans-title' }, draft.name), el('span', { class: 'ans-count' }, `${plural(turn.route.stops.length, 'stop')} · ${turn.route.total_km} km`)));
        kids.push(el('div', { class: 'route' }, ...turn.route.stops.map((st, i) => {
          const x = xs.find(y => showId(y.show).endsWith('/' + st.id));
          return el('button', { class: 'route-stop', onclick: () => x && push('discover', showDetailPage(shows, shows.indexOf(x.show))) },
            el('span', { class: 'stop-n' }, String(i + 1)),
            el('div', { class: 'stop-text' }, el('div', { class: 'stop-name' }, st.venue), el('div', { class: 'stop-show' }, st.name),
              el('div', { class: 'stop-meta' }, [st.arrive_label, st.hours, i ? `${st.walk_min} min walk` : null].filter(Boolean).join(' · '))));
        })));
        if (turn.route.skipped && turn.route.skipped.length) kids.push(el('p', { class: 'ai-note', style: 'padding:0 14px' }, `Skipped ${turn.route.skipped.map(s => `${s.venue} (${s.reason})`).join(', ')}.`));
      } else {
        kids.push(el('div', { class: 'ans-head' }, el('span', { class: 'ans-title' }, draft.name), el('span', { class: 'ans-count' }, plural(xs.length, 'show'))));
        xs.slice(0, 5).forEach((x, i) => kids.push(el('button', { class: 'ans-entry', onclick: () => push('discover', showDetailPage(shows, i)) },
          showImg(x.show) ? el('img', { class: 'ans-thumb', src: showImg(x.show), alt: '' }) : el('span', { class: 'ans-thumb' }),
          el('div', { class: 'ans-text' }, el('div', { class: 'ans-name' }, displayName(x.show)),
            el('div', { class: 'ans-venue' }, `${listLine(x.show.venue)}${x.show.venue.neighborhood ? ' · ' + x.show.venue.neighborhood : ''}`),
            x.note ? el('div', { class: 'why' }, x.note) : null))));
        if (xs.length > 5) kids.push(el('button', { class: 'ans-more', onclick: openList }, `Show all ${xs.length}`));
      }
      kids.push(el('div', { class: 'ans-actions' }, saveBtn, mapBtn, draft.kind === 'route' || xs.length <= 5 ? el('button', { class: 'ans-act', onclick: openList }, icon('listBullet'), 'Open') : null));
      return el('div', { class: 'answer-card', 'data-draft': draft.id }, ...kids);
    }
    function aiMsg(turn, live) {
      const parts = [];
      if (live && turn.status) parts.push(el('div', { class: 'ai-status' }, turn.status));
      parts.push(el('p', null, turn.text || ''));
      const card = answerCard(turn);
      if (card) parts.push(card);
      if (turn.note) parts.push(el('p', { class: 'ai-note' }, turn.note));
      if (turn.error) parts.push(el('div', { class: 'ai-error' }, turn.error, ' ', el('button', { class: 'chip', style: 'display:inline-flex;margin-left:6px', onclick: () => retry(turn) }, 'Retry')));
      if (turn.suggestions && turn.suggestions.length && !live) parts.push(el('div', { class: 'followups' }, ...turn.suggestions.map(t => el('button', { class: 'chip', onclick: () => ask(t) }, t))));
      return el('div', { class: 'msg ai' }, el('span', { class: 'ai-ico' }, icon('sparkle')), el('div', { class: 'ai-body' }, ...parts));
    }
    let liveNode = null, liveTurn = null;
    function render() {
      body.innerHTML = '';
      newBtn.hidden = !chat.turns.length;
      if (!chat.turns.length) { body.appendChild(idle()); return; }
      chat.turns.forEach(t => {
        if (t.role === 'user') body.appendChild(userMsg(t.text));
        else { const n = aiMsg(t, t === liveTurn); body.appendChild(n); if (t === liveTurn) liveNode = n; }
      });
      scroll.scrollTop = scroll.scrollHeight;
    }
    const updateLive = () => { if (!liveNode || !liveTurn) return; const n = aiMsg(liveTurn, true); liveNode.replaceWith(n); liveNode = n; scroll.scrollTop = scroll.scrollHeight; };

    function retry(turn) {
      const i = chat.turns.indexOf(turn);
      const q = i > 0 ? chat.turns[i - 1].text : '';
      chat.turns.splice(Math.max(0, i - 1), 2);
      saveChat(chat);
      if (q) ask(q); else render();
    }
    async function ask(q) {
      q = (q || '').trim();
      if (!q || busy) return;
      busy = true; sendBtn.disabled = true; input.value = '';
      const history = chat.turns.slice(-12).map(t => t.role === 'user' ? { role: 'user', text: t.text } : { role: 'assistant', text: t.text, presented: t.presented || undefined });
      const turn = { role: 'assistant', text: '', status: 'Thinking', presented: null, draftId: null, route: null, suggestions: [] };
      chat.turns.push({ role: 'user', text: q }, turn);
      liveTurn = turn;
      render();
      const ctxList = currentList();
      try {
        const res = await fetch('api/discover', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            city: state.cityKey, question: q, history,
            filterSummary: filterSummary(), activeList: ctxList ? ctxList.name : null,
            savedLists: lists.all().map(l => l.name),
            savedShows: [...state.saved].filter(id => id.startsWith(state.cityKey + '/')).map(id => id.split('/')[1]),
          }),
        });
        if (!res.ok) { const err = await res.json().catch(() => ({})); throw new Error(err.error || `HTTP ${res.status}`); }
        let sawDone = false;
        await readSSE(res.body, (event, data) => {
          if (event === 'text') turn.text += data.delta;
          else if (event === 'status') turn.status = data.label;
          else if (event === 'list') {
            const draft = lists.draft({ name: data.title, kind: data.kind, desc: '', entries: data.entries, source: { query: q } });
            turn.draftId = draft.id;
            turn.presented = { title: data.title, kind: data.kind, ids: data.entries.map(e => e.id), dropped: data.dropped || [] };
            turn.suggestions = data.suggestions || [];
          } else if (event === 'route') turn.route = data;
          else if (event === 'note') turn.note = data.message;
          else if (event === 'error') turn.error = data.message || "Couldn't answer that just now.";
          else if (event === 'done') { sawDone = true; if (window.console) console.debug('discover', data); }
          updateLive();
        });
        if (!sawDone && !turn.error) turn.note = (turn.note ? turn.note + ' ' : '') + 'Cut short.';
      } catch (e) {
        turn.error = /HTTP 429|too many/i.test(e.message) ? 'Too many questions at once. Give it a minute.' : /not configured/i.test(e.message) ? 'Discover is not set up on this deployment.' : "Couldn't answer that just now.";
      } finally {
        turn.status = null; liveTurn = null; liveNode = null; busy = false; sendBtn.disabled = false;
        saveChat(chat); render();
      }
    }
    page.refresh = () => { if (!busy) { chat = loadChat(); render(); } };
    render();
    return page;
  }

  // ---------------- tabs & city switching ----------------
  const screens = {
    featured: document.getElementById('screen-featured'),
    list: document.getElementById('screen-list'),
    map: document.getElementById('screen-map'),
    discover: document.getElementById('screen-discover'),
  };
  const tabBtns = document.querySelectorAll('.tab-btn');
  function setTab(tab) {
    state.tab = tab;
    Object.entries(screens).forEach(([k, elm]) => elm.classList.toggle('active', k === tab));
    tabBtns.forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    if (tab === 'map') MapTab.ensureInit();
  }
  tabBtns.forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));

  function setCity(key) {
    if (key !== state.cityKey) {
      state.cityKey = key;
      persistCity();
      rebuildTabs();
      MapTab.cityChanged();
    }
    closeAllSheets();
  }

  function rebuildTabs() {
    const hoods = city().neighborhoods;
    state.filter.hoods = state.filter.hoods.filter(h => hoods.includes(h));   // city switch
    const l = currentList();
    if (l && l.city && l.city !== state.cityKey) state.filter.list = null;   // a list belongs to its city
    pagesRoot.featured.innerHTML = '';
    pagesRoot.list.innerHTML = '';
    pagesRoot.discover.innerHTML = '';
    push('featured', featuredRoot());
    push('list', listRoot());
    push('discover', discoverRoot());
    document.querySelectorAll('[data-filter-btn]').forEach(updateFilterBadge);
    renderMapContext();
    renderMapLegend();
  }

  // ---------------- boot ----------------
  // iOS Safari: pinches should zoom photos, never the page (viewport flags
  // alone don't stop Safari's page zoom).
  document.addEventListener('gesturestart', e => e.preventDefault());

  rebuildTabs();
  setTab('featured');
  // test hook
  window.DemoDebug = { receptionDate, hasUpcomingReception, isActiveShow, galleryTier, venueRank, appRank, filteredShows, mapVenues, cityVenues, favoriteVenues, favoritesAsList, ranking, cityPrefs, GALLERY_TIER_CUTOFF, lists, curatedLists, listVisible, FILTER_VERSION };
})();
