"""Vigía: vigila el riesgo de Kali, te avisa cuando algo se tuerce y lleva la sala de mando (el Puente).

Desde el Puente puedes parar o arrancar el bot, pulsar el freno, cerrar todas las apuestas y aplicar
los ajustes que propone la Vigía. Ella solo propone: lo que cambia el bot lo decides tú (y lo
confirma la web antes de hacerlo).
"""

from __future__ import annotations

import time
from decimal import Decimal

from ..names import money, plural
from ..sources.panel import PanelError
from .base import Agent
from .kali import dec

DROP = Decimal("0.20")  # caída de probabilidad que merece un aviso
HEAVY = Decimal("0.20")  # una sola apuesta con más de esto del saldo
SETTINGS_EVERY = 300.0  # los ajustes del bot cambian poco: se leen cada 5 minutos
SAFER_MIN_PRICE = Decimal("0.92")


def proposals(values: dict) -> list:
    """Ajustes que conviene cambiar, cada uno con el cambio exacto que se aplicaría."""
    out = []
    strategy = values.get("strategy") or {}
    params = strategy.get("params") or {}
    risk = values.get("risk") or {}
    if strategy.get("name") == "favorites":
        if dec(params.get("min_price"), Decimal("0.88")) < SAFER_MIN_PRICE:
            out.append(
                {
                    "id": "min_price",
                    "title": "Comprar el favorito solo desde 92¢",
                    "why": (
                        "Con 6 semanas de datos reales, desde 92¢ fallaron un 40 % menos de apuestas a cambio "
                        "de ganar un 17 % menos. Con poco saldo, menos sustos."
                    ),
                    "change": {"section": "strategy", "key": "min_price", "value": str(SAFER_MIN_PRICE)},
                }
            )
        order, cap = dec(params.get("order_size")), dec(params.get("max_position"))
        if order and cap and cap > order:
            out.append(
                {
                    "id": "max_position",
                    "title": f"Como mucho {int(order)} contratos por mercado",
                    "why": (
                        f"Cada orden es de {int(order)}, pero puede ir sumando hasta {int(cap)} en el mismo partido. "
                        "Así un solo fallo no se lleva más de una apuesta."
                    ),
                    "change": {"section": "strategy", "key": "max_position", "value": str(int(order))},
                }
            )
    if dec(risk.get("max_daily_loss_pct"), Decimal(0)) <= 0:
        out.append(
            {
                "id": "max_daily_loss_pct",
                "title": "Parar las compras el día que se pierda un 25 %",
                "why": "Un mal sábado ya no se llevará la semana: lo apostado sigue y al día siguiente vuelve.",
                "change": {"section": "risk", "key": "max_daily_loss_pct", "value": "0.25"},
            }
        )
    market_pct = dec(risk.get("max_market_pct"), Decimal(0))
    if market_pct <= 0 or market_pct > Decimal("0.2"):
        out.append(
            {
                "id": "max_market_pct",
                "title": "Como mucho un 15 % del saldo en una apuesta",
                "why": "Con 5 contratos y 20 $, cada fallo se llevaba el 20 % de la cuenta.",
                "change": {"section": "risk", "key": "max_market_pct", "value": "0.15"},
            }
        )
    return out


