"use strict";

/* ===================== utilidades ===================== */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Crea nodos sin innerHTML: todo el texto que viene de la API se inserta como texto.
function el(tag, props, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

const STRATEGY_NAMES = { favorites: "Favoritos", fair_value: "Valor justo", market_maker: "Creador de mercado" };

const fmt = {
  cents(v) {
    if (v == null || v === "") return "—";
    const c = Math.round(Number(v) * 1000) / 10;
    return (Number.isInteger(c) ? String(c) : c.toFixed(1).replace(".", ",")) + "¢";
  },
  // 0.932 → "93%" (sin signo, para umbrales y probabilidades).
  share(v) {
    if (v == null || v === "") return "—";
    return Math.round(Number(v) * 100).toLocaleString("es-ES") + "%";
  },
  money(v, sign = false) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    const abs = Math.abs(n).toLocaleString("es-ES", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return (n < 0 ? "−" : sign && n > 0 ? "+" : "") + "$" + abs;
  },
  qty(v) {
    if (v == null || v === "") return "—";
    const n = Number(v);
    return n.toLocaleString("es-ES", { maximumFractionDigits: 2 });
  },
  pct(v, digits = 1) {
    if (v == null || v === "") return "—";
    const n = Number(v) * 100;
    const abs = Math.abs(n).toLocaleString("es-ES", { minimumFractionDigits: digits, maximumFractionDigits: digits });
    return (n > 0 ? "+" : n < 0 ? "−" : "") + abs + "%";
  },
  hours(h) {
    if (h == null) return "—";
    if (h < 0) return "cerrado";
    if (h < 1) return Math.max(1, Math.round(h * 60)) + " min";
    if (h < 48) return Math.floor(h) + " h";
    return Math.round(h / 24) + " d";
  },
  ago(iso) {
    if (!iso) return "—";
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    if (s < 60) return `hace ${s} s`;
    if (s < 3600) return `hace ${Math.round(s / 60)} min`;
    return `hace ${Math.round(s / 3600)} h`;
  },
  time(iso) {
    if (!iso) return "";
    return new Date(iso).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  },
  // Hora corta; si no es de hoy, con el día delante ("7 oct 22:15").
  clock(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const time = d.toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === new Date().toDateString()) return time;
    return `${d.toLocaleDateString("es-ES", { day: "numeric", month: "short" })} ${time}`;
  },
};

const signClass = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "");
const cap = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : "");

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) if (value != null) node.setAttribute(key, value);
  return node;
}

/* ===================== nombres de los mercados ===================== */

// Las series del bot: KXHIGH + ciudad (temperatura máxima) y KX + liga + GAME (partidos).
const CITIES = {
  NY: "Nueva York",
  LAX: "Los Ángeles",
  CHI: "Chicago",
  MIA: "Miami",
  AUS: "Austin",
  DEN: "Denver",
  PHIL: "Filadelfia",
};
const MONTHS = { JAN: "ene", FEB: "feb", MAR: "mar", APR: "abr", MAY: "may", JUN: "jun", JUL: "jul", AUG: "ago", SEP: "sept", OCT: "oct", NOV: "nov", DEC: "dic" };

// "KXHIGHNY-26OCT08-B66.5" → "8 oct": la fecha del evento va en el ticker.
function tickerDate(ticker) {
  const m = /^(\d{2})([A-Z]{3})(\d{2})/.exec(ticker.split("-")[1] || "");
  return m && MONTHS[m[2]] ? `${Number(m[3])} ${MONTHS[m[2]]}` : "";
}

// Los tramos llegan en inglés: "66° to 67°", "82° or below", "91° or above".
function bracketText(text) {
  return text
    .replace(/(-?\d+(?:\.\d+)?)°?\s+to\s+(-?\d+(?:\.\d+)?)°/i, "$1° a $2°")
    .replace(/\s+or (below|less|lower)\b/i, " o menos")
    .replace(/\s+or (above|more|higher)\b/i, " o más");
}

// Nombre legible de un mercado:
//   title: el evento ("Máxima en Chicago", "Los Angeles D vs San Diego");
//   outcome: a qué se apuesta ("78° a 79°", "gana Los Angeles D");
//   short: para las filas ("Chicago · 78° a 79°", "Los Angeles D gana");
//   long: para el detalle; group: el tipo ("Temp. máxima", "MLB"); date: "8 oct".
function marketLabel(ticker, title, subtitle) {
  const t = String(ticker || "");
  const parts = t.split("-");
  const date = tickerDate(t);
  const weather = /^KX(HIGH|LOW)([A-Z]+)$/.exec(parts[0]);
  if (weather) {
    const city = CITIES[weather[2]] || weather[2];
    const high = weather[1] === "HIGH";
    let outcome = subtitle ? bracketText(subtitle) : "";
    const tail = /^B(-?\d+)\.5$/.exec(parts[2] || ""); // sin nombre: "B66.5" es el tramo 66° a 67°
    if (!outcome && tail) outcome = `${tail[1]}° a ${Number(tail[1]) + 1}°`;
    if (!outcome && parts[2]) outcome = `tramo ${parts[2]}`;
    const event = `${high ? "Máxima" : "Mínima"} en ${city}`;
    return {
      kind: "weather",
      title: event,
      outcome,
      date,
      short: outcome ? `${city} · ${outcome}` : event,
      long: outcome ? `${event}, ${outcome}` : event,
      group: high ? "Temp. máxima" : "Temp. mínima",
    };
  }
  const game = /^KX([A-Z0-9]+)GAME$/.exec(parts[0]);
  if (game) {
    const match = title ? title.replace(/\s*winner\??$/i, "").replace(/\?$/, "") : game[1];
    const team = subtitle || parts[2] || "";
    const short = team ? `${team} gana` : match;
    return { kind: "sports", title: match, outcome: team ? `gana ${team}` : "", date, short, long: short, group: game[1] };
  }
  const name = title || t;
  const short = subtitle ? `${name} · ${subtitle}` : name;
  return { kind: "other", title: name, outcome: subtitle || "", date, short, long: short, group: "" };
}

// Nombres que ya se conocen (de posiciones, órdenes o /api/labels); null = no tiene.
const labelCache = new Map();
let labelQueue = new Set();
let labelTimer = null;

function rememberLabel(ticker, title, subtitle) {
  if (ticker && title) labelCache.set(ticker, { title, subtitle: subtitle || "" });
}

function labelFor(ticker, title, subtitle) {
  rememberLabel(ticker, title, subtitle);
  const known = labelCache.get(ticker);
  return marketLabel(ticker, known && known.title, known && known.subtitle);
}

const labelText = (l) => [l.title, l.outcome].filter(Boolean).join(" · ");

// Nombre de un mercado dentro de una frase; si aún no se conoce, se pide y se actualiza solo.
function tickerNode(ticker) {
  if (!labelCache.has(ticker)) {
    labelQueue.add(ticker);
    clearTimeout(labelTimer);
    labelTimer = setTimeout(flushLabels, 80);
  }
  return el("span", { class: "tk", "data-ticker": ticker, text: labelText(labelFor(ticker)) });
}

async function flushLabels() {
  const tickers = [...labelQueue].filter((t) => !labelCache.has(t)).slice(0, 100);
  labelQueue = new Set();
  if (tickers.length) {
    for (const t of tickers) labelCache.set(t, null); // no se vuelven a pedir aunque falle
    try {
      const names = await api(`/api/labels?tickers=${encodeURIComponent(tickers.join(","))}`);
      for (const [t, n] of Object.entries(names || {})) labelCache.set(t, n);
    } catch {
      /* se queda el nombre sacado del ticker */
    }
  }
  renameTickers();
}

function renameTickers() {
  for (const node of $$(".tk[data-ticker]")) node.textContent = labelText(labelFor(node.dataset.ticker));
}

function chip(side) {
  const kind = side === "yes" ? "yes" : side === "no" ? "no" : "both";
  return el("span", { class: `chip ${kind}`, text: kind === "yes" ? "SÍ" : kind === "no" ? "NO" : "SÍ+NO" });
}

const valueClass = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "zero");

function feeEstimate(rate, count, price) {
  const raw = rate * count * price * (1 - price);
  return raw > 0 ? Math.ceil(raw * 100 - 1e-9) / 100 : 0;
}

class AuthError extends Error {}

async function api(path, { method = "GET", body } = {}) {
  const options = { method, credentials: "same-origin", headers: { "X-Requested-With": "kalshi-bot" } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, options);
  } catch {
    throw new Error("Sin conexión con el panel");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  if (res.status === 401 && path !== "/api/login") {
    showLogin();
    throw new AuthError((data && data.error) || "Inicia sesión");
  }
  if (!res.ok) throw new Error((data && data.error) || `Error ${res.status}`);
  return data;
}

let toastTimer = null;
function toast(message, kind = "") {
  const node = $("#toast");
  node.textContent = message;
  node.className = "toast " + kind;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (node.hidden = true), kind === "error" ? 5000 : 3000);
}

/* ===================== hoja inferior y confirmaciones ===================== */

let sheetOnClose = null;

function openSheet(...nodes) {
  $("#sheet-body").replaceChildren(...nodes.filter(Boolean));
  $("#sheet").hidden = false;
  $("#sheet-backdrop").hidden = false;
  $("#sheet").scrollTop = 0;
}

function closeSheet() {
  $("#sheet").hidden = true;
  $("#sheet-backdrop").hidden = true;
  const cb = sheetOnClose;
  sheetOnClose = null;
  if (cb) cb();
}

function confirmDialog({ title, message, confirmLabel = "Confirmar", danger = false, requireText = null, extra = null }) {
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      sheetOnClose = null;
      closeSheet();
      resolve(value);
    };
    const input = requireText
      ? el("input", { placeholder: `Escribe ${requireText}`, autocapitalize: "characters", autocomplete: "off" })
      : null;
    const check = extra ? el("input", { type: "checkbox" }) : null;
    const ok = el("button", { class: `btn full ${danger ? "danger" : "primary"}`, text: confirmLabel, type: "button" });
    ok.addEventListener("click", () => {
      if (input && input.value.trim().toUpperCase() !== requireText) {
        toast(`Escribe ${requireText} para confirmar`, "error");
        input.focus();
        return;
      }
      finish({ ok: true, extra: check ? check.checked : undefined });
    });
    const cancel = el("button", { class: "btn full", text: "Cancelar", type: "button", onclick: () => finish({ ok: false }) });
    openSheet(
      el("h2", { text: title }),
      typeof message === "string" ? el("p", { class: "muted", text: message }) : message,
      check ? el("label", { class: "toggle" }, el("span", { text: extra.label }), check) : null,
      input,
      ok,
      cancel,
    );
    sheetOnClose = () => finish({ ok: false });
  });
}

/* ===================== estado y navegación ===================== */

const state = {
  view: "home",
  status: null,
  settings: null,
  logsAfter: 0,
  pollCount: 0,
  strategy: null,
  readers: {},
  fairValues: [],
  resultsDays: 30,
  resultsScope: "bot",
  detailFrom: "results",
  curve: null,
};

function showLogin() {
  $("#app").hidden = true;
  $("#login").hidden = false;
  setTimeout(() => $("#login-password").focus(), 50);
}

function showApp() {
  $("#login").hidden = true;
  $("#app").hidden = false;
}

function switchView(name) {
  state.view = name;
  document.body.dataset.view = name;
  for (const view of $$(".view")) view.hidden = view.id !== `view-${name}`;
  const tabName = name === "detail" ? state.detailFrom : name;
  for (const tab of $$(".tab")) tab.classList.toggle("active", tab.dataset.view === tabName);
  $("#view-title").textContent = $(`#view-${name}`).dataset.title;
  window.scrollTo(0, 0);
  if (name === "home") loadHome();
  if (name === "settings") loadSettings();
  if (name === "results") loadResults();
  if (name === "markets" && $("#market-card").hidden && $("#event-card").hidden) loadEvents();
}

async function init() {
  for (const tab of $$(".tab")) tab.addEventListener("click", () => switchView(tab.dataset.view));
  $("#login-form").addEventListener("submit", onLogin);
  $("#sheet-backdrop").addEventListener("click", closeSheet);
  $("#sheet-close").addEventListener("click", closeSheet);
  $("#bot-toggle").addEventListener("click", toggleBot);
  for (const button of $$("#mode-seg button")) button.addEventListener("click", () => setMode(button.dataset.mode));
  $("#size-less").addEventListener("click", () => changeSize(-1));
  $("#size-more").addEventListener("click", () => changeSize(1));
  $("#row-max-price").addEventListener("click", editMaxPrice);
  $("#row-max-loss").addEventListener("click", editMaxLoss);
  for (const toggle of $$("#push-fills, #push-settlements")) toggle.addEventListener("click", () => togglePush(toggle.dataset.kind));
  $("#push-test").addEventListener("click", testPush);
  $("#btn-kill").addEventListener("click", killBot);
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  $("#tile-balance").addEventListener("click", () => {
    loadHome();
    toast("Actualizando…");
  });
  for (const button of $$("#results-period button")) {
    button.addEventListener("click", () => loadResults(Number(button.dataset.days)));
  }
  for (const button of $$("#results-scope button")) {
    button.addEventListener("click", () => loadResults(null, button.dataset.scope));
  }
  $("#market-search").addEventListener("submit", searchMarkets);
  $("#btn-scan").addEventListener("click", runScan);
  $("#btn-research").addEventListener("click", runResearch);
  $("#btn-sweep").addEventListener("click", runSweep);
  $("#btn-save-settings").addEventListener("click", saveSettings);
  $("#btn-logout").addEventListener("click", logout);
  $("#btn-save-cred").addEventListener("click", saveCredentials);
  $("#btn-del-cred").addEventListener("click", deleteCredentials);
  $("#cred-file").addEventListener("change", loadKeyFile);
  $("#btn-diagnose").addEventListener("click", () => runDiagnosis(false));
  $("#btn-diagnose-order").addEventListener("click", () => runDiagnosis(true));
  $("#btn-fv-add").addEventListener("click", () => {
    state.fairValues.push({ ticker: "", pct: "" });
    renderFairValues();
  });
  $("#btn-fv-save").addEventListener("click", saveFairValues);
  for (const button of $$("#env-seg button")) button.addEventListener("click", () => setEnv(button.dataset.env));
  // Al volver a la app, todo al día (saldo, apuestas y lo ganado hoy).
  document.addEventListener("visibilitychange", () => {
    if (document.hidden || $("#app").hidden) return;
    if (state.view === "home") loadHome();
    else refreshStatus().catch(() => {});
  });

  try {
    await refreshStatus();
    showApp();
    loadHome();
  } catch (err) {
    if (!(err instanceof AuthError)) {
      showLogin();
      $("#login-error").textContent = err.message;
    }
  }
  setInterval(poll, 5000);
}

