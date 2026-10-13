"""Los vecinos del pueblo."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..config import Config
from ..sources.kalshi_public import KalshiPublic
from ..sources.nws import NWSClient
from ..sources.panel import PanelClient
from ..world import World
from .consejo import ClaudeMember, Investigadora
from .cronista import Cronista
from .herrera import Herrera
from .kali import Kali
from .mentora import Mentora
from .nube import Nube
from .probadora import Probadora
from .radar import Radar
from .vigia import Vigia


def tz_minutes(tz: str):
    """Lo que el panel espera en `tz`: minutos de UTC menos la hora local (como getTimezoneOffset)."""
    zone = ZoneInfo(tz)

    def offset() -> int:
        delta = datetime.now(timezone.utc).astimezone(zone).utcoffset()
        return -int(delta.total_seconds() // 60) if delta is not None else 0

    return offset


def build_agents(world: World, config: Config, panel=None) -> list:
    """Los vecinos. `panel`: de dónde leen el bot (dentro del bot, el propio bot; si no, su panel web)."""
    if panel is None and config.panel_configured:
        panel = PanelClient(config.panel_url, config.panel_password)
    agents = [
        Kali(world, panel, tz_minutes(config.tz)),
        Herrera(world, panel, tz_minutes(config.tz), config.tz, kalshi=KalshiPublic(config.kalshi_api)),
        Nube(world, NWSClient(config.contact)),
        Vigia(world, panel),
        Radar(world, KalshiPublic(config.kalshi_api)),
        Cronista(world, config.tz),
        Investigadora(world, KalshiPublic(config.kalshi_api)),
        Probadora(world, KalshiPublic(config.kalshi_api), config.tz),
        Mentora(world, KalshiPublic(config.kalshi_api), config.tz),
    ]
    if config.anthropic_api_key and config.ai_max_calls > 0:
        from ..council.claude import ClaudeForecaster

        forecaster = ClaudeForecaster(config.anthropic_api_key, config.ai_model)
        agents.append(ClaudeMember(world, forecaster, config.ai_max_calls, config.tz))
    return agents
