// /api/sync — keeps the person's marks (saved shows, 👁/💎, favorites, ratings,
// the archive, rankings, lists) the same on every device. The browser holds a
// sync code (Settings → Sync); the doc is stored under a hash of it.
//   GET            {configured}
//   POST {code, doc}  merges doc into the stored one, answers {doc} (the merge)
// Merge rules and the store: _lib/sync.js.

import { MAX_BYTES, cleanDoc, codeOk, makeStore, mergeDocs } from './_lib/sync.js';

const json = (status, obj) => new Response(JSON.stringify(obj), { status, headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' } });

export function GET() {
  return json(200, { ok: true, configured: !!makeStore() });
}

export async function POST(request) {
  const store = makeStore();
  if (!store) return json(503, { error: 'sync is not configured' });
  const text = await request.text();
  if (text.length > MAX_BYTES) return json(413, { error: 'too large' });
  let b;
  try { b = JSON.parse(text); } catch (e) { return json(400, { error: 'invalid JSON' }); }
  if (!codeOk(b && b.code)) return json(400, { error: 'bad code' });
  try {
    const merged = mergeDocs(cleanDoc(await store.get(b.code)), cleanDoc(b.doc));
    await store.put(b.code, merged);
    return json(200, { ok: true, doc: merged });
  } catch (e) {
    return json(502, { error: 'store failed' });
  }
}
