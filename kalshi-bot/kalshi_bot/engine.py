"""Motor del bot: el bucle que une datos, estrategia, riesgo y ejecución.

En cada vuelta:
  1. Comprueba que el exchange esté operando.
  2. Lee saldo y posiciones; corta todo si la pérdida de la sesión supera
     el límite.
  3. Para cada mercado seguido: lee el libro, pregunta a la estrategia qué
     órdenes quiere, las pasa por el gestor de riesgo y reconcilia con las
     órdenes que el bot ya tiene en reposo (cancela las que sobran y crea
     las que faltan).

El bot solo toca órdenes cuyo client_order_id empieza por su prefijo, así
que no cancela órdenes que pongas a mano desde la web.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from .client import KalshiAPIError, KalshiClient, new_client_order_id
from .models import ZERO, Balance, Market, Order, OrderIntent, fmt_count, fmt_price, to_decimal
from .risk import RiskManager
from .strategies.base import MarketContext, Strategy

log = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    poll_interval: float = 10.0
    cancel_on_exit: bool = True
    order_prefix: str = "kb"
    order_ttl_seconds: int = 600  # las órdenes GTC caducan solas si el bot muere; 0 = nunca
    taker_cooldown_seconds: float = 30.0
    requote_tolerance: Decimal = ZERO
    max_consecutive_errors: int = 10
    paper_cash: Decimal = Decimal("1000")  # saldo virtual si no hay API key
    # Selección de mercados
    tickers: list = field(default_factory=list)
    series: list = field(default_factory=list)
    events: list = field(default_factory=list)
    max_markets: int = 10
    min_hours_to_close: float = 0.0
    max_hours_to_close: float = 0.0  # 0 = sin límite
    min_volume_24h: Decimal = ZERO
    refresh_markets_minutes: float = 5.0


class Journal:
    """Registro JSONL de todo lo que el bot envía al exchange."""

    def __init__(self, path: Optional[Path]):
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: str, **data) -> None:
        if self.path is None:
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **data}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


def intent_fields(intent: OrderIntent) -> dict:
    return {
        "ticker": intent.ticker,
        "side": intent.side,
        "price": fmt_price(intent.price),
        "count": fmt_count(intent.count),
        "time_in_force": intent.time_in_force,
        "post_only": intent.post_only,
        "reason": intent.reason,
    }


# --------------------------------------------------------------------------
# Ejecutores: real y simulado
# --------------------------------------------------------------------------


class Executor(ABC):
    dry_run = False

    @abstractmethod
    def resting_orders(self) -> list:
        """Órdenes del bot que siguen en el libro."""

    @abstractmethod
    def place(self, intent: OrderIntent) -> dict: ...

    @abstractmethod
    def cancel(self, order: Order) -> None: ...


class LiveExecutor(Executor):
    def __init__(
        self,
        client: KalshiClient,
        prefix: str,
        journal: Journal,
        ttl_seconds: int = 0,
        clock: Callable[[], float] = time.time,
    ):
        self.client = client
        self.prefix = prefix
        self.journal = journal
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self.order_ids: set = set()  # órdenes creadas o vistas por el bot

    def resting_orders(self) -> list:
        mine = [o for o in self.client.get_orders(status="resting") if o.client_order_id.startswith(self.prefix + "-")]
        self.order_ids.update(o.order_id for o in mine)
        return mine

    def place(self, intent: OrderIntent) -> dict:
        client_order_id = new_client_order_id(self.prefix)
        expiration = int(self.clock()) + self.ttl_seconds if self.ttl_seconds and intent.is_resting else None
        resp = self.client.create_order(intent, client_order_id, expiration)
        result = resp.get("order", resp)
        if result.get("order_id"):
            self.order_ids.add(result["order_id"])
        log.info(
            "ORDEN %s | id=%s llenado=%s pendiente=%s | %s",
            intent.describe(),
            result.get("order_id"),
            result.get("fill_count", result.get("fill_count_fp", "?")),
            result.get("remaining_count", result.get("remaining_count_fp", "?")),
            intent.reason,
        )
        self.journal.record("place", client_order_id=client_order_id, response=result, **intent_fields(intent))
        return result

    def cancel(self, order: Order) -> None:
        self.client.cancel_order(order.order_id, order.ticker)
        log.info(
            "CANCELADA %s %s %s @ %s", order.ticker, order.side, fmt_count(order.remaining), fmt_price(order.price)
        )
        self.journal.record("cancel", order_id=order.order_id, ticker=order.ticker)


class DryRunExecutor(Executor):
    """No envía nada: registra lo que haría y simula las órdenes en reposo
    (sin llenados) para que el log muestre solo los cambios."""

    dry_run = True

    def __init__(self, prefix: str, journal: Journal):
        self.prefix = prefix
        self.journal = journal
        self._orders: dict = {}

    def resting_orders(self) -> list:
        return list(self._orders.values())

    def place(self, intent: OrderIntent) -> dict:
        client_order_id = new_client_order_id(self.prefix)
        order_id = "sim-" + client_order_id
        if intent.is_resting:
            self._orders[order_id] = Order(
                order_id, client_order_id, intent.ticker, intent.side, intent.price, intent.count
            )
        log.info("[SIMULACIÓN] %s | %s", intent.describe(), intent.reason)
        self.journal.record("place", dry_run=True, client_order_id=client_order_id, **intent_fields(intent))
        return {"order_id": order_id, "client_order_id": client_order_id, "simulated": True}

    def cancel(self, order: Order) -> None:
        self._orders.pop(order.order_id, None)
        log.info(
            "[SIMULACIÓN] cancelar %s %s %s @ %s",
            order.ticker,
            order.side,
            fmt_count(order.remaining),
            fmt_price(order.price),
        )
        self.journal.record("cancel", dry_run=True, order_id=order.order_id, ticker=order.ticker)


def reconcile(existing: list, desired: list, tolerance: Decimal = ZERO) -> tuple:
    """Compara órdenes en reposo con las deseadas.

    Devuelve (mantener, cancelar, crear). Una orden existente sirve si tiene
    el mismo lado, un precio dentro de `tolerance` y la misma cantidad
    pendiente.
    """
    unmatched = list(existing)
    keep: list = []
    to_place: list = []
    for intent in desired:
        match = next(
            (
                o
                for o in unmatched
                if o.side == intent.side and abs(o.price - intent.price) <= tolerance and o.remaining == intent.count
            ),
            None,
        )
        if match is not None:
            keep.append(match)
            unmatched.remove(match)
        else:
            to_place.append(intent)
    return keep, unmatched, to_place


# --------------------------------------------------------------------------
# Bot
# --------------------------------------------------------------------------


class Bot:
    def __init__(
        self,
        client: KalshiClient,
        strategy: Strategy,
        risk: RiskManager,
        executor: Executor,
        config: EngineConfig,
        *,
        env_name: str = "",
        journal: Optional[Journal] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self.strategy = strategy
        self.risk = risk
        self.executor = executor
        self.cfg = config
        self.env_name = env_name
        self.journal = journal or Journal(None)
        self._now = now
        self._sleep = sleep
        self._monotonic = monotonic

        self.markets: dict = {}
        self.halted_reason: Optional[str] = None
        self._stop = False
        self._signals = 0
        self._markets_refreshed_at: Optional[float] = None
        self._cooldown_until: dict = {}
        self._consecutive_errors = 0
        self._trading_paused = False
        self._seen_fills: dict = {}  # fill_id -> None, en orden de llegada
        self._fills_since: Optional[int] = None

    # --- ciclo de vida -------------------------------------------------------

    def stop(self) -> None:
        self._stop = True

    def run(self, max_ticks: Optional[int] = None) -> None:
        self._install_signal_handlers()
        self.startup()
        ticks = 0
        try:
            while not self._stop:
                started = self._monotonic()
                try:
                    self.tick()
                    self._consecutive_errors = 0
                except KalshiAPIError as exc:
                    self._consecutive_errors += 1
                    log.error("Error de la API de Kalshi: %s", exc)
                    if exc.is_auth_error:
                        self.halt("Kalshi rechazó las credenciales (revisa KALSHI_API_KEY_ID, la clave y el entorno)")
                except Exception:
                    self._consecutive_errors += 1
                    log.exception("Error inesperado en la vuelta del bot")
                if not self._stop and self._consecutive_errors >= self.cfg.max_consecutive_errors:
                    self.halt(f"{self._consecutive_errors} vueltas seguidas con errores")
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                self._sleep_until(started + self.cfg.poll_interval)
        finally:
            self.shutdown()

    def startup(self) -> None:
        mode = "SIMULACIÓN (no se envía ninguna orden)" if self.executor.dry_run else "EN VIVO (envía órdenes)"
        log.info(
            "Arrancando bot | entorno=%s | modo=%s | estrategia=%s", self.env_name or "?", mode, self.strategy.name
        )
        status = self.client.get_exchange_status()
        log.info(
            "Exchange: activo=%s, trading=%s",
            "sí" if status.get("exchange_active") else "no",
            "sí" if status.get("trading_active") else "no",
        )
        if self.client.authenticated:
            balance = self.client.get_balance()
            log.info("Saldo disponible $%.2f | valor del portafolio $%.2f", balance.cash, balance.portfolio_value)
            self.risk.start_session(balance.equity)
            self._fills_since = int(self._now().timestamp())
        else:
            log.warning(
                "Sin API key: se simula con saldo virtual de $%s y sin posiciones. Solo se leen datos públicos.",
                self.cfg.paper_cash,
            )
            self.risk.start_session(self.cfg.paper_cash)
        self.strategy.on_start()
        existing = self.executor.resting_orders()
        if existing:
            log.info("Hay %d órdenes previas del bot en el libro; se gestionarán", len(existing))

    def shutdown(self) -> None:
        if self.cfg.cancel_on_exit:
            self.cancel_all_bot_orders()
        log.info("Bot detenido%s", f": {self.halted_reason}" if self.halted_reason else "")

    def halt(self, reason: str) -> None:
        """Freno de emergencia: cancela las órdenes del bot y detiene el bucle."""
        log.error("FRENO DE EMERGENCIA: %s", reason)
        self.halted_reason = reason
        self.journal.record("halt", reason=reason)
        self._stop = True
        self.cancel_all_bot_orders()

    def cancel_all_bot_orders(self) -> None:
        try:
            orders = self.executor.resting_orders()
        except Exception as exc:  # noqa: BLE001 - en apagado, mejor seguir
            log.error("No se pudieron leer las órdenes del bot para cancelarlas: %s", exc)
            return
        for order in orders:
            self._safe_cancel(order)

    # --- una vuelta ----------------------------------------------------------

    def tick(self) -> None:
        now = self._now()
        self.strategy.refresh()

        status = self.client.get_exchange_status()
        if not status.get("trading_active", False):
            if not self._trading_paused:
                log.warning("El exchange no está operando ahora (trading_active=false); esperando...")
                self._trading_paused = True
            return
        if self._trading_paused:
            log.info("El exchange volvió a operar")
            self._trading_paused = False

        if self.client.authenticated:
            balance = self.client.get_balance()
            positions = self.client.get_positions()
        else:
            balance = Balance(self.cfg.paper_cash, ZERO)
            positions = {}

        reason = self.risk.check_loss(balance.equity)
        if reason:
            self.halt(reason)
            return

        self._refresh_markets_if_needed(now)
        if self.client.authenticated and not self.executor.dry_run:
            self._log_new_fills()

        resting = self.executor.resting_orders()
        by_ticker: dict = {}
        for order in resting:
            by_ticker.setdefault(order.ticker, []).append(order)

        for ticker in [t for t in by_ticker if t not in self.markets]:
            log.info("Cancelando órdenes del bot en %s (ya no está en la lista de mercados)", ticker)
            for order in by_ticker.pop(ticker):
                self._safe_cancel(order)

        positions_exposure = sum((p.exposure for p in positions.values()), ZERO)
        resting_collateral = {t: sum((o.collateral() for o in os), ZERO) for t, os in by_ticker.items()}

        for ticker, market in self.markets.items():
            if self._stop:
                break
            own = by_ticker.get(ticker, [])
            position = positions[ticker].position if ticker in positions else ZERO

            block = self.risk.market_block_reason(market, now)
            if block:
                if own:
                    log.info("[%s] %s: cancelando %d órdenes", ticker, block, len(own))
                    for order in own:
                        self._safe_cancel(order)
                    resting_collateral[ticker] = ZERO
                continue

            try:
                book = self.client.get_orderbook(ticker)
            except KalshiAPIError as exc:
                log.warning("[%s] no se pudo leer el libro: %s", ticker, exc)
                continue

            ctx = MarketContext(
                market=market,
                book=book,
                book_ex_own=book.without_orders(own),
                position=position,
                own_orders=own,
                now=now,
                cash=balance.cash,
            )
            try:
                intents = [i for i in (self.strategy.on_market(ctx) or []) if i is not None]
            except Exception:
                log.exception("[%s] la estrategia falló; no se tocan órdenes en este mercado", ticker)
                continue
            foreign = [i for i in intents if i.ticker != ticker]
            if foreign:
                log.warning("[%s] se ignoran %d órdenes de la estrategia para otros mercados", ticker, len(foreign))
                intents = [i for i in intents if i.ticker == ticker]

            intents = self._apply_cooldown(ticker, intents, now)
            committed = positions_exposure + sum((c for t, c in resting_collateral.items() if t != ticker), ZERO)
            approved, notes = self.risk.filter_intents(intents, position=position, committed_exposure=committed)
            for note in notes:
                log.info("[%s] riesgo: %s", ticker, note)

            self._execute(ticker, own, approved, now)
            resting_collateral[ticker] = sum((i.count * i.cost_per_contract() for i in approved if i.is_resting), ZERO)

    def _execute(self, ticker: str, own: list, approved: list, now: datetime) -> None:
        resting_wanted = [i for i in approved if i.is_resting]
        takers = [i for i in approved if not i.is_resting]
        _, to_cancel, to_place = reconcile(own, resting_wanted, self.cfg.requote_tolerance)
        # Primero cancelar (libera saldo y evita cruzarte contigo mismo).
        for order in to_cancel:
            self._safe_cancel(order)
        for intent in to_place + takers:
            self._safe_place(intent)
        if takers and self.cfg.taker_cooldown_seconds > 0:
            self._cooldown_until[ticker] = now + timedelta(seconds=self.cfg.taker_cooldown_seconds)

    def _apply_cooldown(self, ticker: str, intents: list, now: datetime) -> list:
        until = self._cooldown_until.get(ticker)
        if until is None or now >= until:
            return intents
        kept = [i for i in intents if i.is_resting]
        if len(kept) != len(intents):
            log.debug("[%s] en enfriamiento hasta %s: se omiten órdenes IOC/FOK", ticker, until.isoformat())
        return kept

    def _safe_place(self, intent: OrderIntent) -> None:
        try:
            self.executor.place(intent)
        except KalshiAPIError as exc:
            log.warning("No se pudo crear %s: %s", intent.describe(), exc)
            self.journal.record("place_error", error=str(exc), **intent_fields(intent))
            if exc.is_auth_error:
                raise

    def _safe_cancel(self, order: Order) -> None:
        try:
            self.executor.cancel(order)
        except KalshiAPIError as exc:
            # Lo normal es que ya se haya llenado o cancelado.
            log.info("No se pudo cancelar %s (%s): %s", order.order_id, order.ticker, exc)

    # --- mercados ------------------------------------------------------------

    def _refresh_markets_if_needed(self, now: datetime) -> None:
        current = self._monotonic()
        interval = self.cfg.refresh_markets_minutes * 60
        if self._markets_refreshed_at is not None and current - self._markets_refreshed_at < interval:
            return
        self.markets = self.load_markets(now)
        self._markets_refreshed_at = current
        if self.markets:
            log.info("Mercados seguidos (%d): %s", len(self.markets), ", ".join(self.markets))
        else:
            log.warning(
                "No hay mercados que seguir: revisa [markets] en config.toml "
                "(tickers, series o events) o los valores justos de la estrategia"
            )

    def load_markets(self, now: datetime) -> dict:
        explicit = list(dict.fromkeys(list(self.cfg.tickers) + list(self.strategy.suggested_tickers())))
        selected: dict = {}
        for start in range(0, len(explicit), 50):
            chunk = explicit[start : start + 50]
            for market in self.client.get_markets(status=None, tickers=chunk):
                selected[market.ticker] = market
        missing = [t for t in explicit if t not in selected]
        if missing:
            log.warning("Tickers no encontrados en Kalshi: %s", ", ".join(missing))

        discovered: list = []
        for series in self.cfg.series:
            discovered += self.client.get_markets(status="open", series_ticker=series)
        for event in self.cfg.events:
            discovered += self.client.get_markets(status="open", event_ticker=event)
        candidates = [m for m in discovered if m.ticker not in selected and self._passes_filters(m, now)]
        candidates.sort(key=lambda m: m.volume_24h, reverse=True)
        for market in candidates[: max(0, self.cfg.max_markets)]:
            selected.setdefault(market.ticker, market)
        return selected

    def _passes_filters(self, market: Market, now: datetime) -> bool:
        if not market.is_active:
            return False
        hours = market.hours_to_close(now)
        if hours is not None:
            if hours < self.cfg.min_hours_to_close:
                return False
            if self.cfg.max_hours_to_close and hours > self.cfg.max_hours_to_close:
                return False
        return market.volume_24h >= self.cfg.min_volume_24h

    # --- llenados ----------------------------------------------------------------

    def _log_new_fills(self) -> None:
        try:
            fills = self.client.get_fills(min_ts=self._fills_since, limit=100)
        except KalshiAPIError as exc:
            log.debug("No se pudieron leer los llenados: %s", exc)
            return
        bot_orders = getattr(self.executor, "order_ids", set())
        for fill in reversed(fills):
            fill_id = fill.get("fill_id") or fill.get("trade_id")
            if not fill_id or fill_id in self._seen_fills:
                continue
            self._seen_fills[fill_id] = None
            from_bot = fill.get("order_id") in bot_orders
            side = fill.get("book_side") or ""
            verb = "COMPRA YES" if side == "bid" else "VENDE YES" if side == "ask" else side
            count = to_decimal(fill.get("count_fp"), to_decimal(fill.get("count"), ZERO))
            log.log(
                logging.INFO if from_bot else logging.DEBUG,
                "LLENADO%s %s %s @ %s %s (%s, comisión $%s)",
                "" if from_bot else " (fuera del bot)",
                verb,
                fmt_count(count),
                fill.get("yes_price_dollars", "?"),
                fill.get("ticker", "?"),
                "taker" if fill.get("is_taker") else "maker",
                fill.get("fee_cost", "?"),
            )
            self.journal.record("fill", from_bot=from_bot, fill=fill)
            ts = fill.get("ts")
            # Pedimos desde un segundo antes: los repetidos se descartan por fill_id.
            if isinstance(ts, int) and (self._fills_since is None or ts - 1 > self._fills_since):
                self._fills_since = ts - 1
        if len(self._seen_fills) > 5000:
            self._seen_fills = dict.fromkeys(list(self._seen_fills)[-1000:])

    # --- utilidades --------------------------------------------------------------

    def _sleep_until(self, deadline: float) -> None:
        while not self._stop:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                return
            self._sleep(min(remaining, 0.5))

    def _install_signal_handlers(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        def handler(signum, frame):  # noqa: ARG001
            self._signals += 1
            if self._signals > 1:
                raise KeyboardInterrupt
            log.info("Señal de parada recibida: terminando la vuelta actual y cancelando órdenes del bot...")
            self._stop = True

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
