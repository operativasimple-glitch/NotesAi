/* La base en 3D: nueve salas flotando en el espacio y los bots caminando a la sala de lo que
 * hacen de verdad (Kali al Mercado cuando compra, Radar al Laboratorio cuando busca...).
 *
 * Se dibuja con Three.js (vendor/three.module.min.js). Cada sala mide 6 × 6 con paredes bajas y
 * puertas; detrás, paredes altas con luz de neón. Los textos (nombres, bocadillos, carteles) son
 * HTML encima del lienzo para que se lean nítidos en el móvil.
 */
import * as THREE from './vendor/three.module.min.js';

const SIZE = 6;
const EDGE = SIZE * 1.5; // la base va de -9 a 9
const WALL_H = 0.75;
const BACK_H = 1.5;
const DOOR = 1.7;
const SPEED = 1.8; // unidades por segundo
const AZIMUTH = Math.PI / 4;
const ELEVATION = 0.84;

export const ROOMS = {
  observatorio: { col: 0, row: 0, name: 'Observatorio', glow: '#7cc6fe', floor: '#dde8fb' },
  laboratorio: { col: 1, row: 0, name: 'Laboratorio', glow: '#5be0a0', floor: '#d9f2e7' },
  forja: { col: 2, row: 0, name: 'Forja', glow: '#ff8a3d', floor: '#e6dcd5' },
  archivo: { col: 0, row: 1, name: 'Archivo', glow: '#c792ea', floor: '#ebe1f7' },
  consejo: { col: 1, row: 1, name: 'Consejo', glow: '#f472b6', floor: '#f3e0ee' },
  mercado: { col: 2, row: 1, name: 'Mercado', glow: '#f2c94c', floor: '#f6edd3' },
  pruebas: { col: 0, row: 2, name: 'Pruebas', glow: '#5ee7f0', floor: '#d8e2ea' },
  puente: { col: 1, row: 2, name: 'Puente', glow: '#ff6b6b', floor: '#f2dfe4' },
  boveda: { col: 2, row: 2, name: 'Bóveda', glow: '#ffd166', floor: '#f5ead0' },
};

// Dónde se pone cada uno en una sala: [x, z, mira hacia x, mira hacia z] (relativo al centro).
const SPOTS = {
  observatorio: [[-0.9, -0.8, -1.6, -1.6], [0.9, -1.1, 0.9, -2.9], [0.4, 0.7, 0, -2], [-0.8, 0.9, -1.6, -1.6], [1.7, 0.4, 1.4, -1.9], [-1.9, 0.5, -2.9, 0.5]],
  laboratorio: [[1.5, -0.6, 1.9, -1.9], [-1.2, -1.4, -1.2, -2.4], [0.4, -1.4, 0.4, -2.4], [-0.5, 0.7, 0, -2], [0.9, 1.1, 0, -2], [-1.8, 1.0, -2.9, 1.0]],
  forja: [[0.0, 0.4, -1.4, -1.4], [1.2, 0.9, 0, -2], [-1.0, 1.3, -2, 0]],
  archivo: [[0.4, -0.9, 0.4, -1.7], [-1.5, 0.0, -2.6, 0.0], [1.7, -1.4, 1.8, -2.4], [0.6, 0.9, 0, -2], [-0.8, 1.4, -2.6, 1.0], [1.7, 1.1, 1.8, -2.4]],
  consejo: [],
  mercado: [[0.6, -1.5, 0.6, -2.4], [-1.3, -1.0, -2.3, -2.3], [1.9, -1.4, 1.9, -2.4], [-0.2, 0.7, -2.3, -2.3], [1.3, 1.1, 0.6, -2.4], [-1.2, 1.4, -2.6, 0.4]],
  pruebas: [[0.0, 0.5, -0.4, -0.4], [1.2, 1.0, 0, 0], [-1.1, 1.2, 0, 0]],
  puente: [[0.0, -1.3, 0.0, -2.2], [2.0, -1.4, 2.0, -2.3], [-1.4, -0.8, -2.3, -2.3], [-0.8, 1.0, 0, -2.2], [0.8, 1.2, 0, -2.2], [1.8, 0.5, 2.0, -2.3]],
  boveda: [[-1.1, -1.1, -2.1, -2.1], [0.8, -1.4, 0.8, -2.3], [0.2, 0.6, -2.1, -2.1], [1.6, 1.0, 0.8, -2.3], [-1.0, 1.4, -2.1, -2.1], [-1.9, 0.0, -2.1, -2.1]],
};
// Los asientos del Consejo, alrededor de la mesa redonda.
for (let i = 0; i < 9; i++) {
  const a = Math.PI * 0.75 + (i * Math.PI * 2) / 9;
  SPOTS.consejo.push([Math.cos(a) * 1.85, Math.sin(a) * 1.85, 0, 0]);
}
// Quién tiene sitio fijo (su "mesa") cuando está en su sala.
const DESK = { kali: 0, nube: 0, radar: 0, cronista: 0, vigia: 0, investigadora: 0, probadora: 0, herrera: 0 };
const MAIL_SPOT = 1; // en el Puente, la terminal con tus mensajes
const BOARD_SPOT = 2; // en el Archivo, el tablón del diario
// Para no atravesar muebles: círculos que se rodean al caminar.
const OBSTACLES = {
  consejo: [[0, 0, 1.5]],
  observatorio: [[1.4, -1.9, 0.6]],
  laboratorio: [[1.9, -1.9, 0.7]],
  boveda: [[-2.1, -2.1, 1.0]],
  mercado: [[-2.3, -2.3, 0.8]],
  puente: [[-2.3, -2.3, 0.8]],
};

export function supportsWebGL() {
  try {
    const c = document.createElement('canvas');
    return Boolean(window.WebGLRenderingContext && (c.getContext('webgl2') || c.getContext('webgl')));
  } catch (err) {
    return false;
  }
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
const rand = (a, b) => a + Math.random() * (b - a);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

function center(room) {
  const r = ROOMS[room];
  return new THREE.Vector3(-SIZE + SIZE * r.col, 0, -SIZE + SIZE * r.row);
}
function roomAt(x, z) {
  const col = clamp(Math.floor((x + EDGE) / SIZE), 0, 2);
  const row = clamp(Math.floor((z + EDGE) / SIZE), 0, 2);
  return Object.keys(ROOMS).find((k) => ROOMS[k].col === col && ROOMS[k].row === row);
}
function neighbors(room) {
  const r = ROOMS[room];
  return Object.keys(ROOMS).filter((k) => Math.abs(ROOMS[k].col - r.col) + Math.abs(ROOMS[k].row - r.row) === 1);
}
function door(a, b) {
  const ca = center(a);
  const cb = center(b);
  return ca.add(cb).multiplyScalar(0.5);
}
function routeRooms(a, b) {
  if (a === b) return [a];
  const prev = { [a]: null };
  const queue = [a];
  while (queue.length) {
    const cur = queue.shift();
    if (cur === b) break;
    for (const n of neighbors(cur)) {
      if (!(n in prev)) {
        prev[n] = cur;
        queue.push(n);
      }
    }
  }
  const path = [];
  for (let cur = b; cur; cur = prev[cur]) path.unshift(cur);
  return path;
}
// Si el tramo a→b atraviesa un mueble, se rodea.
function detour(a, b, room) {
  const list = OBSTACLES[room] || [];
  const c0 = center(room);
  for (const [ox, oz, r] of list) {
    const o = new THREE.Vector3(c0.x + ox, 0, c0.z + oz);
    const ab = b.clone().sub(a);
    const len2 = ab.lengthSq();
    if (len2 < 1e-6) continue;
    const t = clamp(o.clone().sub(a).dot(ab) / len2, 0, 1);
    const p = a.clone().add(ab.clone().multiplyScalar(t));
    if (p.distanceTo(o) < r && t > 0.02 && t < 0.98) {
      let n = p.clone().sub(o);
      if (n.lengthSq() < 1e-4) n = new THREE.Vector3(-ab.z, 0, ab.x);
      n.y = 0;
      n.normalize();
      const q = o.clone().add(n.multiplyScalar(r + 0.35));
      return [q, b];
    }
  }
  return [b];
}

// ------------------------------------------------------------------ materiales y formas

const matCache = new Map();
function mat(color, opts) {
  const key = color + JSON.stringify(opts || {});
  let m = matCache.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({ color, roughness: 0.72, metalness: 0.05, ...(opts || {}) });
    matCache.set(key, m);
  }
  return m;
}
function glowMat(color, intensity) {
  return mat(color, { emissive: color, emissiveIntensity: intensity === undefined ? 1.4 : intensity });
}
function mesh(geo, material, shadow) {
  const m = new THREE.Mesh(geo, material);
  if (shadow !== false) {
    m.castShadow = true;
    m.receiveShadow = true;
  }
  return m;
}
function box(w, h, d, material, x, y, z) {
  const m = mesh(new THREE.BoxGeometry(w, h, d), material);
  m.position.set(x || 0, (y || 0) + h / 2, z || 0);
  return m;
}
function cyl(rt, rb, h, material, x, y, z, seg) {
  const m = mesh(new THREE.CylinderGeometry(rt, rb, h, seg || 24), material);
  m.position.set(x || 0, (y || 0) + h / 2, z || 0);
  return m;
}

let glowTexture = null;
function halo(color, size, opacity) {
  if (!glowTexture) {
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d');
    const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grad.addColorStop(0, 'rgba(255,255,255,1)');
    grad.addColorStop(0.35, 'rgba(255,255,255,0.45)');
    grad.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grad;
    g.fillRect(0, 0, 64, 64);
    glowTexture = new THREE.CanvasTexture(c);
  }
  const s = new THREE.Sprite(new THREE.SpriteMaterial({
    map: glowTexture, color, transparent: true, opacity: opacity === undefined ? 0.6 : opacity,
    blending: THREE.AdditiveBlending, depthWrite: false,
  }));
  s.scale.set(size, size, 1);
  return s;
}

// Una pantalla con dibujo propio (se redibuja cuando cambian los datos).
function screen(w, h, px, draw) {
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(px);
  canvas.height = Math.round((px * h) / w);
  const ctx = canvas.getContext('2d');
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;
  const material = new THREE.MeshBasicMaterial({ map: texture, toneMapped: false });
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(w, h), material);
  const api = {
    mesh: plane, canvas, ctx, texture,
    redraw(data) {
      draw(ctx, canvas.width, canvas.height, data);
      texture.needsUpdate = true;
    },
  };
  api.redraw({});
  return api;
}

const FONT = '"Pixelify Sans", "Courier New", monospace';
const TEXT = '"Atkinson Hyperlegible", system-ui, sans-serif';

function panelBg(ctx, w, h, top, bottom) {
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, top || '#1d1846');
  grad.addColorStop(1, bottom || '#0e0b26');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = 'rgba(140,220,255,0.7)';
  ctx.lineWidth = Math.max(3, w / 120);
  ctx.strokeRect(ctx.lineWidth / 2, ctx.lineWidth / 2, w - ctx.lineWidth, h - ctx.lineWidth);
}

function money(v, sign) {
  const n = Number(v);
  if (!Number.isFinite(n)) return '—';
  const t = Math.abs(n).toFixed(2).replace('.', ',');
  return (n < 0 ? '−' : sign && n > 0 ? '+' : '') + '$' + t;
}

// ------------------------------------------------------------------ los robots

const EYE = { ok: '#9ff3ff', happy: '#9ff3ff', sad: '#a8c4ff', sleep: '#3d5a6e', sick: '#c7f59c', alert: '#ffd166' };

