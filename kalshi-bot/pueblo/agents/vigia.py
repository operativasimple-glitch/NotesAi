"""Vigía: vigila el riesgo de Kali y te avisa a ti cuando algo se tuerce."""

from __future__ import annotations

from decimal import Decimal

from ..names import money, plural
from .base import Agent
from .kali import dec

DROP = Decimal("0.20")  # caída de probabilidad que merece un aviso
HEAVY = Decimal("0.20")  # una sola apuesta con más de esto del saldo


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
        # Los avisos de las últimas 24 horas (para su ficha y el diario de la noche).
        times = [t for t in memory.get("alert_times", []) if now - t < 86400] + [now] * alerts
        memory["alert_times"] = times
        self.status(text, mood=mood, risky=risky, alerts_today=len(times))
        if alerts:
            self.doing("puente", "¡da un aviso!")
        elif kali.get("connected"):
            self.doing("puente", f"vigila {plural(len(positions), 'apuesta', 'apuestas')}")
        else:
            self.doing("puente", "espera a ver a Kali")

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