async function onLogin(event) {
  event.preventDefault();
  $("#login-error").textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { password: $("#login-password").value } });
    $("#login-password").value = "";
    await refreshStatus();
    showApp();
    switchView("home");
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST" });
  } catch {
    /* da igual */
  }
  showLogin();
}

function poll() {
  if (document.hidden || $("#app").hidden) return;
  state.pollCount += 1;
  if (state.view === "home") {
    refreshStatus().catch(() => {});
    refreshLogs();
    if (state.pollCount % 4 === 0) refreshPortfolio();
    if (state.pollCount % 12 === 0) loadHomeResults();
  } else if (state.view === "settings") {
    refreshStatus().catch(() => {});
  }
}

/* ===================== inicio ===================== */

function loadHome() {
  refreshLogs();
  // Las apuestas y los resultados necesitan saber antes si hay API key.
  refreshStatus()
    .catch(() => {})
    .then(() => {
      refreshPortfolio();
      loadHomeResults();
    });
}

async function refreshStatus() {
  const status = await api("/api/status");
  state.status = status;
  renderStatus(status);
  return status;
}

function strategyLabel(name) {
  const known = state.settings && state.settings.strategies.find((s) => s.name === name);
  return (known && known.label) || STRATEGY_NAMES[name] || name;
}

function renderStatus(s) {
  for (const pill of $$("#env-badge, #env-badge-home")) {
    pill.textContent = s.is_production ? "REAL" : "DEMO";
    pill.className = "env-pill" + (s.is_production ? "" : " demo");
  }
  $("#home-date").textContent = new Date()
    .toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short" })
    .replace(/[.,]/g, "")
    .toUpperCase();

  const bot = s.bot;
  const running = bot.state === "running";
  const live = running && bot.mode === "live";
  const dot = $("#state-dot");
  dot.className = "dot" + (running ? (live ? " running" : " sim") : bot.state === "halted" ? " halted" : "");
  let label = "Bot en pausa";
  let sub = `Estrategia ${strategyLabel(bot.strategy)} · actívalo en Ajustes`;
  if (running) {
    label = live ? (s.is_production ? "Bot activo" : "Bot activo (demo)") : "Simulando";
    const parts = [strategyLabel(bot.strategy), `${bot.markets.length} mercados`];
    if (bot.last_tick_at) parts.push(`última vuelta ${fmt.ago(bot.last_tick_at)}`);
    sub = parts.join(" · ");
  } else if (bot.state === "halted") {
    label = "Bot frenado";
    sub = "Revisa el aviso y vuelve a activarlo en Ajustes";
  }
  $("#state-label").textContent = label;
  $("#state-sub").textContent = sub;

  const banner = $("#state-banner");
  banner.hidden = false;
  if (bot.halted_reason && !running) {
    banner.className = "banner danger";
    banner.textContent = "Freno de emergencia: " + bot.halted_reason;
  } else if (!s.credentials.configured) {
    banner.className = "banner info";
    banner.textContent = "Sin API key: puedes simular con datos reales, pero para operar configúrala en Ajustes → Avanzado.";
  } else if (running && bot.consecutive_errors > 0) {
    banner.className = "banner warn";
    banner.textContent = `La API está fallando (${bot.consecutive_errors} vueltas seguidas). Mira la actividad.`;
  } else {
    banner.hidden = true;
  }

  const balance = s.balance;
  $("#stat-equity").textContent = balance ? fmt.money(balance.equity) : "—";
  $("#stat-cash").textContent = balance ? `${fmt.money(balance.cash)} disponible` : "sin saldo";
  $("#stat-portfolio").textContent = balance ? `${fmt.money(balance.portfolio_value)} en juego` : "";
  // Lo que lleva la sesión, junto al freno que la vigila.
  const session = $("#session-line");
  session.hidden = s.session_pnl == null;
  if (s.session_pnl != null) {
    const limit = Number(s.risk.max_session_loss_dollars);
    session.replaceChildren(
      "Desde que arrancó el bot: ",
      el("b", { class: signClass(s.session_pnl), text: fmt.money(s.session_pnl, true) }),
      limit > 0 ? ` · se frena si se pierden ${fmt.money(limit)}` : "",
    );
  }
  const note = $("#balance-note");
  note.hidden = !(s.balance_error || (!s.credentials.configured && running));
  note.textContent = s.balance_error
    ? "No se pudo leer el saldo: " + s.balance_error
    : "Saldo virtual de simulación (sin API key).";

  renderBotCard(s);
}

// Ajustes: el interruptor del bot y el modo (Real / Simulación).
function renderBotCard(s) {
  const bot = s.bot;
  const running = bot.state === "running";
  const mode = selectedMode();
  const toggle = $("#bot-toggle");
  toggle.setAttribute("aria-checked", String(running));
  toggle.disabled = state.botBusy === true;
  let title = "Bot en pausa";
  let sub = "No abrirá operaciones nuevas";
  if (running) {
    title = bot.mode === "live" ? "Bot activo" : "Bot simulando";
    sub = bot.mode === "live" ? "Opera solo según tus límites" : "Mira el mercado sin enviar órdenes";
  } else if (bot.state === "halted") {
    title = "Bot frenado";
    sub = bot.halted_reason || "Se paró por seguridad";
  }
  $("#bot-title").textContent = title;
  $("#bot-sub").textContent = sub;
  $("#mode-live").textContent = s.is_production ? "Real" : "Demo";
  for (const button of $$("#mode-seg button")) {
    button.classList.toggle("active", button.dataset.mode === mode);
    button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
  }
  $("#mode-help").textContent =
    mode === "live"
      ? s.is_production
        ? "Envía órdenes con tu dinero de Kalshi."
        : "Envía órdenes en el entorno demo de Kalshi (dinero ficticio)."
      : "Hace lo mismo pero sin enviar órdenes: verás en Actividad lo que habría comprado.";
}

function selectedMode() {
  const s = state.status;
  if (s && s.bot.state === "running") return s.bot.mode;
  let saved = null;
  try {
    saved = localStorage.getItem("kb-mode");
  } catch {
    saved = null;
  }
  if (saved === "live" || saved === "sim") return saved;
  return s && s.credentials.configured ? "live" : "sim";
}

async function setMode(mode) {
  const s = state.status;
  if (!s || state.botBusy) return;
  if (mode === "live" && !s.credentials.configured) {
    toast("Para operar configura tu API key en Ajustes → Avanzado", "error");
    return;
  }
  const running = s.bot.state === "running";
  if (running && s.bot.mode !== mode) {
    const ok = await confirmDialog({
      title: mode === "live" ? "Pasar a operar" : "Pasar a simulación",
      message: "El bot se detiene y vuelve a arrancar en el modo nuevo.",
      confirmLabel: "Cambiar",
    });
    if (!ok.ok) return;
  }
  try {
    localStorage.setItem("kb-mode", mode);
  } catch {
    /* el modo elegido no se recuerda en este navegador */
  }
  if (running && s.bot.mode !== mode) {
    if (await stopBot(true)) await startBot(mode);
  }
  renderBotCard(state.status);
}

async function toggleBot() {
  const s = state.status;
  if (!s || state.botBusy) return;
  if (s.bot.state === "running") await stopBot();
  else await startBot(selectedMode());
}

async function startBot(mode) {
  const s = state.status;
  const body = { mode };
  if (mode === "live") {
    const risk = s.risk;
    const limits = `Límites: ${fmt.qty(risk.max_order_contracts)} contratos por orden, ${fmt.money(
      risk.max_total_exposure_dollars,
    )} comprometidos como máximo, freno a ${fmt.money(risk.max_session_loss_dollars)} de pérdida.`;
    const result = s.is_production
      ? await confirmDialog({
          title: "Operar con dinero real",
          message: `El bot enviará órdenes reales con la estrategia ${strategyLabel(s.bot.strategy)}. ${limits} Escribe REAL para confirmar.`,
          confirmLabel: "Empezar a operar",
          danger: true,
          requireText: "REAL",
        })
      : await confirmDialog({
          title: "Operar en demo",
          message: `El bot enviará órdenes en el entorno demo (dinero ficticio). ${limits}`,
          confirmLabel: "Empezar",
        });
    if (!result.ok) return false;
    if (s.is_production) body.confirm = "REAL";
  }
  state.botBusy = true;
  renderBotCard(s);
  try {
    await api("/api/bot/start", { method: "POST", body });
    toast(mode === "live" ? "Bot en marcha" : "Simulación en marcha", "success");
    return true;
  } catch (err) {
    toast(err.message, "error");
    return false;
  } finally {
    state.botBusy = false;
    await refreshStatus().catch(() => {});
    setTimeout(refreshLogs, 800);
  }
}

async function stopBot(quiet = false) {
  state.botBusy = true;
  renderBotCard(state.status);
  try {
    await api("/api/bot/stop", { method: "POST" });
    if (!quiet) toast("Bot en pausa");
    return true;
  } catch (err) {
    toast(err.message, "error");
    return false;
  } finally {
    state.botBusy = false;
    await refreshStatus().catch(() => {});
    refreshLogs();
    refreshPortfolio();
  }
}

