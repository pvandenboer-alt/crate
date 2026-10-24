"use strict";
/* Crate: rediscover the music in your Notion music library. Reads Artists, Albums, New Releases and
   the Blocklist straight from Notion and keeps an offline copy (library data and artwork in IndexedDB).
   It writes back only statuses (a release's Status, Excluded for a blocked artist or album), Blocklist
   rows and its usage log (Crate log). */

/* ---------- config ---------- */
const API = 'https://api.notion.com/v1';
const NOTION_VERSION = '2025-09-03';
const DAY = 864e5;
const ls = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true; } catch { return false; } },
  del(k) { try { localStorage.removeItem(k); } catch { } }
};
/* Everything personal lives on this phone only: the Notion key, the database IDs Crate found with it,
   and the Spotify client ID (not a secret, but yours). Nothing is built into the app. */
const cfg = Object.assign({ token: '', spotifyId: '', ds: {} }, ls.get('crate.cfg') || {});
const saveCfg = () => ls.set('crate.cfg', cfg);
const token = () => cfg.token || '';
const DS = Object.assign({ artists: '', albums: '', releases: '', blocklist: '', log: '', report: '' }, cfg.ds);
const connected = () => !!(token() && DS.artists && DS.albums && DS.releases && DS.blocklist);
const Cap = window.Capacitor || null;
const isNative = !!(Cap && Cap.isNativePlatform && Cap.isNativePlatform());
/* Native plugins without their npm JavaScript (this app has no bundler) only appear in Capacitor.Plugins
   once registered, so register any the native side has. */
const plugin = n => {
  if (!Cap) return null;
  if (Cap.Plugins && Cap.Plugins[n]) return Cap.Plugins[n];
  return isNative && Cap.isPluginAvailable && Cap.isPluginAvailable(n) && Cap.registerPlugin ? Cap.registerPlugin(n) : null;
};
const AppP = isNative ? plugin('App') : null;
const Http = isNative ? plugin('CapacitorHttp') : null;
const Net = isNative ? plugin('Network') : null;