function accessory(id, head, color) {
  const top = 0.17;
  const g = new THREE.Group();
  const gold = mat('#ffcf33', { metalness: 0.6, roughness: 0.35, emissive: '#5a3d00', emissiveIntensity: 0.4 });
  if (id === 'kali') {
    g.add(cyl(0.014, 0.014, 0.16, mat('#6b7390'), 0, top, 0, 8));
    const coin = cyl(0.075, 0.075, 0.022, gold, 0, top + 0.17, 0, 20);
    coin.rotation.x = Math.PI / 2;
    coin.userData.spin = 2.2;
    g.add(coin);
  } else if (id === 'nube') {
    const white = mat('#ffffff', { emissive: '#d9ecff', emissiveIntensity: 0.25 });
    for (const [x, y, r] of [[-0.09, 0.05, 0.1], [0.04, 0.09, 0.13], [0.14, 0.04, 0.09]]) {
      const s = mesh(new THREE.SphereGeometry(r, 16, 12), white);
      s.position.set(x, top + y + 0.03, 0);
      g.add(s);
    }
  } else if (id === 'vigia') {
    g.add(cyl(0.09, 0.1, 0.05, mat('#2a2238'), 0, top, 0, 16));
    const siren = mesh(new THREE.SphereGeometry(0.08, 16, 10, 0, Math.PI * 2, 0, Math.PI / 2), glowMat('#ff4d4d', 0.6));
    siren.position.y = top + 0.05;
    siren.userData.siren = true;
    g.add(siren);
  } else if (id === 'radar') {
    g.add(cyl(0.012, 0.012, 0.14, mat('#6b7390'), 0.08, top, 0, 8));
    const dish = mesh(new THREE.SphereGeometry(0.1, 16, 8, 0, Math.PI * 2, 0, Math.PI / 2.6), mat('#eef3f7', { side: THREE.DoubleSide }));
    dish.position.set(0.08, top + 0.16, 0);
    dish.rotation.x = Math.PI;
    dish.userData.spin = 1.5;
    g.add(dish);
  } else if (id === 'cronista' || id === 'investigadora') {
    const rim = mat('#f2d27a', { metalness: 0.5, roughness: 0.4 });
    for (const x of [-0.085, 0.085]) {
      const ring = mesh(new THREE.TorusGeometry(0.06, 0.012, 8, 20), rim, false);
      ring.position.set(x, -0.005, 0.2);
      g.add(ring);
    }
    if (id === 'cronista') {
      const beret = cyl(0.21, 0.23, 0.06, mat('#5d3591'), -0.03, top - 0.01, 0, 20);
      beret.rotation.z = 0.15;
      g.add(beret);
    } else {
      const bun = mesh(new THREE.SphereGeometry(0.09, 14, 10), mat('#b8306f'));
      bun.position.set(0, top + 0.06, -0.05);
      g.add(bun);
    }
  } else if (id === 'herrera') {
    // Casco de trabajo y martillo.
    const helmet = mesh(new THREE.SphereGeometry(0.2, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), mat('#ff8a3d'));
    helmet.position.y = top - 0.02;
    helmet.scale.y = 0.6;
    g.add(helmet);
    g.add(cyl(0.22, 0.22, 0.02, mat('#e06d22'), 0, top - 0.02, 0, 22));
    const hammer = new THREE.Group();
    hammer.add(box(0.025, 0.2, 0.025, mat('#8a5a3c'), 0, 0, 0));
    hammer.add(box(0.1, 0.05, 0.05, mat('#5b6178', { metalness: 0.6 }), 0, 0.18, 0));
    hammer.position.set(0.24, -0.25, 0.05);
    hammer.rotation.z = -0.4;
    g.add(hammer);
  } else if (id === 'tormenta') {
    const bolt = glowMat('#ffe14d', 1.2);
    const parts = [[0.0, 0.05, 0.4], [0.03, 0.13, -0.5], [0.0, 0.21, 0.4]];
    for (const [x, y, rz] of parts) {
      const b = box(0.035, 0.11, 0.03, bolt, x, top + y - 0.05, 0);
      b.rotation.z = rz;
      g.add(b);
    }
  } else if (id === 'dinero') {
    const hat = mat('#1f6b45');
    g.add(cyl(0.25, 0.25, 0.025, hat, 0, top, 0, 24));
    g.add(cyl(0.15, 0.16, 0.2, hat, 0, top + 0.02, 0, 24));
    g.add(cyl(0.155, 0.155, 0.04, glowMat('#7dffb2', 0.6), 0, top + 0.05, 0, 24));
  } else if (id === 'piloto') {
    const cap = mesh(new THREE.SphereGeometry(0.21, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), mat('#26304f'));
    cap.position.y = top - 0.02;
    cap.scale.y = 0.55;
    g.add(cap);
    g.add(box(0.26, 0.02, 0.14, mat('#1b2238'), 0, top - 0.02, 0.2));
    g.add(box(0.07, 0.04, 0.01, gold, 0, top + 0.05, 0.19));
  } else if (id === 'miedo') {
    const drop = mesh(new THREE.SphereGeometry(0.045, 12, 10), mat('#8fd3ff', { emissive: '#4fa4e6', emissiveIntensity: 0.4 }));
    drop.position.set(0.25, 0.08, 0.1);
    drop.scale.y = 1.4;
    drop.userData.drip = true;
    g.add(drop);
  } else if (id === 'codicia') {
    g.add(cyl(0.16, 0.16, 0.07, gold, 0, top, 0, 20));
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2;
      const spike = mesh(new THREE.ConeGeometry(0.035, 0.09, 8), gold);
      spike.position.set(Math.cos(a) * 0.13, top + 0.11, Math.sin(a) * 0.13);
      g.add(spike);
    }
  } else if (id === 'claude') {
    const spark = glowMat('#ffd9c7', 0.9);
    const holder = new THREE.Group();
    holder.position.y = top + 0.14;
    for (let i = 0; i < 4; i++) {
      const ray = box(0.03, 0.24, 0.03, spark, 0, -0.12, 0);
      const pivot = new THREE.Group();
      pivot.rotation.z = (i * Math.PI) / 4;
      pivot.add(ray);
      holder.add(pivot);
    }
    holder.userData.spin = 0.8;
    g.add(holder);
  } else {
    g.add(cyl(0.014, 0.014, 0.12, mat('#6b7390'), 0, top, 0, 8));
    const ball = mesh(new THREE.SphereGeometry(0.045, 12, 10), glowMat(color, 0.6));
    ball.position.y = top + 0.15;
    g.add(ball);
  }
  head.add(g);
  return g;
}

function makeRobot(bot) {
  const color = new THREE.Color(bot.color || '#9aa3b5');
  const shade = color.clone().multiplyScalar(0.7);
  const light = color.clone().lerp(new THREE.Color('#ffffff'), 0.35);
  const root = new THREE.Group();
  const bodyPivot = new THREE.Group(); // sube y baja al caminar
  root.add(bodyPivot);
  const legs = [-0.075, 0.075].map((x) => {
    const pivot = new THREE.Group();
    pivot.position.set(x, 0.24, 0);
    const leg = cyl(0.045, 0.05, 0.22, mat('#6b7390'), 0, -0.24, 0, 10);
    const foot = box(0.1, 0.05, 0.14, mat('#2a2238'), 0, -0.25, 0.02);
    pivot.add(leg, foot);
    bodyPivot.add(pivot);
    return pivot;
  });
  const body = mesh(new THREE.CapsuleGeometry(0.16, 0.14, 4, 14), mat('#' + shade.getHexString()));
  body.position.y = 0.45;
  body.scale.z = 0.85;
  bodyPivot.add(body);
  const chest = box(0.16, 0.1, 0.02, mat('#' + light.getHexString()), 0, 0.43, 0.135);
  bodyPivot.add(chest);
  const arms = [-1, 1].map((side) => {
    const pivot = new THREE.Group();
    pivot.position.set(side * 0.2, 0.56, 0);
    const arm = mesh(new THREE.CapsuleGeometry(0.04, 0.14, 4, 8), mat('#a9b1c6'));
    arm.position.y = -0.1;
    pivot.add(arm);
    pivot.rotation.z = side * 0.18;
    bodyPivot.add(pivot);
    return pivot;
  });
  const head = mesh(new THREE.BoxGeometry(0.46, 0.34, 0.38), mat('#' + color.getHexString()));
  head.position.y = 0.86;
  bodyPivot.add(head);
  const face = mesh(new THREE.PlaneGeometry(0.36, 0.24), mat('#141b2b', { roughness: 0.3 }), false);
  face.position.set(0, -0.005, 0.191);
  head.add(face);
  const eyeMat = new THREE.MeshBasicMaterial({ color: EYE.ok, toneMapped: false });
  const single = bot.id === 'vigia';
  const eyes = (single ? [0] : [-0.08, 0.08]).map((x) => {
    const e = mesh(new THREE.BoxGeometry(single ? 0.12 : 0.07, single ? 0.12 : 0.07, 0.01), eyeMat, false);
    e.position.set(x, 0.0, 0.197);
    head.add(e);
    return e;
  });
  const extra = accessory(bot.id, head, bot.color || '#9aa3b5');
  return { root, bodyPivot, legs, arms, head, eyes, eyeMat, extra };
}

// ------------------------------------------------------------------ un vecino en la base

