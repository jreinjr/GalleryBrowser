// Cross-device sync of the person's marks (see api/sync.js). A doc is
//   { [storageKey]: { [item]: [t, value | null] } }
// one entry per saved show, gallery mark, rating, ranking city, list…; null is
// a deletion. Merging keeps, per item, the entry with the later t (the stored
// one on a tie), so two devices' marks union and a deletion wins only over
// older edits.
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export const MAX_BYTES = 3_000_000;
export const codeOk = c => typeof c === 'string' && /^[a-z0-9]{4}(-[a-z0-9]{4}){3,7}$/.test(c);
const keyOf = code => 'gb-sync:' + createHash('sha256').update(code).digest('hex');

export function cleanDoc(raw) {
  const out = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const [k, items] of Object.entries(raw)) {
    if (!/^[A-Za-z]{1,40}$/.test(k) || !items || typeof items !== 'object') continue;
    const o = {};
    for (const [id, e] of Object.entries(items)) {
      if (!Array.isArray(e) || typeof e[0] !== 'number' || !(e[1] === null || typeof e[1] === 'string')) continue;
      o[id] = [e[0], e[1]];
    }
    out[k] = o;
  }
  return out;
}

export function mergeDocs(stored, incoming) {
  const out = {};
  for (const k of new Set([...Object.keys(stored), ...Object.keys(incoming)])) {
    const a = stored[k] || {}, b = incoming[k] || {}, o = { ...a };
    for (const [id, e] of Object.entries(b)) if (!o[id] || e[0] > o[id][0]) o[id] = e;
    out[k] = o;
  }
  return out;
}

// Upstash Redis over REST: the Vercel Storage integration sets KV_REST_API_*
// (older ones UPSTASH_REDIS_REST_*). The local preview uses a JSON file.
export function makeStore() {
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (url && token) {
    const cmd = async args => {
      const r = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(args) });
      if (!r.ok) throw new Error(`redis ${r.status}`);
      return (await r.json()).result;
    };
    return {
      kind: 'redis',
      async get(code) { const v = await cmd(['GET', keyOf(code)]); return v ? JSON.parse(v) : {}; },
      async put(code, doc) { await cmd(['SET', keyOf(code), JSON.stringify(doc)]); },
    };
  }
  const file = process.env.SYNC_LOCAL_FILE;
  if (file) {
    const all = () => { try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch (e) { return {}; } };
    return {
      kind: 'file',
      async get(code) { return all()[keyOf(code)] || {}; },
      async put(code, doc) {
        const a = all(); a[keyOf(code)] = doc;
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(file + '.tmp', JSON.stringify(a)); fs.renameSync(file + '.tmp', file);
      },
    };
  }
  return null;
}
