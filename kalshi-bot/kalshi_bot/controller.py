"""Controlador para el panel web.

Arranca y detiene el bot en un hilo, guarda los ajustes que se cambian desde
el panel (runtime.json en la carpeta de datos), lanza trabajos en segundo
plano (escáner e investigación) y conserva las últimas líneas del log.
"""

from __future__ import annotations

import collections
import csv
import io
import json
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .client import ENVIRONMENTS, KalshiAPIError, KalshiClient, new_client_order_id
from .config import (
    ConfigError,
    Settings,
    data_dir_from_env,
    delete_panel_credentials,
    load_dotenv,
    load_settings,
    read_overrides,
    save_panel_credentials,
    save_panel_env,
    write_overrides,
)
from .engine import Bot, DryRunExecutor, Journal, LiveExecutor
from .fees import MAKER_FEE_RATE, TAKER_FEE_RATE
from .models import ASK, BID, GTC, IOC, ONE, OrderIntent, ceil_to_tick, floor_to_tick, to_decimal
from .research import run_research, run_sweep
from .results import build_results
from .risk import RiskManager
from .scanner import ScanParams, run_scan
from .strategies import BUILTIN, build_strategy
from .strategies.fair_value import load_fair_values_csv, parse_probability

log = logging.getLogger(__name__)

STATE_FILE = "state.json"
MANUAL_PREFIX = "man"  # las órdenes manuales del panel no las toca el bot
TEST_PREFIX = "diag"  # orden de prueba del diagnóstico
ENV_LABELS = {"demo": "Demo", "prod": "Real"}


class ControllerError(Exception):
    pass


class LogBuffer(logging.Handler):
    """Guarda las últimas líneas del log para enseñarlas en el panel."""

    def __init__(self, capacity: int = 500):
        super().__init__(level=logging.INFO)
        self._lines: collections.deque = collections.deque(maxlen=capacity)
        self._next_id = 1
        self._lock = threading.Lock()
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001 - un log roto no debe tumbar nada
            return
        with self._lock:
            self._lines.append(
                {
                    "id": self._next_id,
                    "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
                    "level": record.levelname,
                    "message": message,
                }
            )
            self._next_id += 1

    def since(self, after: int = 0, limit: int = 200) -> list:
        with self._lock:
            return [line for line in self._lines if line["id"] > after][-limit:]


class Job:
    """Trabajo en segundo plano con progreso (escáner o investigación)."""

    def __init__(self, name: str):
        self.name = name
        self.state = "idle"  # idle | running | done | error
        self.done = 0
        self.total = 0
        self.result: Any = None
        self.error: Optional[str] = None
        self.started_at: Optional[str] = None
        self.finished_at: Optional[str] = None
        self.cancel = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, fn: Callable[["Job"], Any]) -> None:
        with self._lock:
            if self.running:
                raise ControllerError("Ya hay un trabajo de este tipo en marcha")
            self._launch(fn)

    def _launch(self, fn: Callable[["Job"], Any]) -> None:
        self.state, self.done, self.total = "running", 0, 0
        self.result, self.error, self.cancel = None, None, False
        self.started_at, self.finished_at = _now_iso(), None

        def target():
            try:
                self.result = fn(self)
                self.state = "done"
            except Exception as exc:  # noqa: BLE001 - se muestra en el panel
                log.exception("Falló el trabajo %s", self.name)
                self.error = str(exc)
                self.state = "error"
            finally:
                self.finished_at = _now_iso()

        self._thread = threading.Thread(target=target, name=f"job-{self.name}", daemon=True)
        self._thread.start()

    def progress(self, done: int, total: int) -> None:
        self.done, self.total = done, total

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "done": self.done,
            "total": self.total,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _short_error(exc: Exception) -> str:
    if isinstance(exc, KalshiAPIError):
        text = exc.message or str(exc)
        if exc.code and exc.code not in text:
            text = f"{text} [{exc.code}]"
        return f"HTTP {exc.status}: {text}" if exc.status else text
    return str(exc)[:300] or exc.__class__.__name__