class Actor {
  constructor(base, bot) {
    this.base = base;
    this.id = bot.id;
    this.bot = bot;
    this.parts = makeRobot(bot);
    this.root = this.parts.root;
    this.root.traverse((o) => { o.userData.botId = bot.id; });
    base.scene.add(this.root);
    this.room = bot.home && ROOMS[bot.home] ? bot.home : 'consejo';
    this.spot = null;
    const start = this.base.claim(this, this.room);
    this.root.position.copy(start.point);
    this.facing = start.face;
    this.root.rotation.y = this.facing;
    this.path = [];
    this.queue = [];
    this.action = null;
    this.walkT = 0;
    this.blinkIn = rand(1, 4);
    this.hop = 0;
    this.say = null;
    this.badge = null;
    this.badgeUntil = 0;
    this.pillText = '';
    this.pillUntil = 0;
    this.activityKey = '';
    this.tag = el('div', 'tag', bot.name);
    this.pill = el('div', 'pill');
    this.bubble = el('div', 'bubble');
    this.badgeEl = el('div', 'badge3d');
    for (const n of [this.tag, this.pill, this.bubble, this.badgeEl]) {
      n.setAttribute('aria-hidden', 'true');
      base.overlay.append(n);
    }
    this.setMood(bot.mood);
  }
  get mood() {
    return this.bot.mood || 'ok';
  }
  setBot(bot) {
    const moodChanged = bot.mood !== this.bot.mood;
    this.bot = bot;
    if (this.tag.textContent !== bot.name) this.tag.textContent = bot.name;
    if (moodChanged) this.setMood(bot.mood);
    const act = bot.activity;
    if (act && act.room && ROOMS[act.room]) {
      const key = `${act.room}|${act.text}|${act.at}`;
      if (key !== this.activityKey) {
        const first = !this.activityKey;
        this.activityKey = key;
        this.showPill(act.text, first ? 6 : 9);
        if (act.room !== this.room || first) this.goRoom(act.room, first);
      }
    }
  }
  setMood(mood) {
    this.parts.eyeMat.color.set(EYE[mood] || EYE.ok);
  }
  busy() {
    return Boolean(this.action) || this.queue.length > 0;
  }
  plan(...steps) {
    this.queue.push(...steps);
  }
  showPill(text, seconds) {
    if (!text) return;
    this.pillText = text;
    this.pillUntil = this.base.t + seconds;
  }
  icon(text, seconds) {
    this.badge = text;
    this.badgeUntil = this.base.t + (seconds || 2.2);
  }
  speak(text, kind, label, seconds) {
    const shown = text.length > 140 ? text.slice(0, 139).replace(/\s+\S*$/, '') + '…' : text;
    this.say = { text: shown, kind, label, key: Math.random(), until: this.base.t + (seconds || clamp(2.8 + shown.length * 0.05, 3, 9)) };
    return this.say.until;
  }
  // Ir a una sala (a su sitio fijo si lo tiene allí).
  goRoom(room, instant) {
    if (this.queue.some((s) => s.type === 'room')) this.queue = this.queue.filter((s) => s.type !== 'room');
    if (instant || this.base.reduced) {
      this.base.release(this);
      this.room = room;
      const spot = this.base.claim(this, room);
      this.root.position.copy(spot.point);
      this.facing = spot.face;
      return;
    }
    this.plan({ type: 'room', room });
  }
  pathTo(target, room) {
    const from = this.root.position.clone();
    const a = roomAt(from.x, from.z);
    const rooms = routeRooms(a, room);
    const points = [];
    let cur = from;
    const push = (p, inRoom) => {
      for (const q of detour(cur, p, inRoom)) {
        points.push(q);
        cur = q;
      }
    };
    for (let i = 0; i + 1 < rooms.length; i++) push(door(rooms[i], rooms[i + 1]), rooms[i]);
    push(target, room);
    return points;
  }
  begin(step) {
    this.action = step;
    if (step.type === 'room') {
      this.base.release(this);
      this.room = step.room;
      const spot = this.base.claim(this, step.room);
      step.face = spot.face;
      this.path = this.pathTo(spot.point, step.room);
    } else if (step.type === 'walk') {
      const target = typeof step.to === 'function' ? step.to() : step.to;
      this.path = target ? this.pathTo(target.point, target.room) : [];
      step.face = target && target.face;
    } else if (step.type === 'say') {
      step.until = this.speak(step.text, step.kind, step.label, step.seconds);
    } else if (step.type === 'wait') {
      step.until = this.base.t + step.seconds;
    }
  }
  run(step, dt) {
    if (step.type === 'room' || step.type === 'walk') {
      if (!this.path.length) {
        if (typeof step.face === 'number') this.facing = step.face;
        this.walkT = 0;
        return true;
      }
      const goal = this.path[0];
      const pos = this.root.position;
      const dx = goal.x - pos.x;
      const dz = goal.z - pos.z;
      const dist = Math.hypot(dx, dz);
      const v = SPEED * dt;
      this.facing = Math.atan2(dx, dz);
      this.walkT += dt;
      if (dist <= v) {
        pos.x = goal.x;
        pos.z = goal.z;
        this.path.shift();
      } else {
        pos.x += (dx / dist) * v;
        pos.z += (dz / dist) * v;
      }
      return false;
    }
    if (step.type === 'say' || step.type === 'wait') return this.base.t >= step.until;
    if (step.type === 'face') this.faceTo(step.target);
    if (step.type === 'call') step.fn(this);
    return true;
  }
  faceTo(target) {
    if (!target) return;
    const p = target.root ? target.root.position : target;
    this.facing = Math.atan2(p.x - this.root.position.x, p.z - this.root.position.z);
  }
  update(dt, t) {
    const parts = this.parts;
    if (this.say && t > this.say.until) this.say = null;
    if (!this.action && this.queue.length) this.begin(this.queue.shift());
    if (this.action && this.run(this.action, dt)) this.action = null;
    const walking = Boolean(this.action && (this.action.type === 'room' || this.action.type === 'walk') && this.path.length);
    // Girar poco a poco hacia donde mira.
    let diff = this.facing - this.root.rotation.y;
    diff = Math.atan2(Math.sin(diff), Math.cos(diff));
    this.root.rotation.y += diff * Math.min(1, dt * 10);
    // Piernas, brazos y saltitos.
    const swing = walking ? Math.sin(this.walkT * 11) : 0;
    parts.legs[0].rotation.x = swing * 0.6;
    parts.legs[1].rotation.x = -swing * 0.6;
    parts.arms[0].rotation.x = -swing * 0.5;
    parts.arms[1].rotation.x = swing * 0.5;
    let bob = walking ? Math.abs(Math.sin(this.walkT * 11)) * 0.03 : Math.sin(t * 2 + this.root.id) * 0.008;
    if (this.hop > 0) {
      this.hop = Math.max(0, this.hop - dt);
      bob += Math.sin(((0.4 - this.hop) / 0.4) * Math.PI) * 0.18;
    }
    if (this.mood === 'happy' && !walking && !this.busy() && Math.sin(t * 3 + this.root.id) > 0.97) this.hop = 0.4;
    parts.bodyPivot.position.y = bob;
    if (this.mood === 'sleep') {
      parts.bodyPivot.rotation.z = Math.sin(t * 0.8) * 0.05 + 0.12;
      parts.bodyPivot.scale.y = 0.92;
    } else {
      parts.bodyPivot.rotation.z = this.id === 'miedo' && !walking ? Math.sin(t * 40) * 0.015 : 0;
      parts.bodyPivot.scale.y = 1;
    }
    // Parpadeo.
    this.blinkIn -= dt;
    const closed = this.mood === 'sleep' || this.blinkIn < 0;
    if (this.blinkIn < -0.13) this.blinkIn = rand(2.5, 6);
    for (const e of parts.eyes) e.scale.y = closed ? 0.15 : this.mood === 'happy' ? 0.55 : 1;
    // Lo que se mueve de cada uno (moneda, antena, sirena...).
    parts.extra.traverse((o) => {
      if (o.userData.spin) o.rotation.y += dt * o.userData.spin;
      if (o.userData.siren) o.material.emissiveIntensity = this.mood === 'alert' ? (Math.sin(t * 9) > 0 ? 2.6 : 0.4) : 0.5;
      if (o.userData.drip) o.position.y = 0.08 - ((t * 0.3) % 1) * 0.12;
    });
  }
  remove() {
    this.base.release(this);
    this.base.scene.remove(this.root);
    for (const n of [this.tag, this.pill, this.bubble, this.badgeEl]) n.remove();
  }
}

// ------------------------------------------------------------------ la base

export class Base {
  constructor(container, overlay, options) {
    this.container = container;
    this.overlay = overlay;
    this.options = options || {};
    this.reduced = Boolean(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    this.t = 0;
    this.last = 0;
    this.actors = new Map();
    this.effects = [];
    this.flags = { mail: 0 };
    this.data = {};
    this.focusRoom = null;
    this.onPick = null;
    this.occupied = new Map(); // "sala:asiento" → actor
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;
    this.canvas = this.renderer.domElement;
    this.canvas.className = 'base3d';
    this.canvas.setAttribute('aria-hidden', 'true');
    container.prepend(this.canvas);
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-10, 10, 10, -10, 0.1, 200);
    this.view = { target: new THREE.Vector3(0, 0.3, 0), half: 10 };
    this.goal = { target: new THREE.Vector3(0, 0.3, 0), half: 10 };
    this.raycaster = new THREE.Raycaster();
    this.build();
    this.addLabels();
    this.resetBtn = el('button', 'focus-reset', 'Ver toda la base');
    this.resetBtn.type = 'button';
    this.resetBtn.hidden = true;
    this.resetBtn.addEventListener('click', () => this.focus(null));
    container.append(this.resetBtn);
    if ('ResizeObserver' in window) new ResizeObserver(() => this.resize()).observe(container);
    window.addEventListener('resize', () => this.resize());
    this.resize(true);
    this.canvas.addEventListener('click', (e) => this.click(e));
    this.canvas.addEventListener('mousemove', (e) => this.hover(e));
    this.renderer.setAnimationLoop((ts) => this.frame(ts));
  }

  // --- construir --------------------------------------------------------------------

  build() {
    const scene = this.scene;
    scene.add(new THREE.HemisphereLight('#f4ecff', '#3b2c74', 2.1));
    const sun = new THREE.DirectionalLight('#fff6ec', 2.7);
    sun.position.set(7, 16, 11);
    sun.castShadow = true;
    sun.shadow.mapSize.set(1024, 1024);
    const sc = sun.shadow.camera;
    sc.left = -13;
    sc.right = 13;
    sc.top = 13;
    sc.bottom = -13;
    sc.near = 1;
    sc.far = 40;
    sun.shadow.bias = -0.0008;
    sun.shadow.normalBias = 0.02;
    scene.add(sun);
    this.sun = sun;

    // La plataforma y su borde de neón.
    const slab = box(EDGE * 2 + 0.6, 0.9, EDGE * 2 + 0.6, mat('#6a55c8', { roughness: 0.6 }), 0, -0.95, 0);
    slab.castShadow = false;
    scene.add(slab);
    const under = box(EDGE * 2 - 1, 0.5, EDGE * 2 - 1, mat('#4a3a9a'), 0, -1.4, 0);
    under.castShadow = false;
    scene.add(under);
    const neon = glowMat('#62f2ff', 2.2);
    for (const [w, d, x, z] of [[EDGE * 2 + 0.6, 0.08, 0, EDGE + 0.3], [0.08, EDGE * 2 + 0.6, EDGE + 0.3, 0], [EDGE * 2 + 0.6, 0.08, 0, -EDGE - 0.3], [0.08, EDGE * 2 + 0.6, -EDGE - 0.3, 0]]) {
      const strip = box(w, 0.06, d, neon, x, -0.08, z);
      strip.castShadow = false;
      scene.add(strip);
    }
    const glowUnder = halo('#9b6bff', 34, 0.35);
    glowUnder.position.set(0, -3, 0);
    scene.add(glowUnder);

    // Suelos con baldosas, una sala de cada color.
    const tiles = document.createElement('canvas');
    tiles.width = tiles.height = 128;
    const tg = tiles.getContext('2d');
    tg.fillStyle = '#ffffff';
    tg.fillRect(0, 0, 128, 128);
    tg.strokeStyle = 'rgba(80,60,140,0.13)';
    tg.lineWidth = 3;
    for (let i = 0; i <= 128; i += 32) {
      tg.beginPath(); tg.moveTo(i, 0); tg.lineTo(i, 128); tg.stroke();
      tg.beginPath(); tg.moveTo(0, i); tg.lineTo(128, i); tg.stroke();
    }
    const tileTex = new THREE.CanvasTexture(tiles);
    tileTex.colorSpace = THREE.SRGBColorSpace;
    tileTex.wrapS = tileTex.wrapT = THREE.RepeatWrapping;
    tileTex.repeat.set(3, 3);
    this.floors = [];
    for (const [id, r] of Object.entries(ROOMS)) {
      const c = center(id);
      const floor = mesh(new THREE.BoxGeometry(SIZE, 0.12, SIZE), new THREE.MeshStandardMaterial({
        color: r.soon ? '#cfd3e3' : r.floor, map: tileTex, roughness: 0.85,
      }));
      floor.position.set(c.x, -0.06, c.z);
      floor.castShadow = false;
      floor.userData.room = id;
      scene.add(floor);
      this.floors.push(floor);
    }

    // Paredes: altas detrás (con luz arriba), bajas entre salas y con puertas.
    const wall = mat('#f4f1fb', { roughness: 0.6 });
    const cap = mat('#d8cff2');
    const addWall = (x1, z1, x2, z2, h, glow) => {
      const len = Math.hypot(x2 - x1, z2 - z1);
      if (len < 0.05) return;
      const horizontal = Math.abs(z2 - z1) < 1e-6;
      const w = horizontal ? len : 0.16;
      const d = horizontal ? 0.16 : len;
      const cx = (x1 + x2) / 2;
      const cz = (z1 + z2) / 2;
      scene.add(box(w, h, d, wall, cx, 0, cz));
      const top = box(w + 0.02, 0.05, d + 0.02, glow ? neon : cap, cx, h, cz);
      top.castShadow = false;
      scene.add(top);
    };
    addWall(-EDGE, -EDGE, EDGE, -EDGE, BACK_H, true);
    addWall(-EDGE, -EDGE, -EDGE, EDGE, BACK_H, true);
    // Paredes interiores con hueco de puerta en el centro de cada lado de sala.
    for (const line of [-SIZE / 2, SIZE / 2]) {
      for (let k = 0; k < 3; k++) {
        const a = -EDGE + k * SIZE;
        const mid = a + SIZE / 2;
        addWall(line, a, line, mid - DOOR / 2, WALL_H);
        addWall(line, mid + DOOR / 2, line, a + SIZE, WALL_H);
        addWall(a, line, mid - DOOR / 2, line, WALL_H);
        addWall(mid + DOOR / 2, line, a + SIZE, line, WALL_H);
      }
    }
    // Bordillo delante.
    const curb = mat('#e7e1f7');
    scene.add(box(EDGE * 2, 0.22, 0.16, curb, 0, 0, EDGE));
    scene.add(box(0.16, 0.22, EDGE * 2, curb, EDGE, 0, 0));

    // Muebles de cada sala.
    this.animated = [];
    this.screens = {};
    for (const id of Object.keys(ROOMS)) {
      const group = new THREE.Group();
      group.position.copy(center(id));
      this.furnish(id, group);
      scene.add(group);
    }
  }

