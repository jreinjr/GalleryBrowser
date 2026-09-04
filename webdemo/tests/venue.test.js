/* Venue page + gallery-rank sort checks against a built dist (DIST=<dir> overrides
 * webdemo/dist/gallery-browser-demo). No registry venue carries a blurb or rank yet, so the
 * test injects both into window.DEMO_DATA before the app boots (init script), which is the
 * same data shape build.py emits (docs/GALLERIES.md).
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/venue.test.js
 */
const path = require('path');
const http = require('http');
const fs = require('fs');
const { chromium } = require('playwright');

const ROOT = process.env.DIST || path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8934;
const CITY = 'los-angeles';
const BLURB = 'Test blurb: a small gallery on Chung King Road known for artist-run programming.';
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
  await page.addInitScript(([city, blurb]) => {
    localStorage.setItem('selectedCityKey', city);
    localStorage.removeItem('listFilter');
    localStorage.setItem('savedShowIDs', '[]');
    // data.js runs before app.js: patch DEMO_DATA the moment it appears
    let data;
    Object.defineProperty(window, 'DEMO_DATA', {
      configurable: true,
      get() { return data; },
      set(v) {
        data = v;
        const shows = v.shows.filter(s => s.city === city && s.venueId);
        // rank three venues 1..3 (in reverse file order so rank != file order), blurb on rank 1
        const ids = [...new Set(shows.map(s => s.venueId))].slice(0, 3).reverse();
        v.venues = v.venues || {};
        ids.forEach((id, i) => {
          v.venues[id] = { ...(v.venues[id] || { id }), rank: i + 1 };
          if (i === 0) v.venues[id].about = blurb;
        });
        window.__TEST = { ids, shows: shows.length };
      },
    });
  }, [CITY, BLURB]);
  await page.goto(`http://localhost:${PORT}/`);
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .icon-btn');
  const info = await page.evaluate(() => ({
    ...window.__TEST,
    venuesEmitted: Object.keys(window.DEMO_DATA.venues || {}).length,
    allHaveId: window.DEMO_DATA.shows.every(s => !s.venueId || (s.venue.id === s.venueId)),
  }));
  check('build emits a venues map', info.venuesEmitted > 0, `${info.venuesEmitted} venues`);
  check('venue.id mirrors show.venueId', info.allHaveId);

  // Gallery rank sort: widen to all venues and all shows in the filter sheet, pick the sort
  await page.click('#pages-list .icon-btn');
  await page.waitForSelector('.sheet.open .filter-sheet');
  await page.click('.sheet.open .seg-row[data-seg="showRank"] button[data-value="all"]');
  await page.click('.sheet.open .seg-row[data-seg="kind"] button[data-value="all"]');
  await page.click('.sheet.open [data-switch="active"]');   // include shows that have closed or not yet opened
  check('Gallery rank sort offered', (await page.$('.sheet.open [data-sort] .row:has-text("Gallery rank")')) != null);
  await page.click('.sheet.open [data-sort] .row:has-text("Gallery rank")');
  const sortChecked = await page.$('.sheet.open [data-sort] .row:has-text("Gallery rank") .check');
  await page.click('.sheet.open .sheet-foot .capsule-btn');
  await page.waitForSelector('.sheet.open', { state: 'detached' });
  const order = await page.evaluate(() => {
    const names = [...document.querySelectorAll('#pages-list .list-results .show-row .sr-venue .sr-txt')].map(e => e.textContent);
    const byId = Object.fromEntries(window.DEMO_DATA.shows.map(s => [s.venue.name, s.venueId]));
    return names.map(n => byId[n]);
  });
  const ranked = info.ids;
  const firstThree = [...new Set(order.filter(id => ranked.includes(id)))].slice(0, 3);   // multi-show venues repeat
  const firstRankedIdx = order.findIndex(id => ranked.includes(id));
  check('ranked venues sort first, in rank order', firstRankedIdx === 0 && JSON.stringify(firstThree) === JSON.stringify(ranked),
    `${JSON.stringify(firstThree)} vs ${JSON.stringify(ranked)}`);
  check('sort row Gallery rank checked', sortChecked != null);
  check('the gallery rank stays off the show detail venue block', await (async () => {
    await page.click('#pages-list .list-results .show-row .sr-text');
    await page.waitForSelector('#pages-list .page-push .detail-body');
    const n = await page.$$eval('#pages-list .page-push .vb-name .tier-star', els => els.length);
    await page.evaluate(() => document.querySelector('#pages-list .page-push').remove());
    return n === 0;
  })());

  // Venue page: open the rank-1 venue's show, then its venue page; Shows above the blurb
  await page.click('#pages-list .list-results .show-row');
  await page.waitForSelector('#pages-list .venue-block');
  await page.click('#pages-list .venue-block');
  await page.waitForSelector('#pages-list .venue-body');
  const about = await page.$eval('#pages-list .venue-body', body => {
    const kids = [...body.children].map(e => e.className).filter(c => !c.startsWith('tier-pill'));   // the rank pill sits under the title
    const p = body.querySelector('.venue-about');
    const pill = body.querySelector('.tier-pill');
    return { kids, text: p && p.textContent, color: p && getComputedStyle(p).color, maxw: p && getComputedStyle(p).maxWidth, pill: pill && pill.textContent };
  });
  check('venue page shows the blurb', about.text === BLURB, about.text);
  check('rank-1 venue page shows the Top Gallery pill', about.pill === 'Top Gallery', about.pill);
  check('Shows sit above the blurb, blurb above the address lines',
    about.kids.indexOf('venue-shows') === about.kids.indexOf('venue-title') + 1
      && about.kids.indexOf('venue-about') === about.kids.indexOf('venue-shows') + 1
      && about.kids.indexOf('venue-lines') === about.kids.indexOf('venue-about') + 1,
    about.kids.join(','));
  check('blurb styled (max-width set)', /ch|px/.test(about.maxw || ''), about.maxw);
  const vcards = await page.$$eval('#pages-list .venue-body .venue-show-card', els => els.map(e => ({
    h: Math.round(e.getBoundingClientRect().height),
    img: !!e.querySelector('.carousel-slide img'),
    name: (e.querySelector('.card-footer .name') || {}).textContent,
    sub: (e.querySelector('.card-footer .sub') || {}).textContent,
    save: !!e.querySelector('.bookmark-btn'),
    stars: e.querySelectorAll('.tier-star').length,
  })));
  check('venue-page shows render as one Featured-style card', vcards.length === 1
    && vcards.every(c => c.img && c.name && c.save && c.stars === 0), JSON.stringify(vcards));
  check('the card runs about two thirds the height of a Featured card',
    vcards.every(c => c.h === 248), vcards.map(c => c.h).join(','));
  check('the card footer carries the run dates, not the venue name',
    vcards.every(c => /^(Through|Opens) /.test(c.sub || '')), vcards.map(c => c.sub).join(' | '));
  check('the venue map card is a live MapLibre canvas', await (async () => {
    await page.waitForTimeout(400);
    return page.$eval('#pages-list .venue-body .map-card',
      m => !!m.querySelector('canvas.maplibregl-canvas') && !m.querySelector('img'));
  })());
  check('the Top pill is the only rank mark on the gallery page',
    (await page.$$eval('#pages-list .venue-body .tier-pill, #pages-list .venue-body .tier-star', els => els.length)) === 1);

  // ---- the deck: a venue's shows collapse into one card the footer swipes through ----
  await page.evaluate(() => { const root = document.getElementById('pages-list');
    while (root.children.length > 1) root.lastElementChild.remove(); });
  const many = await page.evaluate(() => {           // the LA venue running the most shows
    const n = {}; window.DEMO_DATA.shows.filter(s => s.city === 'los-angeles')
      .forEach(s => (n[s.venue.name] = (n[s.venue.name] || 0) + 1));
    const name = Object.keys(n).sort((a, b) => n[b] - n[a])[0];
    return { name, n: n[name] };
  });
  await page.locator('#pages-list .list-results .show-row', { hasText: many.name }).first().locator('.sr-text').click();
  await page.waitForSelector('#pages-list .venue-block');
  await page.click('#pages-list .venue-block');
  await page.waitForSelector('#pages-list .venue-show-stack');
  await page.waitForTimeout(500);            // let the push animation settle before aiming at the footer
  const deck = await page.evaluate(() => {
    const w = document.querySelector('#pages-list .venue-show-stack');
    return { shows: +w.dataset.shows, cards: w.querySelectorAll('.venue-show-card').length,
      sheets: w.querySelectorAll('.stack-sheet').length,
      count: (w.querySelector('.cf-count') || {}).textContent,
      name: w.querySelector('.name').textContent };
  });
  check(`a venue's ${many.n} shows deal into one card`,
    deck.shows === many.n && deck.cards === 1, JSON.stringify(deck));
  check('sheets behind the card suggest the deck', deck.sheets === Math.min(many.n - 1, 2), `${deck.sheets}`);
  check('the footer counts the deck', deck.count === `1 / ${many.n}`, deck.count);

  const swipe = async dir => {
    const b = await page.$eval('#pages-list .venue-show-card .card-footer',
      e => { const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
    await page.mouse.move(b.x, b.y); await page.mouse.down();
    await page.mouse.move(b.x + dir * 60, b.y, { steps: 6 }); await page.mouse.up();
    await page.waitForTimeout(400);
  };
  const face = () => page.evaluate(() => ({ c: document.querySelector('#pages-list .cf-count').textContent,
    n: document.querySelector('#pages-list .venue-show-card .name').textContent }));
  await swipe(-1);
  const next = await face();
  check('swiping the footer left deals the next show',
    next.c === `2 / ${many.n}` && next.n !== deck.name, JSON.stringify(next));
  await swipe(1); await swipe(1);
  check('swiping right wraps to the back of the deck',
    (await face()).c === `${many.n} / ${many.n}`, (await face()).c);
  await swipe(-1);
  const up = await face();
  await page.click('#pages-list .venue-show-card .cf-text');
  await page.waitForSelector('#pages-list .page-push .detail-body');
  const opened = await page.$eval('#pages-list .page-push .detail-body',
    d => (d.querySelector('.detail-artist') || d.querySelector('.detail-title')).textContent);
  check('tapping the card opens the show that is face up', opened === up.n, `${opened} vs ${up.n}`);

  await browser.close();
  server.close();
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})();
