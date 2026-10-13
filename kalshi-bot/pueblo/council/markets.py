"""Los mercados que discute el Consejo, ya preparados para que cada miembro opine."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Optional

from ..brackets import bracket
from ..names import market_name
from ..sources.kalshi_public import price
from ..sources.nws import CITIES

WEATHER_SERIES = {f"KXHIGH{key}": key for key in CITIES}
WEATHER_TICKER = re.compile(r"^KXHIGH([A-Z]+)-(\d{2})([A-Z]{3})(\d{2})\b")
MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}


def weather_day(ticker: str) -> Optional[tuple]:
    """("NY", fecha) de un mercado de máxima (KXHIGHNY-26OCT10-B72.5 → Nueva York, 10 oct 2026)."""
    m = WEATHER_TICKER.match(ticker)
    if not m or m.group(1) not in CITIES or m.group(3) not in MONTHS:
        return None
    try:
        return m.group(1), date(2000 + int(m.group(2)), MONTHS[m.group(3)], int(m.group(4)))
    except ValueError:
        return None


def _num(value) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def parse_time(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def topic(market: dict, held: bool = False) -> Optional[dict]:
    """Un mercado para el Consejo: precios en dólares (0–1) y, si es de clima, su ciudad, día y tramo."""
    ticker = market.get("ticker") or ""
    if not ticker:
        return None
    bid, ask = price(market, "yes_bid"), price(market, "yes_ask")
    if bid is None and ask is None and not held:
        return None
    mid = (bid + ask) / 2 if bid is not None and ask is not None else None
    subtitle = market.get("yes_sub_title") or market.get("subtitle") or ""
    day = weather_day(ticker)
    kind = bracket(subtitle, ticker) if day else None
    return {
        "ticker": ticker,
        "name": market_name(ticker, market.get("title", ""), subtitle),
        "title": market.get("title", ""),
        "subtitle": subtitle,
        "rules": (market.get("rules_primary") or "")[:600],
        "kind": "weather" if day and kind else "other",
        "city": day[0] if day else None,
        "date": day[1].isoformat() if day else None,
        "bracket": list(kind) if kind else None,
        "bid": _num(bid),
        "ask": _num(ask),
        "mid": _num(mid),
        "last": _num(price(market, "last_price")),
        "previous": _num(price(market, "previous_price")),
        "volume": _num(market.get("volume_24h_fp") or market.get("volume_24h") or 0),
        "close_time": market.get("close_time"),
        "status": market.get("status", ""),
        "held": held,
    }


def hours_left(topic: dict, now: datetime) -> Optional[float]:
    close = parse_time(topic.get("close_time"))
    if close is None:
        return None
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return (close - now).total_seconds() / 3600