  furnish(id, g) {
    const r = ROOMS[id];
    const add = (o) => {
      g.add(o);
      return o;
    };
    const facing = Math.PI / 4; // de cara a la cámara
    const standing = (w, h, px, draw, x, z, y) => {
      const s = screen(w, h, px, draw);
      const frame = box(w + 0.12, h + 0.12, 0.08, mat('#2a2347'), 0, -(h + 0.12) / 2, -0.05);
      const holder = new THREE.Group();
      holder.add(frame, s.mesh);
      const leg = box(0.1, y - h / 2, 0.1, mat('#3a3360'), 0, -y, -0.05);
      holder.add(leg);
      holder.position.set(x, y, z);
      holder.rotation.y = facing;
      s.mesh.position.z = 0.0;
      add(holder);
      return s;
    };
    if (id === 'observatorio') {
      // Telescopio sobre su trípode.
      const tele = new THREE.Group();
      tele.position.set(-1.7, 0, -1.7);
      for (let i = 0; i < 3; i++) {
        const leg = cyl(0.03, 0.03, 0.9, mat('#5b6178'), 0, 0, 0, 8);
        leg.position.y = 0.42;
        leg.rotation.z = 0.35;
        const p = new THREE.Group();
        p.rotation.y = (i * Math.PI * 2) / 3;
        p.add(leg);
        tele.add(p);
      }
      const tube = cyl(0.13, 0.17, 1.3, mat('#f2f4fa', { metalness: 0.3, roughness: 0.4 }), 0, 0, 0, 20);
      tube.position.set(0.15, 1.15, 0.15);
      tube.rotation.set(0.7, 0, -0.5);
      tele.add(tube);
      add(tele);
      // Globo terráqueo que gira.
      const stand = cyl(0.08, 0.2, 0.5, mat('#8a6a4a'), 1.4, 0, -1.9);
      add(stand);
      const globe = mesh(new THREE.SphereGeometry(0.42, 24, 18), mat('#4f8fe0', { roughness: 0.4 }));
      globe.position.set(1.4, 0.95, -1.9);
      const lines = new THREE.Mesh(new THREE.SphereGeometry(0.43, 12, 8), new THREE.MeshBasicMaterial({ color: '#bfe6ff', wireframe: true, transparent: true, opacity: 0.45 }));
      globe.add(lines);
      add(globe);
      this.animated.push((dt) => { globe.rotation.y += dt * 0.4; });
      // Nube que flota.
      const cloud = new THREE.Group();
      const white = mat('#ffffff', { emissive: '#e3f1ff', emissiveIntensity: 0.3 });
      for (const [x, y, rr] of [[-0.3, 0, 0.32], [0.05, 0.12, 0.4], [0.4, 0, 0.3], [0.1, -0.08, 0.3]]) {
        const s = mesh(new THREE.SphereGeometry(rr, 16, 12), white);
        s.position.set(x, y, 0);
        cloud.add(s);
      }
      cloud.position.set(0.2, 2.2, -0.6);
      add(cloud);
      this.animated.push((dt, t) => { cloud.position.y = 2.2 + Math.sin(t * 0.8) * 0.12; });
      this.screens.weather = standing(1.9, 1.1, 384, drawWeather, -0.2, -2.45, 1.3);
    } else if (id === 'laboratorio') {
      // Antena parabólica que gira.
      const dishBase = cyl(0.35, 0.45, 0.4, mat('#cfd6e6'), 1.9, 0, -1.9);
      add(dishBase);
      const dishPivot = new THREE.Group();
      dishPivot.position.set(1.9, 0.75, -1.9);
      const dish = mesh(new THREE.SphereGeometry(0.75, 24, 12, 0, Math.PI * 2, 0, Math.PI / 3), mat('#f4f7fb', { side: THREE.DoubleSide, roughness: 0.35 }));
      dish.rotation.x = Math.PI * 0.75;
      dishPivot.add(dish);
      dishPivot.add(cyl(0.03, 0.03, 0.55, mat('#6b7390'), 0, -0.1, 0.25, 8));
      const tip = mesh(new THREE.SphereGeometry(0.06, 10, 8), glowMat('#5be0a0', 2));
      tip.position.set(0, 0.35, 0.5);
      dishPivot.add(tip);
      add(dishPivot);
      this.animated.push((dt) => { dishPivot.rotation.y += dt * 0.5; });
      // Mesas con pantallas.
      const lines = [];
      for (const x of [-1.2, 0.4]) {
        add(box(1.3, 0.62, 0.62, mat('#ece8f8'), x, 0, -2.4));
        const s = screen(0.9, 0.55, 256, drawScan);
        s.mesh.position.set(x, 0.98, -2.43);
        add(box(0.98, 0.62, 0.05, mat('#2a2347'), x, 0.66, -2.48));
        add(s.mesh);
        lines.push(s);
      }
      this.screens.code = lines;
      // Armario de servidores con luces.
      add(box(0.7, 1.7, 0.6, mat('#2d2a52'), -2.3, 0, -0.6));
      const leds = [];
      for (let i = 0; i < 6; i++) {
        const led = box(0.5, 0.04, 0.02, glowMat(i % 2 ? '#5be0a0' : '#62f2ff', 1.5), -2.3, 0.25 + i * 0.22, -0.29);
        led.castShadow = false;
        leds.push(led);
        add(led);
      }
      this.animated.push((dt, t) => {
        leds.forEach((l, i) => { l.material.emissiveIntensity = Math.sin(t * 3 + i * 1.7) > 0 ? 1.8 : 0.3; });
      });
    } else if (id === 'archivo') {
      // Estanterías con libros.
      const spines = screen(1.4, 1.6, 192, drawBooks);
      for (const z of [-1.0, 0.7]) {
        add(box(0.5, 1.7, 1.5, mat('#8a5a3c'), -2.6, 0, z));
        const face = spines.mesh.clone();
        face.position.set(-2.34, 0.85, z);
        face.rotation.y = Math.PI / 2;
        add(face);
      }
      // Mesa de lectura con su lámpara.
      add(box(1.3, 0.62, 0.7, mat('#a0643c'), 0.4, 0, -1.7));
      add(box(0.4, 0.02, 0.3, mat('#fbf6e9'), 0.3, 0.62, -1.65));
      add(cyl(0.02, 0.02, 0.4, mat('#3a3f52'), 0.85, 0.62, -1.9, 8));
      const bulb = mesh(new THREE.SphereGeometry(0.08, 12, 8), glowMat('#ffd27a', 2.2));
      bulb.position.set(0.85, 1.05, -1.9);
      add(bulb);
      const lampGlow = halo('#ffd27a', 1.4, 0.55);
      lampGlow.position.set(0.85, 1.05, -1.9);
      add(lampGlow);
      // Tablón del diario.
      add(box(0.08, 1.1, 0.08, mat('#6b4429'), 1.3, 0, -2.4));
      add(box(0.08, 1.1, 0.08, mat('#6b4429'), 2.3, 0, -2.4));
      add(box(1.25, 0.75, 0.06, mat('#b98a5a'), 1.8, 0.75, -2.4));
      const notes = [];
      for (const [x, y] of [[1.45, 1.25], [1.8, 1.18], [2.12, 1.27], [1.62, 0.95], [2.0, 0.92]]) {
        const n = box(0.24, 0.3, 0.01, mat('#fbf6e9'), x, y - 0.15, -2.36);
        n.visible = false;
        notes.push(n);
        add(n);
      }
      this.notes = notes;
    } else if (id === 'consejo') {
      // La mesa redonda con su aro de luz y el holograma del mercado que se discute.
      add(cyl(1.3, 1.1, 0.62, mat('#3a2f6b', { roughness: 0.5 }), 0, 0, 0, 40));
      const ring = mesh(new THREE.TorusGeometry(1.3, 0.035, 8, 60), glowMat('#f472b6', 2.4), false);
      ring.rotation.x = Math.PI / 2;
      ring.position.y = 0.62;
      add(ring);
      const glow = halo('#f472b6', 3.4, 0.35);
      glow.position.y = 0.7;
      add(glow);
      for (const [x, z] of SPOTS.consejo.map(([sx, sz]) => [sx * 1.22, sz * 1.22])) {
        add(cyl(0.2, 0.22, 0.32, mat('#5b4b9a'), x, 0, z, 16));
      }
      const holo = screen(1.9, 1.05, 384, drawTopic);
      holo.mesh.material.transparent = true;
      holo.mesh.material.opacity = 0.92;
      holo.mesh.position.set(0, 1.55, 0);
      holo.mesh.rotation.y = facing;
      add(holo.mesh);
      this.animated.push((dt, t) => { holo.mesh.position.y = 1.55 + Math.sin(t * 1.2) * 0.05; });
      this.screens.topic = holo;
      this.screens.board = standing(2.2, 1.65, 448, drawBoard, -2.2, -2.2, 1.55);
    } else if (id === 'mercado') {
      this.screens.ticker = standing(2.0, 1.2, 384, drawTicker, -2.25, -2.25, 1.45);
      add(box(2.4, 0.66, 0.72, mat('#d39a5f'), 0.9, 0, -2.4));
      add(box(2.08, 0.74, 0.05, mat('#2a2347'), 0.95, 0.7, -2.55));
      const curve = screen(1.98, 0.64, 384, drawCurve);
      curve.mesh.position.set(0.95, 1.05, -2.52);
      add(curve.mesh);
      this.screens.curve = curve;
      const gold = mat('#ffcf33', { metalness: 0.65, roughness: 0.3, emissive: '#4a3300', emissiveIntensity: 0.35 });
      for (const [x, z, n] of [[2.2, -1.2, 5], [2.45, -0.9, 3], [1.95, -0.85, 4]]) {
        for (let i = 0; i < n; i++) add(cyl(0.11, 0.11, 0.045, gold, x, i * 0.05, z, 18));
      }
    } else if (id === 'puente') {
      this.screens.radar = standing(1.9, 1.3, 320, drawRisk, -2.25, -2.25, 1.45);
      // Consola con luces.
      for (const x of [-0.8, 0, 0.8]) {
        add(box(0.72, 0.6, 0.55, mat('#ece8f8'), x, 0, -2.3));
        const top = box(0.62, 0.03, 0.4, glowMat(x === 0 ? '#ff6b6b' : '#62f2ff', 1.3), x, 0.6, -2.3);
        top.castShadow = false;
        add(top);
      }
      add(cyl(0.28, 0.24, 0.4, mat('#5b4b9a'), 0, 0, -0.55, 18));
      // Tu terminal: brilla cuando hay algo para ti.
      add(box(0.5, 0.95, 0.4, mat('#2d2a52'), 2.0, 0, -2.35));
      const term = screen(0.42, 0.42, 128, drawMail);
      term.mesh.position.set(2.0, 0.78, -2.14);
      add(term.mesh);
      const termGlow = halo('#ff8fd0', 1.5, 0);
      termGlow.position.set(2.0, 0.8, -2.0);
      add(termGlow);
      this.screens.mail = term;
      this.animated.push((dt, t) => { termGlow.material.opacity = this.flags.mail ? 0.45 + Math.sin(t * 4) * 0.25 : 0; });
    } else if (id === 'boveda') {
      const vault = new THREE.Group();
      vault.position.set(-2.15, 0, -2.15);
      vault.rotation.y = facing;
      vault.add(box(1.9, 1.9, 0.6, mat('#9aa3b5', { metalness: 0.55, roughness: 0.35 }), 0, 0, 0));
      const doorDisc = cyl(0.72, 0.72, 0.1, mat('#c9d0de', { metalness: 0.7, roughness: 0.25 }), 0, 0, 0, 40);
      doorDisc.rotation.x = Math.PI / 2;
      doorDisc.position.set(0, 0.95, 0.33);
      vault.add(doorDisc);
      const ring = mesh(new THREE.TorusGeometry(0.72, 0.04, 8, 48), glowMat('#ffd166', 2), false);
      ring.position.set(0, 0.95, 0.39);
      vault.add(ring);
      const wheel = new THREE.Group();
      wheel.position.set(0, 0.95, 0.42);
      for (let i = 0; i < 3; i++) {
        const spoke = box(0.6, 0.05, 0.05, mat('#5b6178'), 0, -0.025, 0);
        spoke.rotation.z = (i * Math.PI) / 3;
        wheel.add(spoke);
      }
      vault.add(wheel);
      this.animated.push((dt) => { wheel.rotation.z += dt * 0.3; });
      add(vault);
      const gold = mat('#ffcf33', { metalness: 0.65, roughness: 0.3, emissive: '#4a3300', emissiveIntensity: 0.35 });
      for (const [x, z] of [[0.6, -2.3], [1.1, -2.3], [0.85, -2.0], [-2.3, 0.4], [-2.3, 0.9]]) {
        add(box(0.4, 0.16, 0.22, gold, x, 0, z));
        add(box(0.4, 0.16, 0.22, gold, x, 0.16, z));
      }
      this.screens.vault = standing(1.6, 0.8, 320, drawVault, 1.8, -2.45, 1.45);
    } else if (id === 'forja') {
      add(box(1.3, 1.3, 1.1, mat('#4b4655'), -1.6, 0, -1.7));
      const mouth = box(0.7, 0.45, 0.02, glowMat('#ff7a1a', 2.6), -1.6, 0.25, -1.14);
      mouth.castShadow = false;
      add(mouth);
      const fire = new THREE.PointLight('#ff8a3d', 6, 5, 2);
      fire.position.set(-1.6, 0.8, -0.8);
      add(fire);
      this.animated.push((dt, t) => { fire.intensity = 5 + Math.sin(t * 13) * 1.2 + Math.sin(t * 7.3) * 0.8; });
      add(box(0.7, 0.35, 0.3, mat('#3a3f52'), 0.6, 0, -1.8));
      add(box(0.9, 0.12, 0.4, mat('#4b5163'), 0.6, 0.35, -1.8));
      this.screens.forge = standing(2.1, 1.35, 384, drawForge, 1.0, -2.35, 1.5);
    } else if (id === 'pruebas') {
      const pad = cyl(1.2, 1.25, 0.08, mat('#2a2f4a'), 0, 0, 0, 40);
      add(pad);
      const ring = mesh(new THREE.TorusGeometry(1.15, 0.04, 8, 60), glowMat('#5ee7f0', 2.2), false);
      ring.rotation.x = Math.PI / 2;
      ring.position.y = 0.1;
      add(ring);
      const beam = new THREE.Mesh(new THREE.CylinderGeometry(1.0, 1.0, 1.8, 32, 1, true), new THREE.MeshBasicMaterial({ color: '#5ee7f0', transparent: true, opacity: 0.08, side: THREE.DoubleSide, depthWrite: false }));
      beam.position.y = 1.0;
      add(beam);
      for (const z of [-1.6, -0.6]) add(box(0.6, 1.5, 0.6, mat('#e9eef5'), -2.3, 0, z));
      // El marcador de las pruebas de mentira en los partidos.
      this.screens.tests = standing(2.1, 1.3, 384, drawTests, 0.9, -2.3, 1.45);
    }
  }

