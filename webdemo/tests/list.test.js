/* Filter-sheet checks (List + Featured share one filter state) against a built dist (default webdemo/dist/gallery-browser-demo,
 * override with DIST=<dir>). Headless Chrome via the globally installed Playwright:
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/list.test.js
 *
 * There is no show-level rank: the gallery's city-wide rank orders everything,
 * the tiers are the top 20 / top 50, and only the gallery is ever starred.
 */
const path = require('path');
const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = process.env.DIST || path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8933;
const SHOT = process.env.SHOT_DIR || path.join(__dirname, '.shots');
fs.mkdirSync(SHOT, { recursive: true });
const CITY = 'los-angeles';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.json': 'application/json' };

function serve() {
  return new Promise(res => {
    const s = http.createServer((req, r) => {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p === '/') p = '/index.html';
      fs.readFile(path.join(ROOT, p), (err, data) => {
        if (err) { r.writeHead(404); r.end(); return; }
        r.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' });
        r.end(data);
      });
    });
    s.listen(PORT, () => res(s));
  });
}

const results = [];
function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

(async () => {
  const server = await serve();
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 393, height: 852 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('PAGEERROR', e.message));
  await page.addInitScript(([city]) => {
    try { if (sessionStorage.getItem('list-test-inited')) return; sessionStorage.setItem('list-test-inited', '1'); }
    catch (e) { return; }   // about:blank; keep state across the reload step
    localStorage.setItem('selectedCityKey', city);
    localStorage.removeItem('filter');
    localStorage.removeItem('galleryOrder');
    localStorage.setItem('savedShowIDs', '[]');
    localStorage.setItem('favoriteVenueIDs', '[]');
  }, [CITY]);
  await page.goto(`http://localhost:${PORT}/`);
  await page.click('.tab-btn[data-tab="list"]');
  // The tab is a library now; the flat filtered list is its pinned "All shows" row.
  await page.waitForSelector('#pages-list .lib-row.all');
  await page.click('#pages-list .lib-row.all');
  await page.waitForSelector('#pages-list .list-results');

  // Every count is over the *active* shows: "Active shows" is on by default, so
  // the closed and not-yet-open ones only appear once the switch is off (allTotal).
  const data = await page.evaluate(city => {
    const all = window.DEMO_DATA.shows.filter(s => s.city === city);
    const shows = all.filter(s => window.DemoDebug.isActiveShow(s));
    const DAY = 86400e3;
    const pd = str => { const [y, m, d] = str.split('-').map(Number); return new Date(y, m - 1, d); };
    const kind = s => s.venue.kind || (s.venue.isMuseum ? 'museum' : 'gallery');
    const tier = s => window.DemoDebug.galleryTier(s.venue);
    const galleries = shows.filter(s => kind(s) === 'gallery');
    return {
      total: shows.length,
      allTotal: all.length,
      galleries: galleries.length,
      topGalleryShows: galleries.filter(s => tier(s) === 'top').length,
      museums: shows.filter(s => kind(s) === 'museum').length,
      topShows: shows.filter(s => tier(s) === 'top').length,
      notableShows: shows.filter(s => tier(s) !== 'listed').length,
      cutoff: window.DemoDebug.GALLERY_TIER_CUTOFF,
      receptions: shows.filter(s => window.DemoDebug.hasUpcomingReception(s)).length,
      withReceptionText: shows.filter(s => s.reception != null).length,
      closing: shows.filter(s => { const d = pd(s.endDate); const diff = d - Date.now(); return diff >= 0 && diff <= 7 * DAY; }).length,
      hoods: window.DEMO_DATA.cities.find(c => c.key === city).neighborhoods,
      perHood: Object.fromEntries(window.DEMO_DATA.cities.find(c => c.key === city).neighborhoods
        .map(h => [h, shows.filter(s => s.venue.neighborhood === h).length])),
      showsHaveNoRank: all.every(s => s.rank === undefined && s.featured === undefined && s.editorsPick === undefined),
    };
  }, CITY);
  const rows = () => page.$$eval('#pages-list .list-results .show-row', els => els.map(e => ({
    name: e.querySelector('.sr-name .sr-txt').textContent, venue: e.querySelector('.sr-venue .sr-txt').textContent,
    addr: e.querySelector('.sr-addr').textContent,
    // the star leads the gallery's name (top 20 only); the show's own name carries no mark
    venueStar: (e.querySelector('.sr-venue .tier-star') || { dataset: {} }).dataset.tier || '',
    venueStarFirst: e.querySelector('.sr-venue').firstElementChild.classList.contains('tier-star'),
    nameGlyphs: e.querySelectorAll('.sr-name .tier-star, .sr-name .sr-tier').length,
  })));
  // the filtered set's gallery ranks in row order (null = unranked)
  const rowRanks = () => page.evaluate(() => window.DemoDebug.filteredShows().map(s => window.DemoDebug.venueRank(s.venue)));
  const badge = async sel => { const b = await page.$(`${sel} .icon-btn[data-filter-btn] .badge`); return b ? await b.textContent() : ''; };
  const openSheet = async (tab = 'list') => { await page.click(`#pages-${tab} .page:last-child .icon-btn[data-filter-btn]`); await page.waitForSelector('.sheet.open .filter-sheet'); };
  const closeSheet = async () => { await page.click('.sheet.open .sheet-foot .capsule-btn'); await page.waitForSelector('.sheet.open', { state: 'detached' }); };
  const seg = async (key, value) => { await page.click(`.sheet.open .seg-row[data-seg="${key}"] button[data-value="${value}"]`); };
  const segOn = (key, value) => page.$eval(`.sheet.open .seg-row[data-seg="${key}"] button[data-value="${value}"]`, e => e.classList.contains('on'));
  const sw = async key => { await page.click(`.sheet.open [data-switch="${key}"]`); };
  const swOn = key => page.$eval(`.sheet.open [data-switch="${key}"] .switch`, e => e.classList.contains('on'));
  const hood = async label => { await page.locator('.sheet.open .chip-wrap .chip', { hasText: new RegExp('^' + label.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&') + '$') }).click(); };
  const sortBy = async label => { await page.click(`.sheet.open [data-sort] .row:has-text("${label}")`); };
  const doneText = () => page.$eval('.sheet.open .sheet-foot .capsule-btn', e => e.textContent);
  const clearHidden = () => page.$eval('.sheet.open .sheet-foot .ghost', e => e.hidden);
  const monotone = ranks => ranks.every((r, i) => i === 0 || (ranks[i - 1] ?? Infinity) <= (r ?? Infinity));

  // 0. the payload carries no show-level rank at all
  check('shows ship without rank / featured / editorsPick', data.showsHaveNoRank);
  check('tiers are the top 20 and the top 50', data.cutoff.top === 20 && data.cutoff.notable === 50, JSON.stringify(data.cutoff));

  // 1. default = running gallery shows in gallery-rank order, neighborhood on the third line, no badge
  let r = await rows();
  check('running gallery shows listed by default', r.length === data.galleries && r.length > 0, `${r.length} rows / ${data.galleries}`);
  check('no badge at defaults', (await badge('#pages-list')) === '');
  check('rows run best-ranked gallery first', monotone(await rowRanks()), (await rowRanks()).slice(0, 8).join(','));
  const starred = r.filter(x => x.venueStar);
  check('only top-20 galleries are starred, star leading the gallery name',
    starred.length === data.topGalleryShows && starred.length > 0 && starred.every(x => x.venueStar === 'top' && x.venueStarFirst),
    `${starred.length} starred / ${data.topGalleryShows} at top galleries`);
  check('the show name carries no glyph of its own', r.every(x => x.nameGlyphs === 0));
  check('the star is a filled blue one', await page.$eval('#pages-list .tier-star.t-top svg', e =>
    getComputedStyle(e).color === 'rgb(97, 173, 242)' && e.getAttribute('fill') === 'currentColor'));
  check('no second-tier glyph anywhere', (await page.$('#pages-list .t-featured, #pages-list .t-notable, #pages-list .t-picks')) === null);
  await openSheet();
  check('sheet: Galleries + All galleries selected', (await segOn('kind', 'galleries')) && (await segOn('galleryRank', 'all')));
  check('sheet: no Show Rank group (shows are not ranked)', !(await page.$('.sheet.open .seg-row[data-seg="showRank"]')));
  check('sheet: Active shows on by default', await swOn('active'));
  check('done button counts', (await doneText()) === `Show ${data.galleries} shows`, await doneText());
  check('clear hidden at defaults', await clearHidden());
  await seg('kind', 'all');
  r = await rows();
  check('All venues lists everything', r.length === data.total, `${r.length} rows / ${data.total} shows`);
  check('badge counts one group', (await badge('#pages-list')) === '1');
  check('still in gallery-rank order', monotone(await rowRanks()));
  check('neighborhood shown in row', r.every(x => data.hoods.some(h => x.addr.startsWith(h + ' · '))));
  check('clear visible when widened', !(await clearHidden()));

  // 1b. Active shows: on by default, off brings back what has closed or not yet opened
  await sw('active');
  check('Active shows off adds the closed and not-yet-open shows',
    (await rows()).length === data.allTotal && data.allTotal > data.total, `${(await rows()).length} / ${data.allTotal} (active ${data.total})`);
  check('turning Active off counts toward the badge', (await badge('#pages-list')) === '2');
  await sw('active');
  check('Active shows on again', (await rows()).length === data.total && (await swOn('active')), `${(await rows()).length} / ${data.total}`);

  // 2. gallery rank + venue type
  await seg('galleryRank', 'top');
  r = await rows();
  check(`Top 20 = venue rank <= ${data.cutoff.top}`, r.length === data.topShows && r.length > 0 && r.every(x => x.venueStar === 'top'), `${r.length} / ${data.topShows}`);
  await seg('galleryRank', 'notable');
  r = await rows();
  check(`Top 50 = venue rank <= ${data.cutoff.notable}`, r.length === data.notableShows && r.length > data.topShows, `${r.length} / ${data.notableShows}`);
  await seg('galleryRank', 'all');
  await seg('kind', 'museums');
  check('Museums option', (await rows()).length === data.museums, `${(await rows()).length} / ${data.museums}`);
  await seg('kind', 'all');
  await sw('receptions');
  check('Upcoming receptions switch', (await rows()).length === data.receptions && data.receptions < data.withReceptionText,
    `${(await rows()).length} / ${data.receptions} upcoming of ${data.withReceptionText} with text`);
  const parsed = await page.evaluate(() => {
    const D = window.DemoDebug, iso = d => d && `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    return [
      iso(D.receptionDate({ reception: 'Saturday, July 18, 6-9pm', startDate: '2026-07-18' })),
      iso(D.receptionDate({ reception: 'Thursday, September 24, 2026, 7:00pm - 9:00pm', startDate: '2026-09-01' })),
      iso(D.receptionDate({ reception: 'Saturday, September 12, 4-6pm (Fall Open House); Artist Talk Saturday, September 19, 11am', startDate: '2026-09-05' })),
      iso(D.receptionDate({ reception: 'Saturday, January 9, 6-8pm', startDate: '2026-12-20' })),
      D.receptionDate({ reception: 'Opening reception TBA', startDate: '2026-09-01' }),
    ];
  });
  check('reception parser', JSON.stringify(parsed) === JSON.stringify(['2026-07-18', '2026-09-24', '2026-09-12', '2027-01-09', null]), JSON.stringify(parsed));
  await seg('galleryRank', 'notable');
  const both = (await rows()).length;
  check('filters combine (AND)', both <= Math.min(data.receptions, data.notableShows), `${both}`);
  await page.click('.sheet.open .sheet-foot .ghost');
  check('Clear resets to running gallery shows, all ranks',
    (await rows()).length === data.galleries && (await segOn('galleryRank', 'all')) && (await segOn('kind', 'galleries')) && (await swOn('active')) && !(await swOn('receptions')));
  check('Clear hides itself and the badge', (await clearHidden()) && (await badge('#pages-list')) === '');

  // 3. search (inside the sheet)
  await seg('kind', 'all');
  await page.fill('.sheet.open .search-field input', 'gallery');
  const q = await rows();
  check('search narrows', q.length > 0 && q.length < data.total, `${q.length}`);
  await page.click('.sheet.open .search-clear');
  check('search clear', (await rows()).length === data.total);

  // 4. neighborhoods (multi-select chips; All resets)
  const hoodA = data.hoods[0], hoodB = data.hoods[1];
  await hood(hoodA);
  await hood(hoodB);
  check('two neighborhoods', (await rows()).length === data.perHood[hoodA] + data.perHood[hoodB],
    `${(await rows()).length} / ${data.perHood[hoodA] + data.perHood[hoodB]}`);
  await hood('All');
  check('All resets neighborhoods', (await rows()).length === data.total);

  // 5. sort by closing soon is monotone in endDate; Gallery rank is the default sort
  await sortBy('Closing soon');
  const ends = await page.evaluate(city => {
    const byName = {};
    window.DEMO_DATA.shows.filter(s => s.city === city).forEach(s => { byName[(s.artist || s.title) + '|' + s.venue.name] = s.endDate; });
    return [...document.querySelectorAll('#pages-list .list-results .show-row')]
      .map(e => byName[e.querySelector('.sr-name .sr-txt').textContent + '|' + e.querySelector('.sr-venue .sr-txt').textContent]);
  }, CITY);
  check('closing-soon sort monotone', ends.every((d, i) => i === 0 || d >= ends[i - 1]), `${ends.slice(0, 3)}`);
  check('sort row checked', await page.$eval('.sheet.open [data-sort] .row:has-text("Closing soon") .check', e => !!e).catch(() => false));
  check('sort does not count toward the badge', (await badge('#pages-list')) === '1');
  check('no separate Gallery rank sort beside the default', (await page.$$eval('.sheet.open [data-sort] .row', els => els.map(e => e.textContent.trim()))).filter(t => /gallery rank/i.test(t)).length === 1);
  await sortBy('Gallery rank');
  check('Gallery rank sort restores rank order', monotone(await rowRanks()));
  await closeSheet();

  // 6. Featured shares the state; a venue's concurrent shows deal through one card
  await page.click('.tab-btn[data-tab="featured"]');
  await page.waitForSelector('#pages-featured .card');
  const feed = await page.$$eval('#pages-featured .feed > *',
    els => els.map(e => +(e.dataset.shows || 1)));
  const cards = feed.length;
  check('Featured feed covers the same filtered set',
    feed.reduce((a, b) => a + b, 0) === data.total, `${cards} cards over ${data.total} shows`);
  check('Featured feed is one card per venue, not per show',
    cards === await page.evaluate(() => new Set(window.DemoDebug.filteredShows()
      .map(s => window.__venueKey(s.venue))).size), `${cards} cards`);
  check('a venue running several shows deals them from one card', await (async () => {
    const deep = feed.filter(n => n > 1).length;
    if (!deep) return false;                       // the payload must exercise the case
    const i = feed.findIndex(n => n > 1);
    const st = await page.evaluate(n => {
      const e = document.querySelectorAll('#pages-featured .feed > *')[n];
      return { sheets: e.querySelectorAll('.stack-sheet').length,
               cards: e.querySelectorAll('.card').length,
               count: (e.querySelector('.cf-count') || {}).textContent };
    }, i);
    return st.cards === 1 && st.sheets === Math.min(feed[i] - 1, 2) && st.count === `1 / ${feed[i]}`;
  })(), `${feed.filter(n => n > 1).length} decks in the feed`);
  check('swiping a deck footer deals the next show, and a tap opens the face-up one', await (async () => {
    const i = feed.findIndex(n => n > 1);
    await page.evaluate(n => document.querySelectorAll('#pages-featured .feed > *')[n]
      .scrollIntoView({ block: 'center' }), i);
    await page.waitForTimeout(400);
    const sel = `#pages-featured .feed > *:nth-child(${i + 1})`;
    const before = await page.$eval(sel + ' .name', e => e.textContent);
    const b = await page.$eval(sel + ' .card-footer',
      e => { const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await page.mouse.move(b.x, b.y); await page.mouse.down();
    await page.mouse.move(b.x - 60, b.y, { steps: 6 }); await page.mouse.up();
    await page.waitForTimeout(420);
    const after = await page.evaluate(sel => ({ n: document.querySelector(sel + ' .name').textContent,
      c: document.querySelector(sel + ' .cf-count').textContent }), sel);
    if (after.n === before || after.c !== `2 / ${feed[i]}`) return false;
    await page.click(sel + ' .cf-text');
    await page.waitForSelector('#pages-featured .page-push .detail-body');
    const opened = await page.$eval('#pages-featured .page-push .detail-body',
      d => (d.querySelector('.detail-artist') || d.querySelector('.detail-title')).textContent);
    await page.evaluate(() => document.querySelector('#pages-featured .page-push').remove());
    return opened === after.n;
  })());
  check('Featured badge matches', (await badge('#pages-featured')) === '1');
  check('cards carry no rank star', (await page.$$eval('#pages-featured .card .tier-star', els => els.length)) === 0);
  check('the first card is a top-20 gallery', await page.evaluate(() => {
    const first = window.DemoDebug.filteredShows()[0];
    return !!first && window.DemoDebug.galleryTier(first.venue) === 'top';
  }));
  await page.click('#pages-featured .card .bookmark-btn');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('savedShowIDs')));
  check('card bookmark saves the show', saved.length === 1, JSON.stringify(saved));
  check('card bookmark shows saved', await page.$eval('#pages-featured .card .bookmark-btn', e => e.classList.contains('saved')));
  check('card bookmark is white when unsaved', await page.$eval('#pages-featured .feed > *:nth-child(2) .bookmark-btn', e => getComputedStyle(e).color === 'rgb(255, 255, 255)'));
  await page.click('.tab-btn[data-tab="list"]');
  check('list row bookmark mirrors it', await page.$eval('#pages-list .show-row .bookmark-btn', e => e.classList.contains('saved')));
  // "Saved only" became the List group's My Shows option
  await openSheet();
  await page.click('.sheet.open [data-list-group] .row[data-list-option="saved"]');
  check('My Shows as the list context', (await rows()).length === 1);
  await closeSheet();
  await page.click('#pages-list .show-row .bookmark-btn');
  check('unsaving under My Shows empties the list', (await rows()).length === 0 && !(await page.$eval('#pages-list .page:last-child .empty-plain', e => e.hidden)));
  await openSheet();
  await page.click('.sheet.open [data-list-group] .row[data-list-option="all"]');
  await closeSheet();

  // 7. filter persists across reload; detail from a filtered list steps within it
  await openSheet();
  await sw('receptions');
  await closeSheet();
  await page.reload();
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .lib-row.all');
  await page.click('#pages-list .lib-row.all');
  await page.waitForSelector('#pages-list .list-results');
  check('filters persist', (await rows()).length === data.receptions && (await badge('#pages-list')) === '2', `${(await rows()).length} / ${data.receptions}`);
  await page.click('#pages-list .show-row .sr-text');
  await page.waitForSelector('#pages-list .page:last-child .detail-body');
  const stepper = await page.$$('#pages-list .page:last-child .stepper button');
  check('detail opened from filtered list with stepper', stepper.length === 2);
  const detail = await page.evaluate(() => {
    const first = window.DemoDebug.filteredShows()[0];
    return {
      metaGlyphs: document.querySelectorAll('#pages-list .page:last-child .detail-meta-row, #pages-list .page:last-child .sr-tier').length,
      venueStars: document.querySelectorAll('#pages-list .page:last-child .vb-name .tier-star').length,
      venueIsTop: window.DemoDebug.galleryTier(first.venue) === 'top',
    };
  });
  check('the show detail rates nothing of its own', detail.metaGlyphs === 0);
  check('the venue block stars a top-20 gallery only', detail.venueStars === (detail.venueIsTop ? 1 : 0), JSON.stringify(detail));
  await page.screenshot({ path: path.join(SHOT, 'list-detail.png') });

  await browser.close();
  server.close();
  const failed = results.filter(x => !x.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
