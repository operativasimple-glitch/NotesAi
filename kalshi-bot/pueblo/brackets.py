"""Tramos de temperatura de los mercados de máxima: leerlos y ver cómo va una apuesta.

Los usan Nube (para avisar si una apuesta peligra) y el Consejo (para calcular su precio justo).
"""

from __future__ import annotations

import re
from typing import Optional

BETWEEN = re.compile(r"(\d+)\s*°?\s*(?:to|a|-|–)\s*(\d+)\s*°", re.I)
BELOW = re.compile(r"(\d+)\s*°?\s*(?:or below|or less|o menos)", re.I)
ABOVE = re.compile(r"(\d+)\s*°?\s*(?:or above|or more|o más)", re.I)
TICKER_BETWEEN = re.compile(r"-B(\d+)\.5$")


def bracket(subtitle: str, ticker: str = "") -> Optional[tuple]:
    """("between", lo, hi) | ("below", x) | ("above", x) a partir del tramo del mercado."""
    text = subtitle or ""
    m = BETWEEN.search(text)
    if m:
        return ("between", int(m.group(1)), int(m.group(2)))
    m = BELOW.search(text)
    if m:
        return ("below", int(m.group(1)))
    m = ABOVE.search(text)
    if m:
        return ("above", int(m.group(1)))
    m = TICKER_BETWEEN.search(ticker)
    if m:
        lo = int(m.group(1))
        return ("between", lo, lo + 1)
    return None


def judge(kind: tuple, side: str, observed: Optional[int], forecast: Optional[int]) -> str:
    """Cómo va una apuesta de máxima: "ganada", "perdida", "peligro" o "bien".

    La máxima del día solo puede subir: lo ya medido decide unos casos sin esperar al final.
    """
    yes = side == "SÍ"
    if kind[0] == "between":
        lo, hi = kind[1], kind[2]
        if observed is not None and observed > hi:
            return "perdida" if yes else "ganada"
        if forecast is not None:
            inside = lo <= forecast <= hi
            if yes and not inside:
                return "peligro"
            if not yes and inside:
                return "peligro"
        return "bien"
    if kind[0] == "below":  # SÍ gana si la máxima queda en x o menos
        x = kind[1]
        if observed is not None and observed > x:
            return "perdida" if yes else "ganada"
        if forecast is not None and (forecast > x) == yes:
            return "peligro"
        return "bien"
    x = kind[1]  # above: SÍ gana si llega a x o más
    if observed is not None and observed >= x:
        return "ganada" if yes else "perdida"
    if forecast is not None and (forecast < x) == yes:
        return "peligro"
    return "bien"


def degrees(kind: tuple) -> str:
    if kind[0] == "between":
        return f"{kind[1]}° a {kind[2]}°"
    return f"{kind[1]}° o {'menos' if kind[0] == 'below' else 'más'}"
