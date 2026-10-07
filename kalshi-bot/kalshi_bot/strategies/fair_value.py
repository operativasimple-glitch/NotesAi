"""Estrategia "valor justo": tú pones la probabilidad, el bot ejecuta con disciplina.

Para cada mercado le das al bot tu estimación de la probabilidad de que
resuelva YES (tu "valor justo"). El bot:
  - compra YES cuando el precio de venta está por debajo de tu valor justo
    por al menos `min_edge` dólares por contrato DESPUÉS de comisiones;
  - vende YES (equivale a comprar NO) cuando el precio de compra está por
    encima de tu valor justo por al menos `min_edge`.

La ventaja (edge) sale de tu modelo: pronósticos del clima, encuestas,
cuotas de casas de apuestas, datos económicos... El bot solo ejecuta.

Parámetros ([strategy.params] en config.toml):
  fair_values       tabla TICKER = probabilidad (0.35, "35%" o 35)
  fair_values_file  CSV "ticker,probabilidad" que se recarga en caliente;
                    sus valores pisan a los de fair_values
  min_edge          ventaja mínima por contrato tras comisiones (dólares)
  order_size        contratos por orden
  max_position      contratos máximos por mercado (cualquier lado)
  mode              "taker": cruza el spread con órdenes IOC cuando hay ventaja
                    "maker": deja órdenes en el libro a precios con ventaja
  taker_fee_rate / maker_fee_rate   tasas de comisión (ver fees.py)
"""

from __future__ import annotations

import csv
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from ..fees import MAKER_FEE_RATE, TAKER_FEE_RATE, fee_per_contract
from ..models import IOC, ONE, ZERO, to_decimal
from .base import MarketContext, Strategy

log = logging.getLogger(__name__)


def parse_probability(value: Any) -> Optional[Decimal]:
    """Acepta 0.35, "0.35", "35%" o 35 y devuelve Decimal en [0, 1]."""
    text = str(value).strip()
    percent = text.endswith("%")
    number = to_decimal(text.rstrip("%").strip())
    if number is None:
        return None
    if percent or number > 1:
        number = number / 100
    if not (ZERO <= number <= ONE):
        return None
    return number


def load_fair_values_csv(path: Path) -> dict:
    values: dict = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row or not row[0].strip() or row[0].strip().startswith("#"):
                continue
            if len(row) < 2:
                log.warning("Línea ignorada en %s: %s", path, row)
                continue
            ticker, prob = row[0].strip(), parse_probability(row[1])
            if prob is None:
                if ticker.lower() != "ticker":  # cabecera
                    log.warning("Probabilidad inválida para %s en %s: %r", ticker, path, row[1])
                continue
            values[ticker] = prob
    return values