class Vigia(Agent):
    id = "vigia"
    name = "Vigía"
    role = "Vigila el riesgo y te avisa"
    about = (
        "Desde su torre mira las apuestas de Kali: si una se hunde, si hay demasiado dinero en una sola o si "
        "se acerca el freno de pérdidas. También escucha a Nube y te cuenta lo importante."
    )
    home = "puente"
    color = "#ff6b6b"
    interval = 60.0

    def __init__(self, world, panel=None, clock=time.time):
        super().__init__(world, clock)
        self.panel = panel  # el bot (dentro de él) o su panel web; sin él, solo mira lo que cuenta Kali

    def tick(self, now: float) -> None:
        kali = self.peer("kali").get("detail") or {}
        memory = self.memory
        first_seen = memory.setdefault("first_chance", {})
        warned = memory.setdefault("warned", {})
        alerts = 0
        positions = kali.get("positions") or []
        equity = dec((kali.get("balance") or {}).get("equity"), Decimal(0))
        open_now = set()
        for p in positions:
            ticker = p["ticker"]
            open_now.add(ticker)
            chance = dec(p.get("chance"))
            if chance is None:
                continue
            start = dec(first_seen.setdefault(ticker, str(chance)))
            cost = dec(p.get("cost"), Decimal(0))
            if start - chance >= DROP and warned.get(ticker) != "drop":
                warned[ticker] = "drop"
                alerts += 1
                self.say(
                    f"Ojo: «{p['name']}» ({p['side']}) ha bajado del {pct(start)} al {pct(chance)}. "
                    f"Si falla, se pierden {money(cost)}.",
                    to="tu",
                    kind="alert",
                    ticker=ticker,
                )
            elif warned.get(ticker) == "drop" and chance >= start - Decimal("0.05"):
                warned[ticker] = "ok"
                self.say(f"Se recupera «{p['name']}»: vuelve al {pct(chance)}.", to="tu", ticker=ticker)
            if equity > 0 and cost / equity > HEAVY and warned.get(f"{ticker}:heavy") is None:
                warned[f"{ticker}:heavy"] = True
                self.say(
                    f"Kali tiene {money(cost)} en «{p['name']}», un {pct(cost / equity)} del saldo."
                    " Es mucho para una sola apuesta.",
                    to="kali",
                    ticker=ticker,
                )
        for ticker in [t for t in first_seen if t not in open_now]:
            first_seen.pop(ticker, None)
            warned.pop(ticker, None)
            warned.pop(f"{ticker}:heavy", None)

        session = dec(kali.get("session_pnl"))
        limit = dec(kali.get("max_loss"))
        near = session is not None and limit and limit > 0 and session <= -limit * Decimal("0.7")
        if near and not memory.get("near_brake"):
            memory["near_brake"] = True
            alerts += 1
            self.say(
                f"Kali va {money(session, sign=True)} desde que arrancó; el freno salta a −{money(limit)}.",
                to="tu",
                kind="alert",
            )
        elif not near:
            memory["near_brake"] = False

        for message in self.inbox():
            if message["from"] == "nube" and message["kind"] == "alert":
                at_risk = next((p for p in positions if p["ticker"] == (message.get("data") or {}).get("ticker")), None)
                extra = f" Hay {money(dec(at_risk.get('cost'), Decimal(0)))} en juego." if at_risk else ""
                alerts += 1
                self.say(f"Nube avisa: {message['text']}{extra}", to="tu", kind="alert")

        risky = sum(1 for t, w in warned.items() if w == "drop")
        if not kali.get("connected"):
            text, mood = "Esperando a ver el panel de Kali", "sleep"
        elif risky:
            text, mood = (
                f"Vigilando {risky} apuesta{'s' if risky != 1 else ''} que se ha{'n' if risky != 1 else ''} torcido",
                "alert",
            )
        else:
            text, mood = f"Todo en calma · {plural(len(positions), 'apuesta abierta', 'apuestas abiertas')}", "ok"
        bridge = self._bridge(now, kali, positions, equity)
        alerts += self._bridge_alerts(bridge)
        if bridge.get("pause"):
            text, mood = "Bot en pausa hasta mañana: hoy ya perdió bastante", "alert"
        elif bridge.get("state") == "halted":
            text, mood = f"Bot frenado: {bridge.get('halted') or 'freno de emergencia'}", "alert"
        # Los avisos de las últimas 24 horas (para su ficha y el diario de la noche).
        times = [t for t in memory.get("alert_times", []) if now - t < 86400] + [now] * alerts
        memory["alert_times"] = times
        self.status(text, mood=mood, risky=risky, alerts_today=len(times), bridge=bridge)
        if alerts:
            self.doing("puente", "¡da un aviso!")
        elif kali.get("connected"):
            self.doing("puente", f"vigila {plural(len(positions), 'apuesta', 'apuestas')}")
        else:
            self.doing("puente", "espera a ver a Kali")

    # --- la sala de mando ---------------------------------------------------------------

    def _bridge(self, now: float, kali: dict, positions: list, equity: Decimal) -> dict:
        """Lo que enseña el Puente: el bot, su dinero, sus límites y lo que conviene cambiar."""
        bridge: dict = {"controls": bool(getattr(self.panel, "can_act", False)), "connected": self.panel is not None}
        if self.panel is None:
            return bridge
        memory = self.memory
        try:
            status = self.panel.status()
            if now - memory.get("settings_at", 0) >= SETTINGS_EVERY or "settings" not in memory:
                memory["settings"] = (self.panel.settings() or {}).get("values") or {}
                memory["settings_at"] = now
        except PanelError as exc:
            bridge.update(connected=False, error=str(exc))
            return bridge
        bot = status.get("bot") or {}
        risk = status.get("risk") or {}
        balance = status.get("balance") or {}
        equity = dec(balance.get("equity"), equity)
        in_play = sum((dec(p.get("cost"), Decimal(0)) for p in positions), Decimal(0))
        day = bot.get("day") or {}
        start = dec(day.get("start_equity"))
        daily = dec(risk.get("max_daily_loss_pct"), Decimal(0))
        bridge.update(
            state=bot.get("state", "stopped"),
            mode=bot.get("mode"),
            halted=bot.get("halted_reason"),
            pause=bot.get("daily_pause"),
            production=bool(status.get("is_production")),
            equity=str(equity) if equity else None,
            in_play=str(in_play),
            in_play_pct=float(in_play / equity) if equity else None,
            biggest=float(max((dec(p.get("cost"), Decimal(0)) for p in positions), default=Decimal(0)) / equity)
            if equity
            else None,
            limits={
                "market_pct": risk.get("max_market_pct"),
                "exposure_pct": risk.get("max_exposure_pct"),
                "daily_loss_pct": risk.get("max_daily_loss_pct"),
                "session_loss": risk.get("max_session_loss_dollars"),
            },
            day=(
                {
                    "start": str(start),
                    "change": str(equity - start) if equity else None,
                    "pause_at": str((start * (1 - daily)).quantize(Decimal("0.01"))) if daily > 0 else None,
                }
                if start
                else None
            ),
            proposals=proposals(memory.get("settings") or {}),
        )
        return bridge

    def _bridge_alerts(self, bridge: dict) -> int:
        """Te cuenta una vez cuando el bot se pone en pausa por el día o se frena."""
        memory = self.memory
        said = 0
        pause = bridge.get("pause")
        if pause and memory.get("told_pause") != pause:
            memory["told_pause"] = pause
            self.say(f"El bot no compra más hoy: {pause}. Lo apostado sigue hasta el final.", to="tu", kind="alert")
            said += 1
        elif not pause:
            memory.pop("told_pause", None)
        halted = bridge.get("halted") if bridge.get("state") == "halted" else None
        if halted and memory.get("told_halt") != halted:
            memory["told_halt"] = halted
            self.say(
                f"El bot se ha frenado: {halted}. Desde el Puente puedes volver a arrancarlo.", to="tu", kind="alert"
            )
            said += 1
        elif not halted:
            memory.pop("told_halt", None)
        return said

    def talk(self) -> str:
        kali = self.peer("kali").get("detail") or {}
        positions = kali.get("positions") or []
        if not positions:
            return "Sin apuestas abiertas no hay nada que vigilar. Yo sigo en la torre."
        worst = min(positions, key=lambda p: float(p.get("chance") or 1))
        return (
            f"La que más miro es «{worst['name']}» ({worst['side']}), al {pct(dec(worst.get('chance'), Decimal(0)))}. "
            f"Kali tiene {plural(len(positions), 'apuesta abierta', 'apuestas abiertas')}."
        )


def pct(value: Decimal) -> str:
    return f"{round(float(value) * 100)} %"
