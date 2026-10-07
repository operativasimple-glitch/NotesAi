"""Búsqueda de mercados para el bot y el escáner."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from .models import ZERO, Market

log = logging.getLogger(__name__)

# En deportes el cierre oficial llega hasta ~3 días después del partido: se pide
# a la API un margen y se filtra después por el fin previsto (Market.ends_at).
CLOSE_BUFFER_HOURS = 72
# Lo que una regla por serie puede cambiar (ver MarketFilter.series_rules).
RULE_KEYS = ("min_hours_to_close", "max_hours_to_close", "max_markets_per_event")


@dataclass
class MarketFilter:
    series: list = field(default_factory=list)  # buscar en estas series
    events: list = field(default_factory=list)  # ...y en estos eventos
    closing_within_hours: float = 0.0  # >0: buscar en TODOS los mercados que cierran en estas horas
    min_hours_to_close: float = 0.0
    max_hours_to_close: float = 0.0  # 0 = sin límite
    min_volume_24h: Decimal = ZERO
    max_markets: int = 10
    max_markets_per_event: int = 0  # 0 = sin límite
    exclude_series: list = field(default_factory=list)
    # Ajustes propios por prefijo de serie, p. ej. el clima (abre la víspera y tiene
    # varios tramos de temperatura por día): {"KXHIGH": {"max_hours_to_close": 40}}.
    series_rules: dict = field(default_factory=dict)

    @property
    def searches(self) -> bool:
        return bool(self.series or self.events or self.closing_within_hours > 0)

    def rule(self, series: str, key: str):
        """El ajuste `key` para esta serie: el de la regla de prefijo más largo, o el general."""
        best_prefix, value = "", getattr(self, key)
        for prefix, values in self.series_rules.items():
            if series.startswith(prefix) and key in values and len(prefix) > len(best_prefix):
                best_prefix, value = prefix, values[key]
        return value


def passes(market: Market, flt: MarketFilter, now: datetime) -> bool:
    if not market.is_active or market.series in flt.exclude_series:
        return False
    hours = market.hours_to_close(now)
    if hours is not None:
        if hours < flt.rule(market.series, "min_hours_to_close"):
            return False
        max_hours = flt.rule(market.series, "max_hours_to_close")
        if max_hours and hours > max_hours:
            return False
        if flt.closing_within_hours > 0 and hours > flt.closing_within_hours:
            return False
    return market.volume_24h >= flt.min_volume_24h


def discover(client, flt: MarketFilter, now: datetime, *, exclude: Iterable = (), pages: int = 5) -> list:
    """Mercados activos que cumplen el filtro, de más a menos volumen."""
    found: dict = {}
    for series in flt.series:
        for market in client.get_markets(status="open", series_ticker=series):
            found.setdefault(market.ticker, market)
    for event in flt.events:
        for market in client.get_markets(status="open", event_ticker=event):
            found.setdefault(market.ticker, market)
    if flt.closing_within_hours > 0:
        start = int(now.timestamp() + flt.min_hours_to_close * 3600)
        end = int(now.timestamp() + (flt.closing_within_hours + CLOSE_BUFFER_HOURS) * 3600)
        # Kalshi no admite status=open junto a los filtros de cierre: se filtra después.
        window = client.get_markets(
            status=None, min_close_ts=start, max_close_ts=end, mve_filter="exclude", limit=1000, max_pages=pages
        )
        for market in window:
            found.setdefault(market.ticker, market)

    skip = set(exclude)
    candidates = [m for m in found.values() if m.ticker not in skip and passes(m, flt, now)]
    candidates.sort(key=lambda m: m.volume_24h, reverse=True)
    selected: list = []
    per_event: dict = {}
    for market in candidates:
        if len(selected) >= max(0, flt.max_markets):
            break
        count = per_event.get(market.event_ticker, 0)
        per_event_limit = flt.rule(market.series, "max_markets_per_event")
        if per_event_limit and count >= per_event_limit:
            continue
        per_event[market.event_ticker] = count + 1
        selected.append(market)
    return selected
