"""Motor del bot: el bucle que une datos, estrategia, riesgo y ejecución.

En cada vuelta:
  1. Comprueba que el exchange esté operando.
  2. Lee saldo y posiciones; corta todo si la pérdida de la sesión supera
     el límite.
  3. Para cada mercado seguido: lee el libro, pregunta a la estrategia qué
     órdenes quiere, las pasa por el gestor de riesgo y reconcilia con las
     órdenes que el bot ya tiene en reposo (cancela las que sobran y crea
     las que faltan).
  4. Si la estrategia sale antes de tiempo (stop loss, cobrar antes), también
     vigila las posiciones de sus series en mercados que ya no sigue o que
     cierran en minutos, pero ahí solo deja órdenes que reducen la posición.

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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

from .client import KalshiAPIError, KalshiClient, new_client_order_id
from .discovery import MarketFilter, discover
from .models import ASK, BID, ZERO, Balance, Order, OrderIntent, fmt_count, fmt_price, to_decimal
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
    min_error_minutes: float = 3.0  # el freno salta si además los fallos duran este tiempo
    paper_cash: Decimal = Decimal("1000")  # saldo virtual si no hay API key
    # Selección de mercados
    tickers: list = field(default_factory=list)
    series: list = field(default_factory=list)
    events: list = field(default_factory=list)
    max_markets: int = 10
    min_hours_to_close: float = 0.0
    max_hours_to_close: float = 0.0  # 0 = sin límite
    min_volume_24h: Decimal = ZERO
    closing_within_hours: float = 0.0  # >0: buscar en todos los mercados que cierran pronto
    max_markets_per_event: int = 0  # 0 = sin límite
    exclude_series: list = field(default_factory=list)
    series_rules: dict = field(default_factory=dict)  # ajustes propios por prefijo de serie
    refresh_markets_minutes: float = 5.0

    def market_filter(self) -> MarketFilter:
        return MarketFilter(
            series=list(self.series),
            events=list(self.events),
            closing_within_hours=self.closing_within_hours,
            min_hours_to_close=self.min_hours_to_close,
            max_hours_to_close=self.max_hours_to_close,
            min_volume_24h=self.min_volume_24h,
            max_markets=self.max_markets,
            max_markets_per_event=self.max_markets_per_event,
            exclude_series=list(self.exclude_series),
            series_rules={k: dict(v) for k, v in self.series_rules.items()},
        )


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


def event_of(ticker: str) -> str:
    """Evento de un mercado por su ticker (todo menos el último tramo: KXHIGHNY-26OCT08-B66.5)."""
    return ticker.rsplit("-", 1)[0]


def only_reducing(intents: list, position: Decimal) -> list:
    """Deja solo las órdenes que reducen la posición (y como mucho hasta cerrarla)."""
    side = ASK if position > 0 else BID  # vender YES cierra YES; comprar YES cierra NO
    left = abs(position)
    kept = []
    for intent in intents:
        if intent.side != side or left <= 0:
            continue
        count = min(intent.count, left)
        left -= count
        kept.append(replace(intent, count=count))
    return kept


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
        self.exit_markets: dict = {}  # con posición, fuera de la lista: solo para salir
        self.halted_reason: Optional[str] = None
        self._stop = False
        self._signals = 0
        self._markets_refreshed_at: Optional[float] = None
        self._cooldown_until: dict = {}
        self._consecutive_errors = 0
        self._errors_since: Optional[float] = None
        self._trading_paused = False
        self._seen_fills: dict = {}  # fill_id -> None, en orden de llegada
        self._risk_notes: dict = {}  # ticker -> avisos del riesgo de la última vuelta
        self.on_fill: Optional[Callable[[dict], None]] = None  # el panel avisa al móvil
        self._fills_since: Optional[int] = None
        # Estado observable (lo lee el panel web desde otro hilo).
        self.running = False
        self.started_at: Optional[datetime] = None
        self.last_tick_at: Optional[datetime] = None
        self.ticks = 0
        self.last_balance: Optional[Balance] = None
        self.last_positions: dict = {}

    # --- ciclo de vida -------------------------------------------------------

    def stop(self) -> None:
        self._stop = True

    def run(self, max_ticks: Optional[int] = None) -> None:
        self._install_signal_handlers()
        self.running = True
        try:
            self.startup()
        except BaseException:
            self.running = False
            raise
        ticks = 0
        try:
            while not self._stop:
                started = self._monotonic()
                try:
                    self.tick()
                    if self._consecutive_errors:
                        log.info("La API vuelve a responder tras %d vueltas con errores", self._consecutive_errors)
                    self._consecutive_errors, self._errors_since = 0, None
                except KalshiAPIError as exc:
                    self._count_error(started)
                    log.error("Error de la API de Kalshi: %s", exc)
                    if exc.is_auth_error:
                        self.halt("Kalshi rechazó las credenciales (revisa KALSHI_API_KEY_ID, la clave y el entorno)")
                except Exception:
                    self._count_error(started)
                    log.exception("Error inesperado en la vuelta del bot")
                if not self._stop and self._should_halt_for_errors():
                    minutes = (self._monotonic() - (self._errors_since or self._monotonic())) / 60
                    self.halt(f"{self._consecutive_errors} vueltas seguidas con errores durante {minutes:.0f} min")
                ticks += 1
                if max_ticks is not None and ticks >= max_ticks:
                    break
                self._sleep_until(started + self._next_delay())
        finally:
            try:
                self.shutdown()
            finally:
                self.running = False

    def _count_error(self, started: float) -> None:
        self._consecutive_errors += 1
        if self._errors_since is None:
            self._errors_since = started

    def _should_halt_for_errors(self) -> bool:
        """Freno por errores: muchas vueltas seguidas y durante un rato (no un corte de segundos)."""
        if self._consecutive_errors < self.cfg.max_consecutive_errors or self._errors_since is None:
            return False
        return self._monotonic() - self._errors_since >= self.cfg.min_error_minutes * 60

    def _next_delay(self) -> float:
        """Con errores seguidos espera cada vez más (hasta 1 minuto) para no saturar la API."""
        if not self._consecutive_errors:
            return self.cfg.poll_interval
        return min(self.cfg.poll_interval * 2 ** min(self._consecutive_errors - 1, 6), 60.0)

    def startup(self) -> None:
        mode = "SIMULACIÓN (no se envía ninguna orden)" if self.executor.dry_run else "EN VIVO (envía órdenes)"
        log.info(
            "Arrancando bot | entorno=%s | modo=%s | estrategia=%s", self.env_name or "?", mode, self.strategy.name
        )
        self.started_at = self._now()
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
            self.last_balance = balance
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
        self.last_balance = balance
        self.last_positions = positions
        self.last_tick_at = now
        self.ticks += 1

        reason = self.risk.check_loss(balance.equity)
        if reason:
            self.halt(reason)
            return

        refreshed = self._refresh_markets_if_needed(now)
        self._update_exit_markets(positions, refreshed)
        if refreshed:  # olvida los avisos de mercados que ya no se siguen
            self._risk_notes = {
                t: n for t, n in self._risk_notes.items() if t in self.markets or t in self.exit_markets
            }
        if self.client.authenticated and not self.executor.dry_run:
            self._log_new_fills()

        resting = self.executor.resting_orders()
        by_ticker: dict = {}
        for order in resting:
            by_ticker.setdefault(order.ticker, []).append(order)

        for ticker in [t for t in by_ticker if t not in self.markets and t not in self.exit_markets]:
            log.info("Cancelando órdenes del bot en %s (ya no está en la lista de mercados)", ticker)
            for order in by_ticker.pop(ticker):
                self._safe_cancel(order)

        positions_exposure = sum((p.exposure for p in positions.values()), ZERO)
        resting_collateral = {t: sum((o.collateral() for o in os), ZERO) for t, os in by_ticker.items()}
        # Mercados de cada evento en los que ya hay dinero (posición u órdenes del bot).
        busy: dict = {}
        for t in [t for t, p in positions.items() if p.position != 0] + list(by_ticker):
            busy.setdefault(event_of(t), set()).add(t)

        for ticker, market in list(self.markets.items()) + list(self.exit_markets.items()):
            if self._stop:
                break
            own = by_ticker.get(ticker, [])
            position = positions[ticker].position if ticker in positions else ZERO

            block = self.risk.market_block_reason(market, now)
            exit_only = ticker not in self.markets
            if block or exit_only:
                # Aunque ya no se pueda comprar, se deja salir de una posición mientras el mercado opere.
                if not (market.is_active and position != 0 and self.strategy.wants_exits()):
                    if own:
                        log.info("[%s] %s: cancelando %d órdenes", ticker, block or "solo salidas", len(own))
                        for order in own:
                            self._safe_cancel(order)
                        resting_collateral[ticker] = ZERO
                    continue
                exit_only = True

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
                exposure=positions[ticker].exposure if ticker in positions else ZERO,
                exit_only=exit_only,
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
            if exit_only:
                intents = only_reducing(intents, position)
            event = market.event_ticker or event_of(ticker)
            per_event = self.risk.limits.max_positions_per_event
            if per_event and len(busy.get(event, set()) - {ticker}) >= per_event and intents:
                kept = only_reducing(intents, position)
                if len(kept) != len(intents):
                    log.debug("[%s] ya hay dinero en otro mercado de %s: solo se permite salir", ticker, event)
                intents = kept

            intents = self._apply_cooldown(ticker, intents, now)
            committed = positions_exposure + sum((c for t, c in resting_collateral.items() if t != ticker), ZERO)
            approved, notes = self.risk.filter_intents(intents, position=position, committed_exposure=committed)
            # Cada aviso se dice una vez: repetido en cada vuelta taparía el resto de la actividad.
            if tuple(notes) != self._risk_notes.get(ticker, ()):
                for note in notes:
                    log.info("[%s] riesgo: %s", ticker, note)
            if notes:
                self._risk_notes[ticker] = tuple(notes)
            else:
                self._risk_notes.pop(ticker, None)

            self._execute(ticker, own, approved, now)
            resting_collateral[ticker] = sum((i.count * i.cost_per_contract() for i in approved if i.is_resting), ZERO)
            if approved:
                busy.setdefault(event, set()).add(ticker)

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

    def _refresh_markets_if_needed(self, now: datetime) -> bool:
        current = self._monotonic()
        interval = self.cfg.refresh_markets_minutes * 60
        if self._markets_refreshed_at is not None and current - self._markets_refreshed_at < interval:
            return False
        self.markets = self.load_markets(now)
        self._markets_refreshed_at = current
        if self.markets:
            log.info("Mercados seguidos (%d): %s", len(self.markets), ", ".join(self.markets))
        else:
            log.warning(
                "No hay mercados que seguir: revisa [markets] en config.toml "
                "(tickers, series o events) o los valores justos de la estrategia"
            )
        return True

    def follows(self, ticker: str) -> bool:
        """True si el mercado es de las series, eventos o tickers que opera el bot."""
        cfg = self.cfg
        series = ticker.split("-", 1)[0].upper()
        if series in {s.upper() for s in cfg.exclude_series}:
            return False
        if ticker in cfg.tickers or ticker in self.strategy.suggested_tickers():
            return True
        if series in {s.upper() for s in cfg.series}:
            return True
        if any(ticker.startswith(event + "-") for event in cfg.events):
            return True
        return cfg.closing_within_hours > 0  # busca en todos los mercados que cierran pronto

    def _update_exit_markets(self, positions: dict, refreshed: bool) -> None:
        """Mercados con posición que ya no están en la lista pero en los que hay que poder salir."""
        if not self.strategy.wants_exits():
            self.exit_markets = {}
            return
        wanted = [t for t in positions if t not in self.markets and self.follows(t)]
        current = {} if refreshed else {t: m for t, m in self.exit_markets.items() if t in wanted}
        missing = [t for t in wanted if t not in current]
        try:
            for start in range(0, len(missing), 50):
                for market in self.client.get_markets(status=None, tickers=missing[start : start + 50]):
                    current[market.ticker] = market
        except KalshiAPIError as exc:
            log.warning("No se pudieron leer los mercados con posición abierta: %s", exc)
        added = [t for t in current if t not in self.exit_markets]
        if added:
            log.info("Vigilando para salir a tiempo: %s", ", ".join(added))
        self.exit_markets = current

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

        flt = self.cfg.market_filter()
        if flt.searches:
            for market in discover(self.client, flt, now, exclude=selected):
                selected.setdefault(market.ticker, market)
        return selected

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
            if from_bot and self.on_fill is not None:
                try:
                    self.on_fill(fill)
                except Exception:  # noqa: BLE001 - un aviso nunca para al bot
                    log.debug("Falló el aviso del llenado", exc_info=True)
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
