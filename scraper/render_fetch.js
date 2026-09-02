// Headless render of one page for the scraper's render_fetch tool.
//
//   NODE_PATH=/opt/homebrew/lib/node_modules node render_fetch.js <url> [maxChars]
//
// Prints ONE JSON line: {final_url, renderer, title, text, times, html} or
// {error}. Chrome via Playwright's channel:'chrome' (no bundled Chromium on
// this machine), WebKit as the fallback. Images/media/fonts are blocked to
// keep it fast; exit 2 on failure so the Python side can turn it into an
// actionable tool error.
const { chromium, webkit } = require('playwright');

const url = process.argv[2];
const maxChars = parseInt(process.argv[3] || '12000', 10);
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 ' +
           '(KHTML, like Gecko) Version/17.4 Safari/605.1.15';

if (!url || !/^https?:\/\//i.test(url)) {
  process.stdout.write(JSON.stringify({ error: 'render_fetch needs an http(s) URL' }) + '\n');
  process.exit(2);
}

async function launch() {
  try {
    return { browser: await chromium.launch({ channel: 'chrome', headless: true }), renderer: 'chrome' };
  } catch (e) {
    try {
      return { browser: await webkit.launch({ headless: true }), renderer: 'webkit' };
    } catch (e2) {
      throw new Error(`no browser available (chrome: ${e.message.split('\n')[0]}; webkit: ${e2.message.split('\n')[0]})`);
    }
  }
}

(async () => {
  let browser;
  try {
    const l = await launch();
    browser = l.browser;
    const ctx = await browser.newContext({ userAgent: UA, viewport: { width: 1280, height: 900 } });
    await ctx.route('**/*', (route) => {
      const t = route.request().resourceType();
      if (t === 'image' || t === 'media' || t === 'font') return route.abort();
      return route.continue();
    });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await page.waitForLoadState('networkidle', { timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(500);
    const data = await page.evaluate(() => ({
      title: document.title || '',
      text: (document.body && document.body.innerText) || '',
      times: Array.from(document.querySelectorAll('time')).slice(0, 40).map((t) => ({
        datetime: t.getAttribute('datetime'), text: (t.textContent || '').trim().slice(0, 80),
      })),
      html: document.documentElement ? document.documentElement.outerHTML : '',
    }));
    const out = {
      final_url: page.url(), renderer: l.renderer, title: data.title,
      text: data.text.slice(0, maxChars), times: data.times,
      html: data.html.slice(0, 1500000),
    };
    process.stdout.write(JSON.stringify(out) + '\n');
    await browser.close();
    process.exit(0);
  } catch (e) {
    process.stdout.write(JSON.stringify({ error: (e && e.message ? e.message : String(e)).split('\n')[0] }) + '\n');
    if (browser) { try { await browser.close(); } catch (_) {} }
    process.exit(2);
  }
})();
