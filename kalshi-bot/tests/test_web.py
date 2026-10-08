import http.cookiejar
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_bot.client import KalshiAPIError
from kalshi_bot.config import write_overrides
from kalshi_bot.controller import BotController
from kalshi_bot.models import ASK, BID, Fill, Settlement
from kalshi_bot.web import server as web_server
from kalshi_bot.web.server import Sessions, make_server

from .fakes import FakeKalshi, make_book, make_market

T = "KXTEST-26OCT08-B50"
PASSWORD = "secreto-123"


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


class Panel:
    def __init__(self, base, fake, controller):
        self.base, self.fake, self.controller = base, fake, controller
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, csrf=True):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if csrf:
            req.add_header("X-Requested-With", "kalshi-bot")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=10) as resp:
                raw = resp.read()
                return (
                    resp.status,
                    (json.loads(raw) if raw and "json" in resp.headers.get("Content-Type", "") else raw),
                    resp.headers,
                )
        except urllib.error.HTTPError as err:
            raw = err.read()
            is_json = raw and "json" in err.headers.get("Content-Type", "")
            return err.code, (json.loads(raw) if is_json else raw), err.headers

    def login(self):
        status, body, _ = self.call("POST", "/api/login", {"password": PASSWORD})
        assert status == 200, body

    def wait(self, predicate, timeout=8.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.05)
        return False


