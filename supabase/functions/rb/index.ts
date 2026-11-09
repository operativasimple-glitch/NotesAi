// Radar Ballena — servidor de avisos 24/7 (Supabase Edge Function "rb").
//
// · La app llama aquí para registrar el móvil (suscripción push) y mandar la lista de carteras que sigue.
// · Una tarea programada (pg_cron) llama cada minuto con action "check": se revisan las carteras
//   (posiciones en Hyperliquid y movimientos de tokens en Ethereum) y se mandan los avisos.

import { createClient } from 'jsr:@supabase/supabase-js@2';
import { generateVapidKeys, sendPush, type VapidKeys } from './push.ts';

const db = createClient(Deno.env.get('SUPABASE_URL')!, Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!, {
  auth: { persistSession: false }
});

const APP = 'https://operativasimple-glitch.github.io/NotesAi/radar/';
const HL = 'https://api.hyperliquid.xyz/info';
// RPC públicos y gratuitos que permiten buscar transferencias por cartera (eth_getLogs solo por topics)
const ETH_RPCS = ['https://eth.drpc.org', 'https://gateway.tenderly.co/public/mainnet', 'https://rpc.mevblocker.io'];
const TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';
// servicios de push de Apple, Google (Chrome/Android), Mozilla y Microsoft
const PUSH_HOSTS = /(^|\.)(push\.apple\.com|fcm\.googleapis\.com|jmt17\.google\.com|push\.services\.mozilla\.com|notify\.windows\.com)$/;
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'content-type, authorization, apikey, x-client-info',
  'Access-Control-Allow-Methods': 'POST, OPTIONS'
};

const json = (d: unknown, status = 200) =>
  new Response(JSON.stringify(d), { status, headers: { ...CORS, 'Content-Type': 'application/json' } });
const isUuid = (s: unknown) => typeof s === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
const isAddr = (s: unknown) => typeof s === 'string' && /^0x[0-9a-fA-F]{40}$/.test(s);

/* ---------- formato en español ---------- */
const nf = (max: number) => new Intl.NumberFormat('es-ES', { maximumFractionDigits: max });
function usd(v: number): string {
  const s = v < 0 ? '−' : '';
  v = Math.abs(v);
  let t: string;
  if (v < 1e3) t = nf(0).format(v);
  else if (v < 1e6) t = nf(v < 1e4 ? 1 : 0).format(v / 1e3) + ' mil';
  else if (v < 1e9) t = nf(v < 1e8 ? 1 : 0).format(v / 1e6) + ' M';
  else t = nf(2).format(v / 1e9) + ' mil M';
  return s + '$' + t;
}
function px(p: number): string {
  const a = Math.abs(p);
  return nf(a >= 1000 ? 0 : a >= 100 ? 1 : a >= 1 ? 2 : a >= 0.01 ? 4 : 6).format(p);
}
function amt(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return nf(2).format(v / 1e9) + ' mil M';
  if (a >= 1e6) return nf(2).format(v / 1e6) + ' M';
  return nf(a >= 100 ? 0 : a >= 1 ? 2 : 6).format(v);
}

/* ---------- configuración guardada ---------- */
async function getCfg(key: string): Promise<string | null> {
  const { data } = await db.from('rb_config').select('value').eq('key', key).maybeSingle();
  return data ? data.value : null;
}
let vapidCache: VapidKeys | null = null;
async function vapid(): Promise<VapidKeys> {
  if (vapidCache) return vapidCache;
  let v = await getCfg('vapid');
  if (!v) {
    // la primera vez se crean las claves aquí mismo: la privada nunca sale de la base de datos
    const k = await generateVapidKeys();
    await db.from('rb_config').upsert({ key: 'vapid', value: JSON.stringify(k) }, { onConflict: 'key', ignoreDuplicates: true });
    v = await getCfg('vapid');
  }
  vapidCache = JSON.parse(v!);
  return vapidCache!;
}

