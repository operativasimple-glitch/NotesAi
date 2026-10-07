"""Configuración: config.toml (comportamiento del bot) + .env (secretos).

Variables de entorno (normalmente en .env):
  KALSHI_ENV               demo (por defecto) o prod
  KALSHI_API_KEY_ID        ID de tu API key
  KALSHI_PRIVATE_KEY_PATH  ruta al .pem descargado de Kalshi
  KALSHI_PRIVATE_KEY       alternativa: el contenido del PEM (útil en servidores)
  KALSHI_BASE_URL          opcional, para forzar otra URL de la API

Las rutas relativas se interpretan desde la carpeta del config.toml.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from .auth import KalshiSigner
from .client import ENVIRONMENTS, KalshiClient
from .engine import EngineConfig
from .models import to_decimal
from .risk import RiskLimits

log = logging.getLogger(__name__)

KNOWN_KEYS = {
    "bot": {
        "poll_interval_seconds",
        "cancel_on_exit",
        "order_prefix",
        "order_ttl_seconds",
        "taker_cooldown_seconds",
        "requote_tolerance",
        "max_consecutive_errors",
        "paper_cash",
        "log_dir",
    },
    "markets": {
        "tickers",
        "series",
        "events",
        "max_markets",
        "min_hours_to_close",
        "max_hours_to_close",
        "min_volume_24h",
        "refresh_minutes",
    },
    "risk": {
        "max_order_contracts",
        "max_position_per_market",
        "max_total_exposure_dollars",
        "max_session_loss_dollars",
        "min_price",
        "max_price",
        "min_minutes_to_close",
    },
    "strategy": {"name", "params"},
    "http": {"reads_per_second", "writes_per_second", "timeout_seconds", "self_trade_prevention"},
}


class ConfigError(Exception):
    pass


def load_dotenv(path: Path) -> None:
    """Carga KEY=VALUE de un .env sin pisar variables ya definidas."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass
class Settings:
    env: str
    base_url: str
    api_key_id: Optional[str]
    private_key_path: Optional[Path]
    private_key_pem: Optional[str]
    engine: EngineConfig
    risk: RiskLimits
    strategy_name: str
    strategy_params: dict
    reads_per_second: float
    writes_per_second: float
    timeout_seconds: float
    self_trade_prevention: str
    log_dir: Path
    config_path: Optional[Path]

    @property
    def is_production(self) -> bool:
        """True si la URL de la API es la de dinero real (no la de demo)."""
        return "demo" not in urlparse(self.base_url).netloc

    def signer(self) -> Optional[KalshiSigner]:
        """Firmador con tus credenciales, o None si no hay credenciales."""
        has_key = bool(self.private_key_pem or self.private_key_path)
        if not self.api_key_id and not has_key:
            return None
        if not self.api_key_id:
            raise ConfigError("Falta KALSHI_API_KEY_ID en .env")
        if self.private_key_pem:
            return KalshiSigner(self.api_key_id, self.private_key_pem.replace("\\n", "\n"))
        if self.private_key_path is None:
            raise ConfigError("Falta la clave privada: define KALSHI_PRIVATE_KEY_PATH en .env")
        if not self.private_key_path.is_file():
            raise ConfigError(f"No existe el archivo de clave privada: {self.private_key_path}")
        return KalshiSigner.from_file(self.api_key_id, self.private_key_path)

    def client(self, signer: Optional[KalshiSigner] = None) -> KalshiClient:
        return KalshiClient(
            self.base_url,
            signer,
            timeout=self.timeout_seconds,
            reads_per_second=self.reads_per_second,
            writes_per_second=self.writes_per_second,
            self_trade_prevention=self.self_trade_prevention,
        )


def _dec(value: Any, name: str) -> Decimal:
    result = to_decimal(value)
    if result is None:
        raise ConfigError(f"'{name}' debe ser un número (valor: {value!r})")
    return result


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ConfigError(f"'{name}' debe ser true o false (valor: {value!r})")


def _list(value: Any, name: str) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        raise ConfigError(f"'{name}' debe ser una lista, p. ej. [\"ABC\"]")
    return [str(v) for v in value if str(v).strip()]


def _section(data: dict, name: str) -> dict:
    section = data.get(name) or {}
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] debe ser una sección de config.toml")
    unknown = set(section) - KNOWN_KEYS[name]
    if unknown:
        log.warning("Claves desconocidas en [%s] (¿error de escritura?): %s", name, ", ".join(sorted(unknown)))
    return section


