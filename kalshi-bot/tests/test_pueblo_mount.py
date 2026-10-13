"""El pueblo de bots dentro del panel: /pueblo/, misma sesión y los vecinos leyendo el bot sin red."""

import gzip
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from kalshi_bot.config import write_overrides
from kalshi_bot.controller import BotController
from kalshi_bot.web import server as web_server
from kalshi_bot.web.pueblo import LocalPanel, PuebloMount
from kalshi_bot.web.server import make_server

from .fakes import FakeKalshi, make_book, make_market
from .test_web import PASSWORD, Panel

T = "KXTEST-26OCT08-B50"


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


@pytest.fixture
def site(tmp_path, monkeypatch):
    saved = dict(os.environ)
    for key in list(os.environ):
        if key.startswith("KALSHI_") or key == "DASHBOARD_PASSWORD":
            del os.environ[key]
    os.environ["KALSHI_BOT_DATA_DIR"] = str(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(web_server.time, "sleep", lambda s: None)
    soon = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    fake = FakeKalshi(
        markets=[make_market(T, close_time=soon)],
        books={T: make_book(T, bids=[("0.90", 50)], asks=[("0.92", 50)])},
        authenticated=False,
    )
    write_overrides(tmp_path, {"markets": {"series": ["KXTEST"]}})
    def factory(settings, signer):
        fake.authenticated = signer is not None
        return fake

    controller = BotController(None, client_factory=factory)
    # Sin arrancar los hilos: en las pruebas cada vecino trabaja cuando se le llama (sin red).
    pueblo = PuebloMount(controller, tmp_path / "pueblo", "America/Chicago", env={})
    srv = make_server(controller, PASSWORD, "127.0.0.1", 0, pueblo=pueblo)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield Panel(f"http://127.0.0.1:{srv.server_address[1]}", fake, controller), pueblo
    finally:
        controller.stop(timeout=5)
        srv.shutdown()
        srv.server_close()
        os.environ.clear()
        os.environ.update(saved)


def test_pueblo_web_lives_in_the_panel(site):
    panel, _ = site
    status, _, headers = panel.call("GET", "/pueblo", csrf=False)
    assert status == 200 and headers.get_content_type() == "text/html"  # la redirección lleva a /pueblo/
    status, body, headers = panel.call("GET", "/pueblo/", csrf=False)
    assert status == 200 and b"Pueblo de bots" in body and b"Contrase\xc3\xb1a del panel del bot" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    for path in ("/pueblo/app.js", "/pueblo/base3d.js", "/pueblo/vendor/three.module.min.js", "/pueblo/app.css"):
        assert panel.call("GET", path, csrf=False)[0] == 200, path
    assert panel.call("GET", "/pueblo/../config.py", csrf=False)[0] == 404
    assert panel.call("GET", "/pueblo/nada.js", csrf=False)[0] == 404
    # El panel sigue en su sitio.
    status, body, _ = panel.call("GET", "/", csrf=False)
    assert status == 200 and b"Kalshi Bot" in body


def test_pueblo_api_uses_the_panel_session(site):
    panel, _ = site
    assert panel.call("GET", "/pueblo/api/world")[0] == 401
    assert panel.call("POST", "/pueblo/api/login", {"password": PASSWORD}, csrf=False)[0] == 403
    assert panel.call("POST", "/pueblo/api/login", {"password": "nope"})[0] == 401
    assert panel.call("POST", "/pueblo/api/login", {"password": PASSWORD})[0] == 200
    status, world, _ = panel.call("GET", "/pueblo/api/world")
    assert status == 200 and "kali" in {b["id"] for b in world["bots"]} and "messages" in world
    # Entrar en el pueblo es entrar en el panel (y al revés): una sola contraseña.
    assert panel.call("GET", "/api/status")[0] == 200
    assert panel.call("POST", "/pueblo/api/talk", {"bot": "kali"}, csrf=False)[0] == 403
    status, message, _ = panel.call("POST", "/pueblo/api/talk", {"bot": "kali"})
    assert status == 200 and message["from"] == "kali" and message["text"]
    assert panel.call("POST", "/pueblo/api/talk", {"bot": "nadie"})[0] == 404
    assert panel.call("POST", "/pueblo/api/talk", {"bot": "../x"})[0] == 400
    assert panel.call("GET", "/pueblo/api/otra")[0] == 404


def test_big_world_answers_are_gzipped(site):
    panel, pueblo = site
    panel.login()
    for i in range(30):
        pueblo.world.say("kali", f"Mensaje de prueba número {i} con algo de texto para que pese", kind="info")
    import urllib.request

    req = urllib.request.Request(panel.base + "/pueblo/api/world", headers={"Accept-Encoding": "gzip"})
    with panel.opener.open(req, timeout=10) as resp:
        assert resp.headers["Content-Encoding"] == "gzip"
        data = gzip.decompress(resp.read())
    assert b"Mensaje de prueba" in data


def with_key(panel, pem):
    panel.login()
    body = {"key_id": "abc12345xyz", "private_key": pem, "env": "demo"}
    assert panel.call("POST", "/api/credentials", body)[0] == 200


def test_kali_reads_the_bot_without_the_network(site, pem):
    panel, pueblo = site
    with_key(panel, pem)
    kali = pueblo.town.agents["kali"]
    kali.run_if_due(time.time())
    bot = pueblo.world.bots["kali"]
    assert "pausa" in bot["status"].lower()  # el bot está parado: Kali descansa
    panel.controller.start("sim")
    assert panel.wait(lambda: panel.controller.status()["bot"]["ticks"] > 0)
    kali.next_run = 0
    kali.run_if_due(time.time())
    assert "Trabajando" in pueblo.world.bots["kali"]["status"]


def test_local_panel_speaks_like_the_web_panel(site, pem):
    panel, _ = site
    with_key(panel, pem)
    local = LocalPanel(panel.controller)
    status = local.status()
    assert status["bot"]["state"] == "stopped" and isinstance(status["risk"]["max_market_pct"], str)
    assert local.positions() == []
    assert local.labels([]) == {}
    assert isinstance(local.logs(0), list)


def test_the_pueblo_can_be_turned_off(tmp_path, monkeypatch, caplog):
    from kalshi_bot import cli

    monkeypatch.setenv("PUEBLO_ENABLED", "0")
    assert cli._start_pueblo(object(), object()) is None
    monkeypatch.setenv("PUEBLO_ENABLED", "1")
    with caplog.at_level(logging.ERROR):
        assert cli._start_pueblo(object(), object()) is None  # si falla, el panel sigue sin pueblo
    assert "No se pudo abrir el pueblo" in caplog.text
