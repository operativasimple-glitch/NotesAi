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
import time
from pathlib import Path
from typing import Optional, Union

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


class KalshiSigner:
    def __init__(self, key_id: str, private_key_pem: Union[str, bytes]):
        if not key_id:
            raise ValueError("Falta el ID de la API key (KALSHI_API_KEY_ID)")
        pem = private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem
        try:
            key = serialization.load_pem_private_key(pem, password=None)
        except ValueError as exc:
            raise ValueError(
                "No se pudo leer la clave privada. Debe ser el archivo .pem que "
                "descargaste de Kalshi al crear la API key."
            ) from exc
        if not isinstance(key, (RSAPrivateKey, Ed25519PrivateKey)):
            raise ValueError("La clave privada debe ser RSA o Ed25519")
        self.key_id = key_id
        self._key = key

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