async function killBot() {
  const result = await confirmDialog({
    title: "Freno de emergencia",
    message: "Detiene el bot y cancela todas sus órdenes abiertas. Las posiciones que ya tengas se quedan como están.",
    confirmLabel: "Frenar y cancelar",
    danger: true,
    extra: { label: "Cancelar también mis órdenes manuales" },
  });
  if (!result.ok) return;
  try {
    const res = await api("/api/bot/kill", { method: "POST", body: { everything: Boolean(result.extra) } });
    const what = res.cancelled === "all" ? "todas las órdenes" : `${res.cancelled} órdenes`;
    toast(`Bot frenado · canceladas ${what}`, "success");
    await refreshStatus();
    refreshPortfolio();
    refreshLogs();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function refreshLogs() {
  const box = $("#logs");
  try {
    const lines = await api(`/api/logs?after=${state.logsAfter}`);
    if (!lines.length) {
      if (!box.childElementCount) box.append(el("div", { class: "empty", text: "Sin actividad todavía." }));
      return;
    }
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
    const placeholder = box.querySelector(".empty");
    if (placeholder) placeholder.remove();
    for (const line of lines) {
      const node = logLine(line);
      const last = box.lastElementChild;
      if (last && last.dataset.key === node.dataset.key) {
        // La misma línea otra vez (p. ej. varios reintentos): se cuenta en vez de repetirla.
        const times = Number(last.dataset.count || 1) + 1;
        last.dataset.count = String(times);
        last.querySelector(".t").textContent = node.querySelector(".t").textContent;
        let badge = last.querySelector(".times");
        if (!badge) {
          badge = el("span", { class: "times" });
          const lm = last.querySelector(".lm");
          lm.insertBefore(badge, lm.querySelector(".lx"));
        }
        badge.textContent = `×${times}`;
      } else {
        box.append(node);
      }
      state.logsAfter = line.id;
    }
    while (box.childElementCount > 300) box.firstElementChild.remove();
    if (atBottom) box.scrollTop = box.scrollHeight;
  } catch {
    /* se reintenta en la siguiente vuelta */
  }
}

function logLine(line) {
  const { kind, main, extra } = describeLog(line.message);
  const lm = el("div", { class: "lm" }, main, extra && extra.length ? el("span", { class: "lx" }, extra) : null);
  const node = el(
    "div",
    { class: `log-line ${kind} ${line.level}` },
    el("span", { class: "ld", "aria-hidden": "true" }),
    lm,
    el("span", { class: "t", title: fmt.time(line.ts), text: fmt.clock(line.ts) }),
  );
  node.dataset.key = `${line.level}|${lm.textContent}`;
  return node;
}

// Lo que hace una orden de la API (lado del libro y precio del SÍ) dicho como compra de SÍ o de NO.
function orderWords(book, count, yesPrice) {
  const yes = book === "bid";
  const price = yes ? Number(yesPrice) : 1 - Number(yesPrice);
  return `${fmt.qty(count)} ${yes ? "SÍ" : "NO"} a ${fmt.cents(price)}`;
}

const EXIT_REASON = /^(cortar pérdidas|cobrar antes)/;

function riskReason(text) {
  const exposure = /^exposición total máxima \$([\d.]+)/.exec(text);
  if (exposure) return `llegaría al máximo comprometido (${fmt.money(exposure[1])})`;
  if (text.startsWith("precio fuera")) return "precio fuera de los límites de Riesgo";
  if (text.startsWith("límite de posición")) return "límite de contratos por mercado";
  return text;
}
const DESCRIBE = /^(COMPRA|VENDE) YES ([\d.]+) @ ([\d.]+)(?: \(= COMPRA NO @ [\d.]+\))? \[([A-Z]+)( post-only)?\] (\S+)$/;

// Convierte las líneas del log del bot en frases: qué compró, qué canceló, qué falló.
function describeLog(message) {
  let m = /^ORDEN (.+?) \| id=\S* llenado=(\S*) pendiente=(\S*) \| ?(.*)$/.exec(message);
  let d = m && DESCRIBE.exec(m[1]);
  if (d) {
    const book = d[1] === "COMPRA" ? "bid" : "ask";
    const filled = Number(m[2]) || 0;
    const pending = Number(m[3]) || 0;
    const leaving = EXIT_REASON.test(m[4]);
    // Al salir, comprar SÍ cierra un NO y vender SÍ cierra un SÍ.
    const what = leaving ? `vender ${orderWords(book === "bid" ? "ask" : "bid", d[2], d[3])}` : `comprar ${orderWords(book, d[2], d[3])}`;
    const status = filled > 0 && pending === 0 ? "llenada al momento" : filled > 0 ? "llenada en parte" : "esperando en el libro";
    return {
      kind: "order",
      main: [el("b", { text: "Orden" }), ` · ${what}`],
      extra: [tickerNode(d[6]), ` · ${leaving ? m[4] : status}`],
    };
  }
  m = /^\[SIMULACIÓN\] cancelar (\S+) (bid|ask) ([\d.]+) @ ([\d.]+)/.exec(message);
  if (m) return { kind: "cancel", main: [el("b", { text: "Simulación" }), ` · retiraría ${orderWords(m[2], m[3], m[4])}`], extra: [tickerNode(m[1])] };
  m = /^\[SIMULACIÓN\] (.+?) \| ?(.*)$/.exec(message);
  d = m && DESCRIBE.exec(m[1]);
  if (d) {
    const book = d[1] === "COMPRA" ? "bid" : "ask";
    return { kind: "order sim", main: [el("b", { text: "Simulación" }), ` · compraría ${orderWords(book, d[2], d[3])}`], extra: [tickerNode(d[6])] };
  }
  m = /^CANCELADA (\S+) (bid|ask) ([\d.]+) @ ([\d.]+)/.exec(message);
  if (m) return { kind: "cancel", main: [el("b", { text: "Cancelada" }), ` · ${orderWords(m[2], m[3], m[4])}`], extra: [tickerNode(m[1])] };
  m = /^LLENADO( \(fuera del bot\))? (COMPRA|VENDE) YES ([\d.]+) @ ([\d.]+) (\S+) \((maker|taker), comisión \$([\d.]+|\?)\)/.exec(message);
  if (m) {
    const fee = m[7] === "?" ? "" : ` · comisión ${fmt.money(m[7])}`;
    return {
      kind: "fill",
      main: [el("b", { text: m[1] ? "Comprado a mano" : "Comprado" }), ` · ${orderWords(m[2] === "COMPRA" ? "bid" : "ask", m[3], m[4])}`],
      extra: [tickerNode(m[5]), fee],
    };
  }
  m = /^Arrancando bot \| entorno=(\S+) \| modo=(.+?) \| estrategia=(\S+)/.exec(message);
  if (m) {
    const how = m[2].startsWith("EN VIVO") ? (m[1] === "prod" ? "con dinero real" : "en demo") : "en simulación";
    return { kind: "start", main: [el("b", { text: "Bot en marcha" }), ` · ${how}`], extra: [`Estrategia ${strategyLabel(m[3])}`] };
  }
  m = /^HTTP (\d{3}) en \S+ \S+; reintento \d+/.exec(message);
  if (m) {
    const slow = m[1] === "429";
    return {
      kind: "info",
      main: [el("b", { text: slow ? "Kalshi pidió ir más despacio" : `Kalshi respondió con un error (${m[1]})` })],
      extra: ["se reintentó solo"],
    };
  }
  m = /^Kalshi pide ir más despacio \(HTTP 429\): el bot baja a ([\d.]+) peticiones por segundo/.exec(message);
  if (m) {
    const rate = Number(m[1]).toLocaleString("es-ES", { maximumFractionDigits: 1 });
    return {
      kind: "info",
      main: [el("b", { text: "Kalshi pide ir más despacio" })],
      extra: [`el bot baja a ${rate} peticiones por segundo y vuelve a subir solo`],
    };
  }
  m = /^Bot arrancado desde el panel en modo (.+)$/.exec(message);
  if (m) return { kind: "start", main: [el("b", { text: "Activado desde el panel" })], extra: [m[1] === "EN VIVO" ? "operando" : "en simulación"] };
  m = /^Bot detenido(?:: (.*))?$/.exec(message);
  if (m) return { kind: "info", main: [el("b", { text: "Bot en pausa" })], extra: m[1] ? [m[1]] : [] };
  m = /^Exchange: activo=(\S+), trading=(\S+)/.exec(message);
  if (m) return { kind: "info", main: [m[2] === "sí" ? "Kalshi está abierto" : "Kalshi no está operando ahora: el bot espera"], extra: [] };
  m = /^Saldo disponible \$([\d.]+) \| valor del portafolio \$([\d.]+)/.exec(message);
  if (m) return { kind: "info", main: [`Saldo ${fmt.money(m[1])}`], extra: [`en posiciones ${fmt.money(m[2])}`] };
  m = /^Mercados seguidos \((\d+)\): (.*)$/.exec(message);
  if (m) return { kind: "info", main: [`Siguiendo ${plural(Number(m[1]), "mercado", "mercados")}`], extra: [] };
  m = /^\[(\S+)\] riesgo: (rechazada \((.+?)\)|recortada de [\d.]+ a ([\d.]+) contratos): (.+)$/.exec(message);
  d = m && DESCRIBE.exec(m[5]);
  if (d) {
    const book = d[1] === "COMPRA" ? "bid" : "ask";
    const why = m[3] ? riskReason(m[3]) : "límite de contratos por orden";
    const main = m[4]
      ? [el("b", { text: "Recortada" }), ` · comprar ${orderWords(book, m[4], d[3])} (pedía ${fmt.qty(d[2])})`]
      : [el("b", { text: "No se compra" }), ` · ${orderWords(book, d[2], d[3])}`];
    return { kind: "risk", main, extra: [tickerNode(m[1]), ` · ${why}`] };
  }
  m = /^\[([A-Z0-9][A-Z0-9._-]+)\] (.*)$/.exec(message);
  if (m) return { kind: "info", main: [cap(m[2])], extra: [tickerNode(m[1])] };
  return { kind: "info", main: [message], extra: [] };
}

function empty(text) {
  return el("div", { class: "empty", text });
}

async function refreshPortfolio() {
  const positions = $("#positions");
  const orders = $("#orders");
  const s = state.status;
  if (!s || !s.credentials.configured) {
    positions.replaceChildren(empty("Configura tu API key en Ajustes → Avanzado para ver tus apuestas."));
    orders.replaceChildren(empty("—"));
    return;
  }
  try {
    const [pos, ord] = await Promise.all([api("/api/positions"), api("/api/orders")]);
    positions.replaceChildren(...(pos.length ? pos.map(positionItem) : [empty("No hay apuestas abiertas ahora mismo.")]));
    orders.replaceChildren(...(ord.length ? ord.map(orderItem) : [empty("Ninguna orden esperando en el libro.")]));
    renameTickers();
  } catch (err) {
    positions.replaceChildren(empty(err.message));
  }
}

function sideBadge(outcome, text) {
  return el("span", { class: `badge ${outcome}`, text: text || (outcome === "yes" ? "SÍ" : "NO") });
}

// "5 contratos · 93¢": cuántos y a qué precio medio.
function sizeText(contracts, cost) {
  const n = Number(contracts);
  const parts = [`${fmt.qty(contracts)} ${n === 1 ? "contrato" : "contratos"}`];
  if (n > 0 && cost != null) parts.push(fmt.cents(Number(cost) / n));
  return parts.join(" · ");
}

function positionItem(p) {
  const label = labelFor(p.ticker, p.title, p.subtitle);
  const chance = p.chance == null ? null : Number(p.chance);
  let when = "";
  if (p.hours_to_close != null) when = p.hours_to_close <= 0 ? "esperando" : `en ${fmt.hours(p.hours_to_close)}`;
  const sub = [sizeText(p.contracts, p.exposure), `cobra ${fmt.money(p.payout ?? p.contracts)}`];
  return el(
    "button",
    { class: "trade", type: "button", onclick: () => openMarket(p.ticker) },
    chip(p.side),
    el(
      "span",
      { class: "trade-main" },
      el("span", { class: "trade-title", text: label.short }),
      el("span", { class: "trade-sub", text: sub.filter(Boolean).join(" · ") }),
    ),
    el(
      "span",
      { class: "trade-side" },
      el("span", { class: "trade-value", text: chance == null ? "—" : fmt.share(chance) }),
      el("span", { class: "trade-tag", text: when }),
    ),
  );
}

function orderItem(o) {
  const cancel = el("button", {
    class: "btn small",
    type: "button",
    text: "Cancelar",
    onclick: async () => {
      cancel.disabled = true;
      try {
        await api("/api/orders/cancel", { method: "POST", body: { order_id: o.order_id, ticker: o.ticker } });
        toast("Orden cancelada");
        refreshPortfolio();
      } catch (err) {
        toast(err.message, "error");
        cancel.disabled = false;
      }
    },
  });
  const label = labelFor(o.ticker, o.title, o.subtitle);
  const sub = [`Compra ${fmt.qty(o.remaining)} a ${fmt.cents(o.price)}`, o.source === "bot" ? "bot" : "a mano"];
  return el(
    "div",
    { class: "trade" },
    chip(o.outcome),
    el(
      "span",
      { class: "trade-main" },
      el("span", { class: "trade-title", text: label.short }),
      el("span", { class: "trade-sub", text: sub.filter(Boolean).join(" · ") }),
    ),
    cancel,
  );
}

/* ===================== mercados ===================== */

async function loadEvents() {
  const box = $("#event-list");
  $("#event-card").hidden = false;
  box.replaceChildren(empty("Cargando eventos abiertos…"));
  try {
    const events = await api("/api/events");
    box.replaceChildren(
      el("h2", { class: "small muted", text: "Eventos abiertos" }),
      ...(events.length
        ? events.map((e) =>
            el(
              "div",
              {
                class: "item tappable",
                onclick: () => {
                  $("#market-query").value = e.event_ticker;
                  searchMarkets();
                },
              },
              el(
                "div",
                { class: "item-main" },
                el("div", { class: "item-title", text: e.title || e.event_ticker }),
                el("div", { class: "item-sub", text: [e.event_ticker, e.sub_title].filter(Boolean).join(" · ") }),
              ),
            ),
          )
        : [empty("No hay eventos abiertos.")]),
    );
  } catch (err) {
    box.replaceChildren(empty(err.message));
  }
}

async function searchMarkets(event) {
  if (event) event.preventDefault();
  const query = $("#market-query").value.trim().toUpperCase();
  const list = $("#market-list");
  if (!query) {
    list.replaceChildren();
    $("#market-card").hidden = true;
    loadEvents();
    return;
  }
  $("#event-list").replaceChildren();
  $("#event-card").hidden = true;
  $("#market-card").hidden = false;
  list.replaceChildren(empty("Buscando…"));
  const param = query.includes("-") ? `event=${encodeURIComponent(query)}` : `series=${encodeURIComponent(query)}`;
  try {
    const markets = await api(`/api/markets?${param}`);
    list.replaceChildren(...(markets.length ? markets.map(marketItem) : [empty("No hay mercados abiertos con ese filtro.")]));
  } catch (err) {
    list.replaceChildren(empty(err.message));
  }
}

function marketItem(m) {
  const label = labelFor(m.ticker, m.title, m.subtitle);
  const sub = [label.outcome ? label.title : "", label.date, `cierra en ${fmt.hours(m.hours_to_close)}`, `vol ${fmt.qty(m.volume_24h)}`];
  return el(
    "div",
    { class: "item tappable", onclick: () => openMarket(m.ticker) },
    el(
      "div",
      { class: "item-main" },
      el("div", { class: "item-title", text: cap(label.outcome || label.title) }),
      el("div", { class: "item-sub", text: sub.filter(Boolean).join(" · ") }),
    ),
    el(
      "div",
      { class: "item-side" },
      el("div", { class: "big", text: `${fmt.cents(m.yes_bid)} / ${fmt.cents(m.yes_ask)}` }),
      el("div", { class: "small muted", text: "compra / venta SÍ" }),
    ),
  );
}

async function followMarket(ticker) {
  try {
    await api("/api/markets/follow", { method: "POST", body: { ticker } });
    toast("Añadido a los mercados del bot", "success");
  } catch (err) {
    toast(err.message, "error");
  }
}

async function openMarket(ticker) {
  openSheet(empty("Cargando mercado…"));
  let data;
  try {
    data = await api(`/api/market?ticker=${encodeURIComponent(ticker)}`);
  } catch (err) {
    openSheet(el("h2", { text: ticker }), el("p", { class: "error-text", text: err.message }));
    return;
  }
  const m = data.market;
  const book = data.book;
  const bestBid = book.best_bid == null ? null : Number(book.best_bid);
  const bestAsk = book.best_ask == null ? null : Number(book.best_ask);
  const ticket = { outcome: "yes", immediate: false };

  const bookRows = (levels, kind) =>
    levels.length
      ? levels.slice(0, 6).map((l) => el("div", { class: `book-row ${kind}` }, el("span", { class: "p", text: fmt.cents(l.price) }), el("span", { text: fmt.qty(l.size) })))
      : [empty("vacío")];

  const priceInput = el("input", { type: "number", inputmode: "decimal", min: "1", max: "99", step: "0.1" });
  const countInput = el("input", { type: "number", inputmode: "numeric", min: "1", step: "1", value: "10" });
  const immediate = el("input", { type: "checkbox" });
  const summary = el("div", { class: "summary" });
  const seg = el("div", { class: "segmented yesno" });
  const segButtons = ["yes", "no"].map((outcome) =>
    el("button", {
      type: "button",
      "data-outcome": outcome,
      text: outcome === "yes" ? "Comprar SÍ" : "Comprar NO",
      onclick: () => {
        ticket.outcome = outcome;
        setDefaultPrice();
        refresh();
      },
    }),
  );
  seg.append(...segButtons);

  // Precio del lado elegido al que se cruzaría el spread (comprar ya).
  const crossPrice = () => (ticket.outcome === "yes" ? bestAsk : bestBid == null ? null : 1 - bestBid);
  const restPrice = () => (ticket.outcome === "yes" ? bestBid : bestAsk == null ? null : 1 - bestAsk);

  function setDefaultPrice() {
    const p = restPrice() ?? crossPrice();
    priceInput.value = p == null ? "" : String(Math.round(p * 1000) / 10);
  }

  function refresh() {
    for (const b of segButtons) b.classList.toggle("active", b.dataset.outcome === ticket.outcome);
    ticket.immediate = immediate.checked;
    const price = Number(priceInput.value) / 100;
    const count = Math.floor(Number(countInput.value));
    if (!(price > 0 && price < 1) || !(count >= 1)) {
      summary.replaceChildren(empty("Indica precio (1–99¢) y cantidad."));
      return;
    }
    const cross = crossPrice();
    const crosses = ticket.immediate || (cross != null && price >= cross - 1e-9);
    const fee = feeEstimate(crosses ? Number(data.fees.taker_rate) : Number(data.fees.maker_rate), count, price);
    summary.replaceChildren(
      el("div", {}, el("span", { text: "Pagas" }), el("b", { text: fmt.money(price * count) })),
      el("div", {}, el("span", { text: "Si aciertas cobras" }), el("b", { text: fmt.money(count) })),
      el("div", {}, el("span", { text: "Ganancia máxima" }), el("b", { class: "pos", text: fmt.money((1 - price) * count - fee) })),
      el("div", {}, el("span", { text: crosses ? "Comisión (taker) aprox." : "Comisión (maker) aprox." }), el("span", { text: fmt.money(fee) })),
      el("div", { class: "muted" }, el("span", { text: crosses ? "Se ejecuta ya contra el libro" : "Espera en el libro hasta que alguien la tome" })),
    );
  }

  priceInput.addEventListener("input", refresh);
  countInput.addEventListener("input", refresh);
  immediate.addEventListener("change", refresh);
  setDefaultPrice();

  const configured = state.status && state.status.credentials.configured;
  const send = el("button", { class: "btn primary full", type: "button", text: "Enviar orden", disabled: !configured });
  send.addEventListener("click", async () => {
    const price = Number(priceInput.value) / 100;
    const count = Math.floor(Number(countInput.value));
    const body = { ticker: m.ticker, outcome: ticket.outcome, price: price.toFixed(4), count, immediate: immediate.checked };
    if (state.status.is_production) {
      const ok = await confirmDialog({
        title: "Confirmar orden real",
        message: `Comprar ${count} ${ticket.outcome === "yes" ? "SÍ" : "NO"} a ${fmt.cents(price)} en ${m.ticker} (pagas ${fmt.money(price * count)}).`,
        confirmLabel: "Enviar",
        danger: true,
      });
      if (!ok.ok) return;
      body.confirm = true;
    }
    send.disabled = true;
    try {
      const res = await api("/api/orders", { method: "POST", body });
      toast(`Orden enviada a ${fmt.cents(res.price)}`, "success");
      closeSheet();
      refreshPortfolio();
    } catch (err) {
      toast(err.message, "error");
      send.disabled = false;
    }
  });

  const label = labelFor(m.ticker, m.title, m.subtitle);
  openSheet(
    el("h2", { text: labelText(label) }),
    el("p", { class: "muted small", text: [label.date, `cierra en ${fmt.hours(m.hours_to_close)}`, m.ticker].filter(Boolean).join(" · ") }),
    el(
      "div",
      { class: "book" },
      el("div", {}, el("h3", { text: "COMPRAN SÍ" }), ...bookRows(book.bids, "bid")),
      el("div", {}, el("h3", { text: "VENDEN SÍ" }), ...bookRows(book.asks, "ask")),
    ),
    el("p", { class: "muted small", text: "Comprar NO a X¢ equivale a vender SÍ a (100 − X)¢." }),
    seg,
    el(
      "div",
      { class: "grid2" },
      el("label", { class: "field" }, "Precio límite", el("div", { class: "input-unit" }, priceInput, el("span", { text: "¢" }))),
      el("label", { class: "field" }, "Contratos", countInput),
    ),
    el("label", { class: "toggle" }, el("span", { text: "Ejecutar ya (si no, espera en el libro)" }), immediate),
    summary,
    configured ? null : el("p", { class: "muted small", text: "Configura tu API key en Ajustes para poder operar." }),
    send,
    el("button", { class: "btn full", type: "button", text: "Seguir este mercado con el bot", onclick: () => followMarket(m.ticker) }),
    m.rules ? el("details", {}, el("summary", { text: "Reglas del mercado" }), el("div", { class: "rules", text: m.rules })) : null,
  );
  refresh();
}

/* ===================== oportunidades ===================== */

function pollJob(path, { onProgress, onDone, onFinally }) {
  const tick = async () => {
    try {
      const job = await api(path);
      if (job.state === "running") {
        if (onProgress) onProgress(job);
        setTimeout(tick, 1200);
        return;
      }
      if (job.state === "error") toast(job.error || "El trabajo falló", "error");
      else if (job.state === "done") onDone(job.result);
    } catch (err) {
      toast(err.message, "error");
    }
    if (onFinally) onFinally();
  };
  setTimeout(tick, 500);
}

async function runScan() {
  const button = $("#btn-scan");
  try {
    await api("/api/scan", { method: "POST", body: { hours: $("#scan-hours").value, min_volume: $("#scan-volume").value } });
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  button.disabled = true;
  $("#scan-status").textContent = "Escaneando mercados…";
  pollJob("/api/scan", { onDone: renderScan, onFinally: () => (button.disabled = false) });
}

function resultSection(title, help, items) {
  return el(
    "div",
    { class: "card" },
    el("h2", { text: `${title} (${items.length})` }),
    el("p", { class: "muted small", text: help }),
    el("div", { class: "list" }, ...(items.length ? items : [empty("Ninguno ahora mismo.")])),
  );
}

function renderScan(r) {
  $("#scan-status").textContent = `${r.markets_scanned} mercados revisados · ${fmt.time(r.generated_at)}`;
  const favorites = r.favorites.map((f) => {
    const label = labelFor(f.ticker, f.title, f.subtitle);
    const sub = [label.outcome ? label.title : "", label.date, `cierra en ${fmt.hours(f.hours_to_close)}`, `vol ${fmt.qty(f.volume_24h)}`];
    return el(
      "div",
      { class: "item tappable", onclick: () => openMarket(f.ticker) },
      el(
        "div",
        { class: "item-main" },
        el("div", { class: "item-title", text: cap(label.outcome || label.title) }),
        el("div", { class: "item-sub", text: sub.filter(Boolean).join(" · ") }),
      ),
      el("div", { class: "item-side" }, sideBadge(f.side, `${f.side === "yes" ? "SÍ" : "NO"} ${fmt.cents(f.bid)}`), el("div", { class: "small muted", text: `gana ${fmt.cents(f.max_profit)}` })),
    );
  });
  const arbitrage = r.arbitrage.map((a) =>
    el(
      "div",
      { class: "item" },
      el(
        "div",
        { class: "item-main" },
        el("div", { class: "item-title", text: a.title || a.event_ticker }),
        el("div", { class: "item-sub", text: `${a.description} · ${a.legs} resultados${a.warning ? " · ⚠ " + a.warning : ""}` }),
      ),
      el("div", { class: "item-side" }, el("div", { class: "big pos", text: "+" + fmt.cents(a.profit) }), el("div", { class: "small muted", text: "por juego" })),
    ),
  );
  const spreads = r.spreads.map((s) =>
    el(
      "div",
      { class: "item tappable", onclick: () => openMarket(s.ticker) },
      el(
        "div",
        { class: "item-main" },
        el("div", { class: "item-title", text: s.subtitle || s.title || s.ticker }),
        el("div", { class: "item-sub", text: `${s.ticker} · vol ${fmt.qty(s.volume_24h)}` }),
      ),
      el("div", { class: "item-side" }, el("div", { class: "big", text: `${fmt.cents(s.bid)} / ${fmt.cents(s.ask)}` }), el("div", { class: "small muted", text: `spread ${fmt.cents(s.spread)}` })),
    ),
  );
  $("#scan-results").replaceChildren(
    resultSection("Favoritos", "Un lado cotiza entre 88 y 97¢ con spread estrecho: candidatos para la estrategia Favoritos.", favorites),
    resultSection("Arbitraje en eventos", "Comprar en todos los resultados deja beneficio tras comisiones. Confírmalo en el libro: los precios cambian rápido.", arbitrage),
    resultSection("Spreads amplios", "Mucho hueco entre compra y venta: candidatos para el creador de mercado.", spreads),
  );
}

async function runResearch() {
  const button = $("#btn-research");
  const body = { series: $("#research-series").value.trim().toUpperCase(), markets: $("#research-markets").value };
  try {
    await api("/api/research", { method: "POST", body });
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  button.disabled = true;
  const progress = $("#research-progress");
  const bar = progress.querySelector(".progress-bar");
  progress.hidden = false;
  bar.style.width = "3%";
  $("#research-status").textContent = "Descargando mercados liquidados…";
  pollJob("/api/research", {
    onProgress: (job) => {
      if (job.total) {
        bar.style.width = `${Math.max(3, Math.round((job.done / job.total) * 100))}%`;
        $("#research-status").textContent = `Analizando ${job.done} de ${job.total} mercados…`;
      }
    },
    onDone: renderResearch,
    onFinally: () => {
      button.disabled = false;
      progress.hidden = true;
    },
  });
}

async function runSweep() {
  const button = $("#btn-sweep");
  try {
    await api("/api/sweep", { method: "POST", body: {} });
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  button.disabled = true;
  const progress = $("#sweep-progress");
  const bar = progress.querySelector(".progress-bar");
  progress.hidden = false;
  bar.style.width = "3%";
  $("#sweep-status").textContent = "Buscando las series con más volumen…";
  pollJob("/api/sweep", {
    onProgress: (job) => {
      if (job.total) {
        bar.style.width = `${Math.max(3, Math.round((job.done / job.total) * 100))}%`;
        $("#sweep-status").textContent = `Analizando ${job.done} de ${job.total} mercados liquidados…`;
      }
    },
    onDone: renderSweep,
    onFinally: () => {
      button.disabled = false;
      progress.hidden = true;
    },
  });
}

const VERDICTS = {
  gana: "gana",
  pierde: "pierde",
  dudoso: "sin confirmar",
  "sin datos": "sin datos",
};

function bandText(stats) {
  if (!stats || !stats.contracts || Number(stats.contracts) === 0) return "sin compras en la banda del bot";
  const parts = [`${stats.losing_groups} de ${stats.groups} eventos con pérdida`];
  if (stats.ci_low != null) parts.push(`margen ${fmt.pct(stats.ci_low)} a ${fmt.pct(stats.ci_high)}`);
  return parts.join(" · ");
}

async function useSeries(row) {
  const sure = row.verdict === "gana";
  const ok = await confirmDialog({
    title: `Centrar el bot en ${row.series}`,
    message:
      "El bot dejará de buscar en todos los mercados y operará solo esta serie. Puedes cambiarlo en Ajustes → Mercados." +
      (sure ? "" : " Ojo: con los datos que hay no se puede asegurar que gane; simula antes de operar."),
    confirmLabel: "Usar esta serie",
  });
  if (!ok.ok) return;
  try {
    await api("/api/markets/use-series", { method: "POST", body: { series: [row.series] } });
    toast(state.status && state.status.bot.state === "running" ? "Guardado. Reinicia el bot para aplicarlo." : "Serie guardada", "success");
  } catch (err) {
    toast(err.message, "error");
  }
}

function renderSweep(r) {
  $("#sweep-status").textContent = `${r.series_analyzed} series · ${r.markets} mercados liquidados · ${fmt.time(r.generated_at)}`;
  const items = r.rows.map((row) => {
    const band = row.strategy;
    const has = band && band.contracts && Number(band.contracts) > 0;
    const sub = [row.category, `${row.markets} mercados`].filter(Boolean).join(" · ");
    const use = has && Number(band.return_after_fees) > 0
      ? el("button", { class: "btn small", type: "button", text: "Usar", onclick: () => useSeries(row) })
      : null;
    return el(
      "div",
      { class: "item" },
      el(
        "div",
        { class: "item-main" },
        el("div", { class: "item-title", text: `${row.series}${row.title ? " · " + row.title : ""}` }),
        el("div", { class: "item-sub", text: sub }),
        el("div", { class: "item-sub wrap", text: bandText(band) }),
      ),
      el(
        "div",
        { class: "item-side" },
        el("div", { class: "big " + (has ? signClass(band.return_after_fees) : ""), text: has ? fmt.pct(band.return_after_fees, 2) : "—" }),
        el("div", { class: "small muted", text: VERDICTS[row.verdict] || "" }),
      ),
      use,
    );
  });
  const overall = r.overall;
  const head = overall && overall.contracts && Number(overall.contracts) > 0
    ? el(
        "p",
        { class: "small" },
        el("b", { text: `Todas juntas: ${fmt.pct(overall.return_after_fees, 2)} (${VERDICTS[r.overall_verdict] || "sin datos"})` }),
        ` · ${bandText(overall)}`,
      )
    : null;
  $("#sweep-results").replaceChildren(
    head,
    el("div", { class: "list" }, ...(items.length ? items : [empty("No hubo suficientes datos para comparar.")])),
    el("ul", { class: "conclusions" }, ...r.conclusions.map((c) => el("li", { text: c }))),
  );
}

function returnCell(stats) {
  if (!stats || !stats.contracts || Number(stats.contracts) === 0) return el("td", { text: "—" });
  return el("td", { class: signClass(stats.return_after_fees), text: fmt.pct(stats.return_after_fees) });
}

function renderResearch(r) {
  $("#research-status").textContent = `${r.series} · ${r.markets} mercados · ${fmt.qty(r.trades)} operaciones`;
  const rows = r.buckets.map((b) =>
    el(
      "tr",
      {},
      el("td", { text: b.range }),
      returnCell(b.taker),
      returnCell(b.maker),
      el("td", { text: b.all && b.all.contracts && Number(b.all.contracts) ? `${Math.round(Number(b.all.win_rate) * 100)}% vs ${fmt.cents(b.all.avg_price)}` : "—" }),
    ),
  );
  $("#research-results").replaceChildren(
    el(
      "div",
      { class: "table-wrap" },
      el(
        "table",
        {},
        el("thead", {}, el("tr", {}, el("th", { text: "Precio" }), el("th", { text: "Taker" }), el("th", { text: "Maker" }), el("th", { text: "Acierto vs precio" }))),
        el("tbody", {}, ...rows),
      ),
    ),
    el("p", { class: "muted small", text: "Rendimiento tras comisiones de quien compró a ese precio. Si acierta más veces que el precio que pagó, gana." }),
    el("ul", { class: "conclusions" }, ...r.conclusions.map((c) => el("li", { text: c }))),
  );
}

/* ===================== ajustes ===================== */

const MARKET_FIELDS = [
  { key: "series", label: "Series", type: "list", help: "Por defecto, partidos (KXMLBGAME, KXNFLGAME…) y temperatura máxima en 7 ciudades (KXHIGHNY, KXHIGHMIA…): donde los datos dieron ventaja. Si tu estado bloquea los deportes, quita los partidos." },
  { key: "max_hours_to_close", label: "Solo los que terminan en las próximas (horas)", type: "number", help: "0 = sin límite. En los partidos cuenta el final previsto, no el cierre oficial." },
  { key: "max_markets", label: "Mercados máximos a la vez", type: "number" },
  { key: "max_markets_per_event", label: "Mercados que mirar por evento", type: "number", help: "Cuántos mercados de un mismo partido o día seguir. En el clima se miran hasta 4 tramos (regla propia), porque el favorito no suele ser el más negociado. Dónde apostar lo limita Riesgo → Apuestas por evento." },
  { key: "min_volume_24h", label: "Volumen mínimo en 24 h (contratos)", type: "number" },
  { key: "min_hours_to_close", label: "Ignorar si termina en menos de (horas)", type: "number" },
  { key: "closing_within_hours", label: "Buscar además en todos los mercados que terminan en (horas)", type: "number", help: "0 = no buscar; usa solo series, eventos o mercados fijos." },
  { key: "tickers", label: "Mercados fijos", type: "list", help: "Tickers separados por comas." },
  { key: "exclude_series", label: "Series a evitar", type: "list" },
];

const RISK_FIELDS = [
  { key: "max_order_contracts", label: "Contratos por orden (máximo)", type: "number" },
  { key: "max_position_per_market", label: "Contratos por mercado (máximo)", type: "number" },
  { key: "max_total_exposure_dollars", label: "Dinero comprometido máximo", type: "money" },
  { key: "max_session_loss_dollars", label: "Pérdida máxima de la sesión", type: "money", help: "Al alcanzarla, cancela todo y se detiene. Cuenta también lo que compres tú a mano. 0 = desactivado." },
  { key: "max_positions_per_event", label: "Apuestas por evento (máximo)", type: "number", help: "Mercados con dinero a la vez en un mismo partido o día de clima: los tramos vecinos son casi la misma apuesta. Cuenta también lo tuyo. 0 = sin límite." },
  { key: "min_price", label: "Precio mínimo", type: "price" },
  { key: "max_price", label: "Precio máximo", type: "price" },
  { key: "min_minutes_to_close", label: "No operar en los últimos (minutos)", type: "number" },
];

function fieldControl(def, value) {
  let input;
  let read;
  if (def.type === "bool") {
    input = el("input", { type: "checkbox" });
    const v = value ?? def.default; // sin valor guardado manda el de la estrategia
    input.checked = v === true || v === "true";
    read = () => input.checked;
    return { node: el("label", { class: "toggle" }, el("span", { text: def.label }), input), read };
  }
  if (def.type === "select") {
    input = el(
      "select",
      {},
      ...(def.options || []).map((o) => (typeof o === "string" ? el("option", { value: o, text: o }) : el("option", { value: o.value, text: o.label }))),
    );
    input.value = value ?? def.default ?? "";
    read = () => input.value;
  } else if (def.type === "list") {
    input = el("input", { autocapitalize: "characters", autocomplete: "off", spellcheck: "false" });
    input.value = Array.isArray(value) ? value.join(", ") : value || "";
    read = () => input.value.split(",").map((v) => v.trim()).filter(Boolean);
  } else if (def.type === "price") {
    input = el("input", { type: "number", inputmode: "decimal", step: "0.1" });
    const v = value ?? def.default;
    input.value = v == null || v === "" ? "" : String(Math.round(Number(v) * 1000) / 10);
    read = () => (input.value === "" ? undefined : (Number(input.value) / 100).toFixed(4));
  } else {
    input = el("input", { type: "number", inputmode: "decimal", step: "any" });
    const v = value ?? def.default;
    input.value = v == null ? "" : String(v);
    read = () => (input.value === "" ? undefined : input.value);
  }
  const unit = def.type === "price" ? "¢" : def.type === "money" ? "$" : null;
  const control = unit ? el("div", { class: "input-unit" }, input, el("span", { text: unit })) : input;
  return {
    node: el("label", { class: "field" }, def.label, control, def.help ? el("span", { class: "help", text: def.help }) : null),
    read,
  };
}

function renderFields(containerSel, defs, values, section) {
  const box = $(containerSel);
  const readers = {};
  box.replaceChildren(
    ...defs.map((def) => {
      const control = fieldControl(def, values ? values[def.key] : undefined);
      readers[def.key] = control.read;
      return control.node;
    }),
  );
  state.readers[section] = readers;
}

function readSection(section) {
  const out = {};
  for (const [key, read] of Object.entries(state.readers[section] || {})) {
    const value = read();
    if (value !== undefined) out[key] = value;
  }
  return out;
}

async function loadSettings() {
  try {
    state.settings = await api("/api/settings");
  } catch (err) {
    toast(err.message, "error");
    return;
  }
  const p = state.settings;
  renderCredentials(p.credentials);
  state.strategy = p.values.strategy.name;
  renderStrategy();
  renderFields("#markets-fields", MARKET_FIELDS, p.values.markets, "markets");
  renderFields("#risk-fields", RISK_FIELDS, p.values.risk, "risk");
  renderQuickSettings();
  if (state.status) renderBotCard(state.status);
  setupPush();
}

function renderStrategy() {
  const p = state.settings;
  const seg = $("#strategy-seg");
  seg.replaceChildren(
    ...p.strategies.map((s) =>
      el("button", {
        type: "button",
        class: s.name === state.strategy ? "active" : "",
        text: s.label,
        onclick: () => {
          state.strategy = s.name;
          renderStrategy();
        },
      }),
    ),
  );
  const current = p.strategies.find((s) => s.name === state.strategy);
  $("#strategy-desc").textContent = current ? current.description : "Estrategia personalizada (edítala en config.toml).";
  const saved = p.values.strategy.name === state.strategy ? p.values.strategy.params : {};
  renderFields("#strategy-params", current ? current.params : [], saved, "strategy");
  $("#fv-card").hidden = state.strategy !== "fair_value";
  if (state.strategy === "fair_value") loadFairValues();
}

async function saveSettings() {
  const values = {
    markets: readSection("markets"),
    risk: readSection("risk"),
    strategy: { name: state.strategy, params: readSection("strategy") },
  };
  const button = $("#btn-save-settings");
  button.disabled = true;
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: { values } });
    renderQuickSettings();
    afterSave();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    button.disabled = false;
  }
}

/* ===================== ajustes rápidos (arriba del todo) ===================== */

function strategyDef() {
  const p = state.settings;
  return p && p.strategies.find((x) => x.name === p.values.strategy.name);
}

// Valor de un parámetro de la estrategia guardada o, si no se ha tocado, el de por defecto.
function paramValue(key) {
  const saved = state.settings.values.strategy.params || {};
  if (saved[key] != null && saved[key] !== "") return saved[key];
  const def = strategyDef();
  const param = def && def.params.find((x) => x.key === key);
  return param ? param.default : null;
}

const sizeKey = () => (state.settings.values.strategy.name === "market_maker" ? "quote_size" : "order_size");

function renderQuickSettings() {
  const p = state.settings;
  if (!p) return;
  const size = Math.round(Number(state.pendingSize ?? paramValue(sizeKey())) || 0);
  const maxOrder = Number(p.values.risk.max_order_contracts) || 0;
  $("#size-value").textContent = size ? String(size) : "—";
  $("#size-less").disabled = size <= 1;
  $("#size-more").disabled = maxOrder > 0 && size >= maxOrder;
  const def = strategyDef();
  const hasMaxPrice = Boolean(def && def.params.some((x) => x.key === "max_price"));
  $("#row-max-price").hidden = !hasMaxPrice;
  if (hasMaxPrice) $("#max-price-value").textContent = fmt.cents(paramValue("max_price"));
  const loss = Number(p.values.risk.max_session_loss_dollars);
  $("#max-loss-value").textContent = loss > 0 ? fmt.money(loss) : "sin límite";
  $("#limits-help").textContent =
    `Hasta ${maxOrder || "—"} contratos por operación (el tope está en Avanzado → Riesgo). ` +
    "La pérdida máxima cuenta desde que arranca el bot e incluye lo que compres a mano: al llegar, cancela sus órdenes y se para.";
}

let sizeTimer = null;
function changeSize(delta) {
  if (!state.settings) return;
  const current = Math.round(Number(state.pendingSize ?? paramValue(sizeKey())) || 0);
  const maxOrder = Number(state.settings.values.risk.max_order_contracts) || Infinity;
  const next = Math.min(maxOrder, Math.max(1, current + delta));
  if (next === current) return;
  state.pendingSize = next;
  renderQuickSettings();
  // Se guarda al dejar de tocar, no en cada pulsación.
  clearTimeout(sizeTimer);
  sizeTimer = setTimeout(async () => {
    await saveStrategyParam(sizeKey(), String(state.pendingSize));
    state.pendingSize = null;
    renderQuickSettings();
  }, 700);
}

function saveStrategyParam(key, value) {
  const strategy = state.settings.values.strategy;
  return saveQuick({ strategy: { name: strategy.name, params: { ...(strategy.params || {}), [key]: value } } });
}

async function saveQuick(values) {
  try {
    state.settings = await api("/api/settings", { method: "PUT", body: { values } });
  } catch (err) {
    toast(err.message, "error");
    return false;
  }
  // Avanzado enseña lo guardado (un cambio a medias allí se descarta).
  state.strategy = state.settings.values.strategy.name;
  renderStrategy();
  renderFields("#risk-fields", RISK_FIELDS, state.settings.values.risk, "risk");
  renderQuickSettings();
  afterSave();
  return true;
}

// Con el bot en marcha los ajustes se aplican al reiniciarlo: se ofrece hacerlo ya.
function afterSave() {
  const banner = $("#settings-banner");
  if (!state.settings.needs_restart) {
    banner.hidden = true;
    toast("Guardado", "success");
    return;
  }
  banner.className = "banner info";
  banner.hidden = false;
  banner.replaceChildren(
    el("div", { text: "Guardado. El bot lo aplicará cuando se reinicie." }),
    el("button", { class: "btn small", type: "button", text: "Reiniciar ahora", onclick: restartBot }),
  );
}

async function restartBot(event) {
  const button = event && event.currentTarget;
  if (button) button.disabled = true;
  try {
    await api("/api/bot/restart", { method: "POST" });
    $("#settings-banner").hidden = true;
    toast("Bot reiniciado con los ajustes nuevos", "success");
  } catch (err) {
    toast(err.message, "error");
    if (button) button.disabled = false;
  }
  refreshStatus().catch(() => {});
}

function editValue({ title, help, unit, value, onSave }) {
  const input = el("input", { type: "number", inputmode: "decimal", step: "any", value: value ?? "" });
  const save = el("button", { class: "btn primary full", type: "button", text: "Guardar" });
  save.addEventListener("click", async () => {
    save.disabled = true;
    if (await onSave(String(input.value).replace(",", "."))) closeSheet();
    else save.disabled = false;
  });
  openSheet(el("h2", { text: title }), el("p", { class: "card-note", text: help }), el("div", { class: "input-unit" }, input, el("span", { text: unit })), save);
  setTimeout(() => input.focus(), 60);
}

function editMaxPrice() {
  const current = Number(paramValue("max_price"));
  editValue({
    title: "Precio máximo de entrada",
    help: "El bot no compra un favorito por encima de este precio: cerca de 100¢ la ganancia es mínima y una pérdida borra muchos aciertos.",
    unit: "¢",
    value: Number.isFinite(current) ? String(Math.round(current * 1000) / 10) : "",
    onSave: async (raw) => {
      const cents = Number(raw);
      if (!(cents > 50 && cents < 100)) {
        toast("Pon un precio entre 51 y 99¢", "error");
        return false;
      }
      return saveStrategyParam("max_price", (cents / 100).toFixed(4));
    },
  });
}

function editMaxLoss() {
  const current = Number(state.settings.values.risk.max_session_loss_dollars);
  editValue({
    title: "Pérdida máxima",
    help: "Si desde que arranca el bot se pierde esta cantidad (contando lo que compres a mano), cancela sus órdenes y se para. 0 = sin límite.",
    unit: "$",
    value: Number.isFinite(current) ? String(current) : "",
    onSave: async (raw) => {
      const dollars = Number(raw);
      if (raw === "" || !(dollars >= 0)) {
        toast("Pon una cantidad en dólares", "error");
        return false;
      }
      return saveQuick({ risk: { max_session_loss_dollars: String(dollars) } });
    },
  });
}

/* ===================== avisos al móvil ===================== */

const pushState = { reg: null, key: null, sub: null, prefs: { fills: false, settlements: false }, busy: false, ready: false };

const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;

async function setupPush() {
  const help = $("#push-help");
  if (!pushSupported()) {
    for (const toggle of $$("#push-fills, #push-settlements")) toggle.disabled = true;
    help.textContent = /iPhone|iPad|iPod/.test(navigator.userAgent)
      ? "En el iPhone los avisos solo funcionan con el panel instalado: Compartir → Añadir a pantalla de inicio, y ábrelo desde ese icono."
      : "Este navegador no permite avisos.";
    return;
  }
  try {
    pushState.reg = await navigator.serviceWorker.ready;
    if (!pushState.key) pushState.key = (await api("/api/push")).public_key;
    pushState.sub = await pushState.reg.pushManager.getSubscription();
    pushState.prefs = pushState.sub
      ? (await api("/api/push/state", { method: "POST", body: { endpoint: pushState.sub.endpoint } })).prefs
      : { fills: false, settlements: false };
    pushState.ready = true;
  } catch (err) {
    help.textContent = "No se pudieron leer los avisos: " + err.message;
    return;
  }
  renderPush();
}

function renderPush() {
  for (const toggle of $$("#push-fills, #push-settlements")) {
    toggle.disabled = !pushState.ready || pushState.busy;
    toggle.setAttribute("aria-checked", String(Boolean(pushState.prefs[toggle.dataset.kind])));
  }
  const on = pushState.prefs.fills || pushState.prefs.settlements;
  $("#push-test").hidden = !on;
  $("#push-help").textContent =
    Notification.permission === "denied"
      ? "Has bloqueado los avisos de esta app. Actívalos en los ajustes del móvil (Notificaciones) y vuelve aquí."
      : on
        ? "Llegan a este móvil aunque tengas el panel cerrado."
        : "Te avisa en este móvil cuando el bot compra y cuando se cierra un mercado, con lo ganado o perdido.";
}

async function togglePush(kind) {
  if (!pushState.ready || pushState.busy) return;
  const prefs = { ...pushState.prefs, [kind]: !pushState.prefs[kind] };
  const wanted = prefs.fills || prefs.settlements;
  pushState.busy = true;
  try {
    let sub = pushState.sub;
    // El iPhone solo pide permiso si es lo primero que pasa al tocar: suscribirse va antes que nada.
    if (!sub && wanted) {
      sub = await pushState.reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: base64UrlToBytes(pushState.key) });
    }
    if (sub) {
      const res = await api("/api/push/subscribe", { method: "POST", body: { subscription: sub.toJSON(), prefs, origin: location.origin } });
      pushState.prefs = res.prefs;
      if (!wanted) {
        await sub.unsubscribe().catch(() => {});
        sub = null;
      }
    }
    pushState.sub = sub;
    toast(wanted ? "Avisos guardados" : "Avisos desactivados", "success");
  } catch (err) {
    toast(
      Notification.permission === "denied" ? "Has bloqueado los avisos: actívalos en los ajustes del móvil" : "No se pudieron activar los avisos: " + err.message,
      "error",
    );
  } finally {
    pushState.busy = false;
    renderPush();
  }
}

