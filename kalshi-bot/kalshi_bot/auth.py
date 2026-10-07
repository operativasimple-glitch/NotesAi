"""Firma de peticiones para la API de Kalshi.

Cada petición autenticada lleva tres cabeceras:
  KALSHI-ACCESS-KEY        el ID de tu API key
  KALSHI-ACCESS-TIMESTAMP  milisegundos Unix
  KALSHI-ACCESS-SIGNATURE  firma base64 de: timestamp + MÉTODO + ruta (sin query)

Las claves RSA firman con RSA-PSS/SHA-256; Kalshi también acepta claves
Ed25519. El tipo se detecta automáticamente a partir del PEM.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from pathlib import Path
from typing import Optional, Union

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


_PEM_BLOCK = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----(.*?)-----END \1-----", re.S)
_BAD_KEY = (
    "No se pudo leer la clave privada. Debe ser el archivo (o el texto completo, de -----BEGIN a "
    "-----END) que te dio Kalshi al crear la API key."
)


def load_private_key(data: Union[str, bytes]):
    """Lee la clave privada aunque llegue maltratada al copiarla desde el móvil:
    saltos de línea perdidos o cambiados por espacios, "\\n" literales, comillas
    o sin las líneas BEGIN/END."""
    raw = data.encode() if isinstance(data, str) else bytes(data)
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        text = raw.decode("utf-8", "replace").replace("\\n", "\n")
        match = _PEM_BLOCK.search(text)
        body = re.sub(r"[\s\"']", "", match.group(2) if match else text)
        try:
            key = serialization.load_der_private_key(base64.b64decode(body, validate=True), password=None)
        except (binascii.Error, ValueError, TypeError, UnsupportedAlgorithm):
            raise ValueError(_BAD_KEY) from None
    if not isinstance(key, (RSAPrivateKey, Ed25519PrivateKey)):
        raise ValueError("La clave privada debe ser RSA o Ed25519")
    return key


def canonical_pem(data: Union[str, bytes]) -> str:
    """La clave en PEM bien formado (PKCS#8), para guardarla en disco."""
    key = load_private_key(data)
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode("ascii")


class KalshiSigner:
    def __init__(self, key_id: str, private_key_pem: Union[str, bytes]):
        if not key_id:
            raise ValueError("Falta el ID de la API key (KALSHI_API_KEY_ID)")
        self.key_id = key_id
        self._key = load_private_key(private_key_pem)

    @classmethod
    def from_file(cls, key_id: str, path: Union[str, Path]) -> "KalshiSigner":
        return cls(key_id, Path(path).expanduser().read_bytes())

    def sign(self, message: bytes) -> bytes:
        if isinstance(self._key, Ed25519PrivateKey):
            return self._key.sign(message)
        return self._key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )

    def headers(self, method: str, path: str, timestamp_ms: Optional[int] = None) -> dict:
        """Cabeceras de autenticación. `path` es la ruta completa, p. ej.
        /trade-api/v2/portfolio/balance (la query string se ignora)."""
        ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
        message = (ts + method.upper() + path.split("?")[0]).encode("utf-8")
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(self.sign(message)).decode("ascii"),
        }
