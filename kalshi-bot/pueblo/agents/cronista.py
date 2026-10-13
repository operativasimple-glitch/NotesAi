"""Cronista: escribe el diario del pueblo (por la mañana y por la noche) y las noticias."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..names import money, plural
from .base import Agent
from .kali import dec

MORNING = (8, 0)
EVENING = (21, 30)
NEWS = Decimal("2")  # un cierre de $2 o más (ganado o perdido) es noticia


class Cronista(Agent):
    id = "cronista"
    name = "Cronista"
    role = "Escribe el diario del pueblo"
    about = (
        "Por la mañana te cuenta cómo fue ayer y qué se espera hoy; por la noche, el resumen del día. "
        "Si pasa algo gordo (un cierre de $2 o más), lo publica como noticia."
    )
    home = "archivo"
    color = "#c792ea"
    interval = 300.0

    def __init__(self, world, tz: str = "America/Chicago", clock=time.time):
        super().__init__(world, clock)
        self.tz = ZoneInfo(tz)

    def tick(self, now: float) -> None:
        local = datetime.fromtimestamp(now, tz=timezone.utc).astimezone(self.tz)
        day = local.date().isoformat()
        memory = self.memory
        for message in self.inbox():
            net = dec((message.get("data") or {}).get("net"))
            if message["from"] == "kali" and net is not None and abs(net) >= NEWS:
                head = "¡Gran cobro!" if net > 0 else "Mal trago:"
                self.say(f"Noticia · {head} {message['text']}", kind="diary")
        if (local.hour, local.minute) >= MORNING and memory.get("morning") != day:
            memory["morning"] = day
            self.say(self.morning(), to="tu", kind="diary")
        if (local.hour, local.minute) >= EVENING and memory.get("evening") != day:
            memory["evening"] = day
            self.say(self.evening(), to="tu", kind="diary")
        self.status(f"Diario al día · {local:%H:%M}", mood="ok", day=day)
        self.doing("archivo", f"ordena el diario · {local:%H:%M}")

    def morning(self) -> str:
        kali = self.peer("kali").get("detail") or {}
        if not kali.get("connected"):
            return "Buenos días. Kali aún no está conectada al pueblo; en cuanto lo esté, os contaré cómo va."
        yesterday = kali.get("yesterday") or {}
        parts = ["Buenos días."]
        if yesterday.get("markets"):
            net = money(dec(yesterday.get("net"), Decimal(0)), sign=True)
            parts.append(
                f"Ayer Kali cerró {plural(int(yesterday['markets']), 'mercado', 'mercados')}: {net}"
                f" ({plural(int(yesterday.get('wins') or 0), 'ganado', 'ganados')},"
                f" {plural(int(yesterday.get('losses') or 0), 'perdido', 'perdidos')})."
            )
        else:
            parts.append("Ayer Kali no cerró ningún mercado.")
        radar = self.peer("radar").get("detail") or {}
        if radar.get("favorites") is not None:
            favorites = plural(int(radar["favorites"]), "favorito claro", "favoritos claros")
            parts.append(f"Radar ve {favorites} para las próximas horas.")
        nube = self.peer("nube").get("detail") or {}
        cities = [c for c in nube.get("cities") or [] if c.get("today") is not None]
        if cities:
            hot = max(cities, key=lambda c: c["today"])
            parts.append(f"Nube dice que hoy lo más caluroso será {hot['city']}, con {hot['today']}°.")
        return " ".join(parts)

    def evening(self) -> str:
        kali = self.peer("kali").get("detail") or {}
        if not kali.get("connected"):
            return "Buenas noches. Hoy Kali no ha estado conectada al pueblo."
        today = kali.get("today") or {}
        totals = kali.get("totals") or {}
        vigia = self.peer("vigia").get("detail") or {}
        parts = ["Resumen del día:"]
        if today.get("markets"):
            parts.append(
                f"Kali cerró {plural(int(today['markets']), 'mercado', 'mercados')}"
                f" y va {money(dec(today.get('net'), Decimal(0)), sign=True)}"
                f" ({plural(int(today.get('wins') or 0), 'acierto', 'aciertos')},"
                f" {plural(int(today.get('losses') or 0), 'fallo', 'fallos')})."
            )
        else:
            parts.append("Kali no cerró ningún mercado hoy.")
        if totals.get("markets"):
            net = money(dec(totals.get("net"), Decimal(0)), sign=True)
            parts.append(f"En total lleva {net} en {plural(int(totals['markets']), 'mercado', 'mercados')}.")
        council = self.peer("investigadora").get("detail") or {}
        leader = next((r for r in council.get("board") or [] if r.get("trades")), None)
        if leader:
            parts.append(
                f"En el Consejo va ganando {leader['name']}: {money(dec(leader.get('pnl'), Decimal(0)), sign=True)}"
                f" de mentira ({leader.get('wins', 0)} de {leader['trades']} acertadas)."
            )
        if vigia.get("alerts_today"):
            parts.append(f"Vigía dio {plural(int(vigia['alerts_today']), 'aviso', 'avisos')} en las últimas 24 horas.")
        parts.append("Buenas noches.")
        return " ".join(parts)

    def talk(self) -> str:
        local = datetime.fromtimestamp(self.clock(), tz=timezone.utc).astimezone(self.tz)
        return self.morning() if local.hour < 15 else self.evening()
