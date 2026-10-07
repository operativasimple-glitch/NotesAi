"""Interfaz de estrategias.

Una estrategia recibe, para cada mercado y en cada vuelta del bot, un
MarketContext y devuelve la lista de órdenes que QUIERE tener:

  - Órdenes GTC (por defecto): el motor las compara con tus órdenes en reposo,
    deja las que coinciden, cancela las que sobran y crea las que faltan.
    Devolver una lista vacía significa "no quiero órdenes en este mercado".
  - Órdenes IOC/FOK: se envían una vez (toman liquidez al momento).

Todo pasa por el gestor de riesgo antes de enviarse.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from ..models import (
    ASK,
    BID,
    EPSILON,
    GTC,
    ONE,
    Market,
    OrderBook,
    OrderIntent,
    ceil_to_tick,
    floor_to_tick,
    to_decimal,
)


@dataclass
class MarketContext:
    market: Market
    book: OrderBook  # libro completo (incluye tus órdenes)
    book_ex_own: OrderBook  # libro sin tus propias órdenes en reposo
    position: Decimal  # contratos YES netos (negativo = contratos NO)
    own_orders: list  # tus órdenes en reposo en este mercado
    now: datetime
    cash: Decimal  # saldo disponible en dólares

    @property
    def ticker(self) -> str:
        return self.market.ticker

    # --- ticks -------------------------------------------------------------

    def floor(self, price: Decimal) -> Optional[Decimal]:
        return floor_to_tick(price, self.market.price_ranges)

    def ceil(self, price: Decimal) -> Optional[Decimal]:
        return ceil_to_tick(price, self.market.price_ranges)

    def tick_below(self, price: Decimal) -> Optional[Decimal]:
        """Precio válido inmediatamente inferior (estrictamente menor)."""
        return floor_to_tick(price - EPSILON, self.market.price_ranges)

    def tick_above(self, price: Decimal) -> Optional[Decimal]:
        """Precio válido inmediatamente superior (estrictamente mayor)."""
        return ceil_to_tick(price + EPSILON, self.market.price_ranges)

    # --- constructores de órdenes ---------------------------------------

    def buy_yes(
        self, price: Decimal, count: Any, *, tif: str = GTC, post_only: bool = False, reason: str = ""
    ) -> Optional[OrderIntent]:
        """Comprar YES pagando como máximo `price` (se redondea hacia abajo al tick)."""
        p = self.floor(price)
        qty = to_decimal(count)
        if p is None or qty is None or qty <= 0:
            return None
        return OrderIntent(self.ticker, BID, p, qty, tif, post_only, reason)

    def sell_yes(
        self, price: Decimal, count: Any, *, tif: str = GTC, post_only: bool = False, reason: str = ""
    ) -> Optional[OrderIntent]:
        """Vender YES cobrando como mínimo `price` (se redondea hacia arriba al tick).

        Si no tienes YES, esto abre una posición NO pagando (1 - price).
        """
        p = self.ceil(price)
        qty = to_decimal(count)
        if p is None or qty is None or qty <= 0:
            return None
        return OrderIntent(self.ticker, ASK, p, qty, tif, post_only, reason)

    def buy_no(
        self, no_price: Decimal, count: Any, *, tif: str = GTC, post_only: bool = False, reason: str = ""
    ) -> Optional[OrderIntent]:
        """Comprar NO pagando como máximo `no_price` (= vender YES a 1 - no_price)."""
        return self.sell_yes(ONE - no_price, count, tif=tif, post_only=post_only, reason=reason)


class Strategy(ABC):
    """Clase base. Hereda de aquí para crear tu propia estrategia."""

    name = "base"

    def __init__(self, params: Optional[dict] = None):
        self.params = dict(params or {})

    def dec(self, key: str, default: Any) -> Decimal:
        """Lee un parámetro numérico como Decimal."""
        value = to_decimal(self.params.get(key, default))
        if value is None:
            raise ValueError(f"El parámetro '{key}' de la estrategia {self.name} no es un número válido")
        return value

    def suggested_tickers(self) -> list:
        """Mercados que la estrategia quiere operar además de los de la configuración."""
        return []

    def on_start(self) -> None:  # noqa: B027 - gancho opcional
        """Se llama una vez al arrancar el bot."""

    def refresh(self) -> None:  # noqa: B027 - gancho opcional
        """Se llama al inicio de cada vuelta (p. ej. para recargar archivos)."""

    @abstractmethod
    def on_market(self, ctx: MarketContext) -> list:
        """Devuelve la lista de OrderIntent deseadas para este mercado."""
