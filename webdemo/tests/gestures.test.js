/* Gesture regression harness for the Gallery Browser web demo.
 *
 * Run:  NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/tests/gestures.test.js
 * Needs a fresh build first: scraper/.venv/bin/python webdemo/build.py
 *
 * Every touch gesture here is raw CDP Input.dispatchTouchEvent choreography —
 * never Input.synthesizePinchGesture, which births both fingers at once and
 * hides every lifecycle bug (that is how a previous green run shipped broken
 * gestures). CDP semantics, verified empirically by the calibration step below
 * (they are NOT symmetrical, and getting them wrong once cost a debug cycle):
 *   - touchStart / touchMove: `touchPoints` is the full active set; new ids
 *     synthesize touchstarts, listed ids move, unlisted active ids persist.
 *   - touchEnd: `touchPoints` lists the fingers being RELEASED; an empty list
 *     releases every remaining finger. So lift F2 of {F1,F2} = touchEnd [p2].
 *   - Multi-point changes are split into sequential single-change DOM events,
 *     so iOS's batched multi-changedTouches path is untestable here.
 * The calibration asserts event shapes AND identifiers against live DOM
 * listeners before any test result is trusted. What CDP cannot reproduce —
 * batched touchends, WebKit's native-scroll arbitration, Safari's click
 * synthesis heuristics — stays on the on-device checklist.
 */
const { chromium } = require('playwright');
const http = require('http');
const path = require('path');
const fs = require('fs');

const ROOT = process.env.DIST || path.join(__dirname, '..', 'dist', 'gallery-browser-demo');
const PORT = 8931;
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

// points: full active set, e.g. [{x, y, id}]
const touch = (cdp, type, points) =>
  cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points });

async function drag1(cdp, from, to, steps, delayMs, id = 1) {
  await touch(cdp, 'touchStart', [{ ...from, id }]);
  for (let i = 1; i <= steps; i++) {
    await touch(cdp, 'touchMove', [{
      x: from.x + (to.x - from.x) * i / steps,
      y: from.y + (to.y - from.y) * i / steps, id,
    }]);
    if (delayMs) await sleep(delayMs);
  }
  await touch(cdp, 'touchEnd', []);
}

async function rawTap(cdp, x, y, holdMs = 60) {
  await touch(cdp, 'touchStart', [{ x, y, id: 9 }]);
  await sleep(holdMs);
  await touch(cdp, 'touchEnd', []);
}

// In-page helpers: transform parser, .pinch-img reader, viewer reader, session recorder.
async function installHelpers(page) {
  await page.evaluate(() => {
    window.__parse = str => {
      const m = /translate3d\((-?[\d.eE+]+)px, (-?[\d.eE+]+)px, 0(?:px)?\) scale\((-?[\d.eE+]+)\)/
        .exec(str || '');
      return m ? { tx: +m[1], ty: +m[2], s: +m[3] } : null;
    };
    window.__pinchTfm = () => {
      const n = document.querySelector('.pinch-img');
      return n ? window.__parse(n.style.transform) : null;
    };
    window.__viewerTfm = () => {
      const c = document.querySelector('.viewer-counter');
      if (!c) return null;
      const i = parseInt(c.textContent, 10) - 1;
      const img = document.querySelectorAll('.viewer-slide img')[i];
      if (!img) return null;
      return window.__parse(img.style.transform) || { tx: 0, ty: 0, s: 1 };
    };
    // recorder: per-frame samples of the carousel pinch clone; flags any
    // frame-to-frame scale discontinuity (a re-baseline jump would show here)
    window.__rec = { maxScale: 1, maxJump: 0, last: null, gap: true };
    const poll = () => {
      const r = window.__rec, t = window.__pinchTfm();
      if (!t) { r.gap = true; }
      else {
        if (!r.gap && r.last) r.maxJump = Math.max(r.maxJump, Math.abs(t.s - r.last.s));
        r.gap = false; r.last = t;
        r.maxScale = Math.max(r.maxScale, t.s);
      }
      requestAnimationFrame(poll);
    };
    poll();
  });
}

