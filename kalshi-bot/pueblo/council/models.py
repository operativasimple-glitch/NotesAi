"""Cómo piensa cada miembro del Consejo: de un mercado sacan su precio justo (de 0 a 1).

Son reglas sencillas y a la vista, para que el marcador diga algo claro: si una manera de
pensar gana dinero (de mentira) durante semanas, merece la pena estudiarla de verdad.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from statistics import NormalDist
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ..sources.nws import CITIES, today_in


def clamp(p: float, lo: float = 0.01, hi: float = 0.99) -> float:
    return max(lo, min(hi, p))


# --- Cazatormentas: el tiempo con los números del Servicio Meteorológico -------------------


def weather_spread(lead_days: int, local_hour: float) -> float:
    """Cuánto se equivoca la previsión de la máxima (desviación típica, °F)."""
    if lead_days >= 2:
        return 4.0
    if lead_days == 1:
        return 3.0
    if local_hour < 12:
        return 2.2
    if local_hour < 15:
        return 1.6
    if local_hour < 18:
        return 1.0
    return 0.6  # a última hora casi todo está ya medido


def bracket_probability(kind: list, forecast: float, spread: float, observed: Optional[float]) -> float:
    """Probabilidad de que la máxima caiga en el tramo, con la máxima ~ Normal(previsión, spread).

    La máxima del día no puede bajar de lo ya medido: si hay medida, se cuenta solo lo que queda
    por encima. El informe oficial da grados enteros, así que "72° a 73°" es [71,5; 73,5).
    """
    mean = max(forecast, observed) if observed is not None else forecast
    dist = NormalDist(mean, spread)
    floor = observed - 0.5 if observed is not None else -math.inf

    def between(a: float, b: float) -> float:
        a = max(a, floor)
        if b <= a:
            return 0.0
        rest = 1.0 - dist.cdf(floor) if floor > -math.inf else 1.0
        if rest < 1e-9:  # lo medido ya está muy por encima de la previsión
            return 1.0 if a <= observed < b else 0.0
        top = dist.cdf(b) if b < math.inf else 1.0
        return (top - dist.cdf(a)) / rest

    if kind[0] == "between":
        return between(kind[1] - 0.5, kind[2] + 0.5)
    if kind[0] == "below":
        return between(-math.inf, kind[1] + 0.5)
    return between(kind[1] - 0.5, math.inf)


def weather(topic: dict, ctx: dict) -> Optional[float]:
    if topic.get("kind") != "weather" or not topic.get("bracket"):
        return None
    row = (ctx.get("cities") or {}).get(topic.get("city"))
    city = CITIES.get(topic.get("city") or "")
    if not row or city is None:
        return None
    now: datetime = ctx.get("now") or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(city.tz))
    lead = (date.fromisoformat(topic["date"]) - today_in(city, now)).days
    if lead < 0:
        return None
    forecast = row.get("today") if lead == 0 else row.get("tomorrow") if lead == 1 else None
    if forecast is None:
        return None
    observed = row.get("max_so_far") if lead == 0 else None
    hour = local.hour + local.minute / 60
    mean = float(forecast)
    if observed is not None:
        # Según avanza el día pesa más lo medido: hacia las cinco de la tarde la máxima suele estar puesta.
        weight = min(1.0, max(0.0, (hour - 10) / 7))
        mean = max(observed, weight * observed + (1 - weight) * mean)
    return clamp(bracket_probability(topic["bracket"], mean, weather_spread(lead, hour), observed))


# --- los demás: leen el precio del mercado a su manera ------------------------------------


def momentum(topic: dict, ctx: dict) -> Optional[float]:
    """Dinero Listo: si el precio ha subido en el día, cree que seguirá subiendo (y al revés)."""
    mid, last, previous = topic.get("mid"), topic.get("last"), topic.get("previous")
    if mid is None:
        return None
    drift = (last - previous) if last is not None and previous is not None else 0.0
    return clamp(mid + 0.5 * drift)


def favorites(topic: dict, ctx: dict) -> Optional[float]:
    """Piloto: los favoritos ganan un poco más de lo que dice su precio y los tapados un poco menos.

    Es la idea con la que opera Kali (el sesgo favorito-tapado), aquí con apuestas de mentira.
    """
    mid = topic.get("mid")
    if mid is None:
        return None
    if 0.85 <= mid <= 0.97:
        return clamp(mid + 0.04)
    if 0.03 <= mid <= 0.15:
        return clamp(mid - 0.04)
    return clamp(mid)


def fear(topic: dict, ctx: dict) -> Optional[float]:
    """Miedo: todo le parece más incierto de lo que es; acerca cualquier precio al 50 %."""
    mid = topic.get("mid")
    return None if mid is None else clamp(0.5 + 0.7 * (mid - 0.5))


def greed(topic: dict, ctx: dict) -> Optional[float]:
    """Codicia: el que va ganando ganará seguro; aleja cualquier precio del 50 %."""
    mid = topic.get("mid")
    if mid is None:
        return None
    p = clamp(mid, 0.001, 0.999)
    return clamp(1 / (1 + math.exp(-1.6 * math.log(p / (1 - p)))))


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    color: str
    role: str
    about: str
    model: Optional[Callable] = field(default=None, compare=False)  # None: opina por su cuenta (Claude)


MEMBERS = (
    Member(
        "tormenta",
        "Cazatormentas",
        "#2a78d6",
        "Calcula el clima con la previsión del NWS",
        "Para cada tramo de temperatura calcula la probabilidad con la máxima prevista por el Servicio "
        "Meteorológico de EE. UU. y lo que ya se ha medido hoy. Solo opina de los mercados de clima.",
        weather,
    ),
    Member(
        "dinero",
        "Dinero Listo",
        "#1baf7a",
        "Sigue al dinero que se mueve",
        "Mira cómo ha cambiado el precio en el último día y cree que seguirá en esa dirección.",
        momentum,
    ),
    Member(
        "piloto",
        "Piloto",
        "#e87ba4",
        "Cree en los favoritos, como Kali",
        "Piensa que los favoritos claros ganan un poco más de lo que dice su precio. Es la idea de Kali, "
        "pero apostando de mentira en todos los mercados del Consejo.",
        favorites,
    ),
    Member(
        "miedo",
        "Miedo",
        "#4a3aa7",
        "Todo le parece arriesgado",
        "Desconfía de lo que parece seguro: acerca cualquier probabilidad al 50 %. Sirve para ver cuánto "
        "cuesta tener miedo.",
        fear,
    ),
    Member(
        "codicia",
        "Codicia",
        "#eda100",
        "Lo quiere todo",
        "Cree que el que va ganando ganará seguro y que el que va perdiendo no tiene nada que hacer. "
        "Sirve para ver cuánto cuesta la avaricia.",
        greed,
    ),
)
