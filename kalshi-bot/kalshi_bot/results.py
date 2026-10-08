"""Resultados reales de tu cuenta: lo que ganaste o perdiste en cada mercado cerrado.

Se usan dos fuentes de Kalshi:
  - los llenados (/portfolio/fills): cada compra y venta, con su precio y comisión;
  - las liquidaciones (/portfolio/settlements): cómo resolvió cada mercado en el
    que tenías contratos.

Con los llenados se sigue el dinero que entra y sale de cada mercado, también lo
que se vende antes de tiempo. Un mercado cuenta el día en que se cierra: al
liquidarse o, si se vendió todo antes, con la última venta. Si los llenados no
cuadran con la liquidación (el mercado se abrió antes del periodo descargado),
se usa la liquidación tal cual.

Con `only_bot` solo cuenta lo que compró el bot (lo que el panel enseña por defecto): los
mercados con algún llenado de una orden suya, que salen del diario del bot. Sin él cuenta
toda la cuenta, también lo que se compre a mano.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .models import BID, ONE, ZERO, Fill

# Los mercados del bot se abren como mucho un par de días antes de cerrarse: se piden
# los llenados de unos días antes del periodo para conocer lo que costó cada posición.
LOOKBACK_DAYS = 3
# Rendimiento de la estrategia en la prueba con datos reales (INVESTIGACION.md).
EXPECTED_RETURN = Decimal("0.04")
CATEGORIES = {"partidos": "Partidos", "clima": "Clima", "bolsa": "Bolsa", "otros": "Otros"}
MONEY = Decimal("0.01")


def category_of(ticker: str) -> str:
    series = ticker.split("-", 1)[0].upper()
    if series.startswith(("KXHIGH", "KXLOW")):
        return "clima"
    if series.endswith("GAME"):
        return "partidos"
    if series.startswith(("KXINX", "KXNASDAQ")):
        return "bolsa"
    return "otros"


@dataclass
class ClosedMarket:
    ticker: str
    closed_at: datetime
    side: str  # "yes", "no" o "ambos"
    contracts: Decimal  # contratos comprados
    cost: Decimal  # dinero pagado al comprar
    payout: Decimal  # dinero cobrado (ventas antes de tiempo + liquidación)
    fees: Decimal
    sold_early: bool
    result: str = ""  # cómo resolvió el mercado ("" si se vendió todo antes)
    bot: bool = False  # lo compró (al menos en parte) el bot

    @property
    def net(self) -> Decimal:
        return self.payout - self.cost - self.fees


class _Ledger:
    """El dinero de un mercado, llenado a llenado (posición vista desde YES)."""

    def __init__(self) -> None:
        self.position = ZERO
        self.cost = ZERO
        self.payout = ZERO
        self.fees = ZERO
        self.bought_yes = ZERO
        self.bought_no = ZERO
        self.sold = ZERO
        self.last_time: Optional[datetime] = None
        self.bot = False

    def add(self, fill: Fill) -> None:
        count, price = fill.count, fill.price
        if fill.side == BID:  # compra YES; si tenías NO, primero los vende a (1 - precio)
            closing = min(count, max(-self.position, ZERO))
            self.payout += closing * (ONE - price)
            self.cost += (count - closing) * price
            self.bought_yes += count - closing
            self.position += count
        else:  # vende YES; si no tenías YES, compra NO a (1 - precio)
            closing = min(count, max(self.position, ZERO))
            self.payout += closing * price
            self.cost += (count - closing) * (ONE - price)
            self.bought_no += count - closing
            self.position -= count
        self.sold += closing
        self.fees += fill.fee
        if fill.time is not None and (self.last_time is None or fill.time > self.last_time):
            self.last_time = fill.time

    def side(self) -> str:
        if self.bought_yes and self.bought_no:
            return "ambos"
        return "yes" if self.bought_yes else "no"


def close_markets(fills: list, settlements: list, bot_orders: Optional[set] = None) -> tuple:
    """Devuelve (mercados cerrados, tickers con posición aún abierta).

    bot_orders: ids de las órdenes del bot; un mercado es "del bot" si alguno de sus
    llenados viene de una de ellas.
    """
    bot_orders = bot_orders or set()
    ledgers: dict = {}
    for fill in sorted(fills, key=lambda f: f.time or datetime.min.replace(tzinfo=timezone.utc)):
        ledger = ledgers.setdefault(fill.ticker, _Ledger())
        ledger.add(fill)
        ledger.bot = ledger.bot or fill.order_id in bot_orders

    closed: list = []
    settled: set = set()
    for s in settlements:
        settled.add(s.ticker)
        ledger = ledgers.get(s.ticker)
        if s.time is None:
            continue
        if ledger is None or ledger.position != s.position:
            # Faltan llenados (se abrió antes del periodo): se usa la liquidación tal cual.
            if s.cost <= 0 and s.payout <= 0:
                continue
            side = "ambos" if s.yes_count and s.no_count else "yes" if s.yes_count else "no"
            closed.append(
                ClosedMarket(
                    s.ticker, s.time, side, s.yes_count + s.no_count, s.cost, s.payout, s.fees, False, s.result
                )
            )
            continue
        value = s.yes_payout()
        held = ledger.position
        ledger.payout += held * value if held > 0 else -held * (ONE - value)
        closed.append(
            ClosedMarket(
                s.ticker,
                s.time,
                ledger.side(),
                ledger.bought_yes + ledger.bought_no,
                ledger.cost,
                ledger.payout,
                ledger.fees,
                ledger.sold > 0,
                s.result,
                ledger.bot,
            )
        )

    still_open: set = set()
    for ticker, ledger in ledgers.items():
        if ticker in settled:
            continue
        if ledger.position != 0:
            still_open.add(ticker)
        elif ledger.sold > 0 and ledger.last_time is not None:
            closed.append(
                ClosedMarket(
                    ticker,
                    ledger.last_time,
                    ledger.side(),
                    ledger.bought_yes + ledger.bought_no,
                    ledger.cost,
                    ledger.payout,
                    ledger.fees,
                    True,
                    bot=ledger.bot,
                )
            )
    closed.sort(key=lambda m: m.closed_at, reverse=True)
    return closed, still_open


def _totals(markets: list) -> dict:
    cost = sum((m.cost for m in markets), ZERO)
    net = sum((m.net for m in markets), ZERO)
    return {
        "net": net.quantize(MONEY),
        "cost": cost.quantize(MONEY),
        "payout": sum((m.payout for m in markets), ZERO).quantize(MONEY),
        "fees": sum((m.fees for m in markets), ZERO).quantize(MONEY),
        "markets": len(markets),
        "wins": sum(1 for m in markets if m.net > 0),
        "losses": sum(1 for m in markets if m.net < 0),
        "return": (net / cost).quantize(Decimal("0.0001")) if cost > 0 else None,
    }


def summarize(
    closed: list,
    *,
    now: datetime,
    days: int,
    tz_offset_minutes: int = 0,
    open_positions: Optional[dict] = None,
    recent_limit: int = 30,
    only_bot: bool = False,
) -> dict:
    """Agrupa los mercados cerrados por día (hora local), por tipo de mercado y en totales.

    tz_offset_minutes: lo que da getTimezoneOffset() en el navegador (UTC − hora local).
    only_bot: deja solo los mercados que compró el bot.
    """
    if only_bot:
        closed = [m for m in closed if m.bot]
    local_tz = timezone(-timedelta(minutes=tz_offset_minutes))
    today = now.astimezone(local_tz).date()
    first = today - timedelta(days=days - 1)

    def local_day(m: ClosedMarket) -> date:
        return m.closed_at.astimezone(local_tz).date()

    in_period = [m for m in closed if first <= local_day(m) <= today]
    by_day_markets: dict = {}
    for m in in_period:
        by_day_markets.setdefault(local_day(m), []).append(m)
    by_day = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        items = by_day_markets.get(day, [])
        by_day.append(
            {
                "date": day.isoformat(),
                "net": sum((m.net for m in items), ZERO).quantize(MONEY),
                "markets": len(items),
                "wins": sum(1 for m in items if m.net > 0),
                "losses": sum(1 for m in items if m.net < 0),
            }
        )

    by_category = []
    for key, label in CATEGORIES.items():
        items = [m for m in in_period if category_of(m.ticker) == key]
        if items:
            by_category.append({"key": key, "label": label, **_totals(items)})

    recent = []
    for m in in_period[:recent_limit]:
        recent.append(
            {
                "ticker": m.ticker,
                "title": m.ticker,
                "subtitle": "",
                "category": category_of(m.ticker),
                "side": m.side,
                "contracts": m.contracts.quantize(MONEY),
                "cost": m.cost.quantize(MONEY),
                "payout": m.payout.quantize(MONEY),
                "fees": m.fees.quantize(MONEY),
                "net": m.net.quantize(MONEY),
                "closed_at": m.closed_at.isoformat(),
                "date": local_day(m).isoformat(),
                "sold_early": m.sold_early,
                "result": m.result,
            }
        )

    yesterday = today - timedelta(days=1)
    open_positions = open_positions or {}
    return {
        "days": days,
        "scope": "bot" if only_bot else "all",
        "first_day": first.isoformat(),
        "today": today.isoformat(),
        "generated_at": now.isoformat(),
        "totals": _totals(in_period),
        "today_totals": _totals(by_day_markets.get(today, [])),
        "yesterday_totals": _totals(by_day_markets.get(yesterday, [])) if days > 1 else None,
        "by_day": by_day,
        "by_category": by_category,
        "recent": recent,
        "open": {
            "markets": len(open_positions),
            "exposure": sum((p.exposure for p in open_positions.values()), ZERO).quantize(MONEY),
        },
        "expected_return": EXPECTED_RETURN,
    }


def build_results(
    client,
    *,
    now: datetime,
    days: int,
    tz_offset_minutes: int = 0,
    bot_orders: Optional[set] = None,
    only_bot: bool = False,
) -> dict:
    """Descarga de Kalshi lo necesario y devuelve el resumen para el panel."""
    since = int((now - timedelta(days=days + 1)).timestamp())
    settlements = client.get_settlements(min_ts=since)
    fills = client.get_fill_history(min_ts=since - LOOKBACK_DAYS * 86400)
    closed, _ = close_markets(fills, settlements, bot_orders)
    positions = client.get_positions()
    if only_bot:
        bot_tickers = {f.ticker for f in fills if f.order_id in (bot_orders or set())}
        positions = {t: p for t, p in positions.items() if t in bot_tickers}
    report = summarize(
        closed,
        now=now,
        days=days,
        tz_offset_minutes=tz_offset_minutes,
        open_positions=positions,
        only_bot=only_bot,
    )
    report["bot_history"] = bool(bot_orders)
    tickers = [row["ticker"] for row in report["recent"]]
    titles: dict = {}
    try:
        for start in range(0, len(tickers), 50):
            for market in client.get_markets(status=None, tickers=tickers[start : start + 50]):
                titles[market.ticker] = {"title": market.title, "subtitle": market.subtitle}
    except Exception:  # noqa: BLE001 - los títulos son un extra; sin ellos se ve el ticker
        titles = {}
    for row in report["recent"]:
        info = titles.get(row["ticker"])
        if info:
            row["title"] = info["title"] or row["ticker"]
            row["subtitle"] = info["subtitle"]
    return report
