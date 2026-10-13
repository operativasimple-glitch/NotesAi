"""Gestión de riesgo: todo lo que pide una estrategia pasa por aquí antes de
llegar al exchange."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Optional

from .models import BID, ZERO, Market, fmt_count

log = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    max_order_contracts: Decimal = Decimal("5")
    max_position_per_market: Decimal = Decimal("20")
    max_total_exposure: Decimal = Decimal("50")  # dólares comprometidos en total
    max_session_loss: Decimal = Decimal("20")  # dólares; 0 = desactivado
    # Mercados de un mismo evento con dinero a la vez (un partido, un día de clima). Sus
    # mercados son casi la misma apuesta: si falla uno, suele fallar el de al lado. 0 = sin límite.
    max_positions_per_event: int = 1
    min_price: Decimal = Decimal("0.02")
    max_price: Decimal = Decimal("0.98")
    min_minutes_to_close: float = 15.0
    # Según el saldo (dinero + apuestas abiertas), en tanto por uno; 0 = desactivado. Con poco
    # dinero, que un fallo no se lleve lo ganado en una semana ni un mal día toda la cuenta.
    max_market_pct: Decimal = ZERO  # parte del saldo en un mismo mercado
    max_exposure_pct: Decimal = ZERO  # parte del saldo en juego a la vez
    max_daily_loss_pct: Decimal = ZERO  # si en el día el saldo baja esto, no compra hasta el día siguiente


def whole_contracts(value: Decimal) -> Decimal:
    """El bot abre posiciones con contratos enteros."""
    return max(ZERO, value.to_integral_value(rounding=ROUND_FLOOR))


def percent(value: Decimal) -> str:
    """0.15 → "15 %"."""
    return f"{(value * 100).normalize():f} %"


class RiskManager:
    def __init__(self, limits: RiskLimits, day_file: Optional[Path] = None, confirm_ticks: int = 3):
        self.limits = limits
        self.start_equity: Optional[Decimal] = None
        # La pausa salta si la bajada se ve en varias vueltas seguidas: un saldo mal leído una
        # vez (la API a veces tarda en contar las apuestas abiertas) no para el bot todo el día.
        self.confirm_ticks = max(1, confirm_ticks)
        self._below = 0
        # El día en curso (hora local) y con qué saldo empezó. Se guarda en disco para que un
        # reinicio del bot (cada vez que se sube un cambio) no borre la pausa ni el punto de partida.
        self.day_file = day_file
        self.day: Optional[date] = None
        self.day_start_equity: Optional[Decimal] = None
        self.paused_day: Optional[date] = None
        self._load_day()

    def start_session(self, equity: Decimal) -> None:
        self.start_equity = equity

    def track_day(self, equity: Decimal, today: date) -> Optional[str]:
        """Pausa de compras si hoy el saldo ya ha bajado `max_daily_loss_pct`; dura hasta mañana.

        Devuelve el motivo mientras dura (o None). Lo ya apostado sigue hasta el final.
        """
        if self.day != today:
            self.day, self.day_start_equity, self._below = today, equity, 0
            self._save_day()
        pct = self.limits.max_daily_loss_pct
        start = self.day_start_equity
        if pct <= 0 or start is None or start <= 0:
            return None
        self._below = self._below + 1 if start - equity >= pct * start else 0
        if self.paused_day != today and self._below >= self.confirm_ticks:
            self.paused_day = today
            self._save_day()
        if self.paused_day == today:
            return f"pausa hasta mañana: hoy el saldo bajó más del {percent(pct)} (empezó el día con ${start:.2f})"
        return None

    def day_status(self) -> dict:
        return {
            "day": self.day.isoformat() if self.day else None,
            "start_equity": self.day_start_equity,
            "paused": self.paused_day is not None and self.paused_day == self.day,
            "max_daily_loss_pct": self.limits.max_daily_loss_pct,
        }

    def _load_day(self) -> None:
        if self.day_file is None:
            return
        try:
            data = json.loads(self.day_file.read_text(encoding="utf-8"))
            self.day = date.fromisoformat(data["day"])
            self.day_start_equity = Decimal(str(data["start_equity"]))
            self.paused_day = self.day if data.get("paused") else None
        except FileNotFoundError:
            return
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            log.warning("No se pudo leer %s (%s): el día empieza de nuevo", self.day_file, exc)
            self.day = self.day_start_equity = self.paused_day = None

    def _save_day(self) -> None:
        if self.day_file is None or self.day is None:
            return
        data = {
            "day": self.day.isoformat(),
            "start_equity": str(self.day_start_equity),
            "paused": self.paused_day == self.day,
        }
        try:
            self.day_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.day_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            tmp.replace(self.day_file)
        except OSError as exc:
            log.warning("No se pudo guardar %s: %s", self.day_file, exc)

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

    def filter_intents(
        self,
        intents: list,
        *,
        position: Decimal,
        committed_exposure: Decimal,
        equity: Optional[Decimal] = None,
        market_cost: Decimal = ZERO,
    ) -> tuple:
        """Aplica los límites a las órdenes deseadas de UN mercado.

        position: posición actual en ese mercado (+YES / -NO).
        committed_exposure: dólares ya comprometidos en el portafolio
            (posiciones + órdenes en reposo de OTROS mercados).
        equity: valor de la cuenta (dinero + apuestas abiertas), para los límites según el saldo.
        market_cost: lo que costó la posición que ya hay en este mercado.

        Devuelve (órdenes aprobadas, posiblemente recortadas; notas).
        La parte de una orden que reduce una posición existente no gasta
        presupuesto de exposición ni cuenta para el tamaño máximo por orden, y
        puede tener cualquier precio válido: así el bot siempre puede salir.
        Esa parte puede ser una fracción de contrato: Kalshi deja operar
        fracciones y una venta a medias puede dejar, p. ej., 0,85 contratos.
        Lo que abre posición nueva va siempre en contratos enteros.
        """
        lim = self.limits
        approved: list = []
        notes: list = []
        bid_room = lim.max_position_per_market - position
        ask_room = lim.max_position_per_market + position
        closable_by_bid = max(ZERO, -position)  # comprar YES cierra contratos NO
        closable_by_ask = max(ZERO, position)  # vender YES cierra contratos YES
        budget = lim.max_total_exposure - committed_exposure
        budget_note = f"exposición total máxima ${lim.max_total_exposure}"
        market_budget: Optional[Decimal] = None
        market_note = ""
        if equity is not None and equity > 0:
            if lim.max_exposure_pct > 0:
                cap = lim.max_exposure_pct * equity
                if cap - committed_exposure < budget:
                    budget = cap - committed_exposure
                    budget_note = f"máximo en juego: {percent(lim.max_exposure_pct)} del saldo, ${cap:.2f}"
            if lim.max_market_pct > 0:
                cap = lim.max_market_pct * equity
                market_budget = cap - market_cost
                market_note = f"máximo en un mercado: {percent(lim.max_market_pct)} del saldo, ${cap:.2f}"

        for intent in intents:
            closable = closable_by_bid if intent.side == BID else closable_by_ask
            room = bid_room if intent.side == BID else ask_room
            closing = max(ZERO, min(intent.count, closable))
            opening = whole_contracts(min(intent.count - closing, room - closing, lim.max_order_contracts - closing))
            if not (lim.min_price <= intent.price <= lim.max_price):
                if closing <= 0:
                    notes.append(f"rechazada (precio fuera de [{lim.min_price}, {lim.max_price}]): {intent.describe()}")
                    continue
                opening = ZERO  # fuera de rango solo se permite cerrar
            count = closing + opening
            if count <= 0:
                notes.append(f"rechazada (límite de posición o tamaño): {intent.describe()}")
                continue

            unit_cost = intent.cost_per_contract()
            trimmed_by = ""
            for room_left, note in ((budget, budget_note), (market_budget, market_note)):
                if room_left is None or opening * unit_cost <= room_left:
                    continue
                affordable = whole_contracts(room_left / unit_cost) if unit_cost > 0 and room_left > 0 else ZERO
                opening = min(opening, affordable)
                trimmed_by = note
            count = closing + opening
            if count <= 0:
                notes.append(f"rechazada ({trimmed_by or 'límite de posición o tamaño'}): {intent.describe()}")
                continue
            budget -= opening * unit_cost
            if market_budget is not None:
                market_budget -= opening * unit_cost

            if intent.side == BID:
                bid_room -= count
                closable_by_bid -= closing
            else:
                ask_room -= count
                closable_by_ask -= closing

            if count != intent.count:
                why = f" ({trimmed_by})" if trimmed_by else ""
                notes.append(
                    f"recortada de {fmt_count(intent.count)} a {fmt_count(count)} contratos{why}: {intent.describe()}"
                )
            approved.append(replace(intent, count=count))
        return approved, notes
