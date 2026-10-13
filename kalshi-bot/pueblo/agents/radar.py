"""Radar, el explorador: busca en Kalshi favoritos claros en series que Kali no sigue."""

from __future__ import annotations

import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal

from ..names import cents, market_name, plural
from ..sources.kalshi_public import KalshiPublic, dec, price
from .base import Agent

BAND = (Decimal("0.88"), Decimal("0.97"))
MAX_SPREAD = Decimal("0.04")
SERIES_NAMES = {
    "KXNHLGAME": "NHL",
    "KXNFLGAME": "NFL",
    "KXNBAGAME": "NBA",
    "KXWNBAGAME": "WNBA",
    "KXMLBGAME": "MLB",
    "KXNCAAFGAME": "fútbol americano universitario",
    "KXNCAAMBGAME": "baloncesto universitario",
    "KXMLSGAME": "MLS",
    "KXEPLGAME": "Premier League",
    "KXUCLGAME": "Champions",
    "KXLALIGAGAME": "LaLiga",
    "KXATPMATCH": "tenis ATP",
    "KXWTAMATCH": "tenis WTA",
}


def series_of(ticker: str) -> str:
    return ticker.split("-", 1)[0].upper()


def series_label(series: str) -> str:
    if series in SERIES_NAMES:
        return SERIES_NAMES[series]
    if series.startswith("KXHIGH"):
        return "temperatura máxima"
    if series.startswith("KXLOW"):
        return "temperatura mínima"
    return series


def favorite(market: dict):
    """(lado, precio) si el mercado tiene un favorito claro en la banda del bot y poco spread."""
    bid, ask = price(market, "yes_bid"), price(market, "yes_ask")
    if bid is None or ask is None or ask - bid > MAX_SPREAD:
        return None
    if BAND[0] <= bid <= BAND[1]:
        return "SÍ", bid
    no_bid = 1 - ask
    if BAND[0] <= no_bid <= BAND[1]:
        return "NO", no_bid
    return None


class Radar(Agent):
    id = "radar"
    name = "Radar"
    role = "Explorador: busca oportunidades en Kalshi"
    about = (
        "Cada 20 minutos recorre los mercados de Kalshi que cierran en las próximas 12 horas y cuenta los "
        "favoritos claros (88–97¢, poco spread) que Kali no está mirando. Solo mira datos públicos."
    )
    home = "laboratorio"
    color = "#6ee7a8"
    interval = 1200.0
    horizon_hours = 12.0

    def __init__(self, world, kalshi: KalshiPublic, clock=time.time):
        super().__init__(world, clock)
        self.kalshi = kalshi

    def tick(self, now: float) -> None:
        markets = self.kalshi.markets_closing(self.horizon_hours, now=now)
        followed = set((self.peer("kali").get("detail") or {}).get("markets") or [])
        followed_series = {series_of(t) for t in followed}
        found = []
        for market in markets:
            fav = favorite(market)
            if fav is None:
                continue
            volume = dec(market.get("volume_24h_fp")) or dec(market.get("volume_24h")) or Decimal(0)
            if volume < 50:
                continue
            ticker = market.get("ticker", "")
            found.append(
                {
                    "ticker": ticker,
                    "series": series_of(ticker),
                    "name": market_name(ticker, market.get("title", ""), market.get("yes_sub_title", "")),
                    "side": fav[0],
                    "price": str(fav[1]),
                    "closes": market.get("close_time"),
                    "followed": ticker in followed,
                }
            )
        others = [f for f in found if f["series"] not in followed_series]
        by_series = Counter(f["series"] for f in others)
        top = ", ".join(f"{series_label(s)} ({n})" for s, n in by_series.most_common(4))
        memory = self.memory
        last_total = memory.get("last_total")
        quiet = now - memory.get("told_at", 0) < 7200
        if others and (last_total is None or abs(len(others) - last_total) >= 3 or not quiet):
            if last_total is not None or not quiet:
                self.say(
                    f"He visto {len(others)} favoritos claros que Kali no mira: {top}.",
                    to="kali",
                    count=len(others),
                )
                memory["told_at"] = now
        memory["last_total"] = len(others)
        examples = sorted(others, key=lambda f: f["closes"] or "")[:8]
        for f in examples:
            f["label"] = f"{f['name']} · {f['side']} a {cents(Decimal(f['price']))}"
        self.doing("laboratorio", f"mira {plural(len(markets), 'mercado', 'mercados')} · {len(found)} favoritos")
        self.status(
            f"{plural(len(found), 'favorito', 'favoritos')} a la vista · {len(others)} fuera de la lista de Kali",
            mood="ok",
            scanned=len(markets),
            favorites=len(found),
            outside=len(others),
            by_series=[{"series": s, "label": series_label(s), "count": n} for s, n in by_series.most_common(8)],
            examples=examples,
            at=datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        )

    def talk(self) -> str:
        detail = self.world.bot(self.id).get("detail") or {}
        if not detail:
            return "Todavía no he dado mi primera vuelta por los mercados."
        series = detail.get("by_series") or []
        if not series:
            return f"He mirado {detail.get('scanned', 0)} mercados y Kali ya sigue todos los favoritos que he visto."
        top = ", ".join(f"{s['label']} ({s['count']})" for s in series[:3])
        return (
            f"De {detail.get('scanned', 0)} mercados, {detail.get('outside', 0)} favoritos"
            f" están fuera de lo que mira Kali: {top}."
        )
