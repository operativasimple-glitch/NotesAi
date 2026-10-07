from datetime import timedelta
from decimal import Decimal as D

import pytest

from kalshi_bot.discovery import MarketFilter, discover
from kalshi_bot.models import ASK, BID, GTC
from kalshi_bot.research import Research, bucket_index, run_research
from kalshi_bot.scanner import ScanParams, event_arbitrage, run_scan, scan_markets
from kalshi_bot.strategies.favorites import FavoritesStrategy

from .fakes import NOW, FakeKalshi, make_book, make_market, market_payload
from .test_strategies import ctx_for, summary

T = "KXTEST-26OCT08-B50"


# --- estrategia favoritos ---------------------------------------------------------


def fav(**params):
    return FavoritesStrategy(params)


def test_buys_yes_favorite_improving_one_tick():
    book = make_book(T, bids=[("0.90", 50)], asks=[("0.92", 50)])
    assert summary(fav().on_market(ctx_for(book))) == [(BID, D("0.91"), D("10"), GTC, True)]


def test_joins_the_bid_when_improving_would_cross():
    book = make_book(T, bids=[("0.91", 50)], asks=[("0.92", 50)])
    assert summary(fav().on_market(ctx_for(book)))[0][1] == D("0.91")
    assert summary(fav(improve=False).on_market(ctx_for(make_book(T, bids=[("0.90", 5)], asks=[("0.92", 5)]))))[0][
        1
    ] == D("0.90")


def test_buys_no_favorite_by_selling_yes():
    book = make_book(T, bids=[("0.05", 50)], asks=[("0.08", 50)])
    # NO cotiza a 92¢ de compra: el bot ofrece comprar NO a 93¢ = vender YES a 7¢.
    assert summary(fav().on_market(ctx_for(book))) == [(ASK, D("0.07"), D("10"), GTC, True)]


def test_never_pays_more_than_max_price():
    book = make_book(T, bids=[("0.97", 50)], asks=[("0.99", 50)])
    assert summary(fav().on_market(ctx_for(book)))[0][1] == D("0.97")
    book = make_book(T, bids=[("0.01", 50)], asks=[("0.03", 50)])
    assert summary(fav().on_market(ctx_for(book)))[0][1] == D("0.03")  # NO a 97¢ como mucho


def test_skips_wide_spreads_and_prices_outside_the_band():
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.90", 5)], asks=[("0.96", 5)]))) == []
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.50", 5)], asks=[("0.52", 5)]))) == []
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.98", 5)], asks=[("0.99", 5)]))) == []
    assert fav().on_market(ctx_for(make_book(T, bids=[("0.90", 5)]))) == []


def test_respects_max_position_on_each_side():
    yes_book = make_book(T, bids=[("0.90", 5)], asks=[("0.92", 5)])
    assert fav().on_market(ctx_for(yes_book, position=20)) == []
    assert fav().on_market(ctx_for(yes_book, position=15))[0].count == D("5")
    no_book = make_book(T, bids=[("0.05", 5)], asks=[("0.08", 5)])
    assert fav().on_market(ctx_for(no_book, position=-20)) == []


def test_invalid_band():
    with pytest.raises(ValueError):
        fav(min_price=0.4)
    with pytest.raises(ValueError):
        fav(min_price=0.95, max_price=0.9)


# --- búsqueda de mercados ----------------------------------------------------------


def closing_in(ticker, hours, volume="500", event=None):
    return make_market(
        ticker,
        close_time=(NOW + timedelta(hours=hours)).isoformat(),
        volume_24h_fp=volume,
        event_ticker=event or ticker.rsplit("-", 1)[0],
    )


def test_discover_markets_closing_soon_one_per_event():
    fake = FakeKalshi(
        markets=[
            closing_in("KXA-EV1-X", 10, "900", "KXA-EV1"),
            closing_in("KXA-EV1-Y", 12, "800", "KXA-EV1"),
            closing_in("KXB-EV2-X", 30, "100", "KXB-EV2"),
            closing_in("KXC-EV3-X", 60, "999", "KXC-EV3"),
            closing_in("KXD-EV4-X", 5, "700", "KXD-EV4"),
        ]
    )
    flt = MarketFilter(closing_within_hours=48, max_markets=10, max_markets_per_event=1, exclude_series=["KXD"])
    found = discover(fake, flt, NOW)
    assert [m.ticker for m in found] == ["KXA-EV1-X", "KXB-EV2-X"]
    query = fake.market_queries[-1]
    assert query["status"] is None and query["max_close_ts"] == int((NOW + timedelta(hours=48)).timestamp())


# --- escáner -------------------------------------------------------------------------


def test_scan_markets_finds_favorites_and_wide_spreads():
    markets = [
        make_market("FAV-YES", yes_bid_dollars="0.9100", yes_ask_dollars="0.9300"),
        make_market("FAV-NO", yes_bid_dollars="0.0500", yes_ask_dollars="0.0700"),
        make_market("WIDE", yes_bid_dollars="0.3000", yes_ask_dollars="0.4000"),
        make_market("THIN", yes_bid_dollars="0.9100", yes_ask_dollars="0.9300", volume_24h_fp="5"),
    ]
    result = scan_markets(markets, ScanParams(), NOW)
    assert [(f["ticker"], f["side"], f["bid"]) for f in result["favorites"]] == [
        ("FAV-YES", "yes", D("0.91")),
        ("FAV-NO", "no", D("0.93")),
    ]
    assert [s["ticker"] for s in result["spreads"]] == ["WIDE"]


