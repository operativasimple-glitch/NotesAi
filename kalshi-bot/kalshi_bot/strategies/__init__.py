"""Registro de estrategias.

En config.toml, `[strategy] name` puede ser una estrategia incluida
("fair_value", "market_maker") o una tuya con el formato "modulo:Clase",
p. ej. "mis_estrategias:MiEstrategia" (el módulo debe poder importarse
desde la carpeta donde ejecutas el bot).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Optional

from .base import MarketContext, Strategy
from .fair_value import FairValueStrategy
from .market_maker import MarketMakerStrategy

BUILTIN = {cls.name: cls for cls in (FairValueStrategy, MarketMakerStrategy)}

__all__ = ["BUILTIN", "MarketContext", "Strategy", "build_strategy"]


def build_strategy(name: str, params: Optional[dict] = None) -> Strategy:
    if name in BUILTIN:
        return BUILTIN[name](params)
    if ":" in name:
        module_name, class_name = name.split(":", 1)
        cwd = str(Path.cwd())
        if cwd not in sys.path:
            sys.path.insert(0, cwd)
        cls = getattr(importlib.import_module(module_name), class_name)
        if not (isinstance(cls, type) and issubclass(cls, Strategy)):
            raise ValueError(f"{name} no es una subclase de Strategy")
        return cls(params)
    options = ", ".join(sorted(BUILTIN))
    raise ValueError(f"Estrategia desconocida '{name}'. Opciones: {options} o 'modulo:Clase'")
