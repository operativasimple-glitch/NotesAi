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
    return (n > 0 ? "+" : n < 0 ? "−" : "") + Math.abs(n).toFixed(digits) + "%";
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
  $("#market-search").addEventListener("submit", searchMarkets);
  $("#btn-scan").addEventListener("click", runScan);
  $("#btn-research").addEventListener("click", runResearch);
  $("#btn-save-settings").addEventListener("click", saveSettings);
  $("#btn-logout").addEventListener("click", logout);
  $("#btn-save-cred").addEventListener("click", saveCredentials);
  $("#btn-del-cred").addEventListener("click", deleteCredentials);
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
  { key: "closing_within_hours", label: "Buscar en todos los mercados que cierran en (horas)", type: "number", help: "0 = no buscar; usa solo series, eventos o mercados fijos." },
  { key: "max_markets", label: "Mercados máximos a la vez", type: "number" },
  { key: "max_markets_per_event", label: "Máximo por evento", type: "number", help: "1 evita apostar varias veces a lo mismo." },
  { key: "min_volume_24h", label: "Volumen mínimo en 24 h (contratos)", type: "number" },
  { key: "min_hours_to_close", label: "Ignorar si cierra en menos de (horas)", type: "number" },
  { key: "series", label: "Series", type: "list", help: "Separadas por comas, p. ej. KXHIGHNY" },
  { key: "tickers", label: "Mercados fijos", type: "list", help: "Tickers separados por comas." },
  { key: "exclude_series", label: "Series a evitar", type: "list" },
];

const RISK_FIELDS = [
  { key: "max_order_contracts", label: "Contratos por orden (máximo)", type: "number" },
  { key: "max_position_per_market", label: "Contratos por mercado (máximo)", type: "number" },
  { key: "max_total_exposure_dollars", label: "Dinero comprometido máximo", type: "money" },
  { key: "max_session_loss_dollars", label: "Pérdida máxima de la sesión", type: "money", help: "Al alcanzarla, cancela todo y se detiene. 0 = desactivado." },
  { key: "min_price", label: "Precio mínimo", type: "price" },
  { key: "max_price", label: "Precio máximo", type: "price" },
  { key: "min_minutes_to_close", label: "No operar en los últimos (minutos)", type: "number" },
];

function fieldControl(def, value) {
  let input;
  let read;
  if (def.type === "bool") {
    input = el("input", { type: "checkbox" });
    input.checked = value === true || value === "true";
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
  if (!c || c.env === env) return;
  if (env === "prod") {
    const ok = await confirmDialog({
      title: "Cambiar a dinero real",
      message: "A partir de ahora el panel y el bot usarán tu cuenta real de Kalshi (necesitas una API key creada en kalshi.com).",
      confirmLabel: "Cambiar a real",
      danger: true,
    });
    if (!ok.ok) return;
  }
  try {
    await api("/api/env", { method: "POST", body: { env } });
    toast(env === "prod" ? "Entorno: REAL" : "Entorno: DEMO", "success");
    await loadSettings();
    refreshStatus().catch(() => {});
  } catch (err) {
    toast(err.message, "error");
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
  } catch (err) {
    toast(err.message, "error");
  }
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

document.addEventListener("DOMContentLoaded", init);
