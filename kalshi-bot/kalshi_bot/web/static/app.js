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
    const c = Math.round(Number(v) * 10000) / 100;
    return (Number.isInteger(c) ? String(c) : c.toFixed(1)) + "¢";
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
};

const signClass = (v) => (Number(v) > 0 ? "pos" : Number(v) < 0 ? "neg" : "");

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
  for (const view of $$(".view")) view.hidden = view.id !== `view-${name}`;
  for (const tab of $$(".tab")) tab.classList.toggle("active", tab.dataset.view === name);
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
  $("#btn-sim").addEventListener("click", () => startBot("sim"));
  $("#btn-live").addEventListener("click", () => startBot("live"));
  $("#btn-stop").addEventListener("click", stopBot);
  $("#btn-kill").addEventListener("click", killBot);
  $("#btn-refresh-portfolio").addEventListener("click", refreshPortfolio);
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
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && !$("#app").hidden) refreshStatus().catch(() => {});
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
  }
}

/* ===================== inicio ===================== */

function loadHome() {
  refreshStatus().catch(() => {});
  refreshLogs();
  refreshPortfolio();
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
  const badge = $("#env-badge");
  badge.textContent = s.is_production ? "REAL" : "DEMO";
  badge.className = "badge " + (s.is_production ? "prod" : "demo");

  const bot = s.bot;
  const dot = $("#state-dot");
  dot.className = "dot";
  let label = "Detenido";
  let sub = `Estrategia: ${strategyLabel(bot.strategy)}`;
  if (bot.state === "running") {
    const live = bot.mode === "live";
    dot.classList.add(live ? "running" : "sim");
    label = live ? (s.is_production ? "Operando con dinero real" : "Operando en demo") : "Simulando";
    const parts = [strategyLabel(bot.strategy), `${bot.markets.length} mercados`];
    if (bot.last_tick_at) parts.push(`última vuelta ${fmt.ago(bot.last_tick_at)}`);
    sub = parts.join(" · ");
  } else if (bot.state === "halted") {
    dot.classList.add("halted");
    label = "Frenado";
  }
  $("#state-label").textContent = label;
  $("#state-sub").textContent = sub;

  const banner = $("#state-banner");
  banner.hidden = false;
  if (bot.halted_reason && bot.state !== "running") {
    banner.className = "banner danger";
    banner.textContent = "Freno de emergencia: " + bot.halted_reason;
  } else if (!s.credentials.configured) {
    banner.className = "banner info";
    banner.textContent = "Sin API key: puedes simular con datos reales, pero para operar configúrala en Ajustes.";
  } else if (bot.state === "running" && bot.consecutive_errors > 0) {
    banner.className = "banner warn";
    banner.textContent = `La API está fallando (${bot.consecutive_errors} vueltas seguidas). Mira la actividad.`;
  } else {
    banner.hidden = true;
  }

  const running = bot.state === "running";
  $("#start-buttons").hidden = running;
  $("#btn-stop").hidden = !running;
  const live = $("#btn-live");
  live.disabled = !s.credentials.configured;
  live.textContent = s.is_production ? "Operar (dinero real)" : "Operar en demo";

  const balance = s.balance;
  $("#stat-cash").textContent = balance ? fmt.money(balance.cash) : "—";
  $("#stat-portfolio").textContent = balance ? fmt.money(balance.portfolio_value) : "—";
  const pnl = $("#stat-pnl");
  pnl.textContent = s.session_pnl == null ? "—" : fmt.money(s.session_pnl, true);
  pnl.className = "stat-value " + signClass(s.session_pnl);
  const note = $("#balance-note");
  note.hidden = !(s.balance_error || (!s.credentials.configured && running));
  note.textContent = s.balance_error
    ? "No se pudo leer el saldo: " + s.balance_error
    : "Saldo virtual de simulación (sin API key).";
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
    if (!result.ok) return;
    if (s.is_production) body.confirm = "REAL";
  }
  try {
    await api("/api/bot/start", { method: "POST", body });
    toast(mode === "live" ? "Bot en marcha" : "Simulación en marcha", "success");
    await refreshStatus();
    setTimeout(refreshLogs, 800);
  } catch (err) {
    toast(err.message, "error");
  }
}

