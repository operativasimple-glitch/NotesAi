from decimal import Decimal as D

import pytest
import requests

from kalshi_bot.client import KalshiAPIError, KalshiClient, RateLimiter
from kalshi_bot.models import ASK, BID, GTC, IOC, OrderIntent

from .fakes import FakeResponse, FakeSession, market_payload

BASE = "https://external-api.demo.kalshi.co/trade-api/v2"


class RecordingSigner:
    def __init__(self):
        self.calls = []

    def headers(self, method, path):
        self.calls.append((method, path))
        return {"KALSHI-ACCESS-KEY": "k", "KALSHI-ACCESS-TIMESTAMP": "1", "KALSHI-ACCESS-SIGNATURE": "s"}


def make_client(responses, signer=None):
    session = FakeSession(responses)
    sleeps = []
    client = KalshiClient(BASE, signer, session=session, sleep=sleeps.append, reads_per_second=0, writes_per_second=0)
    return client, session, sleeps


def test_public_markets_without_credentials_and_pagination():
    client, session, _ = make_client(
        [
            FakeResponse(200, {"markets": [market_payload("A")], "cursor": "next"}),
            FakeResponse(200, {"markets": [market_payload("B")], "cursor": ""}),
        ]
    )
    markets = client.get_markets(series_ticker="KXTEST")
    assert [m.ticker for m in markets] == ["A", "B"]
    first, second = session.calls
    assert first["url"] == BASE + "/markets"
    assert first["params"] == {"status": "open", "series_ticker": "KXTEST", "limit": 200}
    assert second["params"]["cursor"] == "next"
    assert "KALSHI-ACCESS-KEY" not in first["headers"]


def test_private_endpoint_requires_credentials():
    client, session, _ = make_client([])
    with pytest.raises(KalshiAPIError) as err:
        client.get_balance()
    assert err.value.code == "missing_credentials"
    assert session.calls == []


def test_signs_full_path_without_query_string():
    signer = RecordingSigner()
    client, session, _ = make_client([FakeResponse(200, {"orders": [], "cursor": ""})], signer)
    client.get_orders(status="resting")
    assert signer.calls == [("GET", "/trade-api/v2/portfolio/orders")]
    assert session.calls[0]["params"] == {"status": "resting", "limit": 200}
    assert session.calls[0]["headers"]["KALSHI-ACCESS-KEY"] == "k"


def test_create_order_uses_v2_endpoint_and_dollar_strings():
    signer = RecordingSigner()
    client, session, _ = make_client(
        [FakeResponse(201, {"order_id": "o1", "fill_count": "0.00", "remaining_count": "3.00", "ts_ms": 1})],
        signer,
    )
    intent = OrderIntent("KXTEST-1", BID, D("0.54"), D("3"), GTC, post_only=True)
    resp = client.create_order(intent, "kb-123", expiration_ts=1800000000)
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == BASE + "/portfolio/events/orders"
    assert call["json"] == {
        "ticker": "KXTEST-1",
        "client_order_id": "kb-123",
        "side": "bid",
        "count": "3.00",
        "price": "0.5400",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "cancel_order_on_pause": True,
        "post_only": True,
        "expiration_time": 1800000000,
    }
    assert signer.calls == [("POST", "/trade-api/v2/portfolio/events/orders")]
    assert resp["order_id"] == "o1"


def test_ioc_order_has_no_expiration_or_post_only():
    client, session, _ = make_client([FakeResponse(201, {"order_id": "o1"})], RecordingSigner())
    client.create_order(OrderIntent("T", ASK, D("0.36"), D("2"), IOC), "kb-1", expiration_ts=1800000000)
    body = session.calls[0]["json"]
    assert body["side"] == "ask" and body["time_in_force"] == "immediate_or_cancel"
    assert "expiration_time" not in body and "post_only" not in body


def test_cancel_order_and_cancel_all():
    signer = RecordingSigner()
    client, session, _ = make_client([FakeResponse(200, {"order_id": "o1"}), FakeResponse(200, None)], signer)
    client.cancel_order("o1", "KXTEST-1")
    client.cancel_all_orders()
    assert session.calls[0]["method"] == "DELETE"
    assert session.calls[0]["url"] == BASE + "/portfolio/events/orders/o1"
    assert session.calls[0]["params"] == {"market_ticker": "KXTEST-1"}
    assert session.calls[1]["url"] == BASE + "/portfolio/events/orders"


