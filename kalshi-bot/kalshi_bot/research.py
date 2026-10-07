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
from .models import ONE, ZERO, parse_time, to_decimal

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
# Con menos de 5 eventos perdidos (o ganados) la aproximación normal no vale: una racha sin
# batacazos daría un margen casi nulo. Ahí el margen se amplía con el intervalo de Wilson.
MIN_OUTCOMES = 5
# Ventanas de tiempo antes del cierre, en minutos (en deportes, el cierre es el final del partido).
TIME_WINDOWS = [(0, 15), (15, 30), (30, 60), (60, 180), (180, None)]
# Salidas antes de tiempo que se comparan con esperar a la liquidación:
# (nombre, vender si el favorito cae a este precio, cobrar si sube a este precio).
EXIT_RULES = (
    ("vender si cae a 70¢", Decimal("0.70"), None),
    ("vender si cae a 50¢", Decimal("0.50"), None),
    ("vender si cae a 30¢", Decimal("0.30"), None),
    ("cobrar a 98¢", None, Decimal("0.98")),
    ("cobrar a 99¢", None, Decimal("0.99")),
)
# Al vender de golpe se cobra un tick menos que la operación que dispara la salida.
EXIT_SLIPPAGE = Decimal("0.01")


def window_label(lo: int, hi: Optional[int]) -> str:
    def text(minutes: int) -> str:
        return f"{minutes // 60} h" if minutes >= 60 and minutes % 60 == 0 else f"{minutes} min"

    return f"más de {text(lo)} antes" if hi is None else f"{text(lo)}–{text(hi)} antes"


def split_by_window(trades: list, close_ts: float, windows: list) -> dict:
    """Reparte las operaciones según cuánto faltaba para el cierre cuando se hicieron."""
    out: dict = {}
    for t in trades:
        created = parse_time(t.get("created_time"))
        if created is None:
            continue
        minutes = (close_ts - created.timestamp()) / 60
        for lo, hi in windows:
            if minutes >= lo and (hi is None or minutes < hi):
                out.setdefault((lo, hi), []).append(t)
                break
    return out


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


