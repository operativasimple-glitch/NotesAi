"""Fuentes de datos falsas para probar los bots sin red."""

from __future__ import annotations

from datetime import datetime, timezone


class Clock:
    def __init__(self, start: float = 1_791_500_000.0):  # 2026-10-08 ~23:33 UTC
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakePanel:
    def __init__(self):
        self.state = "running"
        self.balance = {"cash": "15.79", "portfolio_value": "14.25", "equity": "30.04"}
        self.markets = ["KXHIGHNY-26OCT08-B72.5", "KXNHLGAME-26OCT08PITCBJ-PIT"]
        self.position_rows = []
        self.result_rows = []
        self.today = {"net": "4.45", "markets": 20, "wins": 19, "losses": 1}
        self.log_lines = []
        self.labels_map = {}
        self.calls = []

    def status(self):
        self.calls.append("status")
        return {
            "bot": {
                "state": self.state,
                "mode": "live",
                "markets": self.markets,
                "halted_reason": None,
                "consecutive_errors": 0,
                "scan": None,
            },
            "balance": self.balance,
            "session_pnl": "-0.20",
            "risk": {"max_session_loss_dollars": "10"},
        }

    def positions(self):
        self.calls.append("positions")
        return self.position_rows

    def results(self, days=2, tz_minutes=0):
        self.calls.append("results")
        return {
            "today_totals": self.today,
            "yesterday_totals": {"net": "3.27", "markets": 15, "wins": 15, "losses": 0},
            "totals": {"net": "8.84", "markets": 35},
            "recent": self.result_rows,
            "curve": getattr(self, "curve_rows", []),
        }

    def logs(self, after=0):
        return [line for line in self.log_lines if line["id"] > after]

    def labels(self, tickers):
        return {t: self.labels_map[t] for t in tickers if t in self.labels_map}

    def log(self, message, minute=0):
        line_id = (self.log_lines[-1]["id"] + 1) if self.log_lines else 1
        ts = datetime(2026, 10, 8, 20, minute, line_id % 60, tzinfo=timezone.utc).isoformat()
        self.log_lines.append({"id": line_id, "ts": ts, "level": "INFO", "message": message})


def position(ticker, side, contracts, cost, chance, title="", subtitle=""):
    return {
        "ticker": ticker,
        "title": title,
        "subtitle": subtitle,
        "side": side,
        "contracts": str(contracts),
        "exposure": str(cost),
        "chance": str(chance),
        "value": "0",
        "payout": str(contracts),
        "hours_to_close": 3.0,
    }


class FakeNWS:
    def __init__(self):
        self.highs = {}  # ciudad -> {fecha: máxima}
        self.obs = {}  # ciudad -> {"day","max_f","last_f","last_at"}
        self.fail = set()

    def forecast_highs(self, city):
        if city.key in self.fail:
            raise ConnectionError("sin red")
        return dict(self.highs.get(city.key, {}))

    def observed_max(self, city, now=None):
        if city.key in self.fail:
            raise ConnectionError("sin red")
        return self.obs.get(city.key)


class FakeKalshiPublic:
    def __init__(self, markets=()):
        self.markets = list(markets)
        self.series = {}  # serie -> mercados abiertos
        self.by_ticker = {}  # ticker -> mercado (con su resultado cuando se decide)
        self.asked = []

    def markets_closing(self, within_hours, now=None, max_pages=8):
        return list(self.markets)

    def series_markets(self, series, max_pages=2):
        return list(self.series.get(series, []))

    def markets_by_ticker(self, tickers):
        self.asked.append(list(tickers))
        return [self.by_ticker[t] for t in tickers if t in self.by_ticker]


def market(ticker, bid, ask, title="", sub="", volume="500"):
    return {
        "ticker": ticker,
        "title": title,
        "yes_sub_title": sub,
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "volume_24h_fp": volume,
        "close_time": "2026-10-09T03:00:00Z",
    }


def weather_market(ticker, sub, bid, ask, close="2026-10-10T04:59:00Z", **extra):
    return {
        "ticker": ticker,
        "title": "Highest temperature in NYC today?",
        "yes_sub_title": sub,
        "yes_bid_dollars": bid,
        "yes_ask_dollars": ask,
        "close_time": close,
        "status": "active",
        "volume_24h_fp": "800",
        **extra,
    }
