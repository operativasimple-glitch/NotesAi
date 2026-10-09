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

Con `only_bot` solo cuenta lo que hizo el bot (lo que el panel enseña por defecto): sus
órdenes salen de su diario, y en cada mercado se separa lo suyo de lo que hicieras tú a
mano (cada contrato es de quien lo compró). Sin él cuenta toda la cuenta.
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


BOT, YOU = "bot", "you"


@dataclass
class _Money:
    """El dinero de un dueño (el bot o tú) en un mercado."""

    cost: Decimal = ZERO  # lo pagado al comprar
    payout: Decimal = ZERO  # lo cobrado al vender (o al liquidarse)
    fees: Decimal = ZERO
    bought_yes: Decimal = ZERO
    bought_no: Decimal = ZERO
    sold: Decimal = ZERO
    position: Decimal = ZERO  # vista desde YES: + contratos SÍ, - contratos NO
    last_time: Optional[datetime] = None

    def touch(self, when: Optional[datetime]) -> None:
        if when is not None and (self.last_time is None or when > self.last_time):
            self.last_time = when

    def side(self) -> str:
        if self.bought_yes and self.bought_no:
            return "ambos"
        return "yes" if self.bought_yes else "no"


class _Ledger:
    """El dinero de un mercado, llenado a llenado, separado por dueño (el bot o tú).

    Cada compra abre un lote de quien la hizo. Vender, o comprar el lado contrario (en
    Kalshi, comprar SÍ teniendo NO vende primero esos NO), cierra lotes por orden de
    llegada, y lo cobrado es de quien abrió cada lote. Así lo que hagas a mano no cuenta
    como del bot ni al revés. Las comisiones son de quien puso la orden.
    """

    def __init__(self) -> None:
        self.lots: list = []  # [dueño, "yes"/"no", contratos, precio]
        self.owners: dict = {}

    def money(self, owner: str) -> _Money:
        return self.owners.setdefault(owner, _Money())

    def total(self) -> _Money:
        out = _Money()
        for m in self.owners.values():
            out.cost += m.cost
            out.payout += m.payout
            out.fees += m.fees
            out.bought_yes += m.bought_yes
            out.bought_no += m.bought_no
            out.sold += m.sold
            out.position += m.position
            out.touch(m.last_time)
        return out

    def add(self, fill: Fill, owner: str) -> list:
        """Apunta un llenado y devuelve lo que hizo: lotes cerrados (de quien fueran) y el abierto."""
        mine = self.money(owner)
        mine.fees += fill.fee
        mine.touch(fill.time)
        buys_yes = fill.side == BID
        closes = "no" if buys_yes else "yes"
        close_price = ONE - fill.price if buys_yes else fill.price
        left = fill.count
        legs = []
        for lot in self.lots:
            if left <= 0:
                break
            if lot[1] != closes:
                continue
            take = min(lot[2], left)
            theirs = self.money(lot[0])
            theirs.payout += take * close_price
            theirs.sold += take
            theirs.position += take if closes == "no" else -take
            theirs.touch(fill.time)
            lot[2] -= take
            left -= take
            legs.append(
                {
                    "action": "sell",
                    "outcome": closes,
                    "count": take,
                    "price": close_price,
                    "pnl": take * (close_price - lot[3]),
                    "owner": lot[0],
                }
            )
        self.lots = [lot for lot in self.lots if lot[2] > 0]
        if left > 0:
            side = "yes" if buys_yes else "no"
            price = fill.price if buys_yes else ONE - fill.price
            self.lots.append([owner, side, left, price])
            mine.cost += left * price
            if buys_yes:
                mine.bought_yes += left
                mine.position += left
            else:
                mine.bought_no += left
                mine.position -= left
            legs.append({"action": "buy", "outcome": side, "count": left, "price": price, "owner": owner})
        return legs


def _by_time(fills: list) -> list:
    return sorted(fills, key=lambda f: f.time or datetime.min.replace(tzinfo=timezone.utc))


def market_trades(fills: list, ticker: str, bot_orders: Optional[dict] = None) -> list:
    """Las operaciones de un mercado en orden, dichas como compras y ventas de SÍ o de NO.

    Cada llenado se parte en lo que vende de lo que había (de quien fuera: un lote tuyo
    puede cerrarlo una orden del bot y al revés) y lo que compra de nuevo. Cada venta lleva
    lo que ganó o perdió frente a lo que costaron esos contratos (sin comisiones).
    `bot_orders` da el motivo de cada orden del bot (id → motivo); las demás son tuyas.
    """
    bot_orders = bot_orders or {}
    ledger = _Ledger()
    trades = []
    for fill in _by_time([f for f in fills if f.ticker == ticker]):
        owner = BOT if fill.order_id in bot_orders else YOU
        legs: list = []
        for leg in ledger.add(fill, owner):
            same = legs[-1] if legs else None
            if same and same["action"] == leg["action"] == "sell" and same["owner"] == leg["owner"]:
                same["count"] += leg["count"]
                same["pnl"] += leg["pnl"]
            else:
                legs.append(dict(leg))
        for leg in legs:
            if "pnl" in leg:
                leg["pnl"] = leg["pnl"].quantize(MONEY)
        trades.append(
            {
                "time": fill.time.isoformat() if fill.time else None,
                "legs": legs,
                "fee": fill.fee,
                "bot": owner == BOT,
                "reason": bot_orders.get(fill.order_id, ""),
            }
        )
    return trades


