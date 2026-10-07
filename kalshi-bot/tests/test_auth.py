import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from kalshi_bot.auth import KalshiSigner


def pem(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_rsa_headers_sign_timestamp_method_and_path_without_query(rsa_key):
    signer = KalshiSigner("key-123", pem(rsa_key))
    headers = signer.headers("get", "/trade-api/v2/portfolio/balance?limit=5", timestamp_ms=1700000000000)

    assert headers["KALSHI-ACCESS-KEY"] == "key-123"
    assert headers["KALSHI-ACCESS-TIMESTAMP"] == "1700000000000"
    signature = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = b"1700000000000GET/trade-api/v2/portfolio/balance"
    # RSA-PSS con SHA-256 y sal del tamaño del digest, como exige Kalshi.
    rsa_key.public_key().verify(
        signature,
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    with pytest.raises(InvalidSignature):
        rsa_key.public_key().verify(
            signature,
            b"1700000000000GET/trade-api/v2/portfolio/balance?limit=5",
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )


def test_ed25519_keys_are_supported():
    key = ed25519.Ed25519PrivateKey.generate()
    signer = KalshiSigner("key-ed", pem(key).decode())
    headers = signer.headers("POST", "/trade-api/v2/portfolio/events/orders", timestamp_ms=1)
    key.public_key().verify(
        base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), b"1POST/trade-api/v2/portfolio/events/orders"
    )


def test_from_file(tmp_path, rsa_key):
    path = tmp_path / "key.pem"
    path.write_bytes(pem(rsa_key))
    assert KalshiSigner.from_file("abc", path).key_id == "abc"


def test_invalid_key_and_missing_id():
    with pytest.raises(ValueError, match="clave privada"):
        KalshiSigner("abc", "no es un pem")
    with pytest.raises(ValueError, match="KALSHI_API_KEY_ID"):
        KalshiSigner("", "x")
