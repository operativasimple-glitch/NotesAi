"""Autopsia de una apuesta perdida: cuándo se compró, a qué precio y cómo se dio la vuelta.

Junta tres cosas: las operaciones de tu cuenta en ese mercado (cuándo y a cuánto compró el bot),
el mercado ya decidido (cuándo acabó) y las operaciones públicas de Kalshi (lo que valía tu lado
en cada momento). Con eso cuenta, en una frase, qué pasó y si era algo raro o lo normal a ese precio.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from .council.markets import parse_time
from .names import cents, money

ONE = Decimal(1)


def _dec(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def yes_price(trade: dict) -> Optional[Decimal]:
    """Precio del SÍ de una operación pública (los campos nuevos en dólares, los viejos en centavos)."""
    value = _dec(trade.get("yes_price_dollars"))
    if value is None:
        old = _dec(trade.get("yes_price"))
        value = old / 100 if old is not None else None
    return value if value is not None and 0 < value < 1 else None


def entry_of(trades: list, side: str) -> Optional[tuple]:
    """(cuándo, precio medio, contratos) de las compras de ese lado en el mercado, o None."""
    first, cost, count = None, Decimal(0), Decimal(0)
    for trade in trades:
        when = parse_time(trade.get("time"))
        for leg in trade.get("legs") or []:
            if leg.get("action") != "buy" or leg.get("outcome") != side:
                continue
            n, p = _dec(leg.get("count")), _dec(leg.get("price"))
            if not n or p is None:
                continue
            cost += n * p
            count += n
            if when and (first is None or when < first):
                first = when
    if not count or first is None:
        return None
    return first, cost / count, count


def market_end(market: dict) -> Optional[datetime]:
    """Cuándo se decidió: el cierre o, si llega antes, el final previsto (en los partidos)."""
    times = [t for t in (parse_time(market.get("close_time")), parse_time(market.get("expected_expiration_time"))) if t]
    return min(times) if times else None


def autopsy(loss: dict, account_trades: list, market: dict, public_trades: list) -> Optional[dict]:
    """Qué le pasó a una apuesta perdida. `loss` es una fila de Resultados (ticker, side "SÍ"/"NO", net...)."""
    side = "yes" if loss.get("side") == "SÍ" else "no" if loss.get("side") == "NO" else ""
    entry = entry_of(account_trades, side) if side else None
    end = market_end(market)
    if entry is None or end is None:
        return None
    started, paid, contracts = entry
    path = []
    for t in public_trades:
        when, price = parse_time(t.get("created_time")), yes_price(t)
        if when is None or price is None or not (started <= when <= end):
            continue
        path.append((when, price if side == "yes" else ONE - price))
    path.sort(key=lambda x: x[0])
    hours = (end - started).total_seconds() / 3600
    top = max((p for _, p in path), default=paid)
    hour_before = None
    if hours > 1:
        before = [p for w, p in path if (end - w).total_seconds() >= 3600]
        hour_before = before[-1] if before else None
    if hours <= 1 and top >= Decimal("0.95"):
        how = f"la compró en la última hora; llegó a valer {cents(top)} y se dio la vuelta al final"
    elif hour_before is not None and hour_before >= Decimal("0.75"):
        how = f"una hora antes del final aún valía {cents(hour_before)}: se dio la vuelta al final"
    elif hour_before is not None and hour_before < Decimal("0.5"):
        how = f"se fue torciendo: una hora antes del final ya valía {cents(hour_before)}"
    elif top >= Decimal("0.97"):
        how = f"llegó a valer {cents(top)} y luego se hundió"
    else:
        how = "se torció por el camino"
    miss = ONE - paid
    odds = (
        f"A {cents(paid)}, el mercado le daba un {round(float(miss) * 100)} % de fallar "
        f"(1 de cada {max(2, round(1 / float(miss)))})."
        if miss > 0
        else ""
    )
    side_label = "SÍ" if side == "yes" else "NO"
    name = loss.get("name") or loss.get("ticker", "")
    when_text = f"{hours:.1f} h antes del final".replace(".", ",")
    text = f"«{name}» ({side_label} a {cents(paid)}, {when_text}): {how}. {odds}".strip()
    return {
        "ticker": loss.get("ticker"),
        "name": name,
        "side": side_label,
        "price": float(round(paid, 3)),
        "contracts": float(contracts),
        "entry_at": started.isoformat(),
        "hours_before": round(hours, 2),
        "top": float(top),
        "hour_before": float(hour_before) if hour_before is not None else None,
        "how": how,
        "text": text,
        "net": float(_dec(loss.get("net")) or 0),
        "net_text": money(_dec(loss.get("net")) or Decimal(0), sign=True),
        "closed_at": loss.get("closed_at"),
    }