def load_settings(config_path: Optional[str] = None) -> Settings:
    path = Path(config_path) if config_path else Path("config.toml")
    if config_path and not path.is_file():
        raise ConfigError(f"No existe el archivo de configuración {path}")
    base_dir = path.resolve().parent if path.is_file() else Path.cwd()
    load_dotenv(base_dir / ".env")
    if base_dir != Path.cwd():
        load_dotenv(Path.cwd() / ".env")

    data: dict = {}
    if path.is_file():
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} no es un TOML válido: {exc}") from exc
    else:
        log.debug("No se encontró config.toml; se usan valores por defecto")

    unknown_sections = set(data) - set(KNOWN_KEYS)
    if unknown_sections:
        log.warning("Secciones desconocidas en config.toml: %s", ", ".join(sorted(unknown_sections)))
    bot = _section(data, "bot")
    markets = _section(data, "markets")
    risk = _section(data, "risk")
    strategy = _section(data, "strategy")
    http = _section(data, "http")

    env = os.environ.get("KALSHI_ENV", "demo").strip().lower()
    if env not in ENVIRONMENTS:
        raise ConfigError(f"KALSHI_ENV debe ser 'demo' o 'prod' (valor: {env!r})")

    def resolve(value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        p = Path(value).expanduser()
        return p if p.is_absolute() else base_dir / p

    prefix = str(bot.get("order_prefix", "kb"))
    if not re.fullmatch(r"[A-Za-z0-9]{1,8}", prefix):
        raise ConfigError("order_prefix debe tener de 1 a 8 letras o números")

    engine = EngineConfig(
        poll_interval=float(bot.get("poll_interval_seconds", 10)),
        cancel_on_exit=_bool(bot.get("cancel_on_exit", True), "cancel_on_exit"),
        order_prefix=prefix,
        order_ttl_seconds=int(bot.get("order_ttl_seconds", 600)),
        taker_cooldown_seconds=float(bot.get("taker_cooldown_seconds", 30)),
        requote_tolerance=_dec(bot.get("requote_tolerance", 0), "requote_tolerance"),
        max_consecutive_errors=int(bot.get("max_consecutive_errors", 10)),
        paper_cash=_dec(bot.get("paper_cash", 1000), "paper_cash"),
        tickers=_list(markets.get("tickers"), "tickers"),
        series=_list(markets.get("series"), "series"),
        events=_list(markets.get("events"), "events"),
        max_markets=int(markets.get("max_markets", 10)),
        min_hours_to_close=float(markets.get("min_hours_to_close", 0)),
        max_hours_to_close=float(markets.get("max_hours_to_close", 0)),
        min_volume_24h=_dec(markets.get("min_volume_24h", 0), "min_volume_24h"),
        refresh_markets_minutes=float(markets.get("refresh_minutes", 5)),
    )
    if engine.poll_interval < 1:
        raise ConfigError("poll_interval_seconds debe ser al menos 1")
    if engine.order_ttl_seconds and engine.order_ttl_seconds < 60:
        raise ConfigError("order_ttl_seconds debe ser 0 (sin caducidad) o al menos 60")

    limits = RiskLimits(
        max_order_contracts=_dec(risk.get("max_order_contracts", 5), "max_order_contracts"),
        max_position_per_market=_dec(risk.get("max_position_per_market", 20), "max_position_per_market"),
        max_total_exposure=_dec(risk.get("max_total_exposure_dollars", 50), "max_total_exposure_dollars"),
        max_session_loss=_dec(risk.get("max_session_loss_dollars", 20), "max_session_loss_dollars"),
        min_price=_dec(risk.get("min_price", "0.02"), "min_price"),
        max_price=_dec(risk.get("max_price", "0.98"), "max_price"),
        min_minutes_to_close=float(risk.get("min_minutes_to_close", 15)),
    )
    if not (0 < limits.min_price < limits.max_price < 1):
        raise ConfigError("Se requiere 0 < min_price < max_price < 1")

    params = dict(strategy.get("params") or {})
    for key, value in list(params.items()):
        if key.endswith("_file") and isinstance(value, str) and value:
            params[key] = str(resolve(value))

    stp = str(http.get("self_trade_prevention", "taker_at_cross"))
    if stp not in ("taker_at_cross", "maker"):
        raise ConfigError("self_trade_prevention debe ser 'taker_at_cross' o 'maker'")

    return Settings(
        env=env,
        base_url=os.environ.get("KALSHI_BASE_URL") or ENVIRONMENTS[env],
        api_key_id=os.environ.get("KALSHI_API_KEY_ID") or None,
        private_key_path=resolve(os.environ.get("KALSHI_PRIVATE_KEY_PATH")),
        private_key_pem=os.environ.get("KALSHI_PRIVATE_KEY") or None,
        engine=engine,
        risk=limits,
        strategy_name=str(strategy.get("name", "fair_value")),
        strategy_params=params,
        reads_per_second=float(http.get("reads_per_second", 8)),
        writes_per_second=float(http.get("writes_per_second", 4)),
        timeout_seconds=float(http.get("timeout_seconds", 10)),
        self_trade_prevention=stp,
        log_dir=resolve(str(bot.get("log_dir", "logs"))) or base_dir / "logs",
        config_path=path if path.is_file() else None,
    )