async function stopBot() {
  $("#btn-stop").disabled = true;
  try {
    await api("/api/bot/stop", { method: "POST" });
    toast("Bot detenido");
    await refreshStatus();
    refreshLogs();
    refreshPortfolio();
  } catch (err) {
    toast(err.message, "error");
  } finally {
    $("#btn-stop").disabled = false;
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
      box.append(el("div", { class: `log-line ${line.level}` }, el("span", { class: "t", text: fmt.time(line.ts) }), line.message));
      state.logsAfter = line.id;
    }
    while (box.childElementCount > 300) box.firstElementChild.remove();
    if (atBottom) box.scrollTop = box.scrollHeight;
  } catch {
    /* se reintenta en la siguiente vuelta */
  }
}

function empty(text) {
  return el("div", { class: "empty", text });
}

async function refreshPortfolio() {
  const positions = $("#positions");
  const orders = $("#orders");
  const s = state.status;
  if (!s || !s.credentials.configured) {
    positions.replaceChildren(empty("Configura tu API key en Ajustes para ver tus posiciones."));
    orders.replaceChildren(empty("—"));
    return;
  }
  try {
    const [pos, ord] = await Promise.all([api("/api/positions"), api("/api/orders")]);
    positions.replaceChildren(...(pos.length ? pos.map(positionItem) : [empty("No tienes posiciones abiertas.")]));
    orders.replaceChildren(...(ord.length ? ord.map(orderItem) : [empty("No hay órdenes abiertas.")]));
  } catch (err) {
    positions.replaceChildren(empty(err.message));
  }
}

function sideBadge(outcome, text) {
  return el("span", { class: `badge ${outcome}`, text: text || (outcome === "yes" ? "SÍ" : "NO") });
}

