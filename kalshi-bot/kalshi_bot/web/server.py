"""Servidor del panel web (sin dependencias: http.server de la biblioteca estándar).

Seguridad:
  - Todo /api/* (salvo /api/login) exige una sesión: cookie HttpOnly y
    SameSite=Strict firmada con HMAC a partir de la contraseña del panel.
  - Las peticiones que cambian algo deben llevar la cabecera
    X-Requested-With: kalshi-bot. Otra web no puede añadirla sin CORS, así que
    no puede hacer peticiones en tu nombre (CSRF).
  - Tras varios intentos fallidos de contraseña, el login se bloquea un minuto.
  - Cabeceras CSP estrictas: el panel solo carga sus propios archivos.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from datetime import date, datetime
from decimal import Decimal
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from ..client import KalshiAPIError
from ..config import ConfigError
from ..controller import BotController, ControllerError

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-192.png": ("icon-192.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/favicon.ico": ("icon-192.png", "image/png"),
}
COOKIE_NAME = "kb_session"
SESSION_SECONDS = 30 * 24 * 3600
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "kalshi-bot"
MAX_BODY = 256 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
}


def to_json(data) -> bytes:
    def default(value):
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, (set, tuple)):
            return list(value)
        return str(value)

    return json.dumps(data, default=default, ensure_ascii=False).encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Sessions:
    """Tokens de sesión firmados (sin estado en el servidor)."""

    def __init__(self, password: str, clock: Callable[[], float] = time.time):
        self._password = password.encode("utf-8")
        self._secret = hashlib.sha256(b"kalshi-bot-session:" + self._password).digest()
        self._clock = clock
        self._failures: list = []
        self._lock = threading.Lock()

    def check_password(self, candidate: str) -> bool:
        return hmac.compare_digest(
            hashlib.sha256(candidate.encode("utf-8")).digest(), hashlib.sha256(self._password).digest()
        )

    def locked_for(self) -> float:
        """Segundos de bloqueo restantes por intentos fallidos."""
        now = self._clock()
        with self._lock:
            self._failures = [t for t in self._failures if now - t < 300]
            if len(self._failures) >= 5:
                return max(0.0, 60 - (now - self._failures[-1]))
        return 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures.append(self._clock())

    def issue(self) -> str:
        payload = _b64(json.dumps({"exp": int(self._clock()) + SESSION_SECONDS, "n": secrets.token_hex(8)}).encode())
        signature = _b64(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}"

    def valid(self, token: Optional[str]) -> bool:
        if not token or "." not in token:
            return False
        payload, signature = token.rsplit(".", 1)
        expected = _b64(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return False
        try:
            data = json.loads(_unb64(payload))
        except ValueError:
            return False
        return isinstance(data, dict) and int(data.get("exp", 0)) > self._clock()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class PanelApp:
    """Rutas del API. Cada manejador recibe (query, body) y devuelve datos JSON."""

    def __init__(self, controller: BotController, password: str):
        self.controller = controller
        self.sessions = Sessions(password)
        c = controller
        self.routes = [
            ("GET", r"/api/status", lambda q, b: c.status()),
            ("POST", r"/api/bot/start", self._start),
            ("POST", r"/api/bot/stop", lambda q, b: (c.stop(), c.status())[1]),
            ("POST", r"/api/bot/kill", lambda q, b: c.kill(everything=bool(b.get("everything")))),
            ("GET", r"/api/logs", lambda q, b: c.logs.since(int(q.get("after", "0") or 0))),
            ("GET", r"/api/positions", lambda q, b: c.positions()),
            ("GET", r"/api/orders", lambda q, b: c.orders()),
            (
                "GET",
                r"/api/results",
                lambda q, b: c.results(q.get("days") or 30, q.get("tz") or 0, q.get("scope") or "bot"),
            ),
            ("POST", r"/api/orders", self._place_order),
            ("POST", r"/api/orders/cancel", lambda q, b: c.cancel_order(str(b.get("order_id")), b.get("ticker"))),
            ("GET", r"/api/settings", lambda q, b: c.settings_payload()),
            ("PUT", r"/api/settings", self._save_settings),
            ("POST", r"/api/credentials", self._save_credentials),
            ("DELETE", r"/api/credentials", lambda q, b: c.delete_credentials()),
            ("POST", r"/api/env", lambda q, b: c.set_env(str(b.get("env", "")))),
            ("POST", r"/api/diagnose", self._diagnose),
            ("GET", r"/api/fair-values", lambda q, b: c.fair_values()),
            ("PUT", r"/api/fair-values", lambda q, b: c.save_fair_values(b.get("rows"))),
            ("GET", r"/api/events", lambda q, b: c.events(q.get("series"))),
            ("GET", r"/api/markets", lambda q, b: c.markets(q.get("series"), q.get("event"))),
            ("GET", r"/api/market", lambda q, b: c.market_detail(self._required(q, "ticker"))),
            ("POST", r"/api/markets/follow", lambda q, b: c.add_ticker(self._required(b, "ticker"))),
            ("POST", r"/api/scan", lambda q, b: c.start_scan(b)),
            ("GET", r"/api/scan", lambda q, b: c.jobs["scan"].to_dict()),
            ("POST", r"/api/research", lambda q, b: c.start_research(b)),
            ("GET", r"/api/research", lambda q, b: c.jobs["research"].to_dict()),
            ("POST", r"/api/research/cancel", self._cancel_research),
            ("POST", r"/api/sweep", lambda q, b: c.start_sweep(b)),
            ("GET", r"/api/sweep", lambda q, b: c.jobs["sweep"].to_dict()),
            ("POST", r"/api/markets/use-series", lambda q, b: c.use_series(b.get("series") or [])),
        ]

    @staticmethod
    def _required(data: dict, key: str) -> str:
        value = str(data.get(key) or "").strip()
        if not value:
            raise ApiError(400, f"Falta '{key}'")
        return value

    def _start(self, query: dict, body: dict):
        mode = body.get("mode")
        settings = self.controller.settings()
        if mode == "live" and settings.is_production and body.get("confirm") != "REAL":
            raise ApiError(400, "Para operar con dinero real escribe REAL en la confirmación")
        self.controller.start(mode)
        return self.controller.status()

    def _place_order(self, query: dict, body: dict):
        settings = self.controller.settings()
        if settings.is_production and body.get("confirm") is not True:
            raise ApiError(400, "Confirma la orden: es dinero real")
        return self.controller.place_manual_order(
            self._required(body, "ticker"),
            str(body.get("outcome", "")),
            body.get("price"),
            body.get("count"),
            immediate=bool(body.get("immediate")),
        )

    def _save_settings(self, query: dict, body: dict):
        self.controller.save_settings(body.get("values") or {})
        return self.controller.settings_payload()

    def _save_credentials(self, query: dict, body: dict):
        env = str(body.get("env") or "demo")
        return self.controller.save_credentials(str(body.get("key_id") or ""), str(body.get("private_key") or ""), env)

    def _diagnose(self, query: dict, body: dict):
        order_test = body.get("order_test") is True
        if order_test and self.controller.settings().is_production and body.get("confirm") is not True:
            raise ApiError(400, "Confirma la orden de prueba: es dinero real")
        return self.controller.diagnose(order_test=order_test)

    def _cancel_research(self, query: dict, body: dict):
        self.controller.jobs["research"].cancel = True
        return self.controller.jobs["research"].to_dict()

    def dispatch(self, method: str, path: str, query: dict, body: dict):
        matched_path = False
        for route_method, pattern, handler in self.routes:
            if re.fullmatch(pattern, path):
                matched_path = True
                if route_method == method:
                    return handler(query, body)
        raise ApiError(
            405 if matched_path else 404, "Ruta no encontrada" if not matched_path else "Método no permitido"
        )


class Handler(BaseHTTPRequestHandler):
    server_version = "KalshiBotPanel/1.0"
    sys_version = ""

    @property
    def app(self) -> PanelApp:
        return self.server.app  # type: ignore[attr-defined]

    # --- utilidades ----------------------------------------------------------------

    def log_message(self, format, *args):  # noqa: A002 - firma de la clase base
        log.debug("%s %s", self.address_string(), format % args)

    def _send(self, status: int, body: bytes, content_type: str, headers: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data, headers: Optional[dict] = None) -> None:
        self._send(
            status, to_json(data), "application/json; charset=utf-8", {"Cache-Control": "no-store", **(headers or {})}
        )

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _is_https(self) -> bool:
        return self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https"

    def _cookie(self, value: str, max_age: int) -> str:
        cookie = f"{COOKIE_NAME}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        return cookie + ("; Secure" if self._is_https() else "")

    def _session_token(self) -> Optional[str]:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - cookie malformada = sin sesión
            return None
        morsel = jar.get(COOKIE_NAME)
        return morsel.value if morsel else None

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(413, "Petición demasiado grande")
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "JSON no válido") from None
        if not isinstance(data, dict):
            raise ApiError(400, "Se esperaba un objeto JSON")
        return data

    # --- métodos HTTP ----------------------------------------------------------------

    def do_GET(self):
        self._handle("GET")

    def do_HEAD(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        path = url.path
        if path == "/healthz":
            self._send(200, b"ok", "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
            return
        if not path.startswith("/api/"):
            self._static(path)
            return
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if method != "GET" and self.headers.get(CSRF_HEADER) != CSRF_VALUE:
                raise ApiError(403, "Petición rechazada (falta la cabecera del panel)")
            if path == "/api/login" and method == "POST":
                self._login(self._read_body())
                return
            if path == "/api/logout" and method == "POST":
                self._json(200, {"ok": True}, {"Set-Cookie": self._cookie("", 0)})
                return
            if not self.app.sessions.valid(self._session_token()):
                raise ApiError(401, "Inicia sesión")
            body = self._read_body() if method in ("POST", "PUT", "DELETE") else {}
            self._json(200, self.app.dispatch(method, path, query, body))
        except ApiError as exc:
            self._error(exc.status, exc.message)
        except (ControllerError, ConfigError, ValueError) as exc:
            self._error(400, str(exc))
        except KalshiAPIError as exc:
            hint = " Revisa la API key y el entorno." if exc.is_auth_error else ""
            self._error(502, f"Kalshi respondió con un error: {exc.message or exc}{hint}")
        except Exception:  # noqa: BLE001
            log.exception("Error en %s %s", method, path)
            self._error(500, "Error interno del panel (mira el log del servidor)")

    def _login(self, body: dict) -> None:
        wait = self.app.sessions.locked_for()
        if wait > 0:
            raise ApiError(429, f"Demasiados intentos. Espera {int(wait) + 1} segundos.")
        if not self.app.sessions.check_password(str(body.get("password") or "")):
            self.app.sessions.record_failure()
            time.sleep(0.5)
            raise ApiError(401, "Contraseña incorrecta")
        token = self.app.sessions.issue()
        self._json(200, {"ok": True}, {"Set-Cookie": self._cookie(token, SESSION_SECONDS)})

    def _static(self, path: str) -> None:
        entry = STATIC_FILES.get(path)
        if entry is None:
            self._send(404, b"No encontrado", "text/plain; charset=utf-8")
            return
        name, content_type = entry
        try:
            body = (STATIC_DIR / name).read_bytes()
        except FileNotFoundError:
            self._send(404, b"No encontrado", "text/plain; charset=utf-8")
            return
        self._send(200, body, content_type, {"Cache-Control": "no-cache"})


class PanelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, app: PanelApp):
        super().__init__(address, Handler)
        self.app = app


def make_server(controller: BotController, password: str, host: str = "0.0.0.0", port: int = 8000) -> PanelServer:
    return PanelServer((host, port), PanelApp(controller, password))
