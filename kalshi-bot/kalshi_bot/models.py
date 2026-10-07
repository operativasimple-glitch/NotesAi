"""Modelos de datos y utilidades numéricas.

Kalshi expresa los precios como strings en dólares ("0.5600") y las
cantidades de contratos como strings de punto fijo ("10.00"). Todo se
maneja con Decimal para no perder precisión.

Convención del bot (la misma que usa la API V2 de órdenes): todo se ve
desde el lado YES.
  - BID = comprar YES al precio p.
  - ASK = vender YES al precio p, que equivale a comprar NO a (1 - p).
  - Una posición positiva son contratos YES; negativa, contratos NO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any, Iterable, Optional

BID = "bid"
ASK = "ask"

GTC = "good_till_canceled"
IOC = "immediate_or_cancel"
FOK = "fill_or_kill"

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
PRICE_QUANTUM = Decimal("0.0001")
COUNT_QUANTUM = Decimal("0.01")
# Menor que cualquier tick posible: sirve para pedir "el tick estrictamente
# por debajo/encima" de un precio.
EPSILON = Decimal("0.0000001")


def to_decimal(value: Any, default: Optional[Decimal] = None) -> Optional[Decimal]:
    """Convierte str/int/float a Decimal; devuelve `default` si no se puede."""
    if value is None or value == "":
        return default
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def fmt_price(price: Decimal) -> str:
    """Precio en el formato que acepta la API: dólares con 4 decimales."""
    return str(price.quantize(PRICE_QUANTUM))


def fmt_count(count: Decimal) -> str:
    """Cantidad de contratos en punto fijo con 2 decimales."""
    return str(count.quantize(COUNT_QUANTUM))


def parse_time(value: Any) -> Optional[datetime]:
    """Convierte un timestamp ISO-8601 (o Unix en segundos) a datetime UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# --------------------------------------------------------------------------
# Ticks de precio
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceRange:
    """Rango de precios válido de un mercado y su tamaño de tick."""

    start: Decimal
    end: Decimal
    step: Decimal


DEFAULT_PRICE_RANGES = (PriceRange(Decimal("0.01"), Decimal("0.99"), Decimal("0.01")),)


def floor_to_tick(price: Decimal, ranges: Iterable[PriceRange] = DEFAULT_PRICE_RANGES) -> Optional[Decimal]:
    """Mayor precio válido <= price (None si no existe)."""
    best: Optional[Decimal] = None
    for r in ranges:
        if price < r.start:
            continue
        steps = ((min(price, r.end) - r.start) / r.step).to_integral_value(rounding=ROUND_FLOOR)
        candidate = r.start + steps * r.step
        if best is None or candidate > best:
            best = candidate
    return best


def ceil_to_tick(price: Decimal, ranges: Iterable[PriceRange] = DEFAULT_PRICE_RANGES) -> Optional[Decimal]:
    """Menor precio válido >= price (None si no existe)."""
    best: Optional[Decimal] = None
    for r in ranges:
        if price > r.end:
            continue
        steps = ((max(price, r.start) - r.start) / r.step).to_integral_value(rounding=ROUND_CEILING)
        candidate = r.start + steps * r.step
        if candidate > r.end:
            continue
        if best is None or candidate < best:
            best = candidate
    return best


def snap_price(price: Decimal, side: str, ranges: Iterable[PriceRange] = DEFAULT_PRICE_RANGES) -> Optional[Decimal]:
    """Ajusta un precio al tick válido más conservador.

    Al comprar (BID) redondea hacia abajo para no pagar de más; al vender
    (ASK) redondea hacia arriba para no vender más barato.
    """
    return floor_to_tick(price, ranges) if side == BID else ceil_to_tick(price, ranges)