def nested_event(ticker, bids_asks, mutually_exclusive=True):
    return {
        "event_ticker": ticker,
        "series_ticker": ticker.split("-")[0],
        "title": "Evento",
        "mutually_exclusive": mutually_exclusive,
        "markets": [
            market_payload(f"{ticker}-{i}", yes_bid_dollars=b, yes_ask_dollars=a) for i, (b, a) in enumerate(bids_asks)
        ],
    }


def test_event_arbitrage_buy_no_on_all_outcomes():
    events = [
        nested_event("KXARB-1", [("0.40", "0.45"), ("0.35", "0.40"), ("0.33", "0.38")]),
        nested_event("KXARB-2", [("0.40", "0.45"), ("0.35", "0.40"), ("0.33", "0.38")], mutually_exclusive=False),
        nested_event("KXNOARB-3", [("0.40", "0.42"), ("0.31", "0.33"), ("0.30", "0.32")]),
    ]
    found = event_arbitrage(events)
    assert [(a["event_ticker"], a["kind"]) for a in found] == [("KXARB-1", "buy_no_all")]
    # Suma de bids YES = 1.08; NO cuesta 1.92 y paga al menos 2; comisiones ~4.9¢.
    assert found[0]["profit"] == D("0.031")


def test_event_arbitrage_buy_yes_on_all_has_a_warning():
    found = event_arbitrage([nested_event("KXCHEAP-1", [("0.25", "0.30"), ("0.25", "0.30"), ("0.25", "0.30")])])
    assert [a["kind"] for a in found] == ["buy_yes_all"]
    assert found[0]["profit"] == D("0.055") and found[0]["warning"]


def test_run_scan_with_fake_client():
    fake = FakeKalshi(markets=[closing_in("KXA-EV1-X", 10, "900")])
    fake.markets["KXA-EV1-X"] = make_market(
        "KXA-EV1-X", yes_bid_dollars="0.9000", yes_ask_dollars="0.9200", volume_24h_fp="900"
    )
    fake.events = [nested_event("KXARB-1", [("0.40", "0.45"), ("0.35", "0.40"), ("0.33", "0.38")])]
    result = run_scan(fake, ScanParams(), NOW)
    assert result["markets_scanned"] == 1
    assert [f["ticker"] for f in result["favorites"]] == ["KXA-EV1-X"]
    assert [a["event_ticker"] for a in result["arbitrage"]] == ["KXARB-1"]


# --- investigación -------------------------------------------------------------------


def trade(price, count, taker, block=False):
    return {
        "yes_price_dollars": price,
        "count_fp": str(count),
        "taker_outcome_side": taker,
        "is_block_trade": block,
    }


def test_bucket_index():
    assert bucket_index(D("0.03")) == 0
    assert bucket_index(D("0.05")) == 1
    assert bucket_index(D("0.95")) == 9
    assert bucket_index(D("0.999")) == 9


def test_research_tallies_returns_by_role_and_price():
    r = Research()
    # Mercado que salió NO: el taker compró YES a 5¢ (longshot) y el maker se quedó NO a 95¢.
    r.add_market("no", [trade("0.0500", 10, "yes"), trade("0.0500", 99, "yes", block=True), {"bad": 1}])
    # Mercado que salió YES: el taker compró NO a 8¢ y el maker tiene YES a 92¢.
    r.add_market("yes", [trade("0.9200", 10, "no")])
    r.add_market("scalar", [trade("0.5000", 10, "yes")])  # se ignora
    report = r.report()
    assert report["markets"] == 2 and report["trades"] == 2
    rows = {row["range"]: row for row in report["buckets"]}
    assert rows["5–10¢"]["taker"]["return"] == D("-1.0000")  # longshots perdieron todo
    assert rows["95–100¢"]["maker"]["win_rate"] == D("1.0000")
    assert rows["95–100¢"]["maker"]["return"] == D("0.0526")  # 10 / 9.5 - 1
    assert rows["90–95¢"]["maker"]["return"] == D("0.0870")  # 10 / 9.2 - 1
    assert any("favoritos tiene base" in note for note in report["conclusions"])


def test_run_research_with_fake_client():
    fake = FakeKalshi()
    settled = make_market("KXOLD-1", status="finalized", result="no")
    fake.settled = {settled.ticker: settled}
    fake.trades = {"KXOLD-1": [trade("0.0400", 20, "yes"), trade("0.0600", 10, "yes")]}
    calls = []
    report = run_research(fake, series="KXOLD", progress=lambda done, total: calls.append((done, total)))
    assert report["markets"] == 1 and report["trades"] == 2 and calls == [(1, 1)]
    assert "Pocos mercados" in report["conclusions"][-1]