/* ---------- helpers ---------- */
const $ = s => document.querySelector(s);
const app = $('#app'), nav = $('#nav'), tabs = $('#tabs'), overlay = $('#overlay'), ptr = $('#ptr');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const cleanId = id => String(id || '').replace(/-/g, '');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const fold = s => String(s ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/&/g, 'and').replace(/\s+/g, ' ').trim();
const nf = n => Number(n || 0).toLocaleString(undefined);
const year = d => d ? String(d).slice(0, 4) : '';
const thisYear = new Date().getFullYear();
const monthYear = d => { if (!d) return ''; const x = new Date(String(d).slice(0, 10) + 'T00:00:00'); return isNaN(x) ? '' : x.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }); };
const dayMonth = d => { if (!d) return ''; const x = new Date(String(d).slice(0, 10) + 'T00:00:00'); return isNaN(x) ? '' : x.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }); };
const hash = s => { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
const tint = name => { const h = hash(fold(name)) % 360; return `linear-gradient(140deg, hsl(${h} 52% 56%), hsl(${(h + 32) % 360} 48% 36%))`; };
const initials = name => { const w = String(name || '?').replace(/^the\s+/i, '').split(/[\s&,+-]+/).filter(Boolean); return ((w[0] || '?')[0] + (w[1] ? w[1][0] : (w[0] || '')[1] || '')).toUpperCase(); };
const plural = (n, one, many) => `${nf(n)} ${n === 1 ? one : (many || one + 's')}`;
const shuffle = (arr, seed) => { const a = arr.slice(); let s = seed || 1; for (let i = a.length - 1; i > 0; i--) { s = (Math.imul(s, 1103515245) + 12345) >>> 0; const j = s % (i + 1);[a[i], a[j]] = [a[j], a[i]]; } return a; };

const svg = (d, extra = '') => `<svg class="i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" ${extra}>${d}</svg>`;
const I = {
  back: svg('<path d="M15 5l-7 7 7 7"/>', 'stroke-width="2.6"'),
  chev: '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>',
  chevSm: svg('<path d="M9 5l7 7-7 7"/>', 'stroke-width="2.4"'),
  gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>', 'stroke-width="1.8"'),
  refresh: svg('<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 3v6h-6"/>'),
  search: svg('<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>', 'stroke-width="2.2"'),
  close: svg('<path d="M6 6l12 12M18 6L6 18"/>', 'stroke-width="3"'),
  plus: svg('<path d="M12 5v14M5 12h14"/>', 'stroke-width="2.4"'),
  more: svg('<circle cx="5" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/><circle cx="19" cy="12" r="1.6" fill="currentColor" stroke="none"/>'),
  home: svg('<path d="M3.5 10.5L12 3.5l8.5 7V20a1 1 0 0 1-1 1h-5v-6.5h-5V21h-5a1 1 0 0 1-1-1z" fill="currentColor" stroke="none"/>'),
  filter: svg('<path d="M4 6h16M7 12h10M10 18h4"/>', 'stroke-width="2.4"'),
  sort: svg('<path d="M7 4v16M3.5 7.5L7 4l3.5 3.5M17 20V4M13.5 16.5L17 20l3.5-3.5"/>'),
  mic: svg('<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21"/>'),
  disc: svg('<rect x="3.5" y="3.5" width="17" height="17" rx="3"/><circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none"/>'),
  flame: svg('<path d="M12 22c-4.4 0-7.5-3-7.5-7.1 0-4.6 4.1-6.9 4.5-11.4 3.3 2 4.5 4.9 4.1 7.3 1.3-.7 2.1-2 2.4-3.6 2.3 2 4 4.6 4 8 0 4-3.1 6.8-7.5 6.8zm0-2.3c1.9 0 3.2-1.2 3.2-3 0-1.5-.8-2.7-1.9-3.7-.2.9-.8 1.6-1.6 2 .2-1.5-.4-3.1-2-4.2-.3 2.3-2.1 3.5-2.1 5.9 0 1.8 1.3 3 4.4 3z" fill="currentColor" stroke="none" fill-rule="evenodd"/>'),
  sparkle: svg('<path d="M12 2.5l2.3 6.2 6.2 2.3-6.2 2.3L12 19.5l-2.3-6.2L3.5 11l6.2-2.3z" fill="currentColor" stroke="none"/><path d="M19 15.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" fill="currentColor" stroke="none"/>'),
  searchFill: svg('<circle cx="10.5" cy="10.5" r="6.5" stroke-width="2.8"/><path d="M15.5 15.5L21 21" stroke-width="3"/>'),
  play: svg('<path d="M7 4.5v15l12.5-7.5z" fill="currentColor" stroke="none"/>'),
  pause: svg('<rect x="6" y="4.5" width="4.2" height="15" rx="1" fill="currentColor" stroke="none"/><rect x="13.8" y="4.5" width="4.2" height="15" rx="1" fill="currentColor" stroke="none"/>'),
  next: svg('<path d="M3.5 5.5v13l9-6.5z" fill="currentColor" stroke="none"/><path d="M11.5 5.5v13l9-6.5z" fill="currentColor" stroke="none"/>'),
  record: svg('<circle cx="12" cy="12" r="9.2"/><circle cx="12" cy="12" r="5.6" stroke-width="1.2" opacity=".55"/><circle cx="12" cy="12" r="2" fill="currentColor" stroke="none"/>', 'stroke-width="1.8"'),
  check: svg('<path d="M5 12.5l4.5 4.5L19 7.5"/>', 'stroke-width="2.6"'),
  hide: svg('<path d="M4 4l16 16"/><path d="M9.9 5.2A9.7 9.7 0 0 1 12 5c5 0 8.5 4.5 9.5 7a13 13 0 0 1-3 4.1M6.3 6.6A13 13 0 0 0 2.5 12c1 2.5 4.5 7 9.5 7 1.5 0 2.9-.4 4.1-1"/>'),
  block: svg('<circle cx="12" cy="12" r="8.5"/><path d="M6 6l12 12"/>'),
  music: svg('<path d="M9 18V5l11-2v13"/><circle cx="6.5" cy="18" r="2.5"/><circle cx="17.5" cy="16" r="2.5"/>')
};

/* ---------- heat: one five-step scale for artists, albums and songs ---------- */
/* Each kind is ranked against its own kind by plays: top 5% Inferno, next 10% Blaze, 20% Flame,
   30% Ember, the rest Spark. Shown as five rising bars, amber to hot pink; Inferno glows. */
const HEAT = { 5: ['Inferno', 'among your most played'], 4: ['Blaze', 'played a whole lot'], 3: ['Flame', 'played a lot'],
  2: ['Ember', 'played quite a bit'], 1: ['Spark', 'played now and then'] };
const heat = h => h ? `<span class="heat h${h}" role="img" aria-label="${HEAT[h][0]}: ${HEAT[h][1]}"><i></i><i></i><i></i><i></i><i></i></span>` : '';
const heatLine = h => h ? `<span class="heat-line h${h}">${heat(h)}<b>${HEAT[h][0]}</b><span>${HEAT[h][1]}</span></span>` : '';
/* The heat filter is a lens across Home, genres, the library and years: tick the levels you want. */
const heatOn = () => state.heatFilter.length > 0 && state.heatFilter.length < 5;
const heatOk = x => !heatOn() || state.heatFilter.includes(x.h);
const hf = list => heatOn() ? list.filter(heatOk) : list;
function rankHeat(list, plays) {
  list.slice().sort((x, y) => plays(y) - plays(x)).forEach((x, i, all) => {
    const p = i / all.length; x.h = p < .05 ? 5 : p < .15 ? 4 : p < .35 ? 3 : p < .65 ? 2 : 1;
  });
}

/* ---------- Notion ---------- */
async function napi(path, { method = 'GET', body, read = false } = {}) {
  const t = token();
  if (!t) throw { code: 'no_token' };
  const headers = { Authorization: 'Bearer ' + t, 'Notion-Version': NOTION_VERSION };
  const opts = { method, headers };
  if (body) { headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  for (let attempt = 0; attempt < 5; attempt++) {
    let res;
    try { res = await fetch(API + path, opts); }
    catch (e) { if (read && attempt < 1) { await sleep(800); continue; } throw { code: 'offline', message: String(e && e.message || e) }; }
    if (res.status === 429) { await sleep((Number(res.headers.get('Retry-After')) || 1) * 1000 + 300); continue; }
    let data = null; try { data = await res.json(); } catch { }
    if (res.ok) return data;
    if (res.status >= 500 && attempt < 2) { await sleep(900 * (attempt + 1)); continue; }
    throw { code: (data && data.code) || 'http_' + res.status, status: res.status, message: (data && data.message) || '' };
  }
  throw { code: 'rate_limited' };
}
const ERRORS = {
  no_token: 'Connect Notion first.',
  unauthorized: 'Notion rejected the key. Copy the whole Internal Integration Secret again (Settings → Notion → Use a different key).',
  object_not_found: 'Crate can\'t see its databases. In Notion, open the page that holds them → ••• → Connections and add your integration.',
  restricted_resource: 'The integration needs access to the page that holds the Crate databases (••• → Connections), with "Update content" and "Insert content".',
  offline: 'You\'re offline. Showing your saved library.',
  rate_limited: 'Notion is busy. Try again in a minute.'
};
const errText = e => ERRORS[e && e.code] || ('Notion error' + (e && e.message ? ': ' + String(e.message).slice(0, 140) : '. Try again.'));

const rt = a => (a || []).map(t => t.plain_text ?? t.text?.content ?? '').join('');
function pv(p) {
  if (!p) return null;
  const v = p[p.type];
  switch (p.type) {
    case 'title': case 'rich_text': return rt(v);
    case 'select': return v ? v.name : '';
    case 'multi_select': return (v || []).map(o => o.name);
    case 'date': return v ? v.start : '';
    case 'relation': return (v || []).map(r => cleanId(r.id));
    default: return v ?? null;
  }
}
const pairs = s => String(s || '').split(';').map(x => x.trim()).filter(Boolean).map(x => { const i = x.lastIndexOf(':'); return i > 0 ? [x.slice(0, i).trim().toLowerCase(), Number(x.slice(i + 1)) || 0] : [x.toLowerCase(), 0]; });
/* Top songs JSON from the sync: [title, album, plays, peak year, most plays in 30 days, that month, Spotify track ID]. */
function parseSongs(text) {
  try {
    const a = JSON.parse(text || '[]');
    return Array.isArray(a) ? a.filter(Array.isArray).map(x => ({ n: String(x[0] || ''), a: String(x[1] || ''), c: Number(x[2]) || 0, y: Number(x[3]) || null, r: Number(x[4]) || 0, rm: String(x[5] || ''), t: String(x[6] || '') })).filter(x => x.n) : [];
  } catch { return []; }
}
/* Plays by month from the sync: '2014:0.0.3.12.0.0.0.0.0.23.4.1; 2015:...' -> { '2014-04': 12, ... } */
function monthsOf(text) {
  const out = {};
  for (const part of String(text || '').split(';')) {
    const [y, row] = part.trim().split(':');
    if (!/^\d{4}$/.test(y || '') || !row) continue;
    row.split('.').forEach((n, i) => { if (Number(n) > 0) out[`${y}-${String(i + 1).padStart(2, '0')}`] = Number(n); });
  }
  return out;
}
const yearsOf = (s, peak, plays) => { const o = {}; for (const [y, n] of pairs(s)) if (/^\d{4}$/.test(y) && n) o[y] = n; if (!Object.keys(o).length && peak && plays) o[peak] = plays; return o; };
function toArtist(pg) {
  const P = pg.properties || {}, v = k => pv(P[k]);
  const peak = v('Peak year') || null, plays = v('Plays') || 0;
  return {
    id: cleanId(pg.id), name: v('Name') || 'Unknown artist', aliases: String(v('Aliases') || '').split(';').map(s => s.trim()).filter(Boolean),
    status: v('Status') || '', level: v('Level') || '', plays, genres: v('Genres') || [], tags: pairs(v('Tags')).slice(0, 12),
    similar: v('Similar artists') || [], image: v('Image') || '', peak, first: v('First listened') || '',
    last: v('Last listened') || '', spotify: String(v('Spotify ID') || '').trim(), bio: v('Bio') || '', years: yearsOf(v('Plays by year'), peak, plays),
    songs: parseSongs(v('Top songs')), months: monthsOf(v('Plays by month'))
  };
}
function toAlbum(pg) {
  const P = pg.properties || {}, v = k => pv(P[k]);
  const peak = v('Peak year') || null, plays = v('Plays') || 0;
  return {
    id: cleanId(pg.id), name: v('Name') || 'Untitled', artistId: (v('Artist') || [])[0] || '', credited: v('Credited as') || '',
    type: v('Type') || '', release: v('Release date') || '', plays, level: v('Level') || '', peak,
    cover: v('Cover') || '', first: v('First listened') || '', last: v('Last listened') || '',
    tracks: v('Distinct tracks played') || 0, status: v('Status') || '', years: yearsOf(v('Plays by year'), peak, plays),
    spotify: String(v('Spotify ID') || '').trim(), full: v('Full listens') || 0
  };
}
function toRelease(pg) {
  const P = pg.properties || {}, v = k => pv(P[k]);
  return {
    id: cleanId(pg.id), name: v('Name') || 'Untitled', artistId: (v('Artist') || [])[0] || '', credited: v('Credited as') || '',
    release: v('Release date') || '', type: v('Type') || '', status: v('Status') || 'New', found: v('Found on') || '',
    spotify: String(v('Spotify ID') || '').trim(), cover: v('Cover') || '',
    tracks: v('Tracks') || 0, length: v('Length') || 0, label: v('Label') || '', tracklist: v('Track list') || ''
  };
}
const toBlock = pg => { const P = pg.properties || {}; return { id: cleanId(pg.id), name: pv(P.Name) || '', type: pv(P.Type) || 'Artist', artist: pv(P.Artist) || '' }; };
const toExcluded = pg => { const P = pg.properties || {}; return { id: cleanId(pg.id), name: pv(P.Name) || '', image: pv(P.Image) || pv(P.Cover) || '', credited: pv(P['Credited as']) || '' }; };
/* Ask Notion only for the fields the app uses (bios, tags and relation lists of albums are large and
   mostly unused). Property IDs come from the database schema, cached; if Notion ever refuses the
   filter, the app falls back to whole pages. */
const FIELDS = {
  artists: ['Name', 'Aliases', 'Status', 'Plays', 'Genres', 'Tags', 'Similar artists', 'Image', 'Peak year', 'First listened',
    'Last listened', 'Spotify ID', 'Bio', 'Plays by year', 'Top songs', 'Plays by month'],
  albums: ['Name', 'Artist', 'Credited as', 'Type', 'Release date', 'Plays', 'Peak year', 'Cover', 'First listened', 'Last listened',
    'Distinct tracks played', 'Status', 'Plays by year', 'Spotify ID', 'Full listens'],
  releases: ['Name', 'Artist', 'Credited as', 'Release date', 'Type', 'Status', 'Found on', 'Spotify ID', 'Cover', 'Tracks',
    'Length', 'Label', 'Track list'],
  blocklist: ['Name', 'Type', 'Artist']
};
const FIELDS_SIG = String(hash(JSON.stringify(FIELDS)));   // a changed field list re-reads the property IDs
let propIds = ls.get('crate.propIds') || {};
if (propIds._sig !== FIELDS_SIG) propIds = { _sig: FIELDS_SIG };
async function fieldQuery(ds) {
  const want = FIELDS[Object.keys(FIELDS).find(k => DS[k] === ds)];
  if (!want || propIds._off) return '';
  if (!propIds[ds]) {
    try {
      const d = await napi(`/data_sources/${ds}`, { read: true });
      propIds[ds] = want.map(n => (d.properties || {})[n] && d.properties[n].id).filter(Boolean);
      ls.set('crate.propIds', propIds);
    } catch { return ''; }
  }
  if (!(propIds[ds] || []).length) return '';
  return '?' + propIds[ds].map(id => 'filter_properties=' + encodeURIComponent(id)).join('&');
}
async function queryAll(ds, filter) {
  const out = []; let cursor = null, guard = 0, qs = await fieldQuery(ds);
  do {
    const body = { page_size: 100, ...(filter ? { filter } : {}), ...(cursor ? { start_cursor: cursor } : {}) };
    let d;
    try { d = await napi(`/data_sources/${ds}/query${qs}`, { method: 'POST', body, read: true }); }
    catch (e) {
      if (!qs || e.status !== 400) throw e;
      propIds = { _off: true, _sig: FIELDS_SIG }; ls.set('crate.propIds', propIds); qs = '';   // Notion refused the field filter
      d = await napi(`/data_sources/${ds}/query`, { method: 'POST', body, read: true });
    }
    out.push(...(d.results || []).filter(p => p.object === 'page' && !p.in_trash));
    cursor = d.has_more ? d.next_cursor : null;
  } while (cursor && ++guard < 200);
  return out;
}
const setGenres = (pageId, genres) => napi(`/pages/${pageId}`, { method: 'PATCH', body: { properties: { Genres: { multi_select: genres.map(name => ({ name })) } } } });
const setStatus = (pageId, status) => napi(`/pages/${pageId}`, { method: 'PATCH', body: { properties: { Status: { select: { name: status } } } } });
const trashPage = pageId => napi(`/pages/${pageId}`, { method: 'PATCH', body: { in_trash: true } });
const addBlock = (type, name, artist) => napi('/pages', {
  method: 'POST', body: {
    parent: { type: 'data_source_id', data_source_id: DS.blocklist },
    properties: { Name: { title: [{ text: { content: name } }] }, Type: { select: { name: type } }, Artist: { rich_text: artist ? [{ text: { content: artist } }] : [] } }
  }
});

/* ---------- storage: library data and artwork in IndexedDB ---------- */
const idb = (() => {
  let dbp = null;
  const open = () => dbp || (dbp = new Promise((res, rej) => {
    const r = indexedDB.open('crate', 2);
    r.onupgradeneeded = () => { const db = r.result; if (!db.objectStoreNames.contains('img')) db.createObjectStore('img'); if (!db.objectStoreNames.contains('kv')) db.createObjectStore('kv'); };
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  }));
  const tx = async (store, mode, fn) => { const db = await open(); return new Promise((res, rej) => { const t = db.transaction(store, mode); const req = fn(t.objectStore(store)); t.oncomplete = () => res(req && req.result); t.onerror = () => rej(t.error); }); };
  return {
    get: (store, k) => tx(store, 'readonly', st => st.get(k)),
    put: (store, k, v) => tx(store, 'readwrite', st => st.put(v, k)),
    keys: store => tx(store, 'readonly', st => st.getAllKeys()),
    clear: store => tx(store, 'readwrite', st => st.clear()),
    del: (store, keys) => tx(store, 'readwrite', st => { for (const k of keys) st.delete(k); })
  };
})();

/* ---------- usage log: what Crate showed, opened and played, and from which shelf ---------- */
/* Events wait on the phone, so nothing is lost offline, and go to the Notion Crate log in batches after
   a refresh and when Crate comes back to the front. The weekly sync turns them into the Crate report.
   An event is { t: unix seconds, e: seen | open | play | listen | mark, s: shelf the tap was on,
   r: the shelf the visit started from, k: kind, i: id(s), a: artist id, q: 1 when quiet for a year,
   x: extra }. Only Notion IDs and Spotify IDs go in, no names. */
const LOG_MAX = 5000, LOG_CHARS = 48000;   // one Notion row per batch: under 25 text pieces of 2000 characters,
                                          // so a database query returns the whole thing
let logQ = ls.get('crate.log') || [], logHist = ls.get('crate.hist') || [];   // logHist: opens and Plays kept on the phone for Crate Stats
const seenToday = ls.get('crate.seen') || { day: '', keys: [] };
function logEvent(e, f = {}) {
  const ev = { t: Math.floor(Date.now() / 1000), e };
  for (const [k, v] of Object.entries(f)) if (v !== '' && v != null && v !== 0) ev[k] = v;
  logQ.push(ev);
  if (logQ.length > LOG_MAX) logQ = logQ.slice(-LOG_MAX);
  ls.set('crate.log', logQ);
  if (e === 'open' || e === 'play') { logHist.push(ev); if (logHist.length > 3000) logHist = logHist.slice(-3000); ls.set('crate.hist', logHist); }
}
/* A shelf's picks, once a day per shelf (they only change daily, or with the heat filter). */
function logShown(src, ids, x = '') {
  ids = ids.filter(Boolean);
  if (!ids.length) return;
  const key = src + ':' + x + ':' + hash(ids.join(','));
  if (seenToday.day !== today()) { seenToday.day = today(); seenToday.keys = []; }
  if (seenToday.keys.includes(key)) return;
  seenToday.keys.push(key); ls.set('crate.seen', seenToday);
  logEvent('seen', { s: src, i: ids.join(','), x });
}
let logSending = false;
async function sendLog() {
  if (logSending || !logQ.length || !token() || !DS.log || !navigator.onLine) return;
  logSending = true;
  for (let batches = 0; logQ.length && batches < 10; batches++) if (!await sendBatch()) break;
  logSending = false;
}
async function sendBatch() {
  let n = 0, size = 2;
  while (n < logQ.length && size + JSON.stringify(logQ[n]).length + 1 < LOG_CHARS) size += JSON.stringify(logQ[n++]).length + 1;
  const batch = logQ.slice(0, Math.max(n, 1)), chars = Array.from(JSON.stringify(batch)), pieces = [];
  for (let i = 0; i < chars.length; i += 2000) pieces.push({ text: { content: chars.slice(i, i + 2000).join('') } });
  try {
    await napi('/pages', { method: 'POST', body: {
      parent: { type: 'data_source_id', data_source_id: DS.log },
      properties: { Name: { title: [{ text: { content: `${today()} · ${plural(batch.length, 'event')}` } }] },
        Sent: { date: { start: new Date().toISOString() } }, Events: { number: batch.length }, Data: { rich_text: pieces } }
    } });
    logQ = logQ.slice(batch.length); ls.set('crate.log', logQ); ls.set('crate.logSent', Date.now());
    return true;
  } catch (e) { console.warn('Usage log not sent', e); return false; }   // it stays queued for next time
}
/* Which shelf a tap was on (the nearest data-src, else the page), and which shelf the visit started
   from: a page opened from Home's Rediscover shelf keeps "rediscover" as its root, however deep you go. */
let tap = null, lastPlay = null;
const recentTap = ms => tap && Date.now() - tap.at < ms ? tap : null;
document.addEventListener('click', e => {
  const el = e.target.closest('a[href^="#/"], [data-act], [data-sheet]');
  if (!el || el.closest('#tabs')) return;
  const box = el.closest('[data-src]'), top = stack[stack.length - 1] || {};
  if (el.dataset.sheet && tap) { tap.at = Date.now(); return; }   // a sheet's action belongs to the tap that opened it
  const src = box ? box.dataset.src : route().view;
  tap = { src, root: top.root || src, at: Date.now() };
}, true);
/* A Play: what, from where, and whether it had been quiet for a year. Then what Spotify plays next is
   credited to it (see heardCheck). */
function played(kind, id, a, item) {
  const t = recentTap(4000) || { src: route().view, root: (stack[stack.length - 1] || {}).root || route().view };
  const x = item || a;
  logEvent('play', { s: t.src, r: t.root, k: kind, i: id, a: a && a.id !== id ? a.id : '', q: x && quiet(x) ? 1 : 0 });
  lastPlay = { at: Date.now(), src: t.src, root: t.root, artist: a ? a.id : '', names: a ? [a._f, ...a._aliases] : [] };
}

/* ---------- state ---------- */
const state = {
  artists: [], albums: [], releases: [], blocks: [], excluded: { artists: [], albums: [] }, syncedAt: 0, fullAt: 0,
  syncing: false, err: null, loaded: false, homeTab: ls.get('crate.homeTab') || 'rediscover', artistTab: 'years',
  heatFilter: ls.get('crate.heatFilter') || [],
  heatLevel: ls.get('crate.heatLevel') || 5, heatKind: ls.get('crate.heatKind') || 'artists',
  newGenre: ls.get('crate.newGenre') || '',
  tab: 'artists', era: 'all', sort: ls.get('crate.sort') || 'plays', q: '', thumbs: ls.get('crate.thumbs') || {}, bioOpen: false
};
let IX = { artistById: new Map(), albumById: new Map(), albums: [], albumsByArtist: new Map(), releasesByArtist: new Map(), genreList: [], unsorted: [], years: [], artists: [] };
const byLevel = (x, y) => (y.h || 0) - (x.h || 0) || (y.plays || 0) - (x.plays || 0) || x.name.localeCompare(y.name);
/* What the Blocklist hides: artists by name or by their main genre, albums by name (and artist). */
function blockSets() {
  const of = t => state.blocks.filter(b => b.type === t);
  return {
    genres: new Set(of('Genre').map(b => fold(b.name))),
    artists: new Set(of('Artist').map(b => fold(b.name))),
    albums: of('Album').map(b => [fold(b.name), fold(b.artist)])
  };
}
function index() {
  const B = blockSets();
  const hiddenArtist = a => B.artists.has(fold(a.name)) || (a.genres[0] && B.genres.has(fold(a.genres[0])));
  const artists = state.artists.filter(a => !hiddenArtist(a));
  const artistById = new Map(artists.map(a => [a.id, a]));
  const nameOf = al => al.credited || (artistById.get(al.artistId) || {}).name || '';
  const hiddenAlbum = al => B.albums.some(([n, by]) => n === fold(al.name) && (!by || by === fold(nameOf(al))));
  const albums = state.albums.filter(al => (!al.artistId || artistById.has(al.artistId)) && !hiddenAlbum(al));
  rankHeat(artists, a => a.plays || 0);
  rankHeat(albums, al => al.plays || 0);
  const albumsByArtist = new Map(), releasesByArtist = new Map(), genres = new Map(), years = new Map();
  for (const al of albums) { if (!albumsByArtist.has(al.artistId)) albumsByArtist.set(al.artistId, []); albumsByArtist.get(al.artistId).push(al); }
  for (const r of state.releases) { if (!releasesByArtist.has(r.artistId)) releasesByArtist.set(r.artistId, []); releasesByArtist.get(r.artistId).push(r); }
  for (const a of artists) {
    a._f = fold(a.name); a._aliases = a.aliases.map(fold);
    for (const g of a.genres) if (!B.genres.has(fold(g))) { if (!genres.has(g)) genres.set(g, []); genres.get(g).push(a); }
    for (const [y, n] of Object.entries(a.years)) { if (!years.has(y)) years.set(y, { artists: [], plays: 0 }); const Y = years.get(y); Y.artists.push(a); Y.plays += n; }
  }
  for (const al of albums) al._f = fold(al.name + ' ' + al.credited);
  for (const list of genres.values()) list.sort(byLevel);
  for (const list of albumsByArtist.values()) list.sort(byLevel);
  const genreList = [...genres.entries()].map(([name, list]) => ({ name, artists: list, plays: list.reduce((s, a) => s + (a.plays || 0), 0) })).sort((a, b) => b.plays - a.plays);
  const yearList = [...years.entries()].filter(([, Y]) => Y.plays >= 20).map(([y, Y]) => ({ year: y, plays: Y.plays, artists: Y.artists.sort((a, b) => (b.years[y] || 0) - (a.years[y] || 0)) })).sort((a, b) => b.year.localeCompare(a.year));
  // Songs, linked to their artist and (when the album is in the library) to the album and its cover.
  const songs = [];
  for (const a of artists) {
    const byTitle = new Map((albumsByArtist.get(a.id) || []).map(al => [fold(al.name), al]));
    for (const sg of a.songs || []) songs.push({ ...sg, artist: a, album: byTitle.get(fold(sg.a)) || null, _f: fold(sg.n) });
  }
  // Five steps for songs, ranked against every song in the library: top 5%, next 10%, 20%, 30%, the rest.
  rankHeat(songs, sg => sg.c);
  const repeats = songs.filter(x => x.r >= REPEAT_MIN).sort((x, y) => y.r - x.r || y.c - x.c);
  const wholeAlbums = albums.filter(al => al.full >= 3).sort((x, y) => y.full - x.full || byLevel(x, y));
  IX = { songs, repeats, wholeAlbums, artists, artistById, albumById: new Map(albums.map(a => [a.id, a])), albums, albumsByArtist, releasesByArtist, genreList, years: yearList, unsorted: artists.filter(a => !a.genres.length).sort(byLevel) };
}
async function persist() {
  const data = { artists: state.artists, albums: state.albums, releases: state.releases, blocks: state.blocks, excluded: state.excluded,
    syncedAt: state.syncedAt, fullAt: state.fullAt };
  try { await idb.put('kv', 'data', data); } catch (e) { console.warn('Could not save the offline copy', e); }
}
async function restore() {
  let d = null;
  try { d = await idb.get('kv', 'data'); } catch { }
  if (!d) { d = ls.get('crate.data'); if (d) { ls.del('crate.data'); } }   // older builds kept it in localStorage
  if (d) {
    // Older saves lack years/bio: fill the shape so every view works until the next sync.
    state.artists = (d.artists || []).map(a => ({ bio: '', songs: [], months: {}, ...a, years: a.years || (a.peak && a.plays ? { [a.peak]: a.plays } : {}) }));
    state.albums = (d.albums || []).map(al => ({ ...al, years: al.years || (al.peak && al.plays ? { [al.peak]: al.plays } : {}) }));
    state.releases = d.releases || []; state.blocks = d.blocks || []; state.excluded = d.excluded || { artists: [], albums: [] };
    state.syncedAt = d.syncedAt || 0; state.fullAt = d.fullAt || 0;
  }
  state.loaded = true;
  index();
  if (d && !d.artists?.[0]?.years) persist();
}

/* Refreshing fetches only rows edited since the last refresh (a few KB on most days instead of ~10 MB).
   Once a week, or from Settings, it reloads everything to catch deleted rows. */
const FULL_EVERY = 7 * DAY;
const editedSince = t => ({ timestamp: 'last_edited_time', last_edited_time: { on_or_after: new Date(t - 10 * 60e3).toISOString() } });
function mergeRows(list, rows, toObj, keep) {
  const byId = new Map(list.map(x => [x.id, x]));
  const gone = [];
  for (const pg of rows) {
    const x = toObj(pg);
    if (keep(x)) byId.set(x.id, x); else if (byId.delete(x.id)) gone.push(x);
  }
  return [[...byId.values()], gone];
}
async function sync({ full = false } = {}) {
  if (!token() || state.syncing) return;
  state.syncing = true; state.err = null; updateChrome();
  const started = Date.now();
  const incremental = !full && state.artists.length && state.fullAt && started - state.fullAt < FULL_EVERY;
  try {
    const soft = p => p.catch(() => null);
    if (incremental) {
      const since = editedSince(state.syncedAt);
      const [a, al, r, b] = await Promise.all([
        queryAll(DS.artists, since), queryAll(DS.albums, since), queryAll(DS.releases, since), soft(queryAll(DS.blocklist))
      ]);
      let gone;
      [state.artists, gone] = mergeRows(state.artists, a, toArtist, x => x.status !== 'Excluded');
      for (const x of gone) if (!state.excluded.artists.some(e => e.id === x.id)) state.excluded.artists.unshift({ id: x.id, name: x.name, image: x.image });
      [state.albums, gone] = mergeRows(state.albums, al, toAlbum, x => x.status !== 'Excluded');
      for (const x of gone) if (!state.excluded.albums.some(e => e.id === x.id)) state.excluded.albums.unshift({ id: x.id, name: x.name, image: x.cover, credited: x.credited });
      [state.releases] = mergeRows(state.releases, r, toRelease, x => x.status !== 'Not interested');
      if (b) state.blocks = b.map(toBlock);
    } else {
      const [a, al, r] = await Promise.all([
        queryAll(DS.artists, { property: 'Status', select: { does_not_equal: 'Excluded' } }),
        queryAll(DS.albums, { property: 'Status', select: { does_not_equal: 'Excluded' } }),
        queryAll(DS.releases, { property: 'Status', select: { does_not_equal: 'Not interested' } })
      ]);
      // The block list and excluded rows are small; if they can't be read, keep the last copy.
      const [b, xa, xal] = await Promise.all([
        soft(queryAll(DS.blocklist)),
        soft(queryAll(DS.artists, { property: 'Status', select: { equals: 'Excluded' } })),
        soft(queryAll(DS.albums, { property: 'Status', select: { equals: 'Excluded' } }))
      ]);
      state.artists = a.map(toArtist); state.albums = al.map(toAlbum); state.releases = r.map(toRelease);
      if (b) state.blocks = b.map(toBlock);
      if (xa && xal) state.excluded = { artists: xa.map(toExcluded), albums: xal.map(toExcluded) };
      state.fullAt = started;
    }
    state.syncedAt = started;
    index(); persist();
    state.syncing = false;
    render(false);
    prefetchImages();
    sendLog();
    if (!statsFresh() || route().view === 'stats') loadStats();
  } catch (e) {
    state.err = e; state.syncing = false;
    render(false);
    if (e.code !== 'offline' || !state.artists.length) toast(errText(e));
  }
}
const stale = () => Date.now() - state.syncedAt > DAY;

/* ---------- artwork: sizes, lazy loading, offline cache ---------- */
/* Spotify and Last.fm serve every size from the same image id, so lists ask for ~300 px and heroes for 640. */
function sized(u, size) {
  if (!u) return '';
  u = String(u);
  if (/ab67616d0000(b273|1e02|4851)/.test(u)) return u.replace(/ab67616d0000(b273|1e02|4851)/, size === 'lg' ? 'ab67616d0000b273' : 'ab67616d00001e02');
  if (/ab6761610000(e5eb|5174|f178)/.test(u)) return u.replace(/ab6761610000(e5eb|5174|f178)/, size === 'lg' ? 'ab6761610000e5eb' : 'ab67616100005174');
  if (/\/i\/u\/\d+x\d+\//.test(u)) return u.replace(/\/i\/u\/\d+x\d+\//, size === 'lg' ? '/i/u/600x600/' : '/i/u/300x300/');
  if (/dzcdn\.net\/images\/.+\/\d+x\d+-/.test(u)) return u.replace(/\/\d+x\d+-/, size === 'lg' ? '/640x640-' : '/320x320-');   // Deezer
  return u;
}
const objUrls = new Map();
let cachedKeys = null;
async function cachedSet() { if (!cachedKeys) { try { cachedKeys = new Set(await idb.keys('img')); } catch { cachedKeys = new Set(); } } return cachedKeys; }
function b64ToBlob(b64, type) { const bin = atob(b64); const u8 = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i); return new Blob([u8], { type: type || 'image/jpeg' }); }
async function fetchBlob(u) {
  if (Http) {
    const r = await Http.request({ url: u, method: 'GET', responseType: 'blob' });
    if (r.status !== 200 || typeof r.data !== 'string') throw new Error('image ' + r.status);
    const h = r.headers || {}; return b64ToBlob(r.data, h['Content-Type'] || h['content-type']);
  }
  const r = await fetch(u, { mode: 'cors' });
  if (!r.ok) throw new Error('image ' + r.status);
  return r.blob();
}
const cacheQ = [], queued = new Set();
let cacheRunning = 0;
function cacheLater(u, front = false) {
  if (!u || queued.has(u)) return;
  queued.add(u); front ? cacheQ.unshift(u) : cacheQ.push(u);
  while (cacheRunning < 3 && cacheQ.length) runCache();
}
async function runCache() {
  cacheRunning++;
  const keys = await cachedSet();
  while (cacheQ.length) {
    const u = cacheQ.shift();
    if (keys.has(u)) continue;
    try { const b = await fetchBlob(u); if (b && b.size > 500) { await idb.put('img', u, b); keys.add(u); } } catch { }
  }
  cacheRunning--;
}
async function blobUrl(u) {
  if (objUrls.has(u)) return objUrls.get(u);
  const keys = await cachedSet();
  if (!keys.has(u)) return null;
  try { const b = await idb.get('img', u); if (!b) return null; const o = URL.createObjectURL(b); objUrls.set(u, o); return o; } catch { return null; }
}
async function loadImg(img) {
  const u = img.dataset.src, alt = img.dataset.alt || '';
  if (!u) return;
  img.removeAttribute('data-src');
  img.onload = () => { img.classList.add('on'); img.parentElement && img.parentElement.classList.add('has-img'); if (img.dataset.glow) glow(img); };
  img.onerror = () => { if (alt && img.dataset.tried !== '1') { img.dataset.tried = '1'; blobUrl(alt).then(o => { img.src = o || alt; }); } else img.remove(); };
  let src = await blobUrl(u);
  if (!src && alt && !navigator.onLine) src = await blobUrl(alt);
  if (!src) { src = u; cacheLater(u, true); }
  img.src = src;
}
/* Only load artwork near the screen: a genre can hold hundreds of artists. */
const io = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
  for (const en of entries) if (en.isIntersecting) { io.unobserve(en.target); loadImg(en.target); }
}, { rootMargin: '500px 200px' }) : null;
function hydrate(root = document) { root.querySelectorAll('img[data-src]').forEach(img => io ? io.observe(img) : loadImg(img)); }
/* Tint the album page with the cover's average colour (only possible for cached, same-origin images). */
function glow(img) {
  try {
    const c = document.createElement('canvas'); c.width = c.height = 8;
    const x = c.getContext('2d'); x.drawImage(img, 0, 0, 8, 8);
    const d = x.getImageData(0, 0, 8, 8).data; let r = 0, g = 0, b = 0;
    for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; b += d[i + 2]; }
    const n = d.length / 4, head = img.closest('.album-head');
    if (head) head.style.setProperty('--glow', `rgb(${r / n | 0} ${g / n | 0} ${b / n | 0})`);
  } catch { }
}
/* After a sync, save list-size artwork in the background so the library works offline: on Wi-Fi only,
   unless mobile data is allowed in Settings (a full library is ~100 MB). Artwork you scroll past is
   saved either way. Pictures no longer used by the library are cleared out. */
