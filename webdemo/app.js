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
    mapFilter: 'all', // 'myShows' | 'all' | 'receptions'
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
  function dateLine(s) {
    const start = parseDate(s.startDate), end = parseDate(s.endDate);
    if (!end) return '';
    if (start && start > new Date()) return 'Opens ' + fmtLong(start);
    return 'Through ' + fmtLong(end);
  }

  const DAY = 86400e3;
  const featuredShows = () => cityShows().filter(s => s.featured);
  const museumShows = () => cityShows().filter(s => s.venue.isMuseum);
  const savedShows = () => cityShows().filter(s => state.saved.has(showId(s)));
  const editorsPicks = () => cityShows().filter(s => s.editorsPick);
  const receptionShows = () => cityShows().filter(s => s.reception != null);
  const openingThisWeek = () => cityShows().filter(s => {
    const d = parseDate(s.startDate); return d && Math.abs(d - Date.now()) <= 7 * DAY;
  });
  const closingThisWeek = () => cityShows().filter(s => {
    const d = parseDate(s.endDate); if (!d) return false;
    const diff = d - Date.now(); return diff >= 0 && diff <= 7 * DAY;
  });
  const neighborhoodShows = n => cityShows().filter(s => s.venue.neighborhood === n);
  function haversine(lat1, lng1, lat2, lng2) {
    const r = x => x * Math.PI / 180, R = 6371;
    const a = Math.sin(r(lat2 - lat1) / 2) ** 2 +
      Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lng2 - lng1) / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }
  const searchShows = q => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    return cityShows().filter(s =>
      s.title.toLowerCase().includes(t) ||
      (s.artist || '').toLowerCase().includes(t) ||
      s.venue.name.toLowerCase().includes(t));
  };

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
    MapTab.applyFilter();
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
    const footer = el('div', { class: 'card-footer' },
      el('div', { class: 'name' }, displayName(s)),
      el('div', { class: 'venue' }, `${listLine(s.venue)} • ${s.venue.address}`));
    footer.addEventListener('click', () => push(state.tab, showDetailPage(shows, i)));
    return el('div', { class: 'card' }, car, footer);
  }

  function featuredRoot() {
    const shows = featuredShows();
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' },
        el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'),
        el('span')),
      el('div', { class: 'large-title' }, city().displayName),
      shows.length
        ? el('div', { class: 'feed' }, ...shows.map((_, i) => showCard(shows, i)))
        : el('div', { class: 'empty-plain' }, 'No shows in this list right now.'));
    const inline = el('div', { class: 'inline-title' }, city().displayName);
    largeTitleScroll(scroll, inline);
    return el('div', { class: 'page' }, inline, scroll);
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
      const leading = asSheet
        ? el('button', {
            class: 'circle-btn', html: ICONS.xmark, 'aria-label': 'Close',
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

      const venueBlock = el('button', { class: 'venue-block', onclick: () => pushOrSheet(venuePage(s.venue)) },
        el('div', { class: 'vb-text' },
          el('div', { class: 'vb-name' }, listLine(s.venue)),
          el('div', { class: 'vb-line' }, fullAddress(s.venue)),
          ...s.venue.hours.map(h => el('div', { class: 'vb-line' }, h))),
        icon('chevronRight'));

      const body = el('div', { class: 'detail-body' },
        s.artist ? el('div', { class: 'detail-artist' }, s.artist) : null,
        el('div', { class: 'detail-title' }, s.title),
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

  function venuePage(v) {
    const cty = city();
    const mapCard = el('div', { class: 'map-card' });
    const img = el('img', { class: 'map-crop', src: cty.map.src, alt: '' });
    img.style.width = cty.map.w + 'px';
    img.style.height = cty.map.h + 'px';
    const pinEl = el('div', { class: 'pin-marker', html: ICONS.pin });
    mapCard.append(img, pinEl,
      el('div', { class: 'map-attrib' }, DATA.attribution));
    requestAnimationFrame(() => {
      const Z = 1.8;
      const cw = mapCard.clientWidth || 357, ch = 320;
      img.style.transformOrigin = '0 0';
      img.style.transform = `translate(${cw / 2 - v.mapX * Z}px, ${ch / 2 - v.mapY * Z}px) scale(${Z})`;
    });

    const actions = el('div', { class: 'venue-actions' },
      el('a', { class: 'capsule-btn', href: directionsUrl(v), target: '_blank', rel: 'noopener' },
        icon('walk'), el('span', null, 'Directions to venue')),
      v.website ? el('a', { class: 'capsule-btn', href: v.website, target: '_blank', rel: 'noopener' },
        icon('compass'), el('span', null, 'Open website')) : null,
      v.phone ? el('a', { class: 'capsule-btn', href: 'tel:' + v.phone.replace(/[^\d+]/g, '') },
        icon('phone'), el('span', null, 'Call venue')) : null);

    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, backBtn(state.tab), el('span')),
      el('div', { class: 'venue-body' },
        el('div', { class: 'venue-title' }, v.name),
        el('div', { class: 'venue-lines' },
          el('div', null, fullAddress(v)),
          ...v.hours.map(h => el('div', null, h))),
        mapCard,
        actions));
    return el('div', { class: 'page' }, scroll);
  }

  function showRow(s) {
    const row = el('div', { class: 'show-row' },
      el('button', { class: 'sr-text', onclick: () => pushDetailFromRow(s) },
        el('div', { class: 'sr-name' }, displayName(s)),
        el('div', { class: 'sr-venue' }, listLine(s.venue)),
        el('div', { class: 'sr-addr' }, s.venue.address)),
      bookmarkBtn(s));
    return row;
  }
  function pushDetailFromRow(s) {
    const list = cityShows();
    push(state.tab, showDetailPage(list, list.findIndex(x => showId(x) === showId(s))));
  }

  function showListPage(title, shows, opts) {
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, backBtn(state.tab), el('span')),
      el('div', { class: 'large-title' }, title),
      shows.length
        ? el('div', { class: 'group' }, ...shows.map(showRow))
        : (opts && opts.emptyState) || el('div', { class: 'empty-plain' }, 'No shows in this list right now.'));
    const inline = el('div', { class: 'inline-title' }, title);
    largeTitleScroll(scroll, inline);
    return el('div', { class: 'page' }, inline, scroll);
  }

  function museumsPage() {
    const shows = museumShows();
    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' }, backBtn(state.tab), el('span')),
      el('div', { class: 'large-title' }, 'Museums'),
      el('div', { class: 'on-view-header' }, 'On View'),
      el('div', { class: 'group' }, ...shows.map(showRow)));
    return el('div', { class: 'page' }, scroll);
  }

  const myShowsEmpty = () => el('div', { class: 'empty-state' },
    icon('bookmark'),
    el('div', { class: 'es-title' }, 'No Saved Shows Yet'),
    el('div', { class: 'es-caption' },
      'Check out the Featured tab or the Editor’s Picks\nlist to find something great.'));

  function nearbyPage() {
    const cty = city();
    const sortFrom = (lat, lng) =>
      [...cityShows()].sort((a, b) =>
        haversine(lat, lng, a.venue.lat, a.venue.lng) - haversine(lat, lng, b.venue.lat, b.venue.lng));
    const page = showListPage('Nearby', sortFrom(cty.center.lat, cty.center.lng));
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(pos => {
        const sorted = sortFrom(pos.coords.latitude, pos.coords.longitude);
        const group = page.querySelector('.group');
        if (group) { group.innerHTML = ''; sorted.forEach(s => group.appendChild(showRow(s))); }
      }, () => { /* keep city-center order */ }, { timeout: 5000 });
    }
    return page;
  }

  function listRoot() {
    const row = (label, onclick, iconName) => {
      const r = el('button', { class: 'row', onclick });
      if (iconName) r.appendChild(el('span', { class: 'row-icon', html: ICONS[iconName] }));
      r.appendChild(el('span', { class: 'row-label' }, label));
      const c = icon('chevronRight'); c.classList.add('chev');
      r.appendChild(c);
      return r;
    };

    const sections = [];
    if (museumShows().length) {
      sections.push(el('div', { class: 'museum-banner' },
        el('button', { class: 'row', onclick: () => push('list', museumsPage()) },
          el('span', { class: 'label' }, 'Museums'))));
    }
    sections.push(el('div', { class: 'group' },
      row('My Shows', () => push('list', showListPage('My Shows', savedShows(), { emptyState: myShowsEmpty() })), 'bookmarkFill')));

    const hoodGroup = el('div', { class: 'group' },
      row('All Current Shows', () => push('list', showListPage('All Current Shows', cityShows()))));
    city().neighborhoods.forEach(n =>
      hoodGroup.appendChild(row(n, () => push('list', showListPage(n, neighborhoodShows(n))))));
    sections.push(hoodGroup);

    sections.push(el('div', { class: 'group' },
      row('Opening This Week', () => push('list', showListPage('Opening This Week', openingThisWeek()))),
      row('Closing This Week', () => push('list', showListPage('Closing This Week', closingThisWeek()))),
      row('Editor’s Picks', () => push('list', showListPage('Editor’s Picks', editorsPicks()))),
      row('Nearby', () => push('list', nearbyPage()))));

    const scroll = el('div', { class: 'page-scroll' },
      el('div', { class: 'navrow' },
        el('button', { class: 'nav-textbtn', onclick: openCitySheet }, 'Cities'),
        el('button', { class: 'nav-textbtn', html: ICONS.search, 'aria-label': 'Search', style: 'width:22px;height:22px', onclick: openSearchSheet })),
      el('div', { class: 'large-title' }, city().displayName),
      el('div', { style: 'padding-bottom:96px' }, ...sections));
    const inline = el('div', { class: 'inline-title' }, city().displayName);
    largeTitleScroll(scroll, inline);
    return el('div', { class: 'page' }, inline, scroll);
  }

  // ---------------- sheets: city, search ----------------
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

  function openSearchSheet() {
    const input = el('input', { type: 'search', placeholder: 'Artist, gallery, or show', autocomplete: 'off' });
    const results = el('div');
    const emptyState = () => el('div', { class: 'empty-state' },
      icon('search'), el('div', { class: 'es-title' }, 'Search Shows'));
    results.appendChild(emptyState());

    input.addEventListener('input', () => {
      const q = input.value;
      results.innerHTML = '';
      if (!q.trim()) { results.appendChild(emptyState()); return; }
      const hits = searchShows(q);
      if (!hits.length) { results.appendChild(el('div', { class: 'empty-plain' }, 'No matching shows.')); return; }
      results.append(
        el('div', { class: 'on-view-header' }, 'On View'),
        el('div', { class: 'group' }, ...hits.map(s => {
          const r = showRow(s);
          r.querySelector('.sr-text').onclick = () => {
            const list = cityShows();
            let p;
            p = showDetailPage(list, list.findIndex(x => showId(x) === showId(s)),
              { asSheet: true, onClose: () => p.remove() });
            sheetStack[sheetStack.length - 1].sheet.appendChild(p);
          };
          return r;
        })));
    });

    openSheet(el('div', { class: 'page' },
      el('div', { class: 'search-bar' },
        el('div', { class: 'search-field' }, icon('search'), input),
        el('button', { class: 'search-cancel', onclick: () => closeSheet() }, 'Cancel')),
      el('div', { class: 'page-scroll' }, results)));
    setTimeout(() => input.focus(), 350);
  }

  // ---------------- map tab ----------------
  const MapTab = window.DemoMap({
    getCity: city,
    getShows: () => {
      if (state.mapFilter === 'myShows') return savedShows();
      if (state.mapFilter === 'receptions') return receptionShows();
      return cityShows();
    },
    onPinTap: s => {
      const p = showDetailPage([s], 0, { asSheet: true });
      openSheet(p);
    },
  });

  const mapMenuBtn = document.getElementById('map-filter-btn');
  const mapMenu = document.getElementById('map-menu');
  const FILTERS = [['myShows', 'My Shows'], ['all', 'All Shows'], ['receptions', 'Receptions']];
  function renderMapMenu() {
    mapMenu.innerHTML = '';
    FILTERS.forEach(([key, label]) => {
      mapMenu.appendChild(el('button', {
        onclick: () => { state.mapFilter = key; mapMenu.hidden = true; renderMapMenu(); MapTab.applyFilter(); },
      }, el('span', null, label),
        key === state.mapFilter ? el('span', { html: ICONS.check }) : el('span')));
    });
  }
  renderMapMenu();
  mapMenuBtn.addEventListener('click', () => { mapMenu.hidden = !mapMenu.hidden; });
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
    pagesRoot.featured.innerHTML = '';
    pagesRoot.list.innerHTML = '';
    push('featured', featuredRoot());
    push('list', listRoot());
  }

  // ---------------- boot ----------------
  // iOS Safari: pinches should zoom photos, never the page (viewport flags
  // alone don't stop Safari's page zoom).
  document.addEventListener('gesturestart', e => e.preventDefault());

  rebuildTabs();
  setTab('featured');
})();
