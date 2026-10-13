/* Pixel art del pueblo de bots. Todo se dibuja con código: robots, iconos, edificios y mapa.
 *
 * El mapa es una rejilla de casillas de 16 px de arte. En el centro está el pueblo (14 x 14
 * casillas) y alrededor hay bosque, campos y mar para que las pantallas grandes no queden vacías.
 * Este archivo solo dibuja; quién se mueve y qué dice lo decide app.js.
 */
(function () {
  'use strict';

  const T = 16;
  const OUT = '#2a2238'; // contorno de los robots
  const LINE = '#3b2f45'; // contorno de edificios
  const SCREEN = '#17202e';
  const METAL = '#a9b1c6';
  const METAL_D = '#6b7390';
  const GLASS = '#5d93bb';
  const GLASS_HI = '#a9d6f0';

  // ---------------------------------------------------------------- utilidades

  function rgb(h) {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function hex(c) {
    return '#' + c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, '0')).join('');
  }
  function shade(h, f) {
    return hex(rgb(h).map((v) => v * f));
  }
  function mix(a, b, t) {
    const A = rgb(a);
    const B = rgb(b);
    return hex(A.map((v, i) => v + (B[i] - v) * t));
  }
  function makeCanvas(w, h) {
    const c = document.createElement('canvas');
    c.width = w;
    c.height = h;
    const g = c.getContext('2d');
    g.imageSmoothingEnabled = false;
    return [c, g];
  }
  function R(g, x, y, w, h, color) {
    g.fillStyle = color;
    g.fillRect(x, y, w, h);
  }
  function P(g, x, y, color) {
    g.fillStyle = color;
    g.fillRect(x, y, 1, 1);
  }
  // Dibuja filas de texto: cada letra es un color de la paleta; el punto es vacío.
  function paint(g, rows, pal, x, y) {
    for (let j = 0; j < rows.length; j++) {
      const row = rows[j];
      for (let i = 0; i < row.length; i++) {
        const c = pal[row[i]];
        if (c) {
          g.fillStyle = c;
          g.fillRect((x || 0) + i, (y || 0) + j, 1, 1);
        }
      }
    }
  }
  // Elipse de píxeles centrada en (cx, cy): ocupa de cx-rx a cx+rx (sin incluir).
  function ellipse(g, cx, cy, rx, ry, color, topOnly) {
    g.fillStyle = color;
    for (let dy = -ry; dy < (topOnly ? 0 : ry); dy++) {
      const yy = (dy + 0.5) / ry;
      const hw = Math.round(rx * Math.sqrt(Math.max(0, 1 - yy * yy)));
      if (hw > 0) g.fillRect(cx - hw, cy + dy, hw * 2, 1);
    }
  }
  function hash(x, y, s) {
    let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul((s | 0) + 1, 1442695041);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  // ---------------------------------------------------------------- robots
  // Cada vecino es un robot de 16 x 18: cuatro filas de aire arriba para su sombrero o antena,
  // doce de cabeza y cuerpo y dos de piernas. "side" mira a la izquierda (a la derecha se voltea).

  const BODY = {
    front: [
      '...oooooooooo...',
      '..ollllllllllo..',
      '..olssssssssdo..',
      '..obssssssssdo..',
      '..obssssssssdo..',
      '..obssssssssdo..',
      '..obbbbbbbbbdo..',
      '...oooooooooo...',
      '....obbbbbbo....',
      '...ooblllldoo...',
      '..ogobbbbbdogo..',
      '..oo.oddddo.oo..',
    ],
    back: [
      '...oooooooooo...',
      '..ollllllllllo..',
      '..obbbbbbbbbdo..',
      '..obbhhhhhhbdo..',
      '..obbbbbbbbbdo..',
      '..obbhhhhhhbdo..',
      '..obbbbbbbbbdo..',
      '...oooooooooo...',
      '....obbbbbbo....',
      '...oobbbbbdoo...',
      '..ogobbbbbdogo..',
      '..oo.oddddo.oo..',
    ],
    side: [
      '...oooooooooo...',
      '..ollllllllllo..',
      '..osssssbbbbdo..',
      '..osssssbbhbdo..',
      '..osssssbbhbdo..',
      '..osssssbbbbdo..',
      '..obbbbbbbbbdo..',
      '...oooooooooo...',
      '....obbbbbbo....',
      '....obllgbdo....',
      '....obbbgbdo....',
      '.....oddodo.....',
    ],
  };
  const LEGS = {
    front: [
      ['.....oh..ho.....', '.....oo..oo.....'],
      ['.....oo..ho.....', '.........oo.....'],
      ['.....oh..oo.....', '.....oo.........'],
    ],
    side: [
      ['......ohho......', '.....ooooo......'],
      ['.....oh..ho.....', '....oo...oo.....'],
      ['......ohho......', '.....ooooo......'],
    ],
  };
  LEGS.back = LEGS.front;

  // Ojos sobre la pantalla de la cara: [x, y, ancho, alto] en el sprite de 16 x 18.
  const EYES = {
    front: {
      ok: [[5, 7, 2, 2], [9, 7, 2, 2]],
      blink: [[5, 8, 2, 1], [9, 8, 2, 1]],
      happy: [[4, 8, 1, 1], [5, 7, 1, 1], [6, 8, 1, 1], [9, 8, 1, 1], [10, 7, 1, 1], [11, 8, 1, 1]],
      sad: [[5, 8, 2, 1], [9, 8, 2, 1], [6, 7, 1, 1], [9, 7, 1, 1]],
      sleep: [[5, 8, 2, 1], [9, 8, 2, 1]],
      sick: [[4, 6, 1, 1], [6, 6, 1, 1], [5, 7, 1, 1], [4, 8, 1, 1], [6, 8, 1, 1],
        [9, 6, 1, 1], [11, 6, 1, 1], [10, 7, 1, 1], [9, 8, 1, 1], [11, 8, 1, 1]],
      alert: [[5, 6, 2, 3], [9, 6, 2, 3]],
    },
    side: {
      ok: [[4, 7, 2, 2]],
      blink: [[4, 8, 2, 1]],
      happy: [[3, 8, 1, 1], [4, 7, 1, 1], [5, 8, 1, 1]],
      sad: [[4, 8, 2, 1], [5, 7, 1, 1]],
      sleep: [[4, 8, 2, 1]],
      sick: [[3, 6, 1, 1], [5, 6, 1, 1], [4, 7, 1, 1], [3, 8, 1, 1], [5, 8, 1, 1]],
      alert: [[4, 6, 2, 3]],
    },
  };
  // Vigía tiene un solo ojo grande.
  const CYCLOPS = {
    front: {
      ok: [[6, 6, 4, 4]],
      blink: [[6, 8, 4, 1]],
      happy: [[6, 8, 1, 1], [7, 7, 2, 1], [9, 8, 1, 1]],
      sad: [[6, 8, 4, 1], [7, 7, 2, 1]],
      sleep: [[6, 8, 4, 1]],
      sick: [[6, 6, 1, 1], [9, 6, 1, 1], [7, 7, 2, 2], [6, 9, 1, 1], [9, 9, 1, 1]],
      alert: [[6, 6, 4, 4]],
    },
    side: {
      ok: [[3, 6, 3, 4]],
      blink: [[3, 8, 3, 1]],
      happy: [[3, 8, 1, 1], [4, 7, 1, 1], [5, 8, 1, 1]],
      sad: [[3, 8, 3, 1]],
      sleep: [[3, 8, 3, 1]],
      sick: [[3, 6, 1, 1], [5, 6, 1, 1], [4, 7, 1, 1], [3, 8, 1, 1], [5, 8, 1, 1]],
      alert: [[3, 6, 3, 4]],
    },
  };
  const EYE_COLOR = {
    ok: '#9ff3ff', blink: '#9ff3ff', happy: '#9ff3ff', sad: '#a8c4ff', sleep: '#4f7086', sick: '#c7f59c', alert: '#ffd166',
  };

  function eyeRects(id, view, eyes) {
    if (view === 'back') return [];
    const set = id === 'vigia' ? CYCLOPS : EYES;
    return (set[view] || set.front)[eyes] || set.front.ok;
  }

  // Lo que distingue a cada vecino. `a` alterna con el tiempo (luces que parpadean, antenas que giran).
  const ACCESSORY = {
    kali(g, view) {
      const x = view === 'side' ? 8 : 7;
      R(g, x, 2, 2, 2, METAL_D);
      R(g, x - 1, 0, 4, 1, '#ffe680');
      R(g, x - 1, 1, 4, 1, '#e3a827');
      P(g, x - 1, 0, '#c98a1b');
      P(g, x + 2, 1, '#b97a14');
      if (view === 'front') {
        R(g, 7, 13, 2, 1, '#27b36a');
        R(g, 6, 14, 4, 1, '#27b36a');
      }
    },
    nube(g) {
      paint(g, ['......cccc......', '....ccwwwwcc....', '...cwwwwwwwwc...', '...cwwwwwwwwc...'],
        { c: '#9fb8d0', w: '#ffffff' }, 0, 0);
      P(g, 6, 2, '#e3edf6');
      P(g, 10, 3, '#e3edf6');
    },
    vigia(g, view, a) {
      paint(g, ['......oooo......', '.....orrrro.....', '.....orrrro.....'],
        { o: OUT, r: a ? '#ff5050' : '#a8333a' }, 0, 1);
      if (a) {
        P(g, 6, 2, '#ffd0d0');
        P(g, 7, 2, '#ffd0d0');
      }
    },
    radar(g, view, a) {
      const dish = a
        ? ['..........owwo..', '.........owwwwo.', '..........owwo..', '...........hh...']
        : ['.........owwo...', '........owwwwo..', '.........owwo...', '...........hh...'];
      paint(g, dish, { o: OUT, w: '#eef3f7', h: METAL_D }, 0, 0);
      P(g, a ? 12 : 11, 1, a ? '#4dff9a' : '#2a8f5a');
    },
    cronista(g, view) {
      paint(g, ['......p.........', '....pppppp......', '...pppppppp.....'], { p: '#5d3591' }, 0, 1);
      P(g, 5, 2, '#7d55b5');
      if (view === 'front') {
        const rim = '#f2d27a';
        [[4, 7], [7, 7], [4, 8], [7, 8], [8, 7], [11, 7], [8, 8], [11, 8]].forEach(([x, y]) => P(g, x, y, rim));
        R(g, 5, 6, 2, 1, rim);
        R(g, 9, 6, 2, 1, rim);
      }
    },
  };

  function palette(color) {
    return { o: OUT, b: color, d: shade(color, 0.7), l: mix(color, '#ffffff', 0.42), s: SCREEN, g: METAL, h: METAL_D };
  }

  const robotCache = new Map();

  // Un fotograma del robot. view: front | back | side; step: 0 quieto, 1 y 2 pasos;
  // eyes: ok | blink | happy | sad | sleep | sick | alert; a: parpadeo de luces y antenas.
  function robot(bot, view, step, eyes, a) {
    const key = [bot.id, bot.color, view, step, eyes, a ? 1 : 0].join('|');
    let c = robotCache.get(key);
    if (c) return c;
    let g;
    [c, g] = makeCanvas(16, 18);
    const pal = palette(bot.color || '#9aa3b5');
    paint(g, BODY[view], pal, 0, 4);
    paint(g, LEGS[view][step] || LEGS[view][0], pal, 0, 16);
    const color = EYE_COLOR[eyes] || EYE_COLOR.ok;
    for (const [x, y, w, h] of eyeRects(bot.id, view, eyes)) R(g, x, y, w, h, color);
    if (bot.id === 'vigia' && view !== 'back' && (eyes === 'ok' || eyes === 'alert')) {
      R(g, view === 'side' ? 3 : 7, 7, view === 'side' ? 2 : 2, 2, eyes === 'alert' ? '#7a3b00' : '#123a4a');
    }
    const extra = ACCESSORY[bot.id];
    if (extra) extra(g, view, a);
    robotCache.set(key, c);
    return c;
  }

  // Solo los ojos: de noche se pintan encima de la oscuridad para que brillen.
  function robotEyes(bot, view, eyes) {
    const key = ['eyes', bot.id, view, eyes].join('|');
    let c = robotCache.get(key);
    if (c) return c;
    let g;
    [c, g] = makeCanvas(16, 18);
    const color = EYE_COLOR[eyes] || EYE_COLOR.ok;
    for (const [x, y, w, h] of eyeRects(bot.id, view, eyes)) R(g, x, y, w, h, color);
    robotCache.set(key, c);
    return c;
  }

  // Retrato para listas y fichas (PNG en data: para usarlo en <img>).
  const avatarCache = new Map();
  function avatar(bot, mood) {
    const eyes = moodEyes(mood);
    const key = `${bot.id}|${bot.color}|${eyes}`;
    if (avatarCache.has(key)) return avatarCache.get(key);
    const [c, g] = makeCanvas(20, 20);
    g.drawImage(robot(bot, 'front', 0, eyes, true), 2, 1);
    const url = c.toDataURL('image/png');
    avatarCache.set(key, url);
    return url;
  }

  function moodEyes(mood) {
    return { happy: 'happy', sad: 'sad', sleep: 'sleep', sick: 'sick', alert: 'alert' }[mood] || 'ok';
  }

  // ---------------------------------------------------------------- iconos
  // Iconitos para lo que siente un bot: aparecen en un globo pequeño sobre su cabeza.

  const ICONS = {
    alert: { rows: ['..x..', '..x..', '..x..', '..x..', '.....', '..x..'], color: '#e5484d' },
    question: { rows: ['.xxx.', 'x...x', '...x.', '..x..', '.....', '..x..'], color: '#3d6fd8' },
    heart: { rows: ['.x.x.', 'xxxxx', 'xxxxx', '.xxx.', '..x..'], color: '#e5484d' },
    check: { rows: ['....x', '...x.', 'x.x..', '.x...'], color: '#23a35f' },
    note: { rows: ['..xxx', '..x.x', '..x.x', 'xxx.x', 'xx.xx', '...xx'], color: '#8e5bd0' },
    coin: { rows: ['.xxx.', 'xx.xx', 'x.x.x', 'xx.xx', '.xxx.'], color: '#e0a92a' },
    letter: { rows: ['xxxxx', 'xx.xx', 'x.x.x', 'x...x', 'xxxxx'], color: '#3d6fd8' },
    zzz: { rows: ['xxx..', '..x..', '.x...', 'xxx..', '...xx', '....x'], color: '#6b7aa8' },
    drop: { rows: ['..x..', '.xxx.', 'xxxxx', 'xxxxx', '.xxx.'], color: '#4fa4e6' },
    star: { rows: ['..x..', 'xxxxx', '.xxx.', 'x...x'], color: '#e0a92a' },
  };
  const iconCache = new Map();
  function icon(name) {
    if (iconCache.has(name)) return iconCache.get(name);
    const def = ICONS[name] || ICONS.question;
    const [c, g] = makeCanvas(11, 12);
    R(g, 1, 0, 9, 1, OUT);
    R(g, 0, 1, 11, 8, OUT);
    R(g, 1, 9, 9, 1, OUT);
    R(g, 1, 1, 9, 8, '#fffdf6');
    R(g, 4, 10, 3, 1, OUT);
    R(g, 5, 11, 1, 1, OUT);
    R(g, 5, 10, 1, 1, '#fffdf6');
    const top = 1 + Math.floor((8 - def.rows.length) / 2);
    paint(g, def.rows, { x: def.color }, 3, top);
    iconCache.set(name, c);
    return c;
  }

  // ---------------------------------------------------------------- edificios
  // Cada uno devuelve su dibujo fijo, dónde va respecto a su solar (ox, oy), sus ventanas (para
  // encenderlas de noche), sus luces y, si tiene, lo que se mueve (dynamic).

  function bricks(g, x0, y0, w, h, mortar, rowH, brickW) {
    for (let y = y0; y < y0 + h; y += rowH) {
      R(g, x0, y, w, 1, mortar);
      const off = ((y - y0) / rowH) % 2 ? Math.floor(brickW / 2) : 0;
      for (let x = x0 + off; x < x0 + w; x += brickW) R(g, x, y + 1, 1, Math.min(rowH - 1, y0 + h - y - 1), mortar);
    }
  }
  function windowPane(g, x, y, w, h) {
    R(g, x - 1, y - 1, w + 2, h + 2, LINE);
    R(g, x, y, w, h, GLASS);
    R(g, x, y, w, 1, GLASS_HI);
    P(g, x, y + 1, GLASS_HI);
  }
  function door(g, x, y, w, h, wood) {
    R(g, x - 1, y - 1, w + 2, h + 1, LINE);
    R(g, x, y, w, h, wood || '#7a4a2c');
    R(g, x, y, w, 1, shade(wood || '#7a4a2c', 1.25));
    R(g, x + w - 2, y + Math.floor(h / 2), 1, 1, '#f2c94c');
  }

  function observatory() {
    const [c, g] = makeCanvas(48, 58);
    R(g, 3, 30, 42, 28, LINE);
    R(g, 4, 31, 40, 26, '#eef2f7');
    R(g, 35, 31, 9, 26, '#d3dbe6');
    R(g, 4, 53, 40, 4, '#b8c3d0');
    // Cúpula azul con su luz a la izquierda.
    ellipse(g, 24, 31, 22, 21, LINE, true);
    for (let dy = -20; dy < 0; dy++) {
      const yy = (dy + 0.5) / 20;
      const hw = Math.round(21 * Math.sqrt(Math.max(0, 1 - yy * yy)));
      for (let x = 24 - hw; x < 24 + hw; x++) {
        const u = (x - 24 + 0.5) / hw;
        P(g, x, 31 + dy, u < -0.5 ? '#86c8f3' : u > 0.45 ? '#3f86c9' : '#55a2e3');
      }
    }
    R(g, 1, 29, 46, 3, LINE);
    R(g, 2, 30, 44, 1, '#c9d3df');
    // Rendija y telescopio.
    R(g, 21, 13, 6, 16, '#1f2a40');
    for (let i = 0; i < 10; i++) {
      R(g, 24 + i, 20 - i, 3, 3, LINE);
    }
    for (let i = 0; i < 10; i++) {
      R(g, 25 + i, 20 - i, 2, 2, '#9aa3b5');
    }
    R(g, 34, 9, 4, 4, LINE);
    R(g, 35, 10, 2, 2, '#cfd6e2');
    // Veleta.
    R(g, 23, 6, 2, 6, LINE);
    R(g, 19, 6, 10, 1, LINE);
    R(g, 26, 4, 3, 3, '#e5484d');
    // Puerta, ventanas y termómetro.
    door(g, 20, 42, 8, 15);
    windowPane(g, 9, 37, 6, 6);
    windowPane(g, 33, 37, 6, 6);
    R(g, 15, 40, 3, 11, LINE);
    R(g, 16, 41, 1, 8, '#ffffff');
    R(g, 16, 44, 1, 5, '#e5484d');
    R(g, 15, 49, 3, 3, '#e5484d');
    return { canvas: c, ox: 0, oy: -10, windows: [[9, 37, 6, 6], [33, 37, 6, 6]], lights: [] };
  }

  function tower() {
    const [c, g] = makeCanvas(32, 66);
    R(g, 4, 18, 24, 48, LINE);
    R(g, 5, 19, 22, 46, '#aab1bc');
    bricks(g, 5, 19, 22, 46, '#8a919d', 5, 8);
    R(g, 22, 19, 5, 46, 'rgba(40,30,60,0.16)');
    R(g, 5, 19, 2, 46, 'rgba(255,255,255,0.12)');
    // Almenas.
    R(g, 2, 13, 28, 7, LINE);
    R(g, 3, 14, 26, 5, '#c3c9d2');
    for (let i = 0; i < 4; i++) {
      R(g, 2 + i * 8, 8, 6, 6, LINE);
      R(g, 3 + i * 8, 9, 4, 5, '#c3c9d2');
    }
    R(g, 3, 18, 26, 1, '#8a919d');
    // Mástil (la bandera se mueve).
    R(g, 15, 0, 2, 9, '#5a4a3a');
    // Ventana del vigía, ventana media y puerta.
    R(g, 10, 22, 12, 10, LINE);
    R(g, 11, 23, 10, 8, '#2a3550');
    R(g, 13, 36, 6, 9, LINE);
    R(g, 14, 37, 4, 7, '#2a3550');
    R(g, 11, 49, 10, 2, LINE);
    door(g, 12, 51, 8, 15, '#6b4429');
    return {
      canvas: c, ox: 0, oy: -18, windows: [[14, 37, 4, 7]], lights: [],
      dynamic(g2, x, y, t, s) {
        const wave = Math.floor(t * 3) % 2;
        R(g2, x + 17, y + 1, 9, 5, LINE);
        R(g2, x + 17, y + 2, 8, 3, '#ff6b6b');
        R(g2, x + 17, y + 2, 8, 1, '#ff9a9a');
        if (wave) R(g2, x + 23, y + 1, 3, 1, 'rgba(0,0,0,0)');
        R(g2, x + 25, y + (wave ? 3 : 2), 2, 2, '#ff6b6b');
        // La ventana de arriba: de noche, luz; si hay alerta, roja y parpadeando.
        const blink = s.alert && Math.floor(t * 2.5) % 2 === 0;
        if (blink) R(g2, x + 11, y + 23, 10, 8, '#ff5050');
      },
      glow(s, t) {
        if (s.alert && Math.floor(t * 2.5) % 2 === 0) return [{ x: 16, y: 27, r: 26, color: 'rgba(255,80,80,0.55)' }];
        return [{ x: 16, y: 27, r: 14, color: 'rgba(255,214,140,0.45)', night: true }];
      },
      lit: [[11, 23, 10, 8]],
    };
  }

  function lighthouse() {
    const [c, g] = makeCanvas(32, 68);
    // Rocas.
    ellipse(g, 16, 63, 16, 6, '#5d6470');
    ellipse(g, 16, 62, 15, 5, '#7d8590');
    R(g, 3, 60, 4, 2, '#9aa2ad');
    R(g, 22, 59, 5, 2, '#9aa2ad');
    // Torre a rayas, más estrecha arriba.
    for (let y = 22; y < 61; y++) {
      const k = (y - 22) / 38;
      const hw = Math.round(7 + k * 4);
      R(g, 16 - hw - 1, y, (hw + 1) * 2, 1, LINE);
      const band = Math.floor((y - 22) / 7) % 2;
      R(g, 16 - hw, y, hw * 2, 1, band ? '#3fbf7f' : '#f4f4ee');
      R(g, 16 + hw - 3, y, 3, 1, band ? '#2e9963' : '#d6d6cc');
    }
    // Balcón, cuarto de la luz y tejado.
    R(g, 6, 18, 20, 4, LINE);
    R(g, 7, 19, 18, 2, '#3a4152');
    R(g, 9, 8, 14, 11, LINE);
    R(g, 10, 9, 12, 9, '#fff1b8');
    R(g, 15, 9, 2, 9, LINE);
    for (let i = 0; i < 7; i++) {
      R(g, 15 - i, 1 + i, 2 + i * 2, 1, LINE);
      if (i > 0) R(g, 16 - i, 1 + i, i * 2, 1, i < 3 ? '#e05a4f' : '#c0392b');
    }
    R(g, 15, 0, 2, 1, LINE);
    door(g, 13, 51, 6, 9, '#6b4429');
    windowPane(g, 14, 32, 4, 5);
    return {
      canvas: c, ox: 0, oy: -20, windows: [[14, 32, 4, 5]], lights: [],
      glow(s, t) {
        const list = [{ x: 16, y: 13, r: 12, color: 'rgba(255,240,170,0.7)', night: true }];
        return list;
      },
      beam: { x: 16, y: 13 },
    };
  }

  function library() {
    const [c, g] = makeCanvas(48, 54);
    R(g, 2, 24, 44, 30, LINE);
    R(g, 3, 25, 42, 28, '#f1e4c8');
    R(g, 36, 25, 9, 28, '#dccaa6');
    R(g, 3, 25, 42, 2, '#8a5a3c');
    R(g, 3, 39, 42, 2, '#8a5a3c');
    R(g, 3, 25, 2, 28, '#8a5a3c');
    R(g, 43, 25, 2, 28, '#8a5a3c');
    // Tejado morado de tejas.
    for (let y = 2; y < 27; y++) {
      const inset = Math.max(0, 8 - (y - 2));
      R(g, inset, y, 48 - inset * 2, 1, LINE);
      const row = (y - 2) % 4;
      R(g, inset + 1, y, 46 - inset * 2, 1, row === 3 ? '#62379a' : y < 12 ? '#9b6ad8' : '#8e5bd0');
    }
    for (let y = 3; y < 26; y += 4) {
      const off = ((y - 3) / 4) % 2 ? 3 : 0;
      for (let x = 4 + off; x < 44; x += 6) R(g, x, y, 1, 3, '#7445b0');
    }
    R(g, 34, 2, 12, 24, 'rgba(30,10,50,0.14)');
    R(g, 1, 26, 46, 1, LINE);
    // Ventana redonda del tejado.
    ellipse(g, 24, 15, 6, 6, LINE);
    ellipse(g, 24, 15, 5, 5, '#2a3550');
    R(g, 23, 10, 2, 10, LINE);
    R(g, 19, 14, 10, 2, LINE);
    // Ventanas con libros y puerta doble.
    for (const wx of [7, 32]) {
      windowPane(g, wx, 29, 9, 9);
      const colors = ['#e5484d', '#3d6fd8', '#f2c94c', '#27b36a', '#8e5bd0', '#e58f3a', '#3d6fd8', '#e5484d', '#27b36a'];
      for (let i = 0; i < 9; i++) R(g, wx + i, 33 + (i % 3 === 1 ? 1 : 0), 1, 5 - (i % 3 === 1 ? 1 : 0), colors[i]);
    }
    R(g, 18, 37, 12, 17, LINE);
    R(g, 19, 38, 10, 16, '#7a4a2c');
    R(g, 19, 38, 10, 1, '#9c6a44');
    R(g, 23, 38, 2, 16, '#5a3820');
    P(g, 22, 46, '#f2c94c');
    P(g, 25, 46, '#f2c94c');
    return { canvas: c, ox: 0, oy: -6, windows: [[7, 29, 9, 4], [32, 29, 9, 4], [19, 10, 10, 10]], lights: [] };
  }

  function market() {
    const [c, g] = makeCanvas(64, 54);
    R(g, 2, 18, 60, 36, LINE);
    R(g, 3, 19, 58, 34, '#b77a4a');
    for (let y = 22; y < 53; y += 4) R(g, 3, y, 58, 1, '#9c6337');
    // Puesto abierto: dentro, oscuro, con su tablero de precios.
    R(g, 8, 22, 48, 17, '#3e2a20');
    R(g, 11, 23, 22, 12, LINE);
    R(g, 12, 24, 20, 10, '#122130');
    // Mostrador con montones de monedas.
    R(g, 4, 38, 56, 8, LINE);
    R(g, 5, 39, 54, 6, '#d39a5f');
    R(g, 5, 39, 54, 1, '#ecbd88');
    for (const [x, h] of [[38, 5], [43, 7], [48, 4], [53, 6]]) {
      for (let i = 0; i < h; i++) {
        R(g, x, 37 - i, 4, 1, i % 2 ? '#f2c94c' : '#d49a1f');
      }
      R(g, x, 37 - h, 4, 1, '#ffe680');
    }
    // Cajas delante.
    for (const x of [3, 52]) {
      R(g, x, 45, 10, 9, LINE);
      R(g, x + 1, 46, 8, 7, '#a0643c');
      R(g, x + 1, 49, 8, 1, '#7a4a2c');
      R(g, x + 4, 46, 1, 7, '#7a4a2c');
    }
    R(g, 26, 46, 12, 8, '#3e2a20');
    // Toldo a rayas con el borde ondulado.
    R(g, 0, 4, 64, 15, LINE);
    for (let x = 1; x < 63; x++) {
      const stripe = Math.floor((x - 1) / 6) % 2;
      R(g, x, 5, 1, 13, stripe ? '#fff4d6' : '#f2c94c');
      R(g, x, 5, 1, 1, stripe ? '#ffffff' : '#ffe680');
    }
    for (let x = 1; x < 63; x += 6) {
      const stripe = Math.floor((x - 1) / 6) % 2;
      R(g, x, 18, 6, 1, LINE);
      R(g, x + 1, 18, 4, 1, stripe ? '#fff4d6' : '#f2c94c');
      R(g, x + 1, 19, 4, 1, LINE);
    }
    // Letrero con una moneda.
    R(g, 24, 0, 16, 7, LINE);
    R(g, 25, 1, 14, 5, '#5a3820');
    ellipse(g, 32, 3, 3, 2, '#ffd84a');
    P(g, 31, 2, '#fff2a8');
    return {
      canvas: c, ox: 0, oy: -6, windows: [], lights: [],
      dynamic(g2, x, y, t, s) {
        // La gráfica del tablero se mueve; verde si hoy va ganando, roja si va perdiendo.
        const color = s.kaliDown ? '#ff6b6b' : '#4dff9a';
        const shift = Math.floor(t * 4);
        for (let i = 0; i < 20; i++) {
          const k = i + shift;
          const v = Math.round(4 + 2.5 * Math.sin(k * 0.7) + 1.5 * Math.sin(k * 1.9));
          const trend = s.kaliDown ? Math.floor(i / 6) : 3 - Math.floor(i / 6);
          P(g2, x + 12 + i, y + 24 + Math.max(0, Math.min(9, v + trend - 1)), color);
        }
      },
      glow() {
        return [{ x: 32, y: 30, r: 26, color: 'rgba(255,200,120,0.45)', night: true }];
      },
      lit: [[8, 22, 48, 1]],
    };
  }

  function home() {
    const [c, g] = makeCanvas(32, 40);
    R(g, 2, 18, 28, 22, LINE);
    R(g, 3, 19, 26, 20, '#f4dcb5');
    R(g, 24, 19, 5, 20, '#e2c596');
    for (let y = 2; y < 21; y++) {
      const inset = Math.max(0, 6 - (y - 2));
      R(g, inset, y, 32 - 2 * inset, 1, LINE);
      R(g, inset + 1, y, 30 - 2 * inset, 1, (y - 2) % 4 === 3 ? '#a8322f' : '#d9534f');
    }
    R(g, 23, 2, 8, 18, 'rgba(60,10,10,0.14)');
    R(g, 21, 0, 6, 8, LINE);
    R(g, 22, 1, 4, 7, '#9a6b5a');
    door(g, 7, 26, 6, 14, '#8a5a3c');
    windowPane(g, 18, 25, 7, 6);
    R(g, 21, 25, 1, 6, LINE);
    R(g, 18, 27, 7, 1, LINE);
    R(g, 17, 32, 9, 3, '#7a4a2c');
    P(g, 18, 31, '#e5484d');
    P(g, 20, 31, '#f2c94c');
    P(g, 22, 31, '#ff8fb1');
    P(g, 24, 31, '#e5484d');
    return {
      canvas: c, ox: 0, oy: -8, windows: [[18, 25, 7, 6]], lights: [],
      dynamic(g2, x, y, t) {
        for (let i = 0; i < 3; i++) {
          const p = (t * 0.35 + i / 3) % 1;
          const sx = x + 24 + Math.round(Math.sin(p * 6 + i) * 2 + p * 4);
          const sy = y - Math.round(p * 14);
          const size = p < 0.5 ? 2 : 3;
          R(g2, sx, sy, size, size, `rgba(235,235,240,${(0.75 * (1 - p)).toFixed(2)})`);
        }
      },
      glow() {
        return [{ x: 21, y: 28, r: 14, color: 'rgba(255,214,140,0.5)', night: true }];
      },
    };
  }

  function fence(g, x0, y0, w, h, gapFrom, gapTo) {
    const post = '#8a5a3c';
    const rail = '#b5835a';
    for (let x = x0; x < x0 + w; x++) {
      if (x >= gapFrom && x < gapTo) continue;
      P(g, x, y0 + h - 4, rail);
      P(g, x, y0 + 2, rail);
    }
    for (let y = y0; y < y0 + h; y++) {
      P(g, x0, y, rail);
      P(g, x0 + w - 1, y, rail);
    }
    for (let x = x0; x < x0 + w; x += 6) {
      if (!(x >= gapFrom && x < gapTo)) {
        R(g, x, y0 + h - 7, 2, 5, LINE);
        R(g, x, y0 + h - 7, 1, 4, post);
      }
      R(g, x, y0, 2, 5, LINE);
      R(g, x, y0, 1, 4, post);
    }
  }

  function plot(kind) {
    const [c, g] = makeCanvas(48, 36);
    R(g, 1, 7, 46, 27, '#c9a678');
    for (let i = 0; i < 60; i++) P(g, 2 + Math.floor(hash(i, 3, kind.length) * 44), 8 + Math.floor(hash(i, 7, kind.length) * 25), '#b38f62');
    fence(g, 0, 4, 48, 32, 18, 30);
    // Cinta de obras en la entrada.
    for (let x = 18; x < 30; x++) P(g, x, 31, Math.floor(x / 2) % 2 ? '#f2c94c' : '#2a2238');
    if (kind === 'lab') {
      // Andamio y ladrillos.
      for (const x of [12, 26]) R(g, x, 10, 2, 18, '#8a6a4a');
      for (const y of [12, 19, 26]) R(g, 12, y, 16, 1, '#8a6a4a');
      R(g, 12, 18, 16, 2, '#c49a6c');
      for (let i = 0; i < 3; i++) R(g, 33 + i * 3, 25 - (i % 2), 3, 2, '#b5523b');
      R(g, 34, 22, 6, 2, '#b5523b');
      // Cartel con un matraz.
      R(g, 5, 18, 2, 12, '#6b4429');
      R(g, 1, 12, 10, 9, LINE);
      R(g, 2, 13, 8, 7, '#efe0bb');
      paint(g, ['..x..', '..x..', '.xxx.', 'xxxxx'], { x: '#3fbf7f' }, 4, 14);
    } else {
      // Pista con conos y una banderita.
      ellipse(g, 24, 20, 15, 8, '#d9bb8a');
      ellipse(g, 24, 20, 11, 5, '#c9a678');
      for (const [x, y] of [[13, 17], [33, 17], [24, 12]]) {
        R(g, x, y, 3, 1, '#ff8a3a');
        R(g, x - 1, y + 1, 5, 2, '#ff8a3a');
        R(g, x - 1, y + 2, 5, 1, '#ffffff');
      }
      R(g, 41, 10, 1, 14, '#6b4429');
      R(g, 42, 10, 4, 3, '#8e5bd0');
      R(g, 5, 18, 2, 12, '#6b4429');
      R(g, 1, 12, 10, 9, LINE);
      R(g, 2, 13, 8, 7, '#efe0bb');
      paint(g, ['x...x', 'xxxxx', '.xxx.', '..x..', '.xxx.'], { x: '#e0a92a' }, 4, 14);
    }
    return { canvas: c, ox: 0, oy: -4, windows: [], lights: [] };
  }

  function fountain() {
    const [c, g] = makeCanvas(32, 32);
    ellipse(g, 16, 20, 16, 11, LINE);
    ellipse(g, 16, 20, 15, 10, '#bdb6aa');
    ellipse(g, 16, 19, 15, 9, '#d6d0c5');
    ellipse(g, 16, 20, 12, 7, LINE);
    ellipse(g, 16, 20, 11, 6, '#5aaee3');
    ellipse(g, 16, 21, 9, 4, '#4b9fd5');
    R(g, 14, 9, 4, 12, LINE);
    R(g, 15, 10, 2, 11, '#e2ddd3');
    R(g, 10, 8, 12, 3, LINE);
    R(g, 11, 9, 10, 1, '#e2ddd3');
    return {
      canvas: c, ox: 0, oy: 0, windows: [], lights: [],
      dynamic(g2, x, y, t) {
        for (let i = 0; i < 8; i++) {
          const p = (t * 0.9 + i / 8) % 1;
          const side = i % 2 ? 1 : -1;
          const dx = Math.round(side * p * 7);
          const dy = Math.round(-6 + 26 * (p - 0.35) * (p - 0.35) * 2.2);
          P(g2, x + 16 + dx, y + 8 + dy, p < 0.5 ? '#ffffff' : '#bfe6fb');
        }
        const r = Math.floor((t * 6) % 8);
        P(g2, x + 8 + r, y + 21, '#bfe6fb');
        P(g2, x + 23 - r, y + 19, '#bfe6fb');
      },
    };
  }

  function board() {
    const [c, g] = makeCanvas(16, 20);
    R(g, 2, 9, 2, 11, '#6b4429');
    R(g, 12, 9, 2, 11, '#6b4429');
    R(g, 0, 2, 16, 10, LINE);
    R(g, 1, 3, 14, 8, '#b98a5a');
    R(g, 1, 3, 14, 1, '#d1a676');
    return {
      canvas: c, ox: 0, oy: -4, windows: [], lights: [],
      dynamic(g2, x, y, t, s) {
        const spots = [[2, 4], [6, 5], [10, 4], [4, 7]];
        for (let i = 0; i < Math.min(4, s.notes || 0); i++) {
          const [px, py] = spots[i];
          R(g2, x + px, y + py, 3, 4, '#fbf6e9');
          P(g2, x + px + 1, y + py, '#e5484d');
        }
      },
    };
  }

  function mailbox() {
    const [c, g] = makeCanvas(16, 20);
    R(g, 7, 10, 2, 10, '#6b4429');
    R(g, 3, 3, 10, 8, LINE);
    R(g, 4, 4, 8, 6, '#4f7bd9');
    R(g, 4, 4, 8, 1, '#7aa0ee');
    R(g, 5, 6, 6, 1, '#27407a');
    return {
      canvas: c, ox: 0, oy: -4, windows: [], lights: [],
      dynamic(g2, x, y, t, s) {
        if (s.mail) {
          const bob = Math.floor(t * 2) % 2;
          R(g2, x + 12, y + 0 + bob, 1, 6, '#5a4a3a');
          R(g2, x + 13, y + 0 + bob, 3, 3, '#e5484d');
        } else {
          R(g2, x + 12, y + 6, 4, 1, '#c23b3f');
        }
      },
    };
  }

  function lamp() {
    const [c, g] = makeCanvas(16, 24);
    R(g, 7, 6, 2, 16, '#3a3f52');
    R(g, 5, 21, 6, 3, '#3a3f52');
    R(g, 5, 1, 6, 1, '#3a3f52');
    R(g, 5, 2, 6, 5, LINE);
    R(g, 6, 3, 4, 3, '#d8dde6');
    return {
      canvas: c, ox: 0, oy: -8, windows: [], lights: [],
      glow() {
        return [{ x: 8, y: 4, r: 18, color: 'rgba(255,220,150,0.6)', night: true }];
      },
      lit: [[6, 3, 4, 3]],
    };
  }

  // ---------------------------------------------------------------- terreno

  const COLORS = {
    grass: '#6fb35a', grassD: '#5d9f4b', grassL: '#86c76d', tuft: '#4e8c3f',
    path: '#d8b27b', pathD: '#c39a63', pathL: '#e7c690', pathEdge: '#b88e5a',
    plaza: '#c9c2b6', plazaD: '#aaa296', plazaL: '#ddd7cc',
    water: '#4b9fd5', waterD: '#3f90c8', foam: '#d3f0fd',
    sand: '#ecd9a0', sandD: '#dcc487',
    soil: '#7d5236', soilL: '#946543', crop: '#6cc05d',
  };

  function tileGrass(g, x, y, tx, ty) {
    R(g, x, y, T, T, COLORS.grass);
    for (let i = 0; i < 7; i++) {
      const h = hash(tx, ty, i);
      const px = Math.floor(h * 16);
      const py = Math.floor(hash(tx, ty, i + 20) * 16);
      P(g, x + px, y + py, i < 4 ? COLORS.grassD : COLORS.grassL);
    }
    if (hash(tx, ty, 99) < 0.35) {
      const px = 2 + Math.floor(hash(tx, ty, 98) * 11);
      const py = 3 + Math.floor(hash(tx, ty, 97) * 10);
      P(g, x + px, y + py, COLORS.tuft);
      P(g, x + px + 2, y + py, COLORS.tuft);
      P(g, x + px + 1, y + py + 1, COLORS.tuft);
    }
  }
  function tileFlowers(g, x, y, tx, ty) {
    tileGrass(g, x, y, tx, ty);
    const colors = ['#ffffff', '#ffd84a', '#ff8fb1', '#e5484d', '#b9a6ff'];
    for (let i = 0; i < 5; i++) {
      const px = 1 + Math.floor(hash(tx, ty, i + 40) * 14);
      const py = 1 + Math.floor(hash(tx, ty, i + 50) * 14);
      const col = colors[Math.floor(hash(tx, ty, i + 60) * colors.length)];
      P(g, x + px, y + py, col);
      P(g, x + px, y + py + 1, COLORS.tuft);
    }
  }
  function tilePath(g, x, y, tx, ty) {
    R(g, x, y, T, T, COLORS.path);
    for (let i = 0; i < 8; i++) {
      const px = Math.floor(hash(tx, ty, i + 70) * 16);
      const py = Math.floor(hash(tx, ty, i + 80) * 16);
      P(g, x + px, y + py, i < 5 ? COLORS.pathD : COLORS.pathL);
    }
  }
  function tilePlaza(g, x, y, tx, ty) {
    R(g, x, y, T, T, COLORS.plaza);
    for (let row = 0; row < 4; row++) {
      R(g, x, y + row * 4, T, 1, COLORS.plazaD);
      const off = (row + ty) % 2 ? 4 : 0;
      for (let bx = off; bx < T; bx += 8) R(g, x + bx, y + row * 4 + 1, 1, 3, COLORS.plazaD);
      for (let bx = off; bx < T; bx += 8) {
        if (hash(tx * 4 + bx, ty * 4 + row, 5) < 0.3) R(g, x + bx + 1, y + row * 4 + 1, 7, 3, COLORS.plazaL);
      }
    }
  }
  function tileWater(g, x, y, tx, ty) {
    R(g, x, y, T, T, COLORS.water);
    for (let i = 0; i < 4; i++) {
      const px = Math.floor(hash(tx, ty, i + 110) * 13);
      const py = Math.floor(hash(tx, ty, i + 120) * 16);
      R(g, x + px, y + py, 3, 1, COLORS.waterD);
    }
  }
  function tileSand(g, x, y, tx, ty) {
    R(g, x, y, T, T, COLORS.sand);
    for (let i = 0; i < 6; i++) {
      P(g, x + Math.floor(hash(tx, ty, i + 130) * 16), y + Math.floor(hash(tx, ty, i + 140) * 16), COLORS.sandD);
    }
  }
  function tileCrops(g, x, y, tx, ty) {
    R(g, x, y, T, T, COLORS.soil);
    for (let row = 0; row < 4; row++) {
      R(g, x, y + row * 4 + 1, T, 2, COLORS.soilL);
      for (let i = 1; i < T; i += 3) {
        if (hash(tx * 16 + i, ty * 4 + row, 9) < 0.8) {
          P(g, x + i, y + row * 4, COLORS.crop);
          P(g, x + i, y + row * 4 + 1, '#4fa046');
        }
      }
    }
  }

  function tree(g, x, y, kind) {
    ellipse(g, x + 8, y + 15, 7, 2, 'rgba(20,40,20,0.28)');
    R(g, x + 6, y + 8, 4, 7, '#6b4429');
    R(g, x + 8, y + 8, 2, 7, '#553420');
    if (kind === 'pine') {
      for (let i = 0; i < 3; i++) {
        const top = y - 6 + i * 5;
        for (let j = 0; j < 6; j++) {
          const hw = 2 + j + i;
          R(g, x + 8 - hw, top + j, hw * 2, 1, j === 5 ? '#24523a' : '#2f6b48');
          R(g, x + 8 - hw, top + j, Math.max(1, hw - 1), 1, '#3f8a5c');
        }
      }
    } else {
      ellipse(g, x + 8, y + 3, 8, 8, '#2e6a37');
      ellipse(g, x + 8, y + 2, 7, 7, '#3f8f45');
      ellipse(g, x + 6, y, 4, 4, '#58aa57');
      P(g, x + 5, y - 2, '#7cc672');
      if (kind === 'apple') {
        P(g, x + 10, y + 1, '#e5484d');
        P(g, x + 4, y + 4, '#e5484d');
        P(g, x + 11, y + 6, '#e5484d');
      }
    }
  }

  // ---------------------------------------------------------------- el pueblo

  const WORLD_W = 30;
  const WORLD_H = 26;
  const CORE_X = 8;
  const CORE_Y = 6;
  // Letras: casas (O V F B K L A H), fuente f, tablón n, buzón m, farolas l, árbol t,
  // camino =, plaza #, flores ",", agua ~, arena s.
  const CORE = [
    '..t...,....~~~',
    '.OOO..VV..FF~~',
    '.OOO..VV..FF~~',
    '.OOO..VV..FFs~',
    '..=...=...=.s~',
    '============s~',
    '.BBB=#n##=KKKK',
    '.BBB=#ff#=KKKK',
    '.BBB=#ff#=KKKK',
    '..=.=l##l=.=..',
    '==============',
    '.LLL=,HHm=AAA.',
    '.LLL=.HH.=AAA.',
    '==============',
  ];
  const BLOCKED = 'OVFBKLAHfnmlt~c';
  const COST = { '=': 1, '#': 1, '.': 3, ',': 4, s: 3 };

  // Dónde se pone cada uno (en casillas del pueblo).
  const SPOTS = {
    nube: [2, 4], vigia: [6, 4], radar: [10, 4], cronista: [2, 9], kali: [11, 9],
    mailbox: [8, 12], plaza: [6, 9], board: [7, 6], home: [6, 13], lab: [2, 13], arena: [11, 13],
  };

  const PLACES = [
    { id: 'observatorio', owner: 'nube', at: [1, 1], size: [3, 3], make: observatory },
    { id: 'torre', owner: 'vigia', at: [6, 1], size: [2, 3], make: tower },
    { id: 'faro', owner: 'radar', at: [10, 1], size: [2, 3], make: lighthouse },
    { id: 'biblioteca', owner: 'cronista', at: [1, 6], size: [3, 3], make: library },
    { id: 'mercado', owner: 'kali', at: [10, 6], size: [4, 3], make: market },
    { id: 'casa', owner: 'tu', at: [6, 11], size: [2, 2], make: home },
    { id: 'laboratorio', owner: 'lab', at: [1, 11], size: [3, 2], make: () => plot('lab'), baked: true },
    { id: 'arena', owner: 'arena', at: [10, 11], size: [3, 2], make: () => plot('arena'), baked: true },
    { id: 'fuente', owner: null, at: [6, 7], size: [2, 2], make: fountain },
    { id: 'tablon', owner: 'cronista', at: [6, 6], size: [1, 1], make: board },
    { id: 'buzon', owner: 'tu', at: [8, 11], size: [1, 1], make: mailbox },
    { id: 'farola1', owner: null, at: [5, 9], size: [1, 1], make: lamp },
    { id: 'farola2', owner: null, at: [8, 9], size: [1, 1], make: lamp },
  ];

  function buildGrid() {
    const grid = [];
    for (let y = 0; y < WORLD_H; y++) {
      const row = [];
      for (let x = 0; x < WORLD_W; x++) {
        let ch = '.';
        // El mar a la derecha, con su playa.
        const wobble = Math.round(Math.sin(y * 0.9) * 0.8 + Math.sin(y * 0.37 + 1) * 0.9);
        if (y < CORE_Y) {
          if (x >= CORE_X + 11 + Math.max(0, wobble)) ch = '~';
          else if (x === CORE_X + 10 + Math.max(0, wobble) && y > 1) ch = 's';
        } else if (y >= CORE_Y + 6) {
          if (x >= CORE_X + 15 + wobble) ch = '~';
          else if (x >= CORE_X + 14 + wobble) ch = 's';
        } else if (x >= CORE_X + 14) {
          ch = '~';
        }
        row.push(ch);
      }
      grid.push(row);
    }
    for (let cy = 0; cy < CORE.length; cy++) {
      for (let cx = 0; cx < CORE[cy].length; cx++) grid[CORE_Y + cy][CORE_X + cx] = CORE[cy][cx];
    }
    // Caminos que salen del pueblo: al oeste y al sur.
    for (let x = 0; x < CORE_X; x++) grid[CORE_Y + 10][x] = '=';
    for (let y = CORE_Y + 14; y < WORLD_H; y++) grid[y][CORE_X + 4] = '=';
    // Campos al suroeste.
    for (let y = CORE_Y + 12; y < CORE_Y + 18 && y < WORLD_H; y++) {
      for (let x = 1; x < 7; x++) grid[y][x] = 'c';
    }
    // Bosque, árboles sueltos y flores alrededor.
    const near = (x, y) => {
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const r = grid[y + dy];
          if (r && (r[x + dx] === '=' || r[x + dx] === 's' || r[x + dx] === 'c')) return true;
        }
      }
      return false;
    };
    for (let y = 0; y < WORLD_H; y++) {
      for (let x = 0; x < WORLD_W; x++) {
        const inCore = x >= CORE_X && x < CORE_X + 14 && y >= CORE_Y && y < CORE_Y + 14;
        if (inCore || grid[y][x] !== '.' || near(x, y)) continue;
        const west = x < CORE_X - 1;
        const north = y < CORE_Y - 1;
        const south = y > CORE_Y + 14;
        let p = 0;
        if (west && north) p = 0.62;
        else if (west || north) p = 0.42;
        else if (south) p = 0.22;
        else p = 0.08;
        const h = hash(x, y, 7);
        if (h < p) grid[y][x] = 't';
        else if (h > 0.93) grid[y][x] = ',';
      }
    }
    return grid;
  }

  function town() {
    const grid = buildGrid();
    const W = WORLD_W * T;
    const H = WORLD_H * T;
    const [bg, g] = makeCanvas(W, H);
    const kind = (x, y) => (grid[y] && grid[y][x]) || '~';
    for (let y = 0; y < WORLD_H; y++) {
      for (let x = 0; x < WORLD_W; x++) {
        const ch = kind(x, y);
        const px = x * T;
        const py = y * T;
        if (ch === '=') tilePath(g, px, py, x, y);
        else if (ch === '#' || ch === 'f' || ch === 'n' || ch === 'l') tilePlaza(g, px, py, x, y);
        else if (ch === '~') tileWater(g, px, py, x, y);
        else if (ch === 's') tileSand(g, px, py, x, y);
        else if (ch === 'c') tileCrops(g, px, py, x, y);
        else if (ch === ',') tileFlowers(g, px, py, x, y);
        else tileGrass(g, px, py, x, y);
      }
    }
    // Bordes: el camino se come un poco de hierba, la plaza tiene bordillo y el agua espuma.
    const soft = (ch) => ch === '=' || ch === '#' || ch === 'f' || ch === 'n' || ch === 'l';
    for (let y = 0; y < WORLD_H; y++) {
      for (let x = 0; x < WORLD_W; x++) {
        const ch = kind(x, y);
        const px = x * T;
        const py = y * T;
        if (ch === '=') {
          const sides = [[0, -1], [0, 1], [-1, 0], [1, 0]];
          for (const [dx, dy] of sides) {
            const n = kind(x + dx, y + dy);
            if (soft(n) || n === 'O' || n === 'V' || n === 'F' || n === 'B' || n === 'K' || n === 'H' ||
                n === 'L' || n === 'A' || n === 'm') continue;
            for (let i = 0; i < T; i++) {
              const ex = dx === 0 ? px + i : dx < 0 ? px : px + T - 1;
              const ey = dy === 0 ? py + i : dy < 0 ? py : py + T - 1;
              P(g, ex, ey, COLORS.pathEdge);
              if (hash(ex, ey, 3) < 0.35) P(g, ex + (dx === 0 ? 0 : -dx), ey + (dy === 0 ? 0 : -dy), COLORS.grass);
            }
          }
        } else if (ch === '#' || ch === 'l') {
          const sides = [[0, -1], [0, 1], [-1, 0], [1, 0]];
          for (const [dx, dy] of sides) {
            const n = kind(x + dx, y + dy);
            if (n === '#' || n === 'f' || n === 'n' || n === 'l') continue;
            if (dx === 0) R(g, px, dy < 0 ? py : py + T - 1, T, 1, COLORS.plazaD);
            else R(g, dx < 0 ? px : px + T - 1, py, 1, T, COLORS.plazaD);
          }
        } else if (ch === '~') {
          const sides = [[0, -1], [0, 1], [-1, 0], [1, 0]];
          for (const [dx, dy] of sides) {
            const n = kind(x + dx, y + dy);
            if (n === '~') continue;
            for (let i = 0; i < T; i++) {
              if (hash(x * 16 + i, y, dx * 3 + dy) < 0.7) {
                const ex = dx === 0 ? px + i : dx < 0 ? px : px + T - 1;
                const ey = dy === 0 ? py + i : dy < 0 ? py : py + T - 1;
                P(g, ex, ey, COLORS.foam);
              }
            }
          }
        }
      }
    }
    // Sombras de las casas.
    for (const place of PLACES) {
      if (place.baked || place.size[0] < 2) continue;
      const px = (CORE_X + place.at[0]) * T;
      const py = (CORE_Y + place.at[1]) * T;
      R(g, px + 2, py + place.size[1] * T - 1, place.size[0] * T, 3, 'rgba(30,30,50,0.22)');
      R(g, px + place.size[0] * T, py + 4, 3, place.size[1] * T - 2, 'rgba(30,30,50,0.18)');
    }
    // Árboles (pintados en el fondo: nadie camina detrás de ellos).
    for (let y = 0; y < WORLD_H; y++) {
      for (let x = 0; x < WORLD_W; x++) {
        if (kind(x, y) !== 't') continue;
        const h = hash(x, y, 11);
        tree(g, x * T, y * T, h < 0.35 ? 'pine' : h < 0.45 ? 'apple' : 'round');
      }
    }

    const things = [];
    const hits = [];
    for (const place of PLACES) {
      const art = place.make();
      const x = (CORE_X + place.at[0]) * T + art.ox;
      const y = (CORE_Y + place.at[1]) * T + art.oy;
      const base = (CORE_Y + place.at[1] + place.size[1]) * T;
      if (place.baked) g.drawImage(art.canvas, x, y);
      things.push({ ...place, ...art, x, y, base, w: art.canvas.width, h: art.canvas.height });
      if (place.owner) hits.push({ owner: place.owner, place: place.id, x, y, w: art.canvas.width, h: art.canvas.height });
    }

    const spots = {};
    for (const [key, [cx, cy]] of Object.entries(SPOTS)) spots[key] = [CORE_X + cx, CORE_Y + cy];

    function cost(x, y) {
      const ch = kind(x, y);
      if (BLOCKED.includes(ch)) return Infinity;
      return COST[ch] || 3;
    }
    function isWater(x, y) {
      return kind(x, y) === '~';
    }

    return {
      T, W, H, cols: WORLD_W, rows: WORLD_H, grid, background: bg, things, hits, spots, cost, isWater,
      core: { x: CORE_X * T, y: CORE_Y * T, w: 14 * T, h: 14 * T },
    };
  }

  window.Pixel = { T, robot, robotEyes, avatar, moodEyes, icon, town, shade, mix, makeCanvas, hash, ellipse, paint };
})();
