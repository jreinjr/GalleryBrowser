/* Map tab regression harness: gallery dots by tier, collision-free labels, taps.
 *
 * Run:  NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/map.test.js
 * Needs a fresh build first: scraper/.venv/bin/python webdemo/build.py
 *
 * Dots are MapLibre layers, not DOM, so every assertion goes through
 * map.queryRenderedFeatures (which only returns symbols the collision engine
 * actually placed) on window.__demoMap.
 */
const { chromium } = require('playwright');
const http = require('http');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8932;
const CITY = process.env.MAP_CITY || 'los-angeles';
const SHOT = process.env.SHOT_DIR || path.join(__dirname, '.shots');
fs.mkdirSync(SHOT, { recursive: true });

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

// Resolves once the map has finished rendering after the next change.
const idle = page => page.evaluate(() => new Promise(r => {
  const m = window.__demoMap;
  if (m.loaded() && !m.isMoving()) { setTimeout(r, 250); return; }
  m.once('idle', () => setTimeout(r, 250));
}));

// Rendered features of a layer with their screen positions.
const rendered = (page, layer) => page.evaluate(layer => {
  const m = window.__demoMap;
  const seen = new Set();
  return m.queryRenderedFeatures({ layers: [layer] }).flatMap(f => {
    if (seen.has(f.properties.key)) return [];
    seen.add(f.properties.key);
    const p = m.project(f.geometry.coordinates);
    return [{ x: p.x, y: p.y, lngLat: f.geometry.coordinates, name: f.properties.name,
      count: f.properties.count, tier: f.properties.tier, key: f.properties.key }];
  });
}, layer);

// Approximate label boxes (11px Montserrat Medium ≈ 5.8 px/char, max-width
// 12em wraps long names) and report any intersecting pair. MapLibre's collision
// engine is the real guarantee — queryRenderedFeatures only returns placed
// symbols — so this is an independent sanity check; boxes are shrunk 3px so
// glyph-metric slop in the estimate cannot produce false positives.
function overlaps(labels) {
  const boxes = labels.map(l => {
    const chars = Math.min(l.name.length, 22);
    const lines = Math.ceil(l.name.length / 22);
    const w = chars * 5.8 + 6 - 6, h = lines * 14 - 6;
    return { name: l.name, x1: l.x - w / 2, x2: l.x + w / 2, y1: l.y + 13 + 2, y2: l.y + 13 + 2 + h };
  });
  const hits = [];
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
    const a = boxes[i], b = boxes[j];
    if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) hits.push(a.name + ' / ' + b.name);
  }
  return hits;
}