/* ---------- envío de avisos ---------- */
type Device = { id: string; endpoint: string; p256dh: string; auth: string; fails: number };
async function pushTo(dev: Device, data: unknown): Promise<number> {
  let status = 0;
  try { status = await sendPush(dev, data, await vapid(), APP); } catch (_) { status = 0; }
  if (status === 404 || status === 410) {
    await db.from('rb_devices').delete().eq('id', dev.id); // suscripción caducada
  } else if (status >= 200 && status < 300) {
    if (dev.fails) await db.from('rb_devices').update({ fails: 0 }).eq('id', dev.id);
  } else {
    const fails = (dev.fails || 0) + 1;
    if (fails > 30) await db.from('rb_devices').delete().eq('id', dev.id);
    else await db.from('rb_devices').update({ fails }).eq('id', dev.id);
  }
  return status;
}

/* ---------- API para la app ---------- */
async function api(body: Record<string, any>): Promise<Response> {
  const a = body.action;
  if (a === 'config') return json({ vapid: (await vapid()).publicKey });

  if (a === 'register') {
    const s = body.sub || {};
    const keys = s.keys || {};
    let host = '';
    try { const u = new URL(s.endpoint); host = u.protocol === 'https:' ? u.hostname : ''; } catch (_) { /* inválida */ }
    if (!PUSH_HOSTS.test(host) || typeof keys.p256dh !== 'string' || typeof keys.auth !== 'string') {
      return json({ error: 'Suscripción no válida' }, 400);
    }
    const { data, error } = await db.from('rb_devices').upsert({
      endpoint: s.endpoint, p256dh: keys.p256dh, auth: keys.auth,
      ua: String(body.ua || '').slice(0, 200), fails: 0, last_seen: new Date().toISOString()
    }, { onConflict: 'endpoint' }).select('id').single();
    if (error) return json({ error: error.message }, 500);
    return json({ device: data.id });
  }

  if (!isUuid(body.device)) return json({ error: 'Falta el dispositivo' }, 400);
  const { data: dev } = await db.from('rb_devices').select('id,endpoint,p256dh,auth,fails').eq('id', body.device).maybeSingle();
  if (!dev) return json({ error: 'Dispositivo no registrado', gone: true }, 404);
  await db.from('rb_devices').update({ last_seen: new Date().toISOString() }).eq('id', dev.id);

  if (a === 'follows') {
    const list = (Array.isArray(body.follows) ? body.follows : []).slice(0, 300)
      .filter((f: any) => f && isAddr(f.address))
      .map((f: any) => ({
        device_id: dev.id, address: f.address.toLowerCase(),
        chain: f.chain === 'eth' ? 'eth' : 'hl', label: String(f.label || '').slice(0, 60)
      }));
    const uniq = new Map(list.map((f: any) => [f.chain + ':' + f.address, f]));
    await db.from('rb_follows').delete().eq('device_id', dev.id);
    if (uniq.size) {
      const { error } = await db.from('rb_follows').insert([...uniq.values()]);
      if (error) return json({ error: error.message }, 500);
    }
    return json({ ok: true, count: uniq.size });
  }

  if (a === 'events') {
    const { data: fl } = await db.from('rb_follows').select('address,chain').eq('device_id', dev.id);
    if (!fl || !fl.length) return json({ events: [] });
    const since = typeof body.since === 'number' && body.since > 0 ? new Date(body.since).toISOString()
      : new Date(Date.now() - 48 * 3600e3).toISOString();
    const { data: ev } = await db.from('rb_events').select('id,address,chain,title,body,kind,created_at')
      .in('address', [...new Set(fl.map(f => f.address))]).gt('created_at', since)
      .order('created_at', { ascending: false }).limit(100);
    const want = new Set(fl.map(f => f.chain + ':' + f.address));
    return json({ events: (ev || []).filter(e => want.has(e.chain + ':' + e.address)) });
  }

  if (a === 'test') {
    const st = await pushTo(dev as Device, {
      title: '✅ Avisos activados',
      body: 'Radar Ballena te avisará aunque tengas la app cerrada.',
      url: APP, tag: 'rb-test'
    });
    return json({ ok: st >= 200 && st < 300, status: st });
  }

  if (a === 'unregister') {
    await db.from('rb_devices').delete().eq('id', dev.id);
    return json({ ok: true });
  }
  return json({ error: 'Acción desconocida' }, 400);
}