async function onWifi() {
  if (!Net) return true;
  try { const st = await Net.getStatus(); return st.connectionType === 'wifi' || st.connectionType === 'ethernet'; } catch { return true; }
}
async function prefetchImages() {
  const urls = [];
  for (const a of [...IX.artists].sort((x, y) => (y.plays || 0) - (x.plays || 0))) if (a.image) urls.push(sized(a.image, 'sm'));
  for (const al of [...IX.albums].sort((x, y) => (y.plays || 0) - (x.plays || 0))) if (al.cover) urls.push(sized(al.cover, 'sm'));
  for (const r of newReleases()) if (r.cover || state.thumbs[r.id]) urls.push(sized(r.cover, 'sm') || state.thumbs[r.id]);
  fetchThumbs();
  pruneImages(new Set(urls));
  if (ls.get('crate.cellArt') || await onWifi()) urls.forEach(u => cacheLater(u));
}
async function pruneImages(wanted) {
  const lg = new Set([...IX.artists.map(a => sized(a.image, 'lg')), ...IX.albums.map(al => sized(al.cover, 'lg'))]);
  const keys = await cachedSet();
  const old = [...keys].filter(k => !wanted.has(k) && !lg.has(k));
  if (!old.length) return;
  try { await idb.del('img', old); old.forEach(k => { keys.delete(k); const o = objUrls.get(k); if (o) { URL.revokeObjectURL(o); objUrls.delete(k); } }); } catch { }
}
/* New releases have no cover in Notion; Spotify's public oEmbed endpoint gives one without a login. */
async function fetchThumbs() {
  let changed = false;
  for (const r of newReleases()) {   // only the ones on screen
    if (r.cover || state.thumbs[r.id] || !r.spotify || !navigator.onLine) continue;
    try {
      const res = await fetch('https://open.spotify.com/oembed?url=' + encodeURIComponent('https://open.spotify.com/album/' + r.spotify));
      const d = await res.json();
      if (d && d.thumbnail_url) { state.thumbs[r.id] = d.thumbnail_url; cacheLater(d.thumbnail_url); changed = true; }
    } catch { }
  }
  if (changed) { ls.set('crate.thumbs', state.thumbs); if (['new', 'artist', 'home'].includes(route().view)) render(false); }
}
function art(url, name, { circle = false, size = 'sm', h = 0, glowing = false } = {}) {
  const u = sized(url, size), alt = size === 'lg' ? sized(url, 'sm') : '';
  return `<div class="art${circle ? ' circle' : ''}" style="--tint:${tint(name)}">${u ? `<img data-src="${esc(u)}"${alt && alt !== u ? ` data-alt="${esc(alt)}"` : ''}${glowing ? ' data-glow="1"' : ''} alt="" decoding="async">` : ''}<span class="ini" aria-hidden="true">${esc(initials(name))}</span>${heat(h)}</div>`;
}

/* ---------- Spotify: playing and links ---------- */
/* Play buttons ask the Spotify app to play (through Crate's App Remote plugin) while you stay in Crate.
   Without the plugin, a Spotify ID, or the Spotify app, they open Spotify instead. */
function openSpotify(kind, id, query, { app = false } = {}) {
  if (!/^[A-Za-z0-9]{10,40}$/.test(id || '')) id = '';   // never put anything else into a link
  const uri = id ? `spotify:${kind}:${id}` : `spotify:search:${encodeURIComponent(query || '')}`;
  const web = id ? `https://open.spotify.com/${kind}/${id}` : `https://open.spotify.com/search/${encodeURIComponent(query || '')}`;
  if (srOn() && id && !app) { playUri(uri).then(ok => { if (!ok) openLink(uri, web); }); return; }
  openLink(uri, web);
}
function openLink(uri, web) {
  if (!isNative) { window.open(web, '_blank'); return; }
  // Capacitor hands non-app links to Android: spotify: opens the Spotify app; if nothing takes it,
  // the https link opens Spotify (through its app link) or the browser.
  let left = false;
  const onHide = () => { if (document.hidden) left = true; };
  document.addEventListener('visibilitychange', onHide);
  location.href = uri;
  setTimeout(() => { document.removeEventListener('visibilitychange', onHide); if (!left && !document.hidden) location.href = web; }, 1200);
}
const spotifyId = id => /^[A-Za-z0-9]{10,40}$/.test(id || '');
/* No Spotify ID for the artist or album yet (they're matched in the background): play your favourite
   song of theirs instead, when the export gave its track ID, rather than leaving Crate for a search. */
const favTrack = test => (IX.songs || []).find(sg => test(sg) && spotifyId(sg.t));
function openSpotifyArtist(a, o = {}) {
  if (!a) return;
  const sg = srOn() && !o.app && !spotifyId(a.spotify) && favTrack(x => x.artist === a);
  sg ? openSpotify('track', sg.t, sg.n) : openSpotify('artist', a.spotify, a.name, o);
}
function openSpotifyAlbum(al, o = {}) {
  if (!al) return;
  const sg = srOn() && !o.app && !spotifyId(al.spotify) && favTrack(x => x.album === al);
  sg ? openSpotify('track', sg.t, sg.n) : openSpotify('album', al.spotify, `${al.name} ${artistName(al)}`, o);
}

/* The remote: connects on the first Play (Spotify asks once to approve Crate), then shows a mini player
   for whatever Spotify is playing, also when it was started elsewhere. */
const SR = plugin('SpotifyRemote');
const srId = () => String(cfg.spotifyId || '').trim();
const srOn = () => !!(SR && srId());   // Play buttons control Spotify once you've added your client ID
const playLabel = () => srOn() ? 'Play' : 'Open in Spotify';
const REDIRECT = 'crate-music://spotify';   // add this to your Spotify app's Redirect URIs
const SR_HELP = { NotLoggedInException: 'Log in to the Spotify app first', UserNotAuthorizedException: "Spotify hasn't approved Crate yet",
  AuthenticationFailedException: "Spotify hasn't approved Crate yet", OfflineModeException: 'Spotify is in offline mode', NoClientId: '' };
let srReady = null, srError = '', now = null, nowAt = 0;
function srConnect() {
  if (!srReady) srReady = SR.connect({ clientId: srId(), redirectUri: REDIRECT })
    .then(() => { srError = ''; ls.set('crate.remote', true); })
    .catch(e => { srReady = null; srError = [e && e.code, e && e.message].filter(Boolean).join(': ') || 'unknown error'; throw e; });
  return srReady;
}
async function playUri(uri) {
  for (let attempt = 0; attempt < 2; attempt++) {
    try { await srConnect(); await SR.play({ uri }); return true; }
    catch (e) {
      if (e && e.code === 'NotConnected' && !attempt) { srReady = null; continue; }
      console.warn('Spotify remote:', e && e.code, e && e.message);
      if (e && e.code === 'BrowserLogin') { toast('Approve Crate in the browser, then tap Play again'); return true; }
      const why = e && e.code in SR_HELP ? SR_HELP[e.code] : `Couldn't play in Crate (${(e && (e.code || e.message)) || 'error'})`;
      if (why) toast(why + ': opening Spotify instead');
      return false;
    }
  }
  return false;
}
const mini = document.createElement('div');
mini.className = 'mini'; mini.hidden = true; mini.dataset.src = 'player'; mini.setAttribute('role', 'region'); mini.setAttribute('aria-label', 'Now playing');
document.body.insertBefore(mini, overlay);
const artistNamed = n => { const f = fold(n); return IX.artists.find(a => a._f === f || a._aliases.includes(f)); };
const covers = new Map();   // Spotify's cover for each album (key -> data URL), the last few
function onPlayer(p) {
  const prevUri = now && now.uri;
  now = p && p.active ? { ...p, image: covers.get(p.imageKey) || '' } : null; nowAt = Date.now();
  document.body.classList.toggle('has-mini', !!now);
  mini.hidden = !now;
  if (!now) { closeNP(); return; }
  mini.innerHTML = `<button class="mini-main" data-act="mini-open" aria-label="Now playing: ${esc(now.name)} by ${esc(now.artist)}. Opens the player">${now.image ? `<img src="${esc(now.image)}" alt="">` : `<span class="mini-art" style="--tint:${tint(now.album || now.name)}"></span>`}<span class="mini-txt"><b>${esc(now.name)}</b><span>${esc(now.artist)}</span></span></button>
    <button class="mini-btn" data-act="np-toggle" aria-label="${now.paused ? 'Play' : 'Pause'}">${now.paused ? I.play : I.pause}</button><button class="mini-btn" data-act="np-next" aria-label="Next track">${I.next}</button><i class="mini-prog"></i>`;
  if (np.classList.contains('open')) prevUri === now.uri ? updateNP() : renderNP();
  tickPlayer();
}
/* A track by the artist Crate last started, playing for 30 seconds within 3 hours of that Play, counts
   as heard from that shelf (once per track). Last.fm confirms the rest in the weekly report. */
