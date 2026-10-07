"""Investigación con datos reales: ¿quién gana en Kalshi y a qué precios?

Replica el análisis del estudio "Makers and Takers" (ver INVESTIGACION.md) con
mercados recién liquidados. Para cada operación pública hay dos compradores:
uno de YES a p y otro de NO a (1 - p). La API dice qué lado era el taker
(quien cruzó el spread). Con el resultado final del mercado se calcula, por
tramo de precio y por papel (taker/maker), cuánto se pagó, cuánto se cobró y
el rendimiento antes y después de comisiones.

Si en los tramos altos (90-100¢) los makers ganan dinero, la estrategia
`favorites` tiene base en esos mercados; si no, mejor no activarla ahí.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from .fees import MAKER_FEE_RATE, TAKER_FEE_RATE
from .models import ONE, ZERO, to_decimal

log = logging.getLogger(__name__)

BUCKETS = [
    (Decimal("0.00"), Decimal("0.05")),
    (Decimal("0.05"), Decimal("0.10")),
    (Decimal("0.10"), Decimal("0.20")),
    (Decimal("0.20"), Decimal("0.35")),
    (Decimal("0.35"), Decimal("0.50")),
    (Decimal("0.50"), Decimal("0.65")),
    (Decimal("0.65"), Decimal("0.80")),
    (Decimal("0.80"), Decimal("0.90")),
    (Decimal("0.90"), Decimal("0.95")),
    (Decimal("0.95"), Decimal("1.00")),
]
FEE_RATES = {"taker": TAKER_FEE_RATE, "maker": MAKER_FEE_RATE}


def bucket_index(price: Decimal) -> int:
    for i, (lo, hi) in enumerate(BUCKETS):
        if lo <= price < hi:
            return i
    return len(BUCKETS) - 1


@dataclass
class Tally:
    contracts: Decimal = ZERO
    cost: Decimal = ZERO
    payout: Decimal = ZERO
    fees: Decimal = ZERO

    def add(self, price: Decimal, count: Decimal, won: bool, fee_rate: Decimal) -> None:
        self.contracts += count
        self.cost += price * count
        self.payout += count if won else ZERO
        self.fees += fee_rate * count * price * (ONE - price)

    def merge(self, other: "Tally") -> None:
        self.contracts += other.contracts
        self.cost += other.cost
        self.payout += other.payout
        self.fees += other.fees

    def summary(self) -> dict:
        if self.contracts <= 0 or self.cost <= 0:
            return {"contracts": ZERO}
        q = Decimal("0.0001")
        return {
            "contracts": self.contracts.quantize(Decimal("0.01")),
            "avg_price": (self.cost / self.contracts).quantize(q),
            "win_rate": (self.payout / self.contracts).quantize(q),
            "return": (self.payout / self.cost - ONE).quantize(q),
            "return_after_fees": ((self.payout - self.fees) / self.cost - ONE).quantize(q),
        }


class Research:
    """Acumula operaciones de mercados liquidados y resume por tramo de precio."""

    def __init__(self):
        self.tallies = {role: [Tally() for _ in BUCKETS] for role in ("taker", "maker")}
        self.markets = 0
        self.trades = 0

    def add_market(self, result: str, trades: list) -> None:
        if result not in ("yes", "no"):
            return
        self.markets += 1
        yes_won = result == "yes"
        for t in trades:
            if t.get("is_block_trade"):
                continue
            price = to_decimal(t.get("yes_price_dollars"))
            count = to_decimal(t.get("count_fp"), to_decimal(t.get("count")))
            taker_side = t.get("taker_outcome_side") or t.get("taker_side")
            if price is None or count is None or count <= 0 or taker_side not in ("yes", "no"):
                continue
            if not (ZERO < price < ONE):
                continue
            self.trades += 1
            yes_role = "taker" if taker_side == "yes" else "maker"
            no_role = "maker" if yes_role == "taker" else "taker"
            self.tallies[yes_role][bucket_index(price)].add(price, count, yes_won, FEE_RATES[yes_role])
            no_price = ONE - price
            self.tallies[no_role][bucket_index(no_price)].add(no_price, count, not yes_won, FEE_RATES[no_role])

    def report(self) -> dict:
        rows = []
        for i, (lo, hi) in enumerate(BUCKETS):
            both = Tally()
            both.merge(self.tallies["taker"][i])
            both.merge(self.tallies["maker"][i])
            rows.append(
                {
                    "range": f"{int(lo * 100)}–{int(hi * 100)}¢",
                    "low": lo,
                    "high": hi,
                    "taker": self.tallies["taker"][i].summary(),
                    "maker": self.tallies["maker"][i].summary(),
                    "all": both.summary(),
                }
            )
        return {"markets": self.markets, "trades": self.trades, "buckets": rows, "conclusions": self.conclusions()}

    def _combined(self, role: Optional[str], indexes: list) -> Tally:
        tally = Tally()
        for i in indexes:
            for r in ("taker", "maker") if role is None else (role,):
                tally.merge(self.tallies[r][i])
        return tally

    def conclusions(self) -> list:
        notes = []
        cheap = self._combined(None, [0, 1]).summary()
        if cheap.get("contracts"):
            notes.append(
                f"Comprar por debajo de 10¢ rindió {cheap['return'] * 100:+.1f}% "
                f"({cheap['return_after_fees'] * 100:+.1f}% tras comisiones)."
            )
        fav_maker = self._combined("maker", [8, 9]).summary()
        if fav_maker.get("contracts"):
            verdict = (
                "positivo: la estrategia de favoritos tiene base aquí"
                if fav_maker["return_after_fees"] > 0
                else "negativo: la estrategia de favoritos no tiene base en estos mercados"
            )
            notes.append(
                f"Comprar entre 90 y 100¢ como maker rindió {fav_maker['return_after_fees'] * 100:+.2f}% "
                f"tras comisiones ({verdict})."
            )
        taker = self._combined("taker", list(range(len(BUCKETS)))).summary()
        maker = self._combined("maker", list(range(len(BUCKETS)))).summary()
        if taker.get("contracts") and maker.get("contracts"):
            notes.append(
                f"En conjunto, los takers rindieron {taker['return_after_fees'] * 100:+.1f}% y los makers "
                f"{maker['return_after_fees'] * 100:+.1f}% tras comisiones."
            )
        if self.markets < 30:
            notes.append("Pocos mercados: tómalo como orientativo y amplía la muestra.")
        return notes


def run_research(
    client,
    *,
    series: Optional[str] = None,
    max_markets: int = 150,
    skip_last_minutes: int = 0,
    trades_pages: int = 2,
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """Descarga mercados liquidados (y sus operaciones) y devuelve el informe."""
    markets = client.get_markets(
        status="settled",
        series_ticker=series or None,
        mve_filter=None if series else "exclude",
        limit=200,
        max_pages=max(1, -(-max_markets // 200)),
    )
    markets = [m for m in markets if m.result in ("yes", "no") and m.market_type == "binary"][:max_markets]
    research = Research()
    for i, market in enumerate(markets, start=1):
        if should_stop and should_stop():
            break
        max_ts = None
        if skip_last_minutes and market.close_time is not None:
            max_ts = int(market.close_time.timestamp()) - skip_last_minutes * 60
        try:
            trades = client.get_trades(market.ticker, max_ts=max_ts, max_pages=trades_pages)
        except Exception as exc:  # noqa: BLE001 - un mercado fallido no invalida el resto
            log.warning("No se pudieron leer las operaciones de %s: %s", market.ticker, exc)
            continue
        research.add_market(market.result, trades)
        if progress:
            progress(i, len(markets))
    report = research.report()
    report.update(
        {
            "series": series or "(todas)",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "skip_last_minutes": skip_last_minutes,
        }
    )
    return report