class FairValueStrategy(Strategy):
    name = "fair_value"

    def __init__(self, params: Optional[dict] = None):
        super().__init__(params)
        self.min_edge = self.dec("min_edge", "0.04")
        self.order_size = self.dec("order_size", "2")
        self.max_position = self.dec("max_position", "10")
        self.mode = str(self.params.get("mode", "taker")).lower()
        if self.mode not in ("taker", "maker"):
            raise ValueError("fair_value: 'mode' debe ser 'taker' o 'maker'")
        self.taker_fee_rate = self.dec("taker_fee_rate", TAKER_FEE_RATE)
        self.maker_fee_rate = self.dec("maker_fee_rate", MAKER_FEE_RATE)

        self.config_values: dict = {}
        for ticker, raw in (self.params.get("fair_values") or {}).items():
            prob = parse_probability(raw)
            if prob is None:
                raise ValueError(f"fair_value: probabilidad inválida para {ticker}: {raw!r}")
            self.config_values[ticker] = prob

        file_param = self.params.get("fair_values_file")
        self.values_file: Optional[Path] = Path(file_param) if file_param else None
        self.file_values: dict = {}
        self._file_mtime: Optional[float] = None
        self._warned_missing = False
        self.refresh()

    # --- valores justos -----------------------------------------------------

    def refresh(self) -> None:
        if self.values_file is None:
            return
        try:
            mtime = self.values_file.stat().st_mtime
        except FileNotFoundError:
            if not self._warned_missing:
                log.info("No existe %s todavía; se usan solo los valores de config.toml", self.values_file)
                self._warned_missing = True
            self.file_values = {}
            self._file_mtime = None
            return
        if mtime != self._file_mtime:
            self.file_values = load_fair_values_csv(self.values_file)
            self._file_mtime = mtime
            self._warned_missing = False
            log.info("Valores justos cargados de %s: %d mercados", self.values_file, len(self.file_values))

    def fair_value(self, ticker: str) -> Optional[Decimal]:
        if ticker in self.file_values:
            return self.file_values[ticker]
        return self.config_values.get(ticker)

    def suggested_tickers(self) -> list:
        return sorted(set(self.config_values) | set(self.file_values))

    # --- precios límite ------------------------------------------------------

    def max_buy_price(self, ctx: MarketContext, fv: Decimal, fee_rate: Decimal) -> Optional[Decimal]:
        """Precio más alto al que comprar YES deja al menos min_edge tras comisión."""
        price = ctx.floor(fv - self.min_edge)
        while price is not None and price > 0:
            if fv - price - fee_per_contract(price, self.order_size, fee_rate) >= self.min_edge:
                return price
            price = ctx.tick_below(price)
        return None

    def min_sell_price(self, ctx: MarketContext, fv: Decimal, fee_rate: Decimal) -> Optional[Decimal]:
        """Precio más bajo al que vender YES deja al menos min_edge tras comisión."""
        price = ctx.ceil(fv + self.min_edge)
        while price is not None and price < 1:
            if price - fv - fee_per_contract(price, self.order_size, fee_rate) >= self.min_edge:
                return price
            price = ctx.tick_above(price)
        return None

    # --- lógica --------------------------------------------------------------

    def on_market(self, ctx: MarketContext) -> list:
        fv = self.fair_value(ctx.ticker)
        if fv is None:
            return []
        if self.mode == "taker":
            return self._taker(ctx, fv)
        return self._maker(ctx, fv)

    def _taker(self, ctx: MarketContext, fv: Decimal) -> list:
        book = ctx.book_ex_own
        intents = []
        if ctx.position < self.max_position and book.best_ask is not None:
            limit = self.max_buy_price(ctx, fv, self.taker_fee_rate)
            if limit is not None and book.best_ask <= limit:
                qty = min(self.order_size, self.max_position - ctx.position, book.ask_size_up_to(limit))
                intents.append(ctx.buy_yes(limit, qty, tif=IOC, reason=f"valor justo {fv} vs ask {book.best_ask}"))
        if ctx.position > -self.max_position and book.best_bid is not None:
            limit = self.min_sell_price(ctx, fv, self.taker_fee_rate)
            if limit is not None and book.best_bid >= limit:
                qty = min(self.order_size, self.max_position + ctx.position, book.bid_size_down_to(limit))
                intents.append(ctx.sell_yes(limit, qty, tif=IOC, reason=f"valor justo {fv} vs bid {book.best_bid}"))
        return [i for i in intents if i is not None]

    def _maker(self, ctx: MarketContext, fv: Decimal) -> list:
        book = ctx.book_ex_own
        intents = []
        if ctx.position < self.max_position:
            price = self.max_buy_price(ctx, fv, self.maker_fee_rate)
            if price is not None and book.best_ask is not None and price >= book.best_ask:
                price = ctx.tick_below(book.best_ask)  # post-only: no cruzar el libro
            if price is not None:
                qty = min(self.order_size, self.max_position - ctx.position)
                intents.append(ctx.buy_yes(price, qty, post_only=True, reason=f"valor justo {fv}"))
        if ctx.position > -self.max_position:
            price = self.min_sell_price(ctx, fv, self.maker_fee_rate)
            if price is not None and book.best_bid is not None and price <= book.best_bid:
                price = ctx.tick_above(book.best_bid)
            if price is not None:
                qty = min(self.order_size, self.max_position + ctx.position)
                intents.append(ctx.sell_yes(price, qty, post_only=True, reason=f"valor justo {fv}"))
        return [i for i in intents if i is not None]