@pytest.fixture
def panel(tmp_path, monkeypatch):
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("KALSHI_") or key == "DASHBOARD_PASSWORD":
            del os.environ[key]
    os.environ["KALSHI_BOT_DATA_DIR"] = str(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web_server.time, "sleep", lambda s: None)  # sin esperas en los fallos de login

    # Un mercado que se decide dentro de 3 h (con la hora real: el panel usa el reloj de verdad).
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    fake = FakeKalshi(
        markets=[make_market(T, close_time=soon)],
        books={T: make_book(T, bids=[("0.90", 50)], asks=[("0.92", 50)])},
        authenticated=False,
    )
    write_overrides(tmp_path, {"markets": {"series": ["KXTEST"]}})  # en vez de las series de partidos

    def factory(settings, signer):
        fake.authenticated = signer is not None
        return fake

    controller = BotController(None, client_factory=factory)
    root = logging.getLogger()
    root_level = root.level
    root.setLevel(logging.INFO)  # como `kalshi_bot web`: el panel muestra los INFO del bot
    root.addHandler(controller.logs)
    srv = make_server(controller, PASSWORD, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield Panel(f"http://127.0.0.1:{srv.server_address[1]}", fake, controller)
    finally:
        controller.stop(timeout=5)
        srv.shutdown()
        srv.server_close()
        root.removeHandler(controller.logs)
        root.setLevel(root_level)
        os.environ.clear()
        os.environ.update(saved)


def test_static_files_and_security_headers(panel):
    status, body, headers = panel.call("GET", "/", csrf=False)
    assert status == 200 and b"Kalshi Bot" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert headers["X-Frame-Options"] == "DENY"
    assert panel.call("GET", "/app.js", csrf=False)[0] == 200
    assert panel.call("GET", "/manifest.webmanifest", csrf=False)[0] == 200
    assert panel.call("GET", "/../config.py", csrf=False)[0] == 404
    assert panel.call("GET", "/healthz", csrf=False)[1] == b"ok"


def test_login_is_required_and_protected(panel):
    assert panel.call("GET", "/api/status")[0] == 401
    assert panel.call("POST", "/api/login", {"password": PASSWORD}, csrf=False)[0] == 403
    assert panel.call("POST", "/api/login", {"password": "nope"})[0] == 401
    panel.login()
    status, body, _ = panel.call("GET", "/api/status")
    assert status == 200 and body["env"] == "demo" and body["bot"]["state"] == "stopped"
    assert panel.call("POST", "/api/bot/start", {"mode": "sim"}, csrf=False)[0] == 403  # sin cabecera CSRF
    panel.call("POST", "/api/logout")
    assert panel.call("GET", "/api/status")[0] == 401


def test_login_lockout_after_repeated_failures(panel):
    for _ in range(5):
        assert panel.call("POST", "/api/login", {"password": "mal"})[0] == 401
    status, body, _ = panel.call("POST", "/api/login", {"password": PASSWORD})
    assert status == 429 and "Espera" in body["error"]


def test_sessions_tokens():
    now = [1_000_000.0]
    sessions = Sessions("clave-larga", clock=lambda: now[0])
    token = sessions.issue()
    assert sessions.valid(token)
    assert not sessions.valid(token[:-2] + "xx")
    assert not Sessions("otra-clave").valid(token)
    now[0] += 31 * 24 * 3600
    assert not sessions.valid(token)


def test_start_simulation_and_stop(panel):
    panel.login()
    status, body, _ = panel.call("POST", "/api/bot/start", {"mode": "sim"})
    assert status == 200 and body["bot"]["state"] == "running" and body["bot"]["mode"] == "sim"
    assert panel.wait(lambda: panel.call("GET", "/api/status")[1]["bot"]["ticks"] >= 1)
    status, logs, _ = panel.call("GET", "/api/logs?after=0")
    assert any("SIMULACIÓN" in line["message"] for line in logs)
    assert panel.call("POST", "/api/bot/start", {"mode": "sim"})[0] == 400  # ya está en marcha
    status, body, _ = panel.call("POST", "/api/bot/stop")
    assert body["bot"]["state"] == "stopped"
    assert panel.fake.created == []  # la simulación nunca envía órdenes


def test_live_needs_credentials_and_real_money_confirmation(panel, pem):
    panel.login()
    status, body, _ = panel.call("POST", "/api/bot/start", {"mode": "live"})
    assert status == 400 and "API key" in body["error"]

    status, creds, _ = panel.call(
        "POST", "/api/credentials", {"key_id": "abc12345xyz", "private_key": pem, "env": "demo"}
    )
    assert status == 200 and creds["configured"] and creds["source"] == "panel" and creds["key_id_hint"] == "abc12345"
    assert panel.call("POST", "/api/credentials", {"key_id": "x", "private_key": "basura"})[0] == 400

    assert panel.call("POST", "/api/env", {"env": "prod"})[1]["env"] == "prod"
    status, body, _ = panel.call("POST", "/api/bot/start", {"mode": "live"})
    assert status == 400 and "REAL" in body["error"]
    status, body, _ = panel.call("POST", "/api/bot/start", {"mode": "live", "confirm": "REAL"})
    assert status == 200 and body["is_production"] and body["bot"]["mode"] == "live"
    assert panel.wait(lambda: len(panel.fake.created) >= 1)
    created = panel.fake.created[0]
    assert created["intent"].side == BID and created["intent"].price == D("0.91")  # estrategia por defecto: favoritos
    assert panel.call("POST", "/api/env", {"env": "demo"})[0] == 400  # no se cambia de entorno con el bot en marcha

    status, body, _ = panel.call("POST", "/api/bot/kill", {})
    assert status == 200 and body["cancelled"] >= 1
    assert panel.fake.orders == {}
    assert panel.call("DELETE", "/api/credentials")[1]["configured"] is False


def test_manual_order_portfolio_and_cancel(panel, pem):
    panel.login()
    panel.call("POST", "/api/credentials", {"key_id": "abc12345", "private_key": pem, "env": "demo"})
    status, body, _ = panel.call("POST", "/api/orders", {"ticker": T, "outcome": "no", "price": "0.93", "count": 10})
    assert status == 200 and D(body["price"]) == D("0.93")
    intent = panel.fake.created[-1]["intent"]
    assert (intent.side, intent.price, intent.count) == (ASK, D("0.07"), D("10"))
    assert panel.fake.created[-1]["client_order_id"].startswith("man-")

    status, orders, _ = panel.call("GET", "/api/orders")
    assert [(o["outcome"], D(o["price"]), o["source"]) for o in orders] == [("no", D("0.93"), "manual")]
    panel.fake.set_position(T, -10, exposure="9.30")
    status, positions, _ = panel.call("GET", "/api/positions")
    assert positions[0]["side"] == "no" and D(positions[0]["contracts"]) == 10

    order_id = orders[0]["order_id"]
    assert panel.call("POST", "/api/orders/cancel", {"order_id": order_id, "ticker": T})[0] == 200
    assert order_id in panel.fake.cancelled
    assert panel.call("POST", "/api/orders", {"ticker": T, "outcome": "yes", "price": "1.5", "count": 1})[0] == 400


def test_portfolio_shows_market_names_and_what_each_position_is_worth(panel, pem):
    panel.login()
    panel.call("POST", "/api/credentials", {"key_id": "abc12345", "private_key": pem, "env": "demo"})
    panel.fake.set_position(T, -10, exposure="9.30")
    p = panel.call("GET", "/api/positions")[1][0]
    assert (p["title"], p["subtitle"]) == ("¿Mercado de prueba?", "50 o más")
    # El SÍ cotiza a 45¢ / 48¢: el NO vale ahora 1 − 0,465.
    assert (D(p["chance"]), D(p["value"]), D(p["payout"])) == (D("0.535"), D("5.35"), D("10"))
    assert 2.9 < p["hours_to_close"] <= 3 and p["status"] == "active"

    panel.call("POST", "/api/orders", {"ticker": T, "outcome": "no", "price": "0.93", "count": 10})
    assert panel.call("GET", "/api/orders")[1][0]["title"] == "¿Mercado de prueba?"

    queries = len(panel.fake.market_queries)
    names = panel.call("GET", f"/api/labels?tickers={T},no-valido!,KXNADA-1")[1]
    assert names == {T: {"title": "¿Mercado de prueba?", "subtitle": "50 o más"}}
    panel.call("GET", f"/api/labels?tickers={T},KXNADA-1")
    assert len(panel.fake.market_queries) == queries + 1  # los nombres se guardan, también los que no existen

    def broken(**kwargs):
        raise RuntimeError("caída")

    panel.fake.get_markets = broken  # sin nombres ni precios, las posiciones se siguen viendo
    p = panel.call("GET", "/api/positions")[1][0]
    assert (p["title"], p["chance"], p["value"], D(p["contracts"])) == ("", None, None, D("10"))


def test_settings_roundtrip_and_validation(panel):
    panel.login()
    status, payload, _ = panel.call("GET", "/api/settings")
    names = [s["name"] for s in payload["strategies"]]
    assert names == ["favorites", "fair_value", "market_maker"]
    assert payload["values"]["strategy"]["name"] == "favorites"

    values = {
        "strategy": {"name": "market_maker", "params": {"half_spread": "0.0300", "quote_size": "3"}},
        "risk": {"max_order_contracts": "4", "min_price": "0.0500", "max_price": "0.9500"},
        "markets": {"tickers": [T], "series": ["KXA", "KXB"], "closing_within_hours": "0"},
    }
    status, payload, _ = panel.call("PUT", "/api/settings", {"values": values})
    assert status == 200
    assert payload["values"]["strategy"] == {
        "name": "market_maker",
        "params": {"half_spread": "0.0300", "quote_size": "3"},
    }
    assert payload["values"]["risk"]["max_order_contracts"] == "4"
    assert payload["values"]["markets"]["series"] == ["KXA", "KXB"]

    bad = {"risk": {"min_price": "0.9900", "max_price": "0.5000"}}
    assert panel.call("PUT", "/api/settings", {"values": bad})[0] == 400
    bad_param = {"strategy": {"name": "favorites", "params": {"min_price": "0.30"}}}
    assert panel.call("PUT", "/api/settings", {"values": bad_param})[0] == 400

    assert panel.call("POST", "/api/markets/follow", {"ticker": "KXNEW-1"})[1]["tickers"] == [T, "KXNEW-1"]


def test_fair_values_api(panel):
    panel.login()
    rows = [{"ticker": "KXA-1", "probability": "35%"}, {"ticker": "KXB-2", "probability": "0.6"}, {"ticker": ""}]
    status, saved, _ = panel.call("PUT", "/api/fair-values", {"rows": rows})
    assert status == 200 and saved == [
        {"ticker": "KXA-1", "probability": "0.35"},
        {"ticker": "KXB-2", "probability": "0.6"},
    ]
    assert panel.call("GET", "/api/fair-values")[1] == saved
    assert panel.call("PUT", "/api/fair-values", {"rows": [{"ticker": "X", "probability": "abc"}]})[0] == 400


def test_markets_and_market_detail(panel):
    panel.login()
    status, markets, _ = panel.call("GET", "/api/markets?series=KXTEST")
    assert status == 200 and markets[0]["ticker"] == T and D(markets[0]["yes_bid"]) == D("0.45")
    status, detail, _ = panel.call("GET", f"/api/market?ticker={T}")
    assert D(detail["book"]["best_bid"]) == D("0.90") and D(detail["book"]["best_ask"]) == D("0.92")
    assert panel.call("GET", "/api/markets")[0] == 400
    assert panel.call("GET", "/api/events")[0] == 200


def test_scan_and_research_jobs(panel):
    panel.login()
    panel.fake.markets[T] = make_market(T, yes_bid_dollars="0.9000", yes_ask_dollars="0.9200")
    panel.fake.events = []
    assert panel.call("POST", "/api/scan", {"hours": 48})[0] == 200
    assert panel.wait(lambda: panel.call("GET", "/api/scan")[1]["state"] == "done")
    assert [f["ticker"] for f in panel.call("GET", "/api/scan")[1]["result"]["favorites"]] == [T]

    settled = make_market("KXOLD-1", status="finalized", result="no")
    panel.fake.settled = {settled.ticker: settled}
    panel.fake.trades = {"KXOLD-1": [{"yes_price_dollars": "0.0500", "count_fp": "10.00", "taker_outcome_side": "yes"}]}
    assert panel.call("POST", "/api/research", {"series": "KXOLD", "markets": 50})[0] == 200
    assert panel.wait(lambda: panel.call("GET", "/api/research")[1]["state"] == "done")
    job = panel.call("GET", "/api/research")[1]
    assert job["result"]["markets"] == 1 and job["done"] == 1


def test_resume_after_restart_unless_halted(panel):
    controller = panel.controller
    controller._write_state({"desired": "sim", "halted": None})
    controller.resume_if_needed()
    assert controller.is_running()
    controller.shutdown(timeout=5)
    assert controller._read_state()["desired"] == "sim"  # el apagado del servidor no lo olvida
    controller._write_state({"desired": "sim", "halted": "pérdida máxima"})
    controller.resume_if_needed()
    assert not controller.is_running()


def test_status_survives_broken_credentials(panel):
    panel.login()
    os.environ["KALSHI_API_KEY_ID"] = "id-sin-clave"
    os.environ["KALSHI_PRIVATE_KEY_PATH"] = "no-existe.pem"
    status, body, _ = panel.call("GET", "/api/status")
    assert status == 200 and "credenciales no válidas" in body["balance_error"]


def test_sweep_job_and_use_series(panel):
    panel.login()
    from .test_favorites_scanner_research import sweep_fixture

    data = sweep_fixture()
    panel.fake.markets, panel.fake.settled = data.markets, data.settled
    panel.fake.trades, panel.fake.series_info = data.trades, data.series_info
    assert panel.call("POST", "/api/sweep", {"series_count": 5, "per_series": 20})[0] == 200
    assert panel.wait(lambda: panel.call("GET", "/api/sweep")[1]["state"] == "done")
    rows = panel.call("GET", "/api/sweep")[1]["result"]["rows"]
    assert [r["series"] for r in rows] == ["KXGOOD", "KXBAD"]

    status, body, _ = panel.call("POST", "/api/markets/use-series", {"series": ["kxgood"]})
    assert status == 200 and body["series"] == ["KXGOOD"]
    markets = panel.call("GET", "/api/settings")[1]["values"]["markets"]
    assert markets["series"] == ["KXGOOD"] and markets["closing_within_hours"] == 0
    assert panel.call("POST", "/api/markets/use-series", {"series": []})[0] == 400


def test_diagnose_checks_each_step_and_round_trips_a_test_order(panel, pem):
    panel.login()
    status, r, _ = panel.call("POST", "/api/diagnose", {})
    assert status == 200 and r["ok"] is False
    steps = {s["name"]: s for s in r["steps"]}
    assert steps["Conexión con Kalshi"]["ok"]
    assert not steps["API key"]["ok"] and "Ajustes" in steps["API key"]["hint"]
    assert steps["Mercados"]["ok"] and T in steps["Mercados"]["detail"]
    assert steps["Libro de órdenes"]["ok"] and "90¢" in steps["Libro de órdenes"]["detail"]

    # La clave pegada desde el móvil sin saltos de línea se guarda normalizada.
    body = {"key_id": ' "kid-1"\n', "private_key": pem.replace("\n", " "), "env": "demo"}
    assert panel.call("POST", "/api/credentials", body)[0] == 200
    saved = panel.controller.data_dir / "kalshi-key.pem"
    assert saved.read_text().startswith("-----BEGIN PRIVATE KEY-----\n")
    assert oct(saved.stat().st_mode & 0o777) == "0o600"
    assert json.loads((panel.controller.data_dir / "credentials.json").read_text())["key_id"] == "kid-1"

    status, r, _ = panel.call("POST", "/api/diagnose", {"order_test": True})
    assert status == 200 and r["ok"] is True, r
    assert [s["name"] for s in r["steps"]] == [
        "Conexión con Kalshi",
        "API key",
        "Saldo",
        "Cartera",
        "Mercados",
        "Libro de órdenes",
        "Orden de prueba",
    ]
    created = panel.fake.created[-1]
    intent = created["intent"]
    assert created["client_order_id"].startswith("diag-") and created["expiration_ts"]
    assert (intent.ticker, intent.side, intent.price, intent.count, intent.post_only) == (T, BID, D("0.01"), D(1), True)
    assert panel.fake.cancelled and not panel.fake.orders  # cancelada al momento

    # Con dinero real, la orden de prueba exige confirmación explícita.
    assert panel.call("POST", "/api/env", {"env": "prod"})[0] == 200
    status, body, _ = panel.call("POST", "/api/diagnose", {"order_test": True})
    assert status == 400 and "dinero real" in body["error"]
    assert panel.call("POST", "/api/diagnose", {"order_test": True, "confirm": True})[1]["ok"] is True

    # Sin conexión con Kalshi se para en el primer paso y dice por qué.
    panel.fake.failures["get_exchange_status"] = KalshiAPIError(0, "network_error", "sin conexión")
    r = panel.call("POST", "/api/diagnose", {})[1]
    assert [s["name"] for s in r["steps"]] == ["Conexión con Kalshi"] and "EE. UU." in r["steps"][0]["hint"]


def test_diagnose_detects_a_key_from_the_other_environment(panel, pem):
    panel.login()
    assert panel.call("POST", "/api/credentials", {"key_id": "kid-1", "private_key": pem, "env": "demo"})[0] == 200
    fake = panel.fake

    def factory(settings, signer):  # la key solo vale en Real
        fake.authenticated = signer is not None
        if signer is not None and settings.env == "demo":
            fake.failures["get_balance"] = KalshiAPIError(401, "authentication_error", "invalid key")
        else:
            fake.failures.pop("get_balance", None)
        return fake

    panel.controller._client_factory = factory
    r = panel.call("POST", "/api/diagnose", {})[1]
    key = next(s for s in r["steps"] if s["name"] == "API key")
    assert r["suggest_env"] == "prod" and not key["ok"] and "Real" in key["detail"]

    assert panel.call("POST", "/api/env", {"env": "prod"})[0] == 200
    r = panel.call("POST", "/api/diagnose", {})[1]
    assert r["ok"] is True and r["suggest_env"] is None


def test_credentials_reject_a_private_key_in_the_key_id_field(panel, pem):
    panel.login()
    status, body, _ = panel.call("POST", "/api/credentials", {"key_id": pem, "private_key": pem, "env": "demo"})
    assert status == 400 and "Key ID" in body["error"]


def test_results_api(panel, pem):
    panel.login()
    status, body, _ = panel.call("GET", "/api/results")
    assert status == 400 and "API key" in body["error"]
    panel.call("POST", "/api/credentials", {"key_id": "abc12345", "private_key": pem, "env": "demo"})
    now = datetime.now(timezone.utc)
    panel.fake.fill_history = [Fill(T, BID, D("0.92"), D("5"), D("0.01"), now - timedelta(hours=3))]
    panel.fake.settlements = [
        Settlement.from_api(
            {
                "ticker": T,
                "market_result": "yes",
                "yes_count_fp": "5.00",
                "yes_total_cost_dollars": "4.60",
                "revenue": 500,
                "fee_cost": "0.01",
                "settled_time": (now - timedelta(hours=1)).isoformat(),
            }
        )
    ]
    status, body, _ = panel.call("GET", "/api/results?days=500&tz=300&scope=all")
    assert status == 200 and body["days"] == 90 and len(body["by_day"]) == 90 and body["scope"] == "all"
    assert D(body["totals"]["net"]) == D("0.39") and body["totals"]["wins"] == 1
    assert body["recent"][0]["title"] == "¿Mercado de prueba?" and body["by_category"][0]["key"] == "otros"
    assert panel.call("GET", "/api/results?days=semana")[0] == 400

    # Por defecto, solo lo del bot: sin su diario no hay nada suyo.
    status, body, _ = panel.call("GET", "/api/results?days=30")
    assert body["scope"] == "bot" and body["totals"]["markets"] == 0 and body["bot_history"] is False
    # Con la orden en su diario, ese mercado ya cuenta como del bot.
    panel.fake.fill_history[0].order_id = "ord-bot"
    journal = panel.controller.settings().log_dir / "journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(
        json.dumps({"event": "place", "client_order_id": "kb-1", "response": {"order_id": "ord-bot"}}) + "\n",
        encoding="utf-8",
    )
    status, body, _ = panel.call("GET", "/api/results?days=7")
    assert body["bot_history"] is True and D(body["totals"]["net"]) == D("0.39")


