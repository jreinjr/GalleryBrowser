/* Gallery ranking + favorites checks against a built dist (DIST=<dir> overrides
 * webdemo/dist/gallery-browser-demo). Settings → Gallery ranking lists the city's
 * galleries as the app ranks them; Edit / Save keeps a personal order that then
 * drives ranks, tiers and sort everywhere; the heart follows a gallery and
 * "Favorite galleries" is a default list and a filter context.
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/ranking.test.js
 */
const path = require('path');
const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = process.env.DIST || path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8937;
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
    try { if (sessionStorage.getItem('ranking-test-inited')) return; sessionStorage.setItem('ranking-test-inited', '1'); } catch (e) { return; }
    localStorage.setItem('selectedCityKey', city);
    localStorage.removeItem('filter');
    localStorage.removeItem('lists');
    localStorage.removeItem('galleryOrder');
    localStorage.setItem('savedShowIDs', '[]');
    localStorage.setItem('favoriteVenueIDs', '[]');
  }, [CITY]);
  await page.goto(`http://localhost:${PORT}/`);
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .lib-pinned .lib-row');

  // the sheet's backdrop lingers 300 ms after a close and would swallow a raw pointer drag
  const sheetGone = () => page.waitForFunction(() => !document.querySelector('#sheet-root .sheet, #sheet-root .sheet-backdrop'));
  const base = await page.evaluate(() => {
    const D = window.DemoDebug;
    const city = localStorage.getItem('selectedCityKey');
    const order = D.ranking.appOrder(city);
    // registry ids repeat across cities: only this city's shows count
    const running = new Set(window.DEMO_DATA.shows.filter(s => s.city === city && D.isActiveShow(s)).map(s => s.venueId));
    // a top-20 gallery with a show on view, to favorite (its shows make the favorites list)
    const withShow = order.find((v, i) => i < 20 && running.has(v.id) && v.kind === 'gallery');
    return {
      n: order.length, first: order[0], fifth: order[4], withShow,
      withShowShows: window.DEMO_DATA.shows.filter(s => s.city === city && s.venueId === withShow.id && D.isActiveShow(s)).length,
      appRanks: order.slice(0, 5).map(v => v.rank),
    };
  });
  check('the payload carries the city\'s galleries with registry ranks', base.n > 100 && base.appRanks.join(',') === '1,2,3,4,5', `${base.n} venues, ranks ${base.appRanks.join(',')}`);

  // 1. Settings from the gear
  await page.click('#pages-list [data-settings-btn]');
  await page.waitForSelector('#pages-list .settings-page [data-setting="ranking"]');
  const rowsText = () => page.$$eval('#pages-list .settings-page [data-setting]', els => els.map(e => e.dataset.setting + ': ' + e.querySelector('.cr-note').textContent));
  let st = await rowsText();
  check('Settings shows the app ranking and no favorites', st[0].startsWith('ranking: App ranking') && st[1] === 'favorites: None yet', st.join(' | '));

  // 2. the ranking page: every gallery in app order, tier dots for the top 20 / top 50
  await page.click('#pages-list .settings-page [data-setting="ranking"]');
  await page.waitForSelector('#pages-list .galleries-page .rank-row');
  const rows = () => page.$$eval('#pages-list .galleries-page .rank-row', els => els.map(e => ({
    id: e.dataset.id, n: e.querySelector('.rank-n').textContent, tier: e.className.replace(/.*\bt-(\w+).*/, '$1'),
    star: !!e.querySelector('.rr-name .tier-star'), fav: e.querySelector('.fav-btn').classList.contains('on'), grip: !!e.querySelector('.grip'),
  })));
  let r = await rows();
  check('the ranking page lists every gallery, numbered 1..n in app order', r.length === base.n && r[0].id === base.first.id && r[0].n === '1' && r[4].id === base.fifth.id && r[4].n === '5', `${r.length} rows`);
  // the number shown is the registry rank (a ranked venue without a pin leaves a gap), and the tier follows it
  const tierOfN = n => n === '–' ? 'listed' : +n <= 20 ? 'top' : +n <= 50 ? 'notable' : 'listed';
  check('the top 20 wear the blue dot and the star, 21–50 the white dot, the rest are listed',
    r.every(x => x.tier === tierOfN(x.n) && x.star === (x.tier === 'top')) && r.filter(x => x.tier === 'top').length === 20 && r.filter(x => x.tier === 'notable').length >= 25,
    `top=${r.filter(x => x.tier === 'top').length} notable=${r.filter(x => x.tier === 'notable').length} unranked=${r.filter(x => x.n === '–').length}`);
  check('the subtitle says App ranking', /^App ranking/.test(await page.$eval('#pages-list .galleries-page .rank-sub', e => e.textContent)));
  check('no grips outside Edit; Reset hidden without a personal ranking', r.every(x => !x.grip) && (await page.$eval('#pages-list .galleries-page [data-rank-reset]', e => e.hidden)));
  await page.fill('#pages-list .galleries-page .search-field input', base.fifth.name.slice(0, 12));
  check('search narrows the rows and keeps their rank numbers', await (async () => { const q = await rows(); return q.length < r.length && q.some(x => x.id === base.fifth.id && x.n === '5'); })());
  await page.click('#pages-list .galleries-page .search-clear');
  check('search clears', (await rows()).length === base.n);

  // 3. favorite from the row heart
  await page.click(`#pages-list .galleries-page .rank-row[data-id="${base.withShow.id}"] .fav-btn`);
  const favs = await page.evaluate(() => JSON.parse(localStorage.getItem('favoriteVenueIDs')));
  check('the heart on a row favorites the gallery', favs.length === 1 && favs[0] === `${CITY}/${base.withShow.id}` && (await rows()).find(x => x.id === base.withShow.id).fav, JSON.stringify(favs));

  // 4. Edit: move the fifth gallery to #1 via its number, save, and the app follows
  await page.click('#pages-list .galleries-page [data-rank-edit]');
  await page.waitForSelector('#pages-list .galleries-page.editing .rank-row .grip');
  r = await rows();
  check('Edit shows grips, Save and Cancel', r.every(x => x.grip) && !!(await page.$('#pages-list .galleries-page [data-rank-save]')) && !!(await page.$('#pages-list .galleries-page [data-rank-cancel]')));
  await page.click(`#pages-list .galleries-page .rank-row[data-id="${base.fifth.id}"] .rank-n`);
  await page.waitForSelector('.sheet.open .move-sheet .move-num');
  check('tapping the number opens the move sheet on that gallery', (await page.$eval('.sheet.open .move-name', e => e.textContent)) === base.fifth.name && (await page.$eval('.sheet.open .move-num', e => e.value)) === '5');
  await page.fill('.sheet.open .move-num', '1');
  await page.click('.sheet.open [data-move="go"]');
  await sheetGone();
  r = await rows();
  check('the gallery moves to #1 and the old #1 slides to #2', r[0].id === base.fifth.id && r[0].n === '1' && r[1].id === base.first.id && r[1].n === '2', `${r[0].id}, ${r[1].id}`);
  check('nothing is saved until Save', (await page.evaluate(() => localStorage.getItem('galleryOrder'))) === null);
  // drag the (now) second row below the third by its grip
  const drag = await page.evaluate(() => {
    const rows = document.querySelectorAll('#pages-list .galleries-page .rank-row');
    const g = rows[1].querySelector('.grip').getBoundingClientRect(), t = rows[3].getBoundingClientRect();
    return { from: { x: g.x + g.width / 2, y: g.y + g.height / 2 }, to: { x: t.x + 40, y: t.y + t.height * 0.75 } };
  });
  await page.mouse.move(drag.from.x, drag.from.y); await page.mouse.down();
  await page.mouse.move(drag.from.x, drag.from.y + 10, { steps: 3 });
  await page.mouse.move(drag.to.x, drag.to.y, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(150);
  r = await rows();
  check('a drag by the grip reorders too', r[0].id === base.fifth.id && r[3].id === base.first.id && r[3].n === '4', r.slice(0, 5).map(x => x.id).join(' > '));
  await page.click('#pages-list .galleries-page [data-rank-save]');
  await page.waitForSelector('#pages-list .galleries-page:not(.editing)');
  const saved = await page.evaluate(([city, id, first]) => {
    const o = JSON.parse(localStorage.getItem('galleryOrder'));
    const D = window.DemoDebug;
    return { v: o.v, first: o.cities[city].order[0], n: o.cities[city].order.length,
      rankMoved: D.venueRank({ id }), rankOld: D.venueRank({ id: first }), tierOld: D.galleryTier({ id: first }),
      sub: document.querySelector('#pages-list .galleries-page .rank-sub').textContent,
      feedFirst: D.filteredShows()[0] && D.filteredShows()[0].venueId,
      firstOnView: window.DEMO_DATA.shows.some(s => s.venueId === id && D.isActiveShow(s)) };
  }, [CITY, base.fifth.id, base.first.id]);
  check('Save stores the order per city (versioned)', saved.v === 1 && saved.first === base.fifth.id && saved.n === base.n, JSON.stringify(saved));
  check('the saved order is the rank the app uses', saved.rankMoved === 1 && saved.rankOld === 4 && saved.tierOld === 'top' && /^Your ranking/.test(saved.sub), JSON.stringify(saved));
  check('the feed follows the personal ranking', !saved.firstOnView || saved.feedFirst === base.fifth.id, `feed leads with ${saved.feedFirst}`);
  check('Reset appears once a personal ranking exists', !(await page.$eval('#pages-list .galleries-page [data-rank-reset]', e => e.hidden)));

  // 5. the gallery page pill reflects the personal rank; Cancel discards an edit
  await page.click(`#pages-list .galleries-page .rank-row[data-id="${base.fifth.id}"] .rr-main`);
  await page.waitForSelector('#pages-list .venue-page .tier-pill');
  const pill = await page.$eval('#pages-list .venue-page .tier-pill', e => e.textContent);
  check('the venue page pill shows #1 · Top 20 · your ranking', pill === '#1 · Top 20 · your ranking', pill);
  await page.click('#pages-list .venue-page .navrow .circle-btn');
  await page.waitForTimeout(350);
  await page.click('#pages-list .galleries-page [data-rank-edit]');
  await page.waitForSelector('#pages-list .galleries-page.editing');
  await page.click(`#pages-list .galleries-page .rank-row[data-id="${base.first.id}"] .rank-n`);
  await page.waitForSelector('.sheet.open .move-sheet');
  await page.click('.sheet.open [data-move="top"]');
  await sheetGone();
  await page.click('#pages-list .galleries-page [data-rank-cancel]');
  await page.waitForSelector('#pages-list .galleries-page:not(.editing)');
  r = await rows();
  check('Cancel discards the edit', r[0].id === base.fifth.id && (await page.evaluate(([city]) => JSON.parse(localStorage.getItem('galleryOrder')).cities[city].order[0], [CITY])) === base.fifth.id);

  // 6. the personal ranking survives a reload; Reset returns to the app's
  await page.reload();
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .lib-pinned .lib-row');
  check('after a reload the app still ranks by the saved order', (await page.evaluate(id => window.DemoDebug.venueRank({ id }), base.fifth.id)) === 1);
  await page.click('#pages-list [data-settings-btn]');
  await page.waitForSelector('#pages-list .settings-page [data-setting="ranking"]');
  st = await rowsText();
  check('Settings reports Your ranking and one favorite', st[0].startsWith('ranking: Your ranking') && st[1] === 'favorites: 1 gallery', st.join(' | '));
  await page.click('#pages-list .settings-page [data-setting="ranking"]');
  await page.waitForSelector('#pages-list .galleries-page .rank-row');
  page.once('dialog', d => d.accept());
  await page.click('#pages-list .galleries-page [data-rank-reset]');
  await page.waitForTimeout(300);
  r = await rows();
  check('Reset restores the app ranking', r[0].id === base.first.id && (await page.evaluate(() => JSON.parse(localStorage.getItem('galleryOrder')).cities)) && /^App ranking/.test(await page.$eval('#pages-list .galleries-page .rank-sub', e => e.textContent)));
  check('…and the venue ranks follow', (await page.evaluate(id => window.DemoDebug.venueRank({ id }), base.fifth.id)) === 5);
  await page.evaluate(() => { const root = document.getElementById('pages-list'); while (root.children.length > 1) root.lastElementChild.remove(); });

  // 7. Favorite galleries: the default list, the filter context, the map
  await page.evaluate(() => document.querySelector('#pages-list .page').refresh());
  const favRow = await page.$eval('#pages-list .lib-row.favorites .lib-sub', e => e.textContent);
  check('the library\'s Favorite galleries row counts the shows on view at the favorite', favRow === `${base.withShowShows} show${base.withShowShows === 1 ? '' : 's'} on view · 1 gallery`, favRow);
  await page.click('#pages-list .lib-row.favorites');
  await page.waitForSelector('#pages-list .list-page .list-row');
  const fl = await page.evaluate(() => ({
    name: document.querySelector('#pages-list .list-page .lh-name').textContent,
    rows: document.querySelectorAll('#pages-list .list-page .list-row').length,
    acts: [...document.querySelectorAll('#pages-list .list-page .lh-actions .act')].map(e => e.dataset.act),
    venues: new Set([...document.querySelectorAll('#pages-list .list-page .list-row .sr-venue .sr-txt')].map(e => e.textContent)).size,
  }));
  check('the Favorite galleries list holds the favorite\'s shows, with Map and Galleries actions',
    fl.name === 'Favorite galleries' && fl.rows === base.withShowShows && fl.venues === 1 && fl.acts.join(',') === 'Map,Galleries', JSON.stringify(fl));
  await page.click('#pages-list .list-page .lh-actions .act[data-act="Galleries"]');
  await page.waitForSelector('#pages-list .galleries-page .rank-row');
  check('Galleries opens the ranking page', !!(await page.$('#pages-list .galleries-page')));
  await page.evaluate(() => { const root = document.getElementById('pages-list'); while (root.children.length > 1) root.lastElementChild.remove(); });

  await page.click('.tab-btn[data-tab="featured"]');
  await page.click('#pages-featured .icon-btn[data-filter-btn]');
  await page.waitForSelector('.sheet.open .filter-sheet');
  const opts = await page.$$eval('.sheet.open [data-list-group] .row .cr-name', els => els.map(e => e.textContent));
  check('the filter\'s List group offers Favorite galleries', opts.includes('Favorite galleries'), opts.join(','));
  await page.click('.sheet.open [data-list-group] .row[data-list-option="favorites"]');
  await page.waitForTimeout(150);
  check('done button counts the favorite\'s shows', (await page.$eval('.sheet.open .sheet-foot .capsule-btn', e => e.textContent)) === `Show ${base.withShowShows} show${base.withShowShows === 1 ? '' : 's'}`);
  await page.click('.sheet.open .sheet-foot .capsule-btn');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  check('Featured shows the context bar and only the favorite\'s shows',
    (await page.$eval('#pages-featured .ctx-bar .ctx-text', e => e.textContent)) === 'Favorite galleries'
      && (await page.$$('#pages-featured .feed .card')).length === 1);
  await page.click('.tab-btn[data-tab="map"]');
  await page.waitForFunction(() => window.__demoMap && window.__demoMap.isStyleLoaded() && window.__demoMap.getLayer('ctx-dot'));
  await page.waitForFunction(() => window.__demoMap.getLayoutProperty('ctx-dot', 'visibility') === 'visible');
  check('the Map highlights the favorite gallery as the context', await page.evaluate(() =>
    document.querySelector('#map-ctx .ctx-text').textContent === 'Favorite galleries'
      && window.__demoMap.getSource('ctx')._data.features.filter(f => f.properties.on).length === 1));
  await page.click('#map-ctx .ctx-x');
  await page.waitForFunction(() => window.__demoMap.getLayoutProperty('gal-dot', 'visibility') !== 'none');

  // 8. unfavoriting from the ranking sheet on the Map drops the list
  await page.click('#map-rank-btn');
  await page.waitForSelector('.sheet.open .galleries-page .rank-row');
  await page.click(`.sheet.open .galleries-page .rank-row[data-id="${base.withShow.id}"] .fav-btn`);
  await page.waitForTimeout(100);
  check('unfavoriting clears the favorites', (await page.evaluate(() => JSON.parse(localStorage.getItem('favoriteVenueIDs')))).length === 0);
  await page.click('.sheet.open .galleries-page .navrow .circle-btn');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  await page.click('.tab-btn[data-tab="list"]');
  check('the library row goes back to its empty hint', /heart/i.test(await page.$eval('#pages-list .lib-row.favorites .lib-sub', e => e.textContent)));

  await browser.close();
  server.close();
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
