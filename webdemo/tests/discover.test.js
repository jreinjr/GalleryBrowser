/* Discover tab checks with a mocked endpoint: the dev server replays SSE
 * fixtures (tests/fixtures/*.sse), so no API key and no spend.
 *   NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/discover.test.js
 */
const path = require('path');
const { spawn } = require('child_process');
const { chromium } = require('playwright');

const PORT = 8945;
const CITY = 'los-angeles';
const results = [];
function check(name, ok, detail) {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}
function startServer(fixture) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path.join(__dirname, '..', 'devserver.mjs')], {
      env: { ...process.env, PORT: String(PORT), DISCOVER_MOCK: '1', DISCOVER_MOCK_FIXTURE: fixture, ANTHROPIC_API_KEY: 'mock' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.on('data', d => { if (/dev server on/.test(String(d))) resolve(child); });
    child.stderr.on('data', d => process.stderr.write(d));
    child.on('exit', code => reject(new Error('dev server exited ' + code)));
    setTimeout(() => reject(new Error('dev server did not start')), 8000);
  });
}

let server = null;
process.on('exit', () => { if (server) try { server.kill(); } catch (e) { /* gone */ } });
(async () => {
  server = await startServer('discover-list');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 393, height: 852 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('PAGEERROR', e.message));
  await page.addInitScript(([city]) => {
    try { if (sessionStorage.getItem('discover-test-inited')) return; sessionStorage.setItem('discover-test-inited', '1'); } catch (e) { return; }
    localStorage.setItem('selectedCityKey', city);
    localStorage.removeItem('filter');
    localStorage.removeItem('lists');
    localStorage.setItem('savedShowIDs', '[]');
  }, [CITY]);
  await page.goto(`http://localhost:${PORT}/`);
  await page.click('.tab-btn[data-tab="discover"]');
  await page.waitForSelector('#pages-discover .prompt-row');

  // 1. idle state
  const city = await page.evaluate(c => window.DEMO_DATA.cities.find(x => x.key === c).displayName, CITY);
  check('header is the city', (await page.$eval('#pages-discover .large-title', e => e.textContent)) === city);
  const prompts = await page.$$eval('#pages-discover .prompt-row .row-label', els => els.map(e => e.textContent));
  check('four Try prompts, no queer-artists prompt, no Recent, no big icon', prompts.length === 4 && !prompts.some(p => /queer/i.test(p)) && !(await page.$('#pages-discover .ai-ico.big')) && !(await page.$('#pages-discover .ask-row')), prompts.join(' | '));
  check('ask bar is present and New is hidden', !!(await page.$('#pages-discover .ask-bar input')) && await page.$eval('#pages-discover [data-new-chat]', e => e.hidden));

  // 2. a question streams into an answer card
  await page.fill('#pages-discover .ask-bar input', 'ceramics in chinatown');
  await page.click('#pages-discover .ask-send');
  await page.waitForSelector('#pages-discover .answer-card');
  await page.waitForSelector('#pages-discover .followups .chip');
  check('user bubble shows the question', (await page.$eval('#pages-discover .msg.user', e => e.textContent)) === 'ceramics in chinatown');
  check('assistant prose streamed', (await page.$eval('#pages-discover .msg.ai p', e => e.textContent)).startsWith('Three shows in Chinatown'));
  check('answer card titled from the list with two entries and why-lines', (await page.$eval('#pages-discover .answer-card .ans-title', e => e.textContent)) === 'Ceramics in Chinatown' && (await page.$$('#pages-discover .answer-card .ans-entry')).length === 2 && (await page.$$('#pages-discover .answer-card .why')).length === 2);
  check('actions are Save as list and Show on Map, no Open list button for short lists beyond Open', (await page.$$eval('#pages-discover .answer-card .ans-act', els => els.map(e => e.textContent.trim()))).join(',') === 'Save as list,Show on Map,Open');
  check('suggestion chips rendered', (await page.$$('#pages-discover .followups .chip')).length === 3);
  check('New button now visible', !(await page.$eval('#pages-discover [data-new-chat]', e => e.hidden)));
  const draft = await page.evaluate(() => ({ drafts: JSON.parse(sessionStorage.getItem('discover.drafts') || '[]'), lists: localStorage.getItem('lists') }));
  check('the answer is a draft list in sessionStorage, not in saved lists', draft.drafts.length === 1 && draft.drafts[0].kind === 'list' && !draft.lists);

  // 3. Open → list page (non-owned: Save, not Edit); Save as list
  await page.click('#pages-discover .answer-card .ans-act:last-child');
  await page.waitForSelector('#pages-discover .list-page .list-row');
  const acts = await page.$$eval('#pages-discover .list-page .lh-actions .act', els => els.map(e => e.dataset.act));
  check('draft list page offers Map and Save', acts.join(',') === 'Map,Save', acts.join(','));
  check('draft list byline shows the note', await page.$eval('#pages-discover .list-page .list-row .lr-byline', e => e.textContent.includes('clay')));
  await page.click('#pages-discover .page:last-child .navrow .circle-btn');
  await page.waitForTimeout(350);
  await page.click('#pages-discover .answer-card .ans-act.primary');
  await page.waitForSelector('#pages-discover .answer-card .ans-act.done');
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('lists')));
  check('Save as list creates a user list of kind answer with the query as source', saved.lists.length === 1 && saved.lists[0].kind === 'answer' && saved.lists[0].source.query === 'ceramics in chinatown' && saved.lists[0].entries.length === 2);
  await page.click('.tab-btn[data-tab="list"]');
  await page.waitForSelector('#pages-list .lib-grid .lib-tile');
  check('the saved answer appears under Your lists', (await page.$$eval('#pages-list .lib-grid .lib-name', els => els.map(e => e.textContent))).includes('Ceramics in Chinatown'));

  // 4. a chip sends; the route fixture draws on the map
  server.kill(); server = await startServer('discover-route');
  await page.click('.tab-btn[data-tab="discover"]');
  await page.click('#pages-discover .followups .chip');
  await page.waitForSelector('#pages-discover .route .route-stop');
  check('chip sent as a question', (await page.$$eval('#pages-discover .msg.user', els => els.map(e => e.textContent))).length === 2);
  check('route card lists numbered stops with times', (await page.$$('#pages-discover .route .route-stop')).length === 5 && (await page.$eval('#pages-discover .route .route-stop .stop-meta', e => e.textContent)).includes('12pm'));
  check('route list entries carry no times', await page.evaluate(() => { const d = JSON.parse(sessionStorage.getItem('discover.drafts')); const r = d.find(x => x.kind === 'route'); return r && r.entries.every(e => !e.note || !/\d(am|pm)/.test(e.note)); }));
  const transcript = await page.evaluate(c => JSON.parse(sessionStorage.getItem('discover.' + c)), CITY);
  check('transcript keeps the presented ids for follow-ups', transcript.turns.filter(t => t.role === 'assistant').every(t => t.presented && t.presented.ids.length > 0));
  await page.click('#pages-discover .msg.ai:last-of-type .answer-card [data-map-draft]');   // the route answer, not the first card
  await page.waitForFunction(() => window.__demoMap && window.__demoMap.getLayer && window.__demoMap.getLayer('ctx-line') && window.__demoMap.getLayoutProperty('ctx-line', 'visibility') === 'visible');
  check('Show route on Map draws the dashed line and numbered stops', await page.evaluate(() => {
    const src = window.__demoMap.getSource('ctx')._data;
    return src.features.some(f => f.geometry.type === 'LineString') && src.features.filter(f => f.properties.on).length >= 4 && window.__demoMap.getLayoutProperty('ctx-num', 'visibility') === 'visible';
  }));
  check('Map pill names the route', (await page.$eval('#map-ctx .ctx-text', e => e.textContent)) === 'Saturday in the Arts District');

  // 5. the error state
  server.kill();
  await page.click('.tab-btn[data-tab="discover"]');
  await page.fill('#pages-discover .ask-bar input', 'anything');
  await page.click('#pages-discover .ask-send');
  await page.waitForSelector('#pages-discover .ai-error');
  check('a failed request shows the error with Retry', (await page.$eval('#pages-discover .ai-error', e => e.textContent)).includes('Retry'));

  await browser.close();
  const failed = results.filter(r => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