# --------------------------------------------------------------------------
# Mercados y libro de órdenes
# --------------------------------------------------------------------------


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    subtitle: str
    status: str
    close_time: Optional[datetime]
    yes_bid: Optional[Decimal]
    yes_ask: Optional[Decimal]
    last_price: Optional[Decimal]
    volume_24h: Decimal
    open_interest: Decimal
    price_ranges: tuple = DEFAULT_PRICE_RANGES
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict) -> "Market":
        yes_bid = to_decimal(d.get("yes_bid_dollars"))
        yes_ask = to_decimal(d.get("yes_ask_dollars"))
        last = to_decimal(d.get("last_price_dollars"))
        return cls(
            ticker=d["ticker"],
            event_ticker=d.get("event_ticker", ""),
            title=d.get("title") or "",
            subtitle=d.get("yes_sub_title") or d.get("subtitle") or "",
            status=d.get("status", ""),
            close_time=parse_time(d.get("close_time")),
            # La API usa 0 como "sin bid" y 1 como "sin ask".
            yes_bid=yes_bid if yes_bid and yes_bid > 0 else None,
            yes_ask=yes_ask if yes_ask is not None and 0 < yes_ask < 1 else None,
            last_price=last if last and last > 0 else None,
            volume_24h=to_decimal(d.get("volume_24h_fp"), ZERO),
            open_interest=to_decimal(d.get("open_interest_fp"), ZERO),
            price_ranges=_parse_ranges(d.get("price_ranges")) or DEFAULT_PRICE_RANGES,
            raw=d,
        )

    @property
    def is_active(self) -> bool:
        return self.status in ("active", "open")

    def hours_to_close(self, now: datetime) -> Optional[float]:
        if self.close_time is None:
            return None
        return (self.close_time - now).total_seconds() / 3600


def _parse_ranges(raw: Any) -> tuple:
    ranges = []
    for r in raw or []:
        if not isinstance(r, dict):
            continue
        start, end, step = to_decimal(r.get("start")), to_decimal(r.get("end")), to_decimal(r.get("step"))
        if start is None or end is None or step is None or step <= 0 or end < start:
            continue
        ranges.append(PriceRange(start, end, step))
    return tuple(ranges)


@dataclass(frozen=True)
class Level:
    price: Decimal
    size: Decimal


@dataclass
class OrderBook:
    """Libro visto desde YES: bids de mayor a menor, asks de menor a mayor.

    La API solo devuelve bids de YES y bids de NO; un bid de NO a q es un
    ask de YES a (1 - q) con el mismo tamaño.
    """

    ticker: str
    bids: list
    asks: list

    @classmethod
    def from_api(cls, ticker: str, payload: dict) -> "OrderBook":
        book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
        bids = _aggregate(book.get("yes_dollars") or [])
        asks = [(ONE - price, size) for price, size in _aggregate(book.get("no_dollars") or [])]
        return cls(ticker, _levels(bids, descending=True), _levels(asks, descending=False))

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Optional[Decimal]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread(self) -> Optional[Decimal]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    def ask_size_up_to(self, limit: Decimal) -> Decimal:
        """Contratos que se pueden comprar a precio <= limit."""
        return sum((lvl.size for lvl in self.asks if lvl.price <= limit), ZERO)

    def bid_size_down_to(self, limit: Decimal) -> Decimal:
        """Contratos que se pueden vender a precio >= limit."""
        return sum((lvl.size for lvl in self.bids if lvl.price >= limit), ZERO)

    def without_orders(self, orders: Iterable["Order"]) -> "OrderBook":
        """Copia del libro descontando tus propias órdenes en reposo.

        Así una estrategia no reacciona a sus propias cotizaciones.
        """
        own_bids: dict = {}
        own_asks: dict = {}
        for o in orders:
            target = own_bids if o.side == BID else own_asks
            target[o.price] = target.get(o.price, ZERO) + o.remaining

        def strip(levels: list, own: dict) -> list:
            out = []
            for lvl in levels:
                size = lvl.size - own.get(lvl.price, ZERO)
                if size > 0:
                    out.append(Level(lvl.price, size))
            return out

        return OrderBook(self.ticker, strip(self.bids, own_bids), strip(self.asks, own_asks))


def _aggregate(levels: Iterable) -> list:
    totals: dict = {}
    for level in levels:
        if not level or len(level) < 2:
            continue
        price, size = to_decimal(level[0]), to_decimal(level[1])
        if price is None or size is None or size <= 0:
            continue
        totals[price] = totals.get(price, ZERO) + size
    return list(totals.items())


