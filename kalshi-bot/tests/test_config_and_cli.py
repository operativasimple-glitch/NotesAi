import json
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
    assert s.strategy_name == "favorites" and s.strategy_params["min_price"] == 0.88
    assert s.engine.series == ["KXMLBGAME", "KXNFLGAME", "KXNHLGAME", "KXNBAGAME", "KXNCAAFGAME"]
    assert s.engine.closing_within_hours == 0 and s.engine.max_hours_to_close == 6
    assert s.engine.max_markets_per_event == 1 and s.engine.poll_interval == 5
    # Sin config.toml, los valores por defecto son los mismos que los del ejemplo.
    (clean_env / "config.toml").unlink()
    bare = load_settings()
    assert bare.engine == s.engine and bare.risk == s.risk and bare.strategy_name == s.strategy_name
    assert s.risk.max_total_exposure == D("50") and s.risk.min_price == D("0.03")
    assert s.engine.order_ttl_seconds == 600 and s.log_dir == clean_env / "logs"
    assert s.signer() is None
    fv = load_settings(overrides={"strategy": {"name": "fair_value"}})
    assert fv.strategy_params == {"fair_values_file": str(clean_env / "fair_values.csv")}


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


# --- ajustes del panel y credenciales guardadas -------------------------------------


def test_overrides_merge_and_strategy_switch(clean_env):
    (clean_env / "config.toml").write_text(
        '[risk]\nmax_order_contracts = 5\n\n[strategy]\nname = "fair_value"\n\n'
        "[strategy.params]\nmin_edge = 0.05\norder_size = 3\n",
        encoding="utf-8",
    )
    same = load_settings(overrides={"strategy": {"name": "fair_value", "params": {"order_size": 7}}})
    assert same.strategy_params["min_edge"] == 0.05 and same.strategy_params["order_size"] == 7
    switched = load_settings(
        overrides={
            "strategy": {"name": "favorites", "params": {"min_price": "0.9"}},
            "risk": {"max_order_contracts": 9},
        }
    )
    assert switched.strategy_name == "favorites"
    assert switched.strategy_params == {"min_price": "0.9"}  # no arrastra parámetros de otra estrategia
    assert switched.risk.max_order_contracts == D("9")
    assert switched.sections()["risk"]["max_order_contracts"] == D("9")


def test_overrides_file_roundtrip_and_comma_lists(clean_env):
    from kalshi_bot.config import read_overrides, write_overrides

    write_overrides(clean_env, {"markets": {"series": "KXA, KXB", "closing_within_hours": 24}})
    s = load_settings(overrides=read_overrides(clean_env))
    assert s.engine.series == ["KXA", "KXB"] and s.engine.closing_within_hours == 24.0
    assert read_overrides(clean_env / "nada") == {}


def test_panel_credentials_are_used_when_env_is_empty(clean_env, key_pem):
    from kalshi_bot.config import delete_panel_credentials, save_panel_credentials, save_panel_env

    os.environ["KALSHI_BOT_DATA_DIR"] = str(clean_env / "data")
    save_panel_credentials(clean_env / "data", key_id="panel-id", private_key_pem=key_pem.decode(), env="demo")
    key_file = clean_env / "data" / "kalshi-key.pem"
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"
    s = load_settings(overrides={"strategy": {"name": "fair_value"}})
    assert s.credentials_source == "panel" and s.signer().key_id == "panel-id" and s.env == "demo"
    assert s.strategy_params["fair_values_file"] == str(clean_env / "data" / "fair_values.csv")
    assert s.log_dir == clean_env / "data" / "logs"

    save_panel_env(clean_env / "data", "prod")
    assert load_settings().env == "prod" and load_settings().env_locked is False
    os.environ["KALSHI_ENV"] = "demo"
    assert load_settings().env == "demo" and load_settings().env_locked is True  # el entorno manda

    os.environ["KALSHI_API_KEY_ID"] = "env-id"
    os.environ["KALSHI_PRIVATE_KEY"] = key_pem.decode()
    assert load_settings().credentials_source == "env" and load_settings().signer().key_id == "env-id"

    delete_panel_credentials(clean_env / "data")
    assert not key_file.exists()
    with pytest.raises(ValueError):
        save_panel_credentials(clean_env / "data", key_id="x", private_key_pem="basura", env="demo")


