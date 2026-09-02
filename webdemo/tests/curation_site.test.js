/* Client curation site checks (plan Part C). Run after
 *   scraper/.venv/bin/python scraper/curation_site.py build --city los-angeles
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/curation_site.test.js
 * Headless Chrome via the globally installed Playwright (channel 'chrome').
 *   1. no page errors; with the default (live) params the JS featured set equals
 *      content/curation/<city>/curated.json `featured` (JS/Python parity for what shipped)
 *   2. lowering / raising the cutoff changes the feed length live
 *   3. Copy-link URL restores the same params on a fresh load and shows the Shared badge
 *   4. Reset returns to live; weight tooltips exist for every weight slider
 */
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..', '..');
const DIST = path.join(ROOT, 'webdemo', 'dist', 'gallery-browser-curation');
const CITY = process.env.CITY || 'los-angeles';
const SHOT = path.join(ROOT, 'webdemo', 'tests', '.shots');

function serve() {
  const types = { '.html': 'text/html', '.webp': 'image/webp', '.json': 'application/json' };
  return new Promise(res => {
    const s = http.createServer((req, r) => {
      let p = decodeURIComponent(req.url.split('?')[0].split('#')[0]);
      if (p === '/') p = '/index.html';
      const f = path.join(DIST, p);
      if (!f.startsWith(DIST) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) { r.writeHead(404); r.end(); return; }
      r.writeHead(200, { 'content-type': types[path.extname(f)] || 'application/octet-stream' });
      fs.createReadStream(f).pipe(r);
    });
    s.listen(0, '127.0.0.1', () => res(s));   // ephemeral port: other suites may hold theirs
  });
}

let fails = 0;
function check(name, ok, detail) { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); if (!ok) fails++; }

