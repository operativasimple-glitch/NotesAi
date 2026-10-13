"""Configuración por variables de entorno (en Railway: Variables del servicio)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
AI_MODEL = "claude-opus-5-5"


class ConfigError(Exception):
    pass


@dataclass
class Config:
    password: str  # PUEBLO_PASSWORD: para entrar en la web del pueblo
    panel_url: str  # PANEL_URL: dirección del panel del bot de Kalshi (solo se lee)
    panel_password: str  # PANEL_PASSWORD: la contraseña de ese panel
    data_dir: Path  # PUEBLO_DATA_DIR: dónde se guarda la memoria del pueblo
    host: str
    port: int
    tz: str  # PUEBLO_TZ: tu zona horaria, para el diario de la mañana y de la noche
    contact: str  # PUEBLO_CONTACT: contacto para el servicio del tiempo (lo pide en cada petición)
    kalshi_api: str
    anthropic_api_key: str = ""  # ANTHROPIC_API_KEY: opcional; con ella Claude se sienta en el Consejo
    ai_model: str = AI_MODEL  # PUEBLO_AI_MODEL: el modelo de Claude que usa
    ai_max_calls: int = 12  # PUEBLO_AI_MAX_CALLS: preguntas a Claude al día, como mucho

    @property
    def panel_configured(self) -> bool:
        return bool(self.panel_url and self.panel_password)


def load_config(env=None) -> Config:
    env = os.environ if env is None else env
    password = env.get("PUEBLO_PASSWORD", "")
    if len(password) < 8:
        raise ConfigError("Pon PUEBLO_PASSWORD (al menos 8 caracteres): es la contraseña para entrar al pueblo")
    data = env.get("PUEBLO_DATA_DIR") or ("/data" if Path("/data").is_dir() else "data")
    try:
        port = int(env.get("PORT", "8090"))
    except ValueError as exc:
        raise ConfigError("PORT debe ser un número") from exc
    try:
        ai_max_calls = max(0, int(env.get("PUEBLO_AI_MAX_CALLS", "12")))
    except ValueError as exc:
        raise ConfigError("PUEBLO_AI_MAX_CALLS debe ser un número (preguntas a Claude al día)") from exc
    return Config(
        password=password,
        panel_url=env.get("PANEL_URL", "").rstrip("/"),
        panel_password=env.get("PANEL_PASSWORD", ""),
        data_dir=Path(data),
        host=env.get("HOST", "0.0.0.0"),
        port=port,
        tz=env.get("PUEBLO_TZ", "America/Chicago"),
        contact=env.get("PUEBLO_CONTACT", "pueblo-de-bots"),
        kalshi_api=env.get("KALSHI_API", KALSHI_API).rstrip("/"),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", "").strip(),
        ai_model=env.get("PUEBLO_AI_MODEL", "").strip() or AI_MODEL,
        ai_max_calls=ai_max_calls,
    )