/* ---------- revisión periódica ---------- */
async function hl(body: unknown): Promise<any> {
  const r = await fetch(HL, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!r.ok) throw new Error('Hyperliquid ' + r.status);
  return r.json();
}
let rpcIdx = 0;
async function rpc(method: string, params: unknown[]): Promise<any> {
  let last: unknown = null;
  const start = rpcIdx;
  for (let i = 0; i < ETH_RPCS.length; i++) {
    const k = (start + i) % ETH_RPCS.length;
    try {
      const r = await fetch(ETH_RPCS[k], { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }) });
      const j = await r.json();
      if (j.error) throw new Error(j.error.message || 'rpc');
      rpcIdx = k; // nos quedamos con el que funciona
      return j.result;
    } catch (e) { last = e; }
  }
  throw last;
}
async function pool<T>(items: T[], n: number, fn: (x: T) => Promise<void>) {
  let i = 0;
  const run = async () => { while (i < items.length) { const it = items[i++]; await fn(it); } };
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, run));
}

type Ev = { address: string; chain: string; title: string; body: string; kind: string; id?: number };
type Pos = { szi: number; entry: number; usd: number; lev: number | null; liq: number | null; pnl: number };

function diffHL(address: string, old: Record<string, Pos>, now: Record<string, Pos>): Ev[] {
  const out: Ev[] = [];
  const side = (v: number) => (v > 0 ? 'LARGO' : 'CORTO');
  const detail = (p: Pos) => usd(p.usd) + (p.lev ? ' · ' + p.lev + 'x' : '') + ' · entrada ' + px(p.entry) + (p.liq ? ' · liquidación ' + px(p.liq) : '');
  for (const c of new Set([...Object.keys(old), ...Object.keys(now)])) {
    const o = old[c], n = now[c];
    const os = o ? o.szi : 0, ns = n ? n.szi : 0;
    const e = (title: string, body: string, kind: string) => out.push({ address, chain: 'hl', title, body, kind });
    if (!os && ns) e('abre ' + side(ns) + ' en ' + c, detail(n), ns > 0 ? 'buy' : 'sell');
    else if (os && !ns) e('cierra su ' + side(os) + ' en ' + c, 'Era de ' + usd(o.usd) + (o.pnl ? ' · último PnL visto ' + (o.pnl > 0 ? '+' : '') + usd(o.pnl) : ''), os > 0 ? 'sell' : 'buy');
    else if (os && ns && Math.sign(os) !== Math.sign(ns)) e('gira a ' + side(ns) + ' en ' + c, detail(n), ns > 0 ? 'buy' : 'sell');
    else if (os && ns) {
      const ratio = Math.abs(ns) / Math.abs(os);
      const moved = Math.abs(Math.abs(ns) - Math.abs(os)) * (n.usd / Math.abs(ns)); // en dólares
      if (moved < 5000) continue;
      if (ratio > 1.1) e('aumenta su ' + side(ns) + ' en ' + c, 'Ahora ' + detail(n), ns > 0 ? 'buy' : 'sell');
      else if (ratio < 0.9) e('reduce su ' + side(ns) + ' en ' + c, 'Ahora ' + detail(n), ns > 0 ? 'sell' : 'buy');
    }
  }
  return out;
}

