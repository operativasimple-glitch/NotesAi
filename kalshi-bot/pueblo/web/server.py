"""Servidor web del pueblo (http.server de la biblioteca estándar, sin dependencias).

Seguridad, igual que en el panel del bot:
  - /api/* (salvo /api/login) pide sesión: cookie HttpOnly y SameSite=Strict firmada con HMAC.
  - Lo que cambia algo lleva la cabecera X-Requested-With: pueblo (CSRF).
  - Tras 5 contraseñas mal, el login se bloquea un minuto.
  - CSP estricta: la página solo carga sus propios archivos.
"""

from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import secrets
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
COOKIE = "pueblo_session"
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "pueblo"
CSRF_VALUES = (CSRF_VALUE, "kalshi-bot")  # la web del pueblo manda la del panel del bot, donde vive
SESSION_SECONDS = 30 * 86400
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/sprites.js": "sprites.js",
    "/base3d.js": "base3d.js",
    "/vendor/three.module.min.js": "vendor/three.module.min.js",
    "/app.css": "app.css",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/icon-192.png": "icon-192.png",
    "/icon-512.png": "icon-512.png",
    "/fonts/pixelify-sans.woff2": "fonts/pixelify-sans.woff2",
    "/fonts/atkinson-400.woff2": "fonts/atkinson-400.woff2",
    "/fonts/atkinson-700.woff2": "fonts/atkinson-700.woff2",
}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; font-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


_GZIP: dict = {}


def _gzipped(file: Path, body: bytes) -> bytes:
    """Los ficheros de la web, comprimidos una sola vez (Three.js pasa de 670 KB a unos 170 KB)."""
    key = (str(file), len(body), file.stat().st_mtime_ns)
    hit = _GZIP.get(key)
    if hit is None:
        hit = _GZIP[key] = gzip.compress(body, compresslevel=9)
    return hit


def static_file(path: str, accept_gzip: bool = False):
    """(cuerpo, tipo, cabeceras) de un fichero de la web, o None si no existe.

    `path` es relativo a la web del pueblo ("/", "/app.js"...): sirve igual en su servidor que
    dentro del panel del bot, en /pueblo/.
    """
    name = STATIC_FILES.get(path)
    if name is None:
        return None
    file = STATIC / name
    try:
        body = file.read_bytes()
    except OSError:
        return None
    ctype = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
    if name.endswith(".js"):
        ctype = "text/javascript"  # los módulos (la base 3D) necesitan un tipo de JavaScript
    if name.endswith(".webmanifest"):
        ctype = "application/manifest+json"
    text = ctype.startswith("text/") or ctype in ("application/javascript", "application/manifest+json")
    if text:
        ctype += "; charset=utf-8"
    if name.startswith("fonts/"):
        cache = "public, max-age=31536000, immutable"
    elif name.startswith("vendor/"):
        cache = "public, max-age=604800"  # Three.js no cambia de un día para otro
    else:
        cache = "no-cache"
    headers = {"Cache-Control": cache}
    if text and len(body) > 1024:
        headers["Vary"] = "Accept-Encoding"
        if accept_gzip:
            body = _gzipped(file, body)
            headers["Content-Encoding"] = "gzip"
    return body, ctype, headers


