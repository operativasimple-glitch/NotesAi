"""Nube, la meteoróloga: mira previsiones y lo medido hoy en las 7 ciudades del clima.

Avisa a Kali cuando una previsión cambia y a Vigía cuando una apuesta de temperatura de
Kali empieza a peligrar (o ya está ganada o perdida por lo que se ha medido).
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from ..brackets import bracket, degrees, judge
from ..names import plural
from ..sources.nws import CITIES, NWSClient, climate_day, today_in
from .base import Agent

SERIES = re.compile(r"^KXHIGH([A-Z]+)-")


class Nube(Agent):
    id = "nube"
    name = "Nube"
    role = "Meteoróloga: previsiones y temperaturas medidas"
    about = (
        "Mira cada 15 minutos el Servicio Meteorológico de EE. UU. en las 7 ciudades del clima: la máxima "
        "prevista y lo que ya se ha medido hoy. Si una apuesta de temperatura de Kali peligra, se lo dice a Vigía."
    )
    home = "observatorio"
    color = "#7cc6fe"
    interval = 900.0
    forecast_every = 3600.0

    def __init__(self, world, nws: NWSClient, clock=time.time):
        super().__init__(world, clock)
        self.nws = nws

    def tick(self, now: float) -> None:
        when = datetime.fromtimestamp(now, tz=timezone.utc)
        memory = self.memory
        forecasts = memory.setdefault("forecasts", {})  # ciudad -> {fecha: máxima}
        observed = memory.setdefault("observed", {})  # ciudad -> lectura de hoy
        refresh = now - memory.get("forecast_at", 0) >= self.forecast_every
        errors = []
        for key, city in CITIES.items():
            try:
                if refresh or key not in forecasts:
                    highs = self.nws.forecast_highs(city)
                    self._forecast_moved(key, forecasts.get(key) or {}, highs, when)
                    forecasts[key] = highs
                obs = self.nws.observed_max(city, when)
                if obs:
                    observed[key] = obs
            except Exception as exc:  # noqa: BLE001 - una ciudad que falla no para a las demás
                errors.append(f"{city.name}: {exc.__class__.__name__}")
        if refresh:
            memory["forecast_at"] = now
        rows = []
        for key, city in CITIES.items():
            today = today_in(city, when).isoformat()
            tomorrow = (today_in(city, when) + timedelta(days=1)).isoformat()
            obs = observed.get(key) or {}
            if obs.get("day") != climate_day(city, when)[0].isoformat():
                obs = {}
            rows.append(
                {
                    "key": key,
                    "city": city.name,
                    "today": (forecasts.get(key) or {}).get(today),
                    "tomorrow": (forecasts.get(key) or {}).get(tomorrow),
                    "max_so_far": obs.get("max_f"),
                    "now": obs.get("last_f"),
                    "at": obs.get("last_at"),
                }
            )
        watched = self._watch_bets(rows)
        mood = "sick" if len(errors) == len(CITIES) else "ok"
        text = (
            "No consigo el tiempo ahora mismo"
            if mood == "sick"
            else f"Vigilando 7 ciudades · {plural(watched, 'apuesta', 'apuestas')} de Kali"
        )
        self.status(text, mood=mood, cities=rows, errors=errors[:3])
        known = [r for r in rows if r.get("today") is not None]
        if mood == "sick":
            self.doing("observatorio", "el servicio del tiempo no contesta")
        elif refresh and known:
            hot = max(known, key=lambda r: r["today"])
            self.doing("observatorio", f"previsión NWS · {hot['city']} {hot['today']}°")
        else:
            measured = [r for r in rows if r.get("max_so_far") is not None]
            if measured:
                r = max(measured, key=lambda r: r["max_so_far"])
                self.doing("observatorio", f"termómetros · {r['city']} ya marca {r['max_so_far']}°")
            else:
                self.doing("observatorio", "mira el cielo de 7 ciudades")

    def talk(self) -> str:
        rows = (self.world.bot(self.id).get("detail") or {}).get("cities") or []
        known = [r for r in rows if r.get("today") is not None]
        if not known:
            return "Aún no tengo previsiones; dame unos minutos."
        hot = max(known, key=lambda r: r["today"])
        cold = min(known, key=lambda r: r["today"])
        bits = [
            f"Hoy lo más caluroso será {hot['city']} ({hot['today']}°)"
            f" y lo más fresco {cold['city']} ({cold['today']}°)."
        ]
        measured = [r for r in known if r.get("max_so_far") is not None]
        if measured:
            bits.append("Ya medido: " + ", ".join(f"{r['city']} {r['max_so_far']}°" for r in measured[:4]) + ".")
        return " ".join(bits)

    # --- lo que cuenta -----------------------------------------------------------------

    def _forecast_moved(self, key: str, before: dict, after: dict, when: datetime) -> None:
        city = CITIES[key]
        today = today_in(city, when).isoformat()
        old, new = before.get(today), after.get(today)
        if old is None or new is None or abs(new - old) < 2:
            return
        verb = "sube" if new > old else "baja"
        self.say(f"En {city.name} la máxima prevista para hoy {verb} de {old}° a {new}°.", to="kali", city=key)

    def _watch_bets(self, rows: list) -> int:
        """Revisa las apuestas de temperatura de Kali y avisa cuando cambian de estado."""
        kali = self.peer("kali").get("detail") or {}
        by_city = {r["key"]: r for r in rows}
        states = self.memory.setdefault("bets", {})
        watched = 0
        open_now = set()
        for p in kali.get("positions") or []:
            m = SERIES.match(p.get("ticker", ""))
            if not m or m.group(1) not in by_city:
                continue
            kind = bracket(p.get("subtitle", ""), p["ticker"])
            if kind is None:
                continue
            watched += 1
            open_now.add(p["ticker"])
            row = by_city[m.group(1)]
            verdict = judge(kind, p.get("side", ""), row.get("max_so_far"), row.get("today"))
            before = states.get(p["ticker"])
            states[p["ticker"]] = verdict
            if verdict == before or (before is None and verdict == "bien"):
                continue
            what = f"el {p.get('side')} a «{degrees(kind)}» de {row['city']}"
            measured = f"van {row['max_so_far']}°" if row.get("max_so_far") is not None else "aún no hay medidas"
            forecast = f"la previsión dice {row['today']}°" if row.get("today") is not None else "sin previsión"
            if verdict == "perdida":
                self.say(
                    f"Mala noticia: Kali tiene {what} y ya {measured}. Esa apuesta está perdida.",
                    to="vigia",
                    kind="alert",
                    ticker=p["ticker"],
                )
            elif verdict == "peligro":
                self.say(
                    f"Ojo: Kali tiene {what}; {measured} y {forecast}. Peligra.",
                    to="vigia",
                    kind="alert",
                    ticker=p["ticker"],
                )
            elif verdict == "ganada":
                self.say(f"Tranquila, Kali: {what} ya está ganado ({measured}).", to="kali", ticker=p["ticker"])
            elif before == "peligro":
                self.say(
                    f"Falsa alarma: {what} vuelve a ir bien ({measured}, {forecast}).", to="vigia", ticker=p["ticker"]
                )
        for ticker in [t for t in states if t not in open_now]:
            del states[ticker]
        return watched
