"""Estimación de comisiones de Kalshi.

Tabla general de Kalshi: comisión = redondeo hacia arriba al centavo de
    tasa × C × P × (1 − P)
con C = contratos y P = precio en dólares. La tasa general para quien toma
liquidez (taker) es 0.07. Algunas series cobran también a quien deja órdenes
en el libro (maker), típicamente 0.0175. Revisa la tabla de comisiones de
Kalshi si operas mercados con tarifas especiales y ajusta las tasas en la
configuración de la estrategia.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

from .models import CENT, ONE, ZERO

TAKER_FEE_RATE = Decimal("0.07")
MAKER_FEE_RATE = Decimal("0.0175")


def estimate_fee(price: Decimal, count: Decimal, rate: Decimal) -> Decimal:
    """Comisión total estimada de una orden de `count` contratos a `price`."""
    if count <= 0 or rate <= 0:
        return ZERO
    raw = rate * count * price * (ONE - price)
    return raw.quantize(CENT, rounding=ROUND_CEILING)


def fee_per_contract(price: Decimal, count: Decimal, rate: Decimal) -> Decimal:
    """Comisión estimada por contrato (incluye el efecto del redondeo)."""
    if count <= 0:
        return ZERO
    return estimate_fee(price, count, rate) / count
