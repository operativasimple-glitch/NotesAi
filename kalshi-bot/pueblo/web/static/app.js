/* Pueblo de bots: la web.
 *
 * Cada pocos segundos lee el mundo (/api/world): quién vive, qué hace cada uno y los mensajes nuevos.
 * Con eso pinta el diario y las fichas y anima el mapa: cuando un bot le escribe a otro, camina hasta
 * él y se lo dice en un bocadillo; lo que es para ti lo deja en tu buzón.
 * La demostración usa la misma pantalla con datos inventados y sin servidor.
 */
(function () {
  'use strict';

  const P = window.Pixel;
  const T = P.T;
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  const ORDER = ['kali', 'herrera', 'nube', 'vigia', 'radar', 'cronista', 'probadora', 'investigadora', 'tormenta', 'claude', 'piloto',
    'dinero', 'codicia', 'miedo'];
  const DEFAULT_BOTS = [
    { id: 'kali', name: 'Kali', color: '#f2c94c', home: 'mercado' },
    { id: 'herrera', name: 'Herrera', color: '#ff8a3d', home: 'forja' },
    { id: 'nube', name: 'Nube', color: '#7cc6fe', home: 'observatorio' },
    { id: 'vigia', name: 'Vigía', color: '#ff6b6b', home: 'puente' },
    { id: 'radar', name: 'Radar', color: '#6ee7a8', home: 'laboratorio' },
    { id: 'cronista', name: 'Cronista', color: '#c792ea', home: 'archivo' },
    { id: 'probadora', name: 'Probadora', color: '#5ee7f0', home: 'pruebas' },
    { id: 'investigadora', name: 'Investigadora', color: '#dcd8ee', home: 'consejo' },
    { id: 'tormenta', name: 'Cazatormentas', color: '#2a78d6', home: 'consejo' },
    { id: 'piloto', name: 'Piloto', color: '#e87ba4', home: 'consejo' },
    { id: 'dinero', name: 'Dinero Listo', color: '#1baf7a', home: 'consejo' },
    { id: 'codicia', name: 'Codicia', color: '#eda100', home: 'consejo' },
    { id: 'miedo', name: 'Miedo', color: '#4a3aa7', home: 'consejo' },
  ];
  const MOODS = { ok: 'Bien', happy: 'Feliz', sad: 'Triste', sleep: 'Descansando', sick: 'Con problemas', alert: 'En alerta' };
  const KIND_LABEL = { alert: 'Aviso', trade: 'Operación', diary: 'Crónica', chat: 'Charla' };
  const ROOM_INFO = {
    mercado: ['Mercado', 'Aquí trabaja Kali. Cada vez que hace algo de verdad (una orden, una compra, un cobro) viene aquí.'],
    laboratorio: ['Laboratorio', 'Radar busca oportunidades en todos los mercados de Kalshi y la Investigadora prepara los del Consejo.'],
    observatorio: ['Observatorio', 'Nube mira la previsión y lo ya medido en las 7 ciudades del clima.'],
    consejo: ['Consejo', 'Cada personaje da su precio justo para cada mercado y apuesta de mentira. Aquí se lleva el marcador.'],
    boveda: ['Bóveda', 'Donde se cuentan las ganancias y las pérdidas. Kali viene cuando se decide un mercado o mira el saldo.'],
    puente: ['Puente', 'Vigía vigila el riesgo. Aquí está tu terminal: lo que los bots te dejan llega aquí.'],
    archivo: ['Archivo', 'Cronista escribe el diario del pueblo y cuelga las noticias en el tablón.'],
    forja: ['Forja', 'La Herrera repasa el dinero real de Kali: qué liga, ciudad y precio gana o pierde, y qué ajuste del panel lo cambiaría.'],
    pruebas: ['Pruebas', 'La Probadora juega de mentira los partidos como Kali, pero de cuatro maneras a la vez, para ver cuál gana más.'],
  };
  const ROOM_OWNER = {
    mercado: 'kali', boveda: 'kali', forja: 'herrera', observatorio: 'nube', laboratorio: 'radar', archivo: 'cronista',
    pruebas: 'probadora',
  };
  const MEMBER_ABOUT = {
    tormenta: 'Para cada tramo de temperatura calcula la probabilidad con la máxima prevista por el Servicio Meteorológico de EE. UU. y lo ya medido hoy.',
    dinero: 'Mira cómo ha cambiado el precio en el último día y cree que seguirá en esa dirección.',
    piloto: 'Piensa que los favoritos claros ganan un poco más de lo que dice su precio. Es la idea de Kali, apostando de mentira.',
    miedo: 'Desconfía de lo que parece seguro: acerca cualquier probabilidad al 50 %. Sirve para ver cuánto cuesta tener miedo.',
    codicia: 'Cree que el que va ganando ganará seguro. Sirve para ver cuánto cuesta la avaricia.',
    claude: 'Lee cada mercado y da su probabilidad con una frase. Solo está si pones una clave de Anthropic; cada pregunta cuesta un poco.',
  };

  // ---------------------------------------------------------------- utilidades

  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function clip(text, n) {
    if (text.length <= n) return text;
    const cut = text.slice(0, n - 1);
    const space = cut.lastIndexOf(' ');
    return (space > n * 0.6 ? cut.slice(0, space) : cut) + '…';
  }
  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      window.localStorage.setItem(key, String(value));
    } catch (err) {
      // Sin almacenamiento (ventana privada, vista previa): la página funciona igual.
    }
    return null;
  }
  function num(v) {
    if (v === null || v === undefined || v === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  function money(v, sign) {
    const n = num(v);
    if (n === null) return '—';
    const text = Math.abs(n).toFixed(2).replace('.', ',');
    return (n < 0 ? '−' : sign && n > 0 ? '+' : '') + '$' + text;
  }
  function pct(v) {
    const n = num(v);
    return n === null ? '—' : `${Math.round(n * 100)} %`;
  }
  function cents(v) {
    const n = num(v);
    return n === null ? '—' : `${Math.round(n * 100)}¢`;
  }
  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }
  function deg(v) {
    return v === null || v === undefined ? '—' : `${v}°`;
  }
  function hm(date) {
    return date.toLocaleTimeString('es-ES', { hour: '2-digit', minute: '2-digit' });
  }
  function ago(iso) {
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return '';
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 45) return 'ahora';
    if (s < 3600) return `hace ${Math.max(1, Math.round(s / 60))} min`;
    if (s < 6 * 3600) return `hace ${Math.round(s / 3600)} h`;
    const d = new Date(t);
    const today = new Date();
    if (d.toDateString() === today.toDateString()) return hm(d);
    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return `ayer ${hm(d)}`;
    return `${d.toLocaleDateString('es-ES', { day: 'numeric', month: 'short' })} ${hm(d)}`;
  }
  function closesIn(hours) {
    const n = num(hours);
    if (n === null) return '';
    return n < 1 ? 'cierra en menos de 1 h' : `cierra en ${Math.round(n)} h`;
  }
  function sortBots(bots) {
    const rank = (b) => (ORDER.includes(b.id) ? ORDER.indexOf(b.id) : ORDER.length);
    return [...bots].sort((a, b) => rank(a) - rank(b) || String(a.name).localeCompare(String(b.name)));
  }

  // La luz del pueblo sigue tu hora. Con #noche, #tarde o #dia en la dirección se puede forzar.
  function dayPhase(date) {
    const forced = (location.hash || '').slice(1);
    let h = date.getHours() + date.getMinutes() / 60;
    if (forced === 'noche') h = 23;
    else if (forced === 'tarde') h = 19.3;
    else if (forced === 'dia') h = 12;
    let dark = 0;
    if (h >= 20.5 || h < 5.5) dark = 1;
    else if (h < 7.5) dark = (7.5 - h) / 2;
    else if (h >= 18) dark = (h - 18) / 2.5;
    const warm = dark > 0 && dark < 1 ? Math.sin(dark * Math.PI) : 0;
    const name = dark >= 1 ? 'noche' : h < 7.5 ? 'amanecer' : h >= 18 ? 'atardecer' : h < 13 ? 'mañana' : 'tarde';
    return { dark, warm, name };
  }

  // ---------------------------------------------------------------- de dónde salen los datos

  class ApiError extends Error {
    constructor(status, message) {
      super(message);
      this.status = status;
    }
  }

  async function request(method, path, body) {
    // El pueblo vive dentro del panel del bot (/pueblo/): usa su sesión y su cabecera de seguridad.
    const opts = { method, headers: { 'X-Requested-With': 'kalshi-bot' }, credentials: 'same-origin', cache: 'no-store' };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    let resp;
    try {
      resp = await fetch(path, opts);
    } catch (err) {
      throw new ApiError(0, 'Sin conexión');
    }
    let data = null;
    try {
      data = await resp.json();
    } catch (err) {
      data = null;
    }
    if (!resp.ok) throw new ApiError(resp.status, (data && data.error) || `Error ${resp.status}`);
    return data;
  }

  const Api = {
    demo: false,
    every: 5000,
    // Rutas relativas: dentro del panel son /pueblo/api/...
    world: (after) => request('GET', `api/world?after=${after || 0}`),
    talk: (bot) => request('POST', 'api/talk', { bot }),
    login: (password) => request('POST', 'api/login', { password }),
    logout: () => request('POST', 'api/logout', {}),
  };

  // --- la demostración: un pueblo inventado que va contando cosas ---

  const PIT = 'KXNHLGAME-26OCT10PITCBJ-PIT';

  function demoBots() {
    const now = new Date().toISOString();
    return [
      {
        id: 'kali', name: 'Kali', role: 'Opera en Kalshi con tu dinero', home: 'mercado', color: '#f2c94c',
        about: 'Es tu bot de Kalshi: compra favoritos entre 88 y 97¢ y espera a que se decidan. Aquí solo se mira lo que hace; quien manda es su panel.',
        status: 'Trabajando · 31 mercados · hoy +$1,85', mood: 'happy', updated_at: now,
        detail: {
          connected: true, state: 'running', mode: 'live',
          markets: Array.from({ length: 31 }, (_, i) => `DEMO-${i}`),
          balance: { cash: '11.30', portfolio_value: '13.70', equity: '25.00' },
          session_pnl: '1.85', max_loss: '10',
          positions: [
            { ticker: 'KXHIGHMIA-26OCT10-B88.5', name: 'Máxima en Miami · 88° a 89°', side: 'SÍ', contracts: '5', cost: '4.65', chance: '0.94', payout: '5', hours_to_close: 7.5 },
            { ticker: PIT, name: 'Gana Pittsburgh', side: 'NO', contracts: '5', cost: '4.45', chance: '0.88', payout: '5', hours_to_close: 4.2 },
            { ticker: 'KXHIGHNY-26OCT10-B72.5', name: 'Máxima en Nueva York · 72° a 73°', side: 'NO', contracts: '5', cost: '4.60', chance: '0.91', payout: '5', hours_to_close: 9.1 },
          ],
          today: { net: '1.85', markets: 9, wins: 9, losses: 0 },
          yesterday: { net: '2.40', markets: 12, wins: 11, losses: 1 },
          totals: { net: '6.10', markets: 41 },
        },
      },
      {
        id: 'herrera', name: 'Herrera', role: 'Mira qué funciona y qué no en el dinero real de Kali', home: 'forja', color: '#ff8a3d',
        about: 'Repasa los mercados que Kali ha cerrado en los últimos 30 días y los separa por liga, por ciudad y por el precio que pagó.',
        status: '30 días: +$2,49 en 71 mercados · 5 perdidos', mood: 'alert', updated_at: now,
        activity: { room: 'forja', text: 'lo que más pierde: Fútbol americano universitario −$9,80', at: now },
        detail: {
          connected: true, days: 30,
          totals: { markets: 71, wins: 66, losses: 5, net: 2.49, avg_price: 0.922, rate: 0.9296, need: 0.9235, verdict: 'gana' },
          advice: [
            { key: 'series:KXNCAAFGAME', level: 'alert', text: '«Fútbol americano universitario» pierde dinero: 14 mercados, 3 perdidos, −$9,80. Puedes quitar KXNCAAFGAME en Ajustes → Mercados → Series.' },
            { key: 'size', level: 'info', text: 'Cada apuesta es un 19 % de tu saldo y un fallo se come unos 12 aciertos. Con menos contratos por orden (Ajustes → Estrategia) los días malos duelen mucho menos.' },
          ],
          groups: {
            kinds: [
              { key: 'partidos', label: 'Partidos', markets: 30, wins: 27, losses: 3, net: -6.1, rate: 0.9, need: 0.915, verdict: 'pierde' },
              { key: 'clima', label: 'Clima', markets: 41, wins: 39, losses: 2, net: 8.59, rate: 0.951, need: 0.928, verdict: 'gana' },
            ],
            series: [
              { key: 'KXNCAAFGAME', label: 'Fútbol americano universitario', markets: 14, wins: 11, losses: 3, net: -9.8, rate: 0.786, need: 0.909, verdict: 'pierde' },
              { key: 'KXHIGHAUS', label: 'Clima · Austin', markets: 6, wins: 5, losses: 1, net: -2.31, rate: 0.833, need: 0.925, verdict: 'va perdiendo' },
              { key: 'KXNHLGAME', label: 'Hockey (NHL)', markets: 4, wins: 4, losses: 0, net: 1.52, rate: 1, need: 0.905, verdict: 'pocos datos' },
              { key: 'KXMLBGAME', label: 'Béisbol (MLB)', markets: 12, wins: 12, losses: 0, net: 2.18, rate: 1, need: 0.921, verdict: 'va ganando' },
              { key: 'KXHIGHNY', label: 'Clima · Nueva York', markets: 7, wins: 7, losses: 0, net: 2.55, rate: 1, need: 0.93, verdict: 'va ganando' },
            ],
            bands: [
              { key: 'menos de 90¢', label: 'menos de 90¢', markets: 9, wins: 7, losses: 2, net: -6.25, rate: 0.778, need: 0.892, verdict: 'pierde' },
              { key: '90–91¢', label: '90–91¢', markets: 24, wins: 22, losses: 2, net: -1.94, rate: 0.917, need: 0.912, verdict: 'pierde' },
              { key: '92–93¢', label: '92–93¢', markets: 22, wins: 21, losses: 1, net: 4.12, rate: 0.955, need: 0.93, verdict: 'gana' },
              { key: '94¢ o más', label: '94¢ o más', markets: 16, wins: 16, losses: 0, net: 6.56, rate: 1, need: 0.951, verdict: 'gana' },
            ],
          },
          losses: [
            { ticker: 'D1', name: 'Gana Texas', label: 'Fútbol americano universitario', side: 'SÍ', price: 0.89, net: -4.46, date: '2026-10-10' },
            { ticker: 'D2', name: 'Máxima en Austin · 91° a 92°', label: 'Clima · Austin', side: 'NO', price: 0.9, net: -4.51, date: '2026-10-09' },
          ],
          curve: Array.from({ length: 60 }, (_, i) => ({ date: '', total: Math.round((i < 48 ? i * 0.3 : 14.4 - (i - 48) * 1.0) * 100) / 100 })),
        },
      },
      {
        id: 'nube', name: 'Nube', role: 'Meteoróloga: previsiones y temperaturas medidas', home: 'observatorio', color: '#7cc6fe',
        about: 'Mira cada 15 minutos el Servicio Meteorológico de EE. UU. en las 7 ciudades del clima. Si una apuesta de temperatura de Kali peligra, se lo dice a Vigía.',
        status: 'Vigilando 7 ciudades · 2 apuestas de Kali', mood: 'ok', updated_at: now,
        detail: {
          errors: [],
          cities: [
            { key: 'NY', city: 'Nueva York', today: 73, tomorrow: 70, max_so_far: 71, now: 69 },
            { key: 'CHI', city: 'Chicago', today: 68, tomorrow: 64, max_so_far: 66, now: 65 },
            { key: 'LAX', city: 'Los Ángeles', today: 79, tomorrow: 81, max_so_far: 77, now: 76 },
            { key: 'MIA', city: 'Miami', today: 88, tomorrow: 87, max_so_far: 87, now: 86 },
            { key: 'AUS', city: 'Austin', today: 91, tomorrow: 89, max_so_far: 90, now: 88 },
            { key: 'DEN', city: 'Denver', today: 66, tomorrow: 71, max_so_far: 63, now: 61 },
            { key: 'PHIL', city: 'Filadelfia', today: 72, tomorrow: 69, max_so_far: 70, now: 68 },
          ],
        },
      },
      {
        id: 'vigia', name: 'Vigía', role: 'Vigila el riesgo y te avisa', home: 'torre', color: '#ff6b6b',
        about: 'Desde su torre mira las apuestas de Kali: si una se hunde, si hay demasiado dinero en una sola o si se acerca el freno de pérdidas.',
        status: 'Todo en calma · 3 apuestas abiertas', mood: 'ok', updated_at: now, detail: { risky: 0, alerts_today: 1 },
      },
      {
        id: 'radar', name: 'Radar', role: 'Explorador: busca oportunidades en Kalshi', home: 'faro', color: '#6ee7a8',
        about: 'Cada 20 minutos recorre los mercados que cierran en las próximas 12 horas y cuenta los favoritos claros que Kali no está mirando.',
        status: '18 favoritos a la vista · 7 fuera de la lista de Kali', mood: 'ok', updated_at: now,
        detail: {
          scanned: 412, favorites: 18, outside: 7,
          by_series: [
            { series: 'KXNFLGAME', label: 'NFL', count: 3 },
            { series: 'KXATPMATCH', label: 'tenis ATP', count: 2 },
            { series: 'KXMLSGAME', label: 'MLS', count: 2 },
          ],
          examples: [
            { label: 'Gana Kansas City · SÍ a 91¢', closes: null },
            { label: 'Gana Sinner · SÍ a 93¢', closes: null },
            { label: 'Gana Seattle Sounders · NO a 90¢', closes: null },
          ],
        },
      },
      {
        id: 'cronista', name: 'Cronista', role: 'Escribe el diario del pueblo', home: 'biblioteca', color: '#c792ea',
        about: 'Por la mañana te cuenta cómo fue ayer y qué se espera hoy; por la noche, el resumen del día.',
        status: 'Diario al día', mood: 'ok', updated_at: now, detail: {},
      },
    ];
  }

  const DEMO_MEMBERS = [
    ['tormenta', 'Cazatormentas', '#2a78d6', 'Calcula el clima con la previsión del NWS', 14, 10, 2.84, 4],
    ['claude', 'Claude', '#eb6834', 'Analiza los mercados con IA (opcional)', 9, 6, 1.12, 3],
    ['piloto', 'Piloto', '#e87ba4', 'Cree en los favoritos, como Kali', 11, 10, 0.41, 2],
    ['dinero', 'Dinero Listo', '#1baf7a', 'Sigue al dinero que se mueve', 7, 3, -0.62, 2],
    ['codicia', 'Codicia', '#eda100', 'Lo quiere todo', 16, 9, -1.35, 5],
    ['miedo', 'Miedo', '#4a3aa7', 'Todo le parece arriesgado', 18, 5, -2.08, 6],
  ];
  const DEMO_TOPICS = [
    ['KXHIGHNY-26OCT10-B72.5', 'Máxima en Nueva York · 72° a 73°', 0.08, 0.11, true,
      { tormenta: 0.31, dinero: 0.12, piloto: 0.06, miedo: 0.22, codicia: 0.04, claude: 0.24 }],
    ['KXHIGHMIA-26OCT10-B88.5', 'Máxima en Miami · 88° a 89°', 0.93, 0.94, true,
      { tormenta: 0.95, dinero: 0.94, piloto: 0.97, miedo: 0.85, codicia: 0.97, claude: 0.93 }],
    [PIT, 'Gana Pittsburgh', 0.11, 0.13, true, { dinero: 0.1, piloto: 0.08, miedo: 0.21, codicia: 0.05, claude: 0.14 }],
    ['KXHIGHAUS-26OCT10-B91.5', 'Máxima en Austin · 91° a 92°', 0.42, 0.46, false,
      { tormenta: 0.62, dinero: 0.47, piloto: 0.44, miedo: 0.46, codicia: 0.4, claude: 0.55 }],
    ['KXHIGHDEN-26OCT10-B66.5', 'Máxima en Denver · 66° a 67°', 0.5, 0.54, false,
      { tormenta: 0.71, dinero: 0.55, piloto: 0.52, miedo: 0.51, codicia: 0.53, claude: 0.64 }],
    ['KXHIGHCHI-26OCT10-B68.5', 'Máxima en Chicago · 68° a 69°', 0.34, 0.38, false,
      { tormenta: 0.28, dinero: 0.33, piloto: 0.36, miedo: 0.4, codicia: 0.29, claude: 0.31 }],
    ['KXHIGHLAX-26OCT10-B79.5', 'Máxima en Los Ángeles · 79° a 80°', 0.25, 0.29, false,
      { tormenta: 0.19, dinero: 0.25, piloto: 0.27, miedo: 0.34, codicia: 0.18, claude: 0.22 }],
    ['KXHIGHPHIL-26OCT11-B72.5', 'Máxima en Filadelfia · 72° a 73°', 0.31, 0.35, false,
      { tormenta: 0.3, dinero: 0.36, piloto: 0.33, miedo: 0.38, codicia: 0.25, claude: 0.31 }],
  ];

  function median(values) {
    const v = [...values].sort((a, b) => a - b);
    const n = v.length;
    return n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
  }
  function shortName(name) {
    return String(name || '').replace('Máxima en ', '');
  }

  const DEMO_TESTS = [
    { id: 'desde92', name: 'Solo desde 92¢', rule: 'desde 92¢ · 5 contratos', trades: 19, wins: 19, pnl: 3.85, worst_day: 0.6, open: 2 },
    { id: 'max3', name: 'Máx. 3 partidos a la vez', rule: 'desde 88¢ · 5 contratos · máx. 3 a la vez', trades: 22, wins: 21, pnl: 1.95, worst_day: -3.9, open: 3 },
    { id: 'menos', name: '3 contratos por partido', rule: 'desde 88¢ · 3 contratos', trades: 31, wins: 29, pnl: 1.62, worst_day: -2.31, open: 4 },
    { id: 'tal_cual', name: 'Kali tal cual', rule: 'desde 88¢ · 5 contratos', trades: 31, wins: 29, pnl: 1.4, worst_day: -4.05, open: 4 },
  ];

  function demoCouncil(bots) {
    const now = new Date().toISOString();
    const act = (room, text) => ({ room, text, at: now });
    const homes = {
      kali: ['mercado', 'vigila 3 apuestas abiertas'],
      nube: ['observatorio', 'termómetros · Austin ya marca 90°'],
      vigia: ['puente', 'vigila 3 apuestas'],
      radar: ['laboratorio', 'mira 412 mercados · 18 favoritos'],
      cronista: ['archivo', 'ordena el diario'],
    };
    for (const b of bots) {
      if (homes[b.id]) {
        b.home = homes[b.id][0];
        b.activity = act(...homes[b.id]);
      }
    }
    const topics = DEMO_TOPICS.map(([ticker, name, bid, ask, held, fairs]) => ({
      ticker, name, kind: ticker.startsWith('KXHIGH') ? 'weather' : 'other', bid, ask,
      mid: Math.round(((bid + ask) / 2) * 1000) / 1000, held, fairs,
      consensus: Math.round(median(Object.values(fairs)) * 1000) / 1000,
    }));
    const board = DEMO_MEMBERS.map(([id, name, color, , trades, wins, pnl, open]) => (
      { id, name, color, trades, wins, pnl, roi: Math.round((pnl / (trades * 0.6)) * 1000) / 1000, open }
    ));
    bots.push({
      id: 'probadora', name: 'Probadora', role: 'Prueba de mentira otras formas de jugar los partidos', home: 'pruebas',
      color: '#5ee7f0', mood: 'ok', updated_at: now,
      about: 'Sigue los partidos de Kalshi y apuesta de mentira como Kali, pero de cuatro maneras a la vez. Cuando acaba cada partido apunta cuál habría ganado más. No compra nada.',
      status: 'Pruebas en partidos · 4 partidos abiertos de mentira',
      activity: act('pruebas', 'prueba 4 maneras en 9 partidos · va mejor «Solo desde 92¢»'),
      detail: {
        variants: DEMO_TESTS,
        last: [
          { ticker: 'D1', name: 'Gana Ohio State', side: 'SÍ', price: '0.94', count: 5, won: true, pnl: '0.29' },
          { ticker: 'D2', name: 'Gana Texas', side: 'SÍ', price: '0.89', count: 5, won: false, pnl: '-4.46' },
          { ticker: 'D3', name: 'Gana los Dodgers', side: 'NO', price: '0.91', count: 5, won: true, pnl: '0.44' },
        ],
      },
    });
    bots.push({
      id: 'investigadora', name: 'Investigadora', role: 'Organiza el Consejo y lleva el marcador', home: 'consejo',
      color: '#dcd8ee', mood: 'ok', updated_at: now,
      about: 'Trae al Consejo los mercados de clima y las apuestas de Kali, apunta las apuestas de mentira y lleva el marcador.',
      status: `Consejo · ${topics.length} mercados · 22 apuestas abiertas de mentira`,
      activity: act('consejo', 'modera 8 mercados · va ganando Cazatormentas'),
      detail: { topics, board, open: 22, leader: 'tormenta' },
    });
    const reasons = {
      'KXHIGHNY-26OCT10-B72.5': 'Van 71° y la previsión dice 73°: el tramo está muy vivo, más de lo que dice el precio.',
      'KXHIGHAUS-26OCT10-B91.5': 'La previsión dice 91° y ya van 90° a media tarde.',
      'KXHIGHDEN-26OCT10-B66.5': 'Prevén 66° y llevan 63° con sol: es lo más probable.',
    };
    for (const [id, name, color, role, trades, wins, pnl, open] of DEMO_MEMBERS) {
      if (id === 'claude') {
        const opinions = {};
        for (const t of topics) {
          if (t.fairs.claude !== undefined) {
            opinions[t.ticker] = { fair: t.fairs.claude, name: t.name, at: now,
              reason: reasons[t.ticker] || 'Sin datos nuevos: me quedo cerca del precio del mercado.' };
          }
        }
        bots.push({
          id, name, color, role, home: 'consejo', about: MEMBER_ABOUT.claude, updated_at: now, mood: 'happy',
          status: '«Máxima en Austin · 91° a 92°»: 55 % · 7 preguntas hoy',
          activity: act('consejo', 'cree 55 % · Máxima en Austin · 91° a 92°'),
          detail: { opinions, calls: 7, limit: 12 },
        });
        continue;
      }
      const calls = topics
        .filter((t) => t.fairs[id] !== undefined)
        .map((t) => ({ ticker: t.ticker, name: t.name, fair: t.fairs[id], mid: t.mid, edge: t.fairs[id] - t.mid }))
        .sort((x, y) => Math.abs(y.edge) - Math.abs(x.edge));
      const c = calls[0];
      bots.push({
        id, name, color, role, home: 'consejo', about: MEMBER_ABOUT[id], updated_at: now,
        status: `${wins} de ${trades} acertadas · ${money(pnl, true)} de mentira`,
        mood: pnl > 0 ? 'happy' : pnl < 0 ? 'sad' : 'ok',
        activity: act('consejo', c ? `justo ${Math.round(c.fair * 100)}¢ · ${shortName(c.name)} (paga ${Math.round(c.mid * 100)}¢)` : 'escucha'),
        detail: { member: true, record: { trades, wins, pnl, roi: Math.round((pnl / (trades * 0.6)) * 1000) / 1000 },
          calls: calls.slice(0, 10), open: [], last: [], opinions: calls.length, open_count: open },
      });
    }
    return bots;
  }

  const DEMO_SCRIPT = [
    (w) => w.say('nube', 'En Chicago la máxima prevista para hoy sube de 66° a 68°.', 'kali'),
    (w) => {
      w.say('tormenta', 'Apunta: compro el SÍ de «Máxima en Denver · 66° a 67°» a 54¢; para mí vale 71¢. De mentira, claro.',
        'investigadora');
    },
    (w) => {
      w.act('kali', 'boveda', 'gana +$0,35 · Austin 91°–92°');
      w.earn(0.35);
      w.say('kali', 'Cobrado antes: +$0,35 · Máxima en Austin · 91° a 92°', 'todos', 'trade', { net: '0.35' });
    },
    (w) => {
      w.chance(PIT, '0.62');
      w.set('vigia', { mood: 'alert', status: 'Vigilando 1 apuesta que se ha torcido' }, { risky: 1 });
      w.alerts();
      w.say('vigia', 'Ojo: «Gana Pittsburgh» (NO) ha bajado del 88 % al 62 %. Si falla, se pierden $4,45.', 'tu', 'alert');
    },
    (w) => w.say('nube', 'Ojo: Kali tiene el NO a «72° a 73°» de Nueva York; van 71° y la previsión dice 73°. Peligra.', 'vigia', 'alert'),
    (w) => {
      w.say('tormenta', '«Denver 66°–67°»: yo le doy un 71 % y tú solo un 53 %, Codicia. ¿Qué sabes tú que yo no sé?',
        'codicia', 'chat');
      w.say('codicia', '¡El que va ganando gana! Lo quiero todo.', 'tormenta', 'chat');
    },
    (w) => {
      w.alerts();
      w.say('vigia', 'Nube avisa: Ojo: Kali tiene el NO a «72° a 73°» de Nueva York; van 71° y la previsión dice 73°. Peligra. Hay $4,60 en juego.', 'tu', 'alert');
    },
    (w) => {
      w.act('kali', 'mercado', 'compra 5 SÍ a 94¢ · Denver 66°–67°');
      w.say('kali', 'He comprado 5 SÍ a 94¢ · Máxima en Denver · 66° a 67°', 'todos', 'trade');
    },
    (w) => w.say('claude', '«Austin 91°–92°»: le doy un 55 %. La previsión dice 91° y ya van 90° a media tarde.', 'todos'),
    (w) => w.say('investigadora', 'Kali, el Consejo cree que tu NO de «Máxima en Nueva York · 72° a 73°» vale 83¢ y lo pagaste a 92¢.',
      'kali', 'alert'),
    (w) => w.say('radar', 'He visto 4 favoritos claros que Kali no mira: tenis ATP (2), NFL (2).', 'kali'),
    (w) => {
      w.chance(PIT, '0.90');
      w.set('vigia', { mood: 'ok', status: 'Todo en calma · 3 apuestas abiertas' }, { risky: 0 });
      w.say('vigia', 'Se recupera «Gana Pittsburgh»: vuelve al 90 %.', 'tu');
    },
    (w) => w.say('nube', 'Tranquila, Kali: el NO a «85° a 86°» de Austin ya está ganado (van 90°).', 'kali'),
    (w) => {
      w.act('probadora', 'pruebas', 'apunta: Texas pierde · solo desde 92¢ no entró');
      w.say('probadora', 'Partido perdido de mentira: «Gana Texas» (SÍ a 89¢), −$4,46. Solo desde 92¢ no habría entrado.',
        'todos', 'trade', { net: '-4.46', paper: true });
    },
    (w) => {
      w.say('tormenta', '¡Acerté con «Máxima en Miami · 88° a 89°»! +$0,61 de mentira.', 'todos', 'trade', { net: '0.61', paper: true });
      w.say('investigadora', 'Se han decidido 3 apuestas del Consejo: 2 aciertos. Va ganando Cazatormentas (+$3,45).', 'todos');
    },
    (w) => {
      w.earn(0.28);
      w.say('kali', 'Mercado cerrado: +$0,28 · Máxima en Miami · 88° a 89°', 'todos', 'trade', { net: '0.28' });
    },
    (w) => {
      const t = w.bot('kali').detail.today;
      const avisos = w.bot('vigia').detail.alerts_today;
      w.say('cronista', `Resumen del día: Kali cerró ${plural(t.markets, 'mercado', 'mercados')} y va ${money(t.net, true)} ` +
        `(${plural(t.wins, 'acierto', 'aciertos')}, ${plural(t.losses, 'fallo', 'fallos')}). ` +
        `Vigía dio ${plural(avisos, 'aviso', 'avisos')} en las últimas 24 horas. Buenas noches.`, 'tu', 'diary');
    },
  ];

  function demoReply(w, id) {
    const k = w.bot('kali').detail;
    if (id === 'kali') {
      const list = k.positions.map((p) => `${p.name} (${p.side}, ${pct(p.chance)})`).join(', ');
      return `Hoy llevo ${money(k.today.net, true)} en ${plural(k.today.markets, 'mercado', 'mercados')}. ` +
        `Tengo ${plural(k.positions.length, 'apuesta abierta', 'apuestas abiertas')}: ${list}. Saldo: ${money(k.balance.equity)}.`;
    }
    if (id === 'nube') return 'Hoy lo más caluroso será Austin (91°) y lo más fresco Denver (66°). Ya medido: Nueva York 71°, Chicago 66°, Los Ángeles 77°, Miami 87°.';
    if (id === 'vigia') {
      const worst = [...k.positions].sort((a, b) => Number(a.chance) - Number(b.chance))[0];
      return `La que más miro es «${worst.name}» (${worst.side}), al ${pct(worst.chance)}. ` +
        `Kali tiene ${plural(k.positions.length, 'apuesta abierta', 'apuestas abiertas')}.`;
    }
    if (id === 'radar') return 'De 412 mercados, 7 favoritos están fuera de lo que mira Kali: NFL (3), tenis ATP (2), MLS (2).';
    return `Hoy Kali va ${money(k.today.net, true)} en ${plural(k.today.markets, 'mercado', 'mercados')}. Esta noche lo cuento todo en el diario.`;
  }

  class Demo {
    constructor() {
      this.demo = true;
      this.every = 2500;
      this.nextId = 1;
      this.messages = [];
      this.bots = demoCouncil(demoBots());
      this.turn = 0;
      this.rotateAt = Date.now() + 6000;
      this.step = 0;
      const now = Date.now();
      const past = [
        [52, 'cronista', 'Buenos días. Ayer Kali cerró 12 mercados: +$2,40 (11 ganados, 1 perdido). Radar ve 18 favoritos claros para las próximas horas. Nube dice que hoy lo más caluroso será Austin, con 91°.', 'tu', 'diary'],
        [38, 'kali', 'He comprado 5 SÍ a 93¢ · Máxima en Miami · 88° a 89°', 'todos', 'trade'],
        [27, 'vigia', 'Kali tiene $4,65 en «Máxima en Miami · 88° a 89°», un 21 % del saldo. Es mucho para una sola apuesta.', 'kali', 'info'],
        [21, 'nube', 'En Denver la máxima prevista para hoy baja de 68° a 66°.', 'kali', 'info'],
        [9, 'kali', 'Mercado cerrado: +$0,25 · Gana Seattle', 'todos', 'trade', { net: '0.25' }],
        [3, 'radar', 'He visto 7 favoritos claros que Kali no mira: NFL (3), tenis ATP (2), MLS (2).', 'kali', 'info'],
      ];
      for (const [min, from, text, to, kind, data] of past) this.push(from, text, to, kind, data, now - min * 60000);
      this.nextAt = now + 4000;
    }
    push(from, text, to, kind, data, at) {
      const m = { id: this.nextId++, ts: new Date(at || Date.now()).toISOString(), from, to: to || 'todos', text, kind: kind || 'info' };
      if (data) m.data = data;
      this.messages.push(m);
      if (this.messages.length > 200) this.messages.shift();
      return m;
    }
    bot(id) {
      return this.bots.find((b) => b.id === id);
    }
    tick() {
      const now = Date.now();
      let guard = 0;
      while (now >= this.nextAt && guard++ < 4) {
        DEMO_SCRIPT[this.step % DEMO_SCRIPT.length](this);
        this.step += 1;
        this.nextAt += rand(7000, 11000);
      }
      if (now >= this.nextAt) this.nextAt = now + 5000;
      if (now >= this.rotateAt) {
        this.rotateAt = now + rand(8000, 11000);
        this.rotate();
      }
    }
    // Para que la demo se mueva: cada uno va a la sala de lo que "está haciendo".
    rotate() {
      const k = (this.turn += 1);
      const kali = this.bot('kali');
      const kaliActs = [
        ['laboratorio', 'mira 31 mercados · 28 ya decididos'],
        ['mercado', 'vigila 3 apuestas abiertas'],
        ['laboratorio', 'busca favoritos'],
        ['boveda', `cuenta el saldo: ${money(kali.detail.balance.equity)}`],
      ];
      this.act('kali', ...kaliActs[k % kaliActs.length]);
      const inv = [
        ['laboratorio', 'trae los mercados al Consejo'],
        ['consejo', 'modera 8 mercados · va ganando Cazatormentas'],
        ['boveda', 'mira qué mercados se han decidido'],
        ['consejo', 'apunta las apuestas de mentira'],
      ];
      this.act('investigadora', ...inv[k % inv.length]);
      if (k % 3 === 0) this.act('radar', 'laboratorio', `mira ${400 + (k % 7) * 3} mercados · ${16 + (k % 4)} favoritos`);
      if (k % 4 === 1) {
        this.act('nube', 'observatorio', pick(['previsión NWS · Austin 91°', 'termómetros · Miami ya marca 87°', 'mira el cielo de 7 ciudades']));
      }
      if (k % 5 === 2) this.act('cronista', 'boveda', 'cuenta los cierres de hoy');
      if (k % 5 === 4) this.act('cronista', 'archivo', 'ordena el diario');
    }
    act(id, room, text) {
      const b = this.bot(id);
      if (b) {
        b.activity = { room, text, at: new Date().toISOString() };
        b.updated_at = b.activity.at;
      }
    }
    async world(after) {
      this.tick();
      return {
        now: new Date().toISOString(),
        bots: JSON.parse(JSON.stringify(this.bots)),
        messages: this.messages.filter((m) => m.id > (after || 0)).slice(-120),
        last_id: this.nextId - 1,
      };
    }
    async talk(id) {
      if (!this.bot(id)) throw new ApiError(404, 'Ese bot no vive aquí');
      return this.push(id, demoReply(this, id), 'tu', 'chat');
    }
    say(from, text, to, kind, data) {
      return this.push(from, text, to, kind, data);
    }
    set(id, fields, detail) {
      const b = this.bot(id);
      Object.assign(b, fields);
      if (detail) Object.assign(b.detail, detail);
      b.updated_at = new Date().toISOString();
    }
    alerts() {
      this.bot('vigia').detail.alerts_today += 1;
    }
    earn(net) {
      const k = this.bot('kali');
      const d = k.detail;
      d.today.net = (Number(d.today.net) + net).toFixed(2);
      d.today.markets += 1;
      d.today.wins += net > 0 ? 1 : 0;
      d.totals.net = (Number(d.totals.net) + net).toFixed(2);
      d.totals.markets += 1;
      k.status = `Trabajando · ${plural(d.markets.length, 'mercado', 'mercados')} · hoy ${money(d.today.net, true)}`;
      k.updated_at = new Date().toISOString();
    }
    chance(ticker, value) {
      const p = this.bot('kali').detail.positions.find((x) => x.ticker === ticker);
      if (p) p.chance = value;
    }
  }

  // ---------------------------------------------------------------- caminos

  function findPath(map, from, to) {
    const key = (x, y) => y * map.cols + x;
    if (from[0] === to[0] && from[1] === to[1]) return [];
    const dist = new Map([[key(from[0], from[1]), 0]]);
    const prev = new Map();
    const open = [[0, from[0], from[1]]];
    const goal = key(to[0], to[1]);
    while (open.length) {
      let best = 0;
      for (let i = 1; i < open.length; i++) if (open[i][0] < open[best][0]) best = i;
      const [d, x, y] = open.splice(best, 1)[0];
      if (key(x, y) === goal) break;
      if (d > (dist.get(key(x, y)) ?? Infinity)) continue;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = x + dx;
        const ny = y + dy;
        if (nx < 0 || ny < 0 || nx >= map.cols || ny >= map.rows) continue;
        const k = key(nx, ny);
        const c = k === goal ? 1 : map.cost(nx, ny);
        if (!Number.isFinite(c)) continue;
        const nd = d + c;
        if (nd < (dist.get(k) ?? Infinity)) {
          dist.set(k, nd);
          prev.set(k, [x, y]);
          open.push([nd, nx, ny]);
        }
      }
    }
    if (!prev.has(goal)) return null;
    const path = [];
    let cur = to;
    while (cur && !(cur[0] === from[0] && cur[1] === from[1])) {
      path.unshift(cur);
      cur = prev.get(key(cur[0], cur[1]));
    }
    return path;
  }

  // ---------------------------------------------------------------- efectos

  class Coins {
    constructor(x, y, n) {
      this.parts = Array.from({ length: n || 8 }, () => ({ x, y, vx: rand(-24, 24), vy: rand(-70, -35), life: rand(0.8, 1.3) }));
    }
    update(dt) {
      let alive = false;
      for (const p of this.parts) {
        if (p.life <= 0) continue;
        p.life -= dt;
        p.vy += 140 * dt;
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        alive = alive || p.life > 0;
      }
      return alive;
    }
    draw(g, cx, cy) {
      for (const p of this.parts) {
        if (p.life <= 0) continue;
        const x = Math.round(p.x - cx);
        const y = Math.round(p.y - cy);
        g.fillStyle = '#9c6512';
        g.fillRect(x, y, 3, 3);
        g.fillStyle = '#ffd84a';
        g.fillRect(x, y, 2, 2);
      }
    }
  }

  class Rain {
    constructor(actor) {
      this.actor = actor;
      this.life = 3.2;
      this.drops = [];
      this.clock = 0;
    }
    update(dt) {
      this.life -= dt;
      this.clock += dt;
      if (this.life > 0.6 && this.clock > 0.08) {
        this.clock = 0;
        this.drops.push({ x: rand(-6, 6), y: 0 });
      }
      for (const d of this.drops) d.y += 50 * dt;
      this.drops = this.drops.filter((d) => d.y < 16);
      return this.life > 0;
    }
    draw(g, cx, cy) {
      const ax = Math.round(this.actor.x - cx);
      const ay = Math.round(this.actor.y - cy) - 30;
      g.fillStyle = '#6b7390';
      P.ellipse(g, ax, ay + 1, 9, 4, '#5d6478');
      P.ellipse(g, ax - 3, ay, 5, 4, '#7d849a');
      P.ellipse(g, ax + 3, ay - 1, 5, 4, '#7d849a');
      g.fillStyle = '#7cc6fe';
      for (const d of this.drops) g.fillRect(ax + Math.round(d.x), ay + 4 + Math.round(d.y), 1, 2);
    }
  }

  class Sparkle {
    constructor(x, y) {
      this.x = x;
      this.y = y;
      this.life = 0.9;
    }
    update(dt) {
      this.life -= dt;
      return this.life > 0;
    }
    draw(g, cx, cy) {
      const p = 1 - this.life / 0.9;
      const r = Math.round(2 + p * 6);
      const x = Math.round(this.x - cx);
      const y = Math.round(this.y - cy);
      g.fillStyle = p < 0.5 ? '#ffffff' : '#ffe680';
      g.fillRect(x - r, y, 2, 1);
      g.fillRect(x + r - 1, y, 2, 1);
      g.fillRect(x, y - r, 1, 2);
      g.fillRect(x, y + r - 1, 1, 2);
    }
  }

  class Zzz {
    constructor(x, y) {
      this.x = x;
      this.y = y;
      this.life = 2.6;
    }
    update(dt) {
      this.life -= dt;
      return this.life > 0;
    }
    draw(g, cx, cy) {
      const p = 1 - this.life / 2.6;
      const x = Math.round(this.x - cx + Math.sin(p * 6) * 2 + p * 8);
      const y = Math.round(this.y - cy - p * 18);
      g.globalAlpha = Math.min(1, this.life);
      P.paint(g, ['oooo', 'o..o', 'o.o.', 'oo.o', 'oooo'], { o: '#2a2238' }, x - 1, y - 1);
      P.paint(g, ['xxx', '..x', '.x.', 'xxx'], { x: '#eef1fb' }, x, y);
      g.globalAlpha = 1;
    }
  }

  // ---------------------------------------------------------------- los vecinos en el mapa

  const SPEED = 30; // píxeles de arte por segundo
  let sayKey = 0;

  class Actor {
    constructor(town, bot) {
      this.town = town;
      this.id = bot.id;
      this.bot = bot;
      this.home = town.homeOf(bot.id);
      this.jump(this.home);
      this.dir = 'down';
      this.path = [];
      this.queue = [];
      this.action = null;
      this.walkClock = 0;
      this.idleIn = rand(3, 8);
      this.blinkIn = rand(1, 4);
      this.blinkFor = 0;
      this.inside = false;
      this.say = null;
      this.iconName = null;
      this.iconUntil = 0;
      this.hop = 0;
      this.tag = el('div', 'tag', bot.name);
      this.bubble = el('div', 'bubble');
      this.bubble.setAttribute('aria-hidden', 'true');
      town.overlay.append(this.tag, this.bubble);
    }
    setBot(bot) {
      this.bot = bot;
      if (this.tag.textContent !== bot.name) this.tag.textContent = bot.name;
    }
    get mood() {
      return this.bot.mood || 'ok';
    }
    tile() {
      return [Math.floor(this.x / T), Math.floor((this.y - 1) / T)];
    }
    jump(tile) {
      this.x = (tile[0] + 0.5) * T;
      this.y = (tile[1] + 1) * T - 3;
    }
    atHome() {
      const [x, y] = this.tile();
      return x === this.home[0] && y === this.home[1];
    }
    busy() {
      return Boolean(this.action) || this.queue.length > 0;
    }
    plan(...steps) {
      this.queue.push(...steps);
    }
    face(target) {
      if (typeof target === 'string') {
        this.dir = target;
        return;
      }
      if (!target) return;
      const dx = target.x - this.x;
      const dy = target.y - this.y;
      if (Math.abs(dx) >= Math.abs(dy)) this.dir = dx < 0 ? 'left' : 'right';
      else this.dir = dy < 0 ? 'up' : 'down';
    }
    icon(name, dur) {
      this.iconName = name;
      this.iconUntil = this.town.t + (dur || 2);
    }
    speak(text, kind, label, dur) {
      const shown = clip(text, 120);
      this.say = {
        text: shown, kind, label, key: ++sayKey,
        until: this.town.t + (dur || clamp(2.8 + shown.length * 0.05, 3, 9)),
      };
      return this.say.until;
    }

    update(dt) {
      const now = this.town.t;
      this.blinkIn -= dt;
      if (this.blinkIn <= 0) {
        this.blinkFor = 0.14;
        this.blinkIn = rand(2.5, 6);
      }
      if (this.blinkFor > 0) this.blinkFor -= dt;
      if (this.say && now > this.say.until) this.say = null;
      if (this.hop > 0) this.hop = Math.max(0, this.hop - dt);
      if (!this.action && this.queue.length) this.begin(this.queue.shift());
      if (this.action) {
        if (this.run(this.action, dt)) this.action = null;
        return;
      }
      this.idle(dt);
    }
    begin(step) {
      this.action = step;
      if (step.type === 'walk') {
        const to = typeof step.to === 'function' ? step.to() : step.to;
        this.inside = false;
        step.dest = to;
        if (!to) this.path = [];
        else if (this.town.reduced) {
          this.jump(to);
          this.path = [];
        } else this.path = findPath(this.town.map, this.tile(), to) || [];
      } else if (step.type === 'say') {
        step.until = this.speak(step.text, step.kind, step.label, step.dur);
      } else if (step.type === 'wait') {
        step.until = this.town.t + step.dur;
      }
    }
    run(step, dt) {
      if (step.type === 'walk') {
        if (!this.path.length) {
          this.walkClock = 0;
          if (step.face) this.face(step.face);
          return true;
        }
        const [tx, ty] = this.path[0];
        const gx = (tx + 0.5) * T;
        const gy = (ty + 1) * T - 3;
        const dx = gx - this.x;
        const dy = gy - this.y;
        const dist = Math.hypot(dx, dy);
        const v = SPEED * dt;
        if (Math.abs(dx) > Math.abs(dy)) this.dir = dx < 0 ? 'left' : 'right';
        else if (dy !== 0) this.dir = dy < 0 ? 'up' : 'down';
        this.walkClock += dt;
        if (dist <= v) {
          this.x = gx;
          this.y = gy;
          this.path.shift();
        } else {
          this.x += (dx / dist) * v;
          this.y += (dy / dist) * v;
        }
        return false;
      }
      if (step.type === 'say' || step.type === 'wait') return this.town.t >= step.until;
      if (step.type === 'face') this.face(step.dir);
      else if (step.type === 'call') step.fn(this);
      return true;
    }
    idle(dt) {
      this.walkClock = 0;
      const mood = this.mood;
      if (mood === 'sleep') {
        if (!this.inside) {
          if (!this.atHome()) {
            this.plan({ type: 'walk', to: this.home });
            return;
          }
          this.inside = true;
        }
        this.idleIn -= dt;
        if (this.idleIn <= 0) {
          this.idleIn = rand(2.2, 3.6);
          this.town.effects.push(new Zzz(this.x + 2, this.y - 12));
        }
        return;
      }
      if (this.inside) {
        this.inside = false;
        this.jump(this.home);
        this.dir = 'down';
      }
      this.idleIn -= dt;
      if (this.idleIn > 0) return;
      this.idleIn = rand(5, 12);
      if (!this.atHome()) {
        this.plan({ type: 'walk', to: this.home, face: 'down' });
        return;
      }
      const roll = Math.random();
      if (mood === 'sick' && roll < 0.6) {
        this.icon('drop', 2.2);
        return;
      }
      if (mood === 'alert' && roll < 0.5) {
        this.icon('alert', 2);
        this.hop = 0.35;
        return;
      }
      if (this.id === 'vigia') {
        this.dir = this.dir === 'left' ? 'right' : 'left';
        if (roll < 0.3) this.plan({ type: 'wait', dur: 1.6 }, { type: 'face', dir: 'down' });
        return;
      }
      if (this.id === 'kali' && roll < 0.4) {
        this.hop = 0.35;
        if (mood === 'happy') this.town.effects.push(new Coins(this.x, this.y - 18, 3));
        return;
      }
      if (this.id === 'cronista' && roll < 0.3) {
        this.plan(
          { type: 'walk', to: this.town.spot('board'), face: 'up' },
          { type: 'wait', dur: 2.5 },
          { type: 'walk', to: this.home, face: 'down' },
        );
        return;
      }
      if (roll < 0.5) {
        const near = this.town.near(this.home, 2, this);
        if (near) {
          this.plan({ type: 'walk', to: near }, { type: 'wait', dur: rand(1.2, 2.6) }, { type: 'walk', to: this.home, face: 'down' });
        }
      } else if (roll < 0.75) {
        this.dir = pick(['left', 'right', 'down']);
      }
    }

    view() {
      return this.dir === 'up' ? 'back' : this.dir === 'down' ? 'front' : 'side';
    }
    walking() {
      return Boolean(this.action && this.action.type === 'walk' && this.path.length);
    }
    eyes() {
      const base = P.moodEyes(this.mood);
      if (this.blinkFor > 0 && base !== 'sleep' && base !== 'sick') return 'blink';
      return base;
    }
    blit(g, sprite, x, y) {
      if (this.dir === 'right') {
        g.save();
        g.translate(x + 16, y);
        g.scale(-1, 1);
        g.drawImage(sprite, 0, 0);
        g.restore();
      } else {
        g.drawImage(sprite, x, y);
      }
    }
    draw(g, cx, cy, t) {
      const step = this.walking() ? [1, 0, 2, 0][Math.floor(this.walkClock * 8) % 4] : 0;
      const blink = Math.floor(t * 2) % 2 === 0;
      const lights = this.id === 'vigia' ? this.mood === 'alert' && blink : blink;
      const sprite = P.robot(this.bot, this.view(), step, this.eyes(), lights);
      let bob = step ? -1 : 0;
      if (this.hop > 0) bob -= Math.round(Math.sin(((0.35 - this.hop) / 0.35) * Math.PI) * 3);
      else if (this.mood === 'happy' && !this.busy() && Math.floor(t * 2) % 2) bob -= 1;
      const x = Math.round(this.x - 8 - cx);
      const y = Math.round(this.y - 18 - cy) + bob;
      const feet = Math.round(this.y - cy);
      g.fillStyle = 'rgba(20, 20, 40, 0.28)';
      g.fillRect(x + 4, feet - 1, 8, 1);
      g.fillRect(x + 3, feet, 10, 1);
      this.blit(g, sprite, x, y);
      this.drawn = { x, y };
    }
    drawEyes(g) {
      if (this.inside || !this.drawn || this.view() === 'back') return;
      this.blit(g, P.robotEyes(this.bot, this.view(), this.eyes()), this.drawn.x, this.drawn.y);
    }
    drawIcon(g, cx, cy, t) {
      if (this.inside || this.say || !this.iconName || this.town.t > this.iconUntil) return;
      const x = Math.round(this.x - cx - 5);
      const y = Math.round(this.y - cy - 34 + Math.sin(t * 5));
      g.drawImage(P.icon(this.iconName), x, y);
    }
    placeOverlay(toX, toY, width, placed, tags) {
      const sx = toX(this.x);
      const feet = toY(this.y);
      if (!this.inside && !this.say) {
        this.tag.hidden = false;
        if (!this.tagW || this.tagName !== this.bot.name) {
          this.tagName = this.bot.name;
          this.tagW = this.tag.offsetWidth;
          this.tagH = this.tag.offsetHeight;
        }
        const left = clamp(sx - this.tagW / 2, 2, Math.max(2, width - this.tagW - 2));
        let top = feet + 1;
        for (const r of tags) {
          if (left < r.left + r.w + 2 && left + this.tagW + 2 > r.left && top < r.top + r.h && top + this.tagH > r.top) {
            top = r.top + r.h + 1;
          }
        }
        tags.push({ left, top, w: this.tagW, h: this.tagH });
        this.tag.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
      } else {
        this.tag.hidden = true;
      }
      const b = this.bubble;
      if (this.inside || !this.say) {
        if (b.dataset.key) {
          b.classList.remove('on');
          b.dataset.key = '';
        }
        return;
      }
      if (b.dataset.key !== String(this.say.key)) {
        b.dataset.key = String(this.say.key);
        b.className = `bubble ${this.say.kind || 'info'}`;
        b.replaceChildren();
        if (this.say.label) b.append(el('span', 'from', `${this.bot.name} ${this.say.label}`));
        b.append(document.createTextNode(this.say.text));
        this.bw = b.offsetWidth;
        this.bh = b.offsetHeight;
        requestAnimationFrame(() => b.classList.add('on'));
      }
      const head = toY(this.y - 20);
      let top = head - this.bh - 9;
      let below = false;
      if (top < 4) {
        top = feet + 12;
        below = true;
      }
      const left = clamp(sx - this.bw / 2, 4, Math.max(4, width - this.bw - 4));
      for (const r of placed) {
        const overlapX = left < r.left + r.w && left + this.bw > r.left;
        const overlapY = top < r.top + r.h && top + this.bh > r.top;
        if (overlapX && overlapY) top = below ? r.top + r.h + 6 : r.top - this.bh - 6;
      }
      b.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
      b.style.setProperty('--tail', `${Math.round(clamp(sx - left, 10, this.bw - 10))}px`);
      b.classList.toggle('below', below);
      placed.push({ left, top, w: this.bw, h: this.bh });
    }
    remove() {
      this.tag.remove();
      this.bubble.remove();
    }
  }

  // ---------------------------------------------------------------- el mapa

  class Town {
    constructor(canvas, overlay) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.overlay = overlay;
      this.map = P.town();
      [this.view, this.vctx] = P.makeCanvas(1, 1);
      this.actors = new Map();
      this.effects = [];
      this.labels = [];
      this.t = 0;
      this.acc = 0;
      this.last = 0;
      this.scale = 1;
      this.dpr = 1;
      this.vw = 1;
      this.vh = 1;
      this.cam = { x: 0, y: 0 };
      this.width = 0;
      this.flags = { alert: false, mail: 0, notes: 0, kaliDown: false };
      this.reduced = Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
      this.clouds = [0, 1, 2].map(() => ({ x: rand(0, this.map.W), y: rand(0, this.map.H), r: rand(24, 40), v: rand(2.5, 5) }));
      this.onPick = null;
      this.addLabels();
      if ('ResizeObserver' in window) new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
      window.addEventListener('resize', () => this.resize());
      this.resize();
      canvas.addEventListener('click', (e) => this.click(e));
      canvas.addEventListener('mousemove', (e) => this.hover(e));
      requestAnimationFrame((ts) => this.loop(ts));
    }

    addLabels() {
      const names = { laboratorio: 'Pruebas', arena: 'Forja', casa: 'Tu casa' };
      for (const th of this.map.things) {
        if (!names[th.id]) continue;
        const node = el('div', 'place-label', names[th.id]);
        node.setAttribute('aria-hidden', 'true');
        this.overlay.append(node);
        this.labels.push({ node, x: th.x + th.w / 2, y: th.y + (th.id === 'casa' ? 1 : 3) });
      }
    }
    homeOf(id) {
      if (this.map.spots[id]) return this.map.spots[id];
      return this.near(this.map.spots.plaza, 2) || this.map.spots.plaza;
    }
    spot(name) {
      return this.map.spots[name];
    }
    occupied(x, y, except) {
      for (const a of this.actors.values()) {
        if (a === except) continue;
        const [ax, ay] = a.tile();
        if (ax === x && ay === y) return true;
        const dest = a.action && a.action.dest;
        if (dest && dest[0] === x && dest[1] === y) return true;
      }
      return false;
    }
    near(tile, r, except) {
      const options = [];
      for (let dy = -r; dy <= r; dy++) {
        for (let dx = -r; dx <= r; dx++) {
          const x = tile[0] + dx;
          const y = tile[1] + dy;
          if ((dx || dy) && this.map.cost(x, y) <= 3 && this.visible(x, y) && !this.occupied(x, y, except)) {
            options.push([x, y]);
          }
        }
      }
      return options.length ? pick(options) : null;
    }
    visible(x, y) {
      const px = (x + 0.5) * T - this.cam.x;
      const py = (y + 1) * T - this.cam.y;
      return px > 10 && px < this.vw - 10 && py > 22 && py < this.vh - 6;
    }
    // Una casilla libre al lado de `target`, la más cercana a quien llega.
    beside(target, who) {
      const [fx, fy] = who.tile();
      const options = [[-1, 0], [1, 0], [0, 1], [0, -1]]
        .map(([dx, dy]) => [target[0] + dx, target[1] + dy])
        .filter(([x, y]) => this.map.cost(x, y) <= 3 && !this.occupied(x, y, who));
      options.sort((a, b) => Math.abs(a[0] - fx) + Math.abs(a[1] - fy) - (Math.abs(b[0] - fx) + Math.abs(b[1] - fy)));
      return options[0] || target;
    }
    free(target, who) {
      return this.occupied(target[0], target[1], who) ? this.beside(target, who) : target;
    }
    // Los que están sin hacer nada cerca se giran a mirar a quien habla.
    listen(speaker) {
      for (const a of this.actors.values()) {
        if (a === speaker || a.busy() || a.inside) continue;
        if (Math.abs(a.x - speaker.x) + Math.abs(a.y - speaker.y) < T * 6) a.face(speaker);
      }
    }

    sync(bots) {
      const seen = new Set();
      for (const b of bots) {
        seen.add(b.id);
        const actor = this.actors.get(b.id);
        if (actor) actor.setBot(b);
        else this.actors.set(b.id, new Actor(this, b));
      }
      for (const [id, actor] of this.actors) {
        if (!seen.has(id)) {
          actor.remove();
          this.actors.delete(id);
        }
      }
      const kali = bots.find((b) => b.id === 'kali');
      const today = kali && kali.detail && kali.detail.today;
      this.flags.kaliDown = Boolean(today && num(today.net) < 0);
      this.flags.alert = bots.some((b) => b.id === 'vigia' && b.mood === 'alert');
    }

    resize() {
      const box = this.canvas.parentElement;
      const w = box.clientWidth;
      const h = box.clientHeight;
      if (!w || !h) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const W = Math.round(w * dpr);
      const H = Math.round(h * dpr);
      const core = this.map.core;
      // Escala entera (píxeles nítidos); se puede recortar un poco el borde del pueblo.
      const s = Math.max(1, Math.floor(Math.min(W / (core.w - 16), H / (core.h - 16))));
      this.width = w;
      if (W === this.canvas.width && H === this.canvas.height && s === this.scale) return;
      this.canvas.width = W;
      this.canvas.height = H;
      this.canvas.style.width = `${w}px`;
      this.canvas.style.height = `${h}px`;
      this.dpr = dpr;
      this.scale = s;
      this.vw = Math.ceil(W / s);
      this.vh = Math.ceil(H / s);
      this.view.width = this.vw;
      this.view.height = this.vh;
      this.vctx.imageSmoothingEnabled = false;
      this.ctx.imageSmoothingEnabled = false;
      const cx = core.x + core.w / 2;
      const cy = core.y + core.h / 2;
      const maxX = this.map.W - this.vw;
      const maxY = this.map.H - this.vh;
      this.cam.x = Math.round(maxX >= 0 ? clamp(cx - this.vw / 2, 0, maxX) : maxX / 2);
      this.cam.y = Math.round(maxY >= 0 ? clamp(cy - this.vh / 2, 0, maxY) : maxY / 2);
      this.draw();
    }

    loop(ts) {
      requestAnimationFrame((next) => this.loop(next));
      const dt = this.last ? Math.min(0.25, (ts - this.last) / 1000) : 0;
      this.last = ts;
      if (document.hidden) return;
      this.acc += dt;
      if (this.acc < 1 / 30) return;
      const step = Math.min(this.acc, 0.1);
      this.acc = 0;
      this.t += step;
      for (const a of this.actors.values()) a.update(step);
      this.effects = this.effects.filter((fx) => fx.update(step));
      for (const c of this.clouds) {
        c.x += c.v * step;
        if (c.x - c.r * 2 > this.map.W) {
          c.x = -c.r * 2;
          c.y = rand(0, this.map.H);
        }
      }
      this.draw();
    }

    draw() {
      const g = this.vctx;
      const cx = this.cam.x;
      const cy = this.cam.y;
      const t = this.reduced ? 0 : this.t;
      g.fillStyle = '#4b9fd5';
      g.fillRect(0, 0, this.vw, this.vh);
      g.drawImage(this.map.background, -cx, -cy);
      if (!this.reduced) this.drawWater(g, t);
      const items = [];
      for (const th of this.map.things) if (!th.baked) items.push([th.base, th]);
      for (const a of this.actors.values()) if (!a.inside) items.push([a.y, a]);
      items.sort((p, q) => p[0] - q[0]);
      for (const [, it] of items) {
        if (it instanceof Actor) it.draw(g, cx, cy, t);
        else {
          g.drawImage(it.canvas, it.x - cx, it.y - cy);
          if (it.dynamic) it.dynamic(g, it.x - cx, it.y - cy, t, this.flags);
        }
      }
      const phase = dayPhase(new Date());
      if (phase.dark < 0.5 && !this.reduced) this.drawClouds(g, cx, cy, 1 - phase.dark / 0.5);
      if (phase.dark > 0) this.drawNight(g, cx, cy, t, phase);
      for (const fx of this.effects) fx.draw(g, cx, cy);
      for (const a of this.actors.values()) a.drawIcon(g, cx, cy, t);
      this.ctx.drawImage(this.view, 0, 0, this.vw * this.scale, this.vh * this.scale);
      this.placeOverlay();
    }
    drawWater(g, t) {
      const m = this.map;
      const x0 = Math.max(0, Math.floor(this.cam.x / T));
      const y0 = Math.max(0, Math.floor(this.cam.y / T));
      const x1 = Math.min(m.cols - 1, Math.floor((this.cam.x + this.vw) / T));
      const y1 = Math.min(m.rows - 1, Math.floor((this.cam.y + this.vh) / T));
      g.fillStyle = '#a7dcf7';
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
          if (!m.isWater(x, y)) continue;
          const h = P.hash(x, y, 77);
          const phase = (t * 0.5 + h * 7) % 3;
          if (phase > 1.2) continue;
          const px = x * T + (Math.floor(h * 997) % 12) - this.cam.x;
          const py = y * T + (Math.floor(h * 7919) % 14) - this.cam.y;
          g.fillRect(px + Math.floor(phase * 2), py, phase < 0.6 ? 2 : 3, 1);
        }
      }
    }
    drawClouds(g, cx, cy, k) {
      const color = `rgba(24, 34, 80, ${(0.09 * k).toFixed(3)})`;
      for (const c of this.clouds) {
        const x = Math.round(c.x - cx);
        const y = Math.round(c.y - cy);
        P.ellipse(g, x, y, Math.round(c.r), Math.round(c.r * 0.45), color);
        P.ellipse(g, x + Math.round(c.r * 0.6), y - 4, Math.round(c.r * 0.55), Math.round(c.r * 0.35), color);
      }
    }
    drawNight(g, cx, cy, t, phase) {
      const k = phase.dark;
      const tint = P.mix(P.mix('#ffffff', '#ffb27a', phase.warm * 0.5), '#2f3570', k * 0.8);
      g.save();
      g.globalCompositeOperation = 'multiply';
      g.fillStyle = tint;
      g.fillRect(0, 0, this.vw, this.vh);
      if (k > 0.25) {
        const a = Math.min(1, (k - 0.25) / 0.5);
        g.globalCompositeOperation = 'lighter';
        for (const th of this.map.things) {
          if (th.glow) for (const l of th.glow(this.flags, t)) this.light(g, th.x + l.x - cx, th.y + l.y - cy, l.r, l.color, a);
          if (th.beam && !this.reduced) this.beam(g, th.x + th.beam.x - cx, th.y + th.beam.y - cy, t, a);
        }
        g.globalCompositeOperation = 'source-over';
        g.globalAlpha = a;
        g.fillStyle = '#ffd27a';
        for (const th of this.map.things) {
          if (th.id === 'torre' && this.flags.alert) continue;
          for (const [x, y, w, h] of th.windows || []) g.fillRect(th.x + x - cx, th.y + y - cy, w, h);
          for (const [x, y, w, h] of th.lit || []) g.fillRect(th.x + x - cx, th.y + y - cy, w, h);
        }
        g.globalAlpha = 1;
        for (const actor of this.actors.values()) actor.drawEyes(g);
      }
      g.restore();
    }
    light(g, x, y, r, color, a) {
      const grad = g.createRadialGradient(x, y, 0, x, y, r);
      grad.addColorStop(0, color);
      grad.addColorStop(1, 'rgba(0,0,0,0)');
      g.globalAlpha = a;
      g.fillStyle = grad;
      g.fillRect(x - r, y - r, r * 2, r * 2);
      g.globalAlpha = 1;
    }
    beam(g, x, y, t, a) {
      const angle = t * 0.7;
      const len = 130;
      const spread = 0.12;
      const grad = g.createRadialGradient(x, y, 0, x, y, len);
      grad.addColorStop(0, 'rgba(255,244,190,0.55)');
      grad.addColorStop(1, 'rgba(255,244,190,0)');
      g.globalAlpha = a;
      g.fillStyle = grad;
      for (const turn of [0, Math.PI]) {
        g.beginPath();
        g.moveTo(x, y);
        g.lineTo(x + Math.cos(angle + turn - spread) * len, y + Math.sin(angle + turn - spread) * len * 0.55);
        g.lineTo(x + Math.cos(angle + turn + spread) * len, y + Math.sin(angle + turn + spread) * len * 0.55);
        g.closePath();
        g.fill();
      }
      g.globalAlpha = 1;
    }
    placeOverlay() {
      const k = this.scale / this.dpr;
      const toX = (x) => (x - this.cam.x) * k;
      const toY = (y) => (y - this.cam.y) * k;
      for (const lb of this.labels) {
        lb.node.style.transform = `translate(${Math.round(toX(lb.x))}px, ${Math.round(toY(lb.y))}px) translate(-50%, -100%)`;
      }
      const placed = [];
      const tags = [];
      const list = Array.from(this.actors.values()).sort((a, b) => a.y - b.y || a.x - b.x);
      for (const a of list) a.placeOverlay(toX, toY, this.width, placed, tags);
    }

    pick(clientX, clientY) {
      const r = this.canvas.getBoundingClientRect();
      const k = this.scale / this.dpr;
      const x = (clientX - r.left) / k + this.cam.x;
      const y = (clientY - r.top) / k + this.cam.y;
      let best = null;
      for (const a of this.actors.values()) {
        if (a.inside) continue;
        if (x >= a.x - 11 && x <= a.x + 11 && y >= a.y - 24 && y <= a.y + 5 && (!best || a.y > best.y)) best = a;
      }
      if (best) return { bot: best.id };
      const hits = this.map.hits.filter((h) => x >= h.x && x < h.x + h.w && y >= h.y && y < h.y + h.h);
      hits.sort((a, b) => a.w * a.h - b.w * b.h);
      return hits.length ? { owner: hits[0].owner, place: hits[0].place } : null;
    }
    click(e) {
      const hit = this.pick(e.clientX, e.clientY);
      if (hit && this.onPick) this.onPick(hit);
    }
    hover(e) {
      this.canvas.classList.toggle('pointing', Boolean(this.pick(e.clientX, e.clientY)));
    }
  }

  // ---------------------------------------------------------------- de mensaje a escena

  const REACT = { alert: 'alert', trade: 'coin', diary: 'note', info: 'check', chat: 'check' };

  function botName(id) {
    if (id === 'tu') return 'ti';
    if (id === 'todos') return 'todos';
    const b = state.byId.get(id);
    return (b && b.name) || id;
  }

  function sayStep(m, label) {
    return { type: 'say', text: m.text, kind: m.kind, label };
  }

  // Convierte un mensaje en lo que se ve: quién camina hasta dónde y qué dice.
  function perform(m) {
    if (!town) return;
    if (view === '3d') town.perform(m, botName);
    else performPixel(m);
  }

  function setMail(n) {
    if (!town) return;
    if (view === '3d') town.setMail(n);
    else town.flags.mail = n;
  }

  function performPixel(m) {
    const a = town.actors.get(m.from);
    if (!a) return;
    const crowded = a.queue.length > 6;
    const walk = (to, face) => (crowded ? [] : [{ type: 'walk', to, face }]);
    const back = crowded ? [] : [{ type: 'walk', to: a.home, face: 'down' }];
    const label = `a ${botName(m.to)}`;
    if (m.kind === 'chat') {
      a.speak(m.text, 'chat', label);
      a.face('down');
      return;
    }
    if (m.from === 'kali' && m.kind === 'trade') {
      const net = num(m.data && m.data.net);
      const cashed = /^He cobrado|^Cobrado/.test(m.text);
      a.plan(...walk(a.home, 'down'), {
        type: 'call',
        fn: () => {
          if ((net !== null && net < 0) || m.text.includes('−$')) {
            town.effects.push(new Rain(a));
            a.icon('drop', 2.5);
          } else {
            town.effects.push(new Coins(a.x, a.y - 16, cashed || (net && net > 0) ? 10 : 4));
            a.hop = 0.35;
          }
        },
      }, sayStep(m, null));
      return;
    }
    if (m.to === 'tu' || m.kind === 'diary') {
      const diary = m.kind === 'diary';
      const target = diary ? town.spot('board') : town.spot('mailbox');
      a.plan(...walk(() => town.free(target, a), 'up'), {
        type: 'call',
        fn: () => {
          if (diary) town.flags.notes = Math.min(4, town.flags.notes + 1);
          if (m.to === 'tu') {
            town.flags.mail = unread();
            const [mx, my] = town.spot('mailbox');
            town.effects.push(new Sparkle((mx + 0.5) * T, my * T - 8));
          }
          town.listen(a);
        },
      }, sayStep(m, label), ...back);
      return;
    }
    const b = town.actors.get(m.to);
    if (!b) {
      a.plan(...walk(() => town.free(town.spot('plaza'), a), 'down'), { type: 'call', fn: () => town.listen(a) },
        sayStep(m, label), ...back);
      return;
    }
    a.plan(
      ...walk(() => town.beside(b.inside ? b.home : b.tile(), a), null),
      { type: 'call', fn: () => { a.face(b); if (!b.busy()) b.face(a); } },
      sayStep(m, label),
      {
        type: 'call',
        fn: () => {
          b.icon(/^Tranquila/.test(m.text) ? 'heart' : REACT[m.kind] || 'check', 2.4);
          if (m.kind === 'alert') b.hop = 0.35;
        },
      },
      { type: 'wait', dur: 0.8 },
      ...back,
    );
  }

  // ---------------------------------------------------------------- la pantalla

  const state = {
    source: null,
    bots: [],
    byId: new Map(),
    messages: [],
    known: new Set(),
    lastId: 0,
    filter: 'all',
    tab: 'diario',
    seenTu: Number(store('pueblo.visto')) || 0,
    selected: null,
    reply: null,
    timer: null,
    failures: 0,
    online: true,
    feedSig: '',
    cardsSig: '',
    fresh: new Set(),
    room: null,
    openTopics: new Set(),
    councilSig: '',
  };
  let town = null;
  let view = 'pixel'; // 'pixel' (el pueblo) o '3d' (la base)

  function unread() {
    return state.messages.filter((m) => m.to === 'tu' && m.id > state.seenTu).length;
  }

  function addMessages(list) {
    const added = [];
    for (const m of list) {
      if (state.known.has(m.id)) continue;
      state.known.add(m.id);
      state.messages.push(m);
      added.push(m);
    }
    state.messages.sort((a, b) => a.id - b.id);
    if (state.messages.length > 300) {
      for (const m of state.messages.splice(0, state.messages.length - 300)) state.known.delete(m.id);
    }
    return added;
  }

  function apply(snap, first) {
    if ((snap.last_id || 0) < state.lastId) {
      // El pueblo empezó de cero (por ejemplo, se borró su memoria): en la próxima vuelta se lee todo.
      state.messages = [];
      state.known.clear();
      state.lastId = 0;
    }
    state.bots = sortBots(snap.bots || []);
    state.byId = new Map(state.bots.map((b) => [b.id, b]));
    town.sync(state.bots);
    const added = addMessages(snap.messages || []);
    for (const m of added) state.lastId = Math.max(state.lastId, m.id);
    if (added.length) {
      let show = added;
      if (first) show = added.filter((m) => Date.now() - Date.parse(m.ts) < 15 * 60000).slice(-2);
      else if (added.length > 5) show = added.slice(-4);
      for (const m of show) perform(m);
      if (!first) for (const m of added) state.fresh.add(m.id);
    }
    if (first) setMail(unread());
    render();
  }

  function render() {
    renderHeader();
    renderFeeds();
    renderCards();
    renderBadge();
    renderCouncil();
    if (state.selected && $('#sheet').open) fillSheet(state.byId.get(state.selected));
    else if (state.room && $('#sheet').open) fillRoom(state.room);
  }

  function setConn(kind, text) {
    const conn = $('#conn');
    conn.dataset.state = kind;
    $('#conn-text').textContent = text;
  }

  function renderHeader() {
    const now = new Date();
    $('#clock').textContent = `${hm(now)} · ${dayPhase(now).name}`;
    if (!state.source) {
      setConn('wait', 'Falta entrar');
      return;
    }
    if (state.source.demo) {
      setConn('demo', 'Demostración');
      return;
    }
    if (!state.online) {
      setConn('off', 'Sin conexión');
      return;
    }
    const kali = state.byId.get('kali');
    const d = (kali && kali.detail) || {};
    if (!d.connected) setConn('warn', 'Kali sin panel');
    else setConn(d.state === 'running' ? 'ok' : 'warn', { running: 'Kali trabajando', halted: 'Kali frenada' }[d.state] || 'Kali en pausa');
  }

  function messageItem(m, isNew) {
    const li = el('li', `msg kind-${m.kind || 'info'}${isNew ? ' new' : ''}`);
    const from = state.byId.get(m.from) || { id: m.from, name: m.from, color: '#9aa3b5' };
    const img = el('img', 'ava');
    img.alt = '';
    img.width = 40;
    img.height = 40;
    img.src = P.avatar(from, 'ok');
    const body = el('div', 'msg-body');
    const head = el('div', 'msg-head');
    head.append(el('span', 'who', from.name || m.from), el('span', 'to', `→ ${botName(m.to)}`));
    if (KIND_LABEL[m.kind]) head.append(el('span', 'kind', KIND_LABEL[m.kind]));
    const time = el('time', null, ago(m.ts));
    time.dateTime = m.ts;
    head.append(time);
    body.append(head, el('p', 'msg-text', m.text));
    li.append(img, body);
    return li;
  }

  function renderFeeds(force) {
    const minute = Math.floor(Date.now() / 60000);
    const sig = `${state.lastId}|${state.messages.length}|${state.filter}|${minute}|${state.byId.size}`;
    if (!force && sig === state.feedSig) return;
    state.feedSig = sig;
    const items = state.messages.filter((m) => state.filter === 'all' || m.kind === state.filter).slice(-80).reverse();
    $('#feed').replaceChildren(...items.map((m) => messageItem(m, state.fresh.has(m.id))));
    $('#feed-empty').hidden = items.length > 0;
    const mine = state.messages.filter((m) => m.to === 'tu').slice(-60).reverse();
    $('#feed-tu').replaceChildren(...mine.map((m) => messageItem(m, state.fresh.has(m.id))));
    $('#tu-empty').hidden = mine.length > 0;
    state.fresh.clear();
  }

  function renderCards() {
    const minute = Math.floor(Date.now() / 60000);
    const sig = JSON.stringify(state.bots.map((b) => [b.id, b.name, b.mood, b.status, b.role])) + minute;
    if (sig === state.cardsSig) return;
    state.cardsSig = sig;
    const cards = state.bots.map((b) => {
      const btn = el('button', 'card bot-card');
      btn.type = 'button';
      const img = el('img', 'ava');
      img.alt = '';
      img.src = P.avatar(b, b.mood);
      const body = el('div');
      body.append(el('h3', null, b.name), el('p', 'role', b.role || ''), el('p', 'status', b.status || '…'));
      const meta = el('div', 'meta');
      meta.append(el('span', `mood mood-${b.mood || 'ok'}`, MOODS[b.mood] || 'Bien'));
      if (b.updated_at) meta.append(el('span', null, `visto ${ago(b.updated_at)}`));
      body.append(meta);
      btn.append(img, body);
      btn.addEventListener('click', () => openSheet(b.id));
      return btn;
    });
    $('#cards').replaceChildren(...cards);
  }

  function renderBadge() {
    const n = unread();
    const badge = $('#badge-tu');
    badge.hidden = n === 0 || state.tab === 'tu';
    badge.textContent = String(n);
    if (state.tab === 'tu') markRead();
  }

  function markRead() {
    const last = state.messages.filter((m) => m.to === 'tu').reduce((max, m) => Math.max(max, m.id), 0);
    if (last > state.seenTu) {
      state.seenTu = last;
      store('pueblo.visto', last);
    }
    setMail(0);
    $('#badge-tu').hidden = true;
  }

  function showTab(name) {
    state.tab = name;
    for (const tab of $$('.tab')) tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
    $('#panel-diario').hidden = name !== 'diario';
    $('#panel-tu').hidden = name !== 'tu';
    $('#panel-vecinos').hidden = name !== 'vecinos';
    $('#panel-consejo').hidden = name !== 'consejo';
    if (name === 'tu') markRead();
    if (name === 'consejo') renderCouncil(true);
    renderBadge();
  }

  // --- el Consejo: marcador y lo que opina cada uno ---

  const SHORT = { tormenta: 'Ca', dinero: 'Di', piloto: 'Pi', miedo: 'Mi', codicia: 'Co', claude: 'Cl' };

  function inkOn(hex) {
    // Letra oscura sobre colores claros y blanca sobre los oscuros.
    const v = parseInt(String(hex || '#888888').slice(1), 16);
    const lin = (c) => {
      const x = c / 255;
      return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4;
    };
    const L = 0.2126 * lin((v >> 16) & 255) + 0.7152 * lin((v >> 8) & 255) + 0.0722 * lin(v & 255);
    return L > 0.28 ? '#1c2135' : '#ffffff';
  }

  function memberDot(id, small) {
    const b = state.byId.get(id) || { id, name: id, color: '#9aa3b5' };
    const dot = el('span', `mdot${small ? ' small' : ''}`, SHORT[id] || String(b.name || id).slice(0, 2));
    dot.style.setProperty('--c', b.color || '#9aa3b5');
    dot.style.setProperty('--dot-ink', inkOn(b.color));
    return dot;
  }

  function kaliSide(ticker) {
    const pos = (((state.byId.get('kali') || {}).detail || {}).positions || []).find((x) => x.ticker === ticker);
    return pos ? pos.side : null;
  }

  // Una tira de 0 a 100¢: la raya es el precio del mercado y cada punto, el precio justo de uno.
  function strip(t) {
    const box = el('div', 'strip');
    box.setAttribute('role', 'img');
    const fairs = Object.entries(t.fairs || {}).sort((a, b) => a[1] - b[1]);
    const said = fairs.map(([id, f]) => `${(state.byId.get(id) || {}).name || id} ${cents(f)}`).join(', ');
    box.setAttribute('aria-label', `Se paga a ${cents(t.mid)}. ${said}.`);
    const track = el('div', 'track');
    for (const at of [0, 25, 50, 75, 100]) {
      const tick = el('span', 'tick');
      tick.style.left = `${at}%`;
      track.append(tick);
    }
    const market = el('span', 'market');
    market.style.left = `${clamp(num(t.mid) || 0, 0, 1) * 100}%`;
    market.title = `Mercado: se paga a ${cents(t.mid)}`;
    track.append(market);
    if (num(t.consensus) !== null) {
      const c = el('span', 'consensus');
      c.style.left = `${clamp(num(t.consensus), 0, 1) * 100}%`;
      c.title = `El Consejo (la mediana): ${cents(t.consensus)}`;
      track.append(c);
    }
    // Los puntos que caerían encima de otro bajan a otra fila.
    const lanes = [];
    for (const [id, f] of fairs) {
      const x = clamp(f, 0, 1) * 100;
      let lane = lanes.findIndex((last) => x - last >= 7);
      if (lane < 0) {
        lane = lanes.length;
        lanes.push(x);
      } else lanes[lane] = x;
      const dot = memberDot(id);
      dot.style.left = `${x}%`;
      dot.style.top = `${4 + lane * 24}px`;
      dot.title = `${(state.byId.get(id) || {}).name || id}: ${cents(f)}`;
      track.append(dot);
    }
    track.style.height = `${Math.max(1, lanes.length) * 24 + 10}px`;
    const scale = el('div', 'scale');
    for (const label of ['0¢', '50¢', '100¢']) scale.append(el('span', null, label));
    box.append(track, scale);
    return box;
  }

  function topicItem(t) {
    const li = el('li', 'topic');
    const det = el('details');
    det.open = state.openTopics.has(t.ticker);
    det.addEventListener('toggle', () => {
      if (det.open) state.openTopics.add(t.ticker);
      else state.openTopics.delete(t.ticker);
    });
    const sum = el('summary');
    const top = el('div', 'row-top');
    top.append(el('span', 'row-name', t.name || t.ticker));
    const side = kaliSide(t.ticker);
    if (side) top.append(el('span', `side ${side === 'NO' ? 'side-no' : 'side-si'}`, `Kali: ${side}`));
    const bits = [`se paga a ${cents(t.mid)}`];
    if (num(t.consensus) !== null) bits.push(`el Consejo dice ${cents(t.consensus)}`);
    sum.append(top, strip(t), el('div', 'row-sub', bits.join(' · ')));
    det.append(sum);
    // Al abrir: lo mismo en texto, de quien más paga a quien menos.
    const ul = el('ul', 'says');
    const reasons = ((state.byId.get('claude') || {}).detail || {}).opinions || {};
    for (const [id, f] of Object.entries(t.fairs || {}).sort((a, b) => b[1] - a[1])) {
      const li2 = el('li');
      const diff = Math.round((f - (num(t.mid) || 0)) * 100);
      li2.append(memberDot(id, true), el('b', null, (state.byId.get(id) || {}).name || id),
        document.createTextNode(` ${cents(f)} · ${diff === 0 ? 'como el mercado' : `${diff > 0 ? '+' : '−'}${Math.abs(diff)}¢`}`));
      if (id === 'claude' && reasons[t.ticker] && reasons[t.ticker].reason) li2.append(el('p', 'why', reasons[t.ticker].reason));
      ul.append(li2);
    }
    det.append(ul);
    li.append(det);
    return li;
  }

  function boardItem(r, i) {
    const li = el('li', 'board-row');
    const btn = el('button');
    btn.type = 'button';
    btn.addEventListener('click', () => openSheet(r.id));
    const pnl = num(r.pnl) || 0;
    const name = el('span', 'b-name');
    name.append(el('b', null, r.name),
      el('small', null, r.trades ? `${r.wins} de ${r.trades} acertadas · ${r.open || 0} abiertas` : `${r.open || 0} abiertas · ninguna decidida aún`));
    btn.append(el('span', 'b-rank', String(i + 1)), memberDot(r.id), name,
      el('span', `b-pnl ${r.trades ? (pnl > 0 ? 'up' : pnl < 0 ? 'down' : '') : ''}`, r.trades ? money(pnl, true) : '—'));
    li.append(btn);
    return li;
  }

  // Las maneras de jugar los partidos que prueba la Probadora, de la que más gana a la que menos.
  function testsTable(rows) {
    const ul = el('ul', 'rows');
    for (const r of rows) {
      const li = el('li', 'row');
      const top = el('div', 'row-top');
      const pnl = num(r.pnl) || 0;
      top.append(el('span', 'row-name', r.name),
        el('b', `test-pnl ${r.trades ? (pnl > 0 ? 'up' : pnl < 0 ? 'down' : '') : ''}`, r.trades ? money(pnl, true) : '—'));
      const lost = (r.trades || 0) - (r.wins || 0);
      const bits = r.trades
        ? [plural(r.trades, 'partido', 'partidos'), plural(lost, 'perdido', 'perdidos'), `peor día ${money(r.worst_day, true)}`]
        : ['ningún partido decidido aún'];
      li.append(top, el('div', 'row-sub', r.rule || ''), el('div', 'row-sub', bits.join(' · ')));
      ul.append(li);
    }
    return ul;
  }

  function renderCouncil(force) {
    if (state.tab !== 'consejo' && !force) return;
    const inv = state.byId.get('investigadora');
    const d = (inv && inv.detail) || {};
    const claude = ((state.byId.get('claude') || {}).detail || {}).opinions;
    const held = (((state.byId.get('kali') || {}).detail || {}).positions || []).map((x) => [x.ticker, x.side]);
    const tests = ((state.byId.get('probadora') || {}).detail || {}).variants || [];
    const sig = JSON.stringify([d.topics, d.board, claude, held, tests]);
    if (!force && sig === state.councilSig) return;
    state.councilSig = sig;
    const topics = d.topics || [];
    const board = d.board || [];
    $('#council-empty').hidden = topics.length > 0 || board.length > 0;
    $('#board').replaceChildren(...board.map(boardItem));
    $('#board-title').hidden = !board.length;
    const ids = [...new Set(topics.flatMap((t) => Object.keys(t.fairs || {})))];
    $('#legend').replaceChildren(...ids.map((id) => {
      const li = el('li');
      li.append(memberDot(id, true), document.createTextNode((state.byId.get(id) || {}).name || id));
      return li;
    }));
    const legend = el('li', 'legend-market');
    legend.append(el('span', 'mk'), document.createTextNode('precio del mercado'));
    const cons = el('li', 'legend-market');
    cons.append(el('span', 'cs'), document.createTextNode('el Consejo'));
    if (topics.length) $('#legend').append(legend, cons);
    $('#topics-title').hidden = !topics.length;
    $('#topics').replaceChildren(...topics.map(topicItem));
    $('#tests-title').hidden = !tests.length;
    $('#tests').replaceChildren(...(tests.length ? [testsTable(tests)] : []));
  }

  // --- la ficha de cada vecino ---

  function stat(label, value, small, sign) {
    const box = el('div', 'stat');
    const dd = el('dd', sign > 0 ? 'up' : sign < 0 ? 'down' : '', value);
    box.append(el('dt', null, label), dd);
    if (small) dd.append(el('small', null, small));
    return box;
  }

  // Lo que el Consejo cree que vale cada apuesta (de 0 a 1, en el lado que tiene Kali).
  function councilValue(p) {
    const topics = ((state.byId.get('investigadora') || {}).detail || {}).topics || [];
    const t = topics.find((x) => x.ticker === p.ticker);
    if (!t || t.consensus === null || t.consensus === undefined) return null;
    return p.side === 'NO' ? 1 - t.consensus : t.consensus;
  }

  function positionsList(positions, worstFirst) {
    const ul = el('ul', 'rows');
    const list = worstFirst ? [...positions].sort((a, b) => (num(a.chance) ?? 1) - (num(b.chance) ?? 1)) : positions;
    for (const p of list) {
      const li = el('li', 'row');
      const top = el('div', 'row-top');
      top.append(el('span', 'row-name', p.name || p.ticker), el('span', `side ${p.side === 'NO' ? 'side-no' : 'side-si'}`, p.side));
      const chance = num(p.chance);
      const bar = el('div', `bar${chance !== null && chance < 0.6 ? ' low' : ''}`);
      bar.setAttribute('role', 'img');
      bar.setAttribute('aria-label', `Probabilidad de ganar: ${pct(p.chance)}`);
      const fill = el('span');
      fill.style.width = `${Math.round(clamp(chance ?? 0, 0, 1) * 100)}%`;
      bar.append(fill);
      const bits = [`${pct(p.chance)} de ganar`, `pagó ${money(p.cost)}`];
      if (num(p.payout) !== null) bits.push(`cobra ${money(p.payout)}`);
      if (p.hours_to_close !== undefined) bits.push(closesIn(p.hours_to_close));
      li.append(top, bar, el('div', 'row-sub', bits.filter(Boolean).join(' · ')));
      const fair = councilValue(p);
      if (fair !== null) {
        const paid = num(p.cost) !== null && num(p.contracts) ? num(p.cost) / num(p.contracts) : null;
        const worse = paid !== null && fair < paid - 0.05;
        li.append(el('div', `row-sub council-says${worse ? ' worse' : ''}`,
          `El Consejo cree que vale ${cents(fair)}${paid !== null ? ` (la pagó a ${cents(paid)})` : ''}`));
      }
      ul.append(li);
    }
    return ul;
  }

  const DETAIL = {
    kali(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      if (!d.connected) {
        box.append(el('p', 'note warn', 'Kali todavía no ve el bot. Vuelve en un minuto: el pueblo acaba de arrancar.'));
        return box;
      }
      const today = d.today || {};
      const yesterday = d.yesterday || {};
      const totals = d.totals || {};
      const stats = el('dl', 'stats');
      stats.append(
        stat('Saldo', money(d.balance && d.balance.equity)),
        stat('Hoy', money(today.net, true),
          `${plural(today.markets || 0, 'mercado', 'mercados')} · ${plural(today.wins || 0, 'ganado', 'ganados')}`, num(today.net)),
        stat('Ayer', money(yesterday.net, true), plural(yesterday.markets || 0, 'mercado', 'mercados'), num(yesterday.net)),
        stat('Desde el principio', money(totals.net, true), plural(totals.markets || 0, 'mercado', 'mercados'), num(totals.net)),
      );
      box.append(stats);
      const running = { running: 'Bot activo', halted: 'Frenado por el freno de emergencia', stopped: 'En pausa' }[d.state] || 'Sin datos';
      const mode = { live: 'con dinero real', sim: 'en simulación, sin dinero' }[d.mode] || '';
      box.append(el('p', 'row-sub', [running, mode].filter(Boolean).join(' · ')));
      const positions = d.positions || [];
      box.append(el('h3', null, positions.length ? `Apuestas abiertas (${positions.length})` : 'Sin apuestas abiertas'));
      if (positions.length) box.append(positionsList(positions));
      box.append(el('p', 'note', 'Desde el pueblo solo se mira. Para cambiar algo, usa el panel del bot.'));
      return box;
    },
    nube(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      const rows = d.cities || [];
      if (!rows.length) {
        box.append(el('p', 'note', 'Todavía no tengo previsiones. Dame unos minutos.'));
        return box;
      }
      const wrap = el('div', 'table-wrap');
      const table = el('table');
      const head = el('tr');
      for (const h of ['Ciudad', 'Hoy', 'Máx. medida', 'Ahora', 'Mañana']) head.append(el('th', null, h));
      const thead = el('thead');
      thead.append(head);
      const tbody = el('tbody');
      for (const r of rows) {
        const tr = el('tr');
        tr.append(el('td', null, r.city), el('td', null, deg(r.today)), el('td', null, deg(r.max_so_far)), el('td', null, deg(r.now)), el('td', null, deg(r.tomorrow)));
        tbody.append(tr);
      }
      table.append(thead, tbody);
      wrap.append(table);
      box.append(wrap);
      box.append(el('p', 'row-sub', 'Hoy y mañana: máxima prevista por el Servicio Meteorológico de EE. UU. Máx. medida: lo más alto que ha marcado hoy la estación con la que Kalshi decide.'));
      if (d.errors && d.errors.length) box.append(el('p', 'note warn', `Sin datos ahora de: ${d.errors.join(', ')}`));
      return box;
    },
    vigia(b) {
      const d = b.detail || {};
      const kali = (state.byId.get('kali') || {}).detail || {};
      const box = el('div', 'detail');
      const stats = el('dl', 'stats');
      stats.append(stat('Apuestas torcidas', String(d.risky || 0)), stat('Avisos en 24 h', String(d.alerts_today || 0)));
      box.append(stats);
      const positions = kali.positions || [];
      if (positions.length) {
        box.append(el('h3', null, 'Las apuestas de Kali, de la más floja a la más segura'));
        box.append(positionsList(positions, true));
      }
      return box;
    },
    radar(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      if (d.scanned === undefined) {
        box.append(el('p', 'note', 'Todavía no he dado mi primera vuelta por los mercados.'));
        return box;
      }
      const stats = el('dl', 'stats');
      stats.append(stat('Mercados mirados', String(d.scanned)), stat('Favoritos claros', String(d.favorites)), stat('Fuera de Kali', String(d.outside)));
      box.append(stats);
      const series = d.by_series || [];
      if (series.length) {
        box.append(el('h3', null, 'Dónde están los que Kali no mira'));
        const ul = el('ul', 'rows');
        const top = Math.max(...series.map((s) => s.count));
        for (const s of series) {
          const li = el('li', 'row');
          const row = el('div', 'row-top');
          row.append(el('span', 'row-name', s.label), el('span', 'row-sub', String(s.count)));
          const bar = el('div', 'bar');
          const fill = el('span');
          fill.style.width = `${Math.round((s.count / top) * 100)}%`;
          bar.append(fill);
          li.append(row, bar);
          ul.append(li);
        }
        box.append(ul);
      }
      const examples = d.examples || [];
      if (examples.length) {
        box.append(el('h3', null, 'Ejemplos'));
        const ul = el('ul', 'rows');
        for (const e of examples.slice(0, 6)) ul.append(el('li', 'row', e.label || e.name));
        box.append(ul);
      }
      box.append(el('p', 'note', 'Radar solo mira: Kali no compra lo que él encuentra. Sirve para saber si merece la pena que el bot siga más series.'));
      return box;
    },
    investigadora(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      const stats = el('dl', 'stats');
      const leader = (d.board || []).find((r) => r.id === d.leader);
      stats.append(stat('Mercados en el Consejo', String((d.topics || []).length)),
        stat('Apuestas abiertas', String(d.open || 0), 'de mentira'));
      box.append(stats);
      if (leader) box.append(el('p', 'row-sub', `Va ganando ${leader.name}: ${money(leader.pnl, true)} de mentira (${leader.wins} de ${leader.trades} acertadas).`));
      const btn = el('button', 'btn ghost', 'Ver el marcador del Consejo');
      btn.type = 'button';
      btn.addEventListener('click', () => {
        closeSheet();
        goTab('consejo');
      });
      box.append(btn);
      return box;
    },
    herrera(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      if (!d.connected) {
        box.append(el('p', 'note warn', 'La Herrera todavía no ve los resultados del bot. Vuelve en un minuto.'));
        return box;
      }
      const t = d.totals;
      if (!t) {
        box.append(el('p', 'note', 'Kali todavía no ha cerrado mercados en estos 30 días.'));
        return box;
      }
      const stats = el('dl', 'stats');
      stats.append(
        stat(`Últimos ${d.days || 30} días`, money(t.net, true), `${plural(t.markets, 'mercado', 'mercados')} · ${plural(t.losses, 'perdido', 'perdidos')}`, num(t.net)),
        stat('Acierto', pct(t.rate), `necesita ${pct(t.need)} con lo que paga`, num(t.rate) >= num(t.need) ? 1 : -1),
      );
      box.append(stats);
      for (const a of d.advice || []) box.append(el('p', `note${a.level === 'alert' ? ' warn' : ''}`, a.text));
      const groups = d.groups || {};
      const sections = [['Por liga y ciudad', groups.series], ['Por precio de compra', groups.bands], ['Por tipo', groups.kinds]];
      for (const [title, list] of sections) {
        if (!list || !list.length) continue;
        box.append(el('h3', null, title), groupRows(list));
      }
      const losses = d.losses || [];
      if (losses.length) {
        box.append(el('h3', null, 'Últimas pérdidas'));
        const ul = el('ul', 'rows');
        for (const l of losses.slice(0, 8)) {
          const li = el('li', 'row');
          const top = el('div', 'row-top');
          top.append(el('span', 'row-name', l.name), el('b', 'test-pnl down', money(l.net, true)));
          li.append(top, el('div', 'row-sub', [l.label, `${l.side} a ${cents(l.price)}`, l.date].filter(Boolean).join(' · ')));
          ul.append(li);
        }
        box.append(ul);
      }
      box.append(el('p', 'note', 'Son los mercados que ha cerrado el bot con dinero real (los compras tuyas a mano no cuentan). La Herrera no cambia nada: los ajustes se cambian en el panel.'));
      return box;
    },
    probadora(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      const rows = d.variants || [];
      if (!rows.length) {
        box.append(el('p', 'note', 'Todavía no he probado ningún partido. Empiezo con los que acaban en las próximas 6 horas.'));
        return box;
      }
      box.append(testsTable(rows));
      const last = d.last || [];
      if (last.length) {
        box.append(el('h3', null, 'Últimos partidos (Kali tal cual, de mentira)'));
        const ul = el('ul', 'rows');
        for (const t of last.slice(0, 6)) {
          const li = el('li', 'row');
          const top = el('div', 'row-top');
          const n = num(t.pnl);
          top.append(el('span', 'row-name', t.name || t.ticker),
            el('span', `row-sub ${n > 0 ? 'up' : n < 0 ? 'down' : ''}`, `${t.won ? 'ganado' : 'perdido'} · ${money(n, true)}`));
          li.append(top, el('div', 'row-sub', `${t.side} a ${cents(t.price)} · ${t.count} contratos`));
          ul.append(li);
        }
        box.append(ul);
      }
      box.append(el('p', 'note', 'Todo es de mentira: no compra nada. Supone que se habría llevado el precio que había en ese momento.'));
      return box;
    },
    claude(b) {
      const d = b.detail || {};
      const box = el('div', 'detail');
      const row = boardRow(b.id);
      const stats = el('dl', 'stats');
      stats.append(stat('Preguntas hoy', `${d.calls || 0} de ${d.limit || '—'}`),
        stat('Marcador', row && row.trades ? money(row.pnl, true) : '—',
          row && row.trades ? `${row.wins} de ${row.trades} acertadas` : 'sin apuestas decididas', row ? num(row.pnl) : 0));
      box.append(stats);
      const ops = Object.entries(d.opinions || {}).map(([ticker, o]) => ({ ticker, ...o }))
        .sort((x, y) => String(y.at).localeCompare(String(x.at))).slice(0, 8);
      if (ops.length) {
        box.append(el('h3', null, 'Lo último que ha pensado'));
        const ul = el('ul', 'rows');
        for (const o of ops) {
          const li = el('li', 'row');
          const top = el('div', 'row-top');
          top.append(el('span', 'row-name', o.name || o.ticker), el('span', 'row-sub', pct(o.fair)));
          li.append(top, el('div', 'row-sub', o.reason || ''));
          ul.append(li);
        }
        box.append(ul);
      }
      box.append(el('p', 'note', 'Cada pregunta a Claude cuesta un poco de tu cuenta de Anthropic (unos céntimos). Hay un límite al día.'));
      return box;
    },
    cronista() {
      const box = el('div', 'detail');
      const diary = state.messages.filter((m) => m.from === 'cronista').slice(-5).reverse();
      box.append(el('p', 'row-sub', 'Escribe a las 8:00 y a las 21:30, y una noticia cuando Kali gana o pierde $2 o más de golpe.'));
      if (diary.length) {
        const ol = el('ol', 'feed mini');
        for (const m of diary) ol.append(messageItem(m));
        box.append(ol);
      }
      return box;
    },
  };

  const VERDICT = { gana: 'Gana', pierde: 'Pierde', 'va ganando': 'Va ganando', 'va perdiendo': 'Va perdiendo', justo: 'Justo', 'pocos datos': 'Pocos datos' };

  // Grupos de mercados de la Herrera: qué gana y qué pierde, con el acierto que necesita.
  function groupRows(list) {
    const ul = el('ul', 'rows');
    for (const g of list) {
      const li = el('li', 'row');
      const top = el('div', 'row-top');
      const net = num(g.net) || 0;
      const right = el('span', 'group-right');
      right.append(el('span', `verdict verdict-${String(g.verdict).replace(/ /g, '-')}`, VERDICT[g.verdict] || g.verdict),
        el('b', `test-pnl ${net > 0 ? 'up' : net < 0 ? 'down' : ''}`, money(net, true)));
      top.append(el('span', 'row-name', g.label), right);
      const bits = [plural(g.markets, 'mercado', 'mercados'), plural(g.losses, 'perdido', 'perdidos')];
      if (g.rate !== null && g.rate !== undefined) bits.push(`acierta ${pct(g.rate)} (necesita ${pct(g.need)})`);
      li.append(top, el('div', 'row-sub', bits.join(' · ')));
      ul.append(li);
    }
    return ul;
  }

  function boardRow(id) {
    return (((state.byId.get('investigadora') || {}).detail || {}).board || []).find((r) => r.id === id) || null;
  }

  function memberDetail(b) {
    const d = b.detail || {};
    const rec = d.record || {};
    const box = el('div', 'detail');
    const stats = el('dl', 'stats');
    stats.append(
      stat('Marcador', rec.trades ? money(rec.pnl, true) : '—', rec.trades ? 'de mentira' : 'sin apuestas decididas', num(rec.pnl)),
      stat('Acertadas', rec.trades ? `${rec.wins} de ${rec.trades}` : '—', rec.trades ? `rinde ${pct(rec.roi)}` : ''),
    );
    box.append(stats);
    const calls = Array.isArray(d.calls) ? d.calls : [];
    if (calls.length) {
      box.append(el('h3', null, 'Donde más se separa del mercado'));
      const ul = el('ul', 'rows');
      for (const c of calls.slice(0, 5)) {
        const li = el('li', 'row');
        const top = el('div', 'row-top');
        top.append(el('span', 'row-name', c.name || c.ticker), el('span', 'row-sub', `${cents(c.fair)} justo`));
        const diff = Math.round((num(c.edge) || 0) * 100);
        li.append(top, el('div', 'row-sub', `se paga a ${cents(c.mid)} · ${diff > 0 ? '+' : diff < 0 ? '−' : ''}${Math.abs(diff)}¢ ${diff >= 0 ? 'por encima' : 'por debajo'}`));
        ul.append(li);
      }
      box.append(ul);
    }
    const open = d.open || [];
    if (open.length) {
      box.append(el('h3', null, `Apuestas abiertas de mentira (${open.length})`));
      const ul = el('ul', 'rows');
      for (const t of open.slice(-6).reverse()) {
        const li = el('li', 'row');
        const top = el('div', 'row-top');
        top.append(el('span', 'row-name', t.name || t.ticker), el('span', `side ${t.side === 'NO' ? 'side-no' : 'side-si'}`, t.side));
        li.append(top, el('div', 'row-sub', `1 contrato a ${cents(t.price)} · para él vale ${cents(t.side === 'NO' ? 1 - t.fair : t.fair)}`));
        ul.append(li);
      }
      box.append(ul);
    }
    const last = d.last || [];
    if (last.length) {
      box.append(el('h3', null, 'Ya decididas'));
      const ul = el('ul', 'rows');
      for (const t of last.slice(0, 5)) {
        const li = el('li', 'row');
        const top = el('div', 'row-top');
        const n = num(t.pnl);
        top.append(el('span', 'row-name', t.name || t.ticker), el('span', `row-sub ${n > 0 ? 'up' : n < 0 ? 'down' : ''}`, `${n > 0 ? 'acertó' : 'falló'} · ${money(n, true)}`));
        li.append(top);
        ul.append(li);
      }
      box.append(ul);
    }
    box.append(el('p', 'note', 'Apuesta 1 contrato de mentira cuando su precio justo se separa del mercado más que la comisión. No usa dinero.'));
    return box;
  }

  function fillSheet(bot) {
    if (!bot) return;
    const body = $('#sheet-body');
    const head = el('div', 'sheet-head');
    const img = el('img', 'ava');
    img.alt = '';
    img.src = P.avatar(bot, bot.mood);
    const titles = el('div');
    const h2 = el('h2', null, bot.name);
    h2.id = 'sheet-title';
    titles.append(h2, el('p', 'role', bot.role || ''));
    head.append(img, titles, el('span', `mood mood-${bot.mood || 'ok'}`, MOODS[bot.mood] || 'Bien'));
    const parts = [head, el('p', 'status', bot.status || '…')];
    if (state.reply && state.reply.bot === bot.id) {
      if (state.reply.error) {
        parts.push(el('p', 'note warn', state.reply.text));
      } else {
        const reply = el('div', 'reply');
        reply.append(el('b', null, `${bot.name} te dice: `), document.createTextNode(state.reply.text));
        parts.push(reply);
      }
    }
    const make = DETAIL[bot.id] || ((bot.detail || {}).member ? memberDetail : null);
    const detail = make ? make(bot) : null;
    if (detail) parts.push(detail);
    if (bot.about) parts.push(el('p', 'about', bot.about));
    if (bot.id !== 'cronista') {
      const recent = state.messages.filter((m) => m.from === bot.id || m.to === bot.id).slice(-4).reverse();
      if (recent.length) {
        parts.push(el('h3', null, 'Últimos mensajes'));
        const ol = el('ol', 'feed mini');
        for (const m of recent) ol.append(messageItem(m));
        parts.push(ol);
      }
    }
    const scroll = $('#sheet').querySelector('.sheet-inner').scrollTop;
    body.replaceChildren(...parts);
    $('#sheet').querySelector('.sheet-inner').scrollTop = scroll;
  }

  function showSheet() {
    const sheet = $('#sheet');
    if (sheet.open) return;
    if (typeof sheet.showModal === 'function') sheet.showModal();
    else sheet.setAttribute('open', '');
  }
  function closeSheet() {
    const sheet = $('#sheet');
    if (sheet.open) {
      if (typeof sheet.close === 'function') sheet.close();
      else sheet.removeAttribute('open');
    }
    state.selected = null;
    state.room = null;
  }

  function openSheet(id) {
    const bot = state.byId.get(id);
    if (!bot) return;
    state.selected = id;
    state.room = null;
    state.reply = null;
    fillSheet(bot);
    $('#sheet').querySelector('.sheet-inner').scrollTop = 0;
    const talk = $('#talk-btn');
    talk.hidden = false;
    talk.disabled = false;
    talk.textContent = `Hablar con ${bot.name}`;
    showSheet();
  }

  // El dibujo de una sala en la cabecera de su ficha.
  function roomPicture(room) {
    if (view === 'pixel' && town && town.map) {
      const owner = { pruebas: 'lab', forja: 'arena' }[room] || room;
      const place = town.map.things.find((th) => th.owner === owner || th.id === room);
      const img = el('img', 'ava');
      img.alt = '';
      if (place) img.src = place.canvas.toDataURL('image/png');
      return img;
    }
    const box = el('span', `ava room-ava room-${room}`);
    box.setAttribute('aria-hidden', 'true');
    return box;
  }

  function goTab(name) {
    showTab(name);
    $('.tabs').scrollIntoView({ behavior: town && town.reduced ? 'auto' : 'smooth', block: 'start' });
  }

  // Quién está ahora en una sala y qué hace.
  function roomPeople(room) {
    const out = [];
    for (const b of state.bots) {
      const actor = town && town.actors && town.actors.get(b.id);
      const here = actor && actor.room ? actor.room : (b.activity && b.activity.room) || b.home;
      if (here === room) out.push(b);
    }
    return out;
  }

  function fillRoom(room) {
    const info = ROOM_INFO[room];
    if (!info) return;
    const h2 = el('h2', null, info[0]);
    h2.id = 'sheet-title';
    const head = el('div', 'sheet-head');
    const titles = el('div');
    titles.append(h2, el('p', 'role', 'Sala de la base'));
    const people = roomPeople(room);
    head.append(roomPicture(room), titles, el('span', 'mood', plural(people.length, 'bot', 'bots')));
    const parts = [head, el('p', 'about', info[1])];
    if (people.length) {
      parts.push(el('h3', null, 'Aquí ahora'));
      const ul = el('ul', 'rows');
      for (const b of people) {
        const li = el('li');
        const btn = el('button', 'row who-row');
        btn.type = 'button';
        const img = el('img', 'ava');
        img.alt = '';
        img.src = P.avatar(b, b.mood);
        const txt = el('div');
        const act = b.activity && b.activity.room === room ? b.activity.text : b.status;
        txt.append(el('span', 'row-name', b.name), el('div', 'row-sub', act || ''));
        btn.append(img, txt);
        btn.addEventListener('click', () => openSheet(b.id));
        li.append(btn);
        ul.append(li);
      }
      parts.push(ul);
    } else {
      parts.push(el('p', 'note', 'Ahora no hay nadie. Vendrán cuando tengan algo que hacer aquí.'));
    }
    const scroll = $('#sheet').querySelector('.sheet-inner').scrollTop;
    $('#sheet-body').replaceChildren(...parts);
    $('#sheet').querySelector('.sheet-inner').scrollTop = scroll;
  }

  function openRoom(room) {
    if (state.byId.has(ROOM_OWNER[room])) {
      openSheet(ROOM_OWNER[room]);
      return;
    }
    if (room === 'consejo' && state.source) {
      goTab('consejo');
      return;
    }
    if (room === 'puente' && state.source) {
      goTab('tu');
      return;
    }
    if (!ROOM_INFO[room]) return;
    state.selected = null;
    state.room = room;
    state.reply = null;
    fillRoom(room);
    $('#sheet').querySelector('.sheet-inner').scrollTop = 0;
    $('#talk-btn').hidden = true;
    showSheet();
  }

  function showReply(m, error) {
    state.reply = { bot: m.from, text: m.text, error: Boolean(error) };
    const bot = state.byId.get(m.from);
    if (bot) fillSheet(bot);
    $('#sheet').querySelector('.sheet-inner').scrollTo({ top: 0, behavior: town && town.reduced ? 'auto' : 'smooth' });
  }

  async function talk() {
    const id = state.selected;
    if (!id || !state.source) return;
    const btn = $('#talk-btn');
    btn.disabled = true;
    try {
      const m = await state.source.talk(id);
      addMessages([m]);
      state.fresh.add(m.id);
      showReply(m);
      perform(m);
      renderFeeds(true);
      renderBadge();
    } catch (err) {
      if (err.status === 401) {
        window.location.reload();
        return;
      }
      showReply({ from: id, text: 'No ha contestado ahora. Prueba otra vez en un momento.' }, true);
    } finally {
      btn.disabled = false;
    }
  }

  function onPick(hit) {
    if (hit.bot) {
      openSheet(hit.bot);
      return;
    }
    if (hit.room) {
      openRoom(hit.room);
      return;
    }
    if (hit.owner === 'tu') {
      if (!state.source) return;
      goTab('tu');
      return;
    }
    if (hit.owner === 'lab') {
      openRoom('pruebas');
      return;
    }
    if (hit.owner === 'arena') {
      openRoom('forja');
      return;
    }
    if (state.byId.has(hit.owner)) openSheet(hit.owner);
  }

  // ---------------------------------------------------------------- arranque

  function schedule(ms) {
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, ms);
  }

  async function poll() {
    if (!state.source) return;
    try {
      const snap = await state.source.world(state.lastId);
      state.online = true;
      state.failures = 0;
      apply(snap, false);
      schedule(state.source.every);
    } catch (err) {
      if (err.status === 401) {
        window.location.reload();
        return;
      }
      state.online = false;
      state.failures += 1;
      renderHeader();
      schedule(Math.min(30000, state.source.every * 2 ** state.failures));
    }
  }

  function start(source, snap) {
    state.source = source;
    $('#login').hidden = true;
    $('#app').hidden = false;
    $('#demo-banner').hidden = !source.demo;
    $('#logout-btn').textContent = source.demo ? 'Salir de la demostración' : 'Salir';
    $('#logout-btn').hidden = Boolean(source.demo && document.querySelector('meta[name="pueblo-mode"]'));
    apply(snap, true);
    schedule(source.every);
  }

  function startDemo() {
    const demo = new Demo();
    demo.world(0).then((snap) => start(demo, snap));
  }

  function showLogin(message) {
    $('#app').hidden = true;
    $('#login').hidden = false;
    $('#login-error').textContent = message || '';
    renderHeader();
  }

  async function onLogin(e) {
    e.preventDefault();
    const btn = $('#login-btn');
    btn.disabled = true;
    $('#login-error').textContent = '';
    try {
      await Api.login($('#password').value);
      window.location.reload();
    } catch (err) {
      const text = err.status === 429 ? err.message : err.status === 401 ? 'Esa no es la contraseña.' : 'No se pudo entrar. Revisa la conexión.';
      $('#login-error').textContent = text;
      btn.disabled = false;
    }
  }

  async function onLogout() {
    if (state.source && !state.source.demo) {
      try {
        await Api.logout();
      } catch (err) {
        // Aunque falle, al recargar se pide otra vez la contraseña si la sesión no vale.
      }
    }
    window.location.hash = '';
    window.location.reload();
  }

  function wantsPixel() {
    return window.location.hash.includes('pixel');
  }

  async function makeTown() {
    if (!wantsPixel()) {
      try {
        const mod = await import('./base3d.js');
        if (mod.supportsWebGL()) {
          const map = $('.map');
          map.classList.add('space');
          map.setAttribute('aria-label', 'La base de los bots');
          $('#map').hidden = true;
          const base = new mod.Base(map, $('#overlay'), { onMail: () => setMail(unread()) });
          view = '3d';
          return base;
        }
      } catch (err) {
        // Sin WebGL o sin el módulo: se queda el pueblo pixel, que funciona en cualquier móvil.
        console.warn('Base 3D no disponible', err);
      }
    }
    view = 'pixel';
    return new Town($('#map'), $('#overlay'));
  }

  async function boot() {
    town = await makeTown();
    town.onPick = onPick;
    town.sync(DEFAULT_BOTS.map((b) => ({ ...b, mood: 'ok', status: '', detail: {} })));
    $('#login-form').addEventListener('submit', onLogin);
    $('#logout-btn').addEventListener('click', onLogout);
    $('#talk-btn').addEventListener('click', talk);
    $('#sheet-close').addEventListener('click', closeSheet);
    $('#sheet').addEventListener('click', (e) => {
      if (e.target === e.currentTarget) closeSheet();
    });
    $('#sheet').addEventListener('close', () => {
      state.selected = null;
      state.room = null;
    });
    for (const tab of $$('.tab')) tab.addEventListener('click', () => showTab(tab.dataset.tab));
    for (const chip of $$('.chip')) {
      chip.addEventListener('click', () => {
        state.filter = chip.dataset.filter;
        for (const c of $$('.chip')) c.setAttribute('aria-pressed', String(c === chip));
        renderFeeds(true);
      });
    }
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && state.source) schedule(200);
    });
    setInterval(() => {
      renderHeader();
      renderFeeds();
    }, 20000);
    renderHeader();

    const meta = document.querySelector('meta[name="pueblo-mode"]');
    if ((meta && meta.content === 'demo') || window.location.hash.includes('demo')) {
      startDemo();
      return;
    }
    Api.world(0)
      .then((snap) => start(Api, snap))
      .catch((err) => showLogin(err.status === 401 ? '' : 'No se pudo conectar con el pueblo. Prueba a recargar.'));
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
