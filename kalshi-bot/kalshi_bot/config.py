"""Configuración: config.toml (comportamiento del bot) + .env (secretos).

Variables de entorno (normalmente en .env o en el panel del servidor):
  KALSHI_ENV               demo (por defecto) o prod
  KALSHI_API_KEY_ID        ID de tu API key
  KALSHI_PRIVATE_KEY_PATH  ruta al .pem descargado de Kalshi
  KALSHI_PRIVATE_KEY       alternativa: el contenido del PEM (útil en servidores)
  KALSHI_BASE_URL          opcional, para forzar otra URL de la API
  KALSHI_BOT_DATA_DIR      carpeta para datos del panel web (ajustes, credenciales, logs)

Si no hay credenciales en el entorno, se usan las guardadas desde el panel web
(credentials.json + kalshi-key.pem en la carpeta de datos).

Las rutas relativas se interpretan desde KALSHI_BOT_DATA_DIR si está definida
y, si no, desde la carpeta del config.toml.
"""

from __future__ import annotations

import json
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

CREDENTIALS_FILE = "credentials.json"
KEY_FILE = "kalshi-key.pem"
OVERRIDES_FILE = "runtime.json"

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
        "closing_within_hours",
        "max_markets",
        "max_markets_per_event",
        "min_hours_to_close",
        "max_hours_to_close",
        "min_volume_24h",
        "exclude_series",
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
    data_dir: Path = Path(".")
    credentials_source: str = ""  # "env", "panel" o "" (sin credenciales)
    env_locked: bool = False  # True si KALSHI_ENV viene del entorno

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

    def sections(self) -> dict:
        """Valores efectivos con las mismas claves que config.toml (para el panel)."""
        e, r = self.engine, self.risk
        return {
            "bot": {
                "poll_interval_seconds": e.poll_interval,
                "cancel_on_exit": e.cancel_on_exit,
                "order_ttl_seconds": e.order_ttl_seconds,
                "taker_cooldown_seconds": e.taker_cooldown_seconds,
                "paper_cash": e.paper_cash,
            },
            "markets": {
                "tickers": list(e.tickers),
                "series": list(e.series),
                "events": list(e.events),
                "closing_within_hours": e.closing_within_hours,
                "max_markets": e.max_markets,
                "max_markets_per_event": e.max_markets_per_event,
                "min_hours_to_close": e.min_hours_to_close,
                "max_hours_to_close": e.max_hours_to_close,
                "min_volume_24h": e.min_volume_24h,
                "exclude_series": list(e.exclude_series),
            },
            "risk": {
                "max_order_contracts": r.max_order_contracts,
                "max_position_per_market": r.max_position_per_market,
                "max_total_exposure_dollars": r.max_total_exposure,
                "max_session_loss_dollars": r.max_session_loss,
                "min_price": r.min_price,
                "max_price": r.max_price,
                "min_minutes_to_close": r.min_minutes_to_close,
            },
            "strategy": {"name": self.strategy_name, "params": dict(self.strategy_params)},
        }


def _dec(value: Any, name: str) -> Decimal:
    result = to_decimal(value)
    if result is None:
        raise ConfigError(f"'{name}' debe ser un número (valor: {value!r})")
    return result


