"""Kali: el bot de Kalshi de verdad, visto desde el pueblo (solo se lee su panel)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from ..names import cents, market_name, money, plural, quantity
from ..sources.panel import PanelClient
from .base import Agent

FILL = re.compile(
    r"^LLENADO( \(salida\)| \(fuera del bot\))? (COMPRA|VENDE) YES ([\d.]+) @ ([\d.]+) (\S+) \((maker|taker)"
)
HALT = re.compile(r"^FRENO DE EMERGENCIA: (.+)$")
ORDER = re.compile(r"^ORDEN (COMPRA|VENDE) YES ([\d.]+) @ ([\d.]+) .*?\] (\S+)(?: \|.*\| (.*))?$")
CANCEL = re.compile(r"^CANCELADA (\S+)")
RISK = re.compile(r"^\[(\S+)\] riesgo: (.+)$")
FOLLOWING = re.compile(r"^Mercados seguidos \((\d+)\)")
BALANCE = re.compile(r"^Saldo disponible \$([\d.]+)")
SCAN_WORDS = {
    "active": "con apuesta",
    "decided": "ya decididos",
    "empty_book": "sin ofertas",
    "wide_spread": "con spread ancho",
    "position_cap": "en el límite",
    "no_favorite": "sin favorito",
    "other_side": "del otro lado",
    "event_busy": "con el evento ocupado",
    "risk": "frenados por riesgo",
    "cooldown": "en espera",
    "no_signal": "sin señal",
    "book_error": "sin poder leer el libro",
    "error": "con error",
    "exchange_paused": "con el exchange parado",
}


def dec(value, default: Optional[Decimal] = None) -> Optional[Decimal]:
    try:
        return Decimal(str(value)) if value not in (None, "") else default
    except Exception:  # noqa: BLE001
        return default


def fill_words(message: str) -> Optional[tuple]:
    """("compra"|"cobro", contratos, "SÍ"|"NO", precio de ese lado, ticker) de una línea LLENADO del bot."""
    m = FILL.match(message)
    if not m or m.group(1) == " (fuera del bot)":
        return None
    leaving = m.group(1) == " (salida)"
    buys_yes = m.group(2) == "COMPRA"
    count, yes_price = Decimal(m.group(3)), Decimal(m.group(4))
    # Al salir, comprar SÍ cierra un NO y vender SÍ cierra un SÍ.
    yes_side = buys_yes != leaving
    side_price = yes_price if yes_side else 1 - yes_price
    return ("cobro" if leaving else "compra", count, "SÍ" if yes_side else "NO", side_price, m.group(5))


def log_activity(message: str, names: dict) -> Optional[tuple]:
    """(sala, qué hace) de una línea del registro del bot, o None si no dice nada nuevo."""
    fill = fill_words(message)
    if fill:
        what, count, side, side_price, ticker = fill
        verb = "cobra" if what == "cobro" else "compra"
        return "mercado", f"{verb} {quantity(count)} {side} a {cents(side_price)} · {names.get(ticker, ticker)}"
    m = ORDER.match(message)
    if m:
        buys_yes, ticker = m.group(1) == "COMPRA", m.group(4)
        count, yes_price = Decimal(m.group(2)), Decimal(m.group(3))
        reason = (m.group(5) or "").lower()
        side, side_price = ("SÍ", yes_price) if buys_yes else ("NO", 1 - yes_price)
        if "cobrar" in reason or "cortar" in reason:
            sold = "NO" if buys_yes else "SÍ"
            return "mercado", f"vende su {sold} · {names.get(ticker, ticker)}"
        return "mercado", f"orden: {quantity(count)} {side} a {cents(side_price)} · {names.get(ticker, ticker)}"
    m = CANCEL.match(message)
    if m:
        return "mercado", f"retira una orden · {names.get(m.group(1), m.group(1))}"
    m = HALT.match(message)
    if m:
        return "puente", "¡freno de emergencia!"
    m = RISK.match(message)
    if m:
        return "puente", f"riesgo: {m.group(2)}"
    if message.startswith("Error de la API de Kalshi"):
        return "puente", "Kalshi da un error"
    m = FOLLOWING.match(message)
    if m:
        return "laboratorio", f"sigue {plural(int(m.group(1)), 'mercado', 'mercados')}"
    m = BALANCE.match(message)
    if m:
        return "boveda", f"saldo {money(Decimal(m.group(1)))}"
    return None


def scan_words(scan: Optional[dict]) -> Optional[str]:
    """«mira 35 mercados · 33 ya decididos» a partir del resumen de la última vuelta del bot."""
    if not scan or not scan.get("total"):
        return None
    reasons = sorted((scan.get("reasons") or {}).items(), key=lambda kv: -kv[1])
    text = f"mira {plural(int(scan['total']), 'mercado', 'mercados')}"
    if reasons:
        key, n = reasons[0]
        text += f" · {n} {SCAN_WORDS.get(key, key)}"
    return text


class Kali(Agent):
    id = "kali"
    name = "Kali"
    role = "Opera en Kalshi con tu dinero"
    about = (
        "Es tu bot de Kalshi de verdad: compra favoritos entre 88 y 97¢ y espera a que se decidan. "
        "Aquí solo se mira lo que hace; quien manda es su panel."
    )
    home = "mercado"
    color = "#f2c94c"
    interval = 30.0

    def __init__(self, world, panel: Optional[PanelClient], tz_minutes: Callable[[], int] = lambda: 0, clock=time.time):
        super().__init__(world, clock)
        self.panel = panel
        self.tz_minutes = tz_minutes
        self.names: dict = {}
        self.action: Optional[tuple] = None  # lo último que ha hecho de verdad en esta vuelta
        self.turn = 0

    # --- la vuelta ---------------------------------------------------------------------

    def tick(self, now: float) -> None:
        if self.panel is None:
            self.status("No veo mi panel: falta PANEL_URL y PANEL_PASSWORD", mood="sick", connected=False)
            if not self.memory.get("told_no_panel"):
                self.memory["told_no_panel"] = True
                self.say(
                    "Todavía no puedo ver mi panel. Cuando pongas PANEL_URL y PANEL_PASSWORD os cuento lo que hago.",
                    to="tu",
                    kind="alert",
                )
            return
        status = self.panel.status()
        bot = status.get("bot") or {}
        state = bot.get("state", "stopped")
        self._state_change(state, bot.get("halted_reason"))
        self.action = None
        self.turn += 1
        self._read_logs()
        memory = self.memory
        if now - memory.get("positions_at", 0) >= 55 or "positions" not in memory:
            memory["positions"] = [self._position(p) for p in self.panel.positions()]
            memory["positions_at"] = now
        if now - memory.get("results_at", 0) >= 290 or "today" not in memory:
            self._read_results(self.panel.results(days=2, tz_minutes=self.tz_minutes()))
            memory["results_at"] = now

        balance = status.get("balance") or {}
        risk = status.get("risk") or {}
        today = memory.get("today") or {}
        net = dec(today.get("net"), Decimal(0))
        positions = memory.get("positions", [])
        if state == "running":
            markets = plural(len(bot.get("markets") or []), "mercado", "mercados")
            text = f"Trabajando · {markets} · hoy {money(net, sign=True)}"
            mood = "happy" if net > 0 else "sad" if net < 0 else "ok"
        elif state == "halted":
            text, mood = f"Frenada: {bot.get('halted_reason') or 'freno de emergencia'}", "sick"
        else:
            text, mood = "En pausa (descansando)", "sleep"
        if bot.get("consecutive_errors"):
            text, mood = "Kalshi no me contesta bien, reintento", "sick"
        self._move(state, bot, positions, balance)
        self.status(
            text,
            mood=mood,
            connected=True,
            state=state,
            mode=bot.get("mode"),
            markets=bot.get("markets") or [],
            scan=bot.get("scan"),
            balance=balance,
            session_pnl=status.get("session_pnl"),
            max_loss=risk.get("max_session_loss_dollars"),
            positions=positions,
            today=today,
            yesterday=memory.get("yesterday") or {},
            totals=memory.get("totals") or {},
        )

    def _move(self, state: str, bot: dict, positions: list, balance: dict) -> None:
        """A qué sala va: a la de lo último que ha hecho; si no hay nada nuevo, hace su ronda."""
        if state == "halted":
            self.doing("puente", "frenada por el freno de emergencia")
        elif state != "running":
            self.doing("puente", "en pausa")
        elif self.action:
            self.doing(*self.action)
        elif self.turn % 6 == 0 and balance.get("equity") is not None:
            self.doing("boveda", f"cuenta el saldo: {money(dec(balance.get('equity'), Decimal(0)))}")
        elif self.turn % 2 == 0 and positions:
            self.doing("mercado", f"vigila {plural(len(positions), 'apuesta abierta', 'apuestas abiertas')}")
        else:
            self.doing("laboratorio", scan_words(bot.get("scan")) or "busca favoritos")

    def talk(self) -> str:
        bot = self.world.bot(self.id)
        detail = bot.get("detail") or {}
        if not detail.get("connected"):
            return "No veo mi panel todavía: falta conectarme (PANEL_URL y PANEL_PASSWORD)."
        today = detail.get("today") or {}
        positions = detail.get("positions") or []
        balance = (detail.get("balance") or {}).get("equity")
        parts = [
            f"Hoy llevo {money(dec(today.get('net'), Decimal(0)), sign=True)}"
            f" en {plural(int(today.get('markets') or 0), 'mercado', 'mercados')}."
        ]
        if positions:
            names = ", ".join(
                f"{p['name']} ({p['side']}, {round(float(p['chance'] or 0) * 100)} %)" for p in positions[:3]
            )
            many = "s" if len(positions) != 1 else ""
            parts.append(f"Tengo {len(positions)} apuesta{many} abierta{many}: {names}.")
        else:
            parts.append("Ahora no tengo apuestas abiertas.")
        if balance is not None:
            parts.append(f"Saldo: {money(dec(balance, Decimal(0)))}.")
        return " ".join(parts)

    # --- lo que lee del panel ------------------------------------------------------------

    def _state_change(self, state: str, reason: Optional[str]) -> None:
        before = self.memory.get("state")
        self.memory["state"] = state
        if before is None or before == state:
            return
        if state == "running":
            self.say("¡Vuelvo al trabajo! Ya estoy mirando mercados.")
        elif state == "halted":
            self.say(f"Me he frenado: {reason or 'freno de emergencia'}.", to="tu", kind="alert")
        else:
            self.say("Me han puesto en pausa. Me voy a descansar.")

    def _read_logs(self) -> None:
        # Se pide todo lo que guarda el panel y se descarta lo ya visto (por hora y texto): así un
        # reinicio del panel, que vuelve a numerar y recarga líneas viejas, no repite nada.
        lines = self.panel.logs(after=0)
        seen = list(self.memory.get("seen_logs") or [])
        known = set(seen)
        first_time = not self.memory.get("logs_ready")
        fresh = []
        for line in lines:
            key = f"{str(line.get('ts', ''))[:19]}|{line.get('message', '')}"
            if key in known:
                continue
            known.add(key)
            seen.append(key)
            if not first_time:
                fresh.append(line)
        self.memory["seen_logs"] = seen[-800:]
        self.memory["logs_ready"] = True
        fills = [(line, fill_words(line["message"])) for line in fresh]
        tickers = sorted({f[4] for _, f in fills if f})
        self._learn_names(tickers)
        for line, _fill in fills:
            act = log_activity(line["message"], self.names)
            if act:
                self.action = act
        for line, fill in fills:
            if fill:
                what, count, side, price, ticker = fill
                verb = "He cobrado" if what == "cobro" else "He comprado"
                self.say(
                    f"{verb} {quantity(count)} {side} a {cents(price)} · {self.names.get(ticker, ticker)}",
                    kind="trade",
                    ticker=ticker,
                )
                continue
            halt = HALT.match(line["message"])
            if halt:
                self.say(f"¡Freno de emergencia! {halt.group(1)}", to="tu", kind="alert")

    def _read_results(self, results: dict) -> None:
        memory = self.memory
        memory["today"] = results.get("today_totals") or {}
        memory["yesterday"] = results.get("yesterday_totals") or {}
        memory["totals"] = results.get("totals") or {}
        seen = set(memory.get("seen_closes") or [])
        first_time = "seen_closes" not in memory
        fresh = []
        for row in reversed(results.get("recent") or []):
            key = f"{row['ticker']}|{row.get('closed_at')}"
            if key in seen:
                continue
            seen.add(key)
            if not first_time:
                fresh.append(row)
        memory["seen_closes"] = sorted(seen)[-500:]
        for row in fresh:
            net = dec(row.get("net"), Decimal(0))
            name = market_name(row["ticker"], row.get("title", ""), row.get("subtitle", ""))
            how = "Cobrado antes" if row.get("sold_early") else "Mercado cerrado"
            self.say(f"{how}: {money(net, sign=True)} · {name}", kind="trade", ticker=row["ticker"], net=str(net))
            self.action = ("boveda", f"{'gana' if net >= 0 else 'pierde'} {money(net, sign=True)} · {name}")

    def _position(self, p: dict) -> dict:
        name = market_name(p["ticker"], p.get("title", ""), p.get("subtitle", ""))
        self.names[p["ticker"]] = name
        contracts = dec(p.get("contracts"), Decimal(0))
        cost = dec(p.get("exposure"), Decimal(0))
        return {
            "ticker": p["ticker"],
            "name": name,
            "title": p.get("title", ""),
            "subtitle": p.get("subtitle", ""),
            "side": "SÍ" if p.get("side") == "yes" else "NO",
            "contracts": str(contracts),
            "cost": str(cost),
            "avg_price": str((cost / contracts).quantize(Decimal("0.001"))) if contracts else None,
            "chance": p.get("chance"),
            "value": p.get("value"),
            "payout": p.get("payout"),
            "hours_to_close": p.get("hours_to_close"),
        }

    def _learn_names(self, tickers: list) -> None:
        missing = [t for t in tickers if t not in self.names]
        if not missing:
            return
        try:
            labels = self.panel.labels(missing)
        except Exception:  # noqa: BLE001 - sin nombres se enseña el ticker
            labels = {}
        for ticker in missing:
            info = labels.get(ticker) or {}
            self.names[ticker] = market_name(ticker, info.get("title", ""), info.get("subtitle", ""))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
