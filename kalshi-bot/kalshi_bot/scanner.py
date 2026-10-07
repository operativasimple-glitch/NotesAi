"""Escáner de oportunidades (solo lectura: no envía órdenes).

Busca, con los precios actuales:
  - favoritos: mercados donde un lado cotiza entre fav_min_price y fav_max_price
    con spread estrecho (candidatos para la estrategia `favorites`);
  - spreads amplios: mercados líquidos con mucho hueco entre compra y venta
    (candidatos para `market_maker`);
  - arbitraje en eventos mutuamente excluyentes: si la suma de los mejores bids
    de YES supera 1 $ más comisiones, comprar NO en todos los resultados deja
    beneficio pase lo que pase.

Usa los precios de cabecera que devuelve /markets (no lee cada libro), así que
conviene confirmar en el libro antes de operar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from .discovery import MarketFilter, discover
from .fees import TAKER_FEE_RATE, fee_per_contract
from .models import ONE, ZERO, Market

ARB_LOT = Decimal("10")  # tamaño de referencia para el redondeo de comisiones


@dataclass
class ScanParams:
    closing_within_hours: float = 48.0
    series: list = field(default_factory=list)
    min_volume_24h: Decimal = Decimal("100")
    fav_min_price: Decimal = Decimal("0.88")
    fav_max_price: Decimal = Decimal("0.97")
    max_spread: Decimal = Decimal("0.04")
    wide_spread: Decimal = Decimal("0.06")
    max_markets: int = 2000
    max_events: int = 600
    max_results: int = 30
    include_arbitrage: bool = True


def _hours(market: Market, now: datetime) -> Optional[float]:
    hours = market.hours_to_close(now)
    return round(hours, 1) if hours is not None else None


def favorite_side(market: Market, lo: Decimal, hi: Decimal) -> Optional[tuple]:
    """(lado, bid, ask) del favorito si su bid está entre lo y hi."""
    if market.yes_bid is None or market.yes_ask is None:
        return None
    if lo <= market.yes_bid <= hi:
        return "yes", market.yes_bid, market.yes_ask
    no_bid, no_ask = ONE - market.yes_ask, ONE - market.yes_bid
    if lo <= no_bid <= hi:
        return "no", no_bid, no_ask
    return None


def scan_markets(markets: list, params: ScanParams, now: datetime) -> dict:
    favorites, spreads = [], []
    for m in markets:
        if m.spread is None or m.volume_24h < params.min_volume_24h:
            continue
        fav = favorite_side(m, params.fav_min_price, params.fav_max_price)
        if fav and m.spread <= params.max_spread:
            side, bid, ask = fav
            favorites.append(
                {
                    "ticker": m.ticker,
                    "event_ticker": m.event_ticker,
                    "title": m.title,
                    "subtitle": m.subtitle,
                    "side": side,
                    "bid": bid,
                    "ask": ask,
                    "max_profit": ONE - bid,
                    "hours_to_close": _hours(m, now),
                    "volume_24h": m.volume_24h,
                }
            )
        if m.spread >= params.wide_spread:
            spreads.append(
                {
                    "ticker": m.ticker,
                    "event_ticker": m.event_ticker,
                    "title": m.title,
                    "subtitle": m.subtitle,
                    "bid": m.yes_bid,
                    "ask": m.yes_ask,
                    "spread": m.spread,
                    "hours_to_close": _hours(m, now),
                    "volume_24h": m.volume_24h,
                }
            )
    favorites.sort(key=lambda x: x["volume_24h"], reverse=True)
    spreads.sort(key=lambda x: x["spread"] * x["volume_24h"], reverse=True)
    return {"favorites": favorites[: params.max_results], "spreads": spreads[: params.max_results]}


def event_arbitrage(events: list, fee_rate: Decimal = TAKER_FEE_RATE) -> list:
    """Eventos mutuamente excluyentes donde comprar en todos los resultados deja beneficio."""
    found = []
    for ev in events:
        if not ev.get("mutually_exclusive"):
            continue
        markets = [Market.from_api(m) for m in ev.get("markets") or []]
        markets = [m for m in markets if m.is_active]
        if len(markets) < 2:
            continue
        base = {
            "event_ticker": ev.get("event_ticker", ""),
            "title": ev.get("title", ""),
            "legs": len(markets),
        }
        # Comprar NO en todos: como mucho un resultado sale YES, así que se cobran
        # al menos (n - 1) $ por cada juego de contratos.
        if all(m.yes_bid for m in markets):
            no_prices = [ONE - m.yes_bid for m in markets]
            cost = sum(no_prices, ZERO)
            fees = sum((fee_per_contract(p, ARB_LOT, fee_rate) for p in no_prices), ZERO)
            profit = Decimal(len(markets) - 1) - cost - fees
            if profit > 0:
                found.append(
                    {
                        **base,
                        "kind": "buy_no_all",
                        "description": "Comprar NO en todos los resultados",
                        "cost": cost,
                        "fees": fees,
                        "profit": profit,
                        "sum_yes": sum((m.yes_bid for m in markets), ZERO),
                        "warning": "",
                    }
                )
        # Comprar YES en todos: solo es seguro si un resultado tiene que salir sí o sí.
        if all(m.yes_ask for m in markets):
            yes_prices = [m.yes_ask for m in markets]
            cost = sum(yes_prices, ZERO)
            fees = sum((fee_per_contract(p, ARB_LOT, fee_rate) for p in yes_prices), ZERO)
            profit = ONE - cost - fees
            if profit > 0:
                found.append(
                    {
                        **base,
                        "kind": "buy_yes_all",
                        "description": "Comprar YES en todos los resultados",
                        "cost": cost,
                        "fees": fees,
                        "profit": profit,
                        "sum_yes": cost,
                        "warning": "Solo es arbitraje si alguno de los resultados listados tiene que ocurrir.",
                    }
                )
    found.sort(key=lambda x: x["profit"], reverse=True)
    return found


def run_scan(client, params: ScanParams, now: datetime) -> dict:
    flt = MarketFilter(
        series=list(params.series),
        closing_within_hours=0 if params.series else params.closing_within_hours,
        max_hours_to_close=params.closing_within_hours if params.series else 0,
        min_volume_24h=ZERO,
        max_markets=params.max_markets,
    )
    markets = discover(client, flt, now)
    result = scan_markets(markets, params, now)
    arbitrage: list = []
    if params.include_arbitrage:
        pages = max(1, params.max_events // 200)
        events = client.get_events(status="open", with_nested_markets=True, limit=200, max_pages=pages)
        if params.series:
            events = [e for e in events if e.get("series_ticker") in params.series]
        arbitrage = event_arbitrage(events)[: params.max_results]
    return {
        "generated_at": now.isoformat(),
        "markets_scanned": len(markets),
        "favorites": result["favorites"],
        "spreads": result["spreads"],
        "arbitrage": arbitrage,
    }