def _num(value: Any, name: str, cast=float):
    try:
        return cast(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{name}' debe ser un número (valor: {value!r})") from None


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
        return [v.strip() for v in value.split(",") if v.strip()]
    if not isinstance(value, list):
        raise ConfigError(f"'{name}' debe ser una lista, p. ej. [\"ABC\"]")
    return [str(v).strip() for v in value if str(v).strip()]


def _section(data: dict, name: str) -> dict:
    section = data.get(name) or {}
    if not isinstance(section, dict):
        raise ConfigError(f"[{name}] debe ser una sección de config.toml")
    unknown = set(section) - KNOWN_KEYS[name]
    if unknown:
        log.warning("Claves desconocidas en [%s] (¿error de escritura?): %s", name, ", ".join(sorted(unknown)))
    return section


def merge_overrides(data: dict, overrides: Optional[dict]) -> dict:
    """Aplica los ajustes guardados desde el panel encima de config.toml."""
    if not overrides:
        return data
    merged = {k: dict(v) if isinstance(v, dict) else v for k, v in data.items()}
    for section, values in overrides.items():
        if section not in KNOWN_KEYS or not isinstance(values, dict):
            continue
        if section == "strategy":
            base = dict(merged.get("strategy") or {})
            name = values.get("name", base.get("name"))
            params = dict(base.get("params") or {}) if name == base.get("name") else {}
            params.update(values.get("params") or {})
            merged["strategy"] = {"name": name, "params": params}
        else:
            merged[section] = {**(merged.get(section) or {}), **values}
    return merged


def data_dir_from_env(default: Path) -> Path:
    value = os.environ.get("KALSHI_BOT_DATA_DIR")
    return Path(value).expanduser() if value else default


def read_overrides(data_dir: Path) -> dict:
    try:
        data = json.loads((data_dir / OVERRIDES_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise ConfigError(f"{data_dir / OVERRIDES_FILE} está dañado: {exc}") from exc
    return data if isinstance(data, dict) else {}


def write_overrides(data_dir: Path, overrides: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = data_dir / (OVERRIDES_FILE + ".tmp")
    tmp.write_text(json.dumps(overrides, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(data_dir / OVERRIDES_FILE)


def panel_credentials(data_dir: Path) -> dict:
    try:
        data = json.loads((data_dir / CREDENTIALS_FILE).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_panel_credentials(data_dir: Path, *, key_id: str, private_key_pem: str, env: str) -> None:
    """Guarda credenciales enviadas desde el panel (validando la clave antes)."""
    if env not in ENVIRONMENTS:
        raise ConfigError("El entorno debe ser 'demo' o 'prod'")
    key_id = (key_id or "").strip()
    pem = (private_key_pem or "").strip().replace("\\n", "\n") + "\n"
    KalshiSigner(key_id, pem)  # lanza ValueError si no es válida
    data_dir.mkdir(parents=True, exist_ok=True)
    key_path = data_dir / KEY_FILE
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(pem)
    os.chmod(key_path, 0o600)
    (data_dir / CREDENTIALS_FILE).write_text(json.dumps({"key_id": key_id, "env": env}), encoding="utf-8")


def save_panel_env(data_dir: Path, env: str) -> None:
    if env not in ENVIRONMENTS:
        raise ConfigError("El entorno debe ser 'demo' o 'prod'")
    data = panel_credentials(data_dir)
    data["env"] = env
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / CREDENTIALS_FILE).write_text(json.dumps(data), encoding="utf-8")


def delete_panel_credentials(data_dir: Path) -> None:
    for name in (KEY_FILE, CREDENTIALS_FILE):
        try:
            (data_dir / name).unlink()
        except FileNotFoundError:
            pass


def load_settings(config_path: Optional[str] = None, overrides: Optional[dict] = None) -> Settings:
    path = Path(config_path) if config_path else Path("config.toml")
    if config_path and not path.is_file():
        raise ConfigError(f"No existe el archivo de configuración {path}")
    config_dir = path.resolve().parent if path.is_file() else Path.cwd()
    load_dotenv(config_dir / ".env")
    if config_dir != Path.cwd():
        load_dotenv(Path.cwd() / ".env")
    data_dir = base_dir = data_dir_from_env(config_dir)

    data: dict = {}
    if path.is_file():
        try:
            with open(path, "rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} no es un TOML válido: {exc}") from exc
    else:
        log.debug("No se encontró config.toml; se usan valores por defecto")
    data = merge_overrides(data, overrides)

    unknown_sections = set(data) - set(KNOWN_KEYS)
    if unknown_sections:
        log.warning("Secciones desconocidas en config.toml: %s", ", ".join(sorted(unknown_sections)))
    bot = _section(data, "bot")
    markets = _section(data, "markets")
    risk = _section(data, "risk")
    strategy = _section(data, "strategy")
    http = _section(data, "http")

    def resolve(value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        p = Path(value).expanduser()
        return p if p.is_absolute() else base_dir / p

    # Credenciales: primero el entorno; si no hay, las guardadas desde el panel.
    panel = panel_credentials(data_dir)
    env_value = os.environ.get("KALSHI_ENV")
    env = (env_value or panel.get("env") or "demo").strip().lower()
    if env not in ENVIRONMENTS:
        raise ConfigError(f"KALSHI_ENV debe ser 'demo' o 'prod' (valor: {env!r})")
    api_key_id = os.environ.get("KALSHI_API_KEY_ID") or None
    key_pem = os.environ.get("KALSHI_PRIVATE_KEY") or None
    key_path = resolve(os.environ.get("KALSHI_PRIVATE_KEY_PATH"))
    source = "env" if (api_key_id or key_pem or key_path) else ""
    if not source and panel.get("key_id") and (data_dir / KEY_FILE).is_file():
        api_key_id, key_path, source = str(panel["key_id"]), data_dir / KEY_FILE, "panel"

    prefix = str(bot.get("order_prefix", "kb"))
    if not re.fullmatch(r"[A-Za-z0-9]{1,8}", prefix):
        raise ConfigError("order_prefix debe tener de 1 a 8 letras o números")

    engine = EngineConfig(
        poll_interval=_num(bot.get("poll_interval_seconds", 10), "poll_interval_seconds"),
        cancel_on_exit=_bool(bot.get("cancel_on_exit", True), "cancel_on_exit"),
        order_prefix=prefix,
        order_ttl_seconds=_num(bot.get("order_ttl_seconds", 600), "order_ttl_seconds", int),
        taker_cooldown_seconds=_num(bot.get("taker_cooldown_seconds", 30), "taker_cooldown_seconds"),
        requote_tolerance=_dec(bot.get("requote_tolerance", 0), "requote_tolerance"),
        max_consecutive_errors=_num(bot.get("max_consecutive_errors", 10), "max_consecutive_errors", int),
        paper_cash=_dec(bot.get("paper_cash", 1000), "paper_cash"),
        tickers=_list(markets.get("tickers"), "tickers"),
        series=_list(markets.get("series"), "series"),
        events=_list(markets.get("events"), "events"),
        max_markets=_num(markets.get("max_markets", 10), "max_markets", int),
        min_hours_to_close=_num(markets.get("min_hours_to_close", 0), "min_hours_to_close"),
        max_hours_to_close=_num(markets.get("max_hours_to_close", 0), "max_hours_to_close"),
        min_volume_24h=_dec(markets.get("min_volume_24h", 0), "min_volume_24h"),
        closing_within_hours=_num(markets.get("closing_within_hours", 0), "closing_within_hours"),
        max_markets_per_event=_num(markets.get("max_markets_per_event", 0), "max_markets_per_event", int),
        exclude_series=_list(markets.get("exclude_series"), "exclude_series"),
        refresh_markets_minutes=_num(markets.get("refresh_minutes", 5), "refresh_minutes"),
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
        min_minutes_to_close=_num(risk.get("min_minutes_to_close", 15), "min_minutes_to_close"),
    )
    if not (0 < limits.min_price < limits.max_price < 1):
        raise ConfigError("Se requiere 0 < min_price < max_price < 1")

    strategy_name = str(strategy.get("name", "favorites"))
    params = dict(strategy.get("params") or {})
    if strategy_name == "fair_value" and not params.get("fair_values_file"):
        params["fair_values_file"] = str(data_dir / "fair_values.csv")
    for key, value in list(params.items()):
        if key.endswith("_file") and isinstance(value, str) and value:
            params[key] = str(resolve(value))

    stp = str(http.get("self_trade_prevention", "taker_at_cross"))
    if stp not in ("taker_at_cross", "maker"):
        raise ConfigError("self_trade_prevention debe ser 'taker_at_cross' o 'maker'")

    return Settings(
        env=env,
        base_url=os.environ.get("KALSHI_BASE_URL") or ENVIRONMENTS[env],
        api_key_id=api_key_id,
        private_key_path=key_path,
        private_key_pem=key_pem,
        engine=engine,
        risk=limits,
        strategy_name=strategy_name,
        strategy_params=params,
        reads_per_second=_num(http.get("reads_per_second", 8), "reads_per_second"),
        writes_per_second=_num(http.get("writes_per_second", 4), "writes_per_second"),
        timeout_seconds=_num(http.get("timeout_seconds", 10), "timeout_seconds"),
        self_trade_prevention=stp,
        log_dir=resolve(str(bot.get("log_dir", "logs"))) or base_dir / "logs",
        config_path=path if path.is_file() else None,
        data_dir=data_dir,
        credentials_source=source,
        env_locked=bool(env_value),
    )