class Sessions:
    def __init__(self, password: str):
        self.secret = hashlib.sha256(("pueblo:" + password).encode()).digest()
        self.password = password
        self.failures: list = []
        self.lock = threading.Lock()

    def issue(self) -> str:
        expires = str(int(time.time()) + SESSION_SECONDS)
        nonce = secrets.token_hex(8)
        payload = f"{expires}.{nonce}"
        sig = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{sig}"

    def valid(self, token: str) -> bool:
        try:
            expires, nonce, sig = token.split(".")
        except ValueError:
            return False
        good = hmac.new(self.secret, f"{expires}.{nonce}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(good, sig) and expires.isdigit() and int(expires) > time.time()

    def check(self, password: str) -> bool:
        return hmac.compare_digest(password.encode(), self.password.encode())

    def locked_for(self) -> float:
        with self.lock:
            now = time.time()
            self.failures = [t for t in self.failures if now - t < 60]
            return 60 - (now - self.failures[0]) if len(self.failures) >= 5 else 0.0

    def fail(self) -> None:
        with self.lock:
            self.failures.append(time.time())


class Handler(BaseHTTPRequestHandler):
    server_version = "Pueblo"
    app = None  # se rellena en make_server

    def log_message(self, fmt, *args):  # sin ruido en el log por cada petición
        log.debug("%s - %s", self.address_string(), fmt % args)

    # --- respuestas ---------------------------------------------------------------------

    def _send(self, status: int, body: bytes, ctype: str, extra: dict = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, data, extra: dict = None) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        headers = {"Cache-Control": "no-store", "Vary": "Accept-Encoding", **(extra or {})}
        if len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body, compresslevel=6)
            headers["Content-Encoding"] = "gzip"
        self._send(status, body, "application/json; charset=utf-8", headers)

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _cookie(self, value: str, max_age: int) -> str:
        https = self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https"
        return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}" + ("; Secure" if https else "")

    def _session(self) -> bool:
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except Exception:  # noqa: BLE001
            return False
        morsel = jar.get(COOKIE)
        return bool(morsel) and self.app["sessions"].valid(morsel.value)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 64 * 1024:
            raise ValueError("demasiado grande")
        raw = self.rfile.read(length) if length else b""
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}

    # --- rutas ------------------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/healthz":
            self._send(200, b"ok", "text/plain")
            return
        if not url.path.startswith("/api/"):
            self._static(url.path)
            return
        if not self._session():
            self._error(401, "Inicia sesión")
            return
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/world":
            try:
                after = int(query.get("after", "0") or 0)
            except ValueError:
                after = 0
            try:
                version = int(query["v"]) if query.get("v") else None
            except ValueError:
                version = None
            self._json(200, self.app["world"].snapshot(after, version))
            return
        self._error(404, "No existe")

    def do_POST(self):
        url = urlparse(self.path)
        if self.headers.get(CSRF_HEADER) not in CSRF_VALUES:
            self._error(403, "Falta la cabecera de seguridad")
            return
        try:
            body = self._body()
        except ValueError:
            self._error(400, "Petición no válida")
            return
        if url.path == "/api/login":
            self._login(body)
            return
        if url.path == "/api/logout":
            self._json(200, {"ok": True}, {"Set-Cookie": self._cookie("", 0)})
            return
        if not self._session():
            self._error(401, "Inicia sesión")
            return
        if url.path == "/api/talk":
            bot = str(body.get("bot") or "")
            if not re.fullmatch(r"[a-z]{2,20}", bot):
                self._error(400, "Bot no válido")
                return
            try:
                message = self.app["town"].talk(bot)
            except KeyError:
                self._error(404, "Ese bot no vive aquí")
                return
            self._json(200, message)
            return
        self._error(404, "No existe")

    def _login(self, body: dict) -> None:
        sessions = self.app["sessions"]
        wait = sessions.locked_for()
        if wait > 0:
            self._error(429, f"Demasiados intentos. Espera {int(wait) + 1} segundos.")
            return
        if not sessions.check(str(body.get("password") or "")):
            sessions.fail()
            time.sleep(0.4)
            self._error(401, "Contraseña incorrecta")
            return
        self._json(200, {"ok": True}, {"Set-Cookie": self._cookie(sessions.issue(), SESSION_SECONDS)})

    def _static(self, path: str) -> None:
        found = static_file(path, "gzip" in self.headers.get("Accept-Encoding", ""))
        if found is None:
            self._send(404, b"No encontrado", "text/plain; charset=utf-8")
            return
        body, ctype, headers = found
        self._send(200, body, ctype, headers)


def make_server(world, town, password: str, host: str = "127.0.0.1", port: int = 8090) -> ThreadingHTTPServer:
    handler = type("PuebloHandler", (Handler,), {})
    handler.app = {"world": world, "town": town, "sessions": Sessions(password)}
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