def _cents(price: Optional[Decimal]) -> str:
    if price is None:
        return "—"
    cents = price * 100
    return f"{cents.normalize():f}¢" if cents == cents.to_integral_value() else f"{cents:.1f}¢"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class BotController:
    def __init__(
        self,
        config_path: Optional[str] = None,
        client_factory: Optional[Callable[[Settings, Any], KalshiClient]] = None,
    ):
        self.config_path = config_path
        self._client_factory = client_factory or (lambda settings, signer: settings.client(signer))
        self._lock = threading.RLock()
        self._bot: Optional[Bot] = None
        self._thread: Optional[threading.Thread] = None
        self._mode: Optional[str] = None
        self._balance_cache: tuple = (0.0, None, None)  # (instante, saldo, error)
        self.logs = LogBuffer()
        self.jobs = {"scan": Job("scan"), "research": Job("research"), "sweep": Job("sweep")}
        self._results_cache: dict = {}  # (días, zona horaria) -> (instante, resultado)
        config_dir = Path(config_path).resolve().parent if config_path else Path.cwd()
        load_dotenv(config_dir / ".env")
        self.data_dir = data_dir_from_env(config_dir)

    # --- ajustes ---------------------------------------------------------------

    def overrides(self) -> dict:
        return read_overrides(self.data_dir)

    def settings(self, overrides: Optional[dict] = None) -> Settings:
        return load_settings(self.config_path, overrides=self.overrides() if overrides is None else overrides)

    def client(self, settings: Optional[Settings] = None, require_auth: bool = False) -> KalshiClient:
        settings = settings or self.settings()
        signer = settings.signer()
        if require_auth and signer is None:
            raise ControllerError("Primero configura tu API key de Kalshi en Ajustes")
        return self._client_factory(settings, signer)

    def settings_payload(self) -> dict:
        settings = self.settings()
        strategies = [
            {"name": cls.name, "label": cls.label or cls.name, "description": cls.description, "params": cls.PARAMS}
            for cls in BUILTIN.values()
        ]
        return {
            "values": settings.sections(),
            "strategies": strategies,
            "credentials": self.credentials_info(settings),
            "needs_restart": self.is_running(),
        }

    def save_settings(self, sections: dict) -> Settings:
        """Valida y guarda los ajustes enviados por el panel."""
        if not isinstance(sections, dict):
            raise ControllerError("Formato de ajustes no válido")
        allowed = {"bot", "markets", "risk", "strategy"}
        clean = {k: v for k, v in sections.items() if k in allowed and isinstance(v, dict)}
        merged = dict(self.overrides())
        for key, value in clean.items():
            if key == "strategy":
                merged[key] = {"name": value.get("name"), "params": dict(value.get("params") or {})}
            else:
                merged[key] = {**merged.get(key, {}), **value}
        settings = self.settings(overrides=merged)  # lanza ConfigError si algo no cuadra
        build_strategy(settings.strategy_name, settings.strategy_params)  # valida parámetros
        write_overrides(self.data_dir, merged)
        log.info("Ajustes guardados desde el panel")
        return settings

    def credentials_info(self, settings: Optional[Settings] = None) -> dict:
        settings = settings or self.settings()
        return {
            "configured": bool(settings.credentials_source),
            "source": settings.credentials_source,
            "key_id_hint": (settings.api_key_id or "")[:8],
            "env": settings.env,
            "env_locked": settings.env_locked,
            "is_production": settings.is_production,
        }

    def save_credentials(self, key_id: str, private_key: str, env: str) -> dict:
        settings = self.settings()
        if settings.credentials_source == "env":
            raise ControllerError("Las credenciales vienen de variables de entorno del servidor; cámbialas allí")
        if self.is_running():
            raise ControllerError("Detén el bot antes de cambiar las credenciales")
        try:
            save_panel_credentials(self.data_dir, key_id=key_id, private_key_pem=private_key, env=env)
        except ValueError as exc:
            raise ControllerError(str(exc)) from exc
        log.info("Credenciales guardadas desde el panel (entorno %s)", env)
        self._results_cache = {}
        return self.credentials_info()

    def delete_credentials(self) -> dict:
        if self.is_running():
            raise ControllerError("Detén el bot antes de borrar las credenciales")
        delete_panel_credentials(self.data_dir)
        self._results_cache = {}
        return self.credentials_info()

    def set_env(self, env: str) -> dict:
        settings = self.settings()
        if settings.env_locked:
            raise ControllerError("El entorno está fijado por la variable KALSHI_ENV del servidor")
        if self.is_running():
            raise ControllerError("Detén el bot antes de cambiar de entorno")
        save_panel_env(self.data_dir, env)
        self._results_cache = {}
        return self.credentials_info()

    # --- ciclo de vida del bot ---------------------------------------------------

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, mode: str) -> None:
        if mode not in ("sim", "live"):
            raise ControllerError("Modo desconocido")
        with self._lock:
            if self.is_running():
                raise ControllerError("El bot ya está en marcha")
            settings = self.settings()
            signer = settings.signer()
            if mode == "live" and signer is None:
                raise ControllerError("Para operar en vivo configura primero tu API key en Ajustes")
            client = self._client_factory(settings, signer)
            strategy = build_strategy(settings.strategy_name, settings.strategy_params)
            settings.log_dir.mkdir(parents=True, exist_ok=True)
            journal = Journal(settings.log_dir / "journal.jsonl")
            prefix = settings.engine.order_prefix
            if mode == "live":
                executor = LiveExecutor(client, prefix, journal, ttl_seconds=settings.engine.order_ttl_seconds)
            else:
                executor = DryRunExecutor(prefix, journal)
            bot = Bot(
                client,
                strategy,
                RiskManager(settings.risk),
                executor,
                settings.engine,
                env_name=settings.env,
                journal=journal,
            )
            thread = threading.Thread(target=self._run_bot, args=(bot,), name="kalshi-bot", daemon=True)
            self._bot, self._thread, self._mode = bot, thread, mode
            self._write_state({"desired": mode, "halted": None})
            thread.start()
        log.info("Bot arrancado desde el panel en modo %s", "EN VIVO" if mode == "live" else "simulación")

    def _run_bot(self, bot: Bot) -> None:
        try:
            bot.run()
        except Exception as exc:  # noqa: BLE001
            log.exception("El bot se detuvo por un error")
            bot.halted_reason = bot.halted_reason or f"error: {exc}"
        if bot.halted_reason:
            self._write_state({"desired": None, "halted": bot.halted_reason})

    def stop(self, timeout: float = 30.0) -> None:
        with self._lock:
            bot, thread = self._bot, self._thread
        if bot is not None:
            bot.stop()
        if thread is not None:
            thread.join(timeout)
        state = self._read_state()
        self._write_state({"desired": None, "halted": state.get("halted")})

    def shutdown(self, timeout: float = 30.0) -> None:
        """Apagado del servidor: para el bot pero recuerda que estaba en marcha."""
        with self._lock:
            bot, thread = self._bot, self._thread
        if bot is not None:
            bot.stop()
        if thread is not None:
            thread.join(timeout)

    def kill(self, everything: bool = False) -> dict:
        """Freno de emergencia: cancela primero (lo urgente) y luego para el bot."""
        with self._lock:
            bot = self._bot
        if bot is not None:
            bot.stop()  # que no envíe nada más mientras cancelamos
        settings = self.settings()
        if settings.signer() is None:
            self.stop()
            return {"cancelled": 0, "note": "Sin API key no hay órdenes reales que cancelar"}
        client = self.client(settings, require_auth=True)
        if everything:
            client.cancel_all_orders()
            self.stop()
            log.warning("FRENO: cancelación de TODAS las órdenes de la cuenta enviada desde el panel")
            return {"cancelled": "all"}
        executor = LiveExecutor(client, settings.engine.order_prefix, Journal(settings.log_dir / "journal.jsonl"))
        cancelled: set = set()

        def cancel_open() -> None:
            for order in executor.resting_orders():
                try:
                    executor.cancel(order)
                except KalshiAPIError as exc:  # p. ej. el bot ya la canceló al pararse
                    log.info("No se pudo cancelar %s: %s", order.order_id, exc)
                cancelled.add(order.order_id)

        cancel_open()
        self.stop()
        cancel_open()  # por si entró alguna mientras el bot se detenía
        log.warning("FRENO: %d órdenes del bot canceladas desde el panel", len(cancelled))
        return {"cancelled": len(cancelled)}

    def resume_if_needed(self) -> None:
        """Al reiniciar el servidor, vuelve a arrancar el bot si estaba en marcha."""
        state = self._read_state()
        desired = state.get("desired")
        if desired in ("sim", "live") and not state.get("halted"):
            log.info("Reanudando el bot en modo %s tras el reinicio", desired)
            try:
                self.start(desired)
            except (ControllerError, ConfigError, ValueError) as exc:
                log.error("No se pudo reanudar el bot: %s", exc)

    def _read_state(self) -> dict:
        try:
            data = json.loads((self.data_dir / STATE_FILE).read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_state(self, state: dict) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            (self.data_dir / STATE_FILE).write_text(json.dumps({**state, "updated_at": _now_iso()}), encoding="utf-8")
        except OSError as exc:
            log.warning("No se pudo guardar el estado del bot: %s", exc)

    # --- estado ------------------------------------------------------------------

    def _balance(self, settings: Settings) -> tuple:
        bot = self._bot
        if self.is_running() and bot is not None and bot.last_balance is not None:
            return bot.last_balance, None
        try:
            if settings.signer() is None:
                return None, None
        except (ConfigError, ValueError) as exc:
            return None, f"credenciales no válidas: {exc}"
        stamp, balance, error = self._balance_cache
        if time.monotonic() - stamp < 15:
            return balance, error
        try:
            balance, error = self.client(settings).get_balance(), None
        except Exception as exc:  # noqa: BLE001 - se muestra en el panel
            balance, error = None, str(exc)
        self._balance_cache = (time.monotonic(), balance, error)
        return balance, error

    def status(self) -> dict:
        settings = self.settings()
        bot = self._bot
        running = self.is_running()
        if running:
            state = "running"
        elif bot is not None and bot.halted_reason:
            state = "halted"
        else:
            state = "stopped"
        balance, balance_error = self._balance(settings)
        session_pnl = None
        if running and bot is not None and balance is not None and bot.risk.start_equity is not None:
            session_pnl = balance.equity - bot.risk.start_equity
        strategy = bot.strategy if running and bot is not None else None
        return {
            "env": settings.env,
            "is_production": settings.is_production,
            "credentials": self.credentials_info(settings),
            "bot": {
                "state": state,
                "mode": self._mode if bot is not None else None,
                "strategy": (strategy.name if strategy else settings.strategy_name),
                "started_at": _iso(bot.started_at) if bot else None,
                "last_tick_at": _iso(bot.last_tick_at) if bot else None,
                "ticks": bot.ticks if bot else 0,
                "halted_reason": bot.halted_reason if bot else self._read_state().get("halted"),
                "markets": list(bot.markets)[:100] if bot else [],
                "consecutive_errors": bot._consecutive_errors if bot else 0,
            },
            "balance": (
                {"cash": balance.cash, "portfolio_value": balance.portfolio_value, "equity": balance.equity}
                if balance is not None
                else None
            ),
            "balance_error": balance_error,
            "session_pnl": session_pnl,
            "risk": settings.sections()["risk"],
        }

    # --- portafolio y órdenes ---------------------------------------------------------

    def positions(self) -> list:
        client = self.client(require_auth=True)
        return [
            {
                "ticker": p.ticker,
                "side": "yes" if p.position > 0 else "no",
                "contracts": abs(p.position),
                "exposure": p.exposure,
                "realized_pnl": p.realized_pnl,
                "fees_paid": p.fees_paid,
            }
            for p in sorted(client.get_positions().values(), key=lambda p: p.ticker)
        ]

    def results(self, days: Any = 30, tz_offset_minutes: Any = 0) -> dict:
        """Lo ganado o perdido en los mercados cerrados de los últimos `days` días."""
        try:
            days, tz = int(days), int(tz_offset_minutes)
        except (TypeError, ValueError) as exc:
            raise ControllerError("Periodo no válido") from exc
        days, tz = min(max(days, 1), 90), min(max(tz, -14 * 60), 14 * 60)
        cached = self._results_cache.get((days, tz))
        if cached and time.monotonic() - cached[0] < 20:
            return cached[1]
        client = self.client(require_auth=True)
        data = build_results(client, now=datetime.now(timezone.utc), days=days, tz_offset_minutes=tz)
        self._results_cache = {(days, tz): (time.monotonic(), data)}
        return data

    def orders(self) -> list:
        settings = self.settings()
        client = self.client(settings, require_auth=True)
        prefix = settings.engine.order_prefix + "-"
        return [
            {
                "order_id": o.order_id,
                "ticker": o.ticker,
                "side": o.side,
                "outcome": "yes" if o.side == BID else "no",
                "price": o.price if o.side == BID else ONE - o.price,
                "remaining": o.remaining,
                "filled": o.filled,
                "source": "bot" if o.client_order_id.startswith(prefix) else "manual",
                "created_time": _iso(o.created_time),
            }
            for o in sorted(client.get_orders(status="resting"), key=lambda o: (o.ticker, o.side, o.price))
        ]

    def cancel_order(self, order_id: str, ticker: Optional[str]) -> dict:
        client = self.client(require_auth=True)
        client.cancel_order(order_id, ticker)
        log.info("Orden %s cancelada desde el panel", order_id)
        return {"cancelled": order_id}

    def place_manual_order(self, ticker: str, outcome: str, price: Any, count: Any, immediate: bool = False) -> dict:
        """Orden manual: comprar SÍ o NO a un precio límite (precio del lado elegido)."""
        if outcome not in ("yes", "no"):
            raise ControllerError("Elige SÍ o NO")
        side_price = to_decimal(price)
        qty = to_decimal(count)
        if side_price is None or not (0 < side_price < 1):
            raise ControllerError("El precio debe estar entre 0.01 y 0.99")
        if qty is None or qty < 1 or qty != qty.to_integral_value() or qty > 10000:
            raise ControllerError("La cantidad debe ser un número entero de contratos (1-10000)")
        settings = self.settings()
        client = self.client(settings, require_auth=True)
        market = client.get_market(ticker)
        if outcome == "yes":
            yes_price = floor_to_tick(side_price, market.price_ranges)
            side = BID
        else:
            yes_price = ceil_to_tick(ONE - side_price, market.price_ranges)  # nunca pagar más por NO
            side = ASK
        if yes_price is None:
            raise ControllerError("Precio fuera de los rangos válidos del mercado")
        intent = OrderIntent(ticker, side, yes_price, qty, IOC if immediate else GTC, reason="orden manual")
        resp = client.create_order(intent, new_client_order_id(MANUAL_PREFIX))
        result = resp.get("order", resp)
        paid = yes_price if outcome == "yes" else ONE - yes_price
        log.info("Orden manual: comprar %s %s @ %s en %s", outcome.upper(), qty, paid, ticker)
        return {"order": result, "price": paid}

    # --- mercados -----------------------------------------------------------------

    def events(self, series: Optional[str]) -> list:
        events = self.client().get_events(series_ticker=series or None, limit=100)
        return [
            {
                "event_ticker": e.get("event_ticker"),
                "series_ticker": e.get("series_ticker"),
                "title": e.get("title"),
                "sub_title": e.get("sub_title"),
            }
            for e in events
        ]

    def markets(self, series: Optional[str], event: Optional[str]) -> list:
        if not (series or event):
            raise ControllerError("Indica una serie o un evento")
        now = datetime.now(timezone.utc)
        markets = self.client().get_markets(series_ticker=series or None, event_ticker=event or None, max_pages=3)
        markets.sort(key=lambda m: m.volume_24h, reverse=True)
        return [self._market_dict(m, now) for m in markets[:200]]

    def market_detail(self, ticker: str) -> dict:
        client = self.client()
        market = client.get_market(ticker)
        book = client.get_orderbook(ticker, depth=10)
        now = datetime.now(timezone.utc)
        return {
            "market": {**self._market_dict(market, now), "rules": market.raw.get("rules_primary", "")},
            "book": {
                "bids": [{"price": lvl.price, "size": lvl.size} for lvl in book.bids[:10]],
                "asks": [{"price": lvl.price, "size": lvl.size} for lvl in book.asks[:10]],
                "best_bid": book.best_bid,
                "best_ask": book.best_ask,
            },
            "fees": {"taker_rate": TAKER_FEE_RATE, "maker_rate": MAKER_FEE_RATE},
        }

    @staticmethod
    def _market_dict(m, now: datetime) -> dict:
        hours = m.hours_to_close(now)
        return {
            "ticker": m.ticker,
            "event_ticker": m.event_ticker,
            "title": m.title,
            "subtitle": m.subtitle,
            "status": m.status,
            "yes_bid": m.yes_bid,
            "yes_ask": m.yes_ask,
            "last_price": m.last_price,
            "volume_24h": m.volume_24h,
            "hours_to_close": round(hours, 2) if hours is not None else None,
        }

    # --- valores justos -------------------------------------------------------------

    def _fair_values_path(self) -> Path:
        settings = self.settings(overrides={**self.overrides(), "strategy": {"name": "fair_value"}})
        current = self.settings()
        if current.strategy_name == "fair_value" and current.strategy_params.get("fair_values_file"):
            return Path(current.strategy_params["fair_values_file"])
        return Path(settings.strategy_params["fair_values_file"])

    def fair_values(self) -> list:
        path = self._fair_values_path()
        if not path.is_file():
            return []
        return [{"ticker": t, "probability": p} for t, p in sorted(load_fair_values_csv(path).items())]

    def save_fair_values(self, rows: list) -> list:
        if not isinstance(rows, list):
            raise ControllerError("Formato no válido")
        values = {}
        for row in rows:
            ticker = str((row or {}).get("ticker") or "").strip()
            if not ticker:
                continue
            prob = parse_probability((row or {}).get("probability"))
            if prob is None:
                raise ControllerError(f"Probabilidad no válida para {ticker}")
            values[ticker] = prob
        path = self._fair_values_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["ticker", "probabilidad"])
        for ticker, prob in sorted(values.items()):
            writer.writerow([ticker, str(prob)])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        tmp.replace(path)
        log.info("Valores justos guardados desde el panel (%d mercados)", len(values))
        return self.fair_values()

    # --- diagnóstico ----------------------------------------------------------------------

    def diagnose(self, order_test: bool = False) -> dict:
        """Comprueba paso a paso que el bot puede trabajar con tu cuenta.

        Solo lee datos, salvo con order_test=True: entonces envía una orden de
        1 contrato al precio mínimo (post-only, caduca en un minuto) y la
        cancela al momento, para comprobar que Kalshi acepta las órdenes del bot.
        """
        settings = self.settings()
        steps: list = []
        result = {
            "env": settings.env,
            "is_production": settings.is_production,
            "steps": steps,
            "suggest_env": None,
            "ok": False,
        }

        def add(name: str, ok: bool, detail: str, hint: str = "") -> None:
            steps.append({"name": name, "ok": ok, "detail": detail, "hint": hint})

        public = self._client_factory(settings, None)
        try:
            exchange = public.get_exchange_status()
        except Exception as exc:  # noqa: BLE001 - se muestra en el panel
            add(
                "Conexión con Kalshi",
                False,
                _short_error(exc),
                "El servidor no llega a la API de Kalshi. Si está fuera de EE. UU., muévelo a una región de EE. UU.",
            )
            return result
        host = urlparse(getattr(public, "base_url", settings.base_url)).netloc
        if exchange.get("trading_active"):
            add("Conexión con Kalshi", True, f"{host} responde y el mercado está abierto")
        else:
            add("Conexión con Kalshi", True, f"{host} responde, pero ahora mismo Kalshi tiene el trading pausado")

        try:
            signer = settings.signer()
        except (ConfigError, ValueError) as exc:
            add("API key", False, str(exc), "Vuelve a guardar la key en Ajustes → Cuenta de Kalshi")
            return result
        client = None
        if signer is None:
            add("API key", False, "No hay ninguna API key configurada", "Ponla en Ajustes → Cuenta de Kalshi")
        else:
            client = self._client_factory(settings, signer)
            try:
                balance = client.get_balance()
            except KalshiAPIError as exc:
                other = self._env_accepting(settings, signer) if exc.is_auth_error else None
                if other:
                    result["suggest_env"] = other
                    add(
                        "API key",
                        False,
                        f"Esta key es de {ENV_LABELS[other]}, no de {ENV_LABELS[settings.env]}",
                        f"Cambia el entorno a {ENV_LABELS[other]}",
                    )
                elif exc.is_auth_error:
                    add(
                        "API key",
                        False,
                        f"Kalshi rechaza la key ({_short_error(exc)})",
                        "Comprueba que el Key ID y la clave privada son de la misma key y que no la has borrado "
                        "en Kalshi. Si el error habla de la hora, reinicia el servidor.",
                    )
                else:
                    add("API key", False, _short_error(exc))
                return result
            add("API key", True, f"Key {settings.api_key_id[:8]}… aceptada en {ENV_LABELS[settings.env]}")
            add("Saldo", True, f"Disponible ${balance.cash:.2f} · en posiciones ${balance.portfolio_value:.2f}")
            try:
                positions = client.get_positions()
                orders = client.get_orders(status="resting")
            except KalshiAPIError as exc:
                add("Cartera", False, _short_error(exc))
            else:
                prefix = settings.engine.order_prefix + "-"
                own = sum(1 for o in orders if o.client_order_id.startswith(prefix))
                detail = f"{len(positions)} posiciones abiertas · {len(orders)} órdenes en reposo ({own} del bot)"
                add("Cartera", True, detail)

        try:
            strategy = build_strategy(settings.strategy_name, settings.strategy_params)
            strategy.refresh()
            executor = DryRunExecutor(settings.engine.order_prefix, Journal(None))
            bot = Bot(public, strategy, RiskManager(settings.risk), executor, settings.engine)
            markets = list(bot.load_markets(datetime.now(timezone.utc)).values())
        except Exception as exc:  # noqa: BLE001
            add("Mercados", False, _short_error(exc))
            return result
        if not markets:
            add(
                "Mercados",
                False,
                "Con los ajustes actuales el bot no encuentra mercados que seguir",
                "Amplía Ajustes → Mercados (más horas o menos volumen mínimo) o usa «¿Dónde gana más el bot?»",
            )
        else:
            examples = ", ".join(m.ticker for m in markets[:3])
            add("Mercados", True, f"El bot seguiría {len(markets)} mercados ahora (p. ej. {examples})")
            try:
                book = public.get_orderbook(markets[0].ticker)
            except Exception as exc:  # noqa: BLE001
                add("Libro de órdenes", False, _short_error(exc))
            else:
                add(
                    "Libro de órdenes",
                    True,
                    f"{markets[0].ticker}: mejor compra {_cents(book.best_bid)} · mejor venta {_cents(book.best_ask)}",
                )

        if order_test:
            if client is None:
                add("Orden de prueba", False, "Hace falta una API key válida")
            elif not markets:
                add("Orden de prueba", False, "No hay ningún mercado donde probar")
            else:
                add("Orden de prueba", *self._order_round_trip(client, markets))
        result["ok"] = all(step["ok"] for step in steps)
        return result

    def _env_accepting(self, settings: Settings, signer) -> Optional[str]:
        """Si la key no vale en este entorno, ¿vale en el otro? (las keys de demo y real son distintas)."""
        if settings.env_locked or settings.base_url.rstrip("/") != ENVIRONMENTS.get(settings.env):
            return None
        other = "prod" if settings.env == "demo" else "demo"
        try:
            self._client_factory(replace(settings, env=other, base_url=ENVIRONMENTS[other]), signer).get_balance()
        except Exception:  # noqa: BLE001 - tampoco vale ahí
            return None
        return other

    def _order_round_trip(self, client: KalshiClient, markets: list) -> tuple:
        """Envía 1 contrato al precio mínimo (no se llena: nadie vende a 1¢) y lo cancela."""
        target = price = None
        for market in markets[:5]:
            price = ceil_to_tick(Decimal("0.01"), market.price_ranges)
            if price is None:
                continue
            book = client.get_orderbook(market.ticker)
            if book.best_ask is None or book.best_ask > price:  # post-only: no puede cruzar
                target = market
                break
        if target is None:
            return False, "No encontré un mercado donde la orden de prueba no se cruzara", ""
        intent = OrderIntent(target.ticker, BID, price, Decimal(1), GTC, post_only=True, reason="prueba de conexión")
        try:
            resp = client.create_order(intent, new_client_order_id(TEST_PREFIX), int(time.time()) + 60)
        except KalshiAPIError as exc:
            log.warning("Orden de prueba rechazada: %s", exc)
            return False, f"Kalshi no aceptó la orden: {_short_error(exc)}", "Envíame este mensaje para revisarlo"
        order = resp.get("order", resp)
        order_id = order.get("order_id")
        if not order_id:
            return False, f"Kalshi respondió sin id de orden: {str(order)[:200]}", ""
        try:
            client.cancel_order(order_id, target.ticker)
        except KalshiAPIError as exc:
            log.warning("No se pudo cancelar la orden de prueba %s: %s", order_id, exc)
            return (
                False,
                f"La orden se creó pero no se pudo cancelar: {_short_error(exc)}",
                "Caduca sola en un minuto; también puedes cancelarla en Inicio → Órdenes",
            )
        log.info("Orden de prueba creada y cancelada en %s (id %s)", target.ticker, order_id)
        return True, f"Kalshi aceptó y canceló una orden de 1 contrato a {_cents(price)} en {target.ticker}", ""

    # --- trabajos -----------------------------------------------------------------------

    def start_scan(self, params: dict) -> dict:
        settings = self.settings()
        scan = ScanParams(
            closing_within_hours=float(params.get("hours") or 48),
            series=[s.strip() for s in str(params.get("series") or "").split(",") if s.strip()],
            min_volume_24h=to_decimal(params.get("min_volume"), Decimal("100")),
            fav_min_price=to_decimal(params.get("fav_min_price"), Decimal("0.88")),
            fav_max_price=to_decimal(params.get("fav_max_price"), Decimal("0.97")),
        )
        client = self.client(settings)
        self.jobs["scan"].start(lambda job: run_scan(client, scan, datetime.now(timezone.utc)))
        return self.jobs["scan"].to_dict()

    def start_research(self, params: dict) -> dict:
        client = self.client()
        series = str(params.get("series") or "").strip() or None
        max_markets = max(10, min(int(params.get("markets") or 150), 1000))
        skip = max(0, int(params.get("skip_last_minutes") or 0))
        job = self.jobs["research"]
        job.start(
            lambda j: run_research(
                client,
                series=series,
                max_markets=max_markets,
                skip_last_minutes=skip,
                progress=j.progress,
                should_stop=lambda: j.cancel,
            )
        )
        return job.to_dict()

    def start_sweep(self, params: dict) -> dict:
        client = self.client()
        series_count = max(3, min(int(params.get("series_count") or 12), 40))
        per_series = max(10, min(int(params.get("per_series") or 60), 1000))
        job = self.jobs["sweep"]
        job.start(
            lambda j: run_sweep(
                client,
                series_count=series_count,
                per_series=per_series,
                progress=j.progress,
                should_stop=lambda: j.cancel,
            )
        )
        return job.to_dict()

    def use_series(self, series: list) -> dict:
        """Centra el bot en estas series (desde el barrido): deja de buscar en todos los mercados."""
        clean = [str(s).strip().upper() for s in series or [] if str(s).strip()]
        if not clean:
            raise ControllerError("Indica al menos una serie")
        overrides = self.overrides()
        markets = dict(overrides.get("markets") or {})
        markets["series"] = clean
        markets["closing_within_hours"] = 0
        self.save_settings({"markets": markets})
        log.info("El bot se centra ahora en las series: %s", ", ".join(clean))
        return {"series": clean}

    def add_ticker(self, ticker: str) -> dict:
        """Añade un mercado a la lista fija del bot (desde Oportunidades)."""
        overrides = self.overrides()
        markets = dict(overrides.get("markets") or {})
        current = list(markets.get("tickers") or self.settings().engine.tickers)
        if ticker not in current:
            current.append(ticker)
        markets["tickers"] = current
        self.save_settings({"markets": markets})
        return {"tickers": current}