def test_cli_scan_and_research(fake_cli, capsys):
    fake_cli.markets[T] = make_market(T, yes_bid_dollars="0.9000", yes_ask_dollars="0.9200")
    fake_cli.events = []
    assert cli.main(["scan", "--hours", "48"]) == 0
    out = capsys.readouterr().out
    assert "FAVORITOS" in out and T in out

    settled = make_market("KXOLD-1", status="finalized", result="no")
    fake_cli.settled = {settled.ticker: settled}
    fake_cli.trades = {"KXOLD-1": [{"yes_price_dollars": "0.0500", "count_fp": "10.00", "taker_outcome_side": "yes"}]}
    assert cli.main(["research", "--series", "KXOLD"]) == 0
    out = capsys.readouterr().out
    assert "95–100¢" in out and "favoritos" in out


def test_placeholder_key_path_without_id_is_ignored(clean_env):
    os.environ["KALSHI_PRIVATE_KEY_PATH"] = "./kalshi-key.pem"  # tal cual viene en .env.example
    s = load_settings()
    assert s.credentials_source == "" and s.signer() is None


def test_cli_research_sweep(fake_cli, capsys, tmp_path):
    from .test_favorites_scanner_research import sweep_fixture

    data = sweep_fixture()
    fake_cli.markets, fake_cli.settled = data.markets, data.settled
    fake_cli.trades, fake_cli.series_info = data.trades, data.series_info
    out_file = tmp_path / "sweep.json"
    args = ["research", "--sweep", "--series-count", "5", "--per-series", "20", "--json", str(out_file)]
    assert cli.main(args) == 0
    saved = json.loads(out_file.read_text())
    assert saved["rows"][0]["series"] == "KXGOOD" and saved["overall_verdict"] == "pierde"
    out = capsys.readouterr().out
    assert "KXGOOD" in out and "Mejor serie: KXGOOD" in out and "TODAS JUNTAS" in out
    good_line = next(line for line in out.splitlines() if line.startswith("KXGOOD"))
    assert good_line.endswith("dudoso") and "0/12" in good_line


def test_cli_series_and_research_by_category(fake_cli, capsys):
    from .test_favorites_scanner_research import trade

    fake_cli.series_list = [
        {
            "ticker": "KXNASDAQ100",
            "title": "Nasdaq-100 al cierre",
            "category": "Financials",
            "categories": ["Financials"],
            "volume_fp": "900000.00",
        },
        {
            "ticker": "KXINX",
            "title": "S&P 500 al cierre",
            "category": "Financials",
            "categories": ["Financials"],
            "volume_fp": "2500000.00",
        },
        {
            "ticker": "KXHIGHNY",
            "title": "Máxima en NYC",
            "category": "Climate and Weather",
            "categories": ["Climate and Weather"],
            "volume_fp": "5000000.00",
        },
    ]
    for series in ("KXINX", "KXNASDAQ100"):
        for i in range(3):
            m = make_market(f"{series}-{i}", status="finalized", result="no", event_ticker=f"{series}-E{i}")
            fake_cli.settled[m.ticker] = m
            fake_cli.trades[m.ticker] = [trade("0.0500", 10, "yes")]

    assert cli.main(["series", "--category", "Financials"]) == 0
    out = capsys.readouterr().out
    assert out.index("KXINX") < out.index("KXNASDAQ100") and "KXHIGHNY" not in out  # por volumen, solo esa categoría

    assert cli.main(["research", "--category", "Financials", "--top", "2", "--markets", "5"]) == 0
    captured = capsys.readouterr()
    assert "KXINX, KXNASDAQ100" in captured.err
    lines = {line.split()[0]: line for line in captured.out.splitlines() if line.startswith(("KXINX", "KXNASDAQ100"))}
    assert set(lines) == {"KXINX", "KXNASDAQ100"} and "TODAS JUNTAS" in captured.out

    assert cli.main(["research", "--category", "Nada"]) == 1


def test_series_rules_in_config(clean_env):
    s = load_settings()
    assert s.engine.series_rules == {"KXHIGH": {"max_hours_to_close": 40.0, "max_markets_per_event": 4}}
    rules = {"kxinx": {"max_hours_to_close": "8", "max_markets_per_event": "2"}}
    s = load_settings(overrides={"markets": {"series_rules": rules}})
    assert s.engine.series_rules == {"KXINX": {"max_hours_to_close": 8.0, "max_markets_per_event": 2}}
    assert s.engine.market_filter().rule("KXINXU", "max_markets_per_event") == 2
    for bad in ({"KXA": {"velocidad": 1}}, {"KXA": 3}, "KXA"):
        with pytest.raises(ConfigError, match="series_rules"):
            load_settings(overrides={"markets": {"series_rules": bad}})