async function checkHL(address: string, state: any): Promise<Ev[]> {
  const s = await hl({ type: 'clearinghouseState', user: address });
  const now: Record<string, Pos> = {};
  for (const ap of s.assetPositions || []) {
    const p = ap.position;
    if (!p || +p.szi === 0) continue;
    now[p.coin] = {
      szi: +p.szi, entry: +p.entryPx, usd: +p.positionValue, pnl: +p.unrealizedPnl,
      lev: p.leverage ? p.leverage.value : null, liq: p.liquidationPx == null ? null : +p.liquidationPx
    };
  }
  const fresh = state && state.pos && Date.now() - (state.t || 0) < 15 * 60e3;
  const evs = fresh ? diffHL(address, state.pos, now) : [];
  await db.from('rb_state').upsert({ address, chain: 'hl', data: { pos: now, acct: +(s.marginSummary?.accountValue || 0), t: Date.now() }, updated_at: new Date().toISOString() });
  return evs;
}

/* Ethereum: movimientos de tokens (ERC-20) de la cartera, agrupados por transacción */
const STABLE = new Set(['USDC', 'USDT', 'DAI', 'USD1', 'FDUSD', 'PYUSD', 'USDE', 'USDS', 'TUSD', 'USDP', 'GUSD', 'USDC.E']);
const ALIAS: Record<string, string> = { WETH: 'ETH', STETH: 'ETH', WSTETH: 'ETH', WEETH: 'ETH', CBETH: 'ETH', RETH: 'ETH', WBTC: 'BTC', CBBTC: 'BTC', TBTC: 'BTC' };
const tokenMeta = new Map<string, { symbol: string; decimals: number }>();

function decodeString(hex: string): string {
  const h = (hex || '0x').slice(2);
  if (!h) return '';
  if (h.length === 64) { // bytes32 (tokens antiguos)
    let s = '';
    for (let i = 0; i < 64; i += 2) { const c = parseInt(h.slice(i, i + 2), 16); if (c) s += String.fromCharCode(c); }
    return s.trim();
  }
  const off = parseInt(h.slice(0, 64), 16) * 2;
  const len = parseInt(h.slice(off, off + 64), 16);
  const data = h.slice(off + 64, off + 64 + len * 2);
  const bytes = new Uint8Array(len);
  for (let i = 0; i < len; i++) bytes[i] = parseInt(data.slice(i * 2, i * 2 + 2), 16);
  return new TextDecoder().decode(bytes).trim();
}
async function meta(token: string): Promise<{ symbol: string; decimals: number }> {
  const m = tokenMeta.get(token);
  if (m) return m;
  const { data } = await db.from('rb_state').select('data').eq('address', token).eq('chain', 'token').maybeSingle();
  if (data && data.data && data.data.symbol) { tokenMeta.set(token, data.data); return data.data; }
  let symbol = '?', decimals = 18;
  try { symbol = decodeString(await rpc('eth_call', [{ to: token, data: '0x95d89b41' }, 'latest'])).slice(0, 20) || '?'; } catch (_) { /* sin símbolo */ }
  try { decimals = parseInt(await rpc('eth_call', [{ to: token, data: '0x313ce567' }, 'latest']), 16); } catch (_) { /* 18 */ }
  if (!(decimals >= 0 && decimals <= 36)) decimals = 18;
  const v = { symbol, decimals };
  tokenMeta.set(token, v);
  await db.from('rb_state').upsert({ address: token, chain: 'token', data: v });
  return v;
}
function priceOf(symbol: string, mids: Record<string, string>): number | null {
  const s = symbol.toUpperCase();
  if (STABLE.has(s)) return 1;
  const k = ALIAS[s] || s;
  return mids[k] ? +mids[k] : null;
}

