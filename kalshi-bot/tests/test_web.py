import http.cookiejar
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal as D

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_bot.controller import BotController
from kalshi_bot.models import ASK, BID
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

    fake = FakeKalshi(
        markets=[make_market(T)],
        books={T: make_book(T, bids=[("0.90", 50)], asks=[("0.92", 50)])},
        authenticated=False,
    )

    def factory(settings, signer):
        fake.authenticated = signer is not None
        return fake

    controller = BotController(None, client_factory=factory)
    logging.getLogger().addHandler(controller.logs)
    srv = make_server(controller, PASSWORD, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield Panel(f"http://127.0.0.1:{srv.server_address[1]}", fake, controller)
    finally:
        controller.stop(timeout=5)
        srv.shutdown()
        srv.server_close()
        logging.getLogger().removeHandler(controller.logs)
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