def test_bot_order_ids_come_from_real_bot_orders_in_the_journal(panel):
    journal = panel.controller.settings().log_dir / "journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {"event": "place", "client_order_id": "kb-1", "response": {"order_id": "A"}},
        {"event": "place", "client_order_id": "kb-2", "dry_run": True},  # simulación: no es real
        {"event": "place", "client_order_id": "man-3", "response": {"order_id": "B"}},  # otro prefijo
        {"event": "cancel", "order_id": "C"},
        {"event": "place", "client_order_id": "kb-4", "response": {"order_id": "D"}},
    ]
    journal.write_text("\n".join(json.dumps(x) for x in lines) + "\n{roto\n", encoding="utf-8")
    assert panel.controller.bot_order_ids() == {"A", "D"}
    journal.unlink()
    assert panel.controller.bot_order_ids() == set()


def test_log_history_survives_a_restart(tmp_path):
    from kalshi_bot.controller import LogBuffer

    log_file = tmp_path / "bot.log"
    log_file.write_text(
        "2026-10-08 13:44:01,250 ERROR   kalshi_bot.engine: Error de la API de Kalshi: HTTP 503\n"
        "línea rota\n"
        "2026-10-08 13:44:06,000 ERROR   kalshi_bot.engine: FRENO DE EMERGENCIA: 10 vueltas seguidas con errores\n",
        encoding="utf-8",
    )
    logs = LogBuffer()
    logs.preload(log_file)
    lines = logs.since(0)
    assert [(line["level"], line["message"]) for line in lines] == [
        ("ERROR", "Error de la API de Kalshi: HTTP 503"),
        ("ERROR", "FRENO DE EMERGENCIA: 10 vueltas seguidas con errores"),
    ]
    assert lines[0]["ts"].startswith("2026-10-08T13:44:01.250") and lines[1]["id"] == 2
    LogBuffer().preload(tmp_path / "no-existe.log")  # sin archivo no pasa nada
