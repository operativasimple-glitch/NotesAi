import json
import struct
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kalshi_bot.controller import BotController
from kalshi_bot.models import Settlement
from kalshi_bot.names import cents, market_name, money, quantity
from kalshi_bot.push import PushError, PushService, _hmac, b64url, b64url_decode, encrypt, vapid_token

from .fakes import FakeKalshi, make_market

ENDPOINT = "https://web.push.apple.com/QGuQyavXutnMH"


def browser_keys():
    """Lo que genera el navegador al suscribirse: su par de claves y el secreto de 16 bytes."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return key, public, b"0123456789abcdef"


def decrypt(body: bytes, ua_key, ua_public: bytes, auth: bytes) -> bytes:
    """Descifra como lo haría el navegador (RFC 8291)."""
    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_public, ciphertext = body[21 : 21 + idlen], body[21 + idlen :]
    assert rs == 4096
    shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = _hmac(_hmac(auth, shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    record = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert record.endswith(b"\x02")
    return record[:-1]


def subscription(public: bytes, auth: bytes, endpoint=ENDPOINT):
    return {"endpoint": endpoint, "keys": {"p256dh": b64url(public), "auth": b64url(auth)}}


def test_encryption_matches_the_rfc_8291_example():
    def private(d):
        return ec.derive_private_key(int.from_bytes(b64url_decode(d), "big"), ec.SECP256R1())

    body = encrypt(
        b"When I grow up, I want to be a watermelon",
        b64url_decode("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
        b64url_decode("BTBZMqHH6r4Tts7J_aSIgg"),
        salt=b64url_decode("DGv6ra1nlYgDCS1FRnbzlw"),
        server_key=private("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
    )
    assert b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
        "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def test_encrypted_message_round_trips_and_vapid_token_verifies():
    ua_key, ua_public, auth = browser_keys()
    message = "Compra 5 NO a 94¢ · Máxima en Nueva York".encode()
    assert decrypt(encrypt(message, ua_public, auth), ua_key, ua_public, auth) == message
    with pytest.raises(PushError):
        encrypt(b"x" * 5000, ua_public, auth)

    vapid = ec.generate_private_key(ec.SECP256R1())
    token = vapid_token(vapid, ENDPOINT, "https://panel.example.com", now=1_000_000)
    header, claims, signature = token.split(".")
    assert json.loads(b64url_decode(header)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(b64url_decode(claims)) == {
        "aud": "https://web.push.apple.com",
        "exp": 1_003_600,
        "sub": "https://panel.example.com",
    }
    raw = b64url_decode(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    vapid.public_key().verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # no lanza


class Outbox:
    def __init__(self, status=201):
        self.status = status
        self.sent = []

    def __call__(self, url, data, headers):
        self.sent.append({"url": url, "data": data, "headers": headers})
        return self.status


def test_subscriptions_are_validated_saved_and_dropped_when_expired(tmp_path):
    outbox = Outbox()
    push = PushService(tmp_path, post=outbox, clock=lambda: 1_000_000)
    ua_key, ua_public, auth = browser_keys()
    sub = subscription(ua_public, auth)

    with pytest.raises(PushError):  # solo servicios de avisos de verdad: nada de URLs cualquiera
        push.subscribe(subscription(ua_public, auth, "https://evil.example.com/x"), {"fills": True})
    with pytest.raises(PushError):
        push.subscribe({"endpoint": ENDPOINT, "keys": {"p256dh": "abc", "auth": "def"}}, {"fills": True})

    assert push.subscribe(sub, {"fills": True}, "https://panel.example.com/") == {
        "prefs": {"fills": True, "settlements": False}
    }
    assert push.wants("fills") and not push.wants("settlements")
    assert push.prefs(ENDPOINT)["prefs"]["fills"] is True
    public = push.public_key()  # la clave del panel se crea la primera vez que hace falta
    key_file = tmp_path / "push_vapid.pem"
    assert key_file.exists() and (key_file.stat().st_mode & 0o777) == 0o600
    assert PushService(tmp_path).public_key() == public  # la misma clave tras reiniciar

    assert push.notify("settlements", "Ganado", "no lo quiere") == 0 and outbox.sent == []
    assert push.notify("fills", "Nueva operación del bot", "Compra 5 NO a 94¢", tag="fill-1") == 1
    sent = outbox.sent[-1]
    assert sent["url"] == ENDPOINT
    assert sent["headers"]["Content-Encoding"] == "aes128gcm" and sent["headers"]["TTL"] == "86400"
    assert sent["headers"]["Authorization"].endswith(f", k={public}")
    token = sent["headers"]["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    assert json.loads(b64url_decode(token.split(".")[1]))["sub"] == "https://panel.example.com"
    payload = json.loads(decrypt(sent["data"], ua_key, ua_public, auth))
    assert payload == {"title": "Nueva operación del bot", "body": "Compra 5 NO a 94¢", "tag": "fill-1"}

    outbox.status = 410  # el móvil quitó el permiso: se olvida su suscripción
    assert push.notify("test", "Kalshi Bot", "prueba") == 0
    assert not push.wants("fills") and push.prefs(ENDPOINT)["prefs"] == {"fills": False, "settlements": False}

    push.subscribe(sub, {"fills": True, "settlements": True})
    push.subscribe(sub, {})  # apagar los dos avisos es darse de baja
    assert json.loads((tmp_path / "push_subscriptions.json").read_text()) == []


def test_names_and_amounts_for_the_notifications():
    assert market_name("KXHIGHNY-26OCT08-B66.5", "Highest temperature in NYC?", "66° to 67°") == (
        "Máxima en Nueva York · 66° a 67°"
    )
    assert market_name("KXHIGHLAX-26OCT08-T82", "", "82° or below") == "Máxima en Los Ángeles · 82° o menos"
    assert market_name("KXNHLGAME-26OCT07PITNYR-PIT", "Pittsburgh vs NY Rangers Winner?", "Pittsburgh") == (
        "Gana Pittsburgh"
    )
    assert market_name("KXFED-26DEC-T4.00", "Fed rate?", "Above 4%") == "Fed rate? · Above 4%"
    assert market_name("KXFED-26DEC-T4.00") == "KXFED-26DEC-T4.00"
    assert (money(D("1.78"), sign=True), money(D("-4.65")), money(D("0"), sign=True)) == ("+$1,78", "−$4,65", "$0,00")
    assert (cents(D("0.94")), cents(D("0.935")), cents(D("0.9")), quantity(D("10.00"))) == ("94¢", "93,5¢", "90¢", "10")


def controller_with(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("KALSHI_BOT_DATA_DIR", str(tmp_path))
    for key in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY", "KALSHI_PRIVATE_KEY_PATH", "KALSHI_ENV"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    controller = BotController(None, client_factory=lambda settings, signer: fake)
    outbox = Outbox()
    controller.push = PushService(tmp_path, post=outbox)
    return controller, outbox


def test_fill_and_settlement_notifications(tmp_path, monkeypatch):
    t = "KXHIGHNY-26OCT08-B66.5"
    fake = FakeKalshi(markets=[make_market(t, title="Highest temperature in NYC?", yes_sub_title="66° to 67°")])
    controller, outbox = controller_with(tmp_path, monkeypatch, fake)
    ua_key, ua_public, auth = browser_keys()
    controller.push.subscribe(subscription(ua_public, auth), {"fills": True, "settlements": True})

    def last_message():
        return json.loads(decrypt(outbox.sent[-1]["data"], ua_key, ua_public, auth))

    # Una orden de 10 NO que se llena en dos veces: el segundo aviso lleva el total.
    fill = {"ticker": t, "book_side": "ask", "yes_price_dollars": "0.0600", "count_fp": "4.00", "order_id": "o1"}
    controller._notify_fill(fill)
    controller._notify_fill({**fill, "count_fp": "6.00"})
    message = last_message()
    assert message["title"] == "Nueva operación del bot" and message["tag"] == "fill-o1"
    assert message["body"] == "Compra 10 NO a 94¢ · Máxima en Nueva York · 66° a 67°"
    # Al cobrar antes, el bot vende: sus 10 NO a 99¢ (comprando SÍ a 1¢) o un SÍ a 99¢.
    sold_no = {"book_side": "bid", "yes_price_dollars": "0.0100", "count_fp": "10.00", "order_id": "o2"}
    controller._notify_fill({**fill, **sold_no, "closing": True})
    message = last_message()
    assert message["title"] == "Venta del bot" and message["tag"] == "fill-o2"
    assert message["body"] == "Vende 10 NO a 99¢ · Máxima en Nueva York · 66° a 67°"
    sold_yes = {"yes_price_dollars": "0.9900", "count_fp": "5.00", "order_id": "o3"}
    controller._notify_fill({**fill, **sold_yes, "closing": True})
    assert last_message()["body"] == "Vende 5 SÍ a 99¢ · Máxima en Nueva York · 66° a 67°"

    # Sin API key no se miran las liquidaciones.
    assert controller.check_settlements() == 0
    monkeypatch.setattr(type(controller.settings()), "signer", lambda self: object())
    sent = len(outbox.sent)
    assert controller.check_settlements() == 0 and len(outbox.sent) == sent  # la primera vez solo apunta la hora
    state = json.loads((tmp_path / "push_state.json").read_text())

    def settled(ticker, result, cost, revenue_cents, hours_ago=0.1, count="5.00"):
        return Settlement.from_api(
            {
                "ticker": ticker,
                "market_result": result,
                "no_count_fp": count,
                "no_total_cost_dollars": cost,
                "revenue": revenue_cents,
                "fee_cost": "0.02",
                "settled_time": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
            }
        )

    fake.settlements = [
        settled(t, "no", "4.70", 500),
        settled("KXHIGHCHI-OLD", "no", "4.65", 500, hours_ago=30),
        settled("KXHIGHMIA-SOLD", "no", "0", 0, count="0.00"),  # vendido antes: ya se avisó la venta
    ]
    state["since"] -= 600
    (tmp_path / "push_state.json").write_text(json.dumps(state))
    assert controller.check_settlements() == 1  # la de hace 30 h es de antes de activar los avisos
    message = last_message()
    assert message["title"] == "Mercado cerrado: ganado +$0,28"
    assert message["body"] == "Máxima en Nueva York · 66° a 67° · NO"
    assert controller.check_settlements() == 0  # no se repite
