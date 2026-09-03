/* Screenshots every phone frame in the design lab.
 *
 * Run:  NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/lab/shoot.js [frameId ...]
 * Needs a build first (data.js + images): scraper/.venv/bin/python webdemo/build.py
 * Writes webdemo/lab/shots/<frameId>.jpg (2x, JPEG 88). */
const { chromium } = require('playwright');
const http = require('http');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const PORT = 8941;
const OUT = path.join(__dirname, 'shots');
fs.mkdirSync(OUT, { recursive: true });
const ONLY = process.argv.slice(2);

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png',
  '.jpg': 'image/jpeg', '.webp': 'image/webp', '.json': 'application/json' };
function serve() {
  return new Promise(res => {
    const s = http.createServer((req, r) => {
      let p = decodeURIComponent(req.url.split('?')[0]);
      if (p.endsWith('/')) p += 'index.html';
      fs.readFile(path.join(ROOT, p), (err, data) => {
        if (err) { r.writeHead(404); r.end(); return; }
        r.writeHead(200, { 'Content-Type': TYPES[path.extname(p)] || 'application/octet-stream' });
        r.end(data);
      });
    });
    s.listen(PORT, () => res(s));
  });
}

(async () => {
  const server = await serve();
  // Bundled Chromium may be missing; fall back to the installed Google Chrome.
  const browser = await chromium.launch().catch(() => chromium.launch({ channel: 'chrome' }));
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2 });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(`http://localhost:${PORT}/lab/`, { waitUntil: 'networkidle' });
  // Scroll through once so every map container has been laid out, then wait for tiles.
  const ids = await page.$$eval('.phone[data-frame]', els => els.map(e => e.dataset.frame));
  for (const id of ids) await page.locator(`#frame-${id}`).scrollIntoViewIfNeeded();
  await page.evaluate(() => Promise.all((window.__labMaps || []).map(m => new Promise(r => {
    const done = () => setTimeout(r, 400);
    if (m.loaded() && !m.isMoving()) { done(); return; }
    m.once('idle', done); setTimeout(r, 20000);
  }))));
  await page.waitForTimeout(1500);   // image decode
  for (const id of ids) {
    if (ONLY.length && !ONLY.includes(id)) continue;
    const loc = page.locator(`#frame-${id}`);
    await loc.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
    await loc.screenshot({ path: path.join(OUT, `${id}.jpg`), type: 'jpeg', quality: 88 });
    console.log('shot', id);
  }
  if (errors.length) { console.log('\nPage errors:'); errors.forEach(e => console.log(' -', e)); }
  await browser.close();
  server.close();
})();