  addLabels() {
    this.labels = {};
    for (const [id, r] of Object.entries(ROOMS)) {
      const node = el('button', `room-label${r.soon ? ' soon' : ''}`);
      node.type = 'button';
      node.style.setProperty('--glow', r.glow);
      const name = el('span', 'rl-name', r.name);
      const count = el('span', 'rl-count', r.soon ? 'pronto' : '');
      node.append(name, count);
      node.addEventListener('click', () => this.pickRoom(id));
      this.overlay.append(node);
      const c = center(id);
      this.labels[id] = { node, count, point: new THREE.Vector3(c.x - 2.6, BACK_H + 0.55, c.z - 2.6) };
    }
  }

  // --- asientos ------------------------------------------------------------------------

  claim(actor, room) {
    const spots = SPOTS[room];
    const c = center(room);
    let order = spots.map((_, i) => i);
    const fixed = DESK[actor.id];
    if (room === 'consejo') {
      const seat = this.seatOf(actor.id);
      order = [seat, ...order.filter((i) => i !== seat)];
    } else if (fixed !== undefined && ROOMS[room] && actor.bot && actor.bot.home === room) {
      order = [fixed, ...order.filter((i) => i !== fixed)];
    } else {
      order = order.filter((i) => i !== DESK_RESERVED(room)).concat(order.filter((i) => i === DESK_RESERVED(room)));
    }
    let index = order.find((i) => !this.occupied.has(`${room}:${i}`));
    if (index === undefined) index = order[Math.floor(Math.random() * order.length)];
    const [x, z, fx, fz] = spots[index];
    const jitter = this.occupied.has(`${room}:${index}`) ? rand(-0.35, 0.35) : 0;
    this.occupied.set(`${room}:${index}`, actor);
    actor.spot = `${room}:${index}`;
    const point = new THREE.Vector3(c.x + x + jitter, 0, c.z + z + jitter);
    const face = room === 'consejo'
      ? Math.atan2(c.x - point.x, c.z - point.z)
      : Math.atan2(c.x + fx - point.x, c.z + fz - point.z);
    return { point, face };
  }
  release(actor) {
    if (actor.spot && this.occupied.get(actor.spot) === actor) this.occupied.delete(actor.spot);
    actor.spot = null;
  }
  seatOf(id) {
    const order = ['investigadora', 'tormenta', 'dinero', 'piloto', 'miedo', 'codicia', 'claude'];
    const i = order.indexOf(id);
    return i >= 0 ? i : 7 + (Math.abs(hashCode(id)) % 2);
  }
  // Un punto junto a otro vecino (para ir a hablarle).
  beside(other, who) {
    const p = other.root.position;
    const from = who.root.position;
    const dir = new THREE.Vector3(from.x - p.x, 0, from.z - p.z);
    if (dir.lengthSq() < 1e-4) dir.set(1, 0, 1);
    dir.normalize().multiplyScalar(0.75);
    const point = p.clone().add(dir);
    const room = roomAt(point.x, point.z);
    const c = center(room);
    point.x = clamp(point.x, c.x - 2.6, c.x + 2.6);
    point.z = clamp(point.z, c.z - 2.6, c.z + 2.6);
    return { point, room, face: Math.atan2(p.x - point.x, p.z - point.z) };
  }
  spotOf(room, index) {
    const c = center(room);
    const [x, z, fx, fz] = SPOTS[room][index];
    const point = new THREE.Vector3(c.x + x, 0, c.z + z);
    return { point, room, face: Math.atan2(c.x + fx - point.x, c.z + fz - point.z) };
  }

  // --- datos -----------------------------------------------------------------------------

  sync(bots) {
    const seen = new Set();
    for (const b of bots) {
      seen.add(b.id);
      const actor = this.actors.get(b.id);
      if (actor) actor.setBot(b);
      else {
        const created = new Actor(this, b);
        this.actors.set(b.id, created);
        created.setBot(b);
      }
    }
    for (const [id, actor] of this.actors) {
      if (!seen.has(id)) {
        actor.remove();
        this.actors.delete(id);
      }
    }
    const by = Object.fromEntries(bots.map((b) => [b.id, b]));
    this.setData(by);
  }

  setData(by) {
    this.lastBy = by;
    const d = (id) => (by[id] && by[id].detail) || {};
    const kali = d('kali');
    const council = d('investigadora');
    const nube = d('nube');
    const radar = d('radar');
    const tests = d('probadora');
    const smith = d('herrera');
    const vigia = d('vigia');
    const counts = {
      mercado: kali.connected ? `${(kali.positions || []).length} abiertas` : 'sin panel',
      laboratorio: radar.scanned !== undefined ? `${radar.scanned} mercados` : '',
      observatorio: (nube.cities || []).length ? `${(nube.cities || []).length} ciudades` : '',
      consejo: (council.topics || []).length ? `${council.topics.length} mercados` : '',
      boveda: kali.today && kali.today.net !== undefined ? `hoy ${money(kali.today.net, true)}` : '',
      puente: this.flags.mail ? `${this.flags.mail} para ti` : vigia.alerts_today ? `${vigia.alerts_today} avisos` : 'en calma',
      archivo: 'diario',
      pruebas: (tests.variants || []).length ? `${tests.variants.length} maneras` : '',
      forja: smith.totals ? `30 días ${money(smith.totals.net, true)}` : smith.connected === false ? 'sin panel' : '',
    };
    for (const [id, text] of Object.entries(counts)) {
      if (this.labels[id] && this.labels[id].count.textContent !== text) this.labels[id].count.textContent = text;
    }
    const sig = JSON.stringify([kali.positions, kali.today, council.board, (council.topics || []).length, nube.cities, radar.scanned, radar.by_series, tests.variants, smith.totals, smith.groups, (smith.curve || []).length]);
    if (sig === this.dataSig) return;
    this.dataSig = sig;
    this.data = { kali, council, nube, radar, tests, smith, names: Object.fromEntries(Object.values(by).map((b) => [b.id, b])) };
    this.screens.weather.redraw(this.data);
    this.screens.board.redraw(this.data);
    this.screens.ticker.redraw(this.data);
    this.screens.vault.redraw(this.data);
    if (this.screens.tests) this.screens.tests.redraw(this.data);
    this.screens.code.forEach((s, i) => s.redraw({ ...this.data, part: i }));
    this.screens.curve.redraw(this.data);
    if (this.screens.forge) this.screens.forge.redraw(this.data);
    this.topicIndex = 0;
    this.nextTopicAt = 0;
  }

  setMail(n) {
    if (this.flags.mail === n) return;
    this.flags.mail = n;
    this.screens.mail.redraw({ mail: n });
    if (this.lastBy) this.setData(this.lastBy); // el cartel del Puente dice cuántos son para ti
  }

  // --- escenas: de mensaje a lo que se ve ----------------------------------------------

  perform(m, names) {
    const a = this.actors.get(m.from);
    if (!a) return;
    const label = m.to === 'tu' ? 'a ti' : m.to === 'todos' ? 'a todos' : `a ${(names && names(m.to)) || m.to}`;
    const back = () => [{ type: 'room', room: a.room }];
    const crowded = a.queue.length > 6;
    if (m.kind === 'chat') {
      const other = this.actors.get(m.to);
      if (other) {
        a.faceTo(other);
        other.faceTo(a);
      }
      a.speak(m.text, 'chat', label);
      return;
    }
    if (m.from === 'kali' && m.kind === 'trade') {
      const net = Number(m.data && m.data.net);
      const lost = (Number.isFinite(net) && net < 0) || m.text.includes('−$');
      a.plan(
        ...(crowded ? [] : [{ type: 'room', room: 'mercado' }]),
        { type: 'call', fn: () => { this.burst(a, lost ? 'loss' : 'coins'); a.hop = 0.4; } },
        { type: 'say', text: m.text, kind: 'trade', label: null },
      );
      return;
    }
    if (m.kind === 'trade') {
      a.plan({ type: 'call', fn: () => { this.burst(a, Number(m.data && m.data.net) < 0 ? 'loss' : 'coins'); a.hop = 0.4; } },
        { type: 'say', text: m.text, kind: 'trade', label: 'de mentira' });
      return;
    }
    if (m.to === 'tu' || m.kind === 'diary') {
      const diary = m.kind === 'diary' && m.to !== 'tu';
      const target = m.kind === 'diary' ? this.spotOf('archivo', BOARD_SPOT) : this.spotOf('puente', MAIL_SPOT);
      a.plan(
        ...(crowded ? [] : [{ type: 'walk', to: target }]),
        {
          type: 'call',
          fn: () => {
            if (m.kind === 'diary') this.pinNote();
            if (m.to === 'tu' && this.options.onMail) this.options.onMail();
            this.burst(a, 'spark');
          },
        },
        { type: 'say', text: m.text, kind: m.kind, label: diary ? 'al tablón' : label },
        ...back(),
      );
      return;
    }
    const b = this.actors.get(m.to);
    if (!b) {
      a.plan({ type: 'say', text: m.text, kind: m.kind, label });
      return;
    }
    a.plan(
      ...(crowded ? [] : [{ type: 'walk', to: () => this.beside(b, a) }]),
      { type: 'call', fn: () => { a.faceTo(b); if (!b.busy()) b.faceTo(a); } },
      { type: 'say', text: m.text, kind: m.kind, label },
      { type: 'call', fn: () => { b.icon(m.kind === 'alert' ? '!' : /^Tranquila/.test(m.text) ? '♥' : '✓'); if (m.kind === 'alert') b.hop = 0.4; } },
      { type: 'wait', seconds: 0.8 },
      ...back(),
    );
  }

