// Request guards for /api/discover. None of this is a security boundary — the
// spend-capped API key is — but it stops drive-by browser abuse and malformed
// input. The per-IP bucket and the daily fuse live in module memory, so they
// reset on every cold start and are per instance.

const LIMITS = { questionChars: 500, historyTurns: 12, historyChars: 12000, savedShows: 50, savedLists: 30 };
const BUCKET = { capacity: 10, refillPerMin: 1 };
const DAILY_FUSE = 500;

const hostOf = url => { try { return new URL(url).host; } catch (e) { return null; } };

export function checkOrigin(request) {
  const origin = request.headers.get('origin');
  const referer = request.headers.get('referer');
  const host = request.headers.get('x-forwarded-host') || request.headers.get('host') || '';
  const from = hostOf(origin || referer || '');
  if (!from) return process.env.DISCOVER_ALLOW_NO_ORIGIN === '1' ? null : 'missing origin';
  const isLocal = /^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(from);
  const prod = process.env.VERCEL_PROJECT_PRODUCTION_URL;
  if (from === host || isLocal || (prod && from === prod) || from.endsWith('.vercel.app') && host.endsWith('.vercel.app') && from === host) return null;
  return `origin ${from} not allowed`;
}

export function clientIp(request) {
  const xf = request.headers.get('x-forwarded-for') || '';
  return xf.split(',')[0].trim() || request.headers.get('x-real-ip') || 'unknown';
}

const buckets = new Map();
let dailyCount = 0, dailyDay = '';
export function rateLimit(ip) {
  const today = new Date().toISOString().slice(0, 10);
  if (today !== dailyDay) { dailyDay = today; dailyCount = 0; }
  if (dailyCount >= DAILY_FUSE) return { ok: false, retryAfter: 3600, reason: 'daily cap' };
  const now = Date.now();
  const b = buckets.get(ip) || { tokens: BUCKET.capacity, ts: now };
  b.tokens = Math.min(BUCKET.capacity, b.tokens + (now - b.ts) / 60000 * BUCKET.refillPerMin);
  b.ts = now;
  if (b.tokens < 1) { buckets.set(ip, b); return { ok: false, retryAfter: Math.ceil((1 - b.tokens) / BUCKET.refillPerMin * 60), reason: 'too many requests' }; }
  b.tokens -= 1;
  buckets.set(ip, b);
  dailyCount += 1;
  if (buckets.size > 5000) buckets.clear();
  return { ok: true };
}

const str = (v, max) => typeof v === 'string' ? v.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, '').slice(0, max) : '';

// Returns { body } or { error }.
export function validateBody(raw, cities) {
  if (!raw || typeof raw !== 'object') return { error: 'body must be a JSON object' };
  const city = str(raw.city, 40);
  if (!cities.includes(city)) return { error: `unknown city (have: ${cities.join(', ')})` };
  const question = str(raw.question, LIMITS.questionChars).trim();
  if (!question) return { error: 'question is required' };
  const history = [];
  let chars = 0;
  for (const t of Array.isArray(raw.history) ? raw.history.slice(-LIMITS.historyTurns) : []) {
    if (!t || (t.role !== 'user' && t.role !== 'assistant')) continue;
    const text = str(t.text, 4000);
    if (!text) continue;
    const turn = { role: t.role, text };
    if (t.role === 'assistant' && t.presented && typeof t.presented === 'object') {
      turn.presented = {
        title: str(t.presented.title, 120), kind: t.presented.kind === 'route' ? 'route' : 'list',
        ids: (Array.isArray(t.presented.ids) ? t.presented.ids : []).slice(0, 40).map(x => str(x, 120).replace(/^[a-z-]+\//, '')).filter(Boolean),
        dropped: (Array.isArray(t.presented.dropped) ? t.presented.dropped : []).slice(0, 10).map(x => str(x, 120)).filter(Boolean),
      };
    }
    chars += text.length;
    if (chars > LIMITS.historyChars) break;
    history.push(turn);
  }
  const ctx = {
    filterSummary: str(raw.filterSummary, 200) || null,
    activeList: str(raw.activeList, 120) || null,
    savedLists: (Array.isArray(raw.savedLists) ? raw.savedLists : []).slice(0, LIMITS.savedLists).map(x => str(x, 80)).filter(Boolean),
    savedShows: (Array.isArray(raw.savedShows) ? raw.savedShows : []).slice(0, LIMITS.savedShows).map(x => str(x, 120).replace(/^[a-z-]+\//, '')).filter(Boolean),
  };
  return { body: { city, question, history, ctx } };
}