function positionItem(p) {
  return el(
    "div",
    { class: "item tappable", onclick: () => openMarket(p.ticker) },
    el(
      "div",
      { class: "item-main" },
      el("div", { class: "item-title", text: p.ticker }),
      el("div", { class: "item-sub", text: `Comprometido ${fmt.money(p.exposure)} · comisiones ${fmt.money(p.fees_paid)}` }),
    ),
    el(
      "div",
      { class: "item-side" },
      sideBadge(p.side, `${fmt.qty(p.contracts)} ${p.side === "yes" ? "SÍ" : "NO"}`),
      el("div", { class: "small " + signClass(p.realized_pnl), text: "realizado " + fmt.money(p.realized_pnl, true) }),
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
  return el(
    "div",
    { class: "item" },
    el(
      "div",
      { class: "item-main" },
      el("div", { class: "item-title", text: o.ticker }),
      el(
        "div",
        { class: "item-sub" },
        sideBadge(o.outcome),
        ` ${fmt.qty(o.remaining)} a ${fmt.cents(o.price)} `,
        o.source === "bot" ? el("span", { class: "badge bot", text: "bot" }) : null,
      ),
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
  return el(
    "div",
    { class: "item tappable", onclick: () => openMarket(m.ticker) },
    el(
      "div",
      { class: "item-main" },
      el("div", { class: "item-title", text: m.subtitle || m.title || m.ticker }),
      el("div", { class: "item-sub", text: `cierra en ${fmt.hours(m.hours_to_close)} · vol ${fmt.qty(m.volume_24h)} · ${m.ticker}` }),
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

  openSheet(
    el("h2", { text: m.subtitle || m.title || m.ticker }),
    el("p", { class: "muted small", text: `${m.title || ""} · ${m.ticker} · cierra en ${fmt.hours(m.hours_to_close)}` }),
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
  const favorites = r.favorites.map((f) =>
    el(
      "div",
      { class: "item tappable", onclick: () => openMarket(f.ticker) },
      el(
        "div",
        { class: "item-main" },
        el("div", { class: "item-title", text: f.subtitle || f.title || f.ticker }),
        el("div", { class: "item-sub", text: `cierra en ${fmt.hours(f.hours_to_close)} · vol ${fmt.qty(f.volume_24h)} · ${f.ticker}` }),
      ),
      el("div", { class: "item-side" }, sideBadge(f.side, `${f.side === "yes" ? "SÍ" : "NO"} ${fmt.cents(f.bid)}`), el("div", { class: "small muted", text: `gana ${fmt.cents(f.max_profit)}` })),
    ),
  );
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
    toast(state.settings.needs_restart ? "Guardado. Detén y arranca el bot para aplicarlo." : "Ajustes guardados", "success");
  } catch (err) {
    toast(err.message, "error");
  } finally {
    button.disabled = false;
  }
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

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs || {})) if (value != null) node.setAttribute(key, value);
  return node;
}

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
    el("h2", { text: "Aún no hay resultados" }),
    el("p", {
      class: "muted small",
      text: "Para ver lo que ganas o pierdes cada día, configura tu API key de Kalshi en Ajustes. Solo cuenta el dinero real: en simulación no hay resultados.",
    }),
    el("button", { class: "btn primary full", type: "button", text: "Ir a Ajustes", onclick: () => switchView("settings") }),
  );
}

function statTile(label, value, sign) {
  return el(
    "div",
    { class: "stat" },
    el("div", { class: "stat-label", text: label }),
    el("div", { class: "stat-value " + (sign == null ? "" : signClass(sign)), text: value }),
  );
}

function renderResults(r) {
  const t = r.totals;
  const hero = el(
    "div",
    { class: "card results-hero" },
    el("div", {
      class: "muted small",
      text: `${r.scope === "bot" ? "Ganancia neta del bot" : "Ganancia neta de la cuenta"} · últimos ${r.days} días`,
    }),
    el("div", { class: "hero-figure", text: fmt.money(t.net, true) }),
    el("div", {
      class: "muted small",
      text: t.markets
        ? `${plural(t.markets, "mercado cerrado", "mercados cerrados")} · ${t.wins} ganados · ${t.losses} perdidos`
        : "Ningún mercado cerrado en este periodo",
    }),
  );
  if (t.markets) {
    hero.append(
      el("p", {
        class: "small",
        text: `Rendimiento ${fmt.pct(t.return)} sobre ${fmt.money(t.cost)} invertidos (lo esperado: ≈ ${fmt.pct(r.expected_return, 0)}).`,
      }),
      el("p", { class: "muted small", text: `Comisiones pagadas: ${fmt.money(t.fees)}, ya descontadas.` }),
    );
    if (t.markets < 30) {
      hero.append(
        el("p", {
          class: "muted small",
          text: "Con menos de 30 mercados manda la suerte: un fallo cuesta lo que ganan unos 15 aciertos. Juzga el bot cuando lleve más.",
        }),
      );
    }
  } else {
    const noHistory = r.scope === "bot" && !r.bot_history;
    hero.append(
      el("p", {
        class: "muted small",
        text: noHistory
          ? "Todavía no hay compras del bot registradas: aparecerán aquí cuando opere con dinero real. Lo que hayas comprado tú está en «Toda la cuenta»."
          : "Un mercado cuenta cuando Kalshi lo liquida (los partidos, al terminar; el clima, a la mañana siguiente) o cuando se vende antes. En simulación no hay resultados: solo cuenta lo real.",
      }),
    );
  }

  const today = r.today_totals;
  const yesterday = r.yesterday_totals;
  const stats = el(
    "div",
    { class: "stats" },
    statTile("Hoy", fmt.money(today.net, true), today.net),
    statTile("Ayer", yesterday ? fmt.money(yesterday.net, true) : "—", yesterday && yesterday.net),
    statTile(r.open.markets ? `En juego (${r.open.markets})` : "En juego", fmt.money(r.open.exposure)),
  );

  const chartHost = el("div", { class: "chart" });
  const chartCard = el(
    "div",
    { class: "card" },
    el("h2", { text: "Ganancia por día" }),
    el("p", { class: "muted small", text: "Cada barra es un día: hacia arriba lo ganado y hacia abajo lo perdido. Toca el gráfico para ver el día." }),
    el(
      "div",
      { class: "legend" },
      el("span", {}, el("i", { class: "gain" }), "Ganancia"),
      el("span", {}, el("i", { class: "loss" }), "Pérdida"),
    ),
    chartHost,
    dayTable(r.by_day),
  );

  // Sin mercados cerrados el gráfico sería una línea plana: basta con el resumen.
  const cards = t.markets ? [hero, stats, chartCard] : [hero, stats];
  if (r.by_category.length) {
    cards.push(
      el(
        "div",
        { class: "card" },
        el("h2", { text: "Por tipo de mercado" }),
        el(
          "div",
          { class: "list" },
          r.by_category.map((c) =>
            el(
              "div",
              { class: "item" },
              el(
                "div",
                { class: "item-main" },
                el("div", { class: "item-title", text: c.label }),
                el("div", {
                  class: "item-sub wrap",
                  text: `${plural(c.markets, "mercado", "mercados")} · ${c.wins} ganados · ${c.losses} perdidos · rendimiento ${fmt.pct(c.return)}`,
                }),
              ),
              el("div", { class: "item-side" }, el("div", { class: "big " + signClass(c.net), text: fmt.money(c.net, true) })),
            ),
          ),
        ),
      ),
    );
  }
  if (r.recent.length) {
    const list = el("div", { class: "list" }, r.recent.slice(0, 10).map(resultItem));
    const card = el("div", { class: "card" }, el("h2", { text: "Últimos mercados cerrados" }), list);
    if (r.recent.length > 10) {
      const more = el("button", {
        class: "btn full",
        type: "button",
        text: `Ver ${r.recent.length - 10} más`,
        onclick: () => {
          list.append(...r.recent.slice(10).map(resultItem));
          more.remove();
        },
      });
      card.append(more);
    }
    cards.push(card);
  }
  cards.push(
    el("p", {
      class: "muted small center",
      text:
        r.scope === "bot"
          ? "Solo cuenta lo que compró el bot. Lo que compres tú a mano está en «Toda la cuenta»."
          : "Cuenta todo lo de tu cuenta de Kalshi, también lo que compres a mano.",
    }),
  );
  $("#results-body").replaceChildren(...cards);
  state.resultsChart = null;
  if (t.markets) {
    drawDailyChart(chartHost, r.by_day);
    state.resultsChart = { host: chartHost, days: r.by_day, width: chartHost.clientWidth };
  }
}

function resultItem(m) {
  const side = m.side === "ambos" ? null : m.side;
  const parts = [`${fmt.qty(m.contracts)} contratos`, `pagaste ${fmt.money(m.cost)}`, dayLabel(m.date, { day: "numeric", month: "short" })];
  const outcome = m.sold_early ? "vendido antes" : m.net > 0 ? "ganado" : m.net < 0 ? "perdido" : "sin cambio";
  return el(
    "div",
    { class: "item tappable", onclick: () => openMarket(m.ticker) },
    el(
      "div",
      { class: "item-main" },
      el("div", { class: "item-title", text: m.title }),
      el(
        "div",
        { class: "item-sub wrap" },
        side ? sideBadge(side) : null,
        side ? " " : null,
        [m.subtitle, ...parts].filter(Boolean).join(" · "),
      ),
    ),
    el(
      "div",
      { class: "item-side" },
      el("div", { class: "big " + signClass(m.net), text: fmt.money(m.net, true) }),
      el("div", { class: "tag", text: outcome }),
    ),
  );
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
  if (!c || state.view !== "results" || !c.host.isConnected || c.host.clientWidth === c.width) return;
  c.width = c.host.clientWidth;
  drawDailyChart(c.host, c.days);
});

document.addEventListener("DOMContentLoaded", init);