async function checkETH(address: string, state: any, latest: number, mids: Record<string, string>): Promise<Ev[]> {
  const fresh = state && state.block && Date.now() - (state.t || 0) < 60 * 60e3;
  const from = fresh ? state.block + 1 : 0;
  if (!from) { // primera vez (o datos viejos): empezamos a mirar desde ahora
    await db.from('rb_state').upsert({ address, chain: 'eth', data: { block: latest, t: Date.now() }, updated_at: new Date().toISOString() });
    return [];
  }
  if (from > latest) return [];
  const to = Math.min(latest, from + 99); // los RPC gratuitos no dejan buscar en rangos grandes
  const pad = '0x' + '0'.repeat(24) + address.slice(2);
  const range = { fromBlock: '0x' + from.toString(16), toBlock: '0x' + to.toString(16) };
  const [outs, ins] = await Promise.all([
    rpc('eth_getLogs', [{ ...range, topics: [TRANSFER, pad] }]),
    rpc('eth_getLogs', [{ ...range, topics: [TRANSFER, null, pad] }])
  ]);
  const txs = new Map<string, { out: Map<string, bigint>; inn: Map<string, bigint> }>();
  const add = (l: any, dir: 'out' | 'inn') => {
    if (!l.topics || l.topics.length !== 3 || !l.data || l.data === '0x') return; // ERC-20 normal
    const t = txs.get(l.transactionHash) || { out: new Map(), inn: new Map() };
    const tok = l.address.toLowerCase();
    t[dir].set(tok, (t[dir].get(tok) || 0n) + BigInt(l.data.slice(0, 66)));
    txs.set(l.transactionHash, t);
  };
  (outs || []).forEach((l: any) => add(l, 'out'));
  (ins || []).forEach((l: any) => add(l, 'inn'));

  const evs: Ev[] = [];
  for (const [hash, t] of txs) {
    const side = async (m: Map<string, bigint>) => {
      const parts: { sym: string; qty: number; val: number | null }[] = [];
      for (const [tok, raw] of m) {
        const md = await meta(tok);
        const qty = Number(raw) / 10 ** md.decimals;
        const p = priceOf(md.symbol, mids);
        parts.push({ sym: md.symbol, qty, val: p == null ? null : qty * p });
      }
      return parts;
    };
    const o = await side(t.out), i = await side(t.inn);
    const val = (ps: typeof o) => ps.reduce((s, p) => s + (p.val || 0), 0);
    const txt = (ps: typeof o) => ps.map(p => amt(p.qty) + ' ' + p.sym + (p.val ? ' (' + usd(p.val) + ')' : '')).join(' + ');
    const link = ' · etherscan.io/tx/' + hash.slice(0, 10) + '…';
    if (o.length && i.length) {
      const v = Math.max(val(o), val(i));
      if (v && v < 10000) continue;
      evs.push({ address, chain: 'eth', title: 'compra ' + i.map(p => p.sym).join(' + '), body: 'Cambia ' + txt(o) + ' por ' + txt(i) + link, kind: 'buy' });
    } else if (o.length) {
      const v = val(o);
      if (v && v < 10000) continue;
      evs.push({ address, chain: 'eth', title: 'mueve ' + o.map(p => p.sym).join(' + '), body: 'Envía ' + txt(o) + link, kind: 'sell' });
    } else if (i.length) {
      const v = val(i);
      if (!v || v < 50000) continue; // tokens basura o sin precio: no avisamos
      evs.push({ address, chain: 'eth', title: 'recibe ' + i.map(p => p.sym).join(' + '), body: 'Recibe ' + txt(i) + link, kind: 'buy' });
    }
  }
  await db.from('rb_state').upsert({ address, chain: 'eth', data: { block: to, t: Date.now() }, updated_at: new Date().toISOString() });
  return evs;
}

