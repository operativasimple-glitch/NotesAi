from datetime import datetime, timezone

import pytest

from pueblo.sources.kalshi_public import KalshiPublic, price
from pueblo.sources.nws import CITIES, NWSClient, climate_day
from pueblo.sources.panel import PanelClient, PanelError


class Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        if self._data is None:
            raise ValueError("sin json")
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    """Contesta por orden lo que se le da y apunta cada petición."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def _next(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.answers.pop(0)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)


def test_the_panel_is_only_read_and_the_session_is_renewed():
    ok = Resp(200, {"ok": True})
    session = Session(ok, Resp(200, {"bot": {}}), Resp(401), ok, Resp(200, []))
    panel = PanelClient("https://panel.example/", "secreto", session=session)
    assert panel.status() == {"bot": {}}
    assert panel.positions() == []  # la sesión caducó: entra otra vez y repite
    methods = [(m, url.rsplit("/api/", 1)[1]) for m, url, _ in session.calls]
    login, positions = ("POST", "login"), ("GET", "positions")
    assert methods == [login, ("GET", "status"), positions, login, positions]
    assert all(kw["headers"] == {"X-Requested-With": "kalshi-bot"} for _, _, kw in session.calls)
    assert session.calls[0][2]["json"] == {"password": "secreto"}
    with pytest.raises(PanelError):
        panel.get("/api/orders")  # nada que pueda cambiar algo
    with pytest.raises(PanelError, match="PANEL_PASSWORD"):
        PanelClient("https://panel.example", "mal", session=Session(Resp(401))).status()


def test_forecast_highs_and_what_was_measured_today():
    ny = CITIES["NY"]
    periods = [
        {"isDaytime": True, "temperature": 74, "startTime": "2026-10-10T06:00:00-04:00"},
        {"isDaytime": False, "temperature": 61, "startTime": "2026-10-10T18:00:00-04:00"},
        {"isDaytime": True, "temperature": 70, "startTime": "2026-10-11T06:00:00-04:00"},
    ]
    obs = {
        "features": [
            {"properties": {"timestamp": "2026-10-10T15:51:00+00:00", "temperature": {"value": 22.8}}},
            {"properties": {"timestamp": "2026-10-10T18:51:00+00:00", "temperature": {"value": 23.3}}},
            {"properties": {"timestamp": "2026-10-10T19:51:00+00:00", "temperature": {"value": None}}},
            {"properties": {"timestamp": "2026-10-10T20:51:00+00:00", "temperature": {"value": 21.1}}},
        ]
    }
    session = Session(
        Resp(200, {"properties": {"forecast": "https://api.weather.gov/gridpoints/OKX/33,37/forecast"}}),
        Resp(200, {"properties": {"periods": periods}}),
        Resp(200, {"properties": {"periods": periods}}),
        Resp(200, obs),
    )
    nws = NWSClient("tu@correo", session=session)
    assert nws.forecast_highs(ny) == {"2026-10-10": 74, "2026-10-11": 70}
    assert nws.forecast_highs(ny) == {"2026-10-10": 74, "2026-10-11": 70}  # la dirección del punto se recuerda
    now = datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc)
    assert nws.observed_max(ny, now) == {
        "day": "2026-10-10",
        "max_f": 74,
        "last_f": 70,
        "last_at": "2026-10-10T20:51:00+00:00",
    }
    assert "pueblo-de-bots" in session.calls[0][2]["headers"]["User-Agent"]
    # El día climático va en hora estándar: a las 23:30 de verano en Nueva York aún es "hoy"...
    assert climate_day(ny, datetime(2026, 7, 11, 3, 30, tzinfo=timezone.utc))[0].isoformat() == "2026-07-10"
    # ...y a las 00:30 de verano también (son las 23:30 en hora estándar).
    assert climate_day(ny, datetime(2026, 7, 11, 4, 30, tzinfo=timezone.utc))[0].isoformat() == "2026-07-10"


def test_public_markets_follow_the_pages():
    session = Session(
        Resp(200, {"markets": [{"ticker": "A"}], "cursor": "c1"}),
        Resp(200, {"markets": [{"ticker": "B"}]}),
    )
    kalshi = KalshiPublic("https://api.example/v2", session=session, pause=0)
    assert [m["ticker"] for m in kalshi.markets_closing(12, now=1000)] == ["A", "B"]
    first, second = session.calls
    assert first[2]["params"]["max_close_ts"] == 1000 + 12 * 3600 and second[2]["params"]["cursor"] == "c1"
    assert price({"yes_bid_dollars": "0.9100"}, "yes_bid") == price({"yes_bid": 91}, "yes_bid")
    assert price({"yes_bid": 0}, "yes_bid") is None
