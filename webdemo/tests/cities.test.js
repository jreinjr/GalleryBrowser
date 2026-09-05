/* Cities sheet checks against a built dist (DIST=<dir> overrides
 * webdemo/dist/gallery-browser-demo). The sheet lists the shown cities in the
 * person's order (the seven founding cities out of the box); Edit lists every
 * city with a grip and an eye; Save keeps order and visibility in
 * localStorage['cityOrder']; Cancel discards; Reset returns to the defaults.
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/cities.test.js
 */
const path = require('path');
const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = process.env.DIST || path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8938;
const CITY = 'los-angeles';
const DEFAULTS = ['seattle', 'new-york', 'los-angeles', 'tokyo', 'berlin', 'london', 'paris'];
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
  page.on('dialog', d => d.accept());
  await page.addInitScript(([city]) => {
    try { if (sessionStorage.getItem('cities-test-inited')) return; sessionStorage.setItem('cities-test-inited', '1'); } catch (e) { return; }
    localStorage.setItem('selectedCityKey', city);
    localStorage.removeItem('filter');
    localStorage.removeItem('lists');
    localStorage.removeItem('galleryOrder');
    localStorage.removeItem('cityOrder');
    localStorage.setItem('savedShowIDs', '[]');
    localStorage.setItem('favoriteVenueIDs', '[]');
  }, [CITY]);
  await page.goto(`http://localhost:${PORT}/`);
  await page.waitForSelector('#pages-featured .nav-textbtn');

  const allKeys = await page.evaluate(() => window.DEMO_DATA.cities.map(c => c.key));
  check('the build carries more cities than the seven defaults', allKeys.length > DEFAULTS.length && DEFAULTS.every(k => allKeys.includes(k)), `${allKeys.length} cities`);
  const extra = allKeys.find(k => !DEFAULTS.includes(k));

  const sheetGone = () => page.waitForFunction(() => !document.querySelector('#sheet-root .sheet, #sheet-root .sheet-backdrop'));
  const openSheet = async () => { await page.click('#pages-featured .nav-textbtn'); await page.waitForSelector('.sheet.open .cities-page .city-row'); };
  const rows = () => page.$$eval('.sheet.open .cities-page .city-row', els => els.map(e => ({
    key: e.dataset.city, name: e.querySelector('.cr-name').textContent, check: !!e.querySelector('.check'),
    grip: !!e.querySelector('.grip'), eye: e.querySelector('.eye-btn') ? e.querySelector('.eye-btn').classList.contains('on') : null,
    note: !!e.querySelector('.cr-note'),
  })));

  // 1. out of the box: the seven defaults in build order, no notes, the current city checked
  await openSheet();
  let r = await rows();
  check('the sheet lists the seven default cities in the build\'s order', r.map(x => x.key).join(',') === DEFAULTS.join(','), r.map(x => x.key).join(','));
  check('no availability note under any city', r.every(x => !x.note));
  check('the current city is checked; no grips or eyes outside Edit', r.filter(x => x.check).length === 1 && r.find(x => x.check).key === CITY && r.every(x => !x.grip && x.eye === null));
  check('Edit is offered; Reset is hidden without saved preferences', !!(await page.$('.sheet.open [data-city-edit]')) && (await page.$eval('.sheet.open [data-city-reset]', e => e.hidden)));

  // 2. Edit lists every city with a grip and an eye; hidden ones are off
  await page.click('.sheet.open [data-city-edit]');
  await page.waitForSelector('.sheet.open .cities-page.editing .city-row .grip');
  r = await rows();
  check('Edit lists every city of the build with grips, Save and Cancel', r.length === allKeys.length && r.map(x => x.key).join(',') === allKeys.join(',') && r.every(x => x.grip)
    && !!(await page.$('.sheet.open [data-city-save]')) && !!(await page.$('.sheet.open [data-city-cancel]')), `${r.length} rows`);
  check('the eye is on for the defaults and off for the rest', r.every(x => x.eye === DEFAULTS.includes(x.key)));

  // 3. show an extra city, hide Berlin, drag Paris to the top
  await page.click(`.sheet.open .city-row[data-city="${extra}"] .eye-btn`);
  await page.click('.sheet.open .city-row[data-city="berlin"] .eye-btn');
  r = await rows();
  check('the eye toggles a city on and off', r.find(x => x.key === extra).eye === true && r.find(x => x.key === 'berlin').eye === false);
  const gripBox = await page.$eval('.sheet.open .city-row[data-city="paris"] .grip', e => { const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; });
  const topBox = await page.$eval('.sheet.open .city-row[data-city="seattle"]', e => { const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + 4 }; });
  await page.mouse.move(gripBox.x, gripBox.y);
  await page.mouse.down();
  await page.mouse.move(gripBox.x, gripBox.y - 20, { steps: 4 });
  await page.mouse.move(topBox.x, topBox.y, { steps: 8 });
  await page.mouse.up();
  r = await rows();
  check('dragging a row by its grip reorders the list', r[0].key === 'paris' && r[1].key === 'seattle', r.slice(0, 3).map(x => x.key).join(','));

  // 4. Cancel discards the working copy
  await page.click('.sheet.open [data-city-cancel]');
  await page.waitForSelector('.sheet.open .cities-page:not(.editing) .city-row');
  r = await rows();
  check('Cancel discards the changes', r.map(x => x.key).join(',') === DEFAULTS.join(',') && !(await page.evaluate(() => localStorage.getItem('cityOrder'))));

  // 5. do it again and Save: the list follows, the store holds it, it survives a reload
  await page.click('.sheet.open [data-city-edit]');
  await page.waitForSelector('.sheet.open .cities-page.editing .city-row .grip');
  await page.click(`.sheet.open .city-row[data-city="${extra}"] .eye-btn`);
  await page.click('.sheet.open .city-row[data-city="berlin"] .eye-btn');
  const g2 = await page.$eval('.sheet.open .city-row[data-city="paris"] .grip', e => { const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; });
  const t2 = await page.$eval('.sheet.open .city-row[data-city="seattle"]', e => { const b = e.getBoundingClientRect(); return { x: b.x + b.width / 2, y: b.y + 4 }; });
  await page.mouse.move(g2.x, g2.y); await page.mouse.down();
  await page.mouse.move(g2.x, g2.y - 20, { steps: 4 }); await page.mouse.move(t2.x, t2.y, { steps: 8 }); await page.mouse.up();
  await page.click('.sheet.open [data-city-save]');
  await page.waitForSelector('.sheet.open .cities-page:not(.editing) .city-row');
  r = await rows();
  const expected = ['paris', 'seattle', 'new-york', 'los-angeles', 'tokyo', 'london', ...allKeys.filter(k => k === extra)];
  // the extra city keeps its build slot (after the defaults) since it was not dragged
  check('Save: the sheet shows Paris first, Berlin gone and the extra city in', r.map(x => x.key).join(',') === expected.join(','), r.map(x => x.key).join(','));
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('cityOrder')));
  check('the order and the shown set are stored in cityOrder', stored && stored.v === 1 && stored.order[0] === 'paris' && stored.order.length === allKeys.length && stored.shown.includes(extra) && !stored.shown.includes('berlin'));
  check('Reset appears once preferences are saved', !(await page.$eval('.sheet.open [data-city-reset]', e => e.hidden)));
  check('the checkmark still marks the current city', r.filter(x => x.check).length === 1 && r.find(x => x.check).key === CITY);

  // 6. picking a city switches; the sheet reopens with the saved list
  await page.click(`.sheet.open .city-row[data-city="${extra}"]`);
  await sheetGone();
  check('tapping a city switches to it', (await page.evaluate(() => localStorage.getItem('selectedCityKey'))) === extra);
  await page.reload();
  await page.waitForSelector('#pages-featured .nav-textbtn');
  await openSheet();
  r = await rows();
  check('the saved list survives a reload', r.map(x => x.key).join(',') === expected.join(',') && r.find(x => x.check).key === extra);

  // 7. Reset returns to the defaults
  await page.click('.sheet.open [data-city-reset]');
  await page.waitForFunction(() => document.querySelectorAll('.sheet.open .cities-page .city-row').length === 7);
  r = await rows();
  check('Reset restores the seven defaults and clears the store', r.map(x => x.key).join(',') === DEFAULTS.join(',') && !(await page.evaluate(() => localStorage.getItem('cityOrder'))) && (await page.$eval('.sheet.open [data-city-reset]', e => e.hidden)));

  await browser.close();
  server.close();
  const failed = results.filter(x => !x.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