def _clean_trades(trades: list) -> list:
    """Operaciones válidas como (instante Unix o None, precio YES, contratos, lado del taker)."""
    rows = []
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
        created = parse_time(t.get("created_time"))
        rows.append((created.timestamp() if created else None, price, count, taker_side))
    return rows


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

    def __init__(self, band: tuple = STRATEGY_BAND, exit_rules: tuple = ()):
        self.tallies = {role: [Tally() for _ in BUCKETS] for role in ("taker", "maker")}
        # Por regla de salida: {evento: [coste, neto esperando, neto con la regla,
        # contratos vendidos antes, contratos vendidos que habrían perdido]}.
        self.exit_rules = tuple(exit_rules)
        self.exit_groups: dict = {name: {} for name, _, _ in self.exit_rules}
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

    def add_market(
        self, result: str, trades: list, group: Optional[str] = None, entry_cutoff: Optional[float] = None
    ) -> None:
        """group: el evento del mercado, para medir el error por eventos y no por contratos.

        entry_cutoff: instante (Unix) a partir del cual ya no se compra; las operaciones
        posteriores solo sirven para simular las salidas antes de tiempo.
        """
        if result not in ("yes", "no"):
            return
        self.markets += 1
        key = group or f"mercado-{self.markets}"
        lo, hi = self.band
        yes_won = result == "yes"
        rows = _clean_trades(trades)
        entries = [r for r in rows if entry_cutoff is None or r[0] is None or r[0] < entry_cutoff]
        if self.exit_rules:
            self._simulate_exits(key, rows, yes_won, entry_cutoff)
        favorite_sides: set = set()  # lados ("yes"/"no") comprados a 90–100¢ por makers
        for _, price, count, taker_side in entries:
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

    def _simulate_exits(self, key: str, rows: list, yes_won: bool, entry_cutoff: Optional[float]) -> None:
        """Compara, para cada compra de la banda, esperar a la liquidación con cada regla de salida.

        Se sigue el precio de las operaciones posteriores a la compra: si el favorito cae
        hasta el precio de corte, se vende en ese momento (un tick por debajo y pagando
        comisión de taker); si sube hasta el de cobro, se vende a ese precio como maker.
        """
        lo, hi = self.band
        # La API da las operaciones de la más reciente a la más antigua.
        timeline = sorted((r for r in reversed(rows) if r[0] is not None), key=lambda r: r[0])
        yes = [r[1] for r in timeline]
        cache: dict = {}

        def first_after(below: bool, level: Decimal) -> list:
            """Para cada operación, la primera posterior con precio YES <= level (o >= level)."""
            if (below, level) not in cache:
                out: list = [None] * len(yes)
                upcoming = None
                for i in range(len(yes) - 1, -1, -1):
                    out[i] = upcoming
                    if (yes[i] <= level) if below else (yes[i] >= level):
                        upcoming = i
                cache[(below, level)] = out
            return cache[(below, level)]

        for i, (ts, price, count, taker_side) in enumerate(timeline):
            if entry_cutoff is not None and ts >= entry_cutoff:
                continue
            side = "no" if taker_side == "yes" else "yes"  # el maker compró el otro lado
            paid = price if side == "yes" else ONE - price
            if not (lo <= paid <= hi):
                continue
            won = yes_won if side == "yes" else not yes_won
            entry_fee = MAKER_FEE_RATE * paid * (ONE - paid)
            hold = (count if won else ZERO) - (paid + entry_fee) * count
            for name, stop, take in self.exit_rules:
                exits = []
                if stop is not None:
                    j = first_after(side == "yes", stop if side == "yes" else ONE - stop)[i]
                    if j is not None:
                        sold = max((yes[j] if side == "yes" else ONE - yes[j]) - EXIT_SLIPPAGE, ZERO)
                        exits.append((j, sold, TAKER_FEE_RATE))
                if take is not None:
                    j = first_after(side != "yes", take if side == "yes" else ONE - take)[i]
                    if j is not None:
                        exits.append((j, take, MAKER_FEE_RATE))
                group = self.exit_groups[name].setdefault(key, [ZERO] * 6)
                group[0] += paid * count
                group[1] += hold
                group[5] += count
                if not exits:
                    group[2] += hold
                    continue
                _, sold, rate = min(exits, key=lambda e: e[0])
                group[2] += (sold - rate * sold * (ONE - sold) - paid - entry_fee) * count
                group[3] += count
                if not won:
                    group[4] += count

    def exit_summary(self) -> list:
        """Por regla de salida: rendimiento, diferencia con esperar al final y su margen de error.

        Las dos opciones se miden sobre las mismas compras, así que el margen de la
        diferencia sale de la diferencia en cada evento (método delta).
        """
        q = Decimal("0.0001")
        out = []
        for name, _, _ in self.exit_rules:
            groups = [g for g in self.exit_groups[name].values() if g[0] > 0]
            row: dict = {"rule": name, "groups": len(groups)}
            if groups:
                cost, hold, rule, sold, would_lose, contracts = (sum((g[k] for g in groups), ZERO) for k in range(6))
                diff = (rule - hold) / cost
                row.update(
                    return_hold=(hold / cost).quantize(q),
                    return_rule=(rule / cost).quantize(q),
                    difference=diff.quantize(q),
                    sold_share=(sold / contracts).quantize(q),
                    sold_would_lose_share=(would_lose / sold).quantize(q) if sold else ZERO,
                    groups_with_exits=sum(1 for g in groups if g[3] > 0),
                )
                n = len(groups)
                if n >= 2:
                    spread = sum((((g[2] - g[1]) - diff * g[0]) ** 2 for g in groups), ZERO) / (n * (n - 1))
                    error = Z95 * spread.sqrt() / (cost / n)
                    row["diff_low"], row["diff_high"] = (diff - error).quantize(q), (diff + error).quantize(q)
            out.append(row)
        return out

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
        error se calcula por eventos, no por contratos, con el método delta para el
        cociente ganancia/coste. Si hay menos de MIN_OUTCOMES eventos perdidos (o
        ganados), ese cálculo no es fiable y se toma también el intervalo de Wilson
        sobre la proporción de eventos ganados, para no dar por segura una serie solo
        porque en la muestra no hubo ningún batacazo.
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
            low, high = ratio - error, ratio + error
            losing = out["losing_groups"]
            out["margin_method"] = "delta"
            if min(losing, n - losing) < MIN_OUTCOMES:
                fee_share = (payout - cost - net) / cost
                low_rate, high_rate = (Decimal(str(r)) for r in wilson(n - losing, n))
                price = cost / contracts
                low = min(low, low_rate / price - ONE - fee_share)
                high = max(high, high_rate / price - ONE - fee_share)
                out["margin_method"] = "delta+wilson"
            out["ci_low"], out["ci_high"] = low.quantize(q), high.quantize(q)
        return out

    def band_groups_dump(self) -> dict:
        """Los datos por evento (coste, neto, contratos, cobrado), para combinar pruebas."""
        lo, hi = self.band
        return {
            "band": [str(lo), str(hi)],
            "groups": {key: [str(v) for v in values] for key, values in self.band_groups.items()},
        }

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
    by_time: bool = False,
    keep_groups: bool = False,
    exits: bool = False,
    progress: Optional[Callable[[int, int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> dict:
    """Descarga mercados liquidados (y sus operaciones) y devuelve el informe.

    `series` admite varias separadas por comas: entonces se analizan hasta
    `max_markets` de cada una y el informe suma todas (con el detalle por serie).
    Con `by_time`, además separa el resultado de la estrategia según cuánto
    faltaba para el cierre (para saber si la ventaja está solo al final).
    Con `exits`, compara esperar a la liquidación con vender antes de tiempo
    (EXIT_RULES): se descargan también las operaciones de los últimos minutos,
    pero las compras siguen parando `skip_last_minutes` antes del cierre.
    """
    names = [s.strip().upper() for s in (series or "").split(",") if s.strip()]
    plan: list = []
    for name in names or [None]:
        found = client.get_markets(
            status="settled",
            series_ticker=name,
            mve_filter=None if name else "exclude",
            limit=200,
            max_pages=max(1, -(-max_markets // 200)),
        )
        found = [m for m in found if m.result in ("yes", "no") and m.market_type == "binary"][:max_markets]
        plan += [(name, m) for m in found]
    rules = EXIT_RULES if exits else ()
    research = Research(exit_rules=rules)
    by_series = {name: Research(exit_rules=rules) for name in names} if len(names) > 1 else {}
    windows = [w for w in TIME_WINDOWS if w[0] >= skip_last_minutes] if by_time else []
    by_window = {w: Research() for w in windows}
    for i, (name, market) in enumerate(plan, start=1):
        if should_stop and should_stop():
            break
        cutoff = None
        if skip_last_minutes and market.close_time is not None:
            cutoff = int(market.close_time.timestamp()) - skip_last_minutes * 60
        try:
            trades = client.get_trades(market.ticker, max_ts=None if exits else cutoff, max_pages=trades_pages)
        except Exception as exc:  # noqa: BLE001 - un mercado fallido no invalida el resto
            log.warning("No se pudieron leer las operaciones de %s: %s", market.ticker, exc)
            continue
        entry_cutoff = cutoff if exits else None
        research.add_market(market.result, trades, group=market.event_ticker, entry_cutoff=entry_cutoff)
        if name in by_series:
            by_series[name].add_market(market.result, trades, group=market.event_ticker, entry_cutoff=entry_cutoff)
        if windows and market.close_time is not None:
            split = split_by_window(trades, market.close_time.timestamp(), windows)
            for window, part in split.items():
                by_window[window].add_market(market.result, part, group=market.event_ticker)
        if progress:
            progress(i, len(plan))
    report = research.report()
    report.update(
        {
            "series": ", ".join(names) or "(todas)",
            "by_series": {name: r.band_summary() for name, r in by_series.items()},
            "by_time": [{"window": window_label(*w), **by_window[w].band_summary()} for w in windows],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "skip_last_minutes": skip_last_minutes,
        }
    )
    if exits:
        report["exits"] = research.exit_summary()
        report["exits_by_series"] = {name: r.exit_summary() for name, r in by_series.items()}
    if keep_groups:
        report["groups_dump"] = research.band_groups_dump()
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
