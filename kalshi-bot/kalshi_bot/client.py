"""Cliente REST para la API v2 de Kalshi.

Cubre lo que necesita el bot: datos de mercado (públicos), portafolio y
órdenes con los endpoints V2 (/portfolio/events/orders), que usan un único
libro visto desde YES (side = "bid" | "ask") y precios en dólares.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from typing import Any, Callable, Optional, Sequence
from urllib.parse import quote, urlparse

import requests

from . import __version__
from .auth import KalshiSigner
from .models import (
    Balance,
    Fill,
    Market,
    Order,
    OrderBook,
    OrderIntent,
    Position,
    Settlement,
    fmt_count,
    fmt_price,
)

log = logging.getLogger(__name__)

ENVIRONMENTS = {
    "prod": "https://external-api.kalshi.com/trade-api/v2",
    "demo": "https://external-api.demo.kalshi.co/trade-api/v2",
}
# Direcciones alternativas que el SDK oficial también da por buenas. Si la
# principal no responde (DNS, conexión rechazada), el cliente se pasa a esta.
FALLBACK_URLS = {
    "prod": ["https://api.elections.kalshi.com/trade-api/v2"],
    "demo": ["https://demo-api.kalshi.co/trade-api/v2"],
}
# Dirección que funcionó la última vez, compartida por todos los clientes del
# proceso para no repetir el fallo en cada consulta: {principal: alternativa}.
_working_url: dict = {}

ORDERS_ENDPOINT = "/portfolio/events/orders"


class KalshiAPIError(Exception):
    def __init__(self, status: int, code: str, message: str, *, method: str = "", path: str = "", details: str = ""):
        self.status = status
        self.code = code or ""
        self.message = message or ""
        self.details = details or ""
        self.method = method
        self.path = path
        text = f"{method} {path} -> HTTP {status}"
        if self.code:
            text += f" [{self.code}]"
        if self.message:
            text += f" {self.message}"
        if self.details:
            text += f" ({self.details})"
        super().__init__(text)

    @property
    def is_auth_error(self) -> bool:
        return self.status in (401, 403)


class RateLimiter:
    """Deja pasar como mucho `per_second` peticiones por segundo, y menos si Kalshi lo pide.

    Un HTTP 429 ("demasiadas peticiones") baja el ritmo a la mitad (nunca por debajo de
    `floor`); cada `recover_after` segundos sin otro 429 sube un 25 % hasta volver al máximo.
    Lo pueden compartir varios hilos (el bot y el panel).
    """

    def __init__(
        self,
        per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        *,
        floor: float = 1.0,
        recover_after: float = 60.0,
    ):
        self.max_rate = per_second
        self.rate = per_second
        self.floor = min(floor, per_second)
        self.recover_after = recover_after
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._slowed_at: Optional[float] = None
        self._warned_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def interval(self) -> float:
        return 1.0 / self.rate if self.rate > 0 else 0.0

    def wait(self) -> None:
        with self._lock:
            now = self._clock()
            self._recover(now)
            start = max(now, self._next)
            self._next = start + self.interval
        if start > now:
            self._sleep(start - now)

    def throttle(self) -> Optional[float]:
        """Kalshi respondió 429: baja el ritmo. Devuelve el ritmo nuevo si ha cambiado."""
        with self._lock:
            now = self._clock()
            if self.max_rate <= 0 or self.rate <= self.floor:
                self._slowed_at = now if self.max_rate > 0 else None
                return None
            # Varios 429 seguidos son el mismo aviso: se baja una vez cada 2 segundos como mucho.
            if self._slowed_at is not None and now - self._slowed_at < 2 and self.rate < self.max_rate:
                return None
            self.rate = max(self.floor, self.rate / 2)
            self._slowed_at = now
            return self.rate

    def should_warn(self, every: float = 600.0) -> bool:
        """Para avisar en la actividad una vez cada `every` segundos como mucho (y no en cada 429)."""
        with self._lock:
            now = self._clock()
            if self._warned_at is not None and now - self._warned_at < every:
                return False
            self._warned_at = now
            return True

    def _recover(self, now: float) -> None:
        if self._slowed_at is None or now - self._slowed_at < self.recover_after:
            return
        self.rate = min(self.max_rate, self.rate * 1.25)
        if self.rate >= self.max_rate:
            self._slowed_at = None
            log.info("Kalshi vuelve a aceptar el ritmo normal (%g peticiones por segundo)", self.max_rate)
        else:
            self._slowed_at = now


# Un limitador por servidor y tipo de petición, compartido por todos los clientes del proceso:
# el bot y el panel suman sus peticiones contra el mismo límite de Kalshi.
_limiters: dict = {}
_limiters_lock = threading.Lock()


def _shared_limiter(host: str, kind: str, per_second: float) -> RateLimiter:
    with _limiters_lock:
        limiter = _limiters.get((host, kind))
        if limiter is None or limiter.max_rate != per_second:
            limiter = _limiters[(host, kind)] = RateLimiter(per_second)
        return limiter


def new_client_order_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:24]}"


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        signer: Optional[KalshiSigner] = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        reads_per_second: float = 8.0,
        writes_per_second: float = 4.0,
        self_trade_prevention: str = "taker_at_cross",
        session: Optional[requests.Session] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        fallback_urls: Sequence[str] = (),
    ):
        self._primary_url = base_url.rstrip("/")
        self._urls = [self._primary_url]
        self._urls += [u.rstrip("/") for u in fallback_urls if u.rstrip("/") not in self._urls]
        preferred = _working_url.get(self._primary_url)
        self._set_base_url(preferred if preferred in self._urls else self._primary_url)
        self.signer = signer
        self.timeout = timeout
        self.max_retries = max_retries
        self.self_trade_prevention = self_trade_prevention
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": f"kalshi-bot/{__version__}"})
        self._sleep = sleep
        if clock is time.monotonic and sleep is time.sleep:
            host = urlparse(self._primary_url).netloc
            self._read_limiter = _shared_limiter(host, "read", reads_per_second)
            self._write_limiter = _shared_limiter(host, "write", writes_per_second)
        else:  # relojes de prueba: limitadores propios
            self._read_limiter = RateLimiter(reads_per_second, clock, sleep)
            self._write_limiter = RateLimiter(writes_per_second, clock, sleep)
        # Con API key también se firman los datos públicos: así Kalshi los cuenta contra tu
        # cuenta y no contra la IP del servidor, que en Railway comparten muchos usuarios.
        self._sign_public = True

    @property
    def authenticated(self) -> bool:
        return self.signer is not None

    def _set_base_url(self, url: str) -> None:
        self.base_url = url
        self._base_path = urlparse(url).path  # p. ej. /trade-api/v2

    def _switch_url(self) -> bool:
        """Tras un fallo de conexión, pasa a la siguiente dirección oficial de la API."""
        if len(self._urls) < 2:
            return False
        index = self._urls.index(self.base_url) if self.base_url in self._urls else -1
        new_url = self._urls[(index + 1) % len(self._urls)]
        log.warning("No hay conexión con %s; pruebo con %s", urlparse(self.base_url).netloc, urlparse(new_url).netloc)
        self._set_base_url(new_url)
        if new_url == self._primary_url:
            _working_url.pop(self._primary_url, None)
        else:
            _working_url[self._primary_url] = new_url
        return True

    # ------------------------------------------------------------------
    # Núcleo HTTP
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Optional[dict] = None,
        body: Optional[dict] = None,
        auth: bool = True,
    ) -> dict:
        """Hace una petición y devuelve el JSON.

        auth=True exige credenciales. Los datos de mercado son públicos (auth=False):
        se firman si hay API key y, si Kalshi rechaza la firma, se piden sin firmar.

        Reintenta errores de red y 5xx en GET/DELETE, y 429 en todos los
        métodos. Un POST que falla por red o 5xx NO se reintenta: la orden
        pudo haberse creado y el bot la verá en la siguiente vuelta.
        """
        method = method.upper()
        if auth and self.signer is None:
            raise KalshiAPIError(
                0,
                "missing_credentials",
                "Este comando necesita API key: configura KALSHI_API_KEY_ID y la clave privada en .env",
                method=method,
                path=endpoint,
            )
        clean_params = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        limiter = self._read_limiter if method == "GET" else self._write_limiter
        retry_server_errors = method in ("GET", "DELETE")
        attempt = 0
        while True:
            limiter.wait()
            url = self.base_url + endpoint
            headers = {}
            signed = auth or (self.signer is not None and self._sign_public)
            if signed:
                headers.update(self.signer.headers(method, self._base_path + endpoint))
            try:
                resp = self.session.request(
                    method, url, params=clean_params or None, json=body, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if retry_server_errors and attempt < self.max_retries:
                    attempt += 1
                    # Solo GET/DELETE: un POST que falla pudo llegar a crear la orden.
                    if isinstance(exc, requests.ConnectionError):
                        self._switch_url()
                    delay = self._backoff(attempt)
                    log.warning(
                        "Error de red en %s %s (%s); reintento %d en %.1fs", method, endpoint, exc, attempt, delay
                    )
                    self._sleep(delay)
                    continue
                raise KalshiAPIError(0, "network_error", str(exc), method=method, path=endpoint) from exc

            status = resp.status_code
            if status in (401, 403) and signed and not auth:
                # La key no vale para estos datos públicos (p. ej. es de otro entorno): sin firmar.
                self._sign_public = False
                log.info("Kalshi no acepta la firma en los datos públicos; se piden sin firmar")
                continue
            if status == 429:
                slower = limiter.throttle()
                if slower is not None and limiter.should_warn():
                    log.warning(
                        "Kalshi pide ir más despacio (HTTP 429): el bot baja a %.1f peticiones por segundo", slower
                    )
            retryable = status == 429 or (status >= 500 and retry_server_errors)
            if retryable and attempt < self.max_retries:
                attempt += 1
                delay = self._retry_after(resp) or self._backoff(attempt)
                # Un 429 suelto no es un problema (se reintenta solo): no llena la actividad.
                log.log(
                    logging.DEBUG if status == 429 else logging.WARNING,
                    "HTTP %d en %s %s; reintento %d en %.1fs",
                    status,
                    method,
                    endpoint,
                    attempt,
                    delay,
                )
                self._sleep(delay)
                continue
            if status >= 400:
                raise self._error(resp, method, endpoint)
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise KalshiAPIError(status, "invalid_json", resp.text[:200], method=method, path=endpoint) from exc

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 0.5 * (2**attempt)) + random.uniform(0, 0.25)

    @staticmethod
    def _retry_after(resp: requests.Response) -> Optional[float]:
        value = resp.headers.get("Retry-After")
        try:
            return min(60.0, max(0.0, float(value))) if value else None
        except ValueError:
            return None

    @staticmethod
    def _error(resp: requests.Response, method: str, endpoint: str) -> KalshiAPIError:
        code, message, details = "", resp.reason or "", ""
        try:
            data = resp.json()
        except ValueError:
            data = None
        if isinstance(data, dict):
            err = data.get("error", data)
            if isinstance(err, dict):
                code = err.get("code") or ""
                message = err.get("message") or message
                details = err.get("details") or ""
            elif isinstance(err, str):
                message = err
        elif resp.text:
            message = resp.text[:200]
        return KalshiAPIError(resp.status_code, code, message, method=method, path=endpoint, details=details)

    def _paginate(self, endpoint: str, key: str, params: dict, *, auth: bool, max_pages: int) -> list:
        items: list = []
        params = dict(params)
        for _ in range(max_pages):
            data = self.request("GET", endpoint, params=params, auth=auth)
            items.extend(data.get(key) or [])
            cursor = data.get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor
        return items

    # ------------------------------------------------------------------
    # Exchange y cuenta
    # ------------------------------------------------------------------

    def get_exchange_status(self) -> dict:
        return self.request("GET", "/exchange/status", auth=False)

    def get_account_limits(self) -> dict:
        return self.request("GET", "/account/limits")

    # ------------------------------------------------------------------
    # Datos de mercado (públicos)
    # ------------------------------------------------------------------

    def get_markets(
        self,
        *,
        status: Optional[str] = "open",
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        tickers: Optional[list] = None,
        min_close_ts: Optional[int] = None,
        max_close_ts: Optional[int] = None,
        mve_filter: Optional[str] = None,
        limit: int = 200,
        max_pages: int = 5,
    ) -> list:
        """Lista mercados. Ojo: Kalshi solo combina min/max_close_ts con status vacío o "closed"."""
        params: dict[str, Any] = {
            "status": status,
            "series_ticker": series_ticker,
            "event_ticker": event_ticker,
            "tickers": ",".join(tickers) if tickers else None,
            "min_close_ts": min_close_ts,
            "max_close_ts": max_close_ts,
            "mve_filter": mve_filter,
            "limit": limit,
        }
        raw = self._paginate("/markets", "markets", params, auth=False, max_pages=max_pages)
        return [Market.from_api(m) for m in raw]

    def get_market(self, ticker: str) -> Market:
        data = self.request("GET", f"/markets/{quote(ticker, safe='')}", auth=False)
        return Market.from_api(data["market"])

    def get_orderbook(self, ticker: str, depth: int = 0) -> OrderBook:
        params = {"depth": depth} if depth > 0 else None
        data = self.request("GET", f"/markets/{quote(ticker, safe='')}/orderbook", params=params, auth=False)
        return OrderBook.from_api(ticker, data)

    def get_events(
        self,
        *,
        status: Optional[str] = "open",
        series_ticker: Optional[str] = None,
        with_nested_markets: bool = False,
        limit: int = 100,
        max_pages: int = 1,
    ) -> list:
        params = {
            "status": status,
            "series_ticker": series_ticker,
            "with_nested_markets": "true" if with_nested_markets else None,
            "limit": min(limit, 200),
        }
        return self._paginate("/events", "events", params, auth=False, max_pages=max_pages)[: limit * max_pages]

    def get_trades(
        self, ticker: str, *, min_ts: Optional[int] = None, max_ts: Optional[int] = None, max_pages: int = 3
    ) -> list:
        """Operaciones públicas de un mercado (las más recientes primero)."""
        params = {"ticker": ticker, "min_ts": min_ts, "max_ts": max_ts, "limit": 1000}
        return self._paginate("/markets/trades", "trades", params, auth=False, max_pages=max_pages)

    def get_series(self, series_ticker: str) -> dict:
        """Datos de una serie (título, categoría, tipo de comisión...)."""
        data = self.request("GET", f"/series/{quote(series_ticker, safe='')}", auth=False)
        return data.get("series") or {}

    def get_series_list(self, category: Optional[str] = None, include_volume: bool = True) -> list:
        """Todas las series (o las de una categoría, p. ej. "Financials"), con su volumen total."""
        params = {"category": category, "include_volume": "true" if include_volume else None}
        data = self.request("GET", "/series", params=params, auth=False)
        return list(data.get("series") or [])

    def get_event(self, event_ticker: str) -> dict:
        data = self.request(
            "GET", f"/events/{quote(event_ticker, safe='')}", params={"with_nested_markets": "true"}, auth=False
        )
        return data.get("event") or {}

    # ------------------------------------------------------------------
    # Portafolio (requiere API key)
    # ------------------------------------------------------------------

    def get_balance(self) -> Balance:
        return Balance.from_api(self.request("GET", "/portfolio/balance"))

    def get_positions(self, max_pages: int = 10) -> dict:
        """Posiciones abiertas (no liquidadas) con cantidad distinta de cero."""
        params = {"limit": 200, "count_filter": "position"}
        raw = self._paginate("/portfolio/positions", "market_positions", params, auth=True, max_pages=max_pages)
        positions = [Position.from_api(p) for p in raw]
        return {p.ticker: p for p in positions if p.position != 0}

    def get_orders(self, *, status: str = "resting", ticker: Optional[str] = None, max_pages: int = 10) -> list:
        params = {"status": status, "ticker": ticker, "limit": 200}
        raw = self._paginate("/portfolio/orders", "orders", params, auth=True, max_pages=max_pages)
        return [Order.from_api(o) for o in raw]

    def get_fills(self, *, min_ts: Optional[int] = None, ticker: Optional[str] = None, limit: int = 100) -> list:
        params = {"min_ts": min_ts, "ticker": ticker, "limit": limit}
        return self.request("GET", "/portfolio/fills", params=params).get("fills") or []

    def get_fill_history(
        self, *, min_ts: Optional[int] = None, ticker: Optional[str] = None, max_pages: int = 50
    ) -> list:
        """Todos los llenados desde `min_ts` (lo más reciente primero), ya interpretados.

        Con `ticker`, solo los de ese mercado.
        """
        params = {"min_ts": min_ts, "ticker": ticker, "limit": 100}
        raw = self._paginate("/portfolio/fills", "fills", params, auth=True, max_pages=max_pages)
        return [f for f in (Fill.from_api(d) for d in raw) if f is not None]

    def get_settlements(self, *, min_ts: Optional[int] = None, max_pages: int = 20) -> list:
        """Mercados liquidados en tu cuenta desde `min_ts` (lo más reciente primero)."""
        params = {"min_ts": min_ts, "limit": 100}
        raw = self._paginate("/portfolio/settlements", "settlements", params, auth=True, max_pages=max_pages)
        return [Settlement.from_api(d) for d in raw if d.get("ticker")]

    # ------------------------------------------------------------------
    # Órdenes (endpoints V2)
    # ------------------------------------------------------------------

    def create_order(self, intent: OrderIntent, client_order_id: str, expiration_ts: Optional[int] = None) -> dict:
        body: dict[str, Any] = {
            "ticker": intent.ticker,
            "client_order_id": client_order_id,
            "side": intent.side,
            "count": fmt_count(intent.count),
            "price": fmt_price(intent.price),
            "time_in_force": intent.time_in_force,
            "self_trade_prevention_type": self.self_trade_prevention,
            # Si Kalshi pausa el trading, que cancele la orden en vez de dejarla viva.
            "cancel_order_on_pause": True,
        }
        if intent.post_only:
            body["post_only"] = True
        if expiration_ts and intent.is_resting:
            body["expiration_time"] = int(expiration_ts)
        return self.request("POST", ORDERS_ENDPOINT, body=body)

    def cancel_order(self, order_id: str, ticker: Optional[str] = None) -> dict:
        return self.request("DELETE", f"{ORDERS_ENDPOINT}/{quote(order_id, safe='')}", params={"market_ticker": ticker})

    def cancel_all_orders(self) -> None:
        """Cancela TODAS las órdenes en reposo de la cuenta (no solo las del bot).

        Ojo: según Kalshi, órdenes nuevas enviadas durante el minuto siguiente
        también pueden cancelarse. Úsalo como botón de pánico.
        """
        self.request("DELETE", ORDERS_ENDPOINT)
