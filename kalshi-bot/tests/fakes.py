"""Dobles de prueba: una sesión HTTP falsa y un cliente de Kalshi en memoria."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kalshi_bot.models import Balance, Market, Order, OrderBook, Position

# Relativo al reloj real: la CLI y el panel usan la hora de verdad, y unos mercados de prueba
# con fecha fija acabarían "cerrando" en el pasado.
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, reason="OK"):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.reason = reason
        self.content = b"" if payload is None else json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("sin cuerpo")
        return self._payload


class FakeSession:
    """Devuelve respuestas encoladas y registra cada petición."""

    def __init__(self, responses=None):
        self.headers = {}
        self.responses = list(responses or [])
        self.calls = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers or {}})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def market_payload(ticker="KXTEST-26OCT08-B50", **overrides):
    data = {
        "ticker": ticker,
        "event_ticker": "KXTEST-26OCT08",
        "market_type": "binary",
        "title": "¿Mercado de prueba?",
        "yes_sub_title": "50 o más",
        "status": "active",
        "close_time": (NOW + timedelta(hours=6)).isoformat().replace("+00:00", "Z"),
        "yes_bid_dollars": "0.4500",
        "yes_ask_dollars": "0.4800",
        "last_price_dollars": "0.4600",
        "volume_24h_fp": "1500.00",
        "open_interest_fp": "800.00",
        "price_ranges": [{"start": "0.0100", "end": "0.9900", "step": "0.0100"}],
    }
    data.update(overrides)
    return data


def make_market(ticker="KXTEST-26OCT08-B50", **overrides) -> Market:
    return Market.from_api(market_payload(ticker, **overrides))


def make_book(ticker, bids=(), asks=()) -> OrderBook:
    """bids/asks como [(precio_yes, tamaño)], en el formato de la API."""
    yes = [[str(p), str(s)] for p, s in bids]
    no = [[str(Decimal("1") - Decimal(str(p))), str(s)] for p, s in asks]
    return OrderBook.from_api(ticker, {"orderbook_fp": {"yes_dollars": yes, "no_dollars": no}})


class FakeKalshi:
    """Cliente en memoria con la misma interfaz que KalshiClient (lo que usa el bot)."""

    def __init__(self, markets=(), books=None, authenticated=True):
        self.authenticated = authenticated
        self.markets = {m.ticker: m for m in markets}
        self.books = dict(books or {})
        self.trading_active = True
        self.balance = Balance(Decimal("100"), Decimal("0"))
        self.positions: dict = {}
        self.orders: dict = {}
        self.fills: list = []
        self.fill_history: list = []  # Fill ya interpretados (para resultados)
        self.settlements: list = []  # Settlement (para resultados)
        self.created: list = []
        self.cancelled: list = []
        self.cancel_all_calls = 0
        self.failures: dict = {}  # nombre de método -> excepción a lanzar
        self.settled: dict = {}  # mercados liquidados (para research)
        self.trades: dict = {}  # ticker -> operaciones públicas
        self.events = None  # eventos con mercados anidados (para el escáner)
        self.series_info: dict = {}  # ticker de serie -> datos (título, categoría)
        self.series_list: list = []  # lo que devuelve GET /series
        self.market_queries: list = []
        self._next_id = 1

    def _maybe_fail(self, name):
        if name in self.failures:
            raise self.failures[name]

    # datos
    def get_exchange_status(self):
        self._maybe_fail("get_exchange_status")
        return {"exchange_active": True, "trading_active": self.trading_active}

    def get_markets(
        self,
        *,
        status="open",
        series_ticker=None,
        event_ticker=None,
        tickers=None,
        min_close_ts=None,
        max_close_ts=None,
        **_,
    ):
        self.market_queries.append(
            {"status": status, "series": series_ticker, "min_close_ts": min_close_ts, "max_close_ts": max_close_ts}
        )
        found = list(self.markets.values()) + (list(self.settled.values()) if status == "settled" else [])
        if tickers:
            found = [m for m in found if m.ticker in tickers]
        if event_ticker:
            found = [m for m in found if m.event_ticker == event_ticker]
        if series_ticker:
            found = [m for m in found if m.ticker.startswith(series_ticker)]
        if status == "open":
            found = [m for m in found if m.is_active]
        if status == "settled":
            found = [m for m in found if m.result in ("yes", "no")]
        if min_close_ts is not None:
            found = [m for m in found if m.close_time and m.close_time.timestamp() >= min_close_ts]
        if max_close_ts is not None:
            found = [m for m in found if m.close_time and m.close_time.timestamp() <= max_close_ts]
        return found

    def get_trades(self, ticker, *, min_ts=None, max_ts=None, max_pages=3):
        return list(self.trades.get(ticker, []))

    def get_series(self, ticker):
        return dict(self.series_info.get(ticker, {}))

    def get_series_list(self, category=None, include_volume=True):
        return [dict(s) for s in self.series_list if category is None or category in s.get("categories", [])]

    def get_orderbook(self, ticker, depth=0):
        return self.books[ticker]

    def get_market(self, ticker):
        return self.markets[ticker]

    def get_events(self, **_):
        if self.events is not None:
            return list(self.events)
        return [{"event_ticker": "KXTEST-26OCT08", "series_ticker": "KXTEST", "title": "Evento de prueba"}]

    def get_account_limits(self):
        return {"usage_tier": "basic", "read": {"refill_rate": 200}, "write": {"refill_rate": 100}}

    # portafolio
    def get_balance(self):
        assert self.authenticated, "get_balance sin credenciales"
        self._maybe_fail("get_balance")
        return self.balance

    def get_positions(self):
        assert self.authenticated, "get_positions sin credenciales"
        self._maybe_fail("get_positions")
        return {t: p for t, p in self.positions.items() if p.position != 0}

    def get_fill_history(self, *, min_ts=None, ticker=None, max_pages=50):
        assert self.authenticated, "get_fill_history sin credenciales"
        self._maybe_fail("get_fill_history")
        return [
            f
            for f in self.fill_history
            if (min_ts is None or f.time is None or f.time.timestamp() >= min_ts) and ticker in (None, f.ticker)
        ]

    def get_settlements(self, *, min_ts=None, max_pages=20):
        assert self.authenticated, "get_settlements sin credenciales"
        self._maybe_fail("get_settlements")
        return [s for s in self.settlements if min_ts is None or s.time is None or s.time.timestamp() >= min_ts]

    def set_position(self, ticker, contracts, exposure="0"):
        self.positions[ticker] = Position(ticker, Decimal(str(contracts)), Decimal(exposure), Decimal(0), Decimal(0))

    def get_orders(self, *, status="resting", ticker=None):
        return [o for o in self.orders.values() if ticker is None or o.ticker == ticker]

    def get_fills(self, **_):
        return list(self.fills)

    # órdenes
    def create_order(self, intent, client_order_id, expiration_ts=None):
        order_id = f"ord-{self._next_id}"
        self._next_id += 1
        self.created.append({"intent": intent, "client_order_id": client_order_id, "expiration_ts": expiration_ts})
        if intent.is_resting:
            self.orders[order_id] = Order(
                order_id, client_order_id, intent.ticker, intent.side, intent.price, intent.count
            )
        return {"order_id": order_id, "client_order_id": client_order_id, "fill_count": "0.00"}

    def cancel_order(self, order_id, ticker=None):
        self.cancelled.append(order_id)
        self.orders.pop(order_id, None)
        return {"order_id": order_id}

    def cancel_all_orders(self):
        self.cancel_all_calls += 1
        self.orders.clear()

    def add_manual_order(self, ticker, side, price, count):
        order_id = f"manual-{self._next_id}"
        self._next_id += 1
        self.orders[order_id] = Order(order_id, "web-123", ticker, side, Decimal(str(price)), Decimal(str(count)))
        return order_id
