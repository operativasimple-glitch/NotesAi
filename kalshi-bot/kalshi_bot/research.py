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
FAVORITE_BUCKETS = [8, 9]  # 90–100¢
LONGSHOT_BUCKETS = [0, 1]  # 0–10¢
FAVORITE_FLOOR = Decimal("0.90")


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
        # A nivel de mercado (todas las operaciones de un mercado comparten resultado,
        # así que la muestra efectiva es el número de mercados, no de contratos).
        self.favorite_markets = 0  # mercados donde un maker compró a 90–100¢
        self.favorite_upsets = 0  # ...y ese favorito perdió

    def add_market(self, result: str, trades: list) -> None:
        if result not in ("yes", "no"):
            return
        self.markets += 1
        yes_won = result == "yes"
        favorite_sides: set = set()  # lados ("yes"/"no") comprados a 90–100¢ por makers
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
            if yes_role == "maker" and price >= FAVORITE_FLOOR:
                favorite_sides.add("yes")
            if no_role == "maker" and no_price >= FAVORITE_FLOOR:
                favorite_sides.add("no")
        if favorite_sides:
            self.favorite_markets += 1
            winner = "yes" if yes_won else "no"
            if winner not in favorite_sides:
                self.favorite_upsets += 1

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


# --------------------------------------------------------------------------
# Barrido: ¿en qué series funciona mejor comprar favoritos?
# --------------------------------------------------------------------------


def confidence(markets: int) -> str:
    """Fiabilidad orientativa según el número de mercados (la muestra efectiva)."""
    if markets >= 150:
        return "alta"
    if markets >= 50:
        return "media"
    return "baja"


def series_summary(series: str, research: Research, info: Optional[dict] = None) -> dict:
    every = list(range(len(BUCKETS)))
    info = info or {}
    return {
        "series": series,
        "title": info.get("title") or "",
        "category": info.get("category") or "",
        "markets": research.markets,
        "trades": research.trades,
        "favorites_maker": research._combined("maker", FAVORITE_BUCKETS).summary(),
        "favorites_all": research._combined(None, FAVORITE_BUCKETS).summary(),
        "longshots_taker": research._combined("taker", LONGSHOT_BUCKETS).summary(),
        "takers": research._combined("taker", every).summary(),
        "makers": research._combined("maker", every).summary(),
        "favorite_markets": research.favorite_markets,
        "favorite_upsets": research.favorite_upsets,
        "confidence": confidence(research.markets),
    }


def _favorite_return(row: dict) -> Decimal:
    stats = row["favorites_maker"]
    return stats["return_after_fees"] if stats.get("contracts") else Decimal("-9")


def run_sweep(
    client,
    *,
    series_count: int = 12,
    per_series: int = 60,
    min_markets: int = 10,
    closing_within_hours: float = 168,
    trades_pages: int = 1,
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compara las series que están activas ahora según cómo les fue a los favoritos.

    1. Busca las series con más volumen entre los mercados que cierran pronto
       (lo que el bot operaría).
    2. Para cada serie descarga sus últimos mercados liquidados y sus operaciones.
    3. Ordena las series por el rendimiento de comprar a 90–100¢ como maker.
    """
    now = now or datetime.now(timezone.utc)
    start, end = int(now.timestamp()), int(now.timestamp() + closing_within_hours * 3600)
    live = client.get_markets(
        status=None, min_close_ts=start, max_close_ts=end, mve_filter="exclude", limit=1000, max_pages=3
    )
    volume: dict = {}
    for market in live:
        if market.is_active:
            volume[market.series] = volume.get(market.series, ZERO) + market.volume_24h
    candidates = sorted(volume, key=lambda s: volume[s], reverse=True)[:series_count]

    plan: dict = {}
    for series in candidates:
        settled = client.get_markets(status="settled", series_ticker=series, limit=min(per_series, 200), max_pages=1)
        settled = [m for m in settled if m.result in ("yes", "no") and m.market_type == "binary"][:per_series]
        if len(settled) >= min_markets:
            plan[series] = settled
    total = sum(len(v) for v in plan.values())

    rows, done = [], 0
    for series, markets in plan.items():
        research = Research()
        for market in markets:
            if should_stop and should_stop():
                break
            try:
                trades = client.get_trades(market.ticker, max_pages=trades_pages)
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudieron leer las operaciones de %s: %s", market.ticker, exc)
                trades = None
            if trades is not None:
                research.add_market(market.result, trades)
            done += 1
            if progress:
                progress(done, total)
        try:
            info = client.get_series(series)
        except Exception:  # noqa: BLE001 - el título es opcional
            info = {}
        rows.append(series_summary(series, research, info))

    rows.sort(key=_favorite_return, reverse=True)
    good = [r for r in rows if r["favorites_maker"].get("contracts") and _favorite_return(r) > 0]
    notes = []
    if good:
        best = good[0]
        notes.append(
            f"Mejor serie para favoritos: {best['series']} ({best['title'] or 'sin título'}): "
            f"{_favorite_return(best) * 100:+.2f}% tras comisiones en {best['markets']} mercados "
            f"(fiabilidad {best['confidence']})."
        )
        notes.append(f"{len(good)} de {len(rows)} series dan rendimiento positivo comprando favoritos como maker.")
    elif rows:
        notes.append("Ninguna serie analizada da rendimiento positivo comprando favoritos: mejor no activarla ahora.")
    else:
        notes.append("No hubo suficientes mercados liquidados para comparar series.")
    notes.append(
        "Las operaciones de un mismo mercado comparten resultado: la muestra real es el número de mercados. "
        "Con menos de 50, un solo batacazo cambia mucho el resultado."
    )
    return {
        "generated_at": now.isoformat(),
        "series_analyzed": len(rows),
        "markets": total,
        "rows": rows,
        "conclusions": notes,
    }