(async () => {
  if (!fs.existsSync(path.join(DIST, 'index.html'))) { console.error('no build in ' + DIST); process.exit(2); }
  fs.mkdirSync(SHOT, { recursive: true });
  const curated = JSON.parse(fs.readFileSync(path.join(ROOT, 'content', 'curation', CITY, 'curated.json'), 'utf8'));
  const liveFeed = curated.featured.sort((a, b) => a.featured_rank - b.featured_rank).map(f => f.slug);
  const server = await serve();
  const browser = await chromium.launch({ channel: 'chrome' });
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const base = `http://127.0.0.1:${server.address().port}/`;
  await page.goto(base);
  await page.waitForFunction(() => window.site && window.site.rows().length > 0);
  check('no page errors on load', errors.length === 0, errors.join(' | '));

  // 1. parity with what shipped
  const feed = await page.evaluate(() => window.site.feed());
  check('JS feed length == curated.json featured', feed.length === liveFeed.length, `${feed.length} vs ${liveFeed.length}`);
  check('JS feed order == curated.json featured order', JSON.stringify(feed) === JSON.stringify(liveFeed),
    feed.filter(s => !liveFeed.includes(s)).slice(0, 3).join(',') + ' / ' + liveFeed.filter(s => !feed.includes(s)).slice(0, 3).join(','));
  const live = await page.evaluate(() => ({ isLive: window.site.isLive(), badge: document.querySelector('#mode-badge').textContent.trim(), link: window.site.shareLink() }));
  check('default state is live', live.isLive && /Live settings/.test(live.badge) && !/#p=/.test(live.link), JSON.stringify(live));
  const nAll = await page.evaluate(() => window.site.rows().length);
  const tabs = await page.evaluate(() => [...document.querySelectorAll('.tabs button')].map(b => b.textContent));
  check('tabs carry counts', tabs[0] === `Featured feed (${feed.length})` && tabs[1] === `All discovered shows (${nAll})`, tabs.join(' | '));
  const cards = await page.evaluate(() => document.querySelectorAll('#list .card').length);
  check('feed view renders one card per featured show', cards === feed.length, `${cards}`);
  await page.screenshot({ path: path.join(SHOT, 'curation-desktop.png'), fullPage: false });

  // 2. cutoff slider
  const thr = await page.evaluate(() => window.site.params.threshold);
  await page.fill('input[type=number][data-path="threshold"]', String(Math.min(1, thr + 0.15)));
  await page.dispatchEvent('input[type=number][data-path="threshold"]', 'input');
  const fewer = await page.evaluate(() => window.site.feed().length);
  check('raising the cutoff shortens the feed', fewer < feed.length, `${feed.length} -> ${fewer}`);
  const shared = await page.evaluate(() => ({ isLive: window.site.isLive(), badge: document.querySelector('#mode-badge').textContent.trim(), summary: document.querySelector('#summary').textContent }));
  check('badge flips to Shared and delta shows', !shared.isLive && /differs from live/.test(shared.badge) && /out/.test(shared.summary), JSON.stringify(shared));

  // 3. share link round trip (also bump a weight so the diff has two levels)
  await page.evaluate(() => window.site.set('weights.museum', -0.35));
  const link = await page.evaluate(() => window.site.shareLink());
  check('share link carries #p=', /#p=[A-Za-z0-9_-]+$/.test(link), link);
  const expectFeed = await page.evaluate(() => window.site.feed());
  const expectDiff = await page.evaluate(() => window.site.diff());
  const page2 = await ctx.newPage();
  const errors2 = []; page2.on('pageerror', e => errors2.push(String(e)));
  await page2.goto(base + link.slice(link.indexOf('#')));
  await page2.waitForFunction(() => window.site && window.site.rows().length > 0);
  const got = await page2.evaluate(() => ({ diff: window.site.diff(), feed: window.site.feed(), badge: document.querySelector('#mode-badge').textContent.trim(), thr: document.querySelector('input[type=range][data-path="threshold"]').value }));
  check('shared link restores the params', JSON.stringify(got.diff) === JSON.stringify(expectDiff), JSON.stringify(got.diff) + ' vs ' + JSON.stringify(expectDiff));
  check('shared link reproduces the feed', JSON.stringify(got.feed) === JSON.stringify(expectFeed), `${got.feed.length} vs ${expectFeed.length}`);
  check('shared link shows the Shared badge + slider position', /differs from live/.test(got.badge) && Math.abs(+got.thr - (thr + 0.15)) < 1e-6, got.badge + ' thr=' + got.thr);
  check('no page errors on shared load', errors2.length === 0, errors2.join(' | '));

  // 4. reset + tooltips + evidence drawer + all view
  await page2.click('[data-act="reset"]');
  const afterReset = await page2.evaluate(() => ({ isLive: window.site.isLive(), n: window.site.feed().length, hash: location.hash }));
  check('reset returns to live', afterReset.isLive && afterReset.n === feed.length && afterReset.hash === '', JSON.stringify(afterReset));
  const tips = await page2.evaluate(() => [...document.querySelectorAll('#weights .ctl')].map(c => ({ label: c.querySelector('b').textContent, tip: (c.querySelector('.info .tip') || {}).textContent || '' })));
  check('every weight slider has a tooltip', tips.length === 6 && tips.every(t => t.tip.length > 30), JSON.stringify(tips.map(t => t.label)));
  await page2.click('#list .card:first-child [data-act="toggle"]');
  const drawer = await page2.evaluate(() => { const d = document.querySelector('#list .card:first-child .drawer'); return d ? d.textContent : ''; });
  check('evidence drawer opens with breakdown + critic verdict', /Score breakdown/.test(drawer) && /Critic verdict/.test(drawer) && /Press/.test(drawer), drawer.slice(0, 80));
  await page2.click('.tabs button[data-view="all"]');
  const allCards = await page2.evaluate(() => ({ n: document.querySelectorAll('#list .card').length, cutoff: !!document.querySelector('#list .cutoff'), gated: document.querySelectorAll('#list .card.gated').length }));
  check('all view lists every discovered show with a cutoff divider', allCards.n === nAll && allCards.cutoff && allCards.gated === nAll - feed.length, JSON.stringify(allCards));
  await page2.fill('#q', 'yoko');
  const hits = await page2.evaluate(() => document.querySelectorAll('#list .card').length);
  check('search narrows the list', hits > 0 && hits < nAll, `${hits}`);

  // phone layout
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const mp = await mctx.newPage();
  await mp.goto(base);
  await mp.waitForFunction(() => window.site && window.site.rows().length > 0);
  const m = await mp.evaluate(() => ({ collapsed: document.querySelector('#panel').classList.contains('collapsed'), bodyHidden: getComputedStyle(document.querySelector('.panel-body')).display === 'none', overflow: document.documentElement.scrollWidth <= window.innerWidth + 1 }));
  check('phone: panel starts collapsed and no horizontal overflow', m.collapsed && m.bodyHidden && m.overflow, JSON.stringify(m));
  await mp.click('#panel-toggle');
  const opened = await mp.evaluate(() => getComputedStyle(document.querySelector('.panel-body')).display !== 'none');
  check('phone: toggle opens the panel', opened);
  await mp.screenshot({ path: path.join(SHOT, 'curation-phone.png') });

  await browser.close();
  server.close();
  console.log(fails ? `\n${fails} check(s) failed` : '\nall checks passed');
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
