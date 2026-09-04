/* Design lab 2: Lists + Ask. Static phone mockups over the real LA payload;
 * four frames react to input (marked live). Nothing here ships. */
(function () {
  'use strict';

  const { DATA, ICONS, city, LA, showId, venues, displayName, img, dateLine, fmtShort, parseDate, fullAddress,
    el, svg, phone, DEFAULT_TABS, navrow, citiesBtn, title, page, card, feed, bookmark, showRow, rows, chip, searchField,
    T_COLOR, mapPill, mapFrame, mountSections } = window.Lab;

  const qs = new URLSearchParams(location.search);
  const TODAY = qs.get('today') ? parseDate(qs.get('today')) : new Date(2026, 8, 3);
  const ask = window.LabAsk.makeAsk(LA, DATA.venues, { today: TODAY });
  const byId = new Map(LA.map(s => [showId(s), s]));
  const shows = ids => ids.map(id => byId.get(typeof id === 'string' ? id : id.id)).filter(Boolean);
  const galleryCount = list => new Set(list.map(s => s.venueId || s.venue.name)).size;
  const firstSentence = t => (t || '').split(/(?<=[.!?])\s+/)[0];
  const active = s => ask.isActive(s, TODAY);
  const ended = s => { const e = parseDate(s.endDate); return !!e && e < TODAY; };

  // Tab bars: "Lists" replaces "List"; Ask frames add a fourth tab.
  const TABS_LISTS = [['featured', 'star', 'Featured'], ['list', 'listBullet', 'Lists'], ['map', 'map', 'Map']];
  const TABS_GUIDES = [['featured', 'star', 'Featured'], ['list', 'listBullet', 'Guides'], ['map', 'map', 'Map']];
  const TABS_ASK = [...TABS_LISTS, ['ask', 'sparkle', 'Ask']];

  // ---------- canned answers (the retriever runs once, frames read the result) ----------
  const Q = {
    queer: 'What are some current shows by queer artists in the Hollywood neighborhood?',
    paik: 'I really liked a show last week from Nam June Paik, what else similar to that is showing now?',
    weekend: 'Opening receptions this weekend',
    saturday: 'Plan a Saturday in the Arts District',
  };
  const R = Object.fromEntries(Object.entries(Q).map(([k, q]) => [k, ask.ask(q)]));

  // ---------- list store (lab only; never touches savedShowIDs) ----------
  const KEY = 'lab.lists.v1';
  const store = (() => {
    let state = null; const subs = [];
    const read = () => { try { return JSON.parse(localStorage.getItem(KEY)); } catch (e) { return null; } };
    const write = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ignore */ } subs.forEach(f => f()); };
    const seed = () => ({ version: 1, lists: [
      { id: 'l-electronic', name: 'Interactive electronic art galleries', kind: 'user', desc: 'Screens, sound, neon, machines.', entries: ask.ask('interactive electronic art').hits.slice(0, 7).map(h => ({ type: 'show', id: showId(h.show) })) },
      { id: 'l-chinatown', name: 'Chinatown walk', kind: 'user', desc: 'Chung King Road and around, one afternoon.', entries: LA.filter(s => s.venue.neighborhood === 'Chinatown/East LA' && active(s)).slice(0, 5).map(s => ({ type: 'show', id: showId(s) })) },
    ], recentAsks: Object.values(Q).map(q => ({ q, n: ask.ask(q).hits.length })) });
    state = read(); if (!state || state.version !== 1) { state = seed(); write(); }
    const find = id => state.lists.find(l => l.id === id);
    return {
      get: () => state,
      lists: () => state.lists,
      find,
      create(name, entries, extra) { const l = { id: 'l-' + Date.now().toString(36), name, kind: 'user', entries: entries || [], ...(extra || {}) }; state.lists.unshift(l); write(); return l; },
      rename(id, name) { const l = find(id); if (l) { l.name = name; write(); } },
      remove(id) { state.lists = state.lists.filter(l => l.id !== id); write(); },
      toggleEntry(id, entry) { const l = find(id); if (!l) return; const i = l.entries.findIndex(e => e.id === entry.id); if (i >= 0) l.entries.splice(i, 1); else l.entries.push(entry); write(); },
      has: (id, showIdStr) => { const l = find(id); return !!l && l.entries.some(e => e.id === showIdStr); },
      pushAsk(q, n) { state.recentAsks = [{ q, n }, ...state.recentAsks.filter(a => a.q !== q)].slice(0, 6); write(); },
      reset() { try { localStorage.removeItem(KEY); } catch (e) { /* ignore */ } state = seed(); write(); },
      subscribe: f => subs.push(f),
    };
  })();

  // My Shows = the app's saved set when present, else a fixed six.
  const savedIds = (() => {
    try { const a = JSON.parse(localStorage.getItem('savedShowIDs') || '[]'); if (a.length) return a; } catch (e) { /* ignore */ }
    return ['deitch-urs-fischer', 'lee-mullican-silent-shades', 'broad-yoko-ono', 'art-practice-rahim-fortune', 'the-page-before-me', 'ghebaly-patrick-jackson-all-signs-fail'].map(s => 'los-angeles/' + s);
  })();
  const myShows = () => shows(savedIds);
  const picksList = () => LA.filter(s => s.editorsPick);

  // System + curated lists are computed, not stored.
  const SYSTEM = () => [
    { id: 'all', name: `All shows in ${city.displayName}`, kind: 'system', shows: [LA[3], LA[9], LA[15], LA[21], ...LA], sub: `${LA.length} shows · ${venues.length} venues · filters apply`, n: LA.length },
    { id: 'mine', name: 'My Shows', kind: 'system', shows: myShows(), sub: `${myShows().length} shows · saved by you` },
    { id: 'picks', name: "Editor's Picks", kind: 'curated', shows: picksList(), sub: `${picksList().length} shows · Gallery Browser` },
  ];
  const CURATED = () => [
    { id: 'c-picks', name: "Editor's Picks", by: 'Gallery Browser', shows: picksList(), desc: 'The five shows we would send a visitor to first.' },
    { id: 'c-arts', name: 'Arts District walk', by: 'Gallery Browser', shows: R.saturday.route.stops.map(s => s.show), desc: 'Five galleries you can do on foot in an afternoon.' },
    { id: 'c-museums', name: 'Museums this month', by: 'Gallery Browser', shows: LA.filter(s => s.venue.isMuseum && active(s)).slice(0, 8), desc: 'What the big rooms have up.' },
    { id: 'c-openings', name: 'Openings this weekend', by: 'Gallery Browser', shows: R.weekend.hits.map(h => h.show), desc: 'Receptions Friday to Sunday.' },
    { id: 'c-video', name: 'Video art now', by: 'Gallery Browser', shows: ask.ask('video art').hits.slice(0, 8).map(h => h.show), desc: 'Moving image across the city.' },
  ];
  const userList = l => ({ ...l, shows: shows(l.entries), by: 'You' });

  // ---------- components ----------
  const collage = (list, cls) => {
    const imgs = list.slice(0, 4);
    return el('div', { class: 'collage' + (imgs.length < 4 ? ' n' + imgs.length : '') + (cls ? ' ' + cls : '') },
      ...(imgs.length ? imgs.map(s => el('img', { src: img(s), alt: '' })) : [el('div', { class: 'collage-empty' }, svg('listBullet'))]));
  };
  const meta = l => `${l.shows.length} shows · ${galleryCount(l.shows)} ${galleryCount(l.shows) === 1 ? 'gallery' : 'galleries'}`;
  const libRow = (l, opts) => el('button', { class: 'lib-row' + (opts && opts.cls ? ' ' + opts.cls : '') },
    opts && opts.icon ? el('span', { class: 'lib-ico' }, svg(opts.icon)) : collage(l.shows, 'sm'),
    el('span', { class: 'lib-text' }, el('span', { class: 'lib-name' }, l.name), el('span', { class: 'lib-sub' }, l.sub || meta(l))),
    opts && opts.trail !== undefined ? opts.trail : svg('chevronRight'));
  const libTile = (l, opts) => el('button', { class: 'lib-tile' + (opts && opts.cls ? ' ' + opts.cls : '') },
    collage(l.shows), el('span', { class: 'lib-name' }, l.name), el('span', { class: 'lib-sub' }, opts && opts.sub ? opts.sub : (l.by && l.by !== 'You' ? `${l.by} · ${l.shows.length}` : meta(l))),
    opts && opts.save ? el('span', { class: 'tile-save' }, svg('plus'), 'Save') : null);
  const grid = (...tiles) => el('div', { class: 'lib-grid' }, ...tiles);
  const shelf = (...tiles) => el('div', { class: 'lib-shelf' }, ...tiles);
  const header = (t, trail) => el('div', { class: 'lib-header' }, el('span', null, t), trail || null);
  const iconBtn = (name, on) => el('button', { class: 'icon-btn' + (on ? ' on' : '') }, svg(name));
  const backBtn = () => el('button', { class: 'circle-btn', style: 'background:var(--field)' }, svg('chevronLeft'));
  const ctxBar = (icon, text, count, opts) => el('div', { class: 'ctx-bar' + (opts && opts.cls ? ' ' + opts.cls : '') },
    el('span', { class: 'ctx-ico' }, svg(icon)), el('span', { class: 'ctx-text' }, text), el('span', { class: 'ctx-n' }, count),
    ...((opts && opts.actions) || []).map(a => el('button', { class: 'ctx-act' }, a)), el('button', { class: 'ctx-x' }, svg('xmark')));
  const askRow = (a, opts) => el('button', { class: 'lib-row ask-row' }, el('span', { class: 'lib-ico' }, svg('sparkle')),
    el('span', { class: 'lib-text' }, el('span', { class: 'lib-name' }, a.q.replace(/[?.]$/, '')), el('span', { class: 'lib-sub' }, `${a.n} shows · ${opts && opts.when ? opts.when : 'asked today'}`)), svg('chevronRight'));

  const heroActions = (...acts) => el('div', { class: 'lh-actions' }, ...acts.map(([ic, label, on]) =>
    el('button', { class: 'act' + (on ? ' on' : '') }, el('span', { class: 'act-ico' }, svg(ic)), el('span', null, label))));
  function listHero(l, opts) {
    const o = opts || {};
    return el('div', { class: 'list-hero' },
      collage(l.shows, 'hero'),
      el('div', { class: 'lh-name' }, l.name),
      el('div', { class: 'lh-meta' }, o.meta || `${l.by === 'You' ? 'by You' : l.by} · ${meta(l)}`),
      l.desc ? el('div', { class: 'lh-desc' }, l.desc) : null,
      o.actions || null);
  }
  const rowSub = s => `${dateLine(s, fmtShort)}${s.venue.neighborhood ? ' · ' + s.venue.neighborhood : ''}`;

  // ---------- chat components ----------
  const userMsg = t => el('div', { class: 'msg user' }, t);
  const aiMsg = (...kids) => el('div', { class: 'msg ai' }, el('span', { class: 'ai-ico' }, svg('sparkle')), el('div', { class: 'ai-body' }, ...kids));
  const srcTag = g => el('span', { class: 'src ' + g.field }, g.source);
  function entry(h, opts) {
    const o = opts || {}; const s = h.show;
    const g = h.grounding && h.grounding[0];
    return el('div', { class: 'ans-entry' + (o.open ? ' open' : '') },
      el('img', { class: 'ans-thumb', src: img(s), alt: '' }),
      el('div', { class: 'ans-text' },
        el('div', { class: 'ans-name' }, displayName(s)),
        el('div', { class: 'ans-venue' }, `${s.venue.name} · ${s.venue.neighborhood}`),
        o.why !== false && !o.open && g ? el('div', { class: 'why' }, srcTag(g), el('span', null, g.snippet)) : null,
        o.line ? el('div', { class: 'why' }, o.line) : null,
        o.open && h.grounding ? el('div', { class: 'grounding' }, ...h.grounding.map(gg => el('div', { class: 'g-row' }, srcTag(gg), el('span', null, gg.snippet)))) : null),
      o.trail !== undefined ? o.trail : null);
  }
  const ansActions = (...acts) => el('div', { class: 'ans-actions' }, ...acts.map(([ic, label, primary]) => el('button', { class: 'ans-act' + (primary ? ' primary' : '') }, svg(ic), label)));
  const followups = (...labels) => el('div', { class: 'followups' }, ...labels.map(t => chip(t, false, { cls: 'outline' })));
  function answerCard(res, opts) {
    const o = opts || {}; const n = o.limit || 5;
    return el('div', { class: 'answer-card' },
      el('div', { class: 'ans-head' }, el('span', { class: 'ans-title' }, o.title || res.listDraft.name), el('span', { class: 'ans-count' }, `${res.hits.length} shows`)),
      ...res.hits.slice(0, n).map((h, i) => entry(h, { open: o.openIndex === i, trail: o.trail ? o.trail(h) : undefined })),
      res.hits.length > n ? el('button', { class: 'ans-more' }, `Show all ${res.hits.length}`) : null,
      o.actions !== false ? ansActions(['bookmark', 'Save as list', true], ['map', 'Show on Map'], ['listBullet', 'Open list']) : null);
  }
  const askBar = (value, opts) => el('div', { class: 'ask-bar' + (opts && opts.cls ? ' ' + opts.cls : '') },
    el('div', { class: 'ask-field' }, el('input', { type: 'text', placeholder: 'Ask about shows, galleries, artists…', value: value || '', autocomplete: 'off' }), el('button', { class: 'ask-send' }, svg('send'))));
  const chatPage = (...kids) => el('div', { class: 'page ask-page' }, el('div', { class: 'page-scroll chat' }, ...kids));
  const asked = q => `Asked ${TODAY.toLocaleDateString('en-US', { weekday: 'long' })}`;

  // ================= L · Lists =================
  function libraryPage(vocab) {
    const isGuides = vocab === 'guides';
    const sys = SYSTEM();
    if (isGuides) { sys[0].name = 'Guide to everything in LA'; sys[2].name = "Editor's Guide"; }
    const mine = store.lists().map(userList);
    const curated = CURATED().slice(1, 5);
    const body = [
      el('div', { class: 'lib-pinned' }, ...sys.map((l, i) => libRow(l, { cls: 'pinned' + (i === 0 ? ' all' : '') }))),
    ];
    const yours = [header(isGuides ? 'Your guides' : 'Your lists', el('button', { class: 'lib-new' }, svg('plus'), 'New')), grid(...mine.map(l => libTile(l)))];
    const fromUs = [header(isGuides ? 'Guides from Gallery Browser' : 'Curated'), shelf(...curated.map(l => libTile(l, { cls: 'small', save: true })))];
    if (isGuides) body.push(...fromUs, ...yours); else body.push(...yours, ...fromUs);
    body.push(header('Recent asks'), el('div', { class: 'group' }, ...store.get().recentAsks.slice(0, 3).map(a => askRow(a))));
    return page(navrow(citiesBtn(), iconBtn('sliders')), title(isGuides ? 'Guides' : 'Lists'), ...body);
  }
  function L1a() {
    const ph = phone('L1a', 'list', { tabs: TABS_LISTS }, libraryPage('lists'));
    store.subscribe(() => { const scr = ph.querySelector('.screen'); scr.innerHTML = ''; scr.appendChild(libraryPage('lists')); });
    return ph;
  }
  function L1b() { return phone('L1b', 'list', { tabs: TABS_GUIDES }, libraryPage('guides')); }
  function L2() {
    const list = LA.filter(s => s.featured && !s.venue.isMuseum && active(s));
    const summary = el('div', { class: 'lh-inline' }, el('span', null, `${list.length} shows`), el('span', { class: 'dot' }), el('span', null, 'Galleries · Featured · Active'));
    return phone('L2', 'list', { tabs: TABS_LISTS }, page(navrow(backBtn(), iconBtn('sliders', true)), title(`All shows`), summary, rows(list.slice(0, 12), () => ({}))));
  }
  function L3a() {
    const l = { name: 'My Shows', by: 'You', shows: myShows(), desc: null };
    return phone('L3a', 'list', { tabs: TABS_LISTS }, page(navrow(backBtn(), iconBtn('share')),
      listHero(l, { actions: heroActions(['star', 'Featured'], ['map', 'Map'], ['share', 'Share'], ['pencil', 'Edit']) }),
      rows(l.shows, s => ({ saved: true, sub: rowSub(s) }))));
  }
  function L3b() {
    const l = CURATED()[0];
    return phone('L3b', 'list', { tabs: TABS_LISTS }, page(navrow(backBtn(), iconBtn('share')),
      listHero(l, { meta: `Gallery Browser · updated ${TODAY.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} · ${l.shows.length} shows`,
        actions: el('div', { class: 'lh-cta' }, el('button', { class: 'capsule-btn' }, svg('plus'), el('span', null, 'Save to Library')), el('button', { class: 'capsule-btn ghost-cap' }, svg('map'), el('span', null, 'Map'))) }),
      el('div', { class: 'group list-results' }, ...l.shows.map(s => {
        const r = showRow(s, { sub: null, trail: null, cls: 'note' });
        r.querySelector('.sr-text').appendChild(el('div', { class: 'ed-note' }, firstSentence(s.description)));
        return r;
      }))));
  }
  function L3c() {
    const gone = LA.filter(ended).slice(0, 2);
    const live = LA.filter(s => s.featured && active(s) && s.venue.neighborhood === 'West Hollywood/Fairfax').slice(0, 4);
    const l = { name: "Dana's LA weekend", by: 'Dana', shows: [...live, ...gone], desc: 'Things I want to hit before the 12th. Add yours.' };
    const banner = el('div', { class: 'share-banner' }, svg('link'), el('span', null, el('b', null, 'Shared by Dana'), ` · ${live.length} on view, ${gone.length} no longer on view`));
    return phone('L3c', 'list', { tabs: TABS_LISTS }, page(navrow(backBtn(), iconBtn('share')), banner,
      listHero(l, { meta: `by Dana · ${l.shows.length} shows · ${galleryCount(l.shows)} galleries`,
        actions: el('div', { class: 'lh-cta' }, el('button', { class: 'capsule-btn' }, svg('plus'), el('span', null, 'Save a copy')), el('button', { class: 'capsule-btn ghost-cap' }, svg('map'), el('span', null, 'Open in Map'))) }),
      rows(l.shows, s => ({ sub: ended(s) ? `Closed ${fmtShort(parseDate(s.endDate))}` : rowSub(s), cls: ended(s) ? 'gone' : '', trail: ended(s) ? el('span', { class: 'gone-tag' }, 'closed') : bookmark(false) }))));
  }
  function L3d() {
    const id = 'l-electronic';
    const render = () => {
      const l = userList(store.find(id) || store.lists()[0]);
      const name = el('input', { class: 'edit-name', value: l.name, oninput: e => store.rename(l.id, e.target.value) });
      const desc = el('input', { class: 'edit-desc', value: l.desc || '', placeholder: 'Add a description' });
      const list = el('div', { class: 'group list-results' }, ...l.shows.map(s => showRow(s, { sub: rowSub(s), lead: el('span', { class: 'grip' }, svg('grip')),
        trail: el('button', { class: 'minus', onclick: () => store.toggleEntry(l.id, { type: 'show', id: showId(s) }) }, svg('minus')) })),
        el('button', { class: 'row add-row' }, el('span', { class: 'row-icon' }, svg('plus')), el('span', { class: 'row-label' }, 'Add shows')));
      return page(navrow(el('button', { class: 'nav-textbtn' }, 'Cancel'), el('button', { class: 'nav-textbtn bold' }, 'Done')),
        el('div', { class: 'list-hero edit' }, collage(l.shows, 'hero'), el('button', { class: 'change-cover' }, 'Change cover'), name, desc),
        list, el('button', { class: 'delete-list' }, 'Delete list'));
    };
    const ph = phone('L3d', 'list', { tabs: TABS_LISTS }, render());
    store.subscribe(() => { const scr = ph.querySelector('.screen'); if (document.activeElement && scr.contains(document.activeElement)) return; scr.innerHTML = ''; scr.appendChild(render()); });
    return ph;
  }
  function L4() {
    const s = LA[0];
    const behind = el('div', { class: 'page' }, el('div', { class: 'page-scroll', style: 'padding-top:0' },
      el('div', { class: 'detail-hero' }, el('img', { src: img(s), alt: '', style: 'width:100%;height:340px;object-fit:cover;display:block' }),
        el('div', { class: 'detail-topbar' }, el('button', { class: 'circle-btn' }, svg('chevronLeft')))),
      el('div', { class: 'detail-body' }, el('div', { class: 'detail-artist' }, s.artist), el('div', { class: 'detail-title' }, s.title),
        el('div', { class: 'detail-dates' }, dateLine(s)), el('button', { class: 'capsule-btn detail-save saved' }, svg('check'), el('span', null, 'Added to My Shows')))));
    const toast = el('div', { class: 'toast' }, svg('bookmarkFill'), el('span', null, 'Saved to My Shows'), el('button', null, 'Add to list…'));
    const entryOf = { type: 'show', id: showId(s) };
    let creating = false, draft = '';
    const sheet = el('div', { class: 'sheet-static add-sheet' });
    const render = () => {
      sheet.innerHTML = '';
      const lists = store.lists();
      const rowsEl = lists.map(l => el('button', { class: 'row', onclick: () => store.toggleEntry(l.id, entryOf) },
        collage(shows(l.entries), 'xs'), el('span', { class: 'row-label' }, l.name, el('span', { class: 'row-sub' }, `${l.entries.length} shows`)),
        store.has(l.id, entryOf.id) ? el('span', { class: 'check on' }, svg('check')) : el('span', { class: 'check' })));
      const newRow = creating
        ? el('div', { class: 'row new-list' }, el('span', { class: 'row-icon' }, svg('plus')),
            el('input', { class: 'new-name', placeholder: 'List name', value: draft, oninput: e => { draft = e.target.value; } }),
            el('button', { class: 'create', onclick: () => { if (draft.trim()) { store.create(draft.trim(), [entryOf]); draft = ''; creating = false; render(); } } }, 'Create'))
        : el('button', { class: 'row new-list', onclick: () => { creating = true; render(); sheet.querySelector('.new-name').focus(); } }, el('span', { class: 'row-icon' }, svg('plus')), el('span', { class: 'row-label' }, 'New list…'));
      sheet.append(
        el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Add to list'), el('button', { class: 'sheet-close' }, svg('xmark'))),
        el('div', { class: 'sheet-scroll' },
          el('div', { class: 'add-subject' }, el('img', { src: img(s), alt: '' }), el('span', null, el('b', null, displayName(s)), el('br'), s.venue.name)),
          el('div', { class: 'group' }, el('div', { class: 'row static' }, el('span', { class: 'lib-ico blue' }, svg('bookmarkFill')), el('span', { class: 'row-label' }, 'My Shows', el('span', { class: 'row-sub' }, 'always')), el('span', { class: 'check on' }, svg('check'))), ...rowsEl, newRow)),
        el('div', { class: 'sheet-foot' }, el('button', { class: 'capsule-btn' }, 'Done')));
    };
    render();
    store.subscribe(() => { if (!creating) render(); });
    window.__labPrep = window.__labPrep || {};
    window.__labPrep.L4 = async () => { creating = true; draft = 'Ceramics to see'; render(); };
    return phone('L4', 'list', { tabs: TABS_LISTS }, behind, toast, el('div', { class: 'sheet-backdrop open' }), sheet);
  }
  function L5a() {
    const mine = store.lists();
    const listRow = (label, on, sub) => el('div', { class: 'row' }, el('span', { class: 'row-label' }, label, sub ? el('span', { class: 'row-sub' }, sub) : null), on ? el('span', { class: 'check on' }, svg('check')) : el('span'));
    const toggleRow = (label, on) => el('div', { class: 'row' }, el('span', { class: 'row-label' }, label), el('span', { class: 'switch' + (on ? ' on' : '') }));
    const seg = (opts, on) => el('div', { class: 'seg-row' }, ...opts.map(o => el('button', { class: o === on ? 'on' : '' }, o)));
    const sheet = el('div', { class: 'sheet-static' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Filters'), el('button', { class: 'sheet-close' }, svg('xmark'))),
      el('div', { class: 'sheet-scroll' }, searchField(),
        el('div', { class: 'group-header' }, 'List'),
        el('div', { class: 'group' }, listRow('All shows', false), listRow('My Shows', false, `${myShows().length}`), listRow("Editor's Picks", false, '5'), ...mine.map(l => listRow(l.name, l.id === 'l-electronic', String(l.entries.length)))),
        el('div', { class: 'group-header' }, 'Show'),
        el('div', { class: 'group' }, toggleRow('Active shows', true), toggleRow('Upcoming receptions', false)),
        el('div', { class: 'group-header' }, 'Show Rank'), seg(['All Shows', 'Featured', "Editor's Picks"], 'All Shows'),
        el('div', { class: 'group-header' }, 'Gallery Rank'), seg(['All Galleries', 'Notable', 'Top Ranked'], 'All Galleries')),
      el('div', { class: 'sheet-foot' }, el('button', { class: 'ghost' }, 'Clear'), el('button', { class: 'capsule-btn' }, `Show ${store.find('l-electronic').entries.length} shows`)));
    return phone('L5a', 'featured', { tabs: TABS_LISTS }, page(navrow(citiesBtn(), iconBtn('sliders', true)), title(), feed(LA.slice(0, 2))), el('div', { class: 'sheet-backdrop open' }), sheet);
  }
  function L5b() {
    const l = userList(store.find('l-electronic'));
    return phone('L5b', 'featured', { tabs: TABS_LISTS }, page(navrow(citiesBtn(), iconBtn('sliders', true)), title(),
      ctxBar('listBullet', l.name, `${l.shows.length}`), feed(l.shows.slice(0, 3))));
  }
  function stopGeo(list, dimAll) {
    const ids = new Set(list.map(s => s.venueId || s.venue.name));
    const feats = venues.map(g => {
      const on = g.shows.some(s => ids.has(s.venueId || s.venue.name));
      return { type: 'Feature', geometry: { type: 'Point', coordinates: [g.venue.lng, g.venue.lat] }, properties: { name: g.venue.name, tier: on ? 1 : 3, dim: on ? 0 : 1, on: on ? 1 : 0 } };
    });
    return { type: 'FeatureCollection', features: feats };
  }
  function L5c() {
    const l = userList(store.find('l-electronic'));
    const geo = stopGeo(l.shows);
    const layers = (map, h) => {
      map.addLayer({ id: 'dim', type: 'circle', source: 'v', filter: ['==', ['get', 'on'], 0], paint: { 'circle-color': 'rgba(150,150,158,0.25)', 'circle-radius': 4 } });
      map.addLayer({ id: 'on', type: 'circle', source: 'v', filter: ['==', ['get', 'on'], 1], paint: { 'circle-color': T_COLOR[1], 'circle-radius': 10.5, 'circle-stroke-width': 1.5, 'circle-stroke-color': '#fff' } });
      const lab = h.label({}); lab.filter = ['==', ['get', 'on'], 1]; map.addLayer(lab);
    };
    return mapFrame('L5c', 'custom', [
      el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter')),
      el('div', { class: 'ctx-pill' }, svg('listBullet'), el('span', null, l.name), el('span', { class: 'ctx-n' }, `${galleryCount(l.shows)} galleries`), el('button', null, svg('xmark')))],
      { features: geo, layers, zoom: 10.6, center: [-118.30, 34.06], tabs: TABS_LISTS });
  }
  function L6() {
    const l = { name: 'My Shows', by: 'You', shows: myShows() };
    const behind = page(navrow(backBtn(), iconBtn('share', true)), listHero(l, { actions: heroActions(['star', 'Featured'], ['map', 'Map'], ['share', 'Share', true], ['pencil', 'Edit']) }), rows(l.shows.slice(0, 3), s => ({ saved: true, sub: rowSub(s) })));
    const app = (ic, label, cls) => el('button', { class: 'share-app' }, el('span', { class: 'sa-ico ' + (cls || '') }, svg(ic)), el('span', null, label));
    const sheet = el('div', { class: 'share-sheet' },
      el('div', { class: 'share-preview' }, collage(l.shows, 'sm'), el('span', { class: 'lib-text' }, el('span', { class: 'lib-name' }, l.name), el('span', { class: 'lib-sub' }, `${l.shows.length} shows · gallerybrowser.app/l/8f3k`)), el('button', { class: 'sheet-close' }, svg('xmark'))),
      el('div', { class: 'share-apps' }, app('message', 'Messages', 'green'), app('copy', 'Copy link', 'grey'), app('sparkle', 'AirDrop', 'blue'), app('share', 'More', 'grey')),
      el('div', { class: 'share-note' }, 'Anyone with the link sees the list as it is today. Shows that close drop out on their side too.'));
    return phone('L6', 'list', { tabs: TABS_LISTS }, behind, el('div', { class: 'sheet-backdrop open' }), sheet);
  }
  function L7() {
    const c = CURATED();
    const hero = el('button', { class: 'guide-hero' }, collage(c[0].shows, 'wide'), el('div', { class: 'gh-text' }, el('div', { class: 'gh-kicker' }, 'This week'), el('div', { class: 'gh-name' }, c[0].name), el('div', { class: 'gh-sub' }, c[0].desc)));
    return phone('L7', 'list', { tabs: TABS_GUIDES }, page(navrow(backBtn(), el('span')), title('Guides for LA'), hero,
      header('Walks'), shelf(libTile(c[1], { cls: 'small', save: true }), libTile(userList(store.find('l-chinatown')), { cls: 'small', save: true, sub: 'Gallery Browser · 5' })),
      header('This weekend'), shelf(libTile(c[3], { cls: 'small', save: true }), libTile(c[2], { cls: 'small', save: true })),
      header('By medium'), shelf(libTile(c[4], { cls: 'small', save: true }), libTile(userList(store.find('l-electronic')), { cls: 'small', save: true, sub: 'Gallery Browser · 7' }))));
  }

  // ================= A · Ask =================
  const PROMPTS = [Q.weekend, 'What is closing this week?', Q.saturday, 'Video art on view now', Q.queer.replace('What are some current shows by', 'Shows by')];
  function idleBody() {
    return el('div', { class: 'ask-idle' },
      el('div', { class: 'ask-greet' }, el('span', { class: 'ai-ico big' }, svg('sparkle')), el('div', { class: 'ask-h' }, `Ask about ${city.displayName}`), el('div', { class: 'ask-sub' }, `${LA.length} shows, ${venues.length} venues, artist rosters. Answers become lists you can save.`)),
      el('div', { class: 'group-header' }, 'Try'),
      el('div', { class: 'group prompts' }, ...PROMPTS.map(p => el('button', { class: 'row prompt-row' }, el('span', { class: 'row-label' }, p), svg('chevronRight')))),
      el('div', { class: 'group-header' }, 'Recent'),
      el('div', { class: 'group' }, ...store.get().recentAsks.slice(0, 2).map(a => askRow(a))));
  }
  function A1a() { return phone('A1a', 'ask', { tabs: TABS_ASK }, chatPage(navrow(citiesBtn(), iconBtn('listBullet')), title('Ask'), idleBody()), askBar('')); }
  function A1b() {
    const field = el('button', { class: 'ask-capsule' }, svg('sparkle'), el('span', null, 'Ask about shows…'));
    return phone('A1b', 'featured', { tabs: TABS_LISTS }, page(navrow(citiesBtn(), iconBtn('sliders')), title(), field, feed(LA.filter(s => s.featured).slice(0, 3))));
  }
  function A1c() {
    const q = 'video art in hollywood?';
    const field = el('div', { class: 'search-bar' }, el('div', { class: 'search-field ask-mode' }, svg('sparkle'), el('input', { type: 'search', value: q, autocomplete: 'off' })));
    const askRowEl = el('button', { class: 'ask-affordance' }, svg('sparkle'), el('span', null, el('b', null, 'Ask'), ` “${q}”`), svg('chevronRight'));
    const seg = (opts, on) => el('div', { class: 'seg-row' }, ...opts.map(o => el('button', { class: o === on ? 'on' : '' }, o)));
    const sheet = el('div', { class: 'sheet-static' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Filters'), el('button', { class: 'sheet-close' }, svg('xmark'))),
      el('div', { class: 'sheet-scroll' }, field, askRowEl,
        el('div', { class: 'search-hint' }, 'Plain words filter the list. A question asks.'),
        el('div', { class: 'group-header' }, 'Show'), el('div', { class: 'group' }, el('div', { class: 'row' }, el('span', { class: 'row-label' }, 'Active shows'), el('span', { class: 'switch on' })), el('div', { class: 'row' }, el('span', { class: 'row-label' }, 'Upcoming receptions'), el('span', { class: 'switch' }))),
        el('div', { class: 'group-header' }, 'Show Rank'), seg(['All Shows', 'Featured', "Editor's Picks"], 'Featured')),
      el('div', { class: 'sheet-foot' }, el('button', { class: 'ghost' }, 'Clear'), el('button', { class: 'capsule-btn' }, 'Show 3 shows')));
    return phone('A1c', 'list', { tabs: TABS_LISTS }, page(navrow(citiesBtn(), iconBtn('sliders', true)), title('Lists'), el('div', { class: 'lib-pinned' }, ...SYSTEM().slice(0, 2).map(l => libRow(l, { cls: 'pinned' })))), el('div', { class: 'sheet-backdrop open' }), sheet);
  }
  function A1d() {
    return phone('A1d', 'list', { tabs: TABS_LISTS }, libraryPage('lists'), el('button', { class: 'fab' }, svg('sparkle')));
  }
  function A2a() {
    const r = R.queer;
    const intro = `Nothing on view in Hollywood mentions queer artists or themes. Widening to all of LA, ${r.hits.length} shows explicitly do:`;
    return phone('A2a', 'ask', { tabs: TABS_ASK }, chatPage(navrow(citiesBtn(), iconBtn('listBullet')), el('div', { class: 'chat-body' },
      userMsg(Q.queer),
      aiMsg(el('p', null, intro), followups('✓ Widened to all neighborhoods'), answerCard(r, { title: 'Queer artists and themes · LA', limit: 3 }),
        el('p', { class: 'ai-note' }, 'Matched on the words in show descriptions and gallery blurbs. The artist roster has no identity data, so this is what galleries chose to say.')),
      followups('Only galleries', 'Add West Hollywood', 'On view this weekend'))), askBar(''));
  }
  function A2b() {
    const r = R.paik;
    return phone('A2b', 'ask', { tabs: TABS_ASK }, chatPage(navrow(citiesBtn(), iconBtn('listBullet')), el('div', { class: 'chat-body' },
      userMsg(Q.paik),
      aiMsg(el('p', null, `Reading that as ${r.constraints.reading}. ${r.hits.length} shows on view now lean that way, strongest first:`),
        answerCard(r, { title: 'Like Nam June Paik', limit: 3 }),
        el('p', { class: 'ai-note' }, 'The only Paik show in the app right now is at WATARI-UM in Tokyo, through November.')),
      followups('Not what I meant: Fluxus', 'Korean artists', 'Sculpture with screens'))), askBar(''));
  }
  function A2c() {
    const r = R.weekend;
    const day = g => el('div', { class: 'ans-day' }, el('div', { class: 'ans-dayname' }, g.day),
      ...g.items.map(i => entry(i, { why: false, line: el('span', { class: 'rec-line' + (i.flag ? ' flag' : '') }, i.show.reception.replace(/, 2026/, ''), i.flag ? el('b', null, ` · ${i.flag}`) : null) })));
    return phone('A2c', 'ask', { tabs: TABS_ASK }, chatPage(navrow(citiesBtn(), iconBtn('listBullet')), el('div', { class: 'chat-body' },
      userMsg(Q.weekend),
      aiMsg(el('p', null, `${r.hits.length} receptions Friday to Sunday, from each gallery's own listing. Two are events, not openings.`),
        el('div', { class: 'answer-card' }, ...r.groups.map(day), ansActions(['bookmark', 'Save as list', true], ['map', 'Show on Map']))),
      followups('Only Saturday', 'Near me', 'Add to calendar'))), askBar(''));
  }
  const routeStops = route => el('div', { class: 'route' }, ...route.stops.map((st, i) => el('div', { class: 'route-stop' },
    el('span', { class: 'stop-n' }, String(i + 1)),
    el('div', { class: 'stop-text' }, el('div', { class: 'stop-name' }, st.show.venue.name), el('div', { class: 'stop-show' }, displayName(st.show)),
      el('div', { class: 'stop-meta' }, `${st.arrive} · ${st.note}`, i ? ` · ${st.walkMin} min walk` : '')))));
  function A2d() {
    const r = R.saturday, rt = r.route;
    return phone('A2d', 'ask', { tabs: TABS_ASK }, chatPage(navrow(citiesBtn(), iconBtn('listBullet')), el('div', { class: 'chat-body' },
      userMsg(Q.saturday),
      aiMsg(el('p', null, `${rt.stops.length} stops, ${rt.totalKm.toFixed(1)} km on foot, ${rt.start} to about ${rt.end}. Ordered by walking distance from the top-ranked gallery, checked against Saturday hours.`),
        el('div', { class: 'answer-card' }, routeStops(rt),
          rt.skipped.length ? el('p', { class: 'ai-note' }, `Skipped ${rt.skipped.map(s => `${s.show.venue.name.split('/')[0].trim()} (${s.why})`).join(', ')}.`) : null,
          ansActions(['bookmark', 'Save as list', true], ['map', 'Show route on Map'])),
      ), followups('Start at 10', 'Add lunch', 'Museums too'))), askBar(''));
  }
  function A2e() {
    const body = el('div', { class: 'chat-body' });
    const input = el('input', { type: 'text', placeholder: 'Ask about shows, galleries, artists…', autocomplete: 'off' });
    const send = el('button', { class: 'ask-send' });
    send.innerHTML = ICONS.send;
    const bar = el('div', { class: 'ask-bar' }, el('div', { class: 'ask-field' }, input, send));
    const scroll = el('div', { class: 'page-scroll chat' }, navrow(citiesBtn(), iconBtn('listBullet')), body);
    const showIdle = () => { body.innerHTML = ''; body.append(title('Ask'), idleBody()); body.querySelectorAll('.prompt-row').forEach(b => b.addEventListener('click', () => run(b.textContent))); };
    const run = q => {
      q = (q || '').trim(); if (!q) return;
      const r = ask.ask(q);
      store.pushAsk(q, r.hits.length);
      body.innerHTML = '';
      const saveBtn = el('button', { class: 'ans-act primary', onclick: () => { const l = store.create(r.listDraft.name, r.listDraft.entries, { kind: 'ask', query: q }); saveBtn.replaceWith(el('span', { class: 'ans-act done' }, svg('check'), `Saved “${l.name}”`)); } }, svg('bookmark'), 'Save as list');
      const lead = r.hits.length === 0 ? 'Nothing in the current payload matches that. Try a medium, an artist, a neighborhood, or a day.'
        : (r.relaxed === 'neighborhood' ? `Nothing in ${r.constraints.hoods.join(' / ')} matches. Across LA, ${r.hits.length} shows do:`
        : r.relaxed === 'time' ? `Nothing in that window. Without the date, ${r.hits.length} shows match:`
        : `${r.hits.length} ${r.hits.length === 1 ? 'show' : 'shows'}${r.interpretation.reading ? ' · ' + r.interpretation.reading : ''}:`);
      const card = r.route ? el('div', { class: 'answer-card' }, routeStops(r.route), el('div', { class: 'ans-actions' }, saveBtn, el('button', { class: 'ans-act' }, svg('map'), 'Show on Map')))
        : r.groups ? el('div', { class: 'answer-card' }, ...r.groups.map(g => el('div', { class: 'ans-day' }, el('div', { class: 'ans-dayname' }, g.day), ...g.items.map(i => entry(i, { why: false, line: el('span', { class: 'rec-line' + (i.flag ? ' flag' : '') }, i.show.reception.replace(/, 2026/, ''), i.flag ? el('b', null, ` · ${i.flag}`) : null) })))), el('div', { class: 'ans-actions' }, saveBtn, el('button', { class: 'ans-act' }, svg('map'), 'Show on Map')))
        : r.hits.length ? el('div', { class: 'answer-card' }, el('div', { class: 'ans-head' }, el('span', { class: 'ans-title' }, r.listDraft.name), el('span', { class: 'ans-count' }, `${r.hits.length} shows`)),
            ...r.hits.slice(0, 6).map(h => entry(h)), r.hits.length > 6 ? el('button', { class: 'ans-more' }, `Show all ${r.hits.length}`) : null,
            el('div', { class: 'ans-actions' }, saveBtn, el('button', { class: 'ans-act' }, svg('map'), 'Show on Map'), el('button', { class: 'ans-act' }, svg('listBullet'), 'Open list')))
        : null;
      body.append(userMsg(q), aiMsg(el('p', null, lead), card));
      scroll.scrollTop = scroll.scrollHeight;
      input.value = '';
    };
    send.addEventListener('click', () => run(input.value));
    input.addEventListener('keydown', e => { if (e.key === 'Enter') run(input.value); });
    showIdle();
    window.__labPrep = window.__labPrep || {};
    window.__labPrep.A2e = async () => run('ceramics in chinatown');
    return phone('A2e', 'ask', { tabs: TABS_ASK }, el('div', { class: 'page ask-page' }, scroll), bar);
  }
  function A3a() {
    const r = R.paik;
    const plus = h => el('button', { class: 'entry-plus' }, svg('plus'));
    const head = el('div', { class: 'eph-head' }, el('span', { class: 'eph-tag' }, svg('sparkle'), 'Unsaved list'), el('span', { class: 'eph-title' }, 'Like Nam June Paik'), el('span', { class: 'eph-sub' }, `${r.hits.length} shows · from your question · tap + to keep one`));
    return phone('A3a', 'ask', { tabs: TABS_ASK }, chatPage(navrow(citiesBtn(), iconBtn('listBullet')), el('div', { class: 'chat-body' },
      userMsg(Q.paik),
      aiMsg(el('p', null, `Reading that as ${r.constraints.reading}.`),
        el('div', { class: 'answer-card' }, head, ...r.hits.slice(0, 3).map((h, i) => entry(h, { open: i === 0, trail: plus(h) })),
          el('button', { class: 'ans-more' }, `Show all ${r.hits.length}`), ansActions(['bookmark', 'Save all as list', true], ['map', 'Map']))))), askBar(''));
  }
  function A3b() {
    const rt = R.saturday.route;
    const stops = [];
    rt.stops.forEach((st, i) => {
      const v = st.show.venue;
      const near = stops.find(f => Math.abs(f.geometry.coordinates[0] - v.lng) < 0.0006 && Math.abs(f.geometry.coordinates[1] - v.lat) < 0.0006);
      if (near) { near.properties.stop += '·' + (i + 1); near.properties.name += ' / ' + v.name; return; }
      stops.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [v.lng, v.lat] }, properties: { name: v.name, stop: String(i + 1), tier: 1, on: 1 } });
    });
    const others = venues.filter(g => g.venue.neighborhood === 'Downtown/Arts District' && !rt.stops.some(st => st.show.venue.name === g.venue.name))
      .map(g => ({ type: 'Feature', geometry: { type: 'Point', coordinates: [g.venue.lng, g.venue.lat] }, properties: { name: g.venue.name, tier: 3, dim: 1, on: 0 } }));
    const line = { type: 'Feature', geometry: { type: 'LineString', coordinates: rt.stops.map(st => [st.show.venue.lng, st.show.venue.lat]) }, properties: { line: 1 } };
    const geo = { type: 'FeatureCollection', features: [line, ...others, ...stops] };
    const layers = (map, h) => {
      map.addLayer({ id: 'route', type: 'line', source: 'v', filter: ['==', ['get', 'line'], 1], paint: { 'line-color': '#61ADF2', 'line-width': 3, 'line-dasharray': [1.5, 1.2], 'line-opacity': 0.9 } });
      map.addLayer({ id: 'dim', type: 'circle', source: 'v', filter: ['==', ['get', 'dim'], 1], paint: { 'circle-color': 'rgba(150,150,158,0.3)', 'circle-radius': 4 } });
      map.addLayer({ id: 'stops', type: 'circle', source: 'v', filter: ['has', 'stop'], paint: { 'circle-color': '#61ADF2', 'circle-radius': ['case', ['>', ['length', ['get', 'stop']], 1], 17, 13], 'circle-stroke-width': 2, 'circle-stroke-color': '#fff' } });
      map.addLayer({ id: 'stop-n', type: 'symbol', source: 'v', filter: ['has', 'stop'], layout: { 'text-field': ['get', 'stop'], 'text-font': h.label().layout['text-font'], 'text-size': 13, ...h.alwaysDraw }, paint: { 'text-color': '#000' } });
      const lab = h.label({}); lab.filter = ['has', 'stop']; lab.layout['text-offset'] = [0, 1.5]; map.addLayer(lab);
    };
    const c = rt.stops.reduce((a, st) => [a[0] + st.show.venue.lng / rt.stops.length, a[1] + st.show.venue.lat / rt.stops.length], [0, 0]);
    const card = el('div', { class: 'route-card' },
      el('div', { class: 'rc-head' }, el('span', { class: 'ctx-ico' }, svg('sparkle')), el('span', { class: 'rc-title' }, 'Saturday in the Arts District'), el('span', { class: 'ctx-n' }, `${rt.stops.length} stops`)),
      el('div', { class: 'rc-sub' }, `${rt.start}–${rt.end} · ${rt.totalKm.toFixed(1)} km · starts at ${rt.stops[0].show.venue.name}`),
      el('div', { class: 'rc-actions' }, el('button', { class: 'ans-act primary' }, svg('bookmark'), 'Save'), el('button', { class: 'ans-act' }, svg('walk'), 'Directions'), el('button', { class: 'ans-act' }, svg('xmark'), 'Clear')));
    return mapFrame('A3b', 'custom', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter')), card], { features: geo, layers, zoom: 13.15, center: c, tabs: TABS_ASK });
  }
  function A3c() {
    const r = R.paik;
    return phone('A3c', 'featured', { tabs: TABS_ASK }, page(navrow(citiesBtn(), iconBtn('sliders')), title(),
      ctxBar('sparkle', 'Like Nam June Paik', `${r.hits.length}`, { actions: ['Save'] }), feed(r.hits.slice(0, 3).map(h => h.show))));
  }

  // ================= page =================
  const SECTIONS = [
    { id: 'l1', title: 'L1 · The List tab becomes a Library',
      blurb: `First tap on the tab shows collections, not rows. Today's filtered list is one of them: <b>All shows in Los Angeles</b>. Two vocabularies side by side; every other frame says "Lists".`,
      frames: [
        [L1a, 'L1a', 'Library, "Lists".', 'Pinned rows for All shows, My Shows and Editor\'s Picks; your lists as tiles with a cover collage; a curated shelf; recent asks as rows. Reacts to L4 / L3d / A2e.', { live: true }],
        [L1b, 'L1b', 'Library, "Guides".', 'Same page, guides vocabulary, and the Gallery Browser shelf placed above your own to test a discovery-first framing.'],
        [L2, 'L2', 'The "All shows" special case.', 'Opened from the pinned row: today\'s list view with the shipped filter button and a summary line. Proves the current List survives as one list.'],
      ] },
    { id: 'l3', title: 'L3 · List detail',
      blurb: 'One page shape for owned, curated, received and editing states.',
      frames: [
        [L3a, 'L3a', 'Owner list (My Shows).', 'Collage hero, meta line, four actions (Featured, Map, Share, Edit), rows with filled bookmarks and dates.'],
        [L3b, 'L3b', 'Curated list (Editor\'s Picks).', 'Byline and update date, Save to Library instead of Edit, a one-line editor note per show (first sentence of the description).'],
        [L3c, 'L3c', 'Received via share link.', 'Banner says who shared and how many entries have closed; closed rows are dimmed; Save a copy / Open in Map.'],
        [L3d, 'L3d', 'Edit mode.', 'Rename, description, drag handles, remove, Add shows, Delete. Rename and remove persist to the lab store.', { live: true }],
      ] },
    { id: 'l4', title: 'L4–L7 · Getting things in, viewing a list everywhere, sharing, browsing curated',
      blurb: 'The bookmark stays the one-tap save; lists are the second tap. A chosen list becomes context on Featured and Map.',
      frames: [
        [L4, 'L4', 'Add-to-list from the bookmark.', 'Show detail behind with "Added to My Shows" and a toast offering "Add to list…"; the sheet lists your lists with checks and a New list row that expands to a name field. Creating a list lands in L1a.', { live: true }],
        [L5a, 'L5a', 'List picker inside the Filters sheet.', 'A List group at the top (All shows, My Shows, Editor\'s Picks, your lists) replaces the Saved-only switch; every other filter still applies within the list.'],
        [L5b, 'L5b', 'List as context on Featured.', 'A context bar under the title names the active list and its count; the feed is that list. The × returns to All shows.'],
        [L5c, 'L5c', 'List as context on the Map.', 'Same context as a floating pill; the list\'s venues lit, everything else faded.'],
        [L6, 'L6', 'Share sheet.', 'Link preview card (name, count, collage, short URL), Messages / Copy link / AirDrop / More, and a note on what the recipient sees. Pairs with L3c.'],
        [L7, 'L7', 'Curated guides page.', '"Guides for LA": a hero for this week\'s guide, then shelves (Walks, This weekend, By medium) of tiles with Save.'],
      ] },
    { id: 'a1', title: 'A1 · Where Ask lives',
      blurb: 'Four placements. A1a adds a tab; the others keep three tabs.',
      frames: [
        [A1a, 'A1a', 'Fourth tab, idle state.', 'Greeting, suggested prompts, recent asks, input at the bottom.'],
        [A1b, 'A1b', 'Capsule under the Featured title.', 'The feed is unchanged; tapping the capsule opens Ask as a sheet.'],
        [A1c, 'A1c', 'Inside the filter search.', 'The search field takes questions too; a typed question shows an Ask affordance under it. Plain words still filter.'],
        [A1d, 'A1d', 'Floating sparkle button.', 'Bottom-right above the tab bar, on every tab.'],
      ] },
    { id: 'a2', title: 'A2 · Conversations',
      blurb: `Rendered by a keyword retriever over the real payload, so counts and snippets are honest. Each entry carries a why-line with its source: show description, gallery about, or artist roster (a four-name fixture from the backend dataset).`,
      frames: [
        [A2a, 'A2a', 'Queer artists in Hollywood: honest partial.', `Nothing in Hollywood matches; the answer says so, widens to the city (${R.queer.hits.length} shows) and shows the widening as an applied chip.`],
        [A2b, 'A2b', 'Similar to Nam June Paik.', 'States its reading (video, television, electronic media), ranks strongest first, and points at the one Paik show in the app (Tokyo).'],
        [A2c, 'A2c', 'Opening receptions this weekend.', `${R.weekend.hits.length} receptions grouped by day from each gallery's own reception line; the artist talk and the closing party are flagged as not openings.`],
        [A2d, 'A2d', 'Plan a Saturday in the Arts District.', 'An ordered route with arrival times, Saturday hours and walking minutes; venues that would be closed on arrival are skipped and named.'],
        [A2e, 'A2e', 'Ask phone that works.', 'Type a question (or tap a prompt). The keyword retriever answers; Save as list writes to the lab store and the list appears in L1a. Shot with "ceramics in chinatown".', { live: true }],
      ] },
    { id: 'a3', title: 'A3 · Answers are lists',
      blurb: 'An answer is an unsaved list. Keep all of it, keep one entry, or view it on Map and Featured.',
      frames: [
        [A3a, 'A3a', 'Unsaved-list header and per-entry +.', 'The first entry has its grounding expanded: every source that matched, with the sentence.'],
        [A3b, 'A3b', 'Route on the Map.', 'Numbered stops, a dashed walking line, other Arts District venues faded, a bottom card with Save / Directions / Clear.'],
        [A3c, 'A3c', 'Answer on Featured.', 'The Paik answer as the Featured feed under a context bar with Save.'],
      ] },
  ];

  const reset = el('button', { class: 'lab-reset', onclick: () => { store.reset(); location.reload(); } }, 'Reset lab data');
  mountSections(document.getElementById('lab'), {
    title: 'Gallery Browser · design lab 2: Lists + Ask',
    blurb: `Mockups over the real Los Angeles payload (${LA.length} shows, ${venues.length} venues), today = ${TODAY.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}. Frame IDs are what to reply with. Four frames are live; they share one lab-only store in localStorage. <a href="./">Back to lab 1</a>.`,
    extra: reset,
  }, SECTIONS);
})();
