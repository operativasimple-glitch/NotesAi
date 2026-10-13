import gzip
import json
import threading
import urllib.error
import urllib.request

import pytest

from pueblo.agents.base import Agent
from pueblo.runtime import Town
from pueblo.web.server import make_server
from pueblo.world import World

PASSWORD = "contraseña-de-prueba"


class Echo(Agent):
    id = "eco"
    name = "Eco"
    role = "Repite"
    home = "plaza"

    def tick(self, now):
        self.status("Escuchando")

    def talk(self):
        return "¡Hola!"


@pytest.fixture
def town():
    world = World(None)
    agents = [Echo(world)]
    town = Town(world, agents)
    town.step()
    server = make_server(world, town, PASSWORD, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", world
    server.shutdown()
    server.server_close()


def call(base, method, path, body=None, cookie=None, csrf=True):
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-Requested-With"] = "pueblo"
    if cookie:
        headers["Cookie"] = cookie
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw), resp.headers
    except urllib.error.HTTPError as err:
        raw = err.read()
        return err.code, (json.loads(raw) if raw[:1] == b"{" else raw), err.headers


def test_the_town_needs_a_password_and_shows_its_bots(town):
    base, world = town
    status, body, headers = call(base, "GET", "/")
    assert status == 200 and b"Pueblo de bots" in body
    assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert call(base, "GET", "/api/world")[0] == 401
    assert call(base, "POST", "/api/login", {"password": PASSWORD}, csrf=False)[0] == 403
    assert call(base, "POST", "/api/login", {"password": "mal"})[0] == 401
    status, _, headers = call(base, "POST", "/api/login", {"password": PASSWORD})
    cookie = headers["Set-Cookie"].split(";")[0]
    assert status == 200 and "HttpOnly" in headers["Set-Cookie"]

    status, snap, _ = call(base, "GET", "/api/world", cookie=cookie)
    assert status == 200 and snap["bots"][0]["name"] == "Eco" and snap["bots"][0]["status"] == "Escuchando"
    status, reply, _ = call(base, "POST", "/api/talk", {"bot": "eco"}, cookie=cookie)
    assert status == 200 and reply["text"] == "¡Hola!" and reply["to"] == "tu"
    assert call(base, "POST", "/api/talk", {"bot": "nadie"}, cookie=cookie)[0] == 404
    assert call(base, "POST", "/api/talk", {"bot": "../x"}, cookie=cookie)[0] == 400
    status, snap, _ = call(base, "GET", f"/api/world?after={reply['id'] - 1}", cookie=cookie)
    assert [m["text"] for m in snap["messages"]] == ["¡Hola!"]
    assert call(base, "GET", "/../pueblo/config.py")[0] == 404


def test_login_locks_after_five_wrong_passwords(town, monkeypatch):
    base, _ = town
    monkeypatch.setattr("pueblo.web.server.time.sleep", lambda s: None)
    for _ in range(5):
        assert call(base, "POST", "/api/login", {"password": "mal"})[0] == 401
    assert call(base, "POST", "/api/login", {"password": PASSWORD})[0] == 429


def test_the_3d_base_is_served_compressed(town):
    base, _ = town
    for path in ("/base3d.js", "/vendor/three.module.min.js"):
        req = urllib.request.Request(base + path, headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/javascript")
            assert resp.headers["Content-Encoding"] == "gzip"
            body = gzip.decompress(resp.read())
        assert b"export" in body
    with urllib.request.urlopen(base + "/base3d.js") as resp:  # sin gzip también vale
        assert resp.headers.get("Content-Encoding") is None and b"class Base" in resp.read()
