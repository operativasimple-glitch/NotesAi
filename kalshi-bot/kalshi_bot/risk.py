"""Gestión de riesgo: todo lo que pide una estrategia pasa por aquí antes de
llegar al exchange."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Optional

from .models import BID, ZERO, Market, fmt_count


@dataclass
class RiskLimits:
    max_order_contracts: Decimal = Decimal("5")
    max_position_per_market: Decimal = Decimal("20")
    max_total_exposure: Decimal = Decimal("50")  # dólares comprometidos en total
    max_session_loss: Decimal = Decimal("20")  # dólares; 0 = desactivado
    min_price: Decimal = Decimal("0.02")
    max_price: Decimal = Decimal("0.98")
    min_minutes_to_close: float = 15.0


def whole_contracts(value: Decimal) -> Decimal:
    """El bot opera contratos enteros."""
    return max(ZERO, value.to_integral_value(rounding=ROUND_FLOOR))


class RiskManager:
    def __init__(self, limits: RiskLimits):
        self.limits = limits
        self.start_equity: Optional[Decimal] = None

    def start_session(self, equity: Decimal) -> None:
        self.start_equity = equity

    def check_loss(self, equity: Decimal) -> Optional[str]:
        """Devuelve un motivo si la pérdida de la sesión supera el límite."""
        if self.start_equity is None or self.limits.max_session_loss <= 0:
            return None
        loss = self.start_equity - equity
        if loss >= self.limits.max_session_loss:
            return f"pérdida de la sesión ${loss:.2f} >= límite ${self.limits.max_session_loss}"
        return None

    def market_block_reason(self, market: Market, now: datetime) -> Optional[str]:
        """Motivo por el que no se debe operar este mercado ahora (o None)."""
        if not market.is_active:
            return f"mercado no activo (estado: {market.status or 'desconocido'})"
        hours = market.hours_to_close(now)
        if hours is not None and hours * 60 < self.limits.min_minutes_to_close:
            return f"cierra en {max(hours * 60, 0):.0f} min (mínimo {self.limits.min_minutes_to_close:.0f})"
        return None

    def filter_intents(self, intents: list, *, position: Decimal, committed_exposure: Decimal) -> tuple:
        """Aplica los límites a las órdenes deseadas de UN mercado.

        position: posición actual en ese mercado (+YES / -NO).
        committed_exposure: dólares ya comprometidos en el portafolio
            (posiciones + órdenes en reposo de OTROS mercados).

        Devuelve (órdenes aprobadas, posiblemente recortadas; notas).
        Las órdenes que reducen una posición existente siempre caben en el
        presupuesto de exposición, para que el bot pueda salir de posiciones.
        """
        lim = self.limits
        approved: list = []
        notes: list = []
        bid_room = lim.max_position_per_market - position
        ask_room = lim.max_position_per_market + position
        closable_by_bid = max(ZERO, -position)  # comprar YES cierra contratos NO
        closable_by_ask = max(ZERO, position)  # vender YES cierra contratos YES
        budget = lim.max_total_exposure - committed_exposure

        for intent in intents:
            if not (lim.min_price <= intent.price <= lim.max_price):
                notes.append(f"rechazada (precio fuera de [{lim.min_price}, {lim.max_price}]): {intent.describe()}")
                continue

            count = min(intent.count, lim.max_order_contracts, bid_room if intent.side == BID else ask_room)
            count = whole_contracts(count)
            if count <= 0:
                notes.append(f"rechazada (límite de posición o tamaño): {intent.describe()}")
                continue

            closable = closable_by_bid if intent.side == BID else closable_by_ask
            closing = min(count, closable)
            opening = count - closing
            unit_cost = intent.cost_per_contract()
            if opening * unit_cost > budget:
                affordable = whole_contracts(budget / unit_cost) if unit_cost > 0 and budget > 0 else ZERO
                opening = min(opening, affordable)
                count = closing + opening
                if count <= 0:
                    notes.append(f"rechazada (exposición total máxima ${lim.max_total_exposure}): {intent.describe()}")
                    continue
            budget -= opening * unit_cost

            if intent.side == BID:
                bid_room -= count
                closable_by_bid -= closing
            else:
                ask_room -= count
                closable_by_ask -= closing

            if count != intent.count:
                notes.append(
                    f"recortada de {fmt_count(intent.count)} a {fmt_count(count)} contratos: {intent.describe()}"
                )
            approved.append(replace(intent, count=count))
        return approved, notes