async function testPush() {
  if (!pushState.sub) return;
  try {
    await api("/api/push/test", { method: "POST", body: { endpoint: pushState.sub.endpoint } });
    toast("Aviso de prueba enviado", "success");
  } catch (err) {
    toast(err.message, "error");
  }
}

function base64UrlToBytes(text) {
  const base64 = (text + "=".repeat((4 - (text.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
}

function renderCredentials(c) {
  const where = c.source === "env" ? "variables del servidor" : "guardada en el panel";
  $("#cred-status").textContent = c.configured
    ? `Conectada con la key ${c.key_id_hint}… (${where}).`
    : "Sin API key: el bot solo puede simular.";
  for (const button of $$("#env-seg button")) {
    button.classList.toggle("active", button.dataset.env === c.env);
    button.disabled = c.env_locked;
  }
  $("#cred-details").hidden = c.source === "env";
  $("#btn-del-cred").hidden = c.source !== "panel";
}

async function setEnv(env) {
  const c = state.settings && state.settings.credentials;
  if (!c || c.env === env) return Boolean(c);
  if (env === "prod") {
    const ok = await confirmDialog({
      title: "Cambiar a dinero real",
      message: "A partir de ahora el panel y el bot usarán tu cuenta real de Kalshi (necesitas una API key creada en kalshi.com). El bot sigue sin operar hasta que pulses Operar.",
      confirmLabel: "Cambiar a real",
      danger: true,
    });
    if (!ok.ok) return false;
  }
  try {
    await api("/api/env", { method: "POST", body: { env } });
    toast(env === "prod" ? "Entorno: REAL" : "Entorno: DEMO", "success");
    $("#diag-results").hidden = true;
    await loadSettings();
    refreshStatus().catch(() => {});
    return true;
  } catch (err) {
    toast(err.message, "error");
    return false;
  }
}

async function saveCredentials() {
  const c = state.settings.credentials;
  const body = { key_id: $("#cred-key-id").value.trim(), private_key: $("#cred-pem").value, env: c.env };
  if (!body.key_id || !body.private_key.trim()) {
    toast("Rellena el Key ID y la clave privada", "error");
    return;
  }
  try {
    await api("/api/credentials", { method: "POST", body });
    $("#cred-pem").value = "";
    $("#cred-key-id").value = "";
    $("#cred-details").open = false;
    toast("Credenciales guardadas", "success");
    await loadSettings();
    refreshStatus().catch(() => {});
    runDiagnosis(false);
  } catch (err) {
    toast(err.message, "error");
  }
}

async function loadKeyFile(event) {
  const input = event.target;
  const file = input.files && input.files[0];
  input.value = "";
  if (!file) return;
  if (file.size > 20000) {
    toast("Ese archivo es demasiado grande para ser una clave", "error");
    return;
  }
  const text = (await file.text()).trim();
  if (!/PRIVATE KEY/.test(text) && !/^[A-Za-z0-9+/=\s]{100,}$/.test(text)) {
    toast("Ese archivo no parece la clave privada de Kalshi", "error");
    return;
  }
  $("#cred-pem").value = text;
  toast(`Clave cargada desde «${file.name}»`, "success");
}

async function runDiagnosis(orderTest) {
  const c = state.settings && state.settings.credentials;
  let confirm = false;
  if (orderTest) {
    const ok = await confirmDialog({
      title: "Orden de prueba",
      message:
        c && c.is_production
          ? "Se enviará una orden REAL de compra de 1 contrato a 1¢ (post-only) y se cancelará al instante; si algo falla, caduca sola en un minuto. Como mucho te costaría 1¢."
          : "Se enviará una orden de 1 contrato a 1¢ en demo y se cancelará al instante.",
      confirmLabel: "Enviar la prueba",
    });
    if (!ok.ok) return;
    confirm = true;
  }
  const box = $("#diag-results");
  const buttons = [$("#btn-diagnose"), $("#btn-diagnose-order")];
  for (const b of buttons) b.disabled = true;
  box.hidden = false;
  box.replaceChildren(el("p", { class: "muted small", text: "Probando… (tarda unos segundos)" }));
  try {
    renderDiagnosis(await api("/api/diagnose", { method: "POST", body: { order_test: orderTest, confirm } }), orderTest);
  } catch (err) {
    box.replaceChildren(el("p", { class: "neg small", text: err.message }));
  } finally {
    for (const b of buttons) b.disabled = false;
  }
}

function renderDiagnosis(r, orderTest) {
  const items = r.steps.map((step) =>
    el(
      "li",
      { class: step.ok ? "ok" : "fail" },
      el("span", { class: "diag-icon", "aria-hidden": "true", text: step.ok ? "✓" : "✕" }),
      el(
        "div",
        {},
        el("b", { text: step.name }),
        el("p", { class: "small", text: step.detail }),
        step.hint ? el("p", { class: "muted small", text: step.hint }) : null,
      ),
    ),
  );
  let summary = "Hay algo que arreglar:";
  if (r.ok) {
    summary = orderTest
      ? "Todo listo: el bot puede leer tu cuenta y enviar órdenes."
      : "Todo listo para leer tu cuenta. Para comprobar también las órdenes, usa la prueba de orden.";
  }
  const nodes = [el("p", { class: "diag-summary " + (r.ok ? "pos" : "neg"), text: summary }), el("ul", { class: "diag-list" }, items)];
  if (r.suggest_env) {
    const label = r.suggest_env === "prod" ? "Real" : "Demo";
    nodes.push(
      el("button", {
        class: "btn primary full",
        type: "button",
        text: `Cambiar a ${label} y volver a probar`,
        onclick: async () => {
          if (await setEnv(r.suggest_env)) runDiagnosis(false);
        },
      }),
    );
  }
  $("#diag-results").replaceChildren(...nodes);
}

async function deleteCredentials() {
  const ok = await confirmDialog({ title: "Borrar credenciales", message: "El panel dejará de poder operar hasta que pongas otra API key.", confirmLabel: "Borrar", danger: true });
  if (!ok.ok) return;
  try {
    await api("/api/credentials", { method: "DELETE" });
    toast("Credenciales borradas");
    await loadSettings();
    refreshStatus().catch(() => {});
  } catch (err) {
    toast(err.message, "error");
  }
}

async function loadFairValues() {
  try {
    const rows = await api("/api/fair-values");
    state.fairValues = rows.map((r) => ({ ticker: r.ticker, pct: String(Math.round(Number(r.probability) * 1000) / 10) }));
  } catch (err) {
    toast(err.message, "error");
    state.fairValues = [];
  }
  renderFairValues();
}

function renderFairValues() {
  const box = $("#fv-rows");
  if (!state.fairValues.length) {
    box.replaceChildren(empty("Todavía no hay valores. Pulsa Añadir."));
    return;
  }
  box.replaceChildren(
    ...state.fairValues.map((row, i) => {
      const ticker = el("input", { placeholder: "TICKER", autocapitalize: "characters", autocomplete: "off", spellcheck: "false" });
      ticker.value = row.ticker;
      ticker.addEventListener("input", () => (row.ticker = ticker.value.trim().toUpperCase()));
      const pct = el("input", { type: "number", inputmode: "decimal", min: "0", max: "100", step: "0.1", placeholder: "%" });
      pct.value = row.pct;
      pct.addEventListener("input", () => (row.pct = pct.value));
      const remove = el("button", {
        class: "icon-btn",
        type: "button",
        text: "×",
        "aria-label": "Quitar",
        onclick: () => {
          state.fairValues.splice(i, 1);
          renderFairValues();
        },
      });
      return el("div", { class: "fv-row" }, ticker, el("div", { class: "input-unit" }, pct, el("span", { text: "%" })), remove);
    }),
  );
}

async function saveFairValues() {
  const rows = state.fairValues
    .filter((r) => r.ticker)
    .map((r) => ({ ticker: r.ticker.toUpperCase(), probability: `${r.pct}%` }));
  try {
    await api("/api/fair-values", { method: "PUT", body: { rows } });
    toast("Valores justos guardados", "success");
    loadFairValues();
  } catch (err) {
    toast(err.message, "error");
  }
}

/* ===================== resultados ===================== */

// Las fechas llegan como "2026-10-07" (día local); al mediodía la zona horaria no cambia el día.
function dayLabel(iso, options) {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("es-ES", options);
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

async function loadResults(days, scope) {
  if (days) state.resultsDays = days;
  if (scope) state.resultsScope = scope;
  for (const button of $$("#results-period button")) {
    button.classList.toggle("active", Number(button.dataset.days) === state.resultsDays);
  }
  for (const button of $$("#results-scope button")) {
    button.classList.toggle("active", button.dataset.scope === state.resultsScope);
  }
  const body = $("#results-body");
  const s = state.status;
  if (s && !s.credentials.configured) {
    body.replaceChildren(resultsNoKey());
    return;
  }
  // Mientras carga se queda lo anterior, atenuado: sin saltos.
  if (!body.childElementCount) body.replaceChildren(el("p", { class: "muted small center", text: "Cargando…" }));
  body.classList.add("refreshing");
  const ticket = (state.resultsTicket = (state.resultsTicket || 0) + 1);
  try {
    const tz = new Date().getTimezoneOffset();
    const r = await api(`/api/results?days=${state.resultsDays}&tz=${tz}&scope=${state.resultsScope}`);
    if (ticket === state.resultsTicket) renderResults(r);
  } catch (err) {
    if (ticket !== state.resultsTicket) return;
    body.replaceChildren(
      el(
        "div",
        { class: "card" },
        el("p", { class: "error-text", text: err.message }),
        el("button", { class: "btn full", type: "button", text: "Reintentar", onclick: () => loadResults() }),
      ),
    );
  } finally {
    if (ticket === state.resultsTicket) body.classList.remove("refreshing");
  }
}

function resultsNoKey() {
  return el(
    "div",
    { class: "card" },
    el("div", { class: "card-title", text: "Aún no hay resultados" }),
    el("p", {
      class: "card-note",
      text: "Para ver lo que gana el bot, configura tu API key de Kalshi en Ajustes → Avanzado. Solo cuenta el dinero real: en simulación no hay resultados.",
    }),
    el("button", { class: "btn primary full", type: "button", text: "Ir a Ajustes", onclick: () => switchView("settings") }),
  );
}

function tile(label, value, cls = "") {
  return el("div", { class: "tile" }, el("span", { text: label }), el("b", { class: cls, text: value }));
}

// "2026-10-08" → "JUE 8 OCT"
function dayHeader(iso) {
  return dayLabel(iso, { weekday: "short", day: "numeric", month: "short" }).replace(/[.,]/g, "").toUpperCase();
}

function renderResults(r) {
  const t = r.totals;
  const parts = [
    el(
      "div",
      { class: "tiles" },
      tile("Ganado", fmt.money(t.net, true), signClass(t.net)),
      tile("Acierto", t.markets ? `${t.wins} de ${t.markets}` : "—"),
      tile("Entrada", t.avg_price == null ? "—" : fmt.cents(t.avg_price)),
    ),
  ];
  for (const m of r.recent) rememberLabel(m.ticker, m.title === m.ticker ? "" : m.title, m.subtitle);
  if (r.recent.length) {
    parts.push(dayGroups(r.recent, r.by_day));
  } else {
    const noHistory = r.scope === "bot" && !r.bot_history;
    parts.push(
      el("p", {
        class: "card-note",
        text: noHistory
          ? "Todavía no hay compras del bot con dinero real: aparecerán aquí cuando opere. Lo que hayas comprado tú está en «Toda la cuenta»."
          : "Ningún mercado cerrado en este periodo. Un mercado cuenta cuando Kalshi lo liquida (los partidos, al terminar; el clima, a la mañana siguiente) o cuando se vende antes.",
      }),
    );
  }
  parts.push(
    el("p", {
      class: "help",
      text:
        r.scope === "bot"
          ? "Solo cuenta lo que compró el bot. Lo que compres tú a mano está en «Toda la cuenta»."
          : "Cuenta todo lo de tu cuenta de Kalshi, también lo que compres a mano.",
    }),
  );
  if (t.markets) parts.push(moreResults(r));
  state.resultsChart = null;
  $("#results-body").replaceChildren(...parts);
}

// La lista de mercados cerrados, agrupada por día (con lo ganado ese día).
function dayGroups(rows, byDay, limit = 30) {
  const totals = Object.fromEntries((byDay || []).map((d) => [d.date, d.net]));
  const box = el("div", { class: "rows" });
  const render = (items) => {
    const nodes = [];
    let day = null;
    for (const m of items) {
      if (m.date !== day) {
        day = m.date;
        const total = totals[day];
        nodes.push(
          el(
            "div",
            { class: "day-head" },
            el("span", { class: "eyebrow", text: dayHeader(day) }),
            el("span", { class: "eyebrow", text: total == null ? "" : fmt.money(total, true) }),
          ),
        );
      }
      nodes.push(resultItem(m));
    }
    box.replaceChildren(...nodes);
  };
  render(rows.slice(0, limit));
  if (rows.length <= limit) return box;
  const more = el("button", {
    class: "btn full",
    type: "button",
    text: `Ver ${rows.length - limit} más`,
    onclick: () => {
      render(rows);
      more.remove();
    },
  });
  return el("div", {}, box, more);
}

function resultItem(m) {
  const label = labelFor(m.ticker, m.title === m.ticker ? "" : m.title, m.subtitle);
  const sub = [label.group, sizeText(m.contracts, m.cost), m.sold_early ? "vendido antes" : ""];
  return el(
    "button",
    { class: "trade", type: "button", onclick: () => openDetail(m, "results") },
    chip(m.side === "ambos" ? "both" : m.side),
    el(
      "span",
      { class: "trade-main" },
      el("span", { class: "trade-title", text: label.short }),
      el("span", { class: "trade-sub", text: sub.filter(Boolean).join(" · ") }),
    ),
    el("span", { class: `trade-value ${signClass(m.net)}`, text: fmt.money(m.net, true) }),
  );
}

// Rendimiento, gráfico por día y por tipo de mercado: plegado para no cargar la lista.
function moreResults(r) {
  const t = r.totals;
  const chartHost = el("div", { class: "chart" });
  const notes = [
    `Rendimiento ${fmt.pct(t.return)} sobre ${fmt.money(t.cost)} invertidos (lo esperado: ≈ ${fmt.pct(r.expected_return, 0)}). Comisiones pagadas: ${fmt.money(t.fees)}, ya descontadas.`,
  ];
  if (t.markets < 30) notes.push("Con menos de 30 mercados manda la suerte: un fallo cuesta lo que ganan unos 15 aciertos.");
  const categories = r.by_category.length
    ? el(
        "div",
        { class: "rows" },
        el("div", { class: "day-head" }, el("span", { class: "eyebrow", text: "Por tipo de mercado" })),
        r.by_category.map((c) =>
          el(
            "div",
            { class: "trade" },
            el(
              "span",
              { class: "trade-main" },
              el("span", { class: "trade-title", text: c.label }),
              el("span", {
                class: "trade-sub",
                text: `${plural(c.markets, "mercado", "mercados")} · ${c.wins} ganados · ${c.losses} perdidos · ${fmt.pct(c.return)}`,
              }),
            ),
            el("span", { class: `trade-value ${signClass(c.net)}`, text: fmt.money(c.net, true) }),
          ),
        ),
      )
    : null;
  const details = el(
    "details",
    { class: "more" },
    el("summary", { text: "Más datos" }),
    notes.map((text) => el("p", { class: "card-note", text })),
    el("div", { class: "day-head" }, el("span", { class: "eyebrow", text: "Ganancia por día" })),
    el(
      "div",
      { class: "legend" },
      el("span", {}, el("i", { class: "gain" }), "Ganancia"),
      el("span", {}, el("i", { class: "loss" }), "Pérdida"),
    ),
    chartHost,
    dayTable(r.by_day),
    categories,
  );
  // El gráfico se dibuja al abrir: cerrado no tiene ancho.
  details.addEventListener("toggle", () => {
    if (!details.open) return;
    drawDailyChart(chartHost, r.by_day);
    state.resultsChart = { host: chartHost, days: r.by_day, width: chartHost.clientWidth };
  });
  return details;
}

/* ===================== inicio: lo que lleva ganado el bot ===================== */

function renderToday(r) {
  const today = $("#stat-today");
  if (!r) {
    today.textContent = "—";
    today.className = "";
    $("#stat-today-sub").textContent = "sin API key";
    $("#stat-yesterday").textContent = "";
    return;
  }
  const t = r.today_totals;
  today.textContent = fmt.money(t.net, true);
  today.className = valueClass(t.net);
  $("#stat-today-sub").textContent = t.markets ? `${plural(t.markets, "cierre", "cierres")} del bot` : "ningún cierre aún";
  const y = r.yesterday_totals;
  $("#stat-yesterday").textContent = y && y.markets ? `ayer ${fmt.money(y.net, true)}` : "";
}

async function loadHomeResults() {
  const box = $("#home-results");
  const s = state.status;
  if (s && !s.credentials.configured) {
    box.replaceChildren(resultsNoKey());
    renderToday(null);
    state.curve = null;
    return;
  }
  try {
    const tz = new Date().getTimezoneOffset();
    renderHomeResults(await api(`/api/results?days=90&tz=${tz}&scope=bot`));
  } catch (err) {
    if (!box.childElementCount) {
      box.replaceChildren(el("p", { class: "card-note", text: "No se pudieron leer los resultados: " + err.message }));
    }
  }
}

function renderHomeResults(r) {
  renderToday(r);
  const t = r.totals;
  for (const m of r.recent) rememberLabel(m.ticker, m.title === m.ticker ? "" : m.title, m.subtitle);
  let sub = `${plural(t.markets, "mercado cerrado", "mercados cerrados")} · ${fmt.money(t.cost)} arriesgados`;
  if (!t.markets) sub = r.bot_history ? "Aún no se ha cerrado ningún mercado del bot." : "Aún no hay compras del bot con dinero real.";
  const parts = [
    el(
      "div",
      { class: "earned" },
      el("div", { class: "eyebrow", text: "Ganado por el bot" }),
      el("div", { class: `earned-value ${valueClass(t.net)}`, text: fmt.money(t.net, true) }),
      el("div", { class: "earned-sub", text: sub }),
    ),
  ];
  const curveHost = r.curve.length ? el("div", { class: "curve" }) : null;
  if (curveHost) parts.push(curveHost);
  if (t.markets) parts.push(hitCard(t));
  parts.push(sampleCard(t.markets));
  if (r.recent.length) parts.push(lastCloseCard(r.recent[0]));
  $("#home-results").replaceChildren(...parts);
  state.curve = curveHost ? { host: curveHost, points: r.curve, width: curveHost.clientWidth } : null;
  if (curveHost) drawCurve(curveHost, r.curve);
}

// Acierto frente al umbral: el precio medio (con comisiones) es el acierto que hace falta.
function hitCard(t) {
  const rate = t.markets ? t.wins / t.markets : 0;
  const breakeven = t.breakeven == null ? null : Number(t.breakeven);
  const fill = el("div", { class: "fill" + (breakeven != null && rate < breakeven ? " below" : "") });
  fill.style.width = `${Math.round(rate * 1000) / 10}%`;
  const gauge = el("div", { class: "gauge" }, el("div", { class: "track" }, fill));
  let note = "Cuando el bot cierre mercados verás aquí cuántos acierta.";
  if (breakeven != null) {
    const at = Math.min(100, Math.max(0, breakeven * 100));
    const mark = el("div", { class: "mark" });
    mark.style.left = `${at}%`;
    const label = el("div", { class: "mark-label", text: `umbral ${fmt.share(breakeven)}` });
    if (at > 70) label.style.right = "0";
    else label.style.left = `${at}%`;
    gauge.append(mark, label);
    note = `Entra a ${fmt.cents(t.avg_price)} de media, así que necesita acertar más del ${fmt.share(breakeven)} para ganar.`;
  }
  return el(
    "div",
    { class: "card" },
    el("div", { class: "card-row" }, el("div", { class: "card-title", text: "Acierto" }), el("div", { class: "card-value", text: `${t.wins} de ${t.markets}` })),
    gauge,
    el("p", { class: "card-note", text: note }),
  );
}

function sampleCard(markets) {
  const bar = el("i");
  bar.style.width = `${Math.min(100, markets)}%`;
  return el(
    "div",
    { class: "card" },
    el("div", { class: "card-row" }, el("div", { class: "card-title", text: "Muestra" }), el("div", { class: "card-value", text: `${markets} / 100` })),
    el("div", { class: "progress-plain" }, bar),
    el("p", {
      class: "card-note",
      text: markets >= 100 ? "Ya hay mercados suficientes para saber si el bot tiene ventaja." : "Hasta 100 mercados no se sabe si es ventaja o racha.",
    }),
  );
}

function lastCloseCard(m) {
  const label = labelFor(m.ticker, m.title === m.ticker ? "" : m.title, m.subtitle);
  return el(
    "button",
    { class: "link-card", type: "button", onclick: () => openDetail(m, "home") },
    el("span", { class: "lc-text" }, el("span", { class: "eyebrow", text: "Último cierre" }), el("span", { class: "lc-title", text: label.short })),
    el("span", { class: `lc-value ${signClass(m.net)}`, text: fmt.money(m.net, true) }),
  );
}

// Ganancia acumulada mercado a mercado (una sola serie: la línea lima del diseño).
function drawCurve(host, points) {
  const width = Math.max(240, Math.floor(host.clientWidth || 320));
  const height = 100;
  const top = 12;
  const bottom = 88;
  const totals = [0, ...points.map((p) => Number(p.total))];
  const n = totals.length - 1;
  const hi = Math.max(0, ...totals);
  const lo = Math.min(0, ...totals);
  const y = (v) => top + ((hi - v) / (hi - lo || 1)) * (bottom - top);
  const x = (i) => 2 + (i / n) * (width - 10);
  const coords = totals.map((v, i) => [x(i), y(v)]);
  const line = coords.map(([cx, cy]) => `${cx.toFixed(1)},${cy.toFixed(1)}`).join(" ");
  const zero = y(0);
  const chart = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    height,
    role: "img",
    tabindex: "0",
    "aria-label": `Ganancia acumulada en ${plural(n, "mercado cerrado", "mercados cerrados")}: ${fmt.money(totals[n], true)}. Toca o usa las flechas para ver cada mercado.`,
  });
  chart.append(
    svg("line", { class: "base", x1: 0, x2: width, y1: zero + 0.5, y2: zero + 0.5 }),
    svg("polygon", { class: "area", points: `${coords[0][0].toFixed(1)},${zero} ${line} ${coords[n][0].toFixed(1)},${zero}` }),
    svg("polyline", { class: "line", points: line }),
  );
  const dots = [];
  coords.forEach(([cx, cy], i) => {
    if (i === 0 || (n > 40 && i !== n)) return; // con muchos mercados, solo el último punto
    const dot = svg("circle", { class: i === n ? "pt last" : "pt", cx, cy, r: i === n ? 5 : 3 });
    chart.append(dot);
    dots[i] = dot;
  });
  const hit = svg("rect", { x: 0, y: 0, width, height, fill: "transparent" });
  chart.append(hit);

  const tip = el("div", { class: "curve-tip", hidden: true });
  let selected = null;
  const select = (i) => {
    selected = i;
    dots.forEach((dot, j) => dot && dot.classList.toggle("on", j === i && j !== n));
    if (i == null) {
      tip.hidden = true;
      return;
    }
    const p = points[i - 1];
    tip.replaceChildren(
      el("b", { text: fmt.money(p.net, true) }),
      el("span", { text: `${labelFor(p.ticker).short} · ${dayLabel(p.date, { day: "numeric", month: "short" })}` }),
      el("span", { text: `Acumulado ${fmt.money(p.total, true)}` }),
    );
    tip.hidden = false;
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(coords[i][0], half), width - half)}px`;
    tip.style.top = `${coords[i][1] - 8}px`;
  };
  const indexAt = (event) => {
    const box = chart.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    return Math.min(n, Math.max(1, Math.round(((px - 2) / (width - 10)) * n)));
  };
  hit.addEventListener("pointerdown", (event) => select(indexAt(event)));
  hit.addEventListener("pointermove", (event) => {
    if (event.pointerType === "mouse" || event.buttons) select(indexAt(event));
  });
  hit.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse") select(null);
  });
  chart.addEventListener("focus", () => {
    if (selected == null) select(n);
  });
  chart.addEventListener("blur", () => select(null));
  chart.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      select(Math.min(n, Math.max(1, (selected ?? n) + (event.key === "ArrowLeft" ? -1 : 1))));
    } else if (event.key === "Escape") {
      select(null);
    }
  });
  const day = (iso) => dayLabel(iso, { day: "numeric", month: "short" });
  const labels = el("div", { class: "curve-labels" }, el("span", { text: day(points[0].date) }), el("span", { text: day(points[n - 1].date) }));
  host.replaceChildren(chart, labels, tip);
}

/* ===================== detalle de un mercado cerrado ===================== */

function openDetail(m, from) {
  state.detailFrom = from === "home" ? "home" : "results";
  const label = labelFor(m.ticker, m.title === m.ticker ? "" : m.title, m.subtitle);
  const contracts = Number(m.contracts);
  const cost = Number(m.cost);
  const fees = Number(m.fees);
  const net = Number(m.net);
  const avg = contracts > 0 ? cost / contracts : null;
  const breakeven = contracts > 0 ? (cost + fees) / contracts : null;
  const upside = contracts - cost; // lo que se gana si acierta, antes de comisiones
  const icon = svg("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
  icon.append(svg("path", { d: "M15 5l-7 7 7 7" }));
  const back = el(
    "button",
    { class: "back", type: "button", onclick: () => switchView(state.detailFrom) },
    icon,
    el("span", { text: state.detailFrom === "home" ? "Inicio" : "Resultados" }),
  );
  const status = m.sold_early ? "Vendido antes" : "Cerrado";
  const head = el(
    "div",
    { class: "detail-head" },
    el(
      "div",
      { class: "tags" },
      chip(m.side === "ambos" ? "both" : m.side),
      el("span", { class: "eyebrow", text: `${status} · ${dayLabel(m.date, { day: "numeric", month: "short" })}` }),
    ),
    el("h1", { text: label.long }),
  );
  const result = el(
    "div",
    { class: "earned" },
    el("div", { class: "eyebrow", text: "Resultado" }),
    el(
      "div",
      { class: "result-row" },
      el("div", { class: `result-value ${valueClass(net)}`, text: fmt.money(net, true) }),
      cost > 0 ? el("div", { class: "result-pct", text: fmt.pct(net / cost) }) : null,
    ),
  );
  let risk = null;
  if (contracts > 0 && cost > 0 && upside > 0) {
    const bar = el("div", { class: "risk" });
    bar.style.width = `${Math.min(97, Math.max(3, (cost / contracts) * 100))}%`;
    const times = Math.max(1, Math.round(cost / upside));
    risk = el(
      "div",
      { class: "card" },
      el("div", { class: "card-title", text: "Riesgo y premio" }),
      el("div", { class: "split" }, bar, el("div", { class: "reward" })),
      el(
        "div",
        { class: "split-labels" },
        el("span", { text: `${fmt.money(cost)} en riesgo` }),
        el("span", { class: "pos", text: `${fmt.money(upside)} a ganar` }),
      ),
      el("p", {
        class: "card-note",
        text:
          net < 0
            ? `Esta pérdida se come lo que ganan unos ${times} aciertos a este precio.`
            : `Una pérdida de este tamaño borra ${times} aciertos como este.`,
      }),
    );
  }
  const fact = (name, value) => el("div", {}, el("span", { text: name }), el("span", { text: value }));
  const facts = el(
    "div",
    { class: "facts" },
    fact("Contratos", fmt.qty(m.contracts)),
    fact("Precio de entrada", avg == null ? "—" : fmt.cents(avg)),
    fact("Pagó", fmt.money(cost)),
    fact("Cobró", fmt.money(m.payout)),
    fact("Comisiones", fmt.money(fees)),
    fact("Acierto necesario", breakeven == null ? "—" : `más del ${fmt.share(breakeven)}`),
  );
  const rules = el("button", { class: "link", type: "button", text: "Ver el mercado y sus reglas", onclick: () => openMarket(m.ticker) });
  $("#view-detail").replaceChildren(...[back, head, result, risk, facts, rules].filter(Boolean));
  switchView("detail");
}

function dayTable(days) {
  const active = days.filter((d) => d.markets > 0).reverse();
  const details = el("details", {}, el("summary", { text: "Ver los días en una tabla" }));
  if (!active.length) {
    details.append(empty("Ningún mercado cerrado en este periodo."));
    return details;
  }
  const rows = active.map((d) =>
    el(
      "tr",
      {},
      el("td", { text: dayLabel(d.date, { weekday: "short", day: "numeric", month: "short" }) }),
      el("td", { text: `${d.markets} (${d.wins} ganados, ${d.losses} perdidos)` }),
      el("td", { class: signClass(d.net), text: fmt.money(d.net, true) }),
    ),
  );
  const head = el("tr", {}, el("th", { text: "Día" }), el("th", { text: "Mercados" }), el("th", { text: "Ganancia" }));
  details.append(el("div", { class: "table-wrap" }, el("table", { class: "results-table" }, el("thead", {}, head), el("tbody", {}, rows))));
  return details;
}

// Redondea hacia arriba a 1, 2, 2,5 o 5 por una potencia de 10 (para las marcas del eje).
function niceCeil(value) {
  if (value <= 0) return 0;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 5, 10]) if (step * power >= value - 1e-9) return step * power;
  return 10 * power;
}

function drawDailyChart(host, days) {
  const width = Math.max(240, Math.floor(host.clientWidth || 300));
  const left = 52;
  const right = 4;
  const top = 8;
  const plotH = 150;
  const axisH = 24;
  const plotW = width - left - right;
  const values = days.map((d) => Number(d.net));
  let hi = niceCeil(Math.max(0, ...values));
  let lo = -niceCeil(-Math.min(0, ...values));
  if (hi === 0 && lo === 0) hi = 1;
  const y = (v) => top + ((hi - v) / (hi - lo)) * plotH;
  const band = plotW / days.length;
  const barW = Math.max(1, Math.min(24, band - 2)); // 2 px de aire entre barras
  const chart = svg("svg", {
    viewBox: `0 0 ${width} ${top + plotH + axisH}`,
    height: top + plotH + axisH,
    role: "img",
    tabindex: "0",
    "aria-label": "Ganancia por día. Usa las flechas para recorrer los días; la tabla de debajo tiene los mismos datos.",
  });

  for (const v of [hi, lo]) {
    if (v === 0) continue;
    chart.append(svg("line", { class: "grid", x1: left, x2: width - right, y1: y(v) + 0.5, y2: y(v) + 0.5 }));
  }
  for (const v of new Set([hi, 0, lo])) {
    const label = svg("text", { class: "tick", x: left - 8, y: y(v) + 4, "text-anchor": "end" });
    label.textContent = v === 0 ? "$0" : fmt.money(v, true);
    chart.append(label);
  }

  const bars = [];
  days.forEach((d, i) => {
    const v = Number(d.net);
    if (!v) {
      bars.push(null);
      return;
    }
    const x = left + i * band + (band - barW) / 2;
    const y0 = y(0);
    const y1 = y(v);
    const h = Math.abs(y1 - y0);
    const r = Math.min(4, barW / 2, h);
    // Esquinas redondeadas solo en la punta; la base, recta sobre el cero.
    const path =
      v > 0
        ? `M${x},${y0} V${y1 + r} Q${x},${y1} ${x + r},${y1} H${x + barW - r} Q${x + barW},${y1} ${x + barW},${y1 + r} V${y0} Z`
        : `M${x},${y0} V${y1 - r} Q${x},${y1} ${x + r},${y1} H${x + barW - r} Q${x + barW},${y1} ${x + barW},${y1 - r} V${y0} Z`;
    const bar = svg("path", { class: `bar ${v > 0 ? "gain" : "loss"}`, d: path });
    chart.append(bar);
    bars.push(bar);
  });
  chart.append(svg("line", { class: "base", x1: left, x2: width - right, y1: y(0) + 0.5, y2: y(0) + 0.5 }));

  // Con 7 días se rotula cada uno; con más, el primero, el del medio y el último.
  const dense = days.length > 7;
  const last = days.length - 1;
  const ticks = dense ? [0, Math.floor(last / 2), last] : days.map((_, i) => i);
  for (const i of ticks) {
    const edge = dense && (i === 0 || i === last);
    const x = edge ? (i === 0 ? left : width - right) : left + (i + 0.5) * band;
    const anchor = edge ? (i === 0 ? "start" : "end") : "middle";
    const label = svg("text", { class: "tick", x, y: top + plotH + 17, "text-anchor": anchor });
    const options = dense ? { day: "numeric", month: "short" } : { weekday: "short", day: "numeric" };
    label.textContent = dayLabel(days[i].date, options).replace(",", "");
    chart.append(label);
  }

  const cross = svg("line", { class: "cross", y1: top, y2: top + plotH, visibility: "hidden" });
  chart.append(cross);
  const hit = svg("rect", { x: left, y: top, width: plotW, height: plotH, fill: "transparent" });
  chart.append(hit);

  const tip = el("div", { class: "chart-tip", hidden: true });
  let selected = null;
  const select = (i) => {
    selected = i;
    host.classList.toggle("selecting", i != null);
    bars.forEach((bar, j) => bar && bar.classList.toggle("on", j === i));
    if (i == null) {
      tip.hidden = true;
      cross.setAttribute("visibility", "hidden");
      return;
    }
    const d = days[i];
    const cx = left + (i + 0.5) * band;
    cross.setAttribute("x1", cx);
    cross.setAttribute("x2", cx);
    cross.setAttribute("visibility", "visible");
    const detail = d.markets
      ? `${plural(d.markets, "mercado", "mercados")} · ${d.wins} ganados · ${d.losses} perdidos`
      : "sin mercados cerrados";
    tip.replaceChildren(
      el("b", { text: fmt.money(d.net, true) }),
      el("span", { text: `${dayLabel(d.date, { weekday: "short", day: "numeric", month: "short" })} · ${detail}` }),
    );
    tip.hidden = false;
    const half = tip.offsetWidth / 2;
    tip.style.left = `${Math.min(Math.max(cx, half), width - half)}px`;
  };
  const indexAt = (event) => {
    const box = chart.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * width;
    return Math.min(days.length - 1, Math.max(0, Math.floor((x - left) / band)));
  };
  hit.addEventListener("pointerdown", (event) => select(indexAt(event)));
  hit.addEventListener("pointermove", (event) => {
    if (event.pointerType === "mouse" || event.buttons) select(indexAt(event));
  });
  hit.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse") select(null);
  });
  chart.addEventListener("focus", () => {
    if (selected == null) {
      const last = days.map((d) => d.markets > 0).lastIndexOf(true);
      select(last >= 0 ? last : days.length - 1);
    }
  });
  chart.addEventListener("blur", () => select(null));
  chart.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      const step = event.key === "ArrowLeft" ? -1 : 1;
      select(Math.min(days.length - 1, Math.max(0, (selected ?? days.length - 1) + step)));
    } else if (event.key === "Escape") {
      select(null);
    }
  });
  host.replaceChildren(chart, tip);
}

window.addEventListener("resize", () => {
  const c = state.resultsChart;
  if (c && state.view === "results" && c.host.isConnected && c.host.clientWidth !== c.width) {
    c.width = c.host.clientWidth;
    drawDailyChart(c.host, c.days);
  }
  const k = state.curve;
  if (k && state.view === "home" && k.host.isConnected && k.host.clientWidth !== k.width) {
    k.width = k.host.clientWidth;
    drawCurve(k.host, k.points);
  }
});

document.addEventListener("DOMContentLoaded", init);
