"""Estrategia "favoritos": comprar el lado favorito como maker.

Se apoya en el sesgo favorito–longshot documentado en Kalshi (ver
INVESTIGACION.md): los contratos baratos están sobrevalorados y los caros,
ligeramente infravalorados. El bot deja órdenes de compra post-only (nunca
paga la comisión de taker) en el lado favorito, YES o NO, cuando cotiza entre
`min_price` y `max_price`, y mantiene la posición hasta la liquidación.

Quien le vende el favorito al bot es, en la práctica, alguien que compra el
longshot: el grupo que más pierde según los datos.

Si el favorito cambia de lado en un mercado donde ya tiene posición, no compra el
nuevo: en Kalshi, comprar SÍ teniendo NO vende primero esos NO (y al revés), así
que sería vender lo que tiene a lo poco que vale ya. Se queda con lo que tenía hasta
el final, que con datos reales sale mejor que vender cuando un favorito cae, y nunca
deshace así lo que compres tú a mano.

No compra al favorito nada más cruzar el precio mínimo: espera a que lleve `settle_minutes`
minutos seguidos por encima. Con el histórico minuto a minuto de seis semanas (noviembre de
2026, ver INVESTIGACION.md), comprar en el momento del cruce salía a −1 %: muchas veces es un
salto que se da la vuelta. Esperando 20 minutos, las mismas ligas salían a +2 %, en las dos
mitades del periodo y en cada liga de equipos por separado.

Salidas antes de tiempo (opcionales, 0 = desactivadas): si el favorito se hunde
hasta `stop_loss`, o si ya se puede vender a `take_profit` o más, el bot vende
toda la posición al mejor precio que haya en ese momento. Solo se aplican a
posiciones compradas como favorito (a `min_price` o más, con 5¢ de margen), para
no tocar lo que compres tú a mano a otros precios. El motor vigila esas
posiciones hasta que el mercado cierra, también en los últimos minutos.

El cobro anticipado se limita a las series de `take_profit_series` (por defecto
KXHIGH, la temperatura máxima): con datos reales, cobrar a 99¢ en el clima cuesta
casi nada y libera el dinero horas antes (el pago llega a la mañana siguiente),
mientras que en los partidos salió claramente peor que esperar.

Parámetros ([strategy.params] en config.toml):
  min_price      precio mínimo del favorito (0.88 = 88¢)
  max_price      precio máximo a pagar (0.97); por encima la ganancia es mínima
  order_size     contratos por orden (10 o más para que el redondeo de la
                 comisión no se coma la ganancia)
  max_position   contratos máximos por mercado
  max_spread     ignora libros con un spread mayor (poco líquidos)
  improve        true: mejora en un tick la mejor oferta; false: se pone a la cola
  settle_minutes minutos seguidos que el favorito debe llevar por encima de min_price
                 antes de comprarlo (0 = compra en cuanto lo ve)
  settle_skip_series  series donde no se espera ("KXHIGH": en el clima no hay saltos de
                 marcador y esperar no mejoró nada)
  stop_loss      vende si el favorito cae a este precio (0 = nunca)
  take_profit    vende si ya se puede cobrar este precio (0 = espera al final)
  take_profit_series  series donde se aplica take_profit ("KXHIGH"; vacío = todas)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from ..models import IOC, ONE, ZERO
from ..names import cents
from .base import MarketContext, Strategy

# Margen para reconocer una posición del bot por su precio medio de compra.
ENTRY_TOLERANCE = Decimal("0.05")
# Si pasa más que esto sin ver un mercado, no se sabe qué hizo el precio: la espera empieza de nuevo.
SETTLE_GAP = timedelta(minutes=2)


def series_prefixes(value) -> tuple:
    """Lista de series escrita a mano o como lista: "KXHIGH, kxhighny" → ("KXHIGH", "KXHIGHNY").

    Vacía → () = todas las series.
    """
    if value is not None and not isinstance(value, (str, list, tuple)):
        raise ValueError('favorites: las series deben ser una lista, p. ej. ["KXHIGH"]')
    items = value.split(",") if isinstance(value, str) else list(value or [])
    return tuple(dict.fromkeys(str(item).strip().upper() for item in items if str(item).strip()))


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
        {
            "key": "settle_minutes",
            "label": "Minutos que debe llevar de favorito",
            "default": "20",
            "type": "number",
            "help": "No compra en cuanto cruza el precio mínimo: espera a que aguante. 0 = compra al momento.",
        },
        {
            "key": "settle_skip_series",
            "label": "No esperar en estas series",
            "default": "KXHIGH",
            "type": "list",
            "help": "KXHIGH = temperatura máxima: ahí no hay saltos de marcador y esperar no mejoró nada.",
        },
        {
            "key": "stop_loss",
            "label": "Vender si el favorito cae a",
            "default": "0",
            "type": "price",
            "help": "Corta la pérdida si el partido o el día se tuerce. 0 = esperar siempre al final.",
        },
        {
            "key": "take_profit",
            "label": "Cobrar antes si ya se puede vender a",
            "default": "0",
            "type": "price",
            "help": "Vende un favorito casi ganado para liberar el dinero. 0 = esperar al final.",
        },
        {
            "key": "take_profit_series",
            "label": "Cobrar antes solo en estas series",
            "default": "KXHIGH",
            "type": "list",
            "help": "KXHIGH = temperatura máxima (en partidos, cobrar antes salió peor). Vacío = en todas.",
        },
    ]

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self.min_price = self.dec("min_price", "0.88")
        self.max_price = self.dec("max_price", "0.97")
        self.order_size = self.dec("order_size", "10")
        self.max_position = self.dec("max_position", "20")
        self.max_spread = self.dec("max_spread", "0.04")
        self.improve = str(self.params.get("improve", True)).lower() not in ("false", "0", "no")
        self.settle_minutes = self.dec("settle_minutes", "20")
        self.settle_skip_series = series_prefixes(self.params.get("settle_skip_series", "KXHIGH"))
        # mercado → (lado favorito, desde cuándo lo es sin interrupción, última vez visto)
        self._favorite: dict = {}
        self.stop_loss = self.dec("stop_loss", "0")
        self.take_profit = self.dec("take_profit", "0")
        self.take_profit_series = series_prefixes(self.params.get("take_profit_series", "KXHIGH"))
        if not (Decimal("0.5") < self.min_price <= self.max_price < ONE):
            raise ValueError("favorites: se requiere 0.5 < min_price <= max_price < 1")
        if self.stop_loss and not (ZERO < self.stop_loss < self.min_price):
            raise ValueError("favorites: el precio de corte debe estar entre 0 y el precio mínimo del favorito")
        if self.take_profit and not (self.max_price < self.take_profit < ONE):
            raise ValueError("favorites: el precio de cobro debe estar entre el precio máximo a pagar y 1")
        if not (ZERO <= self.settle_minutes <= 240):
            raise ValueError("favorites: los minutos de espera deben estar entre 0 y 240")

    def wants_exits(self) -> bool:
        return bool(self.stop_loss or self.take_profit)

    def takes_profit_in(self, ticker: str) -> bool:
        """True si en este mercado se cobra antes de tiempo (según `take_profit_series`)."""
        if not self.take_profit:
            return False
        return not self.take_profit_series or ticker.upper().startswith(self.take_profit_series)

    def wants_exits_in(self, ticker: str) -> bool:
        return bool(self.stop_loss) or self.takes_profit_in(ticker)

    def exit_intent(self, ctx: MarketContext):
        """Orden para salir ya de la posición, o None si toca esperar."""
        held = abs(ctx.position)
        if not self.wants_exits_in(ctx.ticker) or held == 0:
            return None
        if ctx.exposure / held < self.min_price - ENTRY_TOLERANCE:
            return None  # no la compró esta estrategia (p. ej. un longshot comprado a mano)
        book = ctx.book_ex_own
        if ctx.position > 0:  # YES: se vende al mejor bid
            price = book.best_bid
            if price is None:
                return None
            value, sell = price, ctx.sell_yes
        else:  # NO: se vende comprando YES al mejor ask (el bid de NO es 1 - ask)
            if book.best_ask is None:
                return None
            price = book.best_ask
            value, sell = ONE - price, ctx.buy_yes
        side = "SÍ" if ctx.position > 0 else "NO"
        if self.stop_loss and value <= self.stop_loss:
            reason = f"cortar pérdidas: el {side} cae a {cents(value)} (corte {cents(self.stop_loss)})"
        elif self.takes_profit_in(ctx.ticker) and value >= self.take_profit:
            reason = f"cobrar antes: el {side} ya se paga a {cents(value)}"
        else:
            return None
        intent = sell(price, held, tif=IOC, reason=reason)
        return replace(intent, closes=True) if intent else None

    def settled(self, ticker: str, bid: Optional[Decimal], ask: Optional[Decimal], now: datetime) -> bool:
        """True si el favorito lleva `settle_minutes` seguidos por encima del precio mínimo.

        Se llama en cada vuelta con el libro del mercado: lleva la cuenta de desde cuándo un lado
        cotiza como favorito. Si el precio baja del mínimo, cambia de lado o el bot deja de ver el
        mercado un par de minutos, la cuenta empieza de nuevo.
        """
        if bid is not None and bid >= self.min_price:
            side = "yes"
        elif ask is not None and ONE - ask >= self.min_price:
            side = "no"
        else:
            self._favorite.pop(ticker, None)
            return False
        seen = self._favorite.get(ticker)
        if seen is None or seen[0] != side or now - seen[2] > SETTLE_GAP:
            since = now
        else:
            since = seen[1]
        self._favorite[ticker] = (side, since, now)
        if len(self._favorite) > 500:  # mercados que ya no se miran
            for old in [t for t, v in self._favorite.items() if now - v[2] > SETTLE_GAP]:
                del self._favorite[old]
        if self.settle_skip_series and ticker.upper().startswith(self.settle_skip_series):
            return True
        return now - since >= timedelta(minutes=float(self.settle_minutes))

    def on_market(self, ctx: MarketContext) -> list:
        leave = self.exit_intent(ctx)
        if leave is not None:
            return [leave]
        book = ctx.book_ex_own
        bid, ask = book.best_bid, book.best_ask
        settled = self.settled(ctx.ticker, bid, ask, ctx.now)  # la cuenta sigue aunque ahora no se compre
        if ctx.exit_only:
            return []
        if (bid is not None and bid > self.max_price) or (ask is not None and ONE - ask > self.max_price):
            ctx.why_not = "decided"  # el favorito ya cotiza por encima del precio máximo: queda poco que ganar
            return []
        if bid is None or ask is None:
            ctx.why_not = "empty_book"
            return []
        if ask - bid > self.max_spread:
            ctx.why_not = "wide_spread"
            return []

        if self.min_price <= bid <= self.max_price:
            # YES es el favorito: comprar YES sin cruzar el spread.
            if ctx.position < 0:
                ctx.why_not = "other_side"
                return []  # tiene NO: comprar YES los vendería a lo poco que valen; se espera al final
            room = self.max_position - ctx.position
            if room <= 0:
                ctx.why_not = "position_cap"
                return []
            if not settled:
                ctx.why_not = "settling"
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
            if ctx.position > 0:
                ctx.why_not = "other_side"
                return []  # tiene YES: comprar NO los vendería a lo poco que valen; se espera al final
            room = self.max_position + ctx.position
            if room <= 0:
                ctx.why_not = "position_cap"
                return []
            if not settled:
                ctx.why_not = "settling"
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
        ctx.why_not = "no_favorite"
        return []