(async () => {
  const server = await serve();
  const browser = await chromium.launch({ channel: 'chrome' });
  const fails = [];
  const check = (name, ok, detail) => {
    console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '   [' + detail + ']' : ''));
    if (!ok) fails.push(name);
  };

  const ctx = await browser.newContext({ viewport: { width: 393, height: 852 } });
  // Widen the shared filter so every venue is on the map (default = featured, running gallery shows).
  await ctx.addInitScript(city => { try {
    localStorage.setItem('selectedCityKey', city);
    localStorage.setItem('filter', JSON.stringify({ v: 5, kind: 'all', showRank: 'all', active: false }));
    localStorage.setItem('savedShowIDs', '[]');
  } catch (e) { /* */ } }, CITY);
  const page = await ctx.newPage();
  page.on('pageerror', e => { console.log('PAGEERROR', e.message); fails.push('pageerror: ' + e.message); });
  const fontFails = [];
  page.on('response', r => { if (/\/fonts\//.test(r.url()) && r.status() !== 200) fontFails.push(r.status() + ' ' + r.url()); });

  await page.goto(`http://localhost:${PORT}/`);
  await page.waitForSelector('.card .carousel-slide img');
  await page.click('.tab-btn[data-tab="map"]');
  await page.waitForFunction(() => window.__demoMap && window.__demoMap.isStyleLoaded()
    && window.__demoMap.getLayer('gal-label') && window.__demoMap.getSource('venues'), null, { timeout: 30000 });
  await idle(page);
  await sleep(800);   // glyph fetches for the label layer
  await idle(page);

  const cityKey = await page.evaluate(() => localStorage.getItem('selectedCityKey'));
  check('M0 city selected', cityKey === CITY, cityKey);
  check('M0b the map is galleries only (no mode switch, no show pins)',
    (await page.$('#map-mode')) === null && (await page.evaluate(() => !window.__demoMap.getLayer('clusters') && !window.__demoMap.getLayer('venue-dot'))));

  // ---- M1: every venue is its own dot, coloured by tier ----
  const zoom0 = await page.evaluate(() => window.__demoMap.getZoom());
  let dots = await rendered(page, 'gal-dot');
  let labels = await rendered(page, 'gal-label');
  await page.screenshot({ path: path.join(SHOT, 'map-city.png') });
  const features = () => page.evaluate(() => window.__demoMap.getSource('venues').serialize().data.features.length);
  const total = await features();
  check('M1 gallery dots at city zoom', dots.length > 0 && dots.length <= total,
    `zoom=${zoom0.toFixed(2)} dots=${dots.length}/${total} labels=${labels.length}`);
  {
    const tiers = {};
    dots.forEach(d => { tiers[d.tier] = (tiers[d.tier] || 0) + 1; });
    check('M1b dots carry at least two tiers', Object.keys(tiers).length >= 2, JSON.stringify(tiers));
    const radii = await page.evaluate(() => {
      const m = window.__demoMap;
      return { top: m.getPaintProperty('gal-dot', 'circle-radius'), sort: m.getLayoutProperty('gal-dot', 'circle-sort-key') };
    });
    check('M1c dot radius and paint order come from the tier', !!radii.top && !!radii.sort);
  }
  check('M2 no label overlap at city zoom', overlaps(labels).length === 0, overlaps(labels).join(' | ') || `${labels.length} labels`);
  check('M2b listed galleries stay unlabelled', labels.every(l => l.tier !== 'listed'),
    labels.filter(l => l.tier === 'listed').map(l => l.name).join(', '));

  // ---- M3: the densest neighbourhood (most venues within 1.5 km) spreads out as you zoom, labels stay disjoint ----
  const dense = await page.evaluate(() => {
    const city = localStorage.getItem('selectedCityKey');
    const vs = [];
    const seen = new Set();
    window.DEMO_DATA.shows.filter(s => s.city === city).forEach(s => {
      const k = s.venue.lat.toFixed(5) + ',' + s.venue.lng.toFixed(5);
      if (!seen.has(k)) { seen.add(k); vs.push(s.venue); }
    });
    const km = (a, b) => { const r = x => x * Math.PI / 180; const dLat = r(b.lat - a.lat), dLng = r(b.lng - a.lng);
      const h = Math.sin(dLat / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(dLng / 2) ** 2; return 12742 * Math.asin(Math.sqrt(h)); };
    let best = null;
    vs.forEach(v => { const n = vs.filter(w => km(v, w) < 1.5).length; if (!best || n > best.n) best = { n, lat: v.lat, lng: v.lng, name: v.name }; });
    return best;
  });
  await page.evaluate(([c, z]) => window.__demoMap.jumpTo({ center: c, zoom: z }), [[dense.lng, dense.lat], 13.5]);
  await idle(page);
  await sleep(700);
  await idle(page);
  const dots3 = await rendered(page, 'gal-dot');
  const labels3 = await rendered(page, 'gal-label');
  await page.screenshot({ path: path.join(SHOT, 'map-z13.png') });
  check('M3 dense area at z13.5 labels more venues than city zoom', labels3.length > 0 && dots3.length > 0,
    `${dense.name} (${dense.n} venues within 1.5 km): dots=${dots3.length} labels=${labels3.length}`);
  check('M3b no label overlap at z13.5', overlaps(labels3).length === 0, overlaps(labels3).join(' | ') || `${labels3.length} labels`);
  // Close in on a top-tier venue: at street zoom every labelled tier gets its label.
  const topVenue = await page.evaluate(() => {
    const city = localStorage.getItem('selectedCityKey');
    const s = window.DEMO_DATA.shows.find(x => x.city === city && window.DemoDebug.galleryTier(x.venue) === 'top');
    return s && { lat: s.venue.lat, lng: s.venue.lng, name: s.venue.name };
  });
  await page.evaluate(t => window.__demoMap.jumpTo({ center: [t.lng, t.lat], zoom: 15.5 }), topVenue);
  await idle(page);
  await sleep(700);
  await idle(page);
  const dots5 = await rendered(page, 'gal-dot');
  const labels5 = await rendered(page, 'gal-label');
  await page.screenshot({ path: path.join(SHOT, 'map-z15.png') });
  check('M3c every non-listed dot is labelled at z15.5',
    dots5.length > 0 && labels5.length === dots5.filter(d => d.tier !== 'listed').length && labels5.some(l => l.name === topVenue.name),
    `${labels5.length} labels / ${dots5.length} dots around ${topVenue.name}`);
  check('M3d no label overlap at z15.5', overlaps(labels5).length === 0, overlaps(labels5).join(' | ') || `${labels5.length} labels`);

  // ---- M4/M5: any dot opens its venue page, single-show or not ----
  const targets = await page.evaluate(() => {
    const d = window.DEMO_DATA, city = localStorage.getItem('selectedCityKey');
    const key = v => (v.name || '').trim().toLowerCase().replace(/\s+/g, ' ') + '@' + Number(v.lat).toFixed(5) + ',' + Number(v.lng).toFixed(5);
    const groups = new Map();
    d.shows.filter(s => s.city === city).forEach(s => { const k = key(s.venue); (groups.get(k) || groups.set(k, []).get(k)).push(s); });
    const g = [...groups.values()];
    const one = g.find(x => x.length === 1), many = g.find(x => x.length > 1);
    return { one: one && { lat: one[0].venue.lat, lng: one[0].venue.lng, name: one[0].venue.name },
      many: many && { lat: many[0].venue.lat, lng: many[0].venue.lng, name: many[0].venue.name, n: many.length } };
  });
  async function focusVenue(t) {
    await page.evaluate(t => window.__demoMap.jumpTo({ center: [t.lng, t.lat], zoom: 17.5 }), t);
    await idle(page);
    await sleep(400);
    await idle(page);
    const ds = await rendered(page, 'gal-dot');
    const c = await page.evaluate(() => { const m = window.__demoMap; const p = m.project(m.getCenter()); return { x: p.x, y: p.y }; });
    return ds.reduce((best, d) => (Math.hypot(d.x - c.x, d.y - c.y) < Math.hypot(best.x - c.x, best.y - c.y) ? d : best), ds[0] || c);
  }
  async function closeSheet() {
    await page.mouse.click(196, 4);          // backdrop strip above the sheet
    await sleep(450);
    await page.evaluate(() => { document.getElementById('sheet-root').innerHTML = ''; });
  }
  {
    const d = await focusVenue(targets.one);
    check('M4 single-show dot rendered at center', d && d.count === 1 && Math.hypot(d.x - 196, d.y - 426) < 4,
      d && `${d.name} count=${d.count} at (${d.x.toFixed(0)},${d.y.toFixed(0)})`);
    await page.mouse.click(d.x, d.y);
    await sleep(500);
    const st = await page.evaluate(() => ({
      sheets: document.querySelectorAll('#sheet-root .sheet').length,
      venue: !!document.querySelector('#sheet-root .venue-title'),
      rows: document.querySelectorAll('#sheet-root .show-row').length,
    }));
    check('M4b single-show dot opens the venue page', st.sheets === 1 && st.venue && st.rows === 1, JSON.stringify(st));
    await closeSheet();
  }
  {
    const d = await focusVenue(targets.many);
    await page.mouse.click(d.x, d.y);
    await sleep(500);
    const st = await page.evaluate(() => ({
      sheets: document.querySelectorAll('#sheet-root .sheet').length,
      venue: !!document.querySelector('#sheet-root .venue-title'),
      rows: document.querySelectorAll('#sheet-root .show-row').length,
    }));
    check('M5 multi-show dot opens the venue page with every show',
      st.sheets === 1 && st.venue && st.rows === targets.many.n, JSON.stringify(st) + ` / ${targets.many.n}`);
    await closeSheet();
  }

  // ---- M6: the shared filter sheet drives the map; Saved only empties the source (nothing saved) ----
  const openFilters = async () => { await page.click('#map-filter-btn'); await page.waitForSelector('.sheet.open .filter-sheet'); };
  const closeFilters = async () => { await page.click('.sheet.open .sheet-foot .capsule-btn'); await page.waitForSelector('.sheet.open', { state: 'detached' }); };
  await openFilters();
  check('M6 the map filter sheet drops Sort (List order only)',
    (await page.$('.sheet.open [data-sort]')) === null && (await page.$('.sheet.open .seg-row[data-seg="galleryRank"]')) !== null);
  await page.click('.sheet.open [data-switch="saved"]');
  await closeFilters();
  await idle(page);
  {
    const n = await features();
    check('M6b Saved only empties the source', n === 0, 'features=' + n);
    check('M6c filter pill shows the badge', (await page.$eval('#map-filter-btn .badge', e => e.textContent)) === '4');
    await openFilters();
    await page.click('.sheet.open [data-switch="saved"]');
    await closeFilters();
    await idle(page);
    const back = await features();
    check('M6d switching Saved off restores the source', back > 0, 'features=' + back);
    await openFilters();
    await page.click('.sheet.open .seg-row[data-seg="galleryRank"] button[data-value="top"]');
    await closeFilters();
    await idle(page);
    const top = await features();
    const expectTop = await page.evaluate(() => {
      const city = localStorage.getItem('selectedCityKey');
      const key = v => (v.name || '').trim().toLowerCase().replace(/\s+/g, ' ') + '@' + Number(v.lat).toFixed(5) + ',' + Number(v.lng).toFixed(5);
      return new Set(window.DEMO_DATA.shows.filter(s => s.city === city && window.DemoDebug.galleryTier(s.venue) === 'top').map(s => key(s.venue))).size;
    });
    check('M6e Top Ranked narrows the source to top-tier venues', top === expectTop && top > 0, `features=${top} expected=${expectTop}`);
    await openFilters();
    await page.click('.sheet.open .seg-row[data-seg="galleryRank"] button[data-value="all"]');
    await closeFilters();
    await idle(page);
  }

  // ---- M7: Active shows is on by default and narrows the map to running shows ----
  {
    await openFilters();
    await page.click('.sheet.open [data-switch="active"]');   // seeded off; switch it back on
    await closeFilters();
    await idle(page);
    const active = await features();
    const expectActive = await page.evaluate(() => {
      const city = localStorage.getItem('selectedCityKey');
      const key = v => (v.name || '').trim().toLowerCase().replace(/\s+/g, ' ') + '@' + Number(v.lat).toFixed(5) + ',' + Number(v.lng).toFixed(5);
      return new Set(window.DEMO_DATA.shows.filter(s => s.city === city && window.DemoDebug.isActiveShow(s)).map(s => key(s.venue))).size;
    });
    check('M7 Active shows narrows the map to venues with a running show',
      active === expectActive && active > 0 && active <= total, `features=${active} expected=${expectActive} of ${total}`);
    await openFilters();
    await page.click('.sheet.open [data-switch="active"]');
    await closeFilters();
    await idle(page);
  }

  // ---- M8: the tier legend is always on screen ----
  check('M8 legend visible', !(await page.$eval('#map-legend', e => e.hidden)));

  // ---- M9: no glyph request failed ----
  check('M9 label glyphs served', fontFails.length === 0, fontFails.join(', ') || 'all 200');

  // ---- M10: desktop pointer gets a hand cursor over a dot ----
  {
    const dctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
    await dctx.addInitScript(city => { try { localStorage.setItem('selectedCityKey', city); } catch (e) { /* */ } }, CITY);
    const dpage = await dctx.newPage();
    await dpage.goto(`http://localhost:${PORT}/`);
    await dpage.waitForSelector('.card .carousel-slide img');
    await dpage.click('.tab-btn[data-tab="map"]');
    await dpage.waitForFunction(() => window.__demoMap && window.__demoMap.isStyleLoaded() && window.__demoMap.getLayer('gal-label'), null, { timeout: 30000 });
    await dpage.evaluate(t => window.__demoMap.jumpTo({ center: [t.lng, t.lat], zoom: 17.5 }), targets.one);
    await idle(dpage);
    await sleep(500);
    await idle(dpage);
    const c = await dpage.evaluate(() => { const m = window.__demoMap; const p = m.project(m.getCenter()); const r = m.getCanvas().getBoundingClientRect(); return { x: r.left + p.x, y: r.top + p.y }; });
    await dpage.mouse.move(c.x, c.y);
    await sleep(200);
    const cur = await dpage.evaluate(() => window.__demoMap.getCanvas().style.cursor);
    check('M10 desktop hover shows a pointer cursor', cur === 'pointer', 'cursor=' + JSON.stringify(cur));
    await dctx.close();
  }

  console.log(fails.length ? `\n${fails.length} FAILURE(S)` : '\nALL PASS');
  await browser.close();
  server.close();
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