def test_get_retries_on_5xx_and_honours_retry_after():
    client, session, sleeps = make_client(
        [
            FakeResponse(503, {"error": {"message": "busy"}}),
            FakeResponse(429, {}, headers={"Retry-After": "2"}),
            FakeResponse(200, {"exchange_active": True, "trading_active": True}),
        ]
    )
    assert client.get_exchange_status()["trading_active"] is True
    assert len(session.calls) == 3
    assert sleeps[1] == 2.0


def test_post_is_not_retried_on_server_error_but_is_on_429():
    client, session, _ = make_client([FakeResponse(500, {"error": {"message": "oops"}})], RecordingSigner())
    with pytest.raises(KalshiAPIError) as err:
        client.create_order(OrderIntent("T", BID, D("0.5"), D("1")), "kb-1")
    assert err.value.status == 500 and len(session.calls) == 1

    client, session, _ = make_client([FakeResponse(429, {}), FakeResponse(201, {"order_id": "o9"})], RecordingSigner())
    assert client.create_order(OrderIntent("T", BID, D("0.5"), D("1")), "kb-1")["order_id"] == "o9"


def test_network_errors():
    client, session, _ = make_client([requests.ConnectionError("caída"), FakeResponse(200, {"trading_active": True})])
    assert client.get_exchange_status()["trading_active"] is True

    client, session, _ = make_client([requests.Timeout("lento")], RecordingSigner())
    with pytest.raises(KalshiAPIError) as err:
        client.create_order(OrderIntent("T", BID, D("0.5"), D("1")), "kb-1")
    assert err.value.code == "network_error" and len(session.calls) == 1


def test_error_parsing():
    client, _, _ = make_client(
        [FakeResponse(400, {"error": {"code": "invalid_price", "message": "bad tick", "details": "0.555"}})],
        RecordingSigner(),
    )
    with pytest.raises(KalshiAPIError) as err:
        client.get_balance()
    exc = err.value
    assert (exc.status, exc.code, exc.message, exc.details) == (400, "invalid_price", "bad tick", "0.555")
    assert "HTTP 400" in str(exc)

    client, _, _ = make_client([FakeResponse(401, {"code": "unauthorized", "message": "nope"})], RecordingSigner())
    with pytest.raises(KalshiAPIError) as err:
        client.get_balance()
    assert err.value.is_auth_error and err.value.code == "unauthorized"


def test_portfolio_parsing():
    client, _, _ = make_client(
        [
            FakeResponse(200, {"balance": 10000, "balance_dollars": "100.0000", "portfolio_value": 2500}),
            FakeResponse(
                200,
                {
                    "market_positions": [
                        {"ticker": "A", "position_fp": "3.00", "market_exposure_dollars": "1.50"},
                        {"ticker": "B", "position_fp": "0.00", "market_exposure_dollars": "0"},
                    ],
                    "event_positions": [],
                    "cursor": "",
                },
            ),
        ],
        RecordingSigner(),
    )
    balance = client.get_balance()
    assert balance.cash == D("100") and balance.equity == D("125")
    positions = client.get_positions()
    assert list(positions) == ["A"] and positions["A"].position == D("3")


def test_orderbook_request():
    client, session, _ = make_client(
        [FakeResponse(200, {"orderbook_fp": {"yes_dollars": [["0.4000", "5.00"]], "no_dollars": [["0.5500", "2.00"]]}})]
    )
    book = client.get_orderbook("KXTEST-1", depth=5)
    assert session.calls[0]["url"] == BASE + "/markets/KXTEST-1/orderbook"
    assert session.calls[0]["params"] == {"depth": 5}
    assert (book.best_bid, book.best_ask) == (D("0.40"), D("0.45"))