const pinchState = page => page.evaluate(() => ({
  overlay: document.querySelectorAll('.pinch-img,.pinch-backdrop').length,
  hidden: [...document.querySelectorAll('.carousel-slide img')]
    .filter(i => i.style.visibility === 'hidden').length,
  pages: document.getElementById('pages-featured').children.length,
  tfm: window.__pinchTfm(),
}));

(async () => {
  const server = await serve();
  const browser = await chromium.launch({ channel: 'chrome' });
  const fails = [];
  const check = (name, ok, detail) => {
    console.log((ok ? 'PASS' : 'FAIL') + '  ' + name + (detail !== undefined ? '   [' + detail + ']' : ''));
    if (!ok) fails.push(name);
  };

  // ============================ MOBILE SUITE ============================
  const ctx = await browser.newContext({
    viewport: { width: 393, height: 852 }, hasTouch: true, isMobile: true,
  });
  const page = await ctx.newPage();
  page.on('pageerror', e => { console.log('PAGEERROR', e.message); fails.push('pageerror: ' + e.message); });
  await page.goto(`http://localhost:${PORT}/`);
  await page.waitForSelector('.card .carousel-slide img');
  await sleep(700);
  await installHelpers(page);
  const cdp = await ctx.newCDPSession(page);

  // ---- Calibration: prove the CDP choreography produces the intended DOM events ----
  await page.evaluate(() => {
    window.__cal = [];
    ['touchstart', 'touchmove', 'touchend', 'touchcancel'].forEach(t =>
      document.addEventListener(t, e => window.__cal.push({
        t, n: e.touches.length, c: [...e.changedTouches].map(x => x.identifier).join(','),
      }), true));
  });
  const cardBox = await page.locator('.card .carousel-slide').first().boundingBox();
  const cx = cardBox.x + cardBox.width / 2, cy = cardBox.y + cardBox.height / 2;
  await touch(cdp, 'touchStart', [{ x: cx - 40, y: cy, id: 1 }]);
  await sleep(30);
  await touch(cdp, 'touchStart', [{ x: cx - 40, y: cy, id: 1 }, { x: cx + 40, y: cy, id: 2 }]);
  await sleep(30);
  await touch(cdp, 'touchMove', [{ x: cx - 44, y: cy, id: 1 }, { x: cx + 44, y: cy, id: 2 }]);
  await sleep(30);
  await touch(cdp, 'touchEnd', [{ x: cx + 44, y: cy, id: 2 }]);   // lift ONLY finger 2
  await sleep(30);
  await touch(cdp, 'touchEnd', []);                                // lift finger 1
  await sleep(500);
  const cal = await page.evaluate(() => window.__cal);
  const calShape = cal.map(e => e.t + ':' + e.n + '(' + e.c + ')').join(' ');
  check('CAL: start(1) start(2) move(2) end(1)=id2 end(0)=id1',
    /touchstart:1\(1\) touchstart:2\(2\) touchmove:2\(.*\) touchend:1\(2\) touchend:0\(1\)/.test(calShape),
    calShape);
  await page.evaluate(() => { window.__cal = []; });
  {
    const st = await pinchState(page);
    check('CAL pinch session cleaned up', st.overlay === 0 && st.hidden === 0 && st.pages === 1,
      JSON.stringify({ overlay: st.overlay, hidden: st.hidden, pages: st.pages }));
  }

  // ---- T1: one-finger vertical drag over a photo still scrolls the feed ----
  const scrollTop = () => page.evaluate(() =>
    document.querySelector('#pages-featured .page-scroll').scrollTop);
  await drag1(cdp, { x: cx, y: cy }, { x: cx, y: cy - 220 }, 12, 8);
  await sleep(400);
  const t1 = await scrollTop();
  check('T1 feed vertical scroll intact', t1 > 60, 'scrollTop=' + t1);
  await page.evaluate(() => {
    document.querySelector('#pages-featured .page-scroll').scrollTo(0, 0);
  });
  await sleep(300);

  // ---- T2: one-finger horizontal drag still pages the carousel ----
  // Real scroll physics; fling estimation can jitter, so allow one logged
  // retry — an actual regression (wrong touch-action etc.) fails both.
  let t2 = null;
  for (let attempt = 0; attempt < 2 && !(t2 && t2.ok); attempt++) {
    if (attempt) console.log('  (T2 retry after scroll-physics flake)');
    await page.evaluate(() =>
      document.querySelector('.card .carousel-track').scrollTo({ left: 0, behavior: 'auto' }));
    await sleep(400);
    await drag1(cdp, { x: cx + 130, y: cy }, { x: cx - 130, y: cy }, 16, 12);
    await sleep(900);
    t2 = await page.evaluate(() => {
      const track = document.querySelector('.card .carousel-track');
      const dots = [...document.querySelectorAll('.card .carousel-dots')[0].children];
      const r = { sl: track.scrollLeft, w: track.clientWidth,
                  dot: dots.findIndex(d => d.classList.contains('on')) };
      r.ok = Math.abs(r.sl - r.w) < 3 && r.dot === 1;
      return r;
    });
  }
  check('T2 carousel native paging intact', t2.ok, JSON.stringify(t2));

  // ---- T3: the full pinch lifecycle on a feed card ----
  await page.evaluate(() => { window.__rec.maxScale = 1; window.__rec.maxJump = 0; });
  let F1 = { x: cx - 40, y: cy, id: 1 };
  let F2 = { x: cx + 40, y: cy, id: 2 };
  await touch(cdp, 'touchStart', [F1]);
  await sleep(30);
  await touch(cdp, 'touchStart', [F1, F2]);
  await sleep(50);
  let st = await pinchState(page);
  check('T3a session starts on second finger', st.overlay === 2 && st.hidden === 1,
    JSON.stringify({ overlay: st.overlay, hidden: st.hidden }));

  for (let i = 0; i < 8; i++) {                       // spread: dist 80 -> 176
    F1 = { ...F1, x: F1.x - 6 }; F2 = { ...F2, x: F2.x + 6 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
  }
  await sleep(60);
  st = await pinchState(page);
  const expScale = Math.hypot(F1.x - F2.x, 0) / 80;
  check('T3b scale tracks finger distance', st.tfm && Math.abs(st.tfm.s - expScale) < 0.12,
    `scale=${st.tfm && st.tfm.s.toFixed(2)} expected≈${expScale.toFixed(2)}`);
  await page.screenshot({ path: SHOT + '/t3-pinch.png' });

  const before2f = st.tfm;
  for (let i = 0; i < 8; i++) {                       // two-finger pan, distance constant
    F1 = { x: F1.x + 5, y: F1.y - 4, id: 1 }; F2 = { x: F2.x + 5, y: F2.y - 4, id: 2 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
  }
  await sleep(60);
  st = await pinchState(page);
  check('T3c two-finger pan translates at constant scale',
    st.tfm && Math.abs(st.tfm.tx - before2f.tx - 40) < 4 &&
    Math.abs(st.tfm.ty - before2f.ty + 32) < 4 && Math.abs(st.tfm.s - before2f.s) < 0.03,
    `Δtx=${st.tfm && (st.tfm.tx - before2f.tx).toFixed(1)} Δty=${st.tfm && (st.tfm.ty - before2f.ty).toFixed(1)} Δs=${st.tfm && (st.tfm.s - before2f.s).toFixed(3)}`);

  await touch(cdp, 'touchEnd', [F2]);                 // lift finger 2 — session must survive
  await sleep(80);
  st = await pinchState(page);
  const afterLift = st.tfm;
  check('T3d clone survives lifting one finger',
    st.overlay === 2 && afterLift && Math.abs(afterLift.s - before2f.s) < 0.03,
    st.overlay === 2 ? `scale=${afterLift && afterLift.s.toFixed(2)}` : 'overlay gone');

  for (let i = 0; i < 10; i++) {                      // one-finger pan with the survivor
    F1 = { x: F1.x - 5, y: F1.y + 4, id: 1 };
    await touch(cdp, 'touchMove', [F1]);
    await sleep(16);
  }
  await sleep(60);
  st = await pinchState(page);
  check('T3e one-finger pan tracks 1:1',
    st.tfm && Math.abs(st.tfm.tx - afterLift.tx + 50) < 4 &&
    Math.abs(st.tfm.ty - afterLift.ty - 40) < 4 && Math.abs(st.tfm.s - afterLift.s) < 0.02,
    `Δtx=${st.tfm && (st.tfm.tx - afterLift.tx).toFixed(1)} Δty=${st.tfm && (st.tfm.ty - afterLift.ty).toFixed(1)}`);
  await page.screenshot({ path: SHOT + '/t3-one-finger-pan.png' });

  const beforeReadd = st.tfm;
  let F3 = { x: F1.x + 90, y: F1.y, id: 3 };          // fresh id: devices never reuse them
  await touch(cdp, 'touchStart', [F1, F3]);
  await touch(cdp, 'touchMove', [F1, F3]);            // zero movement: transform must not jump
  await sleep(60);
  st = await pinchState(page);
  check('T3f re-added finger re-baselines with zero jump',
    st.tfm && Math.abs(st.tfm.s - beforeReadd.s) < 0.01 &&
    Math.abs(st.tfm.tx - beforeReadd.tx) < 1 && Math.abs(st.tfm.ty - beforeReadd.ty) < 1,
    `Δs=${st.tfm && (st.tfm.s - beforeReadd.s).toFixed(3)}`);

  for (let i = 0; i < 6; i++) {                       // resume zooming from current scale
    F1 = { ...F1, x: F1.x - 3 }; F3 = { ...F3, x: F3.x + 3 };
    await touch(cdp, 'touchMove', [F1, F3]);
    await sleep(16);
  }
  await sleep(60);
  st = await pinchState(page);
  check('T3g re-pinch scales up from current value', st.tfm && st.tfm.s > beforeReadd.s + 0.2,
    `scale=${st.tfm && st.tfm.s.toFixed(2)}`);

  await touch(cdp, 'touchEnd', [F3]);                 // staggered full release: F3 first
  await sleep(40);
  await touch(cdp, 'touchEnd', []);
  await sleep(450);
  st = await pinchState(page);
  const rec = await page.evaluate(() => window.__rec);
  check('T3h spring-back + cleanup + no navigation',
    st.overlay === 0 && st.hidden === 0 && st.pages === 1,
    JSON.stringify({ overlay: st.overlay, hidden: st.hidden, pages: st.pages }));
  // T3f asserts exact continuity at the re-baseline instant (Δs<0.01); this is
  // the coarse net for catastrophic jumps — a broken re-baseline shows ~1.0+,
  // while legit per-frame deltas from the choreography stay under ~0.3.
  check('T3i no gross scale discontinuity across the whole session',
    rec.maxScale > 1.8 && rec.maxJump < 0.35,
    `maxScale=${rec.maxScale.toFixed(2)} maxJump=${rec.maxJump.toFixed(3)}`);
  const t3scroll = await scrollTop();
  check('T3j pinch leaked no feed scroll', t3scroll === 0, 'scrollTop=' + t3scroll);

  // ---- T4: touchcancel mid-session tears down cleanly ----
  F1 = { x: cx - 40, y: cy, id: 1 }; F2 = { x: cx + 40, y: cy, id: 2 };
  await touch(cdp, 'touchStart', [F1]);
  await touch(cdp, 'touchStart', [F1, F2]);
  await touch(cdp, 'touchMove', [{ x: cx - 60, y: cy, id: 1 }, { x: cx + 60, y: cy, id: 2 }]);
  await sleep(50);
  await touch(cdp, 'touchCancel', []);
  await sleep(450);
  st = await pinchState(page);
  check('T4 touchcancel teardown', st.overlay === 0 && st.hidden === 0 && st.pages === 1,
    JSON.stringify({ overlay: st.overlay, hidden: st.hidden }));

  // ---- T5: a plain tap still navigates to the detail page ----
  await sleep(300);                                   // clear the 400ms click swallow
  await page.tap('.card .carousel-slide');
  await page.waitForSelector('.detail-hero', { timeout: 5000 });
  st = await pinchState(page);
  check('T5 tap opens detail page', st.pages === 2, 'pages=' + st.pages);
  await sleep(600);                                   // let the push animation finish

  // ---- T6: hero tap opens the viewer at the TAPPED slide ----
  const heroBox = await page.locator('.detail-hero .carousel-track').boundingBox();
  const hx = heroBox.x + heroBox.width / 2, hy = heroBox.y + heroBox.height / 2;
  await drag1(cdp, { x: hx + 130, y: hy }, { x: hx - 130, y: hy }, 10, 8);  // hero -> slide 2
  await sleep(800);
  // tap the track at its center (tapping the first .carousel-slide would make
  // Playwright scroll it back into view, silently un-swiping the hero)
  const heroTap = () => page.tap('.detail-hero .carousel-track',
    { position: { x: heroBox.width / 2, y: heroBox.height / 2 } });
  await heroTap();
  await page.waitForSelector('.viewer', { timeout: 5000 });
  await sleep(400);
  let counter = await page.evaluate(() => document.querySelector('.viewer-counter').textContent);
  check('T6 viewer opens at tapped hero slide', counter.startsWith('2'), counter);
  await page.tap('.viewer-close');
  await sleep(300);

  // ---- T7: expand button opens at the current hero slide ----
  await page.tap('.expand-btn');
  await page.waitForSelector('.viewer', { timeout: 5000 });
  await sleep(400);
  counter = await page.evaluate(() => document.querySelector('.viewer-counter').textContent);
  check('T7 expand opens at current hero slide', counter.startsWith('2'), counter);

  // ---- T8: viewer pinch persists after release ----
  const vb = await page.locator('.viewer').boundingBox();
  const vx = vb.x + vb.width / 2, vy = vb.y + vb.height / 2;
  F1 = { x: vx - 60, y: vy, id: 11 }; F2 = { x: vx + 60, y: vy, id: 12 };
  await touch(cdp, 'touchStart', [F1]);
  await sleep(30);
  await touch(cdp, 'touchStart', [F1, F2]);
  for (let i = 0; i < 8; i++) {                       // dist 120 -> 240 => scale 2
    F1 = { ...F1, x: F1.x - 7.5 }; F2 = { ...F2, x: F2.x + 7.5 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
  }
  await touch(cdp, 'touchEnd', [F1]);
  await sleep(40);
  await touch(cdp, 'touchEnd', []);
  await sleep(500);
  let vt = await page.evaluate(() => window.__viewerTfm());
  check('T8 viewer zoom persists after release', vt && vt.s > 1.6, `scale=${vt && vt.s.toFixed(2)}`);
  await page.screenshot({ path: SHOT + '/t8-viewer-zoomed.png' });

  // ---- T9: viewer one-finger pan respects the clamp ----
  await drag1(cdp, { x: vx, y: vy }, { x: vx + 300, y: vy }, 12, 10, 21);
  await sleep(400);
  const t9 = await page.evaluate(() => {
    const t = window.__viewerTfm();
    const c = document.querySelector('.viewer-counter');
    const i = parseInt(c.textContent, 10) - 1;
    const img = document.querySelectorAll('.viewer-slide img')[i];
    return { t, maxX: (t.s - 1) * img.clientWidth / 2 };
  });
  check('T9 viewer pan clamps at the image edge',
    t9.t && Math.abs(t9.t.tx - t9.maxX) < 2, `tx=${t9.t.tx.toFixed(1)} maxX=${t9.maxX.toFixed(1)}`);

  // ---- T10: double-tap toggles zoom both ways ----
  await sleep(400);
  await rawTap(cdp, vx, vy, 40); await sleep(90); await rawTap(cdp, vx, vy, 40);
  await sleep(500);
  vt = await page.evaluate(() => window.__viewerTfm());
  check('T10a double-tap resets zoom', vt && vt.s === 1, `scale=${vt && vt.s}`);
  await sleep(350);
  await rawTap(cdp, vx + 40, vy, 40); await sleep(90); await rawTap(cdp, vx + 40, vy, 40);
  await sleep(500);
  vt = await page.evaluate(() => window.__viewerTfm());
  check('T10b double-tap zooms back in', vt && Math.abs(vt.s - 2.5) < 0.01, `scale=${vt && vt.s}`);

  // ---- T11: a quick tap right after a pinch must NOT un-zoom (phantom tap) ----
  await sleep(400);
  F1 = { x: vx - 50, y: vy, id: 31 }; F2 = { x: vx + 50, y: vy, id: 32 };
  await touch(cdp, 'touchStart', [F1]);
  await touch(cdp, 'touchStart', [F1, F2]);
  for (let i = 0; i < 4; i++) {
    F1 = { ...F1, x: F1.x - 4 }; F2 = { ...F2, x: F2.x + 4 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
  }
  await touch(cdp, 'touchEnd', [F1]);                 // staggered lift
  await sleep(40);
  await touch(cdp, 'touchEnd', []);
  await sleep(120);
  await rawTap(cdp, vx, vy, 40);                      // the quick tap that used to un-zoom
  await sleep(400);
  vt = await page.evaluate(() => window.__viewerTfm());
  check('T11a quick tap after pinch keeps zoom', vt && vt.s > 1.5, `scale=${vt && vt.s.toFixed(2)}`);
  await sleep(450);
  await rawTap(cdp, vx, vy, 40); await sleep(90); await rawTap(cdp, vx, vy, 40);
  await sleep(500);
  vt = await page.evaluate(() => window.__viewerTfm());
  check('T11b deliberate double-tap still resets', vt && vt.s === 1, `scale=${vt && vt.s}`);

  // ---- T12: surviving finger after a pinch is never dead ----
  await sleep(400);
  // (a) zoomed handoff: pinch to ~2x, lift one, pan with the survivor
  F1 = { x: vx - 40, y: vy, id: 41 }; F2 = { x: vx + 40, y: vy, id: 42 };
  await touch(cdp, 'touchStart', [F1]);
  await touch(cdp, 'touchStart', [F1, F2]);
  for (let i = 0; i < 8; i++) {
    F1 = { ...F1, x: F1.x - 5 }; F2 = { ...F2, x: F2.x + 5 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
  }
  await touch(cdp, 'touchEnd', [F2]);                 // lift finger 2; finger 1 survives
  await sleep(60);
  vt = await page.evaluate(() => window.__viewerTfm());
  const beforePan = vt;
  for (let i = 0; i < 6; i++) {
    F1 = { ...F1, x: F1.x + 6 };
    await touch(cdp, 'touchMove', [F1]);
    await sleep(16);
  }
  await sleep(60);
  vt = await page.evaluate(() => window.__viewerTfm());
  check('T12a surviving finger pans while zoomed', vt && vt.tx > beforePan.tx + 20,
    `Δtx=${vt && (vt.tx - beforePan.tx).toFixed(1)}`);
  await touch(cdp, 'touchEnd', []);
  await sleep(300);
  await rawTap(cdp, vx, vy, 40); await sleep(90); await rawTap(cdp, vx, vy, 40);  // reset zoom
  await sleep(500);
  // (b) pinch back to ~1x, keep one finger, flick it -> must promote to swipe
  counter = await page.evaluate(() => document.querySelector('.viewer-counter').textContent);
  const slideBefore = parseInt(counter, 10);
  F1 = { x: vx - 80, y: vy, id: 51 }; F2 = { x: vx + 80, y: vy, id: 52 };
  await touch(cdp, 'touchStart', [F1]);
  await touch(cdp, 'touchStart', [F1, F2]);
  for (let i = 0; i < 6; i++) {                       // dist 160 -> 244 -> back to 150
    F1 = { ...F1, x: F1.x - 7 }; F2 = { ...F2, x: F2.x + 7 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(12);
  }
  for (let i = 0; i < 8; i++) {
    F1 = { ...F1, x: F1.x + 5.9 }; F2 = { ...F2, x: F2.x - 5.9 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(12);
  }
  await touch(cdp, 'touchEnd', [F2]);                 // lift finger 2; finger 1 stays at ~1x
  await sleep(50);
  for (let i = 0; i < 6; i++) {                       // fast leftward flick
    F1 = { ...F1, x: F1.x - 20 };
    await touch(cdp, 'touchMove', [F1]);
    await sleep(10);
  }
  await touch(cdp, 'touchEnd', []);
  await sleep(600);
  counter = await page.evaluate(() => document.querySelector('.viewer-counter').textContent);
  check('T12b surviving finger at 1x promotes to swipe', parseInt(counter, 10) === slideBefore + 1,
    `${slideBefore} -> ${counter}`);

  // ---- T13: pull-down dismisses ----
  await drag1(cdp, { x: vx, y: vy - 80 }, { x: vx, y: vy + 140 }, 14, 10, 61);
  await sleep(500);
  check('T13 pull-down dismisses viewer',
    await page.evaluate(() => !document.querySelector('.viewer')));

  // ---- T14: pinch-to-close ----
  await heroTap();
  await page.waitForSelector('.viewer', { timeout: 5000 });
  await sleep(400);
  F1 = { x: vx - 100, y: vy, id: 71 }; F2 = { x: vx + 100, y: vy, id: 72 };
  await touch(cdp, 'touchStart', [F1]);
  await touch(cdp, 'touchStart', [F1, F2]);
  for (let i = 0; i < 8; i++) {                       // dist 200 -> 96 => scale 0.48
    F1 = { ...F1, x: F1.x + 6.5 }; F2 = { ...F2, x: F2.x - 6.5 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
  }
  await touch(cdp, 'touchEnd', []);
  await sleep(500);
  check('T14 pinch-to-close dismisses viewer',
    await page.evaluate(() => !document.querySelector('.viewer')));

  // ---- T15: pinch starting at the left edge does not drag the page ----
  const pageTfm = () => page.evaluate(() => {
    const root = document.getElementById('pages-featured');
    return root.lastElementChild.style.transform || '';
  });
  F1 = { x: 10, y: hy, id: 81 };                      // ≤28px: arms the edge-swipe recognizer
  F2 = { x: hx, y: hy, id: 82 };
  await touch(cdp, 'touchStart', [F1]);
  await sleep(30);
  await touch(cdp, 'touchStart', [F1, F2]);
  await sleep(30);
  let sawPinch = false;
  for (let i = 0; i < 6; i++) {                       // drag the edge finger rightwards
    F1 = { ...F1, x: F1.x + 10 };
    await touch(cdp, 'touchMove', [F1, F2]);
    await sleep(16);
    if (!sawPinch) sawPinch = await page.evaluate(() => !!document.querySelector('.pinch-img'));
  }
  const t15tfm = await pageTfm();
  await touch(cdp, 'touchEnd', []);
  await sleep(450);
  check('T15 edge-pinch: page stays put, pinch runs', t15tfm === '' && sawPinch,
    `pageTransform='${t15tfm}' pinch=${sawPinch}`);

  // ---- T16: with the viewer open, an edge drag never moves the page beneath ----
  await heroTap();
  await page.waitForSelector('.viewer', { timeout: 5000 });
  await sleep(400);
  await drag1(cdp, { x: 10, y: 400 }, { x: 150, y: 400 }, 10, 12, 91);
  await sleep(300);
  const t16tfm = await pageTfm();
  check('T16 edge drag under viewer ignored', t16tfm === '', `pageTransform='${t16tfm}'`);
  await page.tap('.viewer-close');
  await sleep(300);

  // ---- T17: normal edge-swipe-back still pops the page ----
  await drag1(cdp, { x: 10, y: 500 }, { x: 260, y: 500 }, 10, 14, 95);
  await sleep(600);
  st = await pinchState(page);
  check('T17 edge-swipe-back still works', st.pages === 1, 'pages=' + st.pages);

  await ctx.close();

  // ============================ DESKTOP SUITE ============================
  const dctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const dpage = await dctx.newPage();
  dpage.on('pageerror', e => { console.log('PAGEERROR(desktop)', e.message); fails.push('desktop pageerror: ' + e.message); });
  await dpage.goto(`http://localhost:${PORT}/`);
  await dpage.waitForSelector('.card .carousel-slide img');
  await sleep(700);
  await installHelpers(dpage);
  const dcdp = await dctx.newCDPSession(dpage);
  const wheel = (x, y, dx, dy, ctrl) => dcdp.send('Input.dispatchMouseEvent', {
    type: 'mouseWheel', x, y, deltaX: dx, deltaY: dy, modifiers: ctrl ? 2 : 0,
  });

  await dpage.click('.card .carousel-slide');
  await dpage.waitForSelector('.detail-hero', { timeout: 5000 });
  await sleep(600);
  await dpage.click('.detail-hero .carousel-slide');
  await dpage.waitForSelector('.viewer', { timeout: 5000 });
  await sleep(400);
  const dvb = await dpage.locator('.viewer').boundingBox();
  const dvx = dvb.x + dvb.width / 2, dvy = dvb.y + dvb.height / 2;

  // ---- D1: trackpad pinch (ctrl+wheel) zooms ----
  await wheel(dvx, dvy, 0, -80, true);
  await sleep(300);
  let dt = await dpage.evaluate(() => window.__viewerTfm());
  check('D1 ctrl+wheel zooms', dt && dt.s > 1.8 && dt.s < 2.7, `scale=${dt && dt.s.toFixed(2)}`);

  // ---- D2: plain two-finger scroll pans while zoomed ----
  const beforeWheelPan = dt;
  await wheel(dvx, dvy, 40, 30, false);
  await sleep(300);
  dt = await dpage.evaluate(() => window.__viewerTfm());
  check('D2 wheel pans when zoomed',
    dt && Math.abs(dt.tx - beforeWheelPan.tx + 40) < 2 && Math.abs(dt.ty - beforeWheelPan.ty + 30) < 2,
    `Δtx=${dt && (dt.tx - beforeWheelPan.tx).toFixed(1)} Δty=${dt && (dt.ty - beforeWheelPan.ty).toFixed(1)}`);

  // ---- D3: mouse drag pans while zoomed ----
  const beforeDragPan = dt;
  await dpage.mouse.move(dvx, dvy);
  await dpage.mouse.down();
  await dpage.mouse.move(dvx - 60, dvy - 40, { steps: 8 });
  await dpage.mouse.up();
  await sleep(300);
  dt = await dpage.evaluate(() => window.__viewerTfm());
  check('D3 mouse drag pans when zoomed',
    dt && beforeDragPan.tx - dt.tx > 30 && beforeDragPan.ty - dt.ty > 20,
    `Δtx=${dt && (dt.tx - beforeDragPan.tx).toFixed(1)} Δty=${dt && (dt.ty - beforeDragPan.ty).toFixed(1)}`);
  await dpage.screenshot({ path: SHOT + '/d3-desktop-pan.png' });

  // ---- D4: drag then an immediate double-click must not toggle zoom ----
  await dpage.mouse.move(dvx, dvy);
  await dpage.mouse.down();
  await dpage.mouse.move(dvx + 30, dvy, { steps: 4 });
  await dpage.mouse.up();
  await dpage.mouse.dblclick(dvx + 30, dvy);
  await sleep(400);
  dt = await dpage.evaluate(() => window.__viewerTfm());
  check('D4 drag+immediate dblclick keeps zoom', dt && dt.s > 1.5, `scale=${dt && dt.s.toFixed(2)}`);

  // ---- D5: double-click toggles after the drag window passes ----
  await sleep(500);
  await dpage.mouse.dblclick(dvx, dvy);
  await sleep(500);
  dt = await dpage.evaluate(() => window.__viewerTfm());
  check('D5 dblclick resets zoom', dt && dt.s === 1, `scale=${dt && dt.s}`);

  // ---- D6: wheel at 1x is inert (no pan, no scroll leak) ----
  const underScroll = () => dpage.evaluate(() => {
    const el = document.querySelector('#pages-featured .page-scroll');
    return el ? el.scrollTop : -1;
  });
  const d6s0 = await underScroll();
  await wheel(dvx, dvy, 0, 120, false);
  await sleep(300);
  dt = await dpage.evaluate(() => window.__viewerTfm());
  const d6s1 = await underScroll();
  check('D6 wheel at 1x is inert', dt && dt.s === 1 && dt.tx === 0 && d6s0 === d6s1,
    `scale=${dt && dt.s} tx=${dt && dt.tx} scroll ${d6s0}->${d6s1}`);

  // ---- D7: mouse drag at 1x swipes between slides ----
  const d7before = await dpage.evaluate(() =>
    parseInt(document.querySelector('.viewer-counter').textContent, 10));
  await dpage.mouse.move(dvx + 100, dvy);
  await dpage.mouse.down();
  await dpage.mouse.move(dvx - 120, dvy, { steps: 10 });
  await dpage.mouse.up();
  await sleep(600);
  const d7after = await dpage.evaluate(() =>
    parseInt(document.querySelector('.viewer-counter').textContent, 10));
  check('D7 mouse drag at 1x swipes slides', d7after === d7before + 1, `${d7before} -> ${d7after}`);

  await dctx.close();

  console.log(fails.length ? `\n${fails.length} FAILURE(S)` : '\nALL PASS');
  await browser.close();
  server.close();
  process.exit(fails.length ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); process.exit(2); });
