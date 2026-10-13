"""Las apuestas de mentira del Consejo y su marcador ("¿Quién acertó?").

Cada miembro apuesta 1 contrato cuando su precio justo se separa bastante del precio del mercado:
compra el SÍ al precio de venta si cree que vale más, o el NO si cree que vale menos. Paga la
comisión de Kalshi como si fuera de verdad. Cuando el mercado se decide, se apunta lo que habría
ganado o perdido. Así se ve qué manera de pensar gana dinero sin arriesgar ni un céntimo.
"""

from __future__ import annotations

import math
from typing import Optional

EDGE = 0.02  # ventaja mínima (ya restada la comisión) para apostar
MAX_HOURS = 36.0  # solo mercados que se deciden pronto: el marcador avanza cada día


def fee(price: float) -> float:
    """Comisión de Kalshi por contrato al comprar al precio de venta: 7 % × p × (1 − p), al céntimo de arriba."""
    return math.ceil(round(0.07 * price * (1 - price) * 100, 6)) / 100


def decide(fair: float, topic: dict) -> Optional[tuple]:
    """("SÍ" | "NO", precio) si la ventaja compensa; None si no."""
    ask, bid = topic.get("ask"), topic.get("bid")
    if ask is not None and fair - ask - fee(ask) >= EDGE:
        return "SÍ", ask
    if bid is not None:
        no = round(1 - bid, 4)
        if (1 - fair) - no - fee(no) >= EDGE:
            return "NO", no
    return None


def pnl(trade: dict, result: str) -> float:
    """Lo que habría ganado (o perdido) un contrato, con la comisión."""
    won = (result == "yes") == (trade["side"] == "SÍ")
    return round((1 - trade["price"] if won else -trade["price"]) - trade["fee"], 4)


def record(settled: list) -> dict:
    """Resumen de las apuestas ya decididas de un miembro."""
    trades = len(settled)
    wins = sum(1 for t in settled if t["pnl"] > 0)
    gained = round(sum(t["pnl"] for t in settled), 4)
    cost = sum(t["price"] + t["fee"] for t in settled)
    return {
        "trades": trades,
        "wins": wins,
        "pnl": gained,
        "roi": round(gained / cost, 4) if cost else 0.0,
    }


def board(members: list, books: dict, settled: dict) -> list:
    """El marcador: de quien más ha ganado (de mentira) a quien menos."""
    rows = []
    for m in members:
        done = settled.get(m["id"]) or []
        rows.append({**m, **record(done), "open": len(books.get(m["id"]) or {})})
    rows.sort(key=lambda r: (-r["pnl"], -r["wins"], r["name"]))
    return rows