let heard = { uri: '', done: false };
function heardCheck() {
  if (!now || !now.uri) return;
  if (heard.uri !== now.uri) heard = { uri: now.uri, done: false };
  if (heard.done || posNow() < 30000) return;
  heard.done = true;
  const lp = lastPlay;
  if (!lp || Date.now() - lp.at > 3 * 3600e3 || !lp.names.includes(fold(now.artist))) return;
  logEvent('listen', { s: lp.src, r: lp.root, k: 'track', i: String(now.uri).split(':').pop(), a: lp.artist });
}
function onCover(c) {
  covers.set(c.key, c.data);
  while (covers.size > 6) covers.delete(covers.keys().next().value);
  if (now && now.imageKey === c.key) onPlayer({ ...now, active: true });
}
const posNow = () => !now ? 0 : Math.min(now.duration || 0, now.position + (now.paused ? 0 : Date.now() - nowAt));
const clock = ms => { const s = Math.max(0, Math.round(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
let seeking = false;
function tickPlayer() {
  if (!now) return;
  heardCheck();
  const pos = posNow(), bar = mini.querySelector('.mini-prog');
  if (bar && now.duration) bar.style.transform = `scaleX(${pos / now.duration})`;
  if (np.classList.contains('open') && !seeking) {
    const r = np.querySelector('.np-seek'); if (r) { r.value = pos; r.style.setProperty('--p', now.duration ? pos / now.duration : 0); }
    const t = np.querySelector('.np-times'); if (t) t.innerHTML = `<span>${clock(pos)}</span><span>-${clock((now.duration || 0) - pos)}</span>`;
  }
}
setInterval(() => { if (now && !now.paused && !document.hidden) tickPlayer(); }, 1000);

/* The full-screen player: tap the mini player. Big cover, seek bar, shuffle, previous / play / next,
   repeat, Liked Songs, and what Crate knows about the song. Swipe down, the chevron or Back closes it. */
const np = document.createElement('div');
np.className = 'np'; np.dataset.src = 'player'; np.setAttribute('role', 'dialog'); np.setAttribute('aria-modal', 'true'); np.setAttribute('aria-label', 'Now playing');
document.body.appendChild(np);
const I2 = {
  down: svg('<path d="M6 9.5l6 6 6-6"/>', 'stroke-width="2.6"'),
  prev: svg('<path d="M20.5 5.5v13l-9-6.5z" fill="currentColor" stroke="none"/><path d="M12.5 5.5v13l-9-6.5z" fill="currentColor" stroke="none"/>'),
  shuffle: svg('<path d="M3 7h3.5c2 0 3.2.9 4.3 2.6l2.4 3.8c1.1 1.7 2.3 2.6 4.3 2.6H21M3 17h3.5c1.5 0 2.5-.5 3.4-1.5M14.1 8.5c.9-1 1.9-1.5 3.4-1.5H21M18 4l3 3-3 3M18 14l3 3-3 3"/>', 'stroke-width="2"'),
  repeat: svg('<path d="M4 11V9a3 3 0 0 1 3-3h13M17 3l3 3-3 3M20 13v2a3 3 0 0 1-3 3H4M7 21l-3-3 3-3"/>', 'stroke-width="2"'),
  heart: svg('<path d="M12 20.5s-8-4.7-8-10.6A4.4 4.4 0 0 1 12 7.4a4.4 4.4 0 0 1 8 2.5c0 5.9-8 10.6-8 10.6z"/>', 'stroke-width="2"'),
  heartOn: svg('<path d="M12 20.5s-8-4.7-8-10.6A4.4 4.4 0 0 1 12 7.4a4.4 4.4 0 0 1 8 2.5c0 5.9-8 10.6-8 10.6z" fill="currentColor"/>', 'stroke-width="2"')
};
const songNamed = (a, name) => a && (IX.songs || []).find(sg => sg.artist === a && trackKey(sg.n) === trackKey(name));
const albumNamed = (a, name) => { const k = trackKey(name); return a && (IX.albumsByArtist.get(a.id) || []).find(al => trackKey(al.name) === k); };
function renderNP() {
  if (!now) return;
  const a = artistNamed(now.artist), sg = songNamed(a, now.name);
  np.style.setProperty('--np-bg', now.image ? `url("${now.image}")` : 'none');
  np.style.setProperty('--tint', tint(now.album || now.name));
  np.innerHTML = `<div class="np-bg" aria-hidden="true"></div>
    <div class="np-top"><button class="np-close" data-act="np-close" aria-label="Close the player">${I2.down}</button><div class="np-from">${now.context ? `<span>Playing from</span><b>${esc(now.context)}</b>` : ''}</div><span class="np-spacer"></span></div>
    <div class="np-art${now.paused ? ' paused' : ''}">${now.image ? `<img src="${esc(now.image)}" alt="">` : `<span class="ini">${esc(initials(now.album || now.name))}</span>`}</div>
    <div class="np-head"><div class="np-titles"><b>${esc(now.name)}</b><button class="np-artist" data-act="np-artist">${esc(now.artist)}</button></div>
      <button class="np-like" data-act="np-like" aria-label="Liked Songs" ${now.canSave === false ? 'disabled' : ''}></button></div>
    ${sg ? `<div class="np-heat">${heat(sg.h)}<span>${sg.h ? HEAT[sg.h][0] + ' · ' : ''}you've played it ${plural(sg.c, 'time')}</span></div>` : ''}
    <input class="np-seek" type="range" min="0" max="${now.duration || 1}" step="1000" value="${posNow()}" aria-label="Position" ${now.canSeek === false ? 'disabled' : ''}>
    <div class="np-times"></div>
    <div class="np-ctrl">
      <button class="np-sm" data-act="np-shuffle" aria-label="Shuffle">${I2.shuffle}</button>
      <button class="np-md" data-act="np-prev" aria-label="Previous">${I2.prev}</button>
      <button class="np-play" data-act="np-toggle"></button>
      <button class="np-md" data-act="np-next" aria-label="Next">${I.next}</button>
      <button class="np-sm" data-act="np-repeat" aria-label="Repeat">${I2.repeat}<i></i></button></div>
    <div class="np-links">${albumNamed(a, now.album) ? `<button data-act="np-album">${I.disc}<span>${esc(now.album)}</span></button>` : ''}<button data-act="np-spotify">${I.music}<span>Open in Spotify</span></button></div>`;
  const r = np.querySelector('.np-seek');
  r.addEventListener('input', () => { seeking = true; r.style.setProperty('--p', r.value / (now.duration || 1)); np.querySelector('.np-times').innerHTML = `<span>${clock(r.value)}</span><span>-${clock((now.duration || 0) - r.value)}</span>`; });
  r.addEventListener('change', () => { const ms = Number(r.value); seeking = false; now = { ...now, position: ms }; nowAt = Date.now(); SR.seek({ ms }).catch(() => { }); tickPlayer(); });
  updateNP();
}
function updateNP() {
  if (!now) return;
  const q = s => np.querySelector(s);
  q('.np-art').classList.toggle('paused', !!now.paused);
  q('.np-play').innerHTML = now.paused ? I.play : I.pause; q('.np-play').setAttribute('aria-label', now.paused ? 'Play' : 'Pause');
  q('[data-act="np-prev"]').disabled = now.canPrev === false; q('[data-act="np-next"]').disabled = now.canNext === false;
  const sh = q('.np-sm[data-act="np-shuffle"]'); sh.setAttribute('aria-pressed', !!now.shuffle); sh.disabled = now.canShuffle === false;
  const rp = q('.np-sm[data-act="np-repeat"]'); rp.setAttribute('aria-pressed', !!now.repeat); rp.classList.toggle('one', now.repeat === 1); rp.disabled = now.canRepeat === false;
  rp.setAttribute('aria-label', ['Repeat: off', 'Repeat: this song', 'Repeat: all'][now.repeat || 0]);
  const like = q('.np-like'); like.innerHTML = now.saved ? I2.heartOn : I2.heart; like.setAttribute('aria-pressed', !!now.saved);
  tickPlayer();
}
function openNP() {
  if (!now) return;
  renderNP(); np.classList.add('open'); document.body.classList.add('np-open');
}
function closeNP() { np.classList.remove('open'); document.body.classList.remove('np-open'); np.style.transform = ''; }
(() => {   // swipe down to close
  let y0 = null, dy = 0;
  np.addEventListener('pointerdown', e => { if (e.target.closest('.np-seek')) return; y0 = e.clientY; dy = 0; });
  np.addEventListener('pointermove', e => { if (y0 === null) return; dy = Math.max(0, e.clientY - y0); if (dy > 8) { np.style.transition = 'none'; np.style.transform = `translateY(${dy}px)`; } });
  const end = () => { if (y0 === null) return; y0 = null; np.style.transition = ''; if (dy > 120) closeNP(); else np.style.transform = ''; };
  np.addEventListener('pointerup', end); np.addEventListener('pointercancel', end);
})();
function playerAction(act) {
  if (!now) return;
  const quiet = () => { };
  switch (act) {
    case 'mini-open': openNP(); break;
    case 'np-close': closeNP(); break;
    case 'np-toggle': { const paused = now.paused; now = { ...now, paused: !paused, position: posNow() }; nowAt = Date.now(); onPlayer({ ...now, active: true }); (paused ? SR.resume() : SR.pause()).catch(quiet); break; }
    case 'np-next': SR.next().catch(quiet); break;
    case 'np-prev': SR.previous().catch(quiet); break;
    case 'np-shuffle': now = { ...now, shuffle: !now.shuffle }; updateNP(); SR.shuffle({ on: now.shuffle }).catch(quiet); break;
    case 'np-repeat': now = { ...now, repeat: ((now.repeat || 0) + 1) % 3 }; updateNP(); SR.repeat({ mode: now.repeat }).catch(quiet); break;
    case 'np-like': { const on = !now.saved; now = { ...now, saved: on }; updateNP(); SR.save({ uri: now.uri, on }).then(() => toast(on ? 'Added to Liked Songs' : 'Removed from Liked Songs'), () => { now = { ...now, saved: !on }; updateNP(); toast("Spotify didn't save that"); }); break; }
    case 'np-artist': { const a = artistNamed(now.artist); closeNP(); if (a) location.hash = '#/artist/' + a.id; else openLink(now.artistUri, 'https://open.spotify.com/artist/' + String(now.artistUri).split(':').pop()); break; }
    case 'np-album': { const al = albumNamed(artistNamed(now.artist), now.album); closeNP(); if (al) location.hash = '#/album/' + al.id; break; }
    case 'np-spotify': openLink(now.uri, 'https://open.spotify.com/track/' + String(now.uri).split(':').pop()); break;
  }
}
if (SR) {
  SR.addListener('playerState', onPlayer);
  SR.addListener('cover', onCover);
  SR.addListener('connection', c => {
    if (c.connected) {   // also after approving Crate in the browser and coming back
      const fresh = !srReady || srError;
      srReady = Promise.resolve(); srError = ''; ls.set('crate.remote', true);
      if (fresh) { toast('Connected to Spotify'); if (route().view === 'settings') render(false); }
    } else {
      srReady = null; onPlayer(null);
      if (c.error) { srError = c.error; if (route().view === 'settings') render(false); }
    }
  });
  if (srId() && ls.get('crate.remote')) setTimeout(() => srConnect().catch(() => { }), 1500);   // show what's playing
}

/* ---------- routing & navigation stack ---------- */
const route = () => { const h = location.hash.replace(/^#\/?/, ''); const [view, ...rest] = h.split('/'); return { view: view || 'home', arg: rest.length ? decodeURIComponent(rest.join('/')) : null, hash: '#/' + h }; };
const stack = [];   // [{hash, title, y}] mirrors the in-app history since the last tab switch
const TABS = [['home', 'Home', I.home], ['heat', 'Heat', I.flame], ['new', 'New', I.sparkle], ['search', 'Search', I.searchFill]];
let replacing = false;   // a page swapped in place (e.g. a year's genre chips): not a new step back
function replaceRoute(h) { replacing = true; location.replace(h); }
function onHash() {
  const h = route().hash, top = stack[stack.length - 1];
  let restoreY = 0;
  if (replacing && top) { replacing = false; top.hash = h; closeSheet(); render(false); return; }
  replacing = false;
  if (stack.length > 1 && stack[stack.length - 2].hash === h) {   // back: restore where you were
    const leaving = stack.pop(); restoreY = stack[stack.length - 1].y || 0;
    Object.assign(state, stack[stack.length - 1].ui || {});
    if (leaving) state.bioOpen = false;
  } else if (!top || top.hash !== h) {                             // forward: remember this page's toggles
    if (top) { top.y = window.scrollY; top.ui = { tab: state.tab, era: state.era }; }
    const t = recentTap(2500), r = route();
    stack.push({ hash: h, title: '', y: 0, root: t ? t.root : (top && top.root) || '' });
    if (t && r.view !== 'settings' && r.view !== 'setup' && r.view !== 'blocklist') logEvent('open', { s: t.src, r: t.root, k: r.view, i: r.arg || '' });
    state.tab = (route().view === 'library' && route().arg === 'albums') || route().view === 'galbums' ? 'albums' : 'artists'; state.era = 'all'; state.bioOpen = false; state.artistTab = 'years';
  }
  closeSheet();
  render(true, restoreY);
}
window.addEventListener('hashchange', onHash);
/* Tapping a tab starts that tab afresh (like Apple Music); tapping the current tab scrolls to the top. */
function goTab(view) {
  const h = view === 'home' ? '#/' : '#/' + view;
  stack.length = 0;
  stack.push({ hash: h, title: '', y: 0 });
  if (route().hash === h) { window.scrollTo({ top: 0, behavior: 'smooth' }); render(false); }
  else location.replace(h);
}
function goBack() { if (stack.length > 1) history.back(); else location.replace('#/'); }

/* ---------- rendering ---------- */
let currentView = null;
function render(resetScroll = true, restoreY = 0) {
  if (!state.loaded) return;
  if (!connected() && route().view !== 'setup') { location.replace('#/setup'); return; }
  const r = route();
  // Re-rendering search while typing would drop the keyboard: just refresh the results.
  if (!resetScroll && r.view === 'search' && document.activeElement && document.activeElement.id === 'q') { updateResults(); renderTabs('search'); return; }
  let v;
  try {
    switch (r.view) {
      case 'new': v = newView(); break;
      case 'heat': v = heatView(); break;
      case 'month': v = monthView(r.arg); break;
      case 'search': v = searchView(); break;
      case 'genre': v = genreView(r.arg); break;
      case 'gartists': case 'galbums': v = genreAllView(r.arg); break;
      case 'library': v = libraryView(); break;
      case 'repeat': v = repeatView(); break;
      case 'whole': v = wholeView(); break;
      case 'songs': v = songsView(r.arg); break;
      case 'year': v = yearView(r.arg); break;
      case 'artist': v = artistView(r.arg); break;
      case 'album': v = albumView(r.arg); break;
      case 'release': v = releaseView(r.arg); break;
      case 'settings': v = settingsView(); break;
      case 'stats': v = statsView(); break;
      case 'blocklist': v = blocklistView(); break;
      case 'setup': v = setupView(); break;
      default: v = homeView();
    }
  } catch (err) { console.error(err); v = { title: 'Crate', tab: 'home', html: `<div class="empty"><h3>Something went wrong</h3><p>${esc(err && err.message || err)}</p></div>` }; }
  currentView = v;
  const top = stack[stack.length - 1]; if (top) top.title = v.title;
  const prev = stack.length > 1 ? stack[stack.length - 2].title : '';
  const backBtn = v.root ? '' : `<button class="back" data-act="back" aria-label="Back">${I.back}<span>${esc(prev || 'Back')}</span></button>`;
  nav.className = 'nav' + (v.hero ? ' over-hero' : '');
  nav.innerHTML = `<div class="left">${backBtn}</div><div class="ntitle">${esc(v.title)}</div><div class="right">${v.right || ''}</div>`;
  if (io) io.disconnect();
  app.className = 'page' + (v.hero ? ' hero-page' : '');
  app.innerHTML = v.html;
  document.body.classList.toggle('no-tabs', !!v.noTabs);
  renderTabs(v.tab);
  hydrate(app);
  if (v.after) v.after();
  if (resetScroll) window.scrollTo(0, restoreY || 0);
  onScroll();
}
/* New Releases: albums and EPs (not singles) out in the last 30 days from Inferno, Blaze and Flame
   artists. A rolling month: older ones drop off by themselves. Announced albums wait in Coming Soon.
   Releases by artists you've blocked stay hidden (blocked artists aren't in the index). */
const RELEASE_TYPES = new Set(['Album', 'EP', 'Live']);
const monthAgo = () => new Date(Date.now() - 30 * DAY).toISOString().slice(0, 10);
const relArtist = r => IX.artistById.get(r.artistId);
const relDay = r => r.release || r.found || '';
const relWatched = r => { const a = relArtist(r); return r.status === 'New' && a && a.h >= 3 && RELEASE_TYPES.has(r.type || 'Album'); };
const newReleases = () => { const since = monthAgo(), t = today(); return state.releases.filter(r => relWatched(r) && relDay(r) >= since && relDay(r) <= t); };
const comingSoon = () => { const t = today(); return state.releases.filter(r => relWatched(r) && relDay(r) > t).sort((a, b) => relDay(a).localeCompare(relDay(b))); };
function renderTabs(active) {
  const n = newReleases().length;
  tabs.innerHTML = TABS.map(([k, label, icon]) => `<a href="${k === 'home' ? '#/' : '#/' + k}" data-tab="${k}" ${active === k ? 'aria-current="page"' : ''}>${icon}<span>${label}</span>${k === 'new' && n ? `<span class="badge" aria-label="${n} new">${n > 99 ? '99+' : n}</span>` : ''}</a>`).join('');
}
function updateChrome() { const b = $('#sync-btn'); if (b) b.classList.toggle('spin', state.syncing); }
function onScroll() {
  const v = currentView; if (!v) return;
  const threshold = v.hero ? Math.max(120, (($('.hero') || {}).offsetHeight || 300) - 90) : 36;
  nav.classList.toggle('solid', window.scrollY > threshold);
}
window.addEventListener('scroll', onScroll, { passive: true });

const shelfHead = (title, sub, href) => `<div class="shead"><div><h2>${href ? `<a href="${href}">${esc(title)}</a>${I.chev}` : esc(title)}</h2>${sub ? `<p>${esc(sub)}</p>` : ''}</div>${href ? `<a class="see" href="${href}">See All</a>` : ''}</div>`;
const artistCard = (a, sub = '') => `<a class="card center" href="#/artist/${a.id}">${art(a.image, a.name, { circle: true, h: a.h })}<div class="t1">${esc(a.name)}</div>${sub ? `<div class="t2">${esc(sub)}</div>` : ''}</a>`;
const albumCard = (al, sub) => `<a class="card" href="#/album/${al.id}">${art(al.cover, al.name, { h: al.h })}<div class="t1">${esc(al.name)}</div><div class="t2">${esc(sub ?? artistName(al)) || '&nbsp;'}</div></a>`;
const artistName = al => { const a = IX.artistById.get(al.artistId); return al.credited || (a ? a.name : ''); };
const moreBtn = act => `<button class="nbtn" data-act="${act}" aria-label="More">${I.more}</button>`;
function statusBanner() {
  if (state.err && state.err.code === 'offline') return `<div class="banner">${I.music}<span>Offline. Showing your saved library${state.syncedAt ? ' from ' + new Date(state.syncedAt).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : ''}.</span></div>`;
  if (state.err) return `<div class="banner"><span>${esc(errText(state.err))}</span><button class="btn-link" data-act="sync">Try again</button></div>`;
  return '';
}
function collageTile(name, sub, artists, href, cls = '') {
  const pick = [...artists.filter(a => a.image), ...artists.filter(a => !a.image)].slice(0, 4);
  while (pick.length < 4) pick.push({ name: name + pick.length, image: '' });
  return `<a class="tile ${cls}" href="${href}"><div class="collage">${pick.map(a => art(a.image, a.name)).join('')}</div><div class="label"><b>${esc(name)}</b><span>${esc(sub)}</span></div></a>`;
}

/* ---------- Home ---------- */
/* Two tabs keep Home calm: Rediscover (picks from your whole history, new every day) and Browse
   (library, years, genres). Every Rediscover shelf draws a fresh random selection each day from
   everything that qualifies, so it never goes stale but stays put while you look around. */
const REPEAT_MIN = 15;
const today = () => new Date().toLocaleDateString('en-CA');
const daily = (list, salt, n) => shuffle(list, hash(today() + salt)).slice(0, n);
const quietSince = () => new Date(Date.now() - 365 * DAY).toISOString().slice(0, 10);
const quiet = x => x.last && x.last.slice(0, 10) <= quietSince();
const monthName = ym => { const [y, m] = String(ym || '').split('-').map(Number); return y && m ? new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: 'short', year: 'numeric' }) : ''; };
/* A song row opens the song in Spotify (the exact track when the export gave its ID). */
function songRow(sg, { rank = 0, withArtist = false, repeatSub = false } = {}) {
  const cover = (sg.album && sg.album.cover) || sg.artist.image;
  // A song you once had on repeat says so (in red) instead of its album: the cover already shows that.
  const onRepeat = !repeatSub && sg.r >= REPEAT_MIN;
  const sub = (repeatSub ? [sg.artist.name, monthName(sg.rm)] : onRepeat ? [withArtist ? sg.artist.name : ''] : [withArtist ? sg.artist.name : '', sg.a, withArtist ? '' : sg.y])
    .filter(Boolean).map(esc);
  if (onRepeat) sub.push(`<span class="rep">On repeat in ${esc(monthName(sg.rm))}</span>`);
  return `<button class="row song" data-act="song" data-t="${esc(sg.t)}" data-a="${sg.artist.id}" data-q="${esc(sg.n + ' ' + sg.artist.name)}" aria-label="${esc(sg.n)} by ${esc(sg.artist.name)}. ${sg.h ? HEAT[sg.h][0] + ', ' + HEAT[sg.h][1] : ''}. ${srOn() ? 'Plays' : 'Opens'} in Spotify">${rank ? `<span class="rank">${rank}</span>` : ''}${art(cover, sg.a || sg.n)}<div class="txt"><div class="t1">${esc(sg.n)}</div><div class="t2">${sub.join(' · ')}</div></div><span class="end">${heat(sg.h)}</span></button>`;
}
const songList = (list, opts = {}) => `<ul class="list">${list.map((sg, i) => `<li>${songRow(sg, { ...opts, rank: opts.ranked ? i + 1 : 0 })}</li>`).join('')}</ul>`;
const songGrid = (list, opts = { repeatSub: true }) => `<div class="songgrid">${list.map(sg => songRow(sg, opts)).join('')}</div>`;
/* Today's Pick: one artist or album from your past, a reason, and a play button. Hotter ones come up
   more often (an Inferno is five times as likely as a Spark), and it changes once a day. */
function todaysPick(keep) {
  const pool = [];
  for (const a of IX.artists) if (keep(a) && quiet(a)) for (let i = 0; i < a.h; i++) pool.push(a);
  for (const al of IX.albums) if (keep(al) && quiet(al) && (al.full >= 3 || al.h >= 4)) for (let i = 0; i < al.h; i++) pool.push(al);
  return daily(pool, 'pick', 1)[0] || null;
}
function pickCard(x) {
  const isAlbum = 'artistId' in x;
  const href = isAlbum ? `#/album/${x.id}` : `#/artist/${x.id}`;
  const why = isAlbum
    ? [artistName(x), x.full >= 3 ? `played start to finish ${nf(x.full)} times` : HEAT[x.h][0], 'last played ' + monthYear(x.last)]
    : [HEAT[x.h][0], x.peak ? 'big in ' + x.peak : '', 'last played ' + monthYear(x.last)];
  return `<div class="pick" data-src="pick"><a class="pick-link" href="${href}" aria-label="Today's pick: ${esc(x.name)}">${art(isAlbum ? x.cover : x.image, x.name, { size: 'lg', h: x.h })}
      <div class="pick-body"><span class="eyebrow">Today's Pick</span><h2>${esc(x.name)}</h2><p>${esc(why.filter(Boolean).join(' · '))}</p></div></a>
    <button class="pick-play" data-act="pick-play" data-id="${x.id}" data-album="${isAlbum ? 1 : ''}" aria-label="${srOn() ? 'Play' : 'Open'} ${esc(x.name)}${srOn() ? '' : ' in Spotify'}">${I.play}</button></div>`;
}
/* This month in earlier years: who you played most, one card per year. */
function monthsAgo() {
  const now = new Date(), mm = String(now.getMonth() + 1).padStart(2, '0'), cy = now.getFullYear(), byYear = {};
  for (const a of hf(IX.artists)) for (const [ym, n] of Object.entries(a.months || {}))
    if (ym.endsWith('-' + mm) && Number(ym.slice(0, 4)) < cy && n >= 3) (byYear[ym.slice(0, 4)] = byYear[ym.slice(0, 4)] || []).push([a, n]);
  return Object.entries(byYear).map(([y, list]) => ({ y, ym: `${y}-${mm}`, artists: list.sort((p, q) => q[1] - p[1]).map(x => x[0]) }))
    .filter(Y => Y.artists.length >= 2).sort((a, b) => b.y - a.y);
}
const monthLabel = (ym, style = 'short') => { const [y, m] = ym.split('-').map(Number); return new Date(y, m - 1, 1).toLocaleDateString(undefined, { month: style, year: 'numeric' }); };
function rediscoverTab() {
  // Without a filter Rediscover draws from Flame and up; with one, from exactly the levels picked.
  const keep = x => heatOn() ? heatOk(x) : x.h >= 3;
  const pick = todaysPick(keep);
  const notPick = x => !pick || x.id !== pick.id;
  const artists = daily(IX.artists.filter(a => keep(a) && quiet(a) && notPick(a)), 'artists', 20);
  const repeats = daily(hf(IX.repeats), 'repeat', 12);
  const whole = daily(hf(IX.wholeAlbums).filter(notPick), 'whole', 12);
  const revisit = daily(IX.albums.filter(al => keep(al) && quiet(al) && notPick(al)), 'revisit', 12);
  const missed = daily(IX.albums.filter(al => { const a = IX.artistById.get(al.artistId); return a && a.h >= 4 && (heatOn() ? heatOk(al) : al.h <= 2) && notPick(al); }), 'missed', 12);
  const months = monthsAgo();
  const monthName = new Date().toLocaleDateString(undefined, { month: 'long' });
  const ids = list => list.map(x => x.id);
  if (pick) logShown('pick', [pick.id]);
  logShown('rediscover', ids(artists)); logShown('on-repeat', repeats.map(sg => sg.artist.id));
  logShown('missed', ids(missed)); logShown('start-to-finish', ids(whole)); logShown('revisit', ids(revisit));
  logShown('years-ago', months.flatMap(Y => Y.artists.slice(0, 4).map(a => a.id)));
  let html = pick ? pickCard(pick) : '';
  if (artists.length) html += `<section class="section" data-src="rediscover">${shelfHead('Rediscover', 'Big for you once, quiet for a year. New picks every day.')}<div class="shelf lg">${artists.map(a => artistCard(a, a.peak ? 'Big in ' + a.peak : 'Last played ' + year(a.last))).join('')}</div></section>`;
  if (months.length) html += `<section class="section" data-src="years-ago">${shelfHead(monthName + ', Years Ago', 'What you played this month in earlier years')}<div class="shelf md">${months.map(Y => collageTile(monthLabel(Y.ym), Y.artists.slice(0, 2).map(a => a.name).join(', '), Y.artists, '#/month/' + Y.ym, 'year month')).join('')}</div></section>`;
  if (repeats.length) html += `<section class="section" data-src="on-repeat">${shelfHead('On Repeat', 'Songs you couldn\'t stop playing', '#/repeat')}${songGrid(repeats)}</section>`;
  if (missed.length) html += `<section class="section" data-src="missed">${shelfHead('Albums You Missed', 'Barely played, by artists you love')}<div class="shelf md">${missed.map(al => albumCard(al)).join('')}</div></section>`;
  if (whole.length) html += `<section class="section" data-src="start-to-finish">${shelfHead('Start to Finish', 'Albums you played front to back', '#/whole')}<div class="shelf md">${whole.map(al => albumCard(al)).join('')}</div></section>`;
  if (revisit.length) html += `<section class="section" data-src="revisit">${shelfHead('Albums to Revisit', 'Played a lot, not in the last year')}<div class="shelf md">${revisit.map(al => albumCard(al)).join('')}</div></section>`;
  return html || `<div class="empty"><p>${heatOn() ? 'Nothing to rediscover at these heat levels.' : 'Nothing to rediscover yet. It fills in once your history is imported.'}</p></div>`;
}
/* One month in one year: your most played artists then. */
function monthView(ym) {
  if (!/^\d{4}-\d{2}$/.test(ym || '')) return { title: 'Month', tab: 'home', html: '<div class="empty"><h3>Unknown month</h3></div>' };
  const list = hf(IX.artists).filter(a => (a.months || {})[ym] >= 2).sort((a, b) => b.months[ym] - a.months[ym]).slice(0, 60);
  const label = monthLabel(ym, 'long');
  const html = `<h1 class="ltitle">${esc(label)}</h1><p class="lsub">What you played most that month</p>${filterStrip()}` + (list.length
    ? `<div class="grid" style="margin-top:16px">${list.map(a => artistCard(a)).join('')}</div>` : '<div class="empty"><p>Nothing here.</p></div>');
  return { title: label, tab: 'home', html };
}
function browseTab() {
  let html = `<section class="section" data-src="browse-library">${shelfHead('Library')}<ul class="list libnav">
      <li><a class="row" href="#/library">${I.mic}<span class="t1">Artists</span><span class="end"><span class="cnt">${nf(IX.artists.length)}</span>${I.chevSm}</span></a></li>
      <li><a class="row" href="#/library/albums">${I.disc}<span class="t1">Albums</span><span class="end"><span class="cnt">${nf(IX.albums.length)}</span>${I.chevSm}</span></a></li></ul></section>`;
  if (IX.years.length > 1) html += `<section class="section" data-src="your-years">${shelfHead('Your Years', 'What you played most, year by year')}<div class="shelf md">${IX.years.map(Y => collageTile(Y.year, plural(Y.artists.length, 'artist'), Y.artists, '#/year/' + Y.year, 'year')).join('')}</div></section>`;
  html += `<section class="section" data-src="genres">${shelfHead('Genres')}<div class="tiles">${IX.genreList.map(g => collageTile(g.name, plural(g.artists.length, 'artist'), g.artists, '#/genre/' + encodeURIComponent(g.name))).join('')}${IX.unsorted.length ? collageTile('No genre yet', plural(IX.unsorted.length, 'artist'), IX.unsorted, '#/genre/~') : ''}</div></section>`;
  return html + `<p class="center-note">${plural(IX.artists.length, 'artist')} · ${plural(IX.albums.length, 'album')}</p>`;
}
function homeView() {
  const right = `<a class="nbtn" href="#/settings" aria-label="Settings">${I.gear}</a>`;   // refresh: pull down
  if (!IX.artists.length) {
    const body = state.syncing ? `<div class="empty"><div class="big">${I.record}</div><h3>Loading your library…</h3><p>The first sync reads every artist and album from Notion. It takes a moment.</p></div>`
      : `<div class="empty"><div class="big">${I.record}</div><h3>No music yet</h3><p>Your Notion library is empty or couldn't be read. Once the import has run, pull down to refresh.</p><button class="btn primary" data-act="sync">Refresh</button></div>`;
    return { title: 'Home', root: true, tab: 'home', right, html: `<h1 class="ltitle">Home</h1>${statusBanner()}${body}` };
  }
  const tab = state.homeTab === 'browse' ? 'browse' : 'rediscover';
  const html = `<h1 class="ltitle">Home</h1>${statusBanner()}
    ${filterStrip()}<div class="seg" role="tablist" aria-label="Home"><button role="tab" data-act="hometab" data-tab="rediscover" aria-pressed="${tab === 'rediscover'}" aria-selected="${tab === 'rediscover'}">Rediscover</button><button role="tab" data-act="hometab" data-tab="browse" aria-pressed="${tab === 'browse'}" aria-selected="${tab === 'browse'}">Browse</button></div>
    ${tab === 'browse' ? browseTab() : rediscoverTab()}`;
  return { title: 'Home', root: true, tab: 'home', right, html };
}
function repeatView() {
  const html = `<h1 class="ltitle">On Repeat</h1><p class="lsub">Songs you once couldn't stop playing, most intense first</p>${IX.repeats.length ? songList(IX.repeats, { repeatSub: true, ranked: true }) : '<div class="empty"><p>Nothing here yet.</p></div>'}`;
  return { title: 'On Repeat', tab: 'home', html };
}
function wholeView() {
  const html = `<h1 class="ltitle">Start to Finish</h1><p class="lsub">Albums you played front to back, most often first</p>${IX.wholeAlbums.length ? `<div class="grid" style="margin-top:16px">${IX.wholeAlbums.map(al => albumCard(al)).join('')}</div>` : '<div class="empty"><p>Nothing here yet.</p></div>'}`;
  return { title: 'Start to Finish', tab: 'home', html };
}
function songsView(id) {
  const a = IX.artistById.get(id);
  if (!a) return { title: 'Top Songs', tab: 'home', html: '<div class="empty" style="padding-top:60px"><h3>Not in your library</h3></div>' };
  const list = IX.songs.filter(sg => sg.artist === a);
  return { title: 'Top Songs', tab: 'home', html: `<h1 class="ltitle">Top Songs</h1><p class="lsub">${esc(a.name)}</p>${songList(list, { ranked: true })}` };
}
const releaseArt = r => { const a = IX.artistById.get(r.artistId); return r.cover || state.thumbs[r.id] || (a && a.image) || ''; };

/* ---------- Genre and Year pages share one layout ---------- */
const decade = y => y ? Math.floor(Number(y) / 10) * 10 : null;
/* Sort orders for the Artists/Albums grids. Empty dates always go last. */
const SORTS = {
  plays: ['Most Played', byLevel],
  name: ['Name', (a, b) => a.name.localeCompare(b.name, 'en', { sensitivity: 'base' })],
  recent: ['Recently Played', (a, b) => (b.last || '').localeCompare(a.last || '') || byLevel(a, b)],
  quiet: ['Longest Unplayed', (a, b) => (!a.last) - (!b.last) || (a.last || '').localeCompare(b.last || '') || byLevel(a, b)]
};
const sortBtn = () => `<button class="nbtn" data-act="sort-menu" aria-label="Sort: ${SORTS[state.sort][0]}">${I.sort}</button>`;
function segAndGrid(artists, albums, { eras = true, sub, sortable = true } = {}) {
  const tab = state.tab, items = tab === 'albums' ? albums : artists;
  const decades = eras ? [...new Set(items.map(x => decade(x.peak)).filter(Boolean))].sort((a, b) => b - a) : [];
  const era = decades.includes(Number(state.era)) ? Number(state.era) : 'all';
  let shown = era === 'all' ? items : items.filter(x => decade(x.peak) === era);
  if (sortable && state.sort !== 'plays') shown = shown.slice().sort(SORTS[state.sort][1]);
  const chips = decades.length > 1 ? `<div class="chips" role="group" aria-label="Era">${['all', ...decades].map(d => `<button class="chip" data-act="era" data-era="${d}" aria-pressed="${String(era) === String(d)}">${d === 'all' ? 'All' : d + 's'}</button>`).join('')}</div>` : '';
  const grid = shown.length ? `<div class="grid" style="margin-top:16px">${shown.map(x => tab === 'albums' ? albumCard(x, sub ? sub(x) : undefined) : artistCard(x)).join('')}</div>` : `<div class="empty"><p>Nothing here${era === 'all' ? '' : ' from the ' + era + 's'}.</p></div>`;
  return `<div class="seg" role="group" aria-label="Show"><button data-act="gtab" data-tab="artists" aria-pressed="${tab !== 'albums'}">Artists</button><button data-act="gtab" data-tab="albums" aria-pressed="${tab === 'albums'}">Albums</button></div>${chips}${grid}`;
}
function findGenre(name) {
  const g = name === '~' ? { name: 'No genre yet', artists: IX.unsorted } : IX.genreList.find(x => x.name === name);
  if (!g) return null;
  const ids = new Set(g.artists.map(a => a.id));
  return { ...g, ids, albums: IX.albums.filter(al => ids.has(al.artistId)).sort(byLevel) };
}
const missingGenre = name => ({ title: name || 'Genre', tab: 'home', html: `<div class="empty" style="padding-top:80px"><h3>Nothing here</h3><p>No artists in this genre any more.</p></div>` });
/* A genre's front page: a mosaic of its biggest artists, then a few short shelves. The full grid
   (with eras and sorting) sits behind See All, so the page reads at a glance. */
function genreView(name) {
  const g = findGenre(name);
  if (!g) return missingGenre(name);
  const enc = encodeURIComponent(name), artists = hf(g.artists), albums = hf(g.albums);   // sorted by heat, then plays
  const years = {};
  for (const a of g.artists) for (const [y, n] of Object.entries(a.years)) years[y] = (years[y] || 0) + n;
  const peak = Object.entries(years).sort((x, y) => y[1] - x[1])[0]?.[0];
  const songs = hf(IX.songs.filter(sg => g.ids.has(sg.artist.id))).sort((x, y) => y.c - x.c).slice(0, 12);
  const quietOnes = daily(artists.filter(a => (heatOn() || a.h >= 3) && quiet(a)), 'genre:' + name, 12);
  logShown('genre-rediscover', quietOnes.map(a => a.id), g.name);
  const pick = [...g.artists.filter(a => a.image), ...g.artists.filter(a => !a.image)].slice(0, 6);
  while (pick.length < 6) pick.push({ name: g.name + pick.length, image: '' });
  let html = `<div class="hero ghero" style="--tint:${tint(g.name)}"><div class="mosaic">${pick.map(a => art(a.image, a.name)).join('')}</div>
      <h1>${esc(g.name)}</h1><p class="gsub">${plural(g.artists.length, 'artist')} · ${plural(g.albums.length, 'album')}${peak ? ' · Most played in ' + peak : ''}</p></div>`;
  html += filterStrip();
  if (artists.length) html += `<section class="section" data-src="genre-essentials">${shelfHead('Your Essentials', 'The ' + g.name + ' you played most', artists.length > 10 ? '#/gartists/' + enc : '')}<div class="shelf md">${artists.slice(0, 10).map(a => artistCard(a)).join('')}</div></section>`;
  else html += `<div class="empty"><p>No ${esc(g.name)} at these heat levels.</p></div>`;
  if (songs.length >= 4) html += `<section class="section" data-src="genre-songs">${shelfHead('Top Songs')}${songGrid(songs, { withArtist: true })}</section>`;
  if (albums.length) html += `<section class="section" data-src="genre-albums">${shelfHead('Albums', '', albums.length > 10 ? '#/galbums/' + enc : '')}<div class="shelf md">${albums.slice(0, 10).map(al => albumCard(al)).join('')}</div></section>`;
  if (quietOnes.length) html += `<section class="section" data-src="genre-rediscover">${shelfHead('Rediscover', 'Quiet for a year. New picks every day.')}<div class="shelf sm">${quietOnes.map(a => artistCard(a, a.peak ? 'Big in ' + a.peak : '')).join('')}</div></section>`;
  const chart = yearsChart({ years }, '/' + enc);   // a year opens this genre in that year
  if (chart) html += `<section class="section" data-src="genre-years">${shelfHead('Your ' + g.name + ' Years')}${chart}</section>`;
  html += `<div class="actions" style="margin-top:26px"><a class="btn" href="#/gartists/${enc}">All ${plural(artists.length, 'artist')}</a><a class="btn" href="#/galbums/${enc}">All ${plural(albums.length, 'album')}</a></div>`;
  return { title: g.name, tab: 'home', hero: true, html, right: name === '~' ? '' : moreBtn('genre-menu'), genre: name === '~' ? null : g.name };
}
/* Every artist or album in a genre: the grid with era filter and sorting. */
function genreAllView(name) {
  const g = findGenre(name);
  if (!g) return missingGenre(name);
  const title = state.tab === 'albums' ? 'Albums' : 'Artists';
  const html = `<h1 class="ltitle">${esc(g.name)}</h1><p class="lsub">${plural(g.artists.length, 'artist')} · ${plural(g.albums.length, 'album')}</p>${filterStrip()}${segAndGrid(hf(g.artists), hf(g.albums))}`;
  return { title: `${g.name} ${title}`, tab: 'home', html, right: sortBtn() };
}
/* Every artist (or album) in the library, laid out like a genre page. */
function libraryView() {
  const albums = IX.albums.slice().sort(byLevel), artists = IX.artists.slice().sort(byLevel);
  const title = state.tab === 'albums' ? 'Albums' : 'Artists';
  const html = `<h1 class="ltitle">${title}</h1><p class="lsub">${plural(artists.length, 'artist')} · ${plural(albums.length, 'album')}</p>${filterStrip()}${segAndGrid(hf(artists), hf(albums))}`;
  return { title, tab: 'home', html, right: sortBtn() };
}
/* A year's most played, optionally narrowed to one genre (#/year/2014/Hip-hop) with chips to switch. */
function yearView(arg) {
  const [y, ...rest] = String(arg || '').split('/'), genre = rest.join('/');
  const Y = IX.years.find(x => x.year === y);
  if (!Y) return { title: y, tab: 'home', html: `<div class="empty" style="padding-top:60px"><h3>No plays in ${esc(y)}</h3></div>` };
  const inGenre = a => !genre || (a && a.genres.includes(genre));
  const artists = hf(Y.artists.filter(inGenre)).slice(0, 100);
  const albums = hf(IX.albums.filter(al => al.years[y] && inGenre(IX.artistById.get(al.artistId)))).sort((a, b) => b.years[y] - a.years[y]).slice(0, 100);
  const byGenre = {};
  for (const a of Y.artists) for (const g of a.genres) byGenre[g] = (byGenre[g] || 0) + (a.years[y] || 0);
  const top = Object.entries(byGenre).sort((a, b) => b[1] - a[1]).slice(0, 8).map(x => x[0]);
  if (genre && !top.includes(genre)) top.unshift(genre);
  const chips = top.length > 1 ? `<div class="chips" role="group" aria-label="Genre">${['', ...top].map(g => `<button class="chip" data-act="year-genre" data-y="${esc(y)}" data-g="${esc(g)}" aria-pressed="${g === genre}">${esc(g || 'All')}</button>`).join('')}</div>` : '';
  const html = `<h1 class="ltitle">${esc(y)}</h1><p class="lsub">Your most played ${genre ? esc(genre) + ' ' : ''}that year</p>${filterStrip()}${chips}${segAndGrid(artists, albums, { eras: false, sortable: false })}`;
  return { title: genre ? `${genre} · ${y}` : y, tab: 'home', html };
}

/* ---------- Artist ---------- */
function similarTo(a) {
  const listed = a.similar.map(id => IX.artistById.get(id)).filter(Boolean);
  if (listed.length) return listed;
  // Fallback: artists sharing 2+ genres, or several strong Last.fm tags.
  const myTags = new Set(a.tags.filter(t => t[1] >= 20).map(t => t[0]));
  const myGenres = new Set(a.genres);
  return IX.artists.filter(b => b.id !== a.id).map(b => {
    const g = b.genres.filter(x => myGenres.has(x)).length;
    const t = b.tags.filter(x => x[1] >= 20 && myTags.has(x[0])).length;
    return [b, g, t];
  }).filter(([, g, t]) => g >= 2 || t >= 3).sort((x, y) => (y[1] * 3 + y[2]) - (x[1] * 3 + x[2]) || (y[0].plays || 0) - (x[0].plays || 0)).slice(0, 10).map(x => x[0]);
}
/* A small bar per year since you first played them: shape, not numbers. */
function yearsChart(a, suffix = '') {   // returns the chart only; the artist page puts it in a tab
  const ys = Object.keys(a.years).map(Number);
  if (ys.length < 2) return '';
  const from = Math.min(...ys), to = Math.max(thisYear, ...ys), max = Math.max(...Object.values(a.years));
  const peak = Number(Object.entries(a.years).sort((x, y) => y[1] - x[1])[0][0]);
  const bars = [];
  for (let y = from; y <= to; y++) { const n = a.years[y] || 0; bars.push(`<a href="#/year/${y}${suffix}" class="${y === peak ? 'pk' : ''}" style="--h:${n ? Math.max(6, Math.round(n / max * 100)) : 2}%" title="${y}"></a>`); }
  return `<div class="ychart" role="img" aria-label="Played from ${from} to ${to}, most in ${peak}"><div class="bars">${bars.join('')}</div><div class="ylabels"><span>${from}</span><span class="pk">${peak}</span><span>${to}</span></div></div>`;
}
function artistView(id) {
  const a = IX.artistById.get(id);
  if (!a) return { title: 'Artist', tab: 'home', html: `<div class="empty" style="padding-top:120px"><h3>Not in your library</h3><p>This artist may have been blocked or removed.</p></div>` };
  const albums = IX.albumsByArtist.get(a.id) || [];
  const sim = similarTo(a);
  const rel = (IX.releasesByArtist.get(a.id) || []).slice().sort((x, y) => (y.release || '').localeCompare(x.release || ''));
  const genres = a.genres.length ? `<div class="genres">${a.genres.map(g => `<a href="#/genre/${encodeURIComponent(g)}">${esc(g)}</a>`).join('')}</div>` : '';
  const bits = [a.peak ? 'Peak ' + a.peak : '', a.first ? 'Since ' + year(a.first) : '', a.last ? 'Last played ' + monthYear(a.last) : ''].filter(Boolean).join(' · ');
  let html = `<div class="hero" style="--tint:${tint(a.name)}">${art(a.image, a.name, { size: 'lg' })}<h1>${esc(a.name)}</h1>
      <button class="play" data-act="spotify-artist" aria-label="${srOn() ? 'Play' : 'Open'} ${esc(a.name)}${srOn() ? '' : ' in Spotify'}">${I.play}</button></div>
    <div class="meta">${heatLine(a.h)}${genres}${bits ? `<div class="line">${esc(bits)}</div>` : ''}<div class="small">${plural(a.plays || 0, 'play')}${a.aliases.length ? ' · also as ' + esc(a.aliases.join(', ')) : ''}</div></div>`;
  const songs = IX.songs.filter(sg => sg.artist === a);
  if (songs.length) html += `<section class="section" data-src="artist-songs">${shelfHead('Top Songs', '', songs.length > 5 ? '#/songs/' + a.id : '')}${songList(songs.slice(0, 5), { ranked: true })}</section>`;
  if (albums.length) html += `<section class="section" data-src="artist-albums">${shelfHead('Your Albums')}<div class="shelf md">${albums.map(al => albumCard(al, [al.type, year(al.release)].filter(Boolean).join(' · '))).join('')}</div></section>`;
  if (sim.length) html += `<section class="section" data-src="similar">${shelfHead('Similar in Your Library', a.similar.length ? '' : 'Shares genres or tags')}<div class="shelf sm">${sim.map(b => artistCard(b)).join('')}</div></section>`;
  // Your Years and About share one spot, behind a segmented control, so the page stays short.
  const chart = yearsChart(a);
  const bio = a.bio ? `<div class="bio${state.bioOpen ? ' open' : ''}"><p>${esc(a.bio).replace(/\n+/g, '</p><p>')}</p>${a.bio.length > 260 ? `<button class="more-link" data-act="bio">${state.bioOpen ? 'Less' : 'More'}</button>` : ''}<div class="src">From Last.fm</div></div>` : '';
  const info = [chart && ['years', 'Your Years', chart], bio && ['about', 'About', bio]].filter(Boolean);
  if (info.length === 1) html += `<section class="section">${shelfHead(info[0][1])}${info[0][2]}</section>`;
  if (info.length === 2) {
    const cur = info.find(t => t[0] === state.artistTab) || info[0];
    html += `<section class="section"><div class="seg" role="tablist" style="margin-top:0;margin-bottom:14px">${info.map(t => `<button role="tab" data-act="atab" data-tab="${t[0]}" aria-pressed="${t === cur}" aria-selected="${t === cur}">${t[1]}</button>`).join('')}</div>${cur[2]}</section>`;
  }
  if (rel.length) html += `<section class="section" data-src="artist-releases">${shelfHead('New Releases')}<div class="shelf md">${rel.map(r => releaseCard(r, { withArtist: false })).join('')}</div></section>`;
  html += `<div class="actions" style="margin-top:28px"><button class="btn primary" data-act="spotify-artist">${I.play}${playLabel()}</button></div>`;
  return { title: a.name, tab: 'home', hero: true, html, right: moreBtn('artist-menu'), artist: a };
}

/* ---------- Album ---------- */
function albumView(id) {
  const al = IX.albumById.get(id);
  if (!al) return { title: 'Album', tab: 'home', html: `<div class="empty" style="padding-top:120px"><h3>Album not found</h3><p>It may have been blocked or removed.</p></div>` };
  const a = IX.artistById.get(al.artistId);
  const sub = [al.type || 'Album', year(al.release)].filter(Boolean).join(' · ');
  const more = (IX.albumsByArtist.get(al.artistId) || []).filter(x => x.id !== al.id);
  const yrs = Object.keys(al.years).sort();
  const span = yrs.length ? (yrs[0] === yrs[yrs.length - 1] ? `in ${yrs[0]}` : `${yrs[0]}–${yrs[yrs.length - 1]}`) : '';
  let html = `<div class="album-head">${art(al.cover, al.name, { size: 'lg', glowing: true })}<h1>${esc(al.name)}</h1>
      ${a ? `<a class="by" href="#/artist/${a.id}">${esc(al.credited || a.name)}</a>` : `<div class="by">${esc(al.credited)}</div>`}<div class="sub">${esc(sub)}</div></div>
    <div class="actions"><button class="btn primary" data-act="spotify-album">${I.play}${playLabel()}</button></div>
    <div class="meta" style="align-items:center;text-align:center;margin-top:16px">${heatLine(al.h)}${al.full ? `<div class="line">Played start to finish ${al.full === 1 ? 'once' : al.full === 2 ? 'twice' : nf(al.full) + ' times'}</div>` : ''}<div class="small">${plural(al.plays || 0, 'play')}${al.tracks ? ' · ' + plural(al.tracks, 'track') + ' played' : ''}${span ? ' · ' + span : ''}</div></div>`;
  const favs = IX.songs.filter(sg => sg.album === al).slice(0, 5);
  if (favs.length) html += `<section class="section" data-src="album-favourites">${shelfHead('Your Favourites')}${songList(favs, { ranked: true })}</section>`;
  if (more.length) html += `<section class="section" data-src="album-more">${shelfHead('More by ' + (a ? a.name : 'this artist'))}<div class="shelf md">${more.map(x => albumCard(x, year(x.release))).join('')}</div></section>`;
  return { title: al.name, tab: 'home', html, right: moreBtn('album-menu'), album: al };
}

/* ---------- Search ---------- */
function searchResults(q) {
  const f = fold(q);
  if (!f) return `<div class="section" style="margin-top:8px">${shelfHead('Browse Genres')}${IX.genreList.map(g => `<a class="slink" href="#/genre/${encodeURIComponent(g.name)}">${esc(g.name)}</a>`).join('')}</div>`;
  const score = (n, extra = []) => { if (n.startsWith(f)) return 3; if (n.includes(' ' + f)) return 2; if (n.includes(f)) return 1; return extra.some(x => x.includes(f)) ? 1 : 0; };
  const top = (list, fn, n) => list.map(x => [x, fn(x)]).filter(x => x[1]).sort((x, y) => y[1] - x[1] || (y[0].plays || 0) - (x[0].plays || 0)).slice(0, n).map(x => x[0]);
  const artists = top(IX.artists, a => score(a._f, a._aliases), 30);
  const albums = top(IX.albums, al => score(al._f), 40);
  const songs = IX.songs.map(sg => [sg, score(sg._f)]).filter(x => x[1]).sort((x, y) => y[1] - x[1] || y[0].c - x[0].c).slice(0, 20).map(x => x[0]);
  if (!artists.length && !albums.length && !songs.length) return `<div class="empty"><h3>No results</h3><p>Nothing in your library matches “${esc(q)}”.</p></div>`;
  const aliasHit = a => !a._f.includes(f) && a.aliases.find(x => fold(x).includes(f));
  let html = '';
  if (artists.length) html += `<section class="section" style="margin-top:8px">${shelfHead('Artists')}<ul class="list">${artists.map(a => `<li><a class="row" href="#/artist/${a.id}">${art(a.image, a.name, { circle: true })}<div class="txt"><div class="t1">${esc(a.name)}</div><div class="t2">${aliasHit(a) ? 'Also as ' + esc(aliasHit(a)) : 'Artist'}${a.h ? ' · ' + HEAT[a.h][0] : ''}</div></div><span class="end">${heat(a.h)}</span></a></li>`).join('')}</ul></section>`;
  if (songs.length) html += `<section class="section">${shelfHead('Songs')}${songList(songs, { withArtist: true })}</section>`;
  if (albums.length) html += `<section class="section">${shelfHead('Albums')}<ul class="list">${albums.map(al => `<li><a class="row" href="#/album/${al.id}">${art(al.cover, al.name)}<div class="txt"><div class="t1">${esc(al.name)}</div><div class="t2">${esc([al.type || 'Album', artistName(al)].filter(Boolean).join(' · '))}</div></div><span class="end">${heat(al.h)}</span></a></li>`).join('')}</ul></section>`;
  return html;
}
function updateResults() { const box = $('#results'); if (!box) return; box.innerHTML = searchResults(state.q); hydrate(box); const c = $('.field .clear'); if (c) c.hidden = !state.q; }
function searchView() {
  const html = `<h1 class="ltitle">Search</h1><div class="searchbar"><label class="field">${I.search}<input id="q" type="search" placeholder="Artists, albums and songs" value="${esc(state.q)}" autocomplete="off" autocapitalize="off" enterkeyhint="search"><button class="clear" data-act="clear-q" aria-label="Clear" ${state.q ? '' : 'hidden'}>${I.close}</button></label></div><div id="results" data-src="search">${searchResults(state.q)}</div>`;
  let t = null;
  return {
    title: 'Search', root: true, tab: 'search', html, after: () => {
      const q = $('#q');
      q.addEventListener('input', () => { state.q = q.value; clearTimeout(t); t = setTimeout(updateResults, 90); });
      q.addEventListener('keydown', e => { if (e.key === 'Enter') q.blur(); });
    }
  };
}

/* ---------- heat filter: button, strip and sheet ---------- */
/* The heat filter: a row of chips at the top of Home, genres, the library, years, months and New
   (like Spotify's Music / Podcasts chips). Tap levels on and off; All (nothing ticked) shows everything. */
function filterStrip() {
  const on = new Set(state.heatFilter);
  return `<div class="hchips hfilter" role="group" aria-label="Heat filter"><button class="hchip all" data-act="heat-clear" aria-pressed="${!on.size}">All</button>${[5, 4, 3, 2, 1].map(n =>
    `<button class="hchip hl${n}" data-act="heat-toggle" data-h="${n}" aria-pressed="${on.has(n)}">${heat(n)}<span>${HEAT[n][0]}</span></button>`).join('')}</div>`;
}
function toggleHeat(n) {
  const on = new Set(state.heatFilter);
  on.has(n) ? on.delete(n) : on.add(n);
  state.heatFilter = on.size === 5 ? [] : [...on].sort();
  ls.set('crate.heatFilter', state.heatFilter);
  const strip = app.querySelector('.hfilter'), x = strip ? strip.scrollLeft : 0;
  render(false);
  const again = app.querySelector('.hfilter'); if (again) again.scrollLeft = x;   // keep the row where it was
}

/* ---------- Heat: your all-time greats, level by level ---------- */
const HEAT_SHARE = { 5: 'Top 5%', 4: 'Next 10%', 3: 'Next 20%', 2: 'Next 30%', 1: 'The rest' };
const KINDS = [['artists', 'Artists', 'artist'], ['albums', 'Albums', 'album'], ['songs', 'Songs', 'song']];
function heatItems(kind, h) {
  if (kind === 'albums') return IX.albums.filter(x => x.h === h).sort((x, y) => (y.plays || 0) - (x.plays || 0));
  if (kind === 'songs') return IX.songs.filter(x => x.h === h).sort((x, y) => y.c - x.c);
  return IX.artists.filter(x => x.h === h).sort((x, y) => (y.plays || 0) - (x.plays || 0));
}
/* One look for every kind: where it goes, its picture, its two lines. Songs open in Spotify. */
function heatEntry(kind, x) {
  if (kind === 'songs') return { act: `data-act="song" data-t="${esc(x.t)}" data-a="${x.artist.id}" data-q="${esc(x.n + ' ' + x.artist.name)}"`, tag: 'button',
    img: (x.album && x.album.cover) || x.artist.image, name: x.n, sub: [x.artist.name, x.y].filter(Boolean).join(' · ') };
  if (kind === 'albums') return { act: `href="#/album/${x.id}"`, tag: 'a', img: x.cover, name: x.name,
    sub: [artistName(x), year(x.release)].filter(Boolean).join(' · ') };
  return { act: `href="#/artist/${x.id}"`, tag: 'a', img: x.image, name: x.name, sub: [x.genres[0], x.peak ? 'Peak ' + x.peak : ''].filter(Boolean).join(' · '), circle: true };
}
function heatView() {
  const h = [1, 2, 3, 4, 5].includes(state.heatLevel) ? state.heatLevel : 5;
  const kind = KINDS.some(k => k[0] === state.heatKind) ? state.heatKind : 'artists';
  const [, kindLabel, one] = KINDS.find(k => k[0] === kind);
  const all = heatItems(kind, h), items = all.slice(0, 300), total = all.length;
  const chips = [5, 4, 3, 2, 1].map(n => `<button class="hchip hl${n}" data-act="heat-level" data-h="${n}" aria-pressed="${n === h}">${heat(n)}<span>${HEAT[n][0]}</span></button>`).join('');
  const hero = `<div class="heat-hero hl${h}"><div class="haze" aria-hidden="true"></div>
      <div class="hh-top">${heat(h)}<span>${HEAT_SHARE[h]} of your ${kindLabel.toLowerCase()}</span></div>
      <div class="hh-name">${HEAT[h][0]}</div>
      <div class="hh-sub">${plural(total, one)} · ${HEAT[h][1]}</div></div>`;
  const card = (x, rank, big) => { const e = heatEntry(kind, x);
    return `<${e.tag} class="pod${big ? ' big' : ''}" ${e.act}><div class="pod-art">${art(e.img, e.name, { size: big ? 'lg' : 'sm' })}<span class="rk">${rank}</span></div><div class="t1">${esc(e.name)}</div><div class="t2">${esc(e.sub)}</div></${e.tag}>`; };
  const podium = items.length ? `<div class="podium" data-src="heat">${card(items[0], 1, true)}${items.slice(1, 3).map((x, i) => card(x, i + 2, false)).join('')}</div>` : '';
  const rest = items.slice(3).map((x, i) => { const e = heatEntry(kind, x);
    return `<li><${e.tag} class="row hrow" ${e.act}><span class="rank">${i + 4}</span>${e.circle ? art(e.img, e.name, { circle: true }) : art(e.img, e.name)}<div class="txt"><div class="t1">${esc(e.name)}</div><div class="t2">${esc(e.sub)}</div></div></${e.tag}></li>`; }).join('');
  const html = `<h1 class="ltitle">Heat</h1><p class="lsub">Your all-time most played, level by level</p>
    <div class="hchips" role="group" aria-label="Heat level">${chips}</div>
    <div class="seg" role="group" aria-label="Show">${KINDS.map(([k, label]) => `<button data-act="heat-kind" data-k="${k}" aria-pressed="${k === kind}">${label}</button>`).join('')}</div>
    ${hero}${items.length ? podium + (rest ? `<ul class="list hlist" data-src="heat">${rest}</ul>` : '') + (total > items.length ? `<p class="center-note">Showing the first ${items.length} of ${nf(total)}</p>` : '')
      : `<div class="empty"><p>Nothing at this level yet.</p></div>`}`;
  return { title: 'Heat', root: true, tab: 'heat', html };
}

/* ---------- New releases ---------- */
/* Grouped by the artist's main genre, hottest artists first, so you see at a glance which ones matter. */
const relDate = d => { if (!d) return ''; const x = new Date(String(d).slice(0, 10) + 'T00:00:00'); if (isNaN(x)) return '';
  return x.toLocaleDateString(undefined, { day: 'numeric', month: 'short', ...(String(d).slice(0, 4) !== today().slice(0, 4) ? { year: 'numeric' } : {}) }); };
const relWhen = r => relDay(r) > today() ? 'Out ' + relDate(relDay(r)) : relDate(relDay(r));
const relRank = (x, y) => ((relArtist(y) || {}).h || 0) - ((relArtist(x) || {}).h || 0) || ((relArtist(y) || {}).plays || 0) - ((relArtist(x) || {}).plays || 0) || relDay(y).localeCompare(relDay(x));
const relGenre = r => ((relArtist(r) || {}).genres || [])[0] || 'Other';
function releaseCard(r, { withArtist = true } = {}) {
  const a = relArtist(r);
  const sub = withArtist ? (r.credited || (a ? a.name : '')) : [r.type, relWhen(r)].filter(Boolean).join(' · ');
  return `<a class="card rcard" href="#/release/${r.id}">${art(releaseArt(r), r.name, { h: a ? a.h : 0 })}<div class="t1">${esc(r.name)}</div><div class="t2">${esc(sub)}</div>${withArtist ? `<div class="t3">${esc([r.type, relWhen(r)].filter(Boolean).join(' · '))}</div>` : ''}</a>`;
}
function releaseHero(r) {
  const a = relArtist(r);
  const why = [a ? a.name : r.credited, a && a.h ? HEAT[a.h][0] : '', r.type, r.tracks ? plural(r.tracks, 'track') : ''].filter(Boolean);
  return `<div class="pick" data-src="new-top"><a class="pick-link" href="#/release/${r.id}" aria-label="Hottest new release: ${esc(r.name)}">${art(releaseArt(r), r.name, { size: 'lg', h: a ? a.h : 0 })}
      <div class="pick-body"><span class="eyebrow">Hottest New Release · ${esc(relWhen(r))}</span><h2>${esc(r.name)}</h2><p>${esc(why.join(' · '))}</p></div></a>
    <button class="pick-play" data-act="release-spotify" data-id="${r.id}" aria-label="${srOn() ? 'Play' : 'Open'} ${esc(r.name)}${srOn() ? '' : ' in Spotify'}">${I.play}</button></div>`;
}
function newView() {
  const out = newReleases().filter(r => heatOk(relArtist(r))).sort(relRank);
  const soon = comingSoon().filter(r => heatOk(relArtist(r)));
  const groups = new Map();
  for (const r of out) { const g = relGenre(r); if (!groups.has(g)) groups.set(g, []); groups.get(g).push(r); }
  const order = [...groups.entries()].sort((x, y) => relRank(x[1][0], y[1][0]) || y[1].length - x[1].length);
  const pick = groups.has(state.newGenre) ? state.newGenre : '';
  const chips = order.length > 1 ? `<div class="chips" role="group" aria-label="Genre">${[['', out], ...order].map(([g, list]) => `<button class="chip" data-act="new-genre" data-g="${esc(g)}" aria-pressed="${g === pick}">${esc(g || 'All')}<span class="chip-n">${list.length}</span></button>`).join('')}</div>` : '';
  const shown = pick ? groups.get(pick) : out;
  logShown('new', shown.map(r => r.id));
  let html = `<h1 class="ltitle">New Releases</h1><p class="lsub">${out.length ? `${plural(out.length, 'album or EP', 'albums and EPs')} this month from artists you love` : 'Albums and EPs from your Inferno, Blaze and Flame artists'}</p>${statusBanner()}${filterStrip()}${chips}`;
  if (shown.length) {
    html += releaseHero(shown[0]);
    const rest = shown.slice(1);
    if (pick || order.length === 1) html += rest.length ? `<section class="section" data-src="new">${shelfHead(pick ? 'More ' + pick : 'More This Month')}<div class="grid">${rest.map(r => releaseCard(r)).join('')}</div></section>` : '';
    else for (const [g, list] of order) {
      const items = list.filter(r => r !== shown[0]);
      if (items.length) html += `<section class="section" data-src="new">${shelfHead(g, plural(list.length, 'new release'))}<div class="shelf md">${items.map(r => releaseCard(r)).join('')}</div></section>`;
    }
  } else html += `<div class="empty"><div class="big">${I.sparkle}</div><h3>All caught up</h3><p>${heatOn() ? 'Nothing new at these heat levels.' : 'Nothing new this month. Each artist is checked about once a week.'}</p></div>`;
  if (soon.length && !pick) html += `<section class="section" data-src="coming-soon">${shelfHead('Coming Soon', 'Announced by artists you love')}<div class="shelf md">${soon.map(r => releaseCard(r)).join('')}</div></section>`;
  return { title: 'New Releases', root: true, tab: 'new', html };
}
/* A release page: enough to decide whether it's for you before going to Spotify. */
const mmss = s => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const trackKey = t => fold(String(t || '').replace(/\s*[([][^)\]]*[)\]]/g, '').split(/\s+-\s+/)[0]);
function releaseView(id) {
  const r = state.releases.find(x => x.id === id);
  if (!r) return { title: 'Release', tab: 'new', html: `<div class="empty" style="padding-top:120px"><h3>Release not found</h3><p>It may have been removed.</p></div>` };
  const a = relArtist(r);
  let tracks = [];
  try { tracks = JSON.parse(r.tracklist || '[]'); } catch { }
  const size = [r.tracks ? plural(r.tracks, 'track') : '', r.length ? `${r.length} min` : ''].filter(Boolean).join(', ');
  const mine = a ? new Map(IX.songs.filter(sg => sg.artist === a).map(sg => [trackKey(sg.n), sg])) : new Map();
  const done = r.status !== 'New';
  let html = `<div class="album-head">${art(releaseArt(r), r.name, { size: 'lg', glowing: true })}<h1>${esc(r.name)}</h1>
      ${a ? `<a class="by" href="#/artist/${a.id}">${esc(r.credited || a.name)}</a>` : `<div class="by">${esc(r.credited)}</div>`}
      <div class="sub">${esc([r.type || 'Album', relWhen(r)].filter(Boolean).join(' · '))}</div>
      ${size || r.label ? `<div class="rel-facts">${esc([size, r.label].filter(Boolean).join(' · '))}</div>` : ''}</div>
    <div class="actions"><button class="btn primary" data-act="release-spotify" data-id="${r.id}">${I.play}${playLabel()}</button></div>
    <div class="actions rel-acts">${done
      ? `<span class="rel-done">${r.status === 'Listened' ? I.check + 'Listened' : r.status === 'Not interested' ? I.hide + 'Not for you' : esc(r.status)}</span><button class="btn" data-act="rel-status" data-id="${r.id}" data-status="New">Undo</button>`
      : `<button class="btn" data-act="rel-status" data-id="${r.id}" data-status="Listened">${I.check}Listened</button><button class="btn" data-act="rel-status" data-id="${r.id}" data-status="Not interested">${I.hide}Not for Me</button>`}</div>`;
  if (a) {
    const bits = [`${plural(a.plays || 0, 'play')}`, a.first ? 'since ' + year(a.first) : '', a.peak ? 'most in ' + a.peak : ''].filter(Boolean).join(' · ');
    html += `<section class="section">${shelfHead('You and ' + a.name)}<div class="meta" style="margin-top:0">${heatLine(a.h)}<div class="line">${esc(bits)}</div>${a.genres.length ? `<div class="genres">${a.genres.map(g => `<a href="#/genre/${encodeURIComponent(g)}">${esc(g)}</a>`).join('')}</div>` : ''}</div></section>`;
  }
  if (tracks.length) {
    const known = tracks.filter(t => mine.has(trackKey(t[0]))).length;
    html += `<section class="section">${shelfHead('Tracks', known ? `You already know ${known === 1 ? 'one' : nf(known)} of these` : '')}<ol class="list tracks">${tracks.map(([t, sec], i) => {
      const sg = mine.get(trackKey(t));
      return `<li><div class="row trk${sg ? ' known' : ''}"><span class="rank">${i + 1}</span><div class="txt"><div class="t1">${esc(t)}</div>${sg ? `<div class="t2">You've played it ${plural(sg.c, 'time')}</div>` : ''}</div><span class="end">${sg ? heat(sg.h) : ''}<span class="dur">${sec ? mmss(sec) : ''}</span></span></div></li>`; }).join('')}</ol>
      ${r.tracks > tracks.length ? `<p class="center-note">and ${plural(r.tracks - tracks.length, 'more track')}</p>` : ''}</section>`;
  }
  const favs = a ? IX.songs.filter(sg => sg.artist === a).slice(0, 3) : [];
  if (favs.length) html += `<section class="section" data-src="release-favourites">${shelfHead('Your Favourites by ' + a.name, '', '#/songs/' + a.id)}${songList(favs, { ranked: true })}</section>`;
  const albums = a ? IX.albumsByArtist.get(a.id) || [] : [];
  if (albums.length) html += `<section class="section" data-src="release-albums">${shelfHead('Your ' + a.name + ' Albums')}<div class="shelf md">${albums.map(al => albumCard(al, [al.type, year(al.release)].filter(Boolean).join(' · '))).join('')}</div></section>`;
  return { title: r.name, tab: 'new', html, release: r };
}
async function changeRelease(id, status, { undo = true } = {}) {
  const r = state.releases.find(x => x.id === id); if (!r) return;
  const before = r.status;
  r.status = status; persist();
  renderTabs(currentView && currentView.tab);
  try {
    await setStatus(id, status);
    logEvent('mark', { k: 'release', i: id, a: r.artistId, x: status });
    if (undo && status !== 'New') toast(status === 'Listened' ? 'Marked as listened' : 'Hidden from New Releases', () => changeRelease(id, before, { undo: false }).then(() => render(false)));
  } catch (e) {
    r.status = before; persist(); render(false);
    toast(errText(e));
  }
}

/* ---------- Blocking ---------- */
/* Artists and albums already in the library are blocked by setting their Status to Excluded (the sync
   never touches those again). Anything else (a song, a genre, an artist or album not in the library)
   becomes a row in the Notion Blocklist, which the weekly sync reads. */
async function blockArtist(a) {
  try {
    await setStatus(a.id, 'Excluded');
    state.artists = state.artists.filter(x => x.id !== a.id);
    state.excluded.artists.unshift({ id: a.id, name: a.name, image: a.image });
    index(); persist();
    if (route().view === 'artist') goBack(); else render(false);
    toast(`${a.name} blocked`, () => unblockExcluded('artists', a.id, a));
  } catch (e) { toast(errText(e)); }
}
async function blockAlbum(al) {
  try {
    await setStatus(al.id, 'Excluded');
    state.albums = state.albums.filter(x => x.id !== al.id);
    state.excluded.albums.unshift({ id: al.id, name: al.name, image: al.cover, credited: artistName(al) });
    index(); persist();
    if (route().view === 'album') goBack(); else render(false);
    toast(`${al.name} blocked`, () => unblockExcluded('albums', al.id, al));
  } catch (e) { toast(errText(e)); }
}
async function blockOther(type, name, artist = '') {
  try {
    const pg = await addBlock(type, name, artist);
    const b = { id: cleanId(pg.id), name, type, artist };
    state.blocks.unshift(b);
    index(); persist();
    if (type === 'Genre' && route().view === 'genre') goBack(); else render(false);
    toast(`${name} blocked`, () => unblockRow(b));
  } catch (e) { toast(errText(e)); }
}
async function unblockExcluded(kind, id, full) {
  try {
    await setStatus(id, 'Review');
    state.excluded[kind] = state.excluded[kind].filter(x => x.id !== id);
    if (full) { full.status = 'Review'; (kind === 'artists' ? state.artists : state.albums).push(full); }
    index(); persist(); render(false);
    toast('Unblocked');
    if (!full) sync();   // its full details come back with the next sync
  } catch (e) { toast(errText(e)); }
}
async function unblockRow(b) {
  try {
    await trashPage(b.id);
    state.blocks = state.blocks.filter(x => x.id !== b.id);
    index(); persist(); render(false);
    toast('Unblocked');
  } catch (e) { toast(errText(e)); }
}
const BLOCK_TYPES = ['Artist', 'Album', 'Song', 'Genre'];
function blocklistView() {
  const ex = state.excluded;
  const groups = [
    ['Artists', [...ex.artists.map(x => ({ ...x, kind: 'artists', sub: 'Removed from your library' })), ...state.blocks.filter(b => b.type === 'Artist').map(b => ({ ...b, sub: 'Never added' }))]],
    ['Albums', [...ex.albums.map(x => ({ ...x, kind: 'albums', sub: x.credited || 'Removed from your library' })), ...state.blocks.filter(b => b.type === 'Album').map(b => ({ ...b, sub: b.artist || 'Any artist' }))]],
    ['Songs', state.blocks.filter(b => b.type === 'Song').map(b => ({ ...b, sub: b.artist || 'Any artist' })), 'Plays of these songs don\'t count'],
    ['Genres', state.blocks.filter(b => b.type === 'Genre').map(b => ({ ...b, sub: 'Genre' })), 'Artists whose main genre is one of these are hidden']
  ];
  const rows = items => items.map(x => `<li><div class="row">${x.type === 'Genre' || x.type === 'Song' ? `<div class="art sm-ic" style="--tint:${tint(x.name)}"><span class="ini">${esc(initials(x.name))}</span></div>` : art(x.image, x.name, { circle: x.kind === 'artists' || x.type === 'Artist' })}<div class="txt"><div class="t1">${esc(x.name)}</div><div class="t2">${esc(x.sub)}</div></div><button class="unblock" data-act="unblock" data-id="${x.id}" data-kind="${x.kind || ''}">Unblock</button></div></li>`).join('');
  const total = groups.reduce((s, g) => s + g[1].length, 0);
  const html = `<h1 class="ltitle">Blocklist</h1><p class="lsub">Blocked things stay out of Crate and the weekly sync. Block from an artist, album or genre page, or add anything here.</p>
    ${total ? groups.filter(g => g[1].length).map(([t, items, note]) => `<section class="section">${shelfHead(t, note)}<ul class="list">${rows(items)}</ul></section>`).join('')
      : `<div class="empty"><div class="big">${I.block}</div><h3>Nothing blocked</h3><p>Tap + to block an artist, album, song or genre.</p></div>`}`;
  return { title: 'Blocklist', tab: 'home', html, right: `<button class="nbtn" data-act="block-add" aria-label="Block something">${I.plus}</button>` };
}
function blockForm(type = 'Artist', again = false) {
  const genres = [...new Set(state.artists.flatMap(a => a.genres))].sort();
  overlay.innerHTML = `<div class="scrim${again ? ' still' : ''}" data-act="close-sheet"></div><form class="sheet form${again ? ' still' : ''}" id="block-form" role="dialog" aria-modal="true" aria-label="Block something">
    <div class="group"><div class="head"><b>Block</b>It stays out of Crate and the weekly sync.</div>
      <div class="pad"><div class="seg" role="group" aria-label="Type" style="margin:0">${BLOCK_TYPES.map(t => `<button type="button" data-btype="${t}" aria-pressed="${t === type}">${t}</button>`).join('')}</div>
        <label class="field" style="margin-top:12px"><input id="b-name" placeholder="${type === 'Genre' ? 'Genre' : type + ' name'}" autocomplete="off" ${type === 'Genre' ? 'list="b-genres"' : ''} required></label>
        <datalist id="b-genres">${genres.map(g => `<option value="${esc(g)}">`).join('')}</datalist>
        ${type === 'Album' || type === 'Song' ? `<label class="field" style="margin-top:8px"><input id="b-artist" placeholder="By (optional)" autocomplete="off"></label>` : ''}
        <p class="hint">${type === 'Song' ? 'Plays of this song stop counting (from the next sync; a re-import also removes past plays).' : type === 'Genre' ? 'Hides artists whose main genre this is, and the sync stops adding them.' : type === 'Artist' ? 'If they\'re in your library they\'re removed; otherwise the sync never adds them.' : 'If it\'s in your library it\'s removed; otherwise the sync never adds it.'}</p></div>
      <button type="submit" class="destructive">Block ${type}</button></div>
    <div class="group"><button type="button" class="cancel" data-act="close-sheet">Cancel</button></div></form>`;
  overlay.querySelectorAll('[data-btype]').forEach(b => b.addEventListener('click', () => blockForm(b.dataset.btype, true)));
  const nameEl = $('#b-name'); setTimeout(() => nameEl.focus(), 50);
  $('#block-form').addEventListener('submit', e => {
    e.preventDefault();
    const name = nameEl.value.trim(), by = ($('#b-artist') || {}).value?.trim() || '';
    if (!name) return;
    closeSheet();
    if (type === 'Artist') { const a = state.artists.find(x => fold(x.name) === fold(name) || x.aliases.some(y => fold(y) === fold(name))); return a ? blockArtist(a) : blockOther('Artist', name); }
    if (type === 'Album') {
      const al = state.albums.find(x => fold(x.name) === fold(name) && (!by || fold(artistName(x)) === fold(by)));
      return al ? blockAlbum(al) : blockOther('Album', name, by);
    }
    blockOther(type, name, by);
  });
}

/* ---------- Editing genres ---------- */
/* Genres set here count as yours: the weekly sync only updates genres it set itself. */
const GENRES = ['Nederhop', 'Hip-hop', 'Alternative rock', 'Rock', 'Indie', 'Singer-songwriter', 'Folk', 'Soul', 'R&B', 'Funk', 'Jazz',
  'Reggae', 'Electronic', 'Dance', 'Hardcore', 'Pop', 'Metal', 'Punk', 'Blues', 'Country', 'Soundtrack'];
function genreForm(a, picked = null) {
  const again = !!picked;   // re-drawn after a tap: don't slide the sheet in again
  picked = picked || a.genres.slice();
  const all = [...new Set([...GENRES, ...state.artists.flatMap(x => x.genres)])].sort((x, y) => x.localeCompare(y));
  overlay.innerHTML = `<div class="scrim${again ? ' still' : ''}" data-act="close-sheet"></div><div class="sheet form${again ? ' still' : ''}" role="dialog" aria-modal="true" aria-label="Edit genres">
    <div class="group"><div class="head"><b>Genres for ${esc(a.name)}</b>${picked.length >= 3 ? 'Three chosen. Tap one to remove it.' : 'Pick up to three; the first is the main one.'}</div>
      <div class="pad"><div class="gpick">${all.map(g => { const i = picked.indexOf(g); return `<button type="button" class="chip" data-g="${esc(g)}" aria-pressed="${i >= 0}"${i < 0 && picked.length >= 3 ? ' aria-disabled="true"' : ''}>${i >= 0 ? `<span class="n">${i + 1}</span>` : ''}${esc(g)}</button>`; }).join('')}</div></div>
      <button type="button" id="g-save" ${picked.length ? '' : 'disabled'}>Save</button></div>
    <div class="group"><button type="button" class="cancel" data-act="close-sheet">Cancel</button></div></div>`;
  overlay.querySelectorAll('[data-g]').forEach(b => b.addEventListener('click', () => {
    const g = b.dataset.g, i = picked.indexOf(g);
    if (i >= 0) picked.splice(i, 1); else if (picked.length < 3) picked.push(g); else return;
    genreForm(a, picked);
  }));
  $('#g-save').addEventListener('click', async () => {
    const before = a.genres.slice(), next = picked.slice();
    closeSheet();
    try {
      await setGenres(a.id, next);
      a.genres = next; index(); persist(); render(false);
      toast('Genres saved', async () => { try { await setGenres(a.id, before); a.genres = before; index(); persist(); render(false); } catch (e) { toast(errText(e)); } });
    } catch (e) { toast(errText(e)); }
  });
}

/* ---------- Crate Stats: the weekly report as a dashboard ---------- */
/* The weekly sync works the numbers out (it has Last.fm, so it knows what you actually listened to) and
   leaves them as JSON in the App data toggle of the Crate report page. Crate reads that, keeps a copy for
   offline, and adds what this phone has logged since. Feature names follow FEATURES in
   sync/musiclib/report.py. */
const FEATURE_NAMES = {
  pick: "Today's Pick", rediscover: 'Rediscover', 'years-ago': 'This Month, Years Ago', 'on-repeat': 'On Repeat',
  missed: 'Albums You Missed', 'start-to-finish': 'Start to Finish', revisit: 'Albums to Revisit', 'browse-library': 'Library',
  library: 'Library', gartists: 'Genre: all artists', galbums: 'Genre: all albums', 'your-years': 'Your Years', year: 'Year pages',
  month: 'Month pages', genres: 'Genres', genre: 'Genre pages', 'genre-essentials': 'Genre: Your Essentials', 'genre-songs': 'Genre: Top Songs',
  'genre-albums': 'Genre: Albums', 'genre-rediscover': 'Genre: Rediscover', 'genre-years': 'Genre: Your Years', heat: 'Heat',
  new: 'New Releases', 'new-top': 'New Releases: top pick', 'coming-soon': 'Coming Soon', release: 'Release page',
  'release-favourites': 'Release: your favourites', 'release-albums': 'Release: your albums', search: 'Search', artist: 'Artist page',
  'artist-songs': 'Artist: Top Songs', 'artist-albums': 'Artist: Your Albums', similar: 'Similar in Your Library',
  'artist-releases': 'Artist: New Releases', album: 'Album page', 'album-favourites': 'Album: Your Favourites', 'album-more': 'Album: More by',
  songs: 'Top Songs (all)', repeat: 'On Repeat (all)', whole: 'Start to Finish (all)', player: 'Player', home: 'Home'
};
const featName = k => FEATURE_NAMES[k] || k || 'Unknown';
let statsCache = ls.get('crate.stats') || null, statsLoading = false, statsTried = 0;
const statsFresh = () => statsCache && Date.now() - statsCache.at < 6 * 3600e3;
async function loadStats() {
  if (statsLoading || !token() || !DS.report) return;
  statsLoading = true; statsTried = Date.now();
  try {
    const blocks = []; let cursor = null, guard = 0;
    do {
      const d = await napi(`/blocks/${DS.report}/children?page_size=100${cursor ? '&start_cursor=' + encodeURIComponent(cursor) : ''}`, { read: true });
      blocks.push(...(d.results || [])); cursor = d.has_more ? d.next_cursor : null;
    } while (cursor && ++guard < 5);
    const head = blocks.find(b => b.type === 'heading_3' && b.heading_3.is_toggleable && rt(b.heading_3.rich_text).trim() === 'App data');
    if (head) {
      const d = await napi(`/blocks/${head.id}/children?page_size=10`, { read: true });
      const code = (d.results || []).find(b => b.type === 'code');
      const data = code ? JSON.parse(rt(code.code.rich_text)) : null;
      if (data && data.v === 1) { statsCache = { data, at: Date.now() }; ls.set('crate.stats', statsCache); }
    }
  } catch (e) { console.warn('Crate stats not loaded', e); }
  statsLoading = false;
  if (route().view === 'stats') render(false);
}
const pct = (x, y) => y ? Math.round(100 * x / y) + '%' : '–';
const monthShort = ym => monthLabel(ym, 'short').split(' ')[0];
const monthRead = m => `${monthLabel(m.m, 'long')}${m.partial ? ' so far' : ''}: ${plural(m.n, 'artist')} back${m.crate ? `, ${nf(m.via)} through Crate` : ''}${m.ripe && m.n ? `, ${nf(m.stuck)} stuck` : ''}`;
/* Rediscoveries per month: through Crate (accent) under the other comebacks (grey), the average before
   Crate as a dashed line. Tap a month for its numbers. */
function monthChart(months, base) {
  const H = 140, max = Math.max(1, base || 0, ...months.map(m => m.n)), px = n => n ? Math.max(2, Math.round(n / max * H)) : 0;
  const first = months.findIndex(m => m.crate);
  const cols = months.map((m, i) => {
    const via = px(m.via), other = px(m.n - m.via);
    return `<button class="mc-col${i === first && i > 0 ? ' first-crate' : ''}" data-act="mc" data-i="${i}" aria-pressed="${i === months.length - 1}" aria-label="${esc(monthRead(m))}">
      <span class="mc-stack">${other ? `<i style="height:${other}px"></i>` : ''}${via ? `<i class="via" style="height:${via}px"></i>` : ''}</span><span class="mc-x">${esc(monthShort(m.m))}</span></button>`;
  }).join('');
  const line = base ? `<i class="mc-base" style="bottom:${px(base)}px" aria-hidden="true"></i>` : '';
  return `<div class="mchart">${line}${cols}</div>
    <p class="mc-read" aria-live="polite">${esc(monthRead(months[months.length - 1]))}</p>
    <div class="mc-legend"><span><i class="via"></i>Through Crate</span><span><i></i>Other comebacks</span>${base ? `<span><i class="base"></i>Average before Crate (${esc(String(base))})</span>` : ''}</div>`;
}
/* One row per feature: how much you use it (opens, Plays) and how well it works (heard, back after a
   quiet year, stuck). The bar is its Plays, the accent part the ones you actually listened to. */
function featureRows(list) {
  const maxP = Math.max(1, ...list.map(f => f.played || 0));
  return `<ul class="list flist">${list.map(f => {
    const use = [f.opened ? plural(f.opened, 'open') : '', f.played ? plural(f.played, 'Play') : '', f.heard ? nf(f.heard) + ' heard' : ''].filter(Boolean).join(' · ')
      || (f.seen ? plural(f.seen, 'pick') + ' shown' : '');
    const bar = f.played ? `<div class="fbar" role="img" aria-label="${f.heard || 0} of ${f.played} Plays heard">${f.heard ? `<i class="via" style="width:${f.heard / maxP * 100}%"></i>` : ''}${f.played > (f.heard || 0) ? `<i style="width:${(f.played - (f.heard || 0)) / maxP * 100}%"></i>` : ''}</div>` : '';
    const more = [f.seen && f.played ? plural(f.seen, 'pick') + ' shown' : '', f.matured ? `stuck ${f.stuck} of ${f.matured}` : ''].filter(Boolean).join(' · ');
    return `<li><div class="row frow"><div class="txt"><div class="t1">${esc(f.label || featName(f.k))}</div><div class="t2">${esc(use)}</div>${bar}${more ? `<div class="t3">${esc(more)}</div>` : ''}</div>
      ${f.found != null ? `<span class="end fnum"><b>${nf(f.found)}</b><span>back</span></span>` : ''}</div></li>`;
  }).join('')}</ul>`;
}
/* What this phone logged (opens and Plays per feature), for the days the weekly report hasn't covered. */
function localUse(since = 0) {
  const by = new Map();
  for (const e of logHist) {
    if (e.t * 1000 < since || (e.e !== 'open' && e.e !== 'play')) continue;
    const f = by.get(e.s) || { k: e.s, label: featName(e.s), opened: 0, played: 0 };
    e.e === 'open' ? f.opened++ : f.played++;
    by.set(e.s, f);
  }
  return [...by.values()].sort((a, b) => b.opened + b.played - a.opened - a.played);
}
function statsView() {
  const d = statsCache && statsCache.data;
  const after = () => { if (!statsFresh() && navigator.onLine && Date.now() - statsTried > 60e3) loadStats(); };   // a failed load waits a minute
  let html = `<h1 class="ltitle">Crate Stats</h1><p class="lsub">How Crate helps you rediscover music${d ? ' · updated ' + esc(dayMonth(d.updated)) : ''}</p>`;
  if (!d) {
    const mine = localUse();
    html += statsLoading ? `<div class="empty"><h3>Loading…</h3></div>`
      : `<div class="empty"><div class="big">${I.flame}</div><h3>No report yet</h3><p>The weekly sync writes the first Crate report on Monday morning, with what you actually listened to. Until then, this is what this phone has logged.</p></div>`;
    if (mine.length) html += `<section class="section">${shelfHead('So Far', 'Opens and Plays on this phone')}${featureRows(mine)}</section>`;
    return { title: 'Crate Stats', tab: 'home', html, after };
  }
  const months = d.months || [], last = months[months.length - 1] || { m: today().slice(0, 7), n: 0, via: 0 };
  // Early in a month its count says little: lead with the last full month until the 15th.
  const cur = last.partial && Number(String(d.updated).slice(8, 10)) < 15 && months.length > 1 ? months[months.length - 2] : last;
  const b = d.baseline || {}, u = d.usage, feats = d.features || [];
  const stuck = feats.reduce((s, f) => s + (f.stuck || 0), 0), matured = feats.reduce((s, f) => s + (f.matured || 0), 0);
  html += `<div class="st-hero"><span>Back after a quiet year · ${esc(monthLabel(cur.m, 'long'))}${cur.partial ? ' so far' : ''}</span><b>${nf(cur.n)}</b>
      <span>${b.per_month != null ? `Before Crate: ${esc(String(b.per_month))} a month, ${Math.round((b.stick || 0) * 100)}% stuck` : ''}</span></div>
    <div class="st-tiles">
      <div class="st-tile"><span>Through Crate</span><b>${nf(cur.via || 0)}</b><small>of ${nf(cur.n)} in ${esc(monthShort(cur.m))}</small></div>
      <div class="st-tile"><span>Heard</span><b>${u ? pct(u.heard, u.played) : '–'}</b><small>${u ? `${nf(u.heard)} of ${plural(u.played, 'Play')}` : 'No Plays yet'}</small></div>
      <div class="st-tile"><span>Stuck</span><b>${pct(stuck, matured)}</b><small>${matured ? `${nf(stuck)} of ${plural(matured, 'artist')} after 6 weeks` : 'Shows after 6 weeks'}</small></div></div>`;
  if (months.length) html += `<section class="section">${shelfHead('Month by Month', 'Artists back after more than a year')}${monthChart(months, b.per_month)}</section>`;
  if (u) {
    const tab = state.statsTab === 'features' ? 'features' : 'starts';
    html += `<section class="section">${shelfHead('Features', `Since ${dayMonth(u.since)}: ${plural(u.opened, 'open')}, ${plural(u.played, 'Play')}, ${nf(u.found)} back`)}
      <div class="seg" role="group" aria-label="Count by" style="margin-top:0;margin-bottom:6px"><button data-act="stats-tab" data-tab="starts" aria-pressed="${tab === 'starts'}">Where You Started</button><button data-act="stats-tab" data-tab="features" aria-pressed="${tab === 'features'}">Where You Tapped</button></div>
      ${featureRows(tab === 'starts' ? d.starts || [] : feats)}</section>`;
  }
  if ((d.back || []).length) html += `<section class="section">${shelfHead('Back from the Quiet', 'Rediscovered through Crate')}<ul class="list">${d.back.map(x => {
    const a = IX.artistById.get(x.a), al = x.i ? IX.albumById.get(x.i) : null, href = al ? '#/album/' + al.id : a ? '#/artist/' + a.id : '';
    return `<li><${href ? `a href="${href}"` : 'div'} class="row">${art(al ? al.cover : a ? a.image : '', x.name, { circle: !al })}<div class="txt"><div class="t1">${esc(x.name)}${x.album ? ' – ' + esc(x.album) : ''}</div><div class="t2">${esc(featName(x.s))} · ${esc(dayMonth(x.d))}</div></div>${href ? '</a>' : '</div>'}</li>`;
  }).join('')}</ul></section>`;
  const since = localUse(new Date(d.updated + 'T00:00:00').getTime()), opens = since.reduce((s, f) => s + f.opened, 0), plays = since.reduce((s, f) => s + f.played, 0);
  html += `<p class="foot" style="margin-top:22px">${opens || plays ? `Since this report, on this phone: ${plural(opens, 'open')}, ${plural(plays, 'Play')}. Next Monday's report adds whether you listened. ` : ''}Heard: Last.fm or Crate's player had the artist within 3 hours of the Play. Back: heard after a year without plays. Stuck: more of that artist 1 to 6 weeks later. The full report is the Crate report page in Notion.</p>`;
  return { title: 'Crate Stats', tab: 'home', html, after };
}

/* ---------- Settings & setup ---------- */
function settingsView() {
  const nBlocked = state.blocks.length + state.excluded.artists.length + state.excluded.albums.length;
  const when = t => new Date(t).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' });
  const spotify = !isNative ? 'Opens links' : !SR ? 'Not in this build' : !srId() ? 'Add your client ID' : srReady && !srError ? 'Connected' : srError ? 'Not connected' : 'Connects on first Play';
  const html = `<h1 class="ltitle">Settings</h1>
    <div class="inset"><div class="cell"><span>Library</span><span class="v">${plural(IX.artists.length, 'artist')}, ${plural(IX.albums.length, 'album')}</span></div>
      <div class="cell"><span>Last synced</span><span class="v">${state.syncedAt ? when(state.syncedAt) : 'Never'}</span></div>
      <button class="cell action" data-act="sync-full" style="width:100%">${state.syncing ? 'Syncing…' : 'Reload everything'}</button></div>
    ${DS.report ? `<div class="inset"><a class="cell" href="#/stats"><span>Crate Stats</span><span class="v">${statsCache && statsCache.data && statsCache.data.months && statsCache.data.months.length ? plural(statsCache.data.months[statsCache.data.months.length - 1].n, 'comeback') + ' this month' : ''}</span>${I.chevSm}</a></div>` : ''}
    <div class="inset"><a class="cell" href="#/blocklist"><span>Blocklist</span><span class="v">${nBlocked || ''}</span>${I.chevSm}</a></div>
    <div class="inset"><div class="cell"><span>Spotify</span><span class="v">${spotify}</span></div>
      ${srError ? `<div class="cell"><span class="v" style="margin:0;text-align:left">${esc(srError)}</span></div>` : ''}
      ${SR ? `<div class="cell"><input id="spid" placeholder="Spotify client ID" autocomplete="off" autocapitalize="off" spellcheck="false" value="${esc(srId())}"></div>
      <button class="cell action" data-act="sr-save" style="width:100%">${srId() ? 'Save and connect' : 'Save'}</button>` : ''}</div>
    <p class="foot">Play buttons ask the Spotify app to play while you stay in Crate. Add the Client ID of your Spotify developer app (not the secret); Spotify asks once to approve Crate. Without it, Crate opens Spotify instead.</p>
    <div class="inset"><div class="cell"><span>Notion</span><span class="v">${connected() ? 'Connected' : 'Not connected'}</span></div>
      <a class="cell action" href="#/setup">${token() ? 'Use a different key' : 'Connect'}</a></div>
    <p class="foot">Crate reads your Crate databases in Notion with a key that stays on this phone. It only writes statuses (a release you've heard, something you block), Blocklist rows and its usage log.</p>
    ${DS.log ? `<div class="inset"><div class="cell"><span>Usage log</span><span class="v">${logQ.length ? plural(logQ.length, 'event') + ' waiting' : ls.get('crate.logSent') ? 'Sent ' + when(ls.get('crate.logSent')) : 'Nothing yet'}</span></div></div>
    <p class="foot">What Crate shows you and what you play from it go to the Crate log in your Notion, for the weekly Crate report. Only IDs, no names, and nothing goes anywhere else.</p>` : ''}
    <div class="inset"><button class="cell danger" data-act="clear-cache" style="width:100%">Clear saved artwork</button></div>
    <div class="inset"><label class="cell"><span>Save all artwork on mobile data</span><input type="checkbox" class="switch" data-act="cell-art" ${ls.get('crate.cellArt') ? 'checked' : ''}></label></div>
    <p class="foot">Artwork is saved on the phone so the library works offline (about 100 MB for a big library). By default that happens on Wi-Fi only; pictures you scroll past are always saved.</p>`;
  return { title: 'Settings', tab: 'home', html };
}
function saveSpotifyId() {
  const v = ($('#spid') ? $('#spid').value : '').trim();
  if (v && !/^[0-9a-f]{32}$/i.test(v)) { toast("That doesn't look like a Spotify client ID (32 letters and digits)"); return; }
  cfg.spotifyId = v; saveCfg(); srReady = null; srError = '';
  if (!v) { if (SR) SR.disconnect().catch(() => { }); toast('Spotify client ID removed'); render(false); return; }
  srConnect().then(() => toast('Connected to Spotify'), e => toast(e && e.code === 'BrowserLogin' ? 'Approve Crate in the browser, then come back' : "Saved. Couldn't connect to Spotify yet")).finally(() => render(false));
}

/* ---------- connecting Notion: find the Crate databases by their columns ---------- */
/* The Set up Notion workflow creates them (with the same marker columns as sync/musiclib/notion.py),
   so renaming them in Notion is fine. Two of a kind is something you sort out: Crate never guesses. */
const MARKERS = {
  artists: ['Top songs', 'Plays by month', 'Aliases'], albums: ['Full listens', 'Distinct tracks played'],
  releases: ['Found on', 'Track list'], blocklist: ['Type:select', 'Artist:rich_text'], log: ['Events', 'Data', 'Sent']
};
const NAMES = { artists: 'Artists', albums: 'Albums', releases: 'New Releases', blocklist: 'Blocklist', log: 'Crate log' };
const looksLike = (props, k) => MARKERS[k].every(m => { const [n, t] = m.split(':'); return props[n] && (!t || props[n].type === t); });
async function searchAll(value, query = '') {
  const out = []; let cursor = null;
  do {
    const d = await napi('/search', { method: 'POST', body: { filter: { property: 'object', value }, page_size: 100, ...(query ? { query } : {}), ...(cursor ? { start_cursor: cursor } : {}) }, read: true });
    out.push(...(d.results || []).filter(x => !x.in_trash && !x.archived));
    cursor = d.has_more ? d.next_cursor : null;
  } while (cursor && out.length < 1000);
  return out;
}
const pageTitle = p => { const t = Object.values(p.properties || {}).find(x => x.type === 'title'); return t ? rt(t.title) : ''; };
async function findDatabases() {
  const found = {};
  for (const ds of await searchAll('data_source')) {
    const props = ds.properties || (await napi(`/data_sources/${ds.id}`, { read: true })).properties || {};
    for (const k of Object.keys(MARKERS)) if (looksLike(props, k)) (found[k] = found[k] || []).push(cleanId(ds.id));
  }
  const report = (await searchAll('page', 'Crate report')).find(p => pageTitle(p).trim().toLowerCase() === 'crate report');
  return { found, report: report ? cleanId(report.id) : '' };
}
function setupView() {
  const html = `<div class="setup-hero"><div class="logo">${I.record}</div><h1>Crate</h1><p>Paste the key of your Notion integration. Crate finds the databases that the <b>Set up Notion</b> step in your GitHub copy of Crate created. The key stays on this phone.</p></div>
    <div class="inset"><div class="cell"><input id="tok" type="password" placeholder="ntn_… or secret_…" autocomplete="off" autocapitalize="off" spellcheck="false" value=""></div></div>
    <div id="setup-err"></div>
    <div class="actions"><button class="btn primary" data-act="connect">Connect</button></div>
    ${connected() ? `<div class="actions"><a class="btn" href="#/">Cancel</a></div>` : ''}
    <p class="foot" style="margin-top:22px">New to Crate? The setup guide (docs/SETUP.md in the Crate repository on GitHub) takes you from a Spotify data request to this screen.</p>`;
  return { title: 'Connect', root: !connected(), noTabs: !connected(), tab: '', html };
}
async function connect() {
  const v = ($('#tok').value || '').trim(), errBox = $('#setup-err');
  const say = msg => { errBox.innerHTML = `<p class="err">${esc(msg)}</p>`; };
  if (!v) { say('Paste the key first.'); return; }
  const old = cfg.token; cfg.token = v;
  errBox.innerHTML = '<p class="foot">Looking for your Crate databases…</p>';
  try {
    const { found, report } = await findDatabases();
    const need = ['artists', 'albums', 'releases', 'blocklist'];
    const missing = need.filter(k => !found[k]), twice = Object.keys(found).filter(k => found[k].length > 1);
    if (missing.length) { cfg.token = old; say(`Crate can't find ${missing.map(k => NAMES[k]).join(', ')}. Run the Set up Notion workflow in your GitHub copy of Crate, and check this integration can see the page that holds the databases (••• → Connections).`); return; }
    if (twice.length) { cfg.token = old; say(`This integration can see more than one ${twice.map(k => NAMES[k]).join(' and ')} database. Remove it from the copies Crate shouldn't use (••• → Connections), then try again.`); return; }
    const ids = { artists: found.artists[0], albums: found.albums[0], releases: found.releases[0], blocklist: found.blocklist[0], log: (found.log || [])[0] || '', report };
    const changed = ids.artists !== DS.artists;
    Object.assign(DS, ids); cfg.ds = ids; saveCfg();
    if (changed) { propIds = { _sig: FIELDS_SIG }; ls.set('crate.propIds', propIds); statsCache = null; ls.del('crate.stats'); logQ = []; ls.set('crate.log', logQ); }
    stack.length = 0; location.replace('#/'); sync({ full: true });
  } catch (e) { cfg.token = old; say(errText(e)); }
}

/* ---------- sheets & toasts ---------- */
function sheet(head, actions) {
  overlay.innerHTML = `<div class="scrim" data-act="close-sheet"></div><div class="sheet" role="dialog" aria-modal="true"><div class="group">${head ? `<div class="head">${head}</div>` : ''}${actions.map((a, i) => `<button data-sheet="${i}" class="${a.cls || ''}">${esc(a.label)}</button>`).join('')}</div><div class="group"><button class="cancel" data-act="close-sheet">Cancel</button></div></div>`;
  overlay.querySelectorAll('[data-sheet]').forEach(b => b.addEventListener('click', () => { const a = actions[Number(b.dataset.sheet)]; closeSheet(); a.run(); }));
}
const closeSheet = () => { overlay.innerHTML = ''; };
let toastTimer = null;
function toast(msg, undo) {
  document.querySelectorAll('.toast').forEach(t => t.remove());
  const t = document.createElement('div'); t.className = 'toast'; t.setAttribute('role', 'status');
  t.innerHTML = `<span>${esc(msg)}</span>${undo ? '<button>Undo</button>' : ''}`;
  if (undo) t.querySelector('button').addEventListener('click', () => { t.remove(); undo(); });
  document.body.appendChild(t);
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.remove(), undo ? 5000 : 3200);
}

