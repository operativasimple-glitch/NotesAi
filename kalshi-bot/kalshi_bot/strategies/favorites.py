"""Estrategia "favoritos": comprar el lado favorito como maker.

Se apoya en el sesgo favorito–longshot documentado en Kalshi (ver
INVESTIGACION.md): los contratos baratos están sobrevalorados y los caros,
ligeramente infravalorados. El bot deja órdenes de compra post-only (nunca
paga la comisión de taker) en el lado favorito, YES o NO, cuando cotiza entre
`min_price` y `max_price`, y mantiene la posición hasta la liquidación.

Quien le vende el favorito al bot es, en la práctica, alguien que compra el
longshot: el grupo que más pierde según los datos.

Parámetros ([strategy.params] en config.toml):
  min_price      precio mínimo del favorito (0.88 = 88¢)
  max_price      precio máximo a pagar (0.97); por encima la ganancia es mínima
  order_size     contratos por orden (10 o más para que el redondeo de la
                 comisión no se coma la ganancia)
  max_position   contratos máximos por mercado
  max_spread     ignora libros con un spread mayor (poco líquidos)
  improve        true: mejora en un tick la mejor oferta; false: se pone a la cola
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..models import ONE
from .base import MarketContext, Strategy


class FavoritesStrategy(Strategy):
    name = "favorites"
    label = "Favoritos"
    description = "Compra el lado favorito (88–97¢) con órdenes que esperan en el libro."
    PARAMS = [
        {"key": "min_price", "label": "Precio mínimo del favorito", "default": "0.88", "type": "price"},
        {"key": "max_price", "label": "Precio máximo a pagar", "default": "0.97", "type": "price"},
        {"key": "order_size", "label": "Contratos por orden", "default": "10", "type": "number"},
        {"key": "max_position", "label": "Contratos máximos por mercado", "default": "20", "type": "number"},
        {"key": "max_spread", "label": "Spread máximo del mercado", "default": "0.04", "type": "price"},
        {"key": "improve", "label": "Mejorar en un tick la mejor oferta", "default": True, "type": "bool"},
    ]

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self.min_price = self.dec("min_price", "0.88")
        self.max_price = self.dec("max_price", "0.97")
        self.order_size = self.dec("order_size", "10")
        self.max_position = self.dec("max_position", "20")
        self.max_spread = self.dec("max_spread", "0.04")
        self.improve = str(self.params.get("improve", True)).lower() not in ("false", "0", "no")
        if not (Decimal("0.5") < self.min_price <= self.max_price < ONE):
            raise ValueError("favorites: se requiere 0.5 < min_price <= max_price < 1")

    def on_market(self, ctx: MarketContext) -> list:
        book = ctx.book_ex_own
        bid, ask = book.best_bid, book.best_ask
        if bid is None or ask is None or ask - bid > self.max_spread:
            return []

        if self.min_price <= bid <= self.max_price:
            # YES es el favorito: comprar YES sin cruzar el spread.
            room = self.max_position - ctx.position
            if room <= 0:
                return []
            price = ctx.tick_above(bid) if self.improve else bid
            if price is None or price >= ask:
                price = bid
            price = min(price, ctx.floor(self.max_price) or price)
            intent = ctx.buy_yes(
                price, min(self.order_size, room), post_only=True, reason=f"favorito YES (bid {bid}, ask {ask})"
            )
            return [intent] if intent else []

        no_bid = ONE - ask
        if self.min_price <= no_bid <= self.max_price:
            # NO es el favorito: comprar NO = vender YES en el lado ask.
            room = self.max_position + ctx.position
            if room <= 0:
                return []
            yes_price = ctx.tick_below(ask) if self.improve else ask
            if yes_price is None or yes_price <= bid:
                yes_price = ask
            floor_yes = ctx.ceil(ONE - self.max_price)
            if floor_yes is not None:
                yes_price = max(yes_price, floor_yes)
            intent = ctx.sell_yes(
                yes_price,
                min(self.order_size, room),
                post_only=True,
                reason=f"favorito NO (bid NO {no_bid}, ask NO {ONE - bid})",
            )
            return [intent] if intent else []
        return []
