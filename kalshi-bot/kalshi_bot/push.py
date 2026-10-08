"""Avisos al móvil (Web Push).

Cada móvil que activa los avisos deja aquí su suscripción: la dirección del servicio de
avisos de su navegador (Apple, Google, Mozilla…) y dos claves. Para avisarle, el panel
cifra el mensaje con esas claves (RFC 8291) y lo firma con su propia clave VAPID
(RFC 8292); el servicio de avisos solo ve un mensaje cifrado y se lo entrega al móvil.

La clave VAPID y las suscripciones se guardan en el directorio de datos (/data en Railway).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

KEY_FILE = "push_vapid.pem"
SUBSCRIPTIONS_FILE = "push_subscriptions.json"
KINDS = ("fills", "settlements")  # al abrir una operación / al cerrarse un mercado
# El panel solo envía a los servicios de avisos de los navegadores, nunca a otra dirección.
PUSH_HOSTS = ("push.apple.com", "fcm.googleapis.com", "push.services.mozilla.com", "notify.windows.com")
RECORD_SIZE = 4096
MAX_PAYLOAD = 3000  # el servicio acepta 4096 bytes con la cabecera y el cifrado
MAX_SUBSCRIPTIONS = 20
TTL_SECONDS = 24 * 3600


class PushError(ValueError):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    text = str(text).strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(data)
    return mac.finalize()


def _point(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(
    payload: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    salt: Optional[bytes] = None,
    server_key: Optional[ec.EllipticCurvePrivateKey] = None,
) -> bytes:
    """Cifra un aviso para un navegador: RFC 8291 con aes128gcm (RFC 8188), un solo registro."""
    if len(payload) > MAX_PAYLOAD:
        raise PushError("Aviso demasiado largo")
    salt = salt or os.urandom(16)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    # HKDF en un solo paso (32 bytes o menos): extraer y expandir con HMAC-SHA-256.
    ikm = _hmac(_hmac(auth_secret, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02: último registro, sin relleno
    return salt + struct.pack("!I", RECORD_SIZE) + bytes([len(as_public)]) + as_public + ciphertext


def vapid_token(key: ec.EllipticCurvePrivateKey, endpoint: str, subject: str, now: float) -> str:
    """JWT ES256 que identifica al panel ante el servicio de avisos (RFC 8292)."""
    url = urlparse(endpoint)
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": f"{url.scheme}://{url.netloc}", "exp": int(now) + 3600, "sub": subject}
    signing_input = ".".join(b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims))
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing_input}.{b64url(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _http_post(url: str, data: bytes, headers: dict) -> int:
    return requests.post(url, data=data, headers=headers, timeout=10).status_code


class PushService:
    """Guarda las suscripciones y envía los avisos."""

    def __init__(
        self,
        data_dir: Path,
        *,
        post: Callable[[str, bytes, dict], int] = _http_post,
        clock: Callable[[], float] = time.time,
    ):
        self.data_dir = Path(data_dir)
        self._post = post
        self._clock = clock
        self._lock = threading.RLock()
        self._key: Optional[ec.EllipticCurvePrivateKey] = None

    # --- clave VAPID ------------------------------------------------------------------

    def _private_key(self) -> ec.EllipticCurvePrivateKey:
        with self._lock:
            if self._key is None:
                path = self.data_dir / KEY_FILE
                if path.exists():
                    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
                    if not isinstance(key, ec.EllipticCurvePrivateKey):
                        raise PushError(f"{KEY_FILE} no es una clave P-256")
                    self._key = key
                else:
                    self._key = ec.generate_private_key(ec.SECP256R1())
                    pem = self._key.private_bytes(
                        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                    )
                    self.data_dir.mkdir(parents=True, exist_ok=True)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(pem)
            return self._key

    def public_key(self) -> str:
        """La clave pública que el navegador necesita para suscribirse."""
        return b64url(_point(self._private_key().public_key()))

    # --- suscripciones ----------------------------------------------------------------

    def _load(self) -> list:
        try:
            data = json.loads((self.data_dir / SUBSCRIPTIONS_FILE).read_text("utf-8"))
        except (OSError, ValueError):
            return []
        return [s for s in data if isinstance(s, dict) and s.get("endpoint")] if isinstance(data, list) else []

    def _save(self, subscriptions: list) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / SUBSCRIPTIONS_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(subscriptions, indent=1), "utf-8")
        os.replace(tmp, path)

    def subscribe(self, subscription: Any, prefs: Any, origin: Any = "") -> dict:
        """Guarda (o actualiza) la suscripción de un móvil con los avisos que quiere."""
        if not isinstance(subscription, dict):
            raise PushError("Suscripción no válida")
        endpoint = str(subscription.get("endpoint") or "")
        url = urlparse(endpoint)
        host = (url.hostname or "").lower()
        if url.scheme != "https" or not any(host == h or host.endswith("." + h) for h in PUSH_HOSTS):
            raise PushError("Ese servicio de avisos no está permitido")
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        try:
            ua_public, auth = b64url_decode(keys.get("p256dh", "")), b64url_decode(keys.get("auth", ""))
        except (ValueError, TypeError) as exc:
            raise PushError("Claves de la suscripción no válidas") from exc
        if len(ua_public) != 65 or ua_public[0] != 4 or len(auth) < 16:
            raise PushError("Claves de la suscripción no válidas")
        wanted = {kind: bool(isinstance(prefs, dict) and prefs.get(kind)) for kind in KINDS}
        origin = str(origin or "")
        parsed = urlparse(origin)
        # El "sub" de la firma: la dirección del panel (https) o, si no la hay, un contacto genérico.
        subject = f"https://{parsed.netloc}" if parsed.scheme == "https" and parsed.netloc else ""
        with self._lock:
            subscriptions = [s for s in self._load() if s["endpoint"] != endpoint]
            if any(wanted.values()):
                subscriptions.append(
                    {
                        "endpoint": endpoint,
                        "keys": {"p256dh": b64url(ua_public), "auth": b64url(auth)},
                        "prefs": wanted,
                        "subject": subject,
                        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                )
            self._save(subscriptions[-MAX_SUBSCRIPTIONS:])
        log.info("Avisos del móvil %s", "activados" if any(wanted.values()) else "desactivados")
        return {"prefs": wanted}

    def unsubscribe(self, endpoint: Any) -> dict:
        with self._lock:
            self._save([s for s in self._load() if s["endpoint"] != str(endpoint or "")])
        return {"prefs": {kind: False for kind in KINDS}}

    def prefs(self, endpoint: Any) -> dict:
        """Los avisos que tiene activados un móvil (por su suscripción)."""
        for sub in self._load():
            if sub["endpoint"] == str(endpoint or ""):
                return {"prefs": {kind: bool(sub.get("prefs", {}).get(kind)) for kind in KINDS}}
        return {"prefs": {kind: False for kind in KINDS}}

    def wants(self, kind: str) -> bool:
        return any(sub.get("prefs", {}).get(kind) for sub in self._load())

    # --- envío ------------------------------------------------------------------------

    def notify(self, kind: str, title: str, body: str, *, tag: str = "", endpoint: str = "") -> int:
        """Envía un aviso a los móviles que lo quieren ("test": a todos, o solo a `endpoint`).

        Devuelve cuántos se entregaron. Las suscripciones caducadas se borran.
        """
        payload = json.dumps({"title": title, "body": body, "tag": tag}, ensure_ascii=False).encode("utf-8")
        sent, gone = 0, []
        for sub in self._load():
            if endpoint and sub["endpoint"] != endpoint:
                continue
            if kind != "test" and not sub.get("prefs", {}).get(kind):
                continue
            try:
                status = self.send(sub, payload)
            except Exception as exc:  # noqa: BLE001 - un móvil que falla no para a los demás
                log.warning("No se pudo enviar un aviso al móvil: %s", exc)
                continue
            if status in (404, 410):  # el móvil quitó el permiso o borró la app
                gone.append(sub["endpoint"])
            elif 200 <= status < 300:
                sent += 1
            else:
                log.warning("El servicio de avisos respondió %s", status)
        if gone:
            with self._lock:
                self._save([s for s in self._load() if s["endpoint"] not in gone])
        return sent

    def notify_async(self, kind: str, title: str, body: str, *, tag: str = "") -> None:
        """Como notify, sin esperar: el bot no se para por un aviso."""
        threading.Thread(
            target=self.notify, args=(kind, title, body), kwargs={"tag": tag}, name="push", daemon=True
        ).start()

    def send(self, sub: dict, payload: bytes) -> int:
        keys = sub["keys"]
        data = encrypt(payload, b64url_decode(keys["p256dh"]), b64url_decode(keys["auth"]))
        subject = sub.get("subject") or "mailto:avisos@kalshi-bot.invalid"
        token = vapid_token(self._private_key(), sub["endpoint"], subject, self._clock())
        headers = {
            "TTL": str(TTL_SECONDS),
            "Urgency": "normal",
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "Authorization": f"vapid t={token}, k={self.public_key()}",
        }
        return self._post(sub["endpoint"], data, headers)