def close_markets(fills: list, settlements: list, bot_orders=None, owner: Optional[str] = None) -> tuple:
    """Devuelve (mercados cerrados, tickers con posición aún abierta).

    bot_orders: ids de las órdenes del bot; un mercado es "del bot" si alguno de sus
    llenados viene de una de ellas.
    owner: None = toda la cuenta; "bot" = solo lo que abrió el bot (sus lotes), sin lo
    que hicieras tú a mano en esos mismos mercados.
    """
    bot_orders = bot_orders or set()
    ledgers: dict = {}
    for fill in _by_time(fills):
        ledger = ledgers.setdefault(fill.ticker, _Ledger())
        ledger.add(fill, BOT if fill.order_id in bot_orders else YOU)

    def view(ledger: _Ledger) -> _Money:
        return ledger.money(BOT) if owner == BOT else ledger.total()

    closed: list = []
    settled: set = set()
    for s in settlements:
        settled.add(s.ticker)
        ledger = ledgers.get(s.ticker)
        if s.time is None:
            continue
        if owner == BOT and (ledger is None or BOT not in ledger.owners):
            continue  # el bot no operó aquí
        if ledger is None or ledger.total().position != s.position:
            # Faltan llenados (se abrió antes del periodo): se usa la liquidación tal cual.
            if owner == BOT or (s.cost <= 0 and s.payout <= 0):
                continue  # sin los llenados no se puede separar lo del bot
            side = "ambos" if s.yes_count and s.no_count else "yes" if s.yes_count else "no"
            closed.append(
                ClosedMarket(
                    s.ticker, s.time, side, s.yes_count + s.no_count, s.cost, s.payout, s.fees, False, s.result
                )
            )
            continue
        money = view(ledger)
        value = s.yes_payout()
        held = money.position
        payout = money.payout + (held * value if held > 0 else -held * (ONE - value))
        closed.append(
            ClosedMarket(
                s.ticker,
                s.time,
                money.side(),
                money.bought_yes + money.bought_no,
                money.cost,
                payout,
                money.fees,
                money.sold > 0,
                s.result,
                BOT in ledger.owners,
            )
        )

    still_open: set = set()
    for ticker, ledger in ledgers.items():
        if ticker in settled or (owner == BOT and BOT not in ledger.owners):
            continue
        money = view(ledger)
        if money.position != 0:
            still_open.add(ticker)
        elif (money.sold > 0 or owner == BOT) and money.last_time is not None:
            closed.append(
                ClosedMarket(
                    ticker,
                    money.last_time,
                    money.side(),
                    money.bought_yes + money.bought_no,
                    money.cost,
                    money.payout,
                    money.fees,
                    True,
                    bot=BOT in ledger.owners,
                )
            )
    closed.sort(key=lambda m: m.closed_at, reverse=True)
    return closed, still_open


def _totals(markets: list) -> dict:
    cost = sum((m.cost for m in markets), ZERO)
    net = sum((m.net for m in markets), ZERO)
    fees = sum((m.fees for m in markets), ZERO)
    contracts = sum((m.contracts for m in markets), ZERO)
    rate = Decimal("0.0001")
    return {
        "net": net.quantize(MONEY),
        "cost": cost.quantize(MONEY),
        "payout": sum((m.payout for m in markets), ZERO).quantize(MONEY),
        "fees": fees.quantize(MONEY),
        "markets": len(markets),
        "wins": sum(1 for m in markets if m.net > 0),
        "losses": sum(1 for m in markets if m.net < 0),
        "return": (net / cost).quantize(rate) if cost > 0 else None,
        "contracts": contracts.quantize(MONEY),
        # Precio medio de entrada y el acierto que hace falta para no perder (con comisiones).
        "avg_price": (cost / contracts).quantize(rate) if contracts > 0 else None,
        "breakeven": ((cost + fees) / contracts).quantize(rate) if contracts > 0 else None,
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

    # Ganancia acumulada mercado a mercado, en el orden en que se cerraron.
    curve = []
    running = ZERO
    for m in sorted(in_period, key=lambda m: m.closed_at):
        running += m.net
        curve.append(
            {
                "ticker": m.ticker,
                "date": local_day(m).isoformat(),
                "net": m.net.quantize(MONEY),
                "total": running.quantize(MONEY),
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
        "curve": curve,
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
    closed, _ = close_markets(fills, settlements, bot_orders, owner=BOT if only_bot else None)
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
        recent_limit=200,
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
