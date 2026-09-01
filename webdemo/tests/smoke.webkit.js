/* WebKit smoke test — engine-parity sanity only.
 *
 * Run:  NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/smoke.webkit.js
 * Needs: node /opt/homebrew/lib/node_modules/playwright/cli.js install webkit
 *
 * Playwright WebKit has no CDP, so raw multitouch cannot be dispatched here —
 * this proves the bundle parses and basic flows work in the WebKit engine, and
 * nothing more. Gesture behavior on iOS is proved on a real device.
 */
const { webkit } = require('playwright');
const http = require('http');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8932;
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
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const server = await serve();
  const browser = await webkit.launch();
  const ctx = await browser.newContext({
    viewport: { width: 393, height: 852 }, hasTouch: true, isMobile: true,
  });
  const page = await ctx.newPage();
  const fails = [];
  const check = (name, ok, detail) => {
    console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '   [' + detail + ']' : ''));
    if (!ok) fails.push(name);
  };
  page.on('pageerror', e => { console.log('PAGEERROR', e.message); fails.push('pageerror: ' + e.message); });

  await page.goto(`http://localhost:${PORT}/`);
  await page.waitForSelector('.card .carousel-slide img', { timeout: 10000 });
  await sleep(700);
  check('WK loads with cards rendered', true);

  await page.tap('.card .carousel-slide');
  await page.waitForSelector('.detail-hero', { timeout: 5000 });
  check('WK tap opens detail page', true);
  await sleep(600);

  await page.tap('.detail-hero .carousel-slide');
  await page.waitForSelector('.viewer', { timeout: 5000 });
  check('WK hero tap opens viewer', true);
  await sleep(300);

  await page.tap('.viewer-close');
  await sleep(400);
  check('WK viewer close works', await page.evaluate(() => !document.querySelector('.viewer')));

  const backOk = await page.evaluate(() => !!document.querySelector('.detail-hero'));
  check('WK back on detail page after close', backOk);

  console.log(fails.length ? `\n${fails.length} FAILURE(S)` : '\nALL PASS');
  await browser.close();
  server.close();
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
