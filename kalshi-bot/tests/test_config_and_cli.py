import logging
import os
from decimal import Decimal as D
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_bot import cli
from kalshi_bot.config import ConfigError, Settings, load_dotenv, load_settings
from kalshi_bot.engine import Bot

from .fakes import FakeKalshi, make_book, make_market

T = "KXTEST-26OCT08-B50"
EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "config.example.toml"


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("KALSHI_"):
            del os.environ[key]
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def key_pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def test_example_config_loads_with_defaults(clean_env):
    (clean_env / "config.toml").write_text(EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    s = load_settings()
    assert s.env == "demo" and s.base_url.startswith("https://external-api.demo.kalshi.co")
    assert s.strategy_name == "fair_value"
    assert s.strategy_params["fair_values_file"] == str(clean_env / "fair_values.csv")
    assert s.risk.max_total_exposure == D("50") and s.risk.min_price == D("0.03")
    assert s.engine.order_ttl_seconds == 600 and s.log_dir == clean_env / "logs"
    assert s.signer() is None


def test_env_file_credentials_and_relative_paths(clean_env, key_pem):
    (clean_env / "mi-key.pem").write_bytes(key_pem)
    (clean_env / ".env").write_text(
        "# comentario\nexport KALSHI_ENV=prod\nKALSHI_API_KEY_ID='abc-123'\nKALSHI_PRIVATE_KEY_PATH=./mi-key.pem\n",
        encoding="utf-8",
    )
    (clean_env / "config.toml").write_text('[markets]\nseries = "KXHIGHNY"\n', encoding="utf-8")
    s = load_settings()
    assert s.env == "prod" and s.base_url == "https://external-api.kalshi.com/trade-api/v2"
    assert s.private_key_path == clean_env / "mi-key.pem"
    assert s.signer().key_id == "abc-123"
    assert s.engine.series == ["KXHIGHNY"]


def test_inline_private_key(clean_env, key_pem):
    os.environ["KALSHI_API_KEY_ID"] = "id"
    os.environ["KALSHI_PRIVATE_KEY"] = key_pem.decode().replace("\n", "\\n")
    assert load_settings().signer().key_id == "id"


def test_dotenv_does_not_override_existing_variables(clean_env):
    os.environ["KALSHI_ENV"] = "demo"
    (clean_env / ".env").write_text('KALSHI_ENV=prod\nKALSHI_API_KEY_ID="x"\n', encoding="utf-8")
    load_dotenv(clean_env / ".env")
    assert os.environ["KALSHI_ENV"] == "demo" and os.environ["KALSHI_API_KEY_ID"] == "x"


def test_config_errors(clean_env, caplog):
    os.environ["KALSHI_ENV"] = "real"
    with pytest.raises(ConfigError, match="KALSHI_ENV"):
        load_settings()
    del os.environ["KALSHI_ENV"]

    os.environ["KALSHI_API_KEY_ID"] = "abc"
    with pytest.raises(ConfigError, match="KALSHI_PRIVATE_KEY_PATH"):
        load_settings().signer()
    os.environ["KALSHI_PRIVATE_KEY_PATH"] = "no-existe.pem"
    with pytest.raises(ConfigError, match="No existe"):
        load_settings().signer()

    (clean_env / "config.toml").write_text("[risk\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="TOML"):
        load_settings()

    (clean_env / "config.toml").write_text('[bot]\norder_prefix = "con espacios"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="order_prefix"):
        load_settings()

    (clean_env / "config.toml").write_text("[risk]\nmax_exposicion = 5\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        load_settings()
    assert "max_exposicion" in caplog.text

    with pytest.raises(ConfigError, match="No existe el archivo de configuración"):
        load_settings("otro.toml")


# --- CLI (sin red: el cliente se sustituye por FakeKalshi) -------------------------


@pytest.fixture
def fake_cli(clean_env, monkeypatch):
    fake = FakeKalshi(
        markets=[make_market(T)],
        books={T: make_book(T, bids=[("0.40", 10)], asks=[("0.50", 10)])},
        authenticated=False,
    )
    monkeypatch.setattr(Settings, "client", lambda self, signer=None: fake)
    monkeypatch.setattr(Bot, "_install_signal_handlers", lambda self: None)
    root = logging.getLogger()
    handlers = list(root.handlers)
    yield fake
    for handler in root.handlers[:]:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()


def test_cli_markets_book_events_and_check(fake_cli, capsys):
    assert cli.main(["markets"]) == 2
    assert cli.main(["markets", "--series", "KXTEST"]) == 0
    out = capsys.readouterr().out
    assert T in out and "45¢" in out and "48¢" in out

    assert cli.main(["book", T]) == 0
    out = capsys.readouterr().out
    assert "Mejor bid 40¢ | mejor ask 50¢ | spread 10¢" in out

    assert cli.main(["events"]) == 0
    assert "KXTEST-26OCT08" in capsys.readouterr().out

    assert cli.main(["check"]) == 0
    assert "no configuradas" in capsys.readouterr().out


def test_cli_private_commands_need_credentials(fake_cli, capsys):
    assert cli.main(["positions"]) == 2
    assert "credenciales" in capsys.readouterr().err
    assert cli.main(["run", "--live", "--once"]) == 2


def test_cli_run_once_in_simulation(fake_cli, capsys):
    (fake_cli_dir := Path.cwd()).joinpath("config.toml").write_text(
        '[markets]\ntickers = ["%s"]\n\n[strategy]\nname = "market_maker"\n\n'
        "[strategy.params]\nhalf_spread = 0.02\nquote_size = 1\n" % T,
        encoding="utf-8",
    )
    assert cli.main(["run", "--once"]) == 0
    assert fake_cli.created == []  # simulación: nada llega al exchange
    log_text = (fake_cli_dir / "logs" / "bot.log").read_text(encoding="utf-8")
    assert "[SIMULACIÓN] COMPRA YES 1.00 @ 0.4300" in log_text
    assert "[SIMULACIÓN] VENDE YES 1.00 @ 0.4700" in log_text
    assert (fake_cli_dir / "logs" / "journal.jsonl").is_file()


def test_cli_formatting_helpers():
    assert cli.cents(D("0.455")) == "45.5¢"
    assert cli.cents(D("0.40")) == "40¢"
    assert cli.cents(None) == "—"
    assert cli.contracts(D("12.50")) == "12.5"
    assert cli.contracts(D("3.00")) == "3"


def test_real_money_detection_uses_the_api_host(clean_env):
    assert load_settings().is_production is False
    os.environ["KALSHI_ENV"] = "prod"
    assert load_settings().is_production is True
    os.environ["KALSHI_ENV"] = "demo"
    os.environ["KALSHI_BASE_URL"] = "https://api.elections.kalshi.com/trade-api/v2"
    assert load_settings().is_production is True  # la URL manda sobre KALSHI_ENV
