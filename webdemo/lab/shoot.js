/* Screenshots every phone frame in a design lab page.
 *
 * Run:  NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/lab/shoot.js [--page index|lists] [frameId ...]
 * Needs a build first (data.js + images): scraper/.venv/bin/python webdemo/build.py
 * Writes webdemo/lab/shots/<frameId>.jpg for index, shots/lists/<frameId>.jpg for lists (2x, JPEG 88).
 * Live frames register window.__labPrep[id] = async () => {...}; every hook runs
 * before the shots so a live frame is captured in a meaningful state. The lab
 * list store and the app's saved set are seeded so the shots are deterministic. */
const { chromium } = require('playwright');
const http = require('http');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const PORT = 8941;
const args = process.argv.slice(2);
let PAGE = 'index';
const ONLY = [];
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--page') PAGE = args[++i].replace(/\.html$/, '');
  else ONLY.push(args[i]);
}
const OUT = PAGE === 'index' ? path.join(__dirname, 'shots') : path.join(__dirname, 'shots', PAGE);
fs.mkdirSync(OUT, { recursive: true });
const URL = `http://localhost:${PORT}/lab/${PAGE === 'index' ? '' : PAGE + '.html'}?today=2026-09-03`;

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
  const context = await browser.newContext({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 2 });
  // Deterministic state: the lab pages seed their own store when the key is
  // empty (see lists.js LAB_SEED); the app's saved set gets a fixed six.
  await context.addInitScript(() => {
    try {
      localStorage.removeItem('lab.lists.v1');
      localStorage.setItem('savedShowIDs', JSON.stringify([
        'los-angeles/deitch-urs-fischer', 'los-angeles/lee-mullican-silent-shades', 'los-angeles/broad-yoko-ono',
        'los-angeles/art-practice-rahim-fortune', 'los-angeles/the-page-before-me', 'los-angeles/ghebaly-patrick-jackson-all-signs-fail']));
    } catch (e) { /* ignore */ }
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(URL, { waitUntil: 'networkidle' });
  // Scroll through once so every map container has been laid out, then wait for tiles.
  const ids = await page.$$eval('.phone[data-frame]', els => els.map(e => e.dataset.frame));
  for (const id of ids) await page.locator(`#frame-${id}`).scrollIntoViewIfNeeded();
  await page.evaluate(() => Promise.all((window.__labMaps || []).map(m => new Promise(r => {
    const done = () => setTimeout(r, 400);
    if (m.loaded() && !m.isMoving()) { done(); return; }
    m.once('idle', done); setTimeout(r, 20000);
  }))));
  // Put the live frames in their canned state.
  const prepped = await page.evaluate(async () => {
    const hooks = window.__labPrep || {};
    for (const f of Object.values(hooks)) await f();
    return Object.keys(hooks);
  });
  if (prepped.length) console.log('prepped', prepped.join(' '));
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
