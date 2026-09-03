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

  const state = {
    cityKey: store.get('selectedCityKey', DATA.defaultCity),
    saved: new Set(JSON.parse(store.get('savedShowIDs', '[]'))),
    tab: 'featured',
  };
  if (!DATA.cities.some(c => c.key === state.cityKey)) state.cityKey = DATA.defaultCity;

  const persistSaved = () => store.set('savedShowIDs', JSON.stringify([...state.saved]));
  const persistCity = () => store.set('selectedCityKey', state.cityKey);

  // ---------------- data helpers ----------------
  const cityByKey = Object.fromEntries(DATA.cities.map(c => [c.key, c]));
  const showsByCity = {};
  DATA.shows.forEach(s => { (showsByCity[s.city] = showsByCity[s.city] || []).push(s); });

  const city = () => cityByKey[state.cityKey];
  const cityShows = () => showsByCity[state.cityKey] || [];
  const showId = s => s.city + '/' + s.slug;
  // Shows embed their own venue copy; this key identifies "the same venue"
  // across shows (normalized name + coordinate to ~1 m) so several concurrent
  // shows collapse into one map pin / one venue page.
  const venueKey = v => {
    const name = (v.name || '').trim().toLowerCase().replace(/\s+/g, ' ');
    const pos = Number(v.lat).toFixed(5) + ',' + Number(v.lng).toFixed(5);
    return name ? name + '@' + pos : pos;
  };
  const venueShows = v => { const k = venueKey(v); return cityShows().filter(s => venueKey(s.venue) === k); };
  // Venue-level data (blurb, gallery rank) lives in DATA.venues keyed by venueId;
  // the embedded copy carries the same fields as a fallback for older bundles.
  const VENUES = DATA.venues || {};
  const venueRecord = v => { const id = v && (v.venueId || v.id); return (id && VENUES[id]) || null; };
  const venueAbout = v => { const r = venueRecord(v); return (r && r.about) || (v && v.about) || null; };
  const venueRank = v => { const r = venueRecord(v); const n = r && r.rank != null ? r.rank : (v && v.rank); return n == null ? null : +n; };
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
  function toggleSaved(id) {
    if (state.saved.has(id)) state.saved.delete(id); else state.saved.add(id);
    persistSaved();
    refreshBookmarkUI();
    if (state.filter.saved) refreshAll();   // "Saved only": the row set itself changes
    else MapTab.applyFilter();
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

  // ---------------- navigation ----------------
  const pagesRoot = {
    featured: document.getElementById('pages-featured'),
    list: document.getElementById('pages-list'),
  };
  function push(tab, page) {
    if (pagesRoot[tab].children.length) page.classList.add('page-push');
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
      const dotsEl = el('div', { class: 'carousel-dots ' + (dots === 'bottom' ? 'bottom-center' : 'top-left') });
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
  function showCard(shows, i) {
    const s = shows[i];
    const car = makeCarousel(s.images, {
      aspect: '1 / 1', dots: 'top-left',
      onTap: () => push(state.tab, showDetailPage(shows, i)),
    });
    const text = el('div', { class: 'cf-text' },
      el('div', { class: 'name' }, displayName(s)),
      el('div', { class: 'venue' }, `${listLine(s.venue)} • ${s.venue.address}`));
    text.addEventListener('click', () => push(state.tab, showDetailPage(shows, i)));
    const footer = el('div', { class: 'card-footer' }, text, bookmarkBtn(s));
    return el('div', { class: 'card' }, car, footer);
  }

  // Featured and List are two renderings of the same filtered set (state.filter);
  // each root page exposes refresh() so refreshAll() can re-render it in place.
  function featuredRoot() {
    const feed = el('div', { class: 'feed' });
    const empty = el('div', { class: 'empty-plain', hidden: '' }, 'No shows match these filters.');
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' },
        el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'),
        filterButton()),
      el('div', { class: 'large-title' }, city().displayName),
      feed, empty);
    const inline = el('div', { class: 'inline-title' }, city().displayName);
    largeTitleScroll(scroll, inline);
    const page = el('div', { class: 'page' }, inline, scroll);
    page.refresh = () => {
      const shows = filteredShows();
      feed.innerHTML = '';
      shows.forEach((_, i) => feed.appendChild(showCard(shows, i)));
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
      if (!asSheet && shows.length > 1) {
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

      const venueBlock = el('button', {
        class: 'venue-block',
        onclick: () => pushOrSheet(venuePage(s.venue, asSheet ? { inSheet: true } : undefined)),
      },
        el('div', { class: 'vb-text' },
          el('div', { class: 'vb-name' }, listLine(s.venue)),
          el('div', { class: 'vb-line' }, fullAddress(s.venue)),
          ...s.venue.hours.map(h => el('div', { class: 'vb-line' }, h))),
        icon('chevronRight'));

      const body = el('div', { class: 'detail-body' },
        s.artist ? el('div', { class: 'detail-artist' }, s.artist) : null,
        el('div', { class: 'detail-title' }, s.title),
        metaRow(tierGlyph(showTier(s))),   // a gallery's own rank shows only on its page
        el('div', { class: 'detail-dates' }, dateLine(s)),
        s.reception ? el('div', { class: 'detail-reception' }, 'Reception: ' + s.reception) : null,
        saveBtn,
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

    const actions = el('div', { class: 'venue-actions' },
      el('a', { class: 'capsule-btn', href: directionsUrl(v), target: '_blank', rel: 'noopener' },
        icon('walk'), el('span', null, 'Directions to venue')),
      v.website ? el('a', { class: 'capsule-btn', href: v.website, target: '_blank', rel: 'noopener' },
        icon('compass'), el('span', null, 'Open website')) : null,
      v.phone ? el('a', { class: 'capsule-btn', href: 'tel:' + v.phone.replace(/[^\d+]/g, '') },
        icon('phone'), el('span', null, 'Call venue')) : null);

    const page = el('div', { class: 'page' });
    const shows = venueShows(v);
    const openShow = inSheet ? s => pushShowInSheet(page.parentElement, s) : pushDetailFromRow;
    const showsSection = shows.length
      ? el('div', { class: 'venue-shows' },
          el('div', { class: 'group-header' }, 'Shows'),
          el('div', { class: 'venue-show-list' },
            ...shows.map((_, i) => venueShowCard(shows, i, openShow))))
      : null;

    const leading = asSheet
      ? el('button', { class: 'circle-btn', html: ICONS.xmark, 'aria-label': 'Close' })
      : backBtn(state.tab);
    if (asSheet) leading.onclick = () => closeSheet();

    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, leading, el('span')),
      el('div', { class: 'venue-body' },
        el('div', { class: 'venue-title' }, v.name),
        tierPill(galleryTier(v)),
        showsSection,
        venueAbout(v) ? el('p', { class: 'venue-about' }, venueAbout(v)) : null,
        el('div', { class: 'venue-lines' },
          el('div', null, fullAddress(v)),
          ...v.hours.map(h => el('div', null, h))),
        mapCard,
        actions));
    page.appendChild(scroll);
    return page;
  }

  // A venue's shows read as the Featured card in miniature — photo, frosted
  // footer — at a third the height, so several fit above the fold. The venue is
  // the subject of the page, so the second line carries dates, not the address.
  function venueShowCard(shows, i, onOpen) {
    const s = shows[i];
    const open = () => (onOpen || pushDetailFromRow)(s);
    const car = makeCarousel(s.images, { height: 124, onTap: open });
    const text = el('div', { class: 'cf-text' },
      el('div', { class: 'name' }, displayName(s)),
      el('div', { class: 'sub' }, dateLine(s, fmtShort)));
    text.addEventListener('click', open);
    return el('div', { class: 'card venue-show-card' }, car,
      el('div', { class: 'card-footer' }, text, bookmarkBtn(s)));
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
        el('div', { class: 'sr-name' }, tierStar(showTier(s)), el('span', { class: 'sr-txt' }, displayName(s))),
        el('div', { class: 'sr-venue' }, el('span', { class: 'sr-txt' }, listLine(s.venue))),
        el('div', { class: 'sr-addr' }, s.venue.neighborhood ? `${s.venue.neighborhood} · ${s.venue.address}` : s.venue.address)),
      bookmarkBtn(s));
  }
  function pushDetailFromRow(s) {
    const list = cityShows();
    push(state.tab, showDetailPage(list, list.findIndex(x => showId(x) === showId(s))));
  }

  // ---------------- filters: one state shared by Featured, List and Map ----------------
  // Defaults: featured gallery shows that are running now (museums, the long
  // tail, unranked galleries and shows that have closed or not yet opened are
  // opt-in). kind: 'all' | 'galleries' | 'museums'; showRank: 'all' |
  // 'featured' | 'picks'; galleryRank: 'all' | 'notable' | 'top'; active /
  // saved / receptions are toggles; sort: SORTS key (List order only).
  const FILTER_VERSION = 5;
  const FILTER_DEFAULT = { v: FILTER_VERSION, q: '', hoods: [], kind: 'galleries', showRank: 'featured', galleryRank: 'all',
    active: true, saved: false, receptions: false, sort: 'rank' };
  const KINDS = [['all', 'All venues'], ['galleries', 'Galleries'], ['museums', 'Museums']];
  const SHOW_RANKS = [['all', 'All Shows'], ['featured', 'Featured'], ['picks', "Editor's Picks"]];
  const GALLERY_RANKS = [['all', 'All Galleries'], ['notable', 'Notable'], ['top', 'Top Ranked']];
  const SORTS = [
    ['rank', 'Ranking'], ['closing', 'Closing soon'], ['opened', 'Recently opened'],
    ['reception', 'Reception soon'], ['venue', 'Venue A–Z'], ['gallery', 'Gallery rank'],
    ['nearby', 'Nearby'],
  ];
  // Gallery tiers come from the registry rank (rank_venues.py, city-wide over
  // every venue, not just those with shows): Top = the 25 best, Notable = the
  // 100 best, Listed = everything else or unranked.
  const GALLERY_TIER_CUTOFF = { top: 25, notable: 100 };
  const galleryTier = v => {
    const r = venueRank(v);
    return r == null ? 'listed' : r <= GALLERY_TIER_CUTOFF.top ? 'top' : r <= GALLERY_TIER_CUTOFF.notable ? 'notable' : 'listed';
  };
  const showTier = s => s.editorsPick ? 'picks' : s.featured ? 'featured' : null;

  function loadFilter() {
    let o = {};
    try { o = JSON.parse(store.get('filter', '{}')) || {}; } catch (e) { /* ignore */ }
    if (o.v !== FILTER_VERSION) o = {};   // older filter shape: start from the defaults
    const f = { ...FILTER_DEFAULT, ...o };
    f.hoods = Array.isArray(o.hoods) ? [...o.hoods] : [];
    if (!SORTS.some(([k]) => k === f.sort)) f.sort = 'rank';
    if (!KINDS.some(([k]) => k === f.kind)) f.kind = 'galleries';
    if (!SHOW_RANKS.some(([k]) => k === f.showRank)) f.showRank = FILTER_DEFAULT.showRank;
    if (!GALLERY_RANKS.some(([k]) => k === f.galleryRank)) f.galleryRank = FILTER_DEFAULT.galleryRank;
    return f;
  }
  state.filter = loadFilter();
  const persistFilter = () => store.set('filter', JSON.stringify(state.filter));
  const resetFilter = () => { Object.assign(state.filter, { ...FILTER_DEFAULT, hoods: [], sort: state.filter.sort }); };
  // Number of filter groups off their default: the badge on the filter button.
  const filterActiveCount = f => [f.q.trim(), f.hoods.length, f.kind !== FILTER_DEFAULT.kind,
    f.showRank !== FILTER_DEFAULT.showRank, f.galleryRank !== FILTER_DEFAULT.galleryRank,
    f.active !== FILTER_DEFAULT.active, f.saved, f.receptions]
    .filter(Boolean).length;

  const matchesQuery = (s, t) => !t ||
    s.title.toLowerCase().includes(t) ||
    (s.artist || '').toLowerCase().includes(t) ||
    s.venue.name.toLowerCase().includes(t);

  // The venue-kind split. `kind` is the registry's source of truth ('gallery',
  // 'museum', 'nonprofit', 'project_space', 'university', 'other'); isMuseum is
  // the derived mirror and only the fallback for records built before kind was
  // carried through. Nonprofits and project spaces count as galleries — only
  // museums are set apart.
  const showIsMuseum = s => (s.venue.kind ? s.venue.kind === 'museum' : !!s.venue.isMuseum);

  // Pure: shows -> shows passing every active filter.
  function filterShows(shows, f) {
    const t = f.q.trim().toLowerCase();
    const hoods = new Set(f.hoods);
    return shows.filter(s => {
      if (!matchesQuery(s, t)) return false;
      if (hoods.size && !hoods.has(s.venue.neighborhood)) return false;
      if (f.kind === 'museums' && !showIsMuseum(s)) return false;
      if (f.kind === 'galleries' && showIsMuseum(s)) return false;
      if (f.showRank === 'featured' && !s.featured) return false;
      if (f.showRank === 'picks' && !s.editorsPick) return false;
      if (f.galleryRank !== 'all') {
        const tier = galleryTier(s.venue);
        if (f.galleryRank === 'top' ? tier !== 'top' : tier === 'listed') return false;
      }
      if (f.active && !isActiveShow(s)) return false;
      if (f.saved && !state.saved.has(showId(s))) return false;
      if (f.receptions && !hasUpcomingReception(s)) return false;
      return true;
    });
  }
  // Pure: stable sort by the chosen key; ties fall back to curation rank.
  function sortShows(shows, sort, origin) {
    const byRank = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9);
    const time = str => { const d = parseDate(str); return d ? d.getTime() : null; };
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
    } else if (sort === 'gallery') {
      // best-ranked gallery first (registry rank via rank_venues.py); unranked venues last
      const key = s => { const r = venueRank(s.venue); return r == null ? Infinity : r; };
      arr.sort((a, b) => key(a) - key(b) || byRank(a, b));
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
    persistFilter();
    [pagesRoot.featured, pagesRoot.list].forEach(root => {
      const page = root.firstElementChild;
      if (page && page.refresh) page.refresh();
    });
    document.querySelectorAll('[data-filter-btn]').forEach(updateFilterBadge);
    MapTab.applyFilter();
  }

  // ---------------- rank glyphs ----------------
  // Three dots + a short word; nothing for the "All" / listed / plain cases.
  // Only the top tier is marked, with a filled blue star: Editor's Pick for a
  // show, Top for a gallery. Featured and Notable get nothing.
  const TIER_GLYPH = {
    picks: { icon: 'star', label: "Editor's Pick" },
    top: { icon: 'star', label: 'Top Gallery' },
  };
  // The star alone, as a prefix to whatever it rates.
  function tierStar(tier) {
    const g = TIER_GLYPH[tier];
    if (!g) return null;
    return el('span', { class: 'tier-star t-' + tier, 'data-tier': tier }, icon(g.icon));
  }
  // Star + word, for the show detail header and the venue-page pill.
  function tierGlyph(tier) {
    const g = TIER_GLYPH[tier];
    if (!g) return null;
    return el('span', { class: 'sr-tier t-' + tier, 'data-tier': tier }, icon(g.icon), g.label);
  }
  function tierPill(tier) {
    const g = TIER_GLYPH[tier];
    if (!g) return null;
    return el('div', { class: 'tier-pill t-' + tier, 'data-tier': tier }, icon(g.icon), g.label);
  }
  const metaRow = (...glyphs) => {
    const kids = glyphs.filter(Boolean);
    return kids.length ? el('div', { class: 'detail-meta-row' }, ...kids) : null;
  };

  // ---------------- filter button + sheet ----------------
  function updateFilterBadge(btn) {
    const n = filterActiveCount(state.filter);
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
    const hoodWrap = el('div', { class: 'chip-wrap', 'data-hoods': '' });
    const sortGroup = el('div', { class: 'group', 'data-sort': '' });

    const body = el('div', { class: 'page-scroll' },
      el('div', { class: 'search-bar' }, el('div', { class: 'search-field' }, icon('search'), input, clearQ)),
      header('Show'),
      el('div', { class: 'group' }, switchRow('Active shows', 'active'),
        switchRow('Saved only', 'saved'), switchRow('Upcoming receptions', 'receptions')),
      header('Show Rank'), seg('showRank', SHOW_RANKS),
      header('Gallery Rank'), seg('galleryRank', GALLERY_RANKS),
      header('Venue Type'), seg('kind', KINDS),
      header('Neighborhoods'), hoodWrap,
      ...(withSort ? [header('Sort'), sortGroup] : []));
    const countEl = el('span');
    const clearBtn = el('button', { class: 'ghost', onclick: () => {
      resetFilter(); input.value = ''; clearQ.hidden = true; update();
    } }, 'Clear');
    const doneBtn = el('button', { class: 'capsule-btn', onclick: () => closeSheet() }, countEl);
    const sheetPage = el('div', { class: 'page filter-sheet' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Filters'), sheetCloseBtn()),
      body,
      el('div', { class: 'sheet-foot' }, clearBtn, doneBtn));

    function update(apply) {
      if (apply !== false) refreshAll();
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
      const n = filteredShows().length;
      countEl.textContent = `Show ${n} show${n === 1 ? '' : 's'}`;
      clearBtn.hidden = !filterActiveCount(f);
    }
    update(false);
    openSheet(sheetPage);
  }

  // ---------------- list tab ----------------
  function listRoot() {
    const group = el('div', { class: 'group list-results' });
    const empty = el('div', { class: 'empty-plain', hidden: '' }, 'No shows match these filters.');
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' },
        el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'),
        filterButton()),
      el('div', { class: 'large-title' }, city().displayName),
      el('div', { style: 'padding-bottom:96px' }, group, empty));
    const inline = el('div', { class: 'inline-title' }, city().displayName);
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

  // ---------------- city sheet ----------------
  function openCitySheet() {
    const rows = DATA.cities.map(c => {
      const r = el('button', { class: 'row city-row', onclick: () => { setCity(c.key); } },
        el('span', { style: 'flex:1;min-width:0' },
          el('div', { class: 'cr-name' }, c.displayName),
          c.availabilityNote ? el('div', { class: 'cr-note' }, c.availabilityNote) : null),
        c.key === state.cityKey ? el('span', { class: 'check', html: ICONS.check }) : el('span'));
      return r;
    });
    openSheet(el('div', { class: 'page' },
      el('div', { class: 'sheet-header' },
        el('div', { class: 'sheet-title' }, 'Cities'), sheetCloseBtn()),
      el('div', { class: 'page-scroll' },
        el('div', { class: 'group', style: 'margin-top:12px' }, ...rows))));
  }

  // ---------------- map tab ----------------
  const MapTab = window.DemoMap({
    getCity: city,
    getShows: filteredShows,
    venueKey,
    venueTier: galleryTier,
    onVenueTap: v => {
      openSheet(venuePage(v, { asSheet: true }));
    },
  });

  const mapFilterBtn = document.getElementById('map-filter-btn');
  mapFilterBtn.append(icon('sliders'), el('span', null, 'Filter'));
  mapFilterBtn.dataset.filterBtn = '';
  updateFilterBadge(mapFilterBtn);
  mapFilterBtn.addEventListener('click', () => openFilterSheet({ sort: false }));   // Sort orders the List only
  document.getElementById('map-cities-btn').addEventListener('click', openCitySheet);

  // ---------------- tabs & city switching ----------------
  const screens = {
    featured: document.getElementById('screen-featured'),
    list: document.getElementById('screen-list'),
    map: document.getElementById('screen-map'),
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
    pagesRoot.featured.innerHTML = '';
    pagesRoot.list.innerHTML = '';
    push('featured', featuredRoot());
    push('list', listRoot());
    document.querySelectorAll('[data-filter-btn]').forEach(updateFilterBadge);
  }

  // ---------------- boot ----------------
  // iOS Safari: pinches should zoom photos, never the page (viewport flags
  // alone don't stop Safari's page zoom).
  document.addEventListener('gesturestart', e => e.preventDefault());

  rebuildTabs();
  setTab('featured');
  // test hook
  window.DemoDebug = { receptionDate, hasUpcomingReception, isActiveShow, galleryTier, showTier, GALLERY_TIER_CUTOFF };
})();