  pinNote() {
    if (!this.notes) return;
    const hidden = this.notes.find((n) => !n.visible);
    if (hidden) hidden.visible = true;
  }

  burst(actor, kind) {
    const p = actor.root.position;
    const group = new THREE.Group();
    group.position.set(p.x, 1.1, p.z);
    this.scene.add(group);
    const parts = [];
    const n = kind === 'coins' ? 12 : kind === 'loss' ? 10 : 8;
    for (let i = 0; i < n; i++) {
      let m;
      if (kind === 'coins') m = cyl(0.06, 0.06, 0.02, mat('#ffcf33', { metalness: 0.6, roughness: 0.3, emissive: '#6a4a00', emissiveIntensity: 0.5 }), 0, 0, 0, 12);
      else if (kind === 'loss') m = box(0.02, 0.12, 0.02, glowMat('#8fd3ff', 1), 0, 0, 0);
      else m = mesh(new THREE.OctahedronGeometry(0.05), glowMat('#ffe680', 2), false);
      m.castShadow = false;
      group.add(m);
      const v = kind === 'loss'
        ? new THREE.Vector3(rand(-0.4, 0.4), rand(-1.5, -0.5), rand(-0.4, 0.4))
        : new THREE.Vector3(rand(-1.3, 1.3), rand(1.5, 3), rand(-1.3, 1.3));
      if (kind === 'loss') m.position.set(rand(-0.3, 0.3), rand(0.3, 0.6), rand(-0.3, 0.3));
      parts.push({ m, v });
    }
    if (kind === 'loss') {
      const cloud = new THREE.Group();
      for (const [x, y, rr] of [[-0.18, 0, 0.18], [0.02, 0.06, 0.22], [0.22, 0, 0.16]]) {
        const s = mesh(new THREE.SphereGeometry(rr, 12, 10), mat('#7d849a'), false);
        s.position.set(x, 0.75 + y, 0);
        cloud.add(s);
      }
      group.add(cloud);
    }
    this.effects.push({ group, parts, life: kind === 'loss' ? 2.6 : 1.4, kind });
  }

  // --- cámara ------------------------------------------------------------------------------

  frame3(target, half) {
    this.goal.target.copy(target);
    this.goal.half = half;
  }
  overviewHalf() {
    // Lo que mide la base vista desde la cámara, con un margen.
    const corners = [];
    for (const x of [-EDGE - 0.4, EDGE + 0.4]) {
      for (const z of [-EDGE - 0.4, EDGE + 0.4]) {
        corners.push(new THREE.Vector3(x, -1.0, z), new THREE.Vector3(x, BACK_H + 0.8, z));
      }
    }
    const right = new THREE.Vector3(Math.cos(AZIMUTH), 0, -Math.sin(AZIMUTH));
    const fwd = new THREE.Vector3(Math.sin(AZIMUTH) * Math.cos(ELEVATION), Math.sin(ELEVATION), Math.cos(AZIMUTH) * Math.cos(ELEVATION));
    const up = new THREE.Vector3().crossVectors(fwd, right).normalize();
    let w = 0;
    let h = 0;
    const c = new THREE.Vector3(0, 0.3, 0);
    for (const p of corners) {
      const d = p.clone().sub(c);
      w = Math.max(w, Math.abs(d.dot(right)));
      h = Math.max(h, Math.abs(d.dot(up)));
    }
    return { w: w * 1.02, h: h * 1.04 };
  }
  focus(room) {
    this.focusRoom = room;
    this.resetBtn.hidden = !room;
    this.container.classList.toggle('focused', Boolean(room));
    for (const [id, l] of Object.entries(this.labels)) l.node.classList.toggle('active', id === room);
    if (room) {
      const c = center(room);
      this.frame3(new THREE.Vector3(c.x, 0.4, c.z), 4.6);
    } else {
      this.frame3(new THREE.Vector3(0, 0.3, 0), null);
    }
  }
  pickRoom(room) {
    if (ROOMS[room].soon) {
      if (this.onPick) this.onPick({ room });
      return;
    }
    if (this.focusRoom === room) {
      if (this.onPick) this.onPick({ room });
    } else {
      this.focus(room);
    }
  }
  resize(first) {
    const w = this.container.clientWidth;
    const h = this.container.clientHeight;
    if (!w || !h) return;
    this.width = w;
    this.height = h;
    this.renderer.setSize(w, h, false);
    this.canvas.style.width = `${w}px`;
    this.canvas.style.height = `${h}px`;
    const size = this.overviewHalf();
    const aspect = w / h;
    this.overview = Math.max(size.h, size.w / aspect);
    if (!this.focusRoom) this.goal.half = this.overview;
    if (first) {
      this.view.half = this.goal.half;
      this.view.target.copy(this.goal.target);
    }
    this.applyCamera();
  }
  applyCamera() {
    const half = this.view.half || this.overview;
    const aspect = (this.width || 1) / (this.height || 1);
    const cam = this.camera;
    cam.left = -half * aspect;
    cam.right = half * aspect;
    cam.top = half;
    cam.bottom = -half;
    const d = 50;
    const t = this.view.target;
    cam.position.set(
      t.x + Math.sin(AZIMUTH) * Math.cos(ELEVATION) * d,
      t.y + Math.sin(ELEVATION) * d,
      t.z + Math.cos(AZIMUTH) * Math.cos(ELEVATION) * d,
    );
    cam.lookAt(t);
    cam.updateProjectionMatrix();
  }

  // --- el bucle ------------------------------------------------------------------------------

  frame(ts) {
    const dt = this.last ? Math.min(0.1, (ts - this.last) / 1000) : 0;
    this.last = ts;
    if (document.hidden) return;
    this.t += dt;
    const t = this.reduced ? 0 : this.t;
    // La cámara se acerca o se aleja poco a poco.
    const goalHalf = this.goal.half || this.overview;
    const k = this.reduced ? 1 : Math.min(1, dt * 4);
    this.view.half += (goalHalf - this.view.half) * k;
    this.view.target.lerp(this.goal.target, k);
    this.applyCamera();
    for (const a of this.actors.values()) a.update(dt, t);
    if (!this.reduced) for (const fn of this.animated) fn(dt, t);
    this.effects = this.effects.filter((fx) => {
      fx.life -= dt;
      for (const p of fx.parts) {
        if (fx.kind === 'loss') {
          p.m.position.y -= dt * 1.2;
          if (p.m.position.y < -0.9) p.m.position.y = rand(0.4, 0.7);
        } else {
          p.v.y -= 6 * dt;
          p.m.position.addScaledVector(p.v, dt);
          p.m.rotation.x += dt * 6;
        }
      }
      if (fx.life <= 0) {
        this.scene.remove(fx.group);
        return false;
      }
      return true;
    });
    this.tickScreens(t);
    this.renderer.render(this.scene, this.camera);
    this.place();
  }

  tickScreens(t) {
    if (this.screens.radar && (!this.radarAt || t - this.radarAt > 0.12 || this.reduced)) {
      this.radarAt = t;
      this.screens.radar.redraw({ t, kali: this.data.kali });
    }
    const topics = (this.data.council && this.data.council.topics) || [];
    if (topics.length && this.t >= (this.nextTopicAt || 0)) {
      this.nextTopicAt = this.t + 7;
      const ordered = [...topics].sort((x, y) => (y.held ? 1 : 0) - (x.held ? 1 : 0) || spread(y) - spread(x));
      const tp = ordered[(this.topicIndex = ((this.topicIndex || 0) + 1) % Math.min(6, ordered.length))];
      this.screens.topic.redraw({ topic: tp, names: this.data.names });
    }
  }

