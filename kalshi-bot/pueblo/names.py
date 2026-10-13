"""Nombres legibles de los mercados y cantidades en español, para los avisos del móvil.

El panel hace lo mismo en el navegador (app.js); aquí basta lo que cabe en un aviso.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

# Series del bot: KXHIGH + ciudad (temperatura máxima) y KX + liga + GAME (partidos).
CITIES = {
    "NY": "Nueva York",
    "LAX": "Los Ángeles",
    "CHI": "Chicago",
    "MIA": "Miami",
    "AUS": "Austin",
    "DEN": "Denver",
    "PHIL": "Filadelfia",
}


def bracket(text: str) -> str:
    """Los tramos llegan en inglés: "66° to 67°" → "66° a 67°", "82° or below" → "82° o menos"."""
    text = re.sub(r"\s+to\s+", " a ", text)
    text = re.sub(r"\s+or (below|less|lower)\b", " o menos", text, flags=re.I)
    return re.sub(r"\s+or (above|more|higher)\b", " o más", text, flags=re.I)


def market_name(ticker: str, title: str = "", subtitle: str = "") -> str:
    """Nombre corto: "Máxima en Chicago · 78° a 79°", "Gana Pittsburgh" o el título y subtítulo."""
    series = ticker.split("-", 1)[0]
    weather = re.fullmatch(r"KX(HIGH|LOW)([A-Z]+)", series)
    if weather:
        what = "Máxima" if weather.group(1) == "HIGH" else "Mínima"
        name = f"{what} en {CITIES.get(weather.group(2), weather.group(2))}"
        return f"{name} · {bracket(subtitle)}" if subtitle else name
    if re.fullmatch(r"KX[A-Z0-9]+GAME", series) and subtitle:
        return f"Gana {subtitle}"
    return " · ".join(part for part in (title, subtitle) if part) or ticker


def money(value: Decimal, sign: bool = False) -> str:
    """$1,78 · +$0,35 · −$4,65 (coma decimal y signo menos de verdad)."""
    text = f"{abs(value):.2f}".replace(".", ",")
    prefix = "−" if value < 0 else "+" if sign and value > 0 else ""
    return f"{prefix}${text}"


def cents(price: Optional[Decimal]) -> str:
    """0.94 → "94¢", 0.935 → "93,5¢"."""
    if price is None:
        return "?"
    value = (price * 100).normalize()
    return f"{value:f}".replace(".", ",") + "¢"


def plural(n: int, one: str, many: str) -> str:
    """plural(1, "mercado", "mercados") → "1 mercado"; con 0 o más de 1, el plural."""
    return f"{n} {one if n == 1 else many}"


def quantity(count: Decimal) -> str:
    value = count.normalize()
    return f"{value:f}".replace(".", ",")