/* ---------- clicks ---------- */
document.addEventListener('click', e => {
  const tab = e.target.closest('[data-tab]');
  if (tab && tab.closest('#tabs')) { e.preventDefault(); goTab(tab.dataset.tab); return; }
  const el = e.target.closest('[data-act]'); if (!el) return;
  const row = el.closest('.row'); if (row && row.dataset.swiped) { e.preventDefault(); return; }
  const v = currentView || {};
  switch (el.dataset.act) {
    case 'back': goBack(); break;
    case 'sync': sync(); break;
    case 'sync-full': sync({ full: true }); break;
    case 'gtab': state.tab = el.dataset.tab; state.era = 'all'; render(false); break;
    case 'era': state.era = el.dataset.era; render(false); break;
    case 'bio': state.bioOpen = !state.bioOpen; render(false); break;
    case 'clear-q': e.preventDefault(); state.q = ''; $('#q').value = ''; updateResults(); $('#q').focus(); break;
    case 'spotify-artist': if (v.artist) played('artist', v.artist.id, v.artist); openSpotifyArtist(v.artist); break;
    case 'release-spotify': { const r = state.releases.find(x => x.id === el.dataset.id); if (r) played('release', r.id, relArtist(r), r); if (r) openSpotify('album', r.spotify, `${r.name} ${r.credited || (relArtist(r) || {}).name || ''}`); break; }
    case 'new-genre': state.newGenre = el.dataset.g; ls.set('crate.newGenre', state.newGenre); render(false); break;
    case 'spotify-album': if (v.album) played('album', v.album.id, IX.artistById.get(v.album.artistId), v.album); openSpotifyAlbum(v.album); break;
    case 'pick-play': {
      const x = el.dataset.album ? IX.albumById.get(el.dataset.id) : IX.artistById.get(el.dataset.id);
      if (x) played(el.dataset.album ? 'album' : 'artist', x.id, el.dataset.album ? IX.artistById.get(x.artistId) : x, x);
      el.dataset.album ? openSpotifyAlbum(x) : openSpotifyArtist(x); break;
    }
    case 'song': played('track', el.dataset.t, IX.artistById.get(el.dataset.a)); openSpotify('track', el.dataset.t, el.dataset.q); break;
    case 'mini-open': case 'np-close': case 'np-toggle': case 'np-next': case 'np-prev': case 'np-shuffle': case 'np-repeat':
    case 'np-like': case 'np-artist': case 'np-album': case 'np-spotify': playerAction(el.dataset.act); break;
    case 'sr-connect': srReady = null; srConnect().then(() => toast('Connected to Spotify'), e => toast(e && e.code === 'BrowserLogin' ? 'Approve Crate in the browser, then come back' : "Couldn't connect to Spotify")).finally(() => render(false)); break;
    case 'hometab': state.homeTab = el.dataset.tab; ls.set('crate.homeTab', state.homeTab); render(false); break;
    case 'heat-level': state.heatLevel = Number(el.dataset.h); ls.set('crate.heatLevel', state.heatLevel); render(false); break;
    case 'heat-toggle': toggleHeat(Number(el.dataset.h)); break;
    case 'heat-clear': state.heatFilter = []; ls.set('crate.heatFilter', []); render(false); break;
    case 'year-genre': replaceRoute('#/year/' + el.dataset.y + (el.dataset.g ? '/' + encodeURIComponent(el.dataset.g) : '')); break;
    case 'heat-kind': state.heatKind = el.dataset.k; ls.set('crate.heatKind', state.heatKind); render(false); break;
    case 'atab': state.artistTab = el.dataset.tab; render(false); break;
    case 'stats-tab': state.statsTab = el.dataset.tab; render(false); break;
    case 'mc': {
      const m = ((statsCache && statsCache.data && statsCache.data.months) || [])[Number(el.dataset.i)];
      if (!m) break;
      app.querySelectorAll('.mc-col').forEach(c => c.setAttribute('aria-pressed', String(c === el)));
      const read = app.querySelector('.mc-read'); if (read) read.textContent = monthRead(m);
      break;
    }
    case 'rel-status': changeRelease(el.dataset.id, el.dataset.status).then(() => render(false)); break;
    case 'artist-menu': if (v.artist) sheet(`<b>${esc(v.artist.name)}</b>Blocking removes the artist from Crate and the weekly sync won't add them again.`, [
      { label: 'Open in Spotify', run: () => { played('artist', v.artist.id, v.artist); openSpotifyArtist(v.artist, { app: true }); } },
      { label: 'Edit Genres', run: () => genreForm(v.artist) },
      { label: 'Block Artist', cls: 'destructive', run: () => blockArtist(v.artist) }]); break;
    case 'album-menu': if (v.album) sheet(`<b>${esc(v.album.name)}</b>Blocking removes the album from Crate and the weekly sync won't add it again.`, [
      { label: 'Open in Spotify', run: () => { played('album', v.album.id, IX.artistById.get(v.album.artistId), v.album); openSpotifyAlbum(v.album, { app: true }); } },
      { label: 'Block Album', cls: 'destructive', run: () => blockAlbum(v.album) }]); break;
    case 'genre-menu': if (v.genre) sheet(`<b>${esc(v.genre)}</b>Blocking hides artists whose main genre this is, and the weekly sync stops adding them.`, [
      { label: 'Block Genre', cls: 'destructive', run: () => blockOther('Genre', v.genre) }]); break;
    case 'block-add': blockForm(); break;
    case 'sort-menu': sheet('<b>Sort by</b>', Object.entries(SORTS).map(([k, [label]]) => ({
      label: (k === state.sort ? '✓ ' : '') + label, cls: k === state.sort ? 'current' : '',
      run: () => { state.sort = k; ls.set('crate.sort', k); render(false); }
    }))); break;
    case 'unblock': {
      const { id, kind } = el.dataset;
      if (kind) unblockExcluded(kind, id, null);
      else { const b = state.blocks.find(x => x.id === id); if (b) unblockRow(b); }
      break;
    }
    case 'close-sheet': closeSheet(); break;
    case 'connect': connect(); break;
    case 'sr-save': saveSpotifyId(); break;
    case 'cell-art': ls.set('crate.cellArt', el.checked); if (el.checked) prefetchImages(); break;
    case 'clear-cache': idb.clear('img').then(() => { cachedKeys = new Set(); objUrls.forEach(u => URL.revokeObjectURL(u)); objUrls.clear(); queued.clear(); toast('Saved artwork cleared'); prefetchImages(); }); break;
  }
});