  // Los textos encima: nombres, bocadillos, carteles de sala.
  place() {
    const w = this.width;
    const h = this.height;
    const v = new THREE.Vector3();
    const project = (p) => {
      v.copy(p).project(this.camera);
      return [(v.x + 1) * 0.5 * w, (1 - v.y) * 0.5 * h, v.z];
    };
    for (const [id, l] of Object.entries(this.labels)) {
      const [x, y] = project(l.point);
      const show = !this.focusRoom || id === this.focusRoom;
      l.node.hidden = !show;
      if (show) l.node.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -100%)`;
    }
    const placed = [];
    const tags = [];
    const list = Array.from(this.actors.values()).sort((a, b) => (b.root.position.x + b.root.position.z) - (a.root.position.x + a.root.position.z));
    const focusOnly = this.focusRoom;
    const head = new THREE.Vector3();
    // Vista de toda la base: pocos bocadillos y etiquetas a la vez (los más nuevos), para que se lea.
    // Lo que dice cada miembro del Consejo se ve entrando en su sala.
    let talkers = null;
    let pills = null;
    const small = w < 600;
    if (!focusOnly) {
      talkers = new Set(list.filter((a) => a.say).sort((x, y) => y.say.until - x.say.until).slice(0, small ? 1 : 2));
      pills = new Set(list
        .filter((a) => a.pillText && this.t < a.pillUntil && !(a.bot.home === 'consejo' && a.id !== 'investigadora'))
        .sort((x, y) => y.pillUntil - x.pillUntil)
        .slice(0, small ? 2 : 4));
    } else {
      // Dentro de una sala: todos los que hablan, y las etiquetas de los que más acaban de hacer algo.
      const here = list.filter((a) => a.pillText && (a.room === focusOnly || roomAt(a.root.position.x, a.root.position.z) === focusOnly));
      pills = new Set(here.sort((x, y) => y.pillUntil - x.pillUntil).slice(0, small ? 3 : 7));
    }
    const minTop = focusOnly ? 46 : 4; // arriba está el botón "Ver toda la base"
    for (const a of list) {
      const inFocus = !focusOnly || a.room === focusOnly || roomAt(a.root.position.x, a.root.position.z) === focusOnly;
      const saying = Boolean(a.say) && (!talkers || talkers.has(a));
      head.set(a.root.position.x, 1.3 + a.parts.bodyPivot.position.y, a.root.position.z);
      const [hx, hy] = project(head);
      const [fx, fy] = project(a.root.position);
      const offscreen = hx < -40 || hx > w + 40 || hy < -40 || fy > h + 40;
      // Nombre bajo los pies.
      if (!offscreen && inFocus && !saying) {
        a.tag.hidden = false;
        if (!a.tagW || a.tagName !== a.bot.name) {
          a.tagName = a.bot.name;
          a.tagW = a.tag.offsetWidth;
          a.tagH = a.tag.offsetHeight;
        }
        let left = clamp(fx - a.tagW / 2, 2, Math.max(2, w - a.tagW - 2));
        let top = fy + 2;
        for (const r of tags) {
          if (left < r.left + r.w + 2 && left + a.tagW + 2 > r.left && top < r.top + r.h && top + a.tagH > r.top) top = r.top + r.h + 1;
        }
        tags.push({ left, top, w: a.tagW, h: a.tagH });
        a.tag.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
      } else {
        a.tag.hidden = true;
      }
      // Lo que está haciendo (una etiqueta pequeña que dura unos segundos, o siempre si miras su sala).
      const pillOn = !offscreen && !saying && a.pillText && (pills ? pills.has(a) : inFocus);
      if (pillOn) {
        a.pill.hidden = false;
        if (a.pill.textContent !== a.pillText || !a.pillW) {
          a.pill.textContent = a.pillText;
          a.pillW = a.pill.offsetWidth;
          a.pillH = a.pill.offsetHeight;
        }
        a.pill.style.setProperty('--c', a.bot.color || '#9aa3b5');
        const left = clamp(hx - a.pillW / 2, 2, Math.max(2, w - a.pillW - 2));
        let top = hy - a.pillH - 4;
        for (const r of placed) {
          if (left < r.left + r.w && left + a.pillW > r.left && top < r.top + r.h && top + a.pillH > r.top) top = r.top - a.pillH - 3;
        }
        placed.push({ left, top, w: a.pillW, h: a.pillH });
        a.pill.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
        a.pill.classList.toggle('fade', !focusOnly && a.pillUntil - this.t < 1);
      } else {
        a.pill.hidden = true;
      }
      // Iconito encima (✓, !, ♥).
      if (!offscreen && a.badge && this.t < a.badgeUntil && !saying) {
        a.badgeEl.hidden = false;
        a.badgeEl.textContent = a.badge;
        a.badgeEl.style.transform = `translate(${Math.round(hx)}px, ${Math.round(hy - 6)}px) translate(-50%, -100%)`;
      } else {
        a.badgeEl.hidden = true;
      }
      // Bocadillo.
      const b = a.bubble;
      if (offscreen || !saying || (focusOnly && !inFocus)) {
        if (b.dataset.key) {
          b.classList.remove('on');
          b.dataset.key = '';
        }
        continue;
      }
      if (b.dataset.key !== String(a.say.key)) {
        b.dataset.key = String(a.say.key);
        b.className = `bubble ${a.say.kind || 'info'}`;
        b.replaceChildren();
        if (a.say.label) b.append(el('span', 'from', `${a.bot.name} ${a.say.label}`));
        b.append(document.createTextNode(a.say.text));
        a.bw = b.offsetWidth;
        a.bh = b.offsetHeight;
        requestAnimationFrame(() => b.classList.add('on'));
      }
      let top = hy - a.bh - 10;
      let below = false;
      if (top < minTop) {
        top = fy + 14;
        below = true;
      }
      const left = clamp(hx - a.bw / 2, 4, Math.max(4, w - a.bw - 4));
      for (const r of placed) {
        if (left < r.left + r.w && left + a.bw > r.left && top < r.top + r.h && top + a.bh > r.top) {
          top = below ? r.top + r.h + 6 : r.top - a.bh - 6;
        }
      }
      b.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
      b.style.setProperty('--tail', `${Math.round(clamp(hx - left, 10, a.bw - 10))}px`);
      b.classList.toggle('below', below);
      placed.push({ left, top, w: a.bw, h: a.bh });
    }
  }

  // --- tocar ------------------------------------------------------------------------------

  ndc(e) {
    const r = this.canvas.getBoundingClientRect();
    return new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  }
  pickAt(e) {
    this.raycaster.setFromCamera(this.ndc(e), this.camera);
    const robots = [];
    for (const a of this.actors.values()) robots.push(a.root);
    const hit = this.raycaster.intersectObjects(robots, true)[0];
    if (hit) return { bot: hit.object.userData.botId };
    // Tocar cerca de un robot también vale (son pequeños en el móvil).
    const r = this.canvas.getBoundingClientRect();
    const x = e.clientX - r.left;
    const y = e.clientY - r.top;
    let best = null;
    const v = new THREE.Vector3();
    for (const a of this.actors.values()) {
      v.set(a.root.position.x, 0.6, a.root.position.z).project(this.camera);
      const sx = (v.x + 1) * 0.5 * this.width;
      const sy = (1 - v.y) * 0.5 * this.height;
      const d = Math.hypot(sx - x, sy - y);
      if (d < 22 && (!best || d < best.d)) best = { d, id: a.id };
    }
    if (best) return { bot: best.id };
    const floor = this.raycaster.intersectObjects(this.floors, false)[0];
    if (floor) return { room: floor.object.userData.room };
    return null;
  }
  click(e) {
    const hit = this.pickAt(e);
    if (!hit) {
      if (this.focusRoom) this.focus(null);
      return;
    }
    if (hit.bot) {
      if (this.onPick) this.onPick(hit);
      return;
    }
    this.pickRoom(hit.room);
  }
  hover(e) {
    if (e.pointerType === 'touch') return;
    this.canvas.classList.toggle('pointing', Boolean(this.pickAt(e)));
  }
}

function DESK_RESERVED(room) {
  // El sitio de la mesa de cada sala se lo guarda su dueño.
  return { puente: 0, mercado: 0, observatorio: 0, laboratorio: 0, archivo: 0 }[room];
}
function hashCode(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  return h;
}
function spread(t) {
  const v = Object.values((t && t.fairs) || {});
  return v.length > 1 ? Math.max(...v) - Math.min(...v) : 0;
}

// ------------------------------------------------------------------ pantallas

function drawWeather(ctx, w, h, data) {
  panelBg(ctx, w, h, '#16305a', '#0b1630');
  ctx.fillStyle = '#9fe0ff';
  ctx.font = `700 ${h * 0.13}px ${FONT}`;
  ctx.fillText('EL TIEMPO HOY', w * 0.06, h * 0.18);
  const rows = ((data && data.nube && data.nube.cities) || []).filter((r) => r.today != null).slice(0, 4);
  ctx.font = `700 ${h * 0.12}px ${TEXT}`;
  rows.forEach((r, i) => {
    const y = h * (0.38 + i * 0.17);
    ctx.fillStyle = '#e8f4ff';
    ctx.fillText(r.city, w * 0.06, y);
    ctx.fillStyle = '#ffd27a';
    ctx.textAlign = 'right';
    ctx.fillText(`${r.today}°${r.max_so_far != null ? ` · ${r.max_so_far}°` : ''}`, w * 0.94, y);
    ctx.textAlign = 'left';
  });
  if (!rows.length) {
    ctx.fillStyle = '#9fb6d9';
    ctx.fillText('Esperando al NWS…', w * 0.06, h * 0.5);
  }
}

function drawScan(ctx, w, h, data) {
  // Lo que encuentra Radar de verdad: cuántos mercados mira y dónde están los favoritos.
  panelBg(ctx, w, h, '#0c1f1a', '#04100c');
  const radar = (data && data.radar) || {};
  ctx.fillStyle = '#5be0a0';
  ctx.font = `700 ${h * 0.14}px ${FONT}`;
  if (radar.scanned === undefined) {
    ctx.fillText('RADAR', w * 0.07, h * 0.22);
    ctx.fillStyle = '#a7d8c2';
    ctx.font = `700 ${h * 0.12}px ${TEXT}`;
    ctx.fillText('Primera vuelta…', w * 0.07, h * 0.55);
    return;
  }
  if (!data.part) {
    ctx.fillText('RADAR · MIRA', w * 0.07, h * 0.22);
    const rows = [[radar.scanned, 'mercados'], [radar.favorites, 'favoritos claros'], [radar.outside, 'fuera de Kali']];
    rows.forEach(([n, label], i) => {
      const y = h * (0.47 + i * 0.2);
      ctx.fillStyle = '#e6fff4';
      ctx.font = `700 ${h * 0.15}px ${TEXT}`;
      ctx.fillText(String(n ?? '—'), w * 0.07, y);
      ctx.fillStyle = '#a7d8c2';
      ctx.font = `400 ${h * 0.12}px ${TEXT}`;
      ctx.fillText(label, w * 0.38, y);
    });
    return;
  }
  ctx.fillText('DÓNDE', w * 0.07, h * 0.22);
  const series = (radar.by_series || []).slice(0, 4);
  if (!series.length) {
    ctx.fillStyle = '#a7d8c2';
    ctx.font = `400 ${h * 0.12}px ${TEXT}`;
    ctx.fillText('Nada fuera de Kali', w * 0.07, h * 0.5);
    return;
  }
  series.forEach((sr, i) => {
    const y = h * (0.45 + i * 0.17);
    ctx.fillStyle = '#e6fff4';
    ctx.font = `700 ${h * 0.12}px ${TEXT}`;
    ctx.fillText(fitText(ctx, sr.label || sr.series, w * 0.66), w * 0.07, y);
    ctx.textAlign = 'right';
    ctx.fillText(String(sr.count), w * 0.93, y);
    ctx.textAlign = 'left';
  });
}

function drawBooks(ctx, w, h) {
  ctx.fillStyle = '#5a3820';
  ctx.fillRect(0, 0, w, h);
  const colors = ['#e5484d', '#3d6fd8', '#f2c94c', '#27b36a', '#8e5bd0', '#e58f3a', '#5ee7f0'];
  for (let shelf = 0; shelf < 4; shelf++) {
    const y0 = (shelf * h) / 4;
    ctx.fillStyle = '#3e2615';
    ctx.fillRect(0, y0 + h / 4 - 6, w, 6);
    let x = 4;
    let i = shelf * 3;
    while (x < w - 10) {
      const bw = 10 + ((i * 7) % 9);
      const bh = h / 4 - 14 - ((i * 5) % 10);
      ctx.fillStyle = colors[i % colors.length];
      ctx.fillRect(x, y0 + h / 4 - 6 - bh, bw, bh);
      x += bw + 2;
      i++;
    }
  }
}

function drawTopic(ctx, w, h, data) {
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(40, 18, 70, 0.78)';
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = '#f9a8d4';
  ctx.lineWidth = 5;
  ctx.strokeRect(2.5, 2.5, w - 5, h - 5);
  const t = data && data.topic;
  ctx.fillStyle = '#ffd6ec';
  ctx.font = `700 ${h * 0.11}px ${FONT}`;
  ctx.fillText(t ? (t.held ? 'APUESTA DE KALI' : 'EN EL CONSEJO') : 'EL CONSEJO', w * 0.05, h * 0.16);
  if (!t) {
    ctx.fillStyle = '#ffffff';
    ctx.font = `700 ${h * 0.12}px ${TEXT}`;
    ctx.fillText('Esperando mercados…', w * 0.05, h * 0.45);
    return;
  }
  ctx.fillStyle = '#ffffff';
  ctx.font = `700 ${h * 0.12}px ${TEXT}`;
  const name = t.name.length > 34 ? t.name.slice(0, 33) + '…' : t.name;
  ctx.fillText(name, w * 0.05, h * 0.34);
  // Barra de 0 a 100 con el precio y la opinión de cada uno.
  const x0 = w * 0.06;
  const x1 = w * 0.94;
  const y = h * 0.62;
  ctx.fillStyle = 'rgba(255,255,255,0.18)';
  ctx.fillRect(x0, y - 3, x1 - x0, 6);
  if (t.mid != null) {
    const px = x0 + (x1 - x0) * t.mid;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(px - 2, y - 18, 4, 36);
    ctx.font = `700 ${h * 0.095}px ${TEXT}`;
    ctx.textAlign = 'center';
    ctx.fillText(`precio ${Math.round(t.mid * 100)}¢`, clamp(px, w * 0.18, w * 0.82), h * 0.93);
    ctx.textAlign = 'left';
  }
  const names = (data && data.names) || {};
  for (const [id, fair] of Object.entries(t.fairs || {})) {
    const fx = x0 + (x1 - x0) * fair;
    ctx.beginPath();
    ctx.arc(fx, y, h * 0.055, 0, Math.PI * 2);
    ctx.fillStyle = (names[id] && names[id].color) || '#cccccc';
    ctx.fill();
    ctx.lineWidth = 3;
    ctx.strokeStyle = '#1c1238';
    ctx.stroke();
  }
}

function drawBoard(ctx, w, h, data) {
  panelBg(ctx, w, h, '#2a1450', '#140a2e');
  ctx.fillStyle = '#ffd6ec';
  ctx.font = `700 ${h * 0.1}px ${FONT}`;
  ctx.fillText('¿QUIÉN ACERTÓ?', w * 0.06, h * 0.15);
  ctx.font = `400 ${h * 0.06}px ${TEXT}`;
  ctx.fillStyle = '#c9b6e8';
  ctx.fillText('apuestas de mentira del Consejo', w * 0.06, h * 0.24);
  const board = ((data && data.council && data.council.board) || []).slice(0, 6);
  board.forEach((r, i) => {
    const y = h * (0.37 + i * 0.105);
    ctx.fillStyle = r.color || '#cccccc';
    ctx.fillRect(w * 0.06, y - h * 0.045, h * 0.05, h * 0.05);
    ctx.fillStyle = '#ffffff';
    ctx.font = `700 ${h * 0.07}px ${TEXT}`;
    ctx.fillText(r.name, w * 0.06 + h * 0.08, y);
    ctx.textAlign = 'right';
    ctx.fillStyle = r.pnl > 0 ? '#7dffb2' : r.pnl < 0 ? '#ff9b94' : '#d9d2ee';
    ctx.fillText(r.trades ? money(r.pnl, true) : '—', w * 0.94, y);
    ctx.fillStyle = '#c9b6e8';
    ctx.font = `400 ${h * 0.055}px ${TEXT}`;
    ctx.fillText(r.trades ? `${r.wins}/${r.trades}` : '', w * 0.7, y);
    ctx.textAlign = 'left';
  });
  if (!board.length) {
    ctx.fillStyle = '#d9d2ee';
    ctx.font = `700 ${h * 0.07}px ${TEXT}`;
    ctx.fillText('El marcador está a cero', w * 0.06, h * 0.45);
  }
}

// Recorta un texto (con …) para que quepa en ese ancho de la pantalla.
function fitText(ctx, text, max) {
  if (ctx.measureText(text).width <= max) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(`${t}…`).width > max) t = t.slice(0, -1);
  return `${t.trimEnd()}…`;
}

function drawTicker(ctx, w, h, data) {
  panelBg(ctx, w, h, '#2b2108', '#120d02');
  ctx.fillStyle = '#ffe680';
  ctx.font = `700 ${h * 0.13}px ${FONT}`;
  ctx.fillText('KALI · APUESTAS', w * 0.06, h * 0.18);
  const kali = (data && data.kali) || {};
  const rows = (kali.positions || []).slice(0, 4);
  ctx.font = `700 ${h * 0.11}px ${TEXT}`;
  rows.forEach((p, i) => {
    const y = h * (0.38 + i * 0.17);
    const name = (p.name || p.ticker || '').replace('Máxima en ', '');
    ctx.fillStyle = '#fff6d6';
    ctx.fillText(fitText(ctx, name, w * 0.62), w * 0.06, y);
    ctx.textAlign = 'right';
    const chance = Number(p.chance);
    ctx.fillStyle = chance >= 0.6 ? '#7dffb2' : '#ff9b94';
    ctx.fillText(`${p.side} ${Number.isFinite(chance) ? Math.round(chance * 100) + '%' : ''}`, w * 0.94, y);
    ctx.textAlign = 'left';
  });
  if (!rows.length) {
    ctx.fillStyle = '#d9cfa8';
    ctx.fillText(kali.connected ? 'Sin apuestas abiertas' : 'Sin panel', w * 0.06, h * 0.5);
  }
}

function drawCurve(ctx, w, h, data) {
  // La ganancia real acumulada de Kali en los últimos 30 días (lo que cuenta la Herrera).
  panelBg(ctx, w, h, '#0b1420', '#050a12');
  const smith = (data && data.smith) || {};
  const points = (smith.curve || []).map((p) => Number(p.total)).filter((v) => Number.isFinite(v));
  ctx.fillStyle = '#9fc3ff';
  ctx.font = `700 ${h * 0.15}px ${FONT}`;
  ctx.fillText('KALI · 30 DÍAS', w * 0.04, h * 0.2);
  if (points.length < 2) {
    ctx.fillStyle = '#c6d4ea';
    ctx.font = `400 ${h * 0.15}px ${TEXT}`;
    ctx.fillText(smith.connected === false ? 'Sin panel' : 'Aún sin cierres', w * 0.04, h * 0.62);
    return;
  }
  const last = points[points.length - 1];
  ctx.textAlign = 'right';
  ctx.fillStyle = last >= 0 ? '#7dffb2' : '#ff9b94';
  ctx.font = `700 ${h * 0.17}px ${TEXT}`;
  ctx.fillText(money(last, true), w * 0.96, h * 0.21);
  ctx.textAlign = 'left';
  const lo = Math.min(0, ...points);
  const hi = Math.max(0, ...points);
  const span = hi - lo || 1;
  const x0 = w * 0.04;
  const x1 = w * 0.96;
  const y0 = h * 0.32;
  const y1 = h * 0.92;
  const y = (v) => y1 - ((v - lo) / span) * (y1 - y0);
  ctx.strokeStyle = 'rgba(200,215,240,0.35)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(x0, y(0));
  ctx.lineTo(x1, y(0));
  ctx.stroke();
  ctx.strokeStyle = last >= 0 ? '#4dff9a' : '#ff6b6b';
  ctx.lineWidth = 4;
  ctx.beginPath();
  points.forEach((v, i) => {
    const x = x0 + (i / (points.length - 1)) * (x1 - x0);
    if (i === 0) ctx.moveTo(x, y(v));
    else ctx.lineTo(x, y(v));
  });
  ctx.stroke();
}

function drawRisk(ctx, w, h, data) {
  // El radar de Vigía: cada punto es una apuesta abierta de Kali; cuanto más lejos del centro y más
  // roja, menos probable que gane ahora mismo.
  ctx.fillStyle = '#04140f';
  ctx.fillRect(0, 0, w, h);
  const positions = ((data && data.kali && data.kali.positions) || []).slice(0, 12);
  const weak = positions.filter((p) => Number(p.chance) < 0.6).length;
  ctx.fillStyle = weak ? '#ff9b94' : '#5be0a0';
  ctx.font = `700 ${h * 0.09}px ${FONT}`;
  ctx.fillText(positions.length ? `RIESGO · ${positions.length} ABIERTAS` : 'SIN APUESTAS ABIERTAS', w * 0.04, h * 0.1);
  if (weak) {
    ctx.textAlign = 'right';
    ctx.fillText(`${weak} FLOJA${weak > 1 ? 'S' : ''}`, w * 0.96, h * 0.1);
    ctx.textAlign = 'left';
  }
  const cx = w / 2;
  const cy = h * 0.57;
  const r = Math.min(w, h) * 0.38;
  ctx.strokeStyle = 'rgba(91,224,160,0.4)';
  ctx.lineWidth = 2;
  for (const k of [0.33, 0.66, 1]) {
    ctx.beginPath();
    ctx.arc(cx, cy, r * k, 0, Math.PI * 2);
    ctx.stroke();
  }
  const t = (data && data.t) || 0;
  const a = t * 1.6;
  const grad = ctx.createLinearGradient(cx, cy, cx + Math.cos(a) * r, cy + Math.sin(a) * r);
  grad.addColorStop(0, 'rgba(91,224,160,0.8)');
  grad.addColorStop(1, 'rgba(91,224,160,0)');
  ctx.strokeStyle = grad;
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
  ctx.stroke();
  positions.forEach((p, i) => {
    const chance = Math.max(0, Math.min(1, Number(p.chance) || 0));
    const ang = (i / positions.length) * Math.PI * 2 - Math.PI / 2;
    const rr = r * (0.18 + 0.82 * (1 - chance));
    ctx.fillStyle = chance >= 0.85 ? '#5be0a0' : chance >= 0.6 ? '#ffd166' : '#ff6b6b';
    ctx.beginPath();
    ctx.arc(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr, Math.max(5, h * 0.035), 0, Math.PI * 2);
    ctx.fill();
  });
}

function drawMail(ctx, w, h, data) {
  const n = (data && data.mail) || 0;
  ctx.fillStyle = n ? '#3a1033' : '#141b2b';
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = n ? '#ff8fd0' : '#4a5578';
  ctx.lineWidth = 6;
  ctx.strokeRect(w * 0.18, h * 0.3, w * 0.64, h * 0.42);
  ctx.beginPath();
  ctx.moveTo(w * 0.18, h * 0.3);
  ctx.lineTo(w * 0.5, h * 0.55);
  ctx.lineTo(w * 0.82, h * 0.3);
  ctx.stroke();
  if (n) {
    ctx.fillStyle = '#ff4d6d';
    ctx.beginPath();
    ctx.arc(w * 0.8, h * 0.25, h * 0.16, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#ffffff';
    ctx.font = `700 ${h * 0.2}px ${TEXT}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(Math.min(n, 99)), w * 0.8, h * 0.26);
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }
}