def test_rate_limiter_spaces_requests():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 6))
        now[0] += seconds

    limiter = RateLimiter(4, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [0.25, 0.25]


def test_public_data_is_signed_with_a_key_and_unsigned_if_kalshi_rejects_it():
    # Con API key, Kalshi cuenta las peticiones contra la cuenta y no contra la IP del servidor.
    book = {"orderbook_fp": {"yes_dollars": [], "no_dollars": []}}
    signer = RecordingSigner()
    client, session, _ = make_client([FakeResponse(200, book)], signer)
    client.get_orderbook("KXTEST-1")
    assert signer.calls == [("GET", "/trade-api/v2/markets/KXTEST-1/orderbook")]
    assert session.calls[0]["headers"]["KALSHI-ACCESS-KEY"] == "k"

    # Si Kalshi no acepta la firma (key de otro entorno), se piden sin firmar y se queda así.
    client, session, _ = make_client(
        [FakeResponse(401, {"error": {"message": "bad sig"}}), FakeResponse(200, book), FakeResponse(200, book)],
        RecordingSigner(),
    )
    client.get_orderbook("KXTEST-1")
    client.get_orderbook("KXTEST-2")
    assert [("KALSHI-ACCESS-KEY" in c["headers"]) for c in session.calls] == [True, False, False]


def test_rate_limiter_slows_down_on_429_and_recovers():
    now = [0.0]
    limiter = RateLimiter(8, clock=lambda: now[0], sleep=lambda s: None, recover_after=60)
    assert limiter.throttle() == 4  # a la mitad
    assert limiter.throttle() is None  # el mismo aviso: no baja dos veces seguidas
    now[0] += 3
    assert limiter.throttle() == 2
    now[0] += 3
    assert limiter.throttle() == 1 and limiter.throttle() is None  # nunca por debajo de 1 por segundo
    assert limiter.interval == 1.0
    for _ in range(10):  # cada minuto sin 429 sube un 25 %, hasta el máximo
        now[0] += 61
        limiter.wait()
    assert limiter.rate == 8 and limiter.interval == 0.125
    unlimited = RateLimiter(0, clock=lambda: now[0], sleep=lambda s: None)
    assert unlimited.throttle() is None and unlimited.interval == 0


def test_a_429_is_retried_quietly_and_slows_the_client(caplog):
    session = FakeSession([FakeResponse(429, {}), FakeResponse(429, {}), FakeResponse(200, {"trading_active": True})])
    client = KalshiClient(BASE, session=session, sleep=lambda s: None, clock=lambda: 100.0, reads_per_second=8)
    with caplog.at_level("DEBUG", logger="kalshi_bot.client"):
        assert client.get_exchange_status()["trading_active"] is True
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == ["Kalshi pide ir más despacio (HTTP 429): el bot baja a 4.0 peticiones por segundo"]
    assert client._read_limiter.rate == 4


def test_clients_in_one_process_share_the_limit():
    first = KalshiClient(BASE, reads_per_second=0, writes_per_second=0)
    second = KalshiClient(BASE, reads_per_second=0, writes_per_second=0)
    assert first._read_limiter is second._read_limiter and first._write_limiter is second._write_limiter
    other = KalshiClient("https://external-api.kalshi.com/trade-api/v2", reads_per_second=0)
    assert other._read_limiter is not first._read_limiter  # cada servidor tiene su límite


def test_falls_back_to_the_other_official_host_on_connection_errors():
    from kalshi_bot import client as client_module

    alt = "https://demo-api.kalshi.co/trade-api/v2"
    client_module._working_url.clear()
    try:
        session = FakeSession([requests.ConnectionError("dns"), FakeResponse(200, {"balance": 1})])
        signer = RecordingSigner()
        client = KalshiClient(
            BASE, signer, session=session, sleep=lambda s: None, reads_per_second=0, fallback_urls=[alt]
        )
        assert client.request("GET", "/portfolio/balance") == {"balance": 1}
        assert [c["url"] for c in session.calls] == [BASE + "/portfolio/balance", alt + "/portfolio/balance"]
        assert signer.calls[-1] == ("GET", "/trade-api/v2/portfolio/balance")  # misma ruta firmada

        # Los clientes nuevos empiezan directamente por la dirección que funcionó...
        session = FakeSession([FakeResponse(200, {})])
        KalshiClient(BASE, session=session, reads_per_second=0, fallback_urls=[alt]).get_exchange_status()
        assert session.calls[0]["url"].startswith(alt)
        # ...salvo los que no la tienen entre sus opciones (p. ej. con KALSHI_BASE_URL).
        session = FakeSession([FakeResponse(200, {})])
        KalshiClient(BASE, session=session, reads_per_second=0).get_exchange_status()
        assert session.calls[0]["url"].startswith(BASE)

        # Un POST que falla por red no se repite en la otra dirección: la orden pudo crearse.
        session = FakeSession([requests.ConnectionError("caída")])
        client = KalshiClient(BASE, signer, session=session, writes_per_second=0, fallback_urls=[alt])
        with pytest.raises(KalshiAPIError):
            client.create_order(OrderIntent("T", BID, D("0.5"), D("1")), "kb-1")
        assert len(session.calls) == 1
    finally:
        client_module._working_url.clear()


def test_series_list_is_public_and_filtered_by_category():
    client, session, _ = make_client([FakeResponse(200, {"series": [{"ticker": "KXINX", "volume_fp": "10.00"}]})])
    assert client.get_series_list(category="Financials") == [{"ticker": "KXINX", "volume_fp": "10.00"}]
    call = session.calls[0]
    assert call["url"] == BASE + "/series" and call["params"] == {"category": "Financials", "include_volume": "true"}
    assert "KALSHI-ACCESS-KEY" not in call["headers"]
