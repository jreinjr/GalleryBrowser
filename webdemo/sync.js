/* Sync: the person's marks follow them across devices and web addresses
 * (Carlton, 2026-10-17). Loaded before app.js; app.js keeps reading and
 * writing localStorage as before. Every write to a synced key is diffed per
 * item (a saved show, a gallery's 👁/💎, a picture or show rating, an archive
 * entry, a city's ranking, a list) and stamped in localStorage['sync.meta'];
 * the whole set goes to /api/sync, which merges it with the other devices'
 * (later stamp wins per item, deletions included) and answers the merge.
 * Marks a device had before it joined keep their own dates where they carry
 * one, else count as oldest, so joining only ever adds. Changes from other
 * devices are taken in with a reload: on opening the page, on coming back to
 * it, or when it has sat idle; never under an open sheet. A device joins with
 * the sync code from Settings → Sync, or by opening the join link (#sync=…).
 */
(function () {
  'use strict';
  let LS;
  try { LS = window.localStorage; LS.getItem('x'); } catch (e) { return; }

  // How each synced key splits into items. Per-device niceties (city, filters,
  // card size, last list) stay out.
  const KINDS = {
    savedShowIDs: 'set', favoriteVenueIDs: 'set', gemVenueIDs: 'set', eyeVenueIDs: 'set',
    imageFavs: 'map', showArchive: 'map', heat: 'map', pup: 'map', rankMoves: 'map',
    galleryOrder: 'cities', museumOrder: 'cities', lists: 'lists', cityOrder: 'whole',
  };
  const proto = Object.getPrototypeOf(LS);
  const rawSet = proto.setItem, rawRemove = proto.removeItem;
  const get = k => LS.getItem(k);
  const put = (k, v) => { try { rawSet.call(LS, k, v); } catch (e) { /* full */ } };
  const drop = k => rawRemove.call(LS, k);
  const readJSON = (k, d) => { try { const v = JSON.parse(get(k)); return v == null ? d : v; } catch (e) { return d; } };

  // Gems carried over from the older "See" toggle (app.js reads seeVenueIDs
  // only while gemVenueIDs is unset); make that explicit before syncing.
  if (get('gemVenueIDs') == null && get('seeVenueIDs') != null) put('gemVenueIDs', get('seeVenueIDs'));

  // value string -> Map(item -> [json, ownStamp])
  function explode(key, str) {
    const out = new Map();
    if (str == null) return out;
    let v;
    try { v = JSON.parse(str); } catch (e) { return out; }
    const stamp = x => (x && typeof x === 'object' && +(x.updatedAt || x.savedAt || x.at)) || 0;
    switch (KINDS[key]) {
      case 'set': if (Array.isArray(v)) v.forEach(id => out.set(String(id), ['1', 0])); break;
      case 'map': if (v && typeof v === 'object') Object.entries(v).forEach(([id, x]) => out.set(id, [JSON.stringify(x), stamp(x)])); break;
      case 'cities': if (v && v.cities) Object.entries(v.cities).forEach(([id, x]) => out.set(id, [JSON.stringify(x), stamp(x)])); break;
      case 'lists': if (v && Array.isArray(v.lists)) v.lists.forEach(l => l && l.id && out.set(l.id, [JSON.stringify(l), stamp(l)])); break;
      case 'whole': out.set('_', [str, stamp(v)]); break;
    }
    return out;
  }
  // Map(item -> json) back into the value string, keeping the current order.
  function implode(key, items, cur) {
    const ids = [...explode(key, cur).keys()].filter(id => items.has(id));
    items.forEach((_, id) => { if (!ids.includes(id)) ids.push(id); });
    const obj = () => Object.fromEntries(ids.map(id => [id, JSON.parse(items.get(id))]));
    switch (KINDS[key]) {
      case 'set': return JSON.stringify(ids);
      case 'map': return JSON.stringify(obj());
      case 'cities': return JSON.stringify({ v: 1, cities: obj() });
      case 'lists': return JSON.stringify({ v: 1, lists: ids.map(id => JSON.parse(items.get(id))).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0)) });
      case 'whole': return items.get('_');
    }
  }

  // meta: { key: { item: [stamp, alive 1|0] } }
  let meta = readJSON('sync.meta', {});
  const saveMeta = () => put('sync.meta', JSON.stringify(meta));

  // Stamp what changed on every write the app makes to a synced key.
  function noteWrite(key, before, after) {
    const a = explode(key, before), b = explode(key, after), now = Date.now();
    const m = meta[key] || (meta[key] = {});
    let changed = false;
    b.forEach(([v], id) => { if (!a.has(id) || a.get(id)[0] !== v) { m[id] = [now, 1]; changed = true; } });
    a.forEach((_, id) => { if (!b.has(id)) { m[id] = [now, 0]; changed = true; } });
    if (changed) { saveMeta(); schedule(); }
  }
  proto.setItem = function (k, v) {
    if (this !== LS || !KINDS[k]) return rawSet.call(this, k, v);
    const before = get(k);
    rawSet.call(this, k, v);
    noteWrite(k, before, String(v));
  };
  proto.removeItem = function (k) {
    if (this !== LS || !KINDS[k]) return rawRemove.call(this, k);
    const before = get(k);
    rawRemove.call(this, k);
    noteWrite(k, before, null);
  };

  // This device's doc: every live item with its stamp, and the deletions.
  function localDoc() {
    const doc = {};
    Object.keys(KINDS).forEach(key => {
      const d = {}, m = meta[key] || {};
      explode(key, get(key)).forEach(([v, own], id) => { d[id] = [m[id] && m[id][1] ? m[id][0] : (own || 1), v]; });
      Object.entries(m).forEach(([id, [t, alive]]) => { if (!alive && !(id in d)) d[id] = [t, null]; });
      doc[key] = d;
    });
    return doc;
  }
  // Take the merged doc in; true when anything on this device changes. dry:
  // only say whether it would.
  function apply(doc, dry) {
    let changed = false;
    const mineAll = localDoc();
    Object.keys(KINDS).forEach(key => {
      const r = doc && doc[key];
      if (!r) return;
      const mine = mineAll[key], items = new Map(), m = dry ? {} : (meta[key] || (meta[key] = {}));
      new Set([...Object.keys(mine), ...Object.keys(r)]).forEach(id => {
        const e = (!mine[id] || (r[id] && r[id][0] >= mine[id][0])) ? r[id] : mine[id];
        m[id] = [e[0], e[1] == null ? 0 : 1];
        if (e[1] != null) items.set(id, e[1]);
      });
      const cur = get(key);
      if (!items.size && cur == null) return;
      const next = items.size || KINDS[key] !== 'whole' ? implode(key, items, cur) : null;
      if (next === cur) return;
      changed = true;
      if (dry) return;
      if (next == null) drop(key); else put(key, next);
    });
    if (!dry) saveMeta();
    return changed;
  }

  const code = () => get('sync.code');
  let status = { state: code() ? 'idle' : 'off', at: +get('sync.lastAt') || 0, error: null };
  const listeners = new Set();
  const tell = patch => { Object.assign(status, patch); listeners.forEach(f => { try { f(status); } catch (e) { /* */ } }); };
  let lastRemote = null, pending = false, inflight = null, timer = null, lastTouch = Date.now();

  // A reload is how app.js picks up other devices' changes. Not under an open
  // sheet or while typing, and not twice in a row.
  function canReload() {
    if (document.querySelector('.sheet.open, .sheet-backdrop.open')) return false;
    const a = document.activeElement;
    if (a && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return false;
    try { if (Date.now() - (+sessionStorage.getItem('sync.reloadAt') || 0) < 15000) return false; } catch (e) { /* */ }
    return true;
  }
  function reload() { try { sessionStorage.setItem('sync.reloadAt', String(Date.now())); } catch (e) { /* */ } location.reload(); }

  // take: also take other devices' changes in (with a reload) when safe now.
  async function run(take) {
    if (!code()) return false;
    if (inflight) { await inflight; }
    inflight = (async () => {
      tell({ state: 'syncing' });
      try {
        const r = await fetch('/api/sync', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ code: code(), doc: localDoc() }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(r.status === 503 ? 'Sync is not set up on the server yet' : (j.error || 'HTTP ' + r.status));
        put('sync.lastAt', String(Date.now()));
        const remote = j.doc;
        // Did the server have anything this device lacks?
        const differs = apply(remote, true);
        pending = differs;
        tell({ state: 'idle', at: Date.now(), error: null });
        if (differs && (take === 'force' || (take && canReload()))) { apply(remote); reload(); return true; }
        lastRemote = differs ? remote : null;
        return false;
      } catch (e) {
        tell({ state: 'error', error: e.message || 'Could not reach sync' });
        return false;
      } finally { inflight = null; }
    })();
    return inflight;
  }
  function schedule() { if (!code()) return; clearTimeout(timer); timer = setTimeout(() => run(false), 2000); }

  // Opening the page, coming back to it, and every minute while it is open.
  ['pointerdown', 'keydown', 'scroll'].forEach(t => addEventListener(t, () => { lastTouch = Date.now(); }, { passive: true, capture: true }));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') run(true); });
  setInterval(() => {
    if (document.visibilityState !== 'visible' || !code()) return;
    if (pending && lastRemote && Date.now() - lastTouch > 20000 && canReload()) { apply(lastRemote); reload(); return; }
    run(Date.now() - lastTouch > 20000);
  }, 60000);

  function newCode() {
    const abc = 'abcdefghjkmnpqrstuvwxyz23456789', b = new Uint8Array(16);
    crypto.getRandomValues(b);
    return [...b].map(x => abc[x % abc.length]).join('').match(/.{4}/g).join('-');
  }
  const clean = c => String(c || '').toLowerCase().replace(/[^a-z0-9]/g, '').match(/.{1,4}/g)?.join('-') || '';

  window.GBSync = {
    status: () => ({ ...status, on: !!code(), code: code() }),
    subscribe(f) { listeners.add(f); return () => listeners.delete(f); },
    joinLink: () => code() ? location.origin + location.pathname + '#sync=' + code() : null,
    start() { put('sync.code', newCode()); return run('force'); },
    join(c) {
      c = clean(c);
      if (!/^[a-z0-9]{4}(-[a-z0-9]{4}){3,7}$/.test(c)) return Promise.resolve({ error: 'That code doesn’t look right' });
      put('sync.code', c);
      return run('force');
    },
    now: () => run('force'),
    stop() { drop('sync.code'); tell({ state: 'off', error: null }); },
  };

  // The join link: #sync=<code> joins this device, then clears the address.
  const m = /[#&]sync=([a-z0-9-]+)/i.exec(location.hash);
  if (m) {
    history.replaceState(null, '', location.pathname + location.search);
    if (clean(m[1]) !== code()) { window.GBSync.join(m[1]); return; }
  }
  if (code()) run(true);
})();