def _levels(pairs: list, descending: bool) -> list:
    # No dependemos del orden que mande la API: ordenamos nosotros.
    return [Level(p, s) for p, s in sorted(pairs, key=lambda x: x[0], reverse=descending)]


# --------------------------------------------------------------------------
# Portafolio
# --------------------------------------------------------------------------


@dataclass
class Balance:
    cash: Decimal
    portfolio_value: Decimal

    @property
    def equity(self) -> Decimal:
        return self.cash + self.portfolio_value

    @classmethod
    def from_api(cls, d: dict) -> "Balance":
        cash = to_decimal(d.get("balance_dollars"))
        if cash is None:
            cash = to_decimal(d.get("balance"), ZERO) / 100
        value = to_decimal(d.get("portfolio_value"), ZERO) / 100
        return cls(cash=cash, portfolio_value=value)


@dataclass
class Position:
    ticker: str
    position: Decimal  # > 0 contratos YES, < 0 contratos NO
    exposure: Decimal  # dólares comprometidos en el mercado
    realized_pnl: Decimal
    fees_paid: Decimal

    @classmethod
    def from_api(cls, d: dict) -> "Position":
        position = to_decimal(d.get("position_fp"))
        if position is None:
            position = to_decimal(d.get("position"), ZERO)
        return cls(
            ticker=d["ticker"],
            position=position,
            exposure=to_decimal(d.get("market_exposure_dollars"), ZERO),
            realized_pnl=to_decimal(d.get("realized_pnl_dollars"), ZERO),
            fees_paid=to_decimal(d.get("fees_paid_dollars"), ZERO),
        )


@dataclass
class Order:
    """Orden existente en el exchange (o simulada en modo dry-run)."""

    order_id: str
    client_order_id: str
    ticker: str
    side: str  # BID / ASK
    price: Decimal  # precio YES
    remaining: Decimal
    filled: Decimal = ZERO
    status: str = "resting"
    created_time: Optional[datetime] = None

    @classmethod
    def from_api(cls, d: dict) -> "Order":
        side = d.get("book_side")
        if side not in (BID, ASK):
            # Campos legacy: comprar YES / vender NO = bid; vender YES / comprar NO = ask.
            side = BID if (d.get("action"), d.get("side")) in (("buy", "yes"), ("sell", "no")) else ASK
        price = to_decimal(d.get("yes_price_dollars"))
        if price is None:
            price = to_decimal(d.get("yes_price"), ZERO) / 100
        remaining = to_decimal(d.get("remaining_count_fp"))
        if remaining is None:
            remaining = to_decimal(d.get("remaining_count"), ZERO)
        filled = to_decimal(d.get("fill_count_fp"))
        if filled is None:
            filled = to_decimal(d.get("fill_count"), ZERO)
        return cls(
            order_id=d["order_id"],
            client_order_id=d.get("client_order_id") or "",
            ticker=d["ticker"],
            side=side,
            price=price,
            remaining=remaining,
            filled=filled,
            status=str(d.get("status", "")),
            created_time=parse_time(d.get("created_time")),
        )

    def collateral(self) -> Decimal:
        """Dinero que bloquea la parte pendiente de la orden."""
        per_contract = self.price if self.side == BID else ONE - self.price
        return per_contract * self.remaining


@dataclass
class OrderIntent:
    """Orden que una estrategia QUIERE tener (o enviar)."""

    ticker: str
    side: str  # BID / ASK
    price: Decimal  # precio YES límite
    count: Decimal
    time_in_force: str = GTC
    post_only: bool = False
    reason: str = ""

    @property
    def is_resting(self) -> bool:
        """True si la orden queda en el libro (GTC); False si es IOC/FOK."""
        return self.time_in_force == GTC

    def cost_per_contract(self) -> Decimal:
        return self.price if self.side == BID else ONE - self.price

    def describe(self) -> str:
        verb = "COMPRA YES" if self.side == BID else "VENDE YES"
        tif = {GTC: "GTC", IOC: "IOC", FOK: "FOK"}.get(self.time_in_force, self.time_in_force)
        extra = " post-only" if self.post_only else ""
        return f"{verb} {fmt_count(self.count)} @ {fmt_price(self.price)} [{tif}{extra}] {self.ticker}"
