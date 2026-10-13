"""El veredicto de la Mentora: ¿gana una estrategia de verdad o solo ha tenido suerte?

La semana en la que Kali acertó 23 de 23 no demostraba nada: a esos precios, acertarlas todas
pasa muchas veces por pura suerte. Por eso aquí una estrategia solo queda «lista» cuando:

  - lleva al menos 40 operaciones decididas y 14 días probándose (dos fines de semana),
  - gana dinero con las comisiones pagadas,
  - la probabilidad de que lo ganado sea suerte es menor del 5 %,
  - y nunca ha llegado a perder más de un 30 % desde su mejor momento.

«Probabilidad de que sea suerte»: si alguien hiciera las mismas apuestas, del mismo tamaño y al
mismo precio, pero sin ninguna ventaja (cada apuesta sale con la probabilidad que le daba el
precio), ¿cuántas veces ganaría al menos lo mismo? Se calcula jugándolo 2000 veces. Lo que ya
estaba asegurado al comprar (las parejas SÍ + NO, los tramos que suman 1 $) no depende de la
suerte. Las apuestas de un mismo día y ciudad van algo juntas, así que el número real es un poco
más alto: por eso se pide tan poco (5 %).
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Optional

from ..names import money, plural
from .ledger import Ledger, dec

MIN_OPS = 40
MIN_DAYS = 14
MAX_LUCK = 0.05
MAX_DRAWDOWN = Decimal("0.30")
SIMS = 2000

STATES = {
    "aprendiendo": "Aprendiendo",
    "descartada": "Descartada",
    "arriesgada": "Arriesgada",
    "dudosa": "Puede ser suerte",
    "lista": "Lista",
}


def luck(ops: list, sims: int = SIMS) -> Optional[float]:
    """Probabilidad de ganar al menos lo mismo apostando igual pero sin ventaja ninguna (0 a 1)."""
    if not ops:
        return None
    observed = sum(float(op.get("pnl") or 0) for op in ops)
    bets = [(float(c), float(p)) for op in ops for c, p in op.get("bets") or [] if 0 < float(p) < 1]
    if not bets:
        return 0.0 if observed > 0 else 1.0  # todo estaba asegurado al comprar: no hay suerte
    rng = random.Random(len(ops) * 7919 + len(bets))  # siempre el mismo número con los mismos datos
    hits = 0
    for _ in range(sims):
        total = 0.0
        for count, p in bets:
            total += count * ((1.0 if rng.random() < p else 0.0) - p)
        if total >= observed - 1e-9:
            hits += 1
    return (hits + 1) / (sims + 1)


def pct(value: float) -> str:
    if value < 0.01:
        return "menos de un 1 %"
    return f"un {round(value * 100)} %"


def summary(account: dict, today: str, luck_value: Optional[float]) -> dict:
    """Cómo va una estrategia y qué le falta para poder operar de verdad."""
    ledger = Ledger(account)
    totals = account.get("totals") or {}
    n = int(totals.get("ops") or 0)
    wins = int(totals.get("wins") or 0)
    pnl = dec(totals.get("pnl"))
    spent = dec(totals.get("spent"))
    days = ledger.days(today)
    drawdown = ledger.drawdown()
    equity = ledger.equity()
    checks = [
        {"label": f"Al menos {MIN_OPS} operaciones decididas", "ok": n >= MIN_OPS, "value": f"{n} de {MIN_OPS}"},
        {"label": f"Al menos {MIN_DAYS} días probándose", "ok": days >= MIN_DAYS, "value": f"{days} de {MIN_DAYS}"},
        {"label": "Gana dinero (con comisiones)", "ok": pnl > 0, "value": money(pnl, sign=True)},
        {
            "label": "Menos de un 5 % de que sea suerte",
            "ok": luck_value is not None and pnl > 0 and luck_value < MAX_LUCK,
            "value": "—" if luck_value is None else f"{round(luck_value * 100)} %",
        },
        {
            "label": "Nunca ha perdido más de un 30 % desde su mejor momento",
            "ok": drawdown <= MAX_DRAWDOWN,
            "value": f"{round(float(drawdown) * 100)} %",
        },
    ]
    if n < MIN_OPS or days < MIN_DAYS:
        state = "aprendiendo"
        missing = []
        if n < MIN_OPS:
            missing.append(plural(MIN_OPS - n, "operación", "operaciones"))
        if days < MIN_DAYS:
            missing.append(plural(MIN_DAYS - days, "día", "días"))
        so_far = f" De momento va {money(pnl, sign=True)}." if n else " Todavía no se ha decidido ninguna."
        text = f"Aún no se puede juzgar: le faltan {' y '.join(missing)}.{so_far}"
    elif pnl <= 0:
        state = "descartada"
        text = (
            f"Pierde dinero de mentira ({money(pnl, sign=True)} en {n} operaciones). Así no vale para operar de verdad."
        )
    elif drawdown > MAX_DRAWDOWN:
        state = "arriesgada"
        text = (
            f"Gana {money(pnl, sign=True)}, pero llegó a perder un {round(float(drawdown) * 100)} % desde su mejor "
            "momento: demasiado riesgo para tu dinero."
        )
    elif luck_value is None or luck_value >= MAX_LUCK:
        state = "dudosa"
        text = (
            f"Gana {money(pnl, sign=True)} en {n} operaciones, pero hay {pct(luck_value or 1.0)} de que sea suerte. "
            "Necesita más operaciones para saberlo."
        )
    else:
        state = "lista"
        text = (
            f"Gana {money(pnl, sign=True)} en {n} operaciones y solo hay {pct(luck_value)} de que sea suerte. "
            "Podría operar de verdad en paralelo con Kali, con su propio dinero, pero solo si tú lo apruebas "
            "y le das un presupuesto."
        )
    return {
        "ops": n,
        "wins": wins,
        "losses": int(totals.get("losses") or 0),
        "pnl": float(round(pnl, 2)),
        "roi": float(round(pnl / spent, 4)) if spent else 0.0,
        "equity": float(round(equity, 2)),
        "start": float(dec(account.get("start"))),
        "in_play": float(round(ledger.exposure(), 2)),
        "open": len(ledger.open),
        "days": days,
        "worst_day": float(round(ledger.worst_day(), 2)),
        "drawdown": float(round(drawdown, 4)),
        "luck": None if luck_value is None else round(luck_value, 4),
        "paused": ledger.paused(),
        "state": state,
        "label": STATES[state],
        "text": text,
        "checks": checks,
        "progress": round(min(1.0, n / MIN_OPS) * 0.5 + min(1.0, days / MIN_DAYS) * 0.5, 3),
    }
