"""Estrategia "creador de mercado" (plantilla educativa).

Deja una orden de compra y una de venta alrededor del precio medio del libro
para intentar ganar el spread. Si acumula inventario, desplaza ambas
cotizaciones para reducirlo (si tienes YES de más, baja precios para vender
antes y comprar menos).

Riesgo principal: selección adversa. Quien sabe más que el bot le llenará
justo antes de que el precio se mueva en su contra. Pruébala en demo y con
tamaños pequeños; no es una estrategia "segura".

Parámetros ([strategy.params] en config.toml):
  half_spread        distancia de cada cotización al precio medio (dólares)
  quote_size         contratos por cotización
  max_position       inventario máximo por mercado (contratos)
  skew_per_contract  desplazamiento de las cotizaciones por contrato de inventario
  min_book_spread    solo cotiza si el spread del libro es al menos esto
"""

from __future__ import annotations

from typing import Optional

from ..models import ASK, BID
from .base import MarketContext, Strategy


class MarketMakerStrategy(Strategy):
    name = "market_maker"

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self.half_spread = self.dec("half_spread", "0.02")
        self.quote_size = self.dec("quote_size", "2")
        self.max_position = self.dec("max_position", "10")
        self.skew_per_contract = self.dec("skew_per_contract", "0.002")
        self.min_book_spread = self.dec("min_book_spread", "0.03")

    def on_market(self, ctx: MarketContext) -> list:
        book = ctx.book_ex_own  # sin tus propias órdenes, para no perseguirte a ti mismo
        if book.best_bid is None or book.best_ask is None:
            return []  # un lado del libro vacío: no hay precio de referencia fiable
        if book.spread < self.min_book_spread:
            return []  # spread demasiado estrecho para ganar algo

        reference = book.mid - self.skew_per_contract * ctx.position
        bid_price = reference - self.half_spread
        ask_price = reference + self.half_spread

        # Las cotizaciones son post-only: nunca deben cruzar el libro.
        below_ask = ctx.tick_below(book.best_ask)
        above_bid = ctx.tick_above(book.best_bid)
        if below_ask is not None:
            bid_price = min(bid_price, below_ask)
        if above_bid is not None:
            ask_price = max(ask_price, above_bid)

        intents = []
        if ctx.position < self.max_position:
            qty = min(self.quote_size, self.max_position - ctx.position)
            intents.append(ctx.buy_yes(bid_price, qty, post_only=True, reason=f"mid {book.mid}"))
        if ctx.position > -self.max_position:
            qty = min(self.quote_size, self.max_position + ctx.position)
            intents.append(ctx.sell_yes(ask_price, qty, post_only=True, reason=f"mid {book.mid}"))
        intents = [i for i in intents if i is not None]

        bids = [i for i in intents if i.side == BID]
        asks = [i for i in intents if i.side == ASK]
        if bids and asks and bids[0].price >= asks[0].price:
            return []  # libro demasiado estrecho tras redondear a ticks
        return intents
