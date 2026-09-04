/* Design lab: static mockups of filter / rank / map options, built from the
 * real LA payload and the app's own stylesheet. Nothing here ships; it is a
 * review surface. Every frame is a function that returns a .phone element.
 * Shared helpers live in lab-common.js (window.Lab). */
(function () {
  'use strict';

  const { DATA, ICONS, city, LA, showId, venueKey, TIER2, tierOf, TIER_LABEL, TIER_SHORT, venues, galleries, PICKS_N,
    displayName, img, dateLine, fullAddress,
    el, svg, phone, navrow, citiesBtn, title, page, feed, showRow, rows, chip, chipRow, searchField, status, tierDots,
    T_COLOR, mapPill, legend, mapFrame, mountSections } = window.Lab;

  // A filter state used across the Q1 frames: Galleries, 2 neighborhoods, ranking.
  const F = { kind: 'galleries', hoods: ['Hollywood', 'Los Feliz/NELA'], featured: true, receptions: false, saved: false, sort: 'Ranking' };
  const inHoods = s => F.hoods.includes(s.venue.neighborhood);
  const featuredLA = LA.filter(s => s.featured);
  const filtered = featuredLA.filter(s => !s.venue.isMuseum && inHoods(s));
  const activeCount = 2;   // Galleries (non-default? default is galleries) + 2 hoods -> count the hood group + featured

  /* The production sticky bar, verbatim structure. */
  function fullBar(opts) {
    const o = opts || {};
    return el('div', { class: 'list-filters' + (o.cls ? ' ' + o.cls : '') },
      o.head || null,
      searchField(),
      chipRow(chip('Featured', F.featured), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('2 neighborhoods', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(filtered.length, true));
  }

  // ================= Q1: filters =================
  const filterBtn = (on, n) => el('button', { class: 'icon-btn' + (on ? ' on' : ''), 'aria-label': 'Filters' }, svg('sliders'), n ? el('span', { class: 'badge' }, String(n)) : null);

  function F1a() {
    const summary = el('div', { class: 'filter-summary' },
      chip('Featured', true), chip('Galleries', true), chip('Hollywood', false, { cls: 'sub' }), chip('Los Feliz/NELA', false, { cls: 'sub' }),
      el('span', { class: 'count' }, `${filtered.length} shows`));
    return phone('F1a', 'featured', page(navrow(citiesBtn(), filterBtn(false, activeCount)), title(), summary, feed(filtered.slice(0, 3))));
  }
  function F1b() {
    const head = el('div', { class: 'panel-head' }, el('span', { class: 'ph-title' }, 'Filters'), el('button', { class: 'ph-done' }, 'Done', svg('chevronUp')));
    return phone('F1b', 'featured', page(navrow(citiesBtn(), filterBtn(true, activeCount)), title(), fullBar({ head, cls: 'expanded' }), feed(filtered.slice(0, 2))));
  }
  function F1c() {
    const summary = el('div', { class: 'filter-summary' },
      chip('Featured', true), chip('Galleries', true), chip('Hollywood', false, { cls: 'sub' }), chip('Los Feliz/NELA', false, { cls: 'sub' }),
      el('span', { class: 'count' }, `${filtered.length} shows`));
    return phone('F1c', 'list', page(navrow(citiesBtn(), filterBtn(false, activeCount)), title(), summary, rows(filtered)));
  }
  function F2a() {
    const search = el('button', { class: 'icon-btn', 'aria-label': 'Search' }, svg('search'));
    const r = el('div', { class: 'chip-row', style: 'padding-bottom:10px' },
      chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('2 neighborhoods', true, { menu: true, cls: 'chip-menu' }),
      chip('Featured', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' }));
    return phone('F2a', 'featured', page(navrow(citiesBtn(), search), title(), r, feed(filtered.slice(0, 3))));
  }
  function F2b() {
    const search = el('button', { class: 'icon-btn', 'aria-label': 'Search' }, svg('search'));
    const bar = el('div', { class: 'list-filters' },
      el('div', { class: 'chip-row' },
        chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('2 neighborhoods', true, { menu: true, cls: 'chip-menu' }),
        chip('Featured', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(filtered.length, true));
    return phone('F2b', 'list', page(navrow(citiesBtn(), search), title(), bar, rows(filtered)));
  }
  function F3a() {
    const pills = el('div', { class: 'pill-row' },
      chip('Filters', true, { lead: svg('sliders'), n: activeCount }),
      chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu outline' }),
      el('span', { class: 'list-count', style: 'margin-left:auto' }, `${filtered.length} shows`));
    return phone('F3a', 'featured', page(navrow(citiesBtn()), title(), pills, feed(filtered.slice(0, 3))));
  }
  function F3b() {
    const toggleRow = (label, on) => el('div', { class: 'row' }, el('span', { class: 'row-label' }, label), el('span', { class: 'switch' + (on ? ' on' : '') }));
    const radioRow = (label, on) => el('div', { class: 'row' }, el('span', { class: 'row-label' }, label), on ? el('span', { class: 'check', html: ICONS.check, style: 'width:18px;height:18px;color:var(--blue)' }) : el('span'));
    const sheet = el('div', { class: 'sheet-static' },
      el('div', { class: 'sheet-header' }, el('div', { class: 'sheet-title' }, 'Filters'), el('button', { class: 'sheet-close' }, svg('xmark'))),
      el('div', { class: 'sheet-scroll' },
        searchField(),
        el('div', { class: 'group-header' }, 'Show'),
        el('div', { class: 'group' }, toggleRow('Featured only', true), toggleRow('Saved only', false), toggleRow('Upcoming receptions', false)),
        el('div', { class: 'group-header' }, 'Venues'),
        el('div', { class: 'seg-row' }, el('button', null, 'All venues'), el('button', { class: 'on' }, 'Galleries'), el('button', null, 'Museums')),
        el('div', { class: 'group-header' }, 'Neighborhoods'),
        el('div', { class: 'chip-wrap' }, chip('All', false), ...city.neighborhoods.slice(0, 9).map(h => chip(h, F.hoods.includes(h))))),
      el('div', { class: 'sheet-foot' }, el('button', { class: 'ghost' }, 'Clear'), el('button', { class: 'capsule-btn' }, `Show ${filtered.length} shows`)));
    const behind = page(navrow(citiesBtn()), title(), el('div', { class: 'pill-row' }, chip('Filters', true, { lead: svg('sliders'), n: activeCount }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu outline' })), feed(filtered.slice(0, 2)));
    return phone('F3b', 'featured', behind, el('div', { class: 'sheet-backdrop open' }), sheet);
  }
  function F4() {
    const head = el('div', { class: 'panel-head' }, el('span', { class: 'ph-title' }, 'Filters'), el('button', { class: 'ph-done' }, 'Done', svg('chevronUp')));
    const location = el('div', null,
      el('div', { class: 'group-header', style: 'padding-left:16px' }, 'Location'),
      el('div', { class: 'group', style: 'margin-bottom:10px' },
        el('div', { class: 'row' }, el('span', { class: 'row-label' }, 'City'), el('span', { class: 'row-value' }, 'Los Angeles'), el('span', { class: 'chev', html: ICONS.chevronRight, style: 'display:flex' })),
        el('div', { class: 'row' }, el('span', { class: 'row-label' }, 'Neighborhoods'), el('span', { class: 'row-value' }, '2 selected'), el('span', { class: 'chev', html: ICONS.chevronRight, style: 'display:flex' }))));
    const bar = el('div', { class: 'list-filters expanded' }, head, location, searchField(),
      chipRow(chip('Featured', true), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(filtered.length, true));
    return phone('F4', 'featured', page(navrow(el('span'), filterBtn(true, 3)), title(), bar, feed(filtered.slice(0, 1))));
  }

  // ================= Q2: rank =================
  // Venue page subject: a top-ranked gallery with more than one show if possible.
  const subject = galleries.find(g => g.shows.length > 1 && g.tier === 1) || galleries[0];
  const subject2 = galleries.find(g => g.tier === 2) || galleries[5];

  function venuePage(g, afterTitle, opts) {
    const v = g.venue;
    const mapCard = el('div', { class: 'map-card' });
    // the shipped card runs a live MapLibre map; these static phones just stand it in
    mapCard.append(el('div', { class: 'pin-marker', html: ICONS.pin, style: 'position:absolute;left:50%;top:50%;transform:translate(-50%,-100%)' }));
    const actions = el('div', { class: 'venue-actions' },
      el('a', { class: 'capsule-btn' }, svg('walk'), el('span', null, 'Directions to venue')),
      el('a', { class: 'capsule-btn' }, svg('compass'), el('span', null, 'Open website')));
    const body = el('div', { class: 'venue-body' },
      el('div', { class: 'venue-title' }, v.name),
      afterTitle,
      el('div', { class: 'venue-lines' }, el('div', null, fullAddress(v)), ...v.hours.map(h => el('div', null, h))),
      opts && opts.card ? opts.card : null,
      el('div', { class: 'venue-shows' }, el('div', { class: 'group-header' }, 'Shows'), el('div', { class: 'group' }, ...g.shows.map(s => showRow(s)))),
      mapCard, actions);
    return page(navrow(el('button', { class: 'circle-btn', style: 'background:var(--field)' }, svg('chevronLeft'))), body);
  }

  function R1a() {
    const g = subject;
    const chipEl = el('div', { class: 'rank-chip outline' }, svg('trophy'), `#${g.gpos} gallery in Los Angeles`);
    return phone('R1a', 'featured', venuePage(g, chipEl));
  }
  function R1b() {
    const s = LA[2];
    const g = venues.find(v => v.key === venueKey(s.venue));
    const hero = el('div', { class: 'detail-hero' }, el('img', { src: img(s), alt: '', style: 'width:100%;height:340px;object-fit:cover;display:block' }),
      el('div', { class: 'detail-topbar' }, el('button', { class: 'circle-btn' }, svg('chevronLeft')),
        el('div', { class: 'stepper' }, el('button', null, svg('chevronUp')), el('button', null, svg('chevronDown')))));
    const body = el('div', { class: 'detail-body' },
      s.artist ? el('div', { class: 'detail-artist' }, s.artist) : null,
      el('div', { class: 'detail-title' }, s.title),
      el('div', { class: 'detail-meta-row' },
        el('span', { class: 'rank-chip soft', style: 'margin-top:0' }, svg('star'), `Editor's Pick`),
        el('span', { class: 'rank-chip outline', style: 'margin-top:0' }, `Show #${s.rank}`),
        el('span', { class: 'rank-chip outline', style: 'margin-top:0' }, `Gallery #${g.gpos}`)),
      el('div', { class: 'detail-dates' }, dateLine(s)),
      el('button', { class: 'capsule-btn detail-save' }, el('span', null, 'Add to My Shows')),
      el('button', { class: 'venue-block' }, el('div', { class: 'vb-text' }, el('div', { class: 'vb-name' }, s.venue.name), el('div', { class: 'vb-line' }, fullAddress(s.venue)), ...s.venue.hours.map(h => el('div', { class: 'vb-line' }, h))), svg('chevronRight')),
      el('div', { class: 'divider' }),
      el('div', { class: 'detail-desc' }, ...s.description.split(/\n\s*\n/).slice(0, 1).map(p => el('p', null, p))));
    const pg = el('div', { class: 'page' }, el('div', { class: 'page-scroll', style: 'padding-top:0' }, hero, body));
    return phone('R1b', 'featured', pg);
  }
  function R2a() {
    const g = subject2;
    const pill = el('div', { class: 'tier-pill t' + g.tier }, tierDots(g.tier), TIER_LABEL[g.tier]);
    return phone('R2a', 'featured', venuePage(g, pill));
  }
  function R2b() {
    const shows = featuredLA.filter(s => !s.venue.isMuseum).slice(0, 40);
    const bar = el('div', { class: 'list-filters' }, searchField(),
      chipRow(chip('Featured', true), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('Neighborhoods', false, { menu: true, cls: 'chip-menu' }), chip('Sort: Gallery rank', false, { menu: true, cls: 'chip-menu' })),
      status(shows.length, true));
    const mixed = [...shows.slice(0, 4), ...featuredLA.filter(s => tierOf(s.score) === 2).slice(0, 3), ...LA.filter(s => tierOf(s.score) === 3 && !s.venue.isMuseum).slice(0, 2)];
    const withTier = s => { const t = tierOf(s.score); return { afterVenue: el('span', { class: 'sr-tier t' + t }, tierDots(t), TIER_SHORT[t]) }; };
    return phone('R2b', 'list', page(navrow(citiesBtn()), title(), bar, rows(mixed, withTier)));
  }
  function R3() {
    const g = subject;
    const pct = Math.max(0, Math.min(1, (g.score + 0.05) / 0.6));
    const thr = (TIER2 + 0.05) / 0.6;
    const why = [['Press coverage', 0.18], ['Art fairs', 0.12], ['Artist notability', 0.10], ['Venue history', 0.06], ['Museum weighting', -0.02]];
    const card = el('div', { class: 'rank-card' },
      el('div', { class: 'rc-top' }, el('div', { class: 'rc-rank' }, `#${g.gpos}`, el('small', null, `of ${galleries.length} galleries`)), el('div', { class: 'rc-tier' }, TIER_LABEL[g.tier])),
      el('div', { class: 'score-bar' }, el('div', { class: 'fill', style: `width:${pct * 100}%` }), el('div', { class: 'thresh', style: `left:${thr * 100}%` }), el('div', { class: 'thresh-lbl', style: `left:${thr * 100}%` }, 'Featured')),
      el('div', { class: 'rc-axis' }, el('span', null, 'Listed'), el('span', null, 'Top')),
      el('div', { class: 'rc-why' }, el('span', null, 'Why this ranking'), svg('chevronUp')),
      ...why.map(([l, v]) => el('div', { class: 'why-row' }, el('span', { class: 'wl' }, l), el('span', { class: 'wb' }, el('i', { class: v < 0 ? 'neg' : '', style: `width:${Math.abs(v) / 0.2 * 100}%` })), el('span', { class: 'wv' }, (v > 0 ? '+' : '') + v.toFixed(2)))));
    return phone('R3', 'featured', venuePage(g, null, { card }));
  }
  function R4() {
    const shows = featuredLA.filter(s => !s.venue.isMuseum);
    const bar = el('div', { class: 'list-filters' }, searchField(),
      chipRow(chip('Featured', true), chip('Saved', false), chip('Upcoming receptions', false)),
      el('div', { class: 'chip-row menus' }, chip('Galleries', true, { menu: true, cls: 'chip-menu' }), chip('Neighborhoods', false, { menu: true, cls: 'chip-menu' }), chip('Sort: Ranking', false, { menu: true, cls: 'chip-menu' })),
      status(shows.length, true));
    let i = 0;
    const numbered = () => { i += 1; return { lead: el('div', { class: 'sr-num' + (i > 10 ? ' dim' : '') }, String(i)) }; };
    return phone('R4', 'list', page(navrow(citiesBtn()), title(), bar, rows(shows.slice(0, 14), numbered)));
  }


  const seg = which => el('div', { class: 'seg-pill' }, el('button', { class: which === 'shows' ? 'on' : '' }, 'Shows'), el('button', { class: which === 'galleries' ? 'on' : '' }, 'Galleries'));

  function M1a() {
    return mapFrame('M1a', 'shows', [el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), seg('shows'), mapPill('Filter'))]);
  }
  function M1b() {
    return mapFrame('M1b', 'tier', [
      el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), seg('galleries'), mapPill('Filter')),
      legend([`background:${T_COLOR[1]}`, 'Top galleries'], [`background:${T_COLOR[2]}`, 'Notable'], [`background:${T_COLOR[3]};border:none;width:8px;height:8px;margin:2px`, 'Listed']),
      el('div', { class: 'map-count' }, `${galleries.length} galleries`)]);
  }
  function M2() {
    return mapFrame('M2', 'size', [el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), seg('galleries'), mapPill('Filter'))]);
  }
  function M3() {
    return mapFrame('M3', 'stars', [
      el('div', { class: 'map-overlay-top three' }, mapPill('Cities'), mapPill(`Picks`, { on: true, lead: svg('star') }), mapPill('Filter')),
      el('div', { class: 'map-count' }, `Editor's Picks · top ${PICKS_N}`)]);
  }
  function M4() {
    const row = (t, on, sub) => el('button', { class: sub ? 'sub' : '' }, el('span', null, t), on ? svg('check') : el('span'));
    const head = t => el('div', { class: 'mm-head' }, t);
    const menu = el('div', { class: 'map-menu' },
      head('Show'), row('All shows', false), row('My Shows', false), row('Upcoming receptions', false),
      head('Rank'), row(`Editor's Picks (top ${PICKS_N})`, true), row('Top 100', false), row('Everything', false),
      head('Venues'), row('All venues', false), row('Galleries', true), row('Museums', false));
    return mapFrame('M4', 'topn', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter · 2', { on: true })), menu], { topN: PICKS_N });
  }
  function topnPill(n) {
    return el('div', { class: 'topn-pill' }, el('span', { class: 'lbl' }, 'Top'), ...[10, 30, 100].map(k => el('button', { class: k === n ? 'on' : '' }, String(k))), el('button', { class: n === Infinity ? 'on' : '' }, 'All'));
  }
  function M5a() { return mapFrame('M5a', 'topn', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter')), topnPill(10)], { topN: 10 }); }
  function M5b() { return mapFrame('M5b', 'topn', [el('div', { class: 'map-overlay-top' }, mapPill('Cities'), mapPill('Filter')), topnPill(100)], { topN: 100 }); }

  // ================= page =================
  const SECTIONS = [
    { id: 'q1', title: 'Q1 · Filters on Featured, collapsible on both tabs',
      blurb: `Filter state in every frame: Featured on, Galleries, Hollywood + Los Feliz/NELA, sorted by ranking (${filtered.length} shows). Cities stays a global control except in F4.`,
      frames: [
        [F1a, 'F1a', 'Filter button + collapsed summary (Featured).', 'Sliders icon in the navrow with an active-count badge. The one-line summary shows the live values; tapping any chip or the button expands.'],
        [F1b, 'F1b', 'Same, expanded in place.', 'The production List bar drops in under the title with a Filters/Done header. Nothing new to learn; List and Featured share one component.'],
        [F1c, 'F1c', 'F1 collapsed on List.', 'The List tab gets the same slim default. The tall bar becomes opt-in.'],
        [F2a, 'F2a', 'Picker chips only, search behind a button (Featured).', 'No toggles row and no search field. Featured/Saved/Receptions fold into one "Featured" picker. Always one row tall.'],
        [F2b, 'F2b', 'F2 on List.', 'Same row plus count/Clear. Cheapest change, but a fourth picker and hidden search cost discoverability.'],
        [F3a, 'F3a', 'Filters pill + Sort pill (Featured).', 'Two controls under the title, nothing else. Everything lives in a sheet.'],
        [F3b, 'F3b', 'The Filters sheet.', 'Grouped iOS-style form: search, toggles, venue segment, neighborhood chips, "Show N shows" apply button. Best for many filters, worst for quick toggling.'],
        [F4, 'F4', 'City inside the filter panel.', 'F1b with a Location group (City, Neighborhoods) at the top and no Cities button. Map would still need its own Cities pill, so the city control would exist in two shapes.'],
      ] },
    { id: 'q2', title: 'Q2 · Gallery rank visibility',
      blurb: `Ranking exists per show. Mockups derive a gallery rank as the best show rank at that venue (${galleries.length} ranked galleries in LA) and three tiers from score: Top ≥ 0.35, Notable ≥ 0.22 (the featured threshold), Listed below.`,
      frames: [
        [R1a, 'R1a', 'Rank chip on the venue page.', 'One outlined chip under the name: "#N gallery in Los Angeles". Honest and minimal; a number invites "why #7?".'],
        [R1b, 'R1b', 'Chips on the show detail.', 'Editor\'s Pick + show rank + gallery rank as a meta row under the title. Shows how much a full set of badges costs.'],
        [R2a, 'R2a', 'Tier label instead of a number.', 'Three-dot glyph + "Top gallery / Notable / Listed". Softer than a rank; no promise of precision; same glyph works in rows and on the map legend.'],
        [R2b, 'R2b', 'Tier glyph in list rows + "Sort: Gallery rank".', 'Rows are a mix of tiers to show the glyph at each level. A second sort entry separates gallery rank from show rank.'],
        [R3, 'R3', 'Score card with "Why this ranking".', 'Rank, score bar against the featured threshold, expandable factor breakdown (factor labels here are illustrative). Most transparent; also the busiest venue page.'],
        [R4, 'R4', 'Numbered list rows.', 'A chart-style ordinal column when sorted by ranking, dimmed past 10. Raw ranks are sparse (1, 2, 5, 6…) so this shows positions, not raw rank.'],
      ] },
    { id: 'q3', title: 'Q3 · Map: galleries vs shows, rank encoding, Top N',
      blurb: `Real MapLibre with the production layers. Frames are centered on Hollywood / Mid-Wilshire / Downtown. Editor's Picks here means the top ${PICKS_N} featured shows (the pipeline currently marks 5).`,
      frames: [
        [M1a, 'M1a', 'Shows | Galleries segmented control, Shows mode.', 'Today\'s clustered map with a mode switch in the top center. Shows mode is unchanged.'],
        [M1b, 'M1b', 'Galleries mode, colored by tier.', 'One dot per gallery, no clustering. Blue = Top, white = Notable, small gray = Listed (unlabeled). Legend bottom-left.'],
        [M2, 'M2', 'Galleries mode, size ramp + numbered top 10.', 'Top 10 get numbered pins; the rest scale and fade with score. No legend needed, and the numbers tie back to R4.'],
        [M3, 'M3', 'Editor\'s Picks as star markers.', 'A "Picks" toggle pill. Picks are stars, everything else dims to small gray dots. Reads instantly, but adds a third pill.'],
        [M4, 'M4', 'Sectioned Filter dropdown.', 'The existing dropdown grows Show / Rank / Venues sections. Rank = Editor\'s Picks, Top 100, Everything. No new chrome; one place for everything.'],
        [M5a, 'M5a', '"Top N" stepper, Top 10.', 'A bottom-floating pill cycling 10 / 30 / 100 / All. Below-cutoff galleries stay as faint dots for context.'],
        [M5b, 'M5b', '"Top N" stepper, Top 100.', 'Same control at 100, to show how the map fills in.'],
      ] },
  ];

  mountSections(document.getElementById('lab'), {
    title: 'Gallery Browser · design lab',
    blurb: `Static mockups over the real Los Angeles payload (${LA.length} shows, ${venues.length} venues). Each frame is one option; IDs are what to reply with. Nothing here is wired into the app. See also the <a href="lists.html">Lists + Ask lab</a>.`,
  }, SECTIONS);
})();
