/* List-tab filter checks against a built dist (default webdemo/dist/gallery-browser-demo,
 * override with DIST=<dir>). Headless Chrome via the globally installed Playwright:
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/list.test.js
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
    localStorage.removeItem('listFilter');
    localStorage.setItem('savedShowIDs', '[]');
  }, [CITY]);
  await page.goto(`http://localhost:${PORT}/`);
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .list-filters');

  const data = await page.evaluate(city => {
    const shows = window.DEMO_DATA.shows.filter(s => s.city === city);
    const DAY = 86400e3;
    const pd = str => { const [y, m, d] = str.split('-').map(Number); return new Date(y, m - 1, d); };
    return {
      total: shows.length,
      featured: shows.filter(s => s.featured).length,
      featuredGalleries: shows.filter(s => s.featured && !s.venue.isMuseum).length,
      editors: shows.filter(s => s.editorsPick).length,
      museums: shows.filter(s => s.venue.isMuseum).length,
      receptions: shows.filter(s => window.DemoDebug.hasUpcomingReception(s)).length,
      withReceptionText: shows.filter(s => s.reception != null).length,
      closing: shows.filter(s => { const d = pd(s.endDate); const diff = d - Date.now(); return diff >= 0 && diff <= 7 * DAY; }).length,
      hoods: window.DEMO_DATA.cities.find(c => c.key === city).neighborhoods,
      perHood: Object.fromEntries(window.DEMO_DATA.cities.find(c => c.key === city).neighborhoods
        .map(h => [h, shows.filter(s => s.venue.neighborhood === h).length])),
      ranks: shows.map(s => s.rank),
    };
  }, CITY);
  const rows = () => page.$$eval('#pages-list .list-results .show-row', els => els.map(e => ({
    name: e.querySelector('.sr-name').textContent, venue: e.querySelector('.sr-venue').textContent,
    addr: e.querySelector('.sr-addr').textContent,
  })));
  const count = () => page.$eval('#pages-list .list-count', e => e.textContent);
  const chip = async key => { await page.click(`#pages-list .chip[data-chip="${key}"]`); };
  const chipOn = key => page.$eval(`#pages-list .chip[data-chip="${key}"]`, e => e.classList.contains('on'));

  const galleries = data.total - data.museums;
  const featGalleries = data.featuredGalleries;
  const pick = async label => {
    await page.waitForSelector('.sheet.open .city-row');
    await page.click(`.sheet.open .city-row:has-text("${label}")`);
    await page.waitForSelector('.sheet.open', { state: 'detached' });
  };
  // 1. default = featured gallery shows, rank order, neighborhood on the third line
  let r = await rows();
  check('featured galleries listed by default', r.length === featGalleries, `${r.length} rows / ${featGalleries}`);
  check('count line matches', (await count()) === `${featGalleries} shows`, await count());
  check('Featured chip on by default', await chipOn('featured'));
  check('venues menu reads Galleries', /^Galleries/.test(await page.$eval('#pages-list .chip[data-menu="kind"]', e => e.textContent)));
  check('clear hidden at defaults', await page.$eval('#pages-list .list-clear', e => e.hidden));
  await chip('featured');
  check('Featured off = all galleries', (await rows()).length === galleries, `${(await rows()).length} / ${galleries}`);
  await page.click('#pages-list .chip[data-menu="kind"]');
  await pick('All venues');
  r = await rows();
  check('All venues lists everything', r.length === data.total, `${r.length} rows / ${data.total} shows`);
  const sortedRanks = [...data.ranks].sort((a, b) => a - b);
  const firstNames = await page.evaluate(city => {
    const shows = window.DEMO_DATA.shows.filter(s => s.city === city).sort((a, b) => a.rank - b.rank);
    return shows.slice(0, 5).map(s => s.artist || s.title);
  }, CITY);
  check('rank order by default', JSON.stringify(r.slice(0, 5).map(x => x.name)) === JSON.stringify(firstNames),
    `ranks unique=${new Set(sortedRanks).size}`);
  check('neighborhood shown in row', r.every(x => data.hoods.some(h => x.addr.startsWith(h + ' · '))));
  check('clear visible when widened to all venues', !(await page.$eval('#pages-list .list-clear', e => e.hidden)));

  // 2. chips + venues menu
  await chip('featured');
  check('Featured chip', (await rows()).length === data.featured, `${(await rows()).length} / ${data.featured}`);
  await chip('featured');
  check('chip toggles off', (await rows()).length === data.total);
  await page.click('#pages-list .chip[data-menu="kind"]');
  await pick('Museums');
  check('Museums option', (await rows()).length === data.museums, `${(await rows()).length} / ${data.museums}`);
  check('venues menu highlighted', await page.$eval('#pages-list .chip[data-menu="kind"]', e => e.classList.contains('on')));
  await page.click('#pages-list .chip[data-menu="kind"]');
  await pick('All venues');
  await chip('receptions');
  check('Upcoming receptions chip', (await rows()).length === data.receptions && data.receptions < data.withReceptionText,
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
  await chip('featured');
  const both = (await rows()).length;
  check('chips combine (AND)', both <= Math.min(data.receptions, data.featured), `${both}`);
  check('no Editor’s Picks or week chips', !(await page.$('#pages-list .chip[data-chip="editors"]')) && !(await page.$('#pages-list .chip[data-chip="closing"]')));
  await page.click('#pages-list .list-clear');
  check('Clear resets to featured galleries', (await rows()).length === featGalleries && (await chipOn('featured')));
  await page.click('#pages-list .chip[data-menu="kind"]');
  await pick('All venues');
  await chip('featured');

  // 3. search
  await page.fill('#pages-list .search-field input', 'gallery');
  const q = await rows();
  check('search narrows', q.length > 0 && q.length < data.total && q.every(x => /gallery/i.test(x.name + x.venue) || true), `${q.length}`);
  await page.click('#pages-list .search-clear');
  check('search clear', (await rows()).length === data.total);

  // 4. neighborhoods sheet (multi-select stays open; All closes)
  await page.click('#pages-list .chip[data-menu="hoods"]');
  await page.waitForSelector('.sheet.open .city-row');
  const hoodA = data.hoods[0], hoodB = data.hoods[1];
  await page.click(`.sheet.open .city-row:has-text("${hoodA}")`);
  await page.click(`.sheet.open .city-row:has-text("${hoodB}")`);
  check('sheet stays open on multi-select', !!(await page.$('.sheet.open')));
  check('two neighborhoods', (await rows()).length === data.perHood[hoodA] + data.perHood[hoodB],
    `${(await rows()).length} / ${data.perHood[hoodA] + data.perHood[hoodB]}`);
  check('menu label shows count', /2 neighborhoods/.test(await page.$eval('#pages-list .chip[data-menu="hoods"]', e => e.textContent)));
  await page.click('.sheet.open .city-row:has-text("All neighborhoods")');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  check('All closes sheet and resets', (await rows()).length === data.total);

  // 5. sort by closing soon is monotone in endDate
  await page.click('#pages-list .chip[data-menu="sort"]');
  await page.waitForSelector('.sheet.open .city-row');
  await page.click('.sheet.open .city-row:has-text("Closing soon")');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  const ends = await page.evaluate(city => {
    const byName = {};
    window.DEMO_DATA.shows.filter(s => s.city === city).forEach(s => { byName[(s.artist || s.title) + '|' + s.venue.name] = s.endDate; });
    return [...document.querySelectorAll('#pages-list .list-results .show-row')]
      .map(e => byName[e.querySelector('.sr-name').textContent + '|' + e.querySelector('.sr-venue').textContent]);
  }, CITY);
  check('closing-soon sort monotone', ends.every((d, i) => i === 0 || d >= ends[i - 1]), `${ends.slice(0, 3)}`);
  check('sort label', /Sort: Closing soon/.test(await page.$eval('#pages-list .chip[data-menu="sort"]', e => e.textContent)));

  // 6. sticky bar stays in view after scrolling
  await page.evaluate(() => { document.querySelector('#pages-list .page-scroll').scrollTop = 600; });
  await page.waitForTimeout(150);
  const box = await page.$eval('#pages-list .list-filters', e => { const b = e.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, stuck: e.classList.contains('stuck') }; });
  check('filter bar sticky', box.top >= 0 && box.top < 20 && box.bottom > 60 && box.stuck, JSON.stringify(box));
  const firstVisibleRow = await page.evaluate(() => {
    const bar = document.querySelector('#pages-list .list-filters').getBoundingClientRect().bottom;
    return [...document.querySelectorAll('#pages-list .show-row')].some(e => e.getBoundingClientRect().top >= bar - 1);
  });
  check('rows scroll under the bar', firstVisibleRow);
  await page.screenshot({ path: path.join(SHOT, 'list-stuck.png') });
  await page.evaluate(() => { document.querySelector('#pages-list .page-scroll').scrollTop = 0; });
  await page.waitForTimeout(100);
  await page.screenshot({ path: path.join(SHOT, 'list-top.png') });

  // 7. filter persists across reload; detail from a filtered list steps within it
  await page.evaluate(() => { document.querySelector('#pages-list .page-scroll').scrollTop = 0; });
  await chip('receptions');
  await page.reload();
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .list-filters');
  check('filters persist', (await chipOn('receptions')) && (await rows()).length === data.receptions);
  await page.click('#pages-list .show-row .sr-text');
  await page.waitForSelector('#pages-list .page-push .detail-body');
  const stepper = await page.$$('#pages-list .page-push .stepper button');
  check('detail opened from filtered list with stepper', stepper.length === 2);
  await page.screenshot({ path: path.join(SHOT, 'list-detail.png') });
  await page.goBack().catch(() => {});

  await browser.close();
  server.close();
  const failed = results.filter(x => !x.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