async function check(): Promise<Record<string, unknown>> {
  const { data: locked } = await db.rpc('rb_try_lock', { secs: 55 });
  if (!locked) return { skipped: 'otra revisión en curso' };
  const t0 = Date.now();
  const report: Record<string, unknown> = {};
  try {
    const { data: follows } = await db.from('rb_follows').select('device_id,address,chain,label');
    if (!follows || !follows.length) return { targets: 0 };
    const targets = new Map<string, { address: string; chain: string; devs: Map<string, string> }>();
    for (const f of follows) {
      const k = f.chain + ':' + f.address;
      const t = targets.get(k) || { address: f.address, chain: f.chain, devs: new Map() };
      t.devs.set(f.device_id, f.label || f.address.slice(0, 6) + '…' + f.address.slice(-4));
      targets.set(k, t);
    }
    const { data: states } = await db.from('rb_state').select('address,chain,data').in('chain', ['hl', 'eth'])
      .in('address', [...new Set([...targets.values()].map(t => t.address))]);
    const stateOf = new Map((states || []).map(s => [s.chain + ':' + s.address, s.data]));

    const evs: Ev[] = [];
    const errors: string[] = [];
    const list = [...targets.values()];
    await pool(list.filter(t => t.chain === 'hl'), 5, async t => {
      try { evs.push(...await checkHL(t.address, stateOf.get('hl:' + t.address))); }
      catch (e) { errors.push('hl ' + t.address.slice(0, 8) + ': ' + (e as Error).message); }
    });
    const eth = list.filter(t => t.chain === 'eth');
    if (eth.length) {
      try {
        const latest = parseInt(await rpc('eth_blockNumber', []), 16);
        const mids = await hl({ type: 'allMids' }).catch(() => ({}));
        await pool(eth, 2, async t => {
          try { evs.push(...await checkETH(t.address, stateOf.get('eth:' + t.address), latest, mids)); }
          catch (e) { errors.push('eth ' + t.address.slice(0, 8) + ': ' + (e as Error).message); }
        });
      } catch (e) { errors.push('eth: ' + (e as Error).message); }
    }

    // guardar y avisar: el título lleva el nombre que cada móvil le puso a la cartera
    let sent = 0;
    if (evs.length) {
      const { data: ins } = await db.from('rb_events')
        .insert(evs.map(e => ({ ...e, url: APP + '#w=' + e.address + (e.chain === 'eth' ? '&c=eth' : '') }))).select('id');
      (ins || []).forEach((r, i) => { if (evs[i]) evs[i].id = r.id; });
      const devIds = [...new Set(evs.flatMap(e => [...targets.get(e.chain + ':' + e.address)!.devs.keys()]))];
      const { data: devs } = await db.from('rb_devices').select('id,endpoint,p256dh,auth,fails').in('id', devIds);
      const devMap = new Map((devs || []).map(d => [d.id, d as Device]));
      const jobs: (() => Promise<void>)[] = [];
      for (const e of evs) {
        for (const [devId, label] of targets.get(e.chain + ':' + e.address)!.devs) {
          const d = devMap.get(devId);
          if (!d) continue;
          jobs.push(async () => {
            const st = await pushTo(d, {
              title: '🐋 ' + label + ' ' + e.title, body: e.body,
              url: APP + '#w=' + e.address + (e.chain === 'eth' ? '&c=eth' : ''),
              tag: 'rb-' + e.address.slice(2, 10) + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
              address: e.address, chain: e.chain, kind: e.kind, sid: e.id ? 's' + e.id : undefined
            });
            if (st >= 200 && st < 300) sent++;
          });
        }
      }
      await pool(jobs, 6, async j => j());
    }
    const last = +((await getCfg('cleanup_at')) || 0);
    if (Date.now() - last > 24 * 3600e3) {
      await db.from('rb_config').upsert({ key: 'cleanup_at', value: String(Date.now()) });
      await db.from('rb_events').delete().lt('created_at', new Date(Date.now() - 14 * 86400e3).toISOString());
    }
    Object.assign(report, { targets: targets.size, events: evs.length, sent, errors, ms: Date.now() - t0 });
    return report;
  } finally {
    await db.from('rb_config').update({ value: '0' }).eq('key', 'lock');
  }
}

Deno.serve(async (req: Request) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: CORS });
  if (req.method !== 'POST') return json({ error: 'Usa POST' }, 405);
  let body: Record<string, any> = {};
  try { body = await req.json(); } catch (_) { /* vacío */ }
  try {
    if (body.action === 'check') {
      const secret = req.headers.get('x-rb-cron');
      if (!secret || secret !== await getCfg('cron_secret')) return json({ error: 'No autorizado' }, 401);
      return json(await check());
    }
    return await api(body);
  } catch (e) {
    console.error(e);
    return json({ error: (e as Error).message || 'Error' }, 500);
  }
});
