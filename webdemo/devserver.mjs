/* Local server for the web demo with the Discover function mounted:
 *   node webdemo/devserver.mjs            # http://localhost:8000
 * Serves webdemo/dist/gallery-browser-demo (build first) and routes
 * /api/discover to webdemo/api/discover.js, the same code Vercel runs.
 * ANTHROPIC_API_KEY comes from the environment or from .env at the repo root
 * (or, in a worktree, the main checkout's .env). DIST=<dir> and PORT=<n>
 * override the defaults; DISCOVER_MOCK=1 replays tests/fixtures/discover-*.sse
 * instead of calling the API (used by tests/discover.test.js).
 *
 * Local only (Vercel never runs this file): /api/local-rank lets the map
 * card's Change rank write straight into the repo. GET says which cities can be
 * moved; POST {city, id, to} runs scraper/move_venue_rank.py (venue_order.json +
 * rank_venues.py apply), rebuilds the dist so a reload keeps the change, and
 * answers with the city's new ranks.
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { execSync, execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
const DIST = process.env.DIST || path.join(HERE, 'dist', 'gallery-browser-demo');
const PORT = Number(process.env.PORT || 8000);
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };

function loadEnv() {
  const candidates = [path.join(ROOT, '.env')];
  try {
    const common = execSync('git rev-parse --git-common-dir', { cwd: ROOT, encoding: 'utf8' }).trim();
    candidates.push(path.join(path.resolve(ROOT, common), '..', '.env'));
  } catch (e) { /* not a git checkout */ }
  for (const f of candidates) {
    if (!fs.existsSync(f)) continue;
    for (const line of fs.readFileSync(f, 'utf8').split('\n')) {
      const m = /^([A-Z_][A-Z0-9_]*)=(.*)$/.exec(line.trim());
      if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
    return f;
  }
  return null;
}
const envFile = loadEnv();
if (!process.env.DISCOVER_ALLOW_NO_ORIGIN) process.env.DISCOVER_ALLOW_NO_ORIGIN = '1';   // curl and the eval send no Origin

// Discover needs the Anthropic SDK (npm i in webdemo/); without it the rest of
// the preview still serves and /api/discover answers 503.
let POST = null, GET = null;
try { ({ POST, GET } = await import('./api/discover.js')); } catch (e) { console.warn('Discover off:', e.message.split('\n')[0]); }

function toRequest(req, body) {
  const url = `http://${req.headers.host || 'localhost'}${req.url}`;
  const headers = new Headers();
  for (const [k, v] of Object.entries(req.headers)) if (typeof v === 'string') headers.set(k, v);
  const ctl = new AbortController();
  req.on('close', () => { if (!req.complete) ctl.abort(); });
  return new Request(url, { method: req.method, headers, body: body.length ? body : undefined, signal: ctl.signal });
}
async function pipe(webRes, res) {
  res.writeHead(webRes.status, Object.fromEntries(webRes.headers.entries()));
  if (!webRes.body) { res.end(); return; }
  const reader = webRes.body.getReader();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    res.write(Buffer.from(value));
  }
  res.end();
}
function mock(res) {
  const q = process.env.DISCOVER_MOCK_FIXTURE || 'discover-list';
  const f = path.join(HERE, 'tests', 'fixtures', `${q}.sse`);
  res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache' });
  // a Windows checkout (core.autocrlf) hands the fixture over with CRLF; SSE frames end in \n\n
  const chunks = fs.readFileSync(f, 'utf8').replace(/\r\n/g, '\n').split('\n\n').filter(Boolean);
  let i = 0;
  const tick = () => { if (i >= chunks.length) { res.end(); return; } res.write(chunks[i++] + '\n\n'); setTimeout(tick, 60); };
  tick();
}

const PY = fs.existsSync(path.join(ROOT, 'scraper', '.venv', 'bin', 'python')) ? path.join(ROOT, 'scraper', '.venv', 'bin', 'python') : 'python3';
const RANK_CITIES = ['los-angeles'];   // keep in step with PARAMS in scraper/move_venue_rank.py
const run = (args) => new Promise(resolve => execFile(PY, args, { cwd: ROOT, maxBuffer: 64 << 20 },
  (err, stdout, stderr) => resolve({ code: err ? (err.code || 1) : 0, stdout, stderr })));
let rankQueue = Promise.resolve();     // one move at a time: each rewrites the order file and the registry
async function localRank(req, res) {
  const json = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(body)); };
  if (req.method === 'GET') return json(200, { ok: true, cities: RANK_CITIES });
  if (req.method !== 'POST') return json(405, { ok: false, error: 'method' });
  const chunks = [];
  for await (const ch of req) chunks.push(ch);
  let b;
  try { b = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (e) { return json(400, { ok: false, error: 'bad json' }); }
  const to = Number(b.to);
  if (!RANK_CITIES.includes(b.city) || !/^[a-z0-9-]+$/.test(String(b.id || '')) || !(to >= 1 && to <= 10000)) return json(400, { ok: false, error: 'bad request' });
  const job = rankQueue.then(async () => {
    const mv = await run([path.join(ROOT, 'scraper', 'move_venue_rank.py'), '--city', b.city, '--id', b.id, '--to', String(Math.round(to))]);
    let out;
    try { out = JSON.parse(mv.stdout.trim().split('\n').pop()); } catch (e) { out = { ok: false, error: 'move failed', log: mv.stderr.slice(-2000) }; }
    if (!out.ok) return out;
    const build = await run([path.join(HERE, 'build.py')]);
    if (build.code !== 0) console.error('build after rank move failed:', build.stderr.slice(-2000));
    console.log(`rank move: ${b.city}/${b.id} -> ${out.to} (registry rank ${out.rank})`);
    return out;
  });
  rankQueue = job.catch(() => {});
  const out = await job;
  return json(out.ok ? 200 : 500, out);
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.url.startsWith('/api/local-rank')) return localRank(req, res);
    if (req.url.startsWith('/api/discover')) {
      if (!POST) { res.writeHead(503); res.end('Discover is not available on this preview'); return; }
      if (req.method === 'POST' && process.env.DISCOVER_MOCK === '1') { req.resume(); await new Promise(r => req.on('end', r)); return mock(res); }
      const chunks = [];
      for await (const ch of req) chunks.push(ch);
      const webReq = toRequest(req, Buffer.concat(chunks));
      const webRes = req.method === 'POST' ? await POST(webReq) : GET(webReq);
      return pipe(webRes, res);
    }
    let p = decodeURIComponent(req.url.split('?')[0]);
    if (p === '/') p = '/index.html';
    const file = path.join(DIST, p);
    if (!file.startsWith(DIST)) { res.writeHead(403); res.end(); return; }
    fs.readFile(file, (err, data) => {
      if (err) { res.writeHead(404); res.end('not found'); return; }
      res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream' });
      res.end(data);
    });
  } catch (e) {
    console.error(e);
    if (!res.headersSent) res.writeHead(500);
    res.end('server error');
  }
});
server.listen(PORT, () => {
  console.log(`dev server on http://localhost:${PORT}  dist=${DIST}`);
  console.log(`  env: ${envFile || 'none'}  key: ${process.env.ANTHROPIC_API_KEY ? 'set' : 'MISSING'}  mock: ${process.env.DISCOVER_MOCK === '1'}`);
});