/* ---------- pull to refresh ---------- */
(() => {
  let y0 = null, pulled = 0;
  ptr.innerHTML = I.refresh;
  window.addEventListener('touchstart', e => { y0 = window.scrollY <= 0 && !overlay.innerHTML ? e.touches[0].clientY : null; pulled = 0; }, { passive: true });
  window.addEventListener('touchmove', e => {
    if (y0 == null) return;
    pulled = window.scrollY > 0 ? 0 : Math.max(0, e.touches[0].clientY - y0);
    const p = Math.min(pulled, 110);
    ptr.style.opacity = Math.min(1, p / 70); ptr.style.transform = `translateY(${p / 2 - 40}px) rotate(${p * 3}deg)`;
  }, { passive: true });
  window.addEventListener('touchend', () => {
    if (y0 == null) return;
    if (pulled > 80 && token()) { ptr.classList.add('go'); sync().finally(() => { ptr.classList.remove('go'); ptr.style.opacity = 0; ptr.style.transform = ''; }); }
    else { ptr.style.opacity = 0; ptr.style.transform = ''; }
    y0 = null;
  });
})();

/* ---------- start ---------- */
if (AppP) {
  AppP.addListener('backButton', () => { if (np.classList.contains('open')) closeNP(); else if (overlay.innerHTML) closeSheet(); else if (stack.length > 1) history.back(); else AppP.exitApp(); });
  AppP.addListener('resume', () => { if (stale()) sync(); else { sendLog(); if (route().view === 'home') render(false); } });   // daily picks roll over
}
window.addEventListener('online', () => { if (state.err && state.err.code === 'offline') sync(); });
stack.push({ hash: route().hash, title: '', y: 0 });
restore().then(() => {
  render();
  if (token() && (!state.artists.length || stale())) sync();
  else if (token()) { prefetchImages(); sendLog(); }
});
