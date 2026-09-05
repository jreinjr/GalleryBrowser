/* Lists tab checks against a built dist (default webdemo/dist/gallery-browser-demo, override with DIST=<dir>).
 * Headless Chrome via the globally installed Playwright:
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/lists.test.js
 */
const path = require('path');
const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = process.env.DIST || path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8936;
const CITY = 'los-angeles';
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.json': 'application/json' };

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
    try { if (sessionStorage.getItem('lists-test-inited')) return; sessionStorage.setItem('lists-test-inited', '1'); } catch (e) { return; }
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

  const data = await page.evaluate(city => {
    const all = window.DEMO_DATA.shows.filter(s => s.city === city && window.DemoDebug.isActiveShow(s));
    // the default filter: running gallery shows, best-ranked gallery first
    const featGal = window.DemoDebug.filteredShows();
    const ended = window.DEMO_DATA.shows.find(s => s.city === city && s.endDate && s.endDate < '2026-01-01') || window.DEMO_DATA.shows.filter(s => s.city === city).sort((a, b) => (a.endDate || '9') < (b.endDate || '9') ? -1 : 1)[0];
    return {
      city: window.DEMO_DATA.cities.find(c => c.key === city).displayName,
      featGal: featGal.length,
      first: featGal[0].slug,
      second: featGal[1].slug,
      curated: window.DemoDebug.curatedLists().map(l => l.id),
      curatedNames: window.DemoDebug.curatedLists().map(l => l.name),
      endedSlug: ended && !window.DemoDebug.isActiveShow(ended) ? ended.slug : null,
      filterVersion: window.DemoDebug.FILTER_VERSION,
    };
  }, CITY);
  const pinned = () => page.$$eval('#pages-list .lib-pinned .lib-row .lib-name', els => els.map(e => e.textContent));
  const tabs = await page.$$eval('#tabbar .tab-btn span:last-child', els => els.map(e => e.textContent));

  // 1. shell
  check('four tabs: Featured, Lists, Map, Discover', tabs.join(',') === 'Featured,Lists,Map,Discover', tabs.join(','));
  check('Lists tab header is the city', (await page.$eval('#pages-list .large-title', e => e.textContent)) === data.city);
  check('filter version bumped to 7 (no show rank)', data.filterVersion === 7);

  // 2. library at defaults: All shows and the Favorite galleries default list
  let p = await pinned();
  check('"All shows in <City>" and "Favorite galleries" pinned at defaults', p.length === 2 && p[0] === `All shows in ${data.city}` && p[1] === 'Favorite galleries', p.join(' | '));
  check('the empty favorites row explains the heart', /heart/i.test(await page.$eval('#pages-list .lib-row.favorites .lib-sub', e => e.textContent)));
  check('a settings gear sits beside the filter button', !!(await page.$('#pages-list .navrow .nav-btns [data-settings-btn] + [data-filter-btn]')));
  check('no Your lists section at defaults', await page.$eval('#pages-list .lib-grid', e => e.hidden));
  check("Curated starts with Top 20 galleries (no Editor's Picks), no Museums this month, no Recent asks", data.curated[0] === 'c-top' && !data.curated.includes('c-picks') && !data.curatedNames.some(n => /museum/i.test(n)) && !(await page.$('#pages-list .ask-row')), data.curatedNames.join(' | '));
  check('curated shelf shows the curated lists with Save pills', (await page.$$('#pages-list .lib-shelf .lib-tile .tile-save')).length === data.curated.length);
  check('See all button beside Curated', !!(await page.$('#pages-list [data-see-all]')));
  const sub = await page.$eval('#pages-list .lib-pinned .lib-row.all .lib-sub', e => e.textContent);
  check('All shows row counts the filtered set', sub.startsWith(`${data.featGal} shows`), sub);

  // 3. All shows opens the flat list
  await page.click('#pages-list .lib-pinned .lib-row.all');
  await page.waitForSelector('#pages-list .list-results .show-row');
  const rows = await page.$$('#pages-list .list-results .show-row');
  check('All shows page lists the featured gallery shows', rows.length === data.featGal, `${rows.length} / ${data.featGal}`);
  check('All shows page has the filter button', !!(await page.$('#pages-list .page:last-child .icon-btn[data-filter-btn]')));

  // 4. bookmark -> toast -> add-to-list sheet -> new list
  await page.click('#pages-list .list-results .show-row .bookmark-btn');
  await page.waitForSelector('.toast.show');
  check('bookmarking shows the toast with Add to list', (await page.$eval('.toast', e => e.textContent)).includes('Add to list'));
  await page.click('.toast button');
  await page.waitForSelector('.sheet.open .add-sheet');
  check('add sheet lists My Shows as always-on', await page.$eval('.sheet.open [data-list="saved"] .check-ring', e => e.classList.contains('on')));
  await page.fill('.sheet.open .new-list .new-name', 'Ceramics to see');
  await page.click('.sheet.open .new-list .create');
  await page.waitForSelector('.sheet.open [data-list^="l-"]');
  check('creating a list from the sheet adds it, checked', await page.$eval('.sheet.open [data-list^="l-"] .check-ring', e => e.classList.contains('on')));
  await page.click('.sheet.open .sheet-foot .capsule-btn');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  await page.click('#pages-list .page:last-child .navrow .circle-btn');   // back to the library
  await page.waitForTimeout(350);
  p = await pinned();
  check('My Shows appears once something is saved, before Favorite galleries', p.length === 3 && p[1] === 'My Shows' && p[2] === 'Favorite galleries', p.join(' | '));
  check('Your lists appears with the new list', !(await page.$eval('#pages-list .lib-grid', e => e.hidden)) && (await page.$$eval('#pages-list .lib-grid .lib-name', els => els.map(e => e.textContent))).includes('Ceramics to see'));

  // 5. list detail: caret byline, Map/Edit actions, no Featured/Share
  await page.click('#pages-list .lib-grid .lib-tile button');
  await page.waitForSelector('#pages-list .list-page .list-row');
  const acts = await page.$$eval('#pages-list .list-page .lh-actions .act', els => els.map(e => e.dataset.act));
  check('owned list actions are Map, Edit and Remove (no Featured, no Share)', acts.join(',') === 'Map,Edit,Remove', acts.join(','));
  check('rows carry a caret, not a bookmark', (await page.$$('#pages-list .list-page .list-row .lr-caret')).length === 1 && !(await page.$('#pages-list .list-page .list-row .bookmark-btn')));
  const bylineHidden = await page.$eval('#pages-list .list-page .list-row .lr-byline', e => getComputedStyle(e).display === 'none');
  await page.click('#pages-list .list-page .list-row .lr-caret');
  const bylineShown = await page.$eval('#pages-list .list-page .list-row .lr-byline', e => getComputedStyle(e).display !== 'none' && e.textContent.length > 10);
  check('caret expands the byline', bylineHidden && bylineShown);

  // 6. edit: rename, add a second show, remove, persist
  await page.click('#pages-list .list-page .lh-actions .act[data-act="Edit"]');
  await page.waitForSelector('#pages-list .list-hero.edit .edit-name');
  await page.fill('#pages-list .list-hero.edit .edit-name', 'Ceramics, renamed');
  await page.fill('#pages-list .list-hero.edit .edit-desc', 'Clay across town.');
  await page.click('#pages-list .add-row');
  await page.waitForSelector('.sheet.open .picker-list .row');
  const pickSel = `.sheet.open .picker-list .row[data-pick="${CITY}/${data.second}"]`;
  const pickFound = !!(await page.$(pickSel));
  if (pickFound) await page.click(pickSel);
  const afterPick = await page.evaluate(() => JSON.parse(localStorage.getItem('lists')).lists[0].entries.length);
  await page.click('.sheet.open .sheet-foot .capsule-btn');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  try {
    await page.waitForFunction(() => document.querySelectorAll('#pages-list .page:last-child .list-rows .list-row').length === 2, null, { timeout: 5000 });
  } catch (e) {
    const rowsNow = await page.$$eval('#pages-list .page:last-child .list-rows .list-row', els => els.map(r => r.dataset.id));
    check('picker adds the second show', false, `picker row found ${pickFound}, entries after pick ${afterPick}, rows now ${JSON.stringify(rowsNow)}, second ${data.second}`);
  }
  await page.click('#pages-list .navrow .nav-textbtn.bold');   // Done
  await page.waitForTimeout(350);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('lists')));
  check('edit persisted name, description and membership', stored.v === 1 && stored.lists[0].name === 'Ceramics, renamed' && stored.lists[0].desc === 'Clay across town.' && stored.lists[0].entries.length === 2, JSON.stringify(stored.lists[0]).slice(0, 160));
  check('list detail shows the new description', (await page.$eval('#pages-list .list-page .lh-desc', e => e.textContent)) === 'Clay across town.');

  // 7. filter List group replaces Saved only and restricts rows on Featured
  await page.click('.tab-btn[data-tab="featured"]');
  await page.click('#pages-featured .icon-btn[data-filter-btn]');
  await page.waitForSelector('.sheet.open .filter-sheet');
  check('filter sheet has no Saved-only switch', !(await page.$('.sheet.open [data-switch="saved"]')));
  const options = await page.$$eval('.sheet.open [data-list-group] .row .cr-name', els => els.map(e => e.textContent));
  check('List group offers All shows, My Shows and the saved list, not curated ones', options.join(',') === 'All shows,My Shows,Ceramics, renamed', options.join(','));
  await page.click('.sheet.open [data-list-group] .row[data-list-option^="l-"]');
  await page.waitForTimeout(150);
  check('done button counts the list', (await page.$eval('.sheet.open .sheet-foot .capsule-btn', e => e.textContent)) === 'Show 2 shows');
  await page.click('.sheet.open .sheet-foot .capsule-btn');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  check('Featured shows the context bar with the list name', (await page.$eval('#pages-featured .ctx-bar .ctx-text', e => e.textContent)) === 'Ceramics, renamed');
  check('Featured feed is restricted to the list', (await page.$$('#pages-featured .feed .card')).length === 2);
  check('badge counts the list', (await page.$eval('#pages-featured .icon-btn .badge', e => e.textContent)) === '1');

  // 8. Map pill and context layer
  await page.click('.tab-btn[data-tab="map"]');
  await page.waitForFunction(() => window.__demoMap && window.__demoMap.isStyleLoaded() && window.__demoMap.getLayer('ctx-dot'));
  await page.waitForFunction(() => window.__demoMap.getLayoutProperty('ctx-dot', 'visibility') === 'visible');
  check('Map shows the context pill', !(await page.$eval('#map-ctx', e => e.hidden)) && (await page.$eval('#map-ctx .ctx-text', e => e.textContent)) === 'Ceramics, renamed');
  check('gallery dots hidden, context dots shown', await page.evaluate(() => window.__demoMap.getLayoutProperty('gal-dot', 'visibility') === 'none' && window.__demoMap.getSource('ctx')._data.features.filter(f => f.properties.on).length === 2));
  await page.click('#map-ctx .ctx-x');
  await page.waitForFunction(() => window.__demoMap.getLayoutProperty('gal-dot', 'visibility') !== 'none');
  check('× clears the context on the Map', await page.$eval('#map-ctx', e => e.hidden));
  check('clearing returns Featured to All shows', await page.evaluate(() => JSON.parse(localStorage.getItem('filter')).list === null));

  // 9. curated Save pill copies into Your lists; curated page
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForTimeout(100);
  check('a pushed page does not slide in again when its tab is re-shown', await page.evaluate(() => document.querySelector('#pages-list .page:last-child .navrow .circle-btn').getBoundingClientRect().x < 100));
  await page.click('#pages-list .page:last-child .navrow .circle-btn');   // back from the list page to the library
  await page.waitForTimeout(350);
  check('back pops the list page even after a re-render from another tab', (await page.$$('#pages-list > .page')).length === 1);
  await page.click('#pages-list .lib-shelf .lib-tile .tile-save');
  await page.waitForFunction(() => document.querySelectorAll('#pages-list .lib-grid .lib-tile').length === 2);
  check('Save on a curated tile copies it into Your lists', (await page.$eval('#pages-list .lib-shelf .lib-tile .tile-save', e => e.textContent)).includes('Saved'));

  // 9b. Remove: an owned list's detail page offers Remove (confirmed), the library drops it
  await page.click('#pages-list .lib-grid .lib-tile:first-child button');
  await page.waitForSelector('#pages-list .list-page .lh-actions .act[data-act="Remove"]');
  const actsOwned = await page.$$eval('#pages-list .list-page .lh-actions .act', els => els.map(e => e.dataset.act));
  check('owned list actions are Map, Edit, Remove', actsOwned.join(',') === 'Map,Edit,Remove', actsOwned.join(','));
  page.once('dialog', d => d.dismiss());
  await page.click('#pages-list .list-page .lh-actions .act[data-act="Remove"]');
  await page.waitForTimeout(300);
  check('cancelling the confirm keeps the list', (await page.$$('#pages-list > .page')).length === 2 && (await page.evaluate(() => JSON.parse(localStorage.getItem('lists')).lists.length)) === 2);
  page.once('dialog', d => d.accept());
  await page.click('#pages-list .list-page .lh-actions .act[data-act="Remove"]');
  await page.waitForFunction(() => document.querySelectorAll('#pages-list > .page').length === 1);
  await page.waitForTimeout(200);
  check('confirming removes the list and returns to the library', (await page.evaluate(() => JSON.parse(localStorage.getItem('lists')).lists.length)) === 1 && (await page.$$('#pages-list .lib-grid .lib-tile')).length === 1);
  check('the curated tile offers Save again once its copy is gone', (await page.$eval('#pages-list .lib-shelf .lib-tile .tile-save', e => e.textContent)).includes('Save') && !(await page.$eval('#pages-list .lib-shelf .lib-tile .tile-save', e => e.textContent)).includes('Saved'));
  await page.click('#pages-list [data-see-all]');
  await page.waitForSelector('#pages-list .guide-hero');
  check('curated page leads with Top 20 galleries', (await page.$eval('#pages-list .guide-hero .gh-name', e => e.textContent)) === 'Top 20 galleries');
  await page.click('#pages-list .page:last-child .navrow .circle-btn');
  await page.waitForTimeout(350);

  // 10. hidden-when-ended: a list whose only show has closed disappears
  if (data.endedSlug) {
    await page.evaluate(([city, slug]) => {
      const o = JSON.parse(localStorage.getItem('lists'));
      o.lists.push({ id: 'l-ended', name: 'Old list', desc: '', kind: 'user', city, createdAt: 1, updatedAt: 1, cover: null, entries: [{ id: city + '/' + slug, note: null }], source: null });
      localStorage.setItem('lists', JSON.stringify(o));
    }, [CITY, data.endedSlug]);
    await page.reload();
    await page.click('.tab-btn[data-tab="list"]');
    await page.waitForSelector('#pages-list .lib-grid .lib-tile');
    const names = await page.$$eval('#pages-list .lib-grid .lib-name', els => els.map(e => e.textContent));
    check('a list whose shows all ended is hidden from the library', !names.includes('Old list') && names.length === 1, names.join(' | '));
    await page.click('#pages-featured .icon-btn[data-filter-btn]').catch(() => {});
    await page.click('.tab-btn[data-tab="featured"]');
    await page.click('#pages-featured .icon-btn[data-filter-btn]');
    await page.waitForSelector('.sheet.open .filter-sheet');
    const opts = await page.$$eval('.sheet.open [data-list-group] .row .cr-name', els => els.map(e => e.textContent));
    check('…and from the filter picker', !opts.includes('Old list'), opts.join(','));
  } else {
    check('hidden-when-ended (skipped: no ended show in the payload)', true);
  }

  await browser.close();
  server.close();
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
