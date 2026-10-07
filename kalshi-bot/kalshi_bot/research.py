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
import math
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
STRATEGY_BAND = (Decimal("0.88"), Decimal("0.97"))  # lo que compra la estrategia favorites
Z95 = Decimal("1.96")


def wilson(successes: int, n: int, z: float = 1.96) -> tuple:
    """Intervalo de Wilson para una proporción: sigue siendo razonable aunque no haya ningún fallo."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def pct(value: Decimal, decimals: int = 2) -> str:
    return f"{value * 100:+.{decimals}f}%"


def verdict(stats: dict) -> str:
    """ "gana", "pierde", "dudoso" o "sin datos", según el intervalo de confianza del 95 %."""
    if not stats.get("contracts") or "ci_low" not in stats:
        return "sin datos"
    if stats["ci_low"] > 0:
        return "gana"
    if stats["ci_high"] < 0:
        return "pierde"
    return "dudoso"


def band_note(stats: dict, subject: str = "La estrategia del bot") -> str:
    """Una frase con el rendimiento de la banda, su margen de error y el veredicto."""
    if not stats.get("contracts"):
        return f"{subject}: no hubo compras de makers entre {stats.get('band', '88–97¢')} en la muestra."
    text = f"{subject} (comprar a {stats['band']} como maker) rindió {pct(stats['return_after_fees'])} tras comisiones"
    if "ci_low" in stats:
        text += f" en {stats['groups']} eventos (margen de error: {pct(stats['ci_low'])} a {pct(stats['ci_high'])})"
    return (
        text
        + {
            "gana": ": gana, y no es casualidad.",
            "pierde": ": pierde dinero.",
            "dudoso": ": con estos datos no se puede asegurar que gane.",
            "sin datos": ".",
        }[verdict(stats)]
    )


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

    def __init__(self, band: tuple = STRATEGY_BAND):
        self.tallies = {role: [Tally() for _ in BUCKETS] for role in ("taker", "maker")}
        # Compras de makers dentro de la banda de la estrategia, agrupadas por evento
        # (los mercados de un evento están ligados): {evento: [coste, neto, contratos, cobrado]}.
        self.band = band
        self.band_groups: dict = {}
        self.markets = 0
        self.trades = 0
        # A nivel de mercado (todas las operaciones de un mercado comparten resultado,
        # así que la muestra efectiva es el número de mercados, no de contratos).
        self.favorite_markets = 0  # mercados donde un maker compró a 90–100¢
        self.favorite_upsets = 0  # ...y ese favorito perdió

    def add_market(self, result: str, trades: list, group: Optional[str] = None) -> None:
        """group: el evento del mercado, para medir el error por eventos y no por contratos."""
        if result not in ("yes", "no"):
            return
        self.markets += 1
        key = group or f"mercado-{self.markets}"
        lo, hi = self.band
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
            if yes_role == "maker" and lo <= price <= hi:
                self._band_add(key, price, count, yes_won)
            if no_role == "maker" and lo <= no_price <= hi:
                self._band_add(key, no_price, count, not yes_won)
            if yes_role == "maker" and price >= FAVORITE_FLOOR:
                favorite_sides.add("yes")
            if no_role == "maker" and no_price >= FAVORITE_FLOOR:
                favorite_sides.add("no")
        if favorite_sides:
            self.favorite_markets += 1
            winner = "yes" if yes_won else "no"
            if winner not in favorite_sides:
                self.favorite_upsets += 1

    def _band_add(self, key: str, price: Decimal, count: Decimal, won: bool) -> None:
        entry = self.band_groups.setdefault(key, [ZERO, ZERO, ZERO, ZERO])
        payout = count if won else ZERO
        cost = price * count
        entry[0] += cost
        entry[1] += payout - cost - MAKER_FEE_RATE * count * price * (ONE - price)
        entry[2] += count
        entry[3] += payout

    def band_summary(self) -> dict:
        """Rendimiento de comprar dentro de la banda como maker, con su intervalo de confianza del 95 %.

        Todas las operaciones de un evento dependen del mismo resultado, así que el
        error se calcula por eventos, no por contratos. Se toma el más amplio de dos
        intervalos: el del cociente ganancia/coste (método delta) y el que sale de la
        proporción de eventos ganados (Wilson). El segundo evita dar por segura una
        serie solo porque en la muestra no hubo ningún batacazo.
        """
        lo, hi = self.band
        groups = [g for g in self.band_groups.values() if g[0] > 0]
        out: dict = {"band": f"{int(lo * 100)}–{int(hi * 100)}¢", "groups": len(groups), "contracts": ZERO}
        if not groups:
            return out
        q = Decimal("0.0001")
        cost = sum((g[0] for g in groups), ZERO)
        net = sum((g[1] for g in groups), ZERO)
        contracts = sum((g[2] for g in groups), ZERO)
        payout = sum((g[3] for g in groups), ZERO)
        ratio = net / cost
        out.update(
            contracts=contracts.quantize(Decimal("0.01")),
            avg_price=(cost / contracts).quantize(q),
            win_rate=(payout / contracts).quantize(q),
            return_after_fees=ratio.quantize(q),
            net_per_contract=(net / contracts).quantize(q),
            losing_groups=sum(1 for g in groups if g[1] < 0),
        )
        n = len(groups)
        if n >= 2:
            spread = sum(((g[1] - ratio * g[0]) ** 2 for g in groups), ZERO) / (n * (n - 1))
            error = Z95 * spread.sqrt() / (cost / n)
            fee_share = (payout - cost - net) / cost
            low_rate, high_rate = (Decimal(str(r)) for r in wilson(n - out["losing_groups"], n))
            price = cost / contracts
            out["ci_low"] = min(ratio - error, low_rate / price - ONE - fee_share).quantize(q)
            out["ci_high"] = max(ratio + error, high_rate / price - ONE - fee_share).quantize(q)
        return out

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
        return {
            "markets": self.markets,
            "trades": self.trades,
            "buckets": rows,
            "strategy": self.band_summary(),
            "conclusions": self.conclusions(),
        }

    def _combined(self, role: Optional[str], indexes: list) -> Tally:
        tally = Tally()
        for i in indexes:
            for r in ("taker", "maker") if role is None else (role,):
                tally.merge(self.tallies[r][i])
        return tally

    def conclusions(self) -> list:
        notes = [band_note(self.band_summary())]
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
        research.add_market(market.result, trades, group=market.event_ticker)
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
    strategy = research.band_summary()
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
        "strategy": strategy,
        "verdict": verdict(strategy),
        "confidence": confidence(research.markets),
    }


def _strategy_return(row: dict) -> Decimal:
    stats = row["strategy"]
    return stats["return_after_fees"] if stats.get("contracts") else Decimal("-9")


def run_sweep(
    client,
    *,
    series_count: int = 12,
    per_series: int = 60,
    min_markets: int = 10,
    closing_within_hours: float = 168,
    trades_pages: int = 2,
    skip_last_minutes: int = 15,
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Compara las series que están activas ahora según cómo le habría ido al bot.

    1. Busca las series con más volumen entre los mercados que cierran pronto
       (lo que el bot operaría).
    2. Para cada serie descarga sus últimos mercados liquidados y sus operaciones,
       sin los últimos minutos antes del cierre (el bot no opera ahí).
    3. Ordena las series por el rendimiento de comprar como maker en la banda de
       la estrategia (88–97¢), con su margen de error.
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
        settled = client.get_markets(
            status="settled", series_ticker=series, limit=min(per_series, 200), max_pages=-(-per_series // 200)
        )
        settled = [m for m in settled if m.result in ("yes", "no") and m.market_type == "binary"][:per_series]
        if len(settled) >= min_markets:
            plan[series] = settled
    total = sum(len(v) for v in plan.values())

    rows, done = [], 0
    overall = Research()
    for series, markets in plan.items():
        research = Research()
        for market in markets:
            if should_stop and should_stop():
                break
            max_ts = None
            if skip_last_minutes and market.close_time is not None:
                max_ts = int(market.close_time.timestamp()) - skip_last_minutes * 60
            try:
                trades = client.get_trades(market.ticker, max_ts=max_ts, max_pages=trades_pages)
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudieron leer las operaciones de %s: %s", market.ticker, exc)
                trades = None
            if trades is not None:
                research.add_market(market.result, trades, group=market.event_ticker)
                overall.add_market(market.result, trades, group=market.event_ticker)
            done += 1
            if progress:
                progress(done, total)
        try:
            info = client.get_series(series)
        except Exception:  # noqa: BLE001 - el título es opcional
            info = {}
        rows.append(series_summary(series, research, info))

    rows.sort(key=_strategy_return, reverse=True)
    summary = overall.band_summary()
    notes = []
    if rows:
        notes.append(band_note(summary, f"En las {len(rows)} series juntas, la estrategia del bot"))
        good = [r for r in rows if r["strategy"].get("contracts") and _strategy_return(r) > 0]
        sure = [r for r in good if r["verdict"] == "gana"]
        if good:
            best = good[0]
            notes.append(
                f"Mejor serie: {best['series']} ({best['title'] or 'sin título'}): "
                f"{pct(_strategy_return(best))} tras comisiones en {best['strategy']['groups']} eventos."
            )
        notes.append(
            f"{len(good)} de {len(rows)} series dan rendimiento positivo; en {len(sure)} el margen de error "
            "permite decir que ganan de verdad."
        )
    else:
        notes.append("No hubo suficientes mercados liquidados para comparar series.")
    notes.append(
        "Los mercados de un mismo evento comparten resultado: la muestra real es el número de eventos. "
        "Con pocos, un solo batacazo cambia mucho el resultado."
    )
    return {
        "generated_at": now.isoformat(),
        "series_analyzed": len(rows),
        "markets": total,
        "skip_last_minutes": skip_last_minutes,
        "overall": summary,
        "overall_verdict": verdict(summary),
        "rows": rows,
        "conclusions": notes,
    }