function drawForge(ctx, w, h, data) {
  // La Herrera: lo que gana y lo que pierde Kali con dinero real, por liga y ciudad.
  panelBg(ctx, w, h, '#2a1408', '#120803');
  const smith = (data && data.smith) || {};
  ctx.fillStyle = '#ffb27a';
  ctx.font = `700 ${h * 0.11}px ${FONT}`;
  ctx.fillText('DINERO REAL · 30 DÍAS', w * 0.05, h * 0.15);
  ctx.font = `700 ${h * 0.1}px ${TEXT}`;
  if (!smith.connected || !smith.totals) {
    ctx.fillStyle = '#f2d2bb';
    ctx.fillText(smith.connected === false ? 'Sin panel' : 'Aún sin cierres', w * 0.05, h * 0.45);
    return;
  }
  const series = (smith.groups && smith.groups.series) || [];
  const shown = series.length > 4 ? [...series.slice(0, 2), ...series.slice(-2)] : series;
  shown.forEach((g, i) => {
    const y = h * (0.33 + i * 0.14);
    ctx.fillStyle = '#fff1e6';
    ctx.fillText(fitText(ctx, g.label, w * 0.62), w * 0.05, y);
    ctx.textAlign = 'right';
    const net = Number(g.net);
    ctx.fillStyle = net > 0 ? '#7dffb2' : net < 0 ? '#ff9b94' : '#ffffff';
    ctx.fillText(money(net, true), w * 0.95, y);
    ctx.textAlign = 'left';
  });
  const t = smith.totals;
  ctx.fillStyle = '#ffd7b8';
  ctx.font = `400 ${h * 0.09}px ${TEXT}`;
  ctx.fillText(`Total ${money(Number(t.net), true)} · ${t.wins} de ${t.markets} ganados`, w * 0.05, h * 0.93);
}

function drawTests(ctx, w, h, data) {
  panelBg(ctx, w, h, '#0d2a33', '#04141a');
  ctx.fillStyle = '#9ff3ff';
  ctx.font = `700 ${h * 0.12}px ${FONT}`;
  ctx.fillText('PRUEBAS · PARTIDOS', w * 0.06, h * 0.17);
  const rows = ((data && data.tests && data.tests.variants) || []).slice(0, 4);
  ctx.font = `700 ${h * 0.105}px ${TEXT}`;
  rows.forEach((r, i) => {
    const y = h * (0.37 + i * 0.17);
    ctx.fillStyle = '#e6fbff';
    ctx.fillText(fitText(ctx, r.name, w * 0.6), w * 0.06, y);
    ctx.textAlign = 'right';
    const pnl = Number(r.pnl);
    ctx.fillStyle = !r.trades ? '#9fb7bd' : pnl > 0 ? '#7dffb2' : pnl < 0 ? '#ff9b94' : '#ffffff';
    ctx.fillText(r.trades ? money(pnl, true) : '—', w * 0.94, y);
    ctx.textAlign = 'left';
  });
  if (!rows.length) {
    ctx.fillStyle = '#9fb7bd';
    ctx.fillText('Esperando partidos', w * 0.06, h * 0.5);
  }
}

function drawVault(ctx, w, h, data) {
  panelBg(ctx, w, h, '#2b2108', '#120d02');
  const kali = (data && data.kali) || {};
  const today = kali.today || {};
  ctx.fillStyle = '#ffe680';
  ctx.font = `700 ${h * 0.2}px ${FONT}`;
  ctx.fillText('HOY', w * 0.07, h * 0.32);
  const net = Number(today.net);
  ctx.fillStyle = net > 0 ? '#7dffb2' : net < 0 ? '#ff9b94' : '#ffffff';
  ctx.font = `700 ${h * 0.3}px ${TEXT}`;
  ctx.textAlign = 'right';
  ctx.fillText(Number.isFinite(net) ? money(net, true) : '—', w * 0.93, h * 0.4);
  ctx.textAlign = 'left';
  ctx.fillStyle = '#e8dcb0';
  ctx.font = `400 ${h * 0.17}px ${TEXT}`;
  ctx.fillText(today.markets ? `${today.wins || 0} ganados · ${today.losses || 0} perdidos` : 'sin cierres todavía', w * 0.07, h * 0.78);
}
